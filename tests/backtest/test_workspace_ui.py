"""Independent UI presentation checks: precision and no hidden aggregation."""
from decimal import Decimal

from lobcap.backtest.workspace_ui.editor import settings_from_inputs, parse_object, parse_axis_list
from lobcap.backtest.workspace_ui.results import slice_rows, comparison_spec
from lobcap.config.backtest import load_backtest_config
from lobcap.config.experiment import ROOT


def test_editor_preserves_untouched_values_and_exact_decimal():
    from lobcap.config.backtest import variant
    base = variant(load_backtest_config(ROOT/'configs/backtest/binance_recorded.yaml'), initial_cash='7654', entry_ms=97)
    changed = settings_from_inputs(base, {'maker_bps':'0.501', 'half_spread_ticks':'2.25'})
    assert changed.maker_bps == Decimal('0.501')
    assert changed.half_spread_ticks == Decimal('2.25')
    assert changed.initial_cash == Decimal('7654') and changed.entry_ms == 97
    assert base.maker_bps != changed.maker_bps


def test_slice_separates_scenarios_and_never_averages_other_axes():
    rows = [
        {'scenario_id':'base','axis_values':{'x':'1','z':'0'},'fill_count':1,'net_pnl':'-2'},
        {'scenario_id':'base','axis_values':{'x':'1','z':'1'},'fill_count':20,'net_pnl':'5'},
        {'scenario_id':'touch','axis_values':{'x':'1','z':'1'},'fill_count':30,'net_pnl':'100'},
    ]
    assert slice_rows(rows,'base',{'z':'1'},min_fills=10) == [rows[1]]
    assert slice_rows(rows,'base',{'z':'0'},min_fills=10) == []


def test_editor_json_rejects_non_mapping():
    import pytest
    with pytest.raises(ValueError):
        parse_object('[1,2]', 'axes')
    assert parse_object('{"coefficient":0.501}', 'parameters')['coefficient'] == Decimal('0.501')


def test_cost_sensitivity_slice_retains_all_scenarios_explicitly():
    rows = [{'scenario_id':'cost0','execution':{'orders_with_fill':3}},
            {'scenario_id':'cost2','execution':{'orders_with_fill':2}}]
    assert slice_rows(rows, None, min_fills=1) == rows


def test_simple_axis_rejects_missing_values():
    import pytest
    for text in ('', '0.5,,1', ',1', '1,', '1, ,2'):
        with pytest.raises(ValueError, match='빈 항목'):
            parse_axis_list(text)
    assert parse_axis_list(' 0.5, 1,2 ') == ['0.5','1','2']


def test_comparison_snapshot_keeps_explicit_filters_and_axes():
    import pytest
    row = {'sample_sha256':'sample','run_id':'run1','variant_id':'v1','effective_hash':'exact','execution_contract_version':'binance-own-orders-v2'}
    spec = comparison_spec([row],['job1'],'strategy','scenario',None,3,'net_pnl',['x','y'],{'z':'2'},['run1'])
    assert spec['runs'] == [{'run_id':'run1','variant_id':'v1','effective_hash':'exact'}]
    assert spec['execution_contract_version']=='binance-own-orders-v2'
    assert spec['fixed_axis_values'] == {'z':'2'} and spec['minimum_filled_orders'] == 3
    assert spec['overlay_run_ids'] == ['run1'] and spec['sort_ascending'] is False
    with pytest.raises(ValueError):
        comparison_spec([row],['job1'],'strategy','s',None,0,'fees',[],{},list('1234567'))


def test_workspace_navigation_never_submits(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip('streamlit')
    from streamlit.testing.v1 import AppTest
    from lobcap.backtest.workspace.store import WorkspaceStore
    def forbidden(*args, **kwargs):
        raise AssertionError('Rendering cannot submit a simulation')
    monkeypatch.setattr(WorkspaceStore, 'submit', forbidden)
    script = '''
from dataclasses import replace
from pathlib import Path
from lobcap.config.backtest import load_backtest_config
from lobcap.config.experiment import ROOT
from lobcap.backtest.workspace_ui import render_workspace
base=replace(load_backtest_config(ROOT/'configs/backtest/binance_recorded.yaml'),output_path=Path(OUTPUT))
render_workspace(base, base.output_path/'samples'/base.sample.sample_id)
'''.replace('OUTPUT', repr(str(tmp_path)))
    app = AppTest.from_string(script).run(timeout=30)
    assert not app.exception
    assert [t.label for t in app.tabs] == ['전략 편집','실험 실행','결과 비교','연구 이력']
    app.run(timeout=30)
    assert not app.exception


def test_advanced_axes_survive_revision_remount_and_second_save(tmp_path, monkeypatch):
    import json
    import pytest
    pytest.importorskip('streamlit')
    from streamlit.testing.v1 import AppTest
    import lobcap.backtest.workspace.resolve as resolver
    calls = []
    def capture(draft, axes=None, **kwargs):
        calls.append(axes.copy())
        return {'draft_revision':draft.revision}
    monkeypatch.setattr(resolver, 'resolve_experiment', capture)
    script = '''
import streamlit as st
from pathlib import Path
from lobcap.config.backtest import load_backtest_config,variant
from lobcap.config.experiment import ROOT
from lobcap.backtest.workspace.contracts import StrategyDraft
from lobcap.backtest.workspace.store import WorkspaceStore
from lobcap.backtest.workspace_ui.editor import render_editor
base=variant(load_backtest_config(ROOT/'configs/backtest/binance_recorded.yaml'),expression_parameters={'strength':'1'})
if 'ws_draft' not in st.session_state:
    st.session_state['ws_draft']=StrategyDraft(base,'revision test')
render_editor(st.session_state['ws_draft'],WorkspaceStore(Path(OUTPUT)),Path(OUTPUT))
'''.replace('OUTPUT', repr(str(tmp_path)))
    app = AppTest.from_string(script).run(timeout=30)
    axes = {'half_spread_ticks':['0.5','1'],'inventory_skew_ticks':['0','1'],'parameter.strength':['1','2']}
    next(w for w in app.radio if w.label=='탐색 입력 방식').set_value('고급 JSON')
    next(w for w in app.text_area if w.label=='전략 탐색 축 (JSON)').set_value(json.dumps(axes))
    next(w for w in app.button if w.label=='초안 저장 · 실행 미리보기').click().run(timeout=30)
    assert not app.exception
    assert next(w for w in app.radio if w.label=='탐색 입력 방식').value == '고급 JSON'
    next(w for w in app.button if w.label=='초안 저장 · 실행 미리보기').click().run(timeout=30)
    assert not app.exception
    assert len(calls)==4 and all(value==axes for value in calls)
    assert app.session_state['ws_draft'].revision == 2
