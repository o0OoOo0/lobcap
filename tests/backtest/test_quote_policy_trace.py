from dataclasses import replace
from decimal import Decimal as D

import pytest

from lobcap.backtest.artifacts import MemorySink
from lobcap.backtest.engine import run
from lobcap.backtest.quote_policy import plan_actions
from lobcap.backtest.strategies import StrategyParameters, StrategyPolicy, Features
from lobcap.backtest.types import DesiredQuotes, Quote, PortfolioView
from lobcap.config.backtest import variant, ConfigError
from test_engine import config, initial, trade, depth
from test_execution import market


def known():
    return {'buy':{'order_id':'order-00000001','side':'buy','price':'100','quantity':'.002',
        'remaining':'.001','submitted_ns':0,'activation_ns':0,'status':'ACTIVE'}}


def test_keep_partial_and_reset_target_are_explicit_and_shrink_overrides_hold():
    c=config(partial_fill_policy='keep_remaining',min_hold_ms=1000,reprice_threshold_ticks='10')
    target=DesiredQuotes(Quote('buy',D(100),D('.002')),None)
    assert plan_actions(c,target,known(),1)[0]['reason']=='keep_partial'
    assert plan_actions(variant(c,partial_fill_policy='reset_target'),target,known(),1)[0]['action']=='cancel'
    shrunk=DesiredQuotes(Quote('buy',D(100),D('.001')),None)
    assert plan_actions(c,shrunk,known(),1)[0]['reason']=='target_size_changed'
    increased=DesiredQuotes(Quote('buy',D(100),D('.003')),None)
    assert plan_actions(c,increased,known(),1)[0]['reason']=='target_size_changed'
    stopped=DesiredQuotes(None,None,'stale_book')
    assert plan_actions(c,stopped,known(),1)[0]['action']=='cancel'


def test_threshold_and_hold_do_not_override_retained_order_hard_risk():
    c=config(partial_fill_policy='keep_remaining',min_hold_ms=1000,reprice_threshold_ticks='2')
    target=DesiredQuotes(Quote('buy',D('99.9'),D('.002')),None)
    assert plan_actions(c,target,known(),1)[0]['reason']=='below_reprice_threshold'
    target=DesiredQuotes(Quote('buy',D('99.8'),D('.002')),None)
    assert plan_actions(c,target,known(),1)[0]['reason']=='minimum_hold'
    assert plan_actions(c,target,known(),1_000_000_000)[0]['reason']=='replace'
    unsafe=replace(market(1),bid=D(99),ask=D(100))
    assert plan_actions(c,target,known(),1,unsafe)[0]['reason']=='retained_order_risk'


def test_inventory_step_and_flow_hysteresis_are_observable_and_run_local():
    c=config(strategy='inventory_size')
    params=StrategyParameters.from_config(c)
    policy=StrategyPolicy()
    for inventory,side in ((D('.006'),'bid'),(D('-.006'),'ask')):
        q=policy.decide(params,market(0),PortfolioView(D(1000),inventory,D(1000)))
        assert getattr(q,side) is None
    params=StrategyParameters.from_config(variant(c,strategy='flow_withdraw'))
    view=PortfolioView(D(1000),D(0),D(1000))
    for flow,withdraw in (('.6',True),('.4',True),('.3',True),('.29',False)):
        q=policy.decide(params,replace(market(0),trade_imbalance=D(flow),trade_volume=D(1)),view)
        assert (q.ask is None)==withdraw
    fresh=StrategyPolicy()
    assert fresh.decide(params,replace(market(0),trade_imbalance=D('.4'),trade_volume=D(1)),view).ask


@pytest.mark.parametrize('clock',['exchange','recorded'])
def test_no_trade_zero_inventory_and_trace_source_visibility(clock):
    c=config(strategy='no_trade',clock_mode=clock)
    source=replace(depth(1,100_000_000),receive_ns=600_000_000)
    sink=MemorySink()
    result=run(c,initial(),[source],sink,bootstrap_receive_ns=0)
    assert result.summary['net_pnl']==0 and not sink.rows.get('fills')
    traces=sink.rows['decision_traces']
    assert len({r['decision_id'] for r in traces})==len(traces)
    first=next(r for r in traces if r['time_ns']==250_000_000)
    assert first['replay_index']==(None if clock=='recorded' else 1)
    assert 'queue' not in str(first['observable_portfolio'])


