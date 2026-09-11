"""Single-screen simulation workbench: editable left, immutable results right."""
from dataclasses import replace
from decimal import Decimal as D,DecimalException
from pathlib import Path
import json
import uuid

from lobcap.config.backtest import variant,INTS
from lobcap.backtest.workspace.contracts import StrategyDraft
from lobcap.backtest.workspace.store import WorkspaceStore
from .experiments import load_context,load_context_draft,save_context,build_experiment,clone_for_next,save_conclusion
from .catalog import index_runs
from .analysis import explain_run,compare_runs,order_drilldown,iter_rows
from .ui import active_fields,LABELS
from .ui_results import compact_timeline,representative_table,utc_display,number_display
from .workbench_model import STRATEGIES,METRICS,economics,reviewed_metrics,policy_examples


def run_label(row):
    c=row['config'];strategy=c.get('strategy','')
    name=STRATEGIES.get(strategy,(row['name'],))[0]
    distance=f" · 반거리 {c.get('half_spread_ticks')} tick" if strategy not in {'join_bbo','inventory_size','flow_withdraw','no_trade'} else ''
    if strategy=='microprice':distance+=f" · 신호 ×{c.get('signal_strength')}"
    if strategy=='inventory_skew':distance+=f" · 이동 {c.get('inventory_skew_ticks')} tick"
    if strategy.startswith('glft'):
        distance=f" · γ {c.get('glft_gamma')}"
        if strategy=='glft_microprice':distance+=f" · 신호 ×{c.get('signal_strength')}"
    return f"{name}{distance} · {c.get('size','?')} BTC · {row['status']} · {row['run_id'][-8:]}"


def render_workbench(base):
    import streamlit as st
    st.markdown('''<style>
      .block-container {padding-top:1.4rem; max-width:1760px;}
      [data-testid="stMetric"] {background:rgba(125,145,170,.08);border:1px solid rgba(125,145,170,.18);padding:12px;border-radius:8px;}
      [data-testid="stMetricValue"] {font-size:1.6rem;}
      </style>''',unsafe_allow_html=True)
    st.title('Market Making · Simulation Lab')
    st.caption('설정 → Simulate → 주문과 손익 확인 → 한 가지 변경. BTCUSDT · 10분 개발 표본 · 실제 주문 전송 없음')
    store=WorkspaceStore(base.output_path/'workspace')
    context=load_context(store)
    draft=load_context_draft(store,context) or StrategyDraft(variant(base,strategy='fixed_spread',partial_fill_policy='keep_remaining'),
        '고정 폭 기준 실험','호가 거리를 넓히면 체결과 재고 손익이 어떻게 달라지는가?')
    if draft.config.sample!=base.sample:
        st.warning('저장 초안의 표본이 현재 준비 표본과 다릅니다. 새 기준 초안을 선택하세요.')
    left,right=st.columns([.37,.63],gap='large')
    with left:
        try:render_editor(store,draft,base)
        except (ValueError,KeyError,OSError,DecimalException,RuntimeError) as exc:st.error(str(exc))
    with right:
        try:render_output(store,base,context)
        except (ValueError,KeyError,OSError,DecimalException,RuntimeError) as exc:st.error(f'결과 근거를 확인해 주세요: {exc}')


