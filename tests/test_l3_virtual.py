from decimal import Decimal
from pathlib import Path

import pytest

from lobcap.l3.book import L3Book
from lobcap.l3.schema import NativeMessage, iter_tardis_lines
from lobcap.l3.virtual import (
    CENSORED,
    FULL_FILL,
    NO_FILL,
    PARTIAL_ONLY,
    PlacementIntent,
    VirtualOrderEngine,
    build_outputs,
    run_virtual_file,
)


ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "tardis_coinbase"
RAW_MINUTE = (
    ROOT / "artifacts" / "validation" / "tardis_coinbase" / "raw_http_slice.txt"
)


@pytest.fixture(scope="module")
def actual_minute_outputs():
    engine, reconstruction = run_virtual_file(RAW_MINUTE)
    return engine, build_outputs(engine, reconstruction)


def _actual_messages(name: str) -> list[NativeMessage]:
    return list(iter_tardis_lines(FIXTURES / name))


def _place_on_book(
    book: L3Book,
    marker: NativeMessage,
    *,
    side: str,
    size: str = "0.01",
    ttl: int = 30,
) -> tuple[VirtualOrderEngine, object]:
    engine = VirtualOrderEngine(
        [
            PlacementIntent(
                virtual_order_id="target",
                side=side,
                size=Decimal(size),
                ttl_seconds=ttl,
                after_sequence=marker.sequence,
            )
        ]
    )
    engine.after_event(marker, book)
    assert not engine.pending
    return engine, engine.orders[0]


def _apply(engine: VirtualOrderEngine, book: L3Book, message: NativeMessage) -> None:
    engine.before_event(message, book)
    applied = {
        "open": book.apply_open,
        "match": book.apply_match,
        "done": book.apply_done,
        "change": book.apply_change,
    }[message.type](message.payload)
    assert applied
    engine.after_event(message, book)


def test_actual_minute_grid_covers_fill_outcomes_and_queue_evolution(
    actual_minute_outputs,
):
    engine, outputs = actual_minute_outputs
    summary = outputs["summary"]
    assert summary["result"] == "PASS"
    assert summary["placements"] == {
        "total": 64,
        "buy": 32,
        "sell": 32,
        "pending_unplaced": 0,
    }
    for outcome in (FULL_FILL, PARTIAL_ONLY, NO_FILL, CENSORED):
        assert summary["outcomes"][outcome] > 0
    assert summary["fill_evidence_order_counts"]["SAME_PRICE_BEHIND_MATCH"] > 0
    assert summary["fill_evidence_order_counts"]["TRADE_THROUGH"] > 0
    assert outputs["invariants"]["valid"] is True

    assert any(row["event_type"] == "post_placement_open" for row in engine.ledger)
    assert any(
        Decimal(row["trade_depletion"]) > 0
        and Decimal(row["ahead_after"]) > 0
        for row in engine.ledger
    )
    assert any(Decimal(row["cancel_depletion"]) > 0 for row in engine.ledger)


def test_actual_grid_placement_is_after_exact_sequence_and_existing_level_is_ahead(
    actual_minute_outputs,
):
    engine, _ = actual_minute_outputs
    for order in engine.orders:
        placement = next(
            row
            for row in engine.ledger
            if row["virtual_order_id"] == order.virtual_order_id
            and row["event_type"] == "placement"
        )
        assert placement["sequence"] == order.placement_sequence
        assert order.ahead_volume_initial == sum(order.ahead_initial.values(), Decimal(0))
        assert order.ahead_initial
        subsequent = [
            row["sequence"]
            for row in engine.ledger
            if row["virtual_order_id"] == order.virtual_order_id
            and row["event_type"] not in {"placement", "ttl_expiry", "sample_end_censor"}
            and row["sequence"] is not None
        ]
        assert all(sequence > order.placement_sequence for sequence in subsequent)


