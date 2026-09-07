# LOB Market-Making Simulation — 상세 기획안 (v2)

**부제:** 호가창 리플레이 시뮬레이터 위에서 마켓메이킹의 트레이드오프를 검증하고,
L3 데이터로 체결 가정의 정답을 만들며, 하드웨어 피드 핸들러로 레이턴시를 실측하는 프로젝트

> v1 대비 변경: Phase 0.5(L3 큐 정답) 신설 · Phase 4를 페어밸류+헤지로 교체 ·
> 국내 KRX/NXT 모듈 스트레치 추가 · 데이터/서버 선택 확정

---

## 1. 문제의식

### 1.1 배경

- 금융 데이터는 노이즈·비정상성·불균형 때문에 "좋은 데이터"라는 개념이 성립하지 않고,
  평가함수 자체가 불안정하다.
- AI/자동화된 탐색은 잘못된 평가함수 아래에서 알파가 아니라 백테스트의 허점을 먼저 찾는다.
- 따라서 **신뢰할 수 있는 평가함수를 만드는 것이 먼저**이며, 손익 구조와 검증 가정이
  명확한 MM 문제로 범위를 제한한다.

### 1.2 이 프로젝트의 답

**손익 4분해 + 가정 민감도 + 가정의 정답 검증**이 곧 신뢰할 수 있는 평가함수가 된다.

기존 기획은 "가정을 나열하고 민감도를 봤다"에서 멈췄다. L3 데이터를 쓰면
**"가정을 검증했다"**로 나아간다. 이게 질적 차이다.

### 1.3 하지 않는 것

- 실전 자금 투입 트레이딩
- 예측 알파 발굴 (시그널은 보조 입력으로만)
- FPGA를 실전 주문 경로에 연결
- 멀티 심볼·멀티 거래소 동시 호가 (스트레치)

---

## 2. 배경 지식 (팀 온보딩용)

### 2.1 MM 손익 분해

```
MM 총손익 = 스프레드 수익 − 역선택 비용 ± 재고 손익 − 헤지 비용 − 수수료
```

| 항목 | 측정 방법 |
|---|---|
| 스프레드 수익 | Σ (mid_t − fill_price) × qty × side |
| 역선택 비용 | Σ (mid_{t+h} − mid_t) × qty × side, h = 1s/5s/30s/5min |
| 재고 손익 | Σ inventory_t × (mid_{t+1} − mid_t) |
| 헤지 비용 | 헤지 체결가 vs 헤지 시점 페어밸류 |
| 수수료 | maker/taker fee |

**회계 원칙:** 총손익은 `Cash + Inventory × Mid − Fees`로 먼저 대사(reconcile)하고,
분해는 원인을 설명하는 진단 프레임으로만 쓴다. 분해 합계와 총손익의 차이(residual)는
반드시 리포트에 표기한다.

### 2.2 Avellaneda–Stoikov (2008)

- 재고 q가 쌓일수록 reservation price를 반대로 이동: `r = s − q·γ·σ²·(T−t)`
- 최적 스프레드: `δ_bid + δ_ask = γσ²(T−t) + (2/γ)·ln(1 + γ/k)`
- 체결 강도 `λ(δ) = A·e^{−kδ}` — 실제 데이터에서 형태와 파라미터를 재추정한다

### 2.3 백테스트가 거짓말하는 지점

| 함정 | 내용 | 대응 |
|---|---|---|
| 큐 순위 | 가격이 닿았다 ≠ 체결됐다 | **L3로 정답 생성 후 캘리브레이션** (Phase 0.5) |
| 레이턴시 | 관측→도달 사이 시장이 움직임 | **실측 분포 주입** (FPGA 트랙) |
| 짧은 표본 | 일별 손익은 잡음이 실력을 압도 | 블록 부트스트랩 CI |
| 다중 비교 | 그리드를 돌리면 우연히 좋은 조합이 나옴 | `search_count` 기록, OOS 분리 |

### 2.4 실제 MM 회사에서 FPGA의 역할

