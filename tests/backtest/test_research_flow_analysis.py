import gzip
import json
from copy import deepcopy
from decimal import Decimal as D

import pytest

from lobcap.backtest.research_flow.catalog import index_runs, run_record, comparison_guard
from lobcap.backtest.research_flow.analysis import explain_run, order_drilldown, compare_runs, terminal_liquidation_reference


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def rows(path, channel, values):
    with gzip.open(path / f"{channel}.jsonl.gz", "wt", encoding="utf-8") as stream:
        for row in values:
            stream.write(json.dumps(row) + "\n")


def fixture(path, run_id="a", status="PASS", trace=True):
    config = {"sample": {"start_ns": 0, "end_ns": 10}, "instrument": {"lot": ".001", "tick": ".1"},
              "initial_cash": "100", "initial_inventory": "0", "max_inventory": ".001",
              "execution_contract_version": "v2", "strategy": "test", "clock_mode": "exchange",
              "alpha": "0", "entry_ms": 0, "cancel_ms": 0, "report_ms": 0,
              "maker_bps": "0", "taker_bps": "5", "max_trade_participation": "1", "impact_stress_bps": "0"}
    write_json(path / "run_manifest.json", {"run_id": run_id, "status": status, "resolved_config": config,
               "sample_sha256": "same", "deterministic_digest": "digest", "started_at": "2026", "code_version": "test-commit"})
    write_json(path / "summary.json", {"net_pnl": "-.0011"})
    rows(path, "fills", [{"order_id": "o1", "side": "buy", "quantity": ".001", "time_ns": 2,
                          "spread_contribution": ".001", "fee": ".0001", "reason": "same_price"}])
    rows(path, "ledger", [{"time_ns": 0, "inventory": "0", "mark_price": "100", "equity": "100"},
                          {"time_ns": 2, "inventory": ".001", "mark_price": "100", "equity": "100.0009"},
                          {"time_ns": 6, "inventory": ".001", "mark_price": "98", "equity": "99.9989"}])
    rows(path, "orders", [{"order_id": "o1", "time_ns": 1, "decision_id": "d1", "event": "submit"},
                          {"order_id": "o1", "time_ns": 2, "decision_id": "d1", "event": "full_fill"}])
    if trace:
        rows(path, "decision_traces", [{"time_ns": 1, "decision_id": "d1", "risk_limited_target": {"bid": 99},
                                      "actions": [{"order_id": "o1", "action": "submit"}]}])
    return path


def test_unified_index_deduplicates_without_migrating_and_keeps_canceled(tmp_path):
    output, workspace = tmp_path / "output", tmp_path / "workspace"
    path = fixture(output / "runs" / "a")
    write_json(workspace / "jobs" / "j.json", {"job_id": "j", "name": "Hypothesis", "status": "CANCELED",
                  "completed": {"v1": {"output_dir": str(path), "run_id": "a"}}, "failed": {}, "created_at": "2026"})
    write_json(workspace / "experiments" / "j.json", {"runs": [{"variant_id": "v1"}, {"variant_id": "v2"}],
                  "research_context": {"hypothesis": "test"}})
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    index = index_runs(output, workspace)
    assert len(index) == 2
    completed = next(r for r in index if r["run_id"] == "a")
    assert completed["rankable"] and completed["sources"] == ["legacy", "workspace"]
    assert completed["research_context"] == {"hypothesis": "test"}
    assert not next(r for r in index if r["run_id"] != "a")["rankable"]
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_comparison_guards_versions_capital_sample_and_failed(tmp_path):
    a = run_record(fixture(tmp_path / "a"))
    assert comparison_guard(a, a)["comparable"]
    for key, value in (("sample_sha256", "other"), ("execution_contract_version", "v3"), ("rankable", False)):
        b = deepcopy(a); b[key] = value
        assert not comparison_guard(a, b)["comparable"]
    b = deepcopy(a); b["config"]["initial_inventory"] = ".001"
    assert not comparison_guard(a, b, True)["comparable"]
    b = deepcopy(a); b["config"]["maker_bps"] = "2"
    assert not comparison_guard(a, b)["comparable"]
    assert comparison_guard(a, b, True)["comparable"]


def test_equation_explanation_and_time_weighted_exposure(tmp_path):
    result = explain_run(fixture(tmp_path / "a"))
    assert result["identity"]["reconciled"]
    assert D(result["identity"]["net_pnl"]) == D("-.0011")
    assert D(result["inventory_exposure"]["time_weighted_absolute_inventory"]) == D(".0008")
    assert result["inventory_exposure"]["near_limit_duration_ns"] == 8
    assert any("재고 보유" in finding for finding in result["findings"])


