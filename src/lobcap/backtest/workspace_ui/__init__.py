"""Research workspace UI; importing it does not require web dependencies."""


def render_workspace(base, sample_path):
    import streamlit as st
    from lobcap.backtest.workspace.contracts import StrategyDraft
    from lobcap.backtest.workspace.store import WorkspaceStore
    from .editor import render_editor
    from .experiments import render_experiments
    from .results import render_results
    from .history import render_history

    store = WorkspaceStore(base.output_path / "workspace")
    st.subheader("전략 연구 작업 공간")
    st.write("아이디어 → 설정·수식 → 시뮬레이션 → 비교 → 복제. 같은 10분 표본에서 가설을 반복 탐색합니다.")
    st.caption(f"표본: {base.sample.sample_id} · 소수 입력은 정확한 값으로 저장 · 최대 24개 순차 실행")
    draft = st.session_state.get("ws_draft")
    if draft is None or draft.config.sample != base.sample:
        draft = StrategyDraft(base, "새 전략 실험")
        st.session_state["ws_draft"] = draft
        st.session_state.pop("ws_preview", None)
        st.session_state.update(ws_axes_text="{}", ws_scenarios_text='[{"name":"baseline","changes":{}}]', ws_axes_mode="간편 두 축")
    editor, experiments, results, history = st.tabs(["전략 편집", "실험 실행", "결과 비교", "연구 이력"])
    with editor:
        render_editor(draft, store, sample_path)
    with experiments:
        @st.fragment(run_every="2s")
        def live_jobs():
            render_experiments(store)
        live_jobs()
    with results:
        render_results(store)
    with history:
        render_history(store, base)
