import json
from decimal import Decimal
from pathlib import Path

import pytest

from lobcap.l2.multidate import (
    MultiDateReconstructor,
    aggregate_alpha,
    decompose_ahead_reduction,
    model_specs,
)
from lobcap.l2.schema import assert_blinded_mapping
from lobcap.l3.schema import NativeMessage, iter_tardis_lines


ROOT = Path(__file__).parents[1]
RAW = ROOT / "artifacts/validation/tardis_coinbase/raw_http_slice.txt"
FROZEN = ROOT / "artifacts/virtual_truth/virtual_orders.jsonl"


def change_message(*, old_price: str, new_price: str, old_size: str, new_size: str):
    return NativeMessage(
        "2025-10-01T00:00:00Z",
        {
            "type": "change",
            "product_id": "BTC-USD",
            "sequence": 1,
            "time": "2025-10-01T00:00:00Z",
            "order_id": "maker",
            "side": "buy",
            "reason": "modify_order",
            "old_price": old_price,
            "new_price": new_price,
            "old_size": old_size,
            "new_size": new_size,
        },
    )


def test_same_price_size_increase_is_hidden_priority_loss_not_visible_a():
    result = decompose_ahead_reduction(
        change_message(
            old_price="114110.02",
            new_price="114110.02",
            old_size="0.00002501",
            new_size="0.00003674",
        ),
        cancel=Decimal("0"),
        size_decrease=Decimal("0"),
        priority_loss=Decimal("0.00002501"),
    )

    assert result["A_visible"] == Decimal("0")
    assert result["H_hidden"] == Decimal("0.00002501")
    assert (
        result["ahead_same_price_size_increase_priority_loss"]
        == Decimal("0.00002501")
    )


def test_price_change_away_is_visible_old_level_depletion():
    result = decompose_ahead_reduction(
        change_message(
            old_price="114110.02",
            new_price="114110.01",
            old_size="0.00002501",
            new_size="0.00003674",
        ),
        cancel=Decimal("0"),
        size_decrease=Decimal("0"),
        priority_loss=Decimal("0.00002501"),
    )

    assert result["A_visible"] == Decimal("0.00002501")
    assert result["H_hidden"] == Decimal("0")
    assert result["ahead_price_change_away"] == Decimal("0.00002501")


def test_cancel_and_same_price_decrease_are_visible_without_clipping():
    result = decompose_ahead_reduction(
        NativeMessage(
            "2025-10-01T00:00:00Z",
            {"type": "done", "sequence": 1, "time": "2025-10-01T00:00:00Z"},
        ),
        cancel=Decimal("0.08"),
        size_decrease=Decimal("0.005"),
        priority_loss=Decimal("0"),
    )

    assert result["A_visible"] == Decimal("0.085")
    assert result["H_hidden"] == Decimal("0")


def test_hidden_share_denominator_includes_trade_ahead_reduction():
    result = aggregate_alpha([
        {
            "A_visible": "3",
            "C": "4",
            "H_hidden": "1",
            "total_observed_L3_ahead_reduction": "10",
            "A_le_C": True,
            "A_visible_le_C": True,
        }
    ])

    assert result["hidden_priority_loss_share"] == "0.1"
    assert result["sum_total_observed_L3_ahead_reduction"] == "10"


def test_streaming_multidate_adapter_reproduces_frozen_first_minute_truth():
    if not RAW.is_file():
        pytest.skip(
            "requires excluded raw Tardis slice: "
            "artifacts/validation/tardis_coinbase/raw_http_slice.txt"
        )
    reconstructor = MultiDateReconstructor()
    reconstructor.start_window("2026-09-01", 0, model_specs(None))
    for message in iter_tardis_lines(RAW):
        reconstructor.consume(message)
    result = reconstructor.finish_window()
    frozen = [json.loads(line) for line in FROZEN.read_text(encoding="utf-8").splitlines()]

    assert len(result["truth"]) == len(frozen) == 64
    assert result["diagnostics"]["pending_unplaced"] == 576
    assert result["diagnostics"]["virtual_invariants"]["valid"] is True
    assert not any(result["diagnostics"]["fill_model_invariants"].values())
    assert not any(result["diagnostics"]["projection_invariants"].values())
    assert all(row["A_le_C"] for row in result["structural_rows"])
    assert all(row["A_visible_le_C"] for row in result["structural_rows"])
    assert all(
        Decimal(row["total_observed_L3_ahead_reduction"])
        == Decimal(row["A_raw_queue_depletion"])
        + Decimal(row["ahead_trade_depletion"])
        for row in result["structural_rows"]
    )
    for sample in result["diagnostics"]["l2_samples"]:
        assert_blinded_mapping(sample)

    ignored = {"virtual_order_id"}
    actual_rows = [
        {key: value for key, value in row.items() if key not in ignored}
        for row in result["truth"]
    ]
    frozen_rows = [
        {key: value for key, value in row.items() if key not in ignored}
        for row in frozen
    ]
    assert actual_rows == frozen_rows

    structural_events = [
        event
        for events in result["structural_events"].values()
        for event in events
    ]
    assert structural_events
    assert all(
        "l2_quantity_before" in event and "l2_quantity_after" in event
        for event in structural_events
    )
    for event in structural_events:
        visible = Decimal(event["A_visible"])
        c_value = Decimal(event["C"])
        if visible:
            assert event["l2_quantity_before"] is not None
            assert event["l2_quantity_after"] is not None
            assert visible <= c_value