전략 로직은 소프트웨어다. FPGA는 셋에만 쓰인다:
피드 핸들러/북 빌더, pre-trade risk check, 주문 게이트웨이.
이 프로젝트는 ①을 메인, ②를 스트레치, ③은 하지 않는다.

---

## 3. 시스템 아키텍처

```
┌──────────────────────────────────────────────────────────────────┐
│ Layer 0: 데이터                                                    │
│  Binance WS (L2 diff + trades) → 수집기 → JSONL.gz  [본체, 연속]   │
│  Tardis Coinbase full 채널 (L3)                     [Phase 0.5]   │
│  → LOBMSG 48B 바이너리 변환                                        │
└─────────────┬───────────────────────────┬────────────────────────┘
              │                           │
┌─────────────▼──────────────┐ ┌──────────▼───────────────────────┐
│ Layer 1a: SW 북 빌더        │ │ Layer 1b: HW 북 빌더 (FPGA)       │
│  Python (참조 구현)         │ │  Verilog/HLS, BRAM 가격 인덱스    │
│  C++ (성능 비교)            │ │  → top-of-book, microprice       │
└─────────────┬──────────────┘ └──────────┬───────────────────────┘
              │   bit-exact 검증 ◄─────────┘
              │   레이턴시 실측 (Python / C++ / FPGA)
┌─────────────▼────────────────────────────────────────────────────┐
│ Layer 2: 리플레이 시뮬레이터                                        │
│  이벤트 드리븐 · 큐 모델(L3 캘리브레이션) · 레이턴시 모델(실측)      │
│  체결 엔진 · 수수료 · 시장가 청산 슬리피지 · 헤지 레그               │
└─────────────┬────────────────────────────────────────────────────┘
              │
┌─────────────▼────────────────────────────────────────────────────┐
│ Layer 3: 전략                                                     │
│  Naive ±k tick → A-S 재고 인지 → 페어밸류 + 헤지                   │
└─────────────┬────────────────────────────────────────────────────┘
              │
┌─────────────▼────────────────────────────────────────────────────┐
│ Layer 4: 평가·리포트                                              │
│  손익 4분해 · 부트스트랩 CI · 민감도 히트맵 · FAILURES.md           │
└──────────────────────────────────────────────────────────────────┘
```

### 3.1 LOBMSG — 팀 공통 바이너리 포맷

FPGA와 소프트웨어가 동일 입력을 받도록 **2주차에 고정**한다.

| offset | size | field | 비고 |
|---|---|---|---|
| 0 | 1 | msg_type | 0=LEVEL 1=DELETE 2=TRADE 3=SNAP_BEGIN 4=SNAP_END |
| 1 | 1 | side | 0=bid 1=ask |
| 2 | 6 | padding | 48B 정렬 |
| 8 | 8 | price | **정수 틱** (uint64, LE) |
| 16 | 8 | qty | **정수 lot** (uint64) |
| 24 | 8 | seq | 거래소 update id |
| 32 | 8 | ts_exch | 거래소 시각 (ns) |
| 40 | 8 | ts_recv | 수집기 수신 시각 (ns) |

**총 48 bytes.** 고정폭 → FPGA 파싱이 시프트만으로 끝난다.
정수 가격/수량 → 부동소수점 IP 불필요, bit-exact 비교 가능.

---

## 4. Phase별 계획

### Phase 0 — 인프라 (3주)

| 작업 | 상세 | 담당 |
|---|---|---|
| L2 수집기 | Binance WS depth diff + aggTrade, 시퀀스 갭 감지, JSONL.gz 시간별 롤링 | SW |
| 호가창 재구성 (Python) | 스냅샷 + diff, 참조 구현 | SW |
| LOBMSG 변환기 | JSONL → 48B 바이너리 | SW |
| 리플레이 시뮬레이터 v0 | 이벤트 루프, 전략 API, 주문 API | SW |
| 큐 위치 모델 | 3모드 (아래) | SW |
| 레이턴시 모델 | 고정값 → 분포 샘플링 교체 가능 인터페이스 | SW |
| FPGA 환경 | 툴체인, PC↔보드 통신, 루프백 측정 | FPGA |

