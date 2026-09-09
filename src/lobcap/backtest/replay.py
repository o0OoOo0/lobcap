"""Bounded, streaming Binance preparation. Raw payloads end at this boundary."""
from __future__ import annotations

import gzip
import heapq
import itertools
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

from lobcap.backtest.types import (BookSnapshot, Depletion, ReplayEvent, NS, ZERO,
                                  NORMALIZATION_VERSION, jsonable)
from lobcap.binance_protocol import payload, recv_ns, first_event_covers_snapshot, event_is_older_than_snapshot
from lobcap.config.common import sha256_file
from lobcap.l2.schema import L2LevelUpdate, L2Trade, decimal_value, assert_blinded_mapping
from lobcap.rebuild import OrderBook, first_top_mismatch


def write_json(path, value):
    Path(path).write_text(json.dumps(jsonable(value), sort_keys=True, indent=2) + "\n", encoding="utf-8")


def records(paths):
    for path in sorted(paths):
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as stream:
            for line, text in enumerate(stream, 1):
                if text.strip():
                    yield json.loads(text), f"{path.name}:{line}"


def _date(ns):
    return datetime.fromtimestamp(ns / NS, timezone.utc)


def _inputs(config):
    base = config.data_root / "market=futures" / "symbol=btcusdt"
    begin = _date(config.sample.start_ns - config.sample.warmup_ns - 600 * NS)
    end = _date(config.sample.end_ns - 1)
    dates = []
    day = begin.date()
    while day <= end.date():
        dates.append(day.isoformat())
        day += timedelta(days=1)
    result = {}
    for kind in ("depth", "trade", "snapshot", "event"):
        paths = []
        for date in dates:
            for path in sorted((base / f"kind={kind}" / f"date={date}").glob("*.jsonl*")):
                if kind in {"depth", "trade"}:
                    try:
                        hour = datetime.strptime(path.name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                    except ValueError:
                        hour = begin
                    if hour + timedelta(hours=1) <= begin or hour > end:
                        continue
                paths.append(path)
        if not paths:
            raise ValueError(f"missing {kind} inputs")
        result[kind] = paths
    return result


def _stream(paths, kind, end_ns, start_ns=0):
    previous_time = previous_id = None
    for record, ref in records(paths):
        p = payload(record)
        if record.get("schema_version") != 2 or record.get("kind") != kind:
            raise ValueError(f"expected schema-v2 {kind}: {ref}")
        if p.get("s") != "BTCUSDT" or p.get("st", 1) != 1:
            raise ValueError("only BTCUSDT USD-M captured records are supported")
        t = int(p["T"]) * 1_000_000
        if t < start_ns:
            continue
        seq = int(p["u"] if kind == "depth" else p["a"])
        if previous_time is not None and (t < previous_time or seq <= previous_id):
            raise ValueError(f"timestamp/sequence regression or duplicate: {ref}")
        if kind == "trade" and previous_id is not None and seq != previous_id + 1:
            raise ValueError(f"aggregate-trade ID gap: {ref}")
        previous_time, previous_id = t, seq
        if t >= end_ns:
            break
        yield t, 0 if kind == "trade" else 1, seq, record, ref


def normalize(book, initial, depth, trades, stats, shadows=()):
    """Merge sequence-validated streams; only the current timestamp is buffered."""
    interval_start = initial.time_ns
    traded = defaultdict(lambda: ZERO)
    index = 0
    merged = heapq.merge(depth, trades, key=lambda row: row[:3])
    for t, group in itertools.groupby(merged, key=lambda row: row[0]):
        before = {}
        refs, sequences, receives = [], [], []
        for _, kind, seq, record, ref in group:
            p = payload(record)
            if kind == 0:
                if "nq" not in p:
                    raise ValueError(f"missing nq: {ref}")
                total, normal = decimal_value(p["q"], "q"), decimal_value(p["nq"], "nq")
                if not ZERO <= normal <= total or total <= ZERO or type(p["m"]) is not bool:
                    raise ValueError(f"invalid trade quantity/side: {ref}")
                price = decimal_value(p["p"], "p")
                book.px(p["p"]); book.qty(p["nq"])
                side = "buy" if p["m"] else "sell"
                trade = L2Trade(side, price, normal) if normal else None
                traded[side, price] += normal
                stats["trade_events"] += 1
                stats["excluded_rpi_quantity"] += total - normal
                index += 1
                yield ReplayEvent(index, t, recv_ns(record), "trade", (ref,), (seq,),
                                  trade=trade, total_trade_quantity=total)
            else:
                if int(p["pu"]) != book.last_u:
                    raise ValueError(f"depth pu gap: {ref}")
                for key, side, levels in (("b", "buy", book.bids), ("a", "sell", book.asks)):
                    for price, quantity in p[key]:
                        ip = book.px(price)
                        book.qty(quantity)
                        before.setdefault((side, ip), levels.get(ip, 0))
                book.apply(record)
                if book.crossed() or not book.bids or not book.asks:
                    raise ValueError(f"crossed/empty reconstructed book: {ref}")
                for shadow in shadows:
                    snap, other, done = shadow
                    L = int(payload(snap)["lastUpdateId"])
                    if not done and int(p["U"]) <= L <= int(p["u"]):
                        other.apply(record)
                        if first_top_mismatch(book.top(50), other.top(50)) is not None:
                            raise ValueError("independent top-50 snapshot mismatch")
                        shadow[2] = True
                        stats["snapshot_matches"] += 1
                refs.append(ref); sequences.append(seq); receives.append(recv_ns(record))
                stats["depth_packets"] += 1
        if refs:
            updates, attribution = [], []
            keys = set(before) | {(side, book.px(str(price))) for side, price in traded}
            for side, ip in sorted(keys):
                levels = book.bids if side == "buy" else book.asks
                after = Decimal(levels.get(ip, 0)) * book.lot
                old = Decimal(before.get((side, ip), levels.get(ip, 0))) * book.lot
                price = Decimal(ip) * book.tick
                if old != after:
                    updates.append(L2LevelUpdate(side, price, old, after))
                d, v = max(ZERO, old-after), traded.get((side, price), ZERO)
                if d or v:
                    attribution.append(Depletion(side, price, d, v, max(ZERO, d-v), max(ZERO, v-d)))
            index += 1
            stats["max_depth_interval_ns"] = max(stats["max_depth_interval_ns"], t-interval_start)
            yield ReplayEvent(index, t, max(receives), "depth", tuple(refs), tuple(sequences),
                              tuple(updates), interval_start_ns=interval_start, depletion=tuple(attribution))
            interval_start = t
            traded.clear()


def prepare_sample(config):
    destination = config.output_path / "samples" / config.sample.sample_id
    if destination.exists():
        manifest_path = destination / "sample_manifest.json"
        if manifest_path.exists() and json.loads(manifest_path.read_text()).get("status") == "FAIL":
            destination.rename(destination.with_name(destination.name + ".failed-" + __import__("uuid").uuid4().hex[:8]))
        else:
            load_sample(destination, config)
            return destination
    paths = _inputs(config)
    warmup = config.sample.start_ns - config.sample.warmup_ns
    initial_record = None
    shadows = []
    for record, ref in records(paths["snapshot"]):
        if record.get("schema_version") != 2:
            raise ValueError("expected schema-v2 snapshot")
        t = int(payload(record)["T"]) * 1_000_000
        if recv_ns(record) <= warmup and t <= warmup:
            initial_record = record
        elif config.sample.start_ns <= t < config.sample.end_ns:
            other = OrderBook(config.instrument.tick, config.instrument.lot)
            other.load_snapshot(record)
            shadows.append([record, other, False])
        elif t >= config.sample.end_ns:
            break
    if initial_record is None:
        raise ValueError("no snapshot before feature warmup")
    snapshot_time = int(payload(initial_record)["T"]) * 1_000_000
    for record, ref in records(paths["event"]):
        if snapshot_time <= int(record.get("ts_ns", 0)) < config.sample.end_ns:
            if record.get("event") not in {"snapshot_ok"}:
                raise ValueError(f"acquisition lifecycle boundary/error inside sample bootstrap: {ref}")
    book = OrderBook(config.instrument.tick, config.instrument.lot)
    book.load_snapshot(initial_record)
    coverage_bid = Decimal(min(book.bids)) * book.tick
    coverage_ask = Decimal(max(book.asks)) * book.tick
    depth = iter(_stream(paths["depth"], "depth", config.sample.end_ns, snapshot_time))
    first = None
    for row in depth:
        if event_is_older_than_snapshot(row[3], book.last_u):
            continue
        if not first_event_covers_snapshot(row[3], book.last_u):
            raise ValueError("missing snapshot-overlapping depth event")
        book.apply(row[3]); first = row
        break
    if first is None or first[0] > warmup or book.crossed() or not book.bids or not book.asks:
        raise ValueError("invalid or insufficient snapshot warmup")
    initial = BookSnapshot(
        tuple((Decimal(p)*book.tick, Decimal(q)*book.lot) for p,q in sorted(book.bids.items(), reverse=True)),
        tuple((Decimal(p)*book.tick, Decimal(q)*book.lot) for p,q in sorted(book.asks.items())),
        first[0], book.last_u, coverage_bid, coverage_ask)
    trades = _stream(paths["trade"], "trade", config.sample.end_ns, initial.time_ns + 1)
    inputs = [{"path": str(path), "sha256": sha256_file(path)} for files in paths.values() for path in files]
    destination.mkdir(parents=True)
    stats = Counter()
    stats["excluded_rpi_quantity"] = ZERO
    counts = Counter()
    max_receive_delay = defaultdict(int)
    try:
        write_json(destination / "initial_book.json", initial)
        with gzip.GzipFile(filename=str(destination / "events.jsonl.gz"), mode="wb", mtime=0) as output:
            for event in normalize(book, initial, depth, trades, stats, shadows):
                if config.sample.start_ns <= event.time_ns < config.sample.end_ns:
                    counts[event.kind] += len(event.source_refs)
                    max_receive_delay[event.kind] = max(max_receive_delay[event.kind], event.receive_ns-event.time_ns)
                output.write((json.dumps(jsonable(event), sort_keys=True, separators=(",", ":"))+"\n").encode())
        if not counts["depth"] or not counts["trade"]:
            raise ValueError("sample has no depth/trade coverage")
        if len(shadows) < 1 or not all(item[2] for item in shadows):
            raise ValueError("missing/unaligned independent snapshot validation")
        for item in inputs:
            if sha256_file(item["path"]) != item["sha256"]:
                raise ValueError("source file changed during preparation")
        validation = {"status": "PASS", "stats": dict(stats), "trading_counts": dict(counts),
                      "max_receive_delay_ns": dict(max_receive_delay), "bootstrap_sequence": initial.sequence,
                      "snapshot_matches": stats["snapshot_matches"], "gaps": 0, "crossed": 0}
        write_json(destination / "validation.json", validation)
        files = {name: sha256_file(destination/name) for name in ("initial_book.json", "events.jsonl.gz", "validation.json")}
        sample_hash = __import__("hashlib").sha256(json.dumps({"files":files,"sample":jsonable(config.sample)}, sort_keys=True).encode()).hexdigest()
        write_json(destination / "sample_manifest.json", {"status":"PASS", "sample": config.sample,
            "instrument":config.instrument, "input_artifacts":inputs, "files":files, "sample_sha256":sample_hash})
        return destination
    except Exception as exc:
        write_json(destination / "sample_manifest.json", {"status":"FAIL", "error":str(exc)})
        raise


def load_sample(path, config=None):
    path = Path(path)
    manifest = json.loads((path / "sample_manifest.json").read_text())
    if manifest["status"] != "PASS":
        raise ValueError("sample preparation did not pass; use a new sample id after correcting inputs")
    if manifest["sample"]["normalization_version"] != NORMALIZATION_VERSION:
        raise ValueError("normalization version mismatch")
    if config is not None and (manifest["sample"] != jsonable(config.sample) or manifest["instrument"] != jsonable(config.instrument)):
        raise ValueError("sample configuration mismatch")
    for name, digest in manifest["files"].items():
        if sha256_file(path/name) != digest:
            raise ValueError(f"sample checksum mismatch: {name}")
    row = json.loads((path / "initial_book.json").read_text())
    row["bids"] = tuple(tuple(Decimal(x) for x in pair) for pair in row["bids"])
    row["asks"] = tuple(tuple(Decimal(x) for x in pair) for pair in row["asks"])
    row["coverage_bid"], row["coverage_ask"] = Decimal(row["coverage_bid"]), Decimal(row["coverage_ask"])
    return BookSnapshot(**row), manifest


def iter_events(path):
    previous_index, previous_time = 0, -1
    with gzip.open(Path(path)/"events.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            assert_blinded_mapping(row)
            if row["replay_index"] <= previous_index or row["time_ns"] < previous_time:
                raise ValueError("invalid normalized replay ordering")
            previous_index, previous_time = row["replay_index"], row["time_ns"]
            row["source_refs"], row["source_sequences"] = tuple(row["source_refs"]), tuple(row["source_sequences"])
            row["updates"] = tuple(L2LevelUpdate(u["side"], *(Decimal(u[k]) for k in ("price","quantity_before","quantity_after"))) for u in row["updates"])
            row["depletion"] = tuple(Depletion(d["side"], *(Decimal(d[k]) for k in ("price","visible","trades","nontrade","excess_trade"))) for d in row["depletion"])
            if row["trade"]:
                tr = row["trade"]
                row["trade"] = L2Trade(tr["maker_side"], Decimal(tr["price"]), Decimal(tr["size"]))
            row["total_trade_quantity"] = Decimal(row["total_trade_quantity"])
            yield ReplayEvent(**row)
