"""Frozen Study 1 decomposition, interaction, and execution-sensitivity study.

This module composes the existing strategy, sweep, accounting, and review APIs.
It does not implement an alternate simulator or fill model.
"""
from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
from dataclasses import asdict
from decimal import Decimal, localcontext
import gzip
from itertools import zip_longest
import json
from pathlib import Path

from lobcap.backtest.replay import iter_events, load_sample
from lobcap.backtest.runner import run_sweep
from lobcap.backtest.types import jsonable
from lobcap.config.backtest import variant
from .feature_cache import iter_feature_snapshots
from .sample_catalogue import (
    CATALOGUE_VERSION, DEVELOPMENT, PILOT_OOS_LOCKED, VALIDATION_LOCKED,
    fixed_pilot_windows,
)
from .signal_contracts import AnalysisSchedule
from .signal_study import development_windows, digest
from .strategy_compare import build_scorecard
from .study1 import (
    BP_EXPRESSION, DELTA_METRICS, ADDITIVE_METRICS, economic_value,
    execution_fingerprint, run_record, sealed_config,
)
from .vpin_features import provider
from lobcap.backtest.vpin import NAME as VPIN_NAME


DECOMPOSITION_CASES = (
    "MM-1", "MM-3-zero", "MM-3-gate-only", "MM-3-mean-width", "MM-3-vpin",
)
COMBINED_CASES = (
    "MM-1", "MM-4-zero", "MM-2-bp", "MM-3-vpin", "MM-4-bp-vpin",
    "MM-4-reversed-bp-vpin",
)
PRIMARY_CASES = ("MM-1", "MM-2-bp", "MM-3-vpin", "MM-4-bp-vpin")
VALIDATION_CASES = ("MM-1", "MM-4-bp-vpin")
ROBUSTNESS_SCENARIOS = {
    "fees_1bps": {"maker_bps": "1", "taker_bps": "1"},
    "latency_50_50_100ms": {"entry_ms": 50, "cancel_ms": 50, "report_ms": 100},
    "queue_alpha_0_25": {"alpha": "0.25"},
    "queue_alpha_0_75": {"alpha": "0.75"},
    "joint_fee_latency_queue": {"maker_bps": "1", "taker_bps": "1",
        "entry_ms": 50, "cancel_ms": 50, "report_ms": 100, "alpha": "0.75"},
}


def _base(config):
    return variant(config, strategy="inventory_skew", signal_strength="0",
        signal_expression="", expression_parameters=(), book_pressure_ticks="0")


def decomposition_configs(config, mean_vpin):
    mean = Decimal(str(mean_vpin))
    if not 0 <= mean <= 1:
        raise ValueError("mean VPIN must be in [0,1]")
    base = _base(config)
    width = config.vpin_width_ticks
    if width <= 0:
        raise ValueError("VPIN decomposition requires positive configured width")
    return {
        "MM-1": base,
        "MM-3-zero": variant(base, strategy="vpin_inventory", vpin_width_ticks="0",
            vpin_mode="signal"),
        "MM-3-gate-only": variant(base, strategy="vpin_inventory", vpin_width_ticks="0",
            vpin_mode="gate"),
        "MM-3-mean-width": variant(base, strategy="vpin_inventory", vpin_width_ticks=width,
            vpin_mode="constant", vpin_constant_value=mean),
        "MM-3-vpin": variant(base, strategy="vpin_inventory", vpin_width_ticks=width,
            vpin_mode="signal"),
    }


def combined_configs(config):
    if config.signal_strength <= 0 or config.vpin_width_ticks <= 0:
        raise ValueError("combined Study 1 requires positive BP and VPIN coefficients")
    base = _base(config)
    bp = config.signal_strength
    mm2 = variant(base, strategy="expression", signal_expression=BP_EXPRESSION,
        expression_parameters={"bp_beta": bp})
    vpin = variant(base, strategy="vpin_inventory", vpin_mode="signal")
    return {
        "MM-1": base,
        "MM-4-zero": variant(base, strategy="vpin_inventory", vpin_width_ticks="0",
            vpin_mode="signal", book_pressure_ticks="0"),
        "MM-2-bp": mm2,
        "MM-3-vpin": vpin,
        "MM-4-bp-vpin": variant(vpin, book_pressure_ticks=bp),
        "MM-4-reversed-bp-vpin": variant(vpin, book_pressure_ticks=-bp),
    }


def primary_configs(config):
    combined = combined_configs(config)
    return {label: combined[label] for label in PRIMARY_CASES}


def validation_configs(config):
    combined = combined_configs(config)
    return {label: combined[label] for label in VALIDATION_CASES}