**큐 위치 3모드 (Phase 0.5에서 캘리브레이션 대상이 됨)**

| 모드 | 규칙 |
|---|---|
| 비관 (기본값) | 앞 물량이 **체결로** 전부 빠져야 내 차례. 취소는 안 쳐줌 |
| 중립 | 잔량 감소를 체결·취소 비율로 안분 |
| 낙관 | 가격이 닿으면 체결 (상한선) |

**완료 기준:** 24시간 무중단 수집, `quality.py` 전 항목 PASS, 재구성 북이
스냅샷과 일치, crossed book 0건, LOBMSG 생성 확인.

→ 구현 상세: `03_DATA_COLLECTION.md`, 서버: `04_DEPLOY_ORACLE.md`

---

### Phase 0.5 — L3 큐 정답 연구 ★신규 (2~3주)

**목표: 큐 위치를 "가정"에서 "측정"으로 바꾼다. 이 프로젝트의 핵심 기여.**

1. Tardis 무료 구간(매월 1일)에서 Coinbase BTC-USD `full` 채널 수집 → 60~90일
2. L3 북 재구성 (`code/l3_queue.py`의 `L3Book`)
3. 가상 주문(`VirtualOrder`)을 시간 × 가격 레벨 × 시장국면 격자로 수만 건 배치
   → **정답 라벨 생성** (배치 시점 앞 물량, 체결 여부, 체결까지 시간,
   앞 물량이 체결로 줄었는지 취소로 줄었는지)
4. 동일 데이터를 L2로 다운샘플 → 3모드 예측 생성
5. **혼동행렬**: 정답 vs 각 모드
   - **False Positive** = 존재하지 않는 체결 → 백테스트에만 있는 수익
   - **False Negative** = 놓친 체결 → 과도한 보수성
6. 캘리브레이션: 취소 비율 등을 파라미터로 둔 개선 모델을 정답에 피팅
7. Binance L2에 전이 적용, **전이 오차의 한계를 명시**

**이미 나온 결과:** 앞 물량 8 중 5는 체결, 3은 취소 → 실제로는 체결됐는데
비관 모드는 "체결 안 됨" 예측. **"비관 = 안전"이라는 전제가 틀렸다.**

→ 상세: `05_L3_DATA_RESEARCH.md`

---

### Phase 1 — Naive MM + 회계 틀 (2주)

- 전략: 미드 ± k틱 상시 양방향, 재고 무시
- 손익 대사: `Cash + Inv × Mid − Fees` vs 4분해 합계, residual 기록
- 역선택 horizon 복수 계산 (1s/5s/30s/5min)
- k = 1,2,3,5틱 × 큐 3모드 → 12조합

**기대:** 낙관에서 수익, 비관에서 손실. 손실 대부분이 역선택으로 설명됨.
**"가정 하나로 부호가 뒤집힌다"가 첫 번째 핵심 메시지.**

---

### Phase 2 — 재고 인지 MM (3주)

- λ(δ) 실측: 호가 거리별 체결 강도, 지수 형태 검증
- A-S 구현: γ, k, σ 추정 방식 명시 (rolling realized vol)
- 재고 한도 도달 시 시장가 청산 → 슬리피지 반영
- γ 그리드 (0.01~10, log) × 큐 3모드
- 출력: x축 평균 스프레드, y축 역선택/스프레드수익 비율 → **트레이드오프 곡선**

**기대:** 비관 모드에서 손익 분기 γ 구간 확인. 없으면 그것도 결과 —
"이 심볼·이 기간엔 순수 MM으로 수익 불가"를 CI와 함께 문서화.

---

### Phase 3 — 가정 스트레스 + 레이턴시 실측 (3주)

**3a. 레이턴시 실측 (FPGA 트랙 합류)**

동일 LOBMSG를 세 경로로 흘려 "수신 → top-of-book 갱신 → 호가 결정 출력" 측정.

| 경로 | 측정 | 예상 자릿수 |
|---|---|---|
| Python | `perf_counter_ns()` | 수십~수백 µs |
| C++ | `steady_clock` | 수 µs |
| FPGA | 사이클 카운터 / 오실로스코프 | 수백 ns |

