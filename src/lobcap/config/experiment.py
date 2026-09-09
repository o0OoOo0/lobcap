"""Canonical typed loading and validation for versioned experiment YAML."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, TypeAlias

import yaml

from lobcap.config.common import load_yaml_with_hashes, sha256_file


ROOT = Path(__file__).parents[3]


class ConfigError(ValueError):
    """The requested experiment config is invalid or ambiguous."""


@dataclass(frozen=True, slots=True)
class Window:
    label: str
    start_minute: int
    duration_minutes: int


@dataclass(frozen=True, slots=True)
class PlacementRule:
    price: str
    sides: tuple[str, ...]
    trigger_seconds: tuple[int, ...]
    ordering: str
    branch_model: str


@dataclass(frozen=True, slots=True)
class BaseConfig:
    config_path: Path
    experiment_name: str
    experiment_version: str
    raw_file_sha256: str
    canonical_config_sha256: str
    output_path: Path
    random_seed: int | None
    evaluation_metrics: tuple[str, ...]
    venue_name: str
    market: str
    symbol: str
    source_provider: str
    source_format: str


@dataclass(frozen=True, slots=True)
class FiveCConfig(BaseConfig):
    input_path: Path
    dates: tuple[str, ...]
    windows: tuple[Window, ...]
    placement: PlacementRule
    virtual_sizes: tuple[Decimal, ...]
    ttls_seconds: tuple[int, ...]
    model_names: tuple[str, ...]
    expected_placements: int


@dataclass(frozen=True, slots=True)
class FiveDConfig(BaseConfig):
    raw_input_path: Path
    truth_input_path: Path
    truth_sha256: str
    alpha_grid: tuple[Decimal, ...]
    touch_fill_enabled: bool
    model_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FiveEConfig(BaseConfig):
    calibration_dates: tuple[str, ...]
    validation_dates: tuple[str, ...]
    test_dates: tuple[str, ...]
    windows: tuple[Window, ...]
    placement: PlacementRule
    virtual_sizes: tuple[Decimal, ...]
    ttls_seconds: tuple[int, ...]
    alpha_grid: tuple[Decimal, ...]
    touch_fill_enabled: bool
    structural_alpha_enabled: bool
    model_names: tuple[str, ...]
    channels: tuple[str, ...]
    placements_per_window: int

    @property
    def dates(self) -> tuple[str, ...]:
        return self.calibration_dates + self.validation_dates + self.test_dates


ExperimentConfig: TypeAlias = FiveCConfig | FiveDConfig | FiveEConfig


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{context} must be a mapping")
    return value


def _strict(value: Any, context: str, allowed: set[str], required: set[str]) -> Mapping[str, Any]:
    row = _mapping(value, context)
    unknown, missing = sorted(set(row) - allowed), sorted(required - set(row))
    if unknown:
        raise ConfigError(f"{context} has unknown fields: {unknown}")
    if missing:
        raise ConfigError(f"{context} is missing fields: {missing}")
    return row


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context} must be a non-empty string")
    return value


def _path(value: Any, context: str) -> Path:
    path = Path(_text(value, context))
    return path if path.is_absolute() else ROOT / path


def _list(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{context} must be a non-empty list")
    return value


def _dates(value: Any, context: str) -> tuple[str, ...]:
    result = []
    for item in _list(value, context):
        text = _text(item, context)
        try:
            parsed = date.fromisoformat(text)
        except ValueError as exc:
            raise ConfigError(f"{context} contains invalid ISO date: {text!r}") from exc
        if parsed.isoformat() != text:
            raise ConfigError(f"{context} date must be canonical YYYY-MM-DD: {text!r}")
        result.append(text)
    if len(result) != len(set(result)):
        raise ConfigError(f"{context} contains duplicate dates")
    return tuple(result)


def _windows(value: Any, context: str) -> tuple[Window, ...]:
    result = []
    for item in _list(value, context):
        label = _text(item, context)
        try:
            start_text, end_text = label.split("-", 1)
            start, end = [int(x) for x in start_text.split(":")], [int(x) for x in end_text.split(":")]
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"{context} contains invalid window: {label!r}") from exc
        if len(start) not in {2, 3} or len(start) != len(end):
            raise ConfigError(f"{context} contains invalid window: {label!r}")
        while len(start) < 3:
            start.append(0); end.append(0)
        sh, sm, ss = start; eh, em, es = end
        if not (0 <= sh < 24 and 0 <= eh <= 24 and 0 <= sm < 60 and 0 <= em < 60 and 0 <= ss < 60 and 0 <= es < 60):
            raise ConfigError(f"{context} contains out-of-range window: {label!r}")
        start_s, end_s = sh * 3600 + sm * 60 + ss, eh * 3600 + em * 60 + es
        if end_s <= start_s or start_s % 60 or end_s % 60:
            raise ConfigError(f"{context} windows need positive whole-minute bounds: {label!r}")
        result.append(Window(label, start_s // 60, (end_s - start_s) // 60))
    if len({item.start_minute for item in result}) != len(result):
        raise ConfigError(f"{context} contains duplicate starts")
    return tuple(result)


def _decimals(value: Any, context: str, alpha: bool = False) -> tuple[Decimal, ...]:
    result = []
    for item in _list(value, context):
        try:
            parsed = Decimal(str(item))
        except InvalidOperation as exc:
            raise ConfigError(f"{context} contains invalid decimal: {item!r}") from exc
        valid = parsed.is_finite() and (Decimal(0) <= parsed <= Decimal(1) if alpha else parsed > 0)
        if not valid:
            raise ConfigError(f"{context} values must be {'in [0,1]' if alpha else 'positive'}")
        result.append(parsed)
    if len(result) != len(set(result)):
        raise ConfigError(f"{context} values must be unique")
    return tuple(result)


def _ttls(value: Any) -> tuple[int, ...]:
    result = _list(value, "ttls_seconds")
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in result):
        raise ConfigError("ttls_seconds must contain positive integers")
    if len(result) != len(set(result)):
        raise ConfigError("ttls_seconds values must be unique")
    return tuple(result)


def _metrics(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        result = tuple(_text(item, "evaluation_metrics") for item in value)
    elif isinstance(value, Mapping):
        result = tuple(metric for nested in value.values() for metric in _metrics(nested))
    else:
        raise ConfigError("evaluation_metrics must be a list or mapping")
    if not result:
        raise ConfigError("evaluation_metrics must not be empty")
    return result


def _models(value: Any) -> tuple[str, ...]:
    result = tuple(_text(item, "model_names") for item in _list(value, "model_names"))
    if len(result) != len(set(result)):
        raise ConfigError("model_names must be unique")
    for name in result:
        if name.startswith("alpha="):
            try:
                alpha = Decimal(name.split("=", 1)[1])
            except InvalidOperation as exc:
                raise ConfigError(f"unsupported model: {name}") from exc
            if not Decimal(0) <= alpha <= Decimal(1):
                raise ConfigError(f"unsupported model: {name}")
        elif name not in {"L3_DERIVED_SHADOW_FILL", "STRUCTURAL_ALPHA", "TOUCH_FILL"}:
            raise ConfigError(f"unsupported model: {name}")
    return result


def _placement(value: Any, multidate: bool) -> PlacementRule:
    trigger_key = "per_minute_trigger_seconds" if multidate else "trigger_seconds"
    candidate = _mapping(value, "placement_rule")
    required = {"price", "sides", trigger_key}
    if "inherited_from" not in candidate:
        required |= {"ordering", "branch_model"}
    row = _strict(candidate, "placement_rule", {"price", "sides", trigger_key, "ordering", "branch_model", "inherited_from"}, required)
    sides = tuple(_text(x, "placement_rule.sides") for x in _list(row["sides"], "placement_rule.sides"))
    triggers = _list(row[trigger_key], trigger_key)
    if row["price"] != "reconstructed BBO" or set(sides) - {"buy", "sell"} or len(sides) != len(set(sides)):
        raise ConfigError("only unique buy/sell reconstructed-BBO placement is supported")
    if any(isinstance(x, bool) or not isinstance(x, int) or not 0 <= x < 60 for x in triggers) or len(triggers) != len(set(triggers)):
        raise ConfigError(f"{trigger_key} must be unique integer seconds in [0,59]")
    ordering = _text(row.get("ordering", "first lifecycle event at or after trigger time is fully applied, then place"), "ordering")
    branch = _text(row.get("branch_model", "independent_counterfactual"), "branch_model")
    if "fully applied, then place" not in ordering or branch != "independent_counterfactual":
        raise ConfigError("only existing after-event independent-counterfactual semantics are supported")
    return PlacementRule(row["price"], sides, tuple(triggers), ordering, branch)


def _load(path: str | Path) -> tuple[Path, Mapping[str, Any], str, str]:
    try:
        return load_yaml_with_hashes(path)
    except (yaml.YAMLError, ValueError) as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc


def _common(path: Path, row: Mapping[str, Any], raw_sha: str, canonical_sha: str) -> dict[str, Any]:
    if row.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    seed = row.get("random_seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ConfigError("random_seed must be an integer or null")
    if row.get("randomness") == "none" and seed is not None:
        raise ConfigError("random_seed must be null when randomness is none")
    venue = _strict(row.get("venue"), "venue", {"name", "market", "symbol"}, {"name", "market", "symbol"})
    source = _mapping(row.get("source"), "source")
    if venue["name"] != "coinbase" or venue["market"] != "spot" or venue["symbol"] != "BTC-USD":
        raise ConfigError("only the verified Coinbase BTC-USD spot source is supported")
    if source.get("provider") != "tardis.dev":
        raise ConfigError("only the verified tardis.dev source provider is supported")
    return {"config_path": path, "experiment_name": _text(row.get("experiment_name"), "experiment_name"), "experiment_version": _text(str(row.get("experiment_version", "")), "experiment_version"), "raw_file_sha256": raw_sha, "canonical_config_sha256": canonical_sha, "output_path": _path(row.get("output_location"), "output_location"), "random_seed": seed, "evaluation_metrics": _metrics(row.get("evaluation_metrics")), "venue_name": _text(venue["name"], "venue.name"), "market": _text(venue["market"], "venue.market"), "symbol": _text(venue["symbol"], "venue.symbol"), "source_provider": _text(source.get("provider"), "source.provider"), "source_format": _text(source.get("format"), "source.format")}


def load_five_c_config(path: str | Path) -> FiveCConfig:
    path, row, raw_sha, canonical_sha = _load(path)
    allowed = {"schema_version", "experiment_name", "experiment_version", "provenance", "implementation", "venue", "source", "dates", "windows_utc", "placement_rule", "virtual_sizes_btc", "ttls_seconds", "alpha_grid", "model_names", "split_rule", "random_seed", "randomness", "evaluation_metrics", "expected_placements", "output_location"}
    _strict(row, "5C config", allowed, allowed - {"provenance", "implementation"}); common = _common(path, row, raw_sha, canonical_sha)
    if common["experiment_name"] != "5c_virtual_truth": raise ConfigError("expected 5c_virtual_truth")
    source = _strict(row["source"], "source", {"provider", "format", "input_artifact"}, {"input_artifact"})
    dates, windows, placement = _dates(row["dates"], "dates"), _windows(row["windows_utc"], "windows_utc"), _placement(row["placement_rule"], False)
    sizes, ttls, names = _decimals(row["virtual_sizes_btc"], "virtual_sizes_btc"), _ttls(row["ttls_seconds"]), _models(row["model_names"])
    expected = len(dates) * len(windows) * len(placement.trigger_seconds) * len(placement.sides) * len(sizes) * len(ttls)
    if names != ("L3_DERIVED_SHADOW_FILL",) or row["expected_placements"] != expected: raise ConfigError("5C model/cardinality does not match its placement grid")
    return FiveCConfig(**common, input_path=_path(source["input_artifact"], "source.input_artifact"), dates=dates, windows=windows, placement=placement, virtual_sizes=sizes, ttls_seconds=ttls, model_names=names, expected_placements=expected)


def load_five_d_config(path: str | Path) -> FiveDConfig:
    path, row, raw_sha, canonical_sha = _load(path)
    allowed = {"schema_version", "experiment_name", "experiment_version", "provenance", "implementation", "venue", "source", "dates", "windows_utc", "placement_rule", "virtual_sizes_btc", "ttls_seconds", "alpha_grid", "touch_fill_enabled", "model_names", "split_rule", "random_seed", "randomness", "evaluation_metrics", "expected_key_metrics", "output_location"}
    _strict(row, "5D config", allowed, allowed - {"provenance", "implementation"}); common = _common(path, row, raw_sha, canonical_sha)
    if common["experiment_name"] != "5d_fill_comparison": raise ConfigError("expected 5d_fill_comparison")
    source = _strict(row["source"], "source", {"provider", "format", "raw_input", "truth_input", "truth_sha256"}, {"raw_input", "truth_input", "truth_sha256"})
    alpha, touch, names = _decimals(row["alpha_grid"], "alpha_grid", True), row["touch_fill_enabled"], _models(row["model_names"])
    if not isinstance(touch, bool): raise ConfigError("touch_fill_enabled must be boolean")
    expected = tuple(f"alpha={value:.2f}" for value in alpha) + (("TOUCH_FILL",) if touch else ())
    if names != expected: raise ConfigError(f"model_names must match configured alpha/touch models: {expected}")
    _placement(row["placement_rule"], False); _dates(row["dates"], "dates"); _windows(row["windows_utc"], "windows_utc"); _decimals(row["virtual_sizes_btc"], "virtual_sizes_btc"); _ttls(row["ttls_seconds"])
    return FiveDConfig(**common, raw_input_path=_path(source["raw_input"], "raw_input"), truth_input_path=_path(source["truth_input"], "truth_input"), truth_sha256=_text(source["truth_sha256"], "truth_sha256"), alpha_grid=alpha, touch_fill_enabled=touch, model_names=names)


def load_five_e_config(path: str | Path) -> FiveEConfig:
    path, row, raw_sha, canonical_sha = _load(path)
    allowed = {"schema_version", "experiment_name", "experiment_version", "provenance", "implementation", "bounded_gate_runner", "venue", "source", "dates", "windows_utc", "placement_rule", "virtual_sizes_btc", "ttls_seconds", "placements_per_window", "alpha_grid", "touch_fill_enabled", "structural_alpha_enabled", "model_names", "structural_estimator", "split_rule", "random_seed", "randomness", "evaluation_metrics", "execution_state", "output_location"}
    _strict(row, "5E config", allowed, allowed - {"provenance", "implementation", "bounded_gate_runner"}); common = _common(path, row, raw_sha, canonical_sha)
    if common["experiment_name"] != "5e_multidate": raise ConfigError("expected 5e_multidate")
    split = _strict(row["dates"], "dates", {"calibration", "validation", "test"}, {"calibration", "validation", "test"})
    calibration, validation, test = (_dates(split[name], f"dates.{name}") for name in ("calibration", "validation", "test"))
    if len(calibration + validation + test) != len(set(calibration + validation + test)): raise ConfigError("split dates must be disjoint")
    windows, placement = _windows(row["windows_utc"], "windows_utc"), _placement(row["placement_rule"], True)
    sizes, ttls, alpha = _decimals(row["virtual_sizes_btc"], "virtual_sizes_btc"), _ttls(row["ttls_seconds"]), _decimals(row["alpha_grid"], "alpha_grid", True)
    touch, structural, names = row["touch_fill_enabled"], row["structural_alpha_enabled"], _models(row["model_names"])
    if not isinstance(touch, bool) or not isinstance(structural, bool): raise ConfigError("model enablement flags must be boolean")
    expected_names = tuple(f"alpha={value:.2f}" for value in alpha) + (("STRUCTURAL_ALPHA",) if structural else ()) + (("TOUCH_FILL",) if touch else ())
    if names != expected_names: raise ConfigError(f"model_names must match configured models: {expected_names}")
    if len({window.duration_minutes for window in windows}) != 1: raise ConfigError("all 5E windows must share one duration")
    expected = windows[0].duration_minutes * len(placement.trigger_seconds) * len(placement.sides) * len(sizes) * len(ttls)
    if row["placements_per_window"] != expected: raise ConfigError(f"placements_per_window must be {expected}")
    source = _strict(row["source"], "source", {"provider", "format", "channels"}, {"channels"})
    channels = tuple(_text(item, "source.channels") for item in _list(source["channels"], "source.channels"))
    return FiveEConfig(**common, calibration_dates=calibration, validation_dates=validation, test_dates=test, windows=windows, placement=placement, virtual_sizes=sizes, ttls_seconds=ttls, alpha_grid=alpha, touch_fill_enabled=touch, structural_alpha_enabled=structural, model_names=names, channels=channels, placements_per_window=expected)


def load_experiment_config(path: str | Path) -> ExperimentConfig:
    _, row, _, _ = _load(path)
    loader = {"5c_virtual_truth": load_five_c_config, "5d_fill_comparison": load_five_d_config, "5e_multidate": load_five_e_config}.get(row.get("experiment_name"))
    if loader is None: raise ConfigError(f"unsupported experiment_name: {row.get('experiment_name')!r}")
    return loader(path)


def _display(path: Path) -> str:
    try: return str(path.resolve().relative_to(ROOT))
    except ValueError: return str(path.resolve())


def config_provenance(config: ExperimentConfig) -> dict[str, Any]:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, check=True, capture_output=True, text=True).stdout)
    except (OSError, subprocess.CalledProcessError): commit, dirty = "UNKNOWN", True
    return {"config_path": _display(config.config_path), "raw_file_sha256": config.raw_file_sha256, "canonical_config_sha256": config.canonical_config_sha256, "git_commit": commit, "git_dirty": dirty, "experiment_name": config.experiment_name, "experiment_version": config.experiment_version, "code_version": f"{commit}+dirty" if dirty else commit}


def write_run_manifest(config: ExperimentConfig, *, output_dir: str | Path, status: str, started_at: str, completed_at: str, input_paths: tuple[Path, ...], output_paths: tuple[Path, ...], filename: str = "run_manifest.json") -> Path:
    inputs = [{"path": _display(path), "sha256": sha256_file(path) if path.exists() else "UNKNOWN"} for path in input_paths]
    provenance = config_provenance(config)
    outputs = [{
        "path": _display(path),
        "sha256": sha256_file(path) if path.exists() else "UNKNOWN",
    } for path in output_paths]
    manifest = {"manifest_version": 2, "experiment_name": config.experiment_name, "experiment_version": config.experiment_version, "git_commit": provenance["git_commit"], "code_version": provenance["code_version"], "config_path": provenance["config_path"], "config_provenance": provenance, "data_source": {"input_artifacts": inputs}, "date_window": "defined by versioned config", "evaluation_metrics": list(config.evaluation_metrics), "input_artifacts": inputs, "output_artifact_paths": outputs, "status": status, "started_at": started_at, "completed_at": completed_at}
    target = Path(output_dir) / filename; target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
