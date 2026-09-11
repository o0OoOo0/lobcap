"""Predictive diagnostics over causal feature snapshots and offline labels."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
import math

from .labels import apply_freshness_policy, measure_label, target_age_distribution
from .signal_contracts import VALID


def _pearson(left, right):
    if len(left) < 2:
        return None
    x = [float(value) for value in left]; y = [float(value) for value in right]
    mx, my = sum(x)/len(x), sum(y)/len(y)
    numerator = sum((a-mx)*(b-my) for a, b in zip(x, y))
    denominator = math.sqrt(sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))
    return numerator/denominator if denominator else None


def _ranks(values):
    order = sorted(range(len(values)), key=lambda index: values[index])
    result = [0.0]*len(values)
    position = 0
    while position < len(order):
        end = position+1
        while end < len(order) and values[order[end]] == values[order[position]]:
            end += 1
        rank = (position+end-1)/2
        for index in order[position:end]:
            result[index] = rank
        position = end
    return result


def evaluate_feature(feature_name, snapshots, truth, definitions, freshness_policy,
                     tick, sample_end_ns):
    """Evaluate one feature. Raw ages are reported before the frozen policy is applied."""
    snapshots = tuple(snapshots)
    results = []
    for definition in definitions:
        measured = []
        pairs = []
        statuses = Counter()
        for snapshot in snapshots:
            point = snapshot.point(feature_name)
            label = measure_label(definition, snapshot.analysis_time_ns, snapshot.client_mid,
                snapshot.client_mid_source_exchange_time_ns, truth, tick, sample_end_ns,
                snapshot.acquisition_segment, snapshot.acquisition_segment)
            measured.append(label)
            label = apply_freshness_policy(label, freshness_policy)
            status = label.status if point.status == "AVAILABLE" else "FEATURE_"+point.status
            statuses[status] += 1
            if point.status == "AVAILABLE" and label.status == VALID and point.value is not None:
                pairs.append((point.value, label.value))
        x = [row[0] for row in pairs]; y = [row[1] for row in pairs]
        nonzero = [(a, b) for a, b in pairs if a != 0 and b != 0]
        hits = sum((a > 0) == (b > 0) for a, b in nonzero)
        results.append({"feature_name": feature_name, "label_kind": definition.label_kind,
            "horizon_ns": definition.horizon_ns, "entry_latency_ns": definition.entry_latency_ns,
            "observations": len(snapshots), "valid": len(pairs),
            "coverage": len(pairs)/len(snapshots) if snapshots else None,
            "statuses": dict(sorted(statuses.items())),
            "target_age_distribution": target_age_distribution(measured),
            "pearson_ic": _pearson(x, y),
            "spearman_ic": _pearson(_ranks(x), _ranks(y)),
            "sign_hit_rate": hits/len(nonzero) if nonzero else None,
            "nonzero_sign_observations": len(nonzero),
            "mean_feature": sum(x, Decimal(0))/len(x) if x else None,
            "mean_label": sum(y, Decimal(0))/len(y) if y else None})
    return tuple(results)