def render_editor(store,draft,base):
    import streamlit as st
    st.subheader('01 · 전략 설정')
    saved=store.list_drafts()
    if saved:
        with st.expander('저장한 설정 불러오기'):
            ids=[r['draft_id'] for r in saved]
            chosen=st.selectbox('저장 초안',ids,format_func=lambda i:next(r['name']+' · v'+str(r['revision']) for r in saved if r['draft_id']==i))
            if st.button('이 설정 불러오기'):
                selected=store.load_draft(chosen)
                save_context(store,{'draft_id':selected.draft_id,'draft_revision':selected.revision})
                st.session_state.pop('wb_preview',None);st.rerun()
    strategy=st.selectbox('전략 유형',list(STRATEGIES),index=list(STRATEGIES).index(draft.config.strategy),
        format_func=lambda k:STRATEGIES[k][0],key=f'wb_policy_{draft.draft_id}_{draft.revision}')
    name,idea,trigger,action,risk=STRATEGIES[strategy]
    st.info(idea)
    st.markdown('**언제 판단하나?** '+trigger)
    st.markdown('**어떤 주문을 내나?** '+action)
    unit_note='GLFT에서는 반거리 입력이 최소 폭이며 실제 폭은 추정값에 따라 달라집니다.' if strategy.startswith('glft') else '반거리 0.5 tick는 BBO 참여와 같은 가격이 될 수 있습니다.'
    st.caption(f'가격 단위: 1 tick = {draft.config.instrument.tick} USDT · 최소 주문 단위: {draft.config.instrument.lot} BTC. '+unit_note)
    with st.expander('이 전략의 한계와 공통 실행 규칙'):
        st.write(risk)
        st.write('고정 주기로 판단합니다. 취소 중에도 체결될 수 있고, 기존 주문이 끝나야 다음 주기에 교체합니다. '
                 '오래된 호가·재고 한도·깊이 범위·post-only 조건은 전략보다 우선합니다.')
    selection=(draft.draft_id,draft.revision,strategy)
    if st.session_state.get('wb_selection')!=selection:
        st.session_state.pop('wb_preview',None);st.session_state['wb_selection']=selection
    edits={'strategy':strategy}
    with st.form('wb_editor_'+str(selection)):
        title=st.text_input('실험 이름',draft.name)
        question=st.text_area('이번에 확인할 가설',draft.hypothesis,height=85)
        for field in active_fields(strategy):
            if field in INTS:
                edits[field]=st.number_input(LABELS[field],min_value=1,value=getattr(draft.config,field))
                continue
            label=LABELS[field]
            if strategy.startswith('glft'):
                label={'half_spread_ticks':'최소 호가 반거리 (tick)','max_signal_ticks':'가격 신호 상한 (tick)'}.get(field,label)
            edits[field]=st.text_input(label,str(getattr(draft.config,field)),
                help='반거리는 전체 양방향 폭의 절반입니다. 최종 가격은 tick 반올림과 BBO 제한을 거칩니다.' if field=='half_spread_ticks' else None)
        if strategy=='expression':
            edits['signal_expression']=st.text_area('가격 이동 수식 · 출력 tick',draft.config.signal_expression or '(microprice-mid)/tick',height=130)
            parameters=st.text_area('수식 상수 · JSON',json.dumps(dict(draft.config.expression_parameters),default=str),height=70)
            st.caption('사용 가능: mid, microprice, imbalance, volatility_bps, trade_imbalance, trade_volume, inventory, max_inventory, tick. abs/min/max/clip 및 제한된 산술만 지원합니다.')
        if strategy!='no_trade':
            policy=st.selectbox('부분 체결 후 주문', ['keep_remaining','reset_target'],
                index=int(draft.config.partial_fill_policy=='reset_target'),
                format_func=lambda x:'남은 수량 유지' if x=='keep_remaining' else '취소 후 목표 수량으로 재주문')
            edits['partial_fill_policy']=policy
        with st.expander('위험 한도 · 판단과 주문 주기',expanded=False):
            edits['quote_start_seconds']=st.number_input('공통 거래 시작 지연 (초)',min_value=0,value=draft.config.quote_start_seconds,
                help='준비 시간을 맞춘 비교에 사용합니다. 이 시간 전에도 계수를 추정하지만 신규 호가는 내지 않습니다.')
            edits['max_inventory']=st.text_input('최대 절대 재고 (BTC)',str(draft.config.max_inventory))
            for key,label,minval in [('decision_ms','판단 주기 (ms)',1),('max_age_ms','최대 활성 주문 나이 (ms)',1),
                                      ('min_hold_ms','일반 가격 변경의 최소 유지 (ms)',0),('stale_book_ms','허용 호가 나이 (ms)',1)]:
                edits[key]=st.number_input(label,min_value=minval,value=getattr(draft.config,key))
            edits['reprice_threshold_ticks']=st.text_input('가격 변경 최소 차이 (tick)',str(draft.config.reprice_threshold_ticks))
            edits['initial_cash']=st.text_input('초기 합성 현금 (USDT)',str(draft.config.initial_cash))
            edits['initial_inventory']=st.text_input('초기 재고 (BTC)',str(draft.config.initial_inventory))
        with st.expander('수수료 · 지연 · 체결 가정',expanded=False):
            cost=st.radio('비용 근거',['zero_diagnostic','user_assumed'],format_func=lambda x:'0 비용 진단' if x=='zero_diagnostic' else '사용자 가정 수수료',
                index=int(draft.config.maker_bps!=0 or draft.config.taker_bps!=0))
            edits['maker_bps']=st.text_input('Maker 수수료 (bps)',str(draft.config.maker_bps),help='1 bps = 0.01%. 음수는 리베이트. 계정 수수료를 자동 확인하지 않습니다.')
            edits['taker_bps']=st.text_input('Taker 수수료 (bps) · 기본 maker-only에서는 미사용',str(draft.config.taker_bps))
            clocks=['recorded','exchange'] if base.sample.normalization_version.endswith('v2') else ['exchange']
            edits['clock_mode']=st.selectbox('시장 정보 시계',clocks,index=clocks.index(draft.config.clock_mode),
                format_func=lambda x:'기록 수신 시각 재생' if x=='recorded' else '시장 정보 지연 0 · 진단')
            for key,label in [('entry_ms','주문 도착 지연'),('cancel_ms','취소 도착 지연'),('report_ms','주문·체결 통보 지연')]:
                edits[key]=st.number_input(label+' (ms)',min_value=0,value=getattr(draft.config,key))
            alpha=st.text_input('대기열 감소 α · null은 큐 무시 진단',str(draft.config.alpha) if draft.config.alpha is not None else 'null')
            edits['max_trade_participation']=st.text_input('주문별 누적 거래량 참여 예산',str(draft.config.max_trade_participation))
            st.caption('α는 체결 확률이 아닙니다. 참여 예산도 실측 시장 충격이 아닙니다. 흑자를 만들기 위해 자동으로 변경하지 않습니다.')
        with st.expander('비교 실행 · 한 요소씩'):
            mode=st.selectbox('실행 종류',['single','strategy_compare','assumption_sensitivity','baseline_suite'],
                format_func=lambda x:{'single':'현재 설정 1회','strategy_compare':'전략 설정 한 요소 비교','assumption_sensitivity':'체결·비용 가정 한 요소 비교','baseline_suite':'E0–E4 행동 기준선 5회'}[x])
            fields=['half_spread_ticks','size','inventory_skew_ticks','signal_strength','partial_fill_policy','reprice_threshold_ticks','min_hold_ms',
                    'glft_gamma','glft_window_seconds','glft_min_r2','glft_max_half_ticks',
                    'alpha','entry_ms','cancel_ms','report_ms','maker_bps']
            axis=st.selectbox('비교할 요소',fields)
            values=st.text_input('비교 값 · 쉼표 구분','0.5, 5, 20, 100',help='비교 실행에서만 사용합니다. 단일 실행에는 영향을 주지 않습니다.')
        if draft.research_policy:
            st.warning('불러온 초안에 이전 복합 위험·헤지 정책이 있습니다. 해제 여부를 명시적으로 선택하세요.')
            keep=st.checkbox('기존 복합 정책 유지',value=True)
        else:keep=False
        save=st.form_submit_button('설정 저장')
        preview=st.form_submit_button('Simulate · 실행 설정 확인',type='primary')
    st.caption('입력 수정은 자동 실행되지 않습니다. 오른쪽 결과는 선택한 과거 실행의 고정된 결과입니다.')
    if save or preview:
        edits['alpha']=None if alpha.strip().lower()=='null' else alpha
        if strategy=='expression':edits['expression_parameters']=json.loads(parameters,parse_float=D)
        if cost=='zero_diagnostic':edits.update(maker_bps='0',taker_bps='0')
        config=variant(draft.config,**edits)
        updated=replace(draft,config=config,name=title,hypothesis=question,research_policy=draft.research_policy if keep else None)
        if not title.strip() or not question.strip():raise ValueError('이름과 가설을 입력하세요.')
        if save:
            updated=store.save_draft(updated)
            save_context(store,{'draft_id':updated.draft_id,'draft_revision':updated.revision})
            st.session_state.pop('wb_preview',None);st.rerun()
        if preview:
            if mode=='strategy_compare' and axis not in set(active_fields(strategy))|{'partial_fill_policy','reprice_threshold_ticks','min_hold_ms'}:
                raise ValueError('현재 전략에서 사용하는 설정만 전략 비교 축으로 선택하세요.')
            spec=build_experiment(updated,mode,axis if mode in {'strategy_compare','assumption_sensitivity'} else None,
                [v.strip() for v in values.split(',')] if mode in {'strategy_compare','assumption_sensitivity'} else None,
                cost_profile=cost,sample_path=base.output_path/'samples'/base.sample.sample_id)
            st.session_state['wb_preview']=spec
    spec=st.session_state.get('wb_preview')
    if spec:
        st.success(f"확인할 실행 {len(spec['runs'])}개 · 한 번에 하나씩 실행")
        st.write(spec['hypothesis'])
        st.dataframe([{'전략':STRATEGIES[r['settings']['strategy']][0],'변경':str(r['axis_values']),
            '수량 BTC':r['settings']['size'],'반거리 tick':r['settings']['half_spread_ticks'] if r['settings']['strategy'] not in {'join_bbo','inventory_size','flow_withdraw','no_trade'} else '미사용',
            'Maker bps':r['settings']['maker_bps'],'α':str(r['settings']['alpha'])} for r in spec['runs']],hide_index=True)
        st.caption('폼을 다시 수정했다면 실행 설정 확인을 다시 누르세요. 아래 버튼은 표시된 고정 설정을 제출합니다.')
        from lobcap.backtest.workspace.contracts import config_from_document
        with st.expander('이 설정은 어떤 주문을 내나? · 숫자 예제',expanded=True):
            example=config_from_document(spec['runs'][0]['config_document'])
            st.caption('첫 실행 설정의 가상 상태: bid 80,000 USDT, ask = bid + 1 tick, 변동성 1 bps, 최근 거래 1 BTC, 기존 주문 없음. 실제 표본이나 수익 예측이 아닙니다.')
            if example.strategy.startswith('glft'):
                st.caption('GLFT 숫자 예제만 A=0.1/초/방향, k=0.05/tick, σ=2 tick/√초를 가정합니다. 실행에서는 과거 데이터로 추정합니다. 호가 반거리는 GLFT의 최소 폭으로 사용됩니다.')
            st.dataframe(policy_examples(example),hide_index=True)
        with st.expander('전체 실행 설정'):st.json(spec)
        if st.button('확인한 설정 실행',type='primary'):
            key='wb_submit_'+spec['experiment_hash']
            token=st.session_state.setdefault(key,uuid.uuid4().hex)
            job=store.submit(spec,token)
            from lobcap.backtest.workspace.jobs import launch_worker
            launch_worker(store.root)
            save_context(store,{'job_id':job});st.rerun()


