from decimal import Decimal as D

import pytest

from lobcap.backtest.research_flow.feature_cache import (
    cache_fingerprint, iter_feature_snapshots, read_feature_cache, write_feature_cache,
)
from lobcap.backtest.research_flow.signal_contracts import AnalysisSchedule
from lobcap.backtest.types import BookSnapshot, L2LevelUpdate, ReplayEvent


def initial():
    return BookSnapshot(((D(100), D(3)),), ((D(101), D(1)),), 0, 1, D(90), D(110))


def delayed_depth():
    return ReplayEvent(1, 50, 150, "depth", ("d:1",), updates=(
        L2LevelUpdate("buy", D(100), D(3), D(4)),))


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


def test_feature_cache_roundtrip_and_checksum(tmp_path):
    schedule = AnalysisSchedule(100, 301, 100)
    snapshots = iter_feature_snapshots(initial(), [delayed_depth()], schedule, D(1),
        clock_mode="recorded", bootstrap_receive_ns=0)
    path = tmp_path / "cache"
    manifest = write_feature_cache(path, snapshots, "sample-sha", schedule, "recorded", 500)
    assert manifest["rows"] == 3
    assert list(read_feature_cache(path, manifest["cache_key"]))[1].client_mid == D("100.5")
    assert manifest["cache_key"] == cache_fingerprint("sample-sha", schedule, "recorded", 500)
    with (path / "features.jsonl.gz").open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        list(read_feature_cache(path))


def test_feature_cache_key_changes_with_clock_and_schedule():
    a = AnalysisSchedule(0, 1000, 100)
    b = AnalysisSchedule(0, 1000, 250)
    assert cache_fingerprint("s", a, "recorded", 500) != cache_fingerprint("s", b, "recorded", 500)
    assert cache_fingerprint("s", a, "recorded", 500) != cache_fingerprint("s", a, "exchange", 500)
