"""Daily quality diagnostics for schema-v2 Binance capture."""

from __future__ import annotations

import argparse
import bisect
import glob
import gzip
import json
from collections import Counter
from datetime import date as date_cls, datetime, time as time_cls, timezone
from pathlib import Path
from typing import Iterable

from lobcap.binance_protocol import exchange_time_ms, payload, recv_ns


def read_jsonl_gz(pattern: str) -> Iterable[dict]:
    for path in sorted(glob.glob(pattern)):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                yield json.loads(line)


def read_jsonl(pattern: str) -> Iterable[dict]:
    for path in sorted(glob.glob(pattern)):
        with open(path, "rt", encoding="utf-8") as f:
            for line in f:
                yield json.loads(line)


def coverage_bins(
    target_date: str,
    bins_5m: Counter[int],
    first_ns: int | None,
    last_ns: int | None,
    strict_day: bool,
) -> tuple[int, int]:
    """Return expected and empty 5-minute bins for the requested scope.

    A strict daily check covers the complete UTC target day.  Measuring only
    between the first and last observed events would allow missing leading or
    trailing portions of a day to pass unnoticed.
    """
    if strict_day:
        day = date_cls.fromisoformat(target_date)
        day_start_s = int(
            datetime.combine(day, time_cls.min, tzinfo=timezone.utc).timestamp()
        )
        first_bin = day_start_s // 300
        expected_bins = 24 * 60 // 5
        empty_bins = sum(
            1 for b in range(first_bin, first_bin + expected_bins) if bins_5m[b] == 0
        )
        return expected_bins, empty_bins

    if first_ns is None or last_ns is None:
        return 0, 0
    first_bin = int((first_ns / 1e9) // 300)
    last_bin = int((last_ns / 1e9) // 300)
    expected_bins = last_bin - first_bin + 1
    empty_bins = sum(1 for b in range(first_bin, last_bin + 1) if bins_5m[b] == 0)
    return expected_bins, empty_bins


def check(data: str, symbol: str, date: str, strict_day: bool = True) -> bool:
    base = Path(data) / "market=futures" / f"symbol={symbol}"
    dp = str(base / "kind=depth" / f"date={date}" / "*.jsonl.gz")
    tp = str(base / "kind=trade" / f"date={date}" / "*.jsonl.gz")
    sp = str(base / "kind=snapshot" / f"date={date}" / "*.jsonl.gz")
    ep = str(base / "kind=event" / f"date={date}" / "*.jsonl")

    n_depth = 0
    n_trade = sum(1 for _ in read_jsonl_gz(tp))
    n_snap = sum(1 for _ in read_jsonl_gz(sp))

    events = list(read_jsonl(ep))
    lifecycle_counts = Counter(e.get("event") for e in events)
    process_starts_ns = sorted(
        int(e["ts_ns"])
        for e in events
        if e.get("event") == "process_start" and e.get("ts_ns") is not None
    )

    prev_u = None
    prev_process_segment = None
    process_boundaries = 0
    pu_gaps = []
    latency_ms = []
    bins_5m = Counter()
    first_ns = last_ns = None

    for rec in read_jsonl_gz(dp):
        n_depth += 1
        p = payload(rec)
        u = int(p["u"])
        pu = p.get("pu")
        rns = recv_ns(rec)

        # A new process_start is an explicit acquisition boundary. There was
        # no collector observing the feed between processes, so continuity
        # across that boundary is unknown rather than an unexplained pu gap.
        # WebSocket reconnects inside one process do not reset this state.
        process_segment = bisect.bisect_right(process_starts_ns, rns) - 1
        if (
            prev_process_segment is not None
            and process_segment != prev_process_segment
        ):
            prev_u = None
            process_boundaries += 1

        if prev_u is not None and (pu is None or int(pu) != prev_u):
            pu_gaps.append(
                {
                    "prev_u": prev_u,
                    "U": p.get("U"),
                    "u": u,
                    "pu": pu,
                    "ts_recv_ns": recv_ns(rec),
                }
            )
        prev_u = u
        prev_process_segment = process_segment

        first_ns = rns if first_ns is None else min(first_ns, rns)
        last_ns = rns if last_ns is None else max(last_ns, rns)
        bins_5m[int((rns / 1e9) // 300)] += 1

        exch = exchange_time_ms(rec)
        if exch is not None:
            latency_ms.append(rns / 1e6 - exch)

    persistent_gap_events = lifecycle_counts.get("sequence_gap", 0)

    if n_depth == 0:
        print("depth events: 0 — collection failed")
        return False

    duration_h = 0.0
    if first_ns is not None and last_ns is not None:
        duration_h = (last_ns - first_ns) / 1e9 / 3600

    expected_bins, empty_5m = coverage_bins(
        date, bins_5m, first_ns, last_ns, strict_day
    )

    latency_ms.sort()

    def pct(q: float):
        if not latency_ms:
            return None
        idx = min(int((len(latency_ms) - 1) * q), len(latency_ms) - 1)
        return latency_ms[idx]

    print(f"=== {symbol} futures {date} ===")
    print(f"depth          : {n_depth:,}")
    print(f"trades         : {n_trade:,}")
    print(f"snapshots      : {n_snap:,}")
    print(f"duration       : {duration_h:.2f} h")
    print(f"pu gaps(scan)  : {len(pu_gaps)}")
    print(f"process bounds : {process_boundaries}")
    print(f"gap events(log): {persistent_gap_events}")
    print(f"empty 5m bins  : {empty_5m}/{expected_bins}")
    print(f"lifecycle      : {dict(lifecycle_counts)}")
    if latency_ms:
        print(
            "recv-exch ms    : "
            f"p50={pct(.50):.1f} p95={pct(.95):.1f} p99={pct(.99):.1f}"
        )
        print("NOTE: latency is interpretable only if VM clock sync was healthy.")

    checks = {
        "depth > 0": n_depth > 0,
        "trade > 0": n_trade > 0,
        "snapshot > 0": n_snap > 0,
        "pu gaps == 0": len(pu_gaps) == 0,
        "empty 5m bins == 0": empty_5m == 0,
    }
    if strict_day:
        checks.update(
            {
                "duration >= 23h": duration_h >= 23.0,
                "snapshots >= 12": n_snap >= 12,
            }
        )

    print("\nchecks:")
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    ok = all(checks.values())
    print(f"\n=> {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data")
    ap.add_argument("--symbol", default="btcusdt")
    ap.add_argument("--date", required=True)
    ap.add_argument(
        "--diagnostic",
        action="store_true",
        help="Do not require 23h/12 snapshots; useful for 10-minute test.",
    )
    args = ap.parse_args()
    raise SystemExit(
        0
        if check(args.data, args.symbol.lower(), args.date, strict_day=not args.diagnostic)
        else 1
    )


if __name__ == "__main__":
    main()
