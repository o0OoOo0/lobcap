from pathlib import Path

import pytest

from lobcap.config.experiment import ROOT


def test_streamlit_loads_without_running_simulation(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    import lobcap.backtest.runner as runner
    def unexpected(*args,**kwargs):raise AssertionError("UI rerender must not execute a backtest")
    monkeypatch.setattr(runner,"run_one",unexpected)
    monkeypatch.setattr(runner,"run_sweep",unexpected)
    app=AppTest.from_file(str(ROOT/"apps/backtest_lab.py")).run(timeout=30)
    assert not app.exception


def test_form_submission_resolves_same_default_as_cli(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from types import SimpleNamespace
    from lobcap.config.backtest import load_backtest_config
    import lobcap.backtest.runner as runner
    calls=[]
    def capture(config):
        calls.append(config)
        return SimpleNamespace(output_dir=Path("synthetic-form-result"))
    monkeypatch.setattr(runner,"run_one",capture)
    app=AppTest.from_file(str(ROOT/"apps/backtest_lab.py")).run(timeout=30)
    assert not calls and not app.exception
    next(button for button in app.button if button.label=="Run scenario").click().run(timeout=30)
    assert not app.exception and len(calls)==1
    assert calls[0].effective_sha256==load_backtest_config(ROOT/"configs/backtest/binance_recorded.yaml").effective_sha256
    app.run(timeout=30)
    assert not app.exception and len(calls)==1
