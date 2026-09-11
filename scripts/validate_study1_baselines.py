#!/usr/bin/env python3
"""Rebuild Study 1 review from original run artifacts without rerunning execution."""
import argparse
import json
import math
from pathlib import Path

from validate_signal_development import validate as validate_p0
from lobcap.backtest.research_flow.signal_study import development_windows, digest
from lobcap.backtest.research_flow.study1 import stage_cases, case_configs, sealed_config, review_window, summarize
from lobcap.backtest.workspace.contracts import config_from_document
from lobcap.config.common import sha256_file


def validate(report_path, catalogue_path, p0_report):
    root = Path(report_path).resolve().parent
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    catalogue = json.loads(Path(catalogue_path).read_text(encoding="utf-8"))
    protocol = report["protocol"]
    stage = {"study1-baselines-v1": "baselines", "study1-bp-v1": "bp"}.get(protocol.get("version"))
    if (report["status"] != "PASS" or stage is None
        or protocol["protocol_sha256"] != digest({k: v for k, v in protocol.items() if k != "protocol_sha256"})
        or protocol != json.loads((root/"protocol.json").read_text(encoding="utf-8"))
        or protocol["catalogue_sha256"] != catalogue["catalogue_sha256"]
        or protocol["p0_report_sha256"] != sha256_file(p0_report)
        or protocol["roles_opened"] != ["DEVELOPMENT"] or protocol["locked_roles_opened"] != []):
        raise ValueError("invalid Study 1 protocol or prerequisite provenance")
    validate_p0(p0_report, catalogue_path)
    labels = stage_cases(stage)
    stage_args = (stage,) if stage != "baselines" else ()
    selected = development_windows(catalogue)
    expected = [w.window_id for w, _ in selected]
    if protocol["window_ids"] != expected:
        raise ValueError("Study 1 protocol window selection changed")
    config = config_from_document(protocol["base_config"])
    if config.clock_mode != "recorded" or protocol["cases"] != {k: c.resolved() for k, c in case_configs(config, stage).items()}:
        raise ValueError("Study 1 protocol case settings changed")
    reported = report["results"]["per_window"]
    if len(reported) != len(expected) or {r["window_id"] for r in reported} != set(expected):
        raise ValueError("missing or duplicate Study 1 report windows")
    by_window = {r["window_id"]: r for r in reported}
    rebuilt, diagnostics = [], {}
    for window, seal in selected:
        window_root = root/window.window_id
        c = sealed_config(config, window, seal, config.output_path/"samples"/window.window_id, window_root, *stage_args)
        row = by_window[window.window_id]
        paths = [Path(row["cases"][label]["output_dir"]).resolve() for label in labels]
        if len(set(paths)) != len(labels) or any(not p.is_relative_to(window_root/"runs") for p in paths):
            raise ValueError("Study 1 run artifacts must belong to their window")
        checked = review_window(window, seal, case_configs(c, stage), paths, *stage_args)
        if stage == "bp":
            from lobcap.backtest.research_flow.study1_bp import audit_feature_parity
            checked["bp_feature_parity"] = audit_feature_parity(c, config.output_path/"samples"/window.window_id, paths)
        if checked != row or checked["code_version"] != protocol["code_fingerprint"]:
            raise ValueError("Study 1 report differs from reviewed run artifacts")
        rebuilt.append(checked)
        diagnostics[window.window_id] = {}
        for label, path in zip(labels, paths):
            source = path/"diagnostics.json"
            d = json.loads(source.read_text(encoding="utf-8"))
            diagnostics[window.window_id][label] = {
                "diagnostics_sha256": sha256_file(source),
                "decision_reasons": d["decision_reasons"], "quote_research": d["quote_research"],
                "break_even_maker_bps_fixed_fills": d["break_even_maker_bps_fixed_fills"]}
    if summarize(catalogue, rebuilt, *stage_args) != report["results"]:
        raise ValueError("Study 1 aggregation differs from original pairs")
    phases = report["performance"]
    if (len(phases) != len(expected) or {r["window_id"] for r in phases} != set(expected)
        or any(not isinstance(r.get(k), (int, float)) or isinstance(r[k], bool)
               or not math.isfinite(r[k]) or r[k] <= 0 for r in phases for k in ("wall_seconds", "peak_rss_mib"))
        or max(r["peak_rss_mib"] for r in phases) >= 512):
        raise ValueError("invalid measured Study 1 performance evidence")
    return {"status": "PASS", "validation_version": f"study1-{stage}-acceptance-v1",
        "runtime_code": protocol["code_fingerprint"], "report_sha256": sha256_file(report_path),
        "protocol_sha256": protocol["protocol_sha256"], "windows": len(rebuilt),
        "runs": len(rebuilt)*len(labels), "all_zero_controls_match": True,
        "all_ledgers_reconciled": True, "maximum_phase_rss_mib": max(r["peak_rss_mib"] for r in phases),
        "diagnostics": diagnostics, "locked_roles_opened": [],
        "scope": "reconstructed paired review and aggregation; execution itself was not rerun"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--catalogue", required=True)
    parser.add_argument("--p0-report", required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.report, args.catalogue, args.p0_report), sort_keys=True))
