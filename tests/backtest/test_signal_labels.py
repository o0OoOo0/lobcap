from decimal import Decimal as D
import pytest

from lobcap.backtest.research_flow.labels import (
    MidpointSeries, apply_freshness_policy, measure_label, raw_label_cadence,
    target_age_distribution, truth_midpoint_series, freeze_development_freshness,
)
from lobcap.backtest.research_flow.signal_contracts import (
    CLIENT_FORWARD_RETURN, MARKET_TRUTH_RETURN, POST_LATENCY_RETURN,
    CENSORED_ACQUISITION_BOUNDARY, CENSORED_SAMPLE_END, OBSERVED,
    STALE_ANCHOR, STALE_TARGET, VALID, FreshnessPolicy, LabelDefinition,
)


def series():
    return MidpointSeries(((0, "100", "a"), (90, "101", "a"),
                           (190, "103", "a"), (290, "104", "a")))


def measured(kind, horizon=100, latency=0, decision=100, client_mid="100", client_time=80):
    definition = LabelDefinition(kind, horizon, entry_latency_ns=latency)
    return measure_label(definition, decision, client_mid, client_time, series(), D(1), 400, "a")


def test_three_label_families_separate_truth_staleness_and_entry_decay():
    truth = measured(MARKET_TRUTH_RETURN)
    client = measured(CLIENT_FORWARD_RETURN)
    delayed = measured(POST_LATENCY_RETURN, latency=100)
    assert truth.status == client.status == delayed.status == OBSERVED
    assert truth.value == D(2)      # 103 - exchange mid 101 at decision
    assert client.value == D(3)     # 103 - stale client mid 100
    assert delayed.value == D(1)    # 104 - exchange mid 103 after entry latency
    assert truth.anchor_age_ns == 10
    assert client.anchor_age_ns == 20
    assert delayed.anchor_age_ns == 10
    assert truth.target_age_ns == 10


def test_raw_age_is_preserved_before_horizon_policy_is_frozen():
    point = measured(MARKET_TRUTH_RETURN)
    assert point.status == OBSERVED and point.target_age_ns == 10
    strict_target = FreshnessPolicy("strict", ((100, 20, 5),))
    result = apply_freshness_policy(point, strict_target)
    assert result.status == STALE_TARGET and result.value is None
    accepted = apply_freshness_policy(point, FreshnessPolicy("accepted", ((100, 20, 10),)))
    assert accepted.status == VALID and accepted.value == point.value


def test_client_feed_staleness_is_separate_from_target_staleness():
    point = measured(CLIENT_FORWARD_RETURN, client_time=50)
    result = apply_freshness_policy(point, FreshnessPolicy("feed", ((100, 20, 20),)))
    assert point.anchor_age_ns == 50 and point.target_age_ns == 10
    assert result.status == STALE_ANCHOR


def test_target_age_distribution_has_no_hardcoded_tolerance():
    points = [measured(MARKET_TRUTH_RETURN, decision=t) for t in (100, 110, 120)]
    distribution = target_age_distribution(points)
    assert distribution == {"observations": 3, "minimum_ns": 10, "median_ns": 20,
                            "p95_ns": 30, "maximum_ns": 30}


def test_sample_end_and_segment_boundary_are_censored():
    end = measure_label(LabelDefinition(MARKET_TRUTH_RETURN, 300), 100, "100", 80,
                        series(), D(1), 400, "a")
    assert end.status == CENSORED_SAMPLE_END and end.value is None
    boundary_series = MidpointSeries(((0, "100", "a"), (90, "101", "a"), (190, "103", "b")))
    boundary = measure_label(LabelDefinition(MARKET_TRUTH_RETURN, 100), 100, "100", 80,
                             boundary_series, D(1), 400, "a")
    assert boundary.status == CENSORED_ACQUISITION_BOUNDARY and boundary.value is None


def test_raw_cadence_report_never_promotes_observed_to_valid():
    from lobcap.backtest.research_flow.signal_contracts import FeaturePoint, FeatureSnapshot
    feature = FeaturePoint("x", "v1", D(1), "ticks", "AVAILABLE", 100, 100, 90, 90, ())
    snapshot = FeatureSnapshot(100, D(101), 90, 90, 100, "a", (feature,))
    report = raw_label_cadence((snapshot,), series(),
        (LabelDefinition(MARKET_TRUTH_RETURN, 100),), D(1), 400)
    assert report[0]["statuses"] == {OBSERVED: 1}
    assert report[0]["freshness_policy"] is None
    assert "not VALID" in report[0]["note"]


def test_freshness_freeze_requires_two_development_dates_and_uses_worst_p95():
    from lobcap.backtest.research_flow.sample_catalogue import CatalogueWindow, DEVELOPMENT
    day = 86_400_000_000_000
    windows = (CatalogueWindow("a", "2026-09-07", 0, 1_200_000_000_000,
                               300_000_000_000, DEVELOPMENT),
               CatalogueWindow("b", "2026-09-08", day, day+1_200_000_000_000,
                               300_000_000_000, DEVELOPMENT))
    def row(anchor, target):
        return ({"horizon_ns": 100, "anchor_age_distribution": {"p95_ns": anchor},
                 "target_age_distribution": {"p95_ns": target}},)
    policy, evidence = freeze_development_freshness({"a": row(4, 8), "b": row(6, 7)}, windows)
    assert policy.maximum_ages_ns(100) == (6, 8)
    assert evidence["method"] == "maximum-development-window-p95-v1"
    with pytest.raises(ValueError, match="two development dates"):
        freeze_development_freshness({"a": row(4, 8)}, windows[:1])
