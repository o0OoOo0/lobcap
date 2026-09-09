# 10분 표본 연구 시스템

이 기능은 **같은 개발 표본에서 전략·실행 가정을 비교하고 후보 선정 절차를 검증하는 시스템**이다. 10분을 나눠 OOS라고 부르거나 반복 실행 횟수를 독립 표본 수로 세지 않는다. 어떤 손익에서도 `paper_ready=false`, `paper_candidates=[]`다. 장기 로드맵 전체 구현을 뜻하지 않는다.

## 사용

기존 앱의 **10분 연구·후보 검토**를 선택한다. 수신시간 표본에서 질문·가설 및 전략 1–4개를 정하고 제출한다. 페이지 이동은 실행을 시작하지 않는다. 일반 설정 화면의 편집값은 연구에 전달되지 않는다.

기본 전략은 fixed spread, inventory skew, microprice, flow guard다. 새 volatility adaptive를 포함한 5계열 중 최대 4개를 고른다. 각 전략 6시나리오로 최대 24실행이다.

| 시나리오 | α | maker/taker bps | entry/cancel/report/hedge ms |
|---|---|---|---|
| baseline | .5 | 2 / 5 | 20 / 20 / 20 / 20 |
| queue_zero | 0 | 2 / 5 | 20 / 20 / 20 / 20 |
| queue_one | 1 | 2 / 5 | 20 / 20 / 20 / 20 |
| touch_diagnostic | TOUCH_FILL | 2 / 5 | 20 / 20 / 20 / 20 |
| fees_plus_50pct | .5 | 3 / 7.5 | 20 / 20 / 20 / 20 |
| combined_stress | 0 | 3 / 7.5 | 50 / 50 / 50 / 50 |

수수료와 주문 latency는 연구 가정이며 계정 요율·측정값이 아니다. 시장정보는 캡처 수신 시각을 사용한다. α는 미보정이다.

Ubuntu `/home/0woo/lobcap-mvp`에서:

```bash
.venv/bin/python -m lobcap.backtest.research --preview
.venv/bin/python -m lobcap.backtest.research
.venv/bin/python -m lobcap.backtest.research --strategies volatility_adaptive --hypothesis '변동성 호가 확대가 같은 표본의 비용 민감도를 낮추는가'
.venv/bin/python scripts/validate_backtest_research.py --output artifacts/backtest/validation/research-new.json
```

준비된 arrival-v2 sample이 필요하며 연구 실행이 새 구간을 자동 선택하지 않는다. validation 스크립트는 기본 24실행을 반복하고 순서 독립 digest, source evidence, 성능을 검사한다. 출력 파일은 새 이름을 사용한다. 12일 L3나 장기간 수집을 호출하지 않는다.

## 위험 기준과 실행

- 초기 synthetic cash 1,000 USDT, 초기 재고 0, 한쪽 요청 .001 BTC, 재고 한도 .005 BTC.
- 주문 상한 .002 BTC, 중간가격 기준 보수적 총노출 상한은 알려진 자산의 80%.
- 손실 10 USDT 또는 최대 낙폭 30 USDT에서 kill을 latch한다. 손실 기간은 **이 10분 전체**다. 일별 한도·다일 재개 정책 구현은 아니다.
- 알려진 재고가 .003 BTC 이상이면 메이커 양쪽 취소를 요청한다. 종료 통보 뒤 .001 BTC 방향으로 reduce-only hedge한다. kill이면 목표 재고는 0이다.
- hedge는 한 번 최대 .002 BTC. 도착 시 재구성 호가의 알려진 범위에서 부분 소진한다. 제출 시 관측 midpoint보다 불리한 10bps 밖은 사용하지 않는다.
- 사용한 가상 잔량은 같은 가격의 새 absolute depth update가 올 때까지 재사용하지 않는다. 실제 시장 반응을 추정하는 모델은 아니다.
- 통보 전 hedge를 겹치지 않고, 호가 부족·stale·sub-lot 잔여 재고를 무한 유동성으로 처리하지 않는다.
- 연구 모드 maker fill은 lot으로 내림한다. 기존 일반 백테스트/연구 fill arithmetic은 유지한다.
- strategy 예외는 연구 모드에서 kill·취소 경로로 들어간다. 실행 자체의 실패는 FAIL로 기록하며 취소 확인을 위조하지 않는다.
- client가 받은 상태로 위험을 판단하므로 실제 손실이 한도를 넘은 뒤 중단될 수 있다. 늦은 체결·헤지 비용에 의한 초과 손실을 제거하지 않는다.

