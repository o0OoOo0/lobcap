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
from lobcap.config.backtest import load_backtest_config,variant,STRATEGIES,DECIMALS,INTS
from lobcap.config.experiment import ROOT

st.set_page_config(page_title="Market-Making Backtest Lab",layout="wide")
STRATEGY_LABELS={"fixed_spread":"Fixed spread","inventory_skew":"Inventory skew","microprice":"Microprice / imbalance"}
st.title("Market-Making Backtest Lab")
st.caption("Binance BTCUSDT · exchange-time replay · maker-only · bounded assumption studies")
st.info("Order latency is modeled with zero feed latency. Net PnL includes configured maker fees; funding and terminal liquidation are excluded.")
catalogue=sorted((ROOT/"configs/backtest").glob("*.yaml"))
config_path=st.sidebar.selectbox("Dataset configuration",catalogue,format_func=lambda p:p.stem)
base=load_backtest_config(config_path)
sample_path=base.output_path/"samples"/base.sample.sample_id
st.sidebar.write(base.sample.sample_id)
st.sidebar.caption("10-minute captured sample · 2026-09-06 14:10–14:20 UTC")
configure,compare,sensitivity,audit=st.tabs(["Configure","Compare","Sensitivity","Audit"])

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
        submitted=st.form_submit_button("Run scenario",type="primary")
    if submitted:
        try:
            config=variant(base,strategy=strategy,alpha=None if mode=="TOUCH_FILL" else alpha,size=size,
                half_spread_ticks=half,inventory_skew_ticks=skew,signal_strength=signal,entry_ms=entry,cancel_ms=cancel,
                decision_ms=cadence,max_age_ms=age,maker_bps=maker,taker_bps=taker,max_inventory=limit,
                min_spread_ticks=minimum,stale_book_ms=stale,volatility_widen_bps=widen or None,
                volatility_reduce_bps=reduce or None,volatility_stop_bps=stop or None)
            with st.spinner("Replaying and writing auditable results…"):
                result=run_one(config)
            st.session_state["last_run"]=result.output_dir.name
            st.session_state["scenario_changes"]={k:getattr(config,k) for k in DECIMALS|INTS|{"strategy"}}
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


def run_label(name):
    c=lookup[name][1]["resolved_config"]
    fill="TOUCH_FILL" if c["alpha"] is None else f"α={c['alpha']}"
    return f"{STRATEGY_LABELS[c['strategy']]} · {fill} · entry {c['entry_ms']} / cancel {c['cancel_ms']} ms · fee {c['maker_bps']} bps · {name[-4:]}"

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
        parameter=st.selectbox("Parameter",["alpha","entry_ms","cancel_ms","maker_bps","half_spread_ticks","inventory_skew_ticks","signal_strength"])
        values=st.text_input("Values (comma-separated; null means TOUCH_FILL)","0, 0.5, 1, null")
        run_grid=st.form_submit_button("Run sensitivity")
        run_matrix=st.form_submit_button("Run all 3 strategies × 4 fill models")
    if run_grid or run_matrix:
        try:
            scenario=variant(base,**st.session_state.get("scenario_changes",{}))
            changes=core_matrix() if run_matrix else [{parameter:parse_setting(parameter,v)} for v in values.split(",")]
            bar=st.progress(0)
            comparison,rows=run_sweep(scenario,changes,progress=lambda i,n,r:bar.progress(i/n,text=f"Completed {i}/{n}"))
            st.session_state["comparison"]=str(comparison)
            st.session_state["comparison_axis"]="fill_model" if run_matrix else parameter
        except Exception as exc:st.error(str(exc))
    comparison_path=st.session_state.get("comparison")
    if comparison_path:
        table=pd.read_csv(Path(comparison_path)/"results.csv")
        st.dataframe(table,width="stretch")
        axis=st.session_state.get("comparison_axis","fill_model")
        if axis not in table:axis="fill_model"
        st.plotly_chart(px.line(table,x=axis,y="net_pnl",color="strategy",markers=True,title="Assumption sensitivity — all outcomes shown"),width="stretch")
        pnl=pd.to_numeric(table["net_pnl"])
        st.write(f"Observed net PnL range: {pnl.min():.6f} to {pnl.max():.6f} USDT.")
        st.caption("A sign change brackets an observed break-even region; no optimum or calibrated truth is implied.")
        st.download_button("Download comparison CSV",Path(comparison_path,"results.csv").read_bytes(),"comparison.csv")

with audit:
    st.subheader("Observed data, assumptions, decisions, accounting")
    with st.expander("Model limitations",expanded=True):
        for assumption in ASSUMPTIONS:st.write("• "+assumption)
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
