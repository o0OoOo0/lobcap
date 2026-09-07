"""Offline Binance Futures L2 reconstruction + shadow snapshot reconciliation.

Key Futures rules:
- discard event if u < snapshot L
- first event satisfies U <= L <= u
- subsequent continuity: pu == previous u
"""

from __future__ import annotations

import argparse
import glob
import gzip
import itertools
import json
import struct
from dataclasses import dataclass
from datetime import date as date_cls, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from lobcap.binance_protocol import (
    event_is_older_than_snapshot,
    first_event_covers_snapshot,
    payload,
    recv_ns,
)

LOBMSG = struct.Struct("<BB6xQQQQQ")
MSG_LEVEL = 0
MSG_DELETE = 1
MSG_RESERVED = 2
MSG_SNAP_BEGIN = 3
MSG_SNAP_END = 4


def encode(mtype, side, price, qty, seq, ts_exch, ts_recv) -> bytes:
    return LOBMSG.pack(mtype, side, price, qty, seq, ts_exch, ts_recv)


def read_jsonl_gz(pattern: str) -> Iterable[dict]:
    for path in sorted(glob.glob(pattern)):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                yield json.loads(line)


class OrderBook:
    def __init__(self, tick: Decimal, lot: Decimal):
        self.tick = tick
        self.lot = lot
        self.bids: dict[int, int] = {}
        self.asks: dict[int, int] = {}
        self.last_u: int | None = None

    def px(self, s: str) -> int:
        d = Decimal(str(s)) / self.tick
        i = int(d.to_integral_value())
        if d != i:
            raise ValueError(f"price {s} not multiple of tick {self.tick}")
        return i

    def qty(self, s: str) -> int:
        d = Decimal(str(s)) / self.lot
        i = int(d.to_integral_value())
        if d != i:
            raise ValueError(f"qty {s} not multiple of lot {self.lot}")
        return i

    def load_snapshot(self, snap_rec: dict) -> None:
        s = payload(snap_rec)
        self.bids.clear()
        self.asks.clear()
        for p, q in s["bids"]:
            iq = self.qty(q)
            if iq:
                self.bids[self.px(p)] = iq
        for p, q in s["asks"]:
            iq = self.qty(q)
            if iq:
                self.asks[self.px(p)] = iq
        self.last_u = int(s["lastUpdateId"])

    def apply(self, event_rec: dict) -> None:
        e = payload(event_rec)
        for key, book in (("b", self.bids), ("a", self.asks)):
            for p, q in e[key]:
                ip = self.px(p)
                iq = self.qty(q)
                if iq == 0:
                    book.pop(ip, None)
                else:
                    book[ip] = iq
        self.last_u = int(e["u"])

    def top(self, n: int):
        bids = sorted(self.bids, reverse=True)[:n]
        asks = sorted(self.asks)[:n]
        return (
            [(p, self.bids[p]) for p in bids],
            [(p, self.asks[p]) for p in asks],
        )

    def crossed(self) -> bool:
        if not self.bids or not self.asks:
            return False
        return max(self.bids) >= min(self.asks)


@dataclass
class Shadow:
    snap: dict
    book: OrderBook
    status: str = "WAIT"
    compare_u: int | None = None
    mismatch: dict | None = None
    main_crossed: bool | None = None
    shadow_crossed: bool | None = None


def first_top_mismatch(main_top, shadow_top) -> dict | None:
    """Describe the first top-N level mismatch, if one exists."""
    for side_index, side_name in enumerate(("bids", "asks")):
        main_levels = main_top[side_index]
        shadow_levels = shadow_top[side_index]
        for index in range(max(len(main_levels), len(shadow_levels))):
            main_level = main_levels[index] if index < len(main_levels) else None
            shadow_level = (
                shadow_levels[index] if index < len(shadow_levels) else None
            )
            if main_level != shadow_level:
                return {
                    "side": side_name,
                    "level_index": index,
                    "main": main_level,
                    "shadow": shadow_level,
                }
    return None


def _event_ts_exch_ns(rec: dict) -> int:
    p = payload(rec)
    ms = p.get("T")
    if ms is None:
        ms = p.get("E")
    return 0 if ms is None else int(ms) * 1_000_000


def _snapshot_ts_exch_ns(rec: dict) -> int:
    p = payload(rec)
    ms = p.get("T")
    if ms is None:
        ms = p.get("E")
    return 0 if ms is None else int(ms) * 1_000_000


