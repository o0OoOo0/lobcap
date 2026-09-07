"""Fill assumptions that consume only the blinded L2 schema.

This module deliberately has no dependency on ``lobcap.l3``.  It cannot see
actual order identities, lifecycle reasons, FIFO positions, or the L3-derived
ahead decomposition used later for post-hoc error analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from lobcap.l2.schema import (
    ZERO,
    BlindedPlacement,
    L2Frame,
    L2Projection,
    decimal_text,
    timestamp_ns,
)


FULL_FILL = "FULL_FILL"
PARTIAL_ONLY = "PARTIAL_ONLY"
NO_FILL = "NO_FILL"
CENSORED = "CENSORED"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    alpha: Decimal | None
    touch_fill: bool = False
    interpretation: str = ""

    def __post_init__(self) -> None:
        if self.touch_fill != (self.alpha is None):
            raise ValueError("TOUCH_FILL must not have alpha; queue models must")
        if self.alpha is not None and not (ZERO <= self.alpha <= Decimal("1")):
            raise ValueError("alpha must be in [0,1]")


MODEL_SPECS = (
    ModelSpec("alpha=0.00", Decimal("0.00"), interpretation="pessimistic"),
    ModelSpec("alpha=0.25", Decimal("0.25")),
    ModelSpec(
        "alpha=0.50",
        Decimal("0.50"),
        interpretation="uncalibrated midpoint diagnostic",
    ),
    ModelSpec("alpha=0.75", Decimal("0.75")),
    ModelSpec(
        "alpha=1.00",
        Decimal("1.00"),
        interpretation="queue-optimistic depletion assumption",
    ),
    ModelSpec(
        "TOUCH_FILL",
        None,
        touch_fill=True,
        interpretation="queue-position-ignoring upper bound",
    ),
)


@dataclass(frozen=True, slots=True)
class Prediction:
    model: str
    alpha: Decimal | None
    virtual_order_id: str
    placement_sequence: int
    placement_exchange_time: str
    expiry_time: str
    side: str
    price: Decimal
    virtual_size: Decimal
    initial_queue: Decimal
    final_queue: Decimal
    predicted_filled_quantity: Decimal
    remaining_quantity: Decimal
    outcome: str
    first_fill_sequence: int | None
    first_fill_time: str | None
    full_fill_sequence: int | None
    full_fill_time: str | None
    observed_nontrade_depletion: Decimal
    assumed_nontrade_queue_reduction: Decimal
    trade_queue_reduction: Decimal
    executable_trade_volume: Decimal
    eligible_trade_events: int

    @property
    def any_fill(self) -> bool:
        return self.predicted_filled_quantity > ZERO

    @property
    def full_fill(self) -> bool:
        return self.predicted_filled_quantity == self.virtual_size

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "alpha": decimal_text(self.alpha) if self.alpha is not None else None,
            "virtual_order_id": self.virtual_order_id,
            "placement_sequence": self.placement_sequence,
            "placement_exchange_time": self.placement_exchange_time,
            "expiry_time": self.expiry_time,
            "side": self.side,
            "price": decimal_text(self.price),
            "virtual_size": decimal_text(self.virtual_size),
            "initial_queue": decimal_text(self.initial_queue),
            "final_queue": decimal_text(self.final_queue),
            "predicted_filled_quantity": decimal_text(
                self.predicted_filled_quantity
            ),
            "remaining_quantity": decimal_text(self.remaining_quantity),
            "outcome": self.outcome,
            "any_fill": self.any_fill,
            "full_fill": self.full_fill,
            "first_fill_sequence": self.first_fill_sequence,
            "first_fill_time": self.first_fill_time,
            "full_fill_sequence": self.full_fill_sequence,
            "full_fill_time": self.full_fill_time,
            "observed_nontrade_depletion": decimal_text(
                self.observed_nontrade_depletion
            ),
            "assumed_nontrade_queue_reduction": decimal_text(
                self.assumed_nontrade_queue_reduction
            ),
            "trade_queue_reduction": decimal_text(self.trade_queue_reduction),
            "executable_trade_volume": decimal_text(self.executable_trade_volume),
            "eligible_trade_events": self.eligible_trade_events,
        }


def _is_executable_trade(placement: BlindedPlacement, frame: L2Frame) -> bool:
    trade = frame.trade
    if trade is None or trade.maker_side != placement.side:
        return False
    if placement.side == "buy":
        return trade.price <= placement.price
    return trade.price >= placement.price


def predict_one(
    projection: L2Projection,
    placement: BlindedPlacement,
    model: ModelSpec,
) -> tuple[Prediction, dict[str, int]]:
    queue = projection.initial_queues[placement.virtual_order_id]
    initial_queue = queue
    remaining = placement.size
    filled = ZERO
    observed_nontrade = ZERO
    assumed_nontrade = ZERO
    trade_queue_reduction = ZERO
    executable_trade_volume = ZERO
    eligible_events = 0
    first_sequence: int | None = None
    first_time: str | None = None
    full_sequence: int | None = None
    full_time: str | None = None
    seen_trade_sequences: set[int] = set()
    invariants = {
        "negative_queue": 0,
        "negative_remaining": 0,
        "overfill": 0,
        "double_counted_trade": 0,
        "trade_in_nontrade_depletion": 0,
    }

    for frame in projection.frames:
        if frame.sequence <= placement.placement_sequence:
            continue
        if frame.time_ns >= placement.expiry_time_ns:
            break
        if remaining == ZERO:
            break

        update = frame.update_at(placement.side, placement.price)
        level_depletion = update.depletion if update is not None else ZERO
        same_level_trade = ZERO
        if (
            frame.trade is not None
            and frame.trade.maker_side == placement.side
            and frame.trade.price == placement.price
        ):
            same_level_trade = frame.trade.size
        nontrade = max(ZERO, level_depletion - same_level_trade)
        observed_nontrade += nontrade
        if same_level_trade and nontrade + same_level_trade > level_depletion:
            invariants["trade_in_nontrade_depletion"] += 1

        if not model.touch_fill and nontrade:
            requested = model.alpha * nontrade  # type: ignore[operator]
            reduction = min(queue, requested)
            queue -= reduction
            assumed_nontrade += reduction

        if not _is_executable_trade(placement, frame):
            continue
        if frame.sequence in seen_trade_sequences:
            invariants["double_counted_trade"] += 1
            continue
        seen_trade_sequences.add(frame.sequence)
        eligible_events += 1
        trade_size = frame.trade.size  # type: ignore[union-attr]
        executable_trade_volume += trade_size

        if model.touch_fill:
            virtual_fill = min(remaining, trade_size)
        else:
            consumed_queue = min(queue, trade_size)
            queue -= consumed_queue
            trade_queue_reduction += consumed_queue
            virtual_fill = min(remaining, trade_size - consumed_queue)

        if virtual_fill > ZERO:
            if first_sequence is None:
                first_sequence = frame.sequence
                first_time = frame.exchange_time
            remaining -= virtual_fill
            filled += virtual_fill
            if remaining == ZERO:
                full_sequence = frame.sequence
                full_time = frame.exchange_time

        if queue < ZERO:
            invariants["negative_queue"] += 1
        if remaining < ZERO:
            invariants["negative_remaining"] += 1
        if filled > placement.size:
            invariants["overfill"] += 1

    if remaining == ZERO:
        outcome = FULL_FILL
    elif projection.sample_end_time_ns < placement.expiry_time_ns:
        outcome = CENSORED
    elif filled > ZERO:
        outcome = PARTIAL_ONLY
    else:
        outcome = NO_FILL

    prediction = Prediction(
        model=model.name,
        alpha=model.alpha,
        virtual_order_id=placement.virtual_order_id,
        placement_sequence=placement.placement_sequence,
        placement_exchange_time=placement.placement_exchange_time,
        expiry_time=placement.expiry_time,
        side=placement.side,
        price=placement.price,
        virtual_size=placement.size,
        initial_queue=initial_queue,
        final_queue=queue,
        predicted_filled_quantity=filled,
        remaining_quantity=remaining,
        outcome=outcome,
        first_fill_sequence=first_sequence,
        first_fill_time=first_time,
        full_fill_sequence=full_sequence,
        full_fill_time=full_time,
        observed_nontrade_depletion=observed_nontrade,
        assumed_nontrade_queue_reduction=assumed_nontrade,
        trade_queue_reduction=trade_queue_reduction,
        executable_trade_volume=executable_trade_volume,
        eligible_trade_events=eligible_events,
    )
    return prediction, invariants


def evaluate_models(
    projection: L2Projection,
    placements: Iterable[BlindedPlacement],
    models: Iterable[ModelSpec] = MODEL_SPECS,
) -> tuple[list[Prediction], dict[str, int]]:
    placements = tuple(placements)
    predictions: list[Prediction] = []
    combined = {
        "negative_queue": 0,
        "negative_remaining": 0,
        "overfill": 0,
        "double_counted_trade": 0,
        "trade_in_nontrade_depletion": 0,
    }
    expected_ids = {placement.virtual_order_id for placement in placements}
    if expected_ids != set(projection.initial_queues):
        raise ValueError("projection queues do not match frozen placements")
    for model in models:
        model_ids: list[str] = []
        for placement in placements:
            prediction, invariants = predict_one(projection, placement, model)
            predictions.append(prediction)
            model_ids.append(prediction.virtual_order_id)
            for name, count in invariants.items():
                combined[name] += count
        if model_ids != [placement.virtual_order_id for placement in placements]:
            raise ValueError(f"placement order changed for model {model.name}")
    return predictions, combined


@dataclass(slots=True)
class _StreamingState:
    placement: BlindedPlacement
    model: ModelSpec
    queue: Decimal
    initial_queue: Decimal
    remaining: Decimal
    filled: Decimal = ZERO
    observed_nontrade: Decimal = ZERO
    assumed_nontrade: Decimal = ZERO
    trade_queue_reduction: Decimal = ZERO
    executable_trade_volume: Decimal = ZERO
    eligible_events: int = 0
    first_sequence: int | None = None
    first_time: str | None = None
    full_sequence: int | None = None
    full_time: str | None = None
    seen_trade_sequences: set[int] = field(default_factory=set)


class StreamingFillEngine:
    """Online equivalent of :func:`predict_one` over blinded L2 frames.

    Only active TTL intervals are retained, so long replay warm-up periods do
    not require storing raw messages or a full window of L2 frames.
    """

    def __init__(self, models: Iterable[ModelSpec] = MODEL_SPECS) -> None:
        self.models = tuple(models)
        self.active: list[_StreamingState] = []
        self.predictions: list[Prediction] = []
        self.placement_ids: set[str] = set()
        self.invariants = {
            "negative_queue": 0,
            "negative_remaining": 0,
            "overfill": 0,
            "double_counted_trade": 0,
            "trade_in_nontrade_depletion": 0,
        }

    def add_placement(
        self, placement: BlindedPlacement, initial_queue: Decimal
    ) -> None:
        if placement.virtual_order_id in self.placement_ids:
            raise ValueError(f"duplicate streaming placement: {placement.virtual_order_id}")
        self.placement_ids.add(placement.virtual_order_id)
        for model in self.models:
            self.active.append(
                _StreamingState(
                    placement=placement,
                    model=model,
                    queue=initial_queue,
                    initial_queue=initial_queue,
                    remaining=placement.size,
                )
            )

    def _finish_state(self, state: _StreamingState, outcome: str) -> None:
        self.predictions.append(
            Prediction(
                model=state.model.name,
                alpha=state.model.alpha,
                virtual_order_id=state.placement.virtual_order_id,
                placement_sequence=state.placement.placement_sequence,
                placement_exchange_time=state.placement.placement_exchange_time,
                expiry_time=state.placement.expiry_time,
                side=state.placement.side,
                price=state.placement.price,
                virtual_size=state.placement.size,
                initial_queue=state.initial_queue,
                final_queue=state.queue,
                predicted_filled_quantity=state.filled,
                remaining_quantity=state.remaining,
                outcome=outcome,
                first_fill_sequence=state.first_sequence,
                first_fill_time=state.first_time,
                full_fill_sequence=state.full_sequence,
                full_fill_time=state.full_time,
                observed_nontrade_depletion=state.observed_nontrade,
                assumed_nontrade_queue_reduction=state.assumed_nontrade,
                trade_queue_reduction=state.trade_queue_reduction,
                executable_trade_volume=state.executable_trade_volume,
                eligible_trade_events=state.eligible_events,
            )
        )

    def on_frame(self, frame: L2Frame) -> None:
        still_active: list[_StreamingState] = []
        for state in self.active:
            placement = state.placement
            if frame.time_ns >= placement.expiry_time_ns:
                outcome = PARTIAL_ONLY if state.filled > ZERO else NO_FILL
                self._finish_state(state, outcome)
                continue
            if frame.sequence <= placement.placement_sequence:
                still_active.append(state)
                continue

            update = frame.update_at(placement.side, placement.price)
            level_depletion = update.depletion if update is not None else ZERO
            same_level_trade = ZERO
            if (
                frame.trade is not None
                and frame.trade.maker_side == placement.side
                and frame.trade.price == placement.price
            ):
                same_level_trade = frame.trade.size
            nontrade = max(ZERO, level_depletion - same_level_trade)
            state.observed_nontrade += nontrade
            if same_level_trade and nontrade + same_level_trade > level_depletion:
                self.invariants["trade_in_nontrade_depletion"] += 1

            if not state.model.touch_fill and nontrade:
                requested = state.model.alpha * nontrade  # type: ignore[operator]
                reduction = min(state.queue, requested)
                state.queue -= reduction
                state.assumed_nontrade += reduction

            if _is_executable_trade(placement, frame):
                if frame.sequence in state.seen_trade_sequences:
                    self.invariants["double_counted_trade"] += 1
                else:
                    state.seen_trade_sequences.add(frame.sequence)
                    state.eligible_events += 1
                    trade_size = frame.trade.size  # type: ignore[union-attr]
                    state.executable_trade_volume += trade_size
                    if state.model.touch_fill:
                        virtual_fill = min(state.remaining, trade_size)
                    else:
                        consumed_queue = min(state.queue, trade_size)
                        state.queue -= consumed_queue
                        state.trade_queue_reduction += consumed_queue
                        virtual_fill = min(state.remaining, trade_size - consumed_queue)
                    if virtual_fill > ZERO:
                        if state.first_sequence is None:
                            state.first_sequence = frame.sequence
                            state.first_time = frame.exchange_time
                        state.remaining -= virtual_fill
                        state.filled += virtual_fill
                        if state.remaining == ZERO:
                            state.full_sequence = frame.sequence
                            state.full_time = frame.exchange_time

            if state.queue < ZERO:
                self.invariants["negative_queue"] += 1
            if state.remaining < ZERO:
                self.invariants["negative_remaining"] += 1
            if state.filled > placement.size:
                self.invariants["overfill"] += 1
            if state.remaining == ZERO:
                self._finish_state(state, FULL_FILL)
            else:
                still_active.append(state)
        self.active = still_active

    def finish(self, sample_end_sequence: int, sample_end_time: str) -> list[Prediction]:
        sample_end_time_ns = timestamp_ns(sample_end_time)
        for state in self.active:
            if state.remaining == ZERO:
                outcome = FULL_FILL
            elif sample_end_time_ns < state.placement.expiry_time_ns:
                outcome = CENSORED
            elif state.filled > ZERO:
                outcome = PARTIAL_ONLY
            else:
                outcome = NO_FILL
            self._finish_state(state, outcome)
        self.active = []
        expected = len(self.placement_ids) * len(self.models)
        if len(self.predictions) != expected:
            raise ValueError(
                f"streaming prediction cardinality mismatch: {len(self.predictions)} != {expected}"
            )
        return sorted(
            self.predictions,
            key=lambda item: (item.model, item.placement_sequence, item.virtual_order_id),
        )
