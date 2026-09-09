"""Guided Streamlit workbench; simulations run only on explicit submissions."""
from pathlib import Path
from datetime import datetime, timezone
import gzip
import itertools
import json

import pandas as pd
import plotly.express as px
import streamlit as st
import yaml

from lobcap.backtest.runner import run_one, run_sweep
from lobcap.backtest.replay import load_sample, write_json
from lobcap.backtest.presentation import (STRATEGIES, STRATEGY_HELP, LABELS, SWEEPS,
    read_json, saved_runs, config_from_run, applicable_parameters, sweep_variants,
    comparison_differences, run_label, result_message, saved_experiments, REASONS, STATUSES, EVENTS, validate_ui_config)
from lobcap.config.backtest import load_backtest_config, variant
from lobcap.config.experiment import ROOT

PAGES=["시작·설정", "실행 결과", "실행 비교", "민감도 실험", "주문 추적", "근거·용어"]
st.set_page_config(page_title="시장조성 백테스트 실험실", layout="wide")
st.title("시장조성 백테스트 실험실")
st.caption("Binance BTCUSDT 선물 · 과거 캡처 재생 · 지정가 메이커 주문")
paths=sorted((ROOT/"configs/backtest").glob("*.yaml"))
config_path=st.sidebar.selectbox("데이터·기본 설정", paths,
    index=next((i for i,p in enumerate(paths) if p.stem=="binance_recorded"),0),
    format_func=lambda p:"수신 지연 포함" if p.stem=="binance_recorded" else "v1 비교 기준")
base=load_backtest_config(config_path)
identity=str(config_path)+base.effective_sha256
if st.session_state.get("dataset_identity")!=identity:
    for key in list(st.session_state):
        if key.startswith("lab_"):del st.session_state[key]
    st.session_state["dataset_identity"]=identity
if "lab_route" in st.session_state:
    st.session_state["lab_page"]=st.session_state.pop("lab_route")
page=st.sidebar.radio("작업", PAGES, key="lab_page")
start=datetime.fromtimestamp(base.sample.start_ns/1e9,timezone.utc)
end=datetime.fromtimestamp(base.sample.end_ns/1e9,timezone.utc)
st.sidebar.caption(f"{start:%Y-%m-%d %H:%M}–{end:%H:%M} UTC · {(base.sample.end_ns-base.sample.start_ns)//60_000_000_000}분")
st.sidebar.caption(f"1 tick = {base.instrument.tick} USDT · 최소 수량 단위 {base.instrument.lot} BTC")
st.sidebar.caption("실시간 연결이나 실제 주문 전송은 없습니다. 같은 표본·설정은 같은 결과를 만듭니다.")
sample_path=base.output_path/"samples"/base.sample.sample_id
runs,issues=saved_runs(base.output_path,base.sample.sample_id)
label=lambda name:run_label(name,runs[name][1])
if issues:
    with st.sidebar.expander(f"불완전한 실행 {len(issues)}개"):
        for issue in issues:st.write(issue)


def navigate(target):
    st.session_state["lab_route"]=target
    st.rerun()


def evidence_rows(file):
    if file.exists():
        with gzip.open(file,"rt",encoding="utf-8") as stream:
            for line in stream:yield json.loads(line)


def settings_table(config):
    fields=["strategy","size","max_inventory","half_spread_ticks","alpha","clock_mode",
            "entry_ms","cancel_ms","report_ms","maker_bps","max_trade_participation","impact_stress_bps"]
    rows=[]
    for key in fields:
        value=getattr(config,key)
        if key=="strategy":value=STRATEGIES[value]
        if key=="alpha" and value is None:value="대기열 무시 (TOUCH_FILL)"
        if key=="clock_mode":value={"recorded":"기록된 수신 시간","exchange":"거래소 시간 · 정보 지연 0"}[value]
        rows.append({"설정":LABELS[key],"값":str(value)})
    return rows


