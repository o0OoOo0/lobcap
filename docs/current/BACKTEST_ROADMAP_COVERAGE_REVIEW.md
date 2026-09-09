# Backtest → Paper Trading 로드맵 반영 검토

검토일: 2026-09-10. 대상 코드: `feat/backtest-mvp-v1`, `3b5c7c2`.
비교 문서: 사용자가 제공한 `MM_Backtest_to_PaperTrading_Roadmap.md` (Downloads).
문서는 요구사항 대조 자료이며, 그 안의 merge·OOS 실행·paper/live 거래 제안은 실행 지시로 취급하지 않았다.

## 결론 및 검토 범위

**전체 반영 아님. 현재는 bounded 엔진 검증과 진단용 앱이며, paper trading 후보 선정 시스템은 미완성이다.** Phase A의 주요 기반은 존재하지만 B–E의 실증·운영 절차가 연결되지 않았다. 원래 MVP에서 제외했던 funding/hedge/OOS도 이 로드맵 기준에서는 미충족이다. 원래 범위 밖이었다는 사실을 완료로 세지 않는다.

정적 코드·테스트 본문·설정·기존 검증 기록을 대조했다. 이번 검토에서 VM 재접속, 테스트 재실행, 브라우저 조작, 신규 OOS/시뮬레이션은 하지 않았다. 기존 132 Linux tests PASS는 `BACKTEST_STRICT_REVIEW_VALIDATION.json`의 `aef9b92` 기록이며, 이번 커밋에서 재실행한 결과가 아니다. 동일 파일의 실표본 성능/패리티 기록은 별도 `69aec10…` 커밋을 가리킨다. 기록의 범위를 혼동하지 않는다.

판정: **반영**은 해당 아이디어의 구현 존재, **부분**은 일부 기능/검증만 존재, **미반영**은 현재 백테스트 경로에 없음, **미검증**은 실증 근거를 확인하지 못함. 반영은 실거래 정확성을 뜻하지 않는다. 장별 요구 크기가 달라 단순 완료율을 계산하지 않는다.

## 0–35 전체 항목 대조

