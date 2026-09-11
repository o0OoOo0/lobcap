from decimal import Decimal as D
from pathlib import Path
import pytest

from lobcap.backtest.research_flow.labels import MidpointSeries
from lobcap.backtest.research_flow.signal_analysis import (
    evaluate_feature, evaluate_feature_group, evaluate_redundancy,
    evaluate_redundancy_values, fit_bucket_edges, stability_summary,
)
from lobcap.backtest.research_flow.signal_contracts import (
    CLIENT_FORWARD_RETURN, MARKET_TRUTH_RETURN, POST_LATENCY_RETURN,
    FeaturePoint, FeatureSnapshot, FreshnessPolicy, LabelDefinition,
)
from lobcap.config.experiment import ROOT


def snapshot(t, signal, client_mid, source_time):
    point = FeaturePoint("signal", "v1", D(signal), "ticks", "AVAILABLE", t, t,
                         source_time, source_time, (f"d:{t}",))
    return FeatureSnapshot(t, D(client_mid), source_time, source_time, t, "a", (point,))


def test_three_label_families_are_reported_separately():
    snapshots = (snapshot(100, "1", "100", 90), snapshot(200, "2", "101", 190))
    truth = MidpointSeries(((0, "99", "a"), (90, "100", "a"), (190, "101", "a"),
                            (290, "103", "a"), (390, "106", "a")))
    definitions = (
        LabelDefinition(MARKET_TRUTH_RETURN, 100),
        LabelDefinition(CLIENT_FORWARD_RETURN, 100),
        LabelDefinition(POST_LATENCY_RETURN, 100, entry_latency_ns=100),
    )
    policy = FreshnessPolicy("fixture", ((100, 20, 20),))
    rows = evaluate_feature("signal", snapshots, truth, definitions, policy, D(1), 500)
    assert [row["label_kind"] for row in rows] == [MARKET_TRUTH_RETURN, CLIENT_FORWARD_RETURN, POST_LATENCY_RETURN]
    assert all(row["valid"] == 2 for row in rows)
    assert rows[0]["pearson_ic"] == 1
    assert rows[1]["target_age_distribution"]["maximum_ns"] == 10
    assert rows[2]["entry_latency_ns"] == 100
    assert rows[0]["statistical_significance"] == "NOT_CLAIMED"
    assert rows[0]["non_overlapping"]["observations"] == 2


def test_frozen_buckets_and_redundancy_are_explicit():
    samples = tuple(snapshot(index*100, str(index), str(100+index), index*100)
                    for index in range(1, 7))
    edges = fit_bucket_edges(samples, "signal")
    assert len(edges) == 4
    truth = MidpointSeries(tuple((index*100, str(100+index), "a") for index in range(9)))
    policy = FreshnessPolicy("fixture", ((100, 100, 100),))
    row = evaluate_feature("signal", samples, truth,
        (LabelDefinition(MARKET_TRUTH_RETURN, 100),), policy, D(1), 900,
        edges, "development-crossfit-v1")[0]
    assert row["bucket_policy_id"] == "development-crossfit-v1"
    assert row["bucket_analysis"]["status"] in {"VALID", "SPARSE"}
    duplicate = tuple(FeatureSnapshot(s.analysis_time_ns, s.client_mid,
        s.client_mid_source_exchange_time_ns, s.client_mid_source_receive_time_ns,
        s.client_mid_available_time_ns, s.acquisition_segment,
        s.points + (FeaturePoint("copy", "v1", s.point("signal").value, "ticks", "AVAILABLE",
            s.analysis_time_ns, s.analysis_time_ns, s.analysis_time_ns, s.analysis_time_ns, ()),))
        for s in samples)
    assert evaluate_redundancy(duplicate, ("signal", "copy"))[0]["pearson"] == 1
    compact = tuple((s.point("signal").value, s.point("signal").value) for s in samples)
    assert evaluate_redundancy_values(compact, ("signal", "copy"))[0]["spearman"] == 1


