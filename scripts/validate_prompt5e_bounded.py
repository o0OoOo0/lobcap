"""Bounded Prompt 5E gates; deliberately cannot launch the 12-date run."""

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from lobcap.l2.multidate import (
    MultiDateReconstructor,
    TardisHTTPClient,
    aggregate_alpha,
    fetch_minute,
    model_specs,
    process_date,
    stable_json,
    structural_rows,
    validate_result,
    write_json,
    write_json_gz,
)
from lobcap.l3.schema import parse_tardis_line


ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "artifacts/multidate_calibration/bounded"
DATE = "2025-10-01"


def fetch_offsets_ordered(offsets: range):
    clients = [TardisHTTPClient() for _ in range(8)]
    values = list(offsets)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            for start in range(0, len(values), 8):
                batch = values[start : start + 8]
                futures = [
                    executor.submit(fetch_minute, clients[index], DATE, offset)
                    for index, offset in enumerate(batch)
                ]
                for offset, future in zip(batch, futures, strict=True):
                    raw, report = future.result()
                    yield offset, raw, report
    finally:
        for client in clients:
            client.close()


def decimal_sum(rows: list[dict[str, Any]], name: str) -> Decimal:
    return sum((Decimal(str(row[name])) for row in rows), Decimal("0"))


def summarize_window(window: dict[str, Any], reconstruction: dict[str, Any]) -> dict[str, Any]:
    rows = window["structural_rows"]
    legacy_cases = []
    caused_hidden = caused_bug = other = 0
    for row in rows:
        raw = Decimal(str(row["A_raw_queue_depletion"]))
        visible = Decimal(str(row["A_visible"]))
        c_value = Decimal(str(row["C"]))
        hidden = Decimal(str(row["H_hidden"]))
        if raw <= c_value:
            continue
        if visible > c_value:
            cause = "implementation_or_alignment_bug"
            caused_bug += 1
        elif hidden > 0 and raw == visible + hidden:
            cause = "hidden_priority_loss"
            caused_hidden += 1
        else:
            cause = "other_unexplained"
            other += 1
        virtual_id = str(row["virtual_order_id"])
        component_events = [
            event
            for event in window["structural_events"].get(virtual_id, [])
            if any(
                Decimal(str(event[name])) > 0
                for name in (
                    "ahead_cancellation",
                    "ahead_size_decrease",
                    "ahead_price_change_away",
                    "ahead_same_price_size_increase_priority_loss",
                    "other_priority_loss",
                )
            )
        ]
        legacy_cases.append({**row, "cause": cause, "event_decomposition": component_events})

    hidden_events = {
        (
            int(event["sequence"]),
            str(event["side"]),
            str(event["price"]),
        )
        for events in window["h_events"].values()
        for event in events
        if Decimal(str(event["H_hidden"])) > 0
    }
    total_visible = decimal_sum(rows, "A_visible")
    total_c = decimal_sum(rows, "C")
    total_hidden = decimal_sum(rows, "H_hidden")
    total_l3 = decimal_sum(rows, "total_observed_L3_ahead_reduction")
    return {
        "methodology_version": "prompt5e-v13",
        "date": DATE,
        "window": window["window"],
        "placements": len(rows),
        "original_A_gt_C_cases": len(legacy_cases),
        "caused_by_hidden_priority_loss": caused_hidden,
        "caused_by_implementation_or_alignment_bug": caused_bug,
        "other_unexplained": other,
        "original_case_decomposition": legacy_cases,
        "revised": {
            "A_visible": str(total_visible),
            "C": str(total_c),
            "A_visible_gt_C_event_violations": int(
                window["diagnostics"]["projection_invariants"].get(
                    "A_visible_gt_C_event", 0
                )
            ),
            "A_visible_gt_C_window_violations": sum(
                Decimal(str(row["A_visible"])) > Decimal(str(row["C"]))
                for row in rows
            ),
            "H_hidden": str(total_hidden),
            "total_observed_L3_ahead_reduction": str(total_l3),
            "hidden_priority_loss_event_count_unique": len(hidden_events),
            "hidden_priority_loss_virtual_order_event_count": sum(
                len(events) for events in window["h_events"].values()
            ),
            "hidden_priority_loss_share": (
                str(total_hidden / total_l3) if total_l3 else None
            ),
            "alpha_visible": str(total_visible / total_c) if total_c else None,
        },
        "invariants": {
            "reconstruction_valid": bool(reconstruction["valid"]),
            "sequence": reconstruction["sequence"],
            "acquisition_segments": reconstruction.get("acquisition_segments"),
            "reconstruction_errors": reconstruction["errors"],
            "book": reconstruction["book"],
            "events": reconstruction["events"],
            "virtual": window["diagnostics"]["virtual_invariants"],
            "fill_model": window["diagnostics"]["fill_model_invariants"],
            "projection": window["diagnostics"]["projection_invariants"],
            "structural_violation_examples": window["diagnostics"][
                "structural_violation_examples"
            ],
        },
    }


