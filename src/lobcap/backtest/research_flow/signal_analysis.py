"""Predictive diagnostics over causal feature snapshots and offline labels."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from bisect import bisect_right
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


def _quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered)-1)*fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    weight = Decimal(str(position-low))
    return ordered[low]*(Decimal(1)-weight)+ordered[high]*weight


def fit_bucket_edges(snapshots, feature_name, buckets=5):
    """Fit deterministic edges on an explicitly supplied development partition."""
    if buckets < 2:
        raise ValueError("at least two signal buckets are required")
    values = [snapshot.point(feature_name).value for snapshot in snapshots
              if snapshot.point(feature_name).status == "AVAILABLE"
              and snapshot.point(feature_name).value is not None]
    if len(values) < buckets:
        return ()
    return tuple(_quantile(values, Decimal(index)/Decimal(buckets))
                 for index in range(1, buckets))


def _bucket_report(pairs, edges):
    if not edges:
        return {"status": "UNAVAILABLE", "reason": "frozen bucket edges were not supplied",
                "edges": [], "buckets": [], "top_minus_bottom": None,
                "monotonic_direction": None}
    buckets = [[] for _ in range(len(edges)+1)]
    for feature, label in pairs:
        buckets[bisect_right(edges, feature)].append(label)
    rows = [{"bucket": index+1, "count": len(values),
             "mean_label": sum(values, Decimal(0))/len(values) if values else None}
            for index, values in enumerate(buckets)]
    means = [row["mean_label"] for row in rows]
    valid = all(value is not None for value in means)
    direction = None
    if valid:
        if all(left <= right for left, right in zip(means, means[1:])):
            direction = "NONDECREASING"
        elif all(left >= right for left, right in zip(means, means[1:])):
            direction = "NONINCREASING"
    return {"status": "VALID" if valid else "SPARSE", "edges": list(edges),
            "buckets": rows,
            "top_minus_bottom": means[-1]-means[0] if valid else None,
            "monotonic_direction": direction}


def _non_overlapping(pairs, horizon_ns):
    selected = []
    previous = None
    for time_ns, feature, label in pairs:
        if previous is None or time_ns-previous >= horizon_ns:
            selected.append((feature, label)); previous = time_ns
    x = [row[0] for row in selected]; y = [row[1] for row in selected]
    return {"observations": len(selected), "pearson_ic": _pearson(x, y),
            "spearman_ic": _pearson(_ranks(x), _ranks(y))}


def evaluate_feature(feature_name, snapshots, truth, definitions, freshness_policy,
                     tick, sample_end_ns, bucket_edges=(), bucket_policy_id=None):
    """Evaluate one feature. Raw ages are reported before the frozen policy is applied."""
    return evaluate_feature_group(feature_name,
        ((tuple(snapshots), truth, sample_end_ns),), definitions, freshness_policy,
        tick, bucket_edges, bucket_policy_id)


def evaluate_feature_group(feature_name, contexts, definitions, freshness_policy,
                           tick, bucket_edges=(), bucket_policy_id=None):
    """Evaluate pooled windows without pretending their truth series is continuous."""
    contexts = tuple((tuple(snapshots), truth, sample_end_ns)
                     for snapshots, truth, sample_end_ns in contexts)
    observations = sum(len(snapshots) for snapshots, _, _ in contexts)
    results = []
    for definition in definitions:
        measured = []
        timed_pairs = []
        statuses = Counter()
        for snapshots, truth, sample_end_ns in contexts:
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
                    timed_pairs.append((snapshot.analysis_time_ns, point.value, label.value))
        pairs = [(row[1], row[2]) for row in timed_pairs]
        x = [row[0] for row in pairs]; y = [row[1] for row in pairs]
        nonzero = [(a, b) for a, b in pairs if a != 0 and b != 0]
        hits = sum((a > 0) == (b > 0) for a, b in nonzero)
        mean_feature = sum(x, Decimal(0))/len(x) if x else None
        feature_variance = (sum((value-mean_feature)**2 for value in x)/len(x)
                            if x else None)
        results.append({"feature_name": feature_name, "label_kind": definition.label_kind,
            "horizon_ns": definition.horizon_ns, "entry_latency_ns": definition.entry_latency_ns,
            "observations": observations, "valid": len(pairs),
            "coverage": len(pairs)/observations if observations else None,
            "statuses": dict(sorted(statuses.items())),
            "target_age_distribution": target_age_distribution(measured),
            "pearson_ic": _pearson(x, y),
            "spearman_ic": _pearson(_ranks(x), _ranks(y)),
            "sign_hit_rate": hits/len(nonzero) if nonzero else None,
            "nonzero_sign_observations": len(nonzero),
            "mean_feature": mean_feature,
            "std_feature": feature_variance.sqrt() if feature_variance is not None else None,
            "feature_quantiles": {str(q): _quantile(x, q) for q in
                (Decimal(".05"), Decimal(".25"), Decimal(".5"), Decimal(".75"), Decimal(".95"))},
            "mean_label": sum(y, Decimal(0))/len(y) if y else None,
            "bucket_policy_id": bucket_policy_id,
            "bucket_analysis": _bucket_report(pairs, tuple(bucket_edges)),
            "non_overlapping": _non_overlapping(timed_pairs, definition.horizon_ns),
            "statistical_significance": "NOT_CLAIMED"})
    return tuple(results)


def evaluate_redundancy(snapshots, feature_names):
    """Pairwise feature dependence using only jointly available causal rows."""
    snapshots = tuple(snapshots)
    rows = []
    for index, left in enumerate(feature_names):
        for right in feature_names[index+1:]:
            pairs = []
            for snapshot in snapshots:
                a, b = snapshot.point(left), snapshot.point(right)
                if a.status == b.status == "AVAILABLE" and a.value is not None and b.value is not None:
                    pairs.append((a.value, b.value))
            x = [row[0] for row in pairs]; y = [row[1] for row in pairs]
            rows.append({"left": left, "right": right, "overlap": len(pairs),
                "coverage_overlap": len(pairs)/len(snapshots) if snapshots else None,
                "pearson": _pearson(x, y), "spearman": _pearson(_ranks(x), _ranks(y))})
    return tuple(rows)
