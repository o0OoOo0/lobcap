"""Independent volume checks from public replay, never execution diagnostics."""
from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal as D


class MakerCapacityAudit:
    def __init__(self, events, participation, lot, version=None):
        if version not in (None, 'binance-own-orders-v1', 'binance-own-orders-v2'):
            raise ValueError(f'unsupported execution contract: {version}')
        self.v2=version=='binance-own-orders-v2'
        self.events=sorted((e for e in events if e.trade is not None),key=lambda e:e.replay_index)
        self.times=[e.time_ns for e in self.events]
        assert self.times==sorted(self.times), 'trade timestamps regress'
        self.participation,self.lot=D(participation),D(lot)
        self.used=defaultdict(D)
        self.state={}

    def check(self, fill, order, source):
        quantity=D(fill['quantity'])
        assert quantity>0 and source.trade is not None
        assert order['activation_ns']<source.time_ns==fill['time_ns']<=order['terminal_ns']
        assert source.trade.maker_side==order['side']==fill['side']
        price=D(order['price'])
        eligible=lambda e: e.trade.maker_side==order['side'] and (
            e.trade.price<=price if order['side']=='buy' else e.trade.price>=price)
        assert eligible(source), 'fill has no eligible trade evidence'
        self.used[source.replay_index]+=quantity
        assert self.used[source.replay_index]<=source.trade.size, 'historical trade volume reused'
        if not self.v2:
            assert self.used[source.replay_index]<=source.trade.size*self.participation, 'legacy per-event capacity exceeded'
            return
        assert quantity % self.lot==0, 'own fill violates lot'
        oid=fill['order_id']
        cursor,volume,filled,last=self.state.get(oid,(bisect_right(self.times,order['activation_ns']),D(0),D(0),-1))
        assert source.replay_index>=last, 'order fill evidence regresses'
        while cursor<len(self.events) and self.events[cursor].replay_index<=source.replay_index:
            event=self.events[cursor]
            if eligible(event):volume+=event.trade.size
            cursor+=1
        filled+=quantity
        assert filled<=D(order['quantity']), 'order overfilled'
        assert filled<=volume*self.participation, 'order lifetime participation capacity exceeded'
        self.state[oid]=(cursor,volume,filled,source.replay_index)
