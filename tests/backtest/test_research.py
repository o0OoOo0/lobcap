from dataclasses import replace
from decimal import Decimal as D
import json

import pytest

from lobcap.backtest.artifacts import MemorySink
from lobcap.backtest.engine import MarketBook, run
from lobcap.backtest.research import make_plan, evaluate, run_research, summarize_run, fingerprint, render_report
from lobcap.backtest.research_execution import ResearchControl, ResearchPolicy
from lobcap.backtest.types import PortfolioView, DesiredQuotes, Quote, NS
from lobcap.config.backtest import load_backtest_config, variant
from lobcap.config.experiment import ROOT
from test_engine import initial, trade, depth, config
from test_runner import sample


def base():return load_backtest_config(ROOT/'configs/backtest/binance_recorded.yaml')


def test_plan_freezes_research_settings_and_rejects_fake_oos():
    p=make_plan(variant(base(),maker_bps='99',initial_cash='9999'),['microprice','fixed_spread'])
    assert p['base_config']['settings']['maker_bps']=='2'
    assert p['base_config']['settings']['initial_cash']=='1000'
    assert p['strategies']==['fixed_spread','microprice']
    assert fingerprint(p)==fingerprint(make_plan(base(),['fixed_spread','microprice']))
    with pytest.raises(ValueError):make_plan(base(),['fixed_spread']*2)
    with pytest.raises(ValueError):make_plan(replace(base(),sample=replace(base().sample,end_ns=base().sample.end_ns+NS)))
    with pytest.raises(ValueError):make_plan(base(),hypothesis=' ')


def test_profitable_fake_results_still_cannot_promote_and_touch_cannot_rescue():
    p=make_plan(base(),['fixed_spread'])
    rows=[{'strategy':'fixed_spread','scenario':s,'status':'PASS','metrics':{
        'maker_fills':100,'net_pnl':'1','max_drawdown':'0','kill_reason':None}} for s in p['scenarios']]
    r=evaluate(rows,p)
    assert r['paper_ready'] is False and r['paper_candidates']==[]
    assert r['exploratory_ranking'][0]['exploratory_status']=='PASS'
    next(r for r in rows if r['scenario']=='queue_zero')['metrics']['net_pnl']='-1'
    next(r for r in rows if r['scenario']=='touch_diagnostic')['metrics']['net_pnl']='100000'
    assert evaluate(rows,p)['exploratory_ranking'][0]['exploratory_status']=='FAIL'
    assert 'incomplete_or_failed_scenarios' in evaluate(rows[:-1],p)['exploratory_ranking'][0]['reasons']


def test_policy_validation():
    for changes in ({'loss_limit':D(0)},{'max_gross_fraction':D('1.1')},{'hedge_target':D('.004')},{'hedge_ms':True}):
        with pytest.raises(ValueError):replace(ResearchPolicy(),**changes).validate()


def test_research_lot_rounding_does_not_change_legacy_queue_arithmetic():
    from lobcap.backtest.execution import Execution
    from test_execution import market
    c=config(alpha='0',stale_book_ms=10000)
    book=MarketBook(initial())
    for strict,expected in ((False,D('.0005')),(True,D(0))):
        engine=Execution(c,MemorySink());engine.enforce_lots=strict
        engine.submit(Quote('buy',D(100),D('.001')),0,{})
        engine.advance(0,market(0),book)
        engine.orders['buy'].queue=D('.0005')
        fills=engine.on_event(trade(1,1,size='.001'))
        assert sum((f.quantity for f in fills),D(0))==expected


def test_hedge_waits_for_terminal_and_executes_depth_with_delay_and_no_reuse():
    c=config(taker_bps='5',stale_book_ms=10000)
    p=ResearchPolicy(hedge_ms=20,hedge_price_guard_bps=D(100))
    sink=MemorySink();control=ResearchControl(c,p,sink);book=MarketBook(initial())
    event=depth(1,1);book.apply(event);control.on_market(event)
    orders={'buy':object()};cancels=[]
    view=PortfolioView(D(1000),D('.004'),D(1000))
    control.review(2,book.view(2),view,lambda *a:cancels.append(a),orders)
    assert cancels and control.pending is None
    control.review(3,book.view(3),view,lambda *a:None,{})
    assert control.execute(20_000_002,book,D('.004'))==[]
    fills=control.execute(20_000_003,book,D('.004'))
    assert sum(f.quantity for f in fills)==D('.002')
    assert all(f.role=='taker' and f.price==D(100) and f.side=='sell' for f in fills)
    control.consumed[('buy',D(100))]=D(1)
    control.pending=(30_000_000,'sell',D('.002'),D('100.5'),2)
    assert control.execute(30_000_000,book,D('.004'))==[]
    control.pending=(40_000_000,'sell',D('.002'),D('100.5'),3)
    assert control.execute(40_000_000,book,D('-.004'))==[]