| 절 | 주제 | 판정 | 코드 근거 및 남은 차이 |
|---|---|---|---|
| 0 | 여러 기간·가정·OOS에서 robust한 전략 | 부분 | `runner.py` 비교·민감도는 존재. 여러 기간 검증·후보 승격 gate 없음. |
| 1 | 기존 상태 / 필요한 시스템 | 부분 | 문서의 '아직 필요' 중 replay/lifecycle/accounting/dashboard 등은 현재 구현됨. 기존 collector/L3/5E 성과 전체는 이번에 재검증하지 않음. |
| 2 | 결정적 replay·수신 시간·누출 방지 | 반영, 검증 범위 제한 | `engine.py:run`, `arrival.py:run_arrival`, `replay.py:normalize`. exchange/recorded 두 모드, 공개/실제 상태 분리와 prefix 테스트. reconnect는 재생 중 복구보다 표본 거부 방식. 모든 누출이 없다는 일반적 증명은 아님. |
| 3 | 데이터 품질 gate | 부분 | `replay.py:_stream/normalize/prepare_sample/load_sample`: ID/pu·타임스탬프·중복·nq·snapshot·crossed/empty·체크섬·acquisition boundary 검사. 장기간 segment 승인/UNKNOWN 목록·제외구간 레지스트리 없음. 내부 긴 depth 간격은 최대값 통계 및 실행 staleness로 처리; 일괄 품질 거부 기준은 없음. |
| 4 | 주문 lifecycle·race·partial | 반영 | `execution.py:Execution`, `arrival.py`: pending/active/cancel/terminal, 부분체결 수량 및 이벤트, serial replacement, tie와 no-overfill 테스트. INTENDED/PARTIALLY_FILLED가 독립 enum이 아니어도 의도/부분체결 동작은 존재. |
| 5 | latency model | 부분 | 실기록 수신 지연, 고정 entry/cancel/private report 지연. 수치 grid는 설정 가능. Fixed/Empirical/Random 교체형 모델·telemetry 로더·hedge latency 없음. recorded 지연은 경험적 주문 latency 분포 보정과 다름. |
| 6 | shared fill / alpha / 전략 격리 | 반영 | `execution.py:on_event`가 `l2.fill_models.advance_queue` 호출. arbitrary Decimal alpha와 TOUCH_FILL, `StrategyParameters`에서 queue/alpha 제외. .25/.75도 설정 가능. |
| 7 | L3 calibration track | 부분, 결과 미검증 | `l2/multidate.py`에 연구용 calibration/OOS 코드 존재. `configs/experiments/5e_multidate.yaml`은 full 12-date NOT_RUN. backtest model은 명시적으로 uncalibrated; 보정 파일·학습기간·출처를 연결하는 인터페이스 없음. 숫자 alpha 수동 입력은 보정 통합이 아님. |
| 8 | passive slippage·hedge VWAP | 부분 | maker fill은 지정가, 임의 slippage 추가 없음. `diagnostics.py`의 impact 비용은 별도 stress. 공격적 주문 L2 소진/VWAP 없음. |
| 9 | cashflow·fees/rebates·회계 identity | 부분 | `accounting.py:Portfolio` Decimal 회계·signed fee·gross/net·MTM·리베이트 반영. synthetic ledger/midpoint 기준. 실제 mark price 및 hedge 비용 없음. |
| 10 | Binance futures mechanics | 부분, 주요 공백 | `types.py:InstrumentSpec` tick .10/lot .001과 선형 BTC/USDT 단위. 독립 min quantity/min notional, 계정 fee tier, funding, leverage, initial/maintenance margin, liquidation, 거래소 mark price 없음. taker_bps는 저장되지만 실행상 비활성. |
| 11 | risk manager·kill switch | 부분, 주요 공백 | `strategies.py:risk_control` 재고·staleness·spread·coverage·vol gate, serial 한쪽 1주문, `execution.py:assert_inventory` 최악 노출 검사. 독립 max order size/gross exposure, loss/drawdown/rate 한도 및 latch형 kill switch 없음. `size`는 요청 수량이지 별도 risk ceiling이 아님. |
| 12 | 초기 전략 5종 아이디어 | 대부분 반영 | fixed/inventory/microprice + flow_guard. flow_guard 내 과거 변동성 기반 spread 확대와 흐름별 한쪽 억제, 공통 vol widen/reduce/stop도 존재. 별도 volatility_adaptive 전략 식별자/독립 비교는 없음. 모두 미보정 예제. |
| 13 | MM 성과지표 전체 | 부분 | 아래 상세 지표 표 참고. 기본 손익·재고·fill·markout이 있으나 다수 위험/시간/robustness 지표 없음. |
| 14 | PnL 분해 | 부분, 의도적 수식 차이 | spread_capture + inventory_mtm − signed fees = net을 정확 대조. hedge 없음. adverse selection을 다시 차감하지 않는 현재 구현은 적절함; markout 중복 반영 방지. |
| 15 | signed markout 100ms/1s/5s | 반영 | `metrics.py:on_fill/_resolve/finish`, 250ms freshness와 표본 끝 censor, coverage/드리프트 분리. 매수·매도 수식 존재. 테스트 본문은 매수 수치 assertion이 중심으로 매도 exact 기대값 회귀 보강 여지. |
| 16 | 고정 시간 Sharpe | 반영, 더 보수적 | `metrics.py:finish` 1분 수익률, 최소 60개 완전 bucket 전 미제공, 비연율화. config는 10–30분만 허용하므로 일반 UI 실행은 unavailable. 5분·daily 옵션 없음. |
| 17 | 전략변수/체결가정 분리 | 부분 | strategy API는 alpha/queue 차단. `BacktestConfig`는 평평한 설정이고 동일 sweep API가 두 범주를 허용. optimizer는 없음; alpha를 PnL로 선택 못하게 하는 연구 정책/gate도 없음. fee-aware strategy에 maker fee 전달은 합리적 입력임. |
| 18 | temporal split / walk-forward | 미반영(backtest) | `SampleSpec`에 split/fold 없음. loader는 단일 10–30분, `run_sweep`은 동일 sample만. 연구용 L3 split을 전략 OOS로 간주할 수 없음. |
| 19 | untouched final test 보호 | 미반영(backtest) | training/validation/final_test 역할, 잠금·조회 기록·승격 승인 없음. 이미 반복 열람한 demo는 final test가 될 수 없음. |
| 20 | parameter neighborhood/plateau | 부분 | 1변수 sweep과 saved history 존재. 2D heatmap, 이웃 안정성 점수/기간별 비교/plateau 판정 없음. |
| 21 | execution sensitivity | 부분 | alpha/clock/entry/cancel/report/maker fee sweep 가능. taker/hedge slippage는 비활성 또는 없음. TOUCH_FILL 의존도 후보 탈락 규칙 없음. |
| 22 | 종합 stress matrix | 부분 | bounded validation scripts의 core matrix·1변수 sweep. entry/cancel 5/20/50/100 값은 입력 가능하나 지정 corner matrix·calibrated model·fee tier 대비 +25/+50%·여러 regime 검증 없음. 실행 가능과 통과 실증은 다름. |
| 23 | regime analysis | 부분 | `metrics.py:_groups`: spread/imbalance/volatility/time bucket, inventory와 설정별 집계. depth/liquidity, trade intensity, trend/range, flow regime 통합 분류·다기간 안정성 없음. 단순 BBO imbalance는 aggressor-flow imbalance와 다름. |
| 24 | 다축 전략 selection | 미반영(절차) | 개별 지표 비교는 가능하나 OOS·stability 기반 후보 판정/승격 모듈 없음. |
| 25 | multiple testing/selection bias | 부분 | config/Git/input hashes·변형·history 저장. 사전 hypothesis/reason·split 고정·누적 trial 관리/selection bias 통제 없음. |
| 26 | capacity / market impact | 부분 | no-impact 명시, 거래 한 건별 fill participation cap과 가상 bps 비용 stress. 문서 요구 `order_size/top_level_depth`, `order_size/recent_traded_volume` 별도 지표 없음. 체결 참여 cap은 주문 용량 비율과 다름. 실제 endogenous impact/규모별 붕괴 실증 없음. |
| 27 | hedge / forced liquidation | 미반영 | maker-only. 실제 L2 소진·taker fee·hedge latency/VWAP·강제 청산 경로 없음. |
| 28 | failure simulation·stop/cancel | 부분, 주요 공백 | 데이터 오류 거부·stale/vol quote suppression·cancel lifecycle 일부. strategy/execution exception에서 runner는 FAIL 기록 후 재발생; 공통 kill→outstanding cancel→확인 절차 없음. reconnect 회복·spread 폭증·liquidity 소멸 등 종합 fault injection 없음. |
| 29 | unit/integration suite | 부분 | toy book·ordering·지연·회계·censoring·alpha·UI/CLI 테스트 존재. 아래 검증 공백 참고. 132 PASS만으로 문서의 모든 항목을 충족하지 않음. |
| 30 | validation level | Level 1 중심 | bounded correctness 증거 있음. Level 2 multi-day/week 전략 연구, Level 3 untouched OOS/stress 실증 없음. |
| 31 | pre-paper DoD | 미충족 | replay/accounting/baseline/provenance 등 기반 있음. calibrated fill, realistic futures costs, full risk/failure, OOS/final/stability gates 미충족. |
| 32 | paper의 역할 | 설명 수준 / 구현 없음 | live feed→동일 전략·risk adapter, timing telemetry·backtest-paper discrepancy 보고서 없음. shadow paper만으로 실제 queue/impact truth 확보 불가라는 제한은 유지해야 함. |
| 33 | 전체 개발 순서 | Phase A 중심 | feature branch 상태이며 main merge 여부 전체 역사는 이번에 재검증하지 않음. B calibration/data approval, C research, D selection, E live validation의 연결 workflow 없음. |
| 34 | 최종 선택 원칙 | 미검증 | 해당 원칙을 만족한 후보/검증 기간/보고서 없음. 높은 10분 PnL로 대체 불가. |
| 35 | 연구 스토리·backtest-paper gap | 부분 | 공유 fill arithmetic·deterministic engine·uncertainty stress가 기반. robust 후보와 실시간 차이 계측까지 이어진 연구는 아직 아님. |

