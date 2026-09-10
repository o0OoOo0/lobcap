from dataclasses import replace
import json
import pytest
from test_runner import sample
from lobcap.backtest.workspace.contracts import StrategyDraft
from lobcap.backtest.workspace.store import WorkspaceStore
from lobcap.backtest.workspace.jobs import verify_spec
from lobcap.backtest.research_flow.experiments import build_experiment,save_conclusion,save_context,load_context,load_context_draft


def test_intent_is_frozen_and_assumptions_cannot_be_strategy_axes(sample):
    config,path=sample
    draft=StrategyDraft(config,'research','수량 변경이 재고를 줄이는가?')
    spec=build_experiment(draft,sample_path=path)
    verify_spec(spec)
    assert spec['research_context']['fees_verified'] is False
    spec['research_context']['question']='tampered'
    with pytest.raises(ValueError,match='checksum'):verify_spec(spec)
    for field in ('maker_bps','alpha','initial_cash','max_inventory'):
        with pytest.raises(ValueError):build_experiment(draft,'strategy_compare',field,['1','2'],sample_path=path)
    with pytest.raises(ValueError):build_experiment(draft,'assumption_sensitivity','half_spread_ticks',['1','2'],sample_path=path)


def test_suite_has_five_explicit_policies_and_same_costs(sample):
    config,path=sample
    spec=build_experiment(StrategyDraft(config,'suite','행동의 차이가 무엇인가?'),'baseline_suite',sample_path=path)
    verify_spec(spec)
    assert [r['research_label'] for r in spec['runs']]==['E0','E1','E2','E3','E4']
    assert len({r['scenario_hash'] for r in spec['runs']})==1
    e1,e2=spec['runs'][1:3]
    diffs={k for k in e1['settings'] if e1['settings'][k]!=e2['settings'][k]}
    assert diffs=={'partial_fill_policy'}


def test_no_candidate_is_valid_and_context_survives_new_store(tmp_path):
    store=WorkspaceStore(tmp_path/'workspace')
    save_context(store,{'route':'결과','run_id':'run1'})
    save_context(store,{'baseline_id':'base'})
    assert load_context(WorkspaceStore(store.root))=={'route':'결과','run_id':'run1','baseline_id':'base'}
    row=save_conclusion(store,'run1','no_candidate','재고 MTM 손실이 가장 컸다.','잔량 유지가 영향을 주는가?',{'partial_fill_policy':'keep_remaining'})
    assert row['outcome']=='no_candidate'
    assert row['paper_eligibility']=='INSUFFICIENT_EVIDENCE'
    assert len(list((store.root/'conclusions/run1').glob('*.json')))==1


def test_refresh_restores_selected_revision_not_latest(sample,tmp_path):
    config,_=sample
    store=WorkspaceStore(tmp_path/'store')
    first=store.save_draft(StrategyDraft(config,'first','question'))
    save_context(store,{'draft_id':first.draft_id,'draft_revision':first.revision})
    store.save_draft(replace(first,name='second'))
    assert load_context_draft(WorkspaceStore(store.root)).name=='first'
