from dataclasses import replace
from decimal import Decimal as D, localcontext
import gzip
import json
from types import SimpleNamespace
from copy import deepcopy
from pathlib import Path

import pytest

from lobcap.backtest.vpin import VolumeBuckets, VpinSettings, NAME
from lobcap.backtest.research_flow.study1_vpin import observations, audit_feature_parity, feature_window, summarize_prerequisite
from lobcap.backtest.research_flow import study1
from lobcap.backtest.research_flow.signal_study import development_windows
from lobcap.backtest.replay import load_sample, iter_events, write_json, sample_fingerprint
from lobcap.backtest.runner import run_sweep
from lobcap.backtest.artifacts import MemorySink
from lobcap.backtest.engine import run
from lobcap.backtest.types import ReplayEvent, L2Trade, jsonable
from lobcap.config.backtest import variant
from lobcap.config.common import sha256_file
from test_study1_bp import bp_sample
from test_signal_study import catalogue


def test_hand_computed_split_rolling_conservation_and_staleness():
    b=VolumeBuckets(VpinSettings(D(1),2,10))
    b.observe(D('.75'),'sell',1,2,3,('a',))
    assert b.snapshot(3)['status']=='WARMUP'
    b.observe(D('1.5'),'buy',4,5,6,('b',))
    s=b.snapshot(6)
    assert s['value']==D('.75')  # |.75-.25| and |0-1|, divided by 2
    assert s['completed_buckets']==2 and s['partial_volume']==D('.25')
    assert s['normal_volume']==D('2.25') and s['source_refs']==('b',)
    b.observe(D('.5'),'sell',7,8,9,('c',))
    assert b.snapshot(9)['value']==D('.75') and b.snapshot(9)['available_ns']==6
    assert b.snapshot(15)['status']=='STALE'  # exchange age controls as well as arrival age
    b.observe(D('.25'),'sell',16,17,18,('d',))
    assert b.snapshot(18)['value']==D('.75') and b.snapshot(18)['completed_buckets']==3
    assert b.snapshot(18)['oldest_retained_completion_ns']==6


def test_large_trade_bounded_state_and_decimal_context_independence():
    values=[]
    for precision in (28,50):
        with localcontext() as ctx:
            ctx.prec=precision
            b=VolumeBuckets(VpinSettings(D('.03'),3,100))
            b.observe(D('300000.01'),'sell',0,0,0)
            values.append(b.snapshot(0))
            assert len(b.buckets)==3 and b.completed==10000000
    assert values[0]==values[1] and values[0]['value']==1


@pytest.mark.parametrize('qty,side,exchange,receive,available',[
    (D('-1'),'buy',0,0,0),(D('NaN'),'buy',0,0,0),(D(1),'invalid',0,0,0),
    (D(1),'buy',2,1,3),(D(1),'buy',1,3,2)])
def test_invalid_vpin_inputs_fail(qty,side,exchange,receive,available):
    with pytest.raises(ValueError):VolumeBuckets().observe(qty,side,exchange,receive,available)


def test_no_trade_partial_or_zero_volume_can_refresh_completed_value():
    b=VolumeBuckets(VpinSettings(D(1),1,10));b.observe(D(1),'buy',0,0,0)
    b.observe(D(0),'sell',8,8,8);b.observe(D('.1'),'sell',9,9,9)
    assert b.snapshot(11)['status']=='STALE' and b.snapshot(11)['available_ns']==0
    with pytest.raises(ValueError):b.snapshot(8)
    with pytest.raises(ValueError):b.observe(D(1),'buy',7,7,7)
    assert VolumeBuckets().snapshot(100)['status']=='WARMUP'  # independent run reset


