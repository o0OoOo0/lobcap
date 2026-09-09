"""Independently check source-linked fills in a completed acceptance matrix."""
import argparse
import csv
import gzip
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from lobcap.backtest.replay import iter_events, load_sample, write_json
from lobcap.config.backtest import load_backtest_config
from lobcap.config.common import sha256_file
from lobcap.config.experiment import ROOT


def rows(path):
    if path.exists():
        with gzip.open(path,"rt") as stream:
            for line in stream:
                yield json.loads(line)


def audit(config,report_path):
    report=json.loads(report_path.read_text())
    assert report["status"]=="PASS", "acceptance matrix must pass first"
    sample=config.output_path/"samples"/config.sample.sample_id
    _,sample_manifest=load_sample(sample,config)
    run_ids=[]
    for comparison in report["core_comparisons"]:
        with open(Path(comparison)/"results.csv",newline="") as stream:
            run_ids.extend(row["run_id"] for row in csv.DictReader(stream))
    for sensitivity in report["sensitivities"].values():
        run_ids.extend(row["run_id"] for row in sensitivity["results"])
    assert len(run_ids)==len(set(run_ids))==report["runs_validated"]
    # Keep only public trades; no depth book histories or entire run tables.
    trades={event.replay_index:event for event in iter_events(sample) if event.trade is not None}
    fill_count=0
    censored_markouts=0
    for run_id in run_ids:
        path=config.output_path/"runs"/run_id
        manifest=json.loads((path/"run_manifest.json").read_text())
        assert manifest["sample_sha256"]==sample_manifest["sample_sha256"]
        for artifact in manifest["output_artifact_paths"]:
            artifact_path=Path(artifact["path"])
            if not artifact_path.is_absolute():artifact_path=ROOT/artifact_path
            assert sha256_file(artifact_path)==artifact["sha256"], "run artifact checksum mismatch"
        order_states={}
        for order in rows(path/"orders.jsonl.gz"):
            order_states[order["order_id"]]=order
        used=defaultdict(Decimal)
        for fill in rows(path/"fills.jsonl.gz"):
            source=trades[fill["replay_index"]]
            quantity,price=Decimal(fill["quantity"]),Decimal(fill["price"])
            assert quantity>0 and fill["side"]==source.trade.maker_side
            assert fill["source_refs"]==list(source.source_refs)
            assert fill["time_ns"]==source.time_ns<config.sample.end_ns
            assert source.trade.price<=price if fill["side"]=="buy" else source.trade.price>=price
            state=order_states[fill["order_id"]]
            assert state["activation_ns"]<fill["time_ns"]<=state["terminal_ns"]
            assert price==Decimal(state["price"])
            expected_fee=price*quantity*Decimal(manifest["resolved_config"]["maker_bps"])/10000
            assert Decimal(fill["fee"])==expected_fee
            used[source.replay_index]+=quantity
            assert used[source.replay_index]<=source.trade.size, "historical trade volume reused"
            fill_count+=1
        if (path/"markouts.csv.gz").exists():
            with gzip.open(path/"markouts.csv.gz","rt") as stream:
                censored_markouts+=sum(row["missing_reason"]=="sample_end" for row in csv.DictReader(stream))
    assert censored_markouts>0,"expected visible sample-end markout censorship"
    return {"status":"PASS","runs_audited":len(run_ids),"fills_source_checked":fill_count,
        "sample_end_markouts":censored_markouts,"sample_sha256":sample_manifest["sample_sha256"],
        "acceptance_report_sha256":sha256_file(report_path),
        "checks":["run artifact checksums","source side, time, price and references","normal quantity not reused",
                  "activation precedes fill; fill no later than termination","signed maker fees",
                  "no terminal liquidation fills","visible markout censorship"]}


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True)
    parser.add_argument("--acceptance-report",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError("audit output already exists")
    result=audit(load_backtest_config(args.config),args.acceptance_report)
    write_json(args.output,result)
    print(json.dumps(result,sort_keys=True))
