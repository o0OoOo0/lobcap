"""Corruption and role-boundary probes for DEVELOPMENT acceptance."""
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal as D

import pytest

from lobcap.backtest.research_flow.feature_cache import _cache_identity, cache_fingerprint
from lobcap.backtest.research_flow.labels import freeze_development_freshness
from lobcap.backtest.research_flow.sample_catalogue import (
    DEVELOPMENT, excluded_window, fixed_pilot_windows, seal_catalogue,
)
from lobcap.backtest.research_flow.signal_analysis import stability_summary
from lobcap.backtest.research_flow.signal_contracts import AnalysisSchedule
from lobcap.backtest.research_flow.signal_study import (
    FEATURES, CACHE_FEATURES, HORIZONS, FEATURE_GROUPS, bucket_id,
    definitions, development_windows, digest, validate_report,
)
from lobcap.backtest.types import jsonable


def catalogue(exclude=()):
    rows = []
    for w in fixed_pilot_windows():
        if w.window_id in exclude:
            rows.append(excluded_window(w, "quality gate failed"))
            continue
        row = {"window": asdict(w), "role": w.role, "quality_status": "PASS",
            "outcomes_access": "DEVELOPMENT_ONLY" if w.role == DEVELOPMENT else "LOCKED",
            "sample_sha256": digest(w.window_id), "normalization_version": "fixture-normalization",
            "source_schema_version": "fixture-schema", "acquisition_segments": [w.window_id],
            "validation": {"bootstrap_receive_ns": w.start_ns-w.warmup_ns}}
        row["sealed_manifest_sha256"] = digest(row)
        rows.append(row)
    return jsonable(seal_catalogue(rows))


def evidence(exclude=()):
    cat = catalogue(exclude)
    selected = development_windows(cat)
    dates = sorted({w.date for w, _ in selected})
    caches, manifests, cadence = {}, {}, {}
    for w, seal in selected:
        schedule = AnalysisSchedule(w.start_ns, w.end_ns)
        identity = dict(tick=D(".1"), bootstrap_receive_ns=seal["validation"]["bootstrap_receive_ns"],
            acquisition_segment=w.window_id, normalization_version="fixture-normalization",
            source_schema_version="fixture-schema", code_fingerprint="a"*40, feature_names=CACHE_FEATURES)
        key = cache_fingerprint(seal["sample_sha256"], schedule, "recorded", 500_000_000, **identity)
        manifests[w.window_id] = {"status": "PASS", "rows": 12000, "cache_key": key,
            **_cache_identity(seal["sample_sha256"], schedule, "recorded", 500_000_000, **identity)}
        caches[w.window_id] = {"date": w.date, "cache_key": key}
        cadence[w.window_id] = [{"label_kind": d.label_kind, "horizon_ns": d.horizon_ns,
            "entry_latency_ns": d.entry_latency_ns, "observations": 12000, "statuses": {"OBSERVED": 12000},
            "anchor_age_distribution": {"p95_ns": 10}, "target_age_distribution": {"p95_ns": 10}}
            for d in definitions(10_000_000)]
    policy, freshness = freeze_development_freshness(cadence, tuple(w for w, _ in selected))
    def scope(count, date):
        result = {}
        for feature in FEATURES:
            result[feature] = [{"feature_name": feature, "label_kind": d.label_kind,
                "horizon_ns": d.horizon_ns, "entry_latency_ns": d.entry_latency_ns,
                "observations": count, "valid": count, "coverage": 1.0, "statuses": {"VALID": count},
                "pearson_ic": .1, "spearman_ic": .2, "statistical_significance": "NOT_CLAIMED",
                "bucket_policy_id": bucket_id(feature, set(dates)-{date}, ()) if date else None,
                "bucket_analysis": {"status": "UNAVAILABLE", "edges": [], "buckets": [], "monotonic_direction": None}}
                for d in definitions(10_000_000)]
        return result
    reports = {"per_window": {w.window_id: scope(12000, w.date) for w, _ in selected},
        "per_date": {date: scope(sum(12000 for w, _ in selected if w.date == date), date) for date in dates},
        "pooled": scope(len(selected)*12000, None)}
    phases = [{"phase": p, "id": w.window_id, "peak_rss_mib": 100}
              for w, _ in selected for p in ("cache_write", "cadence")]
    phases += [{"phase": "feature_group", "id": i, "features": group, "peak_rss_mib": 200}
               for i, group in enumerate(FEATURE_GROUPS, 1)]
    phases += [{"phase": "redundancy", "id": "pooled", "peak_rss_mib": 100}]
    report = {"status": "PASS", "study_version": "signal-development-v1",
        "catalogue_sha256": cat["catalogue_sha256"], "roles_opened": [DEVELOPMENT],
        "locked_roles_opened": [], "statistical_significance": "NOT_CLAIMED",
        "paper_eligibility": "INSUFFICIENT_EVIDENCE", "features": FEATURES, "label_horizons_ns": HORIZONS,
        "code_fingerprint": "a"*40, "entry_latency_scenario_ms": 10, "feature_caches": caches,
        "freshness_policy": policy, "reports": reports, "stability": stability_summary(reports["per_date"]),
        "redundancy": [{"left": a, "right": b, "overlap": 12000*len(selected), "coverage_overlap": 1.0}
                       for i, a in enumerate(FEATURES) for b in FEATURES[i+1:]], "phase_performance": phases}
    return jsonable(report), cat, jsonable(freshness), jsonable(manifests)


