"""Event-driven causal market features with independently scheduled snapshots."""
from __future__ import annotations

from collections import deque
from decimal import Decimal

from lobcap.backtest.engine import MarketBook
from lobcap.backtest.types import ZERO
from .feature_registry import FEATURE_REGISTRY, FEATURE_SET_VERSION, WINDOWS_NS
from .signal_contracts import FeaturePoint, FeatureSnapshot


VERSION = FEATURE_SET_VERSION


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
        self.start_client_ns = available
        self.initial_exchange_ns = initial.time_ns
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
                self.book.top_levels("buy",10), self.book.top_levels("sell",10))
        return self._top_cache[0][:count], self._top_cache[1][:count]

    def _complete_depth(self, bids, asks, count):
        return (len(bids) >= count and len(asks) >= count
                and bids[count-1][0] >= self.book.coverage_bid
                and asks[count-1][0] <= self.book.coverage_ask)

    def _value_map(self, now_ns):
        values = {}
        self._anchor_metadata = {}
        bid_size, ask_size = self.book.bids[self.book.bid], self.book.asks[self.book.ask]
        mid = self.book.mid
        micro = (self.book.ask * bid_size + self.book.bid * ask_size) / (bid_size + ask_size)
        values.update(mid=mid, spread_ticks=(self.book.ask-self.book.bid)/self.tick,
                      spread_bps=(self.book.ask-self.book.bid)/mid*Decimal(10000),
                      microprice=micro, microprice_minus_mid_ticks=(micro-mid)/self.tick)
        values.update(bid_top_depth=bid_size, ask_top_depth=ask_size,
                      total_top_depth=bid_size+ask_size)
        for count in (1, 2, 5, 10):
            bids, asks = self._depth(count)
            complete = self._complete_depth(bids, asks, count)
            bq = _sum(q for _, q in bids) if complete else None
            aq = _sum(q for _, q in asks) if complete else None
            values[f"imbalance_{count}"] = ((bq-aq)/(bq+aq)
                if complete and bq+aq else None)
            values[f"bid_depth_{count}"] = bq
            values[f"ask_depth_{count}"] = aq
        bids, asks = self._depth(5)
        complete = self._complete_depth(bids, asks, 5)
        denominator = _sum(bids[i][1]+asks[i][1] for i in range(5)) if complete else ZERO
        vamp = (_sum(asks[i][0]*bids[i][1]+bids[i][0]*asks[i][1] for i in range(5))/denominator
                if complete and denominator else None)
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
            name = "mid_return"+suffix
            values[name] = ((mid/anchor[1]).ln()*Decimal(10000)
                                                  if anchor and anchor[1] > 0 else None)
            if anchor is not None:
                self._anchor_metadata[name] = (anchor[0], now_ns-window-anchor[0])
        values["book_age_ns"] = Decimal(now_ns-self.last_depth_client_ns)
        values["feed_delay_ns"] = Decimal(max(0,
            self.last_depth_receive_ns-self.last_depth_exchange_ns))
        return values

    def _source_metadata(self, definition):
        if definition.input_sources == ("trade",):
            return (self.last_trade_client_ns if self.last_trade_client_ns is not None else self.start_client_ns,
                    self.last_trade_exchange_ns if self.last_trade_exchange_ns is not None else self.initial_exchange_ns,
                    self.last_trade_receive_ns if self.last_trade_receive_ns is not None else self.start_client_ns,
                    self.last_trade_refs)
        return (self.last_depth_client_ns, self.last_depth_exchange_ns,
                self.last_depth_receive_ns, self.last_depth_refs)

    def snapshot(self, analysis_time_ns: int):
        if analysis_time_ns < self.last_client_ns:
            raise ValueError("analysis snapshot precedes observed client state")
        self._trim(analysis_time_ns)
        values = self._value_map(analysis_time_ns)
        if set(values) != set(FEATURE_REGISTRY):
            missing = sorted(set(FEATURE_REGISTRY)-set(values))
            extra = sorted(set(values)-set(FEATURE_REGISTRY))
            raise RuntimeError(f"feature registry mismatch: missing={missing}, extra={extra}")
        points = []
        for name, value in sorted(values.items()):
            definition = FEATURE_REGISTRY[name]
            available, exchange, receive, refs = self._source_metadata(definition)
            warm = analysis_time_ns-self.start_client_ns >= definition.minimum_warmup_ns
            if not warm:
                status = "WARMUP"
            elif definition.minimum_depth_levels and value is None:
                status = "INSUFFICIENT_DEPTH"
            elif definition.stale_after_ns and analysis_time_ns-available > min(
                    definition.stale_after_ns, self.stale_after_ns):
                status = "STALE"
            else:
                status = "UNAVAILABLE" if value is None else "AVAILABLE"
            if status != "AVAILABLE" and definition.missing_value_policy != "carry_with_age":
                value = None
            anchor_time, anchor_age = self._anchor_metadata.get(name, (None, None))
            points.append(FeaturePoint(name, definition.version, value, definition.unit, status, analysis_time_ns,
                available, exchange, receive, tuple(refs), anchor_time, anchor_age))
        return tuple(points)

    def observation(self, analysis_time_ns: int, acquisition_segment: str):
        return FeatureSnapshot(analysis_time_ns, self.book.mid, self.last_depth_exchange_ns,
            self.last_depth_receive_ns, self.last_depth_client_ns, acquisition_segment,
            self.snapshot(analysis_time_ns))
