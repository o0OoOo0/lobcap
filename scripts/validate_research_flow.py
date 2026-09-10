"""Frozen E0–E4 behavior comparison with source/ledger and trace acceptance."""
import argparse
import json
from pathlib import Path
import subprocess
from decimal import Decimal
from dataclasses import replace

from lobcap.config.backtest import load_backtest_config,variant
from lobcap.backtest.diagnostics import read_rows
from lobcap.backtest.replay import write_json
from lobcap.backtest.workspace.contracts import StrategyDraft,fingerprint,config_from_run_spec
from lobcap.backtest.workspace.store import WorkspaceStore
from lobcap.backtest.workspace.worker import run_pending
from lobcap.backtest.research_flow.experiments import build_experiment,save_conclusion,clone_for_next
from lobcap.backtest.research_flow.catalog import index_runs,comparison_guard
from lobcap.backtest.research_flow.analysis import explain_run,compare_runs,reference_from_run
from validate_backtest_workspace import audit_run


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='configs/backtest/binance_recorded.yaml')
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists() or args.workspace.exists():raise ValueError('use fresh paths')
    config=load_backtest_config(args.config)
    sample=config.output_path/'samples'/config.sample.sample_id
    store=WorkspaceStore(args.workspace)
    draft=store.save_draft(StrategyDraft(config,'E0–E4 주문 행동 비교','같은 시장에서 주문 유지·재고 수량·흐름 철회가 행동과 손익을 어떻게 바꾸는가?'))
    spec=build_experiment(draft,'baseline_suite',sample_path=sample)
    report={'status':'RUNNING','sample':config.sample.sample_id,'jobs':[],'runs':[]}
    assert subprocess.check_output(['systemctl','is-active','lobcap@btcusdt.service'],text=True).strip()=='active'
    expected={}
    try:
        for repeat in range(2):
            if repeat:
                spec['runs']=list(reversed(spec['runs']))
                spec.pop('experiment_hash')
                spec['experiment_hash']=fingerprint(spec)
            job=store.submit(spec,f'behavior-acceptance-{repeat}')
            run_pending(store.root,wait_for_lock=True)
            state=store.get_job(job)
            assert state['status']=='COMPLETED',state
            report['jobs'].append(job)
            for row in spec['runs']:
                done=state['completed'][row['variant_id']]
                path=Path(done['output_dir'])
                cfg=config_from_run_spec(row)
                audit=audit_run(path,cfg,sample)
                traces=list(read_rows(path/'decision_traces.jsonl.gz'))
                assert traces,'new runs require decision trace'
                trace_ids={t['decision_id'] for t in traces}
                assert len(trace_ids)==len(traces)
                for fill in read_rows(path/'fills.jsonl.gz'):
                    assert fill.get('decision_id') in trace_ids or dict(fill.get('decision',[])).get('decision_id') in trace_ids
                if row['research_label']=='E0':
                    assert not [r for r in read_rows(path/'orders.jsonl.gz') if r.get('event')=='submit']
                    assert Decimal(str(done['summary']['net_pnl']))==0
                if repeat:assert expected[row['research_label']]==done['deterministic_digest']
                else:expected[row['research_label']]=done['deterministic_digest']
                explanation=explain_run(path)
                assert explanation['identity']['reconciled'], explanation['identity']
                manifest=json.loads((path/'run_manifest.json').read_text())
                performance=manifest['performance']
                assert performance['wall_seconds']<60,performance
                assert performance['process_peak_rss_mib']<512,performance
                report['runs'].append({'label':row['research_label'],'repeat':repeat,'output_dir':str(path),
                    'digest':done['deterministic_digest'],'trace_count':len(traces),'audit':audit,
                    'code_version':manifest['code_version'],'performance':performance,
                    'explanation':explanation,'terminal_reference':reference_from_run(path,sample)})
                print(f'{repeat+1}: {row["research_label"]} source/ledger/trace PASS',flush=True)
        first={r['label']:r for r in report['runs'] if r['repeat']==0}
        report['behavior_comparisons']={key:compare_runs(first['E1']['output_dir'],first[key]['output_dir']) for key in ('E0','E2','E3','E4')}
        for comparison in report['behavior_comparisons'].values():
            assert comparison['guard']['comparable'],comparison['guard']
            assert comparison['pnl_delta_identity']['reconciled']
        # At one lot there cannot be a sub-lot partial fill. Equality is evidence,
        # not a reason to silently alter the baseline experiment.
        report['one_lot_policy_note']='E1/E2 may have identical behavior because fills are integer lots.'
        partial_draft=replace(draft,config=variant(config,strategy='join_bbo',size='0.003',partial_fill_policy='keep_remaining'),
            name='3 lot 부분 체결 정책 확인',hypothesis='부분 체결이 가능한 3 lot에서 잔량 유지와 재설정은 다른 주문 행동을 만드는가?')
        partial_spec=build_experiment(partial_draft,'strategy_compare','partial_fill_policy',
            ['keep_remaining','reset_target'],sample_path=sample)
        partial_job=store.submit(partial_spec,'three-lot-policy-acceptance')
        run_pending(store.root,wait_for_lock=True)
        partial_state=store.get_job(partial_job)
        assert partial_state['status']=='COMPLETED',partial_state
        partial_paths=[]
        for row in partial_spec['runs']:
            path=Path(partial_state['completed'][row['variant_id']]['output_dir'])
            audit_run(path,config_from_run_spec(row),sample)
            assert explain_run(path)['identity']['reconciled']
            partial_paths.append(path)
        report['partial_policy_experiment']={'job_id':partial_job,'paths':[str(p) for p in partial_paths],
            'comparison':compare_runs(*partial_paths)}
        assert report['partial_policy_experiment']['comparison']['first_action_divergence'] is not None
        cloned=clone_for_next(first['E1']['output_dir'],'부분 체결 후 재설정만 바꾸면 어떤 차이가 생기는가?',{'partial_fill_policy':'reset_target'})
        assert cloned.parent_run_id==Path(first['E1']['output_dir']).name
        save_conclusion(store,cloned.parent_run_id,'needs_more_data','행동 차이를 확인했고 손익 기여는 표본 내 설명이다.',cloned.hypothesis,{'partial_fill_policy':'reset_target'})
        report['status']='PASS'
        report['determinism']='PASS'
        report['collector']=subprocess.check_output(['systemctl','is-active','lobcap@btcusdt.service'],text=True).strip()
        assert report['collector']=='active'
    except BaseException as exc:
        report['status']='FAIL';report['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:write_json(args.output,report)


if __name__=='__main__':main()
