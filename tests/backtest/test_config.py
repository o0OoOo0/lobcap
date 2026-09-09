from decimal import Decimal

import pytest

from lobcap.config.backtest import load_backtest_config, variant
from lobcap.config.experiment import ConfigError, ROOT
from lobcap.backtest.runner import parse_setting


def test_config_and_lossless_alpha():
    config = load_backtest_config(ROOT / "configs/backtest/binance_demo.yaml")
    a, b = variant(config, alpha="0.501"), variant(config, alpha="0.502")
    assert a.model.name != b.model.name
    assert a.effective_sha256 != b.effective_sha256
    assert a.alpha == Decimal("0.501")
    assert variant(config, alpha=None).model.touch_fill
    exact="0.12345678901234567890123456789"
    assert variant(config,alpha=parse_setting("alpha",exact)).alpha==Decimal(exact)


@pytest.mark.parametrize("changes", [{"alpha": "NaN"}, {"alpha": "1.01"}, {"entry_ms": -1}, {"entry_ms": True}, {"size": 0}, {"surprise": 1}])
def test_bad_settings(changes):
    with pytest.raises(ConfigError):
        variant(load_backtest_config(ROOT / "configs/backtest/binance_demo.yaml"), **changes)