## 손익과 감사

role별 maker/taker 수수료를 원장에 반영한다. gross PnL = 체결 시 중간가격 대비 signed 기여 + 재고 MTM, net = gross − signed fees를 대조한다. 이 체결 기여에는 taker의 불리한 spread 소진도 들어간다. slippage나 markout을 다시 차감하지 않는다. fill의 role로 maker/hedge를 구분한다.

별도 분석: 시간가중 평균 절대 재고·재고 2차 모멘트·한도 체류시간·낙폭 지속시간·first/full-fill 시간·주문 평균 수명·거래대금당 net PnL·maker/taker fees·capacity 두 비율. capacity는 결정 시 진단이며 실측 용량 상한으로 사용하지 않는다.

각 실행은 fresh state다. 실행 전에 설정·정책·가설·표본 해시·코드 내용 해시를 동결하고 기존 Git provenance에 근거를 추가한다.

```text
artifacts/backtest/research/<experiment_id>/
  experiment.json          # 동결 계획·provenance
  base_config.yaml         # CLI 재현 시작점
  state.json               # 상태·실행 경로·파일 hashes
  attempt-*.json           # 재개 전 상태 보존
  evaluations/runs/...     # 기존 run artifacts + risk/hedges/capacity
  selection.json           # 탐색 순위·승격 차단 사유
  results.csv
  report.html              # 스크립트 없는 독립 검토 보고서
  handoff.json             # research_only; paper_ready=false
```

`--resume <experiment>`에는 같은 config·전략·가설을 지정한다. 계획·표본·코드가 다르면 재개를 거부한다. 완료 실행의 파일 hashes와 원장을 재검사해 재사용하고, 실패 실행 디렉터리는 보존한다. 영구 scheduler나 자동 재개 서비스는 없다.

탐색 PASS: 모든 비-TOUCH 시나리오 완료, 각 maker fill 20건 이상, 각 net PnL 양수, 낙폭 30 USDT 이하, net loss 10 USDT 이하, kill 없음. 순위는 탐색 PASS 우선, 최악 net PnL 내림차순, 전략 식별자 순이다. 사전 고정한 **동일 표본 진단 규칙**이며 정식 선정 규칙이 아니다.

paper는 항상 보류한다. 독립 OOS, untouched final, L3 calibration, 실제 선물 mechanics, 다기간 regime/parameter stability, live execution은 미검증이다. 수익이나 TOUCH_FILL 결과로 우회하지 못한다. export도 paper 승인서가 아니다.

## 미구현 범위

거래소 mark price·funding·margin tiers·최소 주문금액 이력, 정밀 선물 지갑/청산, 다기간 분할·final 보호·walk-forward, 12일 보정 실행, 최적화/plateau, empirical latency, 요청 rate limiter, live paper, 실제 market impact, terminal liquidation 가치 평가, 공개 배포는 미구현이다. 새 데이터가 필요한 근거를 만들어내지 않는다.

1배는 연구 노출 예산의 의도이며 거래소 레버리지/증거금 엔진 구현을 뜻하지 않는다. lot/tick은 표본 메타데이터다. 기존 collector·systemd·연구 resource import·accepted artifacts는 변경하지 않는다.
