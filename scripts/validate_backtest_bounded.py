"""Offline bounded acceptance gate. Does not invoke research/OOS or collection."""
import argparse
import csv
import gzip
import json
import subprocess
import time
from decimal import Decimal
from pathlib import Path

from lobcap.backtest.runner import run_sweep,core_matrix
from lobcap.backtest.replay import prepare_sample,load_sample,write_json
from lobcap.config.backtest import load_backtest_config
from lobcap.config.experiment import config_provenance


def reconcile_artifact(path):
    manifest=json.loads((path/"run_manifest.json").read_text())
    c=manifest["resolved_config"]
    initial_cash=Decimal(c["initial_cash"])
    inventory=Decimal(c["initial_inventory"])
    cash=initial_cash
    with gzip.open(path/"fills.jsonl.gz","rt") if (path/"fills.jsonl.gz").exists() else __import__("contextlib").nullcontext([]) as stream:
        for line in stream:
            fill=json.loads(line)
            sign=1 if fill["side"]=="buy" else -1
            q,p,fee=(Decimal(fill[k]) for k in ("quantity","price","fee"))
            inventory+=sign*q;cash-=sign*q*p+fee
    summary=json.loads((path/"summary.json").read_text())
    assert inventory==Decimal(summary["ending_inventory"])
    assert cash==Decimal(summary["ending_cash"])
    with gzip.open(path/"ledger.jsonl.gz","rt") as stream:
        for line in stream:
            row=json.loads(line)
            d=lambda k:Decimal(row[k])
            assert d("equity")==d("cash")+d("inventory")*d("mark_price")
            assert d("gross_pnl")==d("spread_capture")+d("inventory_mtm")
            assert d("net_pnl")==d("gross_pnl")-d("fees")
    assert summary["sharpe"] is None
    for h in ("100","1000","5000"):
        assert summary["markouts"][h]["valid"]<=summary["markouts"][h]["observations"]
    performance=manifest["performance"]
    assert performance["wall_seconds"]<60,performance
    assert performance["process_peak_rss_mib"] is not None and performance["process_peak_rss_mib"]<512,performance
    return performance


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True)
    parser.add_argument("--output",default="artifacts/backtest/validation")
    args=parser.parse_args()
    config=load_backtest_config(args.config)
    report_path=Path(args.output)
    report_path.mkdir(parents=True,exist_ok=False)
    report={"status":"RUNNING","provenance":config_provenance(config)}
    started=time.perf_counter()
    try:
        before=subprocess.check_output(["systemctl","is-active","lobcap@btcusdt.service"],text=True).strip()
        assert before=="active"
        prep=time.perf_counter();sample=prepare_sample(config)
        report["preparation_seconds"]=time.perf_counter()-prep
        _,manifest=load_sample(sample,config)
        validation=json.loads((sample/"validation.json").read_text())
        assert validation["trading_counts"]=={"depth":5881,"trade":8043},validation
        assert validation["snapshot_matches"]==2
        report["sample"]=manifest;report["sample_validation"]=validation
        def progress(i,n,r):print(f"{i}/{n} {r.output_dir.name}: net={r.summary['net_pnl']}",flush=True)
        first,rows1=run_sweep(config,core_matrix(),sample,progress)
        second,rows2=run_sweep(config,list(reversed(core_matrix())),sample,progress)
        assert {r["effective_config_sha256"]:r["deterministic_digest"] for r in rows1}=={r["effective_config_sha256"]:r["deterministic_digest"] for r in rows2}
        report["core_matrix"]=rows1;report["determinism"]="PASS"
        report["core_comparisons"]=[str(first),str(second)]
        sweep_specs={"alpha":["0","0.1234","0.501","0.502","1",None],
            "entry_ms":[0,10,50,100,250],"cancel_ms":[0,10,50,100,250],
            "maker_bps":["-0.1","0","0.5","1","2"],"half_spread_ticks":["0.5","1","2"],
            "inventory_skew_ticks":["0","1","2"]}
        report["sensitivities"]={}
        all_rows=rows1+rows2
        for parameter,values in sweep_specs.items():
            changes=[{parameter:v,**({"strategy":"inventory_skew"} if parameter=="inventory_skew_ticks" else {})} for v in values]
            output,rows=run_sweep(config,changes,sample,progress)
            report["sensitivities"][parameter]={"comparison":str(output),"results":rows}
            all_rows+=rows
        performance=[]
        for row in all_rows:
            performance.append(reconcile_artifact(config.output_path/"runs"/row["run_id"]))
        report["runs_validated"]=len(all_rows)
        report["performance"]={"max_run_wall_seconds":max(p["wall_seconds"] for p in performance),
            "max_process_peak_rss_mib":max(p["process_peak_rss_mib"] for p in performance),
            "total_output_bytes":sum(p["output_bytes"] for p in performance)}
        report["collector_after"]=subprocess.check_output(["systemctl","is-active","lobcap@btcusdt.service"],text=True).strip()
        assert report["collector_after"]=="active"
        report["status"]="PASS"
    except Exception as exc:
        report["status"]="FAIL";report["error"]=f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["wall_seconds"]=time.perf_counter()-started
        write_json(report_path/"report.json",report)
        print(report_path/"report.json",flush=True)


if __name__=="__main__":main()
