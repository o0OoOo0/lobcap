#!/usr/bin/env python3
"""Verify DEVELOPMENT populations, provenance and cache checksums."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from lobcap.backtest.research_flow.signal_study import validate_report
from lobcap.config.common import sha256_file


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate(report_path, catalogue_path, memory_limit_mib=512):
    report = _read(report_path)
    root = Path(report_path).resolve().parent
    manifests = {}
    for wid, cache in report.get("feature_caches", {}).items():
        path = Path(cache["path"]).resolve()
        if not path.is_relative_to(root/"feature_cache"):
            raise ValueError("feature cache must belong to this study")
        manifest = _read(path/"feature_manifest.json")
        if set(manifest.get("files", {})) != {"features.jsonl.gz"}:
            raise ValueError("unexpected feature cache artifacts")
        for name, digest in manifest["files"].items():
            if sha256_file(path/name) != digest:
                raise ValueError("feature cache checksum mismatch")
        manifests[wid] = manifest
    result = validate_report(report, _read(catalogue_path),
        _read(root/"freshness_policy.json"), manifests, memory_limit_mib)
    result["cache_checksums_verified"] = True
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--catalogue", required=True)
    parser.add_argument("--memory-limit-mib", type=float, default=512)
    args = parser.parse_args(argv)
    print(json.dumps(validate(args.report, args.catalogue, args.memory_limit_mib),
                     sort_keys=True))


if __name__ == "__main__":
    main()
