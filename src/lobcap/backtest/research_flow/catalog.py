"""Read-only, provenance-preserving index for legacy and workspace runs."""
from __future__ import annotations

import json
from pathlib import Path
from functools import lru_cache
import re
import subprocess


def read_object(path):
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected artifact object: {path.name}")
    return value


def run_record(run_dir):
    path = Path(run_dir)
    manifest = read_object(path / "run_manifest.json")
    config = read_object(path / "resolved_config.json") or manifest.get("resolved_config", {})
    summary = read_object(path / "summary.json")
    status = manifest.get("status", "INCOMPLETE")
    warnings = []
    if not manifest:
        warnings.append("Run manifest missing; this execution cannot be ranked.")
    if not (path / "decision_traces.jsonl.gz").exists():
        warnings.append("Legacy execution: decision stage trace unavailable.")
    return {"run_id": manifest.get("run_id", path.name), "output_dir": str(path),
            "name": config.get("strategy", path.name), "status": status,
            "source": "legacy", "sources": ["legacy"], "job_ids": [],
            "rankable": status in {"PASS", "COMPLETED"} and bool(summary) and bool(manifest),
            "sample_sha256": manifest.get("sample_sha256"),
            "execution_contract_version": config.get("execution_contract_version", "legacy-v1"),
            "config": config, "summary": summary, "manifest": manifest,
            "created_at": manifest.get("started_at", ""), "warnings": warnings}


def index_runs(output_root, workspace_root=None):
    """Index metadata only. Never creates a WorkspaceStore or migrates artifacts.

    Duplicate run IDs preserve all original paths; conflicting copies cannot rank.
    Job-only records keep failed/canceled/unstarted variants visible without PnL.
    """
    records = {}

    def safe_record(path):
        try:
            return run_record(path)
        except (OSError, ValueError, TypeError) as exc:
            return {"run_id": "invalid-" + str(Path(path).resolve()), "output_dir": str(path),
                    "name": Path(path).name, "status": "INVALID_ARTIFACT", "source": "legacy",
                    "sources": ["legacy"], "job_ids": [], "rankable": False,
                    "sample_sha256": None, "execution_contract_version": None,
                    "config": {}, "summary": {}, "manifest": {}, "created_at": "",
                    "warnings": [f"Unreadable execution evidence: {type(exc).__name__}: {exc}"]}

    def insert(row):
        key = row["run_id"]
        if key not in records:
            row["artifact_locations"] = [row["output_dir"]] if row["output_dir"] else []
            records[key] = row
            return
        old = records[key]
        location = row["output_dir"]
        if location and location not in old["artifact_locations"]:
            old["artifact_locations"].append(location)
            if (old["manifest"].get("deterministic_digest") != row["manifest"].get("deterministic_digest")
                    or old["status"] != row["status"] or old["config"] != row["config"] or old["summary"] != row["summary"]):
                old["rankable"] = False
                old["warnings"].append("Conflicting artifacts share a run ID; inspect original locations.")
        for source in row["sources"]:
            if source not in old["sources"]:
                old["sources"].append(source)
        old["job_ids"] = sorted(set(old["job_ids"] + row["job_ids"]))
        if row["source"] == "workspace":
            old["source"], old["name"] = "workspace", row["name"]
            old["job_status"] = row.get("job_status")
            old["research_context"] = row.get("research_context", {})
            old["hypothesis"] = row.get("hypothesis", "")
            old["variant_id"] = row.get("variant_id")

    output = Path(output_root)
    # rglob does not recurse into symlink directories on supported Python versions.
    # Explicit known roots retain the deployed legacy runs/research directory links.
    seen_paths = set()
    for root in (output, output / "runs", output / "research", output / "research" / "runs"):
        if root.exists():
            for manifest in sorted(root.rglob("run_manifest.json")):
                resolved = manifest.resolve()
                if resolved not in seen_paths:
                    seen_paths.add(resolved)
                    insert(safe_record(manifest.parent))
    if workspace_root is not None:
        workspace = Path(workspace_root)
        for job_file in sorted((workspace / "jobs").glob("*.json")):
            try:
                state = read_object(job_file)
            except (OSError, ValueError, TypeError) as exc:
                row = safe_record(job_file.parent / job_file.stem)
                row.update(run_id=f"invalid-job-{job_file.stem}", name=job_file.stem,
                           status="INVALID_ARTIFACT", rankable=False,
                           warnings=[f"Unreadable job metadata: {exc}"], output_dir=None)
                insert(row)
                continue
            job_id = state.get("job_id", job_file.stem)
            try:
                spec = read_object(workspace / "experiments" / f"{job_id}.json")
            except (OSError, ValueError, TypeError) as exc:
                spec = {"metadata_error": str(exc)}
            variants = {r["variant_id"]: r for r in spec.get("runs", [])}
            identifiers = sorted(set(variants) | set(state.get("completed", {})) | set(state.get("failed", {})))
            if not identifiers:
                identifiers = [None]
            for identifier in identifiers:
                result = state.get("completed", {}).get(identifier) or state.get("failed", {}).get(identifier) or {}
                path = result.get("output_dir")
                if path and Path(path).exists():
                    row = safe_record(path)
                else:
                    row = {"run_id": result.get("run_id", f"job-{job_id}-{identifier or 'pending'}"),
                           "output_dir": path or None, "status": result.get("status", state.get("status", "UNKNOWN")),
                           "rankable": False, "sample_sha256": spec.get("sample_sha256"),
                           "execution_contract_version": None, "config": {}, "summary": {},
                           "manifest": {}, "warnings": ["Execution artifacts unavailable; not ranked."],
                           "created_at": state.get("created_at", "")}
                row.update(source="workspace", sources=["workspace"], job_ids=[job_id],
                           name=state.get("name", spec.get("name", job_id)),
                           variant_id=identifier, job_status=state.get("status"),
                           research_context=spec.get("research_context", {}), hypothesis=spec.get("hypothesis", ""))
                variant = variants.get(identifier, {})
                if variant.get("research_label"):
                    row["name"] = f"{variant['research_label']} · {variant.get('research_name', row['name'])}"
                if spec.get("metadata_error"):
                    row["warnings"].append("Experiment metadata unreadable: " + spec["metadata_error"])
                    row["rankable"] = False
                insert(row)
    return sorted(records.values(), key=lambda r: (r["created_at"], r["run_id"]), reverse=True)