def select_run():
    if not runs:
        st.info("아직 완료된 실행이 없습니다. ‘시작·설정’에서 첫 백테스트를 실행하세요.")
        st.stop()
    wanted=st.session_state.pop("lab_select_next",None)
    if wanted in runs:st.session_state["lab_run"]=wanted
    if st.session_state.get("lab_run") not in runs:st.session_state["lab_run"]=next(iter(runs))
    name=st.selectbox("확인할 실행",list(runs),format_func=label,key="lab_run")
    path,manifest=runs[name]
    st.caption(f"실행 ID {name} · 표본 {manifest['sample_sha256'][:12]} · 종료 시점 기준 평가, 자동 청산 없음")
    with st.expander("이 결과에 실제 적용된 설정"):
        st.dataframe(settings_table(config_from_run(base,manifest)),hide_index=True,width="stretch")
        st.download_button("실행 설정 YAML 다운로드",(path/"config.yaml").read_bytes(),"backtest_config.yaml")
    return path,manifest


def render_setup():
    st.subheader("1. 설정을 정하고 재생하기")
    st.write("전략이 과거 호가·거래를 순서대로 받으며 주문했다고 가정합니다. 먼저 기본값으로 실행한 뒤, 결과에서 손실 원인과 체결 근거를 확인하세요.")
    if sample_path.exists():
        validation=read_json(sample_path/"validation.json")
        if validation.get("status")=="PASS":
            counts=validation.get("trading_counts",{})
            st.success(f"표본 준비 완료 · 호가 {counts.get('depth',0):,}건 / 거래 {counts.get('trade',0):,}건 · 독립 스냅샷 일치 {validation.get('snapshot_matches',0)}회")
        else:st.warning("표본 준비가 실패했습니다. 실행 시 원본을 다시 점검합니다.")
    else:st.info("첫 실행에는 표본 준비가 포함됩니다. Ubuntu 원본 데이터 경로가 필요합니다.")
    source=st.selectbox("설정 불러오기",["기본 설정"]+list(runs),format_func=lambda name:name if name=='기본 설정' else label(name),key="lab_setup_source")
    template=base if source=='기본 설정' else config_from_run(base,runs[source][1])
    if st.session_state.get('lab_setup_context')!=source:
        st.session_state['lab_setup_context']=source
        st.session_state['lab_strategy']=template.strategy
        st.session_state['lab_fill_mode']='대기열 무시 (TOUCH_FILL)' if template.alpha is None else '대기열 추정'
    st.caption("불러온 설정과 전략을 먼저 선택한 뒤 값을 수정하세요. 실행 버튼을 눌러야 입력이 적용됩니다.")
    strategy=st.selectbox("전략",list(STRATEGIES),index=list(STRATEGIES).index(template.strategy),format_func=STRATEGIES.get,key="lab_strategy")
    st.info(STRATEGY_HELP[strategy])
    fill_mode=st.radio("체결 가정",["대기열 추정","대기열 무시 (TOUCH_FILL)"],index=int(template.alpha is None),horizontal=True,key="lab_fill_mode")
    st.caption("대기열 추정은 내 앞의 수량부터 소진합니다. 대기열 무시는 거래가 도달하면 앞선 대기량을 무시합니다. 어느 쪽도 실제 체결 확률이나 수익의 상한을 뜻하지 않습니다.")
    changes={"strategy":strategy}
    with st.form("scenario_"+source+"_"+strategy):
        left,right=st.columns(2)
        with left:
            changes["size"]=st.text_input(LABELS["size"],str(template.size),help="매수·매도 각각의 주문 수량입니다. 0.001 BTC × 체결가격이 한 번의 거래대금입니다.")
            changes["half_spread_ticks"]=st.text_input(LABELS["half_spread_ticks"],str(template.half_spread_ticks),help="0.5 tick은 중심에서 0.05 USDT입니다. 틱 반올림과 현재 최우선 호가 제한 때문에 실제 주문 간격은 더 넓어질 수 있습니다.")
        with right:
            changes["max_inventory"]=st.text_input(LABELS["max_inventory"],str(template.max_inventory),help="잔여 주문이 모두 체결되는 경우까지 포함한 BTC 한도입니다. 취소 요청만으로 예약량을 없애지 않습니다.")
            changes["maker_bps"]=st.text_input(LABELS["maker_bps"],str(template.maker_bps),help="1 bps = 0.01%. 거래대금 100 USDT에 1 bps면 0.01 USDT 비용입니다. 음수는 리베이트입니다.")
        st.caption("수수료 0은 비용을 제외한 진단값입니다. 본인 계정의 수수료를 가정할 때는 직접 입력하세요.")
        with st.expander("전략별 조절값"):
            if strategy in {"inventory_skew","flow_guard"}:
                changes["inventory_skew_ticks"]=st.text_input(LABELS["inventory_skew_ticks"],str(template.inventory_skew_ticks))
            if strategy=="microprice":
                changes["signal_strength"]=st.text_input(LABELS["signal_strength"],str(template.signal_strength),help="0은 중간가격, 1은 최우선 잔량으로 계산한 microprice 중심입니다.")
            if strategy=="flow_guard":
                for key in ("flow_threshold","flow_min_volume","volatility_spread_multiplier"):
                    changes[key]=st.text_input(LABELS[key],str(getattr(base,key)))
                st.caption("30초 변동성이 준비되지 않으면 호가를 중단합니다. 흐름 불균형은 −1~1이고, 중단 기준은 0~1입니다.")
            if strategy=="fixed_spread":st.write("이 전략의 조절값은 위의 호가 거리와 주문 수량입니다.")
        with st.expander("체결·지연 가정"):
            if fill_mode=="대기열 추정":
                changes["alpha"]=st.text_input(LABELS["alpha"],str(template.alpha if template.alpha is not None else '0.5'),help="0은 거래 외 잔량 감소를 대기열 진전에 반영하지 않고, 1은 전부 반영합니다. 체결 확률이 아닙니다.")
            else:changes["alpha"]=None
            clocks=["exchange"] if template.sample.normalization_version.endswith("v1") else ["recorded","exchange"]
            changes["clock_mode"]=st.selectbox(LABELS["clock_mode"],clocks,index=clocks.index(template.clock_mode),format_func=lambda x:"기록 수신 시간" if x=="recorded" else "거래소 시간 · 정보 지연 0")
            for key in ("entry_ms","cancel_ms","report_ms"):
                changes[key]=st.number_input(LABELS[key],min_value=0,value=getattr(base,key),step=10)
            changes["max_trade_participation"]=st.text_input(LABELS["max_trade_participation"],str(template.max_trade_participation),help="0.1은 공개 거래 한 건의 최대 10%만 내 체결에 허용합니다. 상한을 최소 수량 단위로 내리므로 작은 거래에서는 체결이 0일 수 있습니다.")
            st.caption("수신 시간은 수집기 위치 기준입니다. 주문·취소·통보 지연 값은 별도 가정입니다.")
        with st.expander("위험 제어·가상 비용"):
            for key in ("decision_ms","max_age_ms","stale_book_ms"):
                changes[key]=st.number_input(LABELS[key],min_value=1,value=getattr(base,key))
            changes["min_spread_ticks"]=st.text_input(LABELS["min_spread_ticks"],str(template.min_spread_ticks))
            for key,title in (("volatility_widen_bps","호가를 넓히는 변동성 (bps)"),("volatility_reduce_bps","수량을 줄이는 변동성 (bps)"),("volatility_stop_bps","호가를 중단하는 변동성 (bps)")):
                changes[key]=st.text_input(title,"" if getattr(base,key) is None else str(getattr(base,key)),help="비워두면 끕니다. 과거 30초의 1초 수익률 표준편차입니다.") or None
            changes["impact_stress_bps"]=st.text_input(LABELS["impact_stress_bps"],str(template.impact_stress_bps),help="실측 임팩트가 아닙니다. 원래 체결·손익을 보존하고, 거래대금에 가상의 추가 비용을 적용한 결과를 따로 계산합니다.")
            st.caption("테이커 주문과 테이커 비용은 사용하지 않습니다. 펀딩·시장 반응·자동 청산도 포함하지 않습니다.")
        submitted=st.form_submit_button("백테스트 실행",type="primary")
    if submitted:
        config=validate_ui_config(variant(template,**changes))
        with st.spinner("호가 재생 → 주문·체결 → 손익 대조 중…"):
            result=run_one(config)
        st.session_state["lab_select_next"]=result.output_dir.name
        st.session_state["lab_completed"]=result.output_dir.name
        navigate("실행 결과")


