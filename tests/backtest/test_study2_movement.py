from decimal import Decimal as D
import gzip
import json
import pytest
from types import SimpleNamespace

from lobcap.backtest.research_flow.labels import MidpointSeries
from lobcap.backtest.research_flow.signal_contracts import VALID
from lobcap.backtest.research_flow.signal_contracts import FreshnessPolicy
from lobcap.backtest.research_flow.study2_movement import (
    HORIZONS, _excursion, aggregate_lifetimes, aggregate_movement,
    fit_regime_edges, movement_window, order_lifetime_window, regime_name,
    spacing_candidates,
)
from test_study1_bp import bp_sample


def _write_gzip(path, rows):
    with gzip.open(path, 'wt', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row)+'\n')


def test_hand_computed_excursion_and_intermediate_coverage():
    truth = MidpointSeries(((0, D(100), 's'), (10, D(103), 's'),
        (20, D(98), 's'), (30, D(102), 's')))
    label = SimpleNamespace(status=VALID, target_requested_ns=30,
        target_age_ns=0, anchor_mid=D(100), value=D(2))
    result = _excursion(label, truth, 0, D(1))
    assert result == {'status': VALID, 'final_displacement_ticks': D(2),
        'absolute_displacement_ticks': D(2), 'up_excursion_ticks': D(3),
        'down_excursion_ticks': D(2), 'maximum_absolute_excursion_ticks': D(3),
        'target_age_ns': 0}
    uncovered = MidpointSeries(truth.rows, (True, True, False, True))
    assert _excursion(label, uncovered, 0, D(1))['status'] == 'CENSORED_UNKNOWN_COVERAGE'

    extended = MidpointSeries((*truth.rows, (40, D(500), 's')))
    assert _excursion(label, extended, 0, D(1)) == result


def test_regime_fit_uses_only_available_causal_values(tmp_path):
    path = tmp_path/'regime.jsonl.gz'
    _write_gzip(path, [{'status': 'AVAILABLE', 'value': str(value)} for value in range(6)]
        + [{'status': 'STALE', 'value': None}])
    fit = fit_regime_edges([path])
    assert fit['status'] == 'PASS' and fit['observations'] == 6
    assert sum(fit['counts'].values()) == 6 and fit['future_movement_used'] is False
    low, high = map(D, fit['edges_bps'])
    assert regime_name(low, (low, high)) == 'LOW'
    assert regime_name(high, (low, high)) == 'NORMAL'
    assert regime_name(high+D(1), (low, high)) == 'HIGH'


def test_order_lifetime_and_replacement_distributions(tmp_path):
    run = tmp_path/'run'; run.mkdir()
    rows = [
        {'event':'submit','order_id':'o1','side':'buy','time_ns':0},
        {'event':'activate','order_id':'o1','side':'buy','time_ns':10},
        {'event':'partial_fill','order_id':'o1','side':'buy','time_ns':30},
        {'event':'terminal','order_id':'o1','side':'buy','time_ns':100,
            'activation_ns':10,'submitted_ns':0,'status':'CANCELED','reason':'replace'},
        {'event':'submit','order_id':'o2','side':'buy','time_ns':100},
        {'event':'activate','order_id':'o2','side':'buy','time_ns':130},
        {'event':'terminal','order_id':'o2','side':'buy','time_ns':200,
            'activation_ns':130,'submitted_ns':100,'status':'FILLED','reason':'fully_filled'}]
    _write_gzip(run/'orders.jsonl.gz', rows)
    window = order_lifetime_window(run)
    assert window['active_lifetime_ns'] == [90, 70]
    assert window['submitted_to_terminal_ns'] == [100, 100]
    assert window['time_to_first_fill_ns'] == [20]
    assert window['replacement_downtime_ns'] == [30]
    pooled = aggregate_lifetimes([window])
    assert pooled['active_lifetime_ns']['quantiles']['0.5'] == '80.0'


def test_movement_aggregation_and_lifetime_aligned_spacing(tmp_path):
    path = tmp_path/'movement.jsonl.gz'; rows = []
    for index, (regime, scale) in enumerate((('LOW', 1), ('NORMAL', 2), ('HIGH', 4))):
        outcomes = {}
        for horizon in HORIZONS:
            value = D(scale)*D(horizon)/D(1_000_000_000)
            outcomes[str(horizon)] = {'status': VALID, 'non_overlapping': True,
                'final_displacement_ticks': str(value),
                'absolute_displacement_ticks': str(abs(value)),
                'up_excursion_ticks': str(value+D(1)), 'down_excursion_ticks': '0',
                'maximum_absolute_excursion_ticks': str(value+D(1))}
        rows.append({'analysis_time_ns': index, 'regime': regime, 'outcomes': outcomes})
    _write_gzip(path, rows)
    movement = aggregate_movement([('2026-09-07', path)])
    assert movement['pooled']['1000000000']['non_overlapping'][
        'maximum_absolute_excursion_ticks']['count'] == 3
    lifetimes = {'active_lifetime_ns': {'quantiles': {'0.5': '800000000'}}}
    spacing = spacing_candidates(movement, lifetimes)
    assert spacing['selected_horizon_ns'] == 1_000_000_000
    assert spacing['pooled']['ceil_unique_ticks']
    assert spacing['strategy_status'] == 'RESEARCH_GEOMETRY_ONLY_MULTI_ORDER_NOT_IMPLEMENTED'


def test_movement_artifact_roundtrip_uses_existing_freshness(bp_sample, tmp_path, monkeypatch):
    from lobcap.backtest.research_flow import study2_movement
    config, sample = bp_sample
    horizons = (100_000_000,)
    monkeypatch.setattr(study2_movement, 'HORIZONS', horizons)
    freshness = FreshnessPolicy('fixture', tuple((h, 2_000_000_000, 2_000_000_000)
        for h in horizons))
    artifact = tmp_path/'movement.jsonl.gz'
    written = movement_window(config, sample, freshness, ('0.1', '0.5'), artifact)
    assert written['observations'] == 20
    assert movement_window(config, sample, freshness, ('0.1', '0.5'), artifact, False) == written

    with gzip.open(artifact, 'rt', encoding='utf-8') as stream:
        rows = [json.loads(line) for line in stream]
    rows[0]['regime'] = 'HIGH' if rows[0]['regime'] != 'HIGH' else 'LOW'
    _write_gzip(artifact, rows)
    with pytest.raises(ValueError, match='differs from source replay'):
        movement_window(config, sample, freshness, ('0.1', '0.5'), artifact, False)
