"""Bounded dual-clock acceptance. Reuses the frozen interval; no research OOS jobs."""
import argparse
import json
import subprocess
import time
from pathlib import Path
from decimal import Decimal

from lobcap.backtest.runner import run_sweep,run_one
from lobcap.backtest.replay import prepare_sample,write_json,load_sample
from lobcap.config.backtest import load_backtest_config,variant,STRATEGIES
from lobcap.config.experiment import config_provenance
from validate_backtest_bounded import reconcile_artifact
from audit_backtest_evidence import audit
from lobcap.backtest.diagnostics import read_rows


def check_client(path):
    manifest=json.loads((path/'run_manifest.json').read_text())
    config=manifest['resolved_config']
    received=[r for r in read_rows(path/'timeline.jsonl.gz') if r['event']=='fill_received']
    inventory=Decimal(config['initial_inventory']);cursor=0
    fills={r['order_id']+':'+str(r['replay_index']):r for r in read_rows(path/'fills.jsonl.gz')}
    for row in read_rows(path/'decisions.jsonl.gz'):
        if 'observed_book_receive_ns' not in row:continue
        assert row['observed_book_exchange_ns']<=row['time_ns']
        assert row['observed_book_receive_ns']<=row['time_ns']
        while cursor<len(received) and received[cursor]['time_ns']<=row['time_ns']:
            report=received[cursor]
            fill=fills[report['order_id']+':'+str(report['replay_index'])]
            inventory+=Decimal(fill['quantity'])*(1 if fill['side']=='buy' else -1)
            cursor+=1
        assert inventory==Decimal(row['inventory']), 'strategy inventory differs from delivered fill reports'
        assert inventory+Decimal(row['buy_exposure_known'])<=Decimal(config['max_inventory'])
        assert inventory-Decimal(row['sell_exposure_known'])>=-Decimal(config['max_inventory'])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='configs/backtest/binance_recorded.yaml')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    c=load_backtest_config(args.config)
    report={'status':'RUNNING','provenance':config_provenance(c)}
    started=time.perf_counter()
    all_rows=[]
    try:
        assert subprocess.check_output(['systemctl','is-active','lobcap@btcusdt.service'],text=True).strip()=='active'
        prep=time.perf_counter();sample=prepare_sample(c)
        report['preparation_seconds']=time.perf_counter()-prep
        _,report['sample']=load_sample(sample,c)
        def progress(i,n,result):
            p=reconcile_artifact(result.output_dir)
            check_client(result.output_dir)
            print(f'{i}/{n} {result.output_dir.name}: {p["wall_seconds"]:.2f}s, {p["process_peak_rss_mib"]:.1f}MiB',flush=True)
        matrix=[{'strategy':s,'clock_mode':clock,'alpha':alpha,'report_ms':0}
                for s in STRATEGIES for clock in ('exchange','recorded') for alpha in ('0.5',None)]
        a,rows_a=run_sweep(c,matrix,sample,progress)
        b,rows_b=run_sweep(c,list(reversed(matrix)),sample,progress)
        assert {r['effective_config_sha256']:r['deterministic_digest'] for r in rows_a}=={r['effective_config_sha256']:r['deterministic_digest'] for r in rows_b}
        report.update(core_comparisons=[str(a),str(b)],core_matrix=rows_a,determinism='PASS',sensitivities={})
        all_rows=rows_a+rows_b
        for parameter,values in {'report_ms':[0,20,100],'max_trade_participation':['1','0.1','0.01'],
                                  'impact_stress_bps':['0','1','5']}.items():
            path,rows=run_sweep(c,[{parameter:v} for v in values],sample,progress)
            report['sensitivities'][parameter]={'comparison':str(path),'results':rows}
            all_rows+=rows
        # Preserve the original engine's canonical output, not merely its PnL.
        legacy=load_backtest_config('configs/backtest/binance_demo.yaml')
        r=run_one(legacy)
        assert r.digest=='f723f938e9ba732896202fba7d1948d9b9da9f9f02aa94c235c6dc52fd21396d'
        report['v1_exact_digest_parity']='PASS'
        performance=[reconcile_artifact(c.output_path/'runs'/r['run_id']) for r in all_rows]
        report['runs_validated']=len(all_rows)
        report['performance']={'max_run_wall_seconds':max(p['wall_seconds'] for p in performance),
            'max_process_peak_rss_mib':max(p['process_peak_rss_mib'] for p in performance)}
        report['collector_after']=subprocess.check_output(['systemctl','is-active','lobcap@btcusdt.service'],text=True).strip()
        assert report['collector_after']=='active'
        report['status']='PASS'
        report['client_audit']='PASS'
        report['wall_seconds']=time.perf_counter()-started
        write_json(args.output/'report.json',report)
        write_json(args.output/'source_audit.json',audit(c,args.output/'report.json'))
    except BaseException as exc:
        report['status']='CANCELED' if isinstance(exc,KeyboardInterrupt) else 'FAIL'
        report['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report.setdefault('wall_seconds',time.perf_counter()-started)
        write_json(args.output/'report.json',report)
        print(args.output/'report.json',flush=True)


if __name__=='__main__':main()
