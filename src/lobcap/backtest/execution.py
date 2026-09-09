"""Serial own-order lifecycle. Queue state stays private to this module."""
from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from lobcap.backtest.types import (OrderRecord, FillEvent, PortfolioView, ZERO, jsonable)
from lobcap.l2.fill_models import advance_queue


@dataclass(slots=True)
class _Order:
    order_id: str
    side: str
    price: Decimal
    quantity: Decimal
    remaining: Decimal
    submitted_ns: int
    decision: tuple
    status: str = "PENDING_ENTRY"
    activation_ns: int | None = None
    cancel_requested_ns: int | None = None
    terminal_ns: int | None = None
    reason: str = ""
    queue: Decimal = ZERO
    terminal: bool = False

    def record(self):
        return OrderRecord(self.order_id,self.side,self.price,self.quantity,self.remaining,
            self.status,self.submitted_ns,self.activation_ns,self.cancel_requested_ns,self.terminal_ns,self.reason)


class Execution:
    def __init__(self, config, sink):
        self.config, self.sink = config, sink
        self.orders = {}
        self.heap = []
        self.ordinal = self.next_id = 0
        self.replacement_wait = {}
        self.stats = Counter()
        self.stats.update({k:ZERO for k in ("activated_quantity","filled_quantity","skipped_nontrade_quantity","order_lifetime_ns")})

    def _schedule(self, t, kind, order):
        self.ordinal += 1
        heapq.heappush(self.heap,(t,{"cancel":0,"entry":1,"age":2}[kind],order.side,order.order_id,self.ordinal,kind,order))

    @property
    def next_deadline(self):
        return self.heap[0][0] if self.heap else None

    def _emit(self, order, now, event):
        self.sink.write("orders", {**jsonable(order.record()),"time_ns":now,"event":event,"modeled_queue":order.queue})

    def submit(self, quote, now, decision):
        if quote.side in self.orders:
            raise AssertionError("serial replacement forbids overlapping same-side orders")
        self.next_id += 1
        order = _Order(f"order-{self.next_id:08d}",quote.side,quote.price,quote.quantity,quote.quantity,now,tuple(decision.items()))
        self.orders[quote.side] = order
        self.stats["submitted"] += 1
        self._emit(order,now,"submit")
        self._schedule(now+self.config.entry_ms*1_000_000,"entry",order)

    def request_cancel(self, order, now, reason):
        if order.terminal or order.cancel_requested_ns is not None:
            return
        order.cancel_requested_ns=now
        order.status="CANCEL_PENDING"
        order.reason=reason
        self.stats["cancel_requests"] += 1
        self._emit(order,now,"cancel_request")
        self._schedule(now+self.config.cancel_ms*1_000_000,"cancel",order)

    def _finish(self, order, now, status, reason):
        if order.terminal:
            return
        order.terminal, order.terminal_ns, order.status, order.reason=True,now,status,reason
        if self.orders.get(order.side) is order:
            del self.orders[order.side]
        if status=="CANCELED" and reason=="replace":
            self.replacement_wait.setdefault(order.side,now)
        self.stats[status.lower()] += 1
        filled=order.quantity-order.remaining
        if order.activation_ns is not None:
            self.stats["order_lifetime_ns"] += now-order.activation_ns
            if filled > 0:
                self.stats["orders_with_fill"] += 1
                self.stats["full_orders" if order.remaining==0 else "partial_orders"] += 1
        self._emit(order,now,"terminal")

    def advance(self, now, market, book):
        while self.heap and self.heap[0][0] <= now:
            t,_,_,_,_,kind,order=heapq.heappop(self.heap)
            if order.terminal:
                if kind=="cancel" and order.status=="FILLED":
                    self.stats["cancel_after_fill_noop"] += 1
                    self._emit(order,t,"cancel_noop")
                continue
            if kind=="cancel":
                reason="canceled_before_entry" if order.activation_ns is None else order.reason
                self._finish(order,t,"CANCELED",reason)
            elif kind=="entry":
                invalid = (market is None or market.book_age_ns>self.config.stale_book_ms*1_000_000
                           or market.bid>=market.ask or not market.coverage_bid <= order.price <= market.coverage_ask)
                marketable = market is not None and (order.price>=market.ask if order.side=="buy" else order.price<=market.bid)
                if invalid or marketable:
                    self._finish(order,t,"REJECTED","invalid_book" if invalid else "post_only_marketable")
                else:
                    order.activation_ns=t
                    if order.side in self.replacement_wait:
                        self.stats["replacement_downtime_ns"] += t-self.replacement_wait.pop(order.side)
                    if order.cancel_requested_ns is None:order.status="ACTIVE"
                    order.queue=book.quantity(order.side,order.price)
                    self.stats["activated"] += 1
                    self.stats["activated_quantity"] += order.quantity
                    self._emit(order,t,"activate")
                    self._schedule(t+self.config.max_age_ms*1_000_000,"age",order)
            else:
                self.request_cancel(order,t,"max_order_age")

    def on_event(self, event):
        fills=[]
        for side in ("buy","sell"):
            order=self.orders.get(side)
            if order is None or order.activation_ns is None or order.activation_ns>=event.time_ns:
                continue
            nontrade=ZERO
            for d in event.depletion:
                if d.side==side and d.price==order.price:
                    if order.activation_ns <= event.interval_start_ns:
                        nontrade=d.nontrade
                    else:
                        self.stats["skipped_nontrade_quantity"] += d.nontrade
            tr=event.trade
            executable=ZERO
            if tr is not None and tr.maker_side==side and (tr.price<=order.price if side=="buy" else tr.price>=order.price):
                executable=tr.size
            transition=advance_queue(order.queue,order.remaining,nontrade,executable,self.config.model)
            if nontrade or executable:
                self.sink.write("execution", {"time_ns":event.time_ns,"replay_index":event.replay_index,
                    "order_id":order.order_id,"queue_before":order.queue,"queue_after":transition.queue,
                    "remaining_before":order.remaining,"remaining_after":transition.remaining,
                    "nontrade_depletion":nontrade,"nontrade_reduction":transition.nontrade_reduction,
                    "trade_volume":executable,"trade_queue_reduction":transition.trade_reduction,
                    "fill_quantity":transition.filled,"source_refs":event.source_refs})
            order.queue,order.remaining=transition.queue,transition.remaining
            if transition.filled:
                self.stats["filled_quantity"] += transition.filled
                self.stats["fill_events"] += 1
                pending=order.cancel_requested_ns is not None
                self.stats["cancel_pending_fills"] += int(pending)
                fills.append(FillEvent(order.order_id,event.time_ns,event.replay_index,side,order.price,
                    transition.filled,event.source_refs,reason="same_price" if tr.price==order.price else "trade_through",
                    cancel_pending=pending,decision=order.decision))
                self._emit(order,event.time_ns,"partial_fill" if order.remaining else "full_fill")
                if not order.remaining:self._finish(order,event.time_ns,"FILLED","fully_filled")
        return fills

    def reconcile(self, desired, now, decision):
        for side,quote in (("buy",desired.bid),("sell",desired.ask)):
            order=self.orders.get(side)
            if order is not None:
                if quote is None or quote.price!=order.price or quote.quantity!=order.remaining:
                    self.request_cancel(order,now,desired.reason if quote is None else "replace")
            elif quote is not None:
                self.submit(quote,now,decision)

    def exposure(self, side):
        order=self.orders.get(side)
        return order.remaining if order else ZERO

    def assert_inventory(self, inventory):
        if inventory+self.exposure("buy")>self.config.max_inventory or inventory-self.exposure("sell") < -self.config.max_inventory:
            raise AssertionError("worst-case inventory limit breached")

    def finish(self, now):
        for order in tuple(self.orders.values()):
            self._finish(order,now,"CENSORED","sample_end_pending" if order.activation_ns is None else "sample_end_active")
        for started in self.replacement_wait.values():
            self.stats["replacement_downtime_ns"] += now-started
        self.replacement_wait.clear()
