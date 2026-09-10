import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import pytest
from lobcap.backtest.presentation import (saved_runs,config_from_run,comparison_differences,
    applicable_parameters,sweep_variants,result_message)
from lobcap.backtest.replay import write_json
from lobcap.config.backtest import load_backtest_config,variant
from lobcap.config.experiment import ROOT


def config():return load_backtest_config(ROOT/'configs/backtest/binance_recorded.yaml')


def manifest(c,**extras):
    return {'sample_id':c.sample.sample_id,'sample_sha256':'hash','run_id':'one','resolved_config':c.resolved(),**extras}


def test_saved_configuration_is_restored_exactly_and_wrong_sample_rejected():
    c=config();changed=variant(c,entry_ms=123,alpha='.501',maker_bps='2')
    assert config_from_run(c,manifest(changed)).effective_sha256==changed.effective_sha256
    with pytest.raises(ValueError,match='다른 표본'):config_from_run(c,manifest(c,sample_id='different'))


def test_compare_checks_all_settings_and_sample_identity():
    a=manifest(config());b=manifest(variant(config(),max_trade_participation='.01',impact_stress_bps='2'),run_id='two')
    assert len(comparison_differences([a,b]))==2
    assert comparison_differences([a,a])==[]
    b['sample_sha256']='different'
    with pytest.raises(ValueError,match='체크섬'):comparison_differences([a,b])


def test_compare_rejects_old_engine_and_compares_structured_coefficients():
    a=manifest(config());old=manifest(config(),run_id='old')
    old['resolved_config'].pop('execution_contract_version')
    with pytest.raises(ValueError,match='엔진 버전'):comparison_differences([a,old])
    b=manifest(config(),run_id='two')
    a['resolved_config']['expression_parameters']=[['beta','1']]
    b['resolved_config']['expression_parameters']=[['beta','2']]
    assert len(comparison_differences([a,b]))==1


def test_sweep_rejects_inactive_empty_duplicate_or_unbounded_variants():
    c=config()
    assert 'inventory_skew_ticks' not in applicable_parameters(c.strategy)
    for key,text in [('signal_strength','0,1'),('alpha','0,'),('alpha','.5,0.50'),('entry_ms',','.join(map(str,range(25))))]:
        with pytest.raises(ValueError):sweep_variants(c,key,text)
    assert sweep_variants(c,'alpha','.501,.502,null')==[{'alpha':'.501'},{'alpha':'.502'},{'alpha':None}]


def test_corrupted_and_incomplete_runs_do_not_hide_valid_runs(tmp_path):
    folder=tmp_path/'runs';folder.mkdir()
    for name in ('good','bad','partial'):(folder/name).mkdir()
    (folder/'bad/run_manifest.json').write_text('{truncated')
    c=config()
    write_json(folder/'partial/run_manifest.json',manifest(c,status='FAIL',error='interrupted'))
    write_json(folder/'good/run_manifest.json',manifest(c,status='PASS'))
    for name in ('summary.json','config.yaml','timeseries.csv.gz'):(folder/'good'/name).touch()
    runs,issues=saved_runs(tmp_path,c.sample.sample_id)
    assert list(runs)==['good'] and len(issues)==2


def test_atomic_json_failure_keeps_previous_complete_file(tmp_path,monkeypatch):
    import os
    path=tmp_path/'manifest.json';write_json(path,{'status':'RUNNING'})
    def fail(*args):raise OSError('simulated replacement failure')
    monkeypatch.setattr(os,'replace',fail)
    with pytest.raises(OSError):write_json(path,{'status':'PASS'})
    assert json.loads(path.read_text())=={'status':'RUNNING'}
    assert list(tmp_path.iterdir())==[path]


def test_zero_fill_and_rebate_interpretation():
    assert '체결 0건' in result_message({'execution':{}})
    summary={'execution':{'fill_events':1},'spread_capture':'-2','inventory_mtm':'0','fees':'-1','net_pnl':'-1'}
    assert '스프레드 기여' in result_message(summary)


def test_saved_experiment_survives_session_reset_and_rejects_mismatch(tmp_path):
    from lobcap.backtest.presentation import saved_experiments
    folder=tmp_path/'comparisons/test';folder.mkdir(parents=True)
    write_json(folder/'lab_experiment.json',{'sample_id':'sample','sample_sha256':'hash','parameter':'alpha','source':'기본 설정'})
    write_json(folder/'comparison_manifest.json',{'status':'PASS','sample_sha256':'hash'})
    (folder/'results.csv').write_text('alpha,net_pnl\n0,-1\n1,2\n')
    rows,issues=saved_experiments(tmp_path,'sample')
    assert list(rows)==['test'] and not issues
    write_json(folder/'comparison_manifest.json',{'status':'PASS','sample_sha256':'other'})
    rows,issues=saved_experiments(tmp_path,'sample')
    assert not rows and len(issues)==1


def test_ui_rejects_excessive_decision_count_and_zero_rounded_order():
    from lobcap.backtest.presentation import validate_ui_config
    for changes in ({'decision_ms':1},{'size':'.0001'},{'max_inventory':'.0001'}):
        with pytest.raises(ValueError):validate_ui_config(variant(config(),**changes))
    assert validate_ui_config(config())==config()
