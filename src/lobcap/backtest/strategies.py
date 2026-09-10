"""Interpretable strategies and causal quote-risk controls."""
from collections import deque
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import math
import statistics

from lobcap.backtest.types import Quote, DesiredQuotes, ZERO


@dataclass(frozen=True, slots=True)
class StrategyParameters:
    strategy: str
    size: Decimal
    half_spread_ticks: Decimal
    inventory_skew_ticks: Decimal
    signal_strength: Decimal
    max_inventory: Decimal
    tick: Decimal
    maker_bps: Decimal = ZERO
    flow_threshold: Decimal = Decimal("0.6")
    flow_min_volume: Decimal = Decimal("0.005")
    volatility_spread_multiplier: Decimal = Decimal(1)
    signal_expression: str = ""
    expression_parameters: tuple = ()
    max_signal_ticks: Decimal = Decimal(10)
    lot: Decimal = Decimal('.001')

    @classmethod
    def from_config(cls, config):
        return cls(config.strategy,config.size,config.half_spread_ticks,config.inventory_skew_ticks,
                   config.signal_strength,config.max_inventory,config.instrument.tick,config.maker_bps,
                   config.flow_threshold,config.flow_min_volume,config.volatility_spread_multiplier,
                   config.signal_expression,config.expression_parameters,config.max_signal_ticks,config.instrument.lot)


class StrategyPolicy:
    """Per-run causal strategy memory. Contains no simulator or queue handles."""
    def __init__(self):self.withdraw_side=None

    def decide(self, config, market, portfolio, trace=None):
        return decide(config,market,portfolio,trace=trace,state=self)


class Features:
    def __init__(self):
        self.mids=deque(maxlen=31)
        self.volatility=None
        self.trades=deque()
        self.delivered_trades=deque()

    def observe_trade(self,event,now):
        if event.trade is not None:
            self.delivered_trades.append((now,event.trade.size,1 if event.trade.maker_side=="sell" else -1))
        if event.trade is not None and now-event.time_ns<=1_000_000_000:
            self.trades.append((now,event.trade.size,1 if event.trade.maker_side=="sell" else -1))

    def flow(self,now,include_late=False):
        while self.trades and self.trades[0][0]<=now-1_000_000_000:self.trades.popleft()
        while self.delivered_trades and self.delivered_trades[0][0]<=now-1_000_000_000:self.delivered_trades.popleft()
        trades=self.delivered_trades if include_late else self.trades
        volume=sum((row[1] for row in trades),ZERO)
        signed=sum((row[1]*row[2] for row in trades),ZERO)
        return (signed/volume if volume else None),volume

    def on_second(self, now, mid, fresh):
        self.mids.append(float(mid) if fresh else None)
        if len(self.mids)==31 and all(v is not None and v>0 for v in self.mids):
            values=list(self.mids)
            self.volatility=statistics.pstdev(math.log(b/a)*10000 for a,b in zip(values,values[1:]))
        else:self.volatility=None


def _round(price,tick,buy):
    return (price/tick).to_integral_value(rounding=ROUND_FLOOR if buy else ROUND_CEILING)*tick


