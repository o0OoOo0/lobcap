"""VPIN prerequisite diagnostics and offline/runtime input acceptance."""
from collections import Counter
from contextlib import ExitStack
from decimal import Decimal, localcontext
import gzip
from itertools import zip_longest
import json
from pathlib import Path

from lobcap.backtest.replay import load_sample, iter_events
from lobcap.backtest.types import jsonable
from lobcap.backtest.vpin import NAME, VERSION
from lobcap.config.common import sha256_file
from .feature_cache import iter_feature_snapshots
from .labels import truth_midpoint_series, measure_label, apply_freshness_policy
from .signal_analysis import _pearson, _ranks, _bucket_report
from .signal_contracts import AnalysisSchedule, LabelDefinition, FreshnessPolicy, CLIENT_FORWARD_RETURN
from .vpin_features import provider


CASES = ('MM-1', 'MM-3-zero', 'MM-3', 'MM-3-constant')
HORIZONS = (100_000_000, 500_000_000, 1_000_000_000, 5_000_000_000)
EDGES = tuple(Decimal(x) for x in ('.2','.4','.6','.8'))


def observations(config, sample_path, interval_ns):
    initial, manifest = load_sample(sample_path, config)
    holder = []
    def factory(*args):
        engine = provider(config)(*args); holder.append(engine); return engine
    schedule = AnalysisSchedule(config.sample.start_ns, config.sample.end_ns, interval_ns)
    snapshots = iter_feature_snapshots(initial, lambda: iter_events(sample_path), schedule,
        config.instrument.tick, bootstrap_receive_ns=manifest['bootstrap_receive_ns'],
        acquisition_segment=manifest.get('acquisition_segments', ['segment-1'])[0],
        feature_names=(NAME,), engine_factory=factory)
    for snapshot in snapshots:
        yield snapshot, holder[0].vpin.snapshot(snapshot.analysis_time_ns)


def feature_window(config, sample_path, freshness_row, artifact, write=True):
    """Causal 100ms inputs, then existing fresh/covered labels offline only."""
    initial, manifest = load_sample(sample_path, config)
    segment = manifest.get('acquisition_segments', ['segment-1'])[0]
    truth = truth_midpoint_series(initial, iter_events(sample_path), segment)
    freshness = FreshnessPolicy(freshness_row['policy_id'],
        tuple(tuple(row) for row in freshness_row['maximum_age_by_horizon_ns']))
    definitions = tuple(LabelDefinition(CLIENT_FORWARD_RETURN, h) for h in HORIZONS)
    pairs = {h: [] for h in HORIZONS}; nonoverlap = {h: [] for h in HORIZONS}
    last = {h: None for h in HORIZONS}; label_statuses = {h: Counter() for h in HORIZONS}
    statuses = Counter(); count = 0; final_state = None
    artifact = Path(artifact)
    with ExitStack() as stack, localcontext() as ctx:
        ctx.prec = 50
        if write:
            artifact.parent.mkdir(parents=True, exist_ok=True)
            stream = stack.enter_context(artifact.open('xb'))
            output = stack.enter_context(gzip.GzipFile(fileobj=stream, mode='wb', mtime=0))
        else:
            stream = stack.enter_context(gzip.open(artifact, 'rt', encoding='utf-8'))
            stored = iter(stream)
        for snapshot, state in observations(config, sample_path, 100_000_000):
            row = jsonable({'snapshot': snapshot, 'state': state})
            if write: output.write((json.dumps(row, sort_keys=True)+'\n').encode())
            elif json.loads(next(stored, 'null')) != row:
                raise ValueError('VPIN feature artifact differs from causal replay')
            point = snapshot.point(NAME); count += 1; statuses[point.status] += 1; final_state = state
            for definition in definitions:
                h = definition.horizon_ns
                label = measure_label(definition, snapshot.analysis_time_ns, snapshot.client_mid,
                    snapshot.client_mid_source_exchange_time_ns, truth, config.instrument.tick,
                    config.sample.end_ns, segment, segment, snapshot.client_mid_coverage)
                label = apply_freshness_policy(label, freshness)
                status = label.status if point.status == 'AVAILABLE' else 'FEATURE_'+point.status
                label_statuses[h][status] += 1
                if status == 'VALID':
                    pair = (point.value, abs(label.value))
                    pairs[h].append(pair)
                    if last[h] is None or snapshot.analysis_time_ns-last[h] >= h:
                        nonoverlap[h].append(pair); last[h] = snapshot.analysis_time_ns
        if not write and next(stored, None) is not None:
            raise ValueError('extra VPIN feature artifact rows')
        if not count or not statuses['AVAILABLE']:
            raise ValueError('VPIN prerequisite has no available feature observations')
        def metrics(values):
            x, y = ([r[i] for r in values] for i in (0,1))
            return {'valid':len(values), 'pearson':_pearson(x,y),
                'spearman':_pearson(_ranks(x),_ranks(y)),
                'moments':{k:sum(v,Decimal(0)) for k,v in {
                    'x':x,'y':y,'xx':[v*v for v in x],'yy':[v*v for v in y],
                    'xy':[a*b for a,b in values]}.items()},
                'mean_absolute_move_ticks':sum(y,Decimal(0))/len(y) if y else None,
                'fixed_buckets':_bucket_report(values,EDGES)}
        return jsonable({'status':'PASS','version':VERSION,'sample_sha256':manifest['sample_sha256'],
            'observations':count,'feature_statuses':dict(statuses),'final_state':final_state,
            'feature_artifact_sha256': sha256_file(artifact) if not write else None,
            'toxicity_proxy':'absolute client-forward midpoint change, not fill-conditioned adverse selection',
            'diagnostics':{str(h):{'statuses':dict(label_statuses[h]),'overlapping':metrics(pairs[h]),
                'non_overlapping':metrics(nonoverlap[h])} for h in HORIZONS}})


