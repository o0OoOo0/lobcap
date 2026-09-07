import gzip
import json
from datetime import datetime, timezone

from lobcap.quality import check


def _write_gzip_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def _depth_records(target_date, first_bin, last_bin):
    start = int(
        datetime.fromisoformat(f"{target_date}T00:00:00+00:00").timestamp()
    )
    records = []
    previous_u = None
    for index in range(first_bin, last_bin + 1):
        u = 1_000 + index
        payload = {"U": u, "u": u, "b": [], "a": []}
        if previous_u is not None:
            payload["pu"] = previous_u
        records.append(
            {
                "schema_version": 2,
                "ts_recv_ns": (start + index * 300) * 1_000_000_000,
                "payload": payload,
            }
        )
        previous_u = u
    return records


def _write_quality_fixture(root, target_date, depth_records):
    base = root / "market=futures" / "symbol=btcusdt"
    _write_gzip_jsonl(
        base / "kind=depth" / f"date={target_date}" / "depth.jsonl.gz",
        depth_records,
    )
    _write_gzip_jsonl(
        base / "kind=trade" / f"date={target_date}" / "trade.jsonl.gz",
        [{"payload": {"e": "aggTrade"}}],
    )
    _write_gzip_jsonl(
        base / "kind=snapshot" / f"date={target_date}" / "snapshot.jsonl.gz",
        [{"payload": {"lastUpdateId": i}} for i in range(12)],
    )


def _write_event_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def test_strict_day_rejects_missing_day_edges(tmp_path):
    target_date = "2026-09-06"
    # This spans 23 hours with no internal empty bin.  The former range-only
    # check passed it despite missing the first and final portions of the day.
    records = _depth_records(target_date, first_bin=6, last_bin=282)
    _write_quality_fixture(tmp_path, target_date, records)
    assert not check(str(tmp_path), "btcusdt", target_date, strict_day=True)


def test_strict_day_accepts_all_utc_bins(tmp_path):
    target_date = "2026-09-06"
    records = _depth_records(target_date, first_bin=0, last_bin=287)
    _write_quality_fixture(tmp_path, target_date, records)
    assert check(str(tmp_path), "btcusdt", target_date, strict_day=True)


def test_quality_resets_continuity_only_at_process_start(tmp_path, capsys):
    target_date = "2026-09-06"
    start_ns = int(
        datetime.fromisoformat(f"{target_date}T00:00:00+00:00").timestamp()
        * 1_000_000_000
    )
    records = [
        {
            "ts_recv_ns": start_ns,
            "payload": {"U": 100, "u": 100, "b": [], "a": []},
        },
        {
            "ts_recv_ns": start_ns + 300_000_000_000,
            "payload": {"U": 101, "u": 101, "pu": 100, "b": [], "a": []},
        },
        {
            "ts_recv_ns": start_ns + 600_000_000_000,
            "payload": {"U": 900, "u": 900, "pu": 899, "b": [], "a": []},
        },
    ]
    _write_quality_fixture(tmp_path, target_date, records)
    event_path = (
        tmp_path
        / "market=futures"
        / "symbol=btcusdt"
        / "kind=event"
        / f"date={target_date}"
        / "events.jsonl"
    )
    _write_event_jsonl(
        event_path,
        [
            {"event": "process_start", "ts_ns": start_ns - 1},
            {
                "event": "process_start",
                "ts_ns": start_ns + 600_000_000_000 - 1,
            },
        ],
    )

    assert check(str(tmp_path), "btcusdt", target_date, strict_day=False)
    output = capsys.readouterr().out
    assert "pu gaps(scan)  : 0" in output
    assert "process bounds : 1" in output
