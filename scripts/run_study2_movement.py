#!/usr/bin/env python3
"""Run sealed DEVELOPMENT Study 2 B1 movement and excursion research."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_signal_development import _code_fingerprint, _isolated
from validate_signal_development import validate as validate_p0
from lobcap.backtest.replay import write_json
from lobcap.backtest.research_flow.signal_study import development_windows, digest
from lobcap.backtest.research_flow.study1 import sealed_config
from lobcap.backtest.research_flow.study2_movement import (
    GRID_NS, REGIME_VERSION, aggregate_lifetimes,
    aggregate_movement_phase, fit_regime_edges, movement_phase, regime_input_phase,
    movement_protocol, spacing_candidates, study1_lifetime_rows,
)
from lobcap.backtest.runner import config_document
from lobcap.config.backtest import load_backtest_config
from lobcap.config.common import sha256_file
from lobcap.config.experiment import ROOT


def _performance(result, window_id, phase):
    return {"phase": phase, "window_id": window_id,
        "wall_seconds": result["wall_seconds"], "peak_rss_mib": result["peak_rss_mib"]}


def run(config_path, catalogue_path, p0_report, study1_report, output):
    config = load_backtest_config(config_path)
    if config.clock_mode != "recorded":
        raise ValueError("Study 2 movement requires recorded clock")
    code = _code_fingerprint(ROOT)
    catalogue = json.loads(Path(catalogue_path).read_text(encoding="utf-8"))
    selected = development_windows(catalogue)
    prerequisite = validate_p0(p0_report, catalogue_path)
    p0 = json.loads(Path(p0_report).read_text(encoding="utf-8"))
    study = Path(output).resolve()
    for window, seal in selected:
        sealed_config(config, window, seal, config.output_path/"samples"/window.window_id,
            study/window.window_id, "vpin")
    # Validate and freeze the reused order source before opening new outcomes.
    lifetime_rows = study1_lifetime_rows(study1_report, selected)
    input_protocol = {"version": "study2-movement-input-v1", "code_fingerprint": code,
        "catalogue_sha256": catalogue["catalogue_sha256"],
        "p0_report_sha256": sha256_file(p0_report), "p0_code_fingerprint": p0["code_fingerprint"],
        "p0_acceptance": {"status": prerequisite["status"],
            "cache_checksums_verified": prerequisite["cache_checksums_verified"]},
        "study1_report_sha256": sha256_file(study1_report),
        "study1_lifetime_source": "accepted VPIN-study MM-1 orders; simulator not rerun",
        "base_config": config_document(config),
        "window_ids": [window.window_id for window, _ in selected],
        "roles_opened": ["DEVELOPMENT"], "locked_roles_opened": [],
        "regime_feature": "abs(mid_return_1000ms) in bps", "regime_fit": REGIME_VERSION,
        "grid_ns": GRID_NS, "future_movement_used_for_regime_fit": False}
    input_protocol["protocol_sha256"] = digest(input_protocol)
    study.mkdir(parents=True, exist_ok=False)
    write_json(study/"input_protocol.json", input_protocol)
    performance = []
    try:
        input_rows = []
        for window, seal in selected:
            result = _isolated(regime_input_phase, config, window, seal,
                config.output_path/"samples"/window.window_id, study)
            input_rows.append(result["window"])
            performance.append(_performance(result, window.window_id, "regime_input"))
        input_paths = [study/"regime_inputs"/(window.window_id+".jsonl.gz")
            for window, _ in selected]
        regime_fit = fit_regime_edges(input_paths)
        input_report = {"status": "PASS", "protocol_sha256": input_protocol["protocol_sha256"],
            "code_fingerprint": code, "per_window": input_rows, "fit": regime_fit}
        write_json(study/"regime_input_report.json", input_report)
        protocol = movement_protocol(code, input_protocol["protocol_sha256"],
            sha256_file(study/"regime_input_report.json"), regime_fit["edges_bps"])
        write_json(study/"movement_protocol.json", protocol)
        movement_rows = []
        for window, seal in selected:
            result = _isolated(movement_phase, config, window, seal,
                config.output_path/"samples"/window.window_id, p0["freshness_policy"],
                regime_fit["edges_bps"], study)
            movement_rows.append(result["window"])
            performance.append(_performance(result, window.window_id, "movement_excursion"))
        artifacts = [(window.date, study/"movement"/(window.window_id+".jsonl.gz"))
            for window, _ in selected]
        aggregate = _isolated(aggregate_movement_phase, artifacts)
        performance.append(_performance(aggregate, "all-development", "movement_aggregation"))
        movement_summary = aggregate["summary"]
        lifetime_summary = aggregate_lifetimes(lifetime_rows)
        spacing = spacing_candidates(movement_summary, lifetime_summary)
        report = {"status": "PASS", "protocol": protocol,
            "regime_input": input_report, "movement_windows": movement_rows,
            "movement": movement_summary, "order_lifetimes": {"per_window": lifetime_rows,
                "pooled": lifetime_summary}, "spacing_candidates": spacing,
            "performance": performance, "roles_opened": ["DEVELOPMENT"],
            "locked_roles_opened": [], "study2_phase": "B1",
            "next_gate": "review geometry and execution exposure before multi-order engine work",
            "paper_eligibility": "INSUFFICIENT_EVIDENCE"}
        write_json(study/"study2_movement_report.json", report)
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
    parser.add_argument("--study1-report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run(args.config, args.catalogue, args.p0_report, args.study1_report, args.output)
    print(json.dumps({"status": result["status"], "path": args.output,
        "selected_horizon_ns": result["spacing_candidates"]["selected_horizon_ns"]}, sort_keys=True))
