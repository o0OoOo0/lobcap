# L3(주문 단위) 데이터 조사 — 큐 위치를 "가정"에서 "측정"으로

## 요약

**결론: 맞습니다. Coinbase는 다른 거래소가 안 주는 걸 줍니다. 그리고 무료로 쓸 수 있는
경로가 있습니다.** 이건 프로젝트의 성격을 바꿀 만한 발견입니다.

| 레벨 | 내용 | 큐 위치 계산 |
|---|---|---|
| L1 | best bid/ask | 불가 |
| L2 | 가격 레벨별 **합산** 잔량 (Binance, 업비트, 대부분) | **불가 — 가정할 수밖에 없음** |
| **L3 / MBO** | **개별 주문 단위** (order_id, 가격, 수량, 도착 순서) | **정확히 계산 가능** |

기존 기획의 가장 큰 약점은 "큐 위치를 관측할 수 없으니 비관/중립/낙관 3모드로 가정하고
민감도만 본다"였습니다. L3가 있으면 **정답을 만들 수 있고**, L2 기반 가정 모델을 그
정답에 대고 캘리브레이션할 수 있습니다.

---

## 1. Coinbase가 주는 것

Coinbase Exchange WebSocket의 `full` 채널은 주문 생애주기 전체를 개별 주문 단위로 보냅니다.

| 메시지 | 의미 | 북 변경 |
|---|---|---|
| `received` | 주문 접수 (아직 북에 없음) | ✗ |
| `open` | 주문이 북에 등재 — **여기서 큐 순위 확정** | ✓ |
| `match` | 체결. `maker_order_id`로 **어느 주문이 체결됐는지 특정** | ✓ |
| `done` | 종료. `reason: filled` vs `canceled` **구분됨** | ✓ |
| `change` | 수량 감소 (우선순위는 유지) | ✓ |

Coinbase는 price-time priority FIFO 매칭 엔진이므로 **`open` 메시지 도착 순서 = 큐 순서**입니다.
따라서 시각 t에 가격 p로 주문을 냈다면:

- 내 앞에 정확히 어떤 order_id들이 있었는지
- 그것들이 각각 **체결됐는지 취소됐는지**
- 내 주문이 언제 체결됐을지 (또는 안 됐을지)

를 전부 계산할 수 있습니다. **가정이 아니라 정답입니다.**

> Binance/업비트의 L2로는 이게 원리적으로 불가능합니다. 레벨 잔량이 100에서 80으로 줄었을 때
> 20이 체결인지 취소인지, 그게 내 앞인지 뒤인지 알 수 없기 때문입니다.

---

## 2. 접근 경로 — 여기가 함정입니다

### ✗ Coinbase 직접 연결: 사실상 불가

`full`, `level3`, `level2` 채널은 **인증이 필요**하고, 그 인증은
**Coinbase Exchange(기관용, exchange.coinbase.com) 계정**을 요구합니다.
리테일용 Coinbase Advanced Trade 계정으로는 발급되지 않습니다.
Exchange 계정은 심사를 거치는 기관 대상이라 학생 팀 프로젝트로는 현실적이지 않습니다.

(참고: Coinbase Pro는 2023년 12월 1일 종료되어 기존 무인증 경로도 막혔습니다.)

### ✓ Tardis.dev — 실제로 쓸 수 있는 경로

Tardis.dev가 Coinbase Exchange 데이터를 **2019-03-30부터** 아카이빙하고 있고,
`full` 채널의 모든 L3 메시지를 exchange-native 포맷 그대로 보관합니다.

캡처 채널: `received`, `open`, `done`, `change`, `match`, `full_snapshot`

`full_snapshot`은 Tardis가 생성한 채널입니다. Coinbase `full` 채널이 초기 스냅샷을 주지 않아서
REST에서 가져와 `"generated": true`로 삽입한 것이고, 이게 있어야 히스토리컬 L3 북 재구성이
가능합니다. 시퀀스 겹침 검증도 2020-06-11부터 적용돼 있습니다.

**핵심: API 키 없이 매월 1일 데이터를 받을 수 있습니다.**

2019년 3월 ~ 2026년 9월이면 **약 90일치 L3 데이터가 무료**입니다.
큐 모델 캘리브레이션에는 차고 넘칩니다.

