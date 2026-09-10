"""Scheduler parity, causal reporting and fail-closed ledger contracts."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from lobcap.backtest.accounting import Portfolio
from lobcap.backtest.arrival import run_arrival
from lobcap.backtest.artifacts import MemorySink
from lobcap.backtest.engine import run
from lobcap.backtest.types import DesiredQuotes, FillEvent, NS, Quote
from test_engine import config, depth, initial, trade
from test_execution import market


def test_zero_latency_schedulers_expose_identical_trade_flow():
    c = config(alpha=None, stale_book_ms=10000)
    observations = []
    for runner in (run, run_arrival):
        seen = []
        def strategy(m, p):
            seen.append((m.time_ns, m.trade_imbalance, m.trade_volume))
            return DesiredQuotes(None, None)
        runner(c, initial(), [trade(1, 100_000_000, side="sell", size=".003")], MemorySink(), strategy)
        observations.append(seen)
    assert observations[0] == observations[1]
    assert observations[0][1] == (250_000_000, D(1), D(".003"))
    assert next(row for row in observations[0] if row[0] == 1_250_000_000)[1:] == (None, D(0))


def test_age_cancel_created_at_decision_waits_until_after_decision():
    c = config(alpha=None, max_age_ms=250, stale_book_ms=10000)
    submits = []
    for runner in (run, run_arrival):
        sink = MemorySink()
        runner(c, initial(), [], sink,
               lambda m, p: DesiredQuotes(Quote("buy", D(100), D(".001")), None))
        submits.append([r["time_ns"] for r in sink.rows["orders"] if r["event"] == "submit"])
    assert submits[0] == submits[1]
    assert submits[0][:2] == [0, 500_000_000]


@pytest.mark.parametrize("changes", [
    {"side": "invalid"}, {"quantity": D(0)}, {"quantity": D("-.001")},
    {"quantity": D("NaN")}, {"quantity": D("Infinity")},
    {"price": D(0)}, {"price": D(-1)}, {"price": D("NaN")},
    {"price": D("Infinity")}, {"role": "unknown"},
])
def test_invalid_fills_fail_before_any_ledger_mutation(changes):
    sink = MemorySink()
    portfolio = Portfolio(config(), D(100), sink)
    before = (portfolio.cash, portfolio.inventory, portfolio.fees, sink.digest)
    fill = replace(FillEvent("order", 1, 1, "buy", D(100), D(".001"), ("source",)), **changes)
    with pytest.raises(ValueError):
        portfolio.apply_fill(fill, market(1))
    assert (portfolio.cash, portfolio.inventory, portfolio.fees, sink.digest) == before


def test_private_report_at_end_remains_pending_and_markout_uses_exchange_clock():
    c = config(alpha=None, report_ms=5900, stale_book_ms=10000, max_age_ms=10000)
    def strategy(m, p):
        return DesiredQuotes(Quote("buy", D(100), D(".001")), None)
    sink = MemorySink()
    result = run(c, initial(), [trade(1, 100_000_000), depth(2, 200_000_000, bid="99", ask="100")], sink, strategy)
    assert result.summary["ending_inventory"] == D(".001")
    assert result.summary["ending_known_inventory"] == D(0)
    assert not [r for r in sink.rows["timeline"] if r["event"] == "fill_received"]
    short = next(r for r in sink.rows["markouts"] if r["horizon_ms"] == 100)
    assert short["target_time_ns"] == 200_000_000
    assert D(short["midpoint_drift_per_unit"]) == -1
    assert result.summary["pending_reports"] > 0


@pytest.mark.parametrize("runner", [run, run_arrival])
def test_market_event_at_end_is_excluded_and_inventory_is_not_liquidated(runner):
    c = config(alpha=None, stale_book_ms=10000, max_age_ms=10000)
    sink = MemorySink()
    result = runner(c, initial(), [trade(1, 100_000_000), depth(2, 6 * NS, bid="90", ask="91")], sink,
                    lambda m, p: DesiredQuotes(Quote("buy", D(100), D(".001")), None))
    assert result.summary["ending_inventory"] == D(".001")
    assert result.summary["inventory_mtm"] == 0
    assert all(r["time_ns"] < 6 * NS for r in sink.rows["fills"])
