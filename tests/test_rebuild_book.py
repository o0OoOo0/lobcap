import gzip
import json
import struct
from decimal import Decimal

from lobcap.rebuild import OrderBook, collect_inputs, rebuild


def test_snapshot_and_absolute_updates():
    ob = OrderBook(Decimal("0.1"), Decimal("0.001"))
    ob.load_snapshot(
        {
            "payload": {
                "lastUpdateId": 10,
                "bids": [["100.0", "1.000"]],
                "asks": [["100.2", "2.000"]],
            }
        }
    )
    ob.apply(
        {
            "payload": {
                "u": 11,
                "b": [["100.0", "0.500"]],
                "a": [["100.2", "0.000"], ["100.3", "1.000"]],
            }
        }
    )
    assert ob.bids[1000] == 500
    assert 1002 not in ob.asks
    assert ob.asks[1003] == 1000
    assert not ob.crossed()


def _write_gzip_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def _snapshot(last_update_id):
    return {
        "schema_version": 2,
        "ts_recv_ns": last_update_id,
        "payload": {
            "lastUpdateId": last_update_id,
            "bids": [["100.0", "1.000"]],
            "asks": [["100.2", "1.000"]],
        },
    }


def _snapshot_with_bid(last_update_id, bid):
    record = _snapshot(last_update_id)
    record["payload"]["bids"] = [[bid, "1.000"]]
    return record


def _depth(U, u, pu=None):
    payload = {"U": U, "u": u, "b": [], "a": []}
    if pu is not None:
        payload["pu"] = pu
    return {"schema_version": 2, "ts_recv_ns": u, "payload": payload}


def test_collect_inputs_streams_depth_and_marks_missing_warmup(tmp_path):
    target_date = "2026-09-06"
    base = tmp_path / "market=futures" / "symbol=btcusdt"
    _write_gzip_jsonl(
        base / "kind=snapshot" / f"date={target_date}" / "snap.jsonl.gz",
        [_snapshot(10)],
    )
    _write_gzip_jsonl(
        base / "kind=depth" / f"date={target_date}" / "depth.jsonl.gz",
        [_depth(10, 10)],
    )

    initial, events, snapshots, warmup = collect_inputs(
        str(tmp_path), "btcusdt", target_date, warmup_prev=True
    )

    assert initial["payload"]["lastUpdateId"] == 10
    assert not isinstance(events, list)
    assert [e["payload"]["u"] for e in events] == [10]
    assert len(snapshots) == 1
    assert warmup["source"] == "target_day_first_snapshot"
    assert warmup["prefix_unavailable"] is True


def test_rebuild_marks_unaligned_shadow_skipped_and_starts_with_snapshot(tmp_path):
    target_date = "2026-09-06"
    base = tmp_path / "market=futures" / "symbol=btcusdt"
    _write_gzip_jsonl(
        base / "kind=snapshot" / f"date={target_date}" / "snap.jsonl.gz",
        [_snapshot(10), _snapshot(30)],
    )
    _write_gzip_jsonl(
        base / "kind=depth" / f"date={target_date}" / "depth.jsonl.gz",
        [_depth(10, 10)],
    )
    out = tmp_path / "rebuilt"

    stats = rebuild(
        str(tmp_path),
        "btcusdt",
        target_date,
        "0.1",
        "0.001",
        str(out),
        warmup_prev=False,
    )

    assert stats["snap_skipped"] == 1
    report = json.loads(
        (out / f"btcusdt_futures_{target_date}.reconcile.json").read_text()
    )
    assert report["snapshots"][0]["status"] == "SKIPPED"
    assert report["warmup"]["prefix_unavailable"] is True

    lobmsg = (out / f"btcusdt_futures_{target_date}.lobmsg").read_bytes()
    first = struct.unpack("<BB6xQQQQQ", lobmsg[:48])
    assert first[0] == 3  # SNAP_BEGIN
    assert first[4] == 10


def test_rebuild_reports_first_shadow_mismatch_and_crossed_state(tmp_path):
    target_date = "2026-09-06"
    base = tmp_path / "market=futures" / "symbol=btcusdt"
    _write_gzip_jsonl(
        base / "kind=snapshot" / f"date={target_date}" / "snap.jsonl.gz",
        [_snapshot_with_bid(10, "100.0"), _snapshot_with_bid(20, "99.9")],
    )
    _write_gzip_jsonl(
        base / "kind=depth" / f"date={target_date}" / "depth.jsonl.gz",
        [_depth(10, 10), _depth(11, 20, pu=10)],
    )

    out = tmp_path / "rebuilt"
    stats = rebuild(
        str(tmp_path),
        "btcusdt",
        target_date,
        "0.1",
        "0.001",
        str(out),
        warmup_prev=False,
    )

    assert stats["snap_mismatch"] == 1
    report = json.loads(
        (out / f"btcusdt_futures_{target_date}.reconcile.json").read_text()
    )
    comparison = report["snapshots"][0]
    assert comparison["status"] == "MISMATCH"
    assert comparison["mismatch"] == {
        "side": "bids",
        "level_index": 0,
        "main": [1000, 1000],
        "shadow": [999, 1000],
    }
    assert comparison["main_crossed"] is False
    assert comparison["shadow_crossed"] is False
