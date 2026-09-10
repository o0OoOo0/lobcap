"""Capacity is causal, owned by one order, and never executable on depth alone."""
from dataclasses import replace
from decimal import Decimal as D

from lobcap.backtest.types import Quote, ReplayEvent
from test_execution import setup, market, event, Book


def active():
    engine=setup(alpha=None,max_trade_participation='.1')
    engine.submit(Quote('buy',D(100),D('.002')),0,{})
    engine.advance(0,market(0),Book())
    return engine


def test_fractional_capacity_carries_forward_but_not_across_replacement():
    engine=active()
    assert not engine.on_event(event(1,'.005'))
    assert engine.on_event(event(2,'.005'))[0].quantity==D('.001')
    order=engine.orders['buy']
    engine.request_cancel(order,3,'replace')
    engine.advance(3,market(3),Book())
    engine.submit(Quote('buy',D(100),D('.002')),4,{})
    engine.advance(4,market(4),Book())
    assert not engine.on_event(event(5,'.005'))
    assert engine.orders['buy'].eligible_trade_volume==D('.005')


def test_capacity_cannot_spend_on_depth_or_exceed_current_trade():
    engine=active()
    engine.orders['buy'].queue=D(1)
    # Use queue model so the volume earns capacity but consumes the ahead queue.
    from lobcap.config.backtest import variant
    engine.config=variant(engine.config,alpha='0')
    assert not engine.on_event(event(1,'1'))
    assert not engine.on_event(ReplayEvent(2,2,2,'depth'))
    fill=engine.on_event(event(3,'.001'))[0]
    assert fill.quantity==D('.001')
    row=[r for channel,r in engine.sink.rows if channel=='execution'][-1]
    assert row['participation_budget_after']==D('.0991')


def test_activation_tie_does_not_accrue_capacity():
    engine=active()
    assert not engine.on_event(event(0,'10'))
    assert engine.orders['buy'].eligible_trade_volume==0
    assert not engine.on_event(event(1,'.005'))


def test_strict_through_diagnostics_distinguish_queue_bypass_from_volume():
    engine=active()
    engine.orders['buy'].queue=D(9)
    tr=event(1,'.01')
    tr=replace(tr,trade=replace(tr.trade,price=D('99.9')))
    assert engine.on_event(tr)[0].quantity==D('.001')
    row=[r for channel,r in engine.sink.rows if channel=='execution'][-1]
    assert row['trade_through_queue_bypass']==9
    assert row['trade_queue_reduction']==0
    assert row['trade_volume']==D('.01')
