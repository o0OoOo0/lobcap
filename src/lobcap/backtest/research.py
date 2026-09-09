"""Frozen, resumable bounded strategy experiments. Never certifies paper readiness."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, replace
from decimal import Decimal, localcontext
import hashlib
import html
import json
from pathlib import Path
import uuid

import yaml

from lobcap.backtest.diagnostics import read_rows
from lobcap.backtest.replay import load_sample, write_json
from lobcap.backtest.research_execution import ResearchPolicy
from lobcap.backtest.runner import _RUN_LOCK, _run_one, config_document, utc_now
from lobcap.backtest.types import NS, ZERO, jsonable
from lobcap.config.backtest import load_backtest_config, variant, STRATEGIES
from lobcap.config.common import sha256_file
from lobcap.config.experiment import ROOT, config_provenance

SCENARIOS={
    "baseline":{"alpha":"0.5"},
    "queue_zero":{"alpha":"0"},
    "queue_one":{"alpha":"1"},
    "touch_diagnostic":{"alpha":None},
    "fees_plus_50pct":{"maker_bps":"3","taker_bps":"7.5"},
    "combined_stress":{"alpha":"0","maker_bps":"3","taker_bps":"7.5","entry_ms":50,"cancel_ms":50,"report_ms":50},
}
DEFAULT_STRATEGIES=("fixed_spread","inventory_skew","microprice","flow_guard")
DEFAULT_SETTINGS={"initial_cash":"1000","initial_inventory":"0","size":"0.001","max_inventory":"0.005",
    "maker_bps":"2","taker_bps":"5","entry_ms":20,"cancel_ms":20,"report_ms":20,"clock_mode":"recorded",
    "half_spread_ticks":"0.5","inventory_skew_ticks":"1","signal_strength":"1","alpha":"0.5",
    "max_trade_participation":"0.1","impact_stress_bps":"0","decision_ms":250,"max_age_ms":5000,
    "stale_book_ms":500,"min_spread_ticks":"1","volatility_widen_bps":None,"volatility_reduce_bps":None,
    "volatility_stop_bps":None,"flow_threshold":"0.6","flow_min_volume":"0.005","volatility_spread_multiplier":"1"}
BLOCKERS=[
    {"gate":"temporal_oos","status":"NOT_EVALUATED","reason":"동일 10분 개발 표본만 사용했습니다. 반복 실행은 독립 표본이 아닙니다."},
    {"gate":"untouched_final","status":"NOT_EVALUATED","reason":"미열람 최종 평가 기간이 없습니다."},
    {"gate":"l3_calibration","status":"NOT_EVALUATED","reason":"현재 α는 미보정 가정이며 12일 보정 결과가 연결되지 않았습니다."},
    {"gate":"futures_mechanics","status":"NOT_EVALUATED","reason":"실제 funding·mark price·마진·상품 규칙 이력이 없습니다. 중간가격 회계와 가상 비용만 적용했습니다."},
    {"gate":"regime_stability","status":"NOT_EVALUATED","reason":"다기간 시장 국면·파라미터 이웃 안정성을 검증하지 않았습니다."},
    {"gate":"live_execution","status":"NOT_EVALUATED","reason":"실제 queue·market impact·주문 latency를 보정한 결과가 아닙니다."},
]


def fingerprint(value):
    return hashlib.sha256(json.dumps(jsonable(value),sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def code_fingerprint():
    paths=[]
    for folder in (ROOT/"src/lobcap/backtest",ROOT/"src/lobcap/config"):
        paths.extend(folder.glob("*.py"))
    paths.extend((ROOT/"src/lobcap/l2/fill_models.py",ROOT/"src/lobcap/l2/schema.py"))
    return fingerprint({p.relative_to(ROOT).as_posix():sha256_file(p) for p in sorted(paths)})


def make_plan(base,strategies=DEFAULT_STRATEGIES,hypothesis="기본 전략의 손익과 체결 가정 민감도를 동일 표본에서 비교한다."):
    strategies=tuple(strategies)
    if not 1<=len(strategies)<=4 or len(set(strategies))!=len(strategies) or set(strategies)-set(STRATEGIES):
        raise ValueError("1–4 distinct supported strategies are required (six scenarios each)")
    if not isinstance(hypothesis,str) or not hypothesis.strip() or len(hypothesis)>2000:
        raise ValueError("record a hypothesis before running (1–2000 characters)")
    if base.sample.end_ns-base.sample.start_ns!=600*NS:
        raise ValueError("bounded research requires exactly the existing 10-minute sample")
    if base.sample.normalization_version!="binance-net-depletion-arrival-v2":
        raise ValueError("recorded research requires the arrival-v2 sample")
    config=variant(base,**DEFAULT_SETTINGS)
    policy=ResearchPolicy().validate()
    return {"schema_version":1,"mode":"bounded_development","hypothesis":hypothesis.strip(),
        "base_config":config_document(config),"strategies":sorted(strategies),"scenarios":SCENARIOS,
        "risk_policy":jsonable(policy),"selection_policy":{"version":"bounded-selection-v1",
            "minimum_maker_fills":20,"max_drawdown_usdt":"30","max_loss_usdt":"10",
            "paper_promotion_enabled":False},
        "claims":{"leverage":"1x research exposure budget; not an exchange margin simulation",
            "fees":"2/5 bps maker/taker research assumptions, not account fee verification",
            "hedge":"reduce-only after maker terminal reports; no terminal liquidation",
            "calibration":"uncalibrated alpha; TOUCH_FILL excluded from rank"}}


def policy_from_dict(row):
    fields=ResearchPolicy.__dataclass_fields__
    if set(row)!=set(fields):raise ValueError("invalid policy fields")
    values={k:(v if k in {"version","hedge_ms","hedge_enabled"} else Decimal(v)) for k,v in row.items()}
    return ResearchPolicy(**values).validate()


def summarize_run(path,config):
    """Independent ledger checks and time-weighted diagnostics, bounded memory."""
    with localcontext() as ctx:
        ctx.prec=50
        summary=json.loads((path/"summary.json").read_text())
        inv=json.loads((path/"invariants.json").read_text())
        if inv.get("status")!="PASS" or any(value is not True for key,value in inv.items() if key!="status"):
            raise ValueError("run invariants did not pass")
        start,end=config.sample.start_ns,config.sample.end_ns
        last_t=start;last_inv=config.initial_inventory;absolute=square=at_limit=ZERO
        peak=None;underwater_start=None;max_duration=0;last_row=None
        for row in read_rows(path/"ledger.jsonl.gz"):
            t=row["time_ns"]
            if t<last_t or not start<=t<=end:raise ValueError("invalid ledger time")
            cash,q,mid,equity=(Decimal(row[k]) for k in ("cash","inventory","mark_price","equity"))
            if cash+q*mid!=equity:raise ValueError("independent cash identity failed")
            if Decimal(row["net_pnl"])!=Decimal(row["spread_capture"])+Decimal(row["inventory_mtm"])-Decimal(row["fees"]):
                raise ValueError("independent PnL identity failed")
            if abs(q)>config.max_inventory:raise ValueError("observed inventory limit breached")
            dt=t-last_t;absolute+=abs(last_inv)*dt;square+=last_inv**2*dt
            if abs(last_inv)>=config.max_inventory:at_limit+=dt
            if peak is None or equity>=peak:
                if underwater_start is not None:max_duration=max(max_duration,t-underwater_start)
                peak=equity;underwater_start=None
            elif underwater_start is None:underwater_start=t
            last_t,last_inv,last_row=t,q,row
        if last_row is None:raise ValueError("missing ledger")
        dt=end-last_t;absolute+=abs(last_inv)*dt;square+=last_inv**2*dt
        if abs(last_inv)>=config.max_inventory:at_limit+=dt
        if underwater_start is not None:max_duration=max(max_duration,end-underwater_start)
        if Decimal(last_row["net_pnl"])!=Decimal(summary["net_pnl"]):raise ValueError("summary/ledger mismatch")
        maker=taker=0;maker_fee=taker_fee=turnover=ZERO
        for fill in read_rows(path/"fills.jsonl.gz"):
            role=fill["role"];quantity=Decimal(fill["quantity"]);price=Decimal(fill["price"])
            if role not in {"maker","taker"} or quantity<=0:raise ValueError("invalid fill")
            fee=Decimal(fill["fee"])
            rate=config.maker_bps if role=="maker" else config.taker_bps
            if fee!=price*quantity*rate/10000:raise ValueError("fill fee mismatch")
            turnover+=price*quantity
            if role=="maker":maker+=1;maker_fee+=fee
            else:taker+=1;taker_fee+=fee
        if maker_fee+taker_fee!=Decimal(summary["fees"]):raise ValueError("fees/ledger mismatch")
        first={};full={};activated={};lifetimes=[]
        for row in read_rows(path/"orders.jsonl.gz"):
            oid=row["order_id"]
            if row["event"]=="activate":activated[oid]=row["time_ns"]
            if row["event"] in {"partial_fill","full_fill"}:first.setdefault(oid,row["time_ns"])
            if row["event"]=="full_fill":full[oid]=row["time_ns"]
            if row["event"]=="terminal" and oid in activated:lifetimes.append(row["time_ns"]-activated[oid])
        average=lambda values:sum(values)/len(values)/1e6 if values else None
        depths=[];volumes=[];missing_volume=0
        for row in read_rows(path/"capacity.jsonl.gz"):
            if row["order_to_top_depth"] is not None:depths.append(Decimal(row["order_to_top_depth"]))
            if row["order_to_recent_volume"] is not None:volumes.append(Decimal(row["order_to_recent_volume"]))
            else:missing_volume+=1
        duration=end-start
        return jsonable({"net_pnl":Decimal(summary["net_pnl"]),"gross_pnl":Decimal(summary["gross_pnl"]),
            "fees":maker_fee+taker_fee,"maker_fees":maker_fee,"taker_fees":taker_fee,
            "maker_fills":maker,"taker_level_fills":taker,"max_drawdown":summary["max_drawdown"],
            "ending_inventory":summary["ending_inventory"],"max_abs_inventory":summary["max_abs_inventory"],
            "mean_abs_inventory":absolute/duration,"inventory_second_moment":square/duration,
            "time_at_limit_seconds":at_limit/NS,"max_drawdown_duration_seconds":max_duration/NS,
            "mean_first_fill_ms":average([t-activated[k] for k,t in first.items()]),
            "mean_full_fill_ms":average([t-activated[k] for k,t in full.items()]),
            "mean_order_lifetime_ms":average(lifetimes),"turnover_notional":turnover,
            "net_pnl_per_notional":Decimal(summary["net_pnl"])/turnover if turnover else None,
            "max_order_to_top_depth":max(depths) if depths else None,
            "max_order_to_recent_volume":max(volumes) if volumes else None,"capacity_missing_volume":missing_volume,
            "kill_reason":summary["research_execution"]["kill_reason"],"hedges":summary["research_execution"]["hedge_count"],
            "markouts":summary["markouts"],"independent_accounting":"PASS",
            "sharpe_status":"Unavailable — insufficient sample"})


def evaluate(rows,plan):
    policy=plan["selection_policy"];rank=[]
    for strategy in plan["strategies"]:
        items=[r for r in rows if r["strategy"]==strategy and r["scenario"]!="touch_diagnostic"]
        expected=set(plan["scenarios"])-{"touch_diagnostic"}
        reasons=[]
        if {r["scenario"] for r in items}!=expected or any(r["status"]!="PASS" for r in items):
            reasons.append("incomplete_or_failed_scenarios")
        valid=[r for r in items if r["status"]=="PASS"]
        if any(r["metrics"]["maker_fills"]<policy["minimum_maker_fills"] for r in valid):reasons.append("insufficient_fills")
        if any(Decimal(r["metrics"]["net_pnl"])<=0 for r in valid):reasons.append("nonpositive_stress_pnl")
        if any(Decimal(r["metrics"]["max_drawdown"])>Decimal(policy["max_drawdown_usdt"]) or
               Decimal(r["metrics"]["net_pnl"]) < -Decimal(policy["max_loss_usdt"]) or r["metrics"]["kill_reason"] for r in valid):
            reasons.append("risk_gate")
        rank.append({"strategy":strategy,"exploratory_status":"FAIL" if reasons else "PASS",
            "reasons":reasons,"worst_net_pnl":min((Decimal(r["metrics"]["net_pnl"]) for r in valid),default=None),
            "worst_drawdown":max((Decimal(r["metrics"]["max_drawdown"]) for r in valid),default=None)})
    rank.sort(key=lambda r:(bool(r["reasons"]),-(r["worst_net_pnl"] if r["worst_net_pnl"] is not None else Decimal("-Infinity")),r["strategy"]))
    # This mode has no path to promotion, even if edited metadata claims OOS.
    return jsonable({"status":"BLOCKED_INSUFFICIENT_EVIDENCE","mode":"bounded_development",
        "paper_candidates":[],"paper_ready":False,"ranking_scope":"same-sample exploration only; TOUCH_FILL excluded",
        "exploratory_ranking":rank,"gates":BLOCKERS,"unique_trading_seconds":600,
        "note":"Repeated scenarios do not multiply the amount of independent market data."})


def render_report(plan,selection,rows):
    payload=html.escape(json.dumps(jsonable({"plan":plan,"selection":selection,"results":rows}),ensure_ascii=False,indent=2))
    return '<!doctype html><html lang="ko"><meta charset="utf-8"><title>10분 연구 검토 보고서</title><style>body{max-width:1000px;margin:40px auto;font-family:system-ui;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>10분 표본 전략 비교</h1><p>Paper 후보: 없음 — OOS·보정·선물 mechanics 미검증. 이 표본의 순위는 실거래 적합성 순위가 아닙니다.</p><pre>'+payload+'</pre></html>'


def contained(root,relative):
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError("artifact path outside experiment")
    return path


def run_research(base,sample_path,strategies=DEFAULT_STRATEGIES,hypothesis=None,resume=None,progress=None):
    plan=make_plan(base,strategies,hypothesis or "기본 전략의 손익과 체결 가정 민감도를 동일 표본에서 비교한다.")
    _,sample_manifest=load_sample(sample_path,base)
    plan.update(sample_sha256=sample_manifest["sample_sha256"],code_sha256=code_fingerprint())
    plan_hash=fingerprint(plan)
    if not _RUN_LOCK.acquire(blocking=False):raise RuntimeError("Another backtest is running; wait for it to finish.")
    try:
        if resume:
            folder=Path(resume)
            frozen=json.loads((folder/"experiment.json").read_text())
            if frozen.get("plan_sha256")!=plan_hash or fingerprint(frozen["plan"])!=plan_hash:
                raise ValueError("resume requires identical frozen plan, sample and source code")
            previous=json.loads((folder/"state.json").read_text())
            write_json(folder/("attempt-"+uuid.uuid4().hex+".json"),previous)
        else:
            folder=base.output_path/"research"/(plan_hash[:12]+"-"+uuid.uuid4().hex[:8])
            folder.mkdir(parents=True,exist_ok=False)
            write_json(folder/"experiment.json",{"plan":plan,"plan_sha256":plan_hash,"created_at":utc_now(),
                "provenance":config_provenance(base)})
            write_json(folder/"state.json",{"status":"RUNNING","rows":[]})
            (folder/"base_config.yaml").write_text(yaml.safe_dump(config_document(base)),encoding="utf-8")
        state=json.loads((folder/"state.json").read_text())
        completed={r["variant_id"]:r for r in state["rows"] if r["status"]=="PASS"}
        rows=[];total=len(plan["strategies"])*len(plan["scenarios"])
        state.update(status="RUNNING",error=None)
        write_json(folder/"state.json",state)
        try:
            for strategy in plan["strategies"]:
                for scenario,changes in plan["scenarios"].items():
                    key=strategy+":"+scenario
                    config=replace(variant(base,**{**DEFAULT_SETTINGS,"strategy":strategy,**changes}),output_path=folder/"evaluations")
                    policy=policy_from_dict(plan["risk_policy"])
                    if scenario=="combined_stress":policy=replace(policy,hedge_ms=50)
                    prior=completed.get(key)
                    if prior:
                        output=contained(folder,prior["run_path"])
                        if not prior.get("files") or any(sha256_file(contained(output,n))!=h for n,h in prior["files"].items()):
                            raise ValueError("completed evidence changed; cannot resume")
                        row={**prior,"metrics":summarize_run(output,config)}
                    else:
                        result=_run_one(config,sample_path,policy)
                        row={"variant_id":key,"strategy":strategy,"scenario":scenario,"status":"PASS",
                            "run_path":str(result.output_dir.relative_to(folder)),"digest":result.digest,
                            "config_sha256":config.effective_sha256,"policy":jsonable(policy),
                            "metrics":summarize_run(result.output_dir,config),
                            "files":{p.name:sha256_file(p) for p in sorted(result.output_dir.iterdir()) if p.is_file()}}
                    rows.append(row)
                    write_json(folder/"state.json",{"status":"RUNNING","rows":rows,"completed":len(rows),"total":total})
                    if progress:progress(len(rows),total,row)
            selection=evaluate(rows,plan)
            write_json(folder/"selection.json",selection)
            canonical=[{k:r[k] for k in ("variant_id","digest","metrics")} for r in sorted(rows,key=lambda r:r["variant_id"])]
            digest=fingerprint({"results":canonical,"selection":selection})
            (folder/"report.html").write_text(render_report(plan,selection,canonical),encoding="utf-8")
            with (folder/"results.csv").open("w",newline="",encoding="utf-8-sig") as f:
                flat=[{**{k:r[k] for k in ("variant_id","strategy","scenario","digest")},**{k:v for k,v in r["metrics"].items() if not isinstance(v,dict)}} for r in rows]
                writer=csv.DictWriter(f,fieldnames=list(flat[0]));writer.writeheader();writer.writerows(flat)
            write_json(folder/"handoff.json",{"paper_ready":False,"mode":"research_only","candidates":[],
                "selection":selection,"plan_sha256":plan_hash,"deterministic_digest":digest})
            write_json(folder/"state.json",{"status":"PASS","rows":rows,"completed":total,"total":total,
                "deterministic_digest":digest,"finished_at":utc_now(),"paper_ready":False})
        except BaseException as exc:
            write_json(folder/"state.json",{"status":"CANCELED" if isinstance(exc,KeyboardInterrupt) else "FAIL",
                "rows":rows,"completed":len(rows),"total":total,"failed_variant":key,"error":f"{type(exc).__name__}: {exc}"})
            raise
        return folder
    finally:_RUN_LOCK.release()


def main(argv=None):
    parser=argparse.ArgumentParser(description="10-minute development research; paper promotion disabled")
    parser.add_argument("--config",default=str(ROOT/"configs/backtest/binance_recorded.yaml"))
    parser.add_argument("--strategies",nargs="+",default=list(DEFAULT_STRATEGIES),choices=STRATEGIES)
    parser.add_argument("--hypothesis",default="기본 전략의 손익과 체결 가정 민감도를 동일 표본에서 비교한다.")
    parser.add_argument("--sample");parser.add_argument("--resume");parser.add_argument("--preview",action="store_true")
    args=parser.parse_args(argv);base=load_backtest_config(args.config)
    if args.preview:print(json.dumps(make_plan(base,args.strategies,args.hypothesis),ensure_ascii=False,indent=2));return
    sample=Path(args.sample) if args.sample else base.output_path/"samples"/base.sample.sample_id
    print(run_research(base,sample,args.strategies,args.hypothesis,args.resume,
        lambda i,n,r:print(f"{i}/{n} {r['variant_id']}: {r['metrics']['net_pnl']}",flush=True)))


if __name__=="__main__":main()
