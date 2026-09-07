"""Compare frozen 5C shadow labels with blinded L2 fill assumptions."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from lobcap.l2.downsample import downsample_file
from lobcap.l2.fill_models import MODEL_SPECS, Prediction, evaluate_models
from lobcap.l2.schema import (
    ZERO,
    BlindedPlacement,
    L2Projection,
    assert_blinded_mapping,
    decimal_text,
    timestamp_ns,
)


TRUTH_LABEL = "L3-derived shadow-fill label"
FROZEN_TRUTH_SHA256 = "593920dc416631c0f4924d9ff420ae6b8801580c85cdc50c9db06337773f28ca"
ALPHA_MODEL_NAMES = tuple(spec.name for spec in MODEL_SPECS if spec.alpha is not None)


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("rt", encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_frozen_truth(
    path: str | Path,
) -> tuple[list[BlindedPlacement], list[dict[str, Any]]]:
    rows = _read_jsonl(path)
    placements: list[BlindedPlacement] = []
    seen: set[str] = set()
    for row in rows:
        if row.get("label_type") != TRUTH_LABEL:
            raise ValueError("truth artifact label definition changed")
        virtual_id = str(row["virtual_order_id"])
        if virtual_id in seen:
            raise ValueError(f"duplicate frozen placement: {virtual_id}")
        seen.add(virtual_id)
        placements.append(
            BlindedPlacement(
                virtual_order_id=virtual_id,
                placement_sequence=int(row["placement_sequence"]),
                placement_exchange_time=str(row["placement_exchange_time"]),
                placement_time_ns=timestamp_ns(str(row["placement_exchange_time"])),
                expiry_time=str(row["expiry_time"]),
                expiry_time_ns=timestamp_ns(str(row["expiry_time"])),
                side=str(row["side"]),
                price=Decimal(str(row["price"])),
                size=Decimal(str(row["original_size"])),
                ttl_seconds=int(row["ttl_seconds"]),
            )
        )
    return placements, rows


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _confusion(
    truth: Iterable[bool], prediction: Iterable[bool]
) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    for actual, predicted in zip(truth, prediction, strict=True):
        if actual and predicted:
            tp += 1
        elif not actual and predicted:
            fp += 1
        elif actual and not predicted:
            fn += 1
        else:
            tn += 1
    return {
        "TP": tp,
        "FP": fp,
        "FN": fn,
        "TN": tn,
        "n": tp + fp + fn + tn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "false_positive_rate": _ratio(fp, fp + tn),
        "false_negative_rate": _ratio(fn, tp + fn),
    }


def _quantity_metrics(
    truth_rows: list[dict[str, Any]], predictions: list[Prediction]
) -> dict[str, Any]:
    errors = [
        prediction.predicted_filled_quantity
        - Decimal(str(truth["own_filled_volume"]))
        for truth, prediction in zip(truth_rows, predictions, strict=True)
    ]
    absolute = [abs(error) for error in errors]
    over = [max(ZERO, error) for error in errors]
    under = [max(ZERO, -error) for error in errors]
    count = Decimal(len(errors))
    return {
        "n": len(errors),
        "mae": decimal_text(sum(absolute, ZERO) / count),
        "bias": decimal_text(sum(errors, ZERO) / count),
        "mean_overfill_error": decimal_text(sum(over, ZERO) / count),
        "mean_underfill_error": decimal_text(sum(under, ZERO) / count),
    }


def _timing_metric(
    truth_rows: list[dict[str, Any]],
    predictions: list[Prediction],
    truth_field: str,
    predicted_field: str,
) -> dict[str, Any]:
    eligible = [
        (truth, prediction)
        for truth, prediction in zip(truth_rows, predictions, strict=True)
        if truth.get(truth_field) is not None
    ]
    errors: list[Decimal] = []
    missing = 0
    for truth, prediction in eligible:
        predicted_time = getattr(prediction, predicted_field)
        if predicted_time is None:
            missing += 1
            continue
        difference_ns = timestamp_ns(predicted_time) - timestamp_ns(str(truth[truth_field]))
        errors.append(Decimal(difference_ns) / Decimal(1_000_000_000))
    early = sum(error < ZERO for error in errors)
    late = sum(error > ZERO for error in errors)
    matched = len(errors)
    return {
        "truth_filled_n": len(eligible),
        "matched_timing_n": matched,
        "prediction_missing_n": missing,
        "median_error_seconds": (
            decimal_text(median(errors)) if errors else None
        ),
        "mae_seconds": (
            decimal_text(sum((abs(error) for error in errors), ZERO) / Decimal(matched))
            if errors
            else None
        ),
        "early_fill_rate": _ratio(early, matched),
        "late_fill_rate": _ratio(late, matched),
        "on_time_rate": _ratio(matched - early - late, matched),
        "error_sign": "prediction time minus L3-derived shadow-fill time",
        "censored_as_large_time": False,
    }


def _posthoc_regime(
    ids: set[str], truth_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    names = (
        "ahead_volume_initial",
        "ahead_trade_depletion",
        "ahead_cancel_depletion",
        "ahead_modify_decrease",
        "ahead_priority_loss",
    )
    result: dict[str, Any] = {"n": len(ids)}
    for name in names:
        values = [Decimal(str(truth_by_id[item][name])) for item in sorted(ids)]
        result[name] = {
            "total": decimal_text(sum(values, ZERO)),
            "mean": (
                decimal_text(sum(values, ZERO) / Decimal(len(values)))
                if values
                else None
            ),
        }
    result["used_as_model_input"] = False
    return result


def _module_l3_imports() -> list[str]:
    source_path = Path(__file__).with_name("fill_models.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names if alias.name.startswith("lobcap.l3"))
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("lobcap.l3"):
            imports.append(str(node.module))
    return imports


def _leakage_report(
    projection: L2Projection,
    placements: list[BlindedPlacement],
    truth_rows: list[dict[str, Any]],
    predictions: list[Prediction],
    truth_path: str | Path,
) -> dict[str, Any]:
    forbidden_hits: list[str] = []
    serialized_frames: list[dict[str, Any]] = []
    for index, frame in enumerate(projection.serialized_frames()):
        try:
            assert_blinded_mapping(frame)
        except ValueError as exc:
            forbidden_hits.append(f"frame[{index}]: {exc}")
        serialized_frames.append(frame)
    serialized_text = json.dumps(serialized_frames, separators=(",", ":"))
    import re

    uuid_hits = re.findall(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        serialized_text,
        flags=re.IGNORECASE,
    )
    truth_by_id = {str(row["virtual_order_id"]): row for row in truth_rows}
    initial_queue_mismatches = []
    for placement in placements:
        projected = projection.initial_queues[placement.virtual_order_id]
        truth_ahead = Decimal(
            str(truth_by_id[placement.virtual_order_id]["ahead_volume_initial"])
        )
        if projected != truth_ahead:
            initial_queue_mismatches.append(
                {
                    "virtual_order_id": placement.virtual_order_id,
                    "projected": decimal_text(projected),
                    "truth": decimal_text(truth_ahead),
                }
            )

    placement_signature = [placement.as_dict() for placement in placements]
    model_signatures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for prediction in predictions:
        model_signatures[prediction.model].append(
            {
                "virtual_order_id": prediction.virtual_order_id,
                "placement_sequence": prediction.placement_sequence,
                "placement_exchange_time": prediction.placement_exchange_time,
                "expiry_time": prediction.expiry_time,
                "side": prediction.side,
                "price": decimal_text(prediction.price),
                "size": decimal_text(prediction.virtual_size),
            }
        )
    expected_signature = [
        {
            "virtual_order_id": item["virtual_order_id"],
            "placement_sequence": item["placement_sequence"],
            "placement_exchange_time": item["placement_exchange_time"],
            "expiry_time": item["expiry_time"],
            "side": item["side"],
            "price": item["price"],
            "size": item["size"],
        }
        for item in placement_signature
    ]
    signature_mismatches = [
        name for name, signature in model_signatures.items() if signature != expected_signature
    ]
    l3_imports = _module_l3_imports()
    violations = (
        len(forbidden_hits)
        + len(uuid_hits)
        + len(initial_queue_mismatches)
        + len(signature_mismatches)
        + len(l3_imports)
        + sum(not matched for matched in projection.placement_bbo_matches.values())
    )
    return {
        "valid": violations == 0,
        "violations": violations,
        "truth_artifact": str(Path(truth_path)),
        "truth_artifact_sha256": sha256_file(truth_path),
        "expected_frozen_truth_sha256": FROZEN_TRUTH_SHA256,
        "l2_records_checked": len(serialized_frames),
        "forbidden_field_hits": forbidden_hits,
        "observed_exchange_uuid_hits": len(uuid_hits),
        "fill_model_l3_imports": l3_imports,
        "fill_model_receives_truth_rows": False,
        "fill_model_input_types": ["L2Projection", "BlindedPlacement", "ModelSpec"],
        "blinded_placement_fields": list(placement_signature[0]) if placement_signature else [],
        "same_placement_signature_all_models": not signature_mismatches,
        "placement_signature_mismatches": signature_mismatches,
        "initial_queue_matches_posthoc_truth_ahead": not initial_queue_mismatches,
        "initial_queue_mismatches": initial_queue_mismatches,
        "all_frozen_placements_at_bbo": all(projection.placement_bbo_matches.values()),
        "l3_decomposition_usage": "post-hoc error analysis only",
    }


def build_comparison(
    raw_path: str | Path,
    truth_path: str | Path,
) -> dict[str, Any]:
    actual_truth_sha256 = sha256_file(truth_path)
    if actual_truth_sha256 != FROZEN_TRUTH_SHA256:
        raise ValueError(
            "Prompt 5C truth artifact changed: "
            f"expected {FROZEN_TRUTH_SHA256}, got {actual_truth_sha256}"
        )
    placements, truth_rows = load_frozen_truth(truth_path)
    projection, reconstruction = downsample_file(raw_path, placements)
    predictions, model_invariants = evaluate_models(projection, placements)
    truth_by_id = {str(row["virtual_order_id"]): row for row in truth_rows}
    grouped: dict[str, list[Prediction]] = defaultdict(list)
    for prediction in predictions:
        grouped[prediction.model].append(prediction)

    truth_any_all = [Decimal(str(row["own_filled_volume"])) > ZERO for row in truth_rows]
    truth_full_all = [str(row["outcome"]) == "FULL_FILL" for row in truth_rows]
    any_eligible = [
        index
        for index, row in enumerate(truth_rows)
        if not (str(row["outcome"]) == "CENSORED" and not truth_any_all[index])
    ]
    full_eligible = [
        index for index, row in enumerate(truth_rows) if str(row["outcome"]) != "CENSORED"
    ]

    model_metrics: dict[str, Any] = {}
    confusions: dict[str, Any] = {}
    timings: dict[str, Any] = {}
    posthoc: dict[str, Any] = {}
    for name, model_predictions in grouped.items():
        any_matrix = _confusion(
            (truth_any_all[index] for index in any_eligible),
            (model_predictions[index].any_fill for index in any_eligible),
        )
        full_matrix = _confusion(
            (truth_full_all[index] for index in full_eligible),
            (model_predictions[index].full_fill for index in full_eligible),
        )
        quantity = _quantity_metrics(truth_rows, model_predictions)
        model_metrics[name] = {
            "n_placements": len(placements),
            "truth_any_fill_prevalence": _ratio(sum(truth_any_all), len(truth_any_all)),
            "truth_full_fill_prevalence": _ratio(sum(truth_full_all), len(truth_full_all)),
            "predicted_any_fill_rate": _ratio(
                sum(prediction.any_fill for prediction in model_predictions),
                len(model_predictions),
            ),
            "predicted_full_fill_rate": _ratio(
                sum(prediction.full_fill for prediction in model_predictions),
                len(model_predictions),
            ),
            "any_fill_precision": any_matrix["precision"],
            "any_fill_recall": any_matrix["recall"],
            "false_positive_rate": any_matrix["false_positive_rate"],
            "false_negative_rate": any_matrix["false_negative_rate"],
            "full_fill_precision": full_matrix["precision"],
            "full_fill_recall": full_matrix["recall"],
            "fill_quantity": quantity,
        }
        confusions[name] = {
            "any_fill": any_matrix,
            "full_fill": full_matrix,
            "any_fill_excluded_unresolved_censored": len(truth_rows) - len(any_eligible),
            "full_fill_excluded_censored": len(truth_rows) - len(full_eligible),
            "false_positive_definition": "backtest-only fill",
        }
        timings[name] = {
            "first_fill": _timing_metric(
                truth_rows, model_predictions, "first_fill_time", "first_fill_time"
            ),
            "full_fill": _timing_metric(
                truth_rows, model_predictions, "full_fill_time", "full_fill_time"
            ),
        }
        fp_ids = {
            model_predictions[index].virtual_order_id
            for index in any_eligible
            if not truth_any_all[index] and model_predictions[index].any_fill
        }
        fn_ids = {
            model_predictions[index].virtual_order_id
            for index in any_eligible
            if truth_any_all[index] and not model_predictions[index].any_fill
        }
        posthoc[name] = {
            "false_positives": _posthoc_regime(fp_ids, truth_by_id),
            "false_negatives": _posthoc_regime(fn_ids, truth_by_id),
            "warning": "L3 ahead decomposition was joined only after predictions were frozen.",
        }

    any_disagreement = 0
    full_disagreement = 0
    alpha_any_disagreement = 0
    for index in range(len(placements)):
        if len({grouped[name][index].any_fill for name in grouped}) > 1:
            any_disagreement += 1
        if len({grouped[name][index].full_fill for name in grouped}) > 1:
            full_disagreement += 1
        if len({grouped[name][index].any_fill for name in ALPHA_MODEL_NAMES}) > 1:
            alpha_any_disagreement += 1

    alpha_rank = sorted(
        ALPHA_MODEL_NAMES,
        key=lambda name: (
            Decimal(model_metrics[name]["fill_quantity"]["mae"]),
            confusions[name]["any_fill"]["FP"] + confusions[name]["any_fill"]["FN"],
            Decimal(name.split("=")[1]),
        ),
    )
    highest_fp = max(grouped, key=lambda name: confusions[name]["any_fill"]["FP"])
    highest_fn = max(grouped, key=lambda name: confusions[name]["any_fill"]["FN"])
    leakage = _leakage_report(
        projection, placements, truth_rows, predictions, truth_path
    )
    projection_violations = sum(projection.projection_invariants.values())
    invariant_report = {
        "valid": (
            reconstruction["valid"]
            and projection_violations == 0
            and not any(model_invariants.values())
            and leakage["valid"]
        ),
        "reconstruction_valid": reconstruction["valid"],
        "projection": dict(projection.projection_invariants),
        "fill_models": model_invariants,
        "leakage_violations": leakage["violations"],
    }
    summary = {
        "result": "PASS" if invariant_report["valid"] else "FAIL",
        "placements_compared": len(placements),
        "l2_frames_generated": len(projection.frames),
        "l2_level_updates_generated": sum(len(frame.level_updates) for frame in projection.frames),
        "l2_trades_generated": sum(frame.trade is not None for frame in projection.frames),
        "truth_any_fill_rate": _ratio(sum(truth_any_all), len(truth_any_all)),
        "truth_full_fill_rate": _ratio(sum(truth_full_all), len(truth_full_all)),
        "assumption_disagreement": {
            "any_fill_placements_all_models": any_disagreement,
            "full_fill_placements_all_models": full_disagreement,
            "any_fill_placements_alpha_grid_only": alpha_any_disagreement,
        },
        "highest_fp_model": highest_fp,
        "highest_fn_model": highest_fn,
        "descriptive_in_sample_best_alpha": alpha_rank[0],
        "best_alpha_selection": "minimum observed-window fill-quantity MAE; any-fill errors and lower alpha break ties",
        "labeled_as_calibrated": False,
        "deterministic_replay": None,
        "full_day_downloaded": False,
    }
    return {
        "projection": projection,
        "predictions": predictions,
        "model_metrics": {"summary": summary, "models": model_metrics, "posthoc": posthoc},
        "confusion_matrices": confusions,
        "timing_metrics": timings,
        "leakage_check": leakage,
        "invariant_report": invariant_report,
    }


def _stable_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "projection": [frame.as_dict() for frame in result["projection"].frames],
        "initial_queues": {
            key: decimal_text(value)
            for key, value in sorted(result["projection"].initial_queues.items())
        },
        "predictions": [prediction.as_dict() for prediction in result["predictions"]],
        "model_metrics": result["model_metrics"],
        "confusion_matrices": result["confusion_matrices"],
        "timing_metrics": result["timing_metrics"],
        "leakage_check": result["leakage_check"],
        "invariant_report": result["invariant_report"],
    }


def result_digest(result: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(_stable_payload(result), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _comparison_markdown(result: dict[str, Any]) -> str:
    summary = result["model_metrics"]["summary"]
    metrics = result["model_metrics"]["models"]
    confusions = result["confusion_matrices"]
    posthoc = result["model_metrics"]["posthoc"]
    touch = confusions["TOUCH_FILL"]["any_fill"]
    pessimistic = confusions["alpha=0.00"]["any_fill"]
    lines = [
        "# Prompt 5D — Blinded L2 fill-assumption comparison",
        "",
        "This is a one-minute correctness experiment against frozen Prompt 5C ",
        "**L3-derived shadow-fill labels**, not absolute market-impact ground truth.",
        "The alpha grid is not calibrated; `alpha=0.50` is an uncalibrated midpoint diagnostic.",
        "",
        "## Headline",
        "",
        f"- Placements: {summary['placements_compared']}",
        f"- Truth any-fill prevalence: {summary['truth_any_fill_rate']}",
        f"- Truth full-fill prevalence: {summary['truth_full_fill_rate']}",
        f"- Any-fill disagreement across all models: {summary['assumption_disagreement']['any_fill_placements_all_models']} placements",
        f"- Full-fill disagreement across all models: {summary['assumption_disagreement']['full_fill_placements_all_models']} placements",
        f"- Descriptive in-sample best alpha: {summary['descriptive_in_sample_best_alpha']} (not calibrated)",
        "",
        "## Model table",
        "",
        "| Model | Any TP/FP/FN/TN | Precision | Recall | Quantity MAE | First-fill timing MAE (s) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in metrics:
        matrix = confusions[name]["any_fill"]
        timing = result["timing_metrics"][name]["first_fill"]
        lines.append(
            f"| {name} | {matrix['TP']}/{matrix['FP']}/{matrix['FN']}/{matrix['TN']} "
            f"| {matrix['precision']} | {matrix['recall']} "
            f"| {metrics[name]['fill_quantity']['mae']} | {timing['mae_seconds']} |"
        )
    lines.extend(
        [
            "",
            "## Research questions",
            "",
            "1. **How often do assumptions disagree?** "
            f"Any-fill predictions differ on {summary['assumption_disagreement']['any_fill_placements_all_models']} of {summary['placements_compared']} placements; "
            f"the alpha grid alone differs on {summary['assumption_disagreement']['any_fill_placements_alpha_grid_only']}.",
            "2. **Which assumptions create backtest-only fills?** "
            f"The highest-FP model is `{summary['highest_fp_model']}`. Its FP count is {confusions[summary['highest_fp_model']]['any_fill']['FP']}. "
            "The post-hoc section in `model_metrics.json` reports cancellation/priority regimes, and those L3 fields were not model inputs.",
            "3. **Which assumptions miss L3-derived fills?** "
            f"The highest-FN model is `{summary['highest_fn_model']}` with {confusions[summary['highest_fn_model']]['any_fill']['FN']} misses. "
            f"For alpha=0, missed-order post-hoc cancel depletion totals {posthoc['alpha=0.00']['false_negatives']['ahead_cancel_depletion']['total']} BTC.",
            "4. **Is pessimistic always conservative?** "
            + (
                "It produced no any-fill false positives in this minute, so it was empirically conservative here. This is not a theorem or an out-of-sample result."
                if pessimistic["FP"] == 0
                else f"Not even in this minute: alpha=0 produced {pessimistic['FP']} backtest-only fills."
            ),
            "5. **Under which queue/cancellation regimes do errors occur?** "
            "The post-hoc aggregates show the regimes for FP/FN sets, but 64 placements in one minute are insufficient for stable conditioning by queue size or market regime.",
            "6. **Does touch-fill materially exaggerate fills?** "
            f"TOUCH_FILL predicts any fill at rate {metrics['TOUCH_FILL']['predicted_any_fill_rate']} versus truth {summary['truth_any_fill_rate']}, with {touch['FP']} backtest-only fills. "
            "Whether that is materially stable across dates is insufficiently sampled here.",
            "",
            "## Method boundary",
            "",
            "The fill-model module receives only blinded aggregate frames and sanitized placement fields. "
            "Actual order IDs, lifecycle reasons, FIFO positions, and L3 ahead decomposition are absent. "
            "L3 depletion categories are joined only after predictions for error analysis.",
            "Censored observations are retained as censored and are never replaced by an arbitrary large time-to-fill.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(result: dict[str, Any], output_dir: str | Path) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "l2_projection.jsonl").open("wt", encoding="utf-8") as target:
        for frame in result["projection"].serialized_frames():
            target.write(json.dumps(frame, sort_keys=True, separators=(",", ":")) + "\n")
    with (output / "predictions.jsonl").open("wt", encoding="utf-8") as target:
        for prediction in result["predictions"]:
            target.write(
                json.dumps(prediction.as_dict(), sort_keys=True, separators=(",", ":"))
                + "\n"
            )
    for key, filename in (
        ("model_metrics", "model_metrics.json"),
        ("confusion_matrices", "confusion_matrices.json"),
        ("timing_metrics", "timing_metrics.json"),
        ("leakage_check", "leakage_check.json"),
        ("invariant_report", "invariant_report.json"),
    ):
        (output / filename).write_text(
            json.dumps(result[key], indent=2) + "\n", encoding="utf-8"
        )
    (output / "comparison_summary.md").write_text(
        _comparison_markdown(result), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True)
    parser.add_argument("--truth", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--verify-determinism", action="store_true")
    args = parser.parse_args()

    result = build_comparison(args.raw, args.truth)
    if args.verify_determinism:
        second = build_comparison(args.raw, args.truth)
        deterministic = result_digest(result) == result_digest(second)
        result["model_metrics"]["summary"]["deterministic_replay"] = deterministic
        if not deterministic:
            result["model_metrics"]["summary"]["result"] = "FAIL"
            result["invariant_report"]["valid"] = False
    write_outputs(result, args.out)
    print(json.dumps(result["model_metrics"]["summary"], indent=2))
    raise SystemExit(
        0
        if result["model_metrics"]["summary"]["result"] == "PASS"
        and result["model_metrics"]["summary"]["deterministic_replay"] is not False
        else 1
    )


if __name__ == "__main__":
    main()
