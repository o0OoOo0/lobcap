"""Offline midpoint labels.  Runtime strategy modules must not import this file."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from dataclasses import replace
from decimal import Decimal

from .signal_contracts import (
    CLIENT_FORWARD_RETURN, MARKET_TRUTH_RETURN, POST_LATENCY_RETURN,
    CENSORED_ACQUISITION_BOUNDARY, CENSORED_SAMPLE_END, MISSING_ANCHOR,
    MISSING_TARGET, OBSERVED, STALE_ANCHOR, STALE_TARGET, VALID,
    FreshnessPolicy, LabelPoint,
)


class MidpointSeries:
    """Exchange-truth midpoint observations, partitioned by acquisition segment."""

    def __init__(self, observations):
        rows = tuple(observations)
        if not rows:
            raise ValueError("midpoint series is empty")
        previous = -1
        normalized = []
        for time_ns, mid, segment in rows:
            mid = Decimal(mid)
            if time_ns <= previous or mid <= 0 or not mid.is_finite() or not segment:
                raise ValueError("invalid midpoint series")
            normalized.append((time_ns, mid, str(segment)))
            previous = time_ns
        self.rows = tuple(normalized)
        self.times = tuple(row[0] for row in normalized)

    def at_or_before(self, requested_ns):
        index = bisect_right(self.times, requested_ns)-1
        return None if index < 0 else self.rows[index]


def _return_value(anchor, target, unit, tick):
    if unit == "ticks":
        tick = Decimal(tick)
        if tick <= 0:
            raise ValueError("positive tick required")
        return (target-anchor)/tick
    return (target-anchor)/anchor*Decimal(10000)


def measure_label(definition, decision_time_ns, client_mid, client_mid_source_time_ns,
                  truth: MidpointSeries, tick, sample_end_ns, decision_segment,
                  client_mid_segment=None):
    """Measure raw label and ages; freshness is deliberately decided later."""
    latency = definition.entry_latency_ns if definition.label_kind == POST_LATENCY_RETURN else 0
    anchor_requested = decision_time_ns+latency
    target_requested = anchor_requested+definition.horizon_ns
    if target_requested >= sample_end_ns:
        return LabelPoint(definition, decision_time_ns, anchor_requested, target_requested,
            None, None, None, None, None, None, None, CENSORED_SAMPLE_END)
    anchor_row = truth.at_or_before(anchor_requested)
    target_row = truth.at_or_before(target_requested)
    if definition.label_kind == CLIENT_FORWARD_RETURN:
        anchor_mid = Decimal(client_mid) if client_mid is not None else None
        anchor_time = client_mid_source_time_ns if anchor_mid is not None else None
        if anchor_mid is not None and (anchor_time is None or anchor_time > decision_time_ns):
            raise ValueError("client midpoint provenance is missing or from the future")
        anchor_segment = client_mid_segment or decision_segment
    else:
        anchor_mid = anchor_row[1] if anchor_row else None
        anchor_time = anchor_row[0] if anchor_row else None
        anchor_segment = anchor_row[2] if anchor_row else None
    if anchor_mid is None:
        return LabelPoint(definition, decision_time_ns, anchor_requested, target_requested,
            None, target_row[1] if target_row else None, None, target_row[0] if target_row else None,
            None, target_requested-target_row[0] if target_row else None, None, MISSING_ANCHOR)
    if target_row is None:
        return LabelPoint(definition, decision_time_ns, anchor_requested, target_requested,
            anchor_mid, None, anchor_time, None, anchor_requested-anchor_time, None, None, MISSING_TARGET)
    if anchor_segment != decision_segment or target_row[2] != decision_segment:
        return LabelPoint(definition, decision_time_ns, anchor_requested, target_requested,
            anchor_mid, target_row[1], anchor_time, target_row[0], anchor_requested-anchor_time,
            target_requested-target_row[0], None, CENSORED_ACQUISITION_BOUNDARY)
    value = _return_value(anchor_mid, target_row[1], definition.unit, tick)
    return LabelPoint(definition, decision_time_ns, anchor_requested, target_requested,
        anchor_mid, target_row[1], anchor_time, target_row[0], anchor_requested-anchor_time,
        target_requested-target_row[0], value, OBSERVED)


def apply_freshness_policy(point: LabelPoint, policy: FreshnessPolicy):
    """Turn a raw measured label into VALID/STALE after cadence inspection."""
    if point.status != OBSERVED:
        return point
    maximum_anchor, maximum_target = policy.maximum_ages_ns(point.definition.horizon_ns)
    if point.anchor_age_ns is None or point.anchor_age_ns > maximum_anchor:
        return replace(point, value=None, status=STALE_ANCHOR)
    if point.target_age_ns is None or point.target_age_ns > maximum_target:
        return replace(point, value=None, status=STALE_TARGET)
    return replace(point, status=VALID)


def age_distribution(points, field="target_age_ns"):
    """Raw cadence evidence used before a horizon-specific policy is frozen."""
    if field not in {"anchor_age_ns", "target_age_ns"}:
        raise ValueError("unknown label age field")
    ages = sorted(getattr(p, field) for p in points if getattr(p, field) is not None)
    if not ages:
        return {"observations": 0, "minimum_ns": None, "median_ns": None,
                "p95_ns": None, "maximum_ns": None}
    def quantile(q):
        return ages[round((len(ages)-1)*q)]
    return {"observations": len(ages), "minimum_ns": ages[0], "median_ns": quantile(.5),
            "p95_ns": quantile(.95), "maximum_ns": ages[-1]}


def target_age_distribution(points):
    return age_distribution(points, "target_age_ns")


def truth_midpoint_series(initial, events, acquisition_segment):
    """Build exchange-truth midpoint observations from normalized depth batches."""
    from lobcap.backtest.engine import MarketBook
    book = MarketBook(initial)
    rows = [(initial.time_ns, book.mid, acquisition_segment)]
    for event in events:
        if event.kind != "depth":
            continue
        book.apply(event)
        if event.time_ns <= rows[-1][0]:
            raise ValueError("truth depth time did not advance")
        rows.append((event.time_ns, book.mid, acquisition_segment))
    return MidpointSeries(rows)


def raw_label_cadence(snapshots, truth, definitions, tick, sample_end_ns):
    """Measure label availability and ages without selecting a freshness threshold."""
    snapshots = tuple(snapshots)
    report = []
    for definition in definitions:
        points = [measure_label(definition, snapshot.analysis_time_ns, snapshot.client_mid,
                  snapshot.client_mid_source_exchange_time_ns, truth, tick, sample_end_ns,
                  snapshot.acquisition_segment, snapshot.acquisition_segment)
                  for snapshot in snapshots]
        statuses = Counter(point.status for point in points)
        report.append({"label_kind": definition.label_kind, "horizon_ns": definition.horizon_ns,
            "entry_latency_ns": definition.entry_latency_ns, "observations": len(points),
            "statuses": dict(sorted(statuses.items())),
            "anchor_age_distribution": age_distribution(points, "anchor_age_ns"),
            "target_age_distribution": age_distribution(points, "target_age_ns"),
            "freshness_policy": None,
            "note": "Raw cadence evidence only; OBSERVED is not VALID until a DEVELOPMENT-only policy is frozen."})
    return tuple(report)
