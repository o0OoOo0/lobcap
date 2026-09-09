from dataclasses import replace
from decimal import Decimal as D

from lobcap.backtest.strategies import decide as strategy_decide, risk_control, Features, StrategyParameters
from lobcap.backtest.types import PortfolioView
from lobcap.config.backtest import load_backtest_config, variant
from lobcap.config.experiment import ROOT
from test_execution import market


def decide(config,market,portfolio):
    parameters=StrategyParameters.from_config(config)
    assert not hasattr(parameters,"alpha") and not hasattr(parameters,"entry_ms")
    return strategy_decide(parameters,market,portfolio)


def test_strategies_and_inventory_reservation():
    config=load_backtest_config(ROOT/"configs/backtest/binance_demo.yaml")
    m=market(0)
    p=PortfolioView(D(100000),D(".01"),D(100001))
    desired=risk_control(config,m,p,decide(config,m,p))
    assert desired.bid is None and desired.ask.quantity==D(".001")
    skew=variant(config,strategy="inventory_skew",inventory_skew_ticks=20)
    assert decide(skew,m,p).bid.price<decide(config,m,p).bid.price
    signal=variant(config,strategy="microprice",signal_strength=10)
    assert decide(signal,replace(m,microprice=D(101)),p).ask.price>decide(config,m,p).ask.price


def test_observable_gates_and_volatility_controls():
    c=load_backtest_config(ROOT/"configs/backtest/binance_demo.yaml")
    p=PortfolioView(D(100000),D(0),D(100000))
    m=market(0);desired=decide(c,m,p)
    assert risk_control(c,replace(m,book_age_ns=501_000_000),p,desired).reason=="stale_book"
    assert risk_control(c,replace(m,coverage_bid=D(100.1)),p,desired).bid is None
    stop=variant(c,volatility_stop_bps=1)
    assert risk_control(stop,m,p,desired).reason=="volatility_warmup"
    assert risk_control(stop,replace(m,volatility_bps=2),p,desired).reason=="volatility_stop"


def test_features_require_contiguous_past_samples():
    f=Features()
    for t in range(30):f.on_second(t,D(100),True)
    assert f.volatility is None
    f.on_second(30,D(100),True)
    assert f.volatility==0
    f.on_second(31,D(100),False)
    assert f.volatility is None
