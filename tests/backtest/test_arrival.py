from dataclasses import replace
from decimal import Decimal as D

import pytest

from lobcap.backtest.artifacts import MemorySink
from lobcap.backtest.engine import run
from lobcap.backtest.types import DesiredQuotes,Quote,NS
from lobcap.config.backtest import variant,ConfigError
from test_engine import config,initial,trade,depth


def recorded(**changes):
    return variant(config(stale_book_ms=10000),clock_mode="recorded",**changes)


def test_delayed_market_delivery_hides_future_book_and_preserves_prefix():
    seen=[]
    def strategy(m,p):
        seen.append((m.time_ns,m.bid))
        return DesiredQuotes(None,None)
    event=replace(depth(1,100_000_000,bid="99",ask="100"),receive_ns=600_000_000)
    c=recorded()
    a=MemorySink();b=MemorySink()
    run(c,initial(),[event],a,strategy,bootstrap_receive_ns=0)
    assert dict(seen)[500_000_000]==100 and dict(seen)[750_000_000]==99
    suffix=replace(depth(1,100_000_000,bid="102",ask="103"),receive_ns=600_000_000)
    run(c,initial(),[suffix],b,strategy,bootstrap_receive_ns=0)
    assert [r for r in a.rows['decisions'] if r['time_ns']<600_000_000]==[r for r in b.rows['decisions'] if r['time_ns']<600_000_000]


def test_delayed_fill_notification_reserves_unknown_inventory_and_serial_slot():
    seen=[]
    def strategy(m,p):
        seen.append((m.time_ns,p.inventory,p.buy_exposure))
        return DesiredQuotes(Quote('buy',D(100),D('.001')),None)
    c=recorded(alpha=None,report_ms=400,decision_ms=100,max_inventory='.001')
    sink=MemorySink()
    result=run(c,initial(),[replace(trade(1,100_000_000),receive_ns=800_000_000)],sink,strategy,bootstrap_receive_ns=0)
    assert next(row for row in seen if row[0]==400_000_000)[1:]==(D(0),D('.001'))
    assert next(row for row in seen if row[0]==500_000_000)[1:]==(D('.001'),D(0))
    assert result.summary['max_unreported_inventory']==D('.001')
    assert result.summary['execution']['submitted']==1
    assert sink.rows['fills'][0]['time_ns']==100_000_000
    received=next(row for row in sink.rows['timeline'] if row['event']=='fill_received')
    assert received['time_ns']==500_000_000


def test_negative_receipt_delay_and_missing_bootstrap_fail():
    c=recorded()
    with pytest.raises(ValueError,match='bootstrap'):
        run(c,initial(),[],MemorySink())
    with pytest.raises(ValueError,match='negative capture'):
        run(c,initial(),[replace(trade(1,10),receive_ns=9)],MemorySink(),bootstrap_receive_ns=0)


def test_receive_order_regression_is_held_in_sequence():
    c=recorded()
    first=replace(depth(1,100_000_000,bid='99',ask='100'),receive_ns=500_000_000)
    second=replace(depth(2,200_000_000,bid='98',ask='99',oldbid='99',oldask='100'),receive_ns=400_000_000)
    sink=MemorySink();result=run(c,initial(),[first,second],sink,lambda m,p:DesiredQuotes(None,None),bootstrap_receive_ns=0)
    assert result.summary['clock_diagnostics']['sequence_delivery_hold_ns']==100_000_000
    assert next(r for r in sink.rows['decisions'] if r['time_ns']==500_000_000)['bid']=='98'


def test_participation_cap_and_end_unreported_fill():
    c=recorded(alpha=None,max_trade_participation='.1',report_ms=1000,decision_ms=1000,max_age_ms=10000)
    sink=MemorySink()
    result=run(c,initial(),[trade(1,5_900_000_000,size='.015')],sink,bootstrap_receive_ns=0)
    assert D(sink.rows['fills'][0]['quantity'])==D('.001')
    assert result.summary['ending_known_inventory']==0
    assert result.summary['ending_inventory']==D('.001')
    assert result.summary['pending_reports']>0