@lru_cache(maxsize=128)
def display_only_compatibility(left_code, right_code):
    """Attest a narrow display-only diff using the actual immutable Git objects.

    Never infer compatibility from equal version labels alone. Unknown objects,
    dependency changes and any execution/config/data/analysis change fail closed.
    """
    if not all(isinstance(v,str) and re.fullmatch(r'[0-9a-f]{40}',v) for v in (left_code,right_code)):
        return None
    from lobcap.config.experiment import ROOT
    try:
        result=subprocess.run(['git','diff','--name-only','--no-renames',
            left_code+'^{commit}',right_code+'^{commit}','--'],cwd=ROOT,
            text=True,capture_output=True,timeout=3,check=True)
    except (OSError,subprocess.SubprocessError):
        return None
    paths=result.stdout.splitlines()
    allowed={'apps/backtest_lab.py','README_FIRST.md','FAILURES.md',
        'scripts/validate_strategy_response.py',
        'src/lobcap/backtest/research_flow/ui.py',
        'src/lobcap/backtest/research_flow/ui_experiment.py',
        'src/lobcap/backtest/research_flow/ui_results.py',
        'src/lobcap/backtest/research_flow/workbench.py',
        'src/lobcap/backtest/research_flow/workbench_model.py',
        'src/lobcap/backtest/research_flow/catalog.py'}
    if any(path not in allowed and not path.startswith(('tests/','docs/')) for path in paths):
        return None
    return {'kind':'verified_display_only_git_diff','left_code':left_code,
            'right_code':right_code,'changed_paths':paths}


def comparison_guard(left, right, allow_scenario_change=False):
    reasons = []
    compatibility = None
    for row in (left, right):
        if not row.get("rankable"):
            reasons.append(f"Execution {row.get('run_id')} is incomplete, failed, canceled, or missing evidence.")
    if not left.get("sample_sha256") or left.get("sample_sha256") != right.get("sample_sha256"):
        reasons.append("Identical verified sample fingerprints are required.")
    if not left.get("execution_contract_version") or left.get("execution_contract_version") != right.get("execution_contract_version"):
        reasons.append("Execution contract versions differ or are unavailable.")
    a, b = left.get("config", {}), right.get("config", {})
    if a.get('quote_start_seconds',0)!=b.get('quote_start_seconds',0):
        reasons.append('Comparison requires matching quote start delay.')
    for key in ("sample", "instrument", "initial_cash", "initial_inventory", "max_inventory"):
        if key not in a or key not in b or a[key] != b[key]:
            reasons.append(f"Comparison requires matching {key}.")
    for key in ("quote_policy_version", "decision_trace_version"):
        if a.get(key, "legacy-unversioned") != b.get(key, "legacy-unversioned"):
            reasons.append(f"Comparison requires matching {key}.")
    if left.get("manifest", {}).get("research_policy") != right.get("manifest", {}).get("research_policy"):
        reasons.append("Research risk/hedge policies differ.")
    code_a, code_b = left.get("manifest", {}).get("code_version"), right.get("manifest", {}).get("code_version")
    def unverified_code(value):
        return (not isinstance(value, str) or not value.strip()
                or value.strip().lower() in {"unknown", "none", "null", "unavailable"}
                or "dirty" in value.lower())
    if unverified_code(code_a) or unverified_code(code_b):
        reasons.append("Known clean code versions are required; unknown or dirty code cannot certify direct comparison.")
    elif code_a != code_b:
        compatibility=display_only_compatibility(code_a,code_b)
        if compatibility is None:
            reasons.append("Code versions differ; no verified display-only compatibility is available.")
    if not allow_scenario_change:
        for key in ("clock_mode", "alpha", "entry_ms", "cancel_ms", "report_ms", "maker_bps", "taker_bps", "max_trade_participation"):
            if key not in a or key not in b or a[key] != b[key]:
                reasons.append(f"Execution assumption {key} differs or is missing; use explicit sensitivity comparison.")
        if a.get("impact_stress_bps", "0") != b.get("impact_stress_bps", "0"):
            reasons.append("Impact stress diagnostic settings differ.")
    else:
        scenario = {"clock_mode", "alpha", "entry_ms", "cancel_ms", "report_ms", "maker_bps", "taker_bps", "max_trade_participation", "impact_stress_bps"}
        for key in sorted(set(a) | set(b)):
            if key not in scenario and a.get(key) != b.get(key):
                reasons.append(f"Sensitivity must freeze strategy and policy setting {key}.")
    return {"comparable": not reasons, "reasons": list(dict.fromkeys(reasons)),
            "code_compatibility": compatibility,
            "mode": "scenario_sensitivity" if allow_scenario_change else "strategy_comparison"}