def render_result():
    st.subheader("2. 결과와 손익 원인")
    path,manifest=select_run()
    summary=read_json(path/"summary.json")
    st.info(result_message(summary))
    cards=st.columns(4)
    for card,key,title in zip(cards,("net_pnl","spread_capture","inventory_mtm","fees"),("순손익 (USDT)","스프레드 기여 (USDT)","재고 가격변동 (USDT)","수수료 비용 (USDT)")):
        card.metric(title,f"{float(summary[key]):,.6f}")
    st.caption("순손익 = 스프레드 기여 + 재고 가격변동 − 수수료. 재고는 마지막 중간가격으로 평가하며 청산하지 않습니다.")
    stats=summary["execution"]
    cards=st.columns(4)
    cards[0].metric("체결 건수",stats.get("fill_events",0))
    cards[1].metric("체결 수량 (BTC)",str(stats.get("filled_quantity",0)))
    cards[2].metric("최종 재고 (BTC)",str(summary["ending_inventory"]))
    cards[3].metric("최대 낙폭 (USDT)",f"{float(summary['max_drawdown']):,.6f}")
    frame=pd.read_csv(path/"timeseries.csv.gz")
    frame["시각"]=pd.to_datetime(frame["time_ns"],unit="ns",utc=True)
    frame=frame.rename(columns={"net_pnl":"순손익","inventory":"재고 BTC"})
    a,b=st.columns(2)
    a.plotly_chart(px.line(frame,x="시각",y="순손익",title="누적 순손익 · USDT"),width="stretch")
    b.plotly_chart(px.line(frame,x="시각",y="재고 BTC",title="보유 재고"),width="stretch")
    with st.expander("체결·취소·호가 중단 상세"):
        st.dataframe([{"항목":title,"수량":stats.get(key,0)} for key,title in (("submitted","제출 주문"),("activated","활성화 주문"),("rejected","거부 주문"),("canceled","취소 주문"),("censored","종료 시 미해결 주문"),("cancel_pending_fills","취소 대기 중 체결"))],hide_index=True)
        st.write("체결률 = 한 번이라도 체결된 주문 / 활성화된 주문. 취소·표본 종료된 부분 체결도 포함합니다.")
        st.write({"체결률":summary["fill_rate"],"매수 호가 가용 비율":summary["quote_availability"]["buy"],"매도 호가 가용 비율":summary["quote_availability"]["sell"]})
    if (path/"diagnostics.json").exists():
        d=read_json(path/"diagnostics.json")
        st.subheader("체결 후 가격은 유리하게 움직였나")
        rows=[]
        for horizon,item in sorted(d["markouts"].items(),key=lambda pair:int(pair[0])):
            drift=item["volume_weighted_midpoint_drift"]
            rows.append({"체결 후":f"{int(horizon)/1000:g}초","유효 / 전체":f"{item['valid']} / {item['observations']}","관측률":None if item['coverage'] is None else f"{item['coverage']:.1%}","방향 보정 변화 (USDT/BTC)":None if drift is None else float(drift)})
        st.dataframe(rows,hide_index=True,width="stretch")
        st.caption("음수는 매수 후 하락·매도 후 상승입니다. 가격 관측이 250 ms보다 오래되거나 표본 밖이면 제외합니다. 이 값은 손익에 다시 합산하지 않습니다.")
        with st.expander("수수료와 가상 비용 민감도"):
            fee=pd.DataFrame(d['fee_scenarios_fixed_fills']).apply(pd.to_numeric)
            st.plotly_chart(px.line(fee,x='maker_bps',y='net_pnl',markers=True,labels={'maker_bps':'메이커 수수료 bps','net_pnl':'재계산 순손익 USDT'},title="같은 체결에 비용만 바꾼 결과"),width="stretch")
            breakeven=d['break_even_maker_bps_fixed_fills']
            st.write("손익분기 수수료: "+(f"{float(breakeven):.4f} bps" if breakeven is not None else "체결이 없어 계산 불가"))
            st.caption("음수라면 그 크기 이상의 리베이트가 필요한 계산입니다. 비용을 보고 호가를 바꾸는 전략은 ‘민감도 실험’에서 다시 실행하세요.")
            st.metric(f"가상 추가 비용 {d['impact_stress_bps']} bps 적용 후",f"{float(d['stress_adjusted_net_pnl']):,.6f} USDT")
            st.caption("가정한 비용 스트레스입니다. 과거 시장 경로를 바꾸는 마켓임팩트 모델은 아닙니다.")
        with st.expander("주문 판단 이유와 가격 차이"):
            st.dataframe([{"판단 이유":REASONS.get(k,k),"횟수":v} for k,v in d['decision_reasons'].items()],hide_index=True)
            st.write(f"판단 시 중간가격 대비 체결가격 비용: {float(d['decision_mid_shortfall_usdt']):,.6f} USDT (양수일수록 불리)")
            st.caption("스프레드 기여는 체결 시 중간가격과 비교합니다. 위 가격 차이는 판단 시 가격과 비교하므로 같은 값이 아닙니다. 추가 손익 차감도 아닙니다.")
    if (path/'observations.jsonl.gz').exists():
        with st.expander("실제 시뮬레이션 상태와 전략이 알던 상태"):
            obs=pd.DataFrame(evidence_rows(path/'observations.jsonl.gz'))
            obs['시각']=pd.to_datetime(obs['time_ns'],unit='ns',utc=True)
            for column in ('exchange_mid','observed_mid','exchange_inventory','known_inventory'):obs[column]=pd.to_numeric(obs[column])
            obs=obs.rename(columns={'exchange_mid':'거래소 중간가격','observed_mid':'수신한 중간가격','exchange_inventory':'체결된 재고','known_inventory':'통보받은 재고'})
            for fields in (['거래소 중간가격','수신한 중간가격'],['체결된 재고','통보받은 재고']):
                st.plotly_chart(px.line(obs,x='시각',y=fields),width='stretch')
            st.caption("1초 간격 차트입니다. 이벤트별 차이는 주문 추적에서 확인하세요.")
    st.caption("Sharpe: 표본 부족으로 제공하지 않습니다. 계산·회계 검사 통과가 실제 체결 정확도 검증을 뜻하지는 않습니다.")


