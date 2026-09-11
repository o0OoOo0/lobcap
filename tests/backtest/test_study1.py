from copy import deepcopy
from decimal import Decimal as D
import json
import gzip
from pathlib import Path

import pytest
from test_runner import sample
from test_signal_study import catalogue

from lobcap.backtest.artifacts import MemorySink
from lobcap.backtest.engine import run
from lobcap.backtest.replay import iter_events, load_sample
from lobcap.backtest.types import jsonable
from lobcap.backtest.research_flow.signal_study import development_windows
from lobcap.backtest.research_flow.study1 import case_configs, summarize, economic_value
from lobcap.config.backtest import variant


@pytest.mark.parametrize("clock", ["exchange", "recorded"])
def test_zero_coefficient_preserves_economic_execution_and_nonzero_inventory(sample, clock):
    config, path = sample
    config = variant(config, clock_mode=clock, report_ms=20, entry_ms=10, cancel_ms=10,
                     initial_inventory=".005", inventory_skew_ticks="20", alpha=None, stale_book_ms=100000)
    configs = case_configs(config)
    initial, _ = load_sample(path)
    results = []
    for c in configs.values():
        sink = MemorySink()
        result = run(c, initial, iter_events(path), sink, bootstrap_receive_ns=initial.time_ns)
        results.append((result, sink))
    assert results[0][0].summary == results[1][0].summary
    for channel in ("orders", "fills", "ledger", "execution", "risk", "timeseries", "markouts"):
        assert economic_value(results[0][1].rows.get(channel)) == economic_value(results[1][1].rows.get(channel))
    assert economic_value(results[0][1].rows["orders"]) != economic_value(results[2][1].rows["orders"])
    a, b, c = (x.resolved() for x in configs.values())
    for x in (a, b, c):
        x.pop("strategy"); x.pop("inventory_skew_ticks")
    assert a == b == c


def paired_rows(config, cat):
    configs = case_configs(config)
    rows = []
    for index, (window, seal) in enumerate(development_windows(cat)):
        cases = {label: {"config": c.resolved(), "metrics": {k: "0" for k in
                  ("net_pnl", "gross_pnl", "fees", "spread_capture", "inventory_mtm", "max_drawdown",
                   "max_abs_inventory", "ending_inventory", "turnover")}} for label, c in configs.items()}
        delta = {k: "0" for k in cases["MM-0"]["metrics"]}
        delta["net_pnl"] = str(index-3)
        cases["MM-1"]["metrics"]["net_pnl"] = delta["net_pnl"]
        rows.append({"window_id": window.window_id, "date": window.date,
            "sample_sha256": seal["sample_sha256"], "zero_coefficient_parity": True,
            "execution_evidence_sufficient": index != 0, "cases": cases,
            "code_version": "a"*40, "scheduler_identity": "arrival-v1",
            "delta_mm1_minus_mm0": delta})
    return rows


def test_paired_dates_keep_no_fill_windows_and_do_not_sum_drawdowns(sample):
    config, _ = sample
    cat = catalogue()
    rows = paired_rows(config, cat)
    result = summarize(cat, rows)
    assert result["pooled"]["paired_windows"] == 8
    assert result["pooled"]["windows_with_sufficient_execution"] == 7
    assert result["pooled"]["sum_reset_window_delta"]["net_pnl"] == "4"
    assert "max_drawdown" not in result["pooled"]["sum_reset_window_delta"]
    assert len(result["per_date"]) == 2
    assert result["paper_eligibility"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "sample", "zero", "cost", "code", "scheduler", "delta"])
def test_paired_report_rejects_missing_windows_or_drift(sample, corruption):
    config, _ = sample
    cat = catalogue()
    rows = paired_rows(config, cat)
    if corruption == "missing": rows.pop()
    elif corruption == "duplicate": rows[-1] = deepcopy(rows[0])
    elif corruption == "sample": rows[0]["sample_sha256"] = "different"
    elif corruption == "zero": rows[0]["zero_coefficient_parity"] = False
    elif corruption == "cost": rows[0]["cases"]["MM-1"]["config"]["maker_bps"] = "10"
    elif corruption == "code": rows[0]["code_version"] = "b"*40
    elif corruption == "scheduler": rows[0]["scheduler_identity"] = "other"
    else: rows[0]["delta_mm1_minus_mm0"]["net_pnl"] = "100"
    with pytest.raises(ValueError): summarize(cat, rows)


