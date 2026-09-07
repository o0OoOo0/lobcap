"""
collector.py — 원본 L2 스트림 수집기 (Binance spot/futures)

설계 원칙
  1) 호가창을 재구성하지 않는다. 받은 메시지를 ts_recv와 함께 그대로 저장한다.
  2) 스냅샷은 주기적으로 별도 저장한다 (재구성 시작점 + 검증 기준점).
  3) 시퀀스 갭은 감지해서 로그로 남기되, 데이터를 버리지 않는다.
  4) 프로세스가 죽어도 이미 쓴 파일은 온전해야 한다 (시간 단위 롤링).

사용법
  pip install websockets aiohttp pandas pyarrow
  python collector.py --symbol btcusdt --market spot --out ./data
"""

import argparse
import asyncio
import gzip
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone

import aiohttp
import websockets

log = logging.getLogger("collector")

# ─────────────────────────────────────────────────────────────
# 거래소 엔드포인트
# ─────────────────────────────────────────────────────────────
VENUES = {
    "spot": {
        "ws": "wss://stream.binance.com:9443/stream?streams={streams}",
        "rest_depth": "https://api.binance.com/api/v3/depth",
        # spot diff 스트림 최소 주기는 100ms
        "depth_stream": "{sym}@depth@100ms",
        "trade_stream": "{sym}@trade",
        "snap_limit": 5000,
    },
    "futures": {
        # USDT-M perp. depth@0ms = 이벤트 발생 즉시 (MM 연구에는 이쪽이 더 적합)
        "ws": "wss://fstream.binance.com/stream?streams={streams}",
        "rest_depth": "https://fapi.binance.com/fapi/v1/depth",
        "depth_stream": "{sym}@depth@0ms",
        "trade_stream": "{sym}@aggTrade",
        "snap_limit": 1000,
    },
}


def now_ns() -> int:
    return time.time_ns()


