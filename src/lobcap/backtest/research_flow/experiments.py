"""Research intent wraps the existing immutable workspace execution contract."""
from dataclasses import replace
from pathlib import Path
import uuid

from lobcap.config.backtest import variant, DECIMALS, INTS, STRINGS, STRUCTURED
from lobcap.backtest.runner import utc_now
from lobcap.backtest.types import jsonable
from lobcap.backtest.workspace.contracts import StrategyDraft, config_from_document, fingerprint
from lobcap.backtest.workspace.resolve import resolve_experiment, SCENARIO_FIELDS
from lobcap.backtest.workspace.store import atomic_json, read_json

COST_PROFILES={'zero_diagnostic':'0 비용 진단 · 실제 계정 비용 미확인',
               'user_assumed':'사용자 가정 비용 · 실제 계정 비용 미확인'}
KINDS={'single','strategy_compare','strategy_suite','assumption_sensitivity','baseline_suite'}
CONTEXT_KEYS={'route','draft_id','draft_revision','run_id','baseline_id','job_id','cost_profile','experiment_kind'}
FIXED_FIELDS={'initial_cash','initial_inventory','max_inventory'}


def _question(text):
    if not isinstance(text,str) or not text.strip() or len(text)>2000:
        raise ValueError('이 실험이 답할 질문을 1–2000자로 입력하세요.')
    return text.strip()


def build_experiment(draft,kind='single',parameter=None,values=None,
                     cost_profile='zero_diagnostic',sample_path=None,question=None):
    if kind not in KINDS:raise ValueError('지원하지 않는 실험 유형입니다.')
    if cost_profile not in COST_PROFILES:raise ValueError('비용 프로필을 명시적으로 선택하세요.')
    question=_question(draft.hypothesis if question is None else question)
    config=draft.config
    if cost_profile=='zero_diagnostic':config=variant(config,maker_bps='0',taker_bps='0')
    draft=replace(draft,config=config,hypothesis=question)
    if kind in {'single','baseline_suite'} and (parameter is not None or values is not None):
        raise ValueError('이 실험 유형에는 추가 탐색 축을 사용할 수 없습니다.')
    if kind=='strategy_suite':
        from lobcap.config.backtest import STRATEGIES
        if parameter is not None or not isinstance(values,(list,tuple)) or not 2<=len(values)<=12:
            raise ValueError('전략 suite에는 2–12개 전략을 선택하세요.')
        selected=list(values)
        if len(selected)!=len(set(selected)) or any(value not in STRATEGIES for value in selected):
            raise ValueError('전략 suite에 알 수 없거나 중복된 전략이 있습니다.')
        if any(value.startswith('glft') for value in selected):
            config=variant(config,quote_start_seconds=max(config.quote_start_seconds,config.glft_min_seconds))
            draft=replace(draft,config=config)
        spec=resolve_experiment(draft,{'strategy':selected},sample_path=sample_path)
    elif kind in {'strategy_compare','assumption_sensitivity'}:
        if not isinstance(values,(list,tuple)) or not 2<=len(values)<=24:
            raise ValueError('비교할 값을 2–24개 입력하세요.')
        allowed=(DECIMALS|INTS|STRINGS|STRUCTURED)-SCENARIO_FIELDS-FIXED_FIELDS
        if kind=='strategy_compare' and parameter not in allowed:
            raise ValueError('전략 비교는 정책 하나만 변경하며 비용·체결·초기 조건을 고정합니다.')
        if kind=='assumption_sensitivity' and parameter not in SCENARIO_FIELDS:
            raise ValueError('가정 민감도는 비용·체결 가정 하나만 변경합니다.')
        if cost_profile=='zero_diagnostic' and parameter in {'maker_bps','taker_bps'}:
            raise ValueError('수수료 민감도는 사용자 가정 비용 프로필을 선택하세요.')
        spec=resolve_experiment(draft,{parameter:list(values)},sample_path=sample_path)
    elif kind=='baseline_suite':
        if draft.research_policy is not None:
            raise ValueError('E0–E4는 maker 정책 비교입니다. 별도 헤지 정책을 제거한 초안으로 실행하세요.')
        baseline=variant(config,strategy='join_bbo',partial_fill_policy='keep_remaining',
                         reprice_threshold_ticks='0',min_hold_ms=0,
                         signal_expression='',expression_parameters=())
        cases=[('E0','무거래 기준선',{'strategy':'no_trade'}),
               ('E1','최우선 양방향 · 잔량 유지',{}),
               ('E2','부분 체결 후 목표 수량 재설정',{'partial_fill_policy':'reset_target'}),
               ('E3','재고가 쌓이는 방향 수량 축소',{'strategy':'inventory_size'}),
               ('E4','불리한 거래 흐름에서 한쪽 철회',{'strategy':'flow_withdraw'})]
        spec=None
        runs=[]
        for label,name,patch in cases:
            part=resolve_experiment(replace(draft,config=variant(baseline,**patch)),sample_path=sample_path)
            if spec is None:spec=part
            row=part['runs'][0]
            row['research_label']=label
            row['research_name']=name
            row['baseline_label']='E1'
            row['axis_values']={'behavior_case':label}
            runs.append(row)
        spec['runs']=runs
        spec['axes']={'behavior_case':[r['research_label'] for r in runs]}
    else:
        spec=resolve_experiment(draft,sample_path=sample_path)
    spec.pop('experiment_hash',None)
    spec['research_context']={'version':'research-intent-v1','kind':kind,'question':question,
        'cost_profile':cost_profile,'cost_label':COST_PROFILES[cost_profile],
        'execution_profile':'recorded_capture' if config.clock_mode=='recorded' else 'exchange_zero_feed',
        'parameter':parameter,'values':jsonable(values),'parent_run_id':draft.parent_run_id,
        'expected_difference':('같은 표본·실행 가정·거래 시작 시각에서 전략 정책만 비교합니다.' if kind=='strategy_suite'
            else 'E1을 기준으로 주문 유지·재고 수량·흐름 철회를 각각 비교합니다.' if kind=='baseline_suite'
            else f'{parameter} 하나를 변경합니다.' if parameter else '설정 전체를 동결한 기준 실행입니다.'),
        'fees_verified':False,'paper_eligibility':'INSUFFICIENT_EVIDENCE'}
    spec['experiment_hash']=fingerprint(spec)
    return jsonable(spec)


def clone_for_next(run_dir,question,change=None,name=None):
    import yaml
    path=Path(run_dir)
    document=yaml.safe_load((path/'config.yaml').read_text(encoding='utf-8'))
    config=config_from_document(document)
    manifest=read_json(path/'run_manifest.json')
    if change is not None:
        if not isinstance(change,dict) or len(change)!=1:
            raise ValueError('다음 실험에서는 설정 하나만 변경하세요.')
        config=variant(config,**change)
    policy=manifest.get('research_policy')
    return StrategyDraft(config,name or '다음 실험 · '+path.name[-8:],_question(question),
                         parent_run_id=path.name,research_policy=policy)


def save_conclusion(store,run_id,outcome,observation,next_question='',next_change=None):
    if outcome not in {'no_candidate','revise','needs_more_data'}:
        raise ValueError('연구 결론을 선택하세요.')
    observation=_question(observation)
    if next_question:_question(next_question)
    if next_change is not None and (not isinstance(next_change,dict) or len(next_change)!=1):
        raise ValueError('다음 변경은 하나만 기록하세요.')
    row={'version':'research-conclusion-v1','run_id':run_id,'outcome':outcome,
         'observation':observation,'next_question':next_question,'next_change':jsonable(next_change),
         'created_at':utc_now(),'paper_eligibility':'INSUFFICIENT_EVIDENCE'}
    with store.transaction():
        path=store.path('conclusions',run_id,uuid.uuid4().hex+'.json')
        atomic_json(path,row)
    return row


def load_context(store):
    path=store.root/'research_context.json'
    if not path.exists():return {}
    row=read_json(path)
    return {k:v for k,v in row.items() if k in CONTEXT_KEYS}


def save_context(store,context):
    if set(context)-CONTEXT_KEYS:raise ValueError('unknown research context fields')
    with store.transaction():
        saved=load_context(store)
        saved.update(jsonable(context))
        atomic_json(store.root/'research_context.json',saved)
    return saved


def load_context_draft(store,context=None):
    context=load_context(store) if context is None else context
    identifier=context.get('draft_id')
    if not identifier:return None
    revision=context.get('draft_revision')
    if revision is None:return store.load_draft(identifier)
    if type(revision) is not int or revision<1:raise ValueError('invalid saved draft revision')
    row=read_json(store.path('draft_revisions',identifier,f'{revision}.json'))
    return StrategyDraft(config_from_document(row['config_document']),row['name'],row['hypothesis'],
                         row['draft_id'],row['revision'],row.get('parent_run_id'),row.get('research_policy'))
