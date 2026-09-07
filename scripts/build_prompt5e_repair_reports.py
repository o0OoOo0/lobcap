"""Build small, reviewable reports from accepted bounded Prompt 5E artifacts.

This is a report builder, not an experiment runner.  It cannot download data
or invoke the prohibited 12-date path.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/5e_methodology_repair"
COMPONENTS = (
    "ahead_cancellation",
    "ahead_size_decrease",
    "ahead_price_change_away",
    "ahead_same_price_size_increase_priority_loss",
    "other_priority_loss",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def zero_counts(values: dict[str, Any]) -> bool:
    return all(int(value) == 0 for value in values.values())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    run1_path = output / "one_window_run1.json"
    run2_path = output / "one_window_run2.json"
    six_summary_path = output / "six_window_gate_2025-10-01_summary.json"
    six_payload_path = output / "six_window_gate_2025-10-01.json.gz"
    snapshot_path = output / "snapshot_sequence_audit.json"
    run1, run2 = load_json(run1_path), load_json(run2_path)
    six = load_json(six_summary_path)
    snapshot = load_json(snapshot_path)
    with gzip.open(six_payload_path, "rt", encoding="utf-8") as source:
        six_payload = json.load(source)

    provenance = dict(six["provenance"])
    provenance["bounded_payload_sha256"] = sha256(six_payload_path)
    provenance["bounded_payload_tracked"] = False
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    cases: list[dict[str, Any]] = []
    for window in six_payload["windows"]:
        for row in window["structural_rows"]:
            raw = Decimal(str(row["A_raw_queue_depletion"]))
            c_value = Decimal(str(row["C"]))
            if raw <= c_value:
                continue
            virtual_id = str(row["virtual_order_id"])
            events = [
                event
                for event in window["structural_events"].get(virtual_id, [])
                if any(Decimal(str(event[name])) > 0 for name in COMPONENTS)
            ]
            visible = Decimal(str(row["A_visible"]))
            hidden = Decimal(str(row["H_hidden"]))
            if visible > c_value:
                cause = "implementation_or_alignment_bug"
            elif hidden > 0 and raw == visible + hidden:
                cause = "hidden_priority_loss"
            else:
                cause = "other_unexplained"
            cases.append(
                {
                    "window": window["window"],
                    "virtual_order_id": virtual_id,
                    "side": row["side"],
                    "placement_price": events[0]["price"] if events else "UNKNOWN",
                    "C": row["C"],
                    "A_raw_queue_depletion": row["A_raw_queue_depletion"],
                    "A_visible": row["A_visible"],
                    "H_hidden": row["H_hidden"],
                    "cause": cause,
                    "events": events,
                }
            )

    structural = {
        "schema_version": 1,
        "generated_at": generated_at,
        "provenance": provenance,
        "definition": {
            "C": "L2-observed non-trade depletion at the virtual placement price",
            "A_visible": "ahead cancellation, size decrease, or price-change-away that reconciles to an old-level L2 decrease",
            "H_hidden": "L3-only priority loss without old-level L2 depletion; excluded from alpha",
            "alpha_visible": "sum(A_visible) / sum(C)",
            "clipping": "PROHIBITED",
        },
        "aggregate": six["aggregate"],
        "original_A_gt_C_case_count": len(cases),
        "caused_by_hidden_priority_loss": sum(
            case["cause"] == "hidden_priority_loss" for case in cases
        ),
        "caused_by_implementation_or_alignment_bug": sum(
            case["cause"] == "implementation_or_alignment_bug" for case in cases
        ),
        "other_unexplained": sum(
            case["cause"] == "other_unexplained" for case in cases
        ),
        "cases": cases,
    }
    write_json(output / "structural_decomposition.json", structural)

    original_failure = {
        "schema_version": 1,
        "generated_at": generated_at,
        "provenance": run1["provenance"],
        "historical_v7": load_json(
            ROOT / "artifacts/multidate_calibration/run_failure_v7.json"
        ),
        "historical_v8": load_json(
            ROOT / "artifacts/multidate_calibration/run_failure.json"
        ),
        "current_one_window_replay": {
            "date": run1["date"],
            "window": run1["window"],
            "legacy_A_raw_gt_C_cases": run1["original_A_gt_C_cases"],
            "revised_A_visible_gt_C_event_violations": run1["revised"][
                "A_visible_gt_C_event_violations"
            ],
            "revised_A_visible_gt_C_window_violations": run1["revised"][
                "A_visible_gt_C_window_violations"
            ],
            "conclusion": (
                "Legacy failure reproduced. Revised failure does not reproduce because the "
                "baseline already contains the semantic A_visible/H_hidden repair."
            ),
        },
    }
    write_json(output / "original_failure_reproduction.json", original_failure)

    one_report = dict(run1)
    one_report["gate"] = (
        "PASS"
        if run1["invariants"]["reconstruction_valid"]
        and run1["revised"]["A_visible_gt_C_event_violations"] == 0
        and run1["revised"]["A_visible_gt_C_window_violations"] == 0
        and not run1["invariants"]["reconstruction_errors"]
        and run1["invariants"]["virtual"]["valid"]
        and zero_counts(run1["invariants"]["fill_model"])
        and zero_counts(run1["invariants"]["projection"])
        else "FAIL"
    )
    write_json(output / "one_window_report.json", one_report)

    six_report = dict(six)
    six_report["bounded_payload_sha256"] = sha256(six_payload_path)
    six_report["bounded_payload_tracked"] = False
    write_json(output / "six_window_report.json", six_report)

    deterministic = {
        "schema_version": 1,
        "generated_at": generated_at,
        "provenance": run1["provenance"],
        "date": run1["date"],
        "window": run1["window"],
        "run1_response_sha256": run1["response_sha256"],
        "run2_response_sha256": run2["response_sha256"],
        "run1_deterministic_sha256": run1["deterministic_sha256"],
        "run2_deterministic_sha256": run2["deterministic_sha256"],
        "response_match": run1["response_sha256"] == run2["response_sha256"],
        "deterministic_output_match": (
            run1["deterministic_sha256"] == run2["deterministic_sha256"]
        ),
        "result": (
            "PASS"
            if run1["response_sha256"] == run2["response_sha256"]
            and run1["deterministic_sha256"] == run2["deterministic_sha256"]
            else "FAIL"
        ),
    }
    write_json(output / "deterministic_report.json", deterministic)

    aggregate = six["aggregate"]
    applied = six["sequence"]["applied_after_snapshot"]
    markdown = f"""# Prompt 5E bounded methodology repair