def one_window(label: str) -> None:
    reconstructor = MultiDateReconstructor()
    reconstructor.start_window(DATE, 0, model_specs(None))
    client = TardisHTTPClient()
    source_line = 0
    digest = hashlib.sha256()
    reports = []
    try:
        for offset in range(10):
            raw, report = fetch_minute(client, DATE, offset)
            digest.update(offset.to_bytes(2, "big"))
            digest.update(raw)
            messages = 0
            for line in raw.splitlines():
                if not line.strip():
                    continue
                source_line += 1
                reconstructor.consume(parse_tardis_line(line.decode("utf-8"), source_line))
                messages += 1
            reports.append({"offset": offset, "messages": messages, **report})
    finally:
        client.close()
    window = reconstructor.finish_window()
    reconstruction = reconstructor.finish()
    summary = summarize_window(window, reconstruction)
    deterministic_payload = {
        "response_sha256": digest.hexdigest(),
        "truth": window["truth"],
        "predictions": window["predictions"],
        "structural_rows": window["structural_rows"],
        "structural_events": window["structural_events"],
        "queue_ledger": window["queue_ledger"],
        "reconstruction": reconstruction,
    }
    summary["response_sha256"] = digest.hexdigest()
    summary["deterministic_sha256"] = hashlib.sha256(
        stable_json(deterministic_payload).encode("utf-8")
    ).hexdigest()
    summary["download"] = {
        "minute_responses": len(reports),
        "messages": sum(item["messages"] for item in reports),
        "retries": sum(item["attempts"] - 1 for item in reports),
    }
    write_json(OUTPUT / f"one_window_{label}.json", summary)
    print(json.dumps(summary, indent=2))