def test_valid_report_accepts_exact_pass_windows_including_exclusions():
    for exclusions in ((), (fixed_pilot_windows()[0].window_id,)):
        result = validate_report(*evidence(exclusions))
        assert result["status"] == "PASS"
        assert len(result["development_windows"]) == 8-len(exclusions)


@pytest.mark.parametrize("mutation,match", [
    (lambda r: r["features"].__setitem__(0, "not-preregistered"), "exact_features"),
    (lambda r: r["label_horizons_ns"].__setitem__(0, 123), "exact_horizons"),
    (lambda r: r.__setitem__("code_fingerprint", "a"*40+"-dirty"), "clean_code"),
    (lambda r: r["locked_roles_opened"].append("PILOT_OOS_LOCKED"), "development_only"),
    (lambda r: r["phase_performance"].pop(), "exact_phases"),
    (lambda r: r["phase_performance"][0].__setitem__("peak_rss_mib", None), "measured_memory"),
    (lambda r: r["phase_performance"][0].__setitem__("peak_rss_mib", float("nan")), "measured_memory"),
    (lambda r: r["phase_performance"][0].__setitem__("peak_rss_mib", 512), "measured_memory"),
    (lambda r: r["reports"]["pooled"][FEATURES[0]][0].__setitem__("coverage", .5), "population"),
    (lambda r: r["reports"]["pooled"][FEATURES[0]].__setitem__(0, deepcopy(r["reports"]["pooled"][FEATURES[0]][1])), "labels:"),
    (lambda r: r["stability"].pop(), "stability_recomputed"),
    (lambda r: r["redundancy"].pop(), "redundancy_pairs"),
])
def test_report_corruption_fails_closed(mutation, match):
    args = evidence()
    mutation(args[0])
    with pytest.raises(ValueError, match=match):
        validate_report(*args)


def test_catalogue_cannot_relabel_holdout_even_with_recomputed_hashes():
    cat = catalogue()
    row = cat["windows"][8]
    row["window"]["role"] = row["role"] = DEVELOPMENT
    row["outcomes_access"] = "DEVELOPMENT_ONLY"
    row["sealed_manifest_sha256"] = digest({k: v for k, v in row.items() if k != "sealed_manifest_sha256"})
    cat["catalogue_sha256"] = digest({k: v for k, v in cat.items() if k != "catalogue_sha256"})
    with pytest.raises(ValueError, match="role changed"):
        development_windows(cat)


def test_cadence_and_cache_provenance_are_bound_to_report():
    report, cat, freshness, manifests = evidence()
    freshness["raw_cadence"][next(iter(freshness["raw_cadence"]))][0]["anchor_age_distribution"]["p95_ns"] = 99
    with pytest.raises(ValueError, match="freshness_provenance"):
        validate_report(report, cat, freshness, manifests)
    report, cat, freshness, manifests = evidence()
    manifests[next(iter(manifests))]["code_fingerprint"] = "b"*40
    with pytest.raises(ValueError, match="cache_identity"):
        validate_report(report, cat, freshness, manifests)
