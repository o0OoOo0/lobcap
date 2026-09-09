"""R06–R09/R11: durable state and comparison adversaries."""
from dataclasses import replace
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from lobcap.backtest.workspace.contracts import StrategyDraft
from lobcap.backtest.workspace.resolve import resolve_experiment
from lobcap.backtest.workspace.store import WorkspaceStore
from lobcap.backtest.workspace.worker import run_pending
from lobcap.backtest.workspace.compare import compare_rows
from test_runner import sample


def test_r06_revision_conflict_and_idempotent_submission(sample, tmp_path):
    config, path = sample
    store = WorkspaceStore(tmp_path / 'workspace')
    saved = store.save_draft(StrategyDraft(config, 'first'))
    newer = store.save_draft(replace(saved, name='newer'))
    with pytest.raises(ValueError, match='revision|conflict'):
        store.save_draft(replace(saved, name='stale tab'))
    assert store.load_draft(saved.draft_id).name == 'newer'
    spec = resolve_experiment(newer, sample_path=path)
    first = store.submit(spec, 'same-click')
    assert store.submit(spec, 'same-click') == first
    assert len(store.list_jobs()) == 1
    changed = resolve_experiment(replace(newer, name='different'), sample_path=path)
    with pytest.raises(ValueError):
        store.submit(changed, 'same-click')


def test_r07_four_completed_cancel_resume_preserves_exact_artifacts(sample, tmp_path):
    config, path = sample
    store = WorkspaceStore(tmp_path / 'workspace')
    spec = resolve_experiment(StrategyDraft(config, 'resume'), {'half_spread_ticks': [1, 2, 3, 4, 5, 6]}, sample_path=path)
    job = store.submit(spec, 'cancel-test')
    calls = []
    def execute(config, sample_path, research_policy=None, cancel_check=None):
        calls.append(str(config.half_spread_ticks))
        output = tmp_path / f'run-{len(calls)}'; output.mkdir()
        (output / 'proof.json').write_text(json.dumps({'half': calls[-1]}))
        if len(calls) == 4:
            store.request_cancel(job)
        return SimpleNamespace(output_dir=output, digest=calls[-1], summary={'net_pnl': '0'})
    run_pending(store.root, execute)
    before = store.get_job(job)
    assert before['status'] == 'CANCELED' and len(before['completed']) == 4
    hashes = {key: value['output_hashes'] for key, value in before['completed'].items()}
    store.resume(job)
    run_pending(store.root, execute)
    after = store.get_job(job)
    assert after['status'] == 'COMPLETED' and len(calls) == 6
    assert {key: after['completed'][key]['output_hashes'] for key in hashes} == hashes
    assert after['attempt'] == 2
    assert list((store.root / 'attempts' / job).glob('state-*.json'))


def test_r08_completed_artifact_tamper_blocks_resume(sample, tmp_path):
    config, path = sample
    store = WorkspaceStore(tmp_path / 'workspace')
    spec = resolve_experiment(StrategyDraft(config, 'tamper'), sample_path=path)
    job = store.submit(spec, 'tamper')
    output = tmp_path / 'evidence'; output.mkdir()
    evidence = output / 'proof.json'; evidence.write_text('original')
    run_pending(store.root, lambda *a, **k: SimpleNamespace(output_dir=output, digest='same', summary={}))
    store.update_job(job, status='INTERRUPTED')
    evidence.write_text('changed')
    with pytest.raises(ValueError, match='checksum'):
        store.resume(job)


def test_r11_same_label_different_execution_assumptions_not_strategy_comparison():
    rows = [dict(sample_sha256='same', scenario_id='baseline', strategy_id='one', maker_bps='0'),
            dict(sample_sha256='same', scenario_id='baseline', strategy_id='two', maker_bps='3')]
    with pytest.raises(ValueError):
        compare_rows(rows, scenario_id='baseline')


def test_r11_different_samples_rejected_even_with_filter():
    rows = [dict(sample_sha256='a', scenario_id='x', strategy_id='one'),
            dict(sample_sha256='b', scenario_id='x', strategy_id='one')]
    with pytest.raises(ValueError, match='sample'):
        compare_rows(rows, scenario_id='x')


def test_r06_os_lock_prevents_second_process_then_releases_on_death(tmp_path):
    from lobcap.backtest.runtime import RunLock
    path = tmp_path / 'execution.lock'
    lock = RunLock(path)
    assert lock.acquire(blocking=False)
    code = ('import sys; from lobcap.backtest.runtime import RunLock; '
            'lock=RunLock(sys.argv[1]); print(lock.acquire(blocking=False),flush=True)')
    try:
        result = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True, text=True, check=True)
        assert result.stdout.strip() == 'False'
    finally:
        lock.release()
    # Child exits without release(); the OS must relinquish its ownership.
    result = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == 'True'
    assert lock.acquire(blocking=False)
    lock.release()
