"""Unified legacy/current evidence, explanation, and baseline comparison."""
from decimal import Decimal
from pathlib import Path
import json

from .ui import update_context, navigate, active_fields


def _table(rows):
    return [{k:json.dumps(v,ensure_ascii=False,default=str) if isinstance(v,(dict,list,tuple)) else v for k,v in r.items()} for r in rows]


def _label(row):
    return f"{row['name']} · {row['status']} · {'이전 실행' if row['source']=='legacy' else '연구 실험'} · {row['run_id'][-8:]}"


def _select(rows,label,wanted=None):
    import streamlit as st
    ids=[r['run_id'] for r in rows]
    selected=st.selectbox(label,ids,index=ids.index(wanted) if wanted in ids else 0,
                          format_func=lambda x:_label(next(r for r in rows if r['run_id']==x)))
    return next(r for r in rows if r['run_id']==selected)


def _clone(store,row):
    import streamlit as st
    from .experiments import clone_for_next
    fields=['변경 없음','partial_fill_policy','reprice_threshold_ticks','min_hold_ms',*active_fields(row['config'].get('strategy','join_bbo'))]
    field=st.selectbox('복제하며 바꿀 한 요소',fields)
    with st.form('rf_clone_'+row['run_id']):
        question=st.text_area('다음 실험에서 확인할 질문','')
        change=None
        if field!='변경 없음':
            before=row['config'].get(field,'미기록')
            after=st.text_input('새 값',str(before))
            st.write(f'기준 {field} = {before} → 입력한 새 값만 변경합니다.')
            change={field:after}
        st.caption('원본 설정을 복제하고 최신 정책 계약으로 새 초안을 만듭니다. 기존 결과 재현이나 자동 실행이 아닙니다.')
        clone=st.form_submit_button('다음 실험용 전략 복제')
    if clone:
        if not question.strip(): raise ValueError('다음 질문을 적어 주세요.')
        draft=store.save_draft(clone_for_next(row['output_dir'],question,change=change))
        navigate(store,'전략',draft_id=draft.draft_id,draft_revision=draft.revision,baseline_id=row['run_id'])


