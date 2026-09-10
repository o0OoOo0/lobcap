"""UI-independent experiment selection and interpretation rules."""
import json
from decimal import Decimal
from pathlib import Path

from lobcap.backtest.types import jsonable
from lobcap.config.backtest import DECIMALS, INTS, STRINGS, STRUCTURED, variant

STRATEGIES = {"fixed_spread":"고정 스프레드", "inventory_skew":"재고 조절",
              "microprice":"호가 불균형 반영", "flow_guard":"거래 흐름·비용 필터", "volatility_adaptive":"변동성 적응"}
STRATEGY_HELP = {
    "volatility_adaptive":"과거 30초의 변동성이 커지면 호가 폭을 넓힙니다. 변동성 준비 전에는 주문하지 않습니다.",
    "fixed_spread":"현재 중간가격 양쪽에 일정한 간격으로 주문합니다. 가격 방향을 예측하지 않습니다.",
    "inventory_skew":"매수 재고가 늘면 호가 중심을 낮추고, 매도 재고가 늘면 높입니다. 재고를 자동 청산하지는 않습니다.",
    "microprice":"최우선 매수·매도 잔량으로 계산한 가격 신호를 호가 중심에 반영합니다.",
    "flow_guard":"최근 수신한 거래 흐름이 강하게 한쪽으로 쏠리면 반대편 호가를 중단합니다. 변동성과 양수 수수료만큼 호가 폭을 확보합니다."}
LABELS = {"strategy":"전략", "alpha":"거래 외 잔량 감소의 대기열 반영 α", "clock_mode":"시장정보 수신 기준",
    "report_ms":"주문·체결 통보 지연 (ms)", "entry_ms":"주문 도착 지연 (ms)", "cancel_ms":"취소 도착 지연 (ms)",
    "maker_bps":"메이커 수수료 (bps)", "half_spread_ticks":"중심에서 한쪽 호가까지 거리 (tick)",
    "inventory_skew_ticks":"최대 재고일 때 중심 이동 (tick)", "signal_strength":"호가 신호 배수",
    "max_trade_participation":"공개 거래 한 건의 체결 참여 상한", "impact_stress_bps":"가상 추가 비용 (bps)",
    "size":"한쪽 주문 수량 (BTC)", "max_inventory":"최대 절대 재고 (BTC)", "decision_ms":"판단 주기 (ms)",
    "max_age_ms":"주문 최대 유지 시간 (ms)", "stale_book_ms":"호가 최대 허용 나이 (ms)",
    "min_spread_ticks":"최소 시장 스프레드 (tick)", "flow_threshold":"흐름 불균형 중단 기준",
    "flow_min_volume":"흐름 판단 최소 거래량 (BTC)", "volatility_spread_multiplier":"변동성 호가 폭 배수",
    "initial_cash":"초기 가상 현금 (USDT)","initial_inventory":"초기 재고 (BTC)",
    "volatility_widen_bps":"호가 폭 확대 변동성 (bps)","volatility_reduce_bps":"수량 축소 변동성 (bps)",
    "volatility_stop_bps":"호가 중단 변동성 (bps)"}
SWEEPS = {"alpha":"0, 0.5, 1, null", "clock_mode":"exchange, recorded", "report_ms":"0, 20, 100",
    "entry_ms":"0, 10, 50", "cancel_ms":"0, 10, 50", "maker_bps":"-0.1, 0, 1",
    "max_trade_participation":"0.01, 0.1, 1", "impact_stress_bps":"0, 1, 5",
    "half_spread_ticks":"0.5, 1, 2", "inventory_skew_ticks":"0, 1, 2", "signal_strength":"0, 1, 2"}
