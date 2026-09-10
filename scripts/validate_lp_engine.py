"""Bounded LP acceptance: frozen sample, 12 core variants repeated in reverse."""
import argparse
import json
import subprocess
from pathlib import Path

from lobcap.config.backtest import load_backtest_config, variant, BASELINE_STRATEGIES
from lobcap.backtest.runner import run_one
from lobcap.backtest.types import EXECUTION_CONTRACT_VERSION
from lobcap.backtest.replay import write_json
from validate_backtest_workspace import audit_run


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='configs/backtest/binance_recorded.yaml')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        raise ValueError('use a new output path')
    base=load_backtest_config(args.config)
    sample=base.output_path/'samples'/base.sample.sample_id
    cases=[variant(base,strategy=strategy,alpha=alpha) for strategy in BASELINE_STRATEGIES for alpha in ('0','.5','1',None)]
    report={'status':'RUNNING','execution_contract_version':EXECUTION_CONTRACT_VERSION,'runs':[],
            'paper_eligibility':'INSUFFICIENT_EVIDENCE'}
    before=subprocess.check_output(['systemctl','is-active','lobcap@btcusdt.service'],text=True).strip()
    assert before=='active'
    report['collector_before']=before
    expected={}
    try:
        for repeat,sequence in enumerate((cases,list(reversed(cases))),1):
            for config in sequence:
                result=run_one(config,sample)
                audit=audit_run(result.output_dir,config,sample)
                performance=audit['performance']
                assert performance['wall_seconds']<60
                assert performance['process_peak_rss_mib']<512
                key=config.effective_sha256
                if repeat==1:expected[key]=result.digest
                else:assert expected[key]==result.digest
                report['runs'].append({'repeat':repeat,'strategy':config.strategy,'model':config.model.name,
                    'effective_hash':key,'digest':result.digest,'output_dir':str(result.output_dir),'audit':audit})
                write_json(args.output,report)
                print(f'{len(report["runs"])}/24 {config.strategy} {config.model.name}: PASS',flush=True)
        report['collector_after']=subprocess.check_output(['systemctl','is-active','lobcap@btcusdt.service'],text=True).strip()
        assert report['collector_after']=='active'
        report['status']='PASS'
        report['determinism']='PASS'
    except BaseException as exc:
        report['status']='FAIL'
        report['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        write_json(args.output,report)


if __name__=='__main__':main()