여기에 **네트워크 왕복 지연**(페이퍼 트레이딩에서 실측)을 더해 end-to-end 분포 생성.
**결론은 아마 "처리 지연은 네트워크 지연에 묻힌다"일 텐데, 그걸 실측으로 보이는 게 가치다.**

**3b. 스트레스 그리드**

```
큐 모드(3, + 캘리브레이션 모델) × 레이턴시(4: 0 / Python / C++ / p99 네트워크)
× 수수료(3: 리베이트 / 0 / taker급) = 36+ 셀 × 전략 2종
```

- 각 셀: 총손익, Sharpe, 부트스트랩 95% CI
- **생존 전략 정의:** 비관 큐 + p99 레이턴시 + 0 리베이트에서 CI 하한 ≥ 0

---

### Phase 4 — 페어밸류 호가 + 헤지 (ETF LP 축소판) (3주)

| 역할 | ETF LP 실제 | 이 프로젝트 |
|---|---|---|
| 호가 대상 | ETF | 거래소 A (유동성 낮은 곳) |
| 페어밸류 원천 | 바스켓 / 지수선물 / iNAV | 거래소 B 미드 또는 perp |
| 헤지 수단 | 선물 / 바스켓 | 거래소 B perp |
| 괴리 요인 | 프리미엄/디스카운트, 환율 | 거래소 간 베이시스, 펀딩비 |

**구현**
- 거래소 B 데이터 동시 수집 (Phase 0 수집기 재사용)
- 페어밸류 = B 미드 + 베이시스 추정(rolling median)
- 호가 = 페어밸류 ± 스프레드, 재고에 따라 스큐
- 재고 임계치 초과 시 B에서 헤지, 헤지 레그도 큐/레이턴시 모델 적용
- 손익 4분해에 **헤지 비용** 활성화, 순재고(A−B) 기준 재고 손익

**실험:** 헤지 임계치 × 헤지 방식(taker/maker) × 베이시스 윈도우.
두 거래소 레이턴시 차이가 만드는 **stale fair value 역선택** 측정.

**기대:** 헤지로 재고 손익 분산은 줄지만 헤지 비용이 스프레드 수익을 잠식.
이 곡선이 ETF LP 면접 직결 소재.

---

### Phase 5 — 라이브 갭 (페이퍼 3~4주, Phase 3~4와 병행)

- 페이퍼 모드(주문 미전송, 로컬 체결 판정) 또는 테스트넷
- 동일 전략·기간을 (i) 라이브 페이퍼, (ii) 사후 백테스트 → 차이 분해
  - 데이터 갭 / 레이턴시 갭 / 체결 갭
- 결과를 `FAILURES.md`에 "시뮬레이터가 거짓말한 목록"으로 기록

---

### FPGA 트랙 (Phase 0~3 병렬, 8~10주)

**원칙: 크리티컬 패스에 두지 않는다.**

| 단계 | 내용 | 기간 |
|---|---|---|
| F0 | 보드 툴체인, PC↔보드 UDP/DMA, 루프백 측정 | 1~2주 |
| F1 | 48B 파서 + 북 빌더 MVP (BRAM 가격 인덱스, 양쪽 64레벨, 심볼 1개) | 3~4주 |
| F2 | Python 참조 구현과 **bit-exact 검증** (하루치 수백만 메시지) | 1~2주 |
| F3 | 레이턴시·처리량 측정 → Phase 3 합류 | 1주 |
| F4 | (스트레치) 하드웨어 pre-trade risk check | — |

보드가 Zynq/PYNQ 계열이면 ARM에서 Python으로 DMA 제어가 가능해 통합이 훨씬 쉽다.
순수 FPGA면 UDP 수신 IP가 필요하다.

---

### (스트레치) 국내 KRX vs NXT 관측 연구 (2~3주)

Phase 0~3이 안정적으로 끝나고 팀 여력이 있을 때만.