REASONS={'quote':'기본 양방향 호가','flow_guard':'거래 흐름·비용 조건 충족',
    'stale_book':'받은 호가가 너무 오래됨','invalid_book':'비정상 호가','unknown_depth_coverage':'알려진 호가 범위 밖',
    'minimum_spread':'시장 스프레드가 최소 기준 미만','volatility_warmup':'변동성 계산용 과거 관측 부족',
    'volatility_stop':'변동성 중단 기준 초과','volatility_widen_reduce':'변동성에 따라 호가 폭·수량 조절',
    'buy_flow_suppress_ask':'강한 매수 흐름으로 매도 호가 중단','sell_flow_suppress_bid':'강한 매도 흐름으로 매수 호가 중단'}
STATUSES={'PENDING_ENTRY':'접수 대기','ACTIVE':'체결 가능','CANCEL_PENDING':'취소 대기',
    'CANCELED':'취소 완료','FILLED':'전량 체결','REJECTED':'거부','CENSORED':'표본 종료로 관측 중단'}
EVENTS={'submit':'제출 기록','submit_send':'주문 전송','activate':'주문 활성화','activate_received':'활성화 통보 수신',
    'cancel_send':'취소 전송','cancel_request':'취소 요청 기록','partial_fill':'부분 체결','partial_fill_received':'부분 체결 상태 수신',
    'full_fill':'전량 체결','full_fill_received':'전량 체결 상태 수신','fill_received':'체결 내역 수신',
    'terminal':'주문 종료','terminal_received':'종료 통보 수신','cancel_noop':'이미 체결된 주문의 취소',
    'cancel_noop_received':'취소 무효 통보 수신'}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def saved_runs(output, sample_id):
    runs, issues = {}, []
    paths = sorted((Path(output)/"runs").glob("*/run_manifest.json"), key=lambda p:p.parent.name)
    candidates=[]
    for path in paths:
        try:
            manifest=read_json(path)
            if manifest.get("sample_id")!=sample_id:continue
            if manifest.get("status")!="PASS":
                issues.append(f"{path.parent.name}: {manifest.get('status','상태 없음')} — {manifest.get('error') or '완료되지 않은 실행'}")
                continue
            for name in ("summary.json","config.yaml","timeseries.csv.gz"):
                if not (path.parent/name).is_file():raise ValueError(f"{name} 누락")
            if not isinstance(manifest.get("resolved_config"),dict) or not manifest.get("sample_sha256"):
                raise ValueError("설정 또는 표본 지문 누락")
            candidates.append((manifest.get("completed_at", ""),path.parent,manifest))
        except (OSError,ValueError,TypeError) as exc:
            issues.append(f"{path.parent.name}: 읽기 실패 — {exc}")
    for _,path,manifest in sorted(candidates,key=lambda x:(x[0],x[1].name),reverse=True):
        runs[path.name]=(path,manifest)
    return runs,issues


def config_from_run(base, manifest):
    resolved=manifest["resolved_config"]
    if manifest["sample_id"]!=base.sample.sample_id or resolved.get("sample")!=jsonable(base.sample):
        raise ValueError("다른 표본의 설정은 이 실험에 사용할 수 없습니다.")
    if resolved.get("instrument")!=jsonable(base.instrument):raise ValueError("상품 단위가 다릅니다.")
    defaults={"clock_mode":"exchange","report_ms":0,"max_trade_participation":"1","impact_stress_bps":"0"}
    defaults.update({key:value for key,value in resolved.items() if key in DECIMALS|INTS|STRINGS|STRUCTURED})
    return variant(base,**defaults)


