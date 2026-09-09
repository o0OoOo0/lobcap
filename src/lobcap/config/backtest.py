"""Strict configuration for the separate bounded backtest track."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from lobcap.backtest.types import InstrumentSpec, SampleSpec, NS, jsonable
from lobcap.config.common import load_yaml_with_hashes
from lobcap.config.experiment import ConfigError, ROOT
from lobcap.l2.fill_models import ModelSpec
from lobcap.l2.schema import timestamp_ns


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    config_path: Path
    raw_file_sha256: str
    canonical_config_sha256: str
    sample: SampleSpec
    data_root: Path
    output_path: Path
    instrument: InstrumentSpec = InstrumentSpec()
    strategy: str = "fixed_spread"
    size: Decimal = Decimal("0.001")
    half_spread_ticks: Decimal = Decimal("0.5")
    inventory_skew_ticks: Decimal = Decimal(1)
    signal_strength: Decimal = Decimal(1)
    max_inventory: Decimal = Decimal("0.01")
    min_spread_ticks: Decimal = Decimal(1)
    decision_ms: int = 250
    max_age_ms: int = 5000
    stale_book_ms: int = 500
    entry_ms: int = 0
    cancel_ms: int = 0
    alpha: Decimal | None = Decimal("0.5")
    maker_bps: Decimal = Decimal(0)
    taker_bps: Decimal = Decimal(0)
    initial_cash: Decimal = Decimal(100000)
    initial_inventory: Decimal = Decimal(0)
    volatility_widen_bps: Decimal | None = None
    volatility_reduce_bps: Decimal | None = None
    volatility_stop_bps: Decimal | None = None
    experiment_name: str = "backtest_mvp"
    experiment_version: str = "1"
    evaluation_metrics: tuple[str, ...] = ("pnl", "inventory", "fills", "markouts", "drawdown")

    @property
    def model(self) -> ModelSpec:
        if self.alpha is None:
            return ModelSpec("TOUCH_FILL", None, True, "queue-ignoring diagnostic")
        return ModelSpec("alpha=" + format(self.alpha.normalize(), "f"), self.alpha,
                         interpretation="uncalibrated depletion assumption")

    def resolved(self):
        row = jsonable(self)
        for key in ("config_path", "raw_file_sha256", "canonical_config_sha256", "output_path", "data_root"):
            row.pop(key)
        return row

    @property
    def effective_sha256(self):
        return hashlib.sha256(json.dumps(self.resolved(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


DECIMALS = {"size", "half_spread_ticks", "inventory_skew_ticks", "signal_strength", "max_inventory", "min_spread_ticks", "alpha", "maker_bps", "taker_bps", "initial_cash", "initial_inventory", "volatility_widen_bps", "volatility_reduce_bps", "volatility_stop_bps"}
OPTIONAL = {"alpha", "volatility_widen_bps", "volatility_reduce_bps", "volatility_stop_bps"}
INTS = {"decision_ms", "max_age_ms", "stale_book_ms", "entry_ms", "cancel_ms"}
STRATEGIES = ("fixed_spread", "inventory_skew", "microprice")


def validate(config: BacktestConfig) -> BacktestConfig:
    if config.strategy not in STRATEGIES:
        raise ConfigError("unknown strategy")
    for name in DECIMALS:
        value = getattr(config, name)
        if value is None and name in OPTIONAL:
            continue
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ConfigError(f"{name} must be a finite Decimal")
    for name in INTS:
        value = getattr(config, name)
        if type(value) is not int or value < (0 if name in {"entry_ms", "cancel_ms"} else 1):
            raise ConfigError(f"invalid {name}")
    for name in ("size", "max_inventory", "initial_cash"):
        if getattr(config, name) <= 0:
            raise ConfigError(f"{name} must be positive")
    for name in ("half_spread_ticks", "inventory_skew_ticks", "signal_strength", "min_spread_ticks", "volatility_widen_bps", "volatility_reduce_bps", "volatility_stop_bps"):
        if getattr(config, name) is not None and getattr(config, name) < 0:
            raise ConfigError(f"{name} must be nonnegative")
    if config.alpha is not None and not 0 <= config.alpha <= 1:
        raise ConfigError("alpha must be in [0,1]")
    if abs(config.initial_inventory) > config.max_inventory:
        raise ConfigError("initial inventory exceeds limit")
    return config


def variant(config: BacktestConfig, **changes) -> BacktestConfig:
    allowed = DECIMALS | INTS | {"strategy"}
    if set(changes) - allowed:
        raise ConfigError(f"unknown variant fields: {sorted(set(changes)-allowed)}")
    try:
        changes = {k: Decimal(str(v)) if k in DECIMALS and v is not None else v for k, v in changes.items()}
    except Exception as exc:
        raise ConfigError("invalid decimal setting") from exc
    return validate(replace(config, **changes))


def load_backtest_config(path: str | Path) -> BacktestConfig:
    try:
        path, row, raw_sha, canonical_sha = load_yaml_with_hashes(path)
        allowed = {"schema_version", "sample", "data_root", "output_path", "settings"}
        if set(row) != allowed or row["schema_version"] != 1:
            raise ConfigError("expected schema_version=1 and sample/data_root/output_path/settings")
        s = row["sample"]
        if set(s) != {"id", "start", "end", "warmup_seconds"}:
            raise ConfigError("invalid sample fields")
        if not isinstance(s["id"], str) or not s["id"] or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in s["id"]):
            raise ConfigError("sample id must be a safe filename")
        start, end = timestamp_ns(s["start"]), timestamp_ns(s["end"])
        if not 600 * NS <= end-start <= 1800 * NS:
            raise ConfigError("sample must be 10–30 minutes")
        if type(s["warmup_seconds"]) is not int or not 30 <= s["warmup_seconds"] <= 300:
            raise ConfigError("warmup must be 30–300 seconds")
        resolve = lambda value: Path(value) if Path(value).is_absolute() else ROOT / value
        for key,value in row["settings"].items():
            if key in DECIMALS and isinstance(value,float):
                raise ConfigError(f"quote decimal setting {key} in YAML to preserve precision")
        config = BacktestConfig(path, raw_sha, canonical_sha,
            SampleSpec(s["id"], start, end, s["warmup_seconds"] * NS),
            resolve(row["data_root"]), resolve(row["output_path"]))
        return variant(config, **row["settings"])
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"invalid backtest config: {exc}") from exc
