"""Offline BP input/quote-path acceptance; no labels enter strategy execution."""
from collections import Counter
from contextlib import ExitStack
from decimal import Decimal, localcontext
import gzip
from itertools import zip_longest
import json
from pathlib import Path

from lobcap.backtest.replay import load_sample, iter_events
from .feature_cache import iter_feature_snapshots
from .signal_contracts import AnalysisSchedule
from .study1 import BP_CASES, BP_EXPRESSION, DELTA_METRICS, economic_value


def validate_bp_cases(row):
    cases = row["cases"]
    reference = cases["MM-1"]["config"]
    if reference["strategy"] != "inventory_skew" or reference["clock_mode"] != "recorded":
        raise ValueError("BP reference must use recorded inventory skew")
    varying = {"strategy", "signal_expression", "expression_parameters", "max_signal_ticks", "expression_version"}
    betas = []
    for label in BP_CASES[1:]:
        cfg = cases[label]["config"]
        if (cfg["strategy"] != "expression" or cfg["signal_expression"] != BP_EXPRESSION
            or any(cfg.get(k) != reference.get(k) for k in set(cfg)|set(reference) if k not in varying)
            or cfg["max_signal_ticks"] != reference.get("max_signal_ticks", "10")
            or cfg["expression_version"] != reference.get("expression_version", "signal-v1")
            or len(cfg["expression_parameters"]) != 1 or cfg["expression_parameters"][0][0] != "bp_beta"):
            raise ValueError("BP cases may vary only the registered coefficient")
        betas.append(Decimal(cfg["expression_parameters"][0][1]))
    if not all(b.is_finite() for b in betas) or betas[0] != 0 or betas[1] <= 0 or betas[2] != -betas[1]:
        raise ValueError("BP zero/positive/reversed coefficients mismatch")
    if row.get("bp_feature_parity", {}).get("status") != "PASS":
        raise ValueError("BP requires offline/runtime feature parity")
    with localcontext() as ctx:
        ctx.prec = 50
        for k in DELTA_METRICS:
            a, b = (Decimal(str(cases[label]["metrics"][k])) for label in ("MM-1", "MM-2-reversed"))
            if not a.is_finite() or not b.is_finite() or b-a != Decimal(row["delta_reversed_minus_mm1"][k]):
                raise ValueError("invalid reversed BP delta")


def audit_feature_parity(config, sample_path, paths):
    """Replay causal features at actual decisions, including stale/unknown rows.

    Runtime values are rounded to the offline v3 28-digit Decimal context for
    comparison; exchange/availability timestamps must match exactly. Runtime
    book-age gating is conservatively stricter than client-availability age.
    """
    if len(paths) != len(BP_CASES):
        raise ValueError("BP audit requires four execution paths")
    initial, manifest = load_sample(sample_path, config)
    schedule = AnalysisSchedule(config.sample.start_ns, config.sample.end_ns, config.decision_ms*1_000_000)
    statuses, usable, count = Counter(), 0, 0
    effects = {label: {"quote_path_differences_from_mm1": 0,
        "stage_changes": Counter(), "decision_reasons": Counter()} for label in BP_CASES}
    with ExitStack() as stack, localcontext() as ctx:
        ctx.prec = 28
        snapshots = iter_feature_snapshots(initial, lambda: iter_events(sample_path), schedule,
            config.instrument.tick, clock_mode="recorded", bootstrap_receive_ns=manifest["bootstrap_receive_ns"],
            stale_after_ns=config.stale_book_ms*1_000_000, feature_names=("imbalance_1",))
        decisions, traces = [], []
        for path in paths:
            for dest, name in ((decisions, "decisions"), (traces, "decision_traces")):
                stream = stack.enter_context(gzip.open(Path(path)/f"{name}.jsonl.gz", "rt", encoding="utf-8"))
                dest.append(map(json.loads, stream))
        for values in zip_longest(snapshots, *decisions, *traces):
            if any(v is None for v in values):
                raise ValueError("BP decision/trace/snapshot populations differ")
            snapshot, *rest = values
            ds, ts = rest[:4], rest[4:]
            point = snapshot.point("imbalance_1")
            statuses[point.status] += 1
            count += 1
            fresh = snapshot.analysis_time_ns-snapshot.client_mid_source_exchange_time_ns <= config.stale_book_ms*1_000_000
            valid = point.status == "AVAILABLE" and fresh and snapshot.client_mid_coverage
            usable += int(valid)
            reference_quotes = economic_value([ds[0].get(k) for k in ("buy_price", "sell_price", "buy_quantity", "sell_quantity")])
            for label, decision, trace in zip(BP_CASES, ds, ts):
                if (decision["time_ns"] != snapshot.analysis_time_ns or trace["time_ns"] != snapshot.analysis_time_ns
                    or decision["observed_book_exchange_ns"] != snapshot.client_mid_source_exchange_time_ns
                    or decision["observed_book_receive_ns"] != snapshot.client_mid_available_time_ns
                    or Decimal(decision["mid"]) != snapshot.client_mid
                    or (point.status == "AVAILABLE" and +Decimal(decision["imbalance"]) != point.value)):
                    raise ValueError("BP causal feature or timestamp mismatch")
                if not valid and (decision["buy_price"] is not None or decision["sell_price"] is not None):
                    raise ValueError("BP quoted from stale or unknown coverage")
                if decision.get("signal_reason") in {"signal_clipped", "feature_unavailable"}:
                    raise ValueError("unexpected BP expression clipping or missing input")
                effect = effects[label]
                quotes = economic_value([decision.get(k) for k in ("buy_price", "sell_price", "buy_quantity", "sell_quantity")])
                effect["quote_path_differences_from_mm1"] += int(quotes != reference_quotes)
                effect["decision_reasons"][decision["reason"]] += 1
                for key, changed in trace["stage_changes"].items():
                    effect["stage_changes"][key] += int(changed is True)
    if not count or effects["MM-2-zero"]["quote_path_differences_from_mm1"]:
        raise ValueError("BP zero coefficient quote-path parity failed")
    return {"status": "PASS", "feature": "imbalance_1", "comparison_decimal_precision": 28,
        "decisions_per_case": count, "feature_statuses": dict(statuses),
        "runtime_usable_decisions": usable, "runtime_unusable_decisions": count-usable,
        "coverage_policy": "no desired quotes on unknown coverage or stale exchange-age",
        "case_effects": {label: {k: dict(v) if isinstance(v, Counter) else v for k, v in row.items()}
                         for label, row in effects.items()}}