## 성과지표 세부 대조 (§13)

| 묶음 | 현재 제공 | 미제공 또는 이름/의미가 다른 것 |
|---|---|---|
| Profit | gross/net, signed fees, charges/rebates, turnover notional | PnL/hour, PnL/notional 정식 출력, hedge cost. bps spread capture는 net PnL/notional과 다름. |
| Risk | event-time maximum drawdown | drawdown duration, tail loss, worst day/window |
| Inventory | 시간가중 signed mean, max abs, ending | mean absolute, inventory volatility, time at limit. signed mean은 매수·매도가 상쇄될 수 있음. |
| Execution | submitted/canceled/activated/rejected/censored, fill count/rates, full/partial-only, quantity fill rate, cancel-pending fills | time-to-first/full-fill, cancel-before-fill 비율 정식 요약. 원시 주문 기록으로 추가 계산 가능한 것과 현재 지표 제공은 구분. |
| Quoting | side별 quote availability, entry/cancel pending 시간, replacement downtime, 주문 lifetime 누계, decision별 quoted spread | 평균 quoted spread, 평균 lifetime, replacement frequency 정식 요약. lifetime 누계만으로 평균값을 제공했다고 볼 수 없음. |
| MM quality | 총 spread capture, volume-weighted per-unit/bps, signed markout | net spread capture, per-fill 평균, 별도 정의된 realized spread. fill-price markout은 해당 정의와 유사하나 2배/bps 등 관례를 명시하기 전 동일 지표로 세지 않음. |
| Adverse selection | 세 horizon markout + post-fill midpoint drift, coverage/censoring | 다기간/조건별 충분한 표본이라는 실증 |
| Robustness | 가정별 PnL/fill-rate 표와 비교 | 전략 ranking stability, 이웃/기간/regime별 안정성 판정 |

## 우선순위와 검증 공백

1. **Paper 후보 선정 전 필수:** split registry와 final-test 보호, 다기간 품질 승인, walk-forward·사전 실험 가설 및 trial history. 이미 본 데모 구간은 개발 표본으로 표시.
2. **운영 전 필수:** loss/drawdown/order-size/gross/rate 한도와 latch형 kill switch. 정상 quote suppression, 예외, 단절에서 취소가 접수·유효화·확인되는 과정을 테스트. 현재 assertion 실패나 FAIL manifest는 이 절차가 아님.
3. **선물 결과 해석 전 필수:** 거래소 mark price와 midpoint를 분리하고 min notional·funding·fee scenario를 명시. 레버리지·마진·청산을 모델링하지 않으면 그 한계를 결과의 gate로 노출. 현재 ledger 필드 `mark_price`는 실제로 midpoint라 오해 소지가 있음.
4. **체결 현실성 검증:** 연구용 L3 보정 결과·기간·출처 연결 및 Binance 전이 가정 검증. 실제 주문 telemetry가 없는 현재에는 '측정/보정 완료'로 표시 불가.
5. **분석 보강:** capacity 두 비율, 누락된 위험/체결 시간 지표, regime, plateau, ranking stability, corner stress 결과. 참여율 cap·가상 impact 비용은 이를 대체하지 않음.
6. **후속 범위:** 필요시 hedge/VWAP 및 paper adapter·telemetry. 문서에 언급됐다는 이유로 이번 검토에서 live/paper 실행을 시작하지 않음.

