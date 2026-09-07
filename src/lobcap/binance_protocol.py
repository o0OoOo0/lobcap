"""Binance USDⓈ-M Futures protocol helpers.

Current Phase 0 semantics:
- first event after snapshot: U <= L <= u
- after sync: event.pu == previous_event.u
"""

from __future__ import annotations

from typing import Any, Mapping

SCHEMA_VERSION = 2


def payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return exchange payload from v2 envelope or a legacy flat record."""
    p = record.get("payload")
    return p if isinstance(p, Mapping) else record


def recv_ns(record: Mapping[str, Any]) -> int:
    if "ts_recv_ns" in record:
        return int(record["ts_recv_ns"])
    if "ts_recv" in record:  # legacy
        return int(record["ts_recv"])
    raise KeyError("record has no receive timestamp")


def first_event_covers_snapshot(event: Mapping[str, Any], last_update_id: int) -> bool:
    p = payload(event)
    return int(p["U"]) <= int(last_update_id) <= int(p["u"])


def event_is_older_than_snapshot(event: Mapping[str, Any], last_update_id: int) -> bool:
    """Official USDⓈ-M rule: drop if u < snapshot lastUpdateId."""
    return int(payload(event)["u"]) < int(last_update_id)


def futures_contiguous(previous_u: int | None, event: Mapping[str, Any]) -> bool:
    """After synchronization, Futures continuity is pu == previous u."""
    if previous_u is None:
        return True
    p = payload(event)
    pu = p.get("pu")
    return pu is not None and int(pu) == int(previous_u)


def exchange_time_ms(record: Mapping[str, Any]) -> int | None:
    p = payload(record)
    value = p.get("T")
    if value is None:
        value = p.get("E")
    return None if value is None else int(value)
