from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from lobcap.config import (
    ConfigError,
    load_five_c_config,
    load_five_d_config,
    load_five_e_config,
)
from lobcap.l2.compare import build_comparison
from lobcap.l2.fill_models import build_model_specs
from lobcap.l2.multidate import dry_run_plan
from lobcap.l3.virtual import intents_from_config


ROOT = Path(__file__).parents[1]
CONFIGS = ROOT / "configs/experiments"


def _variant(tmp_path, name, mutate):
    source = CONFIGS / name
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    mutate(data)
    target = tmp_path / name
    target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return target


def test_all_production_configs_load_and_are_typed():
    five_c = load_five_c_config(CONFIGS / "5c_virtual_truth.yaml")
    five_d = load_five_d_config(CONFIGS / "5d_fill_comparison.yaml")
    five_e = load_five_e_config(CONFIGS / "5e_multidate.yaml")
    assert five_c.expected_placements == 64
    assert five_d.alpha_grid == tuple(Decimal(x) for x in ("0", ".25", ".5", ".75", "1"))
    assert (len(five_e.dates), len(five_e.windows)) == (12, 6)


def test_invalid_alpha_is_rejected(tmp_path):
    path = _variant(tmp_path, "5d_fill_comparison.yaml", lambda row: row.update(alpha_grid=["0", "1.2"]))
    with pytest.raises(ConfigError, match="alpha_grid"):
        load_five_d_config(path)


def test_invalid_ttl_is_rejected(tmp_path):
    path = _variant(tmp_path, "5c_virtual_truth.yaml", lambda row: row.update(ttls_seconds=[0]))
    with pytest.raises(ConfigError, match="ttls_seconds"):
        load_five_c_config(path)


def test_overlapping_split_is_rejected(tmp_path):
    def overlap(row):
        row["dates"]["validation"][0] = row["dates"]["calibration"][0]
    path = _variant(tmp_path, "5e_multidate.yaml", overlap)
    with pytest.raises(ConfigError, match="disjoint"):
        load_five_e_config(path)


def test_unknown_model_and_unknown_field_are_rejected(tmp_path):
    model_path = _variant(tmp_path, "5d_fill_comparison.yaml", lambda row: row.update(model_names=["MAGIC_FILL"]))
    with pytest.raises(ConfigError, match="unsupported model"):
        load_five_d_config(model_path)
    field_path = _variant(tmp_path, "5c_virtual_truth.yaml", lambda row: row.update(triger_seconds=[5]))
    with pytest.raises(ConfigError, match="unknown fields"):
        load_five_c_config(field_path)


def test_random_seed_must_be_integer_when_present(tmp_path):
    path = _variant(tmp_path, "5c_virtual_truth.yaml", lambda row: row.update(random_seed="7", randomness="seeded"))
    with pytest.raises(ConfigError, match="random_seed"):
        load_five_c_config(path)


def test_canonical_fingerprint_ignores_yaml_comments(tmp_path):
    source = CONFIGS / "5c_virtual_truth.yaml"
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    text = source.read_text(encoding="utf-8")
    first.write_text(text, encoding="utf-8")
    second.write_text("# semantically irrelevant comment\n" + text, encoding="utf-8")
    a, b = load_five_c_config(first), load_five_c_config(second)
    assert a.raw_file_sha256 != b.raw_file_sha256
    assert a.canonical_config_sha256 == b.canonical_config_sha256


def test_5c_intents_use_config_values():
    config = load_five_c_config(CONFIGS / "5c_virtual_truth.yaml")
    placement = replace(config.placement, sides=("sell",), trigger_seconds=(7,))
    custom = replace(config, placement=placement, virtual_sizes=(Decimal("0.003"),), ttls_seconds=(2,), expected_placements=1)
    intents = intents_from_config(custom)
    assert len(intents) == 1
    assert (intents[0].side, intents[0].size, intents[0].ttl_seconds) == ("sell", Decimal("0.003"), 2)
    assert intents[0].virtual_order_id == "v-07-sell-0.003-2s"


def test_5d_comparison_uses_only_configured_alpha_models():
    config = load_five_d_config(CONFIGS / "5d_fill_comparison.yaml")
    custom = replace(config, alpha_grid=(Decimal("0.13"), Decimal("0.61")), touch_fill_enabled=False, model_names=("alpha=0.13", "alpha=0.61"))
    result = build_comparison(config.raw_input_path, config.truth_input_path, custom)
    assert {prediction.model for prediction in result["predictions"]} == {"alpha=0.13", "alpha=0.61"}
    assert len(result["predictions"]) == 128


def test_5e_dry_run_uses_configured_dates_windows_and_grid():
    config = load_five_e_config(CONFIGS / "5e_multidate.yaml")
    custom = replace(config, calibration_dates=config.calibration_dates[:1], validation_dates=config.validation_dates[:1], test_dates=config.test_dates[:1], windows=config.windows[:2])
    plan = dry_run_plan(custom)
    assert plan["dates"] == 3
    assert plan["windows"] == 6
    assert plan["split"] == {"calibration": 1, "validation": 1, "test": 1}
    assert plan["expected_total_placements"] == 6 * config.placements_per_window
    assert plan["full_run_executed"] is False


def test_model_factory_has_no_independent_default_grid():
    models = build_model_specs((Decimal("0.13"), Decimal("0.61")), touch_fill_enabled=False)
    assert [model.name for model in models] == ["alpha=0.13", "alpha=0.61"]