def emit_snapshot(fbin, ob: OrderBook, snap_rec: dict) -> None:
    p = payload(snap_rec)
    seq = int(p["lastUpdateId"])
    ts_recv = int(snap_rec.get("ts_recv_ns", snap_rec.get("ts_recv", 0)))
    ts_exch = _snapshot_ts_exch_ns(snap_rec)
    fbin.write(encode(MSG_SNAP_BEGIN, 0, 0, 0, seq, ts_exch, ts_recv))
    for price in sorted(ob.bids, reverse=True):
        fbin.write(encode(MSG_LEVEL, 0, price, ob.bids[price], seq, ts_exch, ts_recv))
    for price in sorted(ob.asks):
        fbin.write(encode(MSG_LEVEL, 1, price, ob.asks[price], seq, ts_exch, ts_recv))
    fbin.write(encode(MSG_SNAP_END, 0, 0, 0, seq, ts_exch, ts_recv))


def _date_minus_one(s: str) -> str:
    d = date_cls.fromisoformat(s)
    return (d - timedelta(days=1)).isoformat()


def collect_inputs(data: str, symbol: str, target_date: str, warmup_prev: bool):
    base = Path(data) / "market=futures" / f"symbol={symbol}"

    target_snaps = list(
        read_jsonl_gz(str(base / "kind=snapshot" / f"date={target_date}" / "*.jsonl.gz"))
    )
    target_events = read_jsonl_gz(
        str(base / "kind=depth" / f"date={target_date}" / "*.jsonl.gz")
    )

    warm_snap = None
    warm_events: Iterable[dict] = ()
    previous_date = _date_minus_one(target_date)

    if warmup_prev:
        prev_snaps = list(
            read_jsonl_gz(
                str(base / "kind=snapshot" / f"date={previous_date}" / "*.jsonl.gz")
            )
        )
        if prev_snaps:
            warm_snap = prev_snaps[-1]
            L = int(payload(warm_snap)["lastUpdateId"])
            # We may include extra old events; synchronization logic drops them safely.
            prev_events = read_jsonl_gz(
                str(base / "kind=depth" / f"date={previous_date}" / "*.jsonl.gz")
            )
            warm_events = (
                e for e in prev_events if int(payload(e)["u"]) >= L
            )

    used_previous_day = warm_snap is not None
    if warm_snap is None:
        if not target_snaps:
            raise SystemExit("No snapshot available for reconstruction.")
        warm_snap = target_snaps[0]

    warmup_report = {
        "previous_day_requested": warmup_prev,
        "previous_date": previous_date,
        "source": (
            "previous_day_last_snapshot"
            if used_previous_day
            else "target_day_first_snapshot"
        ),
        "prefix_unavailable": not used_previous_day,
    }
    return (
        warm_snap,
        itertools.chain(warm_events, target_events),
        target_snaps,
        warmup_report,
    )


