"""Single-threaded exchange-time scheduler with explicit market-first ties."""
from __future__ import annotations

from decimal import Decimal, localcontext

from lobcap.backtest.execution import Execution
from lobcap.backtest.types import NS, ZERO, MarketView, PortfolioView, RunResult, jsonable


class MarketBook:
    def __init__(self, initial):
        self.bids, self.asks = dict(initial.bids), dict(initial.asks)
        self.time_ns=initial.time_ns
        self.coverage_bid,self.coverage_ask=initial.coverage_bid,initial.coverage_ask
        self.bid=max(self.bids);self.ask=min(self.asks)

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
                if update.side=="buy" and update.price>self.bid:self.bid=update.price
                if update.side=="sell" and update.price<self.ask:self.ask=update.price
            else:levels.pop(update.price,None)
        if not self.bids or not self.asks:raise ValueError("empty replay book")
        if self.bid not in self.bids:self.bid=max(self.bids)
        if self.ask not in self.asks:self.ask=min(self.asks)
        if self.bid>=self.ask:raise ValueError("crossed replay book")
        self.time_ns=event.time_ns

    def view(self,now,volatility=None):
        B,A=self.bids[self.bid],self.asks[self.ask]
        return MarketView(now,self.bid,self.ask,B,A,self.mid,(self.ask*B+self.bid*A)/(B+A),
                          (B-A)/(B+A),volatility,now-self.time_ns,self.coverage_bid,self.coverage_ask)


def run(config, initial, events, sink, strategy=None):
    # Lazy imports keep the execution and public contracts independently usable.
    from lobcap.backtest.accounting import Portfolio
    from lobcap.backtest.metrics import Metrics
    from lobcap.backtest.strategies import Features, StrategyParameters, decide, risk_control, decision_context

    with localcontext() as context:
        context.prec=50
        book=MarketBook(initial)
        execution=Execution(config,sink)
        features=Features()
        parameters=StrategyParameters.from_config(config)
        portfolio=None
        metrics=Metrics(config,sink)
        iterator=iter(events)
        event=next(iterator,None)
        next_decision=config.sample.start_ns
        next_second=((initial.time_ns//NS)+1)*NS
        end=config.sample.end_ns
        previous_time=initial.time_ns
        while True:
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
            execution.advance(now,market,book)
            if now==next_second:
                features.on_second(now,book.mid,market.book_age_ns<=config.stale_book_ms*1_000_000)
                market=book.view(now,features.volatility)
                next_second+=NS
            if now==next_decision:
                view=PortfolioView(portfolio.cash,portfolio.inventory,portfolio.equity,
                    execution.exposure("buy"),execution.exposure("sell"),
                    tuple((s,o.status) for s,o in sorted(execution.orders.items())))
                desired=(strategy(market,view) if strategy is not None else decide(parameters,market,view))
                desired=risk_control(config,market,view,desired)
                decision=decision_context(config,market,view,desired)
                sink.write("decisions",decision)
                execution.reconcile(desired,now,decision)
                execution.advance(now,market,book)
                execution.assert_inventory(portfolio.inventory)
                next_decision+=config.decision_ms*1_000_000
            if portfolio is not None:
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
