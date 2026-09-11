#!/usr/bin/env python3
"""Build causal caches and DEVELOPMENT-only predictive evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from lobcap.backtest.replay import iter_events, load_sample, write_json
from lobcap.backtest.research_flow.feature_cache import (
    iter_feature_snapshots, read_feature_cache, write_feature_cache,
)
from lobcap.backtest.research_flow.labels import (
    freeze_development_freshness, raw_label_cadence, truth_midpoint_series,
)
from lobcap.backtest.research_flow.sample_catalogue import (
    CATALOGUE_VERSION, DEVELOPMENT, CatalogueWindow, assert_outcomes_allowed,
)
from lobcap.backtest.research_flow.signal_analysis import (
    evaluate_feature, evaluate_feature_group, fit_bucket_edges,
    evaluate_redundancy_values, stability_summary,
)
from lobcap.backtest.research_flow.signal_contracts import (
    CLIENT_FORWARD_RETURN, MARKET_TRUTH_RETURN, POST_LATENCY_RETURN,
    AnalysisSchedule, LabelDefinition,
)
from lobcap.backtest.types import jsonable
from lobcap.config.backtest import load_backtest_config


FEATURES = ("microprice_minus_mid_ticks", "imbalance_1", "imbalance_5",
            "vamp_5_minus_mid_ticks", "trade_volume_imbalance_1000ms",
            "bbo_ofi_1000ms", "mid_return_1000ms")
HORIZONS = (100_000_000, 500_000_000, 1_000_000_000, 5_000_000_000)


def _definitions(entry_latency_ns):
    rows = []
    for horizon in HORIZONS:
        rows.extend((LabelDefinition(MARKET_TRUTH_RETURN, horizon),
                     LabelDefinition(CLIENT_FORWARD_RETURN, horizon),
                     LabelDefinition(POST_LATENCY_RETURN, horizon,
                                     entry_latency_ns=entry_latency_ns)))
    return tuple(rows)


def _window(row):
    return CatalogueWindow(**row["window"])


def _code_fingerprint(root):
    result = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                            text=True, capture_output=True, check=True)
    if result.stdout.strip():
        raise ValueError("development study requires a clean code checkout")
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True,
                          capture_output=True, check=True).stdout.strip()


def _catalogue(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    digest = payload.pop("catalogue_sha256", None)
    actual = hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    if payload.get("catalogue_version") != CATALOGUE_VERSION or digest != actual:
        raise ValueError("catalogue fingerprint mismatch")
    payload["catalogue_sha256"] = digest
    return payload


def _cache_identity(manifest, tick, code):
    return {"tick": tick, "bootstrap_receive_ns": manifest["bootstrap_receive_ns"],
        "acquisition_segment": manifest["acquisition_segments"][0],
        "normalization_version": manifest["sample"]["normalization_version"],
        "source_schema_version": manifest["source_schema_version"],
        "code_fingerprint": code}


def _bucket_id(feature, fit_dates, edges):
    payload = {"feature": feature, "fit_dates": sorted(fit_dates),
               "edges": [str(value) for value in edges], "method": "date-crossfit-quintile-v1"}
    return "bucket-"+hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()[:16]


def run(config_path, catalogue_path, output=None, entry_latency_ms=10):
    config = load_backtest_config(config_path)
    catalogue = _catalogue(catalogue_path)
    root = Path(__file__).resolve().parents[1]
    code = _code_fingerprint(root)
    study = Path(output) if output else config.output_path/"research"/"signal-development-v1"
    study.mkdir(parents=True, exist_ok=False)
    selected = []
    for row in catalogue["windows"]:
        window = _window(row)
        if window.role != DEVELOPMENT or row["quality_status"] != "PASS":
            continue
        assert_outcomes_allowed(window)
        selected.append((window, row))
    if len({window.date for window, _ in selected}) < 2:
        raise ValueError("two DEVELOPMENT dates with PASS windows are required")
    definitions = _definitions(entry_latency_ms*1_000_000)
    cache_rows = {}
    cadence = {}
    contexts = {}
    for window, seal in selected:
        sample_path = config.output_path/"samples"/window.window_id
        initial, manifest = load_sample(sample_path)
        if manifest["sample_sha256"] != seal["sample_sha256"]:
            raise ValueError("catalogue/sample hash mismatch")
        schedule = AnalysisSchedule(window.start_ns, window.end_ns, 100_000_000)
        identity = _cache_identity(manifest, config.instrument.tick, code)
        cache_path = study/"feature_cache"/window.window_id
        snapshots = iter_feature_snapshots(initial, iter_events(sample_path), schedule,
            config.instrument.tick, clock_mode="recorded",
            bootstrap_receive_ns=manifest["bootstrap_receive_ns"],
            acquisition_segment=manifest["acquisition_segments"][0])
        cache_manifest = write_feature_cache(cache_path, snapshots, manifest["sample_sha256"],
            schedule, "recorded", 500_000_000, **identity)
        cache_rows[window.window_id] = {"path": str(cache_path),
            "cache_key": cache_manifest["cache_key"], "date": window.date}
        lean = tuple(read_feature_cache(cache_path, cache_manifest["cache_key"], ()))
        truth = truth_midpoint_series(initial, iter_events(sample_path),
                                      manifest["acquisition_segments"][0])
        cadence[window.window_id] = raw_label_cadence(lean, truth, definitions,
            config.instrument.tick, window.end_ns)
        contexts[window.window_id] = (truth, window.end_ns)
    development_windows = tuple(window for window, _ in selected)
    freshness, freshness_evidence = freeze_development_freshness(cadence, development_windows)
    write_json(study/"freshness_policy.json", freshness_evidence)
    reports = {"per_window": {}, "per_date": {}, "pooled": {}}
    for feature in FEATURES:
        feature_series = {}
        for window, _ in selected:
            cache = cache_rows[window.window_id]
            feature_series[window.window_id] = tuple(read_feature_cache(
                cache["path"], cache["cache_key"], (feature,)))
        dates = sorted({window.date for window, _ in selected})
        fitted = {}
        for date in dates:
            training = tuple(snapshot for window, _ in selected if window.date != date
                for snapshot in feature_series[window.window_id])
            edges = fit_bucket_edges(training, feature)
            fitted[date] = (edges, _bucket_id(feature, set(dates)-{date}, edges))
        for window, _ in selected:
            truth, end = contexts[window.window_id]
            edges, policy_id = fitted[window.date]
            reports["per_window"].setdefault(window.window_id, {})[feature] = evaluate_feature(
                feature, feature_series[window.window_id], truth, definitions,
                freshness, config.instrument.tick, end, edges, policy_id)
        for date in dates:
            grouped = tuple((feature_series[window.window_id], *contexts[window.window_id])
                            for window, _ in selected if window.date == date)
            edges, policy_id = fitted[date]
            reports["per_date"].setdefault(date, {})[feature] = evaluate_feature_group(
                feature, grouped, definitions, freshness, config.instrument.tick,
                edges, policy_id)
        grouped = tuple((feature_series[window.window_id], *contexts[window.window_id])
                        for window, _ in selected)
        reports["pooled"][feature] = evaluate_feature_group(feature, grouped, definitions,
            freshness, config.instrument.tick)
    del feature_series
    compact_rows = []
    for window, _ in selected:
        cache = cache_rows[window.window_id]
        for snapshot in read_feature_cache(cache["path"], cache["cache_key"], FEATURES):
            points = {point.feature_name: point for point in snapshot.points}
            compact_rows.append(tuple(points[name].value
                if points[name].status == "AVAILABLE" else None for name in FEATURES))
    redundancy = evaluate_redundancy_values(compact_rows, FEATURES)
    stability = stability_summary(reports["per_date"])
    output_row = {"status": "PASS", "study_version": "signal-development-v1",
        "code_fingerprint": code, "catalogue_sha256": catalogue["catalogue_sha256"],
        "roles_opened": [DEVELOPMENT], "locked_roles_opened": [],
        "entry_latency_scenario_ms": entry_latency_ms, "features": FEATURES,
        "label_horizons_ns": HORIZONS, "feature_caches": cache_rows,
        "freshness_policy": jsonable(freshness), "reports": reports,
        "redundancy": redundancy, "stability": stability,
        "statistical_significance": "NOT_CLAIMED",
        "paper_eligibility": "INSUFFICIENT_EVIDENCE"}
    write_json(study/"development_report.json", output_row)
    return study, output_row


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/backtest/binance_recorded.yaml")
    parser.add_argument("--catalogue", required=True)
    parser.add_argument("--output")
    parser.add_argument("--entry-latency-ms", type=int, default=10)
    args = parser.parse_args(argv)
    path, row = run(args.config, args.catalogue, args.output, args.entry_latency_ms)
    print(json.dumps({"path": str(path), "status": row["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