```python
# pip install tardis-dev
import asyncio
from tardis_dev import Channel, replay

async def main():
    async for ts, msg in replay(
        exchange="coinbase",
        from_date="2024-01-01",       # 매월 1일만 무료
        to_date="2024-01-02",
        filters=[
            Channel("full_snapshot", ["BTC-USD"]),   # 재구성 시작점 (필수)
            Channel("open",   ["BTC-USD"]),
            Channel("done",   ["BTC-USD"]),
            Channel("match",  ["BTC-USD"]),
            Channel("change", ["BTC-USD"]),
        ],
        # api_key 생략 → 매월 1일만 접근
    ):
        print(ts, msg)

asyncio.run(main())
```

**주의 두 가지**

1. `download_datasets()`(CSV)는 **정규화된 L2**(`incremental_book_L2`, `trades`, `quotes`)만 줍니다.
   L3를 받으려면 반드시 위처럼 `replay()`(exchange-native)를 써야 합니다.
2. Tardis 문서에 **Coinbase 시장데이터 라이선스 제한**이 적용된다고 명시돼 있습니다.
   포트폴리오로 공개할 때는 파생 결과(통계·그래프)만 올리고 원본 데이터는 재배포하지 마세요.
   코드는 문제없습니다.

### 유료가 필요해질 때

연속된 기간이 필요하면 Tardis 유료 플랜을 봐야 하는데, 학생 팀에는 부담일 수 있습니다.
**무료 90일(매월 1일)로 충분한 이유**: 큐 모델 캘리브레이션은 연속성이 필요 없습니다.
서로 다른 90개 시장 국면의 표본이 오히려 더 좋습니다.

---

## 3. 주식 L3 — ETF LP 목표에는 이쪽이 더 직접적

크립토보다 **NASDAQ L3가 ETF LP·델타원 면접에서 더 잘 통합니다.**

| 소스 | 내용 | 접근 |
|---|---|---|
| **LOBSTER** | NASDAQ TotalView-ITCH 기반 재구성 LOB. message 파일 + orderbook 파일 | **샘플 파일 무료 공개**, 학술 계정 별도. R 패키지 `lobsteR` 있음 |
| **Databento** | NASDAQ/BX TotalView-ITCH **MBO 스키마** (주문 단위 원본) | 가입 시 무료 크레딧, Python SDK |

LOBSTER 샘플은 NASDAQ 공식 ITCH 샘플 기반이고 Matlab/R 데모 코드까지 제공됩니다.
**"NASDAQ ITCH MBO 데이터로 큐 다이내믹스를 분석했다"는 문장은 크립토 L3보다
증권사·외사 면접에서 훨씬 강하게 먹힙니다.**

권장: Coinbase L3로 파이프라인을 완성한 뒤, LOBSTER/Databento 샘플로 **같은 분석을
주식에 한 번 더 돌리는 것**. 코드가 이미 있으니 추가 비용이 적고, 리쥬메 문장이 하나 늘어납니다.

---

## 4. 프로젝트가 어떻게 바뀌는가

### Before (기존 기획)

> 큐 위치는 관측 불가 → 3모드로 **가정** → 민감도 히트맵만 제시
> 결론: "낙관 가정에서만 수익나는 전략과 비관에서도 사는 전략을 구분했다"

### After (L3 도입)

> Coinbase L3로 큐 위치 **정답**을 계산 → L2 가정 모델을 정답에 대고 **캘리브레이션**
> → 캘리브레이션된 모델을 L3가 없는 Binance/업비트에 **전이(transfer)**
> 결론: "우리 체결 모델은 실측 정답 대비 오차가 이만큼이고, 그 오차가 MM 손익에
> 미치는 영향은 이만큼이다"

**이건 질적으로 다른 결과물입니다.** 앞의 것은 "가정을 나열했다"이고,
뒤의 것은 "가정을 검증했다"입니다. 프로젝트 정체성인 "정직한 평가"가 비로소 완성됩니다.

### 새 Phase 삽입