def prerequisite_phase(config, window, seal, freshness, output):
    from .study1 import sealed_config
    sample_path = config.output_path/'samples'/window.window_id
    cfg = sealed_config(config, window, seal, sample_path, output, 'vpin')
    artifact = Path(output)/'vpin_features'/f'{window.window_id}.jsonl.gz'
    row = feature_window(cfg, sample_path, freshness, artifact)
    row['feature_artifact_sha256'] = sha256_file(artifact)
    row.update(window_id=window.window_id, date=window.date)
    return {'window':row}


def summarize_prerequisite(rows):
    """Pool sufficient statistics without joining labels across reset windows."""
    def group(members):
        result={}
        with localcontext() as ctx:
            ctx.prec=50
            for h in map(str,HORIZONS):
                result[h]={}
                for scope in ('overlapping','non_overlapping'):
                    values=[r['diagnostics'][h][scope] for r in members]
                    n=sum(v['valid'] for v in values)
                    moments={k:sum((Decimal(v['moments'][k]) for v in values),Decimal(0))
                             for k in ('x','y','xx','yy','xy')}
                    vx,vy=(n*moments[k+k]-moments[k]**2 for k in ('x','y'))
                    correlation=(n*moments['xy']-moments['x']*moments['y'])/(vx*vy).sqrt() if n>1 and vx>0 and vy>0 else None
                    buckets=[]
                    for i in range(5):
                        bins=[v['fixed_buckets']['buckets'][i] for v in values]
                        count=sum(b['count'] for b in bins)
                        total=sum((Decimal(b['mean_label'])*b['count'] for b in bins if b['count']),Decimal(0))
                        buckets.append({'bucket':i+1,'count':count,'mean_absolute_move_ticks':total/count if count else None})
                    result[h][scope]={'valid':n,'pearson':correlation,
                        'mean_absolute_move_ticks':moments['y']/n if n else None,'fixed_buckets':buckets}
        return result
    return jsonable({'pooled':group(rows),'per_date':{date:group([r for r in rows if r['date']==date])
        for date in sorted({r['date'] for r in rows})},
        'spearman_scope':'per-window only; ranks are not averaged across windows',
        'non_overlap_scope':'selected separately within each reset window, not independent dates',
        'statistical_significance':'NOT_CLAIMED'})


def audit_feature_parity(config, sample_path, paths):
    from .study1 import economic_value
    if len(paths) != 4: raise ValueError('VPIN requires four cases')
    statuses = Counter(); count = 0
    differences = {k:0 for k in CASES}
    reasons = {k:Counter() for k in CASES}
    with ExitStack() as stack:
        ds=[]; ts=[]
        for p in paths:
            for destination, name in ((ds,'decisions'),(ts,'decision_traces')):
                destination.append(map(json.loads,stack.enter_context(gzip.open(Path(p)/f'{name}.jsonl.gz','rt'))))
        for values in zip_longest(observations(config,sample_path,config.decision_ms*1_000_000),*ds,*ts):
            if any(v is None for v in values): raise ValueError('VPIN decision populations differ')
            (snapshot,state),*rest=values
            decisions,traces=rest[:4],rest[4:]
            count+=1;statuses[state['status']]+=1
            quotes=lambda d:economic_value([d[k] for k in ('buy_price','sell_price','buy_quantity','sell_quantity')])
            reference=quotes(decisions[0])
            for label,d,t in zip(CASES,decisions,traces):
                if d['time_ns']!=snapshot.analysis_time_ns or t['time_ns']!=snapshot.analysis_time_ns:
                    raise ValueError('VPIN decision clock mismatch')
                if label!='MM-1' and t['policy_state']['vpin']!=jsonable(state):
                    raise ValueError('VPIN offline/runtime state mismatch')
                if label in CASES[2:] and state['status']!='AVAILABLE' and (d['buy_price'] is not None or d['sell_price'] is not None):
                    raise ValueError('VPIN quoted from unavailable buckets')
                differences[label]+=int(quotes(d)!=reference);reasons[label][d['reason']]+=1
        if not count or differences['MM-3-zero']: raise ValueError('VPIN zero quote parity failed')
    return {'status':'PASS','decisions_per_case':count,'feature_statuses':dict(statuses),
        'quote_path_differences_from_mm1':differences,
        'decision_reasons':{k:dict(v) for k,v in reasons.items()}}


def validate_cases(row):
    cases=row['cases'];reference=cases['MM-1']['config']
    widths=[]
    if reference['strategy']!='inventory_skew' or reference['clock_mode']!='recorded':
        raise ValueError('VPIN reference configuration mismatch')
    for label in CASES[1:]:
        cfg=cases[label]['config']
        if (cfg['strategy']!='vpin_inventory' or cfg['vpin_version']!=VERSION
            or any(cfg.get(k)!=reference.get(k) for k in set(cfg)|set(reference)
                   if k!='strategy' and not k.startswith('vpin_'))):
            raise ValueError('VPIN cases may vary only registered controls')
        widths.append(Decimal(cfg['vpin_width_ticks']))
        if cfg['vpin_mode'] != ('constant' if label==CASES[3] else 'signal'):
            raise ValueError('VPIN control mode mismatch')
        if any(cfg[k]!=cases[CASES[1]]['config'][k] for k in
               ('vpin_bucket_volume','vpin_bucket_count','vpin_max_age_ms')):
            raise ValueError('VPIN feature setting drift')
    if widths[0]!=0 or widths[1]<=0 or widths[2]!=widths[1]:raise ValueError('VPIN width control mismatch')
    if row.get('vpin_feature_parity',{}).get('status')!='PASS':raise ValueError('VPIN requires causal parity')
