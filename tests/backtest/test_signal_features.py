from dataclasses import replace
from decimal import Decimal as D

import pytest

from lobcap.backtest.research_flow.features import CausalFeatureEngine
from lobcap.backtest.research_flow.feature_registry import FEATURE_REGISTRY
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
    assert row["vamp_5"].status == "INSUFFICIENT_DEPTH"
    assert row["vamp_5"].value is None
    assert row["spread_ticks"].unit == "ticks"
    assert row["microprice_minus_mid_ticks"].unit == "ticks"
    assert set(row) == set(FEATURE_REGISTRY)


def test_vamp_requires_and_uses_exactly_five_levels_per_side():
    book = BookSnapshot(
        tuple((D(100-i), D(i+1)) for i in range(5)),
        tuple((D(101+i), D(5-i)) for i in range(5)), 0, 1, D(90), D(110))
    row = values(CausalFeatureEngine(book, D(1)), 0)
    expected = sum((D(101+i)*D(i+1)+D(100-i)*D(5-i)) for i in range(5)) / D(30)
    assert row["vamp_5"].status == "AVAILABLE"
    assert row["vamp_5"].value == expected


def test_trade_direction_windows_use_quantity_not_packet_count():
    engine = CausalFeatureEngine(initial(), D(1))
    engine.observe(trade(1, 10_000_000, "sell", "3"), 10_000_000)  # maker sell = aggressive buy
    engine.observe(trade(2, 20_000_000, "buy", "1"), 20_000_000)   # maker buy = aggressive sell
    row = values(engine, 100_000_000)
    assert row["signed_trade_volume_100ms"].value == 2
    assert row["trade_volume_imbalance_100ms"].value == D(".5")
    assert row["trade_intensity_100ms"].value == 20


def test_bbo_ofi_is_event_driven_and_snapshot_does_not_update_state():
    engine = CausalFeatureEngine(initial(), D(1))
    update = L2LevelUpdate("buy", D(100), D(3), D(4))
    engine.observe(depth(1, 20_000_000, updates=(update,)), 20_000_000)
    first = values(engine, 100_000_000)
    second = values(engine, 110_000_000)
    assert first["bbo_ofi_100ms"].value == 1
    assert second["bbo_ofi_100ms"].value == 1
    assert engine.last_client_ns == 20_000_000


def test_top_depth_cache_handles_delete_and_readd_without_duplicate_levels():
    engine = CausalFeatureEngine(initial(), D(1))
    engine.observe(depth(1, 10, updates=(
        L2LevelUpdate("buy", D(100), D(3), D(0)),
        L2LevelUpdate("buy", D(98), D(0), D(5)))), 10)
    assert engine.book.top_levels("buy", 3) == ((D(99), D(2)), (D(98), D(5)))
    engine.observe(depth(2, 20, updates=(
        L2LevelUpdate("buy", D(100), D(0), D(7)),)), 20)
    assert engine.book.top_levels("buy", 3) == (
        (D(100), D(7)), (D(99), D(2)), (D(98), D(5)))
    row = values(engine, 20)["bid_depth_5"]
    assert row.status == "INSUFFICIENT_DEPTH" and row.value is None


def test_replay_book_compacts_stale_heap_entries_during_level_churn():
    engine = CausalFeatureEngine(initial(), D(1))
    quantity = D(2)
    for index in range(1, 3001):
        next_quantity = D(0) if quantity else D(2)
        engine.observe(depth(index, index, updates=(
            L2LevelUpdate("buy", D(99), quantity, next_quantity),)), index)
        quantity = next_quantity
    assert len(engine.book._bid_heap) <= len(engine.book.bids)*4+1024
    assert engine.book.bid == D(100)


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
    engine = CausalFeatureEngine(initial(), D(1), initial_client_available_ns=10_000_000)
    event = replace(trade(1, 20_000_000, "sell", "2"), receive_ns=40_000_000)
    assert values(engine, 30_000_000)["signed_trade_volume_100ms"].status == "WARMUP"
    engine.observe(event, 40_000_000)
    assert values(engine, 110_000_000)["signed_trade_volume_100ms"].value == 2
    stale_engine = CausalFeatureEngine(initial(), D(1), stale_after_ns=50,
                                       initial_client_available_ns=10)
    stale = values(stale_engine, 100)
    assert stale["mid"].status == "STALE" and stale["mid"].value is None
    assert stale["book_age_ns"].value == 90


def test_rolling_features_expose_explicit_warmup_state():
    engine = CausalFeatureEngine(initial(), D(1))
    assert values(engine, 99_999_999)["trade_intensity_100ms"].status == "WARMUP"
    assert values(engine, 100_000_000)["trade_intensity_100ms"].status == "AVAILABLE"


def test_delivery_regression_and_future_delivery_fail_closed():
    engine = CausalFeatureEngine(initial(), D(1))
    engine.observe(trade(1, 10, "sell", "1"), 20)
    with pytest.raises(ValueError, match="regressed"):
        engine.observe(trade(2, 21, "sell", "1"), 19)
    with pytest.raises(ValueError, match="before exchange"):
        CausalFeatureEngine(initial(), D(1)).observe(trade(1, 10, "sell", "1"), 9)


def test_feature_projection_is_exact_and_rejects_unknown_names():
    engine = CausalFeatureEngine(initial(), D(1))
    points = engine.snapshot(0, ("spread_ticks", "mid"))
    assert [point.feature_name for point in points] == ["mid", "spread_ticks"]
    with pytest.raises(ValueError, match="unknown feature projection"):
        engine.snapshot(0, ("does_not_exist",))


def test_all_bbo_depth_features_reject_unknown_reconstructed_coverage():
    engine = CausalFeatureEngine(replace(initial(), coverage_ask=D(100)), D(1))
    for name in ("microprice", "microprice_minus_mid_ticks", "imbalance_1",
                 "bid_top_depth", "ask_top_depth", "total_top_depth"):
        point = values(engine, 0)[name]
        assert point.status == "INSUFFICIENT_DEPTH"
        assert point.value is None
    assert not engine.observation(0, "fixture").client_mid_coverage
