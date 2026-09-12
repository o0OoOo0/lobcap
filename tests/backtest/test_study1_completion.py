from copy import deepcopy
from decimal import Decimal as D
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lobcap.backtest.research_flow.study1_completion import (
    COMBINED_CASES, DECOMPOSITION_CASES, ROBUSTNESS_SCENARIOS,
    audit_causal_paths, combined_configs, decomposition_configs, development_gate,
    mixed_observations, pooled_vpin_mean, review_group, summarize_rows,
    validation_gate, _changes,
)
from lobcap.backtest.replay import load_sample
from lobcap.backtest.runner import run_sweep
from lobcap.config.backtest import variant
from test_study1_bp import bp_sample
from test_vpin import vpin_sample


def test_frozen_case_design_and_legacy_identities(vpin_sample):
    config, _ = vpin_sample
    decomposition = decomposition_configs(config, D('.73'))
    assert tuple(decomposition) == DECOMPOSITION_CASES
    assert decomposition['MM-3-gate-only'].vpin_mode == 'gate'
    assert decomposition['MM-3-gate-only'].vpin_width_ticks == 0
    assert decomposition['MM-3-mean-width'].vpin_constant_value == D('.73')
    assert decomposition['MM-3-vpin'].vpin_mode == 'signal'
    combined = combined_configs(config)
    assert tuple(combined) == COMBINED_CASES
    assert combined['MM-4-bp-vpin'].book_pressure_ticks == config.signal_strength
    assert combined['MM-4-reversed-bp-vpin'].book_pressure_ticks == -config.signal_strength
    assert 'vpin_constant_value' not in variant(config,
        strategy='vpin_inventory', vpin_mode='constant').resolved()
    assert 'book_pressure_ticks' not in variant(config, strategy='inventory_skew').resolved()
    with pytest.raises(ValueError):
        decomposition_configs(config, D('1.01'))


def test_mixed_causal_replay_mean_and_decomposition_controls(vpin_sample):
    config, sample = vpin_sample
    rows = list(mixed_observations(config, sample, config.decision_ms*1_000_000))
    assert rows and all({point.feature_name for point in snapshot.points}
        == {'imbalance_1', 'vpin_normal_volume'} for snapshot, _ in rows)
    available = [state['value'] for _, state in rows if state['status'] == 'AVAILABLE']
    fitted = pooled_vpin_mean([{'available': len(available),
        'sum': str(sum(available, D(0)))}])
    assert D(fitted['mean']) == sum(available, D(0))/len(available)
    configs = decomposition_configs(config, fitted['mean'])
    comparison, _ = run_sweep(configs['MM-1'], _changes(configs), sample)
    paths = [Path(path) for path in json.loads(
        (comparison/'comparison_manifest.json').read_text())['runs']]
    audit = audit_causal_paths(config, sample, DECOMPOSITION_CASES, configs, paths)
    effects = audit['case_effects']
    assert effects['MM-3-zero']['quote_path_differences_from_mm1'] == 0
    assert effects['MM-3-gate-only']['quote_path_differences_from_mm1'] > 0
    assert effects['MM-3-mean-width']['quote_path_differences_from_mm1'] > 0
    assert effects['MM-3-vpin']['quote_path_differences_from_mm1'] > 0


def test_combined_zero_bp_vpin_and_reversed_paths(vpin_sample, monkeypatch):
    config, sample = vpin_sample
    configs = combined_configs(config)
    comparison, _ = run_sweep(configs['MM-1'], _changes(configs), sample)
    paths = [Path(path) for path in json.loads(
        (comparison/'comparison_manifest.json').read_text())['runs']]
    audit = audit_causal_paths(config, sample, COMBINED_CASES, configs, paths)
    effects = audit['case_effects']
    assert effects['MM-4-zero']['quote_path_differences_from_mm1'] == 0
    assert effects['MM-2-bp']['quote_path_differences_from_mm1'] > 0
    assert effects['MM-4-bp-vpin']['quote_path_differences_from_mm1'] > 0
    assert effects['MM-4-reversed-bp-vpin']['quote_path_differences_from_mm1'] > 0
    from lobcap.backtest.research_flow import study1_completion
    original = study1_completion.run_record
    def clean_record(path):
        row = original(path)
        row['manifest']['code_version'] = 'a'*40
        return row
    monkeypatch.setattr(study1_completion, 'run_record', clean_record)
    _, seal = load_sample(sample)
    reviewed = review_group(SimpleNamespace(window_id='fixture', date='2026-09-07',
        role='DEVELOPMENT'), seal, configs, paths, COMBINED_CASES, 'MM-4-zero')
    assert reviewed['zero_control_parity'] is True
    summary = summarize_rows([reviewed], COMBINED_CASES)
    assert set(summary['component_contrasts']) == {
        'bp_given_inventory', 'vpin_given_inventory', 'bp_given_vpin', 'vpin_given_bp'}
    assert 'mean_factorial_interaction' in summary


def _summary(delta, drawdown='-0.1', positive=4):
    candidate = {'sum_reset_window': {'net_pnl': str(delta)},
        'mean_window': {'max_drawdown': drawdown},
        'positive_net_pnl_windows': positive, 'negative_net_pnl_windows': 4-positive}
    return {'pooled': {'deltas_from_mm1': {'MM-4-bp-vpin': candidate}},
        'per_date': {'a': {'deltas_from_mm1': {'MM-4-bp-vpin': {
            'sum_reset_window': {'net_pnl': '0.1'}}}},
            'b': {'deltas_from_mm1': {'MM-4-bp-vpin': {
            'sum_reset_window': {'net_pnl': '0.1'}}}}}}


def test_sequential_gates_are_fail_closed_and_fixed():
    combined = _summary('.2')
    robustness = {'baseline': combined,
        **{name: _summary('.1') for name in ROBUSTNESS_SCENARIOS}}
    assert development_gate(combined, robustness)['status'] == 'PASS'
    failed = deepcopy(robustness)
    failed['joint_fee_latency_queue'] = _summary('-.01')
    assert development_gate(combined, failed)['status'] == 'FAIL'
    assert validation_gate(_summary('.2', positive=3), _summary('.1'))['status'] == 'PASS'
    assert validation_gate(_summary('.2', positive=2), _summary('.1'))['status'] == 'FAIL'