def recovery_segment() -> None:
    """Validate the new book segment independently from its generated snapshot."""
    reconstructor = MultiDateReconstructor()
    source_line = 0
    snapshot_found = False
    segment_started = False
    first_segment_sequence = 112615562913
    severe_book_audits = {
        "duplicate_snapshot_order", "negative_snapshot_size", "negative_open_size",
        "negative_remaining_size", "maker_identity_mismatch", "done_remaining_mismatch",
        "filled_done_nonzero", "change_old_state_mismatch", "negative_change_size",
        "uncertain_change_reason", "unknown_maker_match",
    }
    for offset, raw, _ in fetch_offsets_ordered(range(1108, 1210)):
        if offset == 1200:
            reconstructor.start_window(DATE, offset, model_specs(None))
        for line in raw.splitlines():
            if not line.strip():
                continue
            source_line += 1
            message = parse_tardis_line(line.decode("utf-8"), source_line)
            if not segment_started:
                if message.type == "full_snapshot" or message.sequence < first_segment_sequence:
                    continue
                segment_started = True
            if message.type == "full_snapshot":
                snapshot_found = True
            reconstructor.consume(message)
            severe_observed = {
                name: count
                for name, count in reconstructor.book.audit.items()
                if name in severe_book_audits and count
            }
            if not reconstructor.valid or severe_observed:
                report = reconstructor.finish()
                failure = {
                    "methodology_version": "prompt5e-v13",
                    "gate": "FAIL",
                    "failed_offset": offset,
                    "failed_source_line": source_line,
                    "failed_message": {
                        "type": message.type,
                        "sequence": message.sequence,
                        "time": message.payload["time"],
                    },
                    "snapshot_found": snapshot_found,
                    "segment_started": segment_started,
                    "first_segment_sequence": first_segment_sequence,
                    "severe_book_audits": severe_observed,
                    "book_audit_examples": reconstructor.book.audit_examples,
                    "reconstruction": report,
                }
                write_json(OUTPUT / "recovery_segment_18-28_to_20-10.json", failure)
                print(json.dumps(failure, indent=2))
                return
    window = reconstructor.finish_window()
    reconstruction = reconstructor.finish()
    summary = summarize_window(window, reconstruction)
    summary["snapshot_found"] = snapshot_found
    summary["gate"] = (
        "PASS"
        if snapshot_found
        and reconstruction["valid"]
        and summary["revised"]["A_visible_gt_C_event_violations"] == 0
        and summary["revised"]["A_visible_gt_C_window_violations"] == 0
        else "FAIL"
    )
    write_json(OUTPUT / "recovery_segment_18-28_to_20-10.json", summary)
    print(json.dumps(summary, indent=2))


def six_window() -> None:
    result = process_date(DATE, None)
    failures = validate_result(result)
    rows = structural_rows([result])
    original_cases = [
        row
        for row in rows
        if Decimal(str(row["A_raw_queue_depletion"])) > Decimal(str(row["C"]))
    ]
    hidden_caused = [
        row
        for row in original_cases
        if Decimal(str(row["A_visible"])) <= Decimal(str(row["C"]))
        and Decimal(str(row["H_hidden"])) > 0
        and Decimal(str(row["A_raw_queue_depletion"]))
        == Decimal(str(row["A_visible"])) + Decimal(str(row["H_hidden"]))
    ]
    implementation_cases = [
        row
        for row in original_cases
        if Decimal(str(row["A_visible"])) > Decimal(str(row["C"]))
    ]
    hidden_events = {
        (window["window"], int(event["sequence"]), event["side"], event["price"])
        for window in result["windows"]
        for events in window["h_events"].values()
        for event in events
        if Decimal(str(event["H_hidden"])) > 0
    }
    summary = {
        "methodology_version": "prompt5e-v13",
        "date": DATE,
        "windows": len(result["windows"]),
        "placements": len(rows),
        "aggregate": aggregate_alpha(rows),
        "original_A_gt_C_cases": len(original_cases),
        "caused_by_hidden_priority_loss": len(hidden_caused),
        "caused_by_implementation_or_alignment_bug": len(implementation_cases),
        "other_unexplained": (
            len(original_cases) - len(hidden_caused) - len(implementation_cases)
        ),
        "hidden_priority_loss_event_count_unique": len(hidden_events),
        "per_window": [
            {
                "window": window["window"],
                **aggregate_alpha(window["structural_rows"]),
            }
            for window in result["windows"]
        ],
        "sequence": result["reconstruction"]["sequence"],
        "acquisition_segments": result["reconstruction"].get("acquisition_segments"),
        "reconstruction_valid": result["reconstruction"]["valid"],
        "reconstruction_errors": result["reconstruction"]["errors"],
        "book": result["reconstruction"]["book"],
        "events": result["reconstruction"]["events"],
        "validation_failures": failures,
        "result": "PASS" if not failures else "FAIL",
    }
    write_json_gz(OUTPUT / f"six_window_gate_{DATE}.json.gz", result)
    write_json(OUTPUT / f"six_window_gate_{DATE}_summary.json", summary)
    print(json.dumps(summary, indent=2))


