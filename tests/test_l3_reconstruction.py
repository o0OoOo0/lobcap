from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from lobcap.l3.book import L3Book
from lobcap.l3.reconstruct import L3Reconstructor, SequenceTracker
from lobcap.l3.schema import NativeMessage, iter_tardis_lines


FIXTURES = Path(__file__).parent / "fixtures" / "tardis_coinbase"


def _messages(name):
    return list(iter_tardis_lines(FIXTURES / name))


def test_runtime_fixture_replays_late_generated_snapshot_overlap():
    reconstructor = L3Reconstructor()
    messages = _messages("overlap.jsonl")

    # Runtime capture order has eight sequence>S lifecycle events before the
    # generated snapshot appears.
    assert [message.type for message in messages[-2:]] == ["change", "full_snapshot"]
    for message in messages:
        reconstructor.consume(message)
    report = reconstructor.finish()

    assert report["valid"] is True
    assert report["snapshot"] == {
        "loaded": True,
        "sequence": 135356475328,
        "local_timestamp": "2026-09-01T00:00:04.4338508Z",
        "buffered_events_replayed": 8,
        "buffered_events_discarded_at_or_before_snapshot": 0,
    }
    assert report["sequence"]["first_applied_lifecycle_sequence"] == 135356475329
    assert report["sequence"]["capture_order"]["plus_one_pairs"] == 7
    assert report["sequence"]["applied_after_snapshot"]["plus_one_pairs"] == 8
    assert report["sequence"]["capture_order"]["gaps"] == 0
    assert report["events"]["applied"]["open_book_applied"] == 3
    assert report["events"]["applied"]["done_canceled_book_applied"] == 1
    assert report["events"]["applied"]["change_book_applied"] == 1

    canceled = "497e6efd-030f-4d60-a96d-304dbecbb276"
    changed = reconstructor.book.orders[
        "3ca46e63-dfcb-4eec-969b-9355d8ec8743"
    ]
    assert canceled not in reconstructor.book.orders
    assert changed.price == Decimal("78478.08")
    assert changed.remaining_size == Decimal("1.13679021")
    assert changed.source == "snapshot"
    assert changed.open_sequence is None
    assert changed.priority_sequence == 135356475336
    assert changed.fifo_known is True
    assert reconstructor.book.snapshot_internal_fifo_assumed is False


def test_received_does_not_add_to_book_and_actual_multiple_matches_fill_order():
    messages = _messages("partial_filled.jsonl")
    book = L3Book()

    # received is deliberately not an L3Book mutation.
    assert messages[0].type == "received"
    assert not book.orders

    assert book.apply_open(messages[1].payload)
    order_id = "a635fdc1-f646-4c5d-9030-a01854eb04c0"
    assert isinstance(book.orders[order_id].remaining_size, Decimal)
    assert book.apply_match(messages[2].payload)
    assert book.orders[order_id].remaining_size == Decimal("0.00005077")
    assert book.apply_match(messages[3].payload)
    assert book.orders[order_id].remaining_size == Decimal("0.00005074")
    assert book.apply_match(messages[4].payload)
    assert book.orders[order_id].remaining_size == Decimal("0")
    assert book.apply_done(messages[5].payload)
    assert order_id not in book.orders
    assert book.audit["negative_remaining_size"] == 0
    assert book.audit["filled_done_nonzero"] == 0


def test_actual_change_priority_rules_do_not_assume_snapshot_fifo():
    decrease, increase = _messages("change_priority.jsonl")

    decreasing_book = L3Book()
    decreasing_book.load_snapshot(
        {
            "bids": [],
            "asks": [
                [
                    "79461.29",
                    "0.10923149",
                    "3e1dac31-6ba9-4b93-8208-67cc7a5aaa70",
                ],
                ["79461.29", "1", "snapshot-peer"],
            ],
        }
    )
    before = decreasing_book.active_order_ids("sell", "79461.29")
    assert decreasing_book.apply_change(decrease.payload)
    assert decreasing_book.active_order_ids("sell", "79461.29") == before
    decreased = decreasing_book.orders[decrease.payload["order_id"]]
    assert decreased.remaining_size == Decimal("0.01914285")
    # Priority is retained, but its absolute initial snapshot rank stays unknown.
    assert decreased.fifo_known is False

    increasing_book = L3Book()
    increasing_book.load_snapshot(
        {
            "bids": [],
            "asks": [
                [
                    "78559.43",
                    "0.00372007",
                    "bcd2b2cb-9b17-494c-bc93-7441b90c1edf",
                ],
                ["78559.43", "1", "snapshot-peer"],
            ],
        }
    )
    assert increasing_book.apply_change(increase.payload)
    assert increasing_book.active_order_ids("sell", "78559.43") == (
        "snapshot-peer",
        "bcd2b2cb-9b17-494c-bc93-7441b90c1edf",
    )
    increased = increasing_book.orders[increase.payload["order_id"]]
    assert increased.remaining_size == Decimal("0.00400000")
    assert increased.priority_sequence == 135356478987
    assert increased.fifo_known is True