def render_output(store,base,context):
    import streamlit as st
    st.subheader('02 · 결과와 시간 경로')
    jobs=store.list_jobs()
    if jobs:
        current=next((j for j in jobs if j['job_id']==context.get('job_id')),jobs[0])
        with st.expander('실행 현황 · 새로고침',expanded=current['status'] in {'QUEUED','RUNNING'}):
            st.write(f"{current['name']} · {current['status']} · {len(current['completed'])}/{current['total']} 완료")
            if current.get('error'):st.error(current['error'])
            if st.button('실행 상태와 결과 새로고침'):st.rerun()
            if current['status'] in {'RUNNING','QUEUED'} and st.button('실행 중단 요청'):
                store.request_cancel(current['job_id']);st.rerun()
            if current['completed'] and st.button('이 작업의 완료 결과 열기'):
                save_context(store,{'run_id':next(iter(current['completed'].values()))['run_id']});st.rerun()
    rows=index_runs(base.output_path,store.root)
    if not rows:
        st.info('왼쪽에서 전략을 설정하고 실행하세요. 여기에 손익·재고·주문 가격 그래프가 표시됩니다.')
        return
    ids=[r['run_id'] for r in rows]
    selected=st.selectbox('실행 결과 선택',ids,index=ids.index(context['run_id']) if context.get('run_id') in ids else 0,
        format_func=lambda i:run_label(next(r for r in rows if r['run_id']==i)))
    row=next(r for r in rows if r['run_id']==selected)
    save_context(store,{'run_id':selected})
    if not row['rankable']:
        st.warning('정상 완료 또는 근거 일관성을 확인하지 못한 결과입니다. 성과 후보로 사용하지 마세요.')
    if not row['summary'] or not row['output_dir']:
        st.warning('완료된 성과 근거가 없는 실행입니다. 실패·중단 결과를 정상 성과로 표시하지 않습니다.');return
    config,s=row['config'],row['summary']
    st.caption('선택 결과: '+STRATEGIES.get(config.get('strategy'),(config.get('strategy','미기록'),))[0]+
        f" · size {config.get('size')} BTC · maker {config.get('maker_bps')} bps · {config.get('clock_mode')} 시계")
    st.caption(f"주문 / 취소 / 통보 {config.get('entry_ms')} / {config.get('cancel_ms')} / {config.get('report_ms')} ms · α {config.get('alpha')} · 참여 예산 {config.get('max_trade_participation')}")
    st.write('**검증 질문:** '+(row.get('hypothesis') or '이 실행에는 가설 미기록'))
    reviewed=reviewed_metrics(row['output_dir'])
    if not reviewed.get('available'):st.error(reviewed['reason']);return
    if not reviewed['reconciled']:st.error('FIFO·원장 손익이 일치하지 않습니다. 이 결과를 해석하기 전에 근거를 확인하세요.')
    tabs=st.tabs(['성과 · 차트','주문 행동','기준과 비교','정의 · 실행 근거'])
    with tabs[0]:render_charts(row,reviewed)
    with tabs[1]:render_behavior(row,reviewed)
    with tabs[2]:render_comparison(rows,row,context)
    with tabs[3]:
        st.dataframe([{'지표':k,'정의':v} for k,v in METRICS.items()],hide_index=True)
        st.write('순손익 = 체결 시점 midpoint 대비 기여 + 재고 MTM − 수수료. '
                 '다른 분해인 실현+미실현은 수수료 전 손익이며, 위 항목들에 다시 더하지 않습니다.')
        st.write('Sharpe·Fitness·연율 수익률은 10분 표본의 대표 지표로 사용하지 않습니다. '
                 '출발 자본 대비 수익률은 임의의 합성 현금 설정에 의존하며 전략 간 우열의 단독 기준이 아닙니다.')
        st.json({'config':config,'manifest':row['manifest'],'warnings':row['warnings']})
        file=st.selectbox('다운로드 근거',[p.name for p in Path(row['output_dir']).iterdir() if p.is_file()])
        if st.button('다운로드 준비'):
            path=Path(row['output_dir'])/file
            if path.stat().st_size<=64*1024*1024:st.download_button(file,path.read_bytes(),file)
            else:st.info('파일이 64 MiB보다 커 원본 실행 디렉터리에서 확인해야 합니다.')
    with st.expander('다음 실험 · 설정 복제 / 연구 결론'):
        question=st.text_area('다음에 확인할 질문')
        if st.button('선택 결과를 왼쪽 설정으로 복제'):
            draft=store.save_draft(clone_for_next(row['output_dir'],question))
            save_context(store,{'draft_id':draft.draft_id,'draft_revision':draft.revision,'baseline_id':row['run_id']})
            st.session_state.pop('wb_preview',None);st.rerun()
        observation=st.text_area('확인한 근거와 결론')
        outcome=st.selectbox('판단',['no_candidate','revise','needs_more_data'],format_func=lambda x:{'no_candidate':'후보 없음','revise':'수정 후 재검토','needs_more_data':'추가 데이터 필요'}[x])
        if st.button('결론 저장'):
            save_conclusion(store,row['run_id'],outcome,observation,question);st.success('연구 결론을 저장했습니다.')