def test_grouped_report_pools_independent_window_truth_without_joining_them():
    first = (snapshot(100, "1", "100", 90), snapshot(200, "2", "101", 190))
    second = (snapshot(1100, "3", "200", 1090), snapshot(1200, "4", "201", 1190))
    truth_a = MidpointSeries(((0, "99", "a"), (90, "100", "a"), (190, "101", "a"),
                              (290, "103", "a"), (390, "104", "a")))
    truth_b = MidpointSeries(((1000, "199", "a"), (1090, "200", "a"),
                              (1190, "201", "a"), (1290, "205", "a"), (1390, "206", "a")))
    policy = FreshnessPolicy("fixture", ((100, 20, 20),))
    row = evaluate_feature_group("signal", ((first, truth_a, 500), (second, truth_b, 1500)),
        (LabelDefinition(MARKET_TRUTH_RETURN, 100),), policy, D(1))[0]
    assert row["observations"] == row["valid"] == 4
    assert row["coverage"] == 1


def test_stability_requires_two_dates_and_reports_direction_without_p_values():
    base = {"label_kind": MARKET_TRUTH_RETURN, "horizon_ns": 100,
            "entry_latency_ns": 0, "pearson_ic": .2, "spearman_ic": .1,
            "coverage": .8, "bucket_analysis": {"monotonic_direction": "NONDECREASING"}}
    reports = {"2026-09-07": {"signal": (base,)},
               "2026-09-08": {"signal": ({**base, "coverage": .7},)}}
    row = stability_summary(reports)[0]
    assert row["pearson_direction_consistent"]
    assert row["bucket_monotonic_consistent"]
    assert row["coverage_min"] == .7
    assert row["statistical_significance"] == "NOT_CLAIMED"
    with pytest.raises(ValueError, match="two dates"):
        stability_summary({"2026-09-07": reports["2026-09-07"]})


def test_runtime_modules_do_not_import_offline_labels():
    for relative in ("src/lobcap/backtest/engine.py", "src/lobcap/backtest/arrival.py",
                     "src/lobcap/backtest/execution.py", "src/lobcap/backtest/strategies.py",
                     "src/lobcap/backtest/quote_policy.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "research_flow.labels" not in text and " import labels" not in text


def test_shared_label_scopes_match_reference_with_censoring_and_frozen_buckets():
    from dataclasses import replace
    from lobcap.backtest.research_flow.signal_analysis import evaluate_feature_scopes
    contexts = {}
    for index, date in enumerate(("day1", "day1", "day2")):
        offset = (index+1)*10000
        samples = []
        for n in range(1, 9):
            s = snapshot(offset+n*100, str(n % 3-1), str(100+n), offset+n*100-10)
            copy = replace(s.points[0], feature_name="copy", value=D(n),
                           status="INSUFFICIENT_DEPTH" if n == 3 else "AVAILABLE")
            samples.append(replace(s, points=s.points+(copy,), client_mid_coverage=n != 4))
        truth = MidpointSeries(((offset+n*100-10, str(100+n*n), "a") for n in range(10)),
                               (n != 5 for n in range(10)))
        contexts[str(index)] = (tuple(samples), truth, offset+900, date)
    definitions = tuple(LabelDefinition(kind, horizon, entry_latency_ns=20 if kind == POST_LATENCY_RETURN else 0)
        for kind in (MARKET_TRUTH_RETURN, CLIENT_FORWARD_RETURN, POST_LATENCY_RETURN)
        for horizon in (100, 200))
    policy = FreshnessPolicy("fixture", ((100, 20, 20), (200, 20, 5)))
    fitted = {feature: {date: ((D(0), D(1)), date+feature) for date in ("day1", "day2")}
              for feature in ("signal", "copy")}
    actual = evaluate_feature_scopes(tuple(fitted), contexts, definitions, policy, D(1), fitted)
    for scope, sid, members, date in (
        *(("per_window", wid, (wid,), c[3]) for wid, c in contexts.items()),
        ("per_date", "day1", ("0", "1"), "day1"),
        ("per_date", "day2", ("2",), "day2"),
        ("pooled", None, tuple(contexts), None),
    ):
        for feature in fitted:
            edges, policy_id = fitted[feature][date] if date else ((), None)
            expected = evaluate_feature_group(feature, (contexts[wid][:3] for wid in members),
                definitions, policy, D(1), edges, policy_id)
            rows = (actual[scope][sid] if sid else actual[scope])[feature]
            assert rows == list(expected)
