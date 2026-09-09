from pathlib import Path
from types import SimpleNamespace
import pytest
from lobcap.config.experiment import ROOT


def app_test():
    pytest.importorskip('streamlit')
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT/'apps/backtest_lab.py')).run(timeout=30)


def widget(elements,label):
    return next(item for item in elements if item.label==label)


def test_navigation_is_lazy_and_never_runs_a_simulation(monkeypatch):
    import lobcap.backtest.runner as runner
    import lobcap.backtest.presentation as presentation
    def unexpected(*args,**kwargs):raise AssertionError('Navigation must not simulate')
    monkeypatch.setattr(runner,'run_one',unexpected)
    monkeypatch.setattr(runner,'run_sweep',unexpected)
    monkeypatch.setattr(presentation,'saved_runs',lambda *args:({},[]))
    app=app_test()
    assert not app.exception
    for name in ('실행 결과','실행 비교','민감도 실험','주문 추적','근거·용어','시작·설정'):
        widget(app.radio,'작업').set_value(name).run(timeout=30)
        assert not app.exception


def test_form_submission_resolves_same_default_as_cli(monkeypatch):
    from lobcap.config.backtest import load_backtest_config
    import lobcap.backtest.runner as runner
    import lobcap.backtest.presentation as presentation
    calls=[]
    def capture(config):
        calls.append(config)
        return SimpleNamespace(output_dir=Path('synthetic-form-result'))
    monkeypatch.setattr(runner,'run_one',capture)
    monkeypatch.setattr(presentation,'saved_runs',lambda *args:({},[]))
    app=app_test()
    assert not calls and not app.exception
    widget(app.button,'백테스트 실행').click().run(timeout=30)
    assert not app.exception and len(calls)==1
    assert calls[0].effective_sha256==load_backtest_config(ROOT/'configs/backtest/binance_recorded.yaml').effective_sha256
    app.run(timeout=30)
    assert not app.exception and len(calls)==1


def test_strategy_change_does_not_execute_and_hides_unused_controls(monkeypatch):
    import lobcap.backtest.runner as runner
    def unexpected(*args,**kwargs):raise AssertionError('Selection must not simulate')
    monkeypatch.setattr(runner,'run_one',unexpected)
    app=app_test()
    assert '호가 신호 배수' not in [item.label for item in app.text_input]
    widget(app.selectbox,'전략').set_value('microprice').run(timeout=30)
    assert not app.exception
    assert '호가 신호 배수' in [item.label for item in app.text_input]
    assert '최대 재고일 때 중심 이동 (tick)' not in [item.label for item in app.text_input]


def test_sensitivity_uses_explicit_saved_config_not_previous_session(monkeypatch):
    import lobcap.backtest.runner as runner
    import lobcap.backtest.presentation as presentation
    import lobcap.backtest.replay as replay
    from lobcap.config.backtest import load_backtest_config,variant
    base=load_backtest_config(ROOT/'configs/backtest/binance_recorded.yaml')
    prior=variant(base,entry_ms=99,maker_bps='2')
    manifest={'sample_id':base.sample.sample_id,'sample_sha256':'test','resolved_config':prior.resolved(),'run_id':'prior'}
    monkeypatch.setattr(presentation,'saved_runs',lambda *args:({'prior':(Path('/unused'),manifest)},[]))
    monkeypatch.setattr(replay,'load_sample',lambda *args:(None,{'sample_sha256':'test'}))
    calls=[]
    def capture(config,changes,**kwargs):
        calls.append((config,changes))
        raise RuntimeError('captured without running')
    monkeypatch.setattr(runner,'run_sweep',capture)
    app=app_test()
    app.session_state['scenario_changes']={'maker_bps':'99'}
    widget(app.radio,'작업').set_value('민감도 실험').run(timeout=30)
    widget(app.selectbox,'실험 기준').set_value('prior').run(timeout=30)
    widget(app.selectbox,'바꿀 변수').set_value('report_ms').run(timeout=30)
    assert widget(app.text_input,'비교할 값 (쉼표로 구분)').value=='0, 20, 100'
    widget(app.button,'민감도 실험 실행').click().run(timeout=30)
    assert not app.exception and len(calls)==1
    assert calls[0][0].effective_sha256==prior.effective_sha256
    assert calls[0][1]==[{'report_ms':0},{'report_ms':20},{'report_ms':100}]


def test_dataset_switch_clears_experiment_context(monkeypatch):
    import lobcap.backtest.presentation as presentation
    monkeypatch.setattr(presentation,'saved_runs',lambda *args:({},[]))
    app=app_test()
    app.session_state['lab_comparison']={'path':'must_not_be_read'}
    other=ROOT/'configs/backtest/binance_demo.yaml'
    widget(app.selectbox,'데이터·기본 설정').set_value(other).run(timeout=30)
    assert not app.exception and 'lab_comparison' not in app.session_state
    widget(app.radio,'작업').set_value('민감도 실험').run(timeout=30)
    assert not app.exception


def test_selected_run_survives_pages_without_the_run_selector(monkeypatch,tmp_path):
    import lobcap.backtest.presentation as presentation
    from lobcap.config.backtest import load_backtest_config
    c=load_backtest_config(ROOT/'configs/backtest/binance_recorded.yaml')
    runs={}
    for name in ('newer','older'):
        path=tmp_path/name;path.mkdir();(path/'config.yaml').write_text('settings: {}')
        runs[name]=(path,{'run_id':name,'sample_id':c.sample.sample_id,'sample_sha256':'test','resolved_config':c.resolved()})
    monkeypatch.setattr(presentation,'saved_runs',lambda *args:(runs,[]))
    app=app_test()
    widget(app.radio,'작업').set_value('주문 추적').run(timeout=30)
    widget(app.selectbox,'확인할 실행').set_value('older').run(timeout=30)
    widget(app.radio,'작업').set_value('민감도 실험').run(timeout=30)
    widget(app.radio,'작업').set_value('주문 추적').run(timeout=30)
    assert not app.exception
    assert widget(app.selectbox,'확인할 실행').value=='older'
