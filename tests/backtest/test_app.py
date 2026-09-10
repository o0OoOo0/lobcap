"""Acceptance checks for the single four-page research workflow."""
from pathlib import Path
import shutil
import pytest
from test_runner import sample
from lobcap.backtest.runner import config_document
from lobcap.backtest.workspace.store import WorkspaceStore


def widget(elements,label):
    return next(item for item in elements if item.label==label)


def app_for(config):
    pytest.importorskip('streamlit')
    from streamlit.testing.v1 import AppTest
    script = '''
from lobcap.backtest.workspace.contracts import config_from_document
from lobcap.backtest.research_flow.ui import render_platform
render_platform(config_from_document(DOCUMENT))
'''.replace('DOCUMENT',repr(config_document(config)))
    return AppTest.from_string(script)


def test_navigation_is_lazy_and_active_fields_are_relevant(sample,monkeypatch):
    config,_=sample
    def forbidden(*args,**kwargs): raise AssertionError('Navigation must not submit')
    monkeypatch.setattr(WorkspaceStore,'submit',forbidden)
    app=app_for(config).run(timeout=30)
    assert not app.exception and not app.error
    assert widget(app.radio,'작업').options==['전략','실험','결과','비교']
    assert not app.tabs
    assert '신호 배수' not in [x.label for x in app.text_input]
    for page in ['실험','결과','비교','전략']:
        widget(app.radio,'작업').set_value(page).run(timeout=30)
        assert not app.exception and not app.error
    widget(app.selectbox,'행동 정책').set_value('no_trade').run(timeout=30)
    assert '기본 주문 수량 (BTC)' not in [x.label for x in app.text_input]
    assert not app.exception


def test_saved_revision_survives_navigation_and_new_browser_session(sample):
    config,_=sample
    app=app_for(config).run(timeout=30)
    widget(app.text_input,'전략 이름').set_value('durable strategy')
    widget(app.text_area,'검증할 시장 가설').set_value('same policy should preserve exact known state')
    widget(app.button,'초안 저장').click().run(timeout=30)
    assert not app.exception and not app.error
    widget(app.radio,'작업').set_value('실험').run(timeout=30)
    restored=app_for(config).run(timeout=30)
    assert widget(restored.radio,'작업').value=='실험'
    assert any('durable strategy' in c.value for c in restored.caption)
    assert len(WorkspaceStore(config.output_path/'workspace').list_jobs())==0


def test_save_submit_results_compare_and_next_clone(sample,monkeypatch):
    config,path=sample
    target=config.output_path/'samples'/config.sample.sample_id
    target.parent.mkdir(parents=True)
    shutil.copytree(path,target)
    import lobcap.backtest.workspace.jobs as jobs
    launched=[]
    monkeypatch.setattr(jobs,'launch_worker',lambda root:launched.append(root))
    app=app_for(config).run(timeout=30)
    widget(app.button,'초안 저장').click().run(timeout=30)
    widget(app.radio,'작업').set_value('실험').run(timeout=30)
    widget(app.selectbox,'실험 유형').set_value('baseline_suite').run(timeout=30)
    widget(app.button,'실행 설정 확인').click().run(timeout=30)
    assert not app.error and not app.exception
    store=WorkspaceStore(config.output_path/'workspace')
    assert not store.list_jobs() and not launched
    widget(app.button,'확인한 설정으로 실행 제출').click().run(timeout=30)
    assert len(store.list_jobs())==1 and len(launched)==1
    app.run(timeout=30)
    assert len(store.list_jobs())==1 and len(launched)==1
    from lobcap.backtest.workspace.worker import run_pending
    run_pending(store.root)
    widget(app.radio,'작업').set_value('결과').run(timeout=30)
    assert not app.exception and not app.error
    assert len(widget(app.selectbox,'실행 이력 · 이전 실행 포함').options)==5
    widget(app.text_area,'관찰한 사실과 근거').set_value('No candidate: a development sample cannot establish profitability.')
    widget(app.button,'연구 결론 저장').click().run(timeout=30)
    assert list((store.root/'conclusions').rglob('*.json'))
    widget(app.button,'이 실행을 기준으로 비교').click().run(timeout=30)
    assert not app.exception and not app.error
    assert widget(app.radio,'작업').value=='비교'
    widget(app.button,'변경 실행의 주문 근거 열기').click().run(timeout=30)
    widget(app.selectbox,'복제하며 바꿀 한 요소').set_value('partial_fill_policy').run(timeout=30)
    widget(app.text_input,'새 값').set_value('reset_target')
    widget(app.text_area,'다음 실험에서 확인할 질문').set_value('Does changing only partial retention reduce replacement?')
    widget(app.button,'다음 실험용 전략 복제').click().run(timeout=30)
    assert not app.exception and not app.error
    assert widget(app.radio,'작업').value=='전략'
    assert len(store.list_drafts())==2 and len(store.list_jobs())==1
    copied=store.load_draft(store.list_drafts()[0]['draft_id'])
    assert copied.config.partial_fill_policy=='reset_target' and copied.parent_run_id


