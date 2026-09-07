"""Generated-snapshot overlap reconstruction for Tardis Coinbase raw L3."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lobcap.l3.book import L3Book
from lobcap.l3.schema import (
    LIFECYCLE_TYPES,
    SUPPORTED_TYPES,
    NativeMessage,
    iter_tardis_lines,
)


@dataclass(slots=True)
class SequenceTracker:
    last: int | None = None
    pairs: int = 0
    plus_one: int = 0
    gaps: int = 0
    duplicates: int = 0
    reversals: int = 0
    examples: list[dict[str, Any]] = field(default_factory=list)

    def observe(self, sequence: int, source_line: int | None) -> str | None:
        if self.last is None:
            self.last = sequence
            return None
        self.pairs += 1
        previous = self.last
        if sequence == previous + 1:
            self.plus_one += 1
            self.last = sequence
            return None
        if sequence == previous:
            kind = "duplicate"
            self.duplicates += 1
        elif sequence < previous:
            kind = "reversal"
            self.reversals += 1
        else:
            kind = "gap"
            self.gaps += 1
            self.last = sequence
        if len(self.examples) < 5:
            self.examples.append(
                {
                    "kind": kind,
                    "previous": previous,
                    "current": sequence,
                    "source_line": source_line,
                }
            )
        return kind

    def report(self) -> dict[str, Any]:
        return {
            "last_sequence": self.last,
            "comparable_pairs": self.pairs,
            "plus_one_pairs": self.plus_one,
            "gaps": self.gaps,
            "duplicates": self.duplicates,
            "reversals": self.reversals,
            "examples": self.examples,
        }


class L3Reconstructor:
    def __init__(
        self,
        product_id: str = "BTC-USD",
        *,
        allow_recovery_snapshots: bool = False,
    ) -> None:
        self.product_id = product_id
        self.allow_recovery_snapshots = allow_recovery_snapshots
        self.book = L3Book()
        self.capture_sequence = SequenceTracker()
        self.applied_sequence = SequenceTracker()
        self.buffer: list[NativeMessage] = []
        self.snapshot_loaded = False
        self.snapshot_sequence: int | None = None
        self.snapshot_local_timestamp: str | None = None
        self.valid = True
        self.errors: list[dict[str, Any]] = []
        self.observed_counts: Counter[str] = Counter()
        self.applied_counts: Counter[str] = Counter()
        self.buffered_events_replayed = 0
        self.buffered_events_discarded = 0
        self.first_applied_sequence: int | None = None
        self.received_pending: set[str] = set()
        self.applied_received_pending: set[str] = set()
        self.applied_received_never_opened_completed = 0
        self.unknown_done_received_only = 0
        self.truly_unknown_done = 0
        self.unknown_change_received_only = 0
        self.maker_match_counts: Counter[str] = Counter()
        self.maker_orders_with_multiple_matches = 0
        self.maximum_matches_for_one_maker_order = 0
        self.maker_orders_filled_after_match = 0
        self.active_filled_without_prior_maker_match = 0
        self.recovery_pending = False
        self.recovery_segments: list[dict[str, Any]] = []

    def _recovery_permitted(self, message: NativeMessage) -> bool:
        """Return whether this consumer can start a new acquisition segment."""

        return self.allow_recovery_snapshots

    def _start_recovery(self, message: NativeMessage, *, reason: str) -> None:
        self.recovery_pending = True
        self.buffer.clear()
        self.recovery_segments.append(
            {
                "reason": reason,
                "gap_source_line": message.source_line,
                "gap_sequence": message.sequence,
                "prior_applied_sequence": self.applied_sequence.last,
                "prior_book": self.book.summary(),
                "snapshot_sequence": None,
                "snapshot_local_timestamp": None,
                "buffered_events_replayed": 0,
                "buffered_events_discarded_at_or_before_snapshot": 0,
            }
        )

    def _load_recovery_snapshot(self, message: NativeMessage) -> None:
        if not self.recovery_segments or not self.recovery_pending:
            self._start_recovery(message, reason="generated_snapshot_without_sequence_gap")
        segment = self.recovery_segments[-1]
        self.book = L3Book()
        self.book.load_snapshot(message.payload)
        self.received_pending.clear()
        self.applied_received_pending.clear()
        self.maker_match_counts.clear()
        self.applied_sequence.last = message.sequence
        segment["snapshot_sequence"] = message.sequence
        segment["snapshot_local_timestamp"] = message.local_timestamp
        for buffered in sorted(self.buffer, key=lambda item: item.sequence):
            if buffered.sequence <= message.sequence:
                self.buffered_events_discarded += 1
                segment["buffered_events_discarded_at_or_before_snapshot"] += 1
                continue
            self._apply(buffered)
            self.buffered_events_replayed += 1
            segment["buffered_events_replayed"] += 1
        self.buffer.clear()
        self.recovery_pending = False

    def _invalidate(
        self, kind: str, message: NativeMessage, details: dict[str, Any] | None = None
    ) -> None:
        self.valid = False
        error = {
            "kind": kind,
            "source_line": message.source_line,
            "sequence": message.payload.get("sequence"),
        }
        if details:
            error.update(details)
        if len(self.errors) < 20:
            self.errors.append(error)

    def consume(self, message: NativeMessage) -> None:
        payload = message.payload
        message_type = message.type
        self.observed_counts[message_type] += 1
        if message_type not in SUPPORTED_TYPES:
            self._invalidate(
                "unsupported_message_type", message, {"message_type": message_type}
            )
            return
        if payload["product_id"] != self.product_id:
            self._invalidate(
                "unexpected_product",
                message,
                {"product_id": payload["product_id"]},
            )
            return

        order_id = payload.get("order_id")
        if message_type == "received" and order_id is not None:
            self.received_pending.add(str(order_id))
        elif message_type == "open" and order_id is not None:
            self.received_pending.discard(str(order_id))

        if message_type in LIFECYCLE_TYPES:
            anomaly = self.capture_sequence.observe(message.sequence, message.source_line)
            if anomaly:
                if (
                    anomaly == "gap"
                    and self.snapshot_loaded
                    and not self.recovery_pending
                    and self._recovery_permitted(message)
                ):
                    self._start_recovery(message, reason="capture_sequence_gap")
                else:
                    self._invalidate(f"capture_sequence_{anomaly}", message)
            if not self.snapshot_loaded or self.recovery_pending:
                self.buffer.append(message)
            else:
                self._apply(message)
            return

        if message_type == "full_snapshot":
            if self.snapshot_loaded:
                if self._recovery_permitted(message):
                    self._load_recovery_snapshot(message)
                else:
                    self._invalidate("duplicate_full_snapshot", message)
                return
            self.book.load_snapshot(payload)
            self.snapshot_loaded = True
            self.snapshot_sequence = message.sequence
            self.snapshot_local_timestamp = message.local_timestamp
            self.applied_sequence.last = message.sequence
            for buffered in sorted(self.buffer, key=lambda item: item.sequence):
                if buffered.sequence <= message.sequence:
                    self.buffered_events_discarded += 1
                    continue
                self._apply(buffered)
                self.buffered_events_replayed += 1
            self.buffer.clear()

    def _apply(self, message: NativeMessage) -> None:
        if not self.valid:
            return
        anomaly = self.applied_sequence.observe(message.sequence, message.source_line)
        if anomaly:
            self._invalidate(f"applied_sequence_{anomaly}", message)
            return
        if self.first_applied_sequence is None:
            self.first_applied_sequence = message.sequence

        payload = message.payload
        message_type = message.type
        if message_type == "received":
            self.applied_received_pending.add(str(payload["order_id"]))
            self.applied_counts["received_non_book"] += 1
            applied = None
        elif message_type == "open":
            self.applied_received_pending.discard(str(payload["order_id"]))
            applied = self.book.apply_open(payload)
            if not applied and self.book.audit["duplicate_open"]:
                self._invalidate("duplicate_open", message)
        elif message_type == "match":
            maker_id = str(payload["maker_order_id"])
            self.maker_match_counts[maker_id] += 1
            count = self.maker_match_counts[maker_id]
            if count == 2:
                self.maker_orders_with_multiple_matches += 1
            self.maximum_matches_for_one_maker_order = max(
                self.maximum_matches_for_one_maker_order, count
            )
            applied = self.book.apply_match(payload)
        elif message_type == "done":
            reason = str(payload["reason"])
            self.applied_counts[f"done_{reason}_observed"] += 1
            order_id = str(payload["order_id"])
            was_active = order_id in self.book.orders
            was_taker_reconciliation = order_id in self.book.pending_taker_match_size
            applied = self.book.apply_done(payload)
            if applied:
                self.applied_counts[f"done_{reason}_book_applied"] += 1
                if reason == "filled":
                    if self.maker_match_counts[str(payload["order_id"])] > 0:
                        self.maker_orders_filled_after_match += 1
                    elif was_taker_reconciliation:
                        self.applied_counts["done_filled_after_taker_match"] += 1
                    elif was_active:
                        self.active_filled_without_prior_maker_match += 1
            if not applied:
                if order_id in self.received_pending:
                    self.unknown_done_received_only += 1
                else:
                    self.truly_unknown_done += 1
            if order_id in self.applied_received_pending:
                self.applied_received_never_opened_completed += 1
            self.received_pending.discard(order_id)
            self.applied_received_pending.discard(order_id)
            self.maker_match_counts.pop(order_id, None)
        elif message_type == "change":
            applied = self.book.apply_change(payload)
            if not applied:
                order_id = str(payload["order_id"])
                if order_id in self.received_pending:
                    self.unknown_change_received_only += 1
                elif self.book.audit["change_old_state_mismatch"]:
                    self._invalidate("change_old_state_mismatch", message)
        else:  # guarded by schema, retained as an explicit failure path
            self._invalidate("unsupported_lifecycle_type", message)
            return

        self.applied_counts[message_type] += 1
        if applied is True:
            self.applied_counts[f"{message_type}_book_applied"] += 1
        elif applied is False:
            self.applied_counts[f"{message_type}_book_ignored"] += 1

    def finish(self) -> dict[str, Any]:
        if self.recovery_pending:
            self.valid = False
            self.errors.append({"kind": "recovery_snapshot_missing"})
        if not self.snapshot_loaded:
            self.valid = False
            self.errors.append({"kind": "missing_full_snapshot"})

        severe_book_audits = (
            "duplicate_snapshot_order",
            "negative_snapshot_size",
            "negative_open_size",
            "negative_remaining_size",
            "maker_identity_mismatch",
            "done_remaining_mismatch",
            "filled_done_nonzero",
            "change_old_state_mismatch",
            "negative_change_size",
            "negative_taker_reconciled_remaining",
            "uncertain_change_reason",
        )
        for name in severe_book_audits:
            if self.book.audit[name]:
                self.valid = False
        if self.book.audit["unknown_maker_match"]:
            self.valid = False
        if self.book.pending_taker_match_size:
            self.valid = False
            self.errors.append(
                {
                    "kind": "pending_taker_reconciliation",
                    "orders": len(self.book.pending_taker_match_size),
                    "quantity": str(sum(self.book.pending_taker_match_size.values())),
                }
            )
        if self.book.crossed():
            self.valid = False

        book_summary = self.book.summary()
        if any(book_summary["internal_consistency"].values()):
            self.valid = False
        received_but_never_opened = (
            self.applied_received_never_opened_completed
            + len(self.applied_received_pending)
        )
        report = {
            "valid": self.valid,
            "product_id": self.product_id,
            "snapshot": {
                "loaded": self.snapshot_loaded,
                "sequence": self.snapshot_sequence,
                "local_timestamp": self.snapshot_local_timestamp,
                "buffered_events_replayed": self.buffered_events_replayed,
                "buffered_events_discarded_at_or_before_snapshot": self.buffered_events_discarded,
            },
            "sequence": {
                "capture_order": self.capture_sequence.report(),
                "applied_after_snapshot": self.applied_sequence.report(),
                "first_applied_lifecycle_sequence": self.first_applied_sequence,
            },
            "events": {
                "observed": dict(sorted(self.observed_counts.items())),
                "applied": dict(sorted(self.applied_counts.items())),
                "received_but_never_opened": received_but_never_opened,
                "unknown_done_orders": self.book.audit["unknown_done_order"],
                "unknown_done_received_never_opened": self.unknown_done_received_only,
                "unknown_done_boundary_or_unclassified": self.truly_unknown_done,
                "unknown_change_received_never_opened": self.unknown_change_received_only,
                "maker_orders_with_multiple_matches": self.maker_orders_with_multiple_matches,
                "maximum_matches_for_one_maker_order": self.maximum_matches_for_one_maker_order,
                "maker_orders_filled_after_match": self.maker_orders_filled_after_match,
                "active_filled_without_prior_maker_match": self.active_filled_without_prior_maker_match,
            },
            "book": book_summary,
            "protocol": {
                "snapshot_internal_fifo_assumed": False,
                "post_snapshot_resting_set_observable": True,
                "modify_order_priority": {
                    "same_price_size_decrease": "retained",
                    "size_increase_or_price_change": "lost_and_reinserted_at_tail",
                },
                "uncertainties": [
                    "FIFO among same-price orders already in the generated snapshot is unknown.",
                    "STP change is documented but was not present in the runtime sample and is not applied.",
                ],
            },
            "errors": self.errors,
        }
        if self.allow_recovery_snapshots:
            report["acquisition_segments"] = {
                "count": 1 + len(self.recovery_segments),
                "recovered_boundaries": self.recovery_segments,
            }
        return report


def reconstruct_file(input_path: str | Path) -> dict[str, Any]:
    reconstructor = L3Reconstructor()
    for message in iter_tardis_lines(input_path):
        reconstructor.consume(message)
    return reconstructor.finish()


def write_reports(report: dict[str, Any], output_dir: str | Path) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    event_counts = report["events"]
    invariants = {
        "valid": report["valid"],
        "sequence": report["sequence"],
        "negative_remaining_size": report["book"]["audit_counts"].get(
            "negative_remaining_size", 0
        ),
        "unknown_maker_matches": report["book"]["audit_counts"].get(
            "unknown_maker_match", 0
        ),
        "unknown_done_orders": report["events"]["unknown_done_orders"],
        "duplicate_opens": report["book"]["audit_counts"].get("duplicate_open", 0),
        "crossed_book": report["book"]["crossed_book"],
        "internal_consistency": report["book"]["internal_consistency"],
        "book_audit_counts": report["book"]["audit_counts"],
        "book_audit_examples": report["book"]["audit_examples"],
        "errors": report["errors"],
    }
    files = {
        "reconstruction_report.json": report,
        "event_counts.json": event_counts,
        "invariant_report.json": invariants,
    }
    for name, content in files.items():
        (output / name).write_text(
            json.dumps(content, indent=2) + "\n", encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = reconstruct_file(args.input)
    write_reports(report, args.out)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