def render_compare():
    st.subheader("어떤 설정이 결과를 바꿨나")
    selected=st.multiselect("비교할 실행 (2–12개)",list(runs),format_func=label,max_selections=12,key="lab_compare")
    if len(selected)<2:
        st.info("동일 표본의 실행을 두 개 이상 선택하세요. 다른 설정은 아래에 모두 표시됩니다.")
        return
    differences=comparison_differences([runs[name][1] for name in selected])
    if differences:
        st.dataframe(differences,hide_index=True,width="stretch")
        if len(differences)>1:st.warning("여러 설정이 다릅니다. 손익 차이를 하나의 변수 때문이라고 해석할 수 없습니다.")
    else:st.info("실행 설정이 같습니다. 결과 해시까지 같으면 동일한 실험의 반복입니다.")
    summaries=[];curves=[]
    for name in selected:
        path,manifest=runs[name];s=read_json(path/'summary.json')
        summaries.append({'실행':label(name),'순손익 USDT':float(s['net_pnl']),'체결 건수':s['execution'].get('fill_events',0),'최종 재고 BTC':float(s['ending_inventory']),'결과 해시':manifest['deterministic_digest'][:16]})
        frame=pd.read_csv(path/'timeseries.csv.gz');frame['실행']=label(name);frame['시각']=pd.to_datetime(frame['time_ns'],unit='ns',utc=True);curves.append(frame)
    st.dataframe(summaries,hide_index=True,width='stretch')
    st.plotly_chart(px.line(pd.concat(curves),x='시각',y='net_pnl',color='실행',labels={'net_pnl':'순손익 USDT'}),width='stretch')


