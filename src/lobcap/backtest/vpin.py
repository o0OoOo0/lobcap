"""Causal normal-volume imbalance buckets shared by research and policy memory.

This is an aggressor-classified VPIN proxy, not a calibrated probability.
Only completed equal-volume buckets enter its rolling value.
"""
from collections import deque
from dataclasses import dataclass
from decimal import Decimal, localcontext


VERSION = "normal-volume-vpin-v1"
NAME = "vpin_normal_volume"
ZERO = Decimal(0)


@dataclass(frozen=True)
class VpinSettings:
    bucket_volume: Decimal = Decimal(1)
    bucket_count: int = 20
    max_age_ns: int = 5_000_000_000

    def __post_init__(self):
        if (not isinstance(self.bucket_volume, Decimal) or not self.bucket_volume.is_finite()
            or self.bucket_volume <= 0 or type(self.bucket_count) is not int
            or not 1 <= self.bucket_count <= 1000 or type(self.max_age_ns) is not int or self.max_age_ns <= 0):
            raise ValueError("invalid VPIN settings")


class VolumeBuckets:
    def __init__(self, settings=VpinSettings()):
        self.settings = settings
        self.buckets = deque(maxlen=settings.bucket_count)
        self.buy = self.sell = self.total_volume = ZERO
        self.completed = self.trades = 0
        self.last_delivery = None
        self.last_completion = None

    def observe(self, quantity, maker_side, exchange_ns, receive_ns, available_ns, source_refs=()):
        if (not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity < 0
            or maker_side not in {"buy", "sell"}
            or not all(type(t) is int for t in (exchange_ns, receive_ns, available_ns))
            or not exchange_ns <= receive_ns <= available_ns
            or self.last_delivery is not None and available_ns < self.last_delivery):
            raise ValueError("invalid VPIN trade or delivery provenance")
        self.last_delivery = available_ns
        if quantity == 0:
            return
        self.trades += 1
        with localcontext() as ctx:
            ctx.prec = 50
            self.total_volume += quantity
            volume = self.settings.bucket_volume
            provenance = (exchange_ns, receive_ns, available_ns, tuple(source_refs))
            # First complete the partial bucket, then bulk-handle same-side buckets.
            take = min(quantity, volume-self.buy-self.sell)
            if maker_side == "sell": self.buy += take  # buyer is the aggressor
            else: self.sell += take
            quantity -= take
            if self.buy+self.sell == volume:
                self.buckets.append((abs(self.buy-self.sell), provenance))
                self.completed += 1
                self.last_completion = provenance
                self.buy = self.sell = ZERO
            full = int(quantity//volume)
            if full:
                for _ in range(min(full, self.settings.bucket_count)):
                    self.buckets.append((volume, provenance))
                self.completed += full
                self.last_completion = provenance
                quantity -= full*volume
            if maker_side == "sell": self.buy += quantity
            else: self.sell += quantity

    def snapshot(self, now_ns):
        if self.last_delivery is not None and now_ns < self.last_delivery:
            raise ValueError("VPIN snapshot precedes delivery")
        complete = self.last_completion
        ready = len(self.buckets) == self.settings.bucket_count
        status = "WARMUP" if not ready else "AVAILABLE"
        if ready and (now_ns-complete[0] > self.settings.max_age_ns
                      or now_ns-complete[2] > self.settings.max_age_ns):
            status = "STALE"
        with localcontext() as ctx:
            ctx.prec = 50
            value = (sum((b[0] for b in self.buckets), ZERO)/
                     (self.settings.bucket_volume*self.settings.bucket_count)) if status == "AVAILABLE" else None
            residual = self.buy+self.sell
            if self.total_volume != self.completed*self.settings.bucket_volume+residual:
                raise AssertionError("VPIN volume conservation failed")
        return {"version": VERSION, "value": value, "status": status,
            "completed_buckets": self.completed, "retained_buckets": len(self.buckets),
            "normal_volume": self.total_volume, "partial_volume": residual,
            "positive_volume_trades": self.trades,
            "source_exchange_ns": complete[0] if complete else None,
            "source_receive_ns": complete[1] if complete else None,
            "available_ns": complete[2] if complete else None,
            "source_refs": complete[3] if complete else (),
            "oldest_retained_completion_ns": self.buckets[0][1][2] if self.buckets else None}
