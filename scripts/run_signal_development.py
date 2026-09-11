#!/usr/bin/env python3
"""Build causal caches and DEVELOPMENT-only predictive evidence."""
from __future__ import annotations

import argparse
import json
import multiprocessing
from pathlib import Path
import queue as queue_module
import subprocess
import sys
import time
import traceback

from lobcap.backtest.replay import iter_events, load_sample, write_json
from lobcap.backtest.research_flow.feature_cache import (
    iter_feature_snapshots, read_feature_cache, write_feature_cache,
)
from lobcap.backtest.research_flow.labels import (
    freeze_development_freshness, raw_label_cadence, truth_midpoint_series,
)
from lobcap.backtest.research_flow.sample_catalogue import DEVELOPMENT
from lobcap.backtest.research_flow.signal_analysis import (
    evaluate_feature_scopes, fit_bucket_edges,
    evaluate_redundancy_values, stability_summary,
)
from lobcap.backtest.research_flow.signal_contracts import AnalysisSchedule
from lobcap.backtest.types import jsonable
from lobcap.backtest.research_flow.signal_study import (
    FEATURES, CACHE_FEATURES, HORIZONS, FEATURE_GROUPS, definitions,
    development_windows, bucket_id, validate_report,
)
from lobcap.config.backtest import load_backtest_config


def _definitions(entry_latency_ns):
    return definitions(entry_latency_ns)


def _code_fingerprint(root):
    result = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                            text=True, capture_output=True, check=True)
    if result.stdout.strip():
        raise ValueError("development study requires a clean code checkout")
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True,
                          capture_output=True, check=True).stdout.strip()