def render_sensitivity():
    st.subheader("하나의 가정만 바꾸는 실험")
    source=st.selectbox("실험 기준",["기본 설정"]+list(runs),format_func=lambda name:name if name=='기본 설정' else label(name),key="lab_sweep_source")
    scenario=base if source=='기본 설정' else config_from_run(base,runs[source][1])
    if source!='기본 설정':
        _,sample_manifest=load_sample(sample_path,scenario)
        if runs[source][1]['sample_sha256']!=sample_manifest['sample_sha256']:raise ValueError("기준 실행의 표본 체크섬이 현재 표본과 다릅니다.")
    st.caption("아래 기준 설정을 복제합니다. 다른 화면의 입력값이나 마지막 실행을 자동으로 가져오지 않습니다.")
    with st.expander("고정할 기준 설정",expanded=True):st.dataframe(settings_table(scenario),hide_index=True,width='stretch')
    parameter=st.selectbox("바꿀 변수",applicable_parameters(scenario.strategy),format_func=LABELS.get,key="lab_parameter")
    with st.form("sweep"):
        values=st.text_input("비교할 값 (쉼표로 구분)",SWEEPS[parameter],key="lab_values_"+parameter)
        st.caption("α의 null만 대기열 무시를 뜻합니다. 나머지는 표시된 단위를 사용하세요. 최대 24개를 순서대로 실행합니다.")
        submitted=st.form_submit_button("민감도 실험 실행",type='primary')
    if submitted:
        changes=sweep_variants(scenario,parameter,values)
        progress=st.progress(0)
        comparison,rows=run_sweep(scenario,changes,progress=lambda i,n,r:progress.progress(i/n,text=f"{i}/{n} 완료"))
        write_json(comparison/'lab_experiment.json',{'parameter':parameter,'source':source,
            'base_effective_sha256':scenario.effective_sha256,'sample_id':base.sample.sample_id,
            'sample_sha256':rows[0]['sample_sha256']})
        st.session_state['lab_history']=comparison.name
        st.session_state['lab_select_next']=rows[-1]['run_id']
    history,history_issues=saved_experiments(base.output_path,base.sample.sample_id)
    if history_issues:st.warning('읽을 수 없는 저장 실험: '+ '; '.join(history_issues))
    if history:
        if st.session_state.get('lab_history') not in history:st.session_state['lab_history']=next(iter(history))
        chosen=st.selectbox('저장된 민감도 실험',list(history),key='lab_history',format_func=lambda name:LABELS[history[name]['parameter']]+' · '+name[:8])
        saved=history[chosen]
        manifest=read_json(Path(saved['path'])/'comparison_manifest.json')
        if manifest['status']!='PASS':raise ValueError("완료되지 않은 비교입니다.")
        table=pd.read_csv(Path(saved['path'])/'results.csv',dtype={'fill_model':str})
        axis=saved['parameter'];metric='stress_adjusted_net_pnl' if axis=='impact_stress_bps' else 'net_pnl'
        title='가상 비용 차감 후 USDT' if axis=='impact_stress_bps' else '순손익 USDT'
        st.success(f"저장된 실험 {len(table)}/{len(table)} 완료 · 변수: {LABELS[axis]}")
        st.caption(f"기준: {saved['source']} · 비교 ID {Path(saved['path']).name}. 위 입력을 바꿔도 이 저장 결과는 바뀌지 않습니다.")
        table['비교 값']=table['fill_model'] if axis=='alpha' else table[axis].astype(str)
        st.dataframe(table[['비교 값',metric,'ending_inventory','fill_rate']].rename(columns={metric:title,'ending_inventory':'최종 재고 BTC','fill_rate':'주문 체결률'}),hide_index=True,width='stretch')
        st.plotly_chart(px.bar(table,x='비교 값',y=metric,labels={metric:title},title=LABELS[axis]),width='stretch')
        pnl=pd.to_numeric(table[metric]);st.write(f"{title} 범위: {pnl.min():,.6f} ~ {pnl.max():,.6f}")
        st.caption("측정한 값 사이의 손익분기점이나 최적값을 자동 추정하지 않습니다.")
        st.download_button('비교 CSV 다운로드',(Path(saved['path'])/'results.csv').read_bytes(),'comparison.csv')


