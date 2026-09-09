"""Shared YAML fingerprints and bounded-memory file hashing."""
import hashlib
import json
from pathlib import Path

import yaml


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_yaml_with_hashes(path: str | Path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    row = yaml.safe_load(raw)
    if not isinstance(row, dict):
        raise ValueError("config root must be a mapping")
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return path, row, hashlib.sha256(raw).hexdigest(), hashlib.sha256(canonical.encode()).hexdigest()
