"""Pure configuration expansion: the preview is the execution specification."""
from decimal import Decimal
from itertools import product
from pathlib import Path

from lobcap.backtest.runner import config_document
from lobcap.backtest.presentation import validate_ui_config
from lobcap.backtest.types import jsonable
from lobcap.config.backtest import variant, DECIMALS, INTS, STRINGS
from lobcap.config.common import sha256_file
from lobcap.config.experiment import ROOT
from .contracts import fingerprint

SCENARIO_FIELDS = {"alpha", "clock_mode", "entry_ms", "cancel_ms", "report_ms",
    "maker_bps", "taker_bps", "max_trade_participation", "impact_stress_bps"}


def code_fingerprint():
    paths = sorted(set((ROOT / "src/lobcap/backtest").rglob("*.py")) |
        set((ROOT / "src/lobcap/config").rglob("*.py")) |
        {ROOT / "src/lobcap/l2/fill_models.py", ROOT / "src/lobcap/l2/schema.py"})
    return fingerprint({p.relative_to(ROOT).as_posix(): sha256_file(p) for p in paths})


def expand_axis(key, values):
    if isinstance(values, dict):
        if set(values) != {"start", "stop", "step"}:
            raise ValueError("range requires start, stop, step (inclusive stop)")
        start, stop, step = (Decimal(str(values[k])) for k in ("start", "stop", "step"))
        if not all(v.is_finite() for v in (start, stop, step)) or step <= 0 or stop < start:
            raise ValueError("range must be finite and increasing with positive step")
        count = int((stop-start)//step)+1
        if count > 24:
            raise ValueError(f"axis {key} has {count} values; submission limit is 24")
        values = [start+i*step for i in range(count)]
    if not isinstance(values, (tuple, list)) or not values:
        raise ValueError(f"axis {key} needs a nonempty value list")
    normalized, duplicates = [], []
    for value in values:
        if (key in DECIMALS or key.startswith("parameter.") or key.startswith("policy.")) and value is not None:
            value = format(Decimal(str(value)).normalize(), "f")
        elif key in INTS:
            d = Decimal(str(value))
            if d != d.to_integral_value():
                raise ValueError(f"{key} requires integer values")
            value = int(d)
        if value in normalized:
            duplicates.append(value)
        else:
            normalized.append(value)
    return normalized, duplicates


def resolve_experiment(draft, axes=None, scenarios=None, sample_path=None, code_sha256=None):
    if not draft.name.strip() or len(draft.name) > 200 or len(draft.hypothesis) > 2000:
        raise ValueError("provide a name (1–200 characters) and hypothesis ≤2000 characters")
    axes = axes or {}
    parameter_names = {"parameter." + name for name, _ in draft.config.expression_parameters}
    policy_names = {"policy." + name for name in (draft.research_policy or {}) if name not in {"version", "hedge_enabled"}}
    if set(axes) - (DECIMALS | INTS | STRINGS | parameter_names | policy_names):
        raise ValueError("unsupported grid axes; declare expression parameters and policy fields first")
    scenarios = scenarios or [{"name": "baseline", "changes": {}}]
    names = [s["name"] for s in scenarios]
    if len(names) != len(set(names)) or any(not isinstance(n, str) or not n.strip() for n in names):
        raise ValueError("scenario names must be nonempty and unique")
    for scenario in scenarios:
        if set(scenario) != {"name", "changes"} or set(scenario["changes"]) - SCENARIO_FIELDS:
            raise ValueError("scenario patches accept execution/cost assumptions only")
        if set(axes) & set(scenario["changes"]):
            raise ValueError("a field cannot appear in both grid axes and scenario patches")
    expanded, duplicates = {}, {}
    for key in sorted(axes):
        expanded[key], duplicates[key] = expand_axis(key, axes[key])
    count = len(scenarios)
    for values in expanded.values():
        count *= len(values)
    if not 1 <= count <= 24:
        raise ValueError(f"submission contains {count} runs; limit is 24; split axes or scenarios")
    sample_path = Path(sample_path or draft.config.output_path / "samples" / draft.config.sample.sample_id).resolve()
    files = {name: sha256_file(sample_path/name) for name in
        ("sample_manifest.json", "initial_book.json", "events.jsonl.gz", "validation.json")}
    import json
    manifest = json.loads((sample_path / "sample_manifest.json").read_text(encoding="utf-8"))
    from lobcap.backtest.replay import load_sample
    load_sample(sample_path, draft.config)
    policy = jsonable(draft.research_policy)
    if policy is not None:
        from lobcap.backtest.research import policy_from_dict
        policy_from_dict(policy).validate()
        if draft.config.clock_mode != "recorded":
            raise ValueError("research risk/hedge policy requires recorded clock")
    code = code_sha256 or code_fingerprint()
    runs = []
    for values in product(*expanded.values()):
        patch = dict(zip(expanded, values))
        settings_patch = {k: v for k, v in patch.items() if not k.startswith(("parameter.", "policy."))}
        parameters = dict(draft.config.expression_parameters)
        parameters.update({k.split(".", 1)[1]: v for k, v in patch.items() if k.startswith("parameter.")})
        if parameters:
            settings_patch["expression_parameters"] = parameters
        run_policy = dict(policy) if policy is not None else None
        for k, v in patch.items():
            if k.startswith("policy."):
                field = k.split(".", 1)[1]
                if field == "hedge_ms":
                    if Decimal(str(v)) != Decimal(str(v)).to_integral_value():
                        raise ValueError("policy.hedge_ms requires integer values")
                    v = int(v)
                run_policy[field] = v
        if run_policy is not None:
            policy_from_dict(run_policy).validate()
        strategy_config = variant(draft.config, **settings_patch)
        for scenario in scenarios:
            config = variant(strategy_config, **scenario["changes"])
            validate_ui_config(config)
            if policy is not None and config.clock_mode != "recorded":
                raise ValueError("research policy requires recorded clock in every scenario")
            strategy_settings = {k: v for k, v in config_document(strategy_config)["settings"].items() if k not in SCENARIO_FIELDS}
            effective = fingerprint({"config": config.resolved(), "policy": run_policy,
                "sample": files, "code": code})
            runs.append({"variant_id": effective, "effective_hash": effective,
                "config_document": config_document(config), "settings": config_document(config)["settings"],
                "strategy_id": fingerprint({"settings": strategy_settings, "policy": run_policy}),
                "scenario_id": scenario["name"], "scenario_hash": fingerprint({k: config_document(config)["settings"][k] for k in sorted(SCENARIO_FIELDS)}), "axis_values": patch,
                "research_policy": run_policy, "sources": {k: "scenario" if k in scenario["changes"] else
                    "axis" if k in patch or (k == "expression_parameters" and any(key.startswith("parameter.") for key in patch)) else "draft" for k in config_document(config)["settings"]}})
    if len({r["variant_id"] for r in runs}) != len(runs):
        raise ValueError("scenarios produce duplicate effective configurations")
    spec = {"schema_version": 1, "name": draft.name, "hypothesis": draft.hypothesis,
        "draft_id": draft.draft_id, "draft_revision": draft.revision, "parent_run_id": draft.parent_run_id,
        "sample_path": str(sample_path), "sample_sha256": manifest["sample_sha256"],
        "sample_files": files, "code_sha256": code, "axes": expanded, "duplicates_removed": duplicates,
        "scenarios": scenarios, "runs": runs, "paper_eligibility": "INSUFFICIENT_EVIDENCE"}
    spec["experiment_hash"] = fingerprint(spec)
    return jsonable(spec)
