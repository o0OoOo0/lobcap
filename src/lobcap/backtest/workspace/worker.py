"""Finite serial worker; every attempt has separate immutable run artifacts."""
import argparse
import os
from pathlib import Path
import uuid
import time

from lobcap.backtest.runner import _RUN_LOCK, _run_one, utc_now
from lobcap.config.common import sha256_file
from lobcap.backtest.types import jsonable
from lobcap.backtest.runtime import SimulationCanceled
from .contracts import config_from_run_spec
from .jobs import verify_spec, verify_completed
from .store import WorkspaceStore, atomic_json


def run_pending(root, run_function=None, *, wait_for_lock=False):
    store = WorkspaceStore(root)
    if not _RUN_LOCK.acquire(blocking=wait_for_lock):
        return False
    execute = run_function or _run_one
    worker_id = uuid.uuid4().hex
    try:
        for previous in store.list_jobs():
            if previous["status"] == "RUNNING":
                store.update_job(previous["job_id"], status="INTERRUPTED", error="Previous worker lost execution lock")
        while True:
            queued = [s for s in reversed(store.list_jobs()) if s["status"] == "QUEUED"]
            if not queued:
                return True
            state = queued[0]
            job_id = state["job_id"]
            try:
                spec = store.get_experiment(job_id)
                verify_spec(spec)
                verify_completed(state)
                state = store.claim_job(job_id, worker_id)
                if state is None:
                    continue
                for row in spec["runs"]:
                    identifier = row["variant_id"]
                    if identifier in state["completed"]:
                        continue
                    if store.get_job(job_id)["cancel_requested"]:
                        store.update_job(job_id, status="CANCELED", current_variant=None)
                        break
                    store.update_job(job_id, current_variant=identifier)
                    config = config_from_run_spec(row)
                    policy = None
                    if row["research_policy"] is not None:
                        from lobcap.backtest.research import policy_from_dict
                        policy = policy_from_dict(row["research_policy"])
                    try:
                        last_poll = [0.0, False]
                        def cancel_check():
                            now = time.monotonic()
                            if now-last_poll[0] >= 0.05:
                                last_poll[:] = [now, store.get_job(job_id)["cancel_requested"]]
                            return last_poll[1]
                        result = execute(config, spec["sample_path"], research_policy=policy,
                            cancel_check=cancel_check)
                    except BaseException as exc:
                        canceled = isinstance(exc, (SimulationCanceled, KeyboardInterrupt))
                        failure = {"error": f"{type(exc).__name__}: {exc}", "variant_id": identifier,
                            "attempt": state["attempt"], "output_dir": str(getattr(exc, "output_dir", "")),
                            "status": "CANCELED" if canceled else "FAILED"}
                        atomic_json(store.path("attempts", job_id, f"{state['attempt']}-{identifier}.json"), failure)
                        store.update_job(job_id, status=failure["status"], failed={identifier: failure}, error=failure["error"])
                        break
                    output = Path(result.output_dir)
                    completed = {"variant_id": identifier, "run_id": output.name, "output_dir": str(output),
                        "deterministic_digest": result.digest, "summary": jsonable(result.summary),
                        "output_hashes": {p.name: sha256_file(p) for p in output.iterdir() if p.is_file()},
                        "attempt": state["attempt"], "completed_at": utc_now()}
                    atomic_json(store.path("attempts", job_id, f"{state['attempt']}-{identifier}.json"), completed)
                    state["completed"][identifier] = completed
                    store.update_job(job_id, completed=state["completed"])
                else:
                    store.update_job(job_id, status="COMPLETED", current_variant=None, completed_at=utc_now())
            except Exception as exc:
                store.update_job(job_id, status="FAILED", error=f"{type(exc).__name__}: {exc}")
    finally:
        _RUN_LOCK.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    # A launched worker waits for the OS lock. This closes the submit/last-worker
    # exit race: its durable QUEUED job always gets one post-lock queue inspection.
    run_pending(args.root, wait_for_lock=True)


if __name__ == "__main__":
    main()
