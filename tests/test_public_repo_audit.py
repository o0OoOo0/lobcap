from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_public_repo.py"
SPEC = spec_from_file_location("audit_public_repo", SCRIPT)
assert SPEC and SPEC.loader
audit = module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def test_detects_private_runtime_home(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("deploy under /home/" + "alice/lobcap-release", encoding="utf-8")
    assert any(item.reason == "private runtime home" for item in audit.inspect(path))


def test_placeholder_runtime_home_is_allowed(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("deploy under /home/<vm-user>/lobcap-release", encoding="utf-8")
    assert not audit.inspect(path)


def test_detects_large_or_raw_payload(tmp_path, monkeypatch):
    path = tmp_path / "capture.jsonl.gz"
    path.write_bytes(b"sample")
    assert any("market-data payload" in item.reason for item in audit.inspect(path))

    normal = tmp_path / "large.txt"
    normal.write_bytes(b"1234")
    monkeypatch.setattr(audit, "MAX_TRACKED_BYTES", 3)
    assert any("tracked file" in item.reason for item in audit.inspect(normal))