def test_virtual_orders_are_independent_counterfactual_branches(actual_minute_outputs):
    engine, _ = actual_minute_outputs
    small = next(order for order in engine.orders if order.virtual_order_id == "v-05-buy-0.001-30s")
    large = next(order for order in engine.orders if order.virtual_order_id == "v-05-buy-0.01-30s")
    assert small.price == large.price
    assert small.placement_sequence == large.placement_sequence
    assert small.ahead_initial == large.ahead_initial
    assert small.virtual_order_id not in large.ahead_initial
    assert large.virtual_order_id not in small.ahead_initial
    assert small.own_filled_volume == small.original_size
    assert large.own_filled_volume == large.original_size


def test_actual_ahead_partial_matches_and_filled_done_are_not_virtual_fills():
    received, opened, *events = _actual_messages("partial_filled.jsonl")
    book = L3Book()
    assert received.type == "received"
    assert book.apply_open(opened.payload)
    engine, order = _place_on_book(book, opened, side="buy")

    first_match, second_match, final_match, done = events
    _apply(engine, book, first_match)
    assert order.ahead_volume_remaining == Decimal("0.00005077")
    assert order.ahead_trade_depletion == Decimal("0.00000014")
    assert order.own_filled_volume == 0
    _apply(engine, book, second_match)
    _apply(engine, book, final_match)
    _apply(engine, book, done)
    assert order.ahead_volume_remaining == 0
    assert order.ahead_trade_depletion == Decimal("0.00005091")
    assert order.own_filled_volume == 0
    assert engine.invariant_report()["valid"] is True


def test_snapshot_ahead_order_filled_as_taker_loses_priority_at_done():
    book = L3Book()
    book.load_snapshot(
        {
            "bids": [["100", "0.5", "snapshot-taker"]],
            "asks": [["101", "0.5", "maker"]],
        }
    )
    marker = NativeMessage(
        "2026-01-01T00:00:00Z",
        {"type": "received", "sequence": 100, "time": "2026-01-01T00:00:00Z"},
    )
    engine, order = _place_on_book(book, marker, side="buy")
    match = NativeMessage(
        "2026-01-01T00:00:01Z",
        {
            "type": "match",
            "sequence": 101,
            "time": "2026-01-01T00:00:01Z",
            "maker_order_id": "maker",
            "taker_order_id": "snapshot-taker",
            "side": "sell",
            "price": "101",
            "size": "0.1",
        },
    )

    _apply(engine, book, match)
    assert order.ahead_priority_loss == 0
    assert "snapshot-taker" in book.orders
    done = NativeMessage(
        "2026-01-01T00:00:02Z",
        {
            "type": "done",
            "sequence": 102,
            "time": "2026-01-01T00:00:02Z",
            "order_id": "snapshot-taker",
            "side": "buy",
            "price": "105",
            "reason": "filled",
            "remaining_size": "0.4",
        },
    )
    _apply(engine, book, done)
    assert order.ahead_priority_loss == 0
    assert order.ahead_trade_depletion == Decimal("0.5")
    assert order.ahead_volume_remaining == 0
    assert "snapshot-taker" not in book.orders
    assert book.audit["resting_order_seen_as_taker"] == 1


def test_actual_cancel_removes_current_ahead_quantity_separately():
    messages = _actual_messages("overlap.jsonl")
    cancel = next(message for message in messages if message.type == "done")
    snapshot = next(message for message in messages if message.type == "full_snapshot")
    book = L3Book()
    book.load_snapshot(snapshot.payload)
    marker = messages[0]
    engine, order = _place_on_book(book, marker, side="buy")
    before = order.ahead_volume_remaining
    _apply(engine, book, cancel)
    assert order.ahead_cancel_depletion == Decimal("0.02127904")
    assert order.ahead_volume_remaining == before - Decimal("0.02127904")
    assert order.ahead_trade_depletion == 0


