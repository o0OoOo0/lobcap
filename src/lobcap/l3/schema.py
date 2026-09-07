"""Verified Tardis line framing and Coinbase Full-channel schemas.

The Tardis receive timestamp is the text before the first space.  The JSON
payload following it is preserved as the exchange-native Coinbase message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


SUPPORTED_TYPES = frozenset(
    {"full_snapshot", "received", "open", "match", "done", "change"}
)
LIFECYCLE_TYPES = SUPPORTED_TYPES - {"full_snapshot"}


class SchemaError(ValueError):
    """Raised when a raw line does not match the runtime-verified schema."""


@dataclass(frozen=True, slots=True)
class NativeMessage:
    local_timestamp: str
    payload: dict[str, Any]
    source_line: int | None = None

    @property
    def type(self) -> str:
        return str(self.payload["type"])

    @property
    def sequence(self) -> int:
        return int(self.payload["sequence"])


COMMON_REQUIRED = frozenset({"type", "product_id", "sequence", "time"})
REQUIRED_FIELDS = {
    "full_snapshot": COMMON_REQUIRED | {"generated", "bids", "asks"},
    "received": COMMON_REQUIRED | {"order_id", "order_type", "side"},
    "open": COMMON_REQUIRED
    | {"order_id", "side", "price", "remaining_size"},
    "match": COMMON_REQUIRED
    | {"maker_order_id", "taker_order_id", "side", "price", "size"},
    "done": COMMON_REQUIRED | {"order_id", "side", "reason"},
    "change": COMMON_REQUIRED
    | {"order_id", "side", "reason", "old_size", "new_size"},
}


def _require(payload: Mapping[str, Any], names: Iterable[str], message_type: str) -> None:
    missing = sorted(name for name in names if name not in payload)
    if missing:
        raise SchemaError(f"{message_type} missing required fields: {missing}")


def validate_payload(payload: Mapping[str, Any]) -> None:
    message_type = payload.get("type")
    if message_type not in SUPPORTED_TYPES:
        if message_type is None:
            raise SchemaError("Coinbase message has no type")
        # Preserve unknown exchange-native messages so the reconstructor can
        # invalidate the segment and include explicit evidence in its report.
        return
    _require(payload, REQUIRED_FIELDS[str(message_type)], str(message_type))

    sequence = payload["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise SchemaError(f"{message_type} sequence must be an integer")

    if message_type == "full_snapshot":
        for side_name in ("bids", "asks"):
            entries = payload[side_name]
            if not isinstance(entries, list):
                raise SchemaError(f"full_snapshot {side_name} must be a list")
            for entry in entries:
                if not isinstance(entry, list) or len(entry) != 3:
                    raise SchemaError(
                        f"full_snapshot {side_name} entry must be [price,size,order_id]"
                    )
    elif message_type == "received":
        # Limit and market messages have different optional quantity fields.
        if not any(name in payload for name in ("size", "funds")):
            raise SchemaError("received requires observed size or funds")
    elif message_type == "change":
        reason = payload["reason"]
        if reason in {"modify_order", "remainder_after_modification"}:
            _require(payload, {"old_price", "new_price"}, f"change({reason})")
        elif reason == "STP":
            _require(payload, {"price"}, "change(STP)")
    elif message_type == "done" and payload["reason"] not in {"filled", "canceled"}:
        raise SchemaError(f"unsupported done reason: {payload['reason']!r}")


def parse_tardis_line(line: str, source_line: int | None = None) -> NativeMessage:
    line = line.rstrip("\n")
    if not line:
        raise SchemaError("empty Tardis line is a disconnect marker, not a message")
    try:
        local_timestamp, raw_payload = line.split(" ", 1)
    except ValueError as exc:
        raise SchemaError("Tardis line has no receive-timestamp prefix") from exc
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"invalid exchange-native JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SchemaError("Coinbase exchange-native payload must be an object")
    validate_payload(payload)
    return NativeMessage(local_timestamp, payload, source_line)


def iter_tardis_lines(path: str | Path) -> Iterable[NativeMessage]:
    with Path(path).open("rt", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            yield parse_tardis_line(line, line_number)