def render_orders():
    st.subheader("한 주문의 제출부터 체결까지")
    path,manifest=select_run()
    records={}
    for row in evidence_rows(path/'orders.jsonl.gz'):records[row['order_id']]=row
    if not records:
        st.info("주문이 제출되지 않았습니다. 실행 결과의 판단 이유와 호가 중단 조건을 확인하세요.");return
    filled={r['order_id'] for r in evidence_rows(path/'fills.jsonl.gz')}
    ids=sorted(records,key=lambda key:(key not in filled,key))
    oid=st.selectbox('주문',ids,format_func=lambda key:f"{key} · {'매수' if records[key]['side']=='buy' else '매도'} · {STATUSES.get(records[key]['status'],records[key]['status'])} · {'체결 있음' if key in filled else '미체결'}")
    order=records[oid]
    st.write(f"지정가 {order['price']} USDT · 주문 {order['quantity']} BTC · 남은 수량 {order['remaining']} BTC")
    timeline_file=path/'timeline.jsonl.gz'
    trace=[r for r in evidence_rows(timeline_file if timeline_file.exists() else path/'orders.jsonl.gz') if r['order_id']==oid]
    frame=pd.json_normalize(trace);frame['시각']=pd.to_datetime(frame['time_ns'],unit='ns',utc=True)
    if 'actor' not in frame:frame['actor']='exchange'
    frame['actor']=frame['actor'].replace({'client':'전략이 아는 시점','exchange':'거래소 상태','simulation':'표본 종료'})
    frame['event']=frame['event'].map(lambda value:EVENTS.get(value,value))
    st.caption("거래소에서 발생한 시점과 전략이 통보받은 시점이 다를 수 있습니다. 표본 종료는 주문 취소나 청산을 뜻하지 않습니다.")
    st.plotly_chart(px.scatter(frame,x='시각',y='actor',color='event',hover_data=['time_ns']),width='stretch')
    visible=[c for c in ('시각','actor','event','price','remaining','modeled_queue') if c in frame]
    st.dataframe(frame[visible].rename(columns={'actor':'발생 위치','event':'사건','price':'지정가','remaining':'남은 수량','modeled_queue':'추정 선행 대기량'}),hide_index=True,width='stretch')
    fills=[r for r in evidence_rows(path/'fills.jsonl.gz') if r['order_id']==oid]
    queue=[r for r in evidence_rows(path/'execution.jsonl.gz') if r['order_id']==oid]
    with st.expander('공개 거래와 대기열 계산 근거'):
        st.caption('추정 대기량은 진단에만 공개됩니다. 전략 입력에는 포함되지 않습니다.')
        st.dataframe(pd.json_normalize(queue),hide_index=True,width='stretch')
        st.json(fills)
    st.download_button('이 주문 근거 다운로드',json.dumps({'timeline':trace,'fills':fills,'queue':queue},indent=2),oid+'.json')