def _catalogue(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    development_windows(payload)
    return payload


def _cache_identity(manifest, tick, code):
    return {"tick": tick, "bootstrap_receive_ns": manifest["bootstrap_receive_ns"],
        "acquisition_segment": manifest["acquisition_segments"][0],
        "normalization_version": manifest["sample"]["normalization_version"],
        "source_schema_version": manifest["source_schema_version"],
        "code_fingerprint": code, "feature_names": CACHE_FEATURES}


def _bucket_id(feature, fit_dates, edges):
    return bucket_id(feature, fit_dates, edges)


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
    started = time.monotonic()
    print(json.dumps({"phase": target.__name__, "status": "STARTED"}), flush=True)
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
    result["wall_seconds"] = time.monotonic()-started
    print(json.dumps({"phase": target.__name__, "status": "PASS",
        "wall_seconds": result["wall_seconds"], "peak_rss_mib": result["peak_rss_mib"]}), flush=True)
    return result


def _cache_phase(config, window, seal, study, code):
    sample_path = config.output_path/"samples"/window.window_id
    initial, manifest = load_sample(sample_path)
    if manifest["sample_sha256"] != seal["sample_sha256"]:
        raise ValueError("catalogue/sample hash mismatch")
    expected = {"sample_id": window.window_id, "start_ns": window.start_ns,
                "end_ns": window.end_ns, "warmup_ns": window.warmup_ns}
    if (any(manifest["sample"].get(k) != v for k, v in expected.items())
            or manifest["instrument"]["tick"] != str(config.instrument.tick)
            or manifest["acquisition_segments"] != seal["acquisition_segments"]
            or manifest["bootstrap_receive_ns"] != seal["validation"]["bootstrap_receive_ns"]):
        raise ValueError("sealed sample timing, tick or acquisition provenance mismatch")
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


def _feature_phase(config, windows, cache_rows, sample_rows, definitions, freshness, features):
    feature_series, truth_by_window = {}, {}
    for window in windows:
        cache = cache_rows[window.window_id]
        feature_series[window.window_id] = tuple(read_feature_cache(
            cache["path"], cache["cache_key"], features))
        sample = sample_rows[window.window_id]
        initial, _ = load_sample(sample["path"])
        truth_by_window[window.window_id] = truth_midpoint_series(initial,
            iter_events(sample["path"]), sample["segment"])
    dates = sorted({window.date for window in windows})
    fitted_by_feature = {}
    for feature in features:
        fitted = {}
        for date in dates:
            training = tuple(snapshot for window in windows if window.date != date
                for snapshot in feature_series[window.window_id])
            edges = fit_bucket_edges(training, feature)
            fitted[date] = (edges, _bucket_id(feature, set(dates)-{date}, edges))
        fitted_by_feature[feature] = fitted
    contexts = {w.window_id: (feature_series[w.window_id], truth_by_window[w.window_id], w.end_ns, w.date)
                for w in windows}
    reports = evaluate_feature_scopes(features, contexts, definitions, freshness,
                                      config.instrument.tick, fitted_by_feature)
    return {"reports": reports}


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
    if type(entry_latency_ms) is not int or entry_latency_ms < 0:
        raise ValueError("entry latency must be a nonnegative integer")
    config = load_backtest_config(config_path)
    catalogue = _catalogue(catalogue_path)
    root = Path(__file__).resolve().parents[1]
    code = _code_fingerprint(root)
    study = Path(output) if output else config.output_path/"research"/"signal-development-v1"
    selected = development_windows(catalogue)
    study.mkdir(parents=True, exist_ok=False)
    definitions = _definitions(entry_latency_ms*1_000_000)
    cache_rows = {}
    cadence = {}
    sample_rows = {}
    phase_metrics = []
    def record_phase(phase, identifier, result, **extra):
        phase_metrics.append({"phase": phase, "id": identifier,
            "peak_rss_mib": result["peak_rss_mib"], "wall_seconds": result["wall_seconds"], **extra})
        write_json(study/"progress.json", {"status": "RUNNING", "code_fingerprint": code,
            "catalogue_sha256": catalogue["catalogue_sha256"], "completed_phases": phase_metrics})
    for window, seal in selected:
        result = _isolated(_cache_phase, config, window, seal, study, code)
        cache_rows[window.window_id] = result["cache"]
        sample_rows[window.window_id] = result["sample"]
        record_phase("cache_write", window.window_id, result)
        measured = _isolated(_cadence_phase, config, window, result["cache"],
                             result["sample"], definitions)
        cadence[window.window_id] = measured["cadence"]
        record_phase("cadence", window.window_id, measured)
    selected_windows = tuple(window for window, _ in selected)
    freshness, freshness_evidence = freeze_development_freshness(cadence, selected_windows)
    write_json(study/"freshness_policy.json", freshness_evidence)
    windows = tuple(window for window, _ in selected)
    reports = {"per_window": {}, "per_date": {}, "pooled": {}}
    for group_index, features in enumerate(FEATURE_GROUPS, 1):
        result = _isolated(_feature_phase, config, windows, cache_rows, sample_rows,
                           definitions, freshness, features)
        for scope in ("per_window", "per_date"):
            for scope_id, rows in result["reports"][scope].items():
                reports[scope].setdefault(scope_id, {}).update(rows)
        reports["pooled"].update(result["reports"]["pooled"])
        record_phase("feature_group", group_index, result, features=features)
        write_json(study/f"feature_group_{group_index}.json", result["reports"])
    result = _isolated(_redundancy_phase, windows, cache_rows)
    redundancy = result["redundancy"]
    record_phase("redundancy", "pooled", result)
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
    manifests = {wid: json.loads((Path(row["path"])/"feature_manifest.json").read_text())
                 for wid, row in cache_rows.items()}
    write_json(study/"candidate_report.json", {**output_row, "status": "PENDING_ACCEPTANCE"})
    acceptance = validate_report(jsonable(output_row), catalogue,
                                 jsonable(freshness_evidence), manifests)
    write_json(study/"development_report.json", output_row)
    write_json(study/"acceptance.json", acceptance)
    write_json(study/"progress.json", {"status": "PASS", "code_fingerprint": code,
        "catalogue_sha256": catalogue["catalogue_sha256"], "completed_phases": phase_metrics})
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
