"""Thin Streamlit adapter. All simulations use the same CLI runner."""
from pathlib import Path
import gzip
import json

import pandas as pd
import plotly.express as px
import streamlit as st
import yaml

from lobcap.backtest.runner import run_one,run_sweep,core_matrix,parse_setting
from lobcap.backtest.types import ASSUMPTIONS
from lobcap.config.backtest import load_backtest_config,variant,STRATEGIES,DECIMALS,INTS,STRINGS
from lobcap.config.experiment import ROOT

st.set_page_config(page_title="Market-Making Backtest Lab",layout="wide")
STRATEGY_LABELS={"fixed_spread":"Fixed spread","inventory_skew":"Inventory skew","microprice":"Microprice / imbalance","flow_guard":"Flow guard / 비용·주문흐름 필터"}
st.title("Market-Making Backtest Lab")
st.caption("Binance BTCUSDT · captured market replay · maker-only · 주문·체결·손익 추적")
st.info("캡처 데이터로 실행 가정과 손익을 점검합니다. 동일 데이터·설정은 동일 결과를 만듭니다. 수신 지연과 체결 통보는 분리해 모델링하며, 실제 대기열·마켓임팩트는 실측 검증되지 않았습니다.")
catalogue=sorted((ROOT/"configs/backtest").glob("*.yaml"))
config_path=st.sidebar.selectbox("Dataset configuration",catalogue,index=next((i for i,p in enumerate(catalogue) if p.stem=="binance_recorded"),0),format_func=lambda p:"수신 기록 포함 · 고정 10분 샘플" if p.stem=="binance_recorded" else "기존 v1 샘플")
base=load_backtest_config(config_path)
sample_path=base.output_path/"samples"/base.sample.sample_id
st.sidebar.write(base.sample.sample_id)
st.sidebar.caption("10-minute captured sample · 2026-09-06 14:10–14:20 UTC")
configure,compare,diagnose,timeline,sensitivity,audit=st.tabs(["Configure","Compare","손실 진단","주문 타임라인","Sensitivity","Audit"])

with configure:
    st.subheader("Run a scenario")
    with st.form("scenario"):
        a,b,c=st.columns(3)
        with a:
            strategy=st.selectbox("Strategy",STRATEGIES,format_func=lambda x:STRATEGY_LABELS[x])
            mode=st.selectbox("Fill assumption",["alpha","TOUCH_FILL"])
            alpha=st.text_input("Alpha [0, 1] — uncalibrated","0.5")
            size=st.text_input("Size per side (BTC)",str(base.size))
            half=st.text_input("Half-spread (ticks)",str(base.half_spread_ticks))
            skew=st.text_input("Inventory skew at limit (ticks)",str(base.inventory_skew_ticks))
            signal=st.text_input("Microprice signal strength",str(base.signal_strength))
        with b:
            clock_mode=st.selectbox("시장정보 관측 기준",["recorded","exchange"],index=0 if base.clock_mode=="recorded" else 1,
                format_func=lambda value:"기록된 수신 시간" if value=="recorded" else "거래소 시간 · 지연 없는 비교 기준")
            report=st.number_input("체결·주문 상태 통보 지연 (ms)",min_value=0,value=base.report_ms,step=10)
            entry=st.number_input("Entry latency (ms)",min_value=0,value=base.entry_ms,step=10)
            cancel=st.number_input("Cancel latency (ms)",min_value=0,value=base.cancel_ms,step=10)
            cadence=st.number_input("Decision interval (ms)",min_value=1,value=base.decision_ms)
            age=st.number_input("Maximum order age (ms)",min_value=1,value=base.max_age_ms)
            maker=st.text_input("Maker fee (bps; negative = rebate)",str(base.maker_bps))
            taker=st.text_input("Taker fee (bps; inactive in v1)",str(base.taker_bps))
            st.caption("Zero fees are diagnostic settings, not an exchange fee claim.")
        with c:
            limit=st.text_input("Maximum absolute inventory (BTC)",str(base.max_inventory))
            minimum=st.text_input("Minimum prevailing spread (ticks)",str(base.min_spread_ticks))
            stale=st.number_input("Stale-book threshold (ms)",min_value=1,value=base.stale_book_ms)
            widen=st.text_input("Volatility widen threshold (bps; blank = off)","")
            reduce=st.text_input("Volatility size-reduction threshold (bps; blank = off)","")
            stop=st.text_input("Volatility stop threshold (bps; blank = off)","")
            participation=st.text_input("개별 공개 거래량 대비 최대 체결 비율 (0–1)",str(base.max_trade_participation))
            impact=st.text_input("가상 임팩트 비용 스트레스 (bps; 손익과 별도)",str(base.impact_stress_bps))
        with st.expander("Flow guard 전략 설정 · 검증용, 수익 보장 없음"):
            flow_threshold=st.text_input("한쪽 호가 중단 기준: 주문흐름 불균형",str(base.flow_threshold))
            flow_min=st.text_input("흐름 판단 최소 1초 거래량 (BTC)",str(base.flow_min_volume))
            vol_multiplier=st.text_input("변동성 대비 호가 폭 배수",str(base.volatility_spread_multiplier))
            st.caption("최근 수신한 공개 거래만 사용합니다. 강한 매수 흐름에는 매도 호가를, 강한 매도 흐름에는 매수 호가를 중단합니다. 호가 폭은 양수 maker 비용과 과거 변동성을 고려합니다.")
        submitted=st.form_submit_button("Run scenario",type="primary")
    if submitted:
        try:
            config=variant(base,strategy=strategy,alpha=None if mode=="TOUCH_FILL" else alpha,size=size,
                half_spread_ticks=half,inventory_skew_ticks=skew,signal_strength=signal,entry_ms=entry,cancel_ms=cancel,
                decision_ms=cadence,max_age_ms=age,maker_bps=maker,taker_bps=taker,max_inventory=limit,
                min_spread_ticks=minimum,stale_book_ms=stale,volatility_widen_bps=widen or None,
                volatility_reduce_bps=reduce or None,volatility_stop_bps=stop or None,
                clock_mode=clock_mode,report_ms=report,max_trade_participation=participation,impact_stress_bps=impact,
                flow_threshold=flow_threshold,flow_min_volume=flow_min,volatility_spread_multiplier=vol_multiplier)
            with st.spinner("Replaying and writing auditable results…"):
                result=run_one(config)
            st.session_state["last_run"]=result.output_dir.name
            st.session_state["scenario_changes"]={k:getattr(config,k) for k in DECIMALS|INTS|STRINGS}
            st.success(f"Completed: {result.output_dir.name}")
        except Exception as exc:st.error(str(exc))


