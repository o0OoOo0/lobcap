#!/usr/bin/env python3
"""Run the frozen remaining Study 1 experiments with sequential outcome gates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_signal_development import _code_fingerprint, _isolated
from validate_signal_development import validate as validate_p0
from lobcap.backtest.replay import write_json
from lobcap.backtest.research_flow.sample_catalogue import (
    DEVELOPMENT, PILOT_OOS_LOCKED, VALIDATION_LOCKED,
)
from lobcap.backtest.research_flow.signal_study import development_windows, digest
from lobcap.backtest.research_flow.study1 import sealed_config
from lobcap.backtest.research_flow.study1_completion import (
    COMBINED_CASES, DECOMPOSITION_CASES, PRIMARY_CASES, ROBUSTNESS_SCENARIOS,
    VALIDATION_CASES, combined_configs, decomposition_configs, development_gate,
    pooled_vpin_mean, role_windows, run_group, scenario_config, summarize_rows,
    validation_configs, validation_gate, vpin_mean_phase,
)
from lobcap.backtest.research_flow.study1_vpin import prerequisite_phase, summarize_prerequisite
from lobcap.backtest.runner import config_document
from lobcap.config.backtest import load_backtest_config
from lobcap.config.common import sha256_file
from lobcap.config.experiment import ROOT


def _performance(result, window, phase, scenario="baseline"):
    return {"phase": phase, "scenario": scenario, "window_id": window.window_id,
        "wall_seconds": result["wall_seconds"], "peak_rss_mib": result["peak_rss_mib"]}


def _run_rows(config, selected, root, phase, labels, code, mean=None, scenario="baseline"):
    rows, performance = [], []
    for window, seal in selected:
        destination = root/phase/scenario/window.window_id
        result = _isolated(run_group, config, window, seal,
            config.output_path/"samples"/window.window_id, destination, phase, mean, scenario)
        row = result["window"]
        if row["code_version"] != code:
            raise ValueError("runtime code changed after Study 1 protocol freeze")
        rows.append(row)
        performance.append(_performance(result, window, phase, scenario))
        write_json(root/phase/scenario/(window.window_id+".json"), row)
        write_json(root/"progress.json", {"status": "RUNNING", "completed": performance})
    return rows, performance


def _preflight(config, selected, root):
    for window, seal in selected:
        sealed_config(config, window, seal, config.output_path/"samples"/window.window_id,
            root/"preflight"/window.window_id, "vpin")


def run(config_path, catalogue_path, p0_report, output):
    config = load_backtest_config(config_path)
    if config.clock_mode != "recorded":
        raise ValueError("Study 1 completion requires recorded clock")
    code = _code_fingerprint(ROOT)
    catalogue = json.loads(Path(catalogue_path).read_text(encoding="utf-8"))
    development = development_windows(catalogue)
    prerequisite = validate_p0(p0_report, catalogue_path)
    p0 = json.loads(Path(p0_report).read_text(encoding="utf-8"))
    study = Path(output).resolve()
    _preflight(config, development, study)
    study.mkdir(parents=True, exist_ok=False)
    input_protocol = {"version": "study1-completion-input-v1", "code_fingerprint": code,
        "catalogue_sha256": catalogue["catalogue_sha256"],
        "p0_report_sha256": sha256_file(p0_report), "p0_code_fingerprint": p0["code_fingerprint"],
        "p0_acceptance": {"status": prerequisite["status"],
            "cache_checksums_verified": prerequisite["cache_checksums_verified"]},
        "base_config": config_document(config),
        "window_ids": [window.window_id for window, _ in development],
        "fit_role": DEVELOPMENT, "outcomes_used_for_mean": False,
        "mean_fit_grid_ms": config.decision_ms,
        "feature_prerequisite": "100ms causal VPIN plus exact 250ms execution-grid mean fit"}
    input_protocol["protocol_sha256"] = digest(input_protocol)
    write_json(study/"input_protocol.json", input_protocol)
    performance = []
    try:
        feature_rows, mean_rows, feature_performance = [], [], []
        for window, seal in development:
            feature = _isolated(prerequisite_phase, config, window, seal,
                p0["freshness_policy"], study)
            feature_rows.append(feature["window"])
            item = _performance(feature, window, "vpin_100ms_prerequisite")
            feature_performance.append(item); performance.append(item)
        for window, seal in development:
            fitted = _isolated(vpin_mean_phase, config, window, seal,
                config.output_path/"samples"/window.window_id, study)
            mean_rows.append(fitted["window"])
            performance.append(_performance(fitted, window, "vpin_250ms_mean_fit"))
        feature_report = {"status": "PASS", "input_protocol_sha256": input_protocol["protocol_sha256"],
            "code_fingerprint": code, "per_window": feature_rows,
            "summary": summarize_prerequisite(feature_rows), "performance": feature_performance}
        write_json(study/"vpin_prerequisite.json", feature_report)
        mean_report = pooled_vpin_mean(mean_rows)
        mean_report["per_window"] = mean_rows
        write_json(study/"vpin_mean_fit.json", mean_report)
        mean = mean_report["mean"]
        protocol = {"version": "study1-completion-v1", "code_fingerprint": code,
            "input_protocol_sha256": input_protocol["protocol_sha256"],
            "vpin_prerequisite_sha256": sha256_file(study/"vpin_prerequisite.json"),
            "vpin_mean_fit_sha256": sha256_file(study/"vpin_mean_fit.json"),
            "mean_vpin": mean, "base_config": config_document(config),
            "development_window_ids": input_protocol["window_ids"],
            "decomposition_cases": {k: v.resolved() for k, v in decomposition_configs(config, mean).items()},
            "combined_cases": {k: v.resolved() for k, v in combined_configs(config).items()},
            "robustness_scenarios": ROBUSTNESS_SCENARIOS,
            "development_gate": {"baseline": "MM-4 minus MM-1 net PnL positive",
                "dates": "positive on both DEVELOPMENT dates",
                "scenarios": "positive in at least four of six including joint stress",
                "drawdown": "mean window max drawdown not worse"},
            "validation_gate": {"baseline": "pooled MM-4 delta positive",
                "windows": "positive in at least three of four", "joint": "joint stress delta positive",
                "drawdown": "mean window max drawdown not worse"},
            "roles_initially_opened": [DEVELOPMENT], "locked_roles_opened_before_gate": [],
            "selection_rule": "fixed cases and gates; no outcome-based parameter replacement",
            "statistical_significance": "NOT_CLAIMED", "paper_eligibility": "INSUFFICIENT_EVIDENCE"}
        protocol["protocol_sha256"] = digest(protocol)
        write_json(study/"execution_protocol.json", protocol)

        decomposition, perf = _run_rows(config, development, study, "decomposition",
            DECOMPOSITION_CASES, code, mean)
        performance.extend(perf)
        decomposition_summary = summarize_rows(decomposition, DECOMPOSITION_CASES)
        write_json(study/"decomposition_report.json", decomposition_summary)

        combined, perf = _run_rows(config, development, study, "combined",
            COMBINED_CASES, code, mean)
        performance.extend(perf)
        combined_summary = summarize_rows(combined, COMBINED_CASES)
        write_json(study/"combined_report.json", combined_summary)

        robustness = {"baseline": summarize_rows(combined, PRIMARY_CASES)}
        robustness_rows = {"baseline": combined}
        for scenario in ROBUSTNESS_SCENARIOS:
            rows, perf = _run_rows(config, development, study, "robustness",
                PRIMARY_CASES, code, mean, scenario)
            performance.extend(perf)
            robustness_rows[scenario] = rows
            robustness[scenario] = summarize_rows(rows, PRIMARY_CASES)
        write_json(study/"robustness_report.json", robustness)
        dev_gate = development_gate(combined_summary, robustness)
        write_json(study/"development_gate.json", dev_gate)

        roles_opened = [DEVELOPMENT]
        validation_report = validation_gate_report = oos_report = None
        if dev_gate["status"] == "PASS":
            selected = role_windows(catalogue, VALIDATION_LOCKED)
            _preflight(config, selected, study)
            roles_opened.append(VALIDATION_LOCKED)
            validation_scenarios = {}
            for scenario in ("baseline", "joint_fee_latency_queue"):
                rows, perf = _run_rows(config, selected, study, "validation",
                    VALIDATION_CASES, code, mean, scenario)
                performance.extend(perf)
                validation_scenarios[scenario] = summarize_rows(rows, VALIDATION_CASES)
            validation_report = validation_scenarios
            write_json(study/"validation_report.json", validation_report)
            validation_gate_report = validation_gate(validation_scenarios["baseline"],
                validation_scenarios["joint_fee_latency_queue"])
            write_json(study/"validation_gate.json", validation_gate_report)
            if validation_gate_report["status"] == "PASS":
                selected = role_windows(catalogue, PILOT_OOS_LOCKED)
                _preflight(config, selected, study)
                roles_opened.append(PILOT_OOS_LOCKED)
                rows, perf = _run_rows(config, selected, study, "oos",
                    VALIDATION_CASES, code, mean)
                performance.extend(perf)
                oos_report = summarize_rows(rows, VALIDATION_CASES)
                write_json(study/"oos_report.json", oos_report)
        report = {"status": "PASS", "protocol": protocol,
            "decomposition": decomposition_summary, "combined": combined_summary,
            "robustness": robustness, "development_gate": dev_gate,
            "validation": validation_report, "validation_gate": validation_gate_report,
            "pilot_oos": oos_report, "roles_opened": roles_opened,
            "roles_kept_locked": [role for role in (VALIDATION_LOCKED, PILOT_OOS_LOCKED)
                if role not in roles_opened], "performance": performance,
            "promotion_decision": "RESEARCH_ONLY", "paper_eligibility": "INSUFFICIENT_EVIDENCE"}
        write_json(study/"study1_completion_report.json", report)
        write_json(study/"progress.json", {"status": "PASS", "completed": performance})
        return report
    except BaseException as exc:
        write_json(study/"progress.json", {"status": "FAIL", "completed": performance,
            "error": f"{type(exc).__name__}: {exc}"})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/backtest/binance_recorded.yaml")
    parser.add_argument("--catalogue", required=True)
    parser.add_argument("--p0-report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run(args.config, args.catalogue, args.p0_report, args.output)
    print(json.dumps({"status": result["status"], "path": args.output,
        "roles_opened": result["roles_opened"]}, sort_keys=True))