def render_charts(row,r):
    import streamlit as st
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    s=row['summary']
    cards=st.columns(3)
    for col,key,label in zip(cards,['net_pnl','gross_pnl','max_drawdown'],['순손익 · USDT','수수료 전 손익 · USDT','최대 낙폭 · USDT']):
        col.metric(label,number_display(s.get(key)),help=METRICS[key])
    cards=st.columns(3)
    cards[0].metric('체결 이벤트',str(r['fill_events']))
    cards[1].metric('체결 명목금액 · USDT',number_display(r['turnover'],2),help=METRICS['turnover'])
    cards[2].metric('종료 재고 · BTC',number_display(s.get('ending_inventory')))
    if not r['fill_events']:st.info('체결이 없습니다. 손익 0은 수익성이 검증됐다는 뜻이 아닙니다. 주문 거리·활성화·취소 사유를 확인하세요.')
    elif r['net_pnl']<0:
        if r['gross_pnl']>=0:st.warning('수수료 전에는 이익이지만 비용을 반영하면 손실입니다.')
        else:st.warning('수수료 전에도 손실입니다. 체결 시점 기여와 재고 보유 손익을 나눠 확인하세요.')
    path=Path(row['output_dir'])/'timeseries.csv.gz'
    if path.exists():
        frame=pd.read_csv(path);t=pd.to_datetime(frame['time_ns'],unit='ns',utc=True)
        fig=make_subplots(rows=3,cols=1,shared_xaxes=True,vertical_spacing=.06,row_heights=[.38,.26,.36],
            subplot_titles=('누적 손익과 기여 · USDT','재고 · BTC','시장 midpoint와 전략 목표 가격 · USDT'))
        for key,label,color in [('net_pnl','순손익','#35b6dc'),('gross_pnl','수수료 전','#a0a9bb'),('inventory_mtm','재고 MTM','#e4a658'),('spread_capture','체결 시점 기여','#8d80d6')]:
            fig.add_trace(go.Scatter(x=t,y=frame[key],name=label,line=dict(color=color,width=2)),row=1,col=1)
        fig.add_trace(go.Scatter(x=t,y=frame['inventory'],name='재고',line=dict(color='#c990ea',shape='hv')),row=2,col=1)
        fig.add_hline(y=float(row['config']['max_inventory']),line_dash='dot',row=2,col=1)
        fig.add_hline(y=-float(row['config']['max_inventory']),line_dash='dot',row=2,col=1)
        fig.add_trace(go.Scatter(x=t,y=frame['mid'],name='Midpoint',line=dict(color='#a0a9bb')),row=3,col=1)
        decisions=list(iter_rows(row['output_dir'],'decisions'))
        for key,label,color in [('buy_price','목표 매수','#33b89c'),('sell_price','목표 매도','#ee7a77')]:
            points=[d for d in decisions if d.get(key) is not None]
            fig.add_trace(go.Scatter(x=pd.to_datetime([p['time_ns'] for p in points],unit='ns',utc=True),y=[float(p[key]) for p in points],
                name=label,mode='markers',marker=dict(size=3,color=color)),row=3,col=1)
        for side,color,symbol in [('buy','#33b89c','triangle-up'),('sell','#ee7a77','triangle-down')]:
            fills=[f for f in iter_rows(row['output_dir'],'fills') if f['side']==side]
            fig.add_trace(go.Scatter(x=pd.to_datetime([f['time_ns'] for f in fills],unit='ns',utc=True),y=[float(f['price']) for f in fills],
                mode='markers',name='매수 체결' if side=='buy' else '매도 체결',marker=dict(symbol=symbol,size=8,color=color)),row=3,col=1)
        fig.update_layout(height=660,margin=dict(l=10,r=10,t=45,b=10),hovermode='x unified',legend=dict(orientation='h',y=-.08))
        fig.update_xaxes(title_text='UTC 시각',row=3,col=1)
        st.plotly_chart(fig,width='stretch')
        st.caption('손익·재고는 1초 표본, 목표 호가는 결정 시점의 의도입니다. 목표 호가를 실제 활성 주문으로 해석하지 마세요. 체결 마커는 실제 시뮬레이터 체결 기록입니다.')
    st.markdown('**손익의 다른 분해 · 수수료 전**')
    st.dataframe([{'항목':'실현 매매손익 (FIFO)','USDT':number_display(r['realized_gross'])},
                  {'항목':'종료 미실현 손익','USDT':number_display(r['unrealized_gross'])},
                  {'항목':'수수료·리베이트','USDT':number_display(r['fees'])}],hide_index=True)
    st.caption('실현 + 미실현 − 수수료 = 순손익. 초기 재고는 표본 시작 midpoint를 원가 기준으로 사용합니다.')
    st.write('동일 체결 경로의 손익분기 평균 수수료: **'+number_display(r['break_even_fee_bps'],4)+' bps**')
    st.caption(METRICS['break_even_fee'])
    c=row['config'];units=economics(r['reference_mid'],c['instrument']['tick'],c.get('half_spread_ticks',0),c['maker_bps'])
    with st.expander('가격 단위와 비용 크기 이해하기'):
        st.write(f"시작 midpoint {r['reference_mid']} USDT에서 1 tick = {units['tick_usdt']} USDT ≈ {number_display(units['tick_bps'],5)} bps")
        st.write(f"설정 반거리 {c.get('half_spread_ticks')} tick = {units['half_usdt']} USDT ≈ {number_display(units['half_bps'],5)} bps (BBO 직접 참여 정책에서는 미사용)")
        st.write(f"동일 가격·동일 수량 왕복 Maker 비용 ≈ {units['roundtrip_fee_bps']} bps. 이는 한 tick 이익보다 클 수 있습니다.")


