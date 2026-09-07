# Phase 0 — L2 호가 데이터 수집 가이드

## 0. 대원칙

**수집기는 재구성하지 않는다.** 받은 원본 메시지를 `ts_recv`와 함께 그대로 저장하고,
호가창 재구성은 오프라인에서 한다.

이유:
- 재구성 로직에 버그가 있어도 데이터를 다시 모을 필요가 없다 (재구성만 다시 돌리면 됨)
- FPGA 북 빌더와 소프트웨어 참조 구현이 **동일한 입력**을 받아야 bit-exact 비교가 가능하다
- 수집 시점에는 어떤 파생 지표가 필요할지 모른다. 원본이 있으면 나중에 다 만들 수 있다

실제 MM 회사도 이 구조다 (raw capture → offline replay).

---

## 1. 무엇을 수집하는가

| 대상 | 스트림 | 왜 필요한가 |
|---|---|---|
| L2 호가 변화 | `depthUpdate` (diff) | 호가창 재구성의 본체 |
| 체결 | `aggTrade` / `trade` | 큐 소진 중 "체결분"과 "취소분"을 분리하는 데 필수 |
| 주기적 스냅샷 | REST `/depth` | 재구성 시작점 + 독립적 검증 기준점 |

**체결 스트림이 왜 중요한가:** 큐 위치 모델의 핵심은 "내 앞의 물량이 줄었는데,
그게 체결된 건가 취소된 건가"다. 체결이면 내 순번이 앞당겨지지만, 취소면 상황에 따라
다르다. depth diff만으로는 이 둘을 구분할 수 없다. trade 스트림과 대조해야 한다.

---

## 2. 거래소·심볼 선택

### 수집 대상 (실주문 없이 공개 시세만 사용)

| 후보 | 장점 | 단점 |
|---|---|---|
| **Binance USDT-M perp** (권장) | `@0ms` 실시간 diff, 거래량 최대, 메이커 수수료 구조가 MM에 현실적 | — |
| Binance spot | 스팟 기준 | diff 최소 주기 100ms |
| Bybit / OKX perp | 크로스 비교용 | 프로토콜 각각 다름 |
| **업비트** | Phase 4 김치 프리미엄 / Phase 5 실주문 검증용 | 스냅샷 방식, 깊이 제한 |

**추천 조합**
- 주 심볼: `BTCUSDT` perp (Binance) — 유동성 최대, 스프레드 1틱 고정에 가까움
- 부 심볼: `ETHUSDT` perp — 비교군
- 알트 1개 (예: `SOLUSDT`) — 스프레드가 넓어 MM 수익 구조가 실제로 보이는 케이스
- Phase 4용: 업비트 `KRW-BTC` (호가 대상) + Binance `BTCUSDT` (페어밸류 원천)

> 스프레드가 항상 1틱인 심볼만 보면 "스프레드를 좁힌다/넓힌다"는 의사결정 자체가
> 존재하지 않는다. 반드시 스프레드가 변동하는 심볼을 하나 넣을 것.

### 틱 크기 확인 (반드시)

```bash
curl -s "https://fapi.binance.com/fapi/v1/exchangeInfo" \
  | python3 -c "
import sys,json
d=json.load(sys.stdin)
for s in d['symbols']:
    if s['symbol'] in ('BTCUSDT','ETHUSDT','SOLUSDT'):
        f={x['filterType']:x for x in s['filters']}
        print(s['symbol'], 'tick', f['PRICE_FILTER']['tickSize'], 'lot', f['LOT_SIZE']['stepSize'])
"
```

틱 크기를 잘못 잡으면 서로 다른 가격이 같은 정수로 뭉개져 **데이터가 조용히 오염된다.**
`rebuild.py`는 틱에 정렬되지 않은 가격을 만나면 예외를 던지고 죽는다 (의도된 동작).
또한 거래소가 틱 크기를 바꾸는 경우가 있으므로 날짜별로 기록해 둘 것.

---

## 3. Binance depth diff 적용 규칙

가장 많이 틀리는 부분이다. 공식 문서 기준 절차:

1. WebSocket diff 스트림을 먼저 연결하고 **버퍼링 시작**
2. REST 스냅샷을 받는다 → `lastUpdateId = L`
3. `u <= L` 인 이벤트는 버린다 (이미 스냅샷에 반영됨)
4. 첫 적용 이벤트는 `U <= L+1 <= u` 를 만족해야 한다
5. 이후 모든 이벤트는 `U == 직전 u + 1`. 아니면 갭 → 스냅샷부터 재동기화
6. `qty == 0` 이면 해당 가격 레벨 **삭제**, 아니면 **절대량으로 덮어쓰기** (누적 아님)

`rebuild.py`가 이 로직을 구현하고 있고, 갭 발생 시 다음 스냅샷으로 자동 재동기화한다.

**흔한 실수**
- `qty`를 누적으로 처리 → 북이 무한히 커짐
- 재접속 후 시퀀스를 이어서 검사 → 항상 갭으로 잡힘 (재접속 시 `prev_u` 리셋 필요)
- float로 가격 저장 → 같은 레벨이 두 개로 갈라짐

---

## 4. 파일 구조

```
data/
  market=futures/
    symbol=btcusdt/
      kind=depth/    date=2026-09-02/  20260902T000000Z.jsonl.gz  (시간별 롤링)
      kind=trade/    date=2026-09-02/  ...
      kind=snapshot/ date=2026-09-02/  20260902T000000Z.jsonl.gz  (일별)
  gaps_btcusdt_futures_*.json
```