def decide(config, market, portfolio, trace=None, state=None):
    def targets(bid,ask,reason):
        raw=DesiredQuotes(bid,ask,reason)
        rounded=[];limited=[]
        for side,q in (('buy',bid),('sell',ask)):
            if q is None:rounded.append(None);limited.append(None);continue
            price=_round(q.price,config.tick,side=='buy')
            size=(q.quantity/config.lot).to_integral_value(rounding=ROUND_FLOOR)*config.lot
            r=Quote(side,price,size) if size>0 else None
            rounded.append(r)
            p=min(price,market.bid) if side=='buy' else max(price,market.ask)
            limited.append(Quote(side,p,size) if size>0 else None)
        result=DesiredQuotes(*limited,reason)
        if trace is not None:trace.update(raw_target=raw,tick_rounded_target=DesiredQuotes(*rounded,reason),policy_limited_target=result)
        return result
    if config.strategy=='no_trade':return targets(None,None,'no_trade')
    if config.strategy in {'join_bbo','inventory_size','flow_withdraw'}:
        buy=sell=config.size
        reason=config.strategy
        if config.strategy=='inventory_size':
            ratio=portfolio.inventory/config.max_inventory
            if ratio>=Decimal('.6'):buy=max(ZERO,buy-config.lot);reason='long_inventory_reduce_buy'
            elif ratio<=Decimal('-.6'):sell=max(ZERO,sell-config.lot);reason='short_inventory_reduce_sell'
        if config.strategy=='flow_withdraw':
            state=state if state is not None else StrategyPolicy()
            flow=market.trade_imbalance
            if flow is None or market.trade_volume<config.flow_min_volume:state.withdraw_side=None
            elif flow>=Decimal('.6'):state.withdraw_side='sell'
            elif flow<=Decimal('-.6'):state.withdraw_side='buy'
            elif state.withdraw_side=='sell' and flow<Decimal('.3'):state.withdraw_side=None
            elif state.withdraw_side=='buy' and flow>Decimal('-.3'):state.withdraw_side=None
            if state.withdraw_side=='buy':buy=ZERO;reason='flow_withdraw_buy'
            elif state.withdraw_side=='sell':sell=ZERO;reason='flow_withdraw_sell'
        return targets(Quote('buy',market.bid,buy),Quote('sell',market.ask,sell),reason)
    center=market.mid
    reason="quote"
    if config.strategy=="expression":
        from lobcap.backtest.workspace.expressions import compile_expression, FeatureUnavailable
        values={name:getattr(market,name) for name in ("bid","ask","mid","microprice","imbalance","volatility_bps","trade_imbalance","trade_volume")}
        values.update({"tick":config.tick,"spread_ticks":(market.ask-market.bid)/config.tick,
            "inventory":portfolio.inventory,"max_inventory":config.max_inventory,
            "signal_strength":config.signal_strength,"inventory_skew_ticks":config.inventory_skew_ticks})
        values.update(dict(config.expression_parameters))
        try:
            signal=compile_expression(config.signal_expression,tuple(name for name,_ in config.expression_parameters)).evaluate(values)
        except FeatureUnavailable:
            return targets(None,None,"feature_unavailable")
        bounded=min(max(signal,-config.max_signal_ticks),config.max_signal_ticks)
        center+=config.tick*bounded
        if bounded!=signal:reason="signal_clipped"
    if config.strategy=="inventory_skew":
        center-=config.inventory_skew_ticks*config.tick*portfolio.inventory/config.max_inventory
    elif config.strategy=="microprice":
        center+=config.signal_strength*(market.microprice-market.mid)
    half=config.half_spread_ticks*config.tick
    if config.strategy=="volatility_adaptive":
        if market.volatility_bps is None:return targets(None,None,"volatility_warmup")
        half=max(half,market.mid*Decimal(str(market.volatility_bps))*config.volatility_spread_multiplier/10000)
    if config.strategy=="flow_guard":
        if market.volatility_bps is None:return targets(None,None,"volatility_warmup")
        center=market.microprice-config.inventory_skew_ticks*config.tick*portfolio.inventory/config.max_inventory
        half=max(half,market.mid*(max(ZERO,config.maker_bps)+Decimal(str(market.volatility_bps))*config.volatility_spread_multiplier)/10000)
        bid=Quote("buy",center-half,config.size)
        ask=Quote("sell",center+half,config.size)
        if market.trade_imbalance is not None and market.trade_volume>=config.flow_min_volume:
            if market.trade_imbalance>=config.flow_threshold:return targets(bid,None,"buy_flow_suppress_ask")
            if market.trade_imbalance<=-config.flow_threshold:return targets(None,ask,"sell_flow_suppress_bid")
        return targets(bid,ask,"flow_guard")
    return targets(Quote("buy",center-half,config.size),Quote("sell",center+half,config.size),reason)


def risk_control(config, market, portfolio, desired):
    def stop(reason):return DesiredQuotes(None,None,reason)
    if market.bid<=0 or market.bid>=market.ask:return stop("invalid_book")
    if market.book_age_ns>config.stale_book_ms*1_000_000:return stop("stale_book")
    if not market.coverage_bid<=market.bid<market.ask<=market.coverage_ask:return stop("unknown_depth_coverage")
    if (market.ask-market.bid)/config.instrument.tick<config.min_spread_ticks:return stop("minimum_spread")
    thresholds=(config.volatility_widen_bps,config.volatility_reduce_bps,config.volatility_stop_bps)
    if any(v is not None for v in thresholds) and market.volatility_bps is None:
        return stop("volatility_warmup")
    vol=Decimal(str(market.volatility_bps or 0))
    if config.volatility_stop_bps is not None and vol>=config.volatility_stop_bps:
        return stop("volatility_stop")
    widen=config.volatility_widen_bps is not None and vol>=config.volatility_widen_bps
    reduce=config.volatility_reduce_bps is not None and vol>=config.volatility_reduce_bps
    quotes=[]
    for side,quote in (("buy",desired.bid),("sell",desired.ask)):
        if quote is None:quotes.append(None);continue
        if quote.side!=side or quote.price<=0 or quote.quantity<0:
            raise ValueError("invalid strategy quote")
        price=quote.price
        if widen:price+=(-1 if side=="buy" else 1)*config.half_spread_ticks*config.instrument.tick
        price=_round(price,config.instrument.tick,side=="buy")
        price=min(price,market.bid) if side=="buy" else max(price,market.ask)
        capacity=config.max_inventory-portfolio.inventory if side=="buy" else config.max_inventory+portfolio.inventory
        size=min(quote.quantity*(Decimal(".5") if reduce else 1),max(ZERO,capacity))
        size=(size/config.instrument.lot).to_integral_value(rounding=ROUND_FLOOR)*config.instrument.lot
        quotes.append(Quote(side,price,size) if size>0 and market.coverage_bid<=price<=market.coverage_ask else None)
    return DesiredQuotes(*quotes,reason="volatility_widen_reduce" if widen or reduce else desired.reason)


def decision_context(config,market,portfolio,desired):
    return {"time_ns":market.time_ns,"strategy":config.strategy,"inventory":portfolio.inventory,
        "bid":market.bid,"ask":market.ask,"mid":market.mid,"imbalance":market.imbalance,
        "microprice":market.microprice,"volatility_bps":market.volatility_bps,
        "spread_ticks":(market.ask-market.bid)/config.instrument.tick,
        "quoted_spread_ticks":(desired.ask.price-desired.bid.price)/config.instrument.tick if desired.bid and desired.ask else None,
        "buy_price":desired.bid.price if desired.bid else None,"sell_price":desired.ask.price if desired.ask else None,
        "buy_quantity":desired.bid.quantity if desired.bid else ZERO,"sell_quantity":desired.ask.quantity if desired.ask else ZERO,
        "reason":desired.reason}
