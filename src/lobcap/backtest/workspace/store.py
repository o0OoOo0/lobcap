"""Atomic workspace metadata; immutable experiment and completed run evidence."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import uuid

from lobcap.backtest.runner import config_document, utc_now
from lobcap.backtest.types import jsonable
from .contracts import StrategyDraft, config_from_document, fingerprint


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(jsonable(value), stream, sort_keys=True, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class WorkspaceStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, category, identifier, filename=None):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ValueError("invalid workspace identifier")
        base = self.root / category / identifier
        return base / filename if filename else base.with_suffix(".json")

    @contextmanager
    def transaction(self):
        """Short metadata transaction, protected across tabs/processes."""
        path = self.root / ".metadata.lock"
        with path.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b"0"); stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def save_draft(self, draft, expected_revision=None):
        with self.transaction():
            identifier = draft.draft_id or uuid.uuid4().hex
            path = self.path("drafts", identifier)
            old = read_json(path) if path.exists() else None
            revision = old["revision"] if old else 0
            expected = draft.revision if expected_revision is None else expected_revision
            if expected != revision:
                raise ValueError("draft revision conflict; reload before saving")
            saved = replace(draft, draft_id=identifier, revision=revision+1)
            row = {"draft_id": identifier, "revision": saved.revision, "name": saved.name,
                "hypothesis": saved.hypothesis, "parent_run_id": saved.parent_run_id,
                "research_policy": jsonable(saved.research_policy), "config_document": config_document(saved.config),
                "saved_at": utc_now()}
            atomic_json(self.path("draft_revisions", identifier, f"{saved.revision}.json"), row)
            atomic_json(path, row)
            return saved

    def load_draft(self, identifier):
        row = read_json(self.path("drafts", identifier))
        return StrategyDraft(config_from_document(row["config_document"]), row["name"], row["hypothesis"],
            row["draft_id"], row["revision"], row["parent_run_id"], row["research_policy"])

    def list_drafts(self):
        return sorted((read_json(p) for p in (self.root / "drafts").glob("*.json")),
            key=lambda r: r["saved_at"], reverse=True)

    def submit(self, spec, submission_key):
        if not isinstance(submission_key, str) or not submission_key:
            raise ValueError("an explicit submission key is required")
        from .jobs import verify_spec
        verify_spec(spec)
        with self.transaction():
            key_path = self.path("submissions", fingerprint(submission_key))
            if key_path.exists():
                previous = read_json(key_path)
                if previous["experiment_hash"] != spec["experiment_hash"]:
                    raise ValueError("submission key was already used for a different experiment")
                return previous["job_id"]
            job_id = uuid.uuid4().hex
            atomic_json(self.path("experiments", job_id), spec)
            state = {"job_id": job_id, "experiment_hash": spec["experiment_hash"], "name": spec["name"],
                "status": "QUEUED", "created_at": utc_now(), "updated_at": utc_now(), "attempt": 0,
                "current_variant": None, "completed": {}, "failed": {}, "total": len(spec["runs"]),
                "cancel_requested": False, "paper_eligibility": "INSUFFICIENT_EVIDENCE"}
            atomic_json(self.path("jobs", job_id), state)
            atomic_json(key_path, {"job_id": job_id, "experiment_hash": spec["experiment_hash"]})
            return job_id

    def get_job(self, job_id):
        return read_json(self.path("jobs", job_id))

    def get_experiment(self, job_id):
        return read_json(self.path("experiments", job_id))

    def list_jobs(self):
        return sorted((read_json(p) for p in (self.root / "jobs").glob("*.json")),
            key=lambda r: (r["created_at"], r["job_id"]), reverse=True)

    def update_job(self, job_id, **changes):
        with self.transaction():
            state = self.get_job(job_id)
            state.update(changes, updated_at=utc_now())
            atomic_json(self.path("jobs", job_id), state)
            return state

    def claim_job(self, job_id, worker_id):
        with self.transaction():
            state = self.get_job(job_id)
            if state["status"] != "QUEUED" or state["cancel_requested"]:
                return None
            state.update(status="RUNNING", attempt=state["attempt"]+1,
                worker_id=worker_id, worker_pid=os.getpid(), started_at=utc_now(),
                updated_at=utc_now(), error=None)
            atomic_json(self.path("jobs", job_id), state)
            return state

    def request_cancel(self, job_id):
        with self.transaction():
            state = self.get_job(job_id)
            if state["status"] not in {"QUEUED", "RUNNING"}:
                return state
            state["cancel_requested"] = True
            if state["status"] == "QUEUED":
                state["status"] = "CANCELED"
            state["updated_at"] = utc_now()
            atomic_json(self.path("jobs", job_id), state)
            return state

    def resume(self, job_id):
        from .jobs import verify_spec, verify_completed
        with self.transaction():
            state = self.get_job(job_id)
            if state["status"] not in {"FAILED", "CANCELED", "INTERRUPTED"}:
                raise ValueError("only failed, canceled, or interrupted jobs can resume")
            verify_spec(self.get_experiment(job_id))
            verify_completed(state)
            atomic_json(self.path("attempts", job_id, f"state-{state['attempt']}-{uuid.uuid4().hex}.json"), state)
            state.update(status="QUEUED", cancel_requested=False, failed={}, current_variant=None, updated_at=utc_now())
            atomic_json(self.path("jobs", job_id), state)
            return state

    def save_review(self, run_id, note="", favorite=False, candidate=False):
        row = {"run_id": run_id, "note": note, "favorite": bool(favorite), "candidate": bool(candidate),
            "paper_eligibility": "INSUFFICIENT_EVIDENCE", "updated_at": utc_now()}
        with self.transaction():
            atomic_json(self.path("reviews", run_id), row)
        return row

    def get_review(self, run_id):
        path = self.path("reviews", run_id)
        return read_json(path) if path.exists() else {"run_id": run_id, "note": "", "favorite": False, "candidate": False}
