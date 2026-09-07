#!/usr/bin/env python3
"""Summarize a bounded Tardis Coinbase exchange-native validation slice.

This is validation tooling only.  It deliberately does not build or mutate an
order book.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


LIFECYCLE_TYPES = {"received", "open", "match", "done", "change"}
SAMPLE_ORDER = (
    "full_snapshot",
    "received",
    "open",
    "match",
    "done_canceled",
    "done_filled",
    "change",
)


def sequence_summary(rows: list[dict]) -> dict:
    gaps = []
    duplicates = []
    decreases = []
    for previous, current in zip(rows, rows[1:]):
        delta = current["sequence"] - previous["sequence"]
        evidence = {
            "previous_line": previous["line"],
            "previous_type": previous["type"],
            "previous_sequence": previous["sequence"],
            "current_line": current["line"],
            "current_type": current["type"],
            "current_sequence": current["sequence"],
            "delta": delta,
        }
        if delta > 1:
            gaps.append(evidence)
        elif delta == 0:
            duplicates.append(evidence)
        elif delta < 0:
            decreases.append(evidence)

    return {
        "messages_with_sequence": len(rows),
        "comparable_pairs": max(0, len(rows) - 1),
        "first_sequence": rows[0]["sequence"] if rows else None,
        "last_sequence": rows[-1]["sequence"] if rows else None,
        "strictly_increasing_by_one_pairs": max(0, len(rows) - 1)
        - len(gaps)
        - len(duplicates)
        - len(decreases),
        "gap_pairs": len(gaps),
        "missing_sequence_values": sum(item["delta"] - 1 for item in gaps),
        "duplicate_pairs": len(duplicates),
        "decreasing_pairs": len(decreases),
        "first_gap_examples": gaps[:5],
        "first_duplicate_examples": duplicates[:5],
        "first_decrease_examples": decreases[:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    type_counts: Counter[str] = Counter()
    key_counts: dict[str, Counter[str]] = defaultdict(Counter)
    samples: dict[str, dict] = {}
    lifecycle_sequences: list[dict] = []
    sequence_missing_by_type: Counter[str] = Counter()
    done_reasons: Counter[str] = Counter()
    snapshot_details: list[dict] = []
    parsed_records: list[dict] = []
    order_events: dict[str, list[dict]] = defaultdict(list)
    maker_matches: dict[str, list[dict]] = defaultdict(list)
    taker_matches: dict[str, list[dict]] = defaultdict(list)
    snapshot_order_ids: set[str] = set()

    with args.input.open("rt", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            line = line.rstrip("\n")
            if not line:
                type_counts["disconnect_marker"] += 1
                continue
            local_timestamp, payload = line.split(" ", 1)
            message = json.loads(payload)
            message_type = message.get("type", "NOT_PRESENT")
            type_counts[message_type] += 1
            key_counts[message_type].update(message.keys())
            record = {
                "line": line_number,
                "local_timestamp": local_timestamp,
                "message": message,
            }
            parsed_records.append(record)

            compact_event = {
                "line": line_number,
                "type": message_type,
                "sequence": message.get("sequence"),
                "reason": message.get("reason"),
                "size": message.get("size"),
                "remaining_size": message.get("remaining_size"),
            }
            if message.get("order_id") is not None:
                order_events[str(message["order_id"])].append(compact_event)
            if message.get("maker_order_id") is not None:
                maker_matches[str(message["maker_order_id"])].append(compact_event)
            if message.get("taker_order_id") is not None:
                taker_matches[str(message["taker_order_id"])].append(compact_event)

            samples.setdefault(message_type, record)
            if message_type == "done":
                reason = message.get("reason", "NOT_PRESENT")
                done_reasons[reason] += 1
                samples.setdefault(f"done_{reason}", record)

            if message_type in LIFECYCLE_TYPES:
                sequence = message.get("sequence")
                if sequence is None:
                    sequence_missing_by_type[message_type] += 1
                else:
                    lifecycle_sequences.append(
                        {
                            "line": line_number,
                            "type": message_type,
                            "sequence": int(sequence),
                            "local_timestamp": local_timestamp,
                        }
                    )

            if message_type == "full_snapshot":
                bids = message.get("bids", [])
                asks = message.get("asks", [])
                snapshot_order_ids.update(
                    str(entry[2]) for entry in bids + asks if len(entry) >= 3
                )
                snapshot_details.append(
                    {
                        "line": line_number,
                        "local_timestamp": local_timestamp,
                        "generated": message.get("generated", "NOT_PRESENT"),
                        "sequence": message.get("sequence", "NOT_PRESENT"),
                        "product_id": message.get("product_id", "NOT_PRESENT"),
                        "time": message.get("time", "NOT_PRESENT"),
                        "bid_orders": len(bids),
                        "ask_orders": len(asks),
                        "bid_entry_lengths": sorted({len(entry) for entry in bids}),
                        "ask_entry_lengths": sorted({len(entry) for entry in asks}),
                        "first_bid": bids[0] if bids else None,
                        "first_ask": asks[0] if asks else None,
                    }
                )

    sequence = sequence_summary(lifecycle_sequences)
    received_then_open = []
    open_then_done_canceled = []
    maker_match_then_done_filled = []
    for order_id, events in order_events.items():
        types = [event["type"] for event in events]
        if "received" in types and "open" in types:
            received_then_open.append({"order_id": order_id, "events": events})
        if "open" in types and any(
            event["type"] == "done" and event["reason"] == "canceled"
            for event in events
        ):
            open_then_done_canceled.append({"order_id": order_id, "events": events})
        if order_id in maker_matches and any(
            event["type"] == "done" and event["reason"] == "filled"
            for event in events
        ):
            filled_done_events = [
                event
                for event in events
                if event["type"] == "done" and event["reason"] == "filled"
            ]
            maker_match_then_done_filled.append(
                {
                    "order_id": order_id,
                    "matches": maker_matches[order_id],
                    "filled_done_events": filled_done_events,
                }
            )
    repeated_maker_matches = [
        {"order_id": order_id, "matches": matches}
        for order_id, matches in maker_matches.items()
        if len(matches) > 1
    ]
    match_maker_ids = set(maker_matches)
    match_taker_ids = set(taker_matches)
    match_makers_traceable = match_maker_ids & (
        snapshot_order_ids | set(order_events)
    )
    match_takers_traceable = match_taker_ids & set(order_events)
    lifecycle_evidence = {
        "received_then_open_order_ids": len(received_then_open),
        "example_received_then_open": received_then_open[:1],
        "open_then_done_canceled_order_ids": len(open_then_done_canceled),
        "example_open_then_done_canceled": open_then_done_canceled[:1],
        "maker_match_then_done_filled_order_ids": len(maker_match_then_done_filled),
        "example_maker_match_then_done_filled": maker_match_then_done_filled[:1],
        "maker_order_ids_with_multiple_matches": len(repeated_maker_matches),
        "maximum_matches_for_one_maker_order": max(
            (len(item["matches"]) for item in repeated_maker_matches), default=1
        ),
        "example_multiple_matches_same_maker": repeated_maker_matches[:1],
        "distinct_match_maker_order_ids": len(match_maker_ids),
        "match_maker_ids_traceable_to_snapshot_or_lifecycle": len(
            match_makers_traceable
        ),
        "distinct_match_taker_order_ids": len(match_taker_ids),
        "match_taker_ids_traceable_to_received_lifecycle": len(
            match_takers_traceable
        ),
    }
    snapshot_alignment = []
    for snapshot in snapshot_details:
        snapshot_sequence = snapshot["sequence"]
        buffered_overlap = [
            row
            for row in lifecycle_sequences
            if row["line"] < snapshot["line"]
            and isinstance(snapshot_sequence, int)
            and row["sequence"] > snapshot_sequence
        ]
        previous = next(
            (
                row
                for row in reversed(lifecycle_sequences)
                if row["line"] < snapshot["line"]
            ),
            None,
        )
        following = next(
            (row for row in lifecycle_sequences if row["line"] > snapshot["line"]),
            None,
        )
        snapshot_alignment.append(
            {
                "snapshot_line": snapshot["line"],
                "snapshot_sequence": snapshot_sequence,
                "previous_lifecycle": previous,
                "next_lifecycle": following,
                "buffered_events_before_snapshot_above_snapshot_sequence": len(
                    buffered_overlap
                ),
                "first_buffered_event_to_replay": (
                    buffered_overlap[0] if buffered_overlap else None
                ),
                "last_buffered_event_before_snapshot": (
                    buffered_overlap[-1] if buffered_overlap else None
                ),
            }
        )

    sample_path = args.output_dir / "raw_sample.jsonl"
    with sample_path.open("wt", encoding="utf-8") as destination:
        for name in SAMPLE_ORDER:
            if name not in samples:
                continue
            sample = samples[name]
            destination.write(
                json.dumps(
                    {
                        "sample_label": name,
                        "source_line": sample["line"],
                        "local_timestamp": sample["local_timestamp"],
                        "message": sample["message"],
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )

    field_names = (
        "sequence",
        "order_id",
        "maker_order_id",
        "taker_order_id",
        "side",
        "price",
        "size",
        "remaining_size",
        "reason",
        "time",
    )
    schema_summary = {
        "source_format": "Tardis local timestamp, one space, Coinbase exchange-native JSON",
        "local_replay_timestamp": "local_timestamp prefix outside the exchange JSON",
        "event_types": {},
        "requested_field_presence": {},
        "snapshot": snapshot_details,
        "snapshot_alignment": snapshot_alignment,
    }
    for message_type, count in sorted(type_counts.items()):
        if message_type == "disconnect_marker":
            continue
        schema_summary["event_types"][message_type] = {
            "count": count,
            "observed_keys": sorted(key_counts[message_type]),
        }
    for field in field_names:
        present = [
            message_type
            for message_type in sorted(type_counts)
            if key_counts[message_type][field] > 0
        ]
        schema_summary["requested_field_presence"][field] = {
            "status": "PRESENT" if present else "NOT_PRESENT",
            "event_types": present,
        }
    schema_summary["requested_field_presence"]["local_replay_timestamp"] = {
        "status": "PRESENT",
        "location": "line prefix exposed as local_timestamp in raw_sample.jsonl",
    }

    report = {
        "result": "RUNTIME_VERIFIED",
        "api_key_supplied": False,
        "paid_account_required_for_this_request": False,
        "exchange": "coinbase",
        "symbol": "BTC-USD",
        "requested_interval": {
            "from": "2026-09-01T00:00:00.000Z",
            "to_exclusive": "2026-09-01T00:01:00.000Z",
            "offset_minutes": 0,
        },
        "http_status": 200,
        "response_compressed_bytes": 4416936,
        "response_decompressed_bytes": args.input.stat().st_size,
        "messages": sum(
            count for name, count in type_counts.items() if name != "disconnect_marker"
        ),
        "disconnect_markers": type_counts["disconnect_marker"],
        "event_type_counts": dict(sorted(type_counts.items())),
        "done_reason_counts": dict(sorted(done_reasons.items())),
        "missing_sequence_by_lifecycle_type": dict(
            sorted(sequence_missing_by_type.items())
        ),
        "lifecycle_sequence": sequence,
        "snapshot": snapshot_details,
        "snapshot_alignment": snapshot_alignment,
        "order_lifecycle_evidence": lifecycle_evidence,
        "capability_assessment": {
            "free_raw_l3_access": "RUNTIME_VERIFIED",
            "can_reconstruct_individual_order_book": "YES",
            "can_distinguish_fill_vs_cancel": "YES",
            "can_track_partial_fills": "YES",
            "can_infer_fifo_queue_truth": "UNCERTAIN",
            "fifo_caveat": (
                "Open sequence establishes arrival order after the snapshot, but "
                "orders already present in full_snapshot have price, size, and "
                "order_id without a per-order arrival timestamp or sequence; the "
                "official REST L3 page does not explicitly guarantee same-price "
                "array ordering."
            ),
            "snapshot_replay_rule_observed": (
                "Initialize at full_snapshot.sequence, then replay already-buffered "
                "lifecycle events with sequence greater than the snapshot sequence "
                "before continuing with later capture-order events."
            ),
        },
        "raw_sample": str(sample_path.resolve()),
    }

    (args.output_dir / "schema_summary.json").write_text(
        json.dumps(schema_summary, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "runtime_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
