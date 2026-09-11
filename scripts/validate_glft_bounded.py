"""GLFT development-sample comparison with fixed execution and common warmup."""
import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

from lobcap.config.backtest import load_backtest_config,variant
from lobcap.backtest.runner import run_one
from lobcap.backtest.types import jsonable
from lobcap.backtest.research_flow.analysis import iter_rows
from lobcap.backtest.research_flow.workbench_model import reviewed_metrics


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',required=True)
    args=p.parse_args()
    base=load_backtest_config('configs/backtest/binance_recorded.yaml')
    sample=base.output_path/'samples'/base.sample.sample_id
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    base=replace(base,output_path=output)
    common=variant(base,partial_fill_policy='keep_remaining',quote_start_seconds=120,
        maker_bps='0',taker_bps='0',half_spread_ticks='.5')
    cases=[('join_bbo',{'strategy':'join_bbo'}),('fixed_20',{'strategy':'fixed_spread','half_spread_ticks':'20'})]
    cases += [('glft_gamma_'+g,{'strategy':'glft','glft_gamma':g}) for g in ('.03','.1','.3')]
    cases += [('glft_signal_'+g,{'strategy':'glft_microprice','signal_strength':g}) for g in ('0','1','10')]
    cases += [('glft_repeat',{'strategy':'glft'})]
    rows=[]
    for label,patch in cases:
        result=run_one(variant(common,**patch),sample)
        review=reviewed_metrics(result.output_dir);assert review['reconciled']
        traces=list(iter_rows(result.output_dir,'decision_traces'))
        estimates=[t['policy_state']['glft'] for t in traces if 'glft' in t['policy_state']]
        statuses=Counter(r['status'] for r in estimates)
        assert all(o['time_ns']>=common.sample.start_ns+120*1_000_000_000 for o in iter_rows(result.output_dir,'orders'))
        manifest=json.loads((result.output_dir/'run_manifest.json').read_text())
        row={'label':label,'run_id':result.output_dir.name,'output_dir':str(result.output_dir),'digest':result.digest,
             'settings':patch,'summary':result.summary,'review':review,'glft_states':dict(statuses),
             'last_estimate':estimates[-1] if estimates else None,'performance':manifest['performance']}
        rows.append(jsonable(row))
        if label=='glft_repeat':assert result.digest==next(r['digest'] for r in rows if r['label']=='glft_gamma_.1')
        (output/'glft-validation.json').write_text(json.dumps({'sample':base.sample.sample_id,
            'common_quote_start_seconds':120,'purpose':'8-minute common trading interval after 2-minute observation; development evidence only',
            'runs':rows},indent=2),encoding='utf-8')
        print(label,result.summary['net_pnl'],review['fill_events'],dict(statuses),flush=True)
    # Legacy control: new feature must not alter an inactive strategy's outputs.
    old=json.loads((Path('artifacts/backtest/strategy-response')/'strategy-response.json').read_text())
    expected=next(r['digest'] for r in old['runs'] if r['label']=='join_bbo')
    control=run_one(variant(common,strategy='join_bbo',quote_start_seconds=0),sample)
    assert control.digest==expected,'legacy output regression'
    (output/'legacy-parity.json').write_text(json.dumps({'status':'PASS','digest':control.digest,
        'output_dir':str(control.output_dir)},indent=2))


if __name__=='__main__':main()
