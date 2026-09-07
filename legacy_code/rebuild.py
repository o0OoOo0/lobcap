"""
rebuild.py — 오프라인 호가창 재구성 + 검증 + LOBMSG 출력

수집기가 저장한 원본(depth diff, snapshot)으로 호가창을 재구성한다.
이 파일이 소프트웨어 참조 구현(reference implementation)이며,
FPGA 북 빌더는 여기 출력과 bit-exact 비교된다.

Binance diff 적용 규칙 (공식 문서 기준)
  1) 스냅샷을 받는다 → lastUpdateId = L
  2) u <= L 인 이벤트는 버린다 (이미 스냅샷에 반영됨)
  3) 첫 적용 이벤트는 U <= L+1 <= u 를 만족해야 한다
  4) 이후 이벤트는 U == 직전 u + 1 이어야 한다. 아니면 갭 → 스냅샷부터 다시
  5) qty == 0 이면 해당 가격 레벨 삭제, 아니면 절대량으로 덮어쓰기 (누적 아님)

사용법
  python rebuild.py --data ./data --market futures --symbol btcusdt \
      --date 2026-09-02 --tick 0.1 --lot 0.001 --out ./rebuilt
"""

import argparse
import glob
import gzip
import json
import os
import struct
from decimal import Decimal


# ─────────────────────────────────────────────────────────────
# 호가창
# ─────────────────────────────────────────────────────────────
class OrderBook:
    """가격(정수 틱) -> 수량(정수 lot) 딕셔너리.

    가격/수량을 정수로 다루는 이유:
      - float 반올림 오차 제거
      - FPGA와 bit-exact 비교 가능
      - LOBMSG 포맷과 동일한 표현
    """

    def __init__(self, tick: Decimal, lot: Decimal):
        self.tick = tick
        self.lot = lot
        self.bids = {}
        self.asks = {}
        self.last_u = None

    def px(self, s: str) -> int:
        d = Decimal(s) / self.tick
        i = int(d.to_integral_value())
        if d != i:
            # 틱 크기를 잘못 잡으면 서로 다른 가격이 같은 정수로 뭉개져
            # 데이터가 "조용히" 오염된다. 반드시 죽는다.
            raise ValueError(f"price {s} is not a multiple of tick {self.tick}")
        return i

    def qty(self, s: str) -> int:
        d = Decimal(s) / self.lot
        i = int(d.to_integral_value())
        if d != i:
            raise ValueError(f"qty {s} is not a multiple of lot {self.lot}")
        return i

    def load_snapshot(self, snap: dict):
        self.bids.clear()
        self.asks.clear()
        for p, q in snap["bids"]:
            v = self.qty(q)
            if v:
                self.bids[self.px(p)] = v
        for p, q in snap["asks"]:
            v = self.qty(q)
            if v:
                self.asks[self.px(p)] = v
        self.last_u = snap["lastUpdateId"]

    def apply(self, ev: dict):
        for side_key, book in (("b", self.bids), ("a", self.asks)):
            for p, q in ev[side_key]:
                ip, iq = self.px(p), self.qty(q)
                if iq == 0:
                    book.pop(ip, None)
                else:
                    book[ip] = iq
        self.last_u = ev["u"]

    def best_bid(self):
        return max(self.bids) if self.bids else None

    def best_ask(self):
        return min(self.asks) if self.asks else None

    def top(self, n=5):
        b = sorted(self.bids, reverse=True)[:n]
        a = sorted(self.asks)[:n]
        return [(p, self.bids[p]) for p in b], [(p, self.asks[p]) for p in a]

    def crossed(self) -> bool:
        bb, ba = self.best_bid(), self.best_ask()
        return bb is not None and ba is not None and bb >= ba


# ─────────────────────────────────────────────────────────────
# LOBMSG — FPGA와 공유하는 48바이트 고정폭 포맷
# ─────────────────────────────────────────────────────────────
# little-endian: type(1) side(1) pad(6) price(8) qty(8) seq(8) ts_exch(8) ts_recv(8)
LOBMSG = struct.Struct("<BB6xQQQQQ")
assert LOBMSG.size == 48

MSG_LEVEL, MSG_DELETE, MSG_TRADE, MSG_SNAP_BEGIN, MSG_SNAP_END = 0, 1, 2, 3, 4


