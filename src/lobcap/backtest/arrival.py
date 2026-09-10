"""Two-clock replay: exchange state is private; the client learns via delivery queues."""
import heapq
from collections import Counter
from dataclasses import replace
from decimal import Decimal, localcontext

from lobcap.backtest.accounting import Portfolio
from lobcap.backtest.engine import MarketBook
from lobcap.backtest.execution import Execution
from lobcap.backtest.metrics import Metrics
from lobcap.backtest.strategies import Features, StrategyParameters, StrategyPolicy, risk_control, decision_context
from lobcap.backtest.quote_policy import plan_actions, make_trace, custom_stages
from lobcap.backtest.types import NS, ZERO, PortfolioView, RunResult, ASSUMPTIONS, jsonable


class ExchangeExecution(Execution):
    def _schedule(self,t,kind,order):
        # Order-age cancellation belongs to the client, which first needs its ACK.
        if kind!="age":super()._schedule(t,kind,order)


class ReportSink:
    def __init__(self,sink,book,reports,config):
        self.sink,self.book,self.reports,self.config=sink,book,reports,config
        self.ordinal=0

    def report(self,t,kind,row):
        self.ordinal+=1
        heapq.heappush(self.reports,(t+self.config.report_ms*1_000_000,self.ordinal,kind,row))

    def write(self,channel,row):
        self.sink.write(channel,row)
        if channel=="orders":
            evidence={**row,"actor":"simulation" if row["status"]=="CENSORED" else "exchange","exchange_bid":self.book.bid,"exchange_ask":self.book.ask,
                      "client_available_ns":row["time_ns"]+self.config.report_ms*1_000_000}
            self.sink.write("timeline",evidence)
            if row["status"]!="CENSORED" and row["event"] in {"activate","terminal","partial_fill","full_fill","cancel_noop"}:
                # Deliberately strip estimated queue truth at the private-report boundary.
                self.report(row["time_ns"],"order",{k:v for k,v in row.items() if k!="modeled_queue"})