class RollingWriter:
    """시간 단위로 파일을 갈아끼우는 append-only gzip JSONL 라이터.

    Parquet이 아니라 JSONL을 쓰는 이유: 수집 중 프로세스가 죽어도
    이미 flush된 라인까지는 살아남는다. Parquet 변환은 오프라인에서.
    """

    def __init__(self, root: str, symbol: str, market: str, kind: str, roll_sec: int = 3600):
        self.root = root
        self.symbol = symbol
        self.market = market
        self.kind = kind  # depth | trade | snapshot
        self.roll_sec = roll_sec
        self.fh = None
        self.bucket = None
        self.n = 0

    def _path(self, bucket: int) -> str:
        dt = datetime.fromtimestamp(bucket, tz=timezone.utc)
        d = os.path.join(
            self.root, f"market={self.market}", f"symbol={self.symbol}",
            f"kind={self.kind}", f"date={dt:%Y-%m-%d}",
        )
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{dt:%Y%m%dT%H%M%S}Z.jsonl.gz")

    def write(self, obj: dict):
        bucket = (int(time.time()) // self.roll_sec) * self.roll_sec
        if bucket != self.bucket:
            self.close()
            self.bucket = bucket
            self.fh = gzip.open(self._path(bucket), "at", encoding="utf-8")
            log.info("rolled -> %s", self._path(bucket))
        self.fh.write(json.dumps(obj, separators=(",", ":")) + "\n")
        self.n += 1
        if self.n % 200 == 0:      # 너무 잦은 flush는 압축률을 해친다
            self.fh.flush()

    def close(self):
        if self.fh:
            self.fh.flush()
            self.fh.close()
            self.fh = None


class SeqTracker:
    """Binance depth diff의 시퀀스 연속성 검사.

    각 이벤트는 U(첫 update id)와 u(마지막 update id)를 갖는다.
    정상 스트림이면 이번 U == 직전 u + 1 이어야 한다.
    """

    def __init__(self):
        self.prev_u = None
        self.gaps = []

    def check(self, U: int, u: int) -> bool:
        ok = True
        if self.prev_u is not None and U != self.prev_u + 1:
            ok = False
            self.gaps.append({"prev_u": self.prev_u, "U": U, "ts_ns": now_ns()})
            log.warning("SEQ GAP: prev_u=%s U=%s (missing %s)", self.prev_u, U, U - self.prev_u - 1)
        self.prev_u = u
        return ok


class Collector:
    def __init__(self, symbol: str, market: str, out: str, snap_interval: int = 300):
        self.symbol = symbol.lower()
        self.market = market
        self.cfg = VENUES[market]
        self.out = out
        self.snap_interval = snap_interval
        self.stop = asyncio.Event()

        self.w_depth = RollingWriter(out, self.symbol, market, "depth")
        self.w_trade = RollingWriter(out, self.symbol, market, "trade")
        self.w_snap = RollingWriter(out, self.symbol, market, "snapshot", roll_sec=86400)
        self.seq = SeqTracker()

        self.stat = {"depth": 0, "trade": 0, "snap": 0, "reconnect": 0}

    # ── WebSocket ────────────────────────────────────────────
    async def run_ws(self):
        streams = "/".join([
            self.cfg["depth_stream"].format(sym=self.symbol),
            self.cfg["trade_stream"].format(sym=self.symbol),
        ])
        url = self.cfg["ws"].format(streams=streams)

        backoff = 1
        while not self.stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20,
                                              max_queue=None) as ws:
                    log.info("ws connected: %s", url)
                    backoff = 1
                    self.seq.prev_u = None   # 재접속하면 시퀀스가 끊긴다
                    async for raw in ws:
                        # ts_recv는 파싱 전에 찍는다 — 파싱 시간이 레이턴시에 섞이면 안 됨
                        ts_recv = now_ns()
                        msg = json.loads(raw)
                        self.handle(msg, ts_recv)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.stat["reconnect"] += 1
                log.error("ws error (%s), reconnect in %ss", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def handle(self, msg: dict, ts_recv: int):
        data = msg.get("data", msg)
        ev = data.get("e")

        if ev == "depthUpdate":
            self.seq.check(data["U"], data["u"])
            self.w_depth.write({
                "ts_recv": ts_recv,
                "E": data["E"],              # 거래소 이벤트 시각 (ms)
                "T": data.get("T"),          # 트랜잭션 시각 (futures)
                "U": data["U"], "u": data["u"], "pu": data.get("pu"),
                "b": data["b"], "a": data["a"],   # [[price, qty], ...] 문자열 그대로
            })
            self.stat["depth"] += 1

        elif ev in ("trade", "aggTrade"):
            self.w_trade.write({
                "ts_recv": ts_recv,
                "E": data["E"], "T": data["T"],
                "p": data["p"], "q": data["q"],
                "m": data["m"],              # True면 buyer가 maker = 매도 주도 체결
                "id": data.get("t") or data.get("a"),
            })
            self.stat["trade"] += 1

    # ── REST 스냅샷 ──────────────────────────────────────────
    async def run_snapshots(self):
        url = self.cfg["rest_depth"]
        params = {"symbol": self.symbol.upper(), "limit": self.cfg["snap_limit"]}
        async with aiohttp.ClientSession() as sess:
            while not self.stop.is_set():
                try:
                    t0 = now_ns()
                    async with sess.get(url, params=params, timeout=10) as r:
                        snap = await r.json()
                    self.w_snap.write({
                        "ts_req": t0, "ts_recv": now_ns(),
                        "lastUpdateId": snap["lastUpdateId"],
                        "E": snap.get("E"), "T": snap.get("T"),
                        "bids": snap["bids"], "asks": snap["asks"],
                    })
                    self.stat["snap"] += 1
                    log.info("snapshot lastUpdateId=%s | %s", snap["lastUpdateId"], self.stat)
                except Exception as e:
                    log.error("snapshot failed: %s", e)
                await asyncio.sleep(self.snap_interval)

    async def run(self):
        await asyncio.gather(self.run_ws(), self.run_snapshots())

    def close(self):
        for w in (self.w_depth, self.w_trade, self.w_snap):
            w.close()
        if self.seq.gaps:
            p = os.path.join(self.out, f"gaps_{self.symbol}_{self.market}_{int(time.time())}.json")
            with open(p, "w") as f:
                json.dump(self.seq.gaps, f, indent=1)
            log.warning("%d sequence gaps -> %s", len(self.seq.gaps), p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="btcusdt")
    ap.add_argument("--market", default="futures", choices=["spot", "futures"])
    ap.add_argument("--out", default="./data")
    ap.add_argument("--snap-interval", type=int, default=300)
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    c = Collector(a.symbol, a.market, a.out, a.snap_interval)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown():
        c.stop.set()
        for t in asyncio.all_tasks(loop):
            t.cancel()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, shutdown)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(c.run())
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        c.close()
        log.info("final stats: %s", c.stat)


if __name__ == "__main__":
    main()
