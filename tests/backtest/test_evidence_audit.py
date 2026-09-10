from decimal import Decimal as D

import pytest

from lobcap.backtest.evidence_audit import MakerCapacityAudit
from test_engine import trade


def order():
    return {'side':'buy','price':'100','quantity':'.01','activation_ns':0,'terminal_ns':100}


def fill(event,quantity='.001',oid='one'):
    return {'order_id':oid,'quantity':quantity,'side':'buy','time_ns':event.time_ns}


def test_audit_accepts_fractional_capacity_from_prior_public_trade_only_v2():
    events=[trade(1,1,size='.005'),trade(2,2,size='.005')]
    MakerCapacityAudit(events,'.1','.001','binance-own-orders-v2').check(fill(events[1]),order(),events[1])
    with pytest.raises(AssertionError,match='legacy per-event'):
        MakerCapacityAudit(events,'.1','.001').check(fill(events[1]),order(),events[1])


@pytest.mark.parametrize('events',[
    [trade(1,1,size='.005'),trade(2,2,size='10')], # future cannot fund current
    [trade(1,0,size='10'),trade(2,1,size='.005')], # activation tie excluded
    [trade(1,1,price='101',size='10'),trade(2,2,size='.005')], # worse price excluded
    [trade(1,1,side='sell',size='10'),trade(2,2,size='.005')], # other side excluded
])
def test_audit_rejects_overbudget_without_trusting_diagnostic_fields(events):
    source=next(e for e in events if e.trade.size==D('.005'))
    with pytest.raises(AssertionError,match='lifetime participation'):
        MakerCapacityAudit(events,'.1','.001','binance-own-orders-v2').check(fill(source),order(),source)


def test_audit_enforces_cumulative_fills_and_lot():
    events=[trade(1,1,size='.01'),trade(2,2,size='.001')]
    audit=MakerCapacityAudit(events,'.1','.001','binance-own-orders-v2')
    audit.check(fill(events[0]),order(),events[0])
    with pytest.raises(AssertionError,match='lifetime participation'):
        audit.check(fill(events[1]),order(),events[1])
    with pytest.raises(AssertionError,match='violates lot'):
        MakerCapacityAudit(events,'1','.001','binance-own-orders-v2').check(fill(events[0],'.0005'),order(),events[0])
