"""One durable research workflow, without navigation-triggered execution."""
from dataclasses import replace
from decimal import Decimal as D, DecimalException
from datetime import datetime, timezone
import json

from lobcap.backtest.workspace.contracts import StrategyDraft
from lobcap.backtest.workspace.store import WorkspaceStore
from lobcap.config.backtest import variant

PAGES = ('전략', '실험', '결과', '비교')
POLICIES = {'no_trade':'S0 · 무거래 기준선', 'join_bbo':'S1 · 최우선 양방향 호가',
            'inventory_size':'S2 · 재고 기반 수량 축소', 'flow_withdraw':'S3 · 흐름 기반 호가 철회'}
HELP = {'no_trade':'신규 주문 없음. 초기 재고의 평가손익은 남습니다.',
        'join_bbo':'전달된 최우선 bid와 ask에 같은 기본 수량으로 참여합니다.',
        'inventory_size':'알려진 재고가 한도의 60% 이상이면 재고를 늘리는 쪽 수량을 한 lot 줄입니다. 기본 수량이 한 lot이면 그쪽 호가를 중단합니다.',
        'flow_withdraw':'최근 1초 공격적 흐름이 +0.6 이상이면 매도, −0.6 이하면 매수를 철회합니다. 방향별 0.3 안쪽에서 재개합니다. 최소 관측량 미달이면 흐름만으로 추가 중단하지 않습니다.'}
LABELS = {'size':'기본 주문 수량 (BTC)', 'half_spread_ticks':'호가 반거리 (tick)',
          'inventory_skew_ticks':'재고 가격 이동 (tick)', 'signal_strength':'신호 배수',
          'flow_min_volume':'최소 1초 관측량 (BTC)', 'flow_threshold':'흐름 임계값',
          'volatility_spread_multiplier':'변동성 거리 배수', 'max_signal_ticks':'수식 출력 상한 (tick)'}


def active_fields(strategy):
    if strategy == 'no_trade': return ()
    fields = ['size']
    if strategy == 'flow_withdraw': fields += ['flow_min_volume']
    if strategy not in POLICIES:
        fields += ['half_spread_ticks']
        fields += {'inventory_skew':['inventory_skew_ticks'], 'microprice':['signal_strength'],
                   'flow_guard':['inventory_skew_ticks','flow_threshold','flow_min_volume','volatility_spread_multiplier'],
                   'volatility_adaptive':['volatility_spread_multiplier'], 'expression':['max_signal_ticks']}.get(strategy, [])
    return tuple(fields)


def synthetic_preview(config, inventory='0', flow='0'):
    from lobcap.backtest.types import MarketView, PortfolioView
    from lobcap.backtest.strategies import StrategyParameters, decide, risk_control
    b, a = D(100), D(100) + config.instrument.tick
    m = (a+b)/2
    market = MarketView(0,b,a,D(1),D(1),m,m,D(0),1.0,0,D(90),D(110),D(flow),D('.01'))
    p = PortfolioView(config.initial_cash,D(inventory),config.initial_cash+D(inventory)*m)
    raw = decide(StrategyParameters.from_config(config),market,p)
    final = risk_control(config,market,p,raw)
    return [{'방향':side,'정책 의도 가격':str(x.price) if x else '호가 없음',
             '정책 의도 수량':str(x.quantity) if x else '0','위험 제한 후 가격':str(y.price) if y else '호가 없음',
             '위험 제한 후 수량':str(y.quantity) if y else '0','판단':final.reason}
            for side,x,y in [('매수',raw.bid,final.bid),('매도',raw.ask,final.ask)]]


def update_context(store, **changes):
    from .experiments import load_context, save_context
    context = load_context(store)
    context.update(changes)
    save_context(store,context)
    return context


def navigate(store, route, **changes):
    import streamlit as st
    update_context(store,route=route,**changes)
    st.session_state['rf_next_route']=route
    st.rerun()