@pytest.mark.parametrize('clock',['exchange','recorded'])
def test_partial_keep_reduces_cancel_behavior_and_links_decision(clock):
    c=config(strategy='join_bbo',size='.002',alpha=None,clock_mode=clock,stale_book_ms=10000)
    results=[]
    for policy in ('reset_target','keep_remaining'):
        sink=MemorySink()
        run(variant(c,partial_fill_policy=policy),initial(),[trade(1,100_000_000)],sink,bootstrap_receive_ns=0)
        trace=next(r for r in sink.rows['decision_traces'] if r['time_ns']==250_000_000)
        results.append(trace['actions'][0]['action'])
        fill=sink.rows['fills'][0]
        assert fill['decision_id']==sink.rows['decision_traces'][0]['decision_id']
    assert results==['cancel','retain']


def test_s3_flow_uses_delivery_window_without_changing_legacy_flow():
    f=Features();f.observe_trade(trade(1,0,side='sell'),2_000_000_000)
    assert f.flow(2_000_000_000)==(None,D(0))
    assert f.flow(2_000_000_000,include_late=True)==(D(1),D('.001'))
    assert f.flow(3_000_000_000,include_late=True)==(None,D(0))


def test_trace_preserves_unrounded_signal_and_policy_clamp():
    c=config(strategy='microprice',signal_strength='1',half_spread_ticks='.5')
    params=StrategyParameters.from_config(c)
    m=replace(market(0),microprice=D('100.83'))
    trace={}
    StrategyPolicy().decide(params,m,PortfolioView(D(1000),D(0),D(1000)),trace)
    assert trace['raw_target'].bid.price==D('100.78')
    assert trace['tick_rounded_target'].bid.price==D('100.7')
    assert trace['policy_limited_target'].bid.price==D(100)


def test_trace_stage_changes_ignore_reason_and_decimal_formatting_only():
    from lobcap.backtest.quote_policy import make_trace
    before=DesiredQuotes(Quote('buy',D('100'),D('.001')),None,'strategy_reason')
    after=DesiredQuotes(Quote('buy',D('100.00'),D('.0010')),None,'risk_reason')
    trace=make_trace(config(),1,market(0),PortfolioView(D(1000),D(0),D(1000)),
        dict(raw_target=before,tick_rounded_target=after,policy_limited_target=before),after,[])
    assert trace['stage_changes']==dict(tick_rounding=False,price_policy=False,risk=False)


def test_saved_config_document_roundtrip_excludes_derived_versions():
    from lobcap.backtest.runner import config_document
    from lobcap.backtest.workspace.contracts import config_from_document
    c=config(strategy='flow_withdraw',partial_fill_policy='keep_remaining',reprice_threshold_ticks='2',min_hold_ms=30)
    doc=config_document(c)
    assert not {'quote_policy_version','decision_trace_version','execution_contract_version'} & set(doc['settings'])
    restored=config_from_document(doc)
    assert restored.resolved()==c.resolved()


def test_stateful_flow_future_suffix_does_not_change_earlier_traces():
    c=config(strategy='flow_withdraw',flow_min_volume='.001',clock_mode='recorded',stale_book_ms=10000)
    first=trade(1,100_000_000,side='sell',size='.01')
    last=trade(2,1_100_000_000,side='buy',size='.01')
    results=[]
    for suffix in (last,replace(last,trade=replace(last.trade,maker_side='sell'))):
        sink=MemorySink();run(c,initial(),[first,suffix],sink,bootstrap_receive_ns=0)
        results.append([row for row in sink.rows['decision_traces'] if row['time_ns']<last.time_ns])
    assert results[0]==results[1]


@pytest.mark.parametrize('change',[{'partial_fill_policy':'auto'},{'reprice_threshold_ticks':'-.1'},{'min_hold_ms':-1},{'min_hold_ms':True}])
def test_invalid_policy_settings_fail(change):
    with pytest.raises(ConfigError):variant(config(),**change)