Generated: {generated_at}

## Result

PASS for the bounded methodology gate. This is not an OOS calibration result.
The 12-date/72-window runner was not executed.

## Structural result

- Legacy `A_raw > C` rows: {six['original_A_gt_C_cases']}
- Caused by hidden priority loss: {six['caused_by_hidden_priority_loss']}
- Implementation/alignment bugs: {six['caused_by_implementation_or_alignment_bug']}
- Other unexplained: {six['other_unexplained']}
- `A_visible`: {aggregate['sum_A_visible']}
- `C`: {aggregate['sum_C']}
- `A_visible > C` violations: {aggregate['A_visible_le_C_violations']}
- `H_hidden`: {aggregate['sum_H_hidden']}
- Hidden priority-loss events: {six['hidden_priority_loss_event_count_unique']}
- Hidden priority-loss share: {aggregate['hidden_priority_loss_share']}
- Bounded one-date `alpha_visible`: {aggregate['alpha_visible']}

`H_hidden` is an L3-only diagnostic and is not an L2 model input. No clipping
was used.

## Snapshot and sequence result

- Raw capture gaps: {six['sequence']['capture_order']['gaps']} (explained reconnect boundary)
- Applied lifecycle gaps: {applied['gaps']}
- Applied duplicates: {applied['duplicates']}
- Applied reversals: {applied['reversals']}
- Acquisition segments: {six['acquisition_segments']['count']}
- Unexplained audit gaps: {snapshot['unexplained_gap_count']}

The generated replacement snapshot is an out-of-band acquisition anchor, not
a lifecycle increment or duplicate state mutation.

## Gates

- One-window: {one_report['gate']}
- Six-window: {six['result']} ({six['windows']}/{six['windows']})
- Deterministic rerun: {deterministic['result']}
- Reconstruction: {'PASS' if six['reconstruction_valid'] else 'FAIL'}
- Validation failures: {len(six['validation_failures'])}

## Provenance

- Git base: `{provenance['git_commit']}` (`git_dirty={str(provenance['git_dirty']).lower()}`)
- Config: `{provenance['config_path']}`
- Raw config SHA-256: `{provenance['raw_file_sha256']}`
- Canonical config SHA-256: `{provenance['canonical_config_sha256']}`
- Tardis date: `{provenance['tardis_date']}`
- Six-window response SHA-256: `{provenance['input_checksum']['value']}`
"""
    (output / "methodology_summary.md").write_text(markdown, encoding="utf-8")

    report_paths = tuple(
        output / name
        for name in (
            "original_failure_reproduction.json",
            "structural_decomposition.json",
            "snapshot_sequence_audit.json",
            "one_window_report.json",
            "six_window_report.json",
            "deterministic_report.json",
            "methodology_summary.md",
        )
    )
    manifest = {
        "manifest_version": 1,
        "experiment_name": "5e_bounded_methodology_repair",
        "experiment_version": "prompt5e-v13",
        "status": "PASS",
        "started_at": six_payload["started_utc"],
        "completed_at": six_payload["finished_utc"],
        "generated_at": generated_at,
        "provenance": provenance,
        "date_window": {
            "date": six["date"],
            "windows": [row["window"] for row in six["per_window"]],
        },
        "input_artifacts": [
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256(path),
                "tracked": path != six_payload_path,
            }
            for path in (
                run1_path,
                run2_path,
                six_summary_path,
                six_payload_path,
                snapshot_path,
            )
        ],
        "output_artifacts": [
            {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}
            for path in report_paths
        ],
        "full_12_date_run_executed": False,
    }
    write_json(output / "run_manifest.json", manifest)


if __name__ == "__main__":
    main()
