# HftBacktest 전체 문서와 Backtest Lab 대조 검토

검토일: 2026-09-10. 코드 기준: `feat/research-workspace-v1`, `e87a5ac`.

**결론: 전략 탐색용 작업 공간은 갖추었지만, 체결 정확도를 독립적으로 검증하고 신호 자체를 연구하는 계층이 부족하다.** 전략을 더 많이 넣기 전에 체결 규칙을 바로잡고, 신호 연구와 실행 가정을 분리해야 한다. HftBacktest는 독립 비교 엔진 및 설계 참고로 유용하다. 라이브 체결의 정답이나 시장 충격 측정 장치로 취급하면 안 된다.

## 검토 범위와 증거의 한계

공식 사이트 `/en/latest/`에서 연결된 본문·튜토리얼·API 문서 45개 URL을 수집하고 내용을 검토했다. 홈 `/`와 `/index.html` 중복을 제외하면 44개 문서다. 문서별 목록은 아래에, URL·본문 다운로드 SHA-256은 `HFTBACKTEST_DOCUMENTATION_INVENTORY.json`에 남겼다. 과거 버전, 자동 생성 소스 보기, 외부 GitHub 전체 코드 및 인용 논문 전체는 이 범위에 포함하지 않는다. 예제의 실행 결과를 재현한 검토도 아니다.

현재 프로젝트 코드를 정적으로 대조했다. 이번에는 서비스 변경·시뮬레이션·테스트 실행을 하지 않았다. 이전 Ubuntu 회귀 테스트 및 반복 실행 통과는 결정성과 구현 계약의 증거이며 실제 거래소 체결 정확도를 입증하지 않는다. 아래 수량 예시는 코드에서 도출한 사례이며 새로 실행한 테스트 결과가 아니다.

## 1. 이미 있는 기능과 남은 차이

| 영역 | 현재 구현 | 판단 |
|---|---|---|
| 데이터 | 스냅샷 부트스트랩, 연속성, `nq`, 거래/호가 귀속, 소스 체크섬 | 유지. 더 빠른 BBO 입력은 없음 |
| 시계 | exchange 모드와 recorded 도착 시계, 지연된 주문 보고 | 반영됨. 원래 MVP의 단일 시계 계획보다 발전했음 |
| 체결 | 공유 대기열 전이, 부분 체결, 취소 지연, serial replacement | 반영됨. 아래 체결 규칙 차이는 우선 검증 필요 |
| 위험 | 재고 예약, stale/post-only, 선택적 연구 정책의 손실·익스포저·헤지 제어 | 반영됨. 모든 경로의 동일한 거래소 규칙 적용은 미완 |
| 회계 | Decimal 원장, spread/MTM/fee 분해, markout, 손익분기 수수료 | 반영됨. 10분 Sharpe 제한도 유지해야 함 |
| 충격 | 거래 참여율 제한 및 명목 거래액 기준 비용 스트레스 | 가정 기반 진단. 실제 시장 반응이나 측정된 충격이 아님 |
| 탐색 | 제한된 수식, 계수/전략 파라미터 탐색, 실행 가정별 비교 | 반영됨. 신호 예측력·중복 전략·순위 안정성 분석 보강 필요 |
| 운영 | 제한된 직렬 실행, 취소, 저장 결과, 출처와 검증 증거 | 연구 운영 기반. 실제 주문 복구와 거래소 대조는 별도 |

근거: [arrival.py](E:/dev/lobcap-workspace/src/lobcap/backtest/arrival.py:29), [research_execution.py](E:/dev/lobcap-workspace/src/lobcap/backtest/research_execution.py:13), [diagnostics.py](E:/dev/lobcap-workspace/src/lobcap/backtest/diagnostics.py:35), [비교 계약](E:/dev/lobcap-workspace/src/lobcap/backtest/workspace/compare.py:35).

## 2. 전략 순위를 바꿀 수 있는 우선 검토 사항

### P0-A. 동일 가격 체결과 가격 관통 체결을 구분해야 한다

