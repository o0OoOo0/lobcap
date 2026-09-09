"""Explicit submission and durable job controls; rerenders never submit work."""
import json
import uuid


def render_experiments(store):
    import streamlit as st
    import pandas as pd
    from lobcap.backtest.workspace.jobs import launch_worker

    spec = st.session_state.get("ws_preview")
    if spec:
        st.subheader(f"제출 전 확인 · {len(spec['runs'])}개 실행")
        st.write(spec["name"])
        st.caption(f"표본 {spec['sample_sha256'][:16]} · 코드 {spec['code_sha256'][:16]} · 초안 revision {spec['draft_revision']}")
        st.dataframe(pd.DataFrame([{"variant":r["variant_id"][:12], "scenario":r["scenario_id"], **r["settings"]} for r in spec["runs"]]), hide_index=True)
        if any(spec.get("duplicates_removed", {}).values()):
            st.info("정규화 후 중복 제거: " + json.dumps(spec["duplicates_removed"], ensure_ascii=False))
        with st.expander("전체 적용값 · 값의 출처 · 위험 정책"):
            st.json(spec)
        st.caption("한 번에 worker 1개로 순차 실행합니다. 실행 시간은 설정과 시스템 부하에 따라 달라집니다. 화면을 이동하거나 닫아도 제출한 작업은 유지됩니다.")
        key = "ws_submission_" + spec["experiment_hash"]
        if key not in st.session_state:
            st.session_state[key] = uuid.uuid4().hex
        if st.button("이 설정으로 실행 제출", type="primary"):
            try:
                job_id = store.submit(spec, submission_key=st.session_state[key])
                launch_worker(store.root)
                st.session_state["ws_selected_job"] = job_id
                st.success(f"제출했습니다: {job_id}. 같은 제출 버튼을 다시 눌러도 작업이 중복 생성되지 않습니다.")
            except (ValueError, OSError, RuntimeError) as exc:
                st.error(f"제출 실패: {exc}")
        if st.button("같은 설정의 새 반복 실행 준비"):
            st.session_state[key] = uuid.uuid4().hex
            st.info("새 제출 키를 준비했습니다. 실행 제출을 눌러 반복 실행하세요.")
    else:
        st.info("전략 편집에서 초안을 저장하면 실제 실행 설정이 여기에 표시됩니다.")
    st.divider()
    st.subheader("저장된 작업 진행")
    if st.button("진행 상황 새로고침"):
        st.rerun()
    jobs = store.list_jobs()
    if not jobs:
        st.info("아직 제출한 작업이 없습니다.")
        return
    ids = [j["job_id"] for j in jobs]
    selected = st.session_state.get("ws_selected_job")
    selected = st.selectbox("진행 작업", ids, index=ids.index(selected) if selected in ids else 0)
    st.session_state["ws_selected_job"] = selected
    state = store.get_job(selected)
    st.write(f"상태: **{state['status']}**")
    st.progress(len(state['completed']) / max(state['total'], 1), text=f"완료 {len(state['completed'])}/{state['total']} · 시도 {state['attempt']}")
    st.json(state, expanded=False)
    if state["status"] in ("QUEUED", "RUNNING"):
        st.caption("취소는 안전한 이벤트 경계에서 적용됩니다. 강제 청산을 만들지 않으며 중단 결과는 완성된 10분 실행과 구분합니다.")
        if st.button("작업 중단 요청", disabled=state.get("cancel_requested", False)):
            store.request_cancel(selected)
            st.rerun()
    elif state["status"] in ("FAILED", "CANCELED", "INTERRUPTED"):
        if st.button("검증 후 미완료 실행 재개"):
            try:
                store.resume(selected)
                launch_worker(store.root)
                st.rerun()
            except (ValueError, OSError, RuntimeError) as exc:
                st.error(f"재개 불가: {exc}. 변경된 조건으로 새 실험을 만들어 주세요.")
    if state["status"] == "RUNNING" and st.button("종료된 worker 상태 확인"):
        from lobcap.backtest.workspace.jobs import recover_interrupted
        recover_interrupted(store.root)
        st.rerun()