def scenario_config(config, scenario):
    if scenario == "baseline":
        return config
    if scenario not in ROBUSTNESS_SCENARIOS:
        raise ValueError("unknown robustness scenario")
    return variant(config, **ROBUSTNESS_SCENARIOS[scenario])


def mixed_observations(config, sample_path, interval_ns):
    """Replay VPIN and imbalance through one causal delivery stream."""
    initial, manifest = load_sample(sample_path, config)
    holder = []
    def factory(*args):
        engine = provider(config)(*args)
        holder.append(engine)
        return engine
    schedule = AnalysisSchedule(config.sample.start_ns, config.sample.end_ns, interval_ns)
    snapshots = iter_feature_snapshots(initial, lambda: iter_events(sample_path), schedule,
        config.instrument.tick, clock_mode="recorded",
        bootstrap_receive_ns=manifest["bootstrap_receive_ns"],
        stale_after_ns=config.stale_book_ms*1_000_000,
        acquisition_segment=manifest.get("acquisition_segments", ["segment-1"])[0],
        feature_names=("imbalance_1", VPIN_NAME), engine_factory=factory)
    for snapshot in snapshots:
        yield snapshot, holder[0].vpin.snapshot(snapshot.analysis_time_ns)


def vpin_mean_window(config, sample_path):
    count = 0
    total = Decimal(0)
    statuses = Counter()
    with localcontext() as ctx:
        ctx.prec = 50
        for _, state in mixed_observations(config, sample_path, config.decision_ms*1_000_000):
            statuses[state["status"]] += 1
            if state["status"] == "AVAILABLE":
                count += 1
                total += state["value"]
    if not count:
        raise ValueError("no available decision-time VPIN values")
    return jsonable({"available": count, "sum": total, "mean": total/count,
        "feature_statuses": dict(statuses)})


def vpin_mean_phase(config, window, seal, sample_path, output):
    sealed = sealed_config(config, window, seal, sample_path, output, "vpin")
    row = vpin_mean_window(sealed, sample_path)
    row.update(window_id=window.window_id, date=window.date,
        sample_sha256=seal["sample_sha256"])
    return {"window": row}


def pooled_vpin_mean(rows):
    with localcontext() as ctx:
        ctx.prec = 50
        count = sum(row["available"] for row in rows)
        total = sum((Decimal(row["sum"]) for row in rows), Decimal(0))
        if not count:
            raise ValueError("empty VPIN mean fit")
        return jsonable({"available": count, "sum": total, "mean": total/count,
            "fit_scope": "all AVAILABLE 250ms decision-grid inputs in sealed DEVELOPMENT windows",
            "outcomes_used": False})


def _changes(configs):
    fields = ("strategy", "inventory_skew_ticks", "signal_strength", "signal_expression",
        "expression_parameters", "vpin_width_ticks", "vpin_mode", "vpin_constant_value",
        "book_pressure_ticks")
    return [{key: getattr(config, key) for key in fields} for config in configs.values()]


def _quotes(decision):
    return economic_value([decision.get(k) for k in
        ("buy_price", "sell_price", "buy_quantity", "sell_quantity")])


