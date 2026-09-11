"""Immutable multi-day window catalogue and outcome-access gates.

Catalogue construction may inspect market-data quality only. Predictive labels,
strategy decisions and PnL are deliberately absent from this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from lobcap.backtest.engine import MarketBook
from lobcap.backtest.replay import iter_events
from lobcap.backtest.types import NS, jsonable
from lobcap.config.common import sha256_file


CATALOGUE_VERSION = "signal-window-catalogue-v1"
DEVELOPMENT_DEBUG = "DEVELOPMENT_DEBUG"
DEVELOPMENT = "DEVELOPMENT"
VALIDATION_LOCKED = "VALIDATION_LOCKED"
PILOT_OOS_LOCKED = "PILOT_OOS_LOCKED"
FINAL_UNTOUCHED_RESERVED = "FINAL_UNTOUCHED_RESERVED"
ROLES = frozenset({DEVELOPMENT_DEBUG, DEVELOPMENT, VALIDATION_LOCKED,
                   PILOT_OOS_LOCKED, FINAL_UNTOUCHED_RESERVED})
OPEN_ROLES = frozenset({DEVELOPMENT_DEBUG, DEVELOPMENT})


def _ns(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * NS)


@dataclass(frozen=True, slots=True)
class CatalogueWindow:
    window_id: str
    date: str
    start_ns: int
    end_ns: int
    warmup_ns: int
    role: str

    def __post_init__(self):
        if self.role not in ROLES or self.end_ns-self.start_ns != 20*60*NS:
            raise ValueError("catalogue windows must be fixed 20-minute role-bound intervals")
        if self.warmup_ns != 5*60*NS or not self.window_id:
            raise ValueError("catalogue windows require a five-minute warmup and stable id")

    @property
    def outcomes_locked(self):
        return self.role not in OPEN_ROLES


def fixed_pilot_windows():
    """Return the preregistered windows without reading any market outcomes."""
    roles = {"2026-09-07": DEVELOPMENT, "2026-09-08": DEVELOPMENT,
             "2026-09-09": VALIDATION_LOCKED, "2026-09-10": PILOT_OOS_LOCKED}
    rows = []
    for date, role in roles.items():
        for hour in (0, 6, 12, 18):
            stamp = f"{date}T{hour:02d}:10:00Z"
            start = _ns(stamp)
            rows.append(CatalogueWindow(f"btcusdt-{date}-{hour:02d}10-20m",
                date, start, start+20*60*NS, 5*60*NS, role))
    return tuple(rows)


def assert_outcomes_allowed(window: CatalogueWindow):
    if window.outcomes_locked:
        raise PermissionError(f"{window.window_id} is {window.role}; outcomes remain sealed")


def _quantiles(values):
    if not values:
        return {"count": 0, "p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(values)
    pick = lambda p: ordered[min(len(ordered)-1, int((len(ordered)-1)*p))]
    return {"count": len(ordered), "p50": pick(.50), "p95": pick(.95),
            "p99": pick(.99), "max": ordered[-1]}


def market_quality(initial, events, window: CatalogueWindow, tick: Decimal):
    """Compute bounded operational diagnostics without labels or PnL."""
    book = MarketBook(initial)
    spreads, bid_depth, ask_depth, delays, log_returns = [], [], [], [], []
    depth_count = trade_count = 0
    previous_mid = book.mid
    for event in events:
        book.apply(event)
        if not window.start_ns <= event.time_ns < window.end_ns:
            if event.kind == "depth":
                previous_mid = book.mid
            continue
        delays.append(max(0, event.receive_ns-event.time_ns))
        if event.kind == "trade":
            trade_count += 1
            continue
        depth_count += 1
        spreads.append((book.ask-book.bid)/tick)
        bid_depth.append(book.bids[book.bid]); ask_depth.append(book.asks[book.ask])
        if book.mid != previous_mid and previous_mid > 0:
            log_returns.append((book.mid/previous_mid).ln())
        previous_mid = book.mid
    seconds = Decimal(window.end_ns-window.start_ns)/Decimal(NS)
    realized = sum((value*value for value in log_returns), Decimal(0)).sqrt()*Decimal(10000)
    return jsonable({"depth_events": depth_count, "trade_events": trade_count,
        "trade_intensity_per_second": Decimal(trade_count)/seconds,
        "receive_delay_ns": _quantiles(delays), "spread_ticks": _quantiles(spreads),
        "best_bid_depth": _quantiles(bid_depth), "best_ask_depth": _quantiles(ask_depth),
        "realized_mid_volatility_bps": realized})


def seal_window(window: CatalogueWindow, sample_path, initial, sample_manifest):
    """Validate and bind one prepared sample to its preregistered window."""
    path = Path(sample_path)
    validation = json.loads((path/"validation.json").read_text(encoding="utf-8"))
    expected = {"sample_id": window.window_id, "start_ns": window.start_ns,
                "end_ns": window.end_ns, "warmup_ns": window.warmup_ns}
    actual = sample_manifest.get("sample", {})
    if any(actual.get(key) != value for key, value in expected.items()):
        raise ValueError("prepared sample does not match preregistered window")
    segments = sample_manifest.get("acquisition_segments")
    if sample_manifest.get("status") != "PASS" or validation.get("status") != "PASS":
        raise ValueError("only quality-PASS samples can be sealed")
    if not isinstance(segments, list) or len(segments) != 1:
        raise ValueError("exactly one verified acquisition segment is required")
    files = {name: sha256_file(path/name) for name in sample_manifest["files"]}
    if files != sample_manifest["files"]:
        raise ValueError("normalized sample changed before sealing")
    quality = ({"status": "SEALED", "reason": "predictive and descriptive market outcomes are locked"}
        if window.outcomes_locked else market_quality(initial, iter_events(path), window,
            Decimal(sample_manifest["instrument"]["tick"])))
    row = {"catalogue_version": CATALOGUE_VERSION, "quality_status": "PASS",
        "window": jsonable(window), "role": window.role,
        "outcomes_access": "LOCKED" if window.outcomes_locked else "DEVELOPMENT_ONLY",
        "sample_sha256": sample_manifest["sample_sha256"],
        "normalization_version": actual["normalization_version"],
        "source_schema_version": sample_manifest.get("source_schema_version"),
        "acquisition_segments": segments,
        "acquisition_connections": sample_manifest.get("acquisition_connections"),
        "raw_input_artifacts": sample_manifest["input_artifacts"],
        "normalized_artifacts": files, "validation": validation, "market_quality": quality}
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
    row["sealed_manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return row


def excluded_window(window: CatalogueWindow, reason: str):
    if not reason:
        raise ValueError("excluded window requires a retained reason")
    return {"catalogue_version": CATALOGUE_VERSION, "quality_status": "EXCLUDED",
            "window": jsonable(window), "role": window.role,
            "outcomes_access": "LOCKED", "exclusion_reason": reason}


def seal_catalogue(rows):
    rows = tuple(rows)
    expected = {window.window_id for window in fixed_pilot_windows()}
    actual = {row.get("window", {}).get("window_id") for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise ValueError("catalogue must retain every preregistered window exactly once")
    payload = {"catalogue_version": CATALOGUE_VERSION, "windows": rows}
    payload["catalogue_sha256"] = hashlib.sha256(json.dumps(payload,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload
