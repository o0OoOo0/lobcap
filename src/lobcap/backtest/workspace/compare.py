"""Comparison rows retain scenario boundaries and exact submitted settings."""
import json
from pathlib import Path
from .store import WorkspaceStore
from .contracts import StrategyDraft, config_from_run_spec


def result_rows(root, job_id):
    store = WorkspaceStore(root)
    state, spec = store.get_job(job_id), store.get_experiment(job_id)
    from .jobs import verify_completed
    verify_completed(state)
    rows = []
    for run in spec["runs"]:
        completed = state["completed"].get(run["variant_id"])
        if completed:
            path=Path(completed["output_dir"])
            manifest=json.loads((path/"run_manifest.json").read_text(encoding="utf-8"))
            diagnostics=json.loads((path/"diagnostics.json").read_text(encoding="utf-8")) if (path/"diagnostics.json").exists() else {}
            rows.append({**completed["summary"], **run["settings"],
                "execution_contract_version":manifest.get("resolved_config",{}).get("execution_contract_version","legacy-v1"),
                "quote_path_sha256":diagnostics.get("quote_research",{}).get("quote_path_sha256"),
                "run_id": completed["run_id"], "output_dir": completed["output_dir"],
                "variant_id": run["variant_id"], "strategy_id": run["strategy_id"], "scenario_id": run["scenario_id"], "scenario_hash": run["scenario_hash"],
                "sample_sha256": spec["sample_sha256"], "effective_hash": run["effective_hash"],
                "axis_values": run["axis_values"], "paper_eligibility": "INSUFFICIENT_EVIDENCE"})
    return rows


def clone_run(root, job_id, variant_id, name=None):
    store = WorkspaceStore(root)
    spec, state = store.get_experiment(job_id), store.get_job(job_id)
    row = next(r for r in spec["runs"] if r["variant_id"] == variant_id)
    parent = state["completed"].get(variant_id, {}).get("run_id", variant_id)
    return StrategyDraft(config_from_run_spec(row), name or spec["name"] + " copy",
        spec["hypothesis"], parent_run_id=parent, research_policy=row["research_policy"])


def compare_rows(rows, scenario_id=None, strategy_id=None):
    if len({r["sample_sha256"] for r in rows}) > 1:
        raise ValueError("direct comparison requires identical samples")
    if scenario_id is not None:
        rows = [r for r in rows if r["scenario_id"] == scenario_id]
    if strategy_id is not None:
        rows = [r for r in rows if r["strategy_id"] == strategy_id]
    if len({r.get("execution_contract_version","legacy-v1") for r in rows}) > 1:
        raise ValueError("execution contract versions differ; compare one engine version at a time")
    if scenario_id is None and strategy_id is None and len({r["scenario_id"] for r in rows}) > 1:
        raise ValueError("select one scenario or one strategy before comparing")
    if strategy_id is None:
        from .contracts import fingerprint
        from .resolve import SCENARIO_FIELDS
        assumptions = {fingerprint({key: row.get(key) for key in sorted(SCENARIO_FIELDS)}) for row in rows}
        if len(assumptions) > 1:
            raise ValueError("execution assumptions differ; select one strategy for scenario sensitivity")
    return rows


def setting_diff(left, right):
    return {key: {"before": left.get(key), "after": right.get(key)} for key in sorted(set(left)|set(right))
        if left.get(key) != right.get(key)}
