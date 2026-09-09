"""Streaming diagnostics with fixed-time observations and censored markouts."""
from __future__ import annotations

import heapq
import statistics
from collections import defaultdict
from decimal import Decimal

from lobcap.backtest.types import ZERO, NS, ASSUMPTIONS, jsonable


def ratio(a,b):return float(a/b) if b else None


def bucket(value,edges):
    if value is None:return "unavailable"
    value=float(value)
    previous="-inf"
    for edge in edges:
        if value<float(edge):return f"[{previous},{edge})"
        previous=str(edge)
    return f"[{previous},inf)"


class Metrics:
    HORIZONS=(100,1000,5000)

    def __init__(self,config,sink):
        self.config,self.sink=config,sink
        self.pending=[]
        self.ordinal=0
        self.inventory_integral=ZERO
        self.active_ns={"buy":0,"sell":0}
        self.cancel_pending_ns={"buy":0,"sell":0}
        self.entry_pending_ns={"buy":0,"sell":0}
        self.replacement_downtime_ns={"buy":0,"sell":0}
        self.next_chart=config.sample.start_ns
        self.minute_equity=[]
        self.markout_totals={h:{"observations":0,"valid":0,"quantity":ZERO,"markout_value":ZERO,"drift_value":ZERO} for h in self.HORIZONS}
        self.groups={}
        self.last_mtm=ZERO

    @property
    def next_deadline(self):return min(self.next_chart,self.pending[0][0]) if self.pending else self.next_chart

    def integrate(self,start,end,portfolio,execution):
        dt=max(0,min(end,self.config.sample.end_ns)-max(start,self.config.sample.start_ns))
        self.inventory_integral+=portfolio.inventory*dt
        for side in self.active_ns:
            order=execution.orders.get(side)
            if order is not None:
                if order.activation_ns is not None:
                    self.active_ns[side]+=dt
                    if order.cancel_requested_ns is not None:self.cancel_pending_ns[side]+=dt
                else:self.entry_pending_ns[side]+=dt

    def _groups(self,fill,row,market):
        decision=dict(fill.decision)
        return {
            "inventory_fraction":bucket(row["inventory_before"]/self.config.max_inventory,(-.5,0,.5,1)),
            "prevailing_spread_ticks":bucket(row["prevailing_spread_ticks"],(1,2,5,10)),
            "quoted_spread_ticks":bucket(decision.get("quoted_spread_ticks"),(1,2,5,10)),
            "imbalance":bucket(market.imbalance,(-.5,0,.5)),
            "volatility_bps":bucket(market.volatility_bps,(.1,.5,1,2)),
            "utc_five_minute":str((fill.time_ns//NS%86400)//300*5),
            "fill_model":self.config.model.name,
            "entry_ms":str(self.config.entry_ms),"cancel_ms":str(self.config.cancel_ms),
            "maker_bps":str(self.config.maker_bps),
        }

    def _group(self,dimension,label):
        key=dimension+":"+label
        if key not in self.groups:
            self.groups[key]={"dimension":dimension,"bucket":label,"fill_events":0,"quantity":ZERO,
                "spread_capture":ZERO,"fees":ZERO,"inventory_mtm":ZERO,
                "markouts":{str(h):{"valid":0,"observations":0,"quantity":ZERO,"markout_value":ZERO,"drift_value":ZERO} for h in self.HORIZONS}}
        return self.groups[key]

    def on_fill(self,fill,row,market):
        groups=self._groups(fill,row,market)
        for dimension,label in groups.items():
            group=self._group(dimension,label)
            group["fill_events"]+=1;group["quantity"]+=fill.quantity
            group["spread_capture"]+=row["spread_contribution"];group["fees"]+=row["fee"]
        for h in self.HORIZONS:
            self.ordinal+=1
            heapq.heappush(self.pending,(fill.time_ns+h*1_000_000,self.ordinal,h,fill,row["mid_at_fill"],groups))

    def _resolve(self,item,book,censored=None):
        target,_,h,fill,mid_at_fill,groups=item
        reason=censored
        if reason is None and target-book.time_ns>250_000_000:reason="stale_midpoint"
        if reason is None and book.time_ns>target:raise AssertionError("future midpoint used for markout")
        sign=1 if fill.side=="buy" else -1
        markout=sign*(book.mid-fill.price) if reason is None else None
        drift=sign*(book.mid-mid_at_fill) if reason is None else None
        self.sink.write("markouts",{"order_id":fill.order_id,"fill_replay_index":fill.replay_index,
            "fill_time_ns":fill.time_ns,"target_time_ns":target,"horizon_ms":h,"quantity":fill.quantity,
            "side":fill.side,"markout_per_unit":markout,"midpoint_drift_per_unit":drift,
            "midpoint_time_ns":book.time_ns if reason is None else None,"missing_reason":reason})
        targets=[self.markout_totals[h]]+[self._group(k,v)["markouts"][str(h)] for k,v in groups.items()]
        for totals in targets:
            totals["observations"]+=1
            if reason is None:
                totals["valid"]+=1;totals["quantity"]+=fill.quantity
                totals["markout_value"]+=markout*fill.quantity
                totals["drift_value"]+=drift*fill.quantity

    def observe(self,now,portfolio,book,volatility):
        while self.pending and self.pending[0][0]<=now:
            self._resolve(heapq.heappop(self.pending),book)
        if now==self.next_chart:
            self.sink.write("timeseries",{"time_ns":now,"gross_pnl":portfolio.gross_pnl,"net_pnl":portfolio.net_pnl,
                "inventory":portfolio.inventory,"equity":portfolio.equity,"spread_capture":portfolio.spread_capture,
                "inventory_mtm":portfolio.inventory_mtm,"fees":portfolio.fees,"mid":book.mid})
            if (now-self.config.sample.start_ns)%(60*NS)==0:self.minute_equity.append(portfolio.equity)
            self.next_chart+=NS
        self.last_book=book

    def on_mark(self,inventory,contribution):
        label=bucket(inventory/self.config.max_inventory,(-.5,0,.5,1))
        self._group("inventory_fraction",label)["inventory_mtm"]+=contribution

    @staticmethod
    def _final_markout(t):
        return {**t,"coverage":ratio(t["valid"],t["observations"]),
            "volume_weighted_markout":t["markout_value"]/t["quantity"] if t["quantity"] else None,
            "volume_weighted_midpoint_drift":t["drift_value"]/t["quantity"] if t["quantity"] else None}

    def finish(self,end,portfolio,execution):
        while self.pending:self._resolve(heapq.heappop(self.pending),self.last_book,"sample_end")
        self.minute_equity.append(portfolio.equity)
        self.sink.write("timeseries",{"time_ns":end,"gross_pnl":portfolio.gross_pnl,"net_pnl":portfolio.net_pnl,
            "inventory":portfolio.inventory,"equity":portfolio.equity,"spread_capture":portfolio.spread_capture,
            "inventory_mtm":portfolio.inventory_mtm,"fees":portfolio.fees,"mid":portfolio.mid})
        returns=[float(b/a-1) for a,b in zip(self.minute_equity,self.minute_equity[1:]) if a>0]
        sharpe=None
        if len(returns)>=60 and len(returns)==len(self.minute_equity)-1 and statistics.stdev(returns)>0:
            sharpe=statistics.mean(returns)/statistics.stdev(returns)
        for group in self.groups.values():
            group["markouts"]={h:self._final_markout(v) for h,v in group["markouts"].items()}
        self.sink.write("conditional_metrics",{"buckets":list(self.groups.values()),
            "note":"Dimensions overlap; do not sum across dimensions. MTM is partitioned by held inventory; markouts are diagnostics, not additional PnL."})
        s=execution.stats
        duration=end-self.config.sample.start_ns
        return {"net_pnl":portfolio.net_pnl,"gross_pnl":portfolio.gross_pnl,"fees":portfolio.fees,
            "fee_charges":portfolio.charges,"rebates":portfolio.rebates,"spread_capture":portfolio.spread_capture,
            "inventory_mtm":portfolio.inventory_mtm,"ending_inventory":portfolio.inventory,"ending_cash":portfolio.cash,
            "ending_equity":portfolio.equity,"initial_equity":portfolio.initial_equity,
            "mean_inventory":self.inventory_integral/duration,"max_abs_inventory":portfolio.max_abs_inventory,
            "max_drawdown":portfolio.max_drawdown,"fill_rate":ratio(s["orders_with_fill"],s["activated"]),
            "partial_fill_rate":ratio(s["partial_orders"],s["activated"]),"full_fill_rate":ratio(s["full_orders"],s["activated"]),
            "quantity_fill_rate":ratio(s["filled_quantity"],s["activated_quantity"]),"execution":dict(s),
            "quote_availability":{side:ratio(v,duration) for side,v in self.active_ns.items()},
            "cancel_pending_seconds":{side:v/NS for side,v in self.cancel_pending_ns.items()},
            "entry_pending_seconds":{side:v/NS for side,v in self.entry_pending_ns.items()},
            "markouts":{str(h):self._final_markout(t) for h,t in self.markout_totals.items()},
            "sharpe":sharpe,"sharpe_status":"Unavailable — insufficient sample" if sharpe is None else "descriptive, nonannualized one-minute returns",
            "return_bucket_seconds":60,"return_buckets":len(returns),"assumptions":ASSUMPTIONS}
