#!/usr/bin/env python3
"""Rebuild all Study 1 completion reviews from source and original run artifacts."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from validate_signal_development import validate as validate_p0
from lobcap.backtest.research_flow.sample_catalogue import (
    DEVELOPMENT, PILOT_OOS_LOCKED, VALIDATION_LOCKED,
)
from lobcap.backtest.research_flow.signal_study import development_windows, digest
from lobcap.backtest.research_flow.study1 import sealed_config
from lobcap.backtest.research_flow.study1_completion import (
    COMBINED_CASES, DECOMPOSITION_CASES, PRIMARY_CASES, ROBUSTNESS_SCENARIOS,
    VALIDATION_CASES, audit_causal_paths, combined_configs, decomposition_configs,
    development_gate, pooled_vpin_mean, primary_configs, review_group, role_windows,
    scenario_config, summarize_rows, validation_configs, validation_gate,
    vpin_mean_window,
)
from lobcap.backtest.research_flow.study1_vpin import feature_window, summarize_prerequisite
from lobcap.backtest.workspace.contracts import config_from_document
from lobcap.config.common import sha256_file


def _configs(config, phase, mean):
    if phase == "decomposition":
        return decomposition_configs(config, mean), DECOMPOSITION_CASES, "MM-3-zero"
    if phase == "combined":
        return combined_configs(config), COMBINED_CASES, "MM-4-zero"
    if phase == "robustness":
        return primary_configs(config), PRIMARY_CASES, None
    if phase in {"validation", "oos"}:
        return validation_configs(config), VALIDATION_CASES, None
    raise ValueError("unknown completion validation phase")


def _rebuild(root, config, selected, reported, phase, mean, scenario="baseline"):
    if len(reported) != len(selected) or {row["window_id"] for row in reported} != {
            window.window_id for window, _ in selected}:
        raise ValueError("missing completion report windows")
    by_window = {row["window_id"]: row for row in reported}
    rebuilt = []
    for window, seal in selected:
        window_root = root/phase/scenario/window.window_id
        base = sealed_config(config, window, seal,
            config.output_path/"samples"/window.window_id, window_root, "vpin")
        base = scenario_config(base, scenario)
        configs, labels, zero = _configs(base, phase, mean)
        row = by_window[window.window_id]
        paths = [Path(row["cases"][label]["output_dir"]).resolve() for label in labels]
        if (len(set(paths)) != len(labels)
            or any(not path.is_relative_to(window_root/"runs") for path in paths)):
            raise ValueError("completion artifacts escaped their phase/window root")
        checked = review_group(window, seal, configs, paths, labels, zero)
        checked["scenario"] = scenario
        checked["causal_audit"] = audit_causal_paths(base,
            config.output_path/"samples"/window.window_id, labels, configs, paths)
        if checked != row:
            raise ValueError("completion report differs from original run artifacts")
        rebuilt.append(checked)
    return rebuilt


def validate(report_path, catalogue_path, p0_report):
    report_path = Path(report_path).resolve()
    root = report_path.parent
    report = json.loads(report_path.read_text(encoding="utf-8"))
    catalogue = json.loads(Path(catalogue_path).read_text(encoding="utf-8"))
    protocol = json.loads((root/"execution_protocol.json").read_text(encoding="utf-8"))
    input_protocol = json.loads((root/"input_protocol.json").read_text(encoding="utf-8"))
    if (report.get("status") != "PASS" or report.get("protocol") != protocol
        or protocol.get("version") != "study1-completion-v1"
        or protocol.get("protocol_sha256") != digest({k: v for k, v in protocol.items()
            if k != "protocol_sha256"})
        or input_protocol.get("version") != "study1-completion-input-v1"
        or input_protocol.get("protocol_sha256") != digest({k: v for k, v in input_protocol.items()
            if k != "protocol_sha256"})
        or protocol.get("input_protocol_sha256") != input_protocol["protocol_sha256"]
        or input_protocol.get("catalogue_sha256") != catalogue["catalogue_sha256"]
        or input_protocol.get("p0_report_sha256") != sha256_file(p0_report)):
        raise ValueError("invalid Study 1 completion protocol provenance")
    validate_p0(p0_report, catalogue_path)
    config = config_from_document(protocol["base_config"])
    if input_protocol["base_config"] != protocol["base_config"] or config.clock_mode != "recorded":
        raise ValueError("completion base configuration drift")
    development = development_windows(catalogue)
    expected = [window.window_id for window, _ in development]
    if protocol["development_window_ids"] != expected or input_protocol["window_ids"] != expected:
        raise ValueError("completion DEVELOPMENT window selection changed")

    feature_path = root/"vpin_prerequisite.json"
    mean_path = root/"vpin_mean_fit.json"
    feature_report = json.loads(feature_path.read_text(encoding="utf-8"))
    mean_report = json.loads(mean_path.read_text(encoding="utf-8"))
    if (protocol["vpin_prerequisite_sha256"] != sha256_file(feature_path)
        or protocol["vpin_mean_fit_sha256"] != sha256_file(mean_path)
        or feature_report.get("status") != "PASS"
        or feature_report.get("input_protocol_sha256") != input_protocol["protocol_sha256"]
        or feature_report.get("code_fingerprint") != protocol["code_fingerprint"]
        or [row["window_id"] for row in feature_report["per_window"]] != expected
        or [row["window_id"] for row in mean_report["per_window"]] != expected):
        raise ValueError("completion VPIN input provenance mismatch")
    if feature_report["summary"] != summarize_prerequisite(feature_report["per_window"]):
        raise ValueError("completion VPIN prerequisite aggregation mismatch")
    freshness = json.loads(Path(p0_report).read_text(encoding="utf-8"))["freshness_policy"]
    feature_rows = {row["window_id"]: row for row in feature_report["per_window"]}
    rebuilt_means = []
    for window, seal in development:
        sample = config.output_path/"samples"/window.window_id
        sealed = sealed_config(config, window, seal, sample, root, "vpin")
        feature = feature_window(sealed, sample, freshness,
            root/"vpin_features"/(window.window_id+".jsonl.gz"), False)
        feature.update(window_id=window.window_id, date=window.date)
        if feature != feature_rows[window.window_id]:
            raise ValueError("completion VPIN prerequisite differs from source replay")
        mean = vpin_mean_window(sealed, sample)
        mean.update(window_id=window.window_id, date=window.date,
            sample_sha256=seal["sample_sha256"])
        rebuilt_means.append(mean)
    pooled = pooled_vpin_mean(rebuilt_means)
    pooled["per_window"] = rebuilt_means
    if pooled != mean_report or pooled["mean"] != protocol["mean_vpin"]:
        raise ValueError("completion mean-width fit differs from source replay")
    if (protocol["decomposition_cases"] != {k: v.resolved() for k, v in
            decomposition_configs(config, pooled["mean"]).items()}
        or protocol["combined_cases"] != {k: v.resolved() for k, v in combined_configs(config).items()}
        or protocol["robustness_scenarios"] != ROBUSTNESS_SCENARIOS):
        raise ValueError("completion frozen cases changed")

    decomposition = _rebuild(root, config, development,
        report["decomposition"]["per_window"], "decomposition", pooled["mean"])
    combined = _rebuild(root, config, development,
        report["combined"]["per_window"], "combined", pooled["mean"])
    decomposition_summary = summarize_rows(decomposition, DECOMPOSITION_CASES)
    combined_summary = summarize_rows(combined, COMBINED_CASES)
    if decomposition_summary != report["decomposition"] or combined_summary != report["combined"]:
        raise ValueError("completion DEVELOPMENT aggregation mismatch")
    robustness = {"baseline": summarize_rows(combined, PRIMARY_CASES)}
    for scenario in ROBUSTNESS_SCENARIOS:
        rows = _rebuild(root, config, development,
            report["robustness"][scenario]["per_window"], "robustness", pooled["mean"], scenario)
        robustness[scenario] = summarize_rows(rows, PRIMARY_CASES)
    if robustness != report["robustness"]:
        raise ValueError("completion robustness aggregation mismatch")
    dev_gate = development_gate(combined_summary, robustness)
    if dev_gate != report["development_gate"] or dev_gate != json.loads(
            (root/"development_gate.json").read_text(encoding="utf-8")):
        raise ValueError("completion DEVELOPMENT gate mismatch")

    roles = [DEVELOPMENT]
    validation_report = validation_gate_report = oos_report = None
    if dev_gate["status"] == "PASS":
        roles.append(VALIDATION_LOCKED)
        selected = role_windows(catalogue, VALIDATION_LOCKED)
        validation_report = {}
        for scenario in ("baseline", "joint_fee_latency_queue"):
            rows = _rebuild(root, config, selected,
                report["validation"][scenario]["per_window"], "validation", pooled["mean"], scenario)
            validation_report[scenario] = summarize_rows(rows, VALIDATION_CASES)
        validation_gate_report = validation_gate(validation_report["baseline"],
            validation_report["joint_fee_latency_queue"])
        if (validation_report != json.loads((root/"validation_report.json").read_text(encoding="utf-8"))
            or validation_gate_report != json.loads((root/"validation_gate.json").read_text(encoding="utf-8"))):
            raise ValueError("completion validation report or gate artifact mismatch")
        if validation_gate_report["status"] == "PASS":
            roles.append(PILOT_OOS_LOCKED)
            selected = role_windows(catalogue, PILOT_OOS_LOCKED)
            rows = _rebuild(root, config, selected, report["pilot_oos"]["per_window"],
                "oos", pooled["mean"])
            oos_report = summarize_rows(rows, VALIDATION_CASES)
            if oos_report != json.loads((root/"oos_report.json").read_text(encoding="utf-8")):
                raise ValueError("completion OOS report artifact mismatch")
    if (roles != report["roles_opened"] or validation_report != report["validation"]
        or validation_gate_report != report["validation_gate"] or oos_report != report["pilot_oos"]):
        raise ValueError("completion sequential outcome gate mismatch")
    phases = report["performance"]
    if (not phases or any(type(row.get(key)) not in (int, float)
        or isinstance(row[key], bool) or not math.isfinite(row[key]) or row[key] <= 0
        for row in phases for key in ("wall_seconds", "peak_rss_mib"))
        or max(row["peak_rss_mib"] for row in phases) >= 512):
        raise ValueError("invalid completion performance evidence")
    return {"status": "PASS", "validation_version": "study1-completion-acceptance-v1",
        "runtime_code": protocol["code_fingerprint"], "report_sha256": sha256_file(report_path),
        "protocol_sha256": protocol["protocol_sha256"], "development_windows": len(development),
        "execution_phases": len(phases)-2*len(development), "all_ledgers_reconciled": True,
        "source_replayed_vpin_inputs": True, "roles_opened": roles,
        "roles_kept_locked": report["roles_kept_locked"],
        "maximum_phase_rss_mib": max(row["peak_rss_mib"] for row in phases),
        "scope": "source-replayed inputs and rebuilt original artifact reviews; execution not rerun"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--catalogue", required=True)
    parser.add_argument("--p0-report", required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.report, args.catalogue, args.p0_report), sort_keys=True))