def render_platform(base):
    import streamlit as st
    from .experiments import load_context, load_context_draft
    store = WorkspaceStore(base.output_path/'workspace')
    context = load_context(store)
    if 'rf_next_route' in st.session_state: st.session_state['rf_route']=st.session_state.pop('rf_next_route')
    if st.session_state.get('rf_route') not in PAGES:
        st.session_state['rf_route']=context.get('route') if context.get('route') in PAGES else '전략'
    page = st.sidebar.radio('작업',PAGES,key='rf_route')
    update_context(store,route=page)
    draft = load_context_draft(store,context) if context.get('draft_id') else None
    draft = draft or StrategyDraft(variant(base,strategy='join_bbo',partial_fill_policy='keep_remaining'),
                                  '최우선 양방향 기준선','단순 유동성 공급의 체결·재고·비용을 기준선으로 확인한다.')
    st.title('마켓메이킹 연구')
    start=datetime.fromtimestamp(base.sample.start_ns/1e9,timezone.utc)
    end=datetime.fromtimestamp(base.sample.end_ns/1e9,timezone.utc)
    st.caption(f'편집 초안: {draft.name} · 저장 버전 {draft.revision} · BTCUSDT · {start:%Y-%m-%d %H:%M}–{end:%H:%M} UTC')
    if page in {'전략','실험'}:
        st.caption('실행 비용은 실험 화면에서 명시적으로 선택합니다. 정보 시계: '+draft.config.clock_mode+
                   f' · 초안 주문/취소/통보 지연 {draft.config.entry_ms}/{draft.config.cancel_ms}/{draft.config.report_ms} ms (가정)')
    st.warning('10분 개발 표본 · 체결 가정 미보정 · 계정 수수료 미확인 · 실시간 주문 전송 없음')
    with st.sidebar.expander('데이터·비용·모델 범위'):
        st.write(f'1 tick = {base.instrument.tick} USDT · 1 lot = {base.instrument.lot} BTC')
        st.write('기록 수신 시각은 수집기 위치 기준입니다. 주문·취소·통보 지연은 별도 가정입니다.')
        st.write('합성 선물 회계이며 펀딩·시장 반응·증거금 청산은 제외합니다. 종료 재고는 자동 청산하지 않습니다.')
    try:
        if page=='전략': render_strategy(store,draft)
        elif page=='실험':
            from .ui_experiment import render_experiment
            render_experiment(store,draft,base,context)
        else:
            from .ui_results import render_results,render_comparison
            (render_results if page=='결과' else render_comparison)(store,base,context)
    except (ValueError,OSError,KeyError,DecimalException,RuntimeError) as exc:
        st.error(f'입력 또는 실행 근거를 확인해 주세요: {exc}')


