"""Study 2 B1 movement, excursion, and existing-order exposure research."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from contextlib import ExitStack
from decimal import Decimal, ROUND_CEILING, localcontext
import gzip
import json
from pathlib import Path

from lobcap.backtest.replay import iter_events, load_sample
from lobcap.backtest.types import jsonable
from lobcap.config.common import sha256_file
from .feature_cache import iter_feature_snapshots
from .labels import apply_freshness_policy, measure_label, truth_midpoint_series
from .signal_analysis import _quantile
from .signal_contracts import AnalysisSchedule, CLIENT_FORWARD_RETURN, LabelDefinition, VALID
from .strategy_compare import build_scorecard
from .study1 import run_record, sealed_config


VERSION = "study2-movement-excursion-v1"
REGIME_VERSION = "abs-client-mid-return-1000ms-tertiles-v1"
GRID_NS = 100_000_000
HORIZONS = (100_000_000, 500_000_000, 1_000_000_000, 5_000_000_000)
QUANTILES = (Decimal(".5"), Decimal(".75"), Decimal(".9"), Decimal(".95"),
             Decimal(".97"), Decimal(".99"))


def _gzip_writer(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.open("xb")
    return raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0)


def snapshots(config, sample_path):
    initial, manifest = load_sample(sample_path, config)
    schedule = AnalysisSchedule(config.sample.start_ns, config.sample.end_ns, GRID_NS)
    return iter_feature_snapshots(initial, lambda: iter_events(sample_path), schedule,
        config.instrument.tick, clock_mode="recorded",
        bootstrap_receive_ns=manifest["bootstrap_receive_ns"],
        stale_after_ns=config.stale_book_ms*1_000_000,
        acquisition_segment=manifest.get("acquisition_segments", ["segment-1"])[0],
        feature_names=("mid_return_1000ms",))


def regime_input_window(config, sample_path, artifact, write=True):
    """Persist only causal trailing inputs before future movement is measured."""
    artifact = Path(artifact)
    statuses = Counter(); count = available = 0; total = Decimal(0)
    with ExitStack() as stack, localcontext() as ctx:
        ctx.prec = 50
        if write:
            raw, output = _gzip_writer(artifact)
            stack.enter_context(raw); stack.enter_context(output)
        else:
            stored = iter(stack.enter_context(gzip.open(artifact, "rt", encoding="utf-8")))
        for snapshot in snapshots(config, sample_path):
            point = snapshot.point("mid_return_1000ms")
            value = abs(point.value) if point.status == "AVAILABLE" else None
            row = jsonable({"analysis_time_ns": snapshot.analysis_time_ns,
                "feature": "abs(mid_return_1000ms)", "feature_version": point.feature_version,
                "unit": "bps", "status": point.status, "value": value,
                "client_available_time_ns": point.client_available_time_ns,
                "source_exchange_time_ns": point.latest_source_exchange_time_ns,
                "source_receive_time_ns": point.latest_source_receive_time_ns,
                "source_refs": point.source_refs})
            if write:
                output.write((json.dumps(row, sort_keys=True)+"\n").encode())
            elif json.loads(next(stored, "null")) != row:
                raise ValueError("Study 2 regime input differs from causal replay")
            count += 1; statuses[point.status] += 1
            if value is not None:
                available += 1; total += value
        if not write and next(stored, None) is not None:
            raise ValueError("extra Study 2 regime input rows")
    if not count or not available:
        raise ValueError("Study 2 regime input has no available observations")
    return jsonable({"status": "PASS", "version": REGIME_VERSION,
        "observations": count, "available": available, "sum": total,
        "feature_statuses": dict(statuses),
        "artifact_sha256": sha256_file(artifact) if artifact.exists() else None})


def regime_input_phase(config, window, seal, sample_path, output):
    sealed = sealed_config(config, window, seal, sample_path, output, "vpin")
    artifact = Path(output)/"regime_inputs"/(window.window_id+".jsonl.gz")
    row = regime_input_window(sealed, sample_path, artifact)
    row.update(window_id=window.window_id, date=window.date,
        sample_sha256=seal["sample_sha256"])
    return {"window": row}


def _regime_values(paths):
    values = []
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row["status"] == "AVAILABLE":
                    values.append(Decimal(row["value"]))
    return values


def fit_regime_edges(paths):
    values = _regime_values(paths)
    unique = sorted(set(values))
    if len(unique) < 3:
        raise ValueError("regime fit requires at least three distinct causal values")
    edges = (_quantile(values, Decimal(1)/Decimal(3)),
             _quantile(values, Decimal(2)/Decimal(3)))
    if edges[0] >= edges[1]:
        edges = (unique[(len(unique)-1)//3], unique[2*(len(unique)-1)//3])
    if edges[0] >= edges[1]:
        raise ValueError("regime thresholds are not distinct")
    counts = Counter("LOW" if value <= edges[0] else "NORMAL" if value <= edges[1]
                     else "HIGH" for value in values)
    if any(not counts[name] for name in ("LOW", "NORMAL", "HIGH")):
        raise ValueError("fitted regime contains an empty bucket")
    return jsonable({"status": "PASS", "version": REGIME_VERSION,
        "edges_bps": edges, "counts": dict(counts), "observations": len(values),
        "fit_scope": "causal trailing inputs from sealed DEVELOPMENT only",
        "future_movement_used": False})


def regime_name(value, edges):
    if value is None:
        return "UNAVAILABLE"
    low, high = (Decimal(str(edge)) for edge in edges)
    return "LOW" if value <= low else "NORMAL" if value <= high else "HIGH"


def _excursion(label, truth, decision_ns, tick):
    if label.status != VALID:
        return {"status": label.status, "final_displacement_ticks": None,
            "absolute_displacement_ticks": None, "up_excursion_ticks": None,
            "down_excursion_ticks": None, "maximum_absolute_excursion_ticks": None,
            "target_age_ns": label.target_age_ns}
    left = max(0, bisect_right(truth.times, decision_ns)-1)
    right = bisect_right(truth.times, label.target_requested_ns)
    if left >= right or not all(truth.coverage[left:right]):
        return {"status": "CENSORED_UNKNOWN_COVERAGE", "final_displacement_ticks": None,
            "absolute_displacement_ticks": None, "up_excursion_ticks": None,
            "down_excursion_ticks": None, "maximum_absolute_excursion_ticks": None,
            "target_age_ns": label.target_age_ns}
    anchor = label.anchor_mid
    moves = [(row[1]-anchor)/tick for row in truth.rows[left:right]]
    displacement = label.value
    up = max([Decimal(0), *moves]); down = max([Decimal(0), *(-move for move in moves)])
    return {"status": VALID, "final_displacement_ticks": displacement,
        "absolute_displacement_ticks": abs(displacement), "up_excursion_ticks": up,
        "down_excursion_ticks": down, "maximum_absolute_excursion_ticks": max(up, down),
        "target_age_ns": label.target_age_ns}


def movement_window(config, sample_path, freshness, regime_edges, artifact, write=True):
    initial, manifest = load_sample(sample_path, config)
    segment = manifest.get("acquisition_segments", ["segment-1"])[0]
    truth = truth_midpoint_series(initial, iter_events(sample_path), segment)
    definitions = tuple(LabelDefinition(CLIENT_FORWARD_RETURN, horizon) for horizon in HORIZONS)
    statuses = {str(horizon): Counter() for horizon in HORIZONS}
    last_nonoverlap = {horizon: None for horizon in HORIZONS}
    count = 0; regimes = Counter(); artifact = Path(artifact)
    with ExitStack() as stack:
        if write:
            raw, output = _gzip_writer(artifact)
            stack.enter_context(raw); stack.enter_context(output)
        else:
            stored = iter(stack.enter_context(gzip.open(artifact, "rt", encoding="utf-8")))
        for snapshot in snapshots(config, sample_path):
            point = snapshot.point("mid_return_1000ms")
            regime_value = abs(point.value) if point.status == "AVAILABLE" else None
            regime = regime_name(regime_value, regime_edges); regimes[regime] += 1
            outcomes = {}
            for definition in definitions:
                label = measure_label(definition, snapshot.analysis_time_ns, snapshot.client_mid,
                    snapshot.client_mid_source_exchange_time_ns, truth, config.instrument.tick,
                    config.sample.end_ns, segment, segment, snapshot.client_mid_coverage)
                label = apply_freshness_policy(label, freshness)
                result = _excursion(label, truth, snapshot.analysis_time_ns, config.instrument.tick)
                horizon = definition.horizon_ns
                nonoverlap = (result["status"] == VALID and (last_nonoverlap[horizon] is None
                    or snapshot.analysis_time_ns-last_nonoverlap[horizon] >= horizon))
                if nonoverlap:
                    last_nonoverlap[horizon] = snapshot.analysis_time_ns
                result["non_overlapping"] = nonoverlap
                outcomes[str(horizon)] = result
                statuses[str(horizon)][result["status"]] += 1
            row = jsonable({"analysis_time_ns": snapshot.analysis_time_ns,
                "client_mid": snapshot.client_mid,
                "client_mid_source_exchange_time_ns": snapshot.client_mid_source_exchange_time_ns,
                "client_mid_source_receive_time_ns": snapshot.client_mid_source_receive_time_ns,
                "client_mid_coverage": snapshot.client_mid_coverage,
                "regime_feature_value": regime_value, "regime": regime,
                "regime_feature_unit": "bps", "regime_feature_status": point.status,
                "outcomes": outcomes})
            if write:
                output.write((json.dumps(row, sort_keys=True)+"\n").encode())
            elif json.loads(next(stored, "null")) != row:
                raise ValueError("Study 2 movement artifact differs from source replay")
            count += 1
        if not write and next(stored, None) is not None:
            raise ValueError("extra Study 2 movement artifact rows")
    if not count or any(not statuses[str(horizon)][VALID] for horizon in HORIZONS):
        raise ValueError("Study 2 movement window has no valid horizon observations")
    return {"status": "PASS", "version": VERSION, "sample_sha256": manifest["sample_sha256"],
        "observations": count, "regime_counts": dict(regimes),
        "horizon_statuses": {key: dict(value) for key, value in statuses.items()},
        "artifact_sha256": sha256_file(artifact)}


def movement_phase(config, window, seal, sample_path, freshness_row, regime_edges, output):
    from .signal_contracts import FreshnessPolicy
    sealed = sealed_config(config, window, seal, sample_path, output, "vpin")
    freshness = FreshnessPolicy(freshness_row["policy_id"],
        tuple(tuple(row) for row in freshness_row["maximum_age_by_horizon_ns"]))
    artifact = Path(output)/"movement"/(window.window_id+".jsonl.gz")
    row = movement_window(sealed, sample_path, freshness, regime_edges, artifact)
    row.update(window_id=window.window_id, date=window.date)
    return {"window": row}


def _stats(values):
    if not values:
        return {"count": 0, "mean": None, "std": None, "minimum": None,
            "maximum": None, "quantiles": {str(q): None for q in QUANTILES}}
    with localcontext() as ctx:
        ctx.prec = 50
        ordered = sorted(values)
        mean = sum(values, Decimal(0))/len(values)
        variance = sum(((value-mean)**2 for value in values), Decimal(0))/len(values)
        def quantile(fraction):
            position = Decimal(len(ordered)-1)*fraction
            low = int(position); high = int(position.to_integral_value(rounding=ROUND_CEILING))
            if low == high:
                return ordered[low]
            weight = position-low
            return ordered[low]*(Decimal(1)-weight)+ordered[high]*weight
        return jsonable({"count": len(values), "mean": mean, "std": variance.sqrt(),
            "minimum": min(values), "maximum": max(values),
            "quantiles": {str(q): quantile(q) for q in QUANTILES}})


def _empty_movement_group():
    fields = ("final_displacement_ticks", "absolute_displacement_ticks",
        "up_excursion_ticks", "down_excursion_ticks", "maximum_absolute_excursion_ticks")
    return {str(horizon): {scope: {field: [] for field in fields}
        for scope in ("overlapping", "non_overlapping")} for horizon in HORIZONS}


def _parsed_movement(row):
    parsed = []
    for horizon, outcome in row["outcomes"].items():
        if outcome["status"] != VALID:
            continue
        parsed.append((horizon, outcome["non_overlapping"],
            {field: Decimal(outcome[field]) for field in
             ("final_displacement_ticks", "absolute_displacement_ticks",
              "up_excursion_ticks", "down_excursion_ticks",
              "maximum_absolute_excursion_ticks")}))
    return parsed


def _add_movement(grouped, parsed):
    for horizon, non_overlapping, values in parsed:
        for field, value in values.items():
            grouped[horizon]["overlapping"][field].append(value)
            if non_overlapping:
                grouped[horizon]["non_overlapping"][field].append(value)


def _finish_movement_group(grouped):
    return {horizon: {scope: {field: _stats(values) for field, values in fields.items()}
        for scope, fields in scopes.items()} for horizon, scopes in grouped.items()}


def aggregate_movement(artifacts):
    pooled = _empty_movement_group(); dates = {}; regimes = {
        name: _empty_movement_group() for name in ("LOW", "NORMAL", "HIGH")}
    for date, path in artifacts:
        dates.setdefault(date, _empty_movement_group())
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                parsed = _parsed_movement(row)
                _add_movement(pooled, parsed); _add_movement(dates[date], parsed)
                if row["regime"] != "UNAVAILABLE":
                    _add_movement(regimes[row["regime"]], parsed)
    return jsonable({"pooled": _finish_movement_group(pooled),
        "per_date": {date: _finish_movement_group(group)
            for date, group in sorted(dates.items())},
        "per_regime": {name: _finish_movement_group(group)
            for name, group in regimes.items()},
        "overlap_warning": "overlapping 100ms-grid rows are descriptive; spacing uses non-overlapping rows",
        "statistical_significance": "NOT_CLAIMED"})


def aggregate_movement_phase(artifacts):
    return {"summary": aggregate_movement(artifacts)}


def order_lifetime_window(path):
    path = Path(path); source = path/"orders.jsonl.gz"
    first_fill = {}; activations = {}; active = []; submitted = []; replacement = []
    pending_replacement = {}; terminals = Counter(); orders = 0
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line); event = row["event"]; oid = row["order_id"]
            if event == "submit": orders += 1
            if event in {"partial_fill", "full_fill"}:
                first_fill.setdefault(oid, row["time_ns"])
            if event == "activate":
                activations[oid] = row["time_ns"]
                if row["side"] in pending_replacement:
                    replacement.append(row["time_ns"]-pending_replacement.pop(row["side"]))
            if event == "terminal":
                terminals[row["status"]] += 1
                if row["activation_ns"] is not None:
                    active.append(row["time_ns"]-row["activation_ns"])
                    submitted.append(row["time_ns"]-row["submitted_ns"])
                if row["reason"] in {"replace", "target_size_changed"}:
                    pending_replacement.setdefault(row["side"], row["time_ns"])
    if not orders or not active:
        raise ValueError("MM-1 order artifact has no active lifetime evidence")
    time_to_first = [time-activations[oid] for oid, time in first_fill.items()
        if oid in activations]
    return jsonable({"orders": orders, "terminal_statuses": dict(terminals),
        "active_lifetime_ns": active, "submitted_to_terminal_ns": submitted,
        "time_to_first_fill_ns": time_to_first, "replacement_downtime_ns": replacement,
        "unresolved_replacement_intervals": len(pending_replacement),
        "orders_sha256": sha256_file(source)})


def aggregate_lifetimes(rows):
    fields = ("active_lifetime_ns", "submitted_to_terminal_ns", "time_to_first_fill_ns",
        "replacement_downtime_ns")
    result = {field: _stats([Decimal(value) for row in rows for value in row[field]])
        for field in fields}
    result.update(windows=len(rows), orders=sum(row["orders"] for row in rows),
        unresolved_replacement_intervals=sum(row["unresolved_replacement_intervals"] for row in rows))
    return jsonable(result)


def study1_lifetime_rows(report_path, selected):
    """Reuse accepted MM-1 order artifacts instead of rerunning the simulator."""
    report_path = Path(report_path).resolve(); root = report_path.parent
    report = json.loads(report_path.read_text(encoding="utf-8"))
    protocol = report.get("protocol", {})
    if (report.get("status") != "PASS" or protocol.get("version") != "study1-vpin-v1"
        or protocol.get("roles_opened") != ["DEVELOPMENT"]
        or protocol.get("locked_roles_opened") != []):
        raise ValueError("Study 2 requires the accepted DEVELOPMENT-only VPIN Study 1 report")
    reported = report.get("results", {}).get("per_window", [])
    expected = {window.window_id: (window, seal) for window, seal in selected}
    if len(reported) != len(expected) or {row["window_id"] for row in reported} != set(expected):
        raise ValueError("Study 1 lifetime source window mismatch")
    rows = []
    for row in reported:
        window, seal = expected[row["window_id"]]
        path = Path(row["cases"]["MM-1"]["output_dir"]).resolve()
        if not path.is_relative_to(root/window.window_id/"runs"):
            raise ValueError("MM-1 lifetime source escaped accepted Study 1 root")
        record = run_record(path)
        cards = build_scorecard([record])
        recorded_order_hashes = [item.get("sha256")
            for item in record["manifest"].get("output_artifact_paths", [])
            if Path(item.get("path", "")).name == "orders.jsonl.gz"]
        if (not record["rankable"] or record["sample_sha256"] != seal["sample_sha256"]
            or record["config"].get("strategy") != "inventory_skew"
            or len(cards) != 1 or not cards[0]["reconciled"]
            or len(recorded_order_hashes) != 1
            or sha256_file(path/"orders.jsonl.gz") != recorded_order_hashes[0]):
            raise ValueError("MM-1 lifetime source is not reconciled accepted evidence")
        lifetime = order_lifetime_window(path)
        lifetime.update(window_id=window.window_id, date=window.date,
            sample_sha256=seal["sample_sha256"], output_dir=str(path),
            run_code_version=record["manifest"]["code_version"])
        rows.append(lifetime)
    rows.sort(key=lambda row: row["window_id"])
    return rows


def spacing_candidates(movement, lifetimes):
    median = Decimal(lifetimes["active_lifetime_ns"]["quantiles"]["0.5"])
    horizon = min(HORIZONS, key=lambda value: (abs(Decimal(value)-median), value))
    def spacing(group):
        q = group[str(horizon)]["non_overlapping"]["maximum_absolute_excursion_ticks"]["quantiles"]
        raw = {key: q[key] for key in ("0.5", "0.75", "0.9", "0.97")}
        levels = []
        for value in raw.values():
            if value is None:
                continue
            level = max(1, int(Decimal(value).to_integral_value(rounding=ROUND_CEILING)))
            if level not in levels:
                levels.append(level)
        return {"raw_quantile_ticks": raw, "ceil_unique_ticks": levels,
            "levels_available": len(levels)}
    return jsonable({"selected_horizon_ns": horizon,
        "selection_rule": "available horizon nearest pooled median active MM-1 order lifetime",
        "median_active_order_lifetime_ns": median,
        "pooled": spacing(movement["pooled"]),
        "per_regime": {name: spacing(group) for name, group in movement["per_regime"].items()},
        "strategy_status": "RESEARCH_GEOMETRY_ONLY_MULTI_ORDER_NOT_IMPLEMENTED"})