def audit_causal_paths(config, sample_path, labels, configs, paths):
    if tuple(configs) != tuple(labels) or len(paths) != len(labels):
        raise ValueError("causal audit case mismatch")
    counts = Counter()
    effects = {label: {"quote_path_differences_from_mm1": 0,
        "decision_reasons": Counter()} for label in labels}
    with ExitStack() as stack, localcontext() as ctx:
        ctx.prec = 28
        decisions, traces = [], []
        for path in paths:
            decisions.append(map(json.loads, stack.enter_context(gzip.open(
                Path(path)/"decisions.jsonl.gz", "rt", encoding="utf-8"))))
            traces.append(map(json.loads, stack.enter_context(gzip.open(
                Path(path)/"decision_traces.jsonl.gz", "rt", encoding="utf-8"))))
        source = mixed_observations(config, sample_path, config.decision_ms*1_000_000)
        for values in zip_longest(source, *decisions, *traces):
            if any(value is None for value in values):
                raise ValueError("Study 1 completion decision populations differ")
            (snapshot, state), *rest = values
            ds, ts = rest[:len(labels)], rest[len(labels):]
            bp = snapshot.point("imbalance_1")
            counts[("vpin", state["status"])] += 1
            counts[("bp", bp.status)] += 1
            reference = _quotes(ds[0])
            for label, cfg, decision, trace in zip(labels, configs.values(), ds, ts):
                if (decision["time_ns"] != snapshot.analysis_time_ns
                    or trace["time_ns"] != snapshot.analysis_time_ns
                    or decision["observed_book_exchange_ns"] != snapshot.client_mid_source_exchange_time_ns
                    or decision["observed_book_receive_ns"] != snapshot.client_mid_available_time_ns
                    or Decimal(decision["mid"]) != snapshot.client_mid
                    or (bp.status == "AVAILABLE" and +Decimal(decision["imbalance"]) != bp.value)):
                    raise ValueError("Study 1 completion causal feature mismatch")
                if cfg.strategy == "vpin_inventory":
                    if trace["policy_state"]["vpin"] != jsonable(state):
                        raise ValueError("Study 1 completion VPIN state mismatch")
                    gated = cfg.vpin_width_ticks != 0 or cfg.vpin_mode == "gate"
                    if gated and state["status"] != "AVAILABLE" and any(
                            decision.get(k) is not None for k in ("buy_price", "sell_price")):
                        raise ValueError("VPIN case quoted while unavailable")
                    if cfg.book_pressure_ticks and bp.status != "AVAILABLE" and any(
                            decision.get(k) is not None for k in ("buy_price", "sell_price")):
                        raise ValueError("combined case quoted without BP")
                effect = effects[label]
                effect["quote_path_differences_from_mm1"] += int(_quotes(decision) != reference)
                effect["decision_reasons"][decision["reason"]] += 1
    if not sum(v for (feature, _), v in counts.items() if feature == "vpin"):
        raise ValueError("empty causal audit")
    return {"status": "PASS", "decision_grid_ms": config.decision_ms,
        "feature_statuses": {feature: {status: count for (name, status), count in counts.items()
            if name == feature} for feature in ("vpin", "bp")},
        "case_effects": {label: {"quote_path_differences_from_mm1": row["quote_path_differences_from_mm1"],
            "decision_reasons": dict(row["decision_reasons"])} for label, row in effects.items()}}


def review_group(window, seal, configs, paths, labels, zero_label=None):
    if tuple(configs) != tuple(labels) or len(paths) != len(labels):
        raise ValueError("Study 1 completion case order mismatch")
    records = [run_record(path) for path in paths]
    for label, record in zip(labels, records):
        if record["config"] != configs[label].resolved() or record["sample_sha256"] != seal["sample_sha256"]:
            raise ValueError("Study 1 completion run provenance mismatch")
    cards = {row["run_id"]: row for row in build_scorecard(records)}
    if len(cards) != len(labels) or not all(row["reconciled"] for row in cards.values()):
        raise ValueError("Study 1 completion requires reconciled ledgers")
    cases = {label: {"output_dir": str(path), "metrics": cards[record["run_id"]],
        "summary": record["summary"], "config": record["config"],
        "deterministic_digest": record["manifest"]["deterministic_digest"]}
        for label, record, path in zip(labels, records, paths)}
    zero = None
    if zero_label is not None:
        a, b = labels[0], zero_label
        zero = (economic_value(cases[a]["summary"]) == economic_value(cases[b]["summary"])
            and execution_fingerprint(paths[labels.index(a)], "vpin")
                == execution_fingerprint(paths[labels.index(b)], "vpin"))
        if not zero:
            raise ValueError("completion zero composite differs from MM-1")
    with localcontext() as ctx:
        ctx.prec = 50
        deltas = {label: {metric: Decimal(str(cases[label]["metrics"][metric]))
            - Decimal(str(cases[labels[0]]["metrics"][metric])) for metric in DELTA_METRICS}
            for label in labels[1:]}
    return jsonable({"window_id": window.window_id, "date": window.date,
        "role": window.role, "sample_sha256": seal["sample_sha256"], "cases": cases,
        "deltas_from_mm1": deltas, "zero_control_parity": zero,
        "code_version": records[0]["manifest"]["code_version"],
        "scheduler_identity": records[0]["scheduler_identity"],
        "execution_evidence_sufficient": all(cards[r["run_id"]]["comparable"] for r in records)})


