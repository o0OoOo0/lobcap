"""Draft recovery and mutable research notes, separate from execution evidence."""
import json


def render_history(store, base):
    import streamlit as st
    import pandas as pd
    from lobcap.backtest.workspace.contracts import StrategyDraft

    st.subheader("초안 · 실행 이력")
    drafts = store.list_drafts()
    if drafts:
        st.dataframe(pd.DataFrame([{k:v for k,v in d.items() if k not in ("config_document","research_policy")} for d in drafts]), hide_index=True)
        lookup = {d["draft_id"]: d for d in drafts}
        draft_id = st.selectbox("불러올 저장 초안", list(lookup), format_func=lambda x:f"{lookup[x]['name']} · r{lookup[x]['revision']} · {x[:8]}")
        if st.button("저장 초안 불러오기"):
            loaded = store.load_draft(draft_id)
            if loaded.config.sample != base.sample:
                st.error("다른 표본의 초안입니다. 먼저 앱의 표본을 변경해 주세요.")
            else:
                st.session_state["ws_draft"] = loaded
                st.session_state.pop("ws_preview", None)
                st.session_state.update(ws_axes_text="{}", ws_scenarios_text='[{"name":"baseline","changes":{}}]', ws_axes_mode="간편 두 축")
                st.success("전략 편집에서 저장한 값을 이어서 수정할 수 있습니다.")
                st.rerun()
        with st.expander("선택한 초안의 저장 설정"):
            st.json(lookup[draft_id])
    else:
        st.info("저장된 초안이 없습니다.")
    if st.button("현재 표본의 새 초안 만들기"):
        st.session_state["ws_draft"] = StrategyDraft(base, "새 전략 실험")
        st.session_state.pop("ws_preview", None)
        st.session_state.update(ws_axes_text="{}", ws_scenarios_text='[{"name":"baseline","changes":{}}]', ws_axes_mode="간편 두 축")
        st.success("새 초안을 준비했습니다.")
        st.rerun()
    jobs = store.list_jobs()
    if jobs:
        table = [{"작업":j["job_id"],"이름":j["name"],"상태":j["status"],"완료":len(j["completed"]),"전체":j["total"],"작성":j["created_at"]} for j in jobs]
        st.dataframe(pd.DataFrame(table), hide_index=True)
        st.caption(f"이 작업 공간의 누적 제출 {len(jobs)}회. 같은 10분 표본의 재사용은 새로운 독립 관측을 추가하지 않습니다.")
    run_id = st.session_state.get("ws_review_variant")
    if run_id:
        review = store.get_review(run_id)
        st.subheader("선택한 실행의 연구 기록")
        st.code(run_id)
        with st.form("workspace_review_" + run_id):
            note = st.text_area("관찰 · 다음 실험 아이디어", value=review["note"])
            favorite = st.checkbox("즐겨찾기", value=review["favorite"])
            candidate = st.checkbox("탐색 후보로 보관", value=review["candidate"])
            save = st.form_submit_button("연구 기록 저장")
        if save:
            store.save_review(run_id, note=note, favorite=favorite, candidate=candidate)
            st.success("연구 기록을 저장했습니다. 원본 실행 결과는 변경하지 않았습니다.")
        st.caption("Paper 적격성: INSUFFICIENT_EVIDENCE. 탐색 후보 저장과 별개이며, 실제 주문이나 paper 실행을 시작하지 않습니다.")
    with st.expander("기존 실행·연구 찾아보기"):
        st.write("기존 ‘실행 결과’, ‘실행 비교’, ‘10분 연구·후보 검토’ 메뉴에서 이전 아티팩트를 그대로 열 수 있습니다. 새 실험으로 다시 기록하거나 결과를 덮어쓰지 않습니다.")
