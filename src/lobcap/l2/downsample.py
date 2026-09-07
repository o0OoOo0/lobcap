"""Project reconstructed Coinbase L3 events through a blinded aggregate boundary.

Observed order identities are used only inside this trusted projection adapter
to locate affected price levels.  They are discarded before any ``L2Frame`` is
created and never reach a fill model.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from lobcap.l2.schema import (
    ZERO,
    BlindedPlacement,
    L2Frame,
    L2LevelUpdate,
    L2Projection,
    L2Trade,
    decimal_value,
    timestamp_ns,
)
from lobcap.l3.reconstruct import L3Reconstructor
from lobcap.l3.schema import NativeMessage, iter_tardis_lines


LevelKey = tuple[str, Decimal]


class L2ProjectingReconstructor(L3Reconstructor):
    """Verified L3 mutation engine with a one-way, ID-stripping L2 adapter."""

    def __init__(self, placements: Iterable[BlindedPlacement], product_id: str) -> None:
        super().__init__(product_id)
        self.placements = tuple(placements)
        self.by_sequence: dict[int, list[BlindedPlacement]] = defaultdict(list)
        for placement in self.placements:
            self.by_sequence[placement.placement_sequence].append(placement)
        self.frames: list[L2Frame] = []
        self.initial_queues: dict[str, Decimal] = {}
        self.placement_bbo_matches: dict[str, bool] = {}
        self.projection_audit: Counter[str] = Counter()
        self.last_projected_sequence: int | None = None
        self.last_projected_time: str | None = None

    def _quantity(self, key: LevelKey) -> Decimal:
        side, price = key
        return self.book.aggregate_sizes[side].get(price, ZERO)

    def _affected_levels(self, message: NativeMessage) -> tuple[LevelKey, ...]:
        payload = message.payload
        message_type = message.type
        keys: set[LevelKey] = set()
        if message_type == "open":
            keys.add(
                (
                    str(payload["side"]),
                    decimal_value(payload["price"], "open.price"),
                )
            )
        elif message_type == "match":
            keys.add(
                (
                    str(payload["side"]),
                    decimal_value(payload["price"], "match.price"),
                )
            )
        elif message_type in {"done", "change"}:
            # Identity is intentionally contained here, inside the projection
            # adapter.  Only the resolved aggregate side/price leaves it.
            order = self.book.orders.get(str(payload["order_id"]))
            if order is not None:
                keys.add((order.side, order.price))
                if message_type == "change" and payload.get("reason") in {
                    "modify_order", "remainder_after_modification"
                }:
                    keys.add(
                        (
                            order.side,
                            decimal_value(payload["new_price"], "change.new_price"),
                        )
                    )
        return tuple(sorted(keys, key=lambda item: (item[0], item[1])))

    def _capture_placements(self, message: NativeMessage) -> None:
        for placement in self.by_sequence.get(message.sequence, ()):
            queue = self.book.aggregate_sizes[placement.side].get(placement.price, ZERO)
            self.initial_queues[placement.virtual_order_id] = queue
            levels = self.book.levels[placement.side]
            bbo = (
                max(levels) if placement.side == "buy" else min(levels)
            ) if levels else None
            matches = bbo == placement.price
            self.placement_bbo_matches[placement.virtual_order_id] = matches
            if not matches:
                self.projection_audit["placement_not_at_bbo"] += 1
            if str(message.payload["time"]) != placement.placement_exchange_time:
                self.projection_audit["placement_time_mismatch"] += 1

    def _apply(self, message: NativeMessage) -> None:
        if not self.valid:
            return
        if (
            self.applied_sequence.last is not None
            and message.sequence != self.applied_sequence.last + 1
        ):
            super()._apply(message)
            return

        keys = self._affected_levels(message)
        before = {key: self._quantity(key) for key in keys}
        super()._apply(message)
        if not self.valid:
            return

        updates = tuple(
            L2LevelUpdate(key[0], key[1], before[key], self._quantity(key))
            for key in keys
            if before[key] != self._quantity(key)
        )
        trade = None
        if message.type == "match":
            trade = L2Trade(
                maker_side=str(message.payload["side"]),
                price=decimal_value(message.payload["price"], "match.price"),
                size=decimal_value(message.payload["size"], "match.size"),
            )
            update = next(
                (
                    item
                    for item in updates
                    if item.side == trade.maker_side and item.price == trade.price
                ),
                None,
            )
            if update is None or update.depletion != trade.size:
                self.projection_audit["same_level_trade_depth_mismatch"] += 1

        if updates or trade is not None:
            self.frames.append(
                L2Frame(
                    sequence=message.sequence,
                    exchange_time=str(message.payload["time"]),
                    time_ns=timestamp_ns(str(message.payload["time"])),
                    level_updates=updates,
                    trade=trade,
                )
            )
        self.last_projected_sequence = message.sequence
        self.last_projected_time = str(message.payload["time"])
        self._capture_placements(message)

    def projection(self, reconstruction_valid: bool) -> L2Projection:
        missing = len(self.placements) - len(self.initial_queues)
        if missing:
            self.projection_audit["missing_placements"] += missing
        if self.last_projected_sequence is None or self.last_projected_time is None:
            raise ValueError("no post-snapshot lifecycle was projected")
        return L2Projection(
            frames=tuple(self.frames),
            initial_queues=self.initial_queues,
            placement_bbo_matches=self.placement_bbo_matches,
            sample_end_sequence=self.last_projected_sequence,
            sample_end_time=self.last_projected_time,
            sample_end_time_ns=timestamp_ns(self.last_projected_time),
            reconstruction_valid=reconstruction_valid,
            projection_invariants=dict(sorted(self.projection_audit.items())),
        )


def downsample_file(
    input_path: str | Path,
    placements: Iterable[BlindedPlacement],
    product_id: str = "BTC-USD",
) -> tuple[L2Projection, dict]:
    reconstructor = L2ProjectingReconstructor(placements, product_id)
    for message in iter_tardis_lines(input_path):
        reconstructor.consume(message)
    reconstruction = reconstructor.finish()
    return reconstructor.projection(reconstruction["valid"]), reconstruction