def render_results(store,base,context):
    import streamlit as st
    from .catalog import index_runs
    from .analysis import explain_run,order_drilldown,iter_rows,reference_from_run
    from .experiments import save_conclusion
    if st.button('실행 이력 새로고침'): st.rerun()
    rows=index_runs(base.output_path,store.root)
    if not rows:
        st.info('저장된 실행이 없습니다. 전략을 저장하고 실험을 제출하세요.')
        return
    row=_select(rows,'실행 이력 · 이전 실행 포함',context.get('run_id'))
    update_context(store,run_id=row['run_id'])
    st.write('검증한 가설: '+(row.get('hypothesis') or '이전 실행에 가설 미기록'))
    config=row['config']
    st.caption(f"maker {config.get('maker_bps','미기록')} bps · taker {config.get('taker_bps','미기록')} bps · 정보 시계 {config.get('clock_mode','미기록')} · 체결 규칙 {row.get('execution_contract_version','미기록')}")
    if not row['rankable']: st.warning('미완료·실패 또는 근거 부족 실행입니다. 정상 성과 비교에서 제외합니다.')
    if not row.get('output_dir') or not row['summary']:
        st.info('이 실행의 완성된 성과 기록은 아직 없습니다.')
        return
    explanation=explain_run(row['output_dir'])
    summary=explanation['summary']
    st.subheader('손익을 구성한 기록')
    cards=st.columns(4)
    for c,key,label in zip(cards,['net_pnl','spread_capture','inventory_mtm','fees'],['순손익 USDT','체결 시점 기여 USDT','재고 가격변동 USDT','수수료 USDT']):
        c.metric(label,str(summary.get(key,'미기록')))
    st.caption('체결 시점 기여는 당시 midpoint와 체결 가격의 차이입니다. 왕복 스프레드 이익과 다르며 음수일 수 있습니다. markout을 손익에 다시 합산하지 않습니다.')
    for finding in explanation['findings']: st.write(finding)
    st.caption('주요 음의 기여는 원장 분해입니다. 전략 또는 모델의 인과적 실패를 단정하지 않습니다.')
    st.dataframe(_table(explanation['contributions']),hide_index=True)
    if explanation['trace_status']=='legacy_missing': st.info('이전 실행: 정책 단계 추적 미기록. 당시 의도를 새로 추정해 채우지 않습니다.')
    st.write(f"종료 재고 {summary.get('ending_inventory','미기록')} BTC · 최대 재고 {summary.get('max_abs_inventory','미기록')} BTC · 최대 낙폭 {summary.get('max_drawdown','미기록')} USDT")
    exposure=explanation.get('inventory_exposure',{})
    st.write(f"시간가중 절대 재고 {exposure.get('time_weighted_absolute_inventory','미기록')} BTC · 한도 80% 이상 체류 비율 {exposure.get('near_limit_time_fraction','미기록')}")
    st.caption('샤프: 10분 표본에서는 정보 부족으로 제공하지 않습니다. 체결 이벤트 수는 독립 표본 수가 아닙니다.')
    with st.expander('가격 경로와 markout'):
        import pandas as pd
        series=Path(row['output_dir'])/'timeseries.csv.gz'
        if series.exists():
            frame=pd.read_csv(series)
            frame['시각']=pd.to_datetime(frame['time_ns'],unit='ns',utc=True)
            st.line_chart(frame.set_index('시각')[['net_pnl','gross_pnl']])
            st.line_chart(frame.set_index('시각')[['inventory']])
        st.dataframe(_table([{'기간 ms':h,**v} for h,v in summary.get('markouts',{}).items()]),hide_index=True)
        st.caption('markout 단위 USDT/BTC. coverage는 유효 관측 비율이며 미신선·표본 종료는 누락으로 남습니다.')
    with st.expander('종료 재고 정리 참고 평가'):
        if st.button('종료 시점 깊이로 참고 평가 계산'):
            sample_id=config.get('sample',{}).get('sample_id',base.sample.sample_id)
            result=reference_from_run(row['output_dir'],base.output_path/'samples'/sample_id)
            st.dataframe(_table([result]),hide_index=True)
        st.caption('원래 체결·원장을 바꾸지 않는 즉시 정리 참고값입니다. 취소 경쟁과 주문 도착을 재생한 실제 청산 경로가 아닙니다.')
    st.subheader('대표 체결과 주문 한 건 추적')
    st.caption('검토 사례이며 전체 성과의 대표성 보장이 아닙니다. 미체결·거절 주문도 아래 목록에서 선택할 수 있습니다.')
    st.dataframe(_table(explanation['representative_orders']),hide_index=True)
    order_ids=list(dict.fromkeys(r['order_id'] for r in iter_rows(row['output_dir'],'orders') if r.get('order_id')))
    if order_ids:
        order_id=st.selectbox('추적할 주문',order_ids)
        detail=order_drilldown(row['output_dir'],order_id)
        st.caption('거래소 상태와 modeled queue는 사후 진단 전용입니다. 전략이 알던 값은 client/decision 기록으로 구분합니다.')
        st.dataframe(_table(detail['timeline']),hide_index=True)
        if detail['truncated']: st.warning('화면 행 수 제한입니다. 전체 기록은 실행 근거에서 내려받으세요.')
        if detail['missing']: st.info('미기록: '+', '.join(detail['missing']))
    with st.expander('저장된 결정 시점 · 관측 입력과 정책 단계'):
        decisions=[]
        for decision in iter_rows(row['output_dir'],'decisions'):
            decisions.append(decision)
            if len(decisions)>=200: break
        if decisions:
            index=st.selectbox('저장된 관측 시각',range(len(decisions)),format_func=lambda i:str(decisions[i]['time_ns']))
            decision=decisions[index]
            fields=('time_ns','bid','ask','mid','microprice','imbalance','volatility_bps','inventory','trade_imbalance','trade_volume','observed_book_exchange_ns','observed_book_receive_ns')
            st.dataframe(_table([{k:decision[k] for k in fields if k in decision}]),hide_index=True)
            trace=next((r for r in iter_rows(row['output_dir'],'decision_traces') if r['time_ns']==decision['time_ns']),None)
            if trace:
                stages=[]
                for key,label in [('raw_target','원래 의도'),('tick_rounded_target','tick·lot 반올림'),('policy_limited_target','가격 정책 제한'),('risk_limited_target','위험 제한')]:
                    target=trace.get(key)
                    for side in ('bid','ask'):
                        quote=target.get(side) if target else None
                        stages.append({'단계':label,'방향':'매수' if side=='bid' else '매도',
                                       '가격':quote.get('price') if quote else None,
                                       '수량':quote.get('quantity') if quote else None,
                                       '사유':target.get('reason') if target else '미기록'})
                st.dataframe(stages,hide_index=True)
                st.dataframe(_table(trace.get('actions',[])),hide_index=True)
                st.write('단계에서 호가 변경 여부: '+', '.join(f'{k}={v if v is not None else "미기록"}' for k,v in trace.get('stage_changes',{}).items()))
                st.caption('같은 결정의 가격·수량 제한과 실제 주문 행동입니다. 추정 대기열은 관측 입력에 포함하지 않습니다.')
            else: st.info('이 결정의 단계별 정책 추적 미기록')
            st.caption('당시 전달된 입력의 기록입니다. 새 전략 전체의 재고 경로가 동일하다는 뜻이 아닙니다. 처음 200개 결정만 화면에 표시합니다.')
        else: st.info('저장된 의사결정 입력 미기록')
    conclusion_files=sorted((store.root/'conclusions'/row['run_id']).glob('*.json'))
    if conclusion_files:
        st.subheader('저장한 연구 결론')
        for file in conclusion_files:
            saved=json.loads(file.read_text(encoding='utf-8'))
            st.write({'no_candidate':'채택할 후보 없음','revise':'행동 수정 후 재검토','needs_more_data':'추가 데이터 필요'}.get(saved['outcome'],saved['outcome']))
            st.write(saved['observation'])
            if saved.get('next_question'): st.caption('후속 질문: '+saved['next_question'])
    with st.form('rf_conclusion_'+row['run_id']):
        st.subheader('연구 결론')
        outcome=st.selectbox('현재 판단',['no_candidate','revise','needs_more_data'],
            format_func=lambda x:{'no_candidate':'채택할 후보 없음','revise':'행동 하나 수정 후 재검토','needs_more_data':'추가 데이터 필요'}[x])
        observation=st.text_area('관찰한 사실과 근거')
        next_question=st.text_area('후속 질문')
        save=st.form_submit_button('연구 결론 저장')
    if save:
        save_conclusion(store,row['run_id'],outcome,observation,next_question=next_question)
        st.success('결론을 저장했습니다. 페이퍼 후보 자동 승인이 아닙니다.')
    _clone(store,row)
    if st.button('이 실행을 기준으로 비교'): navigate(store,'비교',baseline_id=row['run_id'])
    with st.expander('실행 근거 · 원본 다운로드'):
        st.json({'manifest':row['manifest'],'warnings':row['warnings'],'identity':explanation['identity']})
        names=[name for name in ('config.yaml','run_manifest.json','diagnostics.json','decision_traces.jsonl.gz','decisions.jsonl.gz','execution.jsonl.gz','orders.jsonl.gz','fills.jsonl.gz','ledger.jsonl.gz','markouts.csv.gz') if (Path(row['output_dir'])/name).exists()]
        if names:
            name=st.selectbox('내려받을 근거 파일',names)
            path=Path(row['output_dir'])/name
            st.caption(f'{path.stat().st_size/1024/1024:.2f} MiB')
            if st.button('선택한 파일 다운로드 준비'):
                if path.stat().st_size>64*1024*1024: st.warning('64 MiB를 넘는 파일은 원본 실행 디렉터리에서 확인해 주세요: '+str(path))
                else: st.download_button(name,path.read_bytes(),name,key='rf_download_'+row['run_id']+name)