def test_hedge_price_guard_and_stale_reject():
    c=config();book=MarketBook(initial());sink=MemorySink()
    control=ResearchControl(c,ResearchPolicy(),sink)
    control.last_depth_refs=('depth:1',)
    control.pending=(1,'buy',D('.002'),D(90),1)
    assert control.execute(1,book,D('-.004'))==[]
    control.pending=(NS,'buy',D('.002'),D(101),2)
    assert control.execute(NS,book,D('-.004'))==[]
    assert control.skipped_hedges==1


def test_kill_is_latched_and_cancels_exposed_orders():
    c=config();sink=MemorySink();ctrl=ResearchControl(c,ResearchPolicy(),sink);book=MarketBook(initial())
    ctrl.review(0,book.view(0),PortfolioView(D(1000),D(0),D(1000)),lambda *a:None,{})
    canceled=[]
    ctrl.review(1,book.view(1),PortfolioView(D(980),D(0),D(980)),lambda *a:canceled.append(a),{'buy':object()})
    assert ctrl.kill_reason=='sample_loss_limit' and canceled
    good=PortfolioView(D(1100),D(0),D(1100))
    ctrl.review(2,book.view(2),good,lambda *a:None,{})
    assert ctrl.quotes(2,book.view(2),good,DesiredQuotes(Quote('buy',D(100),D('.001')),None)).bid is None


def test_engine_hedge_and_taker_fee_reconcile_with_unreported_position():
    c=config(alpha=None,initial_inventory='.004',initial_cash='1000',taker_bps='5',report_ms=10,stale_book_ms=10000)
    policy=replace(ResearchPolicy(),hedge_price_guard_bps=D(100))
    sink=MemorySink()
    result=run(c,initial(),[depth(1,1),depth(2,NS)],sink,strategy=lambda m,p:DesiredQuotes(None,None),research_policy=policy)
    fills=sink.rows['fills']
    assert fills and all(f['role']=='taker' for f in fills)
    assert D(result.summary['ending_inventory'])==D('.001')
    assert D(result.summary['fees'])==D('.003')*D(100)*D(5)/10000
    assert result.summary['research_execution']['hedge_count']==2
    assert D(result.summary['gross_pnl'])==D(result.summary['spread_capture'])+D(result.summary['inventory_mtm'])


def test_strategy_exception_kills_in_research_and_old_mode_still_raises():
    c=config(stale_book_ms=10000)
    def broken(*args):raise RuntimeError('fixture failure')
    with pytest.raises(RuntimeError):run(c,initial(),[],MemorySink(),strategy=broken)
    sink=MemorySink();r=run(c,initial(),[],sink,strategy=broken,research_policy=ResearchPolicy())
    assert r.summary['research_execution']['kill_reason']=='strategy_exception:RuntimeError'


def test_html_escapes_untrusted_hypothesis():
    p=make_plan(base(),hypothesis='<script>alert(1)</script>')
    report=render_report(p,evaluate([],p),[])
    assert '<script>' not in report and '&lt;script&gt;' in report


def test_research_resume_integrity_and_fresh_repeat(sample,tmp_path,monkeypatch):
    # Use the real IO/engine but a prepared synthetic arrival sample.
    from lobcap.backtest.replay import write_json,sample_fingerprint
    from lobcap.config.common import sha256_file
    c,path=sample
    c=replace(c,sample=replace(c.sample,normalization_version='binance-net-depletion-arrival-v2'))
    bootstrap=c.sample.start_ns-30*NS
    write_json(path/'validation.json',{'status':'PASS','synthetic':True,'bootstrap_receive_ns':bootstrap})
    files={n:sha256_file(path/n) for n in ('initial_book.json','events.jsonl.gz','validation.json')}
    write_json(path/'sample_manifest.json',{'status':'PASS','sample':c.sample,'instrument':c.instrument,'files':files,
        'bootstrap_receive_ns':bootstrap,'sample_sha256':sample_fingerprint(files,c.sample)})
    first=run_research(c,path,['fixed_spread'])
    a=json.loads((first/'state.json').read_text())
    assert a['status']=='PASS' and a['completed']==6
    assert json.loads((first/'handoff.json').read_text())['paper_ready'] is False
    import lobcap.backtest.research as research
    original=research._run_one
    def forbidden(*args):raise AssertionError('resume must reuse verified complete evidence')
    monkeypatch.setattr(research,'_run_one',forbidden)
    run_research(c,path,['fixed_spread'],resume=first)
    assert json.loads((first/'state.json').read_text())['deterministic_digest']==a['deterministic_digest']
    monkeypatch.setattr(research,'_run_one',original)
    second=run_research(c,path,['fixed_spread'])
    assert json.loads((second/'state.json').read_text())['deterministic_digest']==a['deterministic_digest']
    with pytest.raises(ValueError,match='identical'):run_research(c,path,['microprice'],resume=first)
    output=first/a['rows'][0]['run_path'];(output/'summary.json').write_text('{}')
    with pytest.raises(ValueError,match='evidence changed'):run_research(c,path,['fixed_spread'],resume=first)
