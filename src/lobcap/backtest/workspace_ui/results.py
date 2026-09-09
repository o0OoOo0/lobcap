"""Scenario-separated analysis, exact slices, and source-linked evidence."""
import json
from pathlib import Path


def slice_rows(rows, scenario, fixed=None, min_fills=0):
    fixed = fixed or {}
    return [r for r in rows if (scenario is None or r.get("scenario_id") == scenario)
            and all(str(r.get("axis_values", {}).get(k)) == str(v) for k, v in fixed.items())
            and int(r.get("fill_count", r.get("execution", {}).get("orders_with_fill", 0)) or 0) >= min_fills]


def comparison_spec(rows, jobs, mode, scenario, strategy_id, minimum, sort, axes, fixed, overlay_ids):
    if len(overlay_ids) > 6:
        raise ValueError("곡선 비교는 최대 6개 실행을 선택하세요.")
    if len({r['sample_sha256'] for r in rows}) != 1:
        raise ValueError("동일 표본의 결과만 저장할 수 있습니다.")
    return {"schema_version":1, "type":"ComparisonSpec", "job_ids":list(jobs),
            "sample_sha256":rows[0]['sample_sha256'], "mode":mode, "scenario_hash":scenario,
            "strategy_id":strategy_id, "minimum_filled_orders":int(minimum), "sort":sort,
            "sort_ascending":sort in ('fees','max_drawdown'), "heatmap_axes":axes,
            "fixed_axis_values":dict(fixed), "overlay_run_ids":list(overlay_ids),
            "runs":[{"run_id":r['run_id'],"variant_id":r['variant_id'],"effective_hash":r['effective_hash']} for r in rows]}