def saved_experiments(output, sample_id):
    experiments,issues={},[]
    for file in sorted((Path(output)/'comparisons').glob('*/lab_experiment.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        try:
            row=read_json(file)
            if row['sample_id']!=sample_id:continue
            manifest=read_json(file.parent/'comparison_manifest.json')
            if manifest['status']!='PASS' or manifest['sample_sha256']!=row['sample_sha256']:
                raise ValueError('미완료 또는 표본 지문 불일치')
            if row['parameter'] not in SWEEPS or not (file.parent/'results.csv').is_file():raise ValueError('변수 또는 결과 누락')
            experiments[file.parent.name]={**row,'path':str(file.parent)}
        except (OSError,ValueError,KeyError,TypeError) as exc:issues.append(f'{file.parent.name}: {exc}')
    return experiments,issues


def applicable_parameters(strategy):
    parameters=list(SWEEPS)
    if strategy not in {"inventory_skew","flow_guard"}:parameters.remove("inventory_skew_ticks")
    if strategy!="microprice":parameters.remove("signal_strength")
    return parameters


def sweep_variants(config, parameter, text):
    from lobcap.backtest.runner import parse_setting
    if parameter not in applicable_parameters(config.strategy):raise ValueError("현재 전략에 적용되지 않는 변수입니다.")
    values=[v.strip() for v in text.split(",")]
    if not 2<=len(values)<=24 or any(not v for v in values):raise ValueError("비교할 값을 2–24개 입력하세요. 빈 값은 사용할 수 없습니다.")
    changes=[{parameter:parse_setting(parameter,value)} for value in values]
    configs=[variant(config,**change) for change in changes]
    for current in configs:validate_ui_config(current)
    if len({getattr(c,parameter) for c in configs})!=len(configs):raise ValueError("동일한 값이 중복되어 있습니다.")
    return changes


def validate_ui_config(config):
    decisions=(config.sample.end_ns-config.sample.start_ns)//(config.decision_ms*1_000_000)
    if decisions>60_000:raise ValueError(f"판단 횟수 {decisions:,}회: 화면 실행 한도는 60,000회입니다. 판단 주기를 늘리세요.")
    if config.size<config.instrument.lot:raise ValueError("주문 수량이 최소 수량 단위보다 작아 호가를 낼 수 없습니다.")
    if config.max_inventory<config.instrument.lot:raise ValueError("재고 한도가 최소 주문 수량보다 작습니다.")
    return config


def comparison_differences(manifests):
    if len({m['sample_sha256'] for m in manifests})!=1:raise ValueError("표본 체크섬이 다른 결과는 직접 비교할 수 없습니다.")
    if len({m['resolved_config'].get('execution_contract_version','legacy-v1') for m in manifests})!=1:
        raise ValueError("체결 엔진 버전이 다른 결과는 같은 비교에 사용할 수 없습니다.")
    rows=[]
    keys=sorted(set().union(*(m['resolved_config'].keys() for m in manifests)) & (DECIMALS|INTS|STRINGS|STRUCTURED))
    for key in keys:
        values=[m['resolved_config'].get(key) for m in manifests]
        comparable={Decimal(str(v)) if key in DECIMALS and v is not None else json.dumps(v,sort_keys=True) if key in STRUCTURED else v for v in values}
        if len(comparable)>1:
            rows.append({"설정":LABELS.get(key,key), **{m['run_id']:str(v) for m,v in zip(manifests,values)}})
    return rows


def run_label(name, manifest):
    c=manifest['resolved_config']
    stamp=manifest.get('completed_at','')[:19].replace('T',' ')
    model='대기열 무시' if c['alpha'] is None else 'α='+str(c['alpha'])
    return f"{stamp} UTC · {STRATEGIES.get(c['strategy'],c['strategy'])} · {model} · {name[-8:]}"


def result_message(summary):
    fills=summary.get('execution',{}).get('fill_events',0)
    if not fills:return "체결 0건입니다. 손익이 0이어도 전략이 유효하다는 뜻은 아닙니다. 주문 거부·취소와 호가 가용 시간을 확인하세요."
    parts={"스프레드 기여":Decimal(summary['spread_capture']),"재고 가격변동":Decimal(summary['inventory_mtm']),"수수료":-Decimal(summary['fees'])}
    if Decimal(summary['net_pnl'])<0:
        label=min(parts,key=parts.get)
        return f"가장 큰 음의 손익 기여는 {label} ({parts[label]:,.6f} USDT)입니다. 아래 체결 수와 재고 변화를 함께 확인하세요."
    return "이 구간의 계산 손익은 0 이상입니다. 표본 길이·체결 가정·미포함 비용을 확인한 뒤 해석하세요."
