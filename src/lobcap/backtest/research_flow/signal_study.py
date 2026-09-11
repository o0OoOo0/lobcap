"""Shared, fail-closed acceptance contract for the fixed DEVELOPMENT study."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
import math
import re

from .sample_catalogue import CATALOGUE_VERSION, DEVELOPMENT, fixed_pilot_windows
from .signal_contracts import (
    CLIENT_FORWARD_RETURN, MARKET_TRUTH_RETURN, POST_LATENCY_RETURN, LabelDefinition,
)


FEATURES = ("microprice_minus_mid_ticks", "imbalance_1", "imbalance_5",
            "vamp_5_minus_mid_ticks", "trade_volume_imbalance_1000ms",
            "bbo_ofi_1000ms", "mid_return_1000ms")
CONTROL_FEATURES = ("mid", "spread_ticks", "bid_top_depth", "ask_top_depth",
                    "total_top_depth", "book_age_ns", "feed_delay_ns")
CACHE_FEATURES = FEATURES + CONTROL_FEATURES
HORIZONS = (100_000_000, 500_000_000, 1_000_000_000, 5_000_000_000)
FEATURE_GROUPS = (FEATURES[:4], FEATURES[4:])
GRID_NS = 100_000_000


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def definitions(entry_latency_ns):
    return tuple(LabelDefinition(kind, horizon,
        entry_latency_ns=entry_latency_ns if kind == POST_LATENCY_RETURN else 0)
        for horizon in HORIZONS
        for kind in (MARKET_TRUTH_RETURN, CLIENT_FORWARD_RETURN, POST_LATENCY_RETURN))


def development_windows(catalogue):
    """Check seals/roles before opening outcomes; retain legitimate exclusions."""
    payload = {k: v for k, v in catalogue.items() if k != "catalogue_sha256"}
    if (payload.get("catalogue_version") != CATALOGUE_VERSION
            or catalogue.get("catalogue_sha256") != digest(payload)):
        raise ValueError("catalogue fingerprint mismatch")
    expected = {w.window_id: w for w in fixed_pilot_windows()}
    rows = catalogue.get("windows", ())
    ids = [r.get("window", {}).get("window_id") for r in rows]
    if len(ids) != len(expected) or set(ids) != set(expected):
        raise ValueError("catalogue must retain every preregistered window exactly once")
    selected = []
    for row in rows:
        window = expected[row["window"]["window_id"]]
        if row["window"] != asdict(window) or row.get("role") != window.role:
            raise ValueError("catalogue window bounds or role changed")
        if row.get("quality_status") == "EXCLUDED":
            if not row.get("exclusion_reason") or row.get("outcomes_access") != "LOCKED":
                raise ValueError("excluded window requires a reason and locked outcomes")
            continue
        seal = {k: v for k, v in row.items() if k != "sealed_manifest_sha256"}
        if row.get("quality_status") != "PASS" or row.get("sealed_manifest_sha256") != digest(seal):
            raise ValueError("window seal mismatch")
        access = "DEVELOPMENT_ONLY" if window.role == DEVELOPMENT else "LOCKED"
        if row.get("outcomes_access") != access:
            raise ValueError("window outcome access mismatch")
        if window.role == DEVELOPMENT:
            selected.append((window, row))
    selected.sort(key=lambda pair: pair[0].start_ns)
    if len({w.date for w, _ in selected}) < 2:
        raise ValueError("two DEVELOPMENT dates with PASS windows are required")
    return tuple(selected)


def bucket_id(feature, fit_dates, edges):
    return "bucket-" + digest({"feature": feature, "fit_dates": sorted(fit_dates),
        "edges": [str(value) for value in edges], "method": "date-crossfit-quintile-v1"})[:16]


def _finite(value):
    try:
        return not isinstance(value, bool) and Decimal(str(value)).is_finite()
    except Exception:
        return False


def validate_report(report, catalogue, freshness_evidence, cache_manifests, memory_limit_mib=512):
    """Validate dimensions, populations and provenance, without promoting signals.

    This does not recompute predictive statistics from raw data. File checksum
    verification belongs to the CLI; acceptance retains that explicit boundary.
    """
    from .labels import freeze_development_freshness
    from .signal_analysis import stability_summary
    from lobcap.backtest.types import jsonable

    if not _finite(memory_limit_mib) or memory_limit_mib <= 0:
        raise ValueError("memory limit must be finite and positive")
    selected = development_windows(catalogue)
    windows = {w.window_id: w for w, _ in selected}
    seals = {w.window_id: seal for w, seal in selected}
    dates = sorted({w.date for w in windows.values()})
    checks = {}
    def check(name, condition):
        checks[name] = bool(condition)

    check("report_pass", report.get("status") == "PASS")
    check("study_version", report.get("study_version") == "signal-development-v1")
    check("catalogue_bound", report.get("catalogue_sha256") == catalogue["catalogue_sha256"])
    check("development_only", report.get("roles_opened") == [DEVELOPMENT]
          and report.get("locked_roles_opened") == [])
    check("claims_withheld", report.get("statistical_significance") == "NOT_CLAIMED"
          and report.get("paper_eligibility") == "INSUFFICIENT_EVIDENCE")
    check("exact_features", tuple(report.get("features", ())) == FEATURES)
    check("exact_horizons", tuple(report.get("label_horizons_ns", ())) == HORIZONS)
    code = report.get("code_fingerprint")
    check("clean_code_fingerprint", isinstance(code, str) and re.fullmatch(r"[0-9a-f]{40}", code))
    latency = report.get("entry_latency_scenario_ms")
    check("entry_latency", type(latency) is int and latency >= 0)
    if not checks["entry_latency"]:
        latency = 0
    keys = {(d.label_kind, d.horizon_ns, d.entry_latency_ns) for d in definitions(latency*1_000_000)}
    caches = report.get("feature_caches", {})
    check("cache_windows", set(caches) == set(windows) == set(cache_manifests))
    for wid, window in windows.items():
        cache, manifest = caches.get(wid, {}), cache_manifests.get(wid, {})
        from .feature_cache import CACHE_VERSION, cache_fingerprint
        from .feature_registry import feature_definition_sha256, FEATURE_SET_VERSION
        expected_identity = {
            "sample_sha256": seals[wid]["sample_sha256"],
            "schedule": {"start_ns": window.start_ns, "end_ns": window.end_ns, "interval_ns": GRID_NS},
            "clock_mode": "recorded", "code_fingerprint": code,
            "normalization_version": seals[wid]["normalization_version"],
            "source_schema_version": seals[wid]["source_schema_version"],
            "acquisition_segment": seals[wid]["acquisition_segments"][0],
            "bootstrap_receive_ns": seals[wid]["validation"]["bootstrap_receive_ns"],
        }
        check("cache_identity:"+wid, manifest.get("status") == "PASS"
            and manifest.get("cache_version") == CACHE_VERSION
            and manifest.get("feature_version") == FEATURE_SET_VERSION
            and manifest.get("feature_definition_sha256") == feature_definition_sha256()
            and all(manifest.get(k) == v for k, v in expected_identity.items())
            and manifest.get("rows") == (window.end_ns-window.start_ns)//GRID_NS
            and cache.get("date") == window.date)
        cfg = manifest.get("feature_config", {})
        check("cache_projection:"+wid, cfg.get("feature_names") == sorted(CACHE_FEATURES)
              and cfg.get("stale_after_ns") == 500_000_000
              and _finite(cfg.get("tick")) and Decimal(str(cfg.get("tick"))) > 0
              and manifest.get("feature_config_sha256") == digest(cfg))
        try:
            from .signal_contracts import AnalysisSchedule
            identity = {k: expected_identity[k] for k in ("bootstrap_receive_ns", "acquisition_segment",
                "normalization_version", "source_schema_version", "code_fingerprint")}
            key = cache_fingerprint(expected_identity["sample_sha256"],
                AnalysisSchedule(**expected_identity["schedule"]), "recorded", 500_000_000,
                tick=cfg["tick"], feature_names=CACHE_FEATURES, **identity)
            check("cache_key:"+wid, cache.get("cache_key") == manifest.get("cache_key") == key)
        except (KeyError, ValueError, TypeError):
            check("cache_key:"+wid, False)
    check("common_tick", len({m.get("feature_config", {}).get("tick") for m in cache_manifests.values()}) == 1)
    try:
        policy, evidence = freeze_development_freshness(freshness_evidence["raw_cadence"], tuple(windows.values()))
        check("freshness_provenance", jsonable(evidence) == freshness_evidence
              and jsonable(policy) == report.get("freshness_policy"))
        check("cadence_dimensions", all(
            len(rows) == len(keys) and {(r["label_kind"], r["horizon_ns"], r["entry_latency_ns"]) for r in rows} == keys
            and all(r["observations"] == (windows[wid].end_ns-windows[wid].start_ns)//GRID_NS
                    and sum(r["statuses"].values()) == r["observations"] for r in rows)
            for wid, rows in evidence["raw_cadence"].items()))
    except (KeyError, TypeError, ValueError):
        check("freshness_provenance", False)
    reports = report.get("reports", {})
    check("exact_scopes", set(reports) == {"per_window", "per_date", "pooled"}
          and set(reports.get("per_window", {})) == set(windows)
          and set(reports.get("per_date", {})) == set(dates))
    scopes = [("window:"+wid, reports.get("per_window", {}).get(wid, {}), [window], window.date)
              for wid, window in windows.items()]
    scopes += [("date:"+date, reports.get("per_date", {}).get(date, {}),
                [w for w in windows.values() if w.date == date], date) for date in dates]
    scopes += [("pooled", reports.get("pooled", {}), list(windows.values()), None)]
    for sid, scope, members, date in scopes:
        check("features:"+sid, set(scope) == set(FEATURES))
        count = sum((w.end_ns-w.start_ns)//GRID_NS for w in members)
        for feature in FEATURES:
            rows = scope.get(feature, ())
            row_keys = [(r.get("label_kind"), r.get("horizon_ns"), r.get("entry_latency_ns")) for r in rows]
            check("labels:"+sid+":"+feature, len(rows) == len(keys) and set(row_keys) == keys)
            for row, key in zip(rows, row_keys):
                name = sid+":"+feature+":"+str(key)
                valid = row.get("valid")
                statuses = row.get("statuses", {})
                population_ok = (type(valid) is int and 0 <= valid <= count
                    and row.get("observations") == count
                    and all(type(n) is int and n >= 0 for n in statuses.values())
                    and sum(statuses.values()) == count and statuses.get("VALID", 0) == valid)
                check("population:"+name, population_ok
                    and row.get("feature_name") == feature
                    and row.get("statistical_significance") == "NOT_CLAIMED"
                    and _finite(row.get("coverage"))
                    and population_ok and math.isclose(row["coverage"], valid/count, abs_tol=1e-12))
                check("correlations:"+name, all(row.get(k) is None or
                    (_finite(row[k]) and -1 <= float(row[k]) <= 1) for k in ("pearson_ic", "spearman_ic")))
                bucket = row.get("bucket_analysis", {})
                edges = bucket.get("edges", [])
                check("bucket_policy:"+name, row.get("bucket_policy_id") == (
                    bucket_id(feature, set(dates)-{date}, edges) if date else None)
                    and (bool(date) or not edges))
                bucket_rows = bucket.get("buckets", [])
                check("bucket_population:"+name, (not edges and bucket.get("status") == "UNAVAILABLE")
                    or (len(edges) == 4 and len(bucket_rows) == 5
                        and all(_finite(v) for v in edges)
                        and sorted(map(Decimal, edges)) == list(map(Decimal, edges))
                        and all(type(b.get("count")) is int and b["count"] >= 0 for b in bucket_rows)
                        and sum(b["count"] for b in bucket_rows) == valid))
    # Population pooling must conserve each censor/status class, not just total rows.
    try:
        for wid, window in windows.items():
            for feature in FEATURES:
                for row, date_row in zip(reports["per_window"][wid][feature], reports["per_date"][window.date][feature]):
                    check("crossfit_partition:"+wid+":"+feature+":"+str(row["horizon_ns"])+row["label_kind"],
                          row["bucket_policy_id"] == date_row["bucket_policy_id"]
                          and row["bucket_analysis"]["edges"] == date_row["bucket_analysis"]["edges"])
        for sid, scope, members, _ in scopes:
            if sid.startswith("window:"):
                continue
            for feature in FEATURES:
                for index, row in enumerate(scope[feature]):
                    parts = [reports["per_window"][w.window_id][feature][index] for w in members]
                    statuses = Counter()
                    for part in parts:
                        statuses.update(part["statuses"])
                    check("pooled_population:"+sid+":"+feature+":"+str(index),
                        row["valid"] == sum(p["valid"] for p in parts) and row["statuses"] == dict(statuses))
        check("stability_recomputed", report.get("stability") == jsonable(stability_summary(reports["per_date"])))
    except (KeyError, IndexError, TypeError, ValueError):
        check("pooled_and_stability_complete", False)
    redundancy = report.get("redundancy", ())
    pairs = {(a, b) for i, a in enumerate(FEATURES) for b in FEATURES[i+1:]}
    total = sum((w.end_ns-w.start_ns)//GRID_NS for w in windows.values())
    check("redundancy_pairs", len(redundancy) == len(pairs)
          and {(r.get("left"), r.get("right")) for r in redundancy} == pairs)
    check("redundancy_coverage", all(type(r.get("overlap")) is int and 0 <= r["overlap"] <= total
          and _finite(r.get("coverage_overlap")) and math.isclose(r["coverage_overlap"], r["overlap"]/total)
          for r in redundancy))
    expected_phases = Counter((phase, wid) for wid in windows for phase in ("cache_write", "cadence"))
    expected_phases.update(("feature_group", i) for i in range(1, len(FEATURE_GROUPS)+1))
    expected_phases[("redundancy", "pooled")] = 1
    phases = report.get("phase_performance", ())
    peaks = [r.get("peak_rss_mib") for r in phases]
    check("exact_phases", Counter((r.get("phase"), r.get("id")) for r in phases) == expected_phases)
    check("feature_phase_groups", all(tuple(r.get("features", ())) == FEATURE_GROUPS[r["id"]-1]
          for r in phases if r.get("phase") == "feature_group" and type(r.get("id")) is int and 1 <= r["id"] <= len(FEATURE_GROUPS)))
    check("measured_memory", bool(peaks) and all(_finite(p) and 0 < float(p) < memory_limit_mib for p in peaks))
    failed = sorted(k for k, passed in checks.items() if not passed)
    result = {"status": "FAIL" if failed else "PASS", "validation_version": "signal-development-acceptance-v2",
        "checks": checks, "failed": failed, "phase_count": len(phases),
        "development_windows": sorted(windows), "development_dates": dates,
        "maximum_phase_rss_mib": max(map(float, peaks)) if peaks and all(_finite(p) for p in peaks) else None,
        "scope": "artifact integrity and report consistency; predictive statistics are not independently recomputed"}
    if failed:
        raise ValueError(json.dumps(result, sort_keys=True))
    return result
