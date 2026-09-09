# 전략 연구 작업 공간

현재 10분 Binance BTCUSDT 표본으로 전략·수식·파라미터를 편집하고, 실행 결과를 비교하고, 결과를 복제해 다음 실험을 만드는 작업 공간이다. 표본 내 탐색 후보를 저장할 수 있다. paper 적격성은 별도이며 현재 `INSUFFICIENT_EVIDENCE`다.

## 사용 흐름

1. 앱의 기본 화면 **연구 작업 공간 → 전략 편집**에서 이름과 가설을 적는다. 기존 전략 또는 **직접 작성한 신호 수식**을 선택한다.
2. 주문·위험·비용 설정을 편집한다. 0 bps는 비용 없는 진단 가정이다. 수수료와 체결 alpha는 실제 계정 조건이나 측정된 queue가 아니다.
3. 탐색 축에서 예를 들어 반스프레드 `0.5, 1, 1.5`, 재고 스큐 `0, 1, 2`를 입력한다. 기본 시나리오 1개면 9개 실행이다. 최대 24개, 순차 실행한다.
4. **초안 저장 · 실행 미리보기**를 누른다. **실험 실행**에서 실제 적용 설정을 확인하고 **이 설정으로 실행 제출**을 누른다. 화면 이동만으로 실행하지 않는다.
5. 진행 화면에서 취소하거나 실패·중단 작업을 재개할 수 있다. 완료 실행은 다시 계산하지 않고 checksum을 검증한다. 중간에 멈춘 실행만 처음부터 새 attempt로 계산한다.
6. **결과 비교**에서 동일 가정의 전략을 비교하거나 동일 전략의 비용·체결 민감도를 선택한다. 원본과 복제 작업을 함께 선택해 설정 차이를 확인할 수 있다.
7. **이 실행을 복제해 전략 편집**으로 다음 실험을 만든다. **연구 이력**에서 초안과 노트·즐겨찾기·탐색 후보를 저장한다. 원본 실행 증거는 수정하지 않는다.

## 수식 언어

출력은 호가 중심을 움직이는 ticks다.

```text
signal_strength * (microprice - mid) / tick
    - inventory_skew_ticks * inventory / max_inventory

center = mid + tick * clipped_signal
```

`signal_strength`, `inventory_skew_ticks`는 일반 설정값이다. 추가 계수는 파라미터 JSON에 `{"flow_weight":"0.5"}`처럼 선언한다. 고급 탐색 축 `parameter.flow_weight`로 여러 값을 비교한다. 기본 `max_signal_ticks=10`에서 신호를 제한하며 `signal_reason`과 `clock_diagnostics.signal_clipped_decisions`에 제한 횟수를 기록한다.

가격·mid·microprice·imbalance, 최근 1초 거래 흐름, 과거 30초 변동성, 전달된 재고만 접근한다. `bid`, `ask`, `mid`, `microprice`, `tick`, `imbalance`, `spread_ticks`, `volatility_bps`, `trade_imbalance`, `trade_volume`, `inventory`, `max_inventory`를 제공한다. recorded 시계에서는 아직 도착하지 않은 시장 데이터나 체결 통보를 볼 수 없다.

산술 `+ - * /`, 괄호, `abs`, `min`, `max`, `clip`만 허용한다. 임의 Python, import, attribute 접근, 파일·네트워크, queue 및 미래 데이터는 허용하지 않는다. 수식 최대 1,000자, AST 128개 노드, 깊이 16, 리터럴 유효숫자 28자리다. Decimal 50자리 산술을 사용한다.

필요한 관측값이 없으면 신규 호가를 억제하고 기존 주문 취소 규칙을 따른다. 0으로 나누기 등 산술 오류는 실행 실패로 기록한다. 실패를 0 신호나 정상 10분 결과로 바꾸지 않는다. 예제 계산기는 가상의 입력이며 실제 표본 성과가 아니다.

## CLI와 재현

