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

    @classmethod
    def from_config(cls, config):
        return cls(config.strategy,config.size,config.half_spread_ticks,config.inventory_skew_ticks,
                   config.signal_strength,config.max_inventory,config.instrument.tick)


class Features:
    def __init__(self):
        self.mids=deque(maxlen=31)
        self.volatility=None

    def on_second(self, now, mid, fresh):
        self.mids.append(float(mid) if fresh else None)
        if len(self.mids)==31 and all(v is not None and v>0 for v in self.mids):
            values=list(self.mids)
            self.volatility=statistics.pstdev(math.log(b/a)*10000 for a,b in zip(values,values[1:]))
        else:self.volatility=None


def _round(price,tick,buy):
    return (price/tick).to_integral_value(rounding=ROUND_FLOOR if buy else ROUND_CEILING)*tick


def decide(config, market, portfolio):
    center=market.mid
    if config.strategy=="inventory_skew":
        center-=config.inventory_skew_ticks*config.tick*portfolio.inventory/config.max_inventory
    elif config.strategy=="microprice":
        center+=config.signal_strength*(market.microprice-market.mid)
    half=config.half_spread_ticks*config.tick
    return DesiredQuotes(
        Quote("buy",min(market.bid,_round(center-half,config.tick,True)),config.size),
        Quote("sell",max(market.ask,_round(center+half,config.tick,False)),config.size))


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