def render_strategy(store,draft):
    import streamlit as st
    saved=store.list_drafts()
    if saved:
        ids=[r['draft_id'] for r in saved]
        selected=st.selectbox('저장한 전략',ids,index=ids.index(draft.draft_id) if draft.draft_id in ids else 0,
            format_func=lambda x:next(f"{r['name']} · v{r['revision']}" for r in saved if r['draft_id']==x))
        if st.button('선택한 저장 버전 불러오기'):
            chosen=store.load_draft(selected)
            navigate(store,'전략',draft_id=chosen.draft_id,draft_revision=chosen.revision)
    choices=dict(POLICIES)
    if draft.config.strategy not in choices: choices[draft.config.strategy]='이전 전략 · '+draft.config.strategy
    if st.checkbox('고급 가격 신호 전략 보기',value=draft.config.strategy not in POLICIES):
        choices.update({'fixed_spread':'고정 거리','inventory_skew':'재고 가격 이동','microprice':'Microprice',
                        'volatility_adaptive':'변동성 거리','flow_guard':'이전 흐름 정책','expression':'제한된 가격 수식'})
    strategy=st.selectbox('행동 정책',list(choices),index=list(choices).index(draft.config.strategy),format_func=choices.get)
    st.info(HELP.get(strategy,'기존 제한된 가격 신호와 공통 위험 제어를 사용합니다.'))
    edits={'strategy':strategy}
    with st.form(f'rf_strategy_{draft.draft_id}_{draft.revision}_{strategy}'):
        name=st.text_input('전략 이름',draft.name)
        hypothesis=st.text_area('검증할 시장 가설',draft.hypothesis)
        for field in active_fields(strategy): edits[field]=st.text_input(LABELS[field],str(getattr(draft.config,field)))
        if strategy!='no_trade':
            st.markdown('**주문 유지**')
            ps=['keep_remaining','reset_target']
            edits['partial_fill_policy']=st.selectbox('부분 체결 후',ps,index=ps.index(draft.config.partial_fill_policy),
                format_func=lambda p:'잔량 유지' if p=='keep_remaining' else '목표 수량 재설정 · 취소 후 재주문')
            edits['reprice_threshold_ticks']=st.text_input('재호가 가격 차이 기준 (tick)',str(draft.config.reprice_threshold_ticks))
            for key,label,minimum in [('min_hold_ms','일반 변경의 최소 유지시간 (ms)',0),('max_age_ms','활성화 후 최대 주문 나이 (ms)',1)]:
                edits[key]=st.number_input(label,min_value=minimum,value=getattr(draft.config,key))
            st.caption('위험·오래된 데이터·호가 철회는 최소 유지시간보다 우선합니다. 취소 효력 전에는 체결될 수 있습니다.')
        st.markdown('**공통 위험과 초기 상태**')
        for key,label in [('max_inventory','절대 재고 한도 (BTC)'),('initial_inventory','초기 재고 (BTC)'),('initial_cash','초기 합성 현금 (USDT)')]:
            edits[key]=st.text_input(label,str(getattr(draft.config,key)))
        for key,label in [('decision_ms','의사결정 간격 (ms)'),('stale_book_ms','허용 호가 나이 (ms)')]:
            edits[key]=st.number_input(label,min_value=1,value=getattr(draft.config,key))
        if strategy=='expression':
            edits['signal_expression']=st.text_area('가격 중심 이동 수식 (출력 tick)',draft.config.signal_expression or '(microprice-mid)/tick')
            params=st.text_area('추가 수식 계수 (JSON)',json.dumps(dict(draft.config.expression_parameters),default=str))
            st.caption('제한된 산술과 abs/min/max/clip. 미래·queue·임의 Python은 입력이 아닙니다.')
        keep_research_policy = draft.research_policy is not None
        if draft.research_policy is not None:
            keep_research_policy = st.checkbox('이전 실행의 연구 위험·헤지 정책 유지', value=True)
            st.warning('이 초안에는 별도 위험·헤지 정책이 있습니다. 유지하면 taker 실행이 가능하고 E0–E4 기본 비교에는 사용할 수 없습니다.')
            with st.expander('이전 연구 정책 근거'): st.json(draft.research_policy)
        save=st.form_submit_button('초안 저장',type='primary')
    if save:
        if not name.strip() or not hypothesis.strip(): raise ValueError('이름과 검증할 가설을 함께 적어 주세요.')
        if strategy=='expression': edits['expression_parameters']=json.loads(params,parse_float=D)
        updated=store.save_draft(replace(draft,config=variant(draft.config,**edits),name=name,hypothesis=hypothesis,
                                        research_policy=draft.research_policy if keep_research_policy else None))
        update_context(store,draft_id=updated.draft_id,draft_revision=updated.revision)
        st.rerun()
    st.caption('폼 입력은 초안 저장 후 반영됩니다. 저장·페이지 이동으로 시뮬레이션을 시작하지 않습니다.')
    with st.expander('저장된 전략의 행동 미리보기 · 가상 입력',expanded=True):
        st.write('실제 시점이나 성과 예측이 아닌 한 번의 정책 설명입니다.')
        q=st.text_input('가상으로 알려진 재고 (BTC)','0')
        flow=st.slider('가상 공격적 거래 흐름',-1.0,1.0,0.0,0.1)
        st.dataframe(synthetic_preview(draft.config,q,str(flow)),hide_index=True)
        st.caption('새 정책 상태의 예시입니다. 기존 주문·예약량·철회 이력은 포함하지 않습니다. 저장된 실제 관측값은 결과의 결정 추적에서 확인합니다.')
    if draft.revision and st.button('저장한 전략으로 실험 구성'): navigate(store,'실험')