def render_comparison(store,base,context):
    import streamlit as st
    from .catalog import index_runs,comparison_guard
    from .analysis import compare_runs
    rows=[r for r in index_runs(base.output_path,store.root) if r['rankable']]
    if len(rows)<2:
        st.info('비교할 완료 실행이 두 개 이상 필요합니다. 실패·중단 기록은 결과 이력에서 확인하세요.')
        return
    left=_select(rows,'기준 실행',context.get('baseline_id'))
    update_context(store,baseline_id=left['run_id'])
    right=_select([r for r in rows if r['run_id']!=left['run_id']],'변경 실행',context.get('run_id'))
    update_context(store,run_id=right['run_id'])
    sensitivity=st.radio('비교 목적',['전략 행동 비교','체결·비용 가정 민감도'])=='체결·비용 가정 민감도'
    guard=comparison_guard(left,right,allow_scenario_change=sensitivity)
    diffs=[{'설정':k,'기준':str(left['config'].get(k)),'변경':str(right['config'].get(k))}
           for k in sorted(set(left['config'])|set(right['config'])) if left['config'].get(k)!=right['config'].get(k)]
    st.subheader('무엇을 바꿨는가')
    st.dataframe(diffs,hide_index=True)
    if len(diffs)>1: st.caption('여러 값이 달라졌습니다. 각 변화의 효과를 인과적으로 분해한 비교가 아닙니다.')
    if not guard['comparable']:
        st.warning('같은 조건의 직접 비교로 묶을 수 없습니다.')
        for reason in guard['reasons']: st.write(reason)
        return
    result=compare_runs(left['output_dir'],right['output_dir'],allow_scenario_change=sensitivity)
    st.subheader('기준 대비 손익과 행동 변화')
    st.dataframe(_table(result['contribution_rows']),hide_index=True)
    for warning in result['warnings']: st.info(warning)
    for key,title in [('first_desired_divergence','희망 호가의 첫 차이'),('first_action_divergence','주문 행동의 첫 차이')]:
        point=result[key]
        st.write(title+(': '+str(point['time_ns'])+' ns' if point else ': 기록 범위에서 차이 없음 또는 미기록'))
        if point:
            with st.expander(title+' 근거'): st.dataframe(_table([{'방향':'기준','기록':point['left']},{'방향':'변경','기록':point['right']}]),hide_index=True)
    st.dataframe([{'지표':key,'기준':str(left['summary'].get(key,'미기록')),'변경':str(right['summary'].get(key,'미기록'))}
                  for key in ('max_abs_inventory','max_drawdown','mean_inventory','ending_inventory','replacement_downtime_seconds','quote_availability')],hide_index=True)
    st.caption('호가 경로가 같으면 중복 행동입니다. 다르더라도 독립 전략이거나 새로운 표본은 아닙니다. 가정 시나리오는 신뢰구간·손익 상하한이 아닙니다.')
    if st.button('변경 실행의 주문 근거 열기'): navigate(store,'결과',run_id=right['run_id'])