def rebuild(
    data: str,
    symbol: str,
    target_date: str,
    tick: str,
    lot: str,
    out: str,
    top_n: int = 50,
    warmup_prev: bool = True,
):
    tick_d = Decimal(tick)
    lot_d = Decimal(lot)

    initial_snap, events, independent_snaps, warmup_report = collect_inputs(
        data, symbol, target_date, warmup_prev
    )

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    binpath = out_dir / f"{symbol}_futures_{target_date}.lobmsg"

    main = OrderBook(tick_d, lot_d)
    main.load_snapshot(initial_snap)
    initial_L = main.last_u
    synced = False
    prev_u = None

    shadows: list[Shadow] = []
    initial_id = int(payload(initial_snap)["lastUpdateId"])
    for s in independent_snaps:
        if int(payload(s)["lastUpdateId"]) == initial_id:
            continue
        b = OrderBook(tick_d, lot_d)
        b.load_snapshot(s)
        shadows.append(Shadow(snap=s, book=b))

    stats = {
        "events": 0,
        "applied": 0,
        "dropped_before_sync": 0,
        "pu_gaps": 0,
        "resyncs": 0,
        "crossed": 0,
        "snap_checks": 0,
        "snap_match": 0,
        "snap_mismatch": 0,
        "snap_skipped": 0,
    }

    with binpath.open("wb") as fbin:
        emit_snapshot(fbin, main, initial_snap)

        for rec in events:
            stats["events"] += 1
            e = payload(rec)
            U = int(e["U"])
            u = int(e["u"])

            if not synced:
                if event_is_older_than_snapshot(rec, int(initial_L)):
                    stats["dropped_before_sync"] += 1
                    continue
                if not first_event_covers_snapshot(rec, int(initial_L)):
                    stats["dropped_before_sync"] += 1
                    continue
                synced = True
                prev_u = None
            else:
                pu = e.get("pu")
                if pu is None or int(pu) != int(prev_u):
                    stats["pu_gaps"] += 1

                    # Resynchronize from the earliest later independent snapshot
                    # whose update ID is not behind the current broken event.
                    candidates = [
                        sh for sh in shadows
                        if sh.status == "WAIT"
                        and int(payload(sh.snap)["lastUpdateId"]) >= u
                    ]
                    if not candidates:
                        break

                    chosen = min(
                        candidates,
                        key=lambda sh: int(payload(sh.snap)["lastUpdateId"])
                    )
                    chosen.status = "RESYNC"
                    main.load_snapshot(chosen.snap)
                    initial_L = int(payload(chosen.snap)["lastUpdateId"])
                    emit_snapshot(fbin, main, chosen.snap)
                    synced = False
                    prev_u = None
                    stats["resyncs"] += 1

                    # The current event belongs to the broken pre-resync chain.
                    # Continue until an event overlaps the chosen snapshot.
                    continue

            main.apply(rec)
            prev_u = u
            stats["applied"] += 1
            if main.crossed():
                stats["crossed"] += 1

            ts_exch = _event_ts_exch_ns(rec)
            ts_recv = recv_ns(rec)
            for key, side in (("b", 0), ("a", 1)):
                for p, q in e[key]:
                    ip = main.px(p)
                    iq = main.qty(q)
                    typ = MSG_DELETE if iq == 0 else MSG_LEVEL
                    fbin.write(encode(typ, side, ip, iq, u, ts_exch, ts_recv))

            # Independent snapshot shadow checks at the same final event u.
            for sh in shadows:
                if sh.status != "WAIT":
                    continue
                Ls = int(payload(sh.snap)["lastUpdateId"])

                if u < Ls:
                    continue
                if U <= Ls <= u:
                    sh.book.apply(rec)
                    sh.compare_u = u
                    stats["snap_checks"] += 1
                    main_top = main.top(top_n)
                    shadow_top = sh.book.top(top_n)
                    sh.main_crossed = main.crossed()
                    sh.shadow_crossed = sh.book.crossed()
                    if main_top == shadow_top:
                        sh.status = "MATCH"
                        stats["snap_match"] += 1
                    else:
                        sh.status = "MISMATCH"
                        stats["snap_mismatch"] += 1
                        sh.mismatch = first_top_mismatch(main_top, shadow_top)
                elif U > Ls:
                    sh.status = "SKIPPED"
                    stats["snap_skipped"] += 1

    # Every independent snapshot must end in an explicit terminal state.
    # A remaining WAIT means no event aligned it before input ended.
    for sh in shadows:
        if sh.status == "WAIT":
            sh.status = "SKIPPED"
            stats["snap_skipped"] += 1

    reports = []
    for sh in shadows:
        reports.append(
            {
                "snapshot_lastUpdateId": int(payload(sh.snap)["lastUpdateId"]),
                "status": sh.status,
                "compare_u": sh.compare_u,
                "main_crossed": sh.main_crossed,
                "shadow_crossed": sh.shadow_crossed,
                "mismatch": sh.mismatch,
            }
        )

    report_path = out_dir / f"{symbol}_futures_{target_date}.reconcile.json"
    report_path.write_text(
        json.dumps(
            {
                "initial_snapshot": int(payload(initial_snap)["lastUpdateId"]),
                "warmup": warmup_report,
                "top_n": top_n,
                "stats": stats,
                "snapshots": reports,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"LOBMSG: {binpath}")
    print(f"REPORT: {report_path}")
    print(json.dumps(stats, indent=2))
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data")
    ap.add_argument("--symbol", default="btcusdt")
    ap.add_argument("--date", required=True)
    ap.add_argument("--tick", required=True)
    ap.add_argument("--lot", required=True)
    ap.add_argument("--out", default="./rebuilt")
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--no-prev-warmup", action="store_true")
    a = ap.parse_args()
    rebuild(
        a.data,
        a.symbol.lower(),
        a.date,
        a.tick,
        a.lot,
        a.out,
        a.top_n,
        warmup_prev=not a.no_prev_warmup,
    )


if __name__ == "__main__":
    main()