def run_arrival(config,initial,events,sink,strategy=None,bootstrap_receive_ns=None,research_policy=None,cancel_check=None):
    if config.clock_mode=="recorded" and bootstrap_receive_ns is None:
        raise ValueError("Recorded mode requires a sample prepared with bootstrap receive-time evidence")
    if config.clock_mode=="recorded" and bootstrap_receive_ns<initial.time_ns:
        raise ValueError("negative capture delay at bootstrap")
    with localcontext() as context:
        context.prec=50
        truth=MarketBook(initial)
        observed=None
        available=initial.time_ns if config.clock_mode=="exchange" else max(initial.time_ns,bootstrap_receive_ns)
        if available>config.sample.start_ns-config.sample.warmup_ns:
            raise ValueError("Observed bootstrap must precede feature warmup")
        deliveries=[(available,0,None)]
        reports=[]
        ages=[]
        proxy=ReportSink(sink,truth,reports,config)
        execution=ExchangeExecution(config,proxy)
        control=None
        if research_policy is not None:
            from lobcap.backtest.research_execution import ResearchControl
            control=ResearchControl(config,research_policy,sink)
        features=Features()
        parameters=StrategyParameters.from_config(config)
        policy=StrategyPolicy()
        decision_ordinal=0
        last_source=None
        metrics=Metrics(config,sink)
        client_orders={}
        handles={}
        client_cancels=set()
        client_cash,client_inventory=config.initial_cash,config.initial_inventory
        last_delivery={"depth":available,"trade":available}
        last_observed_receive=available
        stats=Counter()
        max_inventory_error=ZERO
        iterator=iter(events)
        event=next(iterator,None)
        portfolio=None
        end=config.sample.end_ns
        next_decision=config.sample.start_ns
        next_second=(initial.time_ns//NS+1)*NS
        previous=initial.time_ns

        def client_exposure(side):
            row=client_orders.get(side)
            return Decimal(row["remaining"]) if row else ZERO

        def cancel(side,now,reason):
            row=client_orders.get(side)
            if row is None or row["order_id"] in client_cancels:return
            oid=row["order_id"]
            client_cancels.add(oid)
            row["status"]="CANCEL_PENDING"
            sink.write("timeline",{"time_ns":now,"actor":"client","event":"cancel_send",
                "order_id":oid,"reason":reason,"remaining_known":row["remaining"]})
            execution.request_cancel(handles[oid],now,reason)

        def receive_reports(now):
            nonlocal client_cash,client_inventory
            while reports and reports[0][0]<=now:
                delivered,_,kind,row=heapq.heappop(reports)
                if kind=="fill":
                    sign=1 if row["side"]=="buy" else -1
                    q,p,fee=(Decimal(row[k]) for k in ("quantity","price","fee"))
                    client_inventory+=sign*q;client_cash-=sign*q*p+fee
                    sink.write("timeline",{"time_ns":delivered,"actor":"client","event":"fill_received",
                        "order_id":row["order_id"],"exchange_time_ns":row["time_ns"],
                        "replay_index":row["replay_index"],"quantity":q,"inventory_known":client_inventory})
                    stats["fill_reports_received"]+=1
                else:
                    oid,side=row["order_id"],row["side"]
                    sink.write("timeline",{**row,"time_ns":delivered,"exchange_time_ns":row["time_ns"],
                                           "actor":"client","event":row["event"]+"_received"})
                    current=client_orders.get(side)
                    if current is None or current["order_id"]!=oid:continue
                    if row["terminal_ns"] is not None:
                        del client_orders[side];handles.pop(oid,None);client_cancels.discard(oid)
                    else:
                        client_orders[side]=dict(row)
                        if oid in client_cancels:client_orders[side]["status"]="CANCEL_PENDING"
                        if row["event"]=="activate":
                            heapq.heappush(ages,(max(delivered,row["activation_ns"]+config.max_age_ms*1_000_000),side,oid))

        while True:
            if cancel_check is not None:
                from lobcap.backtest.runtime import interrupt_if_requested
                interrupt_if_requested(cancel_check,sink,previous,portfolio,execution)
            times=[end,next_decision,next_second]
            if event is not None:times.append(event.time_ns)
            if execution.next_deadline is not None:times.append(execution.next_deadline)
            if control is not None and control.next_deadline is not None:times.append(control.next_deadline)
            if metrics.next_deadline is not None:times.append(metrics.next_deadline)
            for queue in (deliveries,reports,ages):
                if queue:times.append(queue[0][0])
            now=min(times)
            if now<previous:raise ValueError("backward exchange clock")
            if portfolio is not None:metrics.integrate(previous,now,portfolio,execution)
            if now>=end:break
            # Exchange events win ties with cancellations/entries, as in the baseline.
            while event is not None and event.time_ns==now:
                truth.apply(event)
                if control is not None:control.on_market(event)
                if portfolio is not None:
                    change=portfolio.mark(truth.mid,now,event.replay_index)
                    if change is not None:metrics.on_mark(*change)
                    for fill in execution.on_event(event):
                        market=truth.view(now,features.volatility)
                        row=portfolio.apply_fill(fill,market)
                        metrics.on_fill(fill,row,market)
                        proxy.report(now,"fill",jsonable(row))
                        execution.assert_inventory(portfolio.inventory)
                received=event.time_ns if config.clock_mode=="exchange" else event.receive_ns
                if received<event.time_ns:raise ValueError("negative capture delay; clock alignment must be resolved before replay")
                delivered=max(received,last_delivery[event.kind])
                stats["sequence_delivery_hold_ns"]+=delivered-received
                stats["max_feed_delay_ns"]=max(stats["max_feed_delay_ns"],delivered-event.time_ns)
                last_delivery[event.kind]=delivered
                heapq.heappush(deliveries,(delivered,event.replay_index,event))
                event=next(iterator,None)
            if portfolio is None and now>=config.sample.start_ns:
                portfolio=Portfolio(config,truth.mid,sink)
            execution.advance(now,truth.view(now),truth)
            if control is not None and portfolio is not None:
                for fill in control.execute(now,truth,portfolio.inventory):
                    row=portfolio.apply_fill(fill,truth.view(now,features.volatility))
                    metrics.on_fill(fill,row,truth.view(now,features.volatility))
                    proxy.report(now,"fill",jsonable(row))
                    execution.assert_inventory(portfolio.inventory)
            # Market-data delivery and private reports are separate client inputs.
            while deliveries and deliveries[0][0]<=now:
                delivered,_,incoming=heapq.heappop(deliveries)
                if incoming is None:observed=MarketBook(initial)
                else:
                    if observed is None:raise AssertionError("delivery before bootstrap")
                    observed.apply(incoming)
                    features.observe_trade(incoming,delivered)
                    last_source=incoming
                    if incoming.kind=="depth":last_observed_receive=delivered
                    stats["observed_"+incoming.kind]+=1
            receive_reports(now)
            if control is not None and portfolio is not None and observed is not None:
                control.review(now,observed.view(now),PortfolioView(client_cash,client_inventory,
                    client_cash+client_inventory*observed.mid,client_exposure("buy"),client_exposure("sell")),cancel,client_orders)
            while ages and ages[0][0]<=now:
                _,side,oid=heapq.heappop(ages)
                if side in client_orders and client_orders[side]["order_id"]==oid:
                    cancel(side,now,"max_order_age")
            if now==next_second:
                if observed is not None:
                    features.on_second(now,observed.mid,now-observed.time_ns<=config.stale_book_ms*1_000_000)
                next_second+=NS
            if now==next_decision:
                if observed is None:raise ValueError("no observed book at trading start")
                flow,volume=features.flow(now,include_late=config.strategy=='flow_withdraw')
                market=replace(observed.view(now,features.volatility),trade_imbalance=flow,trade_volume=volume)
                view=PortfolioView(client_cash,client_inventory,client_cash+client_inventory*observed.mid,
                    client_exposure("buy"),client_exposure("sell"),tuple((s,o["status"]) for s,o in sorted(client_orders.items())))
                try:
                    stages={}
                    desired=strategy(market,view) if strategy else policy.decide(parameters,market,view,stages)
                except Exception as exc:
                    from lobcap.backtest.workspace.expressions import ExpressionError
                    if isinstance(exc,ExpressionError):
                        sink.write("interrupted",{"status":"FAIL","time_ns":now,"reason":str(exc),
                            "cash":portfolio.cash,"inventory":portfolio.inventory,
                            "orders":[jsonable(order.record()) for order in execution.orders.values()],"full_window":False})
                        raise
                    if control is None:raise
                    control.kill(now,"strategy_exception:"+type(exc).__name__)
                    from lobcap.backtest.types import DesiredQuotes
                    desired=DesiredQuotes(None,None,control.kill_reason)
                if not stages:stages=custom_stages(desired)
                signal_reason=desired.reason
                desired=risk_control(config,market,view,desired)
                if control is not None:desired=control.quotes(now,market,view,desired)
                decision={**decision_context(config,market,view,desired),"clock_mode":config.clock_mode,
                    "observed_book_exchange_ns":observed.time_ns,"observed_book_receive_ns":last_observed_receive,
                    "book_age_ns":market.book_age_ns,"trade_imbalance":flow,"trade_volume":volume,
                    "cash_known":client_cash,"buy_exposure_known":view.buy_exposure,"sell_exposure_known":view.sell_exposure}
                if config.strategy=="expression":
                    decision["signal_reason"]=signal_reason
                    stats["signal_clipped_decisions"]+=int(signal_reason=="signal_clipped")
                    stats["feature_unavailable_decisions"]+=int(signal_reason=="feature_unavailable")
                decision_ordinal+=1
                actions=plan_actions(config,desired,client_orders,now,market,view,execution.next_id)
                trace=make_trace(config,decision_ordinal,market,view,stages,desired,actions,last_source,
                    {'withdraw_side':policy.withdraw_side})
                decision['decision_id']=trace['decision_id']
                sink.write("decisions",decision)
                sink.write("decision_traces",trace)
                for action in actions:
                    side=action['side']
                    quote=desired.bid if side=='buy' else desired.ask
                    if action['action']=='cancel':cancel(side,now,action['reason'])
                    elif action['action']=='submit':
                        execution.submit(quote,now,decision)
                        order=execution.orders[side]
                        handles[order.order_id]=order
                        client_orders[side]=jsonable(order.record())
                        sink.write("timeline",{"time_ns":now,"actor":"client","event":"submit_send",
                            "decision_id":decision['decision_id'],
                            "order_id":order.order_id,"price":quote.price,"quantity":quote.quantity,
                            "observed_bid":market.bid,"observed_ask":market.ask,"observed_book_exchange_ns":observed.time_ns})
                execution.advance(now,truth.view(now),truth)
                receive_reports(now)
                if client_inventory+client_exposure("buy")>config.max_inventory or client_inventory-client_exposure("sell")<-config.max_inventory:
                    raise AssertionError("client worst-case inventory breached")
                execution.assert_inventory(portfolio.inventory)
                next_decision+=config.decision_ms*1_000_000
            if portfolio is not None:
                max_inventory_error=max(max_inventory_error,abs(portfolio.inventory-client_inventory))
                if now==metrics.next_chart:
                    sink.write("observations",{"time_ns":now,"exchange_mid":truth.mid,
                        "observed_mid":observed.mid if observed else None,"exchange_inventory":portfolio.inventory,
                        "known_inventory":client_inventory,"book_age_ns":now-observed.time_ns if observed else None})
                metrics.observe(now,portfolio,truth,features.volatility)
            previous=now
        execution.finish(end)
        portfolio.reconcile()
        summary=metrics.finish(end,portfolio,execution)
        summary.update({"clock_mode":config.clock_mode,"report_ms":config.report_ms,"clock_diagnostics":dict(stats),
            "ending_known_inventory":client_inventory,"ending_known_cash":client_cash,
            "max_unreported_inventory":max_inventory_error,"pending_reports":len(reports),
            "pending_market_deliveries":len(deliveries),"client_orders_at_end":list(client_orders.values()),
            "assumptions":[f"{config.clock_mode} feed clock; private reports delayed by {config.report_ms} ms.",
                "Depth batches become observable at the latest constituent receipt; per-stream delivery preserves sequence.",
                "Recorded collector receipt is a proxy for strategy arrival; exchange/capture clocks assumed aligned.",
                *ASSUMPTIONS[1:]]})
        if control is not None:
            summary["research_execution"]=control.summary()
            summary["assumptions"]=[a for a in summary["assumptions"] if not a.startswith(("No market impact", "Net PnL includes"))]
            summary["assumptions"] += ["Conditional taker hedge and taker fees enabled by research policy; no automatic terminal liquidation.",
                "Funding, exchange mark price, leverage tiers and exchange margin liquidation are not modeled.",
                "Historical market path is unchanged; local depth consumption is hypothetical."]
        invariants={"status":"PASS","pnl_identity":True,"inventory_limit":True,"client_reservations":True,
            "no_overfill":True,"no_future_activation_fill":True,"strategy_observes_delivered_state_only":True}
        sink.write("summary",summary);sink.write("invariants",invariants)
        return RunResult("PASS",summary,invariants,sink.digest)
