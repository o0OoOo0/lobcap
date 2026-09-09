"""Worker launch and integrity verification shared by CLI and UI."""
import os
from pathlib import Path
import subprocess
import sys

from lobcap.config.common import sha256_file
from .contracts import fingerprint, config_from_run_spec
from .resolve import code_fingerprint


def verify_spec(spec):
    body = {k: v for k, v in spec.items() if k != "experiment_hash"}
    if fingerprint(body) != spec["experiment_hash"]:
        raise ValueError("experiment specification checksum mismatch")
    if spec["code_sha256"] != code_fingerprint():
        raise ValueError("code changed; clone as a new experiment instead of resuming")
    for name, checksum in spec["sample_files"].items():
        if sha256_file(Path(spec["sample_path"])/name) != checksum:
            raise ValueError(f"sample checksum mismatch: {name}")
    for row in spec["runs"]:
        config = config_from_run_spec(row)
        expected = fingerprint({"config": config.resolved(), "policy": row["research_policy"],
            "sample": spec["sample_files"], "code": spec["code_sha256"]})
        if row["effective_hash"] != expected or row["variant_id"] != expected:
            raise ValueError("effective run configuration checksum mismatch")


def verify_completed(state):
    for row in state["completed"].values():
        for name, checksum in row["output_hashes"].items():
            if sha256_file(Path(row["output_dir"])/name) != checksum:
                raise ValueError(f"completed result checksum mismatch: {name}")


def launch_worker(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    source = str(Path(__file__).resolve().parents[3])
    environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
    kwargs = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NO_WINDOW}
    with (root / "worker.log").open("ab") as log:
        process = subprocess.Popen([sys.executable, "-m", "lobcap.backtest.workspace.worker", "--root", str(root)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=environment, **kwargs)
    return process


def recover_interrupted(root):
    """A acquired global lock proves no cooperating worker still owns execution."""
    from lobcap.backtest.runner import _RUN_LOCK
    from .store import WorkspaceStore
    if not _RUN_LOCK.acquire(blocking=False):
        return []
    try:
        store = WorkspaceStore(root)
        recovered = []
        for state in store.list_jobs():
            if state["status"] == "RUNNING":
                store.update_job(state["job_id"], status="INTERRUPTED", error="Worker ended without completing this job")
                recovered.append(state["job_id"])
        return recovered
    finally:
        _RUN_LOCK.release()
