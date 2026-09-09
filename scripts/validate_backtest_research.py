"""Validate the existing 10-minute research matrix; no new data or OOS run."""
import argparse
from collections import defaultdict
from decimal import Decimal, localcontext
import json
from pathlib import Path
import subprocess

from lobcap.backtest.diagnostics import read_rows
from lobcap.backtest.engine import MarketBook
from lobcap.backtest.replay import iter_events,load_sample,write_json
from lobcap.backtest.research import run_research,DEFAULT_STRATEGIES
from lobcap.config.backtest import load_backtest_config


def source_audit(base,sample,folder):
    initial,manifest=load_sample(sample,base)
    public={event.replay_index:event for event in iter_events(sample)}
    state=json.loads((folder/'state.json').read_text())
    maker=taker=0
    with localcontext() as ctx:
        ctx.prec=50
        for row in state['rows']:
            path=folder/row['run_path']
            run_manifest=json.loads((path/'run_manifest.json').read_text())
            assert run_manifest['sample_sha256']==manifest['sample_sha256']
            orders={r['order_id']:r for r in read_rows(path/'orders.jsonl.gz')}
            used=defaultdict(Decimal);consumed=defaultdict(Decimal)
            book=MarketBook(initial);events=iter(public.values());next_event=next(events,None)
            inventory=Decimal(run_manifest['resolved_config']['initial_inventory'])
            for fill in read_rows(path/'fills.jsonl.gz'):
                while next_event is not None and next_event.time_ns<=fill['time_ns']:
                    book.apply(next_event)
                    if next_event.kind=='depth':
                        for update in next_event.updates:consumed.pop((update.side,update.price),None)
                    next_event=next(events,None)
                source=public[fill['replay_index']]
                qty,price=Decimal(fill['quantity']),Decimal(fill['price'])
                assert qty>0 and fill['source_refs']==list(source.source_refs)
                assert base.sample.start_ns<=fill['time_ns']<base.sample.end_ns
                if fill['role']=='maker':
                    assert source.trade is not None and source.time_ns==fill['time_ns']
                    assert fill['side']==source.trade.maker_side
                    assert source.trade.price<=price if fill['side']=='buy' else source.trade.price>=price
                    order=orders[fill['order_id']]
                    assert order['activation_ns']<fill['time_ns']<=order['terminal_ns']
                    assert price==Decimal(order['price'])
                    used[source.replay_index]+=qty
                    assert used[source.replay_index]<=source.trade.size*Decimal(run_manifest['resolved_config']['max_trade_participation'])
                    maker+=1
                else:
                    assert fill['role']=='taker' and source.kind=='depth' and source.time_ns==book.time_ns
                    assert fill['side']=='sell' if inventory>0 else fill['side']=='buy'
                    assert qty<=abs(inventory)
                    side='sell' if fill['side']=='buy' else 'buy'
                    consumed[(side,price)]+=qty
                    assert consumed[(side,price)]<=book.quantity(side,price)
                    assert book.coverage_bid<=price<=book.coverage_ask
                    taker+=1
                inventory+=qty*(1 if fill['side']=='buy' else -1)
            assert inventory==Decimal(row['metrics']['ending_inventory'])
    return {'maker_fills':maker,'taker_level_fills':taker,'checks':'source references, source price/side/time, maker lifecycle/volume cap, reduce-only hedge, no depth reuse, final inventory'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='configs/backtest/binance_recorded.yaml')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('validation output already exists')
    base=load_backtest_config(args.config);sample=base.output_path/'samples'/base.sample.sample_id
    assert subprocess.check_output(['systemctl','is-active','lobcap@btcusdt.service'],text=True).strip()=='active'
    progress=lambda i,n,r:print(f"{i}/{n} {r['variant_id']}: {r['metrics']['net_pnl']}",flush=True)
    first=run_research(base,sample,progress=progress)
    second=run_research(base,sample,tuple(reversed(DEFAULT_STRATEGIES)),progress=progress)
    a=json.loads((first/'state.json').read_text());b=json.loads((second/'state.json').read_text())
    assert a['deterministic_digest']==b['deterministic_digest']
    assert json.loads((first/'selection.json').read_text())['paper_ready'] is False
    audit=source_audit(base,sample,first)
    performance=[]
    for folder,state in ((first,a),(second,b)):
        for row in state['rows']:
            perf=json.loads((folder/row['run_path']/'run_manifest.json').read_text())['performance']
            assert perf['wall_seconds']<60 and perf['process_peak_rss_mib']<512
            performance.append(perf)
    assert subprocess.check_output(['systemctl','is-active','lobcap@btcusdt.service'],text=True).strip()=='active'
    report={'status':'PASS','experiments':[str(first),str(second)],'runs':len(performance),
        'deterministic_digest':a['deterministic_digest'],'source_audit':audit,
        'max_wall_seconds':max(p['wall_seconds'] for p in performance),
        'max_process_rss_mib':max(p['process_peak_rss_mib'] for p in performance),
        'total_output_bytes':sum(p['output_bytes'] for p in performance),
        'collector':'active before and after','paper_ready':False}
    write_json(args.output,report)
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