def saved_runs():
    rows=[]
    for path in sorted((base.output_path/"runs").glob("*/run_manifest.json"),key=lambda p:p.stat().st_mtime,reverse=True):
        manifest=json.loads(path.read_text())
        if manifest["status"]=="PASS" and manifest["sample_id"]==base.sample.sample_id:
            rows.append((path.parent,manifest))
    return rows


runs=saved_runs()
lookup={p.name:(p,m) for p,m in runs}
latest=st.session_state.get("last_run")
if latest in lookup and latest!=st.session_state.get("focused_run"):
    st.session_state["diagnostic_run"]=latest
    st.session_state["timeline_run"]=latest
    st.session_state["focused_run"]=latest


def run_label(name):
    c=lookup[name][1]["resolved_config"]
    fill="TOUCH_FILL" if c["alpha"] is None else f"α={c['alpha']}"
    return f"{STRATEGY_LABELS[c['strategy']]} · {c.get('clock_mode','exchange')} · {fill} · entry {c['entry_ms']} / cancel {c['cancel_ms']} / report {c.get('report_ms',0)} ms · fee {c['maker_bps']} bps · {name[-4:]}"

with compare:
    st.subheader("Compare identical replay data")
    selected=st.multiselect("Saved runs",list(lookup),default=list(lookup)[:min(4,len(lookup))],max_selections=12,format_func=run_label)
    if selected:
        if len({lookup[name][1]["sample_sha256"] for name in selected})!=1:
            st.error("Selected runs have different sample checksums.")
        else:
            summaries=[];curves=[]
            for name in selected:
                path,manifest=lookup[name]
                summary=json.loads((path/"summary.json").read_text())
                settings=manifest["resolved_config"]
                summaries.append({"run":name,"strategy":settings["strategy"],"alpha":settings["alpha"],
                    "clock":settings.get("clock_mode","exchange"),"report_ms":settings.get("report_ms",0),
                    "entry_ms":settings["entry_ms"],"cancel_ms":settings["cancel_ms"],"maker_bps":settings["maker_bps"],
                    **{k:summary[k] for k in ("net_pnl","gross_pnl","fees","spread_capture","inventory_mtm","fill_rate","partial_fill_rate","full_fill_rate","mean_inventory","max_abs_inventory","max_drawdown","ending_inventory")}})
                frame=pd.read_csv(path/"timeseries.csv.gz")
                frame["run"]=f"{STRATEGY_LABELS[settings['strategy']]} · {'TOUCH' if settings['alpha'] is None else 'α='+settings['alpha']} · {name[-4:]}"
                frame["time"]=pd.to_datetime(frame["time_ns"],unit="ns",utc=True);curves.append(frame)
            st.dataframe(pd.DataFrame(summaries),width="stretch")
            frame=pd.concat(curves,ignore_index=True)
            left,right=st.columns(2)
            left.plotly_chart(px.line(frame,x="time",y="net_pnl",color="run",title="Cumulative net PnL (USDT)"),width="stretch")
            right.plotly_chart(px.line(frame,x="time",y="inventory",color="run",title="Inventory (BTC)"),width="stretch")
            st.plotly_chart(px.line(frame,x="time",y="gross_pnl",color="run",title="Gross trading PnL (USDT)"),width="stretch")
            attribution=pd.DataFrame(summaries).melt(id_vars="run",value_vars=["spread_capture","inventory_mtm","fees"],var_name="component",value_name="USDT")
            attribution["USDT"]=pd.to_numeric(attribution["USDT"])
            st.plotly_chart(px.bar(attribution,x="run",y="USDT",color="component",barmode="group",title="PnL components — fees are signed costs"),width="stretch")
            st.caption("Sharpe: unavailable for 10–30 minute samples. Full execution and markout coverage are in Audit.")
    else:st.write("Run a scenario or a comparison matrix to populate this view.")

