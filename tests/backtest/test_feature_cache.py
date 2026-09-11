from decimal import Decimal as D

import pytest

from lobcap.backtest.research_flow.feature_cache import (
    cache_fingerprint, iter_feature_snapshots, read_feature_cache, write_feature_cache,
)
from lobcap.backtest.research_flow.signal_contracts import AnalysisSchedule
from lobcap.backtest.types import BookSnapshot, L2LevelUpdate, L2Trade, ReplayEvent


def initial():
    return BookSnapshot(((D(100), D(3)),), ((D(101), D(1)),), 0, 1, D(90), D(110))


def delayed_depth():
    return ReplayEvent(1, 50, 150, "depth", ("d:1",), updates=(
                       L2LevelUpdate("buy", D(100), D(3), D(4)),))


IDENTITY = {"tick": D(1), "bootstrap_receive_ns": 0,
            "acquisition_segment": "depth-1+trade-1",
            "normalization_version": "binance-normalized-v2",
            "source_schema_version": "capture-v1", "code_fingerprint": "abc123"}


def test_analysis_grid_does_not_imply_delivery_or_decision_clock():
    schedule = AnalysisSchedule(100, 301, 100)
    recorded = list(iter_feature_snapshots(initial(), [delayed_depth()], schedule, D(1),
                    clock_mode="recorded", bootstrap_receive_ns=0))
    exchange = list(iter_feature_snapshots(initial(), [delayed_depth()], schedule, D(1),
                    clock_mode="exchange"))
    assert recorded[0].point("imbalance_1").value == D(".5")
    assert recorded[1].point("imbalance_1").value == D(".6")
    assert exchange[0].point("imbalance_1").value == D(".6")
    assert [row.analysis_time_ns for row in recorded] == [100, 200, 300]


def test_stream_factory_matches_heap_delivery_without_retaining_backlog():
    schedule = AnalysisSchedule(100, 401, 100)
    rows = [delayed_depth(), ReplayEvent(2, 60, 120, "trade", ("t:2",))]
    kwargs = dict(clock_mode="recorded", bootstrap_receive_ns=0)
    materialized = list(iter_feature_snapshots(initial(), rows, schedule, D(1), **kwargs))
    streamed = list(iter_feature_snapshots(initial(), lambda: iter(rows), schedule, D(1),
                                            **kwargs))
    assert streamed == materialized


def test_streaming_preserves_delayed_depth_sequence_and_real_trade_ties():
    schedule = AnalysisSchedule(100_000_000, 600_000_000, 100_000_000)
    rows = [
        ReplayEvent(1, 50_000_000, 300_000_000, "depth", ("d:1",), updates=(
            L2LevelUpdate("buy", D(100), D(3), D(4)),)),
        ReplayEvent(2, 60_000_000, 100_000_000, "trade", ("t:2",),
            trade=L2Trade("sell", D(101), D(1))),
        ReplayEvent(3, 80_000_000, 150_000_000, "depth", ("d:3",), updates=(
            L2LevelUpdate("buy", D(100), D(4), D(5)),)),
        ReplayEvent(4, 90_000_000, 300_000_000, "trade", ("t:4",),
            trade=L2Trade("buy", D(100), D(2))),
    ]
    kwargs = dict(clock_mode="recorded", bootstrap_receive_ns=0)
    a = list(iter_feature_snapshots(initial(), rows, schedule, D(1), **kwargs))
    b = list(iter_feature_snapshots(initial(), lambda: iter(rows), schedule, D(1), **kwargs))
    assert a == b
    assert b[0].point("imbalance_1").value == D(".5")
    assert b[1].point("imbalance_1").value == D(".5")
    assert b[2].point("imbalance_1").value == D(2)/D(3)
    assert b[0].point("signed_trade_volume_100ms").value == 1
    assert b[2].point("signed_trade_volume_100ms").value == -2


