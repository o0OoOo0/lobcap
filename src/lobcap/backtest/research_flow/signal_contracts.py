"""Immutable contracts for causal signal research.

The research sampling schedule is deliberately separate from the strategy
decision clock.  Labels are offline-only objects and must never cross into the
runtime strategy API.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


MARKET_TRUTH_RETURN = "MARKET_TRUTH_RETURN"
CLIENT_FORWARD_RETURN = "CLIENT_FORWARD_RETURN"
POST_LATENCY_RETURN = "POST_LATENCY_RETURN"
LABEL_KINDS = frozenset({MARKET_TRUTH_RETURN, CLIENT_FORWARD_RETURN, POST_LATENCY_RETURN})

OBSERVED = "OBSERVED"
VALID = "VALID"
STALE_ANCHOR = "STALE_ANCHOR"
STALE_TARGET = "STALE_TARGET"
CENSORED_SAMPLE_END = "CENSORED_SAMPLE_END"
CENSORED_ACQUISITION_BOUNDARY = "CENSORED_ACQUISITION_BOUNDARY"
MISSING_ANCHOR = "MISSING_ANCHOR"
MISSING_TARGET = "MISSING_TARGET"


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    feature_name: str
    version: str
    unit: str
    input_sources: tuple[str, ...]
    lookback_ns: int
    minimum_warmup_ns: int
    update_trigger: str
    stale_after_ns: int
    missing_value_policy: str = "unavailable"
    normalization_policy: str = "raw"
    causal: bool = True

    def __post_init__(self):
        if not self.feature_name or not self.version or not self.unit:
            raise ValueError("feature identity and unit are required")
        if not self.input_sources:
            raise ValueError("feature input sources are required")
        if min(self.lookback_ns, self.minimum_warmup_ns, self.stale_after_ns) < 0:
            raise ValueError("feature times must be nonnegative")
        if not self.causal:
            raise ValueError("Signal Lab v1 accepts causal features only")
        if self.missing_value_policy not in {"unavailable", "carry_with_age"}:
            raise ValueError("unknown missing-value policy")
        if self.normalization_policy not in {"raw", "train_only"}:
            raise ValueError("unknown normalization policy")


@dataclass(frozen=True, slots=True)
class FeaturePoint:
    feature_name: str
    feature_version: str
    value: Decimal | None
    unit: str
    status: str
    analysis_time_ns: int
    client_available_time_ns: int
    latest_source_exchange_time_ns: int | None
    latest_source_receive_time_ns: int | None
    source_refs: tuple[str, ...]

    def __post_init__(self):
        if self.client_available_time_ns > self.analysis_time_ns:
            raise ValueError("future client data cannot enter a feature point")
        if self.value is not None and not self.value.is_finite():
            raise ValueError("feature value must be finite")


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    """One analysis-grid observation over event-driven feature state."""

    analysis_time_ns: int
    client_mid: Decimal
    client_mid_source_exchange_time_ns: int
    client_mid_source_receive_time_ns: int
    client_mid_available_time_ns: int
    acquisition_segment: str
    points: tuple[FeaturePoint, ...]

    def __post_init__(self):
        if self.client_mid <= 0 or not self.client_mid.is_finite():
            raise ValueError("client midpoint must be finite and positive")
        if self.client_mid_available_time_ns > self.analysis_time_ns:
            raise ValueError("client midpoint is not available at analysis time")
        if not self.acquisition_segment:
            raise ValueError("acquisition segment is required")

    def point(self, name: str) -> FeaturePoint:
        for point in self.points:
            if point.feature_name == name:
                return point
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class AnalysisSchedule:
    """Research observation grid; never implies a strategy decision cadence."""

    start_ns: int
    end_ns: int
    interval_ns: int = 100_000_000

    def __post_init__(self):
        if self.start_ns < 0 or self.end_ns <= self.start_ns or self.interval_ns <= 0:
            raise ValueError("invalid analysis schedule")

    def times(self):
        return range(self.start_ns, self.end_ns, self.interval_ns)


@dataclass(frozen=True, slots=True)
class LabelDefinition:
    label_kind: str
    horizon_ns: int
    unit: str = "ticks"
    entry_latency_ns: int = 0

    def __post_init__(self):
        if self.label_kind not in LABEL_KINDS:
            raise ValueError("unknown label kind")
        if self.horizon_ns <= 0 or self.entry_latency_ns < 0:
            raise ValueError("invalid label timing")
        if self.label_kind != POST_LATENCY_RETURN and self.entry_latency_ns:
            raise ValueError("entry latency belongs only to POST_LATENCY_RETURN")
        if self.unit not in {"ticks", "bps"}:
            raise ValueError("label unit must be ticks or bps")


@dataclass(frozen=True, slots=True)
class LabelPoint:
    definition: LabelDefinition
    decision_time_ns: int
    anchor_requested_ns: int
    target_requested_ns: int
    anchor_mid: Decimal | None
    target_mid: Decimal | None
    anchor_mid_time_ns: int | None
    target_mid_time_ns: int | None
    anchor_age_ns: int | None
    target_age_ns: int | None
    value: Decimal | None
    status: str

    def __post_init__(self):
        if self.value is not None and not self.value.is_finite():
            raise ValueError("label value must be finite")
        if self.anchor_age_ns is not None and self.anchor_age_ns < 0:
            raise ValueError("anchor midpoint cannot come from the future")
        if self.target_age_ns is not None and self.target_age_ns < 0:
            raise ValueError("target midpoint cannot come from the future")


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """A post-measurement policy fitted without validation/OOS data."""

    policy_id: str
    maximum_age_by_horizon_ns: tuple[tuple[int, int, int], ...]

    def __post_init__(self):
        rows = {h: (anchor, target) for h, anchor, target in self.maximum_age_by_horizon_ns}
        if not self.policy_id or len(rows) != len(self.maximum_age_by_horizon_ns):
            raise ValueError("freshness policy needs a unique id and horizons")
        if any(h <= 0 or anchor < 0 or target < 0 for h, (anchor, target) in rows.items()):
            raise ValueError("invalid freshness threshold")

    def maximum_ages_ns(self, horizon_ns: int) -> tuple[int, int]:
        try:
            return {h: (anchor, target) for h, anchor, target in self.maximum_age_by_horizon_ns}[horizon_ns]
        except KeyError as exc:
            raise ValueError("freshness policy does not cover this horizon") from exc
