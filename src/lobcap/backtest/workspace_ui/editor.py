"""Draft editing. Text decimal inputs preserve exactly what the researcher enters."""
import json
from dataclasses import replace
from decimal import Decimal

from lobcap.config.backtest import DECIMALS, INTS, STRINGS, variant
from lobcap.backtest.presentation import LABELS, STRATEGIES

FORMULA = "signal_strength * (microprice - mid) / tick - inventory_skew_ticks * inventory / max_inventory"
GRID_FIELDS = ["half_spread_ticks", "inventory_skew_ticks", "signal_strength", "size", "max_inventory", "decision_ms", "max_age_ms", "flow_threshold", "flow_min_volume", "volatility_spread_multiplier", "max_signal_ticks"]
GROUPS = {
    "전략 · 주문": ["size", "half_spread_ticks", "inventory_skew_ticks", "signal_strength", "decision_ms", "flow_threshold", "flow_min_volume", "volatility_spread_multiplier"],
    "위험 · 초기 자산": ["max_inventory", "initial_cash", "initial_inventory", "min_spread_ticks", "max_age_ms", "stale_book_ms", "volatility_widen_bps", "volatility_reduce_bps", "volatility_stop_bps"],
    "체결 · 비용 가정": ["alpha", "entry_ms", "cancel_ms", "report_ms", "maker_bps", "taker_bps", "max_trade_participation", "impact_stress_bps"],
}


def parse_object(text, label):
    value = json.loads(text, parse_float=Decimal)
    if not isinstance(value, dict):
        raise ValueError(f"{label}: JSON 객체가 필요합니다.")
    return value


def parse_axis_list(text):
    values = [value.strip() for value in text.split(',')]
    if not values or any(not value for value in values):
        raise ValueError("탐색 값 목록에 빈 항목이 있습니다. 쉼표 사이와 끝의 값을 확인하세요.")
    return values


def settings_from_inputs(config, values):
    """Only edit submitted fields; never reapply a preset behind the form."""
    edits = {}
    for name, value in values.items():
        if name in INTS:
            edits[name] = int(value)
        elif name in DECIMALS:
            edits[name] = None if str(value).strip().lower() in ("", "null", "none") else str(value).strip()
        else:
            edits[name] = value
    return variant(config, **edits)