def render_behavior(row,r):
    import streamlit as st
    s=row['summary'];e=s.get('execution',{})
    if row['config'].get('strategy','').startswith('glft'):render_glft(row)
    st.dataframe([{'지표':'주문 체결률','값':number_display(s.get('fill_rate'),2,True)},
        {'지표':'수량 체결률','값':number_display(s.get('quantity_fill_rate'),2,True)},
        {'지표':'부분 체결로 종료된 주문 비율','값':number_display(s.get('partial_fill_rate'),2,True)},
        {'지표':'판단 횟수','값':str(r['decision_count'])},
        *[{'지표':k,'값':str(v)} for k,v in e.items() if k in {'submitted','activated','rejected','canceled','orders_with_fill','full_orders','partial_orders'}]],hide_index=True)
    st.write('**설정 → 최종 주문 사이에서 무엇이 바뀌었나**')
    st.dataframe([{'단계':{'tick_rounding':'tick·lot 반올림','price_policy':'최우선 호가 제한','risk':'위험 제어'}.get(k,k),'변경된 판단 횟수':v} for k,v in r['stage_changes'].items()],hide_index=True)
    st.caption('반올림·가격 제한·위험 제어의 적용 횟수입니다. 두 설정이 같은 전략 행동인지 판단하려면 기준 비교의 첫 차이를 확인하세요.')
    explanation=explain_run(row['output_dir'])
    st.dataframe(representative_table(explanation['representative_orders']),hide_index=True)
    ids=list(dict.fromkeys(x['order_id'] for x in iter_rows(row['output_dir'],'orders') if x.get('order_id')))
    if ids:
        selected=st.selectbox('주문 한 건 추적',ids)
        details=order_drilldown(row['output_dir'],selected)
        st.dataframe(compact_timeline(details['timeline'],selected),hide_index=True)
        if details['truncated']:st.info('화면은 앞 200행입니다. 원본 파일에 전체 기록이 있습니다.')
        with st.expander('원시 주문 추적'):st.json(details)
    st.markdown('**체결 후 가격 이동 · 손익에 다시 더하지 않음**')
    st.dataframe([{'기간 ms':h,'유효 / 전체':f"{m['valid']} / {m['observations']}",
        '관측률':number_display(m['coverage'],2,True),'체결가 기준 USDT/BTC':number_display(m['volume_weighted_markout']),
        '체결 시 midpoint 이후 이동 USDT/BTC':number_display(m['volume_weighted_midpoint_drift'])} for h,m in s.get('markouts',{}).items()],hide_index=True)
    st.caption('midpoint 이후 이동이 음수면 해당 정의에서 불리한 체결입니다. 250 ms보다 오래된 midpoint와 표본 밖 목표는 누락입니다.')


