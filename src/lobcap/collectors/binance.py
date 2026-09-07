"""Raw Binance USDⓈ-M Futures collector (schema v2).

This collector intentionally does NOT rebuild the book.

Current documented routing:
- depth -> /public
- aggTrade -> /market
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import websockets

from lobcap.binance_protocol import SCHEMA_VERSION

LOG = logging.getLogger("lobcap.binance")

PUBLIC_BASE = "wss://fstream.binance.com/public"
MARKET_BASE = "wss://fstream.binance.com/market"
REST_DEPTH = "https://fapi.binance.com/fapi/v1/depth"


def now_ns() -> int:
    return time.time_ns()


class DurableEventLog:
    """Rare operational events: plain JSONL + flush + fsync per event."""

    def __init__(self, root: Path, symbol: str):
        self.root = root
        self.symbol = symbol

    def write(self, stream: str, event: str, **details: Any) -> None:
        dt = datetime.now(timezone.utc)
        d = (
            self.root
            / "market=futures"
            / f"symbol={self.symbol}"
            / "kind=event"
            / f"date={dt:%Y-%m-%d}"
        )
        d.mkdir(parents=True, exist_ok=True)
        path = d / "events.jsonl"
        rec = {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "venue": "binance",
            "market": "futures",
            "symbol": self.symbol,
            "ts_ns": now_ns(),
            "stream": stream,
            "event": event,
            "details": details,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())


class MemberGzipRollingWriter:
    """Append complete gzip members in batches.

    Concatenated gzip members are valid gzip. If the process dies, previous
    completed members remain readable; only the current in-memory batch is at
    risk.
    """

    def __init__(
        self,
        root: Path,
        symbol: str,
        kind: str,
        roll_sec: int,
        batch_lines: int = 100,
    ):
        self.root = root
        self.symbol = symbol
        self.kind = kind
        self.roll_sec = roll_sec
        self.batch_lines = batch_lines
        self.bucket: int | None = None
        self.path: Path | None = None
        self.buf: list[bytes] = []
        self.n = 0

    def _set_bucket(self, bucket: int) -> None:
        if self.bucket == bucket:
            return
        self.flush()
        self.bucket = bucket
        dt = datetime.fromtimestamp(bucket, tz=timezone.utc)
        d = (
            self.root
            / "market=futures"
            / f"symbol={self.symbol}"
            / f"kind={self.kind}"
            / f"date={dt:%Y-%m-%d}"
        )
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{dt:%Y%m%dT%H%M%S}Z.jsonl.gz"
        LOG.info("writer %s -> %s", self.kind, self.path)

    def write(self, obj: dict[str, Any]) -> None:
        bucket = (int(time.time()) // self.roll_sec) * self.roll_sec
        self._set_bucket(bucket)
        self.buf.append(
            (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
        )
        self.n += 1
        if len(self.buf) >= self.batch_lines:
            self.flush()

    def flush(self) -> None:
        if not self.buf or self.path is None:
            return
        blob = b"".join(self.buf)
        member = gzip.compress(blob, compresslevel=5)
        with self.path.open("ab") as f:
            f.write(member)
            f.flush()
            os.fsync(f.fileno())
        self.buf.clear()

    def close(self) -> None:
        self.flush()


@dataclass
class StreamState:
    name: str
    connection_id: str | None = None
    connects: int = 0
    disconnects: int = 0
    errors: int = 0


class BinanceCollector:
    def __init__(
        self,
        symbol: str,
        out: str,
        snap_interval: int = 300,
        depth_speed: str = "100ms",
        batch_lines: int = 100,
    ):
        self.symbol = symbol.lower()
        self.root = Path(out)
        self.snap_interval = snap_interval
        self.depth_speed = depth_speed
        self.stop = asyncio.Event()

        self.events = DurableEventLog(self.root, self.symbol)
        self.w_depth = MemberGzipRollingWriter(
            self.root, self.symbol, "depth", 3600, batch_lines
        )
        self.w_trade = MemberGzipRollingWriter(
            self.root, self.symbol, "trade", 3600, batch_lines
        )
        self.w_snap = MemberGzipRollingWriter(
            self.root, self.symbol, "snapshot", 86400, 1
        )

        self.depth_state = StreamState("depth")
        self.trade_state = StreamState("trade")

        # Keep previous u across WS reconnects within this process so the first
        # post-reconnect event can reveal whether continuity was lost.
        self.prev_depth_u: int | None = None

        self.stat = {
            "depth": 0,
            "trade": 0,
            "snapshot": 0,
            "sequence_gap": 0,
        }

    def _envelope(
        self,
        kind: str,
        connection_id: str | None,
        ts_recv_ns: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": kind,
            "venue": "binance",
            "market": "futures",
            "symbol": self.symbol,
            "connection_id": connection_id,
            "ts_recv_ns": ts_recv_ns,
            "payload": payload,
        }

    async def _run_stream(self, state: StreamState, url: str, handler) -> None:
        backoff = 1
        while not self.stop.is_set():
            cid = uuid.uuid4().hex
            state.connection_id = cid
            self.events.write(state.name, "connect_attempt", connection_id=cid, url=url)
            try:
                # Binance server ping frames are answered automatically by
                # websockets. We disable client-initiated periodic ping to keep
                # behavior simple and bounded.
                async with websockets.connect(
                    url,
                    ping_interval=None,
                    close_timeout=10,
                    max_queue=2048,
                    max_size=8 * 1024 * 1024,
                ) as ws:
                    state.connects += 1
                    self.events.write(
                        state.name, "connected", connection_id=cid, url=url
                    )
                    backoff = 1
                    async for raw in ws:
                        ts_recv_ns = now_ns()
                        msg = json.loads(raw)
                        handler(msg, ts_recv_ns, cid)
                        if self.stop.is_set():
                            break

                    # Normal close or graceful local break is still visible.
                    state.disconnects += 1
                    self.events.write(
                        state.name,
                        "disconnect",
                        connection_id=cid,
                        reason="websocket_iterator_ended",
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state.errors += 1
                self.events.write(
                    state.name,
                    "error",
                    connection_id=cid,
                    error=repr(exc),
                )
                LOG.exception("%s websocket error", state.name)

            if self.stop.is_set():
                break
            self.events.write(
                state.name, "reconnect_sleep", connection_id=cid, seconds=backoff
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _handle_depth(self, msg: dict[str, Any], ts_recv_ns: int, cid: str) -> None:
        p = msg.get("data", msg)
        if p.get("e") != "depthUpdate":
            return

        pu = p.get("pu")
        if self.prev_depth_u is not None:
            if pu is None or int(pu) != int(self.prev_depth_u):
                self.stat["sequence_gap"] += 1
                self.events.write(
                    "depth",
                    "sequence_gap",
                    connection_id=cid,
                    previous_u=self.prev_depth_u,
                    current_U=p.get("U"),
                    current_u=p.get("u"),
                    current_pu=pu,
                )
        self.prev_depth_u = int(p["u"])

        self.w_depth.write(self._envelope("depth", cid, ts_recv_ns, p))
        self.stat["depth"] += 1

    def _handle_trade(self, msg: dict[str, Any], ts_recv_ns: int, cid: str) -> None:
        p = msg.get("data", msg)
        if p.get("e") != "aggTrade":
            return
        self.w_trade.write(self._envelope("trade", cid, ts_recv_ns, p))
        self.stat["trade"] += 1

    async def run_depth(self) -> None:
        suffix = f"@{self.depth_speed}" if self.depth_speed else ""
        stream = f"{self.symbol}@depth{suffix}"
        url = f"{PUBLIC_BASE}/stream?streams={stream}"
        await self._run_stream(self.depth_state, url, self._handle_depth)

    async def run_trade(self) -> None:
        stream = f"{self.symbol}@aggTrade"
        url = f"{MARKET_BASE}/stream?streams={stream}"
        await self._run_stream(self.trade_state, url, self._handle_trade)

    async def run_snapshots(self) -> None:
        params = {"symbol": self.symbol.upper(), "limit": 1000}
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while not self.stop.is_set():
                ts_req_ns = now_ns()
                try:
                    async with session.get(REST_DEPTH, params=params) as response:
                        text = await response.text()
                        if response.status != 200:
                            raise RuntimeError(
                                f"snapshot HTTP {response.status}: {text[:500]}"
                            )
                        p = json.loads(text)
                    ts_recv_ns = now_ns()
                    rec = {
                        "schema_version": SCHEMA_VERSION,
                        "kind": "snapshot",
                        "venue": "binance",
                        "market": "futures",
                        "symbol": self.symbol,
                        "ts_req_ns": ts_req_ns,
                        "ts_recv_ns": ts_recv_ns,
                        "payload": p,
                    }
                    self.w_snap.write(rec)
                    self.stat["snapshot"] += 1
                    self.events.write(
                        "snapshot",
                        "snapshot_ok",
                        lastUpdateId=p.get("lastUpdateId"),
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.events.write("snapshot", "snapshot_error", error=repr(exc))
                    LOG.exception("snapshot failed")

                try:
                    await asyncio.wait_for(
                        self.stop.wait(), timeout=self.snap_interval
                    )
                except asyncio.TimeoutError:
                    pass

    async def run(self) -> None:
        self.events.write(
            "process",
            "process_start",
            pid=os.getpid(),
            depth_speed=self.depth_speed,
        )
        try:
            await asyncio.gather(
                self.run_depth(),
                self.run_trade(),
                self.run_snapshots(),
            )
        finally:
            self.close()

    def close(self) -> None:
        self.w_depth.close()
        self.w_trade.close()
        self.w_snap.close()
        try:
            self.events.write("process", "process_stop", stats=self.stat)
        except Exception:
            LOG.exception("failed to write process_stop")


async def _amain(args) -> None:
    collector = BinanceCollector(
        symbol=args.symbol,
        out=args.out,
        snap_interval=args.snap_interval,
        depth_speed=args.depth_speed,
        batch_lines=args.batch_lines,
    )

    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        collector.stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    await collector.run()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="btcusdt")
    ap.add_argument("--out", default="/data")
    ap.add_argument("--snap-interval", type=int, default=300)
    ap.add_argument(
        "--depth-speed",
        default="100ms",
        choices=["100ms", "500ms", ""],
        help="Current documented USDⓈ-M diff-depth options; runtime verify before production.",
    )
    ap.add_argument("--batch-lines", type=int, default=100)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        asyncio.run(_amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
