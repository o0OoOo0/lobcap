"""Canonical definitions for the bounded causal feature set."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

from .signal_contracts import FeatureDefinition


FEATURE_SET_VERSION = "core-market-features-v3"
WINDOWS_NS = (100_000_000, 500_000_000, 1_000_000_000, 5_000_000_000)
DEFAULT_STALE_AFTER_NS = 500_000_000


def _definition(name, unit, sources, *, lookback=0, warmup=0, trigger="event",
                stale=DEFAULT_STALE_AFTER_NS, missing="unavailable", depth=0):
    return FeatureDefinition(name, FEATURE_SET_VERSION, unit, tuple(sources), lookback,
        warmup, trigger, stale, missing, "raw", True, depth)


def _definitions():
    rows = [
        _definition("mid", "USDT", ("depth",)),
        _definition("spread_ticks", "ticks", ("depth",)),
        _definition("spread_bps", "bps", ("depth",)),
        _definition("microprice", "USDT", ("depth",), depth=1),
        _definition("microprice_minus_mid_ticks", "ticks", ("depth",), depth=1),
        _definition("vamp_5", "USDT", ("depth",), depth=5),
        _definition("vamp_5_minus_mid_ticks", "ticks", ("depth",), depth=5),
        _definition("book_age_ns", "ns", ("depth",), stale=0, missing="carry_with_age"),
        _definition("feed_delay_ns", "ns", ("depth",), stale=0, missing="carry_with_age"),
        _definition("bid_top_depth", "BTC", ("depth",), depth=1),
        _definition("ask_top_depth", "BTC", ("depth",), depth=1),
        _definition("total_top_depth", "BTC", ("depth",), depth=1),
    ]
    for count in (1, 2, 5, 10):
        rows.extend((
            _definition(f"imbalance_{count}", "dimensionless", ("depth",), depth=count),
            _definition(f"bid_depth_{count}", "BTC", ("depth",), depth=count),
            _definition(f"ask_depth_{count}", "BTC", ("depth",), depth=count),
        ))
    for window in WINDOWS_NS:
        suffix = f"_{window // 1_000_000}ms"
        common = {"lookback": window, "warmup": window, "trigger": "trade_or_depth"}
        rows.extend((
            _definition("signed_trade_volume" + suffix, "BTC", ("trade",), **common),
            _definition("trade_volume_imbalance" + suffix, "dimensionless", ("trade",), **common),
            _definition("trade_intensity" + suffix, "events_per_second", ("trade",), **common),
            _definition("bbo_ofi" + suffix, "BTC", ("depth",), **common),
            _definition("mid_return" + suffix, "bps", ("depth",), **common),
        ))
    return tuple(sorted(rows, key=lambda row: row.feature_name))


FEATURE_DEFINITIONS = _definitions()
FEATURE_REGISTRY = {row.feature_name: row for row in FEATURE_DEFINITIONS}
if len(FEATURE_REGISTRY) != len(FEATURE_DEFINITIONS):
    raise RuntimeError("duplicate feature definition")


def feature_definition_sha256(definitions=FEATURE_DEFINITIONS):
    payload = [asdict(row) for row in sorted(definitions, key=lambda row: row.feature_name)]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