def run_group(config, window, seal, sample_path, output, phase, mean_vpin=None,
              scenario="baseline"):
    base = sealed_config(config, window, seal, sample_path, output, "vpin")
    base = scenario_config(base, scenario)
    if phase == "decomposition":
        configs = decomposition_configs(base, mean_vpin)
        labels, zero = DECOMPOSITION_CASES, "MM-3-zero"
    elif phase == "combined":
        configs = combined_configs(base)
        labels, zero = COMBINED_CASES, "MM-4-zero"
    elif phase == "robustness":
        configs = primary_configs(base)
        labels, zero = PRIMARY_CASES, None
    elif phase in {"validation", "oos"}:
        configs = validation_configs(base)
        labels, zero = VALIDATION_CASES, None
    else:
        raise ValueError("unknown Study 1 completion phase")
    comparison, _ = run_sweep(next(iter(configs.values())), _changes(configs), sample_path)
    paths = [Path(path) for path in json.loads(
        (comparison/"comparison_manifest.json").read_text(encoding="utf-8"))["runs"]]
    row = review_group(window, seal, configs, paths, labels, zero)
    row["scenario"] = scenario
    row["causal_audit"] = audit_causal_paths(base, sample_path, labels, configs, paths)
    return {"window": row}


def _aggregate(members, labels):
    if not members:
        raise ValueError("cannot aggregate empty Study 1 rows")
    reference = labels[0]
    with localcontext() as ctx:
        ctx.prec = 50
        cases = {}
        for label in labels:
            values = {metric: [Decimal(str(row["cases"][label]["metrics"][metric]))
                for row in members] for metric in DELTA_METRICS}
            cases[label] = {
                "sum_reset_window": {metric: sum(values[metric], Decimal(0))
                    for metric in ADDITIVE_METRICS},
                "mean_window": {metric: sum(values[metric], Decimal(0))/len(members)
                    for metric in DELTA_METRICS},
            }
        deltas = {}
        for label in labels[1:]:
            deltas[label] = {
                "sum_reset_window": {metric: cases[label]["sum_reset_window"][metric]
                    - cases[reference]["sum_reset_window"][metric] for metric in ADDITIVE_METRICS},
                "mean_window": {metric: cases[label]["mean_window"][metric]
                    - cases[reference]["mean_window"][metric] for metric in DELTA_METRICS},
                "positive_net_pnl_windows": sum(Decimal(row["deltas_from_mm1"][label]["net_pnl"]) > 0
                    for row in members),
                "negative_net_pnl_windows": sum(Decimal(row["deltas_from_mm1"][label]["net_pnl"]) < 0
                    for row in members),
            }
    return jsonable({"windows": [row["window_id"] for row in members],
        "paired_windows": len(members), "cases": cases, "deltas_from_mm1": deltas})


