from dataclasses import replace
from decimal import Decimal as D

import pytest

from lobcap.backtest.research_flow.features import CausalFeatureEngine
from lobcap.backtest.types import BookSnapshot, L2LevelUpdate, L2Trade, ReplayEvent


def initial():
    return BookSnapshot(((D(100), D(3)), (D(99), D(2))),
                        ((D(101), D(1)), (D(102), D(4))), 0, 1, D(90), D(110))


def depth(index, time_ns, receive_ns=None, updates=()):
    return ReplayEvent(index, time_ns, receive_ns or time_ns, "depth", (f"d:{index}",),
                       updates=tuple(updates))


def trade(index, time_ns, side, quantity):
    return ReplayEvent(index, time_ns, time_ns, "trade", (f"t:{index}",),
                       trade=L2Trade(side, D(100), D(quantity)))


def values(engine, now):
    return {point.feature_name: point for point in engine.snapshot(now)}


def test_exact_book_features_and_vamp_formula():
    engine = CausalFeatureEngine(initial(), D(1))
    row = values(engine, 0)
    assert row["mid"].value == D("100.5")
    assert row["spread_ticks"].value == 1
    assert row["microprice"].value == D("100.75")
    assert row["microprice_minus_mid_ticks"].value == D(".25")
    assert row["imbalance_1"].value == D(".5")
    assert row["imbalance_2"].value == 0
    # (101*3 + 100*1 + 102*2 + 99*4) / 10 = 100.3
    assert row["vamp_5"].value == D("100.3")


def test_trade_direction_windows_use_quantity_not_packet_count():
    engine = CausalFeatureEngine(initial(), D(1))
    engine.observe(trade(1, 10, "sell", "3"), 10)  # maker sell = aggressive buy
    engine.observe(trade(2, 20, "buy", "1"), 20)   # maker buy = aggressive sell
    row = values(engine, 30)
    assert row["signed_trade_volume_100ms"].value == 2
    assert row["trade_volume_imbalance_100ms"].value == D(".5")
    assert row["trade_intensity_100ms"].value == 20


def test_bbo_ofi_is_event_driven_and_snapshot_does_not_update_state():
    engine = CausalFeatureEngine(initial(), D(1))
    update = L2LevelUpdate("buy", D(100), D(3), D(4))
    engine.observe(depth(1, 10, updates=(update,)), 10)
    first = values(engine, 20)
    second = values(engine, 30)
    assert first["bbo_ofi_100ms"].value == 1
    assert second["bbo_ofi_100ms"].value == 1
    assert engine.last_client_ns == 10


def test_future_suffix_cannot_change_past_feature_snapshot():
    prefix = [trade(1, 10, "sell", "2")]
    a = CausalFeatureEngine(initial(), D(1)); b = CausalFeatureEngine(initial(), D(1))
    for event in prefix:
        a.observe(event, event.receive_ns); b.observe(event, event.receive_ns)
    left = a.snapshot(100)
    right = b.snapshot(100)
    assert left == right
    b.observe(trade(2, 200, "buy", "100"), 200)
    assert left == right


def test_delayed_event_is_invisible_until_delivery_and_stale_is_unavailable():
    engine = CausalFeatureEngine(initial(), D(1), stale_after_ns=50, initial_client_available_ns=10)
    event = replace(trade(1, 20, "sell", "2"), receive_ns=40)
    assert values(engine, 30)["signed_trade_volume_100ms"].value == 0
    engine.observe(event, 40)
    assert values(engine, 40)["signed_trade_volume_100ms"].value == 2
    stale = values(engine, 100)
    assert stale["mid"].status == "STALE" and stale["mid"].value is None
    assert stale["book_age_ns"].value == 90


def test_delivery_regression_and_future_delivery_fail_closed():
    engine = CausalFeatureEngine(initial(), D(1))
    engine.observe(trade(1, 10, "sell", "1"), 20)
    with pytest.raises(ValueError, match="regressed"):
        engine.observe(trade(2, 21, "sell", "1"), 19)
    with pytest.raises(ValueError, match="before exchange"):
        CausalFeatureEngine(initial(), D(1)).observe(trade(1, 10, "sell", "1"), 9)