def test_recorded_repeat_digest_and_baseline_unchanged():
    events=[trade(1,100_000_000),depth(2,NS)]
    a=run(recorded(alpha=None),initial(),events,MemorySink(),bootstrap_receive_ns=0)
    b=run(recorded(alpha=None),initial(),events,MemorySink(),bootstrap_receive_ns=0)
    assert a.digest==b.digest
    legacy=run(config(alpha=None),initial(),events,MemorySink())
    assert legacy.summary['net_pnl']==a.summary['net_pnl']


def test_order_age_is_a_client_action_after_activation_is_known():
    c=recorded(alpha=None,report_ms=1000,max_age_ms=100)
    sink=MemorySink()
    run(c,initial(),[trade(1,500_000_000)],sink,bootstrap_receive_ns=0)
    assert sink.rows['fills'][0]['time_ns']==500_000_000
    cancels=[r for r in sink.rows['timeline'] if r['actor']=='client' and r['event']=='cancel_send']
    assert not cancels or min(r['time_ns'] for r in cancels)>=1_000_000_000


def test_recorded_cancel_tie_and_delayed_terminal_prevent_early_replacement():
    c=recorded(alpha=None,cancel_ms=100,report_ms=50,decision_ms=100)
    def strategy(m,p):
        return DesiredQuotes(Quote('buy',D(100) if m.time_ns==0 else D(99),D('.002')),None)
    sink=MemorySink()
    run(c,initial(),[trade(1,200_000_000)],sink,strategy,bootstrap_receive_ns=0)
    assert sink.rows['fills'][0]['cancel_pending']
    second=next(r for r in sink.rows['orders'] if r['event']=='submit' and r['order_id']=='order-00000002')
    assert second['time_ns']==300_000_000


def test_sub_lot_participation_capacity_does_not_fabricate_a_fill():
    sink=MemorySink()
    run(recorded(alpha=None,max_trade_participation='.1'),initial(),[trade(1,100_000_000,size='.005')],sink,bootstrap_receive_ns=0)
    assert not sink.rows.get('fills')


def test_flow_guard_uses_only_observed_flow_and_costs():
    from lobcap.backtest.strategies import decide,StrategyParameters
    from lobcap.backtest.types import PortfolioView
    from test_execution import market
    c=variant(config(),strategy='flow_guard',flow_threshold='.6',maker_bps='1')
    view=replace(market(0),volatility_bps=.1,trade_imbalance=D('.8'),trade_volume=D(1))
    quotes=decide(StrategyParameters.from_config(c),view,PortfolioView(D(1000),D(0),D(1000)))
    assert quotes.bid is not None and quotes.ask is None
    assert quotes.reason=='buy_flow_suppress_ask'
    view=replace(view,trade_imbalance=D('-.8'))
    quotes=decide(StrategyParameters.from_config(c),view,PortfolioView(D(1000),D(0),D(1000)))
    assert quotes.bid is None and quotes.ask is not None


def test_observed_flow_excludes_old_arrivals_and_expires_causally():
    from lobcap.backtest.strategies import Features
    features=Features()
    features.observe_trade(trade(1,0,side='sell',size='10'),2*NS)
    assert features.flow(2*NS)==(None,D(0))
    features.observe_trade(trade(2,2*NS,side='sell',size='1'),2*NS)
    assert features.flow(2*NS)==(D(1),D(1))
    assert features.flow(3*NS)==(None,D(0))


@pytest.mark.parametrize('change',[{'clock_mode':'walltime'},{'report_ms':-1},{'max_trade_participation':'0'},
                                 {'max_trade_participation':'1.1'},{'impact_stress_bps':'-1'}])
def test_strict_clock_and_capacity_settings(change):
    with pytest.raises(ConfigError):variant(config(),**change)