def render_results(store):
    import streamlit as st
    import pandas as pd
    import plotly.express as px
    from lobcap.backtest.workspace.compare import result_rows, clone_run, compare_rows, setting_diff
    from lobcap.backtest.workspace.store import atomic_json, read_json
    from lobcap.backtest.workspace.contracts import fingerprint

    if st.button('결과 새로고침'):
        st.rerun()

    restore = st.session_state.get('ws_comparison_restore', {})
    saved_comparisons = sorted((store.root/'comparisons').glob('*.json'))
    if saved_comparisons:
        with st.expander('저장한 비교 조건'):
            saved_path = st.selectbox('저장된 비교', saved_comparisons, format_func=lambda p:p.stem[:16])
            saved = read_json(saved_path)
            st.json(saved)
            if st.button('이 비교 조건 불러오기'):
                st.session_state['ws_comparison_restore'] = saved
                st.rerun()

    jobs = store.list_jobs()
    if not jobs:
        st.info("실험을 제출하면 여기에 결과가 쌓입니다.")
        return
    ids = [j["job_id"] for j in jobs]
    selected = st.session_state.get("ws_selected_job")
    defaults = [i for i in restore.get('job_ids',[]) if i in ids] or ([selected] if selected in ids else ids[:1])
    selected_jobs = st.multiselect("비교할 작업 · 원본과 복제 실험을 함께 선택", ids, default=defaults, format_func=lambda x:next(j['name'] for j in jobs if j['job_id']==x)+" · "+x[:8])
    try:
        rows = []
        for job_id in selected_jobs:
            spec = store.get_experiment(job_id)
            run_specs = {r['variant_id']:r for r in spec['runs']}
            rows.extend({**r, "source_job_id":job_id, "submitted_settings":run_specs[r['variant_id']]['settings'], "source_scenario_id":r['scenario_id'], "scenario_id":r['scenario_hash']} for r in result_rows(store.root, job_id))
        if len({r['sample_sha256'] for r in rows}) > 1:
            raise ValueError("서로 다른 표본은 직접 비교할 수 없습니다. 같은 표본의 작업만 선택하세요.")
    except (ValueError, OSError, KeyError) as exc:
        st.error(f"결과 근거를 읽지 못했습니다: {exc}")
        return
    if not rows:
        st.info("아직 정상 완료된 실행이 없습니다. 진행 중·중단·실패 결과는 정상 성과 순위에 섞지 않습니다.")
        return
    modes = ["같은 체결 가정에서 전략 비교", "같은 전략의 비용·체결 민감도"]
    mode = st.radio("비교 목적", modes, index=modes.index(restore['mode']) if restore.get('mode') in modes else 0, horizontal=True)
    scenario = None
    strategy_id = None
    if mode == "같은 체결 가정에서 전략 비교":
        scenarios = sorted({r["scenario_id"] for r in rows})
        def scenario_label(key):
            r = next(r for r in rows if r['scenario_id']==key)
            return f"α={r.get('alpha')} · maker={r.get('maker_bps')}bps · entry/cancel/report={r.get('entry_ms')}/{r.get('cancel_ms')}/{r.get('report_ms')}ms · {key[:8]}"
        scenario = st.selectbox("고정할 체결·비용 가정", scenarios, index=scenarios.index(restore['scenario_hash']) if restore.get('scenario_hash') in scenarios else 0, format_func=scenario_label)
        eligible = compare_rows(rows, scenario_id=scenario)
        st.caption("시나리오 이름이 같아도 실제 체결·비용 값이 다르면 분리합니다. 표본 내 순손익 정렬이며 반복 탐색은 독립 검증이 아닙니다.")
    else:
        strategies = sorted({r['strategy_id'] for r in rows})
        strategy_id = st.selectbox("고정할 전략 설정", strategies, index=strategies.index(restore['strategy_id']) if restore.get('strategy_id') in strategies else 0, format_func=lambda x:x[:16])
        eligible = compare_rows(rows, strategy_id=strategy_id)
        st.caption("전략·위험 정책을 고정하고 비용·체결 조건만 비교합니다. 유리한 체결 가정을 최적 전략으로 선정하지 않습니다.")
    minimum = st.number_input("최소 체결된 주문 수 필터", min_value=0, value=int(restore.get('minimum_filled_orders',0)), step=1)
    filtered = slice_rows(eligible, scenario, min_fills=minimum)
    if not filtered:
        st.info("필터에 해당하는 결과가 없습니다. 체결이 없거나 아직 완료되지 않았을 수 있습니다.")
        return
    scalar_rows = [{**{k:v for k,v in r.items() if not isinstance(v, (dict,list,tuple))}, "체결된 주문 수":r.get("execution", {}).get("orders_with_fill",0)} for r in filtered]
    frame = pd.DataFrame(scalar_rows)
    numeric = [k for k in ("net_pnl","gross_pnl","fees","max_drawdown","fill_count","inventory") if k in frame]
    for key in numeric:
        frame[key] = pd.to_numeric(frame[key], errors="coerce")
    sort_options = numeric or ['variant_id']
    sort = st.selectbox("표본 내 정렬 기준", sort_options, index=sort_options.index(restore['sort']) if restore.get('sort') in sort_options else 0)
    st.dataframe(frame.sort_values(sort, ascending=sort in ("fees","max_drawdown")), hide_index=True)
    st.download_button("표시 결과 CSV", frame.to_csv(index=False).encode("utf-8-sig"), "workspace-results.csv")
    axes = sorted({k for r in filtered for k in r.get("axis_values", {})})
    heatmap_axes, fixed = [], {}
    if len(axes) >= 2 and scenario is not None:
        st.subheader("파라미터 민감도 · 고정 조건의 단면")
        saved_axes = restore.get('heatmap_axes', [])
        x = st.selectbox("가로 축", axes, index=axes.index(saved_axes[0]) if saved_axes and saved_axes[0] in axes else 0)
        y_options = [a for a in axes if a != x]
        y = st.selectbox("세로 축", y_options, index=y_options.index(saved_axes[1]) if len(saved_axes)>1 and saved_axes[1] in y_options else 0)
        heatmap_axes = [x,y]
        for axis in axes:
            if axis not in (x,y):
                fixed_options = sorted({str(r['axis_values'].get(axis)) for r in filtered})
                prior_fixed = restore.get('fixed_axis_values', {}).get(axis)
                fixed[axis] = st.selectbox(f"고정: {axis}", fixed_options, index=fixed_options.index(prior_fixed) if prior_fixed in fixed_options else 0)
        sliced = slice_rows(filtered, scenario, fixed)
        cells = [{x:r["axis_values"][x],y:r["axis_values"][y],"net_pnl":float(r["net_pnl"])} for r in sliced if x in r['axis_values'] and y in r['axis_values']]
        if cells:
            heat = pd.DataFrame(cells)
            if heat.duplicated([x,y]).any():
                st.warning("같은 셀에 여러 결과가 있습니다. 추가 조건을 고정해야 하며 평균으로 합치지 않습니다.")
            else:
                pivot = heat.pivot(index=y, columns=x, values="net_pnl")
                st.plotly_chart(px.imshow(pivot, labels={"color":"순손익 USDT"}, text_auto=".4f", aspect="auto"), use_container_width=True)
                st.caption("빈 셀은 미완료 또는 누락된 조합입니다. 다른 설정의 평균값으로 채우지 않습니다.")
    st.subheader('실행 곡선 비교')
    run_ids = [r['run_id'] for r in filtered]
    overlay_defaults = [r for r in restore.get('overlay_run_ids',[]) if r in run_ids] or run_ids[:min(3,len(run_ids))]
    overlay_ids = st.multiselect('겹쳐 볼 실행 (최대 6개)', run_ids, default=overlay_defaults, max_selections=6)
    overlay_series = []
    for r in filtered:
        if r['run_id'] not in overlay_ids:
            continue
        series_file = Path(r['output_dir'])/'timeseries.csv.gz'
        if not series_file.is_file():
            st.warning(f"곡선 파일 없음: {r['run_id']}")
            continue
        series = pd.read_csv(series_file)
        series['time'] = pd.to_datetime(series['time_ns'], unit='ns', utc=True)
        series['실행'] = r['run_id']
        overlay_series.append(series)
    if overlay_series:
        overlay = pd.concat(overlay_series, ignore_index=True)
        for metric,title in (('net_pnl','누적 순손익 비교 (USDT)'),('gross_pnl','누적 총손익 비교 (USDT)'),('inventory','재고 비교 (BTC)')):
            if metric in overlay:
                st.plotly_chart(px.line(overlay,x='time',y=metric,color='실행',title=title), use_container_width=True)
    comparison = comparison_spec(filtered, selected_jobs, mode, scenario, strategy_id, minimum, sort, heatmap_axes, fixed, overlay_ids)
    if st.button('현재 비교 조건 저장'):
        comparison_id = fingerprint(comparison)
        atomic_json(store.root/'comparisons'/f'{comparison_id}.json', comparison)
        st.success('선택한 실행·필터·정렬·고정 축·곡선 조건을 저장했습니다.')
    st.download_button('비교 조건 JSON', json.dumps(comparison,ensure_ascii=False,indent=2).encode('utf-8'), 'comparison.json')
    selected_id = st.selectbox("자세히 볼 실행", [r["run_id"] for r in filtered])
    row = next(r for r in filtered if r["run_id"] == selected_id)
    if len(filtered) > 1:
        baseline_id = st.selectbox("설정 차이의 기준 실행", [r['run_id'] for r in filtered if r['run_id'] != selected_id])
        baseline = next(r for r in filtered if r['run_id']==baseline_id)
        st.write("기준 → 선택한 실행의 설정 차이")
        st.json(setting_diff(baseline['submitted_settings'], row['submitted_settings']))
    path = Path(row["output_dir"])
    with st.expander("정확한 설정 · 진단", expanded=True):
        st.json(row)
    series_file = path / "timeseries.csv.gz"
    if series_file.is_file():
        series = pd.read_csv(series_file)
        series["time"] = pd.to_datetime(series["time_ns"], unit="ns", utc=True)
        for columns, title in ((["net_pnl","gross_pnl"],"누적 손익 (USDT)"),(["inventory"],"재고 (BTC)")):
            present = [c for c in columns if c in series]
            if present:
                st.plotly_chart(px.line(series, x="time", y=present, title=title), use_container_width=True)
    st.caption("Sharpe: 표본 부족으로 제공하지 않습니다. 종료 재고를 자동 청산하지 않습니다. queue와 마켓임팩트 가정은 실제 측정값이 아닙니다.")
    if st.button("이 실행을 복제해 전략 편집"):
        clone = clone_run(store.root, row['source_job_id'], row['variant_id'], name="복제 · " + selected_id[:12])
        st.session_state.update(ws_draft=clone, ws_axes_text="{}", ws_scenarios_text='[{"name":"baseline","changes":{}}]', ws_axes_mode="간편 두 축")
        st.session_state.pop("ws_preview", None)
        st.success("전략 편집에 원본 설정을 그대로 가져왔습니다. 수정 후 새 초안을 저장하세요.")
        st.rerun()
    with st.expander("원장 · 주문 · 체결 근거 다운로드"):
        for name in ("run_manifest.json","config.yaml","summary.json","invariants.json","decisions.jsonl.gz","orders.jsonl.gz","fills.jsonl.gz","ledger.jsonl.gz","markouts.csv.gz","risk.jsonl.gz"):
            file = path / name
            if file.is_file():
                st.download_button(name, file.read_bytes(), name, key="ws_evidence_"+selected_id+name)
    st.session_state["ws_review_variant"] = selected_id
