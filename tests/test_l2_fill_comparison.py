import ast
from decimal import Decimal
from pathlib import Path

import pytest

from lobcap.l2.compare import (
    FROZEN_TRUTH_SHA256,
    build_comparison,
    load_frozen_truth,
    sha256_file,
)
from lobcap.config import load_five_d_config
from lobcap.l2.fill_models import StreamingFillEngine, build_model_specs, predict_one
from lobcap.l2.schema import (
    BlindedPlacement,
    L2Frame,
    L2LevelUpdate,
    L2Projection,
    L2Trade,
    assert_blinded_mapping,
    timestamp_ns,
)


ROOT = Path(__file__).parents[1]
CONFIG = load_five_d_config(ROOT / "configs/experiments/5d_fill_comparison.yaml")
MODEL_SPECS = build_model_specs(CONFIG.alpha_grid, touch_fill_enabled=CONFIG.touch_fill_enabled)
RAW = ROOT / "artifacts/validation/tardis_coinbase/raw_http_slice.txt"
TRUTH = ROOT / "artifacts/virtual_truth/virtual_orders.jsonl"
MODELS = {model.name: model for model in MODEL_SPECS}


@pytest.fixture(scope="module")
def real_comparison():
    if not RAW.is_file():
        pytest.skip(
            "requires excluded raw Tardis slice: "
            "artifacts/validation/tardis_coinbase/raw_http_slice.txt"
        )
    return build_comparison(RAW, TRUTH)


def _time(second: int) -> str:
    return f"2026-09-01T00:00:{second:02d}Z"


def _placement(size: str = "1", ttl: int = 30) -> BlindedPlacement:
    return BlindedPlacement(
        virtual_order_id="virtual-test",
        placement_sequence=10,
        placement_exchange_time=_time(0),
        placement_time_ns=timestamp_ns(_time(0)),
        expiry_time=_time(ttl),
        expiry_time_ns=timestamp_ns(_time(ttl)),
        side="buy",
        price=Decimal("100"),
        size=Decimal(size),
        ttl_seconds=ttl,
    )


def _projection(
    frames: list[L2Frame], initial: str, sample_end: int = 20
) -> L2Projection:
    return L2Projection(
        frames=tuple(frames),
        initial_queues={"virtual-test": Decimal(initial)},
        placement_bbo_matches={"virtual-test": True},
        sample_end_sequence=99,
        sample_end_time=_time(sample_end),
        sample_end_time_ns=timestamp_ns(_time(sample_end)),
        reconstruction_valid=True,
        projection_invariants={},
    )


def _frame(
    sequence: int,
    second: int,
    *,
    before: str | None = None,
    after: str | None = None,
    trade_price: str | None = None,
    trade_size: str | None = None,
) -> L2Frame:
    updates = (
        (
            L2LevelUpdate(
                "buy", Decimal("100"), Decimal(before), Decimal(after)
            ),
        )
        if before is not None and after is not None
        else ()
    )
    trade = (
        L2Trade("buy", Decimal(trade_price), Decimal(trade_size))
        if trade_price is not None and trade_size is not None
        else None
    )
    return L2Frame(sequence, _time(second), timestamp_ns(_time(second)), updates, trade)


def test_real_projection_has_no_identity_leakage_and_uses_frozen_placements(
    real_comparison,
):
    leakage = real_comparison["leakage_check"]
    summary = real_comparison["model_metrics"]["summary"]
    assert leakage["valid"] is True
    assert leakage["forbidden_field_hits"] == []
    assert leakage["observed_exchange_uuid_hits"] == 0
    assert leakage["fill_model_l3_imports"] == []
    assert leakage["fill_model_receives_truth_rows"] is False
    assert leakage["same_placement_signature_all_models"] is True
    assert leakage["initial_queue_matches_posthoc_truth_ahead"] is True
    assert sha256_file(TRUTH) == FROZEN_TRUTH_SHA256
    assert leakage["expected_frozen_truth_sha256"] == FROZEN_TRUTH_SHA256
    assert summary["placements_compared"] == 64
    assert len(real_comparison["predictions"]) == 64 * 6
    assert real_comparison["invariant_report"]["valid"] is True


