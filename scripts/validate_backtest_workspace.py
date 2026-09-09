"""Offline workspace acceptance on the frozen sample; never selects a new interval.

Use --mode smoke for one grid point and one expression, or --mode matrix for
the 3x3 inventory grid plus expression. Both repeat in reverse submission order.
"""
import argparse
from collections import defaultdict
from decimal import Decimal as D, localcontext
import json
from pathlib import Path
import subprocess

from lobcap.backtest.diagnostics import read_rows
from lobcap.backtest.replay import iter_events, load_sample, write_json
from lobcap.backtest.workspace.contracts import StrategyDraft, config_from_run_spec
from lobcap.backtest.workspace.resolve import resolve_experiment
from lobcap.config.backtest import load_backtest_config, variant


def audit_run(folder, config, sample):
    """Recompute cash/position/fees and each ledger transition without Portfolio."""
    folder = Path(folder)
    _, sample_manifest = load_sample(sample, config)
    manifest = json.loads((folder / 'run_manifest.json').read_text())
    assert manifest['sample_sha256'] == sample_manifest['sample_sha256']
    public = {e.replay_index: e for e in iter_events(sample)}
    orders = {r['order_id']: r for r in read_rows(folder / 'orders.jsonl.gz')}
    fills = list(read_rows(folder / 'fills.jsonl.gz'))
    used = defaultdict(D)
    with localcontext() as ctx:
        ctx.prec = 50
        cash, inventory, fees = config.initial_cash, config.initial_inventory, D(0)
        for fill in fills:
            quantity, price = D(fill['quantity']), D(fill['price'])
            sign = D(1) if fill['side'] == 'buy' else D(-1)
            assert quantity > 0
            source = public[fill['replay_index']]
            assert fill['source_refs'] == list(source.source_refs)
            assert config.sample.start_ns <= fill['time_ns'] < config.sample.end_ns
            rate = config.maker_bps if fill['role'] == 'maker' else config.taker_bps
            fee = price * quantity * rate / 10000
            assert D(fill['fee']) == fee
            assert D(fill['inventory_before']) == inventory
            if fill['role'] == 'maker':
                assert source.trade is not None and source.time_ns == fill['time_ns']
                assert source.trade.maker_side == fill['side']
                order = orders[fill['order_id']]
                assert order['activation_ns'] < fill['time_ns'] <= order['terminal_ns']
                assert price == D(order['price'])
                used[source.replay_index] += quantity
                assert used[source.replay_index] <= source.trade.size * config.max_trade_participation
            else:
                assert fill['role'] == 'taker'
                assert sign * inventory < 0 and quantity <= abs(inventory)
            cash -= sign * price * quantity + fee
            inventory += sign * quantity
            fees += fee
            assert D(fill['inventory_after']) == inventory
        ledger = list(read_rows(folder / 'ledger.jsonl.gz'))
        assert ledger
        initial_equity = D(ledger[0]['equity'])
        previous = None
        fill_index = 0
        for row in ledger:
            q, mark, balance = D(row['inventory']), D(row['mark_price']), D(row['cash'])
            assert balance + q * mark == D(row['equity'])
            assert D(row['equity']) - initial_equity == D(row['net_pnl'])
            assert D(row['net_pnl']) + D(row['fees']) == D(row['gross_pnl'])
            assert D(row['spread_capture']) + D(row['inventory_mtm']) == D(row['gross_pnl'])
            if previous is not None:
                if row['event'] == 'mark':
                    assert D(row['inventory_mtm']) - D(previous['inventory_mtm']) == D(previous['inventory']) * (mark - D(previous['mark_price']))
                elif row['event'] == 'fill':
                    fill = fills[fill_index]; fill_index += 1
                    sign = D(1) if fill['side'] == 'buy' else D(-1)
                    assert D(row['spread_capture']) - D(previous['spread_capture']) == sign * D(fill['quantity']) * (mark - D(fill['price']))
            previous = row
        assert fill_index == len(fills)
        assert D(ledger[-1]['cash']) == cash
        assert D(ledger[-1]['inventory']) == inventory
        assert D(ledger[-1]['fees']) == fees
    summary = json.loads((folder / 'summary.json').read_text())
    assert D(summary['net_pnl']) == D(ledger[-1]['net_pnl'])
    assert summary['sharpe'] is None
    assert all(row['event'] != 'liquidation' for row in ledger)
    return {'fills': len(fills), 'ledger_rows': len(ledger), 'ending_inventory': str(inventory),
            'performance': manifest['performance']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='configs/backtest/binance_recorded.yaml')
    parser.add_argument('--sample', type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--mode', choices=('smoke', 'matrix'), default='matrix')
    parser.add_argument('--check-collector', action='store_true')
    args = parser.parse_args()
    if args.output.exists() or args.workspace.exists():
        raise ValueError('use fresh validation output and workspace paths')
    def collector():
        if args.check_collector:
            assert subprocess.check_output(['systemctl', 'is-active', 'lobcap@btcusdt.service'], text=True).strip() == 'active'
    collector()
    base = load_backtest_config(args.config)
    assert base.sample.end_ns - base.sample.start_ns == 600_000_000_000
    sample = args.sample or base.output_path / 'samples' / base.sample.sample_id
    axes = {'half_spread_ticks': ['.5', '1', '1.5'], 'inventory_skew_ticks': ['0', '1', '2']} if args.mode == 'matrix' else {}
    drafts = [(StrategyDraft(variant(base, strategy='inventory_skew'), 'grid validation'), axes),
              (StrategyDraft(variant(base, strategy='expression', signal_expression='(microprice-mid)/tick - inventory/max_inventory'), 'formula validation'), {})]
    from lobcap.backtest.workspace.store import WorkspaceStore
    from lobcap.backtest.workspace.worker import run_pending
    store = WorkspaceStore(args.workspace)
    results, checks, jobs = [], [], []
    for repeat in range(2):
        pass_results = {}
        for draft, grid in (drafts if repeat == 0 else list(reversed(drafts))):
            if repeat:
                grid = {k: list(reversed(v)) for k, v in reversed(list(grid.items()))}
            spec = resolve_experiment(draft, grid, sample_path=sample)
            job = store.submit(spec, f'{repeat}-{draft.name}')
            run_pending(store.root, wait_for_lock=True)
            state = store.get_job(job)
            assert state['status'] == 'COMPLETED', state
            jobs.append(job)
            for row in spec['runs']:
                complete = state['completed'][row['variant_id']]
                folder = Path(complete['output_dir'])
                audit = audit_run(folder, config_from_run_spec(row), sample)
                checks.append(audit)
                assert audit['performance']['wall_seconds'] < 60
                assert audit['performance']['process_peak_rss_mib'] < 512
                pass_results[row['variant_id']] = complete['deterministic_digest']
                print(f"repeat {repeat+1}: {draft.name} {row['variant_id'][:8]} PASS", flush=True)
        results.append(pass_results)
    assert results[0] == results[1]
    collector()
    report = {'status': 'PASS', 'mode': args.mode, 'jobs': jobs, 'runs': len(checks),
              'digests': results[0], 'checks': checks, 'paper_eligibility': 'INSUFFICIENT_EVIDENCE',
              'collector_checked': args.check_collector}
    write_json(args.output, report)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
