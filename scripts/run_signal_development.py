#!/usr/bin/env python3
"""Build causal caches and DEVELOPMENT-only predictive evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
from pathlib import Path
import queue as queue_module
import subprocess
import sys
import traceback

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
CONTROL_FEATURES = ("mid", "spread_ticks", "bid_top_depth", "ask_top_depth",
                    "total_top_depth", "book_age_ns", "feed_delay_ns")
CACHE_FEATURES = FEATURES + CONTROL_FEATURES
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
        "code_fingerprint": code, "feature_names": CACHE_FEATURES}


def _bucket_id(feature, fit_dates, edges):
    payload = {"feature": feature, "fit_dates": sorted(fit_dates),
               "edges": [str(value) for value in edges], "method": "date-crossfit-quintile-v1"}
    return "bucket-"+hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()[:16]


def _peak_rss_mib():
    if not sys.platform.startswith("linux"):
        return None
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024


def _phase_entry(queue, target, args):
    try:
        result = target(*args)
        result["peak_rss_mib"] = _peak_rss_mib()
        queue.put(("PASS", result))
    except BaseException as exc:
        queue.put(("FAIL", {"error": f"{type(exc).__name__}: {exc}",
                            "traceback": traceback.format_exc(),
                            "peak_rss_mib": _peak_rss_mib()}))


def _isolated(target, *args):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_phase_entry, args=(queue, target, args))
    process.start()
    while True:
        try:
            status, result = queue.get(timeout=1)
            break
        except queue_module.Empty:
            if not process.is_alive():
                process.join()
                raise RuntimeError(f"isolated phase exited {process.exitcode} without evidence")
    process.join()
    if status != "PASS" or process.exitcode:
        raise RuntimeError(result)
    if result.get("peak_rss_mib") is not None and result["peak_rss_mib"] >= 512:
        raise MemoryError(f"isolated phase exceeded 512 MiB RSS: {result}")
    return result


def _cache_phase(config, window, seal, study, code):
    sample_path = config.output_path/"samples"/window.window_id
    initial, manifest = load_sample(sample_path)
    if manifest["sample_sha256"] != seal["sample_sha256"]:
        raise ValueError("catalogue/sample hash mismatch")
    schedule = AnalysisSchedule(window.start_ns, window.end_ns, 100_000_000)
    identity = _cache_identity(manifest, config.instrument.tick, code)
    cache_path = study/"feature_cache"/window.window_id
    snapshots = iter_feature_snapshots(initial, lambda: iter_events(sample_path), schedule,
        config.instrument.tick, clock_mode="recorded",
        bootstrap_receive_ns=manifest["bootstrap_receive_ns"],
        acquisition_segment=manifest["acquisition_segments"][0],
        feature_names=CACHE_FEATURES)
    cache_manifest = write_feature_cache(cache_path, snapshots, manifest["sample_sha256"],
        schedule, "recorded", 500_000_000, **identity)
    segment = manifest["acquisition_segments"][0]
    return {"cache": {"path": str(cache_path), "cache_key": cache_manifest["cache_key"],
                      "date": window.date},
            "sample": {"path": str(sample_path), "segment": segment}}


def _cadence_phase(config, window, cache, sample, definitions):
    lean = tuple(read_feature_cache(cache["path"], cache["cache_key"], ()))
    initial, _ = load_sample(sample["path"])
    truth = truth_midpoint_series(initial, iter_events(sample["path"]), sample["segment"])
    cadence = raw_label_cadence(lean, truth, definitions, config.instrument.tick,
                                window.end_ns)
    return {"cadence": cadence}


def _feature_phase(config, windows, cache_rows, sample_rows, definitions, freshness, feature):
    feature_series, truth_by_window = {}, {}
    for window in windows:
        cache = cache_rows[window.window_id]
        feature_series[window.window_id] = tuple(read_feature_cache(
            cache["path"], cache["cache_key"], (feature,)))
        sample = sample_rows[window.window_id]
        initial, _ = load_sample(sample["path"])
        truth_by_window[window.window_id] = truth_midpoint_series(initial,
            iter_events(sample["path"]), sample["segment"])
    dates = sorted({window.date for window in windows})
    fitted = {}
    for date in dates:
        training = tuple(snapshot for window in windows if window.date != date
            for snapshot in feature_series[window.window_id])
        edges = fit_bucket_edges(training, feature)
        fitted[date] = (edges, _bucket_id(feature, set(dates)-{date}, edges))
    per_window, per_date = {}, {}
    for window in windows:
        edges, policy_id = fitted[window.date]
        per_window[window.window_id] = evaluate_feature(feature,
            feature_series[window.window_id], truth_by_window[window.window_id], definitions,
            freshness, config.instrument.tick, window.end_ns, edges, policy_id)
    for date in dates:
        grouped = tuple((feature_series[window.window_id], truth_by_window[window.window_id],
                         window.end_ns) for window in windows if window.date == date)
        edges, policy_id = fitted[date]
        per_date[date] = evaluate_feature_group(feature, grouped, definitions, freshness,
            config.instrument.tick, edges, policy_id)
    grouped = tuple((feature_series[window.window_id], truth_by_window[window.window_id],
                     window.end_ns) for window in windows)
    pooled = evaluate_feature_group(feature, grouped, definitions, freshness,
                                    config.instrument.tick)
    return {"feature": feature, "per_window": per_window,
            "per_date": per_date, "pooled": pooled}


def _redundancy_phase(windows, cache_rows):
    compact_rows = []
    for window in windows:
        cache = cache_rows[window.window_id]
        for snapshot in read_feature_cache(cache["path"], cache["cache_key"], FEATURES):
            points = {point.feature_name: point for point in snapshot.points}
            compact_rows.append(tuple(points[name].value
                if points[name].status == "AVAILABLE" else None for name in FEATURES))
    return {"redundancy": evaluate_redundancy_values(compact_rows, FEATURES)}


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
    sample_rows = {}
    phase_metrics = []
    for window, seal in selected:
        result = _isolated(_cache_phase, config, window, seal, study, code)
        cache_rows[window.window_id] = result["cache"]
        sample_rows[window.window_id] = result["sample"]
        phase_metrics.append({"phase": "cache_write", "id": window.window_id,
                              "peak_rss_mib": result["peak_rss_mib"]})
        measured = _isolated(_cadence_phase, config, window, result["cache"],
                             result["sample"], definitions)
        cadence[window.window_id] = measured["cadence"]
        phase_metrics.append({"phase": "cadence", "id": window.window_id,
                              "peak_rss_mib": measured["peak_rss_mib"]})
    development_windows = tuple(window for window, _ in selected)
    freshness, freshness_evidence = freeze_development_freshness(cadence, development_windows)
    write_json(study/"freshness_policy.json", freshness_evidence)
    reports = {"per_window": {}, "per_date": {}, "pooled": {}}
    windows = tuple(window for window, _ in selected)
    for feature in FEATURES:
        result = _isolated(_feature_phase, config, windows, cache_rows, sample_rows,
                           definitions, freshness, feature)
        for window_id, rows in result["per_window"].items():
            reports["per_window"].setdefault(window_id, {})[feature] = rows
        for date, rows in result["per_date"].items():
            reports["per_date"].setdefault(date, {})[feature] = rows
        reports["pooled"][feature] = result["pooled"]
        phase_metrics.append({"phase": "feature", "id": feature,
                              "peak_rss_mib": result["peak_rss_mib"]})
    result = _isolated(_redundancy_phase, windows, cache_rows)
    redundancy = result["redundancy"]
    phase_metrics.append({"phase": "redundancy", "id": "pooled",
                          "peak_rss_mib": result["peak_rss_mib"]})
    stability = stability_summary(reports["per_date"])
    output_row = {"status": "PASS", "study_version": "signal-development-v1",
        "code_fingerprint": code, "catalogue_sha256": catalogue["catalogue_sha256"],
        "roles_opened": [DEVELOPMENT], "locked_roles_opened": [],
        "entry_latency_scenario_ms": entry_latency_ms, "features": FEATURES,
        "label_horizons_ns": HORIZONS, "feature_caches": cache_rows,
        "freshness_policy": jsonable(freshness), "reports": reports,
        "redundancy": redundancy, "stability": stability,
        "phase_performance": phase_metrics,
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
