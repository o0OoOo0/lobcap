"""The actual entrypoint layout, immutable submission, and independent PnL view."""
import gzip
import json
import shutil
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from test_runner import sample
from lobcap.backtest.research_flow.workbench_model import reviewed_metrics,economics,STRATEGIES
from lobcap.backtest.accounting import Portfolio
from lobcap.backtest.types import FillEvent,jsonable
from lobcap.backtest.runner import config_document,run_one
from lobcap.config.backtest import variant


@pytest.mark.parametrize('initial,sequence,ending',[
    ('0',[('buy','100','.003'),('sell','102','.001'),('sell','101','.004')],'99'),
    ('0',[('sell','102','.002'),('buy','100','.001')],'101'),
    ('.002',[('sell','102','.003')],'103'),
    ('-.002',[('buy','99','.003')],'102'),
    ('0',[],'100'),
])
@pytest.mark.parametrize('fee',['0','1','-0.5'])
def test_fifo_reconciles_real_accounting(sample,tmp_path,initial,sequence,ending,fee):
    config,_=sample
    config=variant(config,initial_inventory=initial,maker_bps=fee)
    class Sink:
        def write(self,name,row):
            with gzip.open(tmp_path/(name+'.jsonl.gz'),'at') as f:f.write(json.dumps(jsonable(row))+'\n')
    p=Portfolio(config,D(100),Sink())
    market=SimpleNamespace(imbalance=D(0),volatility_bps=None,ask=D(101),bid=D(99))
    for i,(side,price,quantity) in enumerate(sequence):
        p.apply_fill(FillEvent(str(i),config.sample.start_ns+i+1,i,side,D(price),D(quantity),('fixture',)),market)
    p.mark(D(ending),config.sample.end_ns-1,99)
    result=reviewed_metrics(tmp_path)
    assert result['reconciled']
    assert result['realized_gross']+result['unrealized_gross']==p.gross_pnl
    assert result['net_pnl']==p.net_pnl
    if sequence:assert result['break_even_fee_bps']==p.gross_pnl/result['turnover']*10000
    else:assert result['break_even_fee_bps'] is None


def test_fifo_detects_corrupt_fill_evidence(sample,tmp_path):
    config,_=sample
    with gzip.open(tmp_path/'ledger.jsonl.gz','wt') as f:
        f.write(json.dumps(dict(inventory='0',mark_price='100',equity='100000',gross_pnl='1',net_pnl='1'))+'\n')
    assert not reviewed_metrics(tmp_path)['reconciled']


def test_price_units_and_profitable_engine_control(sample):
    u=economics('80000','.1','.5','1')
    assert u['tick_bps']==D('.0125') and u['half_usdt']==D('.05')
    assert u['roundtrip_fee_usdt_per_btc']==D(16)
    config,path=sample
    # Controlled trade hits an existing passive bid with an unchanged midpoint.
    result=run_one(variant(config,alpha=None,stale_book_ms=100000),path)
    assert D(result.summary['gross_pnl'])>0
    assert reviewed_metrics(result.output_dir)['reconciled']


def widget(items,label):return next(i for i in items if i.label==label)


def app_for(config):
    pytest.importorskip('streamlit')
    from streamlit.testing.v1 import AppTest
    script='''
from lobcap.backtest.workspace.contracts import config_from_document
from lobcap.backtest.research_flow.workbench import render_workbench
render_workbench(config_from_document(DOCUMENT))
'''.replace('DOCUMENT',repr(config_document(config)))
    return AppTest.from_string(script)


def test_workbench_strategy_explanation_and_no_implicit_submission(sample,monkeypatch):
    from lobcap.backtest.workspace.store import WorkspaceStore
    config,_=sample
    def forbidden(*a,**k):raise AssertionError('navigation submitted')
    monkeypatch.setattr(WorkspaceStore,'submit',forbidden)
    app=app_for(config).run(timeout=30)
    assert not app.exception and not app.error
    assert [x.value for x in app.subheader]==['01 · 전략 설정','02 · 결과와 시간 경로']
    for policy in STRATEGIES:
        widget(app.selectbox,'전략 유형').set_value(policy).run(timeout=30)
        assert not app.exception and not app.error
        assert any('언제 판단하나?' in x.value for x in app.markdown)
        assert any('어떤 주문을 내나?' in x.value for x in app.markdown)
    widget(app.selectbox,'전략 유형').set_value('join_bbo').run(timeout=30)
    assert '호가 반거리 (tick)' not in [x.label for x in app.text_input]


def test_workbench_submit_result_chart_and_clone(sample,monkeypatch):
    from lobcap.backtest.workspace.store import WorkspaceStore
    from lobcap.backtest.workspace.worker import run_pending
    import lobcap.backtest.workspace.jobs as jobs
    config,path=sample
    target=config.output_path/'samples'/config.sample.sample_id
    target.parent.mkdir(parents=True);shutil.copytree(path,target)
    launched=[]
    monkeypatch.setattr(jobs,'launch_worker',lambda root:launched.append(root))
    app=app_for(config).run(timeout=30)
    widget(app.button,'Simulate · 실행 설정 확인').click().run(timeout=30)
    assert not app.error and not app.exception
    store=WorkspaceStore(config.output_path/'workspace')
    assert not store.list_jobs()
    widget(app.button,'확인한 설정 실행').click().run(timeout=30)
    assert len(store.list_jobs())==1 and len(launched)==1
    run_pending(store.root)
    app.run(timeout=30)
    assert not app.error and not app.exception
    assert len(app.get('plotly_chart'))==1
    assert any(x.label=='순손익 · USDT' for x in app.metric)
    assert len(app.tabs)==4
    widget(app.text_area,'다음에 확인할 질문').set_value('Would wider quotes change fill selection?')
    widget(app.button,'선택 결과를 왼쪽 설정으로 복제').click().run(timeout=30)
    assert not app.error and not app.exception
    assert len(store.list_drafts())==1 and len(store.list_jobs())==1
    restored=app_for(config).run(timeout=30)
    assert widget(restored.text_area,'이번에 확인할 가설').value=='Would wider quotes change fill selection?'
