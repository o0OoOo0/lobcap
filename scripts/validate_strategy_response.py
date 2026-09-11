"""Frozen-sample diagnostic ranges; never selects a profitable sample or model."""
import argparse
from dataclasses import replace
import json
from pathlib import Path

from lobcap.config.backtest import load_backtest_config,variant
from lobcap.backtest.runner import run_one
from lobcap.backtest.types import jsonable
from lobcap.backtest.research_flow.workbench_model import reviewed_metrics


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',default='configs/backtest/binance_recorded.yaml')
    p.add_argument('--output',required=True)
    args=p.parse_args()
    base=load_backtest_config(args.config)
    sample=base.output_path/'samples'/base.sample.sample_id
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    config=replace(base,output_path=output)
    cases=[('no_trade',{'strategy':'no_trade'}),('join_bbo',{'strategy':'join_bbo'})]
    cases += [('fixed_'+str(w),{'strategy':'fixed_spread','half_spread_ticks':str(w)}) for w in [.5,5,20,100]]
    cases += [('skew_'+str(w),{'strategy':'inventory_skew','half_spread_ticks':'5','inventory_skew_ticks':str(w)}) for w in [5,50]]
    cases += [('micro_'+str(w),{'strategy':'microprice','half_spread_ticks':'5','signal_strength':str(w)}) for w in [1,10]]
    cases += [('flow_withdraw',{'strategy':'flow_withdraw'})]
    rows=[]
    for label,patch in cases:
        result=run_one(variant(config,partial_fill_policy='keep_remaining',maker_bps='0',taker_bps='0',**patch),sample)
        review=reviewed_metrics(result.output_dir)
        assert review['reconciled'],label
        row={'label':label,'settings':patch,'run_id':result.output_dir.name,'output_dir':str(result.output_dir),
             'digest':result.digest,'summary':result.summary,'review':review}
        rows.append(jsonable(row))
        (output/'strategy-response.json').write_text(json.dumps({'purpose':'Diagnostic parameter response, not optimization or out-of-sample validation',
            'sample':base.sample.sample_id,'runs':rows},indent=2),encoding='utf-8')
        print(label,result.summary['net_pnl'],review['fill_events'],flush=True)


if __name__=='__main__':main()