- KIS API로 KRX + NXT 동일 종목 10호가·체결 동시 수집
- 스프레드·깊이·체결 분포 비교
- 가격 발견 선도성 (리드-래그, 정보 점유율)
- 중간가호가가 유효 스프레드에 미치는 영향
- ETF에서 KRX-NXT 괴리와 iNAV 프리미엄 관계

**백테스트가 아니라 미시구조 실증 연구**라 공매도 제약·큐 문제가 안 걸린다.
국내 증권사 ETF LP 지원 시 강력한 소재.

---

## 5. 일정 (16주)

| 주차 | SW 트랙 | FPGA 트랙 |
|---|---|---|
| 1~3 | Phase 0 인프라 | F0 + LOBMSG 포맷 합의 |
| 3~5 | **Phase 0.5 L3 큐 정답** (병행) | F1 파서/북 빌더 |
| 4~5 | Phase 1 Naive + 회계 | F1 계속 |
| 6~8 | Phase 2 A-S | F2 검증 |
| 9~11 | Phase 3 스트레스 (F3 합류) | F3 → F4 |
| 9~ | Phase 5 페이퍼 시작 (병행) | |
| 12~14 | Phase 4 페어밸류+헤지 | F4 / 문서화 |
| 15~16 | Phase 5 갭 분석, 최종 리포트 | 최종 리포트 |

**시간이 모자랄 때 자르는 순서:**
F4 → 국내 NXT 모듈 → Phase 4 패시브 헤지 변형 → Phase 5 실주문 캘리브레이션 → Phase 4 전체.
**Phase 0 / 0.5 / 1 / 2 / 3 + F1~F3은 반드시 완성.**

---

## 6. 팀 역할

| 역할 | 담당 | 인원 |
|---|---|---|
| 데이터·인프라 | 수집기, 재구성, LOBMSG, 데이터 품질, 서버 운영 | 1 |
| 시뮬레이터 | 리플레이 엔진, 큐/레이턴시 모델, 체결 엔진 | 1 |
| 전략·평가 | A-S, 페어밸류 MM, 회계, 부트스트랩, 히트맵 | 1 |
| L3 연구 | Phase 0.5 (시뮬레이터 담당 겸임 가능) | 0~1 |
| FPGA | F0~F4 | 1 |

**인터페이스 계약(LOBMSG, 전략 API, 리포트 스키마)은 2주차에 확정,
이후 변경은 회의 기록으로 남긴다.**

---

## 7. 리포트 스키마

```json
{
  "run_id": "...",
  "strategy": "as_gamma_0.5",
  "assumptions": {"queue": "calibrated_v2", "latency": "cpp_measured_p50", "fee_bps": 0},
  "period": ["2026-10-01", "2026-10-14"],
  "pnl_total": -123.4,
  "pnl_reconciled": -123.4,
  "decomp": {"spread": 450.1, "adverse_1s": -380.2, "adverse_30s": -510.7,
             "inventory": -90.0, "hedge": 0.0, "fees": -103.3, "residual": 0.0},
  "sharpe_daily": -0.8,
  "ci95_pnl": [-410.2, 160.9],
  "n_fills": 12345,
  "fill_rate_vs_touch": 0.31,
  "queue_model_fp_rate": 0.08,
  "queue_model_fn_rate": 0.14,
  "max_inventory": 4.2,
  "search_count": 36
}
```

- `search_count`: 이 결과에 도달하기까지 돌린 조합 수. 다중 비교 보정 입력.
- `fill_rate_vs_touch`: 가격이 닿은 횟수 대비 실제 체결. 큐 모델 보수성 지표.
- `queue_model_fp/fn_rate`: Phase 0.5 정답 대비 오차율.

---

## 8. 산출물

| 산출물 | 용도 |
|---|---|
| 코드베이스 (GitHub) | 재현성, 포트폴리오 |
| 손익 4분해 리포트 | 면접 설명 자료 |
| 가정 민감도 히트맵 | 핵심 시각 자료 |
| **L3 정답 vs L2 모델 혼동행렬** | **최고 차별화 소재** |
| 스프레드–역선택 트레이드오프 곡선 | A-S 이해 증명 |
| 헤지 트레이드오프 곡선 | ETF LP 직결 |
| FPGA 검증 리포트 | 엔지니어링 역량 |
| 레이턴시 실측 분포 | "가정을 측정으로" 스토리 |
| 라이브 갭 분석 | 검증 방법론 |
| `FAILURES.md` | 정직성·성숙도 |