def test_sequence_tracker_reports_gap_duplicate_and_reversal():
    tracker = SequenceTracker()
    for sequence in (100, 101, 103, 103, 102):
        tracker.observe(sequence, sequence)
    report = tracker.report()
    assert report["plus_one_pairs"] == 1
    assert report["gaps"] == 1
    assert report["duplicates"] == 1
    assert report["reversals"] == 1
    assert [item["kind"] for item in report["examples"]] == [
        "gap",
        "duplicate",
        "reversal",
    ]


def test_generated_snapshots_are_not_lifecycle_sequence_increments():
    reconstructor = L3Reconstructor(allow_recovery_snapshots=True)
    reconstructor.consume(
        _native(
            "full_snapshot",
            100,
            generated=True,
            bids=[["100", "1", "bid"]],
            asks=[["102", "1", "ask"]],
        )
    )
    reconstructor.consume(
        _native(
            "open",
            101,
            order_id="post-snapshot",
            side="buy",
            price="99",
            remaining_size="1",
        )
    )
    reconstructor.consume(
        _native(
            "full_snapshot",
            101,
            generated=True,
            bids=[["101", "2", "replacement-bid"]],
            asks=[["103", "1", "replacement-ask"]],
        )
    )
    reconstructor.consume(
        _native(
            "open",
            102,
            order_id="next-segment",
            side="buy",
            price="100",
            remaining_size="0.5",
        )
    )

    report = reconstructor.finish()
    assert report["valid"] is True
    assert report["sequence"]["capture_order"]["comparable_pairs"] == 1
    assert report["sequence"]["capture_order"]["plus_one_pairs"] == 1
    assert report["sequence"]["capture_order"]["duplicates"] == 0


def test_reconstruction_segment_becomes_invalid_on_sequence_gap():
    messages = _messages("overlap.jsonl")
    changed_payload = dict(messages[3].payload)
    changed_payload["sequence"] += 1
    messages[3] = replace(messages[3], payload=changed_payload)
    reconstructor = L3Reconstructor()
    for message in messages:
        reconstructor.consume(message)
    report = reconstructor.finish()
    assert report["valid"] is False
    assert report["sequence"]["capture_order"]["gaps"] >= 1
    assert any(error["kind"] == "capture_sequence_gap" for error in report["errors"])


def _native(message_type, sequence, **fields):
    payload = {
        "type": message_type,
        "product_id": "BTC-USD",
        "sequence": sequence,
        "time": f"2026-01-01T00:00:0{sequence % 10}Z",
        **fields,
    }
    return NativeMessage(payload["time"], payload, sequence)


def test_snapshot_resting_order_seen_as_taker_is_reconciled_at_done():
    reconstructor = L3Reconstructor()
    taker_id = "snapshot-bid-now-aggressor"
    reconstructor.consume(
        _native(
            "full_snapshot",
            100,
            generated=True,
            bids=[["90", "0.5", taker_id]],
            asks=[["101", "0.5", "maker"]],
        )
    )
    reconstructor.consume(
        _native(
            "match",
            101,
            maker_order_id="maker",
            taker_order_id=taker_id,
            side="sell",
            price="101",
            size="0.5",
        )
    )
    reconstructor.consume(
        _native(
            "done",
            102,
            order_id=taker_id,
            side="buy",
            reason="filled",
            price="105",
            remaining_size="0",
        )
    )

    report = reconstructor.finish()
    assert report["valid"] is True
    assert taker_id not in reconstructor.book.orders
    assert report["book"]["audit_counts"]["resting_order_seen_as_taker"] == 1
    assert report["book"]["audit_counts"]["taker_matches_reconciled_at_done"] == 1
    assert report["events"]["unknown_done_boundary_or_unclassified"] == 0
    assert report["events"]["applied"]["done_filled_after_taker_match"] == 1
    assert report["book"]["pending_taker_reconciliations"] == 0


