from dataclasses import replace
from decimal import Decimal as D,localcontext
import math
import pytest

from lobcap.backtest.glft import Calibration,coefficients,fit_intensity,DEPTHS,NS
from lobcap.backtest.strategies import StrategyParameters,StrategyPolicy,decide,risk_control
from lobcap.backtest.engine import run
from lobcap.backtest.artifacts import MemorySink
from lobcap.backtest.types import PortfolioView
from lobcap.config.backtest import load_backtest_config,variant
from lobcap.config.experiment import ROOT,ConfigError
from test_execution import market
from test_engine import initial,trade


def config(**changes):
    return variant(load_backtest_config(ROOT/'configs/backtest/binance_demo.yaml'),strategy='glft',**changes)


def test_glft_equation_and_units():
    with localcontext() as ctx:
        ctx.prec=50
        half,skew=coefficients(D(2),D('.5'),D(3),D('.5'))
        # xi=gamma=k=.5, Delta=1 => c1=2ln2, c2=1.
        assert skew==D(3)
        assert half==2*D(2).ln()+D('1.5')
        assert coefficients(D(2),D('.5'),D(0),D('.5'))==(2*D(2).ln(),D(0))


def test_intensity_fit_and_fail_closed():
    counts=[round(2*10000*3*math.exp(-.04*x)) for x in DEPTHS]
    fit=fit_intensity(counts,10000,30,.9)
    assert fit['status']=='ready'
    assert abs(fit['A']-3)<D('.001') and abs(fit['k']-D('.04'))<D('.0001')
    assert fit_intensity([0]*8,100,30,.5)['status']=='insufficient_crossings'
    assert fit_intensity([100]*8,100,30,.5)['status']=='insufficient_crossings'
    assert fit_intensity(list(range(100,108)),100,30,.5)['status']=='unstable_intensity_fit'


def feed(cal,t,depth=16,late=False):
    # Different crossing distances provide an identifiable decreasing curve.
    mid=D(100)+(D('.1') if t%2 else D(0))
    for distance in (2,2,4,4,8,depth):
        cal.trade(t*NS-1,t*NS-2-(2*NS if late else 0),mid+D(distance)*cal.p.tick,'sell',D('.001'),mid,True)
    cal.on_second(t*NS,mid,True)


def test_calibration_causal_rolling_and_late_exclusions():
    p=StrategyParameters.from_config(config(glft_window_seconds=30,glft_min_seconds=30))
    a=Calibration(p);b=Calibration(p)
    a.on_second(0,D(100),True);b.on_second(0,D(100),True)
    for t in range(1,31):
        feed(a,t);feed(b,t)
        assert a.snapshot(t*NS)==b.snapshot(t*NS)
    prefix=a.snapshot(30*NS)
    assert prefix['status']=='ready' and prefix['valid_seconds']==30
    for t in range(31,62):feed(a,t,128);feed(b,t,4,late=True)
    assert prefix['crossing_counts']!=a.snapshot(61*NS)['crossing_counts']
    assert b.snapshot(61*NS)['status']=='insufficient_crossings'
    assert b.excluded>0 and len(a.rows)==30
    assert a.snapshot(63*NS)['status']=='stale_estimate'


def policy(c):
    params=StrategyParameters.from_config(c);state=StrategyPolicy(params)
    state.glft.last=0
    state.glft.estimate={'status':'ready','A':D(2),'k':D('.5'),'sigma_ticks_sqrt_second':D(3)}
    return params,state


def test_quote_inventory_signal_fee_and_width_controls():
    c=config(glft_gamma='.5',half_spread_ticks='0')
    params,state=policy(c);m=market(0)
    p=PortfolioView(D(100000),D(0),D(100000))
    neutral=decide(params,m,p,state=state)
    long=decide(params,m,replace(p,inventory=c.size),state=state)
    assert long.bid.price<neutral.bid.price
    assert state.glft_trace['inventory_shift_ticks']==3
    signal,signal_state=policy(variant(c,strategy='glft_microprice',signal_strength='0'))
    assert decide(signal,m,p,state=signal_state)==neutral
    cost,cost_state=policy(variant(c,maker_bps='100'))
    wide=decide(cost,m,p,state=cost_state)
    assert wide.bid.price<=neutral.bid.price and wide.ask.price>=neutral.ask.price
    blocked,blocked_state=policy(variant(c,glft_max_half_ticks='1'))
    assert decide(blocked,m,p,state=blocked_state).reason=='glft_width_limit'
    assert decide(params,m,p).reason=='glft_warmup'


@pytest.mark.parametrize('patch',[{'glft_gamma':'0'},{'glft_min_r2':'1.1'},
    {'glft_window_seconds':30,'glft_min_seconds':60},{'glft_min_seconds':0},{'quote_start_seconds':600}])
def test_strict_glft_config(patch):
    with pytest.raises(ConfigError):config(**patch)


@pytest.mark.parametrize('clock',['exchange','recorded'])
def test_both_engine_clocks_prefix_and_start_gate(clock):
    c=config(clock_mode=clock,glft_window_seconds=30,glft_min_seconds=30,stale_book_ms=1000000,
             quote_start_seconds=35,alpha=None)
    c=replace(c,sample=replace(c.sample,start_ns=0,end_ns=65*NS,warmup_ns=0))
    events=[]
    for t in range(1,64):
        for d in (2,4,8,16):
            i=len(events)+1;events.append(trade(i,t*NS+i,side='sell',price=str(D('100.5')+D(d)*c.instrument.tick)))
    a=MemorySink();b=MemorySink();repeat=MemorySink()
    result=run(c,initial(),events,a,bootstrap_receive_ns=0)
    again=run(c,initial(),events,repeat,bootstrap_receive_ns=0)
    assert result.digest==again.digest
    mutated=[replace(e,trade=replace(e.trade,price=D(110))) if e.time_ns>=45*NS else e for e in events]
    run(c,initial(),mutated,b,bootstrap_receive_ns=0)
    for channel in ('decision_traces','decisions','fills','ledger'):
        assert [r for r in a.rows.get(channel,[]) if r['time_ns']<45*NS]==[r for r in b.rows.get(channel,[]) if r['time_ns']<45*NS]
    assert all(r['time_ns']>=35*NS for r in a.rows.get('orders',[]))
    assert any(r['policy_state']['glft']['status']=='ready' for r in a.rows['decision_traces'])
    assert a.rows.get('orders')


def test_glft_workbench_controls(sample):
    from test_workbench import app_for,widget
    c,_=sample
    app=app_for(c).run(timeout=30)
    widget(app.selectbox,'전략 유형').set_value('glft').run(timeout=30)
    assert not app.error and not app.exception
    assert widget(app.number_input,'추정 창 (초)').value==120
    assert widget(app.text_input,'GLFT 위험 선호 γ').value=='0.1'
    assert '신호 배수' not in [x.label for x in app.text_input]
    widget(app.selectbox,'전략 유형').set_value('glft_microprice').run(timeout=30)
    assert widget(app.text_input,'신호 배수')


from test_runner import sample