---

## 9. 리쥬메 문안

### 국내 증권사 ETF LP / 델타원

> - Reconstructed order-by-order (L3) books from Coinbase full-channel data to derive
>   **ground-truth queue positions**; benchmarked three L2 fill-assumption models against it
>   and showed the "conservative" model systematically **under**-counts fills in
>   cancellation-heavy regimes
> - Built an L2 order-book replay simulator with calibrated queue and measured-latency models;
>   decomposed market-making PnL into spread / adverse selection / inventory / hedging cost
>   and mapped strategy survival across a 36-cell assumption grid with bootstrap CIs
> - Implemented fair-value-based quoting with cross-venue hedging (ETF-LP analogue),
>   quantifying the inventory-variance vs. hedging-cost trade-off

### 외사 프롭 MM (Optiver, IMC, Flow, Citadel Securities)

> - Implemented an FPGA feed handler and order-book builder (Verilog/HLS) processing
>   X M msg/s at Y ns latency, bit-exact against a software reference over N M replayed messages
> - Derived ground-truth queue positions from L3 data and quantified the error of standard
>   L2 fill assumptions; showed naive MM PnL flips sign under realistic fill modeling
> - Reconciled backtest vs. paper-trading PnL over 4 weeks and attributed the gap to
>   data, latency, and fill-model components

### 면접 예상 질문 대응

| 질문 | 답의 재료 |
|---|---|
| MM은 어디서 벌고 어디서 잃나? | 4분해 숫자, horizon별 역선택 실현 |
| 백테스트를 얼마나 믿나? | 히트맵, 부호가 뒤집힌 사례, `FAILURES.md` |
| 체결 가정을 어떻게 검증했나? | **L3 정답, 혼동행렬, 캘리브레이션** |
| 재고 관리는? | γ 스윕 곡선, 헤지 임계치 트레이드오프 |
| ETF LP에서 iNAV 대비 호가는? | stale fair value가 만드는 역선택 |
| FPGA를 왜 썼나? 실전에 쓸 수 있나? | "전략이 아니라 피드 핸들러. 크립토에선 처리 지연이 네트워크에 묻힌다는 걸 실측으로 보였다" |

---

## 10. 리스크와 대응

| 리스크 | 대응 |
|---|---|
| FPGA 트랙 지연 | 병렬 트랙, F1을 top-of-book만으로 축소 |
| 데이터 수집 누락 | 시퀀스 갭 감지 + 재동기화, 갭 구간 제외 후 **기록** |
| 무료 서버 정책 변경 | rclone 백업, 로컬 사본 유지 |
| 비관 가정에서 아무 전략도 수익 불가 | 그 자체가 결과. CI와 함께 보고 |
| 다중 비교 과적합 | `search_count` 기록, OOS 사전 고정 |
| Tardis 무료 구간 축소 | 초기에 90일치 미리 다운로드 |
| 팀원 이탈 | 인터페이스 계약 문서화 |
| 거래소 API 변경 | 거래소별 어댑터 분리 |

---

## 11. 정직한 한계 (FAILURES.md에 명시할 것)

- Coinbase는 **미국 거래소**라 아시아 시간대 유동성 패턴이 Binance/업비트와 다르다.
  캘리브레이션 결과의 전이 가능성은 **검증 대상이지 전제가 아니다.**
- Tardis 무료 구간이 매월 1일이라 **월초 효과** 가능성. 표본 편향으로 기록.
- L3에서도 알 수 없는 것: **내 주문이 실제로 들어갔을 때 시장이 어떻게 반응했을지.**
  가상 주문은 시장에 영향을 주지 않는다고 가정한다. 소액이면 합리적이지만 가정은 가정이다.
- Phase 5 체결 갭은 **부분 검증만** 가능하다 (실주문 없이는 큐 모델을 완전히 검증 불가).