def render_glft(row):
    import streamlit as st
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    from collections import Counter
    records=[dict(t['policy_state']['glft'],decision_time=t['time_ns']) for t in iter_rows(row['output_dir'],'decision_traces')
             if t.get('policy_state',{}).get('glft')]
    st.markdown('**GLFT: 추정 → 폭 → 재고 조정 → 주문**')
    st.caption('A는 방향별 초당 통과 거래 강도, k는 tick당 감소율, σ는 tick/√초입니다. 공개 거래의 거리별 통과 횟수를 추정하며 실제 큐 체결률을 측정한 값이 아닙니다.')
    labels={'ready':'추정 준비 완료','warmup_or_stale':'유효 관측 부족','warmup':'준비 중',
        'stale_estimate':'추정값이 오래됨','insufficient_crossings':'거리별 거래 관측 부족',
        'unstable_intensity_fit':'거래 강도 곡선 불안정','width_limit':'허용 호가 폭 초과'}
    st.dataframe([{'추정 상태':labels.get(k,k),'판단 횟수':v} for k,v in Counter(r['status'] for r in records).items()],hide_index=True)
    if not records:return
    frame=pd.DataFrame(records)
    fig=make_subplots(rows=2,cols=1,shared_xaxes=True,subplot_titles=('계산된 폭과 재고 이동 · tick','추정 유효 관측 · 초'))
    for key,label in [('raw_half_ticks','GLFT 반거리'),('half_ticks','비용·최소 폭 적용'),('inventory_shift_ticks','재고 이동'),('signal_ticks','신호 이동')]:
        if key in frame:fig.add_trace(go.Scatter(x=pd.to_datetime(frame.decision_time,unit='ns',utc=True),y=pd.to_numeric(frame[key]),name=label),row=1,col=1)
    fig.add_trace(go.Scatter(x=pd.to_datetime(frame.decision_time,unit='ns',utc=True),y=frame.get('valid_seconds'),name='유효 관측'),row=2,col=1)
    fig.update_layout(height=430,legend=dict(orientation='h'),margin=dict(t=35,b=10))
    st.plotly_chart(fig,width='stretch')
    st.caption('이 폭은 BBO·위험 제한 적용 전 계산값입니다. 준비 완료 상태에서도 공통 시작 지연이나 위험 제한으로 주문이 없을 수 있습니다.')
    st.dataframe([{k:number_display(r[k],4) if k in {'A','k','r2','sigma_ticks_sqrt_second','half_ticks','inventory_shift_ticks'} else r.get(k)
        for k in ('status','valid_seconds','A','k','r2','sigma_ticks_sqrt_second','half_ticks','inventory_shift_ticks') if k in r} for r in records[-1:]],hide_index=True)
    st.caption('추정 불안정·관측 부족·최대 폭 초과는 대체 계수를 넣지 않고 호가를 중단합니다. 원본 decision_traces에 모든 추정과 거리별 통과 횟수가 있습니다.')