def test_strategy_fields_and_synthetic_preview_contract(sample):
    from lobcap.backtest.research_flow.ui import active_fields,synthetic_preview
    from lobcap.config.backtest import variant
    config,_=sample
    assert active_fields('no_trade')==()
    assert active_fields('join_bbo')==('size',)
    assert active_fields('inventory_size')==('size',)
    assert active_fields('flow_withdraw')==('size','flow_min_volume')
    rows=synthetic_preview(variant(config,strategy='no_trade'))
    assert all(r['위험 제한 후 수량']=='0' for r in rows)


def test_legacy_result_without_workspace_jobs_or_trace_is_visible(sample):
    import json
    from lobcap.backtest.runner import run_one
    from lobcap.backtest.research_flow.experiments import save_conclusion,save_context
    config,path=sample
    from lobcap.config.backtest import variant
    config=variant(config,stale_book_ms=100000)  # Initial fixture book is 30 s old; exercise real orders.
    result=run_one(config,path)
    legacy=config.output_path/'runs'/'legacy-readable'
    legacy.mkdir()
    for file in result.output_dir.iterdir():
        if file.name not in {'decision_traces.jsonl.gz','run_manifest.json'}:
            shutil.copyfile(file,legacy/file.name)
    manifest=json.loads((result.output_dir/'run_manifest.json').read_text())
    manifest['run_id']='legacy-readable'
    (legacy/'run_manifest.json').write_text(json.dumps(manifest))
    original=(legacy/'summary.json').read_bytes()
    store=WorkspaceStore(config.output_path/'workspace')
    save_context(store,{'route':'결과','run_id':'legacy-readable'})
    save_conclusion(store,'legacy-readable','no_candidate','Saved negative conclusion remains visible.')
    app=app_for(config).run(timeout=30)
    assert not app.exception and not app.error
    assert widget(app.selectbox,'실행 이력 · 이전 실행 포함').value=='legacy-readable'
    assert any('미기록' in item.value for item in app.info)
    assert any('Saved negative conclusion' in item.value for item in app.markdown)
    compact=[table.value for table in app.dataframe if '행동·이벤트' in table.value.columns]
    assert compact and '시각 (UTC, ms)' in compact[0].columns
    assert not {'observable_market','observable_portfolio','actions','source_refs'} & set(compact[0].columns)
    assert '주문 시간축 원본 · 전체 필드' in [e.label for e in app.expander]
    assert not store.list_jobs() and original==(legacy/'summary.json').read_bytes()


def test_order_timeline_is_flat_selected_order_and_display_precision():
    from lobcap.backtest.research_flow.ui_results import compact_timeline,number_display,utc_display,representative_table
    trace={'channel':'decision_traces','time_ns':1_123_999_999,'decision_id':'decision-1',
           'observable_market':{'private':'not a display column'},
           'risk_limited_target':{'bid':{'price':'100','quantity':'.001'},'ask':{'price':'101','quantity':'.002'}},
           'actions':[{'side':'buy','order_id':'buy-1','action':'submit','reason':'new_target'},
                      {'side':'sell','order_id':'sell-2','action':'cancel','reason':'replace'}]}
    rows=compact_timeline([trace],'buy-1')
    assert len(rows)==1 and rows[0]['방향']=='매수'
    assert rows[0]['행동·이벤트']=='신규 제출' and rows[0]['수량 의미']=='목표'
    assert rows[0]['수량 BTC']=='0.001000' and rows[0]['결정 ID']=='decision-1'
    assert utc_display(1_123_999_999)=='1970-01-01 00:00:01.123 UTC'
    assert number_display('0.000123456789123456789')=='0.000123'
    assert number_display('0.123456789',2,percent=True)=='12.35%'
    assert number_display(None)=='미기록'
    example=representative_table([{'order_id':'buy-1','selection_reason':'cancel_pending_fill','time_ns':0}])[0]
    assert example['검토 사례']=='취소 대기 중 체결'
