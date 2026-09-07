"""Independent no-market-impact virtual orders on a reconstructed Coinbase L3 book.

The result is an L3-derived shadow-fill label, not an absolute fill ground
truth.  Each virtual order is an independent counterfactual branch: observed
historical order flow is held fixed and virtual orders never enter ``L3Book``
or one another's queue.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from lobcap.config import FiveCConfig, load_five_c_config, write_run_manifest

from lobcap.l3.book import L3Book, ZERO, decimal_value
from lobcap.l3.reconstruct import L3Reconstructor
from lobcap.l3.schema import NativeMessage, iter_tardis_lines


SAME_PRICE_BEHIND_MATCH = "SAME_PRICE_BEHIND_MATCH"
TRADE_THROUGH = "TRADE_THROUGH"
ACTIVE = "ACTIVE"
FULL_FILL = "FULL_FILL"
PARTIAL_ONLY = "PARTIAL_ONLY"
NO_FILL = "NO_FILL"
CENSORED = "CENSORED"
DEFAULT_CONFIG_PATH = Path(__file__).parents[3] / "configs/experiments/5c_virtual_truth.yaml"

_ISO_RE = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?Z$"
)


def timestamp_ns(value: str) -> int:
    """Parse the Coinbase UTC timestamp without losing sub-microsecond digits."""
    match = _ISO_RE.fullmatch(value)
    if not match:
        raise ValueError(f"unsupported UTC timestamp: {value!r}")
    base = datetime.strptime(match.group("base"), "%Y-%m-%dT%H:%M:%S")
    seconds = calendar.timegm(base.timetuple())
    fraction = (match.group("fraction") or "").ljust(9, "0")
    return seconds * 1_000_000_000 + int(fraction or "0")


def timestamp_from_ns(value: int) -> str:
    seconds, fraction = divmod(value, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    return f"{base}.{fraction:09d}Z"


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True, slots=True)
class PlacementIntent:
    virtual_order_id: str
    side: str
    size: Decimal
    ttl_seconds: int
    after_sequence: int | None = None
    at_or_after_time_ns: int | None = None

    def __post_init__(self) -> None:
        if self.side not in {"buy", "sell"}:
            raise ValueError(f"unsupported virtual side: {self.side!r}")
        if self.size <= ZERO:
            raise ValueError("virtual size must be positive")
        if self.ttl_seconds <= 0:
            raise ValueError("TTL must be positive")
        if (self.after_sequence is None) == (self.at_or_after_time_ns is None):
            raise ValueError("set exactly one placement trigger")


@dataclass(slots=True)
class VirtualOrder:
    virtual_order_id: str
    side: str
    price: Decimal
    original_size: Decimal
    remaining_size: Decimal
    ttl_seconds: int
    placement_sequence: int
    placement_exchange_time: str
    placement_time_ns: int
    expiry_time: str
    expiry_time_ns: int
    expiry_sequence: int | None
    ahead_initial: dict[str, Decimal]
    ahead: dict[str, Decimal]
    behind_ids: set[str] = field(default_factory=set)
    ahead_volume_initial: Decimal = ZERO
    ahead_volume_remaining: Decimal = ZERO
    ahead_trade_depletion: Decimal = ZERO
    ahead_cancel_depletion: Decimal = ZERO
    ahead_modify_decrease: Decimal = ZERO
    ahead_priority_loss: Decimal = ZERO
    own_filled_volume: Decimal = ZERO
    first_fill_sequence: int | None = None
    first_fill_time: str | None = None
    full_fill_sequence: int | None = None
    full_fill_time: str | None = None
    outcome: str = ACTIVE
    fill_evidence: Counter[str] = field(default_factory=Counter)
    counted_fill_events: set[tuple[int, str]] = field(default_factory=set)
    observation_end_sequence: int | None = None
    observation_end_time: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label_type": "L3-derived shadow-fill label",
            "virtual_order_id": self.virtual_order_id,
            "placement_sequence": self.placement_sequence,
            "placement_exchange_time": self.placement_exchange_time,
            "side": self.side,
            "price": decimal_text(self.price),
            "original_size": decimal_text(self.original_size),
            "remaining_size": decimal_text(self.remaining_size),
            "ttl_seconds": self.ttl_seconds,
            "expiry_sequence": self.expiry_sequence,
            "expiry_time": self.expiry_time,
            "ahead_order_ids": sorted(self.ahead_initial),
            "ahead_order_count_initial": len(self.ahead_initial),
            "ahead_volume_initial": decimal_text(self.ahead_volume_initial),
            "ahead_volume_remaining": decimal_text(self.ahead_volume_remaining),
            "ahead_trade_depletion": decimal_text(self.ahead_trade_depletion),
            "ahead_cancel_depletion": decimal_text(self.ahead_cancel_depletion),
            "ahead_modify_decrease": decimal_text(self.ahead_modify_decrease),
            "ahead_priority_loss": decimal_text(self.ahead_priority_loss),
            "own_filled_volume": decimal_text(self.own_filled_volume),
            "first_fill_sequence": self.first_fill_sequence,
            "first_fill_time": self.first_fill_time,
            "full_fill_sequence": self.full_fill_sequence,
            "full_fill_time": self.full_fill_time,
            "outcome": self.outcome,
            "fill_evidence_counts": dict(sorted(self.fill_evidence.items())),
            "observation_end_sequence": self.observation_end_sequence,
            "observation_end_time": self.observation_end_time,
            "independent_counterfactual_branch": True,
            "no_market_impact_assumption": True,
        }


class VirtualOrderEngine:
    """Observe real L3 mutations while maintaining independent shadow branches."""

    def __init__(self, intents: Iterable[PlacementIntent]) -> None:
        self.pending = list(intents)
        ids = [intent.virtual_order_id for intent in self.pending]
        if len(ids) != len(set(ids)):
            raise ValueError("virtual_order_id values must be unique")
        self.orders: list[VirtualOrder] = []
        self.active_orders: list[VirtualOrder] = []
        self.ledger: list[dict[str, Any]] = []
        self.invariants: Counter[str] = Counter()
        self.invariant_examples: dict[str, dict[str, Any]] = {}
        self.last_applied_sequence: int | None = None
        self.last_exchange_time: str | None = None

    def _violation(self, name: str, order: VirtualOrder, **details: Any) -> None:
        self.invariants[name] += 1
        self.invariant_examples.setdefault(
            name, {"virtual_order_id": order.virtual_order_id, **details}
        )

    def _append_ledger(
        self,
        order: VirtualOrder,
        sequence: int | None,
        event_type: str,
        ahead_before: Decimal,
        *,
        trade: Decimal = ZERO,
        cancel: Decimal = ZERO,
        modify: Decimal = ZERO,
        priority_loss: Decimal = ZERO,
        virtual_fill: Decimal = ZERO,
        fill_evidence: str | None = None,
        maker_order_id: str | None = None,
        taker_order_id: str | None = None,
    ) -> None:
        self.ledger.append(
            {
                "virtual_order_id": order.virtual_order_id,
                "sequence": sequence,
                "event_type": event_type,
                "ahead_before": decimal_text(ahead_before),
                "ahead_after": decimal_text(order.ahead_volume_remaining),
                "trade_depletion": decimal_text(trade),
                "cancel_depletion": decimal_text(cancel),
                "modify_decrease": decimal_text(modify),
                "priority_loss": decimal_text(priority_loss),
                "virtual_fill": decimal_text(virtual_fill),
                "fill_evidence": fill_evidence,
                "maker_order_id": maker_order_id,
                "taker_order_id": taker_order_id,
            }
        )

    def _place(
        self, intent: PlacementIntent, message: NativeMessage, book: L3Book
    ) -> VirtualOrder:
        levels = book.levels[intent.side]
        if not levels:
            raise ValueError(f"cannot place {intent.side}: reconstructed side is empty")
        price = max(levels) if intent.side == "buy" else min(levels)
        ahead = {
            order_id: book.orders[order_id].remaining_size
            for order_id in book.active_order_ids(intent.side, price)
        }
        initial = sum(ahead.values(), ZERO)
        placement_ns = timestamp_ns(str(message.payload["time"]))
        expiry_ns = placement_ns + intent.ttl_seconds * 1_000_000_000
        order = VirtualOrder(
            virtual_order_id=intent.virtual_order_id,
            side=intent.side,
            price=price,
            original_size=intent.size,
            remaining_size=intent.size,
            ttl_seconds=intent.ttl_seconds,
            placement_sequence=message.sequence,
            placement_exchange_time=str(message.payload["time"]),
            placement_time_ns=placement_ns,
            expiry_time=timestamp_from_ns(expiry_ns),
            expiry_time_ns=expiry_ns,
            expiry_sequence=None,
            ahead_initial=dict(ahead),
            ahead=dict(ahead),
            ahead_volume_initial=initial,
            ahead_volume_remaining=initial,
        )
        self.orders.append(order)
        self.active_orders.append(order)
        self._append_ledger(order, message.sequence, "placement", initial)
        return order

    def before_event(self, message: NativeMessage, book: L3Book) -> None:
        """Observe a lifecycle event immediately before the real book applies it."""
        event_ns = timestamp_ns(str(message.payload["time"]))
        still_active: list[VirtualOrder] = []
        for order in self.active_orders:
            if event_ns >= order.expiry_time_ns:
                order.expiry_sequence = self.last_applied_sequence
                order.observation_end_sequence = self.last_applied_sequence
                order.observation_end_time = order.expiry_time
                order.outcome = PARTIAL_ONLY if order.own_filled_volume else NO_FILL
                self._append_ledger(
                    order,
                    self.last_applied_sequence,
                    "ttl_expiry",
                    order.ahead_volume_remaining,
                )
                continue
            self._process_event(order, message, book)
            if order.outcome == ACTIVE:
                still_active.append(order)
        self.active_orders = still_active

    def _process_event(
        self, order: VirtualOrder, message: NativeMessage, book: L3Book
    ) -> None:
        payload = message.payload
        message_type = message.type
        sequence = message.sequence
        ahead_before = order.ahead_volume_remaining
        trade = cancel = modify = priority_loss = virtual_fill = ZERO
        evidence: str | None = None
        maker_id: str | None = None
        taker_id: str | None = None
        relevant = False

        if message_type == "match":
            maker_id = str(payload["maker_order_id"])
            taker_id = str(payload["taker_order_id"])
            match_size = decimal_value(payload["size"], "match.size")
            match_price = decimal_value(payload["price"], "match.price")
            match_side = str(payload["side"])
            actual_maker = book.orders.get(maker_id)
            if maker_id in order.ahead:
                relevant = True
                tracked = order.ahead[maker_id]
                remaining = tracked - match_size
                trade = match_size
                order.ahead_trade_depletion += match_size
                order.ahead_volume_remaining -= match_size
                if remaining < ZERO:
                    self._violation(
                        "negative_ahead_volume",
                        order,
                        sequence=sequence,
                        maker_order_id=maker_id,
                        tracked=decimal_text(tracked),
                        match_size=decimal_text(match_size),
                    )
                if remaining <= ZERO:
                    order.ahead.pop(maker_id)
                else:
                    order.ahead[maker_id] = remaining
            elif match_side == order.side:
                same_price = match_price == order.price
                through = (
                    order.side == "buy" and match_price <= order.price
                ) or (
                    order.side == "sell" and match_price >= order.price
                )
                if same_price and maker_id in order.behind_ids:
                    relevant = True
                    if order.ahead_volume_remaining != ZERO:
                        self._violation(
                            "behind_match_while_ahead_nonzero",
                            order,
                            sequence=sequence,
                            maker_order_id=maker_id,
                            ahead=decimal_text(order.ahead_volume_remaining),
                        )
                    else:
                        evidence = SAME_PRICE_BEHIND_MATCH
                elif same_price:
                    # At placement every resting same-level maker was captured as
                    # ahead.  Every later arrival/reinsert is explicitly behind.
                    self._violation(
                        "unknown_maker_in_virtual_decision",
                        order,
                        sequence=sequence,
                        maker_order_id=maker_id,
                        maker_present_in_book=actual_maker is not None,
                    )
                    relevant = True
                elif through and order.ahead_volume_remaining == ZERO:
                    relevant = True
                    if actual_maker is None:
                        self._violation(
                            "unknown_maker_in_virtual_decision",
                            order,
                            sequence=sequence,
                            maker_order_id=maker_id,
                            maker_present_in_book=False,
                        )
                    else:
                        evidence = TRADE_THROUGH

                if evidence is not None:
                    key = (sequence, maker_id)
                    if key in order.counted_fill_events:
                        self._violation(
                            "double_counted_observed_volume",
                            order,
                            sequence=sequence,
                            maker_order_id=maker_id,
                        )
                    else:
                        order.counted_fill_events.add(key)
                        virtual_fill = min(match_size, order.remaining_size)
                        self._allocate_fill(order, message, virtual_fill, evidence)

        elif message_type == "done":
            order_id = str(payload["order_id"])
            if order_id in order.ahead:
                relevant = True
                remaining = order.ahead.pop(order_id)
                if payload["reason"] == "canceled":
                    cancel = remaining
                    order.ahead_cancel_depletion += remaining
                    order.ahead_volume_remaining -= remaining
                elif payload["reason"] == "filled":
                    trade = remaining
                    order.ahead_trade_depletion += remaining
                    order.ahead_volume_remaining -= remaining
                elif remaining != ZERO:
                    self._violation(
                        "ahead_filled_done_nonzero",
                        order,
                        sequence=sequence,
                        order_id=order_id,
                        remaining=decimal_text(remaining),
                    )
            order.behind_ids.discard(order_id)

        elif message_type == "change":
            order_id = str(payload["order_id"])
            reason = str(payload.get("reason"))
            if reason not in {"modify_order", "remainder_after_modification", "STP"}:
                return
            if reason == "STP":
                new_price = old_price = decimal_value(payload["price"], "change.price")
            else:
                new_price = decimal_value(payload["new_price"], "change.new_price")
                old_price = decimal_value(payload["old_price"], "change.old_price")
            new_size = decimal_value(payload["new_size"], "change.new_size")
            old_size = decimal_value(payload["old_size"], "change.old_size")
            if order_id in order.ahead:
                relevant = True
                tracked = order.ahead[order_id]
                actual = book.orders.get(order_id)
                size_matches = old_size >= tracked if reason == "modify_order" else old_size == tracked
                if actual is None or tracked != actual.remaining_size or not size_matches:
                    self._violation(
                        "ahead_state_mismatch",
                        order,
                        sequence=sequence,
                        order_id=order_id,
                        tracked=decimal_text(tracked),
                        event_old_size=decimal_text(old_size),
                    )
                    return
                already_executed = old_size - tracked if reason == "modify_order" else ZERO
                new_remaining = new_size - already_executed
                loses_priority = reason != "STP" and (
                    new_price != old_price or new_size > old_size
                )
                if loses_priority:
                    priority_loss = tracked
                    order.ahead_priority_loss += tracked
                    order.ahead_volume_remaining -= tracked
                    order.ahead.pop(order_id)
                elif new_remaining < tracked:
                    modify = tracked - new_remaining
                    order.ahead_modify_decrease += modify
                    order.ahead_volume_remaining -= modify
                    if new_remaining == ZERO:
                        order.ahead.pop(order_id)
                    else:
                        order.ahead[order_id] = new_remaining
            elif order_id in order.behind_ids and new_price != order.price:
                order.behind_ids.discard(order_id)

        if order.ahead_volume_remaining < ZERO:
            self._violation(
                "negative_ahead_volume",
                order,
                sequence=sequence,
                ahead=decimal_text(order.ahead_volume_remaining),
            )
        if relevant or virtual_fill:
            self._append_ledger(
                order,
                sequence,
                message_type,
                ahead_before,
                trade=trade,
                cancel=cancel,
                modify=modify,
                priority_loss=priority_loss,
                virtual_fill=virtual_fill,
                fill_evidence=evidence,
                maker_order_id=maker_id,
                taker_order_id=taker_id,
            )

    def _allocate_fill(
        self,
        order: VirtualOrder,
        message: NativeMessage,
        quantity: Decimal,
        evidence: str,
    ) -> None:
        if quantity <= ZERO:
            return
        payload = message.payload
        price = decimal_value(payload["price"], "match.price")
        side = str(payload["side"])
        valid_price = (
            evidence == SAME_PRICE_BEHIND_MATCH and price == order.price
        ) or (
            evidence == TRADE_THROUGH
            and (
                (order.side == "buy" and price < order.price)
                or (order.side == "sell" and price > order.price)
            )
        )
        if side != order.side or not valid_price:
            self._violation(
                "invalid_side_or_price_fill",
                order,
                sequence=message.sequence,
                event_side=side,
                event_price=decimal_text(price),
                evidence=evidence,
            )
            return
        before = order.remaining_size
        order.remaining_size -= quantity
        order.own_filled_volume += quantity
        order.fill_evidence[evidence] += 1
        if order.first_fill_sequence is None:
            order.first_fill_sequence = message.sequence
            order.first_fill_time = str(payload["time"])
        if order.remaining_size < ZERO or order.own_filled_volume > order.original_size:
            self._violation(
                "virtual_overfill",
                order,
                sequence=message.sequence,
                before=decimal_text(before),
                fill=decimal_text(quantity),
            )
        if order.remaining_size == ZERO:
            order.full_fill_sequence = message.sequence
            order.full_fill_time = str(payload["time"])
            order.outcome = FULL_FILL

    def after_event(self, message: NativeMessage, book: L3Book) -> None:
        """Observe resulting membership, then place orders after this sequence."""
        payload = message.payload
        if message.type == "open":
            actual = book.orders.get(str(payload["order_id"]))
            if actual is not None:
                for order in self.active_orders:
                    if actual.side == order.side and actual.price == order.price:
                        order.behind_ids.add(actual.order_id)
                        self._append_ledger(
                            order,
                            message.sequence,
                            "post_placement_open",
                            order.ahead_volume_remaining,
                        )
        elif message.type == "change" and payload.get("reason") in {
            "modify_order", "remainder_after_modification"
        }:
            actual = book.orders.get(str(payload["order_id"]))
            if actual is not None:
                for order in self.active_orders:
                    if actual.side == order.side and actual.price == order.price:
                        # An ahead order remains in ahead only for a same-price
                        # non-increasing modification.  Every other arrival at P
                        # is a tail reinsert and therefore behind.
                        if actual.order_id not in order.ahead:
                            order.behind_ids.add(actual.order_id)

        self.last_applied_sequence = message.sequence
        self.last_exchange_time = str(payload["time"])
        remaining: list[PlacementIntent] = []
        event_ns = timestamp_ns(self.last_exchange_time)
        for intent in self.pending:
            triggered = (
                intent.after_sequence == message.sequence
                if intent.after_sequence is not None
                else event_ns >= int(intent.at_or_after_time_ns)
            )
            if triggered:
                self._place(intent, message, book)
            else:
                remaining.append(intent)
        self.pending = remaining

    def finish(self) -> None:
        for order in self.active_orders:
            if self.last_exchange_time is None:
                order.outcome = CENSORED
                continue
            last_ns = timestamp_ns(self.last_exchange_time)
            if last_ns >= order.expiry_time_ns:
                order.expiry_sequence = self.last_applied_sequence
                order.outcome = PARTIAL_ONLY if order.own_filled_volume else NO_FILL
                event_type = "ttl_expiry_at_sample_end"
            else:
                order.outcome = CENSORED
                event_type = "sample_end_censor"
            order.observation_end_sequence = self.last_applied_sequence
            order.observation_end_time = self.last_exchange_time
            self._append_ledger(
                order,
                self.last_applied_sequence,
                event_type,
                order.ahead_volume_remaining,
            )
        self.active_orders = []

    def invariant_report(self) -> dict[str, Any]:
        names = (
            "negative_ahead_volume",
            "negative_virtual_remaining",
            "virtual_overfill",
            "double_counted_observed_volume",
            "behind_match_while_ahead_nonzero",
            "unknown_maker_in_virtual_decision",
            "invalid_side_or_price_fill",
            "ahead_state_mismatch",
            "ahead_filled_done_nonzero",
        )
        counts = {name: self.invariants[name] for name in names}
        # Remaining quantity is independently checked at finalization even
        # though allocation uses min() and should make this impossible.
        counts["negative_virtual_remaining"] += sum(
            order.remaining_size < ZERO for order in self.orders
        )
        return {
            "valid": not any(counts.values()),
            "counts": counts,
            "examples": self.invariant_examples,
        }


class VirtualL3Reconstructor(L3Reconstructor):
    """The verified reconstructor with read-only virtual lifecycle hooks."""

    def __init__(self, engine: VirtualOrderEngine, product_id: str = "BTC-USD"):
        super().__init__(product_id)
        self.virtual_engine = engine

    def _apply(self, message: NativeMessage) -> None:
        if not self.valid:
            return
        if (
            self.applied_sequence.last is not None
            and message.sequence != self.applied_sequence.last + 1
        ):
            super()._apply(message)
            return
        self.virtual_engine.before_event(message, self.book)
        super()._apply(message)
        if self.valid:
            self.virtual_engine.after_event(message, self.book)


def intents_from_config(config: FiveCConfig) -> list[PlacementIntent]:
    """Construct the deterministic placement grid from versioned config."""
    if len(config.dates) != 1 or len(config.windows) != 1:
        raise ValueError("5C currently requires exactly one date and one window")
    day = datetime.fromisoformat(config.dates[0]).replace(tzinfo=timezone.utc)
    window = config.windows[0]
    intents: list[PlacementIntent] = []
    for second in config.placement.trigger_seconds:
        trigger = day + timedelta(minutes=window.start_minute, seconds=second)
        target = int(trigger.timestamp()) * 1_000_000_000
        for side in config.placement.sides:
            for size in config.virtual_sizes:
                size_text = decimal_text(size)
                for ttl in config.ttls_seconds:
                    intents.append(PlacementIntent(
                        virtual_order_id=f"v-{second:02d}-{side}-{size_text}-{ttl}s",
                        side=side, size=size, ttl_seconds=ttl,
                        at_or_after_time_ns=target,
                    ))
    return intents


def default_intents() -> list[PlacementIntent]:
    """Compatibility helper backed by the versioned production config."""
    return intents_from_config(load_five_c_config(DEFAULT_CONFIG_PATH))


def run_virtual_file(
    input_path: str | Path,
    intents: Iterable[PlacementIntent] | None = None,
    product_id: str | None = None,
) -> tuple[VirtualOrderEngine, dict[str, Any]]:
    default_config = load_five_c_config(DEFAULT_CONFIG_PATH)
    engine = VirtualOrderEngine(default_intents() if intents is None else intents)
    reconstructor = VirtualL3Reconstructor(engine, product_id or default_config.symbol)
    for message in iter_tardis_lines(input_path):
        reconstructor.consume(message)
    reconstruction = reconstructor.finish()
    engine.finish()
    return engine, reconstruction


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def build_outputs(
    engine: VirtualOrderEngine,
    reconstruction: dict[str, Any],
    *,
    deterministic: bool | None = None,
    config: FiveCConfig | None = None,
) -> dict[str, Any]:
    config = config or load_five_c_config(DEFAULT_CONFIG_PATH)
    orders = [order.as_dict() for order in engine.orders]
    invariant_report = engine.invariant_report()
    outcome_counts = Counter(order["outcome"] for order in orders)
    evidence_order_counts = Counter()
    evidence_event_counts = Counter()
    for order in engine.orders:
        for evidence, count in order.fill_evidence.items():
            evidence_order_counts[evidence] += 1
            evidence_event_counts[evidence] += count
    initial = sorted(order.ahead_volume_initial for order in engine.orders)
    distribution = {
        "min": decimal_text(initial[0]) if initial else None,
        "median": (
            decimal_text(initial[len(initial) // 2]) if initial else None
        ),
        "max": decimal_text(initial[-1]) if initial else None,
    }
    totals = {
        name: decimal_text(sum((getattr(order, name) for order in engine.orders), ZERO))
        for name in (
            "ahead_trade_depletion",
            "ahead_cancel_depletion",
            "ahead_modify_decrease",
            "ahead_priority_loss",
            "own_filled_volume",
        )
    }
    summary = {
        "result": (
            "PASS"
            if reconstruction["valid"] and invariant_report["valid"] and not engine.pending
            else "FAIL"
        ),
        "label_type": "L3-derived shadow-fill label",
        "definition": (
            "Historical L3 order flow is held fixed and a small passive virtual "
            "order is inserted without affecting subsequent observed order flow."
        ),
        "observed_l3_truth": "actual ahead-order executions, cancellations, and modifications",
        "counterfactual_component": "virtual fills under a no-market-impact assumption",
        "independent_branches": True,
        "snapshot_internal_fifo_assumed": False,
        "scope": {
            "placement": "BBO only",
            "sizes_btc": [decimal_text(value) for value in config.virtual_sizes],
            "ttls_seconds": list(config.ttls_seconds),
            "sample": str(config.input_path.relative_to(Path(__file__).parents[3])),
        },
        "placements": {
            "total": len(orders),
            "buy": sum(order["side"] == "buy" for order in orders),
            "sell": sum(order["side"] == "sell" for order in orders),
            "pending_unplaced": len(engine.pending),
        },
        "outcomes": {
            FULL_FILL: outcome_counts[FULL_FILL],
            PARTIAL_ONLY: outcome_counts[PARTIAL_ONLY],
            NO_FILL: outcome_counts[NO_FILL],
            CENSORED: outcome_counts[CENSORED],
        },
        "fill_evidence_order_counts": dict(sorted(evidence_order_counts.items())),
        "fill_evidence_event_counts": dict(sorted(evidence_event_counts.items())),
        "initial_ahead_volume_distribution": distribution,
        "depletion_totals": totals,
        "queue_ledger_rows": len(engine.ledger),
        "reconstruction_valid": reconstruction["valid"],
        "virtual_invariants_valid": invariant_report["valid"],
        "deterministic_replay": deterministic,
        "limitations": [
            "Virtual fills are no-market-impact counterfactual labels, not absolute fill ground truth.",
            "A real inserted order could alter later taker size and the observed match path.",
            "Snapshot-internal order rank is unknown; all orders resting at placement are only classified collectively as ahead.",
            "Diagnostic sizes are engine-validation choices, not a final empirical design.",
        ],
    }
    return {
        "orders": orders,
        "ledger": engine.ledger,
        "summary": summary,
        "invariants": invariant_report,
    }


def output_digest(outputs: dict[str, Any]) -> str:
    return hashlib.sha256(_stable_json(outputs).encode()).hexdigest()


def write_outputs(outputs: dict[str, Any], output_dir: str | Path) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "virtual_orders.jsonl").open("wt", encoding="utf-8") as target:
        for row in outputs["orders"]:
            target.write(_stable_json(row) + "\n")
    with (output / "queue_ledger.jsonl").open("wt", encoding="utf-8") as target:
        for row in outputs["ledger"]:
            target.write(_stable_json(row) + "\n")
    for key, filename in (
        ("summary", "truth_summary.json"),
        ("invariants", "invariant_report.json"),
    ):
        (output / filename).write_text(
            json.dumps(outputs[key], indent=2) + "\n", encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--verify-determinism", action="store_true")
    args = parser.parse_args()

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    config = load_five_c_config(args.config)
    engine, reconstruction = run_virtual_file(config.input_path, intents_from_config(config), config.symbol)
    outputs = build_outputs(engine, reconstruction, config=config)
    if args.verify_determinism:
        second_engine, second_reconstruction = run_virtual_file(
            config.input_path, intents_from_config(config), config.symbol
        )
        second = build_outputs(second_engine, second_reconstruction, config=config)
        deterministic = output_digest(outputs) == output_digest(second)
        outputs["summary"]["deterministic_replay"] = deterministic
        if not deterministic:
            outputs["summary"]["result"] = "FAIL"
    write_outputs(outputs, config.output_path)
    output_paths = tuple(config.output_path / name for name in (
        "virtual_orders.jsonl", "queue_ledger.jsonl",
        "truth_summary.json", "invariant_report.json",
    ))
    write_run_manifest(
        config, output_dir=config.output_path, status=outputs["summary"]["result"],
        started_at=started,
        completed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        input_paths=(config.input_path,), output_paths=output_paths,
    )
    print(json.dumps(outputs["summary"], indent=2))
    raise SystemExit(0 if outputs["summary"]["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
