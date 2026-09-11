from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_signal_study import catalogue
from lobcap.backtest.artifacts import MemorySink
from lobcap.backtest.engine import run
from lobcap.backtest.replay import write_json, load_sample, iter_events, sample_fingerprint
from lobcap.backtest.runner import run_sweep
from lobcap.backtest.research_flow import study1
from lobcap.backtest.research_flow.study1_bp import audit_feature_parity
from lobcap.backtest.research_flow.signal_study import development_windows
from lobcap.backtest.types import BookSnapshot, ReplayEvent, L2LevelUpdate, L2Trade, jsonable, ARRIVAL_NORMALIZATION_VERSION
from lobcap.config.backtest import load_backtest_config, variant
from lobcap.config.common import sha256_file
from lobcap.config.experiment import ROOT


@pytest.fixture
def bp_sample(tmp_path):
    c = load_backtest_config(ROOT/'configs/backtest/binance_recorded.yaml')
    start = c.sample.start_ns
    c = replace(c, sample=replace(c.sample, end_ns=start+2_000_000_000), output_path=tmp_path/'outputs')
    c = variant(c, initial_inventory='.002', signal_strength='3', max_trade_participation='1', alpha=None)
    initial = BookSnapshot(((D(100), D(1)), (D(99), D(1))), ((D('100.1'), D('.5')),),
        start-c.sample.warmup_ns, 0, D('99.5'), D(102))
    def depth(i, t, recv, before, after):
        return ReplayEvent(i, start+t*1_000_000, start+recv*1_000_000, 'depth', (f'd:{i}',),
            updates=(L2LevelUpdate('buy', D(100), D(before), D(after)),))
    events = [depth(1, -100, 50, '1', '3'), depth(2, 50, 250, '3', '1'),
        ReplayEvent(3, start+100_000_000, start+150_000_000, 'trade', ('t:3',),
                    trade=L2Trade('buy', D(100), D('.001'))),
        depth(4, 300, 800, '1', '2'), depth(5, 700, 900, '2', '0'),
        depth(6, 1100, 1200, '0', '.8'), depth(7, 1600, 1800, '.8', '4')]
    path = tmp_path/'sample'; path.mkdir()
    write_json(path/'initial_book.json', initial)
    write_json(path/'validation.json', {'status': 'PASS', 'bootstrap_receive_ns': initial.time_ns})
    with gzip.open(path/'events.jsonl.gz', 'wt') as f:
        for e in events: f.write(json.dumps(jsonable(e))+'\n')
    files = {n: sha256_file(path/n) for n in ('initial_book.json','events.jsonl.gz','validation.json')}
    write_json(path/'sample_manifest.json', {'status': 'PASS', 'sample': c.sample,
        'instrument': c.instrument, 'files': files, 'sample_sha256': sample_fingerprint(files,c.sample),
        'bootstrap_receive_ns': initial.time_ns})
    return c, path


def execute(c, path):
    configs = study1.case_configs(c, 'bp')
    base = configs['MM-1']
    comparison, _ = run_sweep(base, [{k: getattr(v,k) for k in
        ('strategy','inventory_skew_ticks','signal_expression','expression_parameters')}
        for v in configs.values()], path)
    return configs, [Path(p) for p in json.loads((comparison/'comparison_manifest.json').read_text())['runs']]


def test_recorded_bp_controls_causal_parity_and_unknown_coverage(bp_sample, monkeypatch):
    c, path = bp_sample
    configs, paths = execute(c, path)
    parity = audit_feature_parity(c, path, paths)
    assert parity['status'] == 'PASS' and parity['decisions_per_case'] == 8
    assert parity['feature_statuses']['INSUFFICIENT_DEPTH'] > 0
    assert parity['runtime_unusable_decisions'] > 0
    assert parity['case_effects']['MM-2']['quote_path_differences_from_mm1'] > 0
    assert parity['case_effects']['MM-2-reversed']['quote_path_differences_from_mm1'] > 0
    original = study1.run_record
    def fixture_record(p):
        r = original(p); r['manifest']['code_version'] = 'a'*40; return r
    monkeypatch.setattr(study1, 'run_record', fixture_record)
    _, seal = load_sample(path)
    row = study1.review_window(SimpleNamespace(window_id='fixture',date='2026-09-07'), seal, configs, paths, 'bp')
    assert row['zero_coefficient_parity']
    row['bp_feature_parity'] = parity
    cat = catalogue()
    rows = []
    for w, s in development_windows(cat):
        r = deepcopy(row); r.update(window_id=w.window_id, date=w.date, sample_sha256=s['sample_sha256'])
        rows.append(r)
    result = study1.summarize(cat, rows, 'bp')
    assert result['pooled']['paired_windows'] == 8 and 'reversed_control' in result
    rows[0]['cases']['MM-2-reversed']['config']['expression_parameters'][0][1] = '1'
    with pytest.raises(ValueError): study1.summarize(cat, rows, 'bp')
    with gzip.open(paths[2]/'decisions.jsonl.gz','rt') as f: decisions = [json.loads(line) for line in f]
    decisions[1]['imbalance'] = '0.99'
    with gzip.open(paths[2]/'decisions.jsonl.gz','wt') as f:
        for d in decisions: f.write(json.dumps(d)+'\n')
    with pytest.raises(ValueError, match='causal feature'): audit_feature_parity(c,path,paths)


@pytest.mark.parametrize('patch', [{'clock_mode':'exchange'}, {'signal_strength':'0'},
    {'stale_book_ms':501}, {'max_signal_ticks':'1'}, {'inventory_skew_ticks':'0'}])
def test_bp_rejects_incomparable_or_binding_config(bp_sample, patch):
    c, _ = bp_sample
    with pytest.raises(ValueError): study1.case_configs(variant(c, **patch), 'bp')


def test_future_suffix_cannot_change_bp_quote_or_fill_prefix(bp_sample):
    c, path = bp_sample
    cfg = study1.case_configs(c,'bp')['MM-2']
    initial, _ = load_sample(path)
    events = list(iter_events(path))
    changed = [*events[:-1], replace(events[-1], updates=(L2LevelUpdate('buy',D(100),D('.8'),D('99')),))]
    sinks = []
    for stream in (events, changed):
        sink = MemorySink(); run(cfg, initial, iter(stream), sink, bootstrap_receive_ns=initial.time_ns); sinks.append(sink)
    for channel in ('decisions','decision_traces','orders','fills','ledger','execution'):
        prefix = lambda s: [r for r in s.rows.get(channel,[]) if r.get('time_ns',c.sample.end_ns)<c.sample.start_ns+1_600_000_000]
        assert prefix(sinks[0]) == prefix(sinks[1])
