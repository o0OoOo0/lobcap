#!/usr/bin/env python3
"""Fail-closed structural validation for a DEVELOPMENT signal report."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate(report_path, catalogue_path, memory_limit_mib=512):
    report = _read(report_path)
    catalogue = _read(catalogue_path)
    claimed = catalogue.pop("catalogue_sha256", None)
    actual = hashlib.sha256(json.dumps(catalogue, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    checks = {
        "report_pass": report.get("status") == "PASS",
        "catalogue_hash_valid": claimed == actual,
        "catalogue_bound": report.get("catalogue_sha256") == claimed,
        "development_only": report.get("roles_opened") == ["DEVELOPMENT"],
        "locked_roles_unopened": report.get("locked_roles_opened") == [],
        "statistical_claim_withheld": report.get("statistical_significance") == "NOT_CLAIMED",
        "paper_eligibility_withheld": report.get("paper_eligibility") == "INSUFFICIENT_EVIDENCE",
    }
    features = tuple(report.get("features", ()))
    reports = report.get("reports", {})
    checks["seven_preregistered_features"] = len(features) == 7 and len(set(features)) == 7
    checks["eight_development_windows"] = len(reports.get("per_window", {})) == 8
    checks["two_development_dates"] = len(reports.get("per_date", {})) == 2
    checks["pooled_feature_set"] = set(reports.get("pooled", {})) == set(features)
    scopes = list(reports.get("per_window", {}).values()) + list(
        reports.get("per_date", {}).values()) + [reports.get("pooled", {})]
    checks["all_scopes_complete"] = all(set(scope) == set(features) and
        all(len(scope[name]) == 12 for name in features) for scope in scopes)
    phases = report.get("phase_performance", ())
    peaks = [row.get("peak_rss_mib") for row in phases]
    checks["all_phase_memory_measured"] = bool(peaks) and all(
        isinstance(value, (int, float)) for value in peaks)
    checks["phase_memory_below_limit"] = bool(peaks) and max(peaks) < memory_limit_mib
    checks["four_freshness_horizons"] = len(report.get("freshness_policy", {}).get(
        "maximum_age_by_horizon_ns", ())) == 4
    failed = sorted(name for name, passed in checks.items() if not passed)
    result = {"status": "PASS" if not failed else "FAIL", "checks": checks,
              "failed": failed, "phase_count": len(phases),
              "maximum_phase_rss_mib": max(peaks) if peaks and all(
                  isinstance(value, (int, float)) for value in peaks) else None}
    if failed:
        raise ValueError(json.dumps(result, sort_keys=True))
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
