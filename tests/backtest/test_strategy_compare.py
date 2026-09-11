from decimal import Decimal as D

import pytest
from test_runner import sample

from lobcap.backtest.research_flow.experiments import build_experiment
from lobcap.backtest.research_flow.strategy_compare import build_scorecard, loss_driver
from lobcap.backtest.workspace.contracts import StrategyDraft
from lobcap.config.backtest import variant


def test_strategy_suite_freezes_scenario_and_aligns_glft_start(sample):
    config, path = sample
    draft = StrategyDraft(config, "standard suite", "Which policy changes order behavior?")
    spec = build_experiment(draft, "strategy_suite", values=["fixed_spread", "glft", "no_trade"],
                            sample_path=path)
    assert [row["settings"]["strategy"] for row in spec["runs"]] == ["fixed_spread", "glft", "no_trade"]
    assert {row["settings"]["quote_start_seconds"] for row in spec["runs"]} == {config.glft_min_seconds}
    assert len({row["scenario_hash"] for row in spec["runs"]}) == 1
    assert spec["research_context"]["paper_eligibility"] == "INSUFFICIENT_EVIDENCE"


def test_strategy_suite_rejects_duplicates_and_unknowns(sample):
    config, path = sample
    draft = StrategyDraft(config, "suite", "question")
    for values in (["fixed_spread", "fixed_spread"], ["fixed_spread", "oracle"]):
        with pytest.raises(ValueError):
            build_experiment(draft, "strategy_suite", values=values, sample_path=path)


def test_loss_driver_separates_cost_and_inventory():
    reviewed = {"fill_events": 2}
    assert loss_driver({"net_pnl":"-1","gross_pnl":"1","fees":"2"}, reviewed) == "FEES"
    assert loss_driver({"net_pnl":"-3","gross_pnl":"-2","fees":"1",
                        "spread_capture":"1","inventory_mtm":"-3"}, reviewed) == "INVENTORY_MTM"
    assert loss_driver({"net_pnl":"0","gross_pnl":"0"}, {"fill_events":0}) == "NO_EXECUTION"


def test_scorecard_requires_same_sample_and_scenario():
    base = {"rankable":True,"summary":{"net_pnl":"1"},"output_dir":"x","run_id":"a",
            "sample_sha256":"one","config":{"strategy":"fixed_spread"}}
    with pytest.raises(ValueError, match="identical sample"):
        build_scorecard([base, {**base,"run_id":"b","sample_sha256":"two"}])


def test_scorecard_ranks_executed_policies_and_keeps_no_trade_as_control(monkeypatch):
    import lobcap.backtest.research_flow.strategy_compare as module
    monkeypatch.setattr(module, "reviewed_metrics", lambda path: {
        "available":True, "reconciled":True, "fill_events":2,
        "turnover":D(100), "break_even_fee_bps":D("0.2")})
    def row(run_id, strategy, pnl, drawdown, inventory, fills=1):
        return {"rankable":True,"output_dir":run_id,"run_id":run_id,"sample_sha256":"same",
            "config":{"strategy":strategy},"summary":{"net_pnl":pnl,"gross_pnl":pnl,
            "fees":"0","spread_capture":pnl,"inventory_mtm":"0","max_drawdown":drawdown,
            "max_abs_inventory":inventory,"ending_inventory":"0","execution":{
            "orders_with_fill":fills,"activated":2},"markouts":{"1000":{"coverage":1}}}}
    card=build_scorecard([row("a","fixed_spread","1","2",".01"),
                          row("b","inventory_skew","2","1",".005"),
                          row("c","no_trade","0","0","0",0)])
    assert card[0]["strategy"]=="inventory_skew" and card[0]["development_rank"]==1
    control=next(item for item in card if item["control"])
    assert control["development_rank"] is None and control["paper_eligibility"]=="INSUFFICIENT_EVIDENCE"
