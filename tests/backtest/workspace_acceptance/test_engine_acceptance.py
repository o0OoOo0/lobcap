"""R03/R05/R07/R10: causal execution substance, not metadata parity."""
from dataclasses import replace

import pytest

from lobcap.backtest.artifacts import MemorySink
from lobcap.backtest.engine import run
from lobcap.backtest.runtime import SimulationCanceled
from lobcap.config.backtest import variant
from test_engine import config, initial, trade, depth


@pytest.mark.parametrize('builtin,expression', [
    ('microprice', 'signal_strength * (microprice-mid)/tick'),
    ('inventory_skew', '-inventory_skew_ticks * inventory/max_inventory'),
])
def test_r03_builtin_formula_same_quotes_fills_ledger(builtin, expression):
    base = config(strategy=builtin, report_ms=1, alpha=None, stale_book_ms=10000,
                  half_spread_ticks='2', initial_inventory='.002')
    formula = variant(base, strategy='expression', signal_expression=expression)
    events = [trade(1, 100_000_000), depth(2, 1_000_000_000, bid='99', ask='101'),
              trade(3, 1_100_000_000, side='sell', price='101')]
    left, right = MemorySink(), MemorySink()
    run(base, initial(), events, left)
    run(formula, initial(), events, right)
    quote_fields = ('time_ns', 'buy_price', 'sell_price', 'buy_quantity', 'sell_quantity')
    assert [tuple(r[k] for k in quote_fields) for r in left.rows['decisions']] == [tuple(r[k] for k in quote_fields) for r in right.rows['decisions']]
    # Decision audit carries the intentionally different strategy name/formula.
    assert [{k: v for k, v in r.items() if k != 'decision'} for r in left.rows.get('fills', [])] == [{k: v for k, v in r.items() if k != 'decision'} for r in right.rows.get('fills', [])]
    assert left.rows['ledger'] == right.rows['ledger']


def test_r05_recorded_unreceived_depth_cannot_change_formula_decisions():
    base = config(strategy='expression', signal_expression='(microprice-mid)/tick',
                  clock_mode='recorded', stale_book_ms=10000)
    left, right = MemorySink(), MemorySink()
    first = replace(depth(1, 100_000_000, bid='99', ask='101'), receive_ns=600_000_000)
    altered = replace(depth(1, 100_000_000, bid='100', ask='103'), receive_ns=600_000_000)
    run(base, initial(), [first], left, bootstrap_receive_ns=0)
    run(base, initial(), [altered], right, bootstrap_receive_ns=0)
    for channel in ('decisions', 'fills', 'ledger'):
        assert [r for r in left.rows.get(channel, []) if r['time_ns'] < 100_000_000] == [r for r in right.rows.get(channel, []) if r['time_ns'] < 100_000_000]
    # Exchange depth may affect eligibility before receive, but observable quote decisions may not.
    fields = ('time_ns', 'mid', 'buy_price', 'sell_price')
    assert [tuple(r[k] for k in fields) for r in left.rows['decisions'] if r['time_ns'] < 600_000_000] == [tuple(r[k] for k in fields) for r in right.rows['decisions'] if r['time_ns'] < 600_000_000]


def test_r07_cancel_writes_unresolved_evidence_not_full_window_summary():
    base = config(strategy='expression', signal_expression='0', alpha=None, stale_book_ms=10000)
    sink = MemorySink()
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        return calls >= 3
    with pytest.raises(SimulationCanceled):
        run(base, initial(), [trade(1, 100_000_000), depth(2, 1_000_000_000)], sink, cancel_check=cancel)
    row, = sink.rows['interrupted']
    assert row['full_window'] is False and row['terminal_liquidation'] is False
    assert row['time_ns'] < base.sample.end_ns
    assert not sink.rows.get('summary')
