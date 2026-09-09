from dataclasses import replace
from decimal import Decimal as D

import pytest

from lobcap.backtest.accounting import Portfolio
from lobcap.backtest.artifacts import MemorySink, ArtifactSink
from lobcap.backtest.engine import run
from lobcap.backtest.types import BookSnapshot, ReplayEvent, L2Trade, L2LevelUpdate, FillEvent, DesiredQuotes, Quote, NS
from lobcap.config.backtest import load_backtest_config, variant
from lobcap.config.experiment import ROOT
from test_execution import market


def config(**changes):
    c=variant(load_backtest_config(ROOT/"configs/backtest/binance_demo.yaml"),**changes)
    return replace(c,sample=replace(c.sample,start_ns=0,end_ns=6*NS,warmup_ns=0))


def initial():
    return BookSnapshot(((D(100),D(1)),),((D(101),D(1)),),0,0,D(90),D(110))


def trade(index,t,side="buy",price="100",size=".001"):
    return ReplayEvent(index,t,t,"trade",(f"tr:{index}",),trade=L2Trade(side,D(price),D(size)))


def depth(index,t,bid="100",ask="101",oldbid="100",oldask="101"):
    updates=[]
    for side,old,new in (("buy",oldbid,bid),("sell",oldask,ask)):
        if old!=new:
            updates.extend([L2LevelUpdate(side,D(old),D(1),D(0)),L2LevelUpdate(side,D(new),D(0),D(1))])
    return ReplayEvent(index,t,t,"depth",(f"dp:{index}",),updates=tuple(updates),interval_start_ns=max(0,t-NS))


def test_fee_rebate_and_exact_inventory_mtm():
    for fee in ("1","-1"):
        c=config(maker_bps=fee)
        sink=MemorySink();p=Portfolio(c,D(100),sink)
        fill=FillEvent("test",1,1,"buy",D(99),D(".001"),("source:1",))
        p.apply_fill(fill,market(1));p.mark(D(102),2,2)
        assert p.inventory==D(".001")
        assert p.spread_capture==D(".001") and p.inventory_mtm==D(".002")
        assert p.net_pnl==D(".003")-D(99)*D(".001")*D(fee)/10000
        p.reconcile()


def test_replay_determinism_prefix_invariance_and_isolation():
    c=config(alpha=None,stale_book_ms=10000)
    seen=[]
    def strategy(m,p):
        assert not hasattr(m,"queue") and not hasattr(p,"alpha")
        seen.append((m.time_ns,p.inventory))
        return DesiredQuotes(Quote("buy",m.bid,D(".001")),None)
    prefix=[trade(1,100_000_000),depth(2,NS)]
    a=MemorySink();b=MemorySink();repeat=MemorySink()
    result=run(c,initial(),prefix+[depth(3,2*NS,bid="99",ask="100")],a,strategy)
    run(c,initial(),prefix+[depth(3,2*NS,bid="102",ask="103")],b,strategy)
    again=run(c,initial(),prefix+[depth(3,2*NS,bid="99",ask="100")],repeat,strategy)
    assert result.digest==again.digest
    for channel in ("decisions","fills","ledger","orders"):
        assert [r for r in a.rows[channel] if r["time_ns"]<2*NS]==[r for r in b.rows[channel] if r["time_ns"]<2*NS]
    assert result.summary["sharpe"] is None
    assert result.summary["ending_inventory"]==D(".001")


def test_markout_sign_staleness_and_end_censoring():
    c=config(alpha=None,stale_book_ms=10000)
    events=[trade(1,100_000_000),depth(2,200_000_000,bid="99",ask="100"),
            trade(3,5_900_000_000,side="sell",price="101")]
    sink=MemorySink();run(c,initial(),events,sink)
    marks=sink.rows["markouts"]
    first=next(r for r in marks if r["fill_replay_index"]==1 and r["horizon_ms"]==100)
    assert D(first["markout_per_unit"])==D("-.5")
    assert D(first["midpoint_drift_per_unit"])==D(-1)
    assert any(r["missing_reason"]=="stale_midpoint" for r in marks)
    assert any(r["missing_reason"]=="sample_end" for r in marks)


def test_artifact_sink_has_same_digest(tmp_path):
    c=config(alpha=None)
    memory=MemorySink();disk=ArtifactSink(tmp_path/"run")
    events=[trade(1,100_000_000)]
    a=run(c,initial(),events,memory)
    b=run(c,initial(),events,disk);disk.close()
    assert a.digest==b.digest


def test_market_first_cancel_tie_and_serial_replacement():
    c=config(alpha=None,cancel_ms=100,decision_ms=100,stale_book_ms=10000)
    def strategy(m,p):
        return DesiredQuotes(Quote("buy",D(100) if m.time_ns==0 else D(99),D(".002")),None)
    sink=MemorySink()
    run(c,initial(),[trade(1,200_000_000,size=".001")],sink,strategy)
    assert sink.rows["fills"][0]["cancel_pending"]
    orders=sink.rows["orders"]
    first_cancel=next(r for r in orders if r["order_id"]=="order-00000001" and r["event"]=="terminal")
    next_submit=next(r for r in orders if r["order_id"]=="order-00000002" and r["event"]=="submit")
    assert first_cancel["time_ns"]<=next_submit["time_ns"]