with sensitivity:
    st.subheader("Change one assumption at a time")
    with st.form("sweep"):
        parameter=st.selectbox("Parameter",["alpha","clock_mode","report_ms","max_trade_participation","impact_stress_bps","entry_ms","cancel_ms","maker_bps","half_spread_ticks","inventory_skew_ticks","signal_strength"])
        values=st.text_input("Values (comma-separated; null means TOUCH_FILL)","0, 0.5, 1, null")
        run_grid=st.form_submit_button("Run sensitivity")
        run_matrix=st.form_submit_button("Run all 3 strategies × 4 fill models")
        st.caption("clock_mode: exchange, recorded · 고정된 10분에서의 민감도이며 다른 기간의 검증을 대체하지 않습니다.")
    if run_grid or run_matrix:
        try:
            scenario=variant(base,**st.session_state.get("scenario_changes",{}))
            changes=core_matrix() if run_matrix else [{parameter:parse_setting(parameter,v)} for v in values.split(",")]
            bar=st.progress(0)
            comparison,rows=run_sweep(scenario,changes,progress=lambda i,n,r:bar.progress(i/n,text=f"Completed {i}/{n}"))
            st.session_state["comparison"]=str(comparison)
            st.session_state["last_run"]=rows[-1]["run_id"]
            st.session_state["comparison_axis"]="fill_model" if run_matrix else parameter
            st.rerun()
        except Exception as exc:st.error(str(exc))
    comparison_path=st.session_state.get("comparison")
    if comparison_path:
        table=pd.read_csv(Path(comparison_path)/"results.csv")
        st.success(f"Completed {len(table)}/{len(table)} · 저장된 결과")
        st.dataframe(table,width="stretch")
        axis=st.session_state.get("comparison_axis","fill_model")
        if axis not in table:axis="fill_model"
        metric="stress_adjusted_net_pnl" if axis=="impact_stress_bps" else "net_pnl"
        st.plotly_chart(px.line(table,x=axis,y=metric,color="strategy",markers=True,title="Assumption sensitivity — all outcomes shown"),width="stretch")
        pnl=pd.to_numeric(table["net_pnl"])
        st.write(f"Observed net PnL range: {pnl.min():.6f} to {pnl.max():.6f} USDT.")
        st.caption("A sign change brackets an observed break-even region; no optimum or calibrated truth is implied.")
        st.download_button("Download comparison CSV",Path(comparison_path,"results.csv").read_bytes(),"comparison.csv")

