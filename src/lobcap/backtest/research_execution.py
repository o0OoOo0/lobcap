"""Bounded research controls; decisions use delivered state, fills use exchange depth.

This is not an exchange margin engine. Risk equity is midpoint-valued and all
limits are explicit research assumptions, not Binance account rules.
"""
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_FLOOR

from lobcap.backtest.types import DesiredQuotes, FillEvent, ZERO, jsonable


@dataclass(frozen=True)
class ResearchPolicy:
    version: str = "bounded-research-v1"
    loss_limit: Decimal = Decimal("10")
    drawdown_limit: Decimal = Decimal("30")
    max_order: Decimal = Decimal("0.002")
    max_gross_fraction: Decimal = Decimal("0.8")
    hedge_trigger: Decimal = Decimal("0.003")
    hedge_target: Decimal = Decimal("0.001")
    hedge_ms: int = 20
    hedge_price_guard_bps: Decimal = Decimal("10")
    hedge_enabled: bool = True

    def validate(self):
        if self.version!="bounded-research-v1":raise ValueError("unknown research policy")
        for name in ("loss_limit","drawdown_limit","max_order","max_gross_fraction","hedge_trigger","hedge_target","hedge_price_guard_bps"):
            value=getattr(self,name)
            if not isinstance(value,Decimal) or not value.is_finite() or value<0:
                raise ValueError(f"invalid policy {name}")
        if min(self.loss_limit,self.drawdown_limit,self.max_order,self.hedge_price_guard_bps)<=0:
            raise ValueError("risk limits must be positive")
        if not 0<self.max_gross_fraction<=1 or not self.hedge_target<self.hedge_trigger:
            raise ValueError("invalid exposure/hedge limits")
        if type(self.hedge_ms) is not int or self.hedge_ms<0 or type(self.hedge_enabled) is not bool:
            raise ValueError("invalid hedge configuration")
        return self


