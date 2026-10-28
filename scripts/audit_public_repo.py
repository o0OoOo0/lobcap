#!/usr/bin/env python3
"""Fail when the publishable Git tree contains common secrets or bulky data.

This scans the current worktree and index. It deliberately does not claim to
clean Git history; use a dedicated history audit before any history rewrite.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_TRACKED_BYTES = 5 * 1024 * 1024
RAW_SUFFIXES = (".parquet", ".feather", ".jsonl.gz", ".csv.gz", ".pcap")


@dataclass(frozen=True)
class Finding:
    path: str
    reason: str


TEXT_PATTERNS = {
    "private key material": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "GitHub token": re.compile(r"\bgh[oprsu]_[A-Za-z0-9_]{20,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "GCP project identifier": re.compile(r"\bproject-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b"),
    "private runtime home": re.compile(r"/home/[A-Za-z0-9._-]+/lobcap(?:[-A-Za-z0-9._]*)?"),
}


def candidate_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def inspect(path: Path) -> list[Finding]:
    try:
        relative = path.relative_to(ROOT).as_posix()
    except ValueError:
        relative = path.name
    if not path.is_file():
        return []
    size = path.stat().st_size
    findings: list[Finding] = []
    if size > MAX_TRACKED_BYTES:
        findings.append(Finding(relative, f"tracked file is {size} bytes; limit is {MAX_TRACKED_BYTES}"))
    lowered = relative.lower()
    if lowered.endswith(RAW_SUFFIXES):
        findings.append(Finding(relative, "raw or compressed market-data payload is publishable"))
    if size > MAX_TRACKED_BYTES:
        return findings
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return findings
    for label, pattern in TEXT_PATTERNS.items():
        if label == "private runtime home" and relative.startswith(("docs/legacy/", "legacy_code/")):
            continue
        if pattern.search(text):
            findings.append(Finding(relative, label))
    return findings


def main() -> int:
    findings = [finding for path in candidate_paths() for finding in inspect(path)]
    if findings:
        print("Public-repository audit failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding.path}: {finding.reason}", file=sys.stderr)
        return 1
    print("Public-repository audit passed: no credential patterns, private runtime homes, or oversized payloads found.")
    print("Scope: current index/worktree only; Git history is outside this check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