def summarize_rows(rows, labels):
    ids = [row["window_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate windows in Study 1 completion summary")
    codes = {row["code_version"] for row in rows}
    schedulers = {json.dumps(row["scheduler_identity"], sort_keys=True) for row in rows}
    if len(codes) != 1 or len(schedulers) != 1 or any(
            not all(label in row["cases"] for label in labels) for row in rows):
        raise ValueError("Study 1 completion code, scheduler, or case drift")
    ordered = sorted(rows, key=lambda row: row["window_id"])
    result = {"per_window": ordered, "pooled": _aggregate(ordered, labels),
        "per_date": {date: _aggregate([row for row in ordered if row["date"] == date], labels)
            for date in sorted({row["date"] for row in ordered})},
        "aggregation": "independent reset-window paired diagnostics",
        "statistical_significance": "NOT_CLAIMED", "paper_eligibility": "INSUFFICIENT_EVIDENCE"}
    def contrast(treatment, reference):
        with localcontext() as ctx:
            ctx.prec = 50
            values = {metric: [(Decimal(row["cases"][treatment]["metrics"][metric])
                - Decimal(row["cases"][reference]["metrics"][metric])) for row in ordered]
                for metric in DELTA_METRICS}
            return jsonable({"treatment": treatment, "reference": reference,
                "sum_reset_window": {metric: sum(values[metric], Decimal(0))
                    for metric in ADDITIVE_METRICS},
                "mean_window": {metric: sum(values[metric], Decimal(0))/len(ordered)
                    for metric in DELTA_METRICS},
                "positive_net_pnl_windows": sum(value > 0 for value in values["net_pnl"]),
                "negative_net_pnl_windows": sum(value < 0 for value in values["net_pnl"])})
    if tuple(labels) == DECOMPOSITION_CASES:
        result["decomposition_contrasts"] = {
            "availability_gate": contrast("MM-3-gate-only", "MM-1"),
            "mean_width_given_gate": contrast("MM-3-mean-width", "MM-3-gate-only"),
            "adaptive_value_vs_mean_width": contrast("MM-3-vpin", "MM-3-mean-width"),
            "adaptive_width_given_gate": contrast("MM-3-vpin", "MM-3-gate-only")}
    if "MM-4-bp-vpin" in labels and all(x in labels for x in
            ("MM-1", "MM-2-bp", "MM-3-vpin")):
        result["component_contrasts"] = {
            "bp_given_inventory": contrast("MM-2-bp", "MM-1"),
            "vpin_given_inventory": contrast("MM-3-vpin", "MM-1"),
            "bp_given_vpin": contrast("MM-4-bp-vpin", "MM-3-vpin"),
            "vpin_given_bp": contrast("MM-4-bp-vpin", "MM-2-bp")}
    if "MM-4-bp-vpin" in labels and all(x in labels for x in ("MM-2-bp", "MM-3-vpin")):
        with localcontext() as ctx:
            ctx.prec = 50
            interaction = {}
            for metric in DELTA_METRICS:
                values = [(Decimal(row["cases"]["MM-4-bp-vpin"]["metrics"][metric])
                    - Decimal(row["cases"]["MM-2-bp"]["metrics"][metric]))
                    - (Decimal(row["cases"]["MM-3-vpin"]["metrics"][metric])
                    - Decimal(row["cases"]["MM-1"]["metrics"][metric])) for row in ordered]
                interaction[metric] = sum(values, Decimal(0))/len(values)
            result["mean_factorial_interaction"] = jsonable(interaction)
    return result


def role_windows(catalogue, role):
    """Validate all catalogue seals, then select a locked role after an external gate."""
    development_windows(catalogue)  # validates catalogue identity and every row
    if role == DEVELOPMENT:
        return development_windows(catalogue)
    if role not in {VALIDATION_LOCKED, PILOT_OOS_LOCKED}:
        raise ValueError("unsupported Study 1 role")
    expected = {window.window_id: window for window in fixed_pilot_windows()}
    result = []
    for row in catalogue["windows"]:
        window = expected[row["window"]["window_id"]]
        if window.role != role:
            continue
        seal = {k: v for k, v in row.items() if k != "sealed_manifest_sha256"}
        if (row["window"] != asdict(window) or row.get("role") != role
            or row.get("quality_status") != "PASS"
            or row.get("outcomes_access") != "LOCKED"
            or row.get("sealed_manifest_sha256") != digest(seal)):
            raise ValueError("locked Study 1 window seal mismatch")
        result.append((window, row))
    result.sort(key=lambda pair: pair[0].start_ns)
    if not result:
        raise ValueError("no sealed windows for requested role")
    return tuple(result)


def development_gate(combined_summary, robustness):
    baseline = combined_summary
    candidate = "MM-4-bp-vpin"
    baseline_delta = Decimal(baseline["pooled"]["deltas_from_mm1"][candidate]
        ["sum_reset_window"]["net_pnl"])
    date_positive = all(Decimal(row["deltas_from_mm1"][candidate]["sum_reset_window"]["net_pnl"]) > 0
        for row in baseline["per_date"].values())
    scenario_deltas = {name: Decimal(summary["pooled"]["deltas_from_mm1"][candidate]
        ["sum_reset_window"]["net_pnl"]) for name, summary in robustness.items()}
    positive = sum(value > 0 for value in scenario_deltas.values())
    drawdown = Decimal(baseline["pooled"]["deltas_from_mm1"][candidate]
        ["mean_window"]["max_drawdown"])
    checks = {"baseline_net_delta_positive": baseline_delta > 0,
        "both_development_dates_positive": date_positive,
        "at_least_four_of_six_scenarios_positive": positive >= 4,
        "joint_stress_positive": scenario_deltas["joint_fee_latency_queue"] > 0,
        "baseline_mean_drawdown_not_worse": drawdown <= 0}
    return jsonable({"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
        "scenario_net_pnl_deltas": scenario_deltas,
        "rule": "all checks fixed before completion execution; FAIL keeps validation and OOS sealed"})


def validation_gate(baseline, joint):
    candidate = "MM-4-bp-vpin"
    delta = Decimal(baseline["pooled"]["deltas_from_mm1"][candidate]["sum_reset_window"]["net_pnl"])
    joint_delta = Decimal(joint["pooled"]["deltas_from_mm1"][candidate]["sum_reset_window"]["net_pnl"])
    positive = baseline["pooled"]["deltas_from_mm1"][candidate]["positive_net_pnl_windows"]
    drawdown = Decimal(baseline["pooled"]["deltas_from_mm1"][candidate]["mean_window"]["max_drawdown"])
    checks = {"pooled_net_delta_positive": delta > 0, "at_least_three_of_four_windows_positive": positive >= 3,
        "joint_stress_positive": joint_delta > 0, "mean_drawdown_not_worse": drawdown <= 0}
    return jsonable({"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
        "rule": "all checks fixed before validation execution; FAIL keeps pilot OOS sealed"})