def test_fill_model_module_never_imports_l3_classes():
    source = (ROOT / "src/lobcap/l2/fill_models.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not [name for name in imported if name.startswith("lobcap.l3")]


def test_schema_rejects_order_identity_reason_and_l3_decomposition():
    for forbidden in (
        {"order_id": "actual"},
        {"trade": {"maker_order_id": "actual"}},
        {"level": {"reason": "canceled"}},
        {"ahead_order_ids": ["actual"]},
        {"cancel_depletion": "1"},
    ):
        with pytest.raises(ValueError):
            assert_blinded_mapping(forbidden)


def test_alpha_zero_ignores_nontrade_and_alpha_one_uses_all_of_it():
    placement = _placement()
    projection = _projection(
        [
            _frame(11, 1, before="4", after="0"),
            _frame(12, 2, trade_price="99", trade_size="1"),
        ],
        initial="4",
    )
    pessimistic, pessimistic_audit = predict_one(
        projection, placement, MODELS["alpha=0.00"]
    )
    optimistic, optimistic_audit = predict_one(
        projection, placement, MODELS["alpha=1.00"]
    )
    assert pessimistic.assumed_nontrade_queue_reduction == 0
    assert pessimistic.trade_queue_reduction == 1
    assert pessimistic.predicted_filled_quantity == 0
    assert optimistic.assumed_nontrade_queue_reduction == 4
    assert optimistic.trade_queue_reduction == 0
    assert optimistic.predicted_filled_quantity == 1
    assert not any(pessimistic_audit.values())
    assert not any(optimistic_audit.values())


def test_same_level_trade_is_not_double_counted_as_nontrade_depletion():
    projection = _projection(
        [_frame(11, 1, before="2", after="0", trade_price="100", trade_size="2")],
        initial="2",
    )
    prediction, audit = predict_one(
        projection, _placement(), MODELS["alpha=1.00"]
    )
    assert prediction.observed_nontrade_depletion == 0
    assert prediction.assumed_nontrade_queue_reduction == 0
    assert prediction.trade_queue_reduction == 2
    assert prediction.predicted_filled_quantity == 0
    assert audit["double_counted_trade"] == 0
    assert audit["trade_in_nontrade_depletion"] == 0


def test_touch_fill_is_distinct_from_alpha_one_and_fill_is_size_capped():
    projection = _projection(
        [_frame(11, 1, before="2", after="1", trade_price="100", trade_size="1")],
        initial="2",
    )
    queue_model, _ = predict_one(projection, _placement(), MODELS["alpha=1.00"])
    touch, _ = predict_one(projection, _placement(), MODELS["TOUCH_FILL"])
    assert queue_model.predicted_filled_quantity == 0
    assert touch.predicted_filled_quantity == 1

    huge_trade = _projection(
        [_frame(11, 1, trade_price="99", trade_size="10")], initial="0"
    )
    capped, audit = predict_one(
        huge_trade, _placement(size="0.01"), MODELS["TOUCH_FILL"]
    )
    assert capped.predicted_filled_quantity == Decimal("0.01")
    assert capped.remaining_quantity == 0
    assert audit["overfill"] == 0


def test_unresolved_sample_end_is_censored_not_no_fill_or_large_timing():
    projection = _projection([], initial="1", sample_end=5)
    prediction, _ = predict_one(
        projection, _placement(ttl=10), MODELS["alpha=0.00"]
    )
    assert prediction.outcome == "CENSORED"
    assert prediction.first_fill_time is None
    assert prediction.full_fill_time is None


def test_alpha_half_is_explicitly_uncalibrated_midpoint():
    assert MODELS["alpha=0.50"].interpretation == "uncalibrated midpoint diagnostic"


def test_streaming_engine_matches_batch_models_on_frozen_real_sample(
    real_comparison,
):
    projection = real_comparison["projection"]
    placements, _ = load_frozen_truth(TRUTH)
    pending = sorted(placements, key=lambda item: item.placement_sequence)
    engine = StreamingFillEngine(MODEL_SPECS)
    cursor = 0
    for frame in projection.frames:
        while cursor < len(pending) and pending[cursor].placement_sequence < frame.sequence:
            placement = pending[cursor]
            engine.add_placement(
                placement, projection.initial_queues[placement.virtual_order_id]
            )
            cursor += 1
        engine.on_frame(frame)
    while cursor < len(pending):
        placement = pending[cursor]
        engine.add_placement(
            placement, projection.initial_queues[placement.virtual_order_id]
        )
        cursor += 1
    streamed = engine.finish(
        projection.sample_end_sequence, projection.sample_end_time
    )
    batch = sorted(
        real_comparison["predictions"],
        key=lambda item: (item.model, item.placement_sequence, item.virtual_order_id),
    )
    assert [item.as_dict() for item in streamed] == [item.as_dict() for item in batch]
    assert not any(engine.invariants.values())
