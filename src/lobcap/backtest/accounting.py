"""Exact synthetic linear-futures ledger; never liquidates ending inventory."""
from lobcap.backtest.types import ZERO, jsonable


class Portfolio:
    def __init__(self,config,mid,sink):
        self.config,self.sink=config,sink
        self.cash,self.inventory=config.initial_cash,config.initial_inventory
        self.mid=mid
        self.initial_equity=self.cash+self.inventory*mid
        self.fees=self.charges=self.rebates=self.spread_capture=self.inventory_mtm=ZERO
        self.max_abs_inventory=abs(self.inventory)
        self.peak=self.initial_equity
        self.max_drawdown=ZERO
        self._write(config.sample.start_ns,0,"initial")

    @property
    def equity(self):return self.cash+self.inventory*self.mid

    @property
    def net_pnl(self):return self.equity-self.initial_equity

    @property
    def gross_pnl(self):return self.net_pnl+self.fees

    def reconcile(self):
        if self.gross_pnl!=self.spread_capture+self.inventory_mtm:
            raise AssertionError("PnL attribution does not reconcile")
        if self.net_pnl!=self.spread_capture+self.inventory_mtm-self.fees:
            raise AssertionError("equity identity does not reconcile")

    def _write(self,now,index,reason):
        self.reconcile()
        self.peak=max(self.peak,self.equity)
        self.max_drawdown=max(self.max_drawdown,self.peak-self.equity)
        self.max_abs_inventory=max(self.max_abs_inventory,abs(self.inventory))
        row={"time_ns":now,"replay_index":index,"event":reason,"cash":self.cash,"inventory":self.inventory,
             "mark_price":self.mid,"equity":self.equity,"gross_pnl":self.gross_pnl,"net_pnl":self.net_pnl,
             "spread_capture":self.spread_capture,"inventory_mtm":self.inventory_mtm,"fees":self.fees}
        self.sink.write("ledger",row)
        return row

    def mark(self,mid,now,index):
        if mid!=self.mid:
            contribution=self.inventory*(mid-self.mid)
            self.inventory_mtm+=contribution
            self.mid=mid
            self._write(now,index,"mark")
            return self.inventory, contribution
        return None

    def apply_fill(self,fill,market):
        # Reject malformed execution evidence before changing either balances
        # or the audit stream. A bad side must never silently become a sell.
        if fill.side not in {"buy", "sell"}:
            raise ValueError("unknown execution side")
        if not fill.price.is_finite() or fill.price <= ZERO:
            raise ValueError("execution price must be finite and positive")
        if not fill.quantity.is_finite() or fill.quantity <= ZERO:
            raise ValueError("execution quantity must be finite and positive")
        sign=1 if fill.side=="buy" else -1
        before_inventory=self.inventory
        if fill.role not in {"maker", "taker"}:
            raise ValueError("unknown execution role")
        rate=self.config.maker_bps if fill.role=="maker" else self.config.taker_bps
        fee=fill.price*fill.quantity*rate/10000
        spread=sign*fill.quantity*(self.mid-fill.price)
        self.cash-=sign*fill.price*fill.quantity+fee
        self.inventory+=sign*fill.quantity
        self.fees+=fee
        self.charges+=max(ZERO,fee)
        self.rebates+=max(ZERO,-fee)
        self.spread_capture+=spread
        ledger=self._write(fill.time_ns,fill.replay_index,"fill")
        row={**jsonable(fill),"inventory_before":before_inventory,"inventory_after":self.inventory,
             "mid_at_fill":self.mid,"fee":fee,"spread_contribution":spread,
             "spread_capture_per_unit":sign*(self.mid-fill.price),
             "spread_capture_bps":sign*(self.mid-fill.price)/fill.price*10000,
             "imbalance_at_fill":market.imbalance,"volatility_bps_at_fill":market.volatility_bps,
             "prevailing_spread_ticks":(market.ask-market.bid)/self.config.instrument.tick}
        self.sink.write("fills",row)
        return row