@pytest.fixture
def vpin_sample(bp_sample):
    c,path=bp_sample
    c=variant(c,vpin_bucket_volume='.001',vpin_bucket_count=2,vpin_max_age_ms=300,
        vpin_width_ticks='3',signal_strength='1')
    events=list(iter_events(path));start=c.sample.start_ns
    events.extend([
        ReplayEvent(8,start+200_000_000,start+350_000_000,'trade',('v:1',),
            trade=L2Trade('sell',D('100.1'),D('.002')),total_trade_quantity=D(10)),
        ReplayEvent(9,start+900_000_000,start+1_100_000_000,'trade',('v:2',),
            trade=L2Trade('buy',D(100),D('.003')),total_trade_quantity=D(20)),
        ReplayEvent(10,start+1_300_000_000,start+1_350_000_000,'trade',('v:3',),
            trade=L2Trade('sell',D('100.1'),D('.002')),total_trade_quantity=D(30))])
    events=sorted(events,key=lambda e:e.time_ns)
    events=[replace(e,replay_index=i) for i,e in enumerate(events,1)]
    with gzip.open(path/'events.jsonl.gz','wt') as stream:
        for e in events:stream.write(json.dumps(jsonable(e))+'\n')
    manifest=json.loads((path/'sample_manifest.json').read_text())
    manifest['files']['events.jsonl.gz']=sha256_file(path/'events.jsonl.gz')
    manifest['sample_sha256']=sample_fingerprint(manifest['files'],c.sample)
    write_json(path/'sample_manifest.json',manifest)
    return c,path


def test_causal_prerequisite_runtime_parity_zero_control_and_future_prefix(vpin_sample,monkeypatch,tmp_path):
    c,path=vpin_sample
    rows=list(observations(c,path,100_000_000))
    assert rows[3][1]['status']=='WARMUP' and rows[4][1]['value']==1
    assert rows[-1][1]['normal_volume']==D('.008')  # q=60 is never counted
    assert any(state['status']=='STALE' for _,state in rows)
    freshness={'policy_id':'fixture','maximum_age_by_horizon_ns':[(h,500_000_000,500_000_000)
        for h in (100_000_000,500_000_000,1_000_000_000,5_000_000_000)]}
    artifact=tmp_path/'features.jsonl.gz'
    feature=feature_window(c,path,freshness,artifact)
    feature['feature_artifact_sha256']=sha256_file(artifact)
    assert feature_window(c,path,freshness,artifact,False)==feature
    feature.update(date='2026-09-07')
    aggregate=summarize_prerequisite([feature,dict(feature,date='2026-09-08')])
    for h in feature['diagnostics']:
        actual=aggregate['pooled'][h]['overlapping']; expected=feature['diagnostics'][h]['overlapping']
        assert actual['valid']==2*expected['valid']
        assert actual['mean_absolute_move_ticks']==expected['mean_absolute_move_ticks']
        if expected['pearson'] is not None:assert float(actual['pearson'])==pytest.approx(expected['pearson'])
    with pytest.raises(FileExistsError):feature_window(c,path,freshness,artifact)
    configs=study1.case_configs(c,'vpin')
    comparison,_=run_sweep(configs['MM-1'],[{'strategy':v.strategy,'vpin_width_ticks':v.vpin_width_ticks,
        'vpin_mode':v.vpin_mode} for v in configs.values()],path)
    paths=[Path(p) for p in json.loads((comparison/'comparison_manifest.json').read_text())['runs']]
    parity=audit_feature_parity(c,path,paths)
    assert parity['quote_path_differences_from_mm1']['MM-3']>0
    assert parity['quote_path_differences_from_mm1']['MM-3-zero']==0
    original=study1.run_record
    def record(p):
        row=original(p);row['manifest']['code_version']='a'*40;return row
    monkeypatch.setattr(study1,'run_record',record)
    _,seal=load_sample(path)
    row=study1.review_window(SimpleNamespace(window_id='fixture',date='2026-09-07'),seal,configs,paths,'vpin')
    row['vpin_feature_parity']=parity
    allrows=[]
    for w,s in development_windows(catalogue()):
        r=deepcopy(row);r.update(window_id=w.window_id,date=w.date,sample_sha256=s['sample_sha256']);allrows.append(r)
    assert study1.summarize(catalogue(),allrows,'vpin')['constant_control']['pooled']['paired_windows']==8
    allrows[0]['delta_constant_minus_mm1']['net_pnl']='999'
    with pytest.raises(ValueError):study1.summarize(catalogue(),allrows,'vpin')
    initial,_=load_sample(path);events=list(iter_events(path))
    changed=[replace(e,trade=replace(e.trade,size=D('100'))) if e.trade and e.time_ns>=c.sample.start_ns+900_000_000 else e for e in events]
    sinks=[]
    for source in (events,changed):
        sink=MemorySink();run(configs['MM-3'],initial,iter(source),sink,bootstrap_receive_ns=initial.time_ns);sinks.append(sink)
    for channel in ('decisions','decision_traces','orders','fills','ledger','execution'):
        prefix=lambda sink:[r for r in sink.rows.get(channel,[]) if r.get('time_ns',c.sample.end_ns)<c.sample.start_ns+900_000_000]
        assert prefix(sinks[0])==prefix(sinks[1])
    with gzip.open(paths[2]/'decision_traces.jsonl.gz','rt') as stream:traces=[json.loads(s) for s in stream]
    traces[0]['policy_state']['vpin']['completed_buckets']=999
    with gzip.open(paths[2]/'decision_traces.jsonl.gz','wt') as stream:
        for t in traces:stream.write(json.dumps(t)+'\n')
    with pytest.raises(ValueError,match='state mismatch'):audit_feature_parity(c,path,paths)