def render_editor(draft, store, sample_path):
    import streamlit as st
    from lobcap.backtest.workspace.resolve import resolve_experiment

    st.write("가설을 적고 값을 바꾼 다음, 실제 적용 설정을 확인하세요. 저장과 화면 이동은 시뮬레이션을 시작하지 않습니다.")
    config = draft.config
    with st.form("workspace_editor_" + str(draft.draft_id) + "_" + str(draft.revision)):
        name = st.text_input("실험 이름", value=draft.name)
        hypothesis = st.text_area("검증할 가설", value=draft.hypothesis, placeholder="어떤 신호가 어떤 상황에서 도움이 될 것으로 예상하나요?")
        strategies = dict(STRATEGIES, expression="직접 작성한 신호 수식")
        strategy = st.selectbox("전략 템플릿", list(strategies), index=list(strategies).index(config.strategy), format_func=strategies.get)
        values = {"strategy": strategy}
        for title, fields in GROUPS.items():
            with st.expander(title, expanded=title == "전략 · 주문"):
                columns = st.columns(2)
                for i, field in enumerate(fields):
                    old = getattr(config, field)
                    with columns[i % 2]:
                        values[field] = st.text_input(LABELS.get(field, {"taker_bps":"테이커 수수료 (bps)"}.get(field, field)), value="null" if old is None else str(old), key="ws_"+field+"_"+str(draft.draft_id)+"_"+str(draft.revision)+config.effective_sha256[:12])
                if title == "체결 · 비용 가정":
                    values["clock_mode"] = st.selectbox("정보 시계", ["recorded", "exchange"], index=["recorded", "exchange"].index(config.clock_mode))
                    st.caption("α는 대기열 감소 가정입니다. null은 TOUCH_FILL 진단입니다. 수수료는 양수=비용·음수=리베이트, 0=비용 없는 가정입니다. 가상 추가 비용은 측정된 마켓임팩트가 아닙니다.")
        with st.expander("직접 작성하는 신호 수식", expanded=config.strategy == "expression"):
            st.caption("전략 템플릿에서 ‘직접 작성한 신호 수식’을 선택하세요. 출력은 중심 이동 ticks: center = mid + tick × 결과. 반스프레드와 위험 통제는 공통 경로를 사용합니다.")
            formula = st.text_area("중심 이동 수식", value=getattr(config, "signal_expression", "") or FORMULA)
            parameters = st.text_area("추가 수식 파라미터 (JSON, 소수는 문자열)", value=json.dumps({k:str(v) for k,v in getattr(config, "expression_parameters", ())}, ensure_ascii=False))
            signal_limit = st.text_input("신호 이동 상한 (ticks)", value=str(getattr(config, "max_signal_ticks", 10)))
            st.markdown("`bid, ask, mid, microprice`는 가격, `tick`은 가격 단위, `imbalance, trade_imbalance`는 −1~1, `volatility_bps`는 과거 30초 변동성, `trade_volume`은 최근 1초 거래량입니다. `inventory, max_inventory`는 BTC입니다. `spread_ticks`도 사용할 수 있습니다.")
            st.caption("허용: + − × ÷, 괄호, abs/min/max/clip. 필요한 관측값이 없으면 호가를 억제합니다. 수식 오류는 실행 실패로 기록됩니다. queue·미래 데이터·Python 코드는 사용할 수 없습니다.")
            example = st.form_submit_button("수식의 예제 입력 계산")
        with st.expander("탐색 축 · 시나리오"):
            st.write("간편 탐색: 바꿀 파라미터와 쉼표로 구분한 값을 입력하세요. ‘선택 안 함’이면 해당 축은 추가하지 않습니다.")
            previous_axes = json.loads(st.session_state.get("ws_axes_text", "{}"))
            modes = ["간편 두 축", "고급 JSON"]
            inferred_mode = "고급 JSON" if len(previous_axes)>2 or any(k not in GRID_FIELDS or not isinstance(v,list) for k,v in previous_axes.items()) else "간편 두 축"
            previous_mode = st.session_state.get("ws_axes_mode", inferred_mode)
            axes_mode = st.radio("탐색 입력 방식", modes, index=modes.index(previous_mode), horizontal=True)
            simple_axes = []
            choices = [""] + GRID_FIELDS
            for axis_index in range(2):
                previous_name = list(previous_axes)[axis_index] if axis_index < len(previous_axes) else ""
                axis_name = st.selectbox(f"탐색 축 {axis_index+1}", choices, index=choices.index(previous_name) if previous_name in choices else 0, format_func=lambda k:LABELS.get(k,k) if k else "선택 안 함")
                previous_values = previous_axes.get(previous_name, [])
                suggested = ", ".join(str(v) for v in previous_values) if isinstance(previous_values,list) else ""
                axis_values = st.text_input(f"축 {axis_index+1}의 값 목록", value=suggested, placeholder="0.5, 1, 2")
                simple_axes.append((axis_name,axis_values))
            axes_text = st.text_area("전략 탐색 축 (JSON)", value=st.session_state.get("ws_axes_text", "{}"), help='예: {"half_spread_ticks":["0.5","1","2"],"inventory_skew_ticks":["0","1","2"]}. 범위는 {"start":"0","stop":"2","step":"1"}.')
            st.caption("JSON은 고급 JSON 모드를 선택했을 때만 사용합니다. 사용자 정의 수식 파라미터는 parameter.이름 축으로 지정합니다.")
            scenario_text = st.text_area("체결·비용 시나리오 (JSON)", value=st.session_state.get("ws_scenarios_text", '[{"name":"baseline","changes":{}}]'), help='예: [{"name":"baseline","changes":{}},{"name":"fee_cost","changes":{"maker_bps":"2"}}]')
            st.caption("기본은 단일 실행입니다. 3 × 3 = 9개. 시나리오 2개면 18개. 제출당 최대 24개이며 초과분을 몰래 생략하지 않습니다.")
        with st.expander("선택적 손실 제한 · 재고 헤지 정책"):
            old_policy = draft.research_policy
            from lobcap.backtest.types import jsonable
            policy_text = st.text_area("정책 JSON (null은 추가 정책 비활성)", value=json.dumps(jsonable(old_policy), ensure_ascii=False, indent=2))
            st.caption("기본 주문·재고 위험 통제는 항상 적용됩니다. 추가 헤지를 켜면 시장가 실행과 테이커 비용이 발생합니다. 기존 정책을 편집하지 않으면 그대로 유지됩니다.")
        saved = st.form_submit_button("초안 저장 · 실행 미리보기", type="primary")
    if example:
        try:
            from lobcap.backtest.workspace.expressions import compile_expression, parameter_values
            params = dict(parameter_values(parse_object(parameters, "수식 파라미터")))
            observed = {"bid":"99.9","ask":"100.1","mid":"100","microprice":"100.02","tick":"0.1","imbalance":"0.2","spread_ticks":"2","volatility_bps":"1","trade_imbalance":"0.3","trade_volume":"0.01","inventory":"0.002","max_inventory":values["max_inventory"],"signal_strength":values["signal_strength"],"inventory_skew_ticks":values["inventory_skew_ticks"]}
            value = compile_expression(formula, tuple(params)).evaluate({**observed, **params})
            st.info(f"예제의 중심 이동: {value} ticks. 저장·실행하지 않았습니다. 아래 값은 언어 확인용 가상 입력이며 표본 성과가 아닙니다.")
            st.json(observed)
        except (ValueError, TypeError) as exc:
            st.error(f"수식 계산 오류: {exc}")
        return
    if not saved:
        return
    try:
        if strategy == "expression":
            values.update(signal_expression=formula, expression_parameters=parse_object(parameters, "수식 파라미터"), max_signal_ticks=signal_limit)
        edited = settings_from_inputs(config, values)
        policy = json.loads(policy_text, parse_float=Decimal)
        axes = parse_object(axes_text, "탐색 축") if axes_mode == "고급 JSON" else {}
        if axes_mode == "간편 두 축":
            for axis_name, axis_values in simple_axes:
                if not axis_name:
                    continue
                if axis_name in axes:
                    raise ValueError("두 탐색 축은 서로 다른 파라미터여야 합니다.")
                axes[axis_name] = parse_axis_list(axis_values)
            axes_text = json.dumps(axes, ensure_ascii=False)
        scenarios = json.loads(scenario_text, parse_float=Decimal)
        candidate = replace(draft, config=edited, name=name, hypothesis=hypothesis, research_policy=policy)
        spec = resolve_experiment(candidate, axes=axes, scenarios=scenarios, sample_path=sample_path)
        updated = store.save_draft(candidate, expected_revision=draft.revision)
        # Resolve again so the immutable preview names the revision actually saved.
        spec = resolve_experiment(updated, axes=axes, scenarios=scenarios, sample_path=sample_path)
        st.session_state.update(ws_draft=updated, ws_preview=spec, ws_axes_text=axes_text, ws_scenarios_text=scenario_text, ws_axes_mode=axes_mode)
        st.success("초안을 저장했습니다. ‘실험 실행’에서 최종 설정을 확인하고 제출하세요.")
        st.rerun()
    except (ValueError, TypeError, KeyError, OSError) as exc:
        st.error(f"저장하지 못했습니다: {exc}")

