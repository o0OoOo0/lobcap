"""
quality.py — 수집 데이터 품질 점검

매일 자동으로 돌려서 통과 못 하면 그 날짜 데이터는 실험에서 제외한다.
"제외했다"는 사실 자체도 기록해야 결과가 정직해진다.

사용법
  python quality.py --data ./data --market futures --symbol btcusdt --date 2026-09-02
"""

import argparse
import glob
import gzip
import json
import os
from collections import Counter


def read_jsonl(pattern):
    for path in sorted(glob.glob(pattern)):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                yield json.loads(line)


def check(data, market, symbol, date):
    base = os.path.join(data, f"market={market}", f"symbol={symbol}")
    dp = os.path.join(base, "kind=depth", f"date={date}", "*.jsonl.gz")
    sp = os.path.join(base, "kind=snapshot", f"date={date}", "*.jsonl.gz")

    n = 0
    prev_u = None
    gaps = []
    lat = []          # 거래소 시각 -> 수신 시각 지연 (ms)
    per_hour = Counter()
    first_ts = last_ts = None

    for ev in read_jsonl(dp):
        n += 1
        U, u = ev["U"], ev["u"]
        if prev_u is not None and U != prev_u + 1:
            gaps.append((prev_u, U, U - prev_u - 1))
        prev_u = u

        ts_recv_ms = ev["ts_recv"] / 1e6
        ts_exch_ms = ev.get("T") or ev["E"]
        lat.append(ts_recv_ms - ts_exch_ms)

        first_ts = first_ts or ev["ts_recv"]
        last_ts = ev["ts_recv"]
        per_hour[int(ts_recv_ms // 3_600_000)] += 1

    snaps = list(read_jsonl(sp))

    if n == 0:
        print("depth 이벤트 없음 — 수집 실패")
        return False

    lat.sort()
    def pct(p):
        return lat[int(len(lat) * p)]

    dur_h = (last_ts - first_ts) / 1e9 / 3600
    missing = sum(g[2] for g in gaps)

    print(f"=== {symbol} {market} {date} ===")
    print(f"depth 이벤트   : {n:,}")
    print(f"수집 시간      : {dur_h:.2f} h  (평균 {n/max(dur_h*3600,1):.1f} msg/s)")
    print(f"스냅샷         : {len(snaps)}개")
    print(f"시퀀스 갭      : {len(gaps)}건 / 누락 update {missing:,}")
    print(f"수신 지연(ms)  : p50 {pct(0.5):.1f}  p95 {pct(0.95):.1f}  p99 {pct(0.99):.1f}")
    if per_hour:
        lo = min(per_hour.values())
        hi = max(per_hour.values())
        print(f"시간대 편차    : min {lo:,} / max {hi:,} msg/h")

    # 합격 기준 (프로젝트 상황에 맞게 조정)
    checks = {
        "수집 시간 >= 23h": dur_h >= 23,
        "시퀀스 갭 <= 5건": len(gaps) <= 5,
        "스냅샷 >= 12개": len(snaps) >= 12,
        "p99 지연 < 1000ms": pct(0.99) < 1000,
        "빈 시간대 없음": len(per_hour) >= int(dur_h) - 1,
    }
    print("\n판정:")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    ok = all(checks.values())
    print(f"\n=> {'사용 가능' if ok else '실험에서 제외 (사유 기록할 것)'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./data")
    ap.add_argument("--market", default="futures")
    ap.add_argument("--symbol", default="btcusdt")
    ap.add_argument("--date", required=True)
    a = ap.parse_args()
    check(a.data, a.market, a.symbol, a.date)


if __name__ == "__main__":
    main()