class ResearchControl:
    def __init__(self,config,policy,sink):
        self.config,self.policy,self.sink=config,policy.validate(),sink
        self.initial=self.peak=None
        self.kill_reason=None
        self.hedging=False
        self.pending=None
        self.retry_after=0
        self.ordinal=0
        self.last_depth_index=0
        self.last_depth_refs=()
        self.consumed={}
        self.hedge_quantity=self.hedge_fees=ZERO
        self.hedge_count=0
        self.skipped_hedges=0
        sink.write("risk",{"time_ns":config.sample.start_ns,"event":"policy","policy":policy})

    @property
    def next_deadline(self):return self.pending[0] if self.pending else None

    def on_market(self,event):
        if event.kind=="depth":
            self.last_depth_index,self.last_depth_refs=event.replay_index,event.source_refs
            # Only a published update at this level resets its hypothetical usage.
            for update in event.updates:self.consumed.pop((update.side,update.price),None)

    def kill(self,now,reason):
        if self.kill_reason is None:
            self.kill_reason=reason
            self.sink.write("risk",{"time_ns":now,"event":"kill","reason":reason})

    def review(self,now,market,view,cancel,client_orders):
        if self.initial is None:self.initial=self.peak=view.equity
        self.peak=max(self.peak,view.equity)
        if self.initial-view.equity>=self.policy.loss_limit:self.kill(now,"sample_loss_limit")
        if self.peak-view.equity>=self.policy.drawdown_limit:self.kill(now,"drawdown_limit")
        gross=(abs(view.inventory)+view.buy_exposure+view.sell_exposure)*market.mid
        if gross>max(ZERO,view.equity)*self.policy.max_gross_fraction:
            self.kill(now,"gross_exposure_limit")
        if self.policy.hedge_enabled and abs(view.inventory)>=self.policy.hedge_trigger:
            self.hedging=True
        if self.hedging and abs(view.inventory)<=self.policy.hedge_target and self.pending is None and now>=self.retry_after:
            self.hedging=False
        if self.kill_reason or self.hedging:
            for side in tuple(client_orders):cancel(side,now,self.kill_reason or "inventory_hedge")
        target=ZERO if self.kill_reason else self.policy.hedge_target
        if (self.policy.hedge_enabled and (self.kill_reason or self.hedging) and
            not client_orders and self.pending is None and now>=self.retry_after and
            abs(view.inventory)>target and market.book_age_ns<=self.config.stale_book_ms*1_000_000):
            quantity=min(abs(view.inventory)-target,self.policy.max_order)
            quantity=self.lots(quantity)
            if quantity:
                side="sell" if view.inventory>0 else "buy"
                self.ordinal+=1
                self.pending=(now+self.policy.hedge_ms*1_000_000,side,quantity,market.mid,self.ordinal)
                self.sink.write("hedges",{"time_ns":now,"event":"submit","order_id":f"hedge-{self.ordinal:08d}",
                    "side":side,"quantity":quantity,"observed_mid":market.mid,"due_ns":self.pending[0]})

    def lots(self,value):
        return (max(ZERO,value)/self.config.instrument.lot).to_integral_value(rounding=ROUND_FLOOR)*self.config.instrument.lot

    def quotes(self,now,market,view,desired):
        if self.kill_reason or self.hedging or self.pending:
            return DesiredQuotes(None,None,self.kill_reason or "inventory_hedge")
        # Current slots are retained/canceled serially. New slots reserve the
        # opposite outstanding order, including its cancel-pending remainder.
        room=max(ZERO,view.equity*self.policy.max_gross_fraction/market.mid-abs(view.inventory))
        bids,asks=view.buy_exposure,view.sell_exposure
        quotes=[]
        for quote,own,other in ((desired.bid,bids,asks),(desired.ask,asks,bids)):
            if quote is None:quotes.append(None);continue
            available=max(ZERO,room-other)
            if own==0 and other==0:available/=2
            size=self.lots(min(quote.quantity,self.policy.max_order,available))
            quote=replace(quote,quantity=size) if size else None
            quotes.append(quote)
            if quote:
                depth=market.bid_size if quote.side=="buy" else market.ask_size
                self.sink.write("capacity",{"time_ns":now,"side":quote.side,"quantity":size,
                    "order_to_top_depth":size/depth if depth else None,
                    "order_to_recent_volume":size/market.trade_volume if market.trade_volume else None,
                    "recent_volume":market.trade_volume,"scope":"diagnostic; no empirical capacity claim"})
        return DesiredQuotes(*quotes,reason=desired.reason)

    def execute(self,now,book,inventory):
        if self.pending is None or self.pending[0]>now:return []
        _,side,requested,reference,ordinal=self.pending
        self.pending=None
        self.retry_after=now+max(self.config.report_ms*1_000_000,self.config.decision_ms*1_000_000,1)
        remaining=self.lots(min(requested,max(ZERO,inventory if side=="sell" else -inventory)))
        if now-book.time_ns>self.config.stale_book_ms*1_000_000 or not self.last_depth_refs:
            self.skipped_hedges+=1
            self.sink.write("hedges",{"time_ns":now,"event":"reject","order_id":f"hedge-{ordinal:08d}","reason":"stale_or_unlinked_book"})
            return []
        maker_side="buy" if side=="sell" else "sell"
        levels=book.bids if side=="sell" else book.asks
        fills=[]
        for price,quantity in sorted(levels.items(),reverse=side=="sell"):
            if not book.coverage_bid<=price<=book.coverage_ask:continue
            adverse=(price-reference) if side=="buy" else (reference-price)
            if adverse/reference*10000>self.policy.hedge_price_guard_bps:break
            key=(maker_side,price)
            available=max(ZERO,quantity-self.consumed.get(key,ZERO))
            size=self.lots(min(remaining,available))
            if size:
                self.consumed[key]=self.consumed.get(key,ZERO)+size
                fills.append(FillEvent(f"hedge-{ordinal:08d}",now,self.last_depth_index,side,price,size,self.last_depth_refs,
                    role="taker",reason="reduce_only_hedge",decision=(("mid",reference),)))
                remaining-=size
                self.hedge_quantity+=size
                self.hedge_fees+=price*size*self.config.taker_bps/10000
            if not remaining:break
        self.hedge_count+=bool(fills)
        self.sink.write("hedges",{"time_ns":now,"event":"result","order_id":f"hedge-{ordinal:08d}",
            "requested":requested,"filled":sum((f.quantity for f in fills),ZERO),
            "vwap":sum((f.quantity*f.price for f in fills),ZERO)/sum((f.quantity for f in fills),ZERO) if fills else None,
            "source_refs":self.last_depth_refs,"unfilled":requested-sum((f.quantity for f in fills),ZERO)})
        return fills

    def summary(self):
        return {"policy":jsonable(self.policy),"kill_reason":self.kill_reason,"hedge_count":self.hedge_count,
            "hedge_quantity":self.hedge_quantity,"hedge_fees":self.hedge_fees,"rejected_hedges":self.skipped_hedges,
            "pending_hedge":self.pending,"loss_window":"whole bounded sample, not a UTC day",
            "margin_model":"not implemented; midpoint gross exposure cap only",
            "funding":"not included; missing historical funding input",
            "market_impact":"fixed historical path; hypothetical depth consumption is not endogenous impact"}