def test_actual_modify_decrease_retains_ahead_and_increase_loses_priority():
    decrease, increase = _actual_messages("change_priority.jsonl")
    earlier = _actual_messages("partial_filled.jsonl")[0]

    decrease_book = L3Book()
    decrease_book.load_snapshot(
        {"bids": [], "asks": [["79461.29", "0.10923149", decrease.payload["order_id"]]]}
    )
    decrease_engine, decreasing = _place_on_book(
        decrease_book, earlier, side="sell"
    )
    _apply(decrease_engine, decrease_book, decrease)
    assert decreasing.ahead_modify_decrease == Decimal("0.09008864")
    assert decreasing.ahead_priority_loss == 0
    assert decrease.payload["order_id"] in decreasing.ahead

    increase_book = L3Book()
    increase_book.load_snapshot(
        {"bids": [], "asks": [["78559.43", "0.00372007", increase.payload["order_id"]]]}
    )
    increase_engine, increasing = _place_on_book(
        increase_book, earlier, side="sell"
    )
    _apply(increase_engine, increase_book, increase)
    assert increasing.ahead_priority_loss == Decimal("0.00372007")
    assert increasing.ahead_volume_remaining == 0
    assert increase.payload["order_id"] in increasing.behind_ids


def test_actual_price_change_loses_priority_and_does_not_become_same_level_ahead():
    messages = _actual_messages("overlap.jsonl")
    change = next(message for message in messages if message.type == "change")
    book = L3Book()
    book.load_snapshot(
        {
            "bids": [[change.payload["old_price"], change.payload["old_size"], change.payload["order_id"]]],
            "asks": [],
        }
    )
    engine, order = _place_on_book(book, messages[0], side="buy")
    _apply(engine, book, change)
    assert order.ahead_priority_loss == Decimal(change.payload["old_size"])
    assert order.ahead_volume_remaining == 0
    assert change.payload["order_id"] not in order.ahead
    assert change.payload["order_id"] not in order.behind_ids


def test_modify_sizes_are_total_intended_quantity_after_partial_fill():
    marker = NativeMessage(
        "2026-09-01T00:00:00Z",
        {
            "type": "received", "product_id": "BTC-USD", "sequence": 10,
            "time": "2026-09-01T00:00:00Z", "order_id": "marker",
            "order_type": "limit", "side": "sell", "size": "1",
        },
    )
    book = L3Book()
    book.load_snapshot({"bids": [], "asks": [["101", "2", "maker"]]})
    engine, order = _place_on_book(book, marker, side="sell")
    match = NativeMessage(
        "2026-09-01T00:00:01Z",
        {
            "type": "match", "product_id": "BTC-USD", "sequence": 11,
            "time": "2026-09-01T00:00:01Z", "maker_order_id": "maker",
            "taker_order_id": "taker", "side": "sell", "price": "101",
            "size": "0.5",
        },
    )
    change = NativeMessage(
        "2026-09-01T00:00:02Z",
        {
            "type": "change", "product_id": "BTC-USD", "sequence": 12,
            "time": "2026-09-01T00:00:02Z", "order_id": "maker",
            "side": "sell", "reason": "modify_order", "old_price": "101",
            "new_price": "100", "old_size": "2", "new_size": "2",
        },
    )
    _apply(engine, book, match)
    assert book.orders["maker"].remaining_size == Decimal("1.5")
    _apply(engine, book, change)
    assert book.orders["maker"].remaining_size == Decimal("1.5")
    assert book.orders["maker"].price == Decimal("100")
    assert order.ahead_priority_loss == Decimal("1.5")
    assert engine.invariant_report()["valid"] is True


def test_remainder_after_modification_uses_current_remaining_size():
    marker = NativeMessage(
        "2026-09-01T00:00:00Z",
        {
            "type": "received", "product_id": "BTC-USD", "sequence": 20,
            "time": "2026-09-01T00:00:00Z", "order_id": "marker-2",
            "order_type": "limit", "side": "sell", "size": "1",
        },
    )
    book = L3Book()
    book.load_snapshot({"bids": [], "asks": [["101", "3", "maker-2"]]})
    engine, order = _place_on_book(book, marker, side="sell")
    change = NativeMessage(
        "2026-09-01T00:00:01Z",
        {
            "type": "change", "product_id": "BTC-USD", "sequence": 21,
            "time": "2026-09-01T00:00:01Z", "order_id": "maker-2",
            "side": "sell", "reason": "remainder_after_modification",
            "price": "101", "old_price": "101", "new_price": "100",
            "old_size": "3", "new_size": "2",
        },
    )
    _apply(engine, book, change)
    assert book.orders["maker-2"].remaining_size == Decimal("2")
    assert book.orders["maker-2"].price == Decimal("100")
    assert order.ahead_priority_loss == Decimal("3")
    assert engine.invariant_report()["valid"] is True


