"""Event-driven causal market features with independently scheduled snapshots."""
from __future__ import annotations

from collections import deque
from decimal import Decimal

from lobcap.backtest.engine import MarketBook
from lobcap.backtest.types import ZERO
from .signal_contracts import FeaturePoint, FeatureSnapshot


VERSION = "causal-features-v1"
WINDOWS_NS = (100_000_000, 500_000_000, 1_000_000_000, 5_000_000_000)


def _sum(values):
    return sum(values, ZERO)


class CausalFeatureEngine:
    """Update on delivered events; snapshots do not advance market state."""

    def __init__(self, initial, tick: Decimal, stale_after_ns: int = 500_000_000,
                 initial_client_available_ns: int | None = None):
        if tick <= 0 or stale_after_ns < 0:
            raise ValueError("invalid feature engine settings")
        self.book = MarketBook(initial)
        self.tick = tick
        self.stale_after_ns = stale_after_ns
        available = initial.time_ns if initial_client_available_ns is None else initial_client_available_ns
        if available < initial.time_ns:
            raise ValueError("initial book cannot arrive before exchange time")
        self.last_client_ns = available
        self.last_exchange_ns = initial.time_ns
        self.last_receive_ns = available
        self.last_depth_client_ns = available
        self.last_depth_exchange_ns = initial.time_ns
        self.last_depth_receive_ns = available
        self.last_trade_exchange_ns = None
        self.last_trade_receive_ns = None
        self.last_trade_client_ns = None
        self.last_depth_refs = ()
        self.last_trade_refs = ()
        self.trades = deque()
        self.mid_history = deque([(available, self.book.mid)])
        self.ofi = deque()
        self._top_cache = None

    def observe(self, event, client_available_time_ns: int):
        if client_available_time_ns < self.last_client_ns:
            raise ValueError("client delivery time regressed")
        if client_available_time_ns < event.time_ns:
            raise ValueError("client cannot observe an event before exchange time")
        before = (self.book.bid, self.book.asks[self.book.ask], self.book.ask, self.book.bids[self.book.bid])
        self.book.apply(event)
        if event.kind == "depth":
            previous_bid, previous_ask_size, previous_ask, previous_bid_size = before
            bid, bid_size = self.book.bid, self.book.bids[self.book.bid]
            ask, ask_size = self.book.ask, self.book.asks[self.book.ask]
            contribution = ZERO
            if bid >= previous_bid:
                contribution += bid_size
            if bid <= previous_bid:
                contribution -= previous_bid_size
            if ask <= previous_ask:
                contribution -= ask_size
            if ask >= previous_ask:
                contribution += previous_ask_size
            self.ofi.append((client_available_time_ns, contribution))
            self.last_depth_client_ns = client_available_time_ns
            self.last_depth_exchange_ns = event.time_ns
            self.last_depth_receive_ns = event.receive_ns
            self.last_depth_refs = event.source_refs
            self._top_cache = None
            if not self.mid_history or self.mid_history[-1][1] != self.book.mid:
                self.mid_history.append((client_available_time_ns, self.book.mid))
        if event.trade is not None:
            sign = Decimal(1) if event.trade.maker_side == "sell" else Decimal(-1)
            self.trades.append((client_available_time_ns, sign, event.trade.size))
            self.last_trade_client_ns = client_available_time_ns
            self.last_trade_exchange_ns = event.time_ns
            self.last_trade_receive_ns = event.receive_ns
            self.last_trade_refs = event.source_refs
        self.last_client_ns = client_available_time_ns
        self.last_exchange_ns = event.time_ns
        self.last_receive_ns = event.receive_ns
        self._trim(client_available_time_ns)

    def _trim(self, now_ns):
        cutoff = now_ns - max(WINDOWS_NS) - 1_000_000_000
        while self.trades and self.trades[0][0] < cutoff:
            self.trades.popleft()
        while self.ofi and self.ofi[0][0] < cutoff:
            self.ofi.popleft()
        while len(self.mid_history) > 1 and self.mid_history[1][0] <= cutoff:
            self.mid_history.popleft()

    def _depth(self, count):
        if self._top_cache is None:
            self._top_cache = (
                tuple((Decimal(p), Decimal(q)) for p, q in sorted(self.book.bids.items(), reverse=True)[:10]),
                tuple((Decimal(p), Decimal(q)) for p, q in sorted(self.book.asks.items())[:10]))
        return self._top_cache[0][:count], self._top_cache[1][:count]

    def _value_map(self, now_ns):
        values = {}
        bid_size, ask_size = self.book.bids[self.book.bid], self.book.asks[self.book.ask]
        mid = self.book.mid
        micro = (self.book.ask * bid_size + self.book.bid * ask_size) / (bid_size + ask_size)
        values.update(mid=mid, spread_ticks=(self.book.ask-self.book.bid)/self.tick,
                      spread_bps=(self.book.ask-self.book.bid)/mid*Decimal(10000),
                      microprice=micro, microprice_minus_mid_ticks=(micro-mid)/self.tick)
        for count in (1, 2, 5, 10):
            bids, asks = self._depth(count)
            bq, aq = _sum(q for _, q in bids), _sum(q for _, q in asks)
            values[f"imbalance_{count}"] = (bq-aq)/(bq+aq) if bq+aq else None
            values[f"bid_depth_{count}"] = bq
            values[f"ask_depth_{count}"] = aq
        bids, asks = self._depth(5)
        pairs = min(len(bids), len(asks))
        denominator = _sum(bids[i][1]+asks[i][1] for i in range(pairs))
        vamp = (_sum(asks[i][0]*bids[i][1]+bids[i][0]*asks[i][1] for i in range(pairs))/denominator
                if denominator else None)
        values["vamp_5"] = vamp
        values["vamp_5_minus_mid_ticks"] = (vamp-mid)/self.tick if vamp is not None else None
        for window in WINDOWS_NS:
            suffix = f"_{window//1_000_000}ms"
            trades = [row for row in self.trades if now_ns-window < row[0] <= now_ns]
            buy = _sum(q for _, sign, q in trades if sign > 0)
            sell = _sum(q for _, sign, q in trades if sign < 0)
            total = buy+sell
            values["signed_trade_volume"+suffix] = buy-sell
            values["trade_volume_imbalance"+suffix] = (buy-sell)/total if total else None
            values["trade_intensity"+suffix] = Decimal(len(trades))*Decimal(1_000_000_000)/Decimal(window)
            values["bbo_ofi"+suffix] = _sum(value for t, value in self.ofi if now_ns-window < t <= now_ns)
            anchor = next(((t, m) for t, m in reversed(self.mid_history) if t <= now_ns-window), None)
            values["mid_return"+suffix] = ((mid/anchor[1]).ln()*Decimal(10000) if anchor and anchor[1] > 0 else None)
        values["book_age_ns"] = Decimal(now_ns-self.last_depth_client_ns)
        return values

    def snapshot(self, analysis_time_ns: int):
        if analysis_time_ns < self.last_client_ns:
            raise ValueError("analysis snapshot precedes observed client state")
        self._trim(analysis_time_ns)
        stale = analysis_time_ns-self.last_depth_client_ns > self.stale_after_ns
        values = self._value_map(analysis_time_ns)
        points = []
        for name, value in sorted(values.items()):
            status = "STALE" if stale and name != "book_age_ns" else ("UNAVAILABLE" if value is None else "AVAILABLE")
            if status != "AVAILABLE" and name != "book_age_ns":
                value = None
            unit = "USDT" if name in {"mid", "microprice", "vamp_5"} else (
                "dimensionless" if "imbalance" in name else
                "BTC" if "depth" in name or "volume" in name or name.startswith("bbo_ofi") else
                "events_per_second" if name.startswith("trade_intensity") else
                "ns" if name == "book_age_ns" else "dimensionless")
            trade_feature = name.startswith(("signed_trade", "trade_volume", "trade_intensity"))
            available = self.last_trade_client_ns if trade_feature and self.last_trade_client_ns is not None else self.last_depth_client_ns
            exchange = self.last_trade_exchange_ns if trade_feature and self.last_trade_exchange_ns is not None else self.last_depth_exchange_ns
            receive = self.last_trade_receive_ns if trade_feature and self.last_trade_receive_ns is not None else self.last_depth_receive_ns
            refs = self.last_trade_refs if trade_feature and self.last_trade_refs else self.last_depth_refs
            points.append(FeaturePoint(name, VERSION, value, unit, status, analysis_time_ns,
                available, exchange, receive, tuple(refs)))
        return tuple(points)

    def observation(self, analysis_time_ns: int, acquisition_segment: str):
        return FeatureSnapshot(analysis_time_ns, self.book.mid, self.last_depth_exchange_ns,
            self.last_depth_receive_ns, self.last_depth_client_ns, acquisition_segment,
            self.snapshot(analysis_time_ns))
