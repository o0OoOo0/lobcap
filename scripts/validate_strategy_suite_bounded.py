"""Run the UI's standard strategy suite on the frozen ten-minute sample."""
from __future__ import annotations

import argparse
from pathlib import Path

from lobcap.backtest.replay import write_json
from lobcap.backtest.research_flow.catalog import index_runs
from lobcap.backtest.research_flow.experiments import build_experiment
from lobcap.backtest.research_flow.strategy_compare import build_scorecard, suite_note
from lobcap.backtest.types import jsonable
from lobcap.backtest.workspace.contracts import StrategyDraft
from lobcap.backtest.workspace.store import WorkspaceStore
from lobcap.backtest.workspace.worker import run_pending
from lobcap.config.backtest import load_backtest_config


STANDARD_STRATEGIES = (
    "fixed_spread",
    "inventory_skew",
    "microprice",
    "glft",
    "glft_microprice",
    "no_trade",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/backtest/binance_recorded.yaml")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--submission-key", required=True)
    args = parser.parse_args()

    config = load_backtest_config(args.config)
    sample = config.output_path / "samples" / config.sample.sample_id
    workspace = args.workspace or config.output_path / "workspace"
    store = WorkspaceStore(workspace)
    active = [job for job in store.list_jobs() if job["status"] in {"QUEUED", "RUNNING"}]
    if active:
        raise RuntimeError("workspace already has queued or running jobs")

    draft = StrategyDraft(
        config,
        "표준 전략 6종 · 고정 10분 검증",
        "같은 표본과 실행 가정에서 표준 전략의 주문 행동과 손익 구성이 어떻게 다른가?",
    )
    spec = build_experiment(
        draft,
        "strategy_suite",
        values=STANDARD_STRATEGIES,
        sample_path=sample,
    )
    job_id = store.submit(spec, args.submission_key)
    run_pending(store.root, wait_for_lock=True)
    state = store.get_job(job_id)
    if state["status"] != "COMPLETED" or len(state["completed"]) != len(STANDARD_STRATEGIES):
        raise RuntimeError(f"strategy suite did not complete: {state}")

    rows = [row for row in index_runs(config.output_path, store.root) if job_id in row.get("job_ids", ())]
    scorecard = build_scorecard(rows)
    if len(scorecard) != len(STANDARD_STRATEGIES):
        raise RuntimeError("strategy suite scorecard is incomplete")
    if any(not row["reconciled"] for row in scorecard):
        raise RuntimeError("strategy suite contains an unreconciled ledger")
    control = next(row for row in scorecard if row["strategy"] == "no_trade")
    if not control["control"] or control["development_rank"] is not None:
        raise RuntimeError("no-trade control was ranked as a strategy")

    evidence = {
        "status": "PASS",
        "purpose": "Frozen ten-minute development validation; never paper-trading eligibility",
        "sample": config.sample.sample_id,
        "job_id": job_id,
        "strategies": STANDARD_STRATEGIES,
        "research_context": spec["research_context"],
        "scorecard": scorecard,
        "note": suite_note(scorecard),
        "paper_eligibility": "INSUFFICIENT_EVIDENCE",
    }
    write_json(args.evidence, jsonable(evidence))
    print(f"PASS job={job_id} evidence={args.evidence}", flush=True)
    for row in scorecard:
        print(
            row["strategy"],
            row["net_pnl"],
            row["filled_orders"],
            row["development_rank"],
            row["loss_driver"],
            flush=True,
        )


if __name__ == "__main__":
    main()