def test_drilldown_is_bounded_and_reports_missing_legacy_trace(tmp_path):
    path = fixture(tmp_path / "a", trace=False)
    result = order_drilldown(path, "o1", limit=1)
    assert result["truncated"] and len(result["timeline"]) == 1
    assert result["trace_status"] == "legacy_missing"
    assert "decision_traces" in result["missing"]


def test_pair_divergence_and_delta_identity(tmp_path):
    a, b = fixture(tmp_path / "a"), fixture(tmp_path / "b", "b")
    rows(b, "decision_traces", [{"time_ns": 1, "risk_limited_target": {"bid": 98}, "actions": [{"action": "none"}]}])
    result = compare_runs(a, b)
    assert result["pnl_delta_identity"]["reconciled"]
    assert result["first_desired_divergence"]["time_ns"] == 1
    assert result["first_action_divergence"]["time_ns"] == 1


@pytest.mark.parametrize("inventory,expected", [(".002", "-.002099"), ("-.002", "-.002101")])
def test_terminal_reference_long_short_fees_without_book_mutation(inventory, expected):
    book = {"bids": [("99", ".002")], "asks": [("101", ".002")], "time_ns": 0}
    before = deepcopy(book)
    result = terminal_liquidation_reference(inventory, "100", book, 1, "5", ".001")
    assert result["status"] == "COMPLETE" and result["reference_only"]
    assert D(result["midpoint_adjustment"]) == D(expected)
    assert result["net_pnl_reference"] is None
    assert book == before


def test_terminal_reference_stale_depth_and_sub_lot_coverage():
    book = {"bids": [("99", ".001")], "asks": [("101", ".001")], "time_ns": 0}
    assert terminal_liquidation_reference(".001", 100, book, 600_000_000, 0, ".001")["status"] == "UNAVAILABLE"
    partial = terminal_liquidation_reference(".0015", 100, book, 1, 0, ".001")
    assert partial["status"] == "PARTIAL" and partial["residual_quantity"] == "0.0005"
    assert partial["net_pnl_reference"] is None
    assert terminal_liquidation_reference("0", 100, {}, 1, 0, ".001")["status"] == "COMPLETE"


def test_invalid_manifest_does_not_hide_healthy_runs(tmp_path):
    fixture(tmp_path / "runs" / "good")
    bad = tmp_path / "runs" / "bad"
    bad.mkdir()
    (bad / "run_manifest.json").write_text("{broken", encoding="utf-8")
    catalog = index_runs(tmp_path)
    assert len(catalog) == 2
    assert sum(r["rankable"] for r in catalog) == 1
    assert any(r["status"] == "INVALID_ARTIFACT" for r in catalog)


def test_sensitivity_cannot_covertly_change_strategy_or_hard_risk(tmp_path):
    a = run_record(fixture(tmp_path / "a"))
    for field, value in (("strategy", "other"), ("max_inventory", ".01"), ("quote_policy_version", "future")):
        b = deepcopy(a); b["config"][field] = value
        assert not comparison_guard(a, b, True)["comparable"]
    b = deepcopy(a); b["manifest"]["research_policy"] = {"hedge": True}
    assert not comparison_guard(a, b, True)["comparable"]


@pytest.mark.parametrize("version", [None, "", "unknown", "UNKNOWN", "unavailable", "test-commit+dirty"])
def test_matching_unknown_or_dirty_code_cannot_certify_comparison(tmp_path, version):
    a = run_record(fixture(tmp_path / "a"))
    a["manifest"]["code_version"] = version
    # Two identical unverified declarations are still not verification evidence.
    for sensitivity in (False, True):
        result = comparison_guard(a, deepcopy(a), sensitivity)
        assert not result["comparable"]
        assert any("Known clean code" in reason for reason in result["reasons"])


