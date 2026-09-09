from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from lobcap.backtest.workspace import StrategyDraft, WorkspaceStore, resolve_experiment
from lobcap.backtest.workspace.contracts import config_from_run_spec
from lobcap.backtest.workspace.compare import clone_run, result_rows, compare_rows
from lobcap.backtest.workspace.worker import run_pending
from lobcap.config.backtest import variant
from test_runner import sample


def test_lossless_nine_grid_and_scenario_overlap(sample):
    config, path = sample
    draft = StrategyDraft(variant(config, maker_bps="2.1234567890123456789"), "grid")
    axes = {"half_spread_ticks": ["0.5", "1", "2"], "inventory_skew_ticks": ["0", "1", "2"]}
    spec = resolve_experiment(draft, axes, sample_path=path)
    assert len(spec["runs"]) == 9
    assert all(r["settings"]["maker_bps"] == "2.1234567890123456789" for r in spec["runs"])
    assert all(config_from_run_spec(r).effective_sha256 == variant(draft.config, **r["axis_values"]).effective_sha256 for r in spec["runs"])
    with pytest.raises(ValueError, match="both"):
        resolve_experiment(draft, {"maker_bps": ["0", "1"]}, [{"name": "cost", "changes": {"maker_bps": "2"}}], path)
    with pytest.raises(ValueError, match="27"):
        resolve_experiment(draft, {**axes, "signal_strength": ["1", "2", "3"]}, sample_path=path)


def test_draft_revision_idempotent_submit_cancel_resume_and_clone(sample, tmp_path):
    config, path = sample
    store = WorkspaceStore(tmp_path/"workspace")
    draft = store.save_draft(StrategyDraft(config, "grid"))
    newer = store.save_draft(replace(draft, hypothesis="edited"))
    with pytest.raises(ValueError, match="revision"):
        store.save_draft(draft)
    assert store.load_draft(draft.draft_id).revision == newer.revision
    spec = resolve_experiment(newer, {"half_spread_ticks": ["0.5", "1", "2"]}, sample_path=path)
    job = store.submit(spec, "click1")
    assert store.submit(spec, "click1") == job
    called = []
    def execute(config, sample_path, **kwargs):
        called.append(config.half_spread_ticks)
        output = tmp_path/f"run{len(called)}"
        output.mkdir()
        (output/"summary.json").write_text('{"net_pnl":"1"}')
        if len(called) == 1:
            store.request_cancel(job)
        return SimpleNamespace(output_dir=output, digest=str(config.half_spread_ticks), summary={"net_pnl": "1"})
    run_pending(store.root, execute)
    assert store.get_job(job)["status"] == "CANCELED"
    assert len(store.get_job(job)["completed"]) == 1
    store.resume(job)
    run_pending(store.root, execute)
    assert store.get_job(job)["status"] == "COMPLETED"
    assert len(called) == 3
    rows = result_rows(store.root, job)
    assert len(compare_rows(rows, scenario_id="baseline")) == 3
    copy = clone_run(store.root, job, rows[0]["variant_id"])
    assert copy.parent_run_id == "run1"
    assert copy.config.half_spread_ticks == called[0]
    assert copy.draft_id == "" and copy.revision == 0


def test_sample_tamper_and_expression_parameter_axis(sample, tmp_path):
    config, path = sample
    config = variant(config, strategy="expression", signal_expression="k * imbalance", expression_parameters={"k": "1"})
    spec = resolve_experiment(StrategyDraft(config, "expression"), {"parameter.k": ["1", "2"]}, sample_path=path)
    assert [r["settings"]["expression_parameters"] for r in spec["runs"]] == [[["k", "1"]], [["k", "2"]]]
    assert all(r["sources"]["expression_parameters"] == "axis" for r in spec["runs"])
    (path/"validation.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        WorkspaceStore(tmp_path/"workspace").submit(spec, "a")


@pytest.mark.parametrize("axis", [
    {"decision_ms": [250, 1]},
    {"size": ["0.001", "0.0001"]},
    {"max_inventory": ["0.01", "0.0001"]},
])
def test_resolved_combinations_enforce_shared_execution_bounds(sample, axis):
    config, path = sample
    with pytest.raises(ValueError):
        resolve_experiment(StrategyDraft(config, "bounded grid"), axis, sample_path=path)