- **JSONL + gzip**: 프로세스가 죽어도 이미 flush된 라인은 살아남는다.
  Parquet은 파일을 닫아야 읽히므로 수집 단계에는 부적합. 변환은 오프라인에서.
- 시간 단위 롤링: 한 파일이 깨져도 1시간치만 잃는다
- `kind=`, `date=` 파티션: 나중에 pandas/duckdb로 바로 읽힌다

---

## 5. 실행

```bash
pip install websockets aiohttp

# 수집 (24시간 상시)
python collector.py --symbol btcusdt --market futures --out ./data --snap-interval 300

# 매일 아침 품질 점검
python quality.py --data ./data --market futures --symbol btcusdt --date 2026-09-02

# 오프라인 재구성 + LOBMSG 생성
python rebuild.py --data ./data --market futures --symbol btcusdt \
    --date 2026-09-02 --tick 0.1 --lot 0.001 --out ./rebuilt
```

### 상시 운영

집 PC는 재부팅·정전·인터넷 끊김으로 데이터에 구멍이 생긴다. 저렴한 VPS 권장
(도쿄/싱가포르 리전이 Binance에 가깝다 — 수신 지연도 줄고 Phase 3 레이턴시
실측의 현실성도 올라간다).

```ini
# /etc/systemd/system/lobcap.service
[Unit]
Description=LOB collector
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/lobcap
ExecStart=/usr/bin/python3 collector.py --symbol btcusdt --market futures --out /data
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now lobcap
```

심볼별로 별도 프로세스를 띄운다 (`lobcap@btcusdt.service` 형태의 템플릿 유닛 권장).

### 용량 추정

| 심볼 | 대략 msg/s | 원본 JSON | gzip 후 |
|---|---|---|---|
| BTCUSDT perp @0ms | 50~200 | ~2 GB/일 | ~200 MB/일 |
| ETHUSDT perp | 30~100 | ~1 GB/일 | ~100 MB/일 |
| 알트 1개 | 5~30 | ~300 MB/일 | ~30 MB/일 |

3심볼 × 3개월 ≈ **30 GB (압축 기준)**. VPS 디스크 100GB면 충분하다.
처음 하루 돌려보고 실측한 뒤 확정할 것.

---

## 6. 품질 기준 — 언제 데이터를 버리는가

`quality.py` 판정 기준 (조정 가능):

| 항목 | 기준 | 이유 |
|---|---|---|
| 수집 시간 | ≥ 23h | 하루의 일부만 있으면 시간대 편향 발생 |
| 시퀀스 갭 | ≤ 5건 | 갭 구간은 북이 부정확 |
| 스냅샷 수 | ≥ 12개 | 재동기화 지점과 검증 기준점 확보 |
| p99 수신 지연 | < 1000ms | 네트워크 문제 감지 |
| 빈 시간대 | 없음 | 프로세스 중단 감지 |

**중요:** 기준 미달 날짜는 실험에서 제외하되, **제외했다는 사실과 사유를 기록**한다.
조용히 빼면 결과가 좋아 보이게 만드는 데이터 스누핑이 된다. `FAILURES.md`에 남긴다.

---

## 7. LOBMSG — FPGA와 공유하는 48바이트 포맷

```
offset  size  field
  0      1    msg_type   0=LEVEL 1=DELETE 2=TRADE 3=SNAP_BEGIN 4=SNAP_END
  1      1    side       0=bid 1=ask
  2      6    padding    (48바이트 정렬)
  8      8    price      정수 틱 (uint64, little-endian)
 16      8    qty        정수 lot (uint64)
 24      8    seq        거래소 update id
 32      8    ts_exch    거래소 시각 (ns)
 40      8    ts_recv    수집기 수신 시각 (ns)
```

- 고정폭 → FPGA 파싱이 시프트 연산만으로 끝난다
- 정수 가격/수량 → 부동소수점 IP 불필요, bit-exact 비교 가능
- 48바이트 정렬 → DMA 버스트에 유리

**FPGA 팀원과 2주차 안에 이 포맷을 확정하고 문서를 고정할 것.**
이후 변경은 양쪽 재작업을 유발한다.

---

## 8. Phase 0 완료 기준 체크리스트

- [ ] 거래소·심볼 확정, 틱/lot 크기 기록
- [ ] 수집기 24시간 무중단 성공, `quality.py` 전 항목 PASS
- [ ] `rebuild.py`로 재구성한 북이 독립 스냅샷과 매 재동기화 지점에서 일치
- [ ] crossed book (best_bid >= best_ask) 발생 0건
- [ ] LOBMSG 파일 생성 확인, 메시지 수 = 파일크기 / 48
- [ ] 시퀀스 갭 로그가 남는지 인위적 테스트 (WS 강제 끊고 재접속)
- [ ] LOBMSG 포맷 문서 확정 → FPGA 팀원에게 전달
- [ ] 일 단위 용량 실측 → 3개월 저장 계획 수립
- [ ] `FAILURES.md` 생성

---

## 9. 다음 단계

Phase 0가 끝나면 이 데이터 위에:
1. 큐 위치 모델 (비관/중립/낙관) — trade 스트림으로 체결/취소 분리
2. 리플레이 시뮬레이터 이벤트 루프
3. FPGA 북 빌더 (동일 LOBMSG 입력, `rebuild.py`와 bit-exact 대조)

가 올라간다.
