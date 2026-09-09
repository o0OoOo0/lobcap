"""Real worker cancellation/resumption on two frozen 10-minute variants.

Preserves one completed run, interrupts the second, restarts only the second,
and checks immutable completed evidence plus independent resumed-run accounting.
No service, collector, raw-data, or production-code changes.
"""
import argparse
import json
from pathlib import Path
import time

from lobcap.backtest.replay import write_json
from lobcap.backtest.workspace.contracts import StrategyDraft
from lobcap.backtest.workspace.jobs import launch_worker, verify_completed
from lobcap.backtest.workspace.resolve import resolve_experiment
from lobcap.backtest.workspace.store import WorkspaceStore
from lobcap.config.backtest import load_backtest_config, variant
from validate_backtest_workspace import audit_run


def wait_state(store, job, predicate, seconds=180):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        state = store.get_job(job)
        marker = (state['status'], len(state['completed']), state.get('current_variant'))
        if marker != last:
            print(f'job status={marker}', flush=True)
            last = marker
        if predicate(state):
            return state
        if state['status'] in {'FAILED', 'INTERRUPTED'}:
            raise AssertionError(state)
        time.sleep(.05)
    raise TimeoutError(store.get_job(job))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='configs/backtest/binance_recorded.yaml')
    parser.add_argument('--sample', type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.workspace.exists() or args.output.exists():
        raise ValueError('use fresh paths; do not overwrite earlier cancellation evidence')
    base = variant(load_backtest_config(args.config), strategy='expression', signal_expression='(microprice-mid)/tick')
    assert base.sample.end_ns - base.sample.start_ns == 600_000_000_000
    sample = args.sample or base.output_path / 'samples' / base.sample.sample_id
    store = WorkspaceStore(args.workspace)
    spec = resolve_experiment(StrategyDraft(base, 'real worker cancellation'),
                              {'half_spread_ticks': ['.5', '1']}, sample_path=sample)
    job = store.submit(spec, 'real-cancel-on-second-variant')
    process = launch_worker(store.root)
    state = wait_state(store, job, lambda s: len(s['completed']) == 1 and
                       s['status'] == 'RUNNING' and s.get('current_variant') not in s['completed'])
    completed_before = json.loads(json.dumps(state['completed']))
    # Let the next variant enter real replay rather than canceling only the queue.
    # Reach the trading window, not merely the snapshot/feature warmup.
    time.sleep(3)
    started = time.monotonic()
    store.request_cancel(job)
    canceled = wait_state(store, job, lambda s: s['status'] in {'CANCELED', 'COMPLETED'}, seconds=30)
    cancellation_seconds = time.monotonic() - started
    assert canceled['status'] == 'CANCELED', 'second run completed before cancellation; evidence is not a cancellation'
    assert canceled['completed'] == completed_before
    assert cancellation_seconds < 2, cancellation_seconds
    verify_completed(canceled)
    assert process.wait(timeout=30) == 0
    failures = list(canceled['failed'].values())
    assert len(failures) == 1 and failures[0]['status'] == 'CANCELED'
    interrupted_output = Path(failures[0]['output_dir'])
    interrupted_files = sorted(p.name for p in interrupted_output.iterdir() if p.is_file())
    interruption = [json.loads((interrupted_output / 'interrupted.json').read_text())]
    assert interruption[0]['full_window'] is False
    assert interruption[0]['terminal_liquidation'] is False
    assert interruption[0]['inventory'] is not None
    assert interruption[0]['time_ns'] >= base.sample.start_ns
    assert interruption[0]['time_ns'] < base.sample.end_ns
    store.resume(job)
    resumed_process = launch_worker(store.root)
    completed = wait_state(store, job, lambda s: s['status'] == 'COMPLETED')
    assert resumed_process.wait(timeout=30) == 0
    assert completed['attempt'] == 2 and len(completed['completed']) == 2
    for identifier, before in completed_before.items():
        assert completed['completed'][identifier] == before
    verify_completed(completed)
    from lobcap.backtest.workspace.contracts import config_from_run_spec
    audits = [audit_run(completed['completed'][r['variant_id']]['output_dir'], config_from_run_spec(r), sample)
              for r in spec['runs']]
    assert interrupted_output.exists()
    report = {'status': 'PASS', 'job_id': job, 'workspace': str(store.root),
              'cancellation_seconds': cancellation_seconds, 'completed_preserved': len(completed_before),
              'resumed_attempt': completed['attempt'], 'interrupted_output': str(interrupted_output),
              'interrupted_files': interrupted_files, 'interruption': interruption[0], 'audits': audits}
    write_json(args.output, report)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