def test_partial_taker_matches_reconcile_at_remainder_change_then_maker_fill():
    reconstructor = L3Reconstructor()
    order_id = "snapshot-order"
    reconstructor.consume(
        _native(
            "full_snapshot",
            100,
            generated=True,
            bids=[["116341.8", "0.10288913", order_id]],
            asks=[
                ["117162.04", "0.02560556", "maker-1"],
                ["117162.05", "0.01811166", "maker-2"],
            ],
        )
    )
    for sequence, maker, price, size in (
        (101, "maker-1", "117162.04", "0.02560556"),
        (102, "maker-2", "117162.05", "0.01811166"),
    ):
        reconstructor.consume(
            _native(
                "match",
                sequence,
                maker_order_id=maker,
                taker_order_id=order_id,
                side="sell",
                price=price,
                size=size,
            )
        )
    reconstructor.consume(
        _native(
            "change",
            103,
            order_id=order_id,
            side="buy",
            reason="remainder_after_modification",
            old_price="116341.8",
            new_price="117163.19",
            old_size="0.10288913",
            new_size="0.07477105",
        )
    )
    reconstructor.consume(
        _native(
            "match",
            104,
            maker_order_id=order_id,
            taker_order_id="new-taker",
            side="buy",
            price="117163.19",
            size="0.07477105",
        )
    )
    reconstructor.consume(
        _native(
            "done",
            105,
            order_id=order_id,
            side="buy",
            reason="filled",
            price="117163.19",
            remaining_size="0",
        )
    )

    report = reconstructor.finish()
    assert report["valid"] is True
    assert report["book"]["audit_counts"]["resting_order_seen_as_taker"] == 2
    assert report["book"]["audit_counts"]["taker_matches_reconciled_at_change"] == 1
    assert report["book"]["pending_taker_reconciliations"] == 0


def test_opt_in_recovery_snapshot_starts_new_acquisition_segment():
    reconstructor = L3Reconstructor(allow_recovery_snapshots=True)
    reconstructor.consume(
        _native(
            "full_snapshot",
            100,
            generated=True,
            bids=[["100", "1", "old-order"]],
            asks=[["102", "1", "old-ask"]],
        )
    )
    reconstructor.consume(
        _native(
            "open",
            101,
            order_id="pre-gap",
            side="buy",
            price="99",
            remaining_size="1",
        )
    )
    reconstructor.consume(
        _native(
            "done",
            105,
            order_id="missing-era",
            side="buy",
            reason="canceled",
            price="98",
            remaining_size="1",
        )
    )
    reconstructor.consume(
        _native(
            "full_snapshot",
            105,
            generated=True,
            bids=[["101", "2", "new-order"]],
            asks=[["103", "1", "new-ask"]],
        )
    )
    reconstructor.consume(
        _native(
            "open",
            106,
            order_id="post-gap",
            side="buy",
            price="100",
            remaining_size="0.5",
        )
    )

    report = reconstructor.finish()
    assert report["valid"] is True
    assert report["sequence"]["capture_order"]["gaps"] == 1
    assert report["acquisition_segments"]["count"] == 2
    boundary = report["acquisition_segments"]["recovered_boundaries"][0]
    assert boundary["reason"] == "capture_sequence_gap"
    assert boundary["snapshot_sequence"] == 105
    assert boundary["buffered_events_discarded_at_or_before_snapshot"] == 1
    assert set(reconstructor.book.orders) == {"new-order", "new-ask", "post-gap"}


def test_multidate_recovery_is_forbidden_inside_evaluation_window():
    from lobcap.l2.multidate import MultiDateReconstructor

    reconstructor = MultiDateReconstructor()
    reconstructor.consume(
        _native(
            "full_snapshot",
            100,
            generated=True,
            bids=[["100", "1", "bid"]],
            asks=[["102", "1", "ask"]],
        )
    )
    reconstructor.consume(
        _native(
            "open",
            101,
            order_id="pre-gap",
            side="buy",
            price="99",
            remaining_size="1",
        )
    )
    reconstructor.virtual_engine = object()
    reconstructor.consume(
        _native(
            "done",
            105,
            order_id="missing-era",
            side="buy",
            reason="canceled",
            price="98",
            remaining_size="1",
        )
    )
    assert reconstructor.valid is False
    assert any(error["kind"] == "capture_sequence_gap" for error in reconstructor.errors)


def test_tardis_prefix_is_separate_from_unchanged_native_payload():
    message = _messages("partial_filled.jsonl")[0]
    assert message.local_timestamp == "2026-09-01T00:00:02.3228418Z"
    assert "local_timestamp" not in message.payload
    assert message.payload["time"] == "2026-09-01T00:00:02.284032Z"
    assert message.payload["sequence"] == 135356475403
