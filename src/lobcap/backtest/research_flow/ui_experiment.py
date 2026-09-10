"""The single explicit workspace submission route."""
from dataclasses import replace
import uuid

from lobcap.config.backtest import variant
from .ui import active_fields, update_context, navigate


def render_experiment(store,draft,base,context):
    import streamlit as st
    from .experiments import build_experiment
    from lobcap.backtest.workspace.jobs import launch_worker
    kinds={'single':'단일 실행','baseline_suite':'E0–E4 · 기본 행동 비교',
           'strategy_compare':'전략 요소 하나 변경','assumption_sensitivity':'체결·비용 가정 하나 변경'}
    kind=st.selectbox('실험 유형',list(kinds),index=list(kinds).index(context.get('experiment_kind','single')),format_func=kinds.get)
    cost=st.radio('비용 프로필',['zero_diagnostic','user_assumed'],index=int(context.get('cost_profile')=='user_assumed'),
                  format_func=lambda x:'0 비용 진단 · 비용 포함 성과 아님' if x=='zero_diagnostic' else '사용자 가정 비용 · 계정 비용 미확인')
    update_context(store,experiment_kind=kind,cost_profile=cost)
    if draft.research_policy is not None:
        st.warning('저장된 연구 위험·헤지 정책이 적용됩니다. taker 비용은 헤지 체결에 사용됩니다. E0–E4는 전략 화면에서 이 정책을 해제한 초안으로 실행하세요.')
    if not draft.revision: st.info('전략 화면에서 이름과 가설을 저장한 뒤 실행하세요.')
    parameter=None
    if kind in {'strategy_compare','assumption_sensitivity'}:
        fields=(['partial_fill_policy','reprice_threshold_ticks','min_hold_ms',*active_fields(draft.config.strategy)]
                if kind=='strategy_compare' else ['alpha','entry_ms','cancel_ms','report_ms','maker_bps','max_trade_participation'])
        parameter=st.selectbox('변경할 한 요소',fields)
    selection=(draft.draft_id,draft.revision,kind,cost,parameter)
    if st.session_state.get('rf_preview_selection')!=selection:
        st.session_state.pop('rf_preview',None)
        st.session_state['rf_preview_selection']=selection
    with st.form('rf_experiment_'+str(selection)):
        question=st.text_area('이 실험이 답할 질문',draft.hypothesis)
        patches={}
        if cost=='user_assumed':
            patches['maker_bps']=st.text_input('가정 maker 수수료 (bps; 음수는 리베이트)',str(draft.config.maker_bps))
            patches['taker_bps']=st.text_input('가정 taker 수수료 (bps; maker-only 미사용)',str(draft.config.taker_bps))
        clocks=['exchange'] if base.sample.normalization_version.endswith('v1') else ['recorded','exchange']
        patches['clock_mode']=st.selectbox('정보 도착 프로필',clocks,index=clocks.index(draft.config.clock_mode),
            format_func=lambda x:'기록 수신 시각 · 미보정' if x=='recorded' else '정보 지연 0 · 진단')
        for key,label in [('entry_ms','주문 도착 지연'),('cancel_ms','취소 도착 지연'),('report_ms','체결·주문 통보 지연')]:
            patches[key]=st.number_input(label+' (ms; 가정)',min_value=0,value=getattr(draft.config,key))
        patches['alpha']=st.text_input('대기열 감소 α (null = TOUCH_FILL 진단)','null' if draft.config.alpha is None else str(draft.config.alpha))
        patches['max_trade_participation']=st.text_input('주문별 누적 참여 예산 비율',str(draft.config.max_trade_participation))
        st.caption('주문별 적격 공개 거래량의 누적 예산입니다. 교체 주문에 이전하지 않으며 실측 시장 충격이 아닙니다.')
        values=None
        if parameter:
            text=st.text_input('비교할 값 (쉼표로 구분; 기준값 포함)',
                'keep_remaining,reset_target' if parameter=='partial_fill_policy' else str(getattr(draft.config,parameter)))
            values=[v.strip() for v in text.split(',')]
        if kind=='baseline_suite':
            st.write('E0 무거래 / E1 최우선 양방향 / E2 부분 체결 잔량 정책 / E3 재고 수량 축소 / E4 흐름 철회')
            st.caption('같은 데이터·비용·체결·초기 상태·위험 한도를 고정합니다.')
        preview=st.form_submit_button('실행 설정 확인',disabled=not draft.revision)
    if preview:
        patches['alpha']=None if patches['alpha'].strip().lower()=='null' else patches['alpha']
        spec=build_experiment(replace(draft,config=variant(draft.config,**patches)),kind=kind,parameter=parameter,
            values=values,cost_profile=cost,sample_path=base.output_path/'samples'/base.sample.sample_id,question=question)
        st.session_state['rf_preview']=spec
    spec=st.session_state.get('rf_preview')
    if spec:
        st.subheader(f"동결 설정 · {len(spec['runs'])}개 순차 실행")
        st.write(spec.get('research_context',{}).get('question',spec.get('hypothesis','')))
        st.caption('폼을 수정했다면 실행 설정 확인을 다시 누르세요. 제출은 아래 동결 설정을 사용합니다. 예상 시간: 현재 조건 측정값 없음. 최대 24개, worker 1개.')
        st.dataframe([{'실험':r.get('research_label',r['scenario_id']),'전략':r['settings']['strategy'],
            'maker bps':r['settings']['maker_bps'],'α':r['settings']['alpha'],
            '주문/취소/통보 ms':f"{r['settings']['entry_ms']}/{r['settings']['cancel_ms']}/{r['settings']['report_ms']}",
            '변경':str(r.get('axis_values',{}))} for r in spec['runs']],hide_index=True)
        with st.expander('전체 동결 설정·근거'): st.json(spec)
        key='rf_submit_'+spec['experiment_hash']
        if key not in st.session_state: st.session_state[key]=uuid.uuid4().hex
        if st.button('확인한 설정으로 실행 제출',type='primary'):
            job=store.submit(spec,st.session_state[key])
            launch_worker(store.root)
            update_context(store,job_id=job)
            st.success('제출했습니다. 같은 제출 키는 중복 작업을 만들지 않습니다.')
    st.subheader('실험 진행')
    if st.button('진행 상황 새로고침'): st.rerun()
    jobs=store.list_jobs()
    if not jobs:
        st.info('제출한 실험이 없습니다.')
        return
    ids=[j['job_id'] for j in jobs]
    selected=st.selectbox('작업 이력',ids,index=ids.index(context['job_id']) if context.get('job_id') in ids else 0,
        format_func=lambda x:next(j['name']+' · '+j['status'] for j in jobs if j['job_id']==x))
    update_context(store,job_id=selected)
    job=store.get_job(selected)
    st.progress(len(job['completed'])/max(job['total'],1),text=f"{job['status']} · 완료 {len(job['completed'])}/{job['total']}")
    if job.get('error'): st.error(job['error'])
    if job['status'] in {'QUEUED','RUNNING'} and st.button('작업 중단 요청'):
        store.request_cancel(selected)
        st.rerun()
    if job['status'] in {'FAILED','CANCELED','INTERRUPTED'} and st.button('무결성 검증 후 미완료 실행 재개'):
        store.resume(selected)
        launch_worker(store.root)
        st.rerun()
    if job['completed'] and st.button('완료한 실행의 결과 보기'):
        navigate(store,'결과',run_id=next(iter(job['completed'].values()))['run_id'])
