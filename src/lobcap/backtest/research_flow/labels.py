"""Offline midpoint labels.  Runtime strategy modules must not import this file."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from dataclasses import replace
from decimal import Decimal
import hashlib
import json

from .signal_contracts import (
    CLIENT_FORWARD_RETURN, MARKET_TRUTH_RETURN, POST_LATENCY_RETURN,
    CENSORED_ACQUISITION_BOUNDARY, CENSORED_SAMPLE_END, CENSORED_UNKNOWN_COVERAGE, MISSING_ANCHOR,
    MISSING_TARGET, OBSERVED, STALE_ANCHOR, STALE_TARGET, VALID,
    FreshnessPolicy, LabelPoint,
)


class MidpointSeries:
    """Exchange-truth midpoint observations, partitioned by acquisition segment."""

    def __init__(self, observations, coverage=None):
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
        self.coverage = tuple(coverage) if coverage is not None else (True,)*len(self.rows)
        if len(self.coverage) != len(self.rows) or any(type(v) is not bool for v in self.coverage):
            raise ValueError("midpoint coverage must match each observation")

    def at_or_before(self, requested_ns):
        index = bisect_right(self.times, requested_ns)-1
        return None if index < 0 else self.rows[index]

    def covered_at(self, requested_ns):
        index = bisect_right(self.times, requested_ns)-1
        return index >= 0 and self.coverage[index]


def _return_value(anchor, target, unit, tick):
    if unit == "ticks":
        tick = Decimal(tick)
        if tick <= 0:
            raise ValueError("positive tick required")
        return (target-anchor)/tick
    return (target-anchor)/anchor*Decimal(10000)


def measure_label(definition, decision_time_ns, client_mid, client_mid_source_time_ns,
                  truth: MidpointSeries, tick, sample_end_ns, decision_segment,
                  client_mid_segment=None, client_mid_coverage=True):
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
    anchor_covered = (client_mid_coverage if definition.label_kind == CLIENT_FORWARD_RETURN
                      else truth.covered_at(anchor_requested))
    if not anchor_covered or not truth.covered_at(target_requested):
        return LabelPoint(definition, decision_time_ns, anchor_requested, target_requested,
            anchor_mid, target_row[1], anchor_time, target_row[0], anchor_requested-anchor_time,
            target_requested-target_row[0], None, CENSORED_UNKNOWN_COVERAGE)
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
    coverage = [book.coverage_bid <= book.bid < book.ask <= book.coverage_ask]
    for event in events:
        if event.kind != "depth":
            continue
        book.apply(event)
        if event.time_ns <= rows[-1][0]:
            raise ValueError("truth depth time did not advance")
        rows.append((event.time_ns, book.mid, acquisition_segment))
        coverage.append(book.coverage_bid <= book.bid < book.ask <= book.coverage_ask)
    return MidpointSeries(rows, coverage)


def raw_label_cadence(snapshots, truth, definitions, tick, sample_end_ns):
    """Measure label availability and ages without selecting a freshness threshold."""
    accumulator = RawLabelCadence(definitions, truth, tick, sample_end_ns)
    for snapshot in snapshots:
        accumulator.observe(snapshot)
    return accumulator.finish()


class RawLabelCadence:
    """Streaming cadence accumulator that does not retain feature snapshots."""

    def __init__(self, definitions, truth, tick, sample_end_ns):
        self.definitions = tuple(definitions)
        self.truth = truth
        self.tick = tick
        self.sample_end_ns = sample_end_ns
        self._statuses = [Counter() for _ in self.definitions]
        self._anchor_ages = [[] for _ in self.definitions]
        self._target_ages = [[] for _ in self.definitions]
        self._counts = [0 for _ in self.definitions]

    def observe(self, snapshot):
        for index, definition in enumerate(self.definitions):
            point = measure_label(definition, snapshot.analysis_time_ns, snapshot.client_mid,
                snapshot.client_mid_source_exchange_time_ns, self.truth, self.tick,
                self.sample_end_ns, snapshot.acquisition_segment,
                snapshot.acquisition_segment, snapshot.client_mid_coverage)
            self._counts[index] += 1
            self._statuses[index][point.status] += 1
            if point.anchor_age_ns is not None:
                self._anchor_ages[index].append(point.anchor_age_ns)
            if point.target_age_ns is not None:
                self._target_ages[index].append(point.target_age_ns)

    @staticmethod
    def _distribution(ages):
        if not ages:
            return {"observations": 0, "minimum_ns": None, "median_ns": None,
                    "p95_ns": None, "maximum_ns": None}
        ages.sort()
        quantile = lambda q: ages[round((len(ages)-1)*q)]
        return {"observations": len(ages), "minimum_ns": ages[0],
                "median_ns": quantile(.5), "p95_ns": quantile(.95),
                "maximum_ns": ages[-1]}

    def finish(self):
        report = []
        for index, definition in enumerate(self.definitions):
            report.append({"label_kind": definition.label_kind,
                "horizon_ns": definition.horizon_ns,
                "entry_latency_ns": definition.entry_latency_ns,
                "observations": self._counts[index],
                "statuses": dict(sorted(self._statuses[index].items())),
                "anchor_age_distribution": self._distribution(self._anchor_ages[index]),
                "target_age_distribution": self._distribution(self._target_ages[index]),
                "freshness_policy": None,
                "note": "Raw cadence evidence only; OBSERVED is not VALID until a DEVELOPMENT-only policy is frozen."})
        return tuple(report)


def freeze_development_freshness(cadence_by_window, catalogue_windows):
    """Freeze horizon thresholds from at least two DEVELOPMENT dates only.

    Thresholds use the worst per-window p95 so a high-volume window cannot hide
    a slower development window. The evidence remains part of the policy hash.
    """
    from .sample_catalogue import DEVELOPMENT
    windows = {row.window_id: row for row in catalogue_windows}
    if set(cadence_by_window) != set(windows):
        raise ValueError("freshness evidence must match its exact catalogue windows")
    if any(row.role != DEVELOPMENT for row in windows.values()):
        raise ValueError("freshness policy may use DEVELOPMENT windows only")
    if len({row.date for row in windows.values()}) < 2:
        raise ValueError("freshness policy requires at least two development dates")
    by_horizon = {}
    for window_id, reports in sorted(cadence_by_window.items()):
        for report in reports:
            horizon = report["horizon_ns"]
            anchor = report["anchor_age_distribution"].get("p95_ns")
            target = report["target_age_distribution"].get("p95_ns")
            if anchor is None or target is None:
                raise ValueError(f"insufficient cadence evidence: {window_id}")
            by_horizon.setdefault(horizon, []).append((anchor, target))
    thresholds = tuple((horizon, max(row[0] for row in values),
                         max(row[1] for row in values))
                        for horizon, values in sorted(by_horizon.items()))
    evidence = {"method": "maximum-development-window-p95-v1",
        "window_ids": sorted(windows), "dates": sorted({row.date for row in windows.values()}),
        "thresholds": thresholds, "raw_cadence": cadence_by_window}
    policy_id = "development-freshness-" + hashlib.sha256(json.dumps(evidence,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return FreshnessPolicy(policy_id, thresholds), {"policy_id": policy_id, **evidence}
