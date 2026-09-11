#!/usr/bin/env python3
"""Build read-only Signal Lab foundation evidence on one prepared sample."""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import time
from collections import Counter
from pathlib import Path

from lobcap.backtest.replay import iter_events, load_sample, write_json
from lobcap.backtest.research_flow.feature_cache import iter_feature_snapshots
from lobcap.backtest.research_flow.labels import raw_label_cadence, truth_midpoint_series
from lobcap.backtest.research_flow.signal_contracts import (
    AnalysisSchedule, LabelDefinition, CLIENT_FORWARD_RETURN,
    MARKET_TRUTH_RETURN, POST_LATENCY_RETURN,
)
from lobcap.config.backtest import load_backtest_config


HORIZONS_NS = (100_000_000, 250_000_000, 500_000_000, 1_000_000_000, 5_000_000_000)


def git_head(root):
    return subprocess.run(("git", "rev-parse", "HEAD"), cwd=root, check=True,
                          text=True, capture_output=True).stdout.strip()


def build(config_path, sample_path):
    config = load_backtest_config(config_path)
    initial, manifest = load_sample(sample_path, config)
    schedule = AnalysisSchedule(config.sample.start_ns, config.sample.end_ns, 100_000_000)
    begin = time.perf_counter(); cpu = time.process_time()
    snapshots = tuple(iter_feature_snapshots(initial, iter_events(sample_path), schedule,
        config.instrument.tick, config.clock_mode, manifest.get("bootstrap_receive_ns"),
        config.stale_book_ms*1_000_000, manifest["sample"]["sample_id"]))
    truth = truth_midpoint_series(initial, iter_events(sample_path), manifest["sample"]["sample_id"])
    definitions = []
    for horizon in HORIZONS_NS:
        definitions.extend((LabelDefinition(MARKET_TRUTH_RETURN, horizon),
                            LabelDefinition(CLIENT_FORWARD_RETURN, horizon),
                            LabelDefinition(POST_LATENCY_RETURN, horizon,
                                            entry_latency_ns=config.entry_ms*1_000_000)))
    availability = Counter()
    for snapshot in snapshots:
        for name in ("microprice_minus_mid_ticks", "imbalance_1", "imbalance_5",
                     "vamp_5_minus_mid_ticks", "trade_volume_imbalance_1000ms", "bbo_ofi_1000ms"):
            availability[name+":"+snapshot.point(name).status] += 1
    result = {"status": "PASS", "purpose": "Signal Lab foundation; no strategy or paper claim",
        "sample_id": manifest["sample"]["sample_id"], "sample_sha256": manifest["sample_sha256"],
        "clock_mode": config.clock_mode, "analysis_schedule": {"interval_ns": schedule.interval_ns,
            "note": "Research snapshots only; not the strategy decision clock."},
        "strategy_decision_ms": config.decision_ms, "snapshot_count": len(snapshots),
        "feature_availability": dict(sorted(availability.items())),
        "label_cadence": raw_label_cadence(snapshots, truth, definitions,
                                             config.instrument.tick, config.sample.end_ns),
        "freshness_policy": None,
        "performance": {"wall_seconds": time.perf_counter()-begin,
                        "cpu_seconds": time.process_time()-cpu},
        "environment": {"python": platform.python_version()},
        "git_commit": git_head(Path(config_path).resolve().parents[2])}
    if len(snapshots) != (config.sample.end_ns-config.sample.start_ns)//schedule.interval_ns:
        raise AssertionError("analysis schedule row count mismatch")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    result = build(args.config, args.sample)
    if args.output:
        write_json(args.output, result)
        print(json.dumps({"status": result["status"], "output": str(args.output),
            "snapshot_count": result["snapshot_count"],
            "wall_seconds": result["performance"]["wall_seconds"]}, sort_keys=True))
    else:
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