def render_comparison(rows,row,context):
    import streamlit as st
    eligible=[r for r in rows if r['rankable'] and r['run_id']!=row['run_id']]
    if not eligible:st.info('같은 표본의 완료 결과가 하나 더 필요합니다.');return
    ids=[r['run_id'] for r in eligible]
    key=st.selectbox('비교 기준',ids,index=ids.index(context['baseline_id']) if context.get('baseline_id') in ids else 0,
        format_func=lambda i:run_label(next(r for r in eligible if r['run_id']==i)))
    baseline=next(r for r in eligible if r['run_id']==key)
    sensitivity=st.checkbox('체결·비용 가정 민감도로 비교')
    result=compare_runs(baseline['output_dir'],row['output_dir'],allow_scenario_change=sensitivity)
    if not result['guard']['comparable']:
        st.warning('직접 비교 조건이 맞지 않습니다.');st.write(result['guard']['reasons']);return
    changes=[{'설정':k,'기준':str(baseline['config'].get(k)),'현재 결과':str(row['config'].get(k))} for k in row['config'] if row['config'].get(k)!=baseline['config'].get(k)]
    st.dataframe(changes,hide_index=True)
    st.dataframe(result['contribution_rows'],hide_index=True)
    relationship=result.get('behavior_relationship',{})
    if relationship.get('actions_identical') and relationship.get('desired_identical'):st.info('두 설정은 이 표본에서 같은 목표 호가와 주문 행동을 만들었습니다.')
    if relationship.get('partial_fill_policy_exercised') is False:st.info('1 lot 주문에는 부분 체결이 없어 잔량 정책 변경이 작동하지 않았습니다.')
    for k,label in [('first_desired_divergence','목표 호가의 첫 차이'),('first_action_divergence','주문 행동의 첫 차이')]:
        point=result[k]
        st.write(label+': '+(utc_display(point['time_ns']) if point else '차이 없음 또는 추적 미기록'))
        if point:
            with st.expander(label+'의 근거'):st.json(point)
    if result['guard'].get('code_compatibility'):st.caption('Git 차이를 확인한 표시 코드 호환성 적용. 실행·회계 코드는 동일합니다.')
