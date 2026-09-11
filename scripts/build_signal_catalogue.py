#!/usr/bin/env python3
"""Prepare and seal preregistered Signal Research windows serially."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from lobcap.backtest.replay import load_sample, prepare_sample, write_json
from lobcap.backtest.research_flow.sample_catalogue import (
    excluded_window, fixed_pilot_windows, seal_catalogue, seal_window,
)
from lobcap.backtest.types import ARRIVAL_NORMALIZATION_VERSION, SampleSpec
from lobcap.config.backtest import load_backtest_config


def build(base_config, destination=None):
    base = load_backtest_config(base_config)
    rows = []
    root = Path(destination) if destination else base.output_path/"research"/"signal-catalogue-v1"
    root.mkdir(parents=True, exist_ok=True)
    for window in fixed_pilot_windows():
        sample = SampleSpec(window.window_id, window.start_ns, window.end_ns,
                            warmup_ns=window.warmup_ns,
                            normalization_version=ARRIVAL_NORMALIZATION_VERSION)
        config = replace(base, sample=sample)
        try:
            sample_path = prepare_sample(config)
            initial, manifest = load_sample(sample_path, config)
            row = seal_window(window, sample_path, initial, manifest)
        except Exception as exc:
            row = excluded_window(window, f"{type(exc).__name__}: {exc}")
        write_json(root/f"{window.window_id}.json", row)
        rows.append(row)
    catalogue = seal_catalogue(rows)
    write_json(root/"catalogue_manifest.json", catalogue)
    return root, catalogue


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/backtest/binance_recorded.yaml")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    root, catalogue = build(args.config, args.output)
    print(json.dumps({"path": str(root), "catalogue_sha256": catalogue["catalogue_sha256"],
        "pass": sum(row["quality_status"] == "PASS" for row in catalogue["windows"]),
        "excluded": sum(row["quality_status"] == "EXCLUDED" for row in catalogue["windows"])},
        sort_keys=True))


if __name__ == "__main__":
    main()