def test_excluded_windows_are_retained_without_replacement(sample):
    config, _ = sample
    wid = development_windows(catalogue())[0][0].window_id
    cat = catalogue((wid,))
    result = summarize(cat, paired_rows(config, cat))
    assert result["pooled"]["paired_windows"] == 7
    assert result["excluded_windows"][0]["window"]["window_id"] == wid


def test_real_sweep_review_and_file_based_zero_parity(sample, monkeypatch):
    from types import SimpleNamespace
    from lobcap.backtest.research_flow import study1
    from lobcap.backtest.runner import run_sweep
    config, path = sample
    config = variant(config, alpha=None, stale_book_ms=100000)
    configs = case_configs(config)
    comparison, _ = run_sweep(configs["MM-0"], [
        {"strategy": c.strategy, "inventory_skew_ticks": c.inventory_skew_ticks}
        for c in configs.values()], path)
    paths = json.loads((comparison/"comparison_manifest.json").read_text())["runs"]
    _, manifest = load_sample(path)
    original = study1.run_record
    # Synthetic evidence only: this test probes review integration, not Git attestation.
    def fixture_record(path):
        row = original(path)
        row["manifest"]["code_version"] = "a"*40
        return row
    monkeypatch.setattr(study1, "run_record", fixture_record)
    window = SimpleNamespace(window_id="fixture", date="2026-09-07")
    result = study1.review_window(window, manifest, configs, paths)
    assert result["zero_coefficient_parity"]
    assert all(row["metrics"]["reconciled"] for row in result["cases"].values())
    with gzip.open(Path(paths[1])/"ledger.jsonl.gz", "at") as stream:
        stream.write('{"cash":"123"}\n')
    assert study1.execution_fingerprint(paths[0]) != study1.execution_fingerprint(paths[1])


def test_runner_requires_p0_before_creating_any_output(sample, monkeypatch, tmp_path):
    import importlib
    from lobcap.config.experiment import ROOT
    monkeypatch.syspath_prepend(str(ROOT/"scripts"))
    runner = importlib.import_module("run_study1_baselines")
    config, _ = sample
    monkeypatch.setattr(runner, "load_backtest_config", lambda _: variant(config, clock_mode="recorded"))
    monkeypatch.setattr(runner, "_code_fingerprint", lambda _: "a"*40)
    def fail(*args):
        raise ValueError("P0 prerequisite not accepted")
    monkeypatch.setattr(runner, "validate_p0", fail)
    cat = tmp_path/"catalogue.json"
    cat.write_text(json.dumps(catalogue()))
    output = tmp_path/"study"
    with pytest.raises(ValueError, match="P0 prerequisite"):
        runner.run("config", cat, "p0", output)
    assert not output.exists()


@pytest.mark.parametrize("field", ["sample_sha256", "instrument", "acquisition_segments", "source_schema_version", "bootstrap_receive_ns"])
def test_preflight_rejects_sample_seal_drift(sample, monkeypatch, field):
    from lobcap.backtest.research_flow import study1
    config, path = sample
    window, seal = development_windows(catalogue())[0]
    manifest = {"sample_sha256": seal["sample_sha256"],
        "sample": {"sample_id": window.window_id, "start_ns": window.start_ns,
            "end_ns": window.end_ns, "warmup_ns": window.warmup_ns,
            "normalization_version": seal["normalization_version"]},
        "instrument": jsonable(config.instrument),
        "acquisition_segments": seal["acquisition_segments"],
        "source_schema_version": seal["source_schema_version"],
        "bootstrap_receive_ns": seal["validation"]["bootstrap_receive_ns"]}
    manifest[field] = "tampered"
    monkeypatch.setattr(study1, "load_sample", lambda _: (None, manifest))
    with pytest.raises(ValueError, match="catalogue seal"):
        study1.sealed_config(config, window, seal, path, path/"unused")
