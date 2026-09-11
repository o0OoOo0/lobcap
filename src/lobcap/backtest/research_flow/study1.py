"""Study 1 baseline orchestration over the existing execution and review APIs."""
from dataclasses import replace
from decimal import Decimal, localcontext
import gzip
import csv
import hashlib
import json
from pathlib import Path

from lobcap.backtest.replay import load_sample
from lobcap.backtest.runner import run_sweep
from lobcap.backtest.types import SampleSpec, jsonable
from lobcap.config.backtest import variant
from .catalog import run_record
from .signal_study import development_windows, digest
from .strategy_compare import build_scorecard


CASES = ("MM-0", "MM-1-zero", "MM-1")
DELTA_METRICS = ("net_pnl", "gross_pnl", "fees", "spread_capture", "inventory_mtm",
                 "max_drawdown", "max_abs_inventory", "ending_inventory", "turnover")
ADDITIVE_METRICS = ("net_pnl", "gross_pnl", "fees", "spread_capture", "inventory_mtm", "turnover")


def case_configs(config):
    """Reuse policy identities; only strategy and inventory coefficient vary."""
    if config.inventory_skew_ticks <= 0:
        raise ValueError("MM-1 requires a positive inventory coefficient")
    if any(getattr(config, k) is not None for k in
           ("volatility_widen_bps", "volatility_reduce_bps", "volatility_stop_bps")):
        raise ValueError("baseline study requires inactive volatility controls")
    if config.signal_expression or config.expression_parameters:
        raise ValueError("baseline study cannot use a signal expression")
    base = variant(config, strategy="fixed_spread", inventory_skew_ticks="0", signal_strength="0")
    return dict(zip(CASES, (base, variant(base, strategy="inventory_skew"),
        variant(base, strategy="inventory_skew", inventory_skew_ticks=config.inventory_skew_ticks))))


def sealed_config(config, window, seal, sample_path, output):
    _, manifest = load_sample(sample_path)
    expected = {"sample_id": window.window_id, "start_ns": window.start_ns,
                "end_ns": window.end_ns, "warmup_ns": window.warmup_ns}
    if (manifest["sample_sha256"] != seal["sample_sha256"]
        or any(manifest["sample"].get(k) != v for k, v in expected.items())
        or manifest["instrument"] != jsonable(config.instrument)
        or manifest.get("acquisition_segments") != seal["acquisition_segments"]
        or manifest.get("source_schema_version") != seal["source_schema_version"]
        or manifest["sample"]["normalization_version"] != seal["normalization_version"]
        or manifest.get("bootstrap_receive_ns") != seal["validation"]["bootstrap_receive_ns"]):
        raise ValueError("Study 1 sample does not match its catalogue seal")
    result = replace(config, sample=SampleSpec(**manifest["sample"]), output_path=Path(output))
    case_configs(result)
    return result


def economic_value(value):
    """Normalize exact numeric representations and the embedded policy name only."""
    if isinstance(value, dict):
        return {k: economic_value(v) for k, v in value.items() if k != "strategy"}
    if isinstance(value, (list, tuple)):
        return [economic_value(v) for v in value
                if not (isinstance(v, (list, tuple)) and len(v) == 2 and v[0] == "strategy")]
    if isinstance(value, str):
        try:
            d = Decimal(value)
        except ArithmeticError:
            return value
        if d.is_finite():
            text = format(d, "f")
            return "0" if d == 0 else text.rstrip("0").rstrip(".") if "." in text else text
    return value


def execution_fingerprint(path):
    """Hash economic/lifecycle evidence, excluding strategy-name trace metadata."""
    path = Path(path)
    files = {}
    for channel in ("orders", "fills", "ledger", "execution", "risk", "timeseries", "markouts"):
        suffix = "csv" if channel in {"timeseries", "markouts"} else "jsonl"
        source = path/f"{channel}.{suffix}.gz"
        h = hashlib.sha256()
        if source.exists():
            with gzip.open(source, "rt", encoding="utf-8", newline="") as stream:
                rows = csv.DictReader(stream) if suffix == "csv" else (json.loads(line) for line in stream)
                for row in rows:
                    h.update((json.dumps(economic_value(row), sort_keys=True, separators=(",", ":"))+"\n").encode())
            files[channel] = h.hexdigest()
        else:
            files[channel] = None
    return files


