#!/usr/bin/env python3
"""Source-replay and rebuild the Study 2 B1 movement research report."""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
import math
from pathlib import Path

from validate_signal_development import validate as validate_p0
from lobcap.backtest.research_flow.signal_study import development_windows, digest
from lobcap.backtest.research_flow.study1 import sealed_config
from lobcap.backtest.research_flow.study2_movement import (
    GRID_NS, HORIZONS, QUANTILES, REGIME_VERSION, VERSION, aggregate_lifetimes,
    aggregate_movement, fit_regime_edges, movement_window, regime_input_window,
    spacing_candidates, study1_lifetime_rows,
)
from lobcap.backtest.workspace.contracts import config_from_document
from lobcap.config.common import sha256_file


def validate(report_path, catalogue_path, p0_report, study1_report):
    report_path = Path(report_path).resolve(); root = report_path.parent
    report = json.loads(report_path.read_text(encoding="utf-8"))
    catalogue = json.loads(Path(catalogue_path).read_text(encoding="utf-8"))
    input_protocol = json.loads((root/"input_protocol.json").read_text(encoding="utf-8"))
    protocol = json.loads((root/"movement_protocol.json").read_text(encoding="utf-8"))
    if (report.get("status") != "PASS" or report.get("protocol") != protocol
        or protocol.get("version") != VERSION
        or protocol.get("protocol_sha256") != digest({k: v for k, v in protocol.items()
            if k != "protocol_sha256"})
        or input_protocol.get("version") != "study2-movement-input-v1"
        or input_protocol.get("protocol_sha256") != digest({k: v for k, v in input_protocol.items()
            if k != "protocol_sha256"})
        or protocol.get("input_protocol_sha256") != input_protocol["protocol_sha256"]
        or input_protocol.get("catalogue_sha256") != catalogue["catalogue_sha256"]
        or input_protocol.get("p0_report_sha256") != sha256_file(p0_report)
        or input_protocol.get("study1_report_sha256") != sha256_file(study1_report)
        or input_protocol.get("roles_opened") != ["DEVELOPMENT"]
        or input_protocol.get("locked_roles_opened") != []
        or protocol.get("roles_opened") != ["DEVELOPMENT"]
        or protocol.get("locked_roles_opened") != []
        or report.get("roles_opened") != ["DEVELOPMENT"]
        or report.get("locked_roles_opened") != []):
        raise ValueError("invalid Study 2 movement protocol provenance")
    validate_p0(p0_report, catalogue_path)
    config = config_from_document(input_protocol["base_config"])
    if config.clock_mode != "recorded":
        raise ValueError("Study 2 movement base configuration drift")
    selected = development_windows(catalogue)
    expected = [window.window_id for window, _ in selected]
    if input_protocol["window_ids"] != expected:
        raise ValueError("Study 2 DEVELOPMENT window selection changed")
    input_report_path = root/"regime_input_report.json"
    input_report = json.loads(input_report_path.read_text(encoding="utf-8"))
    if (protocol["regime_input_report_sha256"] != sha256_file(input_report_path)
        or input_report.get("status") != "PASS"
        or input_report.get("protocol_sha256") != input_protocol["protocol_sha256"]
        or input_report.get("code_fingerprint") != protocol["code_fingerprint"]
        or [row["window_id"] for row in input_report["per_window"]] != expected):
        raise ValueError("Study 2 regime-input report mismatch")
    rebuilt_inputs = []
    for window, seal in selected:
        sample = config.output_path/"samples"/window.window_id
        sealed = sealed_config(config, window, seal, sample, root, "vpin")
        artifact = root/"regime_inputs"/(window.window_id+".jsonl.gz")
        row = regime_input_window(sealed, sample, artifact, False)
        row.update(window_id=window.window_id, date=window.date,
            sample_sha256=seal["sample_sha256"])
        rebuilt_inputs.append(row)
    if rebuilt_inputs != input_report["per_window"]:
        raise ValueError("Study 2 regime inputs differ from causal source replay")
    input_paths = [root/"regime_inputs"/(window.window_id+".jsonl.gz")
        for window, _ in selected]
    regime_fit = fit_regime_edges(input_paths)
    if (regime_fit != input_report["fit"] or regime_fit["version"] != REGIME_VERSION
        or regime_fit["edges_bps"] != protocol["regime_edges_bps"]
        or protocol["grid_ns"] != GRID_NS or tuple(protocol["horizons_ns"]) != HORIZONS
        or tuple(Decimal(str(value)) for value in protocol["quantiles"]) != QUANTILES):
        raise ValueError("Study 2 regime fit or frozen dimensions changed")
    freshness_row = json.loads(Path(p0_report).read_text(encoding="utf-8"))["freshness_policy"]
    from lobcap.backtest.research_flow.signal_contracts import FreshnessPolicy
    freshness = FreshnessPolicy(freshness_row["policy_id"],
        tuple(tuple(row) for row in freshness_row["maximum_age_by_horizon_ns"]))
    movement_rows = []
    for window, seal in selected:
        sample = config.output_path/"samples"/window.window_id
        sealed = sealed_config(config, window, seal, sample, root, "vpin")
        artifact = root/"movement"/(window.window_id+".jsonl.gz")
        row = movement_window(sealed, sample, freshness, regime_fit["edges_bps"], artifact, False)
        row.update(window_id=window.window_id, date=window.date)
        movement_rows.append(row)
    if movement_rows != report["movement_windows"]:
        raise ValueError("Study 2 movement artifacts differ from source replay")
    artifacts = [(window.date, root/"movement"/(window.window_id+".jsonl.gz"))
        for window, _ in selected]
    movement = aggregate_movement(artifacts)
    if movement != report["movement"]:
        raise ValueError("Study 2 movement aggregation mismatch")
    lifetime_rows = study1_lifetime_rows(study1_report, selected)
    lifetimes = aggregate_lifetimes(lifetime_rows)
    if (lifetime_rows != report["order_lifetimes"]["per_window"]
        or lifetimes != report["order_lifetimes"]["pooled"]):
        raise ValueError("Study 2 MM-1 lifetime source or aggregation mismatch")
    spacing = spacing_candidates(movement, lifetimes)
    if spacing != report["spacing_candidates"]:
        raise ValueError("Study 2 spacing candidates differ from frozen calculation")
    phases = report["performance"]
    if (len(phases) != 2*len(selected)+1
        or any(type(row.get(key)) not in (int, float) or isinstance(row[key], bool)
            or not math.isfinite(row[key]) or row[key] <= 0
            for row in phases for key in ("wall_seconds", "peak_rss_mib"))
        or max(row["peak_rss_mib"] for row in phases) >= 512):
        raise ValueError("invalid Study 2 movement performance evidence")
    return {"status": "PASS", "validation_version": "study2-movement-acceptance-v1",
        "runtime_code": protocol["code_fingerprint"], "report_sha256": sha256_file(report_path),
        "protocol_sha256": protocol["protocol_sha256"], "windows": len(selected),
        "movement_rows": sum(row["observations"] for row in movement_rows),
        "source_replayed": True, "reused_mm1_order_artifacts": True,
        "selected_horizon_ns": spacing["selected_horizon_ns"],
        "locked_roles_opened": [], "maximum_phase_rss_mib": max(
            row["peak_rss_mib"] for row in phases),
        "scope": "input/movement source replay and immutable MM-1 order analysis; no ladder execution"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--catalogue", required=True)
    parser.add_argument("--p0-report", required=True)
    parser.add_argument("--study1-report", required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.report, args.catalogue, args.p0_report,
        args.study1_report), sort_keys=True))
