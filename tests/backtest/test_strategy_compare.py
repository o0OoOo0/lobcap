from decimal import Decimal as D
from dataclasses import replace
import shutil
from copy import deepcopy

import pytest
from test_runner import sample

from lobcap.backtest.research_flow.experiments import build_experiment
from lobcap.backtest.research_flow.strategy_compare import build_scorecard, loss_driver
from lobcap.backtest.workspace.contracts import StrategyDraft
from lobcap.config.backtest import variant


def score_row(run_id='a',pnl='1',strategy='fixed_spread'):
    from lobcap.config.backtest import load_backtest_config
    from lobcap.config.experiment import ROOT
    config=load_backtest_config(ROOT/'configs/backtest/binance_demo.yaml').resolved()
    config['strategy']=strategy
    return {'rankable':True,'output_dir':run_id,'run_id':run_id,'sample_sha256':'same',
        'execution_contract_version':config['execution_contract_version'],'config':config,
        'manifest':{'code_version':'a'*40},'summary':{'net_pnl':pnl,'gross_pnl':pnl,
        'fees':'0','spread_capture':pnl,'inventory_mtm':'0','max_drawdown':'1',
        'max_abs_inventory':'.01','ending_inventory':'0','execution':{'activated':2,'orders_with_fill':1}}}


def reviewed(pnl='1'):
    return {'available':True,'reconciled':True,'fill_events':2,'turnover':D(100),
            'net_pnl':D(pnl),'gross_pnl':D(pnl),'fees':D(0),'break_even_fee_bps':D('.2'),
            'spread_capture':D(pnl),'inventory_mtm':D(0),'max_drawdown':D(1),
            'max_abs_inventory':D('.01'),'ending_inventory':D(0)}


def test_strategy_suite_freezes_scenario_and_aligns_glft_start(sample):
    config, path = sample
    draft = StrategyDraft(config, "standard suite", "Which policy changes order behavior?")
    spec = build_experiment(draft, "strategy_suite", values=["fixed_spread", "glft", "no_trade"],
                            sample_path=path)
    assert [row["settings"]["strategy"] for row in spec["runs"]] == ["fixed_spread", "glft", "no_trade"]
    assert {row["settings"]["quote_start_seconds"] for row in spec["runs"]} == {config.glft_min_seconds}
    assert len({row["scenario_hash"] for row in spec["runs"]}) == 1
    assert spec["research_context"]["paper_eligibility"] == "INSUFFICIENT_EVIDENCE"


def test_strategy_suite_rejects_duplicates_and_unknowns(sample):
    config, path = sample
    draft = StrategyDraft(config, "suite", "question")
    for values in (["fixed_spread", "fixed_spread"], ["fixed_spread", "oracle"]):
        with pytest.raises(ValueError):
            build_experiment(draft, "strategy_suite", values=values, sample_path=path)


def test_loss_driver_separates_cost_and_inventory():
    reviewed = {"fill_events": 2}
    assert loss_driver({"net_pnl":"-1","gross_pnl":"1","fees":"2"}, reviewed) == "FEES"
    assert loss_driver({"net_pnl":"-3","gross_pnl":"-2","fees":"1",
                        "spread_capture":"1","inventory_mtm":"-3"}, reviewed) == "INVENTORY_MTM"
    assert loss_driver({"net_pnl":"0","gross_pnl":"0"}, {"fill_events":0}) == "NO_EXECUTION"


def test_scorecard_requires_same_sample_and_scenario():
    base = {"rankable":True,"summary":{"net_pnl":"1"},"output_dir":"x","run_id":"a",
            "execution_contract_version":"v2",
            "sample_sha256":"one","config":{"strategy":"fixed_spread"}}
    with pytest.raises(ValueError, match="identical sample"):
        build_scorecard([base, {**base,"run_id":"b","sample_sha256":"two"}])


def test_scorecard_ranks_executed_policies_and_keeps_no_trade_as_control(monkeypatch):
    import lobcap.backtest.research_flow.strategy_compare as module
    monkeypatch.setattr(module, "reviewed_metrics", lambda path: {
        **reviewed({'a':'1','b':'2','c':'0'}[path]),
        'max_drawdown':D({'a':'2','b':'1','c':'0'}[path]),
        'max_abs_inventory':D({'a':'.01','b':'.005','c':'0'}[path])})
    def row(run_id, strategy, pnl, drawdown, inventory, fills=1):
        return {**score_row(run_id,pnl,strategy),"summary":{"net_pnl":pnl,"gross_pnl":pnl,
            "fees":"0","spread_capture":pnl,"inventory_mtm":"0","max_drawdown":drawdown,
            "max_abs_inventory":inventory,"ending_inventory":"0","execution":{
            "orders_with_fill":fills,"activated":2},"markouts":{"1000":{"coverage":1}}}}
    card=build_scorecard([row("a","fixed_spread","1","2",".01"),
                          row("b","inventory_skew","2","1",".005"),
                          row("c","no_trade","0","0","0",0)])
    assert card[0]["strategy"]=="inventory_skew" and card[0]["development_rank"]==1
    control=next(item for item in card if item["control"])
    assert control["development_rank"] is None and control["paper_eligibility"]=="INSUFFICIENT_EVIDENCE"


