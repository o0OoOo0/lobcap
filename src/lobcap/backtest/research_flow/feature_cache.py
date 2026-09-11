"""Build reusable analysis-grid snapshots from a normalized replay."""
from __future__ import annotations

import gzip
import hashlib
import heapq
import json
from pathlib import Path

from lobcap.backtest.types import jsonable
from lobcap.config.common import sha256_file
from .features import CausalFeatureEngine, VERSION
from .feature_registry import FEATURE_DEFINITIONS, feature_definition_sha256
from .signal_contracts import AnalysisSchedule, FeaturePoint, FeatureSnapshot


CACHE_VERSION = "feature-cache-v2"


def iter_feature_snapshots(initial, events, schedule: AnalysisSchedule, tick,
                           clock_mode="recorded", bootstrap_receive_ns=None,
                           stale_after_ns=500_000_000, acquisition_segment="segment-1"):
    """Replay exchange events, delivering only information available by each grid time."""
    if clock_mode not in {"exchange", "recorded"}:
        raise ValueError("unknown clock mode")
    available = initial.time_ns if clock_mode == "exchange" else bootstrap_receive_ns
    if available is None or available < initial.time_ns:
        raise ValueError("valid bootstrap receive time is required")
    engine = CausalFeatureEngine(initial, tick, stale_after_ns, available)
    iterator = iter(events)
    event = next(iterator, None)
    deliveries = []
    last_delivery = {"depth": available, "trade": available}
    for now in schedule.times():
        while event is not None and event.time_ns <= now:
            received = event.time_ns if clock_mode == "exchange" else event.receive_ns
            if received < event.time_ns:
                raise ValueError("negative capture delay")
            delivered = max(received, last_delivery[event.kind])
            last_delivery[event.kind] = delivered
            heapq.heappush(deliveries, (delivered, event.replay_index, event))
            event = next(iterator, None)
        while deliveries and deliveries[0][0] <= now:
            delivered, _, incoming = heapq.heappop(deliveries)
            engine.observe(incoming, delivered)
        yield engine.observation(now, acquisition_segment)


def _point_from_row(row):
    from decimal import Decimal
    row = dict(row)
    row["value"] = Decimal(row["value"]) if row["value"] is not None else None
    row["source_refs"] = tuple(row["source_refs"])
    return FeaturePoint(**row)


def snapshot_from_row(row):
    from decimal import Decimal
    row = dict(row)
    row["client_mid"] = Decimal(row["client_mid"])
    row["points"] = tuple(_point_from_row(point) for point in row["points"])
    return FeatureSnapshot(**row)


def _cache_identity(sample_sha256, schedule, clock_mode, stale_after_ns, *, tick,
                    bootstrap_receive_ns, acquisition_segment, normalization_version,
                    source_schema_version, code_fingerprint):
    feature_config = {"tick": str(tick), "stale_after_ns": stale_after_ns}
    payload = {"cache_version": CACHE_VERSION, "feature_version": VERSION,
        "sample_sha256": sample_sha256, "schedule": jsonable(schedule),
        "clock_mode": clock_mode, "bootstrap_receive_ns": bootstrap_receive_ns,
        "acquisition_segment": acquisition_segment,
        "normalization_version": normalization_version,
        "source_schema_version": source_schema_version,
        "code_fingerprint": code_fingerprint,
        "feature_definition_sha256": feature_definition_sha256(FEATURE_DEFINITIONS),
        "feature_config": feature_config,
        "feature_config_sha256": hashlib.sha256(json.dumps(feature_config,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    return payload


def cache_fingerprint(sample_sha256, schedule, clock_mode, stale_after_ns, **identity):
    payload = _cache_identity(sample_sha256, schedule, clock_mode, stale_after_ns, **identity)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_feature_cache(path, snapshots, sample_sha256, schedule, clock_mode, stale_after_ns,
                        **identity):
    from lobcap.backtest.replay import write_json
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    rows = 0
    output = path / "features.jsonl.gz"
    with gzip.GzipFile(filename=str(output), mode="wb", mtime=0) as stream:
        for snapshot in snapshots:
            stream.write((json.dumps(jsonable(snapshot), sort_keys=True, separators=(",", ":"))+"\n").encode())
            rows += 1
    cache_identity = _cache_identity(sample_sha256, schedule, clock_mode, stale_after_ns, **identity)
    manifest = {"status": "PASS", **cache_identity, "rows": rows,
        "files": {"features.jsonl.gz": sha256_file(output)},
        "cache_key": cache_fingerprint(sample_sha256, schedule, clock_mode,
            stale_after_ns, **identity)}
    write_json(path / "feature_manifest.json", manifest)
    return manifest


def read_feature_cache(path, expected_key=None, feature_names=None):
    path = Path(path)
    manifest = json.loads((path / "feature_manifest.json").read_text())
    if manifest.get("status") != "PASS" or manifest.get("cache_version") != CACHE_VERSION:
        raise ValueError("invalid feature cache manifest")
    if expected_key is not None and manifest.get("cache_key") != expected_key:
        raise ValueError("feature cache key mismatch")
    for name, digest in manifest["files"].items():
        if sha256_file(path / name) != digest:
            raise ValueError("feature cache checksum mismatch")
    names = None if feature_names is None else frozenset(feature_names)
    with gzip.open(path / "features.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            snapshot = snapshot_from_row(json.loads(line))
            if names is not None:
                snapshot = FeatureSnapshot(snapshot.analysis_time_ns, snapshot.client_mid,
                    snapshot.client_mid_source_exchange_time_ns,
                    snapshot.client_mid_source_receive_time_ns,
                    snapshot.client_mid_available_time_ns, snapshot.acquisition_segment,
                    tuple(point for point in snapshot.points if point.feature_name in names))
            yield snapshot
