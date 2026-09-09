from collections import Counter
from decimal import Decimal as D

import pytest

from lobcap.backtest.replay import normalize, _stream
from lobcap.backtest.types import BookSnapshot
from lobcap.rebuild import OrderBook


def setup_book():
    book = OrderBook(D(".1"), D(".001"))
    book.load_snapshot({"lastUpdateId":10,"bids":[["100","5"]],"asks":[["101","5"]]})
    initial = BookSnapshot(((D(100),D(5)),), ((D(101),D(5)),), 0, 10, D(90), D(110))
    return book, initial


def trade(t=10, normal="2", total="2"):
    return (t,0,1,{"ts_recv_ns":t+1,"payload":{"p":"100","q":total,"nq":normal,"m":True}},"tr:1")


def depth(t=10, qty="1", seq=11, pu=10):
    return (t,1,seq,{"ts_recv_ns":t+1,"payload":{"U":seq,"u":seq,"pu":pu,"b":[["100",qty]],"a":[]}},f"dp:{seq}")


def test_trade_before_equal_timestamp_depth_and_residual_only():
    book, initial = setup_book()
    events=list(normalize(book,initial,[depth()],[trade()],Counter()))
    assert [e.kind for e in events] == ["trade","depth"]
    d=events[1].depletion[0]
    assert (d.visible,d.trades,d.nontrade,d.excess_trade)==(D(4),D(2),D(2),D(0))
    assert events[1].interval_start_ns==0


def test_same_timestamp_depth_coalesces_and_rpi_is_excluded():
    book, initial = setup_book()
    events=list(normalize(book,initial,[depth(qty="4"),depth(qty="3",seq=12,pu=11)],
                          [trade(normal="1",total="2")],Counter()))
    assert len(events)==2
    assert events[0].trade.size == D(1)
    assert events[1].source_sequences==(11,12)
    assert events[1].depletion[0].nontrade == D(1)


def test_replenishment_is_diagnostic_not_negative_depletion():
    book, initial = setup_book()
    events=list(normalize(book,initial,[depth(qty="5")],[trade()],Counter()))
    assert events[-1].depletion[0].nontrade==0
    assert events[-1].depletion[0].excess_trade==2


@pytest.mark.parametrize("bad", ["missing", "negative", "too_large", "gap"])
def test_invalid_inputs_block(bad):
    book, initial = setup_book()
    tr=trade(); dp=depth()
    if bad=="missing": del tr[3]["payload"]["nq"]
    elif bad=="negative":tr[3]["payload"]["nq"]="-1"
    elif bad=="too_large":tr[3]["payload"]["nq"]="3"
    else: dp[3]["payload"]["pu"]=9
    with pytest.raises(ValueError):list(normalize(book,initial,[dp],[tr],Counter()))