with audit:
    st.subheader("Observed data, assumptions, decisions, accounting")
    with st.expander("Model limitations",expanded=True):
        for assumption in ASSUMPTIONS[1:]:st.write("• "+assumption)
        st.write("• 기록 수신 시간은 수집기 위치 기준입니다. 주문/통보 지연은 설정한 가정이며 실측 계정 경로가 아닙니다.")
    if sample_path.exists():
        with st.expander("Observed sample validation"):
            st.json(json.loads((sample_path/"validation.json").read_text()))
            st.json(json.loads((sample_path/"sample_manifest.json").read_text()))
    if lookup:
        selected_audit=st.selectbox("Run to audit",list(lookup),format_func=run_label)
        path,manifest=lookup[selected_audit]
        summary=json.loads((path/"summary.json").read_text())
        st.json({"markouts":summary["markouts"],"execution":summary["execution"],"sharpe":summary["sharpe_status"]})
        with st.expander("Provenance and assumptions"):st.json(manifest)
        with st.expander("Conditional diagnostics"):
            conditional=json.loads((path/"conditional_metrics.json").read_text())
            st.caption(conditional["note"])
            st.dataframe(pd.json_normalize(conditional["buckets"]),width="stretch")
        artifact=st.selectbox("Evidence artifact",[p.name for p in sorted(path.iterdir())])
        file=path/artifact
        if artifact.endswith(".jsonl.gz"):
            with gzip.open(file,"rt") as f:
                import itertools
                preview=[json.loads(line) for line in itertools.islice(f,100)]
            st.dataframe(pd.json_normalize(preview),width="stretch")
            st.caption("Preview limited to 100 rows; download contains all evidence.")
        elif artifact.endswith(".json"):st.json(json.loads(file.read_text()))
        st.download_button("Download evidence",file.read_bytes(),file.name)


def evidence_rows(file):
    if file.exists():
        with gzip.open(file,"rt") as stream:
            for line in stream:yield json.loads(line)


with diagnose:
    st.subheader("손실이 어디에서 발생했는가")
    if lookup:
        chosen=st.selectbox("진단할 실행",list(lookup),key="diagnostic_run",format_func=run_label)
        path,manifest=lookup[chosen]
        if (path/"diagnostics.json").exists():
            d=json.loads((path/"diagnostics.json").read_text())
            s=json.loads((path/"summary.json").read_text())
            labels={"inventory_mtm":"재고 보유 중 가격변동","spread_capture":"체결 시점의 불리한 가격","fees":"수수료","positive_sample_result":"이 샘플에서는 양수","zero_sample_result":"손익 0 · 체결 유무도 확인하세요"}
            st.write("가장 큰 손실 기여: **"+labels[d["primary_loss_component"]]+"**")
            cards=st.columns(4)
            for card,key,label in zip(cards,["net_pnl","spread_capture","inventory_mtm","fees"],["순손익 USDT","체결 시 스프레드 기여","재고 가격변동 손익","수수료 (양수=비용)"]):
                card.metric(label,f"{float(s[key]):.6f}")
            st.caption("순손익 = 스프레드 기여 + 재고 가격변동 − 수수료. 손익이 맞게 계산된 것과 체결 모델이 실제와 맞는 것은 별개의 검증입니다.")
            marks=[]
            for horizon,item in d["markouts"].items():
                marks.append({"체결 후 ms":int(horizon),"유효 관측":item["valid"],"전체":item["observations"],
                    "커버리지":item["coverage"],"방향 보정 mid 변화 USDT/BTC":float(item["volume_weighted_midpoint_drift"]) if item["volume_weighted_midpoint_drift"] is not None else None})
            st.dataframe(pd.DataFrame(marks),width="stretch")
            st.caption("방향 보정 mid 변화가 음수면 매수 후 하락/매도 후 상승입니다. 이 값은 추가 손익으로 더하거나 빼지 않습니다.")
            st.write(f"음의 스프레드 기여 체결: {d['negative_capture_fills']}건 · 주문 결정 mid 대비 실행 shortfall: {float(d['decision_mid_shortfall_usdt']):.6f} USDT")
            st.caption("Shortfall은 의사결정 시점 가격과 체결가의 차이입니다. 지정가에 임의의 슬리피지를 붙인 비용이 아니며, 이미 기록된 손익에 중복 차감하지 않습니다.")
            a,b=st.columns(2)
            a.metric("실제 재고 / 통보받은 재고",f"{s['ending_inventory']} / {s.get('ending_known_inventory',s['ending_inventory'])} BTC")
            b.metric("통보 대기 때문에 몰랐던 최대 재고",str(s.get("max_unreported_inventory","0"))+" BTC")
            if (path/"observations.jsonl.gz").exists():
                with st.expander("거래소 상태와 전략이 알고 있던 상태 비교"):
                    observations=pd.DataFrame(evidence_rows(path/"observations.jsonl.gz"))
                    observations["time"]=pd.to_datetime(observations["time_ns"],unit="ns",utc=True)
                    for column in ("exchange_mid","observed_mid","exchange_inventory","known_inventory"):
                        observations[column]=pd.to_numeric(observations[column])
                    st.plotly_chart(px.line(observations,x="time",y=["exchange_mid","observed_mid"],title="거래소 mid / 수신한 mid"),width="stretch")
                    st.plotly_chart(px.line(observations,x="time",y=["exchange_inventory","known_inventory"],title="체결된 재고 / 통보받은 재고"),width="stretch")
                    st.caption("차트는 1초 간격입니다. 주문 타임라인과 원본 근거에는 이벤트 시점이 유지됩니다.")
            fee=pd.DataFrame(d["fee_scenarios_fixed_fills"])
            fee["maker_bps"]=pd.to_numeric(fee["maker_bps"]);fee["net_pnl"]=pd.to_numeric(fee["net_pnl"])
            st.plotly_chart(px.line(fee,x="maker_bps",y="net_pnl",markers=True,title="같은 체결 경로의 수수료 재계산"),width="stretch")
            st.caption("전략이 비용에 반응하면 체결도 바뀔 수 있습니다. 이 그래프는 체결 경로를 고정한 비용 진단입니다.")
            st.write(f"가상 임팩트 스트레스 {d['impact_stress_bps']} bps 적용 후: **{float(d['stress_adjusted_net_pnl']):.6f} USDT**")
            st.caption("실측 마켓임팩트 모델이 아닙니다. 과거 시장 경로는 고정되며, 가정한 비용을 별도로 스트레스 테스트합니다.")
            st.dataframe(pd.DataFrame([{"판단 이유":k,"횟수":v} for k,v in d["decision_reasons"].items()]),width="stretch")
            st.download_button("진단 전체 다운로드",(path/"diagnostics.json").read_bytes(),"diagnostics.json")
        else:st.info("새 실행부터 손실 진단이 생성됩니다. 기존 실행의 증거는 Audit에서 확인할 수 있습니다.")
    else:st.info("Configure에서 실행한 뒤 결과를 확인하세요.")

