from decimal import Decimal as D
from pathlib import Path

from lobcap.backtest.research_flow.labels import MidpointSeries
from lobcap.backtest.research_flow.signal_analysis import evaluate_feature
from lobcap.backtest.research_flow.signal_contracts import (
    CLIENT_FORWARD_RETURN, MARKET_TRUTH_RETURN, POST_LATENCY_RETURN,
    FeaturePoint, FeatureSnapshot, FreshnessPolicy, LabelDefinition,
)
from lobcap.config.experiment import ROOT


def snapshot(t, signal, client_mid, source_time):
    point = FeaturePoint("signal", "v1", D(signal), "ticks", "AVAILABLE", t, t,
                         source_time, source_time, (f"d:{t}",))
    return FeatureSnapshot(t, D(client_mid), source_time, source_time, t, "a", (point,))


def test_three_label_families_are_reported_separately():
    snapshots = (snapshot(100, "1", "100", 90), snapshot(200, "2", "101", 190))
    truth = MidpointSeries(((0, "99", "a"), (90, "100", "a"), (190, "101", "a"),
                            (290, "103", "a"), (390, "106", "a")))
    definitions = (
        LabelDefinition(MARKET_TRUTH_RETURN, 100),
        LabelDefinition(CLIENT_FORWARD_RETURN, 100),
        LabelDefinition(POST_LATENCY_RETURN, 100, entry_latency_ns=100),
    )
    policy = FreshnessPolicy("fixture", ((100, 20, 20),))
    rows = evaluate_feature("signal", snapshots, truth, definitions, policy, D(1), 500)
    assert [row["label_kind"] for row in rows] == [MARKET_TRUTH_RETURN, CLIENT_FORWARD_RETURN, POST_LATENCY_RETURN]
    assert all(row["valid"] == 2 for row in rows)
    assert rows[0]["pearson_ic"] == 1
    assert rows[1]["target_age_distribution"]["maximum_ns"] == 10
    assert rows[2]["entry_latency_ns"] == 100


def test_runtime_modules_do_not_import_offline_labels():
    for relative in ("src/lobcap/backtest/engine.py", "src/lobcap/backtest/arrival.py",
                     "src/lobcap/backtest/execution.py", "src/lobcap/backtest/strategies.py",
                     "src/lobcap/backtest/quote_policy.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "research_flow.labels" not in text and " import labels" not in text
