from decimal import Decimal as D

import pytest

from lobcap.backtest.research_flow.signal_contracts import (
    AnalysisSchedule, FeatureDefinition, FeaturePoint, FreshnessPolicy,
    LabelDefinition, CLIENT_FORWARD_RETURN, POST_LATENCY_RETURN,
)


def test_analysis_schedule_is_only_an_observation_grid():
    schedule = AnalysisSchedule(0, 500_000_000, 100_000_000)
    assert list(schedule.times()) == [0, 100_000_000, 200_000_000, 300_000_000, 400_000_000]
    assert not hasattr(schedule, "decision_ms")


def test_feature_contract_rejects_noncausal_or_future_data():
    with pytest.raises(ValueError, match="causal"):
        FeatureDefinition("future", "v1", "ticks", ("depth",), 0, 0, "event", 1, causal=False)
    with pytest.raises(ValueError, match="future client"):
        FeaturePoint("mid", "v1", D(1), "USDT", "AVAILABLE", 10, 11, 9, 10, ())


def test_label_and_freshness_contracts_are_strict():
    assert LabelDefinition(CLIENT_FORWARD_RETURN, 100).entry_latency_ns == 0
    assert LabelDefinition(POST_LATENCY_RETURN, 100, entry_latency_ns=20).entry_latency_ns == 20
    with pytest.raises(ValueError, match="only"):
        LabelDefinition(CLIENT_FORWARD_RETURN, 100, entry_latency_ns=20)
    policy = FreshnessPolicy("dev-cadence-v1", ((100, 10, 5), (500, 20, 30)))
    assert policy.maximum_ages_ns(100) == (10, 5)
    with pytest.raises(ValueError, match="cover"):
        policy.maximum_ages_ns(200)