def test_vpin_config_rejects_exchange_and_preserves_old_identity(vpin_sample):
    c,_=vpin_sample
    with pytest.raises(ValueError):variant(c,strategy='vpin_inventory',clock_mode='exchange')
    original=variant(c,strategy='inventory_skew').resolved()
    assert not any(k.startswith('vpin_') for k in original)
    assert variant(c,strategy='inventory_skew',vpin_bucket_volume='99').resolved()==original


@pytest.mark.parametrize('fail_window',[7,None])
def test_all_vpin_prerequisites_must_finish_before_any_execution(vpin_sample,monkeypatch,tmp_path,fail_window):
    import importlib
    from lobcap.config.experiment import ROOT
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    runner=importlib.import_module('run_study1_baselines')
    config,_=vpin_sample
    monkeypatch.setattr(runner,'load_backtest_config',lambda _:config)
    monkeypatch.setattr(runner,'_code_fingerprint',lambda _:'a'*40)
    monkeypatch.setattr(runner,'validate_p0',lambda *a:{'status':'PASS','cache_checksums_verified':True})
    monkeypatch.setattr(runner,'sealed_config',lambda *a:config)
    from lobcap.backtest.research_flow import study1_vpin
    monkeypatch.setattr(study1_vpin,'summarize_prerequisite',lambda rows:{'synthetic_windows':len(rows)})
    cat=tmp_path/'catalogue.json';cat.write_text(json.dumps(catalogue()))
    p0=tmp_path/'p0.json';p0.write_text(json.dumps({'code_fingerprint':'b'*40,'freshness_policy':{}}))
    output=tmp_path/'study';called=[]
    def phase(target,*args):
        assert (output/'protocol.json').exists()
        if target.__name__=='prerequisite_phase':
            called.append(args[1].window_id)
            if len(called)==fail_window:raise ValueError('synthetic prerequisite failure')
            return {'window':{'window_id':args[1].window_id},'wall_seconds':1,'peak_rss_mib':1}
        assert len(called)==8 and (output/'vpin_prerequisite.json').exists()
        raise ValueError('synthetic execution reached after all prerequisites')
    monkeypatch.setattr(runner,'_isolated',phase)
    with pytest.raises(ValueError,match='prerequisite failure' if fail_window else 'execution reached'):
        runner.run('config',cat,p0,output,'vpin')
    assert len(called)==(fail_window or 8)
    assert (output/'vpin_prerequisite.json').exists()==(fail_window is None)
