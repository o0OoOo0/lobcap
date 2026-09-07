"""Decimal, order-by-order Coinbase L3 state."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


SIDES = ("buy", "sell")
ZERO = Decimal("0")


def decimal_value(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal {field}={value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite decimal {field}={value!r}")
    return result


@dataclass(slots=True)
class Order:
    order_id: str
    side: str
    price: Decimal
    remaining_size: Decimal
    source: str
    open_sequence: int | None
    open_time: str | None
    priority_sequence: int | None
    fifo_known: bool


class L3Book:
    """Order and price-level indexes without assuming snapshot-internal FIFO."""

    snapshot_internal_fifo_assumed = False

    def __init__(self) -> None:
        self.orders: dict[str, Order] = {}
        # Plain dict values preserve insertion order. Snapshot insertion order is
        # retained only for deterministic storage and is never claimed as FIFO.
        self.levels: dict[str, dict[Decimal, dict[str, None]]] = {
            side: {} for side in SIDES
        }
        self.aggregate_sizes: dict[str, dict[Decimal, Decimal]] = {
            side: {} for side in SIDES
        }
        self.audit: Counter[str] = Counter()
        self.audit_examples: dict[str, dict[str, Any]] = {}
        self.pending_taker_match_size: Counter[str] = Counter()

    def _record(self, name: str, evidence: dict[str, Any]) -> None:
        self.audit[name] += 1
        self.audit_examples.setdefault(name, evidence)

    @staticmethod
    def _validate_side(side: str) -> None:
        if side not in SIDES:
            raise ValueError(f"unsupported side: {side!r}")

    def _index_add(self, order: Order) -> None:
        level = self.levels[order.side].setdefault(order.price, {})
        level[order.order_id] = None
        aggregates = self.aggregate_sizes[order.side]
        aggregates[order.price] = aggregates.get(order.price, ZERO) + order.remaining_size

    def _index_remove(self, order: Order) -> None:
        level = self.levels[order.side][order.price]
        level.pop(order.order_id)
        aggregates = self.aggregate_sizes[order.side]
        new_size = aggregates[order.price] - order.remaining_size
        if not level:
            self.levels[order.side].pop(order.price)
            aggregates.pop(order.price)
        else:
            aggregates[order.price] = new_size

    def _replace_size(self, order: Order, new_size: Decimal) -> None:
        aggregates = self.aggregate_sizes[order.side]
        aggregates[order.price] += new_size - order.remaining_size
        if aggregates[order.price] == ZERO:
            # Keep a zero aggregate while a zero-size order awaits its done.
            aggregates[order.price] = ZERO
        order.remaining_size = new_size

    def load_snapshot(self, payload: dict[str, Any]) -> None:
        self.orders.clear()
        self.levels = {side: {} for side in SIDES}
        self.aggregate_sizes = {side: {} for side in SIDES}
        self.pending_taker_match_size.clear()
        for side, key in (("buy", "bids"), ("sell", "asks")):
            for price_raw, size_raw, order_id_raw in payload[key]:
                order_id = str(order_id_raw)
                if order_id in self.orders:
                    self._record("duplicate_snapshot_order", {"order_id": order_id})
                    continue
                size = decimal_value(size_raw, "snapshot.size")
                if size < ZERO:
                    self._record(
                        "negative_snapshot_size",
                        {"order_id": order_id, "size": str(size)},
                    )
                    continue
                order = Order(
                    order_id=order_id,
                    side=side,
                    price=decimal_value(price_raw, "snapshot.price"),
                    remaining_size=size,
                    source="snapshot",
                    open_sequence=None,
                    open_time=None,
                    priority_sequence=None,
                    fifo_known=False,
                )
                self.orders[order_id] = order
                self._index_add(order)

    def apply_open(self, payload: dict[str, Any]) -> bool:
        order_id = str(payload["order_id"])
        if order_id in self.orders:
            self._record(
                "duplicate_open",
                {"order_id": order_id, "sequence": payload["sequence"]},
            )
            return False
        side = str(payload["side"])
        self._validate_side(side)
        size = decimal_value(payload["remaining_size"], "open.remaining_size")
        if size < ZERO:
            self._record(
                "negative_open_size",
                {"order_id": order_id, "size": str(size)},
            )
            return False
        sequence = int(payload["sequence"])
        order = Order(
            order_id=order_id,
            side=side,
            price=decimal_value(payload["price"], "open.price"),
            remaining_size=size,
            source="open",
            open_sequence=sequence,
            open_time=str(payload["time"]),
            priority_sequence=sequence,
            fifo_known=True,
        )
        self.orders[order_id] = order
        self._index_add(order)
        return True

    def apply_match(self, payload: dict[str, Any]) -> bool:
        order_id = str(payload["maker_order_id"])
        taker_id = str(payload["taker_order_id"])
        taker = self.orders.get(taker_id)
        if taker is not None and taker_id != order_id:
            self._record(
                "resting_order_seen_as_taker",
                {
                    "order_id": taker_id,
                    "sequence": payload["sequence"],
                    "old_side": taker.side,
                    "old_price": str(taker.price),
                    "old_remaining_size": str(taker.remaining_size),
                    "match_price": str(payload["price"]),
                },
            )
            self.pending_taker_match_size[taker_id] += decimal_value(
                payload["size"], "match.size"
            )
        order = self.orders.get(order_id)
        if order is None:
            self._record(
                "unknown_maker_match",
                {"order_id": order_id, "sequence": payload["sequence"]},
            )
            return False
        match_size = decimal_value(payload["size"], "match.size")
        event_price = decimal_value(payload["price"], "match.price")
        if event_price != order.price or payload["side"] != order.side:
            self._record(
                "maker_identity_mismatch",
                {
                    "order_id": order_id,
                    "sequence": payload["sequence"],
                    "book_side": order.side,
                    "event_side": payload["side"],
                    "book_price": str(order.price),
                    "event_price": str(event_price),
                },
            )
            return False
        remaining = order.remaining_size - match_size
        if remaining < ZERO:
            self._record(
                "negative_remaining_size",
                {
                    "order_id": order_id,
                    "sequence": payload["sequence"],
                    "before": str(order.remaining_size),
                    "match_size": str(match_size),
                },
            )
            return False
        self._replace_size(order, remaining)
        return True

    def apply_done(self, payload: dict[str, Any]) -> bool:
        order_id = str(payload["order_id"])
        order = self.orders.get(order_id)
        if order is None:
            self._record(
                "unknown_done_order",
                {
                    "order_id": order_id,
                    "sequence": payload["sequence"],
                    "reason": payload["reason"],
                },
            )
            return False
        taker_filled = self.pending_taker_match_size.pop(order_id, ZERO)
        is_filled = payload["reason"] == "filled"
        effective_remaining = ZERO if is_filled else order.remaining_size - taker_filled
        if not is_filled and effective_remaining < ZERO:
            self._record(
                "negative_taker_reconciled_remaining",
                {
                    "order_id": order_id,
                    "sequence": payload["sequence"],
                    "book": str(order.remaining_size),
                    "taker_match_size": str(taker_filled),
                },
            )
        reported = (
            decimal_value(payload["remaining_size"], "done.remaining_size")
            if "remaining_size" in payload else None
        )
        residual_filled_at_done = is_filled and reported == ZERO and order.remaining_size > ZERO
        if residual_filled_at_done:
            self._record(
                "filled_residual_removed_at_done",
                {
                    "order_id": order_id,
                    "sequence": payload["sequence"],
                    "book": str(order.remaining_size),
                    "pending_taker_match_size": str(taker_filled),
                    "reported": str(reported),
                },
            )
        elif reported is not None and reported != effective_remaining:
            self._record(
                "done_remaining_mismatch",
                {
                    "order_id": order_id,
                    "sequence": payload["sequence"],
                    "book": str(order.remaining_size),
                    "pending_taker_match_size": str(taker_filled),
                    "effective_remaining": str(effective_remaining),
                    "reported": str(reported),
                },
            )
        if is_filled and reported not in {None, ZERO}:
            self._record(
                "filled_done_nonzero",
                {
                    "order_id": order_id,
                    "sequence": payload["sequence"],
                    "remaining": str(reported),
                },
            )
        if taker_filled:
            self._record(
                "taker_matches_reconciled_at_done",
                {
                    "order_id": order_id,
                    "sequence": payload["sequence"],
                    "old_level_removed": str(order.remaining_size),
                    "taker_match_size": str(taker_filled),
                    "reported_remaining": str(payload.get("remaining_size")),
                },
            )
        self._index_remove(order)
        self.orders.pop(order_id)
        return True

    def apply_change(self, payload: dict[str, Any]) -> bool:
        order_id = str(payload["order_id"])
        order = self.orders.get(order_id)
        if order is None:
            self._record(
                "unknown_change_order",
                {"order_id": order_id, "sequence": payload["sequence"]},
            )
            return False
        reason = str(payload["reason"])
        if reason not in {"modify_order", "remainder_after_modification", "STP"}:
            self._record(
                "uncertain_change_reason",
                {
                    "order_id": order_id,
                    "sequence": payload["sequence"],
                    "reason": payload["reason"],
                },
            )
            return False

        if reason == "STP":
            old_price = new_price = decimal_value(payload["price"], "change.price")
        else:
            old_price = decimal_value(payload["old_price"], "change.old_price")
            new_price = decimal_value(payload["new_price"], "change.new_price")
        old_size = decimal_value(payload["old_size"], "change.old_size")
        new_size = decimal_value(payload["new_size"], "change.new_size")
        size_matches = (
            old_size >= order.remaining_size
            if reason == "modify_order"
            else old_size == order.remaining_size
        )
        if old_price != order.price or not size_matches:
            self._record(
                "change_old_state_mismatch",
                {
                    "order_id": order_id,
                    "sequence": payload["sequence"],
                    "book_price": str(order.price),
                    "old_price": str(old_price),
                    "book_size": str(order.remaining_size),
                    "old_size": str(old_size),
                },
            )
            return False
        taker_filled = self.pending_taker_match_size.pop(order_id, ZERO)
        already_executed = (
            old_size - order.remaining_size if reason == "modify_order" else ZERO
        )
        new_remaining = new_size - already_executed
        if new_remaining < ZERO:
            self._record(
                "negative_change_size",
                {
                    "order_id": order_id,
                    "new_total_size": str(new_size),
                    "already_executed": str(already_executed),
                    "new_remaining": str(new_remaining),
                },
            )
            return False

        loses_priority = (
            reason != "STP" and (new_price != old_price or new_size > old_size)
        )
        if loses_priority:
            self._index_remove(order)
            order.price = new_price
            order.remaining_size = new_remaining
            order.priority_sequence = int(payload["sequence"])
            order.fifo_known = True
            self._index_add(order)
        else:
            self._replace_size(order, new_remaining)
        if taker_filled:
            self._record(
                "taker_matches_reconciled_at_change",
                {
                    "order_id": order_id,
                    "sequence": payload["sequence"],
                    "old_level_removed": str(old_size),
                    "taker_match_size": str(taker_filled),
                    "new_remaining": str(new_remaining),
                },
            )
        return True

    def active_order_ids(self, side: str, price: Decimal | str) -> tuple[str, ...]:
        """Return the observable resting set in deterministic storage order.

        The tuple is not an assertion of FIFO among snapshot-origin orders.
        """
        self._validate_side(side)
        level = self.levels[side].get(decimal_value(price, "price"), {})
        return tuple(level)

    def crossed(self) -> bool:
        bid_prices = self.levels["buy"]
        ask_prices = self.levels["sell"]
        return bool(bid_prices and ask_prices and max(bid_prices) >= min(ask_prices))

    def check_internal_consistency(self) -> dict[str, int]:
        membership_errors = 0
        aggregate_errors = 0
        indexed_ids: set[str] = set()
        for side in SIDES:
            for price, level in self.levels[side].items():
                expected = ZERO
                for order_id in level:
                    order = self.orders.get(order_id)
                    if order is None or order.side != side or order.price != price:
                        membership_errors += 1
                        continue
                    indexed_ids.add(order_id)
                    expected += order.remaining_size
                if self.aggregate_sizes[side].get(price) != expected:
                    aggregate_errors += 1
            if set(self.aggregate_sizes[side]) != set(self.levels[side]):
                aggregate_errors += len(
                    set(self.aggregate_sizes[side]) ^ set(self.levels[side])
                )
        membership_errors += len(set(self.orders) - indexed_ids)
        return {
            "membership_errors": membership_errors,
            "aggregate_errors": aggregate_errors,
        }

    def summary(self) -> dict[str, Any]:
        internal = self.check_internal_consistency()
        result: dict[str, Any] = {
            "active_order_count": len(self.orders),
            "pending_taker_reconciliations": len(self.pending_taker_match_size),
            "pending_taker_match_size": format(
                sum(self.pending_taker_match_size.values(), ZERO), "f"
            ),
            "snapshot_internal_fifo_assumed": self.snapshot_internal_fifo_assumed,
            "crossed_book": self.crossed(),
            "internal_consistency": internal,
            "sides": {},
            "audit_counts": dict(sorted(self.audit.items())),
            "audit_examples": self.audit_examples,
        }
        for side in SIDES:
            levels = self.levels[side]
            aggregates = self.aggregate_sizes[side]
            order_count = sum(len(ids) for ids in levels.values())
            total_size = sum(aggregates.values(), ZERO)
            result["sides"][side] = {
                "price_level_count": len(levels),
                "active_order_count": order_count,
                "aggregate_size": format(total_size, "f"),
                "best_price": (
                    format(max(levels) if side == "buy" else min(levels), "f")
                    if levels
                    else None
                ),
            }
        return result