def test_known_symlink_run_root_is_indexed(tmp_path):
    original = fixture(tmp_path / "old" / "a")
    output = tmp_path / "new"
    output.mkdir()
    try:
        (output / "runs").symlink_to(original.parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlink permission unavailable on this host")
    assert index_runs(output)[0]["run_id"] == "a"


def test_no_fill_terminal_order_is_available_for_drilldown(tmp_path):
    path = fixture(tmp_path / "a")
    rows(path, "orders", [{"order_id": "unfilled", "event": "terminal", "status": "REJECTED", "reason": "post_only_marketable", "time_ns": 4}])
    assert any(r["order_id"] == "unfilled" for r in explain_run(path)["representative_orders"])


def test_behavior_comparison_ignores_explanations_and_order_id_only_changes(tmp_path):
    a, b = fixture(tmp_path / "a"), fixture(tmp_path / "b", "b")
    for path, price, reason, order_id in ((a, "99.0", "baseline", "o1"), (b, "99", "new explanation", "o2")):
        rows(path, "decision_traces", [{"time_ns": 1, "risk_limited_target": {"bid": {"price": price, "quantity": ".001"}, "ask": None, "reason": reason},
                                       "actions": [{"side": "buy", "action": "submit", "reason": reason, "order_id": order_id}]}])
    result = compare_runs(a, b)
    assert result["first_desired_divergence"] is None
    assert result["first_action_divergence"] is None


def test_representatives_include_favorable_adverse_cancel_pending_and_unfilled(tmp_path):
    path = fixture(tmp_path / "a")
    rows(path, "fills", [{"order_id": key, "side": "buy", "quantity": ".001", "time_ns": i,
                          "spread_contribution": spread, "fee": "0", "reason": "same_price", "cancel_pending": pending}
                         for i, (key, spread, pending) in enumerate((("good", ".001", False), ("bad", "-.002", False), ("cancel", "0", True)), 1)])
    rows(path, "orders", [{"order_id": "no-fill", "event": "terminal", "status": "CANCELED", "time_ns": 4, "quantity": ".001", "remaining": ".001"},
                          {"order_id": "reject", "event": "terminal", "status": "REJECTED", "time_ns": 5}])
    reasons = {r["selection_reason"] for r in explain_run(path)["representative_orders"]}
    assert reasons == {"favorable_spread_at_fill", "adverse_spread_at_fill", "cancel_pending_fill", "unfilled_terminal_order", "rejected_order"}


def test_final_known_book_preserves_receipt_sequence_and_exchange_age(monkeypatch):
    from lobcap.backtest import replay
    from lobcap.backtest.types import BookSnapshot, ReplayEvent, L2LevelUpdate
    from lobcap.backtest.research_flow.analysis import final_book
    initial = BookSnapshot(((D(100), D(1)),), ((D(101), D(1)),), 0, 1, D(90), D(110))
    events = [ReplayEvent(1, 1, 5, "depth", updates=(L2LevelUpdate("buy", D(100), D(1), D(0)), L2LevelUpdate("buy", D(99), D(0), D(1)))),
              ReplayEvent(2, 2, 3, "depth", updates=(L2LevelUpdate("buy", D(99), D(1), D(0)), L2LevelUpdate("buy", D(98), D(0), D(1))))]
    monkeypatch.setattr(replay, "load_sample", lambda path: (initial, {"bootstrap_receive_ns": 0, "sample_sha256": "same"}))
    monkeypatch.setattr(replay, "iter_events", lambda path: iter(events))
    assert dict(final_book("sample", 4, "recorded")["bids"]) == {D(100): D(1)}
    final = final_book("sample", 6, "recorded")
    assert dict(final["bids"]) == {D(98): D(1)}
    assert final["time_ns"] == 2
    assert dict(final_book("sample", 4, "exchange")["bids"]) == {D(98): D(1)}


def test_identical_one_lot_execution_explains_unexercised_partial_policy(tmp_path):
    a, b = fixture(tmp_path / "a"), fixture(tmp_path / "b", "b")
    for path, policy in ((a, "keep_remaining"), (b, "reset_target")):
        manifest = json.loads((path / "run_manifest.json").read_text(encoding="utf-8"))
        manifest["resolved_config"].update(size=".001", partial_fill_policy=policy)
        write_json(path / "run_manifest.json", manifest)
    result = compare_runs(a, b)
    assert result["guard"]["comparable"]
    relationship = result["behavior_relationship"]
    assert relationship["desired_identical"] and relationship["actions_identical"]
    assert relationship["partial_fill_policy_exercised"] is False
    assert any("One-lot" in warning for warning in result["warnings"])


def test_legacy_trace_absence_does_not_claim_identical_behavior(tmp_path):
    result = compare_runs(fixture(tmp_path / "a", trace=False), fixture(tmp_path / "b", "b", trace=False))
    assert result["behavior_relationship"]["desired_identical"] is None
    assert result["behavior_relationship"]["actions_identical"] is None


def test_code_compatibility_requires_actual_display_only_git_diff(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from lobcap.backtest.research_flow import catalog
    a=run_record(fixture(tmp_path/'a'))
    b=deepcopy(a)
    a['manifest']['code_version']='a'*40
    b['manifest']['code_version']='b'*40
    for changed,expected in [('src/lobcap/backtest/research_flow/ui_results.py\ndocs/current/example.md\n',True),
                             ('src/lobcap/backtest/execution.py\n',False),
                             ('src/lobcap/backtest/research_flow/analysis.py\n',False),
                             ('pyproject.toml\n',False)]:
        catalog.display_only_compatibility.cache_clear()
        monkeypatch.setattr(catalog.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(stdout=changed))
        assert comparison_guard(a,b)['comparable'] is expected
    catalog.display_only_compatibility.cache_clear()
    def unavailable(*args,**kwargs):raise OSError('git objects unavailable')
    monkeypatch.setattr(catalog.subprocess,'run',unavailable)
    assert not comparison_guard(a,b)['comparable']
    catalog.display_only_compatibility.cache_clear()
