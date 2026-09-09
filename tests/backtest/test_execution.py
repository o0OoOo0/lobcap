from dataclasses import replace
from decimal import Decimal as D

from lobcap.backtest.execution import Execution
from lobcap.backtest.types import MarketView, Quote, ReplayEvent, Depletion
from lobcap.config.backtest import load_backtest_config, variant
from lobcap.config.experiment import ROOT
from lobcap.l2.schema import L2Trade


class Sink:
    def __init__(self):self.rows=[]
    def write(self,channel,row):self.rows.append((channel,row))


class Book:
    def quantity(self,side,price):return D(0)


def setup(**changes):
    c=variant(load_backtest_config(ROOT/"configs/backtest/binance_demo.yaml"),**changes)
    return Execution(c,Sink())


def market(t):
    return MarketView(t,D(100),D(101),D(1),D(1),D("100.5"),D("100.5"),D(0),None,0,D(90),D(110))


def event(t,qty=".001"):
    return ReplayEvent(1,t,t,"trade",("trade:1",),trade=L2Trade("buy",D(100),D(qty)))


def test_entry_and_cancel_ties_and_partial_accounting():
    x=setup(entry_ms=10,cancel_ms=10,alpha=None)
    x.submit(Quote("buy",D(100),D(".002")),0,{})
    assert not x.on_event(event(10_000_000))
    x.advance(10_000_000,market(10_000_000),Book())
    assert not x.on_event(event(10_000_000))
    order=x.orders["buy"]
    x.request_cancel(order,10_000_000,"replace")
    fills=x.on_event(event(20_000_000))
    assert fills[0].cancel_pending and fills[0].quantity==D(".001")
    x.advance(20_000_000,market(20_000_000),Book())
    assert order.status=="CANCELED" and order.remaining==D(".001")
    assert x.stats["partial_orders"]==1


def test_cancel_before_entry_and_marketable_rejection():
    x=setup(entry_ms=10,cancel_ms=0)
    x.submit(Quote("buy",D(100),D(".001")),0,{})
    order=x.orders["buy"];x.request_cancel(order,0,"stop")
    x.advance(0,market(0),Book());x.advance(10_000_000,market(10_000_000),Book())
    assert order.reason=="canceled_before_entry" and x.stats["activated"]==0
    x.submit(Quote("buy",D(101),D(".001")),20_000_000,{})
    x.advance(30_000_000,market(30_000_000),Book())
    assert x.stats["rejected"]==1


def test_full_fill_cancel_noop_and_no_overfill():
    x=setup(cancel_ms=10,alpha=None)
    x.submit(Quote("buy",D(100),D(".001")),0,{})
    x.advance(0,market(0),Book())
    x.request_cancel(x.orders["buy"],1,"replace")
    assert x.on_event(event(2,"100"))[0].quantity==D(".001")
    assert not x.on_event(event(3,"100"))
    x.advance(10_000_001,market(10_000_001),Book())
    assert x.stats["cancel_after_fill_noop"]==1


def test_depletion_spanning_activation_is_skipped():
    x=setup(alpha=1)
    x.submit(Quote("buy",D(100),D(".001")),10,{})
    x.advance(10,market(10),Book());x.orders["buy"].queue=D(2)
    x.on_event(ReplayEvent(1,20,20,"depth",interval_start_ns=0,
        depletion=(Depletion("buy",D(100),D(2),D(0),D(2),D(0)),)))
    assert x.orders["buy"].queue==2 and x.stats["skipped_nontrade_quantity"]==2
