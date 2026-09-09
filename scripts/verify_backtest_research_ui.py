"""Saved real-result AppTest smoke and legacy golden checks; no browser claim."""
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from lobcap.backtest.runner import run_one
from lobcap.config.backtest import load_backtest_config
from lobcap.config.experiment import ROOT


def main():
    app=AppTest.from_file(str(ROOT/'apps/backtest_lab.py')).run(timeout=30)
    next(item for item in app.radio if item.label=='작업').set_value('10분 연구·후보 검토').run(timeout=30)
    assert not app.exception
    selector=next(item for item in app.selectbox if item.label=='저장된 연구')
    assert selector.value
    assert len(app.dataframe)>=3 and len(app.get('download_button'))>=4
    expected={'binance_demo':'f723f938e9ba732896202fba7d1948d9b9da9f9f02aa94c235c6dc52fd21396d',
        'binance_recorded':'fe87ef329ae21dca8ea8d2efff655db5a07877b28e7d333aa7b3cf556389ef2a'}
    checks={}
    for name,digest in expected.items():
        result=run_one(load_backtest_config(ROOT/f'configs/backtest/{name}.yaml'))
        assert result.digest==digest
        checks[name]={'digest':result.digest,'run':str(result.output_dir)}
    print(json.dumps({'status':'PASS','ui':'saved real experiment, gates, ranking, results, four downloads',
        'selected_experiment':selector.value,'legacy':checks}),flush=True)


if __name__=='__main__':main()
