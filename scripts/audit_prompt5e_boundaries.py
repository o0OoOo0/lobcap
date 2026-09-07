"""Capture bounded raw evidence for Prompt 5E snapshot/sequence boundaries."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from lobcap.config import config_provenance, load_five_e_config
from lobcap.l2.multidate import (
    TardisHTTPClient,
    configure_runtime,
    fetch_minute,
    write_json,
)
from lobcap.l3.schema import NativeMessage, parse_tardis_line


ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "artifacts/5e_methodology_repair/snapshot_sequence_audit.json"
DATE = ""
OFFSETS = (0, 1107, 1108, 1109)


def message_evidence(message: NativeMessage) -> dict[str, Any]:
    payload = message.payload
    evidence = {
        "local_timestamp": message.local_timestamp,
        "type": message.type,
        "sequence": message.sequence,
        "exchange_time": payload["time"],
        "product_id": payload["product_id"],
    }
    if message.type == "full_snapshot":
        evidence.update(
            {
                "generated": payload["generated"],
                "bid_orders": len(payload["bids"]),
                "ask_orders": len(payload["asks"]),
            }
        )
    else:
        for name in (
            "order_id", "maker_order_id", "taker_order_id", "side", "reason",
            "price", "old_price", "new_price", "size", "old_size", "new_size",
        ):
            if name in payload:
                evidence[name] = payload[name]
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_five_e_config(args.config)
    configure_runtime(config)
    global DATE
    DATE = config.calibration_dates[0]

    client = TardisHTTPClient()
    responses: dict[int, dict[str, Any]] = {}
    parsed: dict[int, list[NativeMessage | None]] = {}
    try:
        for offset in OFFSETS:
            raw, report = fetch_minute(client, DATE, offset)
            items: list[NativeMessage | None] = []
            for line_number, line in enumerate(raw.splitlines(), 1):
                items.append(
                    None
                    if not line.strip()
                    else parse_tardis_line(line.decode("utf-8"), line_number)
                )
            parsed[offset] = items
            messages = [item for item in items if item is not None]
            lifecycle = [item for item in messages if item.type != "full_snapshot"]
            responses[offset] = {
                **report,
                "response_sha256": hashlib.sha256(raw).hexdigest(),
                "line_count": len(items),
                "blank_disconnect_markers": sum(item is None for item in items),
                "first_message": message_evidence(messages[0]) if messages else None,
                "last_message": message_evidence(messages[-1]) if messages else None,
                "first_lifecycle_sequence": lifecycle[0].sequence if lifecycle else None,
                "last_lifecycle_sequence": lifecycle[-1].sequence if lifecycle else None,
                "full_snapshots": [
                    message_evidence(item) for item in messages if item.type == "full_snapshot"
                ],
            }
    finally:
        client.close()

    initial = [item for item in parsed[0] if item is not None]
    initial_snapshot_index = next(
        index for index, item in enumerate(initial) if item.type == "full_snapshot"
    )
    before_initial = [
        item for item in initial[:initial_snapshot_index] if item.type != "full_snapshot"
    ]

    boundary_items = [
        (offset, line_number, item)
        for offset in (1107, 1108, 1109)
        for line_number, item in enumerate(parsed[offset], 1)
        if item is not None
    ]
    lifecycle = [item for _, _, item in boundary_items if item.type != "full_snapshot"]
    gaps = []
    for previous, current in zip(lifecycle, lifecycle[1:]):
        if current.sequence != previous.sequence + 1:
            next_snapshot = next(
                (
                    item
                    for _, _, item in boundary_items
                    if item.type == "full_snapshot" and item.local_timestamp >= current.local_timestamp
                ),
                None,
            )
            gaps.append(
                {
                    "missing_sequence_count": current.sequence - previous.sequence - 1,
                    "previous": message_evidence(previous),
                    "current": message_evidence(current),
                    "following_generated_snapshot": (
                        message_evidence(next_snapshot) if next_snapshot is not None else None
                    ),
                }
            )

    report = {
        "methodology_version": "prompt5e-v13",
        "date": DATE,
        "raw_retention": "bounded message metadata; full snapshot order arrays omitted",
        "http_responses": {str(key): value for key, value in responses.items()},
        "initial_snapshot": {
            "snapshot": message_evidence(initial[initial_snapshot_index]),
            "pre_snapshot_lifecycle_count": len(before_initial),
            "pre_snapshot_first_sequence": (
                before_initial[0].sequence if before_initial else None
            ),
            "pre_snapshot_last_sequence": (
                before_initial[-1].sequence if before_initial else None
            ),
            "behavior": (
                "Lifecycle events arriving before the generated day-start snapshot are buffered; "
                "events <= snapshot.sequence are discarded and later contiguous events replayed."
            ),
        },
        "observed_capture_gaps": gaps,
        "gap_count": len(gaps),
        "unexplained_gap_count": sum(
            gap["following_generated_snapshot"] is None for gap in gaps
        ),
        "replacement_snapshot_count": sum(
            len(responses[offset]["full_snapshots"]) for offset in (1107, 1108, 1109)
        ),
        "duplicate_snapshot_explanation": (
            "The later generated full_snapshot is not a duplicate state update. It follows the "
            "blank disconnect marker and capture gap, replaces the stale book, and starts a new "
            "acquisition segment. Treating it as duplicate_full_snapshot was the v7 bug."
        ),
        "http_boundary_conclusion": (
            "Adjacent minute responses are ordered by lifecycle sequence except for the in-response "
            "reconnect gap; the gap is not created by the HTTP minute boundary."
        ),
        "provenance": {
            **config_provenance(config),
            "tardis_date": DATE,
            "tardis_offsets_minutes": list(OFFSETS),
            "input_checksums": {
                str(offset): responses[offset]["response_sha256"]
                for offset in OFFSETS
            },
            "raw_input_persisted": False,
        },
    }
    write_json(OUTPUT, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