with timeline:
    st.subheader("이 주문은 왜 체결됐는가")
    if lookup:
        chosen=st.selectbox("추적할 실행",list(lookup),key="timeline_run",format_func=run_label)
        path,manifest=lookup[chosen]
        file=path/"timeline.jsonl.gz"
        if file.exists():
            ids=list(dict.fromkeys(r["order_id"] for r in evidence_rows(file)))
            filled_ids={r["order_id"] for r in evidence_rows(path/"fills.jsonl.gz")}
            ids.sort(key=lambda value:value not in filled_ids)
            oid=st.selectbox("주문 ID",ids)
            trace=[r for r in evidence_rows(file) if r["order_id"]==oid]
            frame=pd.json_normalize(trace)
            frame["time"]=pd.to_datetime(frame["time_ns"],unit="ns",utc=True)
            st.plotly_chart(px.scatter(frame,x="time",y="actor",color="event",title="거래소 사건과 전략의 인지 시점",hover_data=["time_ns"]),width="stretch")
            columns=[c for c in ["time","actor","event","price","remaining","modeled_queue","exchange_bid","exchange_ask","observed_bid","observed_ask","exchange_time_ns","client_available_ns"] if c in frame]
            st.dataframe(frame[columns],width="stretch")
            fills=[r for r in evidence_rows(path/"fills.jsonl.gz") if r["order_id"]==oid]
            queues=[r for r in evidence_rows(path/"execution.jsonl.gz") if r["order_id"]==oid]
            st.write("공개 거래량 → 대기열 변화 → 체결 수량")
            st.dataframe(pd.json_normalize(queues),width="stretch")
            if fills:
                with st.expander("체결 당시 판단과 원본 참조",expanded=True):st.json(fills)
            else:st.info("이 주문에는 관측된 체결이 없습니다. 위 타임라인에서 취소·거부·표본 종료 여부를 확인하세요.")
            st.download_button("이 주문의 모든 근거 다운로드",json.dumps({"timeline":trace,"fills":fills,"queue_transitions":queues},indent=2),oid+".json")
        else:st.info("기록 수신 모드 또는 통보 지연을 설정한 새 실행에서 상세 타임라인이 생성됩니다.")
