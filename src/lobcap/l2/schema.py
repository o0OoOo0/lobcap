"""Strict schemas for the order-identity-blinded L2 model boundary."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


ZERO = Decimal("0")
SIDES = frozenset({"buy", "sell"})
FORBIDDEN_L2_FIELDS = frozenset(
    {
        "order_id",
        "maker_order_id",
        "taker_order_id",
        "ahead_order_ids",
        "ahead_ids",
        "open",
        "done",
        "change",
        "reason",
        "remaining_size",
        "fifo_position",
        "trade_depletion",
        "cancel_depletion",
        "modify_decrease",
        "priority_loss",
    }
)
_ISO_RE = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?Z$"
)


def decimal_value(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal {field}={value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite decimal {field}={value!r}")
    return result


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def timestamp_ns(value: str) -> int:
    match = _ISO_RE.fullmatch(value)
    if not match:
        raise ValueError(f"unsupported UTC timestamp: {value!r}")
    base = datetime.strptime(match.group("base"), "%Y-%m-%dT%H:%M:%S")
    seconds = calendar.timegm(base.timetuple())
    fraction = (match.group("fraction") or "").ljust(9, "0")
    return seconds * 1_000_000_000 + int(fraction or "0")


def _forbidden_hits(value: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key) in FORBIDDEN_L2_FIELDS:
                hits.append(child_path)
            hits.extend(_forbidden_hits(child, child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            hits.extend(_forbidden_hits(child, f"{path}[{index}]"))
    return hits


def assert_blinded_mapping(value: Mapping[str, Any]) -> None:
    hits = _forbidden_hits(value)
    if hits:
        raise ValueError(f"forbidden order-level fields in L2 record: {hits}")


@dataclass(frozen=True, slots=True)
class L2LevelUpdate:
    side: str
    price: Decimal
    quantity_before: Decimal
    quantity_after: Decimal

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"invalid level side: {self.side!r}")
        if min(self.price, self.quantity_before, self.quantity_after) < ZERO:
            raise ValueError("negative L2 level value")

    @property
    def depletion(self) -> Decimal:
        return max(ZERO, self.quantity_before - self.quantity_after)

    def as_dict(self) -> dict[str, Any]:
        result = {
            "side": self.side,
            "price": decimal_text(self.price),
            "quantity_before": decimal_text(self.quantity_before),
            "quantity_after": decimal_text(self.quantity_after),
        }
        assert_blinded_mapping(result)
        return result


@dataclass(frozen=True, slots=True)
class L2Trade:
    maker_side: str
    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        if self.maker_side not in SIDES:
            raise ValueError(f"invalid maker side: {self.maker_side!r}")
        if self.price < ZERO or self.size <= ZERO:
            raise ValueError("invalid L2 trade value")

    def as_dict(self) -> dict[str, Any]:
        result = {
            "maker_side": self.maker_side,
            "price": decimal_text(self.price),
            "size": decimal_text(self.size),
        }
        assert_blinded_mapping(result)
        return result


@dataclass(frozen=True, slots=True)
class L2Frame:
    sequence: int
    exchange_time: str
    time_ns: int
    level_updates: tuple[L2LevelUpdate, ...]
    trade: L2Trade | None = None

    def __post_init__(self) -> None:
        if self.sequence < 0 or self.time_ns != timestamp_ns(self.exchange_time):
            raise ValueError("invalid L2 frame sequence/time")
        keys = [(update.side, update.price) for update in self.level_updates]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate side/price update within L2 frame")

    def update_at(self, side: str, price: Decimal) -> L2LevelUpdate | None:
        for update in self.level_updates:
            if update.side == side and update.price == price:
                return update
        return None

    def as_dict(self) -> dict[str, Any]:
        result = {
            "sequence": self.sequence,
            "exchange_time": self.exchange_time,
            "level_updates": [update.as_dict() for update in self.level_updates],
            "trade": self.trade.as_dict() if self.trade else None,
        }
        assert_blinded_mapping(result)
        return result


@dataclass(frozen=True, slots=True)
class BlindedPlacement:
    """Frozen 5C placement fields permitted at the L2 model boundary."""

    virtual_order_id: str
    placement_sequence: int
    placement_exchange_time: str
    placement_time_ns: int
    expiry_time: str
    expiry_time_ns: int
    side: str
    price: Decimal
    size: Decimal
    ttl_seconds: int

    def __post_init__(self) -> None:
        if self.side not in SIDES or self.price < ZERO or self.size <= ZERO:
            raise ValueError("invalid blinded placement")
        if self.placement_time_ns != timestamp_ns(self.placement_exchange_time):
            raise ValueError("placement time mismatch")
        if self.expiry_time_ns != timestamp_ns(self.expiry_time):
            raise ValueError("expiry time mismatch")
        if self.expiry_time_ns - self.placement_time_ns != self.ttl_seconds * 1_000_000_000:
            raise ValueError("placement TTL mismatch")

    def as_dict(self) -> dict[str, Any]:
        # virtual_order_id identifies the frozen counterfactual placement, not
        # any observed exchange order.  No actual order identity crosses here.
        result = {
            "virtual_order_id": self.virtual_order_id,
            "placement_sequence": self.placement_sequence,
            "placement_exchange_time": self.placement_exchange_time,
            "expiry_time": self.expiry_time,
            "side": self.side,
            "price": decimal_text(self.price),
            "size": decimal_text(self.size),
            "ttl_seconds": self.ttl_seconds,
        }
        assert_blinded_mapping(result)
        return result


@dataclass(frozen=True, slots=True)
class L2Projection:
    frames: tuple[L2Frame, ...]
    initial_queues: Mapping[str, Decimal]
    placement_bbo_matches: Mapping[str, bool]
    sample_end_sequence: int
    sample_end_time: str
    sample_end_time_ns: int
    reconstruction_valid: bool
    projection_invariants: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.sample_end_time_ns != timestamp_ns(self.sample_end_time):
            raise ValueError("sample end time mismatch")
        if any(value < ZERO for value in self.initial_queues.values()):
            raise ValueError("negative placement queue")
        sequences = [frame.sequence for frame in self.frames]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError("L2 frames must be unique and sequence ordered")

    def serialized_frames(self) -> Iterable[dict[str, Any]]:
        for frame in self.frames:
            yield frame.as_dict()