def review_window(window, seal, configs, paths):
    if tuple(configs) != CASES or len(paths) != len(CASES):
        raise ValueError("Study 1 requires exactly three baseline/control cases")
    records = [run_record(path) for path in paths]
    for label, record in zip(CASES, records):
        if record["config"] != configs[label].resolved() or record["sample_sha256"] != seal["sample_sha256"]:
            raise ValueError("Study 1 run configuration or sample mismatch")
    cards = {row["run_id"]: row for row in build_scorecard(records)}
    if len(cards) != len(CASES) or not all(row["reconciled"] for row in cards.values()):
        raise ValueError("Study 1 requires reconciled evidence for every case")
    zero = (economic_value(records[0]["summary"]) == economic_value(records[1]["summary"])
            and execution_fingerprint(paths[0]) == execution_fingerprint(paths[1]))
    if not zero:
        raise ValueError("zero inventory coefficient does not reproduce MM-0 execution")
    cases = {label: {"output_dir": str(path), "metrics": cards[record["run_id"]],
                      "summary": record["summary"], "config": record["config"],
                      "deterministic_digest": record["manifest"]["deterministic_digest"]}
             for label, record, path in zip(CASES, records, paths)}
    with localcontext() as ctx:
        ctx.prec = 50
        delta = {k: Decimal(str(cases["MM-1"]["metrics"][k]))-
                    Decimal(str(cases["MM-0"]["metrics"][k])) for k in DELTA_METRICS}
    return jsonable({"window_id": window.window_id, "date": window.date,
        "sample_sha256": seal["sample_sha256"], "cases": cases,
        "code_version": records[0]["manifest"]["code_version"],
        "scheduler_identity": records[0]["scheduler_identity"],
        "zero_coefficient_parity": zero, "delta_mm1_minus_mm0": delta,
        "execution_evidence_sufficient": all(cards[r["run_id"]]["comparable"] for r in records)})


def run_window(config, window, seal, sample_path, output):
    config = sealed_config(config, window, seal, sample_path, output)
    configs = case_configs(config)
    baseline = configs["MM-0"]
    changes = [{"strategy": c.strategy, "inventory_skew_ticks": c.inventory_skew_ticks}
               for c in configs.values()]
    comparison, _ = run_sweep(baseline, changes, sample_path)
    paths = json.loads((comparison/"comparison_manifest.json").read_text())["runs"]
    return {"window": review_window(window, seal, configs, paths)}


def summarize(catalogue, rows):
    """Paired reset-window diagnostics. Never synthesize a continuous equity curve."""
    selected = development_windows(catalogue)
    expected = {w.window_id: (w, seal) for w, seal in selected}
    if len(rows) != len(expected) or {r["window_id"] for r in rows} != set(expected):
        raise ValueError("missing or duplicate Study 1 windows")
    settings, codes, schedulers = set(), set(), set()
    for row in rows:
        window, seal = expected[row["window_id"]]
        if row["date"] != window.date or row["sample_sha256"] != seal["sample_sha256"]:
            raise ValueError("Study 1 window provenance mismatch")
        if not row["zero_coefficient_parity"] or set(row["cases"]) != set(CASES):
            raise ValueError("missing baseline control parity")
        baseline = row["cases"]["MM-0"]["config"]
        for label, strategy in zip(CASES, ("fixed_spread", "inventory_skew", "inventory_skew")):
            cfg = row["cases"][label]["config"]
            if (cfg["strategy"] != strategy or
                any(cfg.get(k) != baseline.get(k) for k in set(cfg)|set(baseline)
                    if k not in {"strategy", "inventory_skew_ticks"}) or
                (Decimal(cfg["inventory_skew_ticks"]) <= 0 if label == "MM-1"
                 else Decimal(cfg["inventory_skew_ticks"]) != 0)):
                raise ValueError("Study 1 cases may vary only inventory skew")
        for k in DELTA_METRICS:
            a, b = (Decimal(str(row["cases"][label]["metrics"][k])) for label in ("MM-0", "MM-1"))
            with localcontext() as ctx:
                ctx.prec = 50
                if not a.is_finite() or not b.is_finite() or b-a != Decimal(str(row["delta_mm1_minus_mm0"][k])):
                    raise ValueError("invalid paired metric delta")
        for label in CASES:
            cfg = dict(row["cases"][label]["config"])
            cfg.pop("sample")
            settings.add((label, digest(cfg)))
        codes.add(row["code_version"])
        schedulers.add(row["scheduler_identity"])
    if len(settings) != len(CASES) or len(codes) != 1 or len(schedulers) != 1:
        raise ValueError("multi-day policy, code or scheduler drift")
    def aggregate(members):
        with localcontext() as ctx:
            ctx.prec = 50
            values = {k: [Decimal(str(r["delta_mm1_minus_mm0"][k])) for r in members] for k in DELTA_METRICS}
            return {"windows": [r["window_id"] for r in members],
                "paired_windows": len(members),
                "windows_with_sufficient_execution": sum(r["execution_evidence_sufficient"] for r in members),
                "mean_window_delta": {k: sum(v, Decimal(0))/len(v) for k, v in values.items()},
                "sum_reset_window_delta": {k: sum(values[k], Decimal(0)) for k in ADDITIVE_METRICS},
                "net_pnl_delta_positive_windows": sum(v > 0 for v in values["net_pnl"]),
                "net_pnl_delta_negative_windows": sum(v < 0 for v in values["net_pnl"]),
                "net_pnl_delta_zero_windows": sum(v == 0 for v in values["net_pnl"])}
    ordered = sorted(rows, key=lambda r: r["window_id"])
    return jsonable({"per_window": ordered,
        "per_date": {date: aggregate([r for r in ordered if r["date"] == date]) for date in sorted({r["date"] for r in rows})},
        "pooled": aggregate(ordered), "aggregation": "independent reset-window paired diagnostics",
        "excluded_windows": [r for r in catalogue["windows"] if r.get("quality_status") == "EXCLUDED"],
        "statistical_significance": "NOT_CLAIMED", "paper_eligibility": "INSUFFICIENT_EVIDENCE"})
