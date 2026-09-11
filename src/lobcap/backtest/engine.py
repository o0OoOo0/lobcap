"""Single-threaded exchange-time scheduler with explicit market-first ties."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, localcontext
import heapq

from lobcap.backtest.execution import Execution
from lobcap.backtest.types import NS, ZERO, MarketView, PortfolioView, RunResult, jsonable


class MarketBook:
    def __init__(self, initial):
        self.bids, self.asks = dict(initial.bids), dict(initial.asks)
        self.time_ns=initial.time_ns
        self.coverage_bid,self.coverage_ask=initial.coverage_bid,initial.coverage_ask
        self._bid_versions={price:0 for price in self.bids}
        self._ask_versions={price:0 for price in self.asks}
        self._bid_heap=[(-price,0) for price in self.bids]
        self._ask_heap=[(price,0) for price in self.asks]
        heapq.heapify(self._bid_heap);heapq.heapify(self._ask_heap)
        self.bid=self._best("buy");self.ask=self._best("sell")

    def _touch(self,side,price,present):
        versions=self._bid_versions if side=="buy" else self._ask_versions
        version=versions.get(price,-1)+1
        versions[price]=version
        if present:
            heapq.heappush(self._bid_heap if side=="buy" else self._ask_heap,
                           (-price if side=="buy" else price,version))

    def _best(self,side):
        levels=self.bids if side=="buy" else self.asks
        versions=self._bid_versions if side=="buy" else self._ask_versions
        heap=self._bid_heap if side=="buy" else self._ask_heap
        while heap:
            key,version=heap[0]
            price=-key if side=="buy" else key
            if price in levels and versions.get(price)==version:return price
            heapq.heappop(heap)
        raise ValueError("empty replay book")

    def top_levels(self,side,count):
        """Return best aggregate levels without sorting the complete reconstructed book."""
        if count <= 0: return ()
        levels=self.bids if side=="buy" else self.asks
        versions=self._bid_versions if side=="buy" else self._ask_versions
        heap=self._bid_heap if side=="buy" else self._ask_heap
        selected=[]; result=[]
        while heap and len(result)<count:
            key,version=heapq.heappop(heap)
            price=-key if side=="buy" else key
            if price not in levels or versions.get(price)!=version:continue
            selected.append((key,version));result.append((price,levels[price]))
        for row in selected:heapq.heappush(heap,row)
        return tuple(result)

    def quantity(self, side, price):
        return (self.bids if side=="buy" else self.asks).get(price,ZERO)

    @property
    def mid(self):return (self.bid+self.ask)/2

    def apply(self, event):
        if event.kind!="depth":return
        for update in event.updates:
            levels=self.bids if update.side=="buy" else self.asks
            if levels.get(update.price,ZERO)!=update.quantity_before:
                raise ValueError("normalized quantity_before does not match reconstructed book")
            if update.quantity_after:
                levels[update.price]=update.quantity_after
                self._touch(update.side,update.price,True)
            else:
                levels.pop(update.price,None)
                self._touch(update.side,update.price,False)
        if not self.bids or not self.asks:raise ValueError("empty replay book")
        self.bid=self._best("buy");self.ask=self._best("sell")
        if self.bid>=self.ask:raise ValueError("crossed replay book")
        self.time_ns=event.time_ns

    def view(self,now,volatility=None):
        B,A=self.bids[self.bid],self.asks[self.ask]
        return MarketView(now,self.bid,self.ask,B,A,self.mid,(self.ask*B+self.bid*A)/(B+A),
                          (B-A)/(B+A),volatility,now-self.time_ns,self.coverage_bid,self.coverage_ask)


def run(config, initial, events, sink, strategy=None, bootstrap_receive_ns=None, research_policy=None, cancel_check=None):
    if research_policy is not None or config.clock_mode=="recorded" or config.report_ms or config.strategy in {"flow_guard","expression"}:
        from lobcap.backtest.arrival import run_arrival
        return run_arrival(config,initial,events,sink,strategy,bootstrap_receive_ns,research_policy,cancel_check)
    # Lazy imports keep the execution and public contracts independently usable.
    from lobcap.backtest.accounting import Portfolio
    from lobcap.backtest.metrics import Metrics
    from lobcap.backtest.strategies import Features, StrategyParameters, StrategyPolicy, risk_control, decision_context
    from lobcap.backtest.quote_policy import plan_actions, make_trace, custom_stages

    with localcontext() as context:
        context.prec=50
        book=MarketBook(initial)
        execution=Execution(config,sink)
        features=Features()
        parameters=StrategyParameters.from_config(config)
        policy=StrategyPolicy(parameters)
        decision_ordinal=0
        last_source=None
        portfolio=None
        metrics=Metrics(config,sink)
        iterator=iter(events)
        event=next(iterator,None)
        next_decision=config.sample.start_ns
        next_second=((initial.time_ns//NS)+1)*NS
        end=config.sample.end_ns
        previous_time=initial.time_ns
        while True:
            if cancel_check is not None:
                from lobcap.backtest.runtime import interrupt_if_requested
                interrupt_if_requested(cancel_check,sink,previous_time,portfolio,execution)
            deadlines=[end,next_decision,next_second]
            if event is not None:deadlines.append(event.time_ns)
            if execution.next_deadline is not None:deadlines.append(execution.next_deadline)
            if metrics.next_deadline is not None:deadlines.append(metrics.next_deadline)
            now=min(deadlines)
            if now<previous_time:raise ValueError("event time moved backward")
            if portfolio is not None:
                metrics.integrate(previous_time,now,portfolio,execution)
            if now>=end:break
            # Every market event at this timestamp precedes every internal action.
            while event is not None and event.time_ns==now:
                book.apply(event)
                features.observe_trade(event, now)
                if event.trade is not None:
                    policy.observe_trade(now,event.time_ns,event.trade.price,event.trade.maker_side,event.trade.size,
                        book.mid,now-book.time_ns<=config.stale_book_ms*1_000_000)
                last_source=event
                if portfolio is not None:
                    change=portfolio.mark(book.mid,now,event.replay_index)
                    if change is not None:metrics.on_mark(*change)
                    for fill in execution.on_event(event):
                        row=portfolio.apply_fill(fill,book.view(now,features.volatility))
                        metrics.on_fill(fill,row,book.view(now,features.volatility))
                        execution.assert_inventory(portfolio.inventory)
                event=next(iterator,None)
            if portfolio is None and now>=config.sample.start_ns:
                portfolio=Portfolio(config,book.mid,sink)
                portfolio.mark(book.mid,now,0)
            market=book.view(now,features.volatility)
            # Age requests precede decisions, but their newly-created
            # zero-delay cancellations take effect in the post-decision drain.
            execution.advance(now,market,book,defer_new_cancels=True)
            if now==next_second:
                features.on_second(now,book.mid,market.book_age_ns<=config.stale_book_ms*1_000_000)
                policy.on_second(now,book.mid,market.book_age_ns<=config.stale_book_ms*1_000_000)
                market=book.view(now,features.volatility)
                next_second+=NS
            if now==next_decision:
                flow, volume = features.flow(now,include_late=config.strategy=='flow_withdraw')
                market = replace(market, trade_imbalance=flow, trade_volume=volume)
                view=PortfolioView(portfolio.cash,portfolio.inventory,portfolio.equity,
                    execution.exposure("buy"),execution.exposure("sell"),
                    tuple((s,o.status) for s,o in sorted(execution.orders.items())))
                stages={}
                desired=(strategy(market,view) if strategy is not None else policy.decide(parameters,market,view,stages))
                if not stages:stages=custom_stages(desired)
                desired=risk_control(config,market,view,desired)
                decision=decision_context(config,market,view,desired)
                decision_ordinal+=1
                actions=plan_actions(config,desired,{s:jsonable(o.record()) for s,o in execution.orders.items()},now,market,view,execution.next_id)
                trace=make_trace(config,decision_ordinal,market,view,stages,desired,actions,last_source,
                                 policy.diagnostics())
                decision['decision_id']=trace['decision_id']
                sink.write("decisions",decision)
                sink.write("decision_traces",trace)
                execution.reconcile(desired,now,decision,actions)
                next_decision+=config.decision_ms*1_000_000
            execution.advance(now,market,book)
            if portfolio is not None:
                execution.assert_inventory(portfolio.inventory)
                metrics.observe(now,portfolio,book,features.volatility)
            previous_time=now
        if portfolio is None:raise ValueError("sample has no trading interval")
        execution.finish(end)
        portfolio.reconcile()
        summary=metrics.finish(end,portfolio,execution)
        invariants={"status":"PASS","pnl_identity":True,"inventory_limit":True,
                    "no_overfill":True,"no_future_activation_fill":True}
        sink.write("summary",summary)
        sink.write("invariants",invariants)
        return RunResult("PASS",summary,invariants,sink.digest)
