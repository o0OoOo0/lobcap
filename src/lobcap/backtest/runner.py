"""Shared CLI/UI entry points; serial, isolated runs with immutable evidence."""
from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import platform
import sys
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import yaml

from lobcap.backtest.artifacts import ArtifactSink
from lobcap.backtest.engine import run
from lobcap.backtest.replay import prepare_sample, load_sample, iter_events, write_json
from lobcap.backtest.types import jsonable
from lobcap.config.backtest import load_backtest_config, variant, STRATEGIES, BASELINE_STRATEGIES, STRINGS, DECIMALS, INTS
from lobcap.config.experiment import write_run_manifest

_RUN_LOCK=threading.Lock()


def utc_now():return datetime.now(timezone.utc).isoformat()


def parse_setting(key,value):
    """Keep decimal CLI/form text lossless; YAML floats would round long alpha values."""
    text=value.strip()
    if key in DECIMALS:
        return None if text.lower()=="null" else text
    return yaml.safe_load(text) if key in INTS else text


def config_document(config):
    def iso(ns):
        seconds,fraction=divmod(ns,1_000_000_000)
        return datetime.fromtimestamp(seconds,timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")+(f".{fraction:09d}" if fraction else "")+"Z"
    return {"schema_version":1,"sample":{"id":config.sample.sample_id,"start":iso(config.sample.start_ns),
        "end":iso(config.sample.end_ns),"warmup_seconds":config.sample.warmup_ns//1_000_000_000,
        "normalization_version":config.sample.normalization_version},
        "data_root":str(config.data_root),"output_path":str(config.output_path),
        "settings":{k:jsonable(getattr(config,k)) for k in sorted(DECIMALS|INTS|STRINGS)}}


def _run_one(config, sample_path=None, research_policy=None):
    preparation_start=time.perf_counter()
    sample_path=Path(sample_path) if sample_path else prepare_sample(config)
    preparation_seconds=time.perf_counter()-preparation_start
    begin, cpu=time.perf_counter(),time.process_time()
    initial,sample_manifest=load_sample(sample_path,config)
    run_id=config.effective_sha256[:12]+"-"+uuid.uuid4().hex[:8]
    output=config.output_path/"runs"/run_id
    sink=ArtifactSink(output)
    started=utc_now()
    (output/"config.yaml").write_text(yaml.safe_dump(config_document(config),sort_keys=True),encoding="utf-8")
    write_json(output/"resolved_config.json",config.resolved())
    status,error,result="FAIL",None,None
    try:
        result=run(config,initial,iter_events(sample_path),sink,bootstrap_receive_ns=sample_manifest.get("bootstrap_receive_ns"),research_policy=research_policy)
        sink.close()
        from lobcap.backtest.diagnostics import build_diagnostics
        build_diagnostics(output,config)
        status="PASS"
    except BaseException as exc:
        status="CANCELED" if isinstance(exc,KeyboardInterrupt) else "FAIL"
        error=f"{type(exc).__name__}: {exc}"
        raise
    finally:
        sink.close()
        rss=None
        if sys.platform.startswith("linux"):
            import resource
            rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
        versions={"python":platform.python_version()}
        for package in ("PyYAML","streamlit","plotly","pandas"):
            try:versions[package]=importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:pass
        manifest_path=write_run_manifest(config,output_dir=output,status=status,started_at=started,completed_at=utc_now(),
            input_paths=tuple(sample_path/name for name in ("sample_manifest.json","initial_book.json","events.jsonl.gz")),
            output_paths=tuple(sorted(output.iterdir())))
        manifest=json.loads(manifest_path.read_text())
        manifest.update({"run_id":run_id,"sample_id":config.sample.sample_id,
            "sample_sha256":sample_manifest["sample_sha256"],"effective_config_sha256":config.effective_sha256,
            "deterministic_digest":result.digest if result else None,"error":error,"environment":versions,
            "command":f"python -m lobcap.backtest run --config {output/'config.yaml'} --sample {sample_path}",
            "date_window":jsonable(config.sample),"resolved_config":config.resolved(),
            "performance":{"wall_seconds":time.perf_counter()-begin,"cpu_seconds":time.process_time()-cpu,
                "process_peak_rss_mib":rss,"sample_preparation_seconds":preparation_seconds,
                "output_bytes":sum(p.stat().st_size for p in output.iterdir())}})
        if research_policy is not None:
            manifest["research_policy"]=jsonable(research_policy)
            manifest["command"]=None  # Research manifest provides the complete reproduction command.
        write_json(manifest_path,manifest)
    return replace(result,output_dir=output)


def run_one(config, sample_path=None):
    if not _RUN_LOCK.acquire(blocking=False):raise RuntimeError("Another backtest is running; wait for it to finish.")
    try:return _run_one(config,sample_path)
    finally:_RUN_LOCK.release()


def comparison_row(config,result,sample_hash):
    fields=("net_pnl","gross_pnl","fees","spread_capture","inventory_mtm","ending_inventory",
            "fill_rate","partial_fill_rate","full_fill_rate","max_abs_inventory","max_drawdown")
    return {"run_id":result.output_dir.name,"sample_sha256":sample_hash,"strategy":config.strategy,
        "fill_model":config.model.name,"alpha":config.alpha,"clock_mode":config.clock_mode,"report_ms":config.report_ms,
        "entry_ms":config.entry_ms,"cancel_ms":config.cancel_ms,
        "maker_bps":config.maker_bps,"half_spread_ticks":config.half_spread_ticks,"inventory_skew_ticks":config.inventory_skew_ticks,
        "max_trade_participation":config.max_trade_participation,"impact_stress_bps":config.impact_stress_bps,
        "stress_adjusted_net_pnl":json.loads((result.output_dir/"diagnostics.json").read_text())["stress_adjusted_net_pnl"],
        "effective_config_sha256":config.effective_sha256,"deterministic_digest":result.digest,
        **{k:result.summary[k] for k in fields}}


def run_sweep(config, changes, sample_path=None, progress=None):
    changes=list(changes)
    if not 1<=len(changes)<=24:raise ValueError("sweep must contain 1–24 configurations")
    configs=[variant(config,**c) for c in changes]  # Validate the entire sweep before any run.
    if not _RUN_LOCK.acquire(blocking=False):raise RuntimeError("Another backtest is running; wait for it to finish.")
    try:
        sample_path=Path(sample_path) if sample_path else prepare_sample(config)
        _,sample_manifest=load_sample(sample_path,config)
        comparison=config.output_path/"comparisons"/uuid.uuid4().hex
        comparison.mkdir(parents=True)
        manifest={"status":"RUNNING","sample_sha256":sample_manifest["sample_sha256"],"runs":[],"started_at":utc_now()}
        write_json(comparison/"comparison_manifest.json",manifest)
        rows=[]
        try:
            for i,c in enumerate(configs):
                result=_run_one(c,sample_path)
                rows.append(comparison_row(c,result,sample_manifest["sample_sha256"]))
                manifest["runs"].append(str(result.output_dir))
                write_json(comparison/"comparison_manifest.json",manifest)
                if progress:progress(i+1,len(configs),result)
            with (comparison/"results.csv").open("w",newline="",encoding="utf-8") as f:
                writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(jsonable(rows))
            manifest["status"]="PASS"
        except BaseException as exc:
            manifest["status"]="CANCELED" if isinstance(exc,KeyboardInterrupt) else "FAIL"
            manifest["error"]=f"{type(exc).__name__}: {exc}"
            raise
        finally:
            manifest["completed_at"]=utc_now()
            write_json(comparison/"comparison_manifest.json",manifest)
        return comparison,rows
    finally:_RUN_LOCK.release()


def core_matrix():return [{"strategy":s,"alpha":a} for s in BASELINE_STRATEGIES for a in ("0","0.5","1",None)]


def main(argv=None):
    parser=argparse.ArgumentParser(description="Offline, bounded market-making backtest lab")
    parser.add_argument("action",choices=("prepare","run","sweep"))
    parser.add_argument("--config",required=True)
    parser.add_argument("--sample")
    parser.add_argument("--set",action="append",default=[],metavar="KEY=VALUE")
    parser.add_argument("--parameter",choices=sorted(DECIMALS|INTS|STRINGS))
    parser.add_argument("--values",help="Comma-separated values; use null for TOUCH_FILL")
    parser.add_argument("--core-matrix",action="store_true")
    args=parser.parse_args(argv)
    try:
        config=load_backtest_config(args.config)
        changes=dict(item.split("=",1) for item in args.set)
        config=variant(config,**{k:parse_setting(k,v) for k,v in changes.items()})
        if args.action=="prepare":print(prepare_sample(config))
        elif args.action=="run":
            result=run_one(config,args.sample)
            print(json.dumps({"output":str(result.output_dir),"digest":result.digest,"summary":jsonable(result.summary)}))
        else:
            if args.core_matrix:changes=core_matrix()
            elif args.parameter and args.values:changes=[{args.parameter:parse_setting(args.parameter,v)} for v in args.values.split(",")]
            else:parser.error("sweep requires --core-matrix or --parameter and --values")
            path,rows=run_sweep(config,changes,args.sample,lambda i,n,r:print(f"{i}/{n}: {r.output_dir.name}",flush=True))
            print(path)
    except (ValueError,RuntimeError,OSError) as exc:parser.exit(1,f"{type(exc).__name__}: {exc}\n")