테스트 공백: SELL markout의 수치 기대값 직접 assertion, 연속 다회 부분체결 ledger 시나리오의 충분성, reconnect 구간 배제→재승인 절차, 예외 kill/cancel, 긴 내부 timestamp 정체와 QUALITY 상태의 일치, 모든 stress corner 조합. 기존 테스트명만으로 이 항목들을 완전히 검증했다고 판정하지 않았다.

특히 `prepare_sample`의 `gaps: 0`은 검사된 sequence/acquisition 조건 범위에서 해석해야 한다. 내부 stale duration 전체가 0이라는 뜻이 아니다. `max_depth_interval_ns`를 수집하지만 내부 최대 간격 자체로 FAIL시키지는 않으며, 실행은 별도 stale-book control을 따른다. 정책의 분리와 결과 설명을 보강할 필요가 있다.

## 문서 아이디어를 그대로 적용하면 안 되는 부분

- §14의 근사식에서 adverse selection을 현재 MTM/markout과 다시 차감하면 중복 계산 가능. 현재 exact identity를 유지하고 markout을 별도 진단으로 둔다.
- §16의 짧은 구간 diagnostic Sharpe보다 현재 미제공 정책이 보수적이다. 숫자를 억지로 추가할 필요 없음.
- §4의 상태 명칭 자체보다 상태 전이·remaining quantity·시간 순서가 중요하다. PARTIALLY_FILLED enum 부재를 부분체결 미구현으로 분류하지 않음.
- §7의 Coinbase L3 보정이 있어도 Binance 실제 queue/impact truth를 확보한 것은 아니다.
- §32대로 shadow paper의 모의 체결은 실제 체결 증거가 아니다. 실제 pilot은 별도 단계·별도 명시적 작업 범위다.

## 재현 가능한 근거 위치

- `src/lobcap/backtest/{replay,engine,arrival,execution,strategies,accounting,metrics,diagnostics,runner,presentation,types}.py`
- `src/lobcap/config/backtest.py`, `configs/backtest/*.yaml`
- `src/lobcap/l2/{fill_models,multidate}.py`, `configs/experiments/5e_multidate.yaml`
- `tests/backtest/test_{replay,execution,engine,arrival,strategies,runner,app,presentation,fill_transition,config}.py`
- `docs/current/BACKTEST_{MVP,REALISM,STRICT_REVIEW}_VALIDATION.json`
- `scripts/validate_backtest_{bounded,arrival}.py`, `apps/backtest_lab.py`

## Task completion report

- Files changed: 이 검토 문서와 `FAILURES.md`의 검색 명령 오류 기록만. 실행 코드 변경 없음.
- Commands run: `git status --short`, `git log -1 --oneline`, `git branch --show-current`, `rg`, `Get-Content`로 첨부 문서/코드/테스트/기존 검증 기록 조회; `git diff --check`.
- Official docs checked: 없음. 외부 기술 사실이나 최신 거래소 규칙의 감사가 아닌 저장소와 제공 문서 대조.
- Tests passed: 이번 재실행 없음. 과거 기록 132 Linux PASS를 구분해 인용.
- Tests failed: 이번 테스트 미실행. Windows glob을 잘못 전달한 검색 명령 1건은 올바른 `rg -g` 검색으로 복구.
- Runtime observations: local branch `feat/backtest-mvp-v1`, 검토 시작 시 clean at `3b5c7c2`; VM/서비스 상태는 이번 확인 안 함.
- Data written: 검토 문서 및 명령 오류 기록. 표본/실행/원시 데이터 변경 없음.
- Open risks: 정적 검토는 실거래 정확성 또는 모든 실패 시나리오의 증명이 아님; 로드맵 주요 gate 미충족.
- Recommended next action: paper/live 확대보다 split/final-test 보호, risk failure behavior, futures accounting 범위부터 별도 구현 계획 확정.