현재 [execution.py](E:/dev/lobcap-workspace/src/lobcap/backtest/execution.py:124)는 매수 주문 가격 이하의 매도 거래를 모두 같은 대기열 차감 함수에 전달한다. 예를 들어 매수 100, 앞 대기량 1, 주문량 0.001일 때 99.9의 거래량 0.001도 앞 대기량만 줄일 수 있다. 반면 HftBacktest는 이미 호가에 있던 주문의 가격을 거래가 엄격히 관통하는 경우를 동일 가격의 대기열 체결과 구분한다. [공식 체결 조건](https://hftbacktest.readthedocs.io/en/latest/order_fill.html)

이는 곧바로 HftBacktest 규칙으로 교체하라는 뜻은 아니다. Binance의 집계·분리 시퀀스·일시적인 불일치에서 관통 증거를 어떻게 해석할지 정해야 한다. 동일 가격, 엄격한 관통, 반대 BBO 이동, 데이터 공백을 별도 fixture로 만들고 양 엔진의 차이를 설명해야 한다. 체결 수가 늘면 손익이 좋아진다고도 단정할 수 없다.

### P0-B. 거래 건별 참여율 제한은 집계 방식에 민감하다

[현재 제한](E:/dev/lobcap-workspace/src/lobcap/backtest/execution.py:142)은 각 aggTrade의 수량에 참여율을 곱하고 lot 단위로 내린다. 참여율 10%, lot 0.001, 앞 대기량 0일 때:

| 같은 가격·총 거래량 | 허용 수량 |
|---|---:|
| 0.010 한 건 | 0.001 |
| 0.005 두 건 | 각 0, 합계 0 |

작은 거래 묶음이 많은 표본에서 체결을 크게 억제할 수 있다. 이것은 실제 대기열 추정과 다른 용량 스트레스 정책이다. 현행 동작은 테스트에 명시되어 있으나, 그 경제적 의미가 충분히 분리되어 있지 않다. 제한 전후 체결량·lot 내림 손실·집계 민감도를 보여주고, 기간별 참여율 예산 등 대안과 비교해야 한다. 단순히 제한을 풀어 PnL을 올리는 방식은 부적절하다.

### P0-C. lot 미만 체결 허용이 선택적 정책에 달려 있다

`Execution.enforce_lots` 기본값은 false이고 연구 정책을 넘길 때 true로 바뀐다. [기존 테스트](E:/dev/lobcap-workspace/tests/backtest/test_research.py:52)는 0.0005 체결이 strict=false에서 남고 strict=true에서 제거됨을 명시한다. 현재 표본의 lot 0.001과 일치하는 own-order 체결 계약을 모든 Binance 백테스트 경로에 적용할 필요가 있다.

연구용 연속량 대기열 산술은 보존하되 거래소 어댑터의 수량 규칙과 분리해야 한다. 부분 체결 이후 잔량·재고·참여율 예산까지 일관되는지 검증해야 한다.

### P0-D. 독립 엔진과의 비교가 필요하다

동일 코드를 두 번 실행해 digest가 같아도 같은 오류를 반복할 수 있다. HftBacktest의 버전을 고정하고 작은 정규화 fixture부터 이벤트·BBO·주문·체결·원장을 대조하는 어댑터를 권한다. 차이가 있으면 어느 가정 때문인지 기록한다. 두 모델의 일치는 실제 시장 검증을 대신하지 않는다.

특히 **거래 방향 변환**이 중요하다. 우리 `L2Trade.maker_side`는 maker 방향이지만 HftBacktest 거래 플래그의 BUY/SELL은 aggressor 방향이다. maker buy 거래는 SELL_EVENT로 변환해야 한다. 호가 side는 같은 방식으로 뒤집으면 안 된다. `nq`, timestamp 단위, snapshot 플래그, 보고 지연, Decimal/tick 변환도 계약으로 고정해야 한다. [상수/API](https://hftbacktest.readthedocs.io/en/latest/reference/constants.html)

## 3. 실행 현실성: 더 필요한 모델과 데이터

**동적 지연.** recorded 모드는 실제 공개 데이터 수신 지연을 활용하지만 entry/cancel/report 지연은 고정값이다. 계산 시간과 이벤트 루프 대기도 별도 모델이 없다. 요청·거래소 응답·로컬 수신 시각을 담는 latency profile 인터페이스를 만들고 constant, empirical, stress를 명확히 구분해야 한다. 현재 10분 데이터로 private order latency를 측정했다고 표시하면 안 된다. [Latency Models](https://hftbacktest.readthedocs.io/en/latest/latency_models.html), [Order Latency Data](https://hftbacktest.readthedocs.io/en/latest/tutorials/Order%20Latency%20Data.html), [Backtester의 시간 제어](https://hftbacktest.readthedocs.io/en/latest/reference/backtester.html)

**BBO 데이터 결합.** 수집기는 depth와 aggTrade를 사용하고, BBO는 depth에서 갱신한다. 더 빠른 bookTicker를 별도로 수집하면 의사결정 가격의 신선도를 개선할 가능성이 있다. 기존 표본에 없는 데이터를 복원할 수는 없다. 추가할 때는 depth와 BBO의 시각·출처를 보존하고, 같은 변화가 대기열 감소에 두 번 사용되지 않게 해야 한다. 두 입력을 무조건 덮어쓰면 오히려 모순된 호가가 생긴다. [Fusing Depth Data](https://hftbacktest.readthedocs.io/en/latest/tutorials/Fusing%20Depth%20Data.html)

**실제 대기열·시장 충격.** L2만으로 개별 주문 순서를 정확히 알 수 없고, 현재 고정 alpha 외에 queue 위치에 따라 감소 배분이 달라지는 모델을 비교할 수 있다. L3는 해당 시장의 공개 주문 순서를 더 잘 관찰하게 해 주지만 숨은 유동성과 가상 주문에 대한 다른 참여자의 반응까지 알려주지 않는다. HftBacktest 자체도 역사 재생이 시장을 바꾸지 않는다고 설명한다. [Probability Queue Models](https://hftbacktest.readthedocs.io/en/latest/tutorials/Probability%20Queue%20Models.html), [Level-3](https://hftbacktest.readthedocs.io/en/latest/tutorials/Level-3%20Backtesting.html), [Order Fill](https://hftbacktest.readthedocs.io/en/latest/order_fill.html)

**거래소 제약과 장애.** min notional, 최소/최대 주문량, 가격 제한, 요청 빈도, timeout 뒤 상태 불명, 중복 응답, reconnect와 미체결 재조정이 필요하다. 기존 inventory/loss guard와는 다른 층이다. 역사적 instrument rules를 표본과 함께 고정하고 paper 어댑터가 동일한 규칙을 사용해야 한다. 공개 데이터 shadow 실행은 데이터·결정·복구 경로를 검증하지만 실제 체결률을 검증하지 못한다. 실주문 실험은 이번 범위에 없으며 별도 승인이 필요하다. [실거래 불일치 디버깅](https://hftbacktest.readthedocs.io/en/latest/debugging_backtesting_and_live_discrepancies.html)

## 4. BRAIN식 탐색에 직접 연결되는 문서 아이디어

### 신호 연구 화면을 체결 결과 화면과 분리

현재 제한된 수식은 주로 호가 중심 이동을 표현한다. 다음은 단순 전략 추가보다 재사용 가치가 크다.

- 다중 depth imbalance, VAMP/weighted-depth 계열 공정가격, 거래 흐름과 과거 수익률의 causal feature catalogue.
- 각 feature의 단위, lookback, 신선도, depth coverage와 결측 상태 표시.
- 수식 값 미리보기와 100ms/1s/5s 미래 수익률에 대한 예측력·지속성·커버리지 분석. 미래 값은 평가 label에만 쓰고 전략 입력에는 금지.
- 전체 의사결정 시점의 신호 분석과 체결된 주문만의 markout을 분리. 후자에는 체결 선택 편향이 있다.
- 신호 0, inventory-only, feature 제거 실험으로 개선 원인 확인.

참고: [OBI alpha](https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Order%20Book%20Imbalance.html), [Pricing Framework](https://hftbacktest.readthedocs.io/en/latest/tutorials/Pricing%20Framework.html). 이는 프로젝트에 대한 설계 제안이며 문서 예제의 성과를 재현했다는 주장이 아니다.

### 공정가격·스프레드·재고 skew·사이즈·주문 정책 분리

GLFT는 거래 도착 강도와 변동성에 따라 spread/skew를 정하는 참고점이다. 먼저 단위가 명확한 동적 spread baseline을 넣고 GLFT 계수 추정은 과거 데이터로만 수행해야 한다. 10분 안에서 fitting과 평가를 반복한 결과를 안정적 calibration으로 부르면 안 된다. [GLFT](https://hftbacktest.readthedocs.io/en/latest/tutorials/GLFT%20Market%20Making%20Model%20and%20Grid%20Trading.html), [Simplified GLFT](https://hftbacktest.readthedocs.io/en/latest/tutorials/High-Frequency%20Grid%20Trading%20-%20Simplified%20from%20GLFT.html)

**전략 수보다 실제로 다른 호가 경로의 수를 보여줘야 한다.** BBO microprice는 spread 안에 있고 tick 반올림과 BBO clamp 때문에 서로 다른 계수가 같은 호가를 만들 수 있다. 특히 1-tick spread에서 작은 신호는 변화가 사라질 수 있다. 고유 quote 경로, clamp 비율, 유효 파라미터 변화를 측정하면 사용자가 '설정해도 다 비슷하다'고 느끼는 원인을 설명할 수 있다.

**quote grid와 parameter grid는 다르다.** 현재는 파라미터 조합 탐색이며 각 side에 주문 하나를 직렬 교체한다. 문서의 다중 호가 grid는 여러 가격에 동시에 주문한다. 이를 넣으려면 하나의 거래량을 own orders 사이에 가격·시간 순서대로 배분하는 공동 예산과 모든 주문의 노출 예약이 필요하다. 기존 Execution을 주문마다 복제하면 같은 거래를 여러 번 쓸 위험이 있다. 초기 MVP에서 제외했던 범위이므로 체결 교정 뒤 별도 확장해야 한다. [Grid Trading](https://hftbacktest.readthedocs.io/en/latest/tutorials/High-Frequency%20Grid%20Trading.html)

**large-tick 전략, basis/APT, 다중 시장은 조건부 확장이다.** queue 기반 large-tick 예제를 BTCUSDT에 그대로 적용할 근거는 없다. spot/perpetual basis와 공통 factor는 동기화된 다른 시장 입력이 필요하다. 과거 수신 시각과 결측을 보존해야 하며 동일 BTC 파라미터 여러 개를 독립 포트폴리오로 세면 안 된다. [Large Tick](https://hftbacktest.readthedocs.io/en/latest/tutorials/Queue-Based%20Market%20Making%20in%20Large%20Tick%20Size%20Assets.html), [Basis](https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Basis.html), [APT](https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20APT.html), [Multiple Markets](https://hftbacktest.readthedocs.io/en/latest/tutorials/Making%20Multiple%20Markets.html)

## 5. 탐색 속도, 손익 해석, 후보 선정

**빠른 탐색과 정밀 재검증을 구분한다.** 먼저 표본과 feature 버전별 causal feature를 공유 계산하고 각 run의 재고·주문 상태만 분리한다. 메모리와 CPU를 측정한 후 컴파일 최적화를 검토한다. HftBacktest accelerated 방식은 queue/partial fill/order response latency의 일부 정밀성을 생략하므로 그 결과를 정밀 엔진 결과와 같은 것으로 섞으면 안 된다. 근사 엔진은 선별에 쓰고 후보는 정밀 엔진으로 다시 확인한다. 문서의 가속 배수를 우리 VM 성능으로 주장할 근거는 없다. [Accelerated Backtesting](https://hftbacktest.readthedocs.io/en/latest/tutorials/Accelerated%20Backtesting.html), [JIT](https://hftbacktest.readthedocs.io/en/latest/jit_compilation_overhead.html)

**문서 예제의 수수료 조건부터 맞춰야 한다.** 여러 예제는 maker rebate를 전제한다. 예컨대 -0.005%는 -0.5bps다. 우리 계정의 실제 조건이나 zero-fee 진단과 동일하지 않다. 기존 turnover·손익분기 maker bps·비용별 재평가를 UI에서 전면에 보여주고, 동일 체결에 수수료만 바꾼 진단과 전략을 다시 실행한 fee sweep을 구분해야 한다. 프로그램 참여 조건과 수수료는 문서 예제를 현재 계정 조건으로 사용하지 않는다. [Market Maker Program](https://hftbacktest.readthedocs.io/en/latest/market_maker_program.html), [거래소 간 비교 예제](https://hftbacktest.readthedocs.io/en/latest/tutorials/High-Frequency%20Grid%20Trading%20-%20Comparison%20Across%20Other%20Exchanges.html)

**최고 PnL 하나를 paper 후보로 자동 승격하면 안 된다.** 동일 데이터·실행 가정 안의 순위, 실행 가정을 바꿨을 때의 순위, 인접 계수 안정성을 나눠 제시해야 한다. 탐색 횟수·중복 경로·수수료 의존·체결 불확실성·재고 노출을 후보 카드에 남긴다. 대기열 alpha는 전략의 예측 신호 alpha와 UI 이름부터 구분한다. 실행 가정을 수익이 좋아지는 방향으로 최적화하지 않는다.

현재 10분은 이 연구 흐름과 실패 사례 검증을 만드는 데 쓸 수 있다. 10분 표본을 계속 재사용하면서 얻은 최고값은 외부 검증이 아니다. 짧은 표본 Sharpe 제한은 유지하고 문서 예제의 연율화·resampling 기본값을 그대로 가져오지 않는다. [Statistics](https://hftbacktest.readthedocs.io/en/latest/reference/stats.html)

## 6. 공식 문서도 그대로 복사하면 안 되는 부분

- Order Fill 본문에 MBP만 지원한다는 오래된 문장이 남아 있지만 최신 홈페이지·L3 튜토리얼·초기화 API에는 L3가 있다. 라이브러리 버전과 실제 API를 고정해야 한다.
- GLFT 튜토리얼에는 half-tick 단위 관련 알려진 오류를 과거 결과 보존을 위해 남겼다는 설명이 있다. 수식의 가격/tick 단위와 계수는 별도 검증해야 한다.
- Pricing Framework는 초안이며 price protection 문서는 예제가 미완성이다. 완성된 운영 컴포넌트로 취급하지 않는다.
- 오래된 예제의 depth 해상도·리베이트·거래소 환경은 현재 입력과 다를 수 있다.
- 데이터 timestamp 보정 함수가 있다고 해서 원시 시각을 무조건 바꾸면 안 된다. 전체 표본의 최대 음수 지연으로 이전 시각을 보정하면 미래 suffix 변경이 과거 시계에 영향을 줄 가능성이 있다(우리의 인과성 관점 추론). 원시값 보존과 별도로 검증된 고정 clock offset이 필요하다.

출처: [Order Fill](https://hftbacktest.readthedocs.io/en/latest/order_fill.html), [Initialization](https://hftbacktest.readthedocs.io/en/latest/reference/initialization.html), [GLFT](https://hftbacktest.readthedocs.io/en/latest/tutorials/GLFT%20Market%20Making%20Model%20and%20Grid%20Trading.html), [Pricing Framework](https://hftbacktest.readthedocs.io/en/latest/tutorials/Pricing%20Framework.html), [Price Protection](https://hftbacktest.readthedocs.io/en/latest/tutorials/Risk%20Mitigation%20through%20Price%20Protection%20in%20Extreme%20Market%20Conditions.html), [Data Validation](https://hftbacktest.readthedocs.io/en/latest/reference/data_validation.html).

## 7. 권장 구현 순서와 통과 기준

| 순서 | 작업 | 완료 증거 |
|---|---|---|
| 1 | lot 계약, trade-through, 참여율 집계 의존성 교정/명시 | 최소 반례, 기존 연구 parity, prefix invariance, 회계·노출 불변식 |
| 2 | 고정 버전 HftBacktest 비교 어댑터 | 작은 fixture별 일치 또는 설명된 차이. maker/aggressor 변환 검사 |
| 3 | feature catalogue·신호 평가·동적 spread·유효 전략 중복 검사 | future label 차단, warmup/단위 검증, baseline/ablation 보고서 |
| 4 | feature 공유 계산과 안정성 중심 후보 카드 | 동일 결정·체결 digest, 메모리/시간 측정, 비용·실행 가정 민감도 |
| 5 | BBO 수집 확장·empirical latency 인터페이스·paper 복구 계약 | 새 입력 출처, 지연 profile 구분, 장애 재현. 없는 실제 체결 검증은 미확인 표시 |
| 6 | quote grid·다중 시장·L3 | 필요한 데이터 확보, 공동 거래량 예산·동기화·총 위험 테스트 후 별도 승격 |

1–4는 기존 10분 데이터로 시스템을 구축할 수 있다. 5–6의 관측 정보는 현재 표본에 추가로 가정했다고 해결되지 않는다. 이번 검토에서 구현이나 서비스 변경은 수행하지 않았다.

## 8. 문서별 검토 목록

아래 표는 수집된 모든 URL을 포함한다. '데이터 필요'는 모델 인터페이스 개발도 불가능하다는 뜻이 아니라 실측 검증에 추가 관측이 필요하다는 뜻이다.

| 문서 | 상태 | 프로젝트 적용 |
|---|---|---|
| [HftBacktest](https://hftbacktest.readthedocs.io/en/latest/) | 개선 필요 | 독립 비교 엔진 후보. L2/L3와 재생 한계 확인 |
| [Data](https://hftbacktest.readthedocs.io/en/latest/data.html) | 반영됨 | 소스·수신 시각 보존. provider별 계약은 별도 |
| [Debugging Backtesting and Live Discrepancies](https://hftbacktest.readthedocs.io/en/latest/debugging_backtesting_and_live_discrepancies.html) | 데이터 필요 | 실제 주문 로그와 백테스트 사건 대조 |
| [JIT Compilation Overhead](https://hftbacktest.readthedocs.io/en/latest/jit_compilation_overhead.html) | 후순위 | 프로파일링 후 JIT 도입; cold/warm 시간을 분리 |
| [Latency Models](https://hftbacktest.readthedocs.io/en/latest/latency_models.html) | 개선 필요 | 고정 주문·보고 지연을 profile 인터페이스로 확장 |
| [Market Maker Program](https://hftbacktest.readthedocs.io/en/latest/market_maker_program.html) | 데이터 필요 | 현재 계정 수수료 확인. 예제 리베이트 일반화 금지 |
| [Migration to v2](https://hftbacktest.readthedocs.io/en/latest/migration2.html) | 후순위 | 도입 시 v2 버전 고정과 기존 예제 API 차이 확인 |
| [Order Fill](https://hftbacktest.readthedocs.io/en/latest/order_fill.html) | 개선 필요 | 관통/동일가 체결 분리, queue 모델 차이 검증 |
| [Backtester](https://hftbacktest.readthedocs.io/en/latest/reference/backtester.html) | 개선 필요 | 계산 지연·이벤트 대기·주문 API를 어댑터 비교 기준으로 사용 |
| [Constants](https://hftbacktest.readthedocs.io/en/latest/reference/constants.html) | 개선 필요 | 거래 aggressor와 maker side 변환 계약 |
| [Data Utilities](https://hftbacktest.readthedocs.io/en/latest/reference/data_utilities.html) | 반영됨 | 스트리밍 변환 기반 존재; 타 포맷 어댑터는 후순위 |
| [Data Validation](https://hftbacktest.readthedocs.io/en/latest/reference/data_validation.html) | 반영됨 | 순서 검증 존재. timestamp 자동 보정은 인과성 검토 필요 |
| [Initialization](https://hftbacktest.readthedocs.io/en/latest/reference/initialization.html) | 개선 필요 | tick/lot/fee/latency/queue 설정을 양 엔진 간 명시적으로 대응 |
| [Statistics](https://hftbacktest.readthedocs.io/en/latest/reference/stats.html) | 반영됨 | 회계·markout 존재. 짧은 표본 연율화 기본값 복사 금지 |
| [Accelerated Backtesting](https://hftbacktest.readthedocs.io/en/latest/tutorials/Accelerated%20Backtesting.html) | 개선 필요 | 근사 선별과 정밀 검증 구분; 우선 feature 공유 계산 |
| [Data Preparation](https://hftbacktest.readthedocs.io/en/latest/tutorials/Data%20Preparation.html) | 반영됨 | snapshot·거래·depth 정규화 기반. 외부 converter 의미 대조 |
| [Fusing Depth Data](https://hftbacktest.readthedocs.io/en/latest/tutorials/Fusing%20Depth%20Data.html) | 데이터 필요 | bookTicker 추가 관측 및 중복 없는 BBO/depth 결합 |
| [Guéant–Lehalle–Fernandez-Tapia Market Making Model and Grid Trading](https://hftbacktest.readthedocs.io/en/latest/tutorials/GLFT%20Market%20Making%20Model%20and%20Grid%20Trading.html) | 개선 필요 | 강도·변동성 기반 동적 spread/skew. 알려진 단위 오류 주의 |
| [Getting Started](https://hftbacktest.readthedocs.io/en/latest/tutorials/Getting%20Started.html) | 반영됨 | 기본 재생·주문·계측 존재. 예제 성과는 별도 |
| [High-Frequency Grid Trading - Comparison Across Other Exchanges](https://hftbacktest.readthedocs.io/en/latest/tutorials/High-Frequency%20Grid%20Trading%20-%20Comparison%20Across%20Other%20Exchanges.html) | 데이터 필요 | 거래소별 수수료·tick·지연·자료 품질 통제 |
| [High-Frequency Grid Trading - Simplified from GLFT](https://hftbacktest.readthedocs.io/en/latest/tutorials/High-Frequency%20Grid%20Trading%20-%20Simplified%20from%20GLFT.html) | 개선 필요 | 해석 가능한 동적 spread baseline 설계 참고 |
| [High-Frequency Grid Trading](https://hftbacktest.readthedocs.io/en/latest/tutorials/High-Frequency%20Grid%20Trading.html) | 후순위 | 다중 동시 quote grid; 현재 parameter grid와 다름 |
| [Impact of Order Latency](https://hftbacktest.readthedocs.io/en/latest/tutorials/Impact%20of%20Order%20Latency.html) | 개선 필요 | 지연 민감도 존재. empirical tail·계산 지연 보강 |
| [Integrating Custom Data](https://hftbacktest.readthedocs.io/en/latest/tutorials/Integrating%20Custom%20Data.html) | 개선 필요 | point-in-time feature 입력 계약 확장 |
| [Level-3 Backtesting](https://hftbacktest.readthedocs.io/en/latest/tutorials/Level-3%20Backtesting.html) | 데이터 필요 | 개별 주문 데이터가 있어야 L3 재생 검증 가능 |
| [Making Multiple Markets - Introduction](https://hftbacktest.readthedocs.io/en/latest/tutorials/Making%20Multiple%20Markets%20-%20Introduction.html) | 데이터 필요 | 다중 자산 위험·데이터 동기화 설계 |
| [Making Multiple Markets](https://hftbacktest.readthedocs.io/en/latest/tutorials/Making%20Multiple%20Markets.html) | 데이터 필요 | 공통 factor·시장별 노출 및 동시 실행 제약 |
| [Market Making with Alpha - APT](https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20APT.html) | 데이터 필요 | 동기화된 다중 자산 입력과 과거 beta 추정 |
| [Market Making with Alpha - Basis](https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Basis.html) | 데이터 필요 | spot/perpetual 입력, basis 단위와 lead-lag 검증 |
| [Market Making with Alpha - Order Book Imbalance](https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Order%20Book%20Imbalance.html) | 개선 필요 | BBO 중심에서 다중 depth 신호와 신호 자체 평가로 확장 |
| [Order Latency Data](https://hftbacktest.readthedocs.io/en/latest/tutorials/Order%20Latency%20Data.html) | 데이터 필요 | 요청/거래소/수신 시각 실측. 공개 feed로 대체 불가 |
| [Research Pricing Framework](https://hftbacktest.readthedocs.io/en/latest/tutorials/Pricing%20Framework.html) | 개선 필요 | fair price·spread·skew·size 분리. 초안 상태 주의 |
| [Probability Queue Position Models](https://hftbacktest.readthedocs.io/en/latest/tutorials/Probability%20Queue%20Models.html) | 개선 필요 | 고정 alpha 외 위치 의존 queue 가정 민감도 비교 |
| [Queue-Based Market Making in Large Tick Size Assets](https://hftbacktest.readthedocs.io/en/latest/tutorials/Queue-Based%20Market%20Making%20in%20Large%20Tick%20Size%20Assets.html) | 후순위 | tick regime 분석 선행. BTC에 large-tick 예제 직접 이식 금지 |
| [Risk Mitigation through Price Protection in Extreme Market Conditions](https://hftbacktest.readthedocs.io/en/latest/tutorials/Risk%20Mitigation%20through%20Price%20Protection%20in%20Extreme%20Market%20Conditions.html) | 개선 필요 | 가격 보호·급변/장애 제어. 문서 예제는 미완 |
| [Working with Market Depth and Trades](https://hftbacktest.readthedocs.io/en/latest/tutorials/Working%20with%20Market%20Depth%20and%20Trades.html) | 반영됨 | 호가/거래 기반 입력 존재. 버퍼·조회 계약 어댑터 참고 |
| [Examples](https://hftbacktest.readthedocs.io/en/latest/tutorials/examples.html) | 반영됨 | 튜토리얼 색인과 예제 범위 확인 |
| [HftBacktest](https://hftbacktest.readthedocs.io/en/latest/index.html) | 반영됨 | 홈페이지 중복 URL; 별도 문서로 세지 않음 |
| [hftbacktest.data.utils.binancefutures module](https://hftbacktest.readthedocs.io/en/latest/reference/hftbacktest.data.utils.binancefutures.html) | 개선 필요 | 기존 Binance envelope·nq를 보존하는 별도 변환 필요 |
| [hftbacktest.data.utils.binancehistmktdata module](https://hftbacktest.readthedocs.io/en/latest/reference/hftbacktest.data.utils.binancehistmktdata.html) | 후순위 | 역사 자료 포맷 확장. 현재 캡처 계약과 동일시 금지 |
| [hftbacktest.data.utils.databento module](https://hftbacktest.readthedocs.io/en/latest/reference/hftbacktest.data.utils.databento.html) | 데이터 필요 | Databento/L3 외부 자료가 있을 때 활용 |
| [hftbacktest.data.utils.difforderbooksnapshot module](https://hftbacktest.readthedocs.io/en/latest/reference/hftbacktest.data.utils.difforderbooksnapshot.html) | 반영됨 | snapshot diff·reconciliation 기반 존재; 독립 비교 참고 |
| [hftbacktest.data.utils.migration2 module](https://hftbacktest.readthedocs.io/en/latest/reference/hftbacktest.data.utils.migration2.html) | 후순위 | HftBacktest 이전 버전 데이터 유입 시에만 변환 |
| [hftbacktest.data.utils.snapshot module](https://hftbacktest.readthedocs.io/en/latest/reference/hftbacktest.data.utils.snapshot.html) | 반영됨 | 초기 snapshot 재현과 source boundary 확인 |
| [hftbacktest.data.utils.tardis module](https://hftbacktest.readthedocs.io/en/latest/reference/hftbacktest.data.utils.tardis.html) | 데이터 필요 | Tardis 자료 유입 시 receive clock·side 정규화 검증 |

## 작업 완료 기록

- Files changed: 이 검토 보고서, 문서 inventory, `docs/internal/FAILURES.md`의 도구 재시도 기록.
- Commands run: Git 상태/HEAD 확인, `rg`와 PowerShell 소스·테스트 읽기, stdlib 문서 crawler, inventory 무결성·목록 확인. 문서 다운로드는 읽기 전용 요청.
- Official docs checked: 위 HftBacktest 최신 문서 전체 목록 및 Binance 공개 stream 문서. 외부 라이브러리 소스 전체 검증은 아님.
- Tests passed: 이번 검토에서 테스트를 실행하지 않음.
- Tests failed: 이번 검토에서 테스트를 실행하지 않음. 도구 실패는 `docs/internal/FAILURES.md` 기록.
- Runtime observations: VM 및 collector 상태를 이번에는 재확인하거나 변경하지 않음.
- Data written: 보고서·출처 inventory와 `E:/dev/hft-docs-review/`의 검토용 문서 캐시. 시장 원시 데이터·연구 run 아티팩트 변경 없음.
- Open risks: 실제 체결 calibration 부재, 10분 재사용의 선택 편향, 문서 버전 혼재, 위 체결 계약 차이.
- Recommended next action: 7절 순서 1의 체결 계약부터 수정·검증한 후 독립 엔진 비교와 신호 연구 화면으로 진행.