def render_audit():
    st.subheader('검증 범위와 용어')
    st.write('호가 복원·계산 일관성은 검사할 수 있지만, 실제 내 주문의 대기열 위치와 체결 정확도는 이 L2 데이터만으로 검증할 수 없습니다.')
    st.dataframe([
        {'용어':'tick','뜻':'가격 최소 단위. 이 표본은 0.10 USDT.'},
        {'용어':'bps','뜻':'0.01%. 가격 비율과 수수료에 사용.'},
        {'용어':'중간가격 / mid','뜻':'최우선 매수가와 매도가의 평균.'},
        {'용어':'microprice','뜻':'최우선 양쪽 잔량으로 가중한 가격 신호.'},
        {'용어':'α','뜻':'거래 외 잔량 감소 중 선행 대기량 감소로 반영할 비율. 체결 확률이 아님.'},
        {'용어':'재고 MTM','뜻':'보유 BTC를 현재 중간가격으로 평가할 때 생기는 손익 변화.'},
        {'용어':'markout','뜻':'체결 후 가격 변화. 손익에 더하는 별도 수익이 아님.'},
        {'용어':'검사 통과','뜻':'시뮬레이션 회계·수량·시간 제약을 통과. 실거래 수익성 인증이 아님.'}],hide_index=True,width='stretch')
    st.info('미포함: 시장에 미치는 실제 영향, 펀딩, 헤지, 마진·강제청산, 자동 종료 청산. 짧은 표본에서는 Sharpe를 계산하지 않습니다.')
    if sample_path.exists():
        with st.expander('표본 복원·수집 검증'):st.json(read_json(sample_path/'validation.json'))
    if not runs:return
    path,manifest=select_run()
    st.caption('각 실행에 연결된 근거입니다. 독립 소스 감사 보고서는 별도 검증 작업에서 생성됩니다.')
    with st.expander('실행·코드·표본 출처'):st.json(manifest)
    files=sorted(p.name for p in path.iterdir() if p.is_file())
    name=st.selectbox('다운로드할 근거 파일',files)
    file=path/name
    if name.endswith('.jsonl.gz'):st.dataframe(pd.json_normalize(list(itertools.islice(evidence_rows(file),100))),hide_index=True,width='stretch')
    elif name.endswith('.json'):st.json(read_json(file))
    elif name.endswith('.csv.gz'):st.dataframe(pd.read_csv(file,nrows=100),hide_index=True,width='stretch')
    st.caption('미리보기는 최대 100행입니다. 다운로드에는 해당 파일 전체가 포함됩니다.')
    st.download_button('근거 파일 다운로드',file.read_bytes(),name)


try:
    {PAGES[0]:render_setup,PAGES[1]:render_result,PAGES[2]:render_compare,
     PAGES[3]:render_sensitivity,PAGES[4]:render_orders,PAGES[5]:render_audit}[page]()
except (ValueError,TypeError,KeyError,OSError) as exc:
    st.error('설정 또는 저장된 근거를 처리하지 못했습니다. 입력값과 선택한 실행을 확인하세요.')
    st.code(str(exc),language=None)
    st.caption('실행 전 오류는 새 결과를 만들지 않습니다. 실행 중 실패는 불완전한 실행 목록과 로그에 남습니다.')
except RuntimeError as exc:
    st.error(str(exc))