def postprocess_v13() -> None:
    """Correct only the derived hidden-share denominator in retained v12 data."""
    path = OUTPUT / f"six_window_gate_{DATE}.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        result = json.load(handle)
    if result.get("methodology_version") != "prompt5e-v12":
        raise ValueError("postprocess-v13 requires the accepted v12 bounded artifact")
    for window in result["windows"]:
        truth_by_id = {
            str(row["virtual_order_id"]): row for row in window["truth"]
        }
        for row in window["structural_rows"]:
            truth = truth_by_id[str(row["virtual_order_id"])]
            trade = Decimal(str(truth["ahead_trade_depletion"]))
            raw = Decimal(str(row["A_raw_queue_depletion"]))
            hidden = Decimal(str(row["H_hidden"]))
            total = raw + trade
            row["ahead_trade_depletion"] = str(trade)
            row["total_observed_L3_ahead_reduction"] = str(total)
            row["hidden_priority_loss_share"] = (
                str(hidden / total) if total else None
            )
    result["methodology_version"] = "prompt5e-v13"
    failures = validate_result(result)
    if failures:
        raise ValueError(f"v13 postprocess invalidated bounded gate: {failures}")
    rows = structural_rows([result])
    original_cases = [
        row for row in rows
        if Decimal(str(row["A_raw_queue_depletion"])) > Decimal(str(row["C"]))
    ]
    hidden_caused = [
        row for row in original_cases
        if Decimal(str(row["A_visible"])) <= Decimal(str(row["C"]))
        and Decimal(str(row["H_hidden"])) > 0
        and Decimal(str(row["A_raw_queue_depletion"]))
        == Decimal(str(row["A_visible"])) + Decimal(str(row["H_hidden"]))
    ]
    implementation_cases = [
        row for row in original_cases
        if Decimal(str(row["A_visible"])) > Decimal(str(row["C"]))
    ]
    hidden_events = {
        (window["window"], int(event["sequence"]), event["side"], event["price"])
        for window in result["windows"]
        for events in window["h_events"].values()
        for event in events
        if Decimal(str(event["H_hidden"])) > 0
    }
    summary = {
        "methodology_version": "prompt5e-v13",
        "date": DATE,
        "windows": len(result["windows"]),
        "placements": len(rows),
        "aggregate": aggregate_alpha(rows),
        "original_A_gt_C_cases": len(original_cases),
        "caused_by_hidden_priority_loss": len(hidden_caused),
        "caused_by_implementation_or_alignment_bug": len(implementation_cases),
        "other_unexplained": len(original_cases) - len(hidden_caused) - len(implementation_cases),
        "hidden_priority_loss_event_count_unique": len(hidden_events),
        "per_window": [
            {"window": window["window"], **aggregate_alpha(window["structural_rows"])}
            for window in result["windows"]
        ],
        "sequence": result["reconstruction"]["sequence"],
        "acquisition_segments": result["reconstruction"].get("acquisition_segments"),
        "reconstruction_valid": result["reconstruction"]["valid"],
        "reconstruction_errors": result["reconstruction"]["errors"],
        "book": result["reconstruction"]["book"],
        "events": result["reconstruction"]["events"],
        "validation_failures": failures,
        "result": "PASS",
        "postprocessed_from": "prompt5e-v12 retained bounded result; no replay",
    }
    write_json_gz(path, result)
    write_json(OUTPUT / f"six_window_gate_{DATE}_summary.json", summary)
    print(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=("one-window", "recovery-segment", "six-window", "postprocess-v13")
    )
    parser.add_argument("--label", default="run1")
    args = parser.parse_args()
    if args.mode == "one-window":
        one_window(args.label)
    elif args.mode == "recovery-segment":
        recovery_segment()
    elif args.mode == "postprocess-v13":
        postprocess_v13()
    else:
        six_window()


if __name__ == "__main__":
    main()
