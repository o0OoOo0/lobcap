"""Lazy Streamlit adapter for bounded research, shared with the CLI."""
import json
from pathlib import Path

from lobcap.backtest.research import DEFAULT_STRATEGIES, make_plan, run_research, fingerprint
from lobcap.backtest.presentation import STRATEGIES


def render_research(base,sample_path):
    import streamlit as st
    import pandas as pd

    st.subheader("10분 표본 연구 · 후보 선정 절차")
    st.warning("현재는 시스템 검증용 개발 표본입니다. 모든 결과가 좋아도 paper 후보로 승격하지 않습니다.")
    st.write("전략을 먼저 고정하고 같은 표본에서 기본·α=0·α=1·TOUCH_FILL·비용 증가·복합 스트레스를 실행합니다. TOUCH_FILL은 탐색 순위에서 제외합니다.")
    st.caption("기존 ‘시작·설정’의 편집값은 사용하지 않습니다. 아래에 표시한 고정 연구 설정을 CLI와 동일하게 사용합니다.")
    with st.form("bounded_research"):
        strategies=st.multiselect("비교 전략 (최대 4개)",list(STRATEGIES),default=list(DEFAULT_STRATEGIES),format_func=STRATEGIES.get)
        hypothesis=st.text_area("실험 전에 기록할 질문·가설",value="기본 전략의 손익과 체결 가정 민감도를 동일 표본에서 비교한다.")
        submitted=st.form_submit_button("설정 동결 후 연구 실행",type="primary")
    plan=make_plan(base,strategies,hypothesis)
    st.caption(f"{len(strategies)*6}개 실행 · worker 1개 · 새 연구마다 fresh state · 결과에 따라 가정을 자동 변경하지 않음")
    with st.expander("실제 적용 설정과 판정 기준",expanded=True):
        st.write("자본 1,000 USDT · 주문 0.001 BTC · 최대 재고 0.005 BTC · maker/taker 비용 2/5 bps")
        st.write("표본 손실 10 USDT·낙폭 30 USDT에서 중단. 재고 0.003 BTC부터 취소 확인 후 0.001 BTC 방향으로 헤지합니다.")
        st.caption("일 단위 검증이 아니므로 손실 한도는 이 10분 전체에 적용합니다. 실제 funding·mark price·증거금·최소주문금액 이력은 미검증입니다.")
        st.json(plan)
    if submitted:
        progress=st.progress(0,text="표본 및 설정 검증 중")
        folder=run_research(base,sample_path,strategies,hypothesis,progress=lambda i,n,r:progress.progress(i/n,text=f"{i}/{n} · {STRATEGIES[r['strategy']]} · {r['scenario']}"))
        st.session_state["lab_research_selected"]=folder.name
        st.success("연구 실행 완료. 아래에서 탐색 결과와 paper 승격 차단 사유를 확인하세요.")
    folders={}
    root=base.output_path/"research"
    for file in sorted(root.glob("*/experiment.json"),key=lambda p:p.stat().st_mtime,reverse=True):
        try:
            manifest=json.loads(file.read_text(encoding="utf-8"))
            if manifest["plan"]["base_config"]["sample"]["id"]!=base.sample.sample_id:continue
            if fingerprint(manifest["plan"])!=manifest["plan_sha256"]:raise ValueError("계획 지문 불일치")
            state=json.loads((file.parent/"state.json").read_text(encoding="utf-8"))
            folders[file.parent.name]=(file.parent,manifest,state)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            st.caption(f"불완전한 연구 {file.parent.name}: {exc}")
    if not folders:
        st.info("저장된 연구가 없습니다. 일반 백테스트 실행과 별도로 연구를 제출하세요.")
        return
    names=list(folders)
    selected=st.selectbox("저장된 연구",names,index=names.index(st.session_state.get("lab_research_selected")) if st.session_state.get("lab_research_selected") in names else 0)
    st.session_state["lab_research_selected"]=selected
    path,manifest,state=folders[selected]
    st.caption(f"시스템 실행 상태: {state['status']} · 완료 {state.get('completed',0)}/{state.get('total','?')} · 표본 {manifest['plan']['sample_sha256'][:12]}")
    if state["status"]!="PASS":
        st.warning(state.get("error") or "진행 중입니다. 실패·중단 상태는 성공한 연구로 평가하지 않습니다.")
        st.code(f'python -m lobcap.backtest.research --config "{path / "base_config.yaml"}" --resume "{path}" --strategies '+" ".join(manifest["plan"]["strategies"])+" --hypothesis "+json.dumps(manifest["plan"]["hypothesis"],ensure_ascii=False))
        return
    selection=json.loads((path/"selection.json").read_text(encoding="utf-8"))
    st.error("Paper 후보 0개 · 데이터와 검증 근거 부족")
    st.dataframe(pd.DataFrame(selection["gates"]).rename(columns={"gate":"검증 항목","status":"상태","reason":"승격 보류 사유"}),hide_index=True,width="stretch")
    st.subheader("같은 표본의 탐색 순위")
    frame=pd.DataFrame(selection["exploratory_ranking"])
    frame["strategy"]=frame["strategy"].map(STRATEGIES)
    frame=frame.rename(columns={"strategy":"전략","exploratory_status":"탐색 기준","reasons":"미달 사유",
        "worst_net_pnl":"가장 불리한 시나리오 순손익","worst_drawdown":"최대 낙폭"})
    st.dataframe(frame,hide_index=True,width="stretch")
    st.caption("탐색 PASS도 독립 기간 성능을 의미하지 않습니다. 최소 체결 20건·비진단 시나리오 양수 손익·위험 기준으로만 평가합니다.")
    results=pd.read_csv(path/"results.csv")
    st.dataframe(results,hide_index=True,width="stretch")
    st.caption("capacity 비율은 진단값입니다. 실제 용량·마켓임팩트 한도로 보정된 수치가 아닙니다. 종료 재고를 자동 청산하지 않습니다.")
    for name,label in (("report.html","독립 검토용 HTML"),("results.csv","전체 비교 CSV"),("experiment.json","동결한 실험 설정"),("handoff.json","연구 결과 JSON · paper 승격 불가")):
        st.download_button(label,(path/name).read_bytes(),name,key="research_download_"+name)