def test_feature_cache_roundtrip_and_checksum(tmp_path):
    schedule = AnalysisSchedule(100, 301, 100)
    snapshots = iter_feature_snapshots(initial(), [delayed_depth()], schedule, D(1),
        clock_mode="recorded", bootstrap_receive_ns=0)
    path = tmp_path / "cache"
    manifest = write_feature_cache(path, snapshots, "sample-sha", schedule, "recorded", 500,
                                   **IDENTITY)
    assert manifest["rows"] == 3
    assert list(read_feature_cache(path, manifest["cache_key"]))[1].client_mid == D("100.5")
    assert list(read_feature_cache(path, manifest["cache_key"], ()))[0].points == ()
    assert list(read_feature_cache(path, manifest["cache_key"], ()))[0].client_mid_coverage is True
    assert manifest["cache_key"] == cache_fingerprint("sample-sha", schedule, "recorded", 500,
                                                       **IDENTITY)
    with (path / "features.jsonl.gz").open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        list(read_feature_cache(path))


def test_feature_cache_key_changes_with_clock_and_schedule():
    a = AnalysisSchedule(0, 1000, 100)
    b = AnalysisSchedule(0, 1000, 250)
    assert cache_fingerprint("s", a, "recorded", 500, **IDENTITY) != cache_fingerprint("s", b, "recorded", 500, **IDENTITY)
    assert cache_fingerprint("s", a, "recorded", 500, **IDENTITY) != cache_fingerprint("s", a, "exchange", 500, **IDENTITY)


def test_feature_cache_key_binds_exact_projection():
    schedule = AnalysisSchedule(0, 1000, 100)
    one = cache_fingerprint("s", schedule, "recorded", 500,
                            **IDENTITY, feature_names=("mid",))
    two = cache_fingerprint("s", schedule, "recorded", 500,
                            **IDENTITY, feature_names=("mid", "spread_ticks"))
    assert one != two


@pytest.mark.parametrize("field,value", [
    ("tick", D(".5")), ("bootstrap_receive_ns", 1),
    ("acquisition_segment", "other"), ("normalization_version", "v3"),
    ("source_schema_version", "v2"), ("code_fingerprint", "def456"),
])
def test_feature_cache_key_covers_causal_provenance(field, value):
    schedule = AnalysisSchedule(0, 1000, 100)
    changed = dict(IDENTITY, **{field: value})
    assert cache_fingerprint("s", schedule, "recorded", 500, **IDENTITY) != cache_fingerprint(
        "s", schedule, "recorded", 500, **changed)


@pytest.mark.parametrize("streaming", [False, True])
def test_complete_replay_prefix_is_invariant_to_future_suffix_mutation(streaming):
    schedule = AnalysisSchedule(0, 400_000_000, 100_000_000)
    prefix = [ReplayEvent(1, 50_000_000, 60_000_000, "depth", ("d:1",), updates=(
        L2LevelUpdate("buy", D(100), D(3), D(4)),))]
    left = prefix + [ReplayEvent(2, 250_000_000, 260_000_000, "depth", ("d:2",), updates=(
        L2LevelUpdate("buy", D(100), D(4), D(5)),))]
    right = prefix + [ReplayEvent(2, 250_000_000, 260_000_000, "depth", ("d:9",), updates=(
        L2LevelUpdate("buy", D(100), D(4), D(9)),))]
    kwargs = dict(clock_mode="recorded", bootstrap_receive_ns=0,
                  acquisition_segment="sealed-segment")
    a = list(iter_feature_snapshots(initial(), (lambda: iter(left)) if streaming else left,
                                   schedule, D(1), **kwargs))
    b = list(iter_feature_snapshots(initial(), (lambda: iter(right)) if streaming else right,
                                   schedule, D(1), **kwargs))
    assert a[:3] == b[:3]
    assert a[3] != b[3]  # The changed suffix is actually observed by the replay.