def test_workbench_previews_standard_strategy_suite_without_submitting(sample):
    pytest.importorskip("streamlit")
    from lobcap.backtest.workspace.store import WorkspaceStore
    from test_workbench import app_for, widget
    config,path=sample
    target=config.output_path/'samples'/config.sample.sample_id
    target.parent.mkdir(parents=True);shutil.copytree(path,target)
    app=app_for(config).run(timeout=30)
    widget(app.selectbox,'실행 종류').set_value('strategy_suite').run(timeout=30)
    widget(app.button,'Simulate · 실행 설정 확인').click().run(timeout=30)
    assert not app.exception and not app.error
    assert any('확인할 실행 6개' in item.value for item in app.success)
    assert not WorkspaceStore(config.output_path/'workspace').list_jobs()


def test_strategy_suite_runs_isolated_policies_and_builds_scorecard(sample):
    from lobcap.backtest.research_flow.catalog import index_runs
    from lobcap.backtest.workspace.store import WorkspaceStore
    from lobcap.backtest.workspace.worker import run_pending
    config,path=sample
    store=WorkspaceStore(config.output_path/'workspace')
    draft=StrategyDraft(variant(config,stale_book_ms=100000),"three policies","Compare policy behavior")
    spec=build_experiment(draft,"strategy_suite",
        values=["fixed_spread","inventory_skew","no_trade"],sample_path=path)
    job_id=store.submit(spec,"strategy-suite-test")
    run_pending(store.root)
    state=store.get_job(job_id)
    assert state["status"]=="COMPLETED" and len(state["completed"])==3
    rows=[row for row in index_runs(config.output_path,store.root) if job_id in row.get("job_ids",[])]
    if any('dirty' in row['manifest']['code_version'] for row in rows):
        with pytest.raises(ValueError,match='clean code'):
            build_scorecard(rows)
        return
    scorecard=build_scorecard(rows)
    assert len(scorecard)==3 and len({row["run_id"] for row in scorecard})==3
    assert next(row for row in scorecard if row["strategy"]=="no_trade")["control"]


@pytest.mark.parametrize('field,value', [('initial_cash','9'),('max_inventory','.02'),
    ('quote_start_seconds',60),('entry_ms',100)])
def test_scorecard_enforces_existing_comparison_contract(field,value):
    a=score_row();b=deepcopy(a);b['run_id']='b';b['config'][field]=value
    with pytest.raises(ValueError):build_scorecard([a,b])


@pytest.mark.parametrize('version',['unknown','a'*40+'+dirty','b'*40])
def test_scorecard_rejects_unverified_or_different_code(version):
    a=score_row();b=deepcopy(a);b['run_id']='b';b['manifest']['code_version']=version
    with pytest.raises(ValueError):build_scorecard([a,b])


def test_scorecard_cannot_rank_summary_that_disagrees_with_ledger(monkeypatch):
    monkeypatch.setattr('lobcap.backtest.research_flow.strategy_compare.reviewed_metrics',lambda path:reviewed())
    row=score_row(pnl='999')
    result=build_scorecard([row])[0]
    assert not result['reconciled'] and result['development_rank'] is None


def test_missing_or_nonfinite_metrics_are_not_zero(monkeypatch):
    monkeypatch.setattr('lobcap.backtest.research_flow.strategy_compare.reviewed_metrics',lambda path:reviewed())
    for value in (None,'NaN','Infinity'):
        row=score_row();row['summary']['max_drawdown']=value
        with pytest.raises(ValueError):build_scorecard([row])


def test_identical_scores_have_same_rank(monkeypatch):
    monkeypatch.setattr('lobcap.backtest.research_flow.strategy_compare.reviewed_metrics',lambda path:reviewed())
    a=score_row();b=score_row('b')
    assert [row['development_rank'] for row in build_scorecard([b,a])]==[1,1]


def test_sensitivity_ui_does_not_attempt_strategy_ranking(monkeypatch):
    pytest.importorskip('streamlit')
    from streamlit.testing.v1 import AppTest
    def forbidden(*args,**kwargs):
        raise AssertionError('sensitivity is not a strategy competition')
    monkeypatch.setattr('lobcap.backtest.research_flow.strategy_compare.build_scorecard',forbidden)
    monkeypatch.setattr('lobcap.backtest.research_flow.workbench.compare_runs',
        lambda *args,**kwargs:{'guard':{'comparable':False,'reasons':['select sensitivity']}})
    rows=[score_row(),score_row('b')]
    for row in rows:
        row.update(job_ids=['j'],research_context={'kind':'assumption_sensitivity'},name='test',status='PASS')
    rows[1]['config']['maker_bps']='1'
    script='from lobcap.backtest.research_flow.workbench import render_comparison\n'
    script+=f'rows={rows!r}\nrender_comparison(rows,rows[0],{{"job_id":"j"}})'
    app=AppTest.from_string(script).run(timeout=30)
    assert not app.exception and not app.error
