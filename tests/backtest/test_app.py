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
    app.run(timeout=30)
    assert not app.exception