def test_stp_change_reduces_remaining_without_losing_priority():
    marker = NativeMessage(
        "2026-09-01T00:00:00Z",
        {
            "type": "received", "product_id": "BTC-USD", "sequence": 30,
            "time": "2026-09-01T00:00:00Z", "order_id": "marker-3",
            "order_type": "limit", "side": "buy", "size": "1",
        },
    )
    book = L3Book()
    book.load_snapshot({"bids": [["99", "3", "maker-3"]], "asks": []})
    engine, order = _place_on_book(book, marker, side="buy")
    change = NativeMessage(
        "2026-09-01T00:00:01Z",
        {
            "type": "change", "product_id": "BTC-USD", "sequence": 31,
            "time": "2026-09-01T00:00:01Z", "order_id": "maker-3",
            "side": "buy", "reason": "STP", "price": "99",
            "old_size": "3", "new_size": "2",
        },
    )
    _apply(engine, book, change)
    assert book.orders["maker-3"].remaining_size == Decimal("2")
    assert order.ahead_modify_decrease == Decimal("1")
    assert order.ahead_priority_loss == 0
    assert "maker-3" in order.ahead
    assert engine.invariant_report()["valid"] is True


def test_synthetic_same_price_behind_match_allocates_partial_then_full_fill():
    # Synthetic only because the event sizes are chosen to exercise exact
    # partial/full boundaries; real-minute coverage above verifies this evidence
    # type and real trade-through independently.
    marker = NativeMessage(
        "2026-09-01T00:00:00Z",
        {
            "type": "received",
            "product_id": "BTC-USD",
            "sequence": 10,
            "time": "2026-09-01T00:00:00Z",
            "order_id": "marker",
            "order_type": "limit",
            "side": "buy",
            "size": "1",
        },
    )
    book = L3Book()
    book.load_snapshot({"bids": [["100", "1", "ahead"]], "asks": []})
    engine, order = _place_on_book(book, marker, side="buy", size="0.01")
    cancel = NativeMessage(
        "2026-09-01T00:00:00.1Z",
        {"type": "done", "product_id": "BTC-USD", "sequence": 11, "time": "2026-09-01T00:00:00.1Z", "order_id": "ahead", "side": "buy", "price": "100", "remaining_size": "1", "reason": "canceled"},
    )
    opened = NativeMessage(
        "2026-09-01T00:00:00.2Z",
        {"type": "open", "product_id": "BTC-USD", "sequence": 12, "time": "2026-09-01T00:00:00.2Z", "order_id": "behind", "side": "buy", "price": "100", "remaining_size": "1"},
    )
    _apply(engine, book, cancel)
    _apply(engine, book, opened)
    for sequence, size in ((13, "0.004"), (14, "0.006")):
        match = NativeMessage(
            f"2026-09-01T00:00:00.{sequence}Z",
            {"type": "match", "product_id": "BTC-USD", "sequence": sequence, "time": f"2026-09-01T00:00:00.{sequence}Z", "maker_order_id": "behind", "taker_order_id": f"taker-{sequence}", "side": "buy", "price": "100", "size": size},
        )
        _apply(engine, book, match)
        if sequence == 13:
            assert order.remaining_size == Decimal("0.006")
            assert order.first_fill_sequence == 13
    assert order.outcome == FULL_FILL
    assert order.full_fill_sequence == 14
    assert engine.invariant_report()["valid"] is True
