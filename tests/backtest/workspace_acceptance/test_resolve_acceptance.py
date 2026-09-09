"""R01/R02/R08/R09: submitted intent is the execution contract."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from lobcap.backtest.workspace.contracts import StrategyDraft, config_from_run_spec
from lobcap.backtest.workspace.resolve import resolve_experiment
from lobcap.config.backtest import variant
from lobcap.backtest.research_execution import ResearchPolicy
from lobcap.backtest.types import jsonable
from test_runner import sample


def test_r01_nondefault_settings_survive_preview_and_restoration(sample):
    base, path = sample
    changes = dict(maker_bps='-0.731', initial_cash='12345.67', size='.003',
                   inventory_skew_ticks='3.141', entry_ms=37, cancel_ms=83,
                   alpha='.501', max_inventory='.017', half_spread_ticks='2.7')
    original = variant(base, **changes)
    spec = resolve_experiment(StrategyDraft(original, 'nondefault'), sample_path=path)
    row, = spec['runs']
    restored = config_from_run_spec(row)
    for key in changes:
        assert getattr(restored, key) == getattr(original, key)
        assert row['sources'][key] == 'draft'
    assert restored.effective_sha256 == original.effective_sha256
    assert spec['paper_eligibility'] == 'INSUFFICIENT_EVIDENCE'


def test_r02_nine_exact_grid_points_and_order_independent_ids(sample):
    base, path = sample
    draft = StrategyDraft(variant(base, strategy='inventory_skew'), 'nine')
    axes = {'half_spread_ticks': ['.5', '1', '1.5'], 'inventory_skew_ticks': ['0', '1', '2']}
    first = resolve_experiment(draft, axes, sample_path=path)
    second = resolve_experiment(draft, {k: list(reversed(v)) for k, v in reversed(list(axes.items()))}, sample_path=path)
    expected = {(D(h), D(s)) for h in ('.5', '1', '1.5') for s in ('0', '1', '2')}
    assert {(D(r['settings']['half_spread_ticks']), D(r['settings']['inventory_skew_ticks'])) for r in first['runs']} == expected
    assert len(first['runs']) == 9
    assert {r['variant_id'] for r in first['runs']} == {r['variant_id'] for r in second['runs']}


def test_r01_nondefault_policy_and_formula_parameter_axes_preserved(sample):
    base, path = sample
    base = variant(base, clock_mode='recorded', strategy='expression',
                   signal_expression='coefficient * imbalance', expression_parameters={'coefficient': '.7'})
    policy = jsonable(replace(ResearchPolicy(), loss_limit=D('7.31'), hedge_ms=43))
    draft = StrategyDraft(base, 'policy', research_policy=policy)
    spec = resolve_experiment(draft, {'parameter.coefficient': ['.501', '.502']}, sample_path=path)
    assert len(spec['runs']) == 2
    for row in spec['runs']:
        assert row['research_policy']['loss_limit'] == '7.31'
        assert row['research_policy']['hedge_ms'] == 43
    assert {dict(config_from_run_spec(row).expression_parameters)['coefficient'] for row in spec['runs']} == {D('.501'), D('.502')}


def test_r02_decimal_range_duplicates_cap_and_ambiguous_scenario(sample):
    base, path = sample
    draft = StrategyDraft(base, 'exact decimal')
    spec = resolve_experiment(draft, {'alpha': {'start': '.501', 'stop': '.503', 'step': '.001'}}, sample_path=path)
    assert {r['settings']['alpha'] for r in spec['runs']} == {'.501', '.502', '.503'} or {D(r['settings']['alpha']) for r in spec['runs']} == {D('.501'), D('.502'), D('.503')}
    assert len({r['variant_id'] for r in spec['runs']}) == 3
    duplicates = resolve_experiment(draft, {'half_spread_ticks': ['1', '1.0', '1.00']}, sample_path=path)
    assert len(duplicates['runs']) == 1
    assert len(duplicates['duplicates_removed']['half_spread_ticks']) == 2
    with pytest.raises(ValueError, match='25|limit|24'):
        resolve_experiment(draft, {'half_spread_ticks': list(range(1, 26))}, sample_path=path)
    with pytest.raises(ValueError, match='both|axis|axes'):
        resolve_experiment(draft, {'maker_bps': ['1', '2']}, [{'name': 'fee', 'changes': {'maker_bps': '3'}}], path)


def test_r09_fee_only_new_draft_changes_only_fee_not_parent_config(sample):
    base, path = sample
    parent = resolve_experiment(StrategyDraft(base, 'parent'), sample_path=path)['runs'][0]
    original = dict(parent['settings'])
    child = resolve_experiment(StrategyDraft(variant(config_from_run_spec(parent), maker_bps='-1.234'),
        'child', parent_run_id=parent['variant_id']), sample_path=path)
    changes = {k for k in original if original[k] != child['runs'][0]['settings'][k]}
    assert changes == {'maker_bps'}
    assert parent['settings'] == original
    assert child['parent_run_id'] == parent['variant_id']


def test_r08_changed_source_rejected_instead_of_silently_new_checksum(sample):
    base, path = sample
    with (path / 'events.jsonl.gz').open('ab') as stream:
        stream.write(b'altered source')
    with pytest.raises(ValueError, match='checksum'):
        resolve_experiment(StrategyDraft(base, 'bad source'), sample_path=path)