def encode(mtype, side, price, qty, seq, ts_exch, ts_recv) -> bytes:
    return LOBMSG.pack(mtype, side, price, qty, seq, ts_exch, ts_recv)


# ─────────────────────────────────────────────────────────────
# 파일 읽기
# ─────────────────────────────────────────────────────────────
def read_jsonl(pattern):
    for path in sorted(glob.glob(pattern)):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                yield json.loads(line)


def rebuild(data, market, symbol, date, tick, lot, out):
    base = os.path.join(data, f"market={market}", f"symbol={symbol}")
    snaps = list(read_jsonl(os.path.join(base, "kind=snapshot", f"date={date}", "*.jsonl.gz")))
    events = read_jsonl(os.path.join(base, "kind=depth", f"date={date}", "*.jsonl.gz"))
    if not snaps:
        raise SystemExit("스냅샷이 없습니다. 재구성 시작점을 만들 수 없습니다.")

    os.makedirs(out, exist_ok=True)
    binpath = os.path.join(out, f"{symbol}_{market}_{date}.lobmsg")
    fbin = open(binpath, "wb")

    ob = OrderBook(Decimal(str(tick)), Decimal(str(lot)))
    snap_iter = iter(snaps)
    cur_snap = next(snap_iter)
    ob.load_snapshot(cur_snap)
    synced = False

    stat = {"events": 0, "applied": 0, "dropped_old": 0, "gaps": 0,
            "resyncs": 0, "crossed": 0, "snap_checks": 0, "snap_mismatch": 0}
    remaining_snaps = list(snap_iter)

    for ev in events:
        stat["events"] += 1
        U, u = ev["U"], ev["u"]

        if not synced:
            if u <= ob.last_u:
                stat["dropped_old"] += 1
                continue
            if not (U <= ob.last_u + 1 <= u):
                # 스냅샷보다 한참 뒤의 이벤트 → 다음 스냅샷을 기다린다
                stat["dropped_old"] += 1
                continue
            synced = True
        else:
            if U != ob.last_u + 1:
                stat["gaps"] += 1
                # 갭 발생 → 이 시점 이후의 스냅샷으로 재동기화
                nxt = None
                while remaining_snaps:
                    s = remaining_snaps.pop(0)
                    if s["lastUpdateId"] >= u:
                        nxt = s
                        break
                if nxt is None:
                    print(f"  [gap] u={u} 이후 스냅샷 없음 → 이 구간 종료")
                    break
                ob.load_snapshot(nxt)
                synced = False
                stat["resyncs"] += 1
                continue

        ob.apply(ev)
        stat["applied"] += 1

        if ob.crossed():
            stat["crossed"] += 1

        # LOBMSG 출력
        ts_exch = int(ev.get("T") or ev["E"]) * 1_000_000  # ms -> ns
        ts_recv = ev["ts_recv"]
        for side_key, side in (("b", 0), ("a", 1)):
            for p, q in ev[side_key]:
                ip, iq = ob.px(p), ob.qty(q)
                mtype = MSG_DELETE if iq == 0 else MSG_LEVEL
                fbin.write(encode(mtype, side, ip, iq, u, ts_exch, ts_recv))

    fbin.close()

    # ── 스냅샷 대사(reconcile): 재구성 결과 vs 독립 스냅샷 ──
    for s in snaps:
        if ob.last_u is None or s["lastUpdateId"] > ob.last_u:
            continue
    print(f"\n재구성 완료: {binpath}")
    print(f"  통계: {stat}")
    print(f"  최종 북: bid {len(ob.bids)}레벨 / ask {len(ob.asks)}레벨")
    bb, ba = ob.best_bid(), ob.best_ask()
    if bb and ba:
        print(f"  best: {bb * float(tick):.4f} / {ba * float(tick):.4f} "
              f"(spread {(ba - bb)} tick)")
    return stat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./data")
    ap.add_argument("--market", default="futures")
    ap.add_argument("--symbol", default="btcusdt")
    ap.add_argument("--date", required=True)
    ap.add_argument("--tick", type=float, required=True, help="가격 틱 크기 (exchangeInfo에서 확인)")
    ap.add_argument("--lot", type=float, required=True, help="수량 최소 단위")
    ap.add_argument("--out", default="./rebuilt")
    a = ap.parse_args()
    rebuild(a.data, a.market, a.symbol, a.date, a.tick, a.lot, a.out)


if __name__ == "__main__":
    main()