기존 Ubuntu 체크아웃에서 프로젝트의 web 환경과 source 경로를 사용한다. Streamlit과 CLI는 동일 resolver와 worker를 호출한다.

```bash
export PYTHONPATH="$PWD/src"
python -m lobcap.backtest.workspace preview \
  --root artifacts/backtest/workspace \
  --config configs/backtest/binance_recorded.yaml \
  --design configs/backtest/workspace/grid.json

python -m lobcap.backtest.workspace submit \
  --root artifacts/backtest/workspace \
  --config configs/backtest/binance_recorded.yaml \
  --design configs/backtest/workspace/grid.json \
  --submission-key inventory-grid-attempt-1

python -m lobcap.backtest.workspace status --root artifacts/backtest/workspace
python -m lobcap.backtest.workspace cancel --root artifacts/backtest/workspace --job JOB_ID
python -m lobcap.backtest.workspace resume --root artifacts/backtest/workspace --job JOB_ID
```

같은 submission key는 동일 실험에만 재사용하며 중복 job을 만들지 않는다. 의도적인 반복 실험은 새 key를 사용한다. 설정값이나 수식이 바뀌면 새 실험으로 제출한다. 실행 결과의 `config.yaml`과 동결된 `experiments/<job>.json`을 함께 보관한다.

표본·source code·효과적 설정·위험/헤지 정책·결과 파일의 지문을 확인한다. code fingerprint에는 UI/설정 코드도 포함하므로 코드가 바뀐 뒤 기존 미완료 작업을 재개하면 거부한다. 완료 결과는 계속 열 수 있으며 새 초안으로 복제할 수 있다.

## 작업과 저장

`artifacts/backtest/workspace/` 안에 drafts, draft_revisions, experiments, jobs, attempts, reviews를 저장한다. 개별 실행은 기존 `runs/` 아티팩트 형식을 사용한다. 변경 가능한 메타데이터는 원자적으로 교체하고 완료 실행과 과거 attempts를 보존한다.

worker는 별도 Python 프로세스로 실행하며 UI/CLI가 같은 host-wide OS lock을 공유한다. 실행을 기다리는 launcher는 상태를 계산하지 않고 잠금 해제를 기다린 후 큐를 확인한다. 이 방식은 마지막 worker 종료와 신규 제출이 겹쳐 작업이 큐에 남는 경쟁을 방지한다. 실제 simulation은 하나만 실행한다. Windows의 일반 연구 코드 이식은 이 기능의 범위가 아니다.

작업 중단은 timestamp 그룹 사이에서 확인하며 강제 청산을 만들지 않는다. `interrupted.json`에 마지막 시각·재고·미해결 주문을 남긴다. app 재접속은 worker를 중단하지 않는다. worker가 종료된 경우 **종료된 worker 상태 확인** 또는 CLI status로 INTERRUPTED 상태를 확인하고 재개한다.

## 검증 명령

```bash
python -m pytest -q -p no:cacheprovider
python scripts/validate_backtest_workspace.py \
  --config configs/backtest/binance_recorded.yaml \
  --workspace artifacts/backtest/workspace-validation-new \
  --output artifacts/backtest/workspace-validation-new.json \
  --mode matrix --check-collector
```

검증 경로는 매번 새 경로를 사용한다. matrix는 9개 전략 설정과 수식 1개를 역순으로 반복해 20개 실행의 결정성, source-linked fills, 독립 Decimal 원장 계산, 60초/실행·512 MiB worker 목표를 검사한다. 준비된 표본은 같은 기존 10분 구간을 사용한다. 실제 측정 결과는 completion 문서를 참조한다.

10분 표본의 반복 탐색은 독립 검증 자료를 늘리지 않는다. 정확한 queue와 시장 반응을 복원하지 않으며, funding·margin·시장 전체에 미치는 impact도 검증한 것으로 표시하지 않는다. 손익 부호만으로 전략을 승인하지 않는다.