```
Phase 0   인프라 (기존)
Phase 0.5 ★ L3 큐 정답 연구  ← 신규, 2~3주
Phase 1   Naive MM (기존)
Phase 2   재고 인지 MM (기존)
Phase 3   가정 스트레스 — 이제 "캘리브레이션된 모델 + 실측 오차범위"로 강화
...
```

**Phase 0.5 상세**

1. Tardis 무료 구간(매월 1일)에서 Coinbase BTC-USD `full` 채널 수집 → 60~90일
2. L3 북 재구성 (`l3_queue.py`의 `L3Book`)
3. 가상 주문(VirtualOrder)을 시간·가격·시장국면 격자로 수만 건 배치 → **정답 라벨 생성**
   - 배치 시점 앞 물량, 체결 여부, 체결까지 걸린 시간
   - 앞 물량이 **체결로 줄었는지 취소로 줄었는지** 분해
4. 동일 데이터를 L2로 다운샘플 → 3모드 예측 생성
5. **혼동행렬**: 정답 vs 각 모드
   - False Positive = 존재하지 않는 체결 → **백테스트에만 있는 수익**
   - False Negative = 놓친 체결 → 과도한 보수성
6. 캘리브레이션: 취소 비율 등을 파라미터로 둔 개선 모델을 정답에 피팅
7. Binance L2에 전이 적용, 전이 오차의 한계를 명시

### 첫 테스트에서 이미 나온 결과

`l3_queue.py` 실행 결과 — 앞 물량 8 중 **5는 체결, 3은 취소**되어 실제로는 체결됐는데:

| 모드 | 예측 | 판정 |
|---|---|---|
| 낙관 | 체결됨 | 정답 (우연히) |
| 중립 | 체결됨 | 정답 |
| **비관** | **체결 안 됨** | **오답 (FN)** |

**비관 가정이 항상 안전한 게 아닙니다.** 취소가 많은 시장에서는 체결을 놓치는 방향으로
틀리고, 그러면 MM 전략의 수익 기회를 과소평가합니다. 기존 기획은 "비관 = 보수적 = 안전"으로
전제했는데, 이건 검증 없이 믿을 게 아니었습니다. **L3 없이는 알 수 없던 사실이고,
이것 하나만으로도 Phase 0.5의 값을 합니다.**

---

## 5. 리쥬메 문장이 어떻게 바뀌나

**Before**
> Built an LOB replay simulator with parameterized queue-position assumptions

**After**
> Reconstructed order-by-order (L3/MBO) books from Coinbase full-channel data to derive
> **ground-truth queue positions**; benchmarked three L2 fill-assumption models against it
> and showed the "conservative" model systematically **under**-counts fills in
> cancellation-heavy regimes — then calibrated a corrected model and quantified its
> transfer error to L2-only venues

두 번째가 훨씬 강합니다. "가정했다"가 아니라 "통념을 검증해서 틀렸음을 보였다"이기 때문입니다.
Optiver/IMC/Flow 같은 곳은 정확히 이런 사고방식을 봅니다.

---

## 6. 즉시 할 일

- [ ] `pip install tardis-dev`, 무료 구간으로 Coinbase `full` 채널 하루치 받아보기
- [ ] `l3_queue.py`의 `L3Book`을 실제 데이터에 연결, 재구성 결과를 `full_snapshot`과 대사
- [ ] 가상 주문 배치 격자 설계 (시각 × 가격 레벨 × 시장국면)
- [ ] LOBSTER 샘플 파일 다운로드해서 주식 확장 가능성 확인
- [ ] Coinbase 데이터 라이선스 조항 읽고 공개 범위 결정

---

## 7. 정직한 한계

- Coinbase는 **미국 거래소**라 아시아 시간대 유동성 패턴이 Binance/업비트와 다릅니다.
  캘리브레이션 결과의 전이 가능성은 검증 대상이지 전제가 아닙니다.
- 무료 구간이 매월 1일이라 **월초 효과**가 있을 수 있습니다. 표본 편향으로 기록하세요.
- L3에서도 알 수 없는 게 하나 남습니다: **내 주문이 실제로 들어갔을 때 시장이 어떻게
  반응했을지.** 가상 주문은 시장에 영향을 주지 않는다고 가정합니다. 소액이면 합리적이지만
  가정인 건 맞고, 이건 `FAILURES.md`에 명시할 항목입니다.
