"""Bounded Binance USDⓈ-M REST and WebSocket runtime validation.

This is a validation probe, not the long-running collector. It stores five raw
exchange payloads per stream and a compact machine-readable report under
artifacts/validation/; it never writes to /data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "artifacts" / "validation"
REPORT_PATH = ARTIFACT_DIR / "binance_runtime_report.json"
DEPTH_SAMPLE_PATH = ARTIFACT_DIR / "binance_depth_samples.jsonl"
TRADE_SAMPLE_PATH = ARTIFACT_DIR / "binance_aggtrade_samples.jsonl"

PING_URL = "https://fapi.binance.com/fapi/v1/ping"
DEPTH_REST_URL = "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=5"
DEPTH_WS_URL = (
    "wss://fstream.binance.com/public/stream?streams=btcusdt@depth@100ms"
)
TRADE_WS_URL = (
    "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade"
)

DEPTH_FIELDS = {"e", "E", "T", "s", "U", "u", "pu", "b", "a"}
TRADE_FIELDS = {"e", "E", "a", "s", "p", "q", "f", "l", "T", "m"}
MAX_WS_SECONDS = 220


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def command_output(command: list[str]) -> str:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
    except Exception as exc:  # Evidence belongs in the report even on failure.
        return f"ERROR: {exc!r}"


class ControlFrameObserver(logging.Handler):
    """Observe actual protocol-level server PING and automatic client PONG."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.pings_received = 0
        self.pongs_sent = 0
        self.evidence: list[dict[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        direction = None
        if message.startswith("< PING"):
            self.pings_received += 1
            direction = "server_ping_received"
        elif message.startswith("> PONG"):
            self.pongs_sent += 1
            direction = "automatic_pong_sent"
        if direction and len(self.evidence) < 10:
            self.evidence.append(
                {"utc": utc_now(), "direction": direction, "frame": message}
            )


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(int((len(ordered) - 1) * fraction), len(ordered) - 1)
    return round(ordered[index], 3)


async def rest_probe(session: aiohttp.ClientSession, name: str, url: str) -> dict:
    started = utc_now()
    try:
        async with session.get(url) as response:
            body = await response.text()
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None

            schema: dict[str, Any] = {
                "json_type": type(parsed).__name__ if parsed is not None else None,
                "keys": sorted(parsed) if isinstance(parsed, dict) else None,
            }
            if name == "depth" and isinstance(parsed, dict):
                bids = parsed.get("bids")
                asks = parsed.get("asks")
                schema.update(
                    {
                        "required_keys_present": all(
                            key in parsed
                            for key in ("lastUpdateId", "E", "T", "bids", "asks")
                        ),
                        "bids_type": type(bids).__name__,
                        "asks_type": type(asks).__name__,
                        "bid_level_widths": sorted(
                            {len(level) for level in bids if isinstance(level, list)}
                        )
                        if isinstance(bids, list)
                        else None,
                        "ask_level_widths": sorted(
                            {len(level) for level in asks if isinstance(level, list)}
                        )
                        if isinstance(asks, list)
                        else None,
                    }
                )
            elif name == "ping":
                schema["empty_object"] = parsed == {}

            return {
                "name": name,
                "url": url,
                "utc_started": started,
                "utc_completed": utc_now(),
                "http_status": response.status,
                "is_451": response.status == 451,
                "is_403": response.status == 403,
                "content_type": response.headers.get("Content-Type"),
                "schema": schema,
                "response": parsed if parsed is not None else body[:1000],
                "error": None,
            }
    except Exception as exc:
        return {
            "name": name,
            "url": url,
            "utc_started": started,
            "utc_completed": utc_now(),
            "http_status": None,
            "is_451": False,
            "is_403": False,
            "schema": None,
            "response": None,
            "error": repr(exc),
        }


async def ws_probe(
    name: str,
    url: str,
    event_name: str,
    required_fields: set[str],
    minimum_messages: int,
) -> tuple[dict, list[dict]]:
    started = utc_now()
    observer = ControlFrameObserver()
    logger = logging.getLogger(f"binance.validation.{name}.{time.time_ns()}")
    logger.handlers.clear()
    logger.addHandler(observer)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    samples: list[dict] = []
    count = 0
    field_union: set[str] = set()
    missing_field_messages = 0
    pairs = 0
    mismatches = 0
    first_mismatch = None
    previous_u = None
    latency_event_ms: list[float] = []
    latency_transaction_ms: list[float] = []
    connected_utc = None
    error = None
    unexpected_close = None
    close_code = None
    close_reason = None

    deadline = time.monotonic() + MAX_WS_SECONDS
    try:
        async with websockets.connect(
            url,
            open_timeout=15,
            ping_interval=None,
            close_timeout=10,
            max_queue=2048,
            max_size=8 * 1024 * 1024,
            logger=logger,
        ) as ws:
            connected_utc = utc_now()
            unexpected_close = False
            while time.monotonic() < deadline:
                remaining = max(deadline - time.monotonic(), 0.001)
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break

                received_ns = time.time_ns()
                message = json.loads(raw)
                payload = message.get("data", message)
                if payload.get("e") != event_name:
                    continue

                count += 1
                field_union.update(payload)
                if not required_fields.issubset(payload):
                    missing_field_messages += 1
                if len(samples) < 5:
                    samples.append(payload)

                event_ms = payload.get("E")
                transaction_ms = payload.get("T")
                if event_ms is not None:
                    latency_event_ms.append(received_ns / 1e6 - int(event_ms))
                if transaction_ms is not None:
                    latency_transaction_ms.append(
                        received_ns / 1e6 - int(transaction_ms)
                    )

                if name == "depth":
                    current_pu = payload.get("pu")
                    current_u = payload.get("u")
                    if previous_u is not None:
                        pairs += 1
                        if current_pu is None or int(current_pu) != int(previous_u):
                            mismatches += 1
                            if first_mismatch is None:
                                first_mismatch = {
                                    "previous_u": previous_u,
                                    "current_U": payload.get("U"),
                                    "current_u": current_u,
                                    "current_pu": current_pu,
                                }
                    if current_u is not None:
                        previous_u = int(current_u)

                if (
                    count >= minimum_messages
                    and observer.pings_received > 0
                    and observer.pongs_sent > 0
                ):
                    break

            close_code = ws.close_code
            close_reason = ws.close_reason
    except ConnectionClosed as exc:
        unexpected_close = True
        close_code = exc.code
        close_reason = exc.reason
        error = repr(exc)
    except Exception as exc:
        unexpected_close = True
        error = repr(exc)

    result = {
        "name": name,
        "url": url,
        "utc_started": started,
        "utc_connected": connected_utc,
        "utc_completed": utc_now(),
        "websockets_version": websockets.__version__,
        "connected": connected_utc is not None,
        "message_count": count,
        "minimum_required": minimum_messages,
        "sample_count": len(samples),
        "observed_fields": sorted(field_union),
        "required_fields": sorted(required_fields),
        "messages_missing_required_fields": missing_field_messages,
        "server_ping_frames_received": observer.pings_received,
        "automatic_pong_frames_sent": observer.pongs_sent,
        "auto_pong_observed": (
            observer.pings_received > 0 and observer.pongs_sent > 0
        ),
        "control_frame_evidence": observer.evidence,
        "unexpected_close": unexpected_close,
        "close_code_before_local_close": close_code,
        "close_reason_before_local_close": close_reason,
        "error": error,
        "recv_minus_event_ms_diagnostic": {
            "p50": percentile(latency_event_ms, 0.50),
            "p95": percentile(latency_event_ms, 0.95),
            "p99": percentile(latency_event_ms, 0.99),
            "interpretation": "diagnostic only; not a precise network-latency measurement",
        },
        "recv_minus_transaction_ms_diagnostic": {
            "p50": percentile(latency_transaction_ms, 0.50),
            "p95": percentile(latency_transaction_ms, 0.95),
            "p99": percentile(latency_transaction_ms, 0.99),
            "interpretation": "diagnostic only; not a precise network-latency measurement",
        },
    }
    if name == "depth":
        result["sequence_sanity"] = {
            "rule": "current.pu == previous.u",
            "total_pairs": pairs,
            "mismatches": mismatches,
            "first_mismatch": first_mismatch,
            "spot_style_U_check_performed": False,
        }
    return result, samples


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def is_success(report: dict) -> bool:
    ping = report["rest"]["ping"]
    depth_rest = report["rest"]["depth"]
    depth = report["websocket"]["depth"]
    trade = report["websocket"]["aggTrade"]
    ping_schema = ping.get("schema") or {}
    depth_rest_schema = depth_rest.get("schema") or {}
    return all(
        (
            ping["http_status"] == 200,
            ping_schema.get("empty_object") is True,
            depth_rest["http_status"] == 200,
            depth_rest_schema.get("required_keys_present") is True,
            depth["connected"],
            depth["message_count"] >= 100,
            depth["messages_missing_required_fields"] == 0,
            depth["sequence_sanity"]["mismatches"] == 0,
            depth["auto_pong_observed"],
            depth["unexpected_close"] is False,
            trade["connected"],
            trade["message_count"] >= 10,
            trade["messages_missing_required_fields"] == 0,
            trade["auto_pong_observed"],
            trade["unexpected_close"] is False,
        )
    )


async def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "validation": "Prompt 2A — Binance runtime validation only",
        "host": command_output(["hostname"]),
        "utc_started": utc_now(),
        "clock": {
            "timedatectl": command_output(["timedatectl", "status"]),
            "chronyc_tracking": command_output(["chronyc", "tracking"]),
        },
        "rest": {},
        "websocket": {},
        "connection_24h_disconnect": "DOC_VERIFIED, NOT_RUNTIME_TESTED",
    }

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        ping, depth_rest = await asyncio.gather(
            rest_probe(session, "ping", PING_URL),
            rest_probe(session, "depth", DEPTH_REST_URL),
        )
    report["rest"] = {"ping": ping, "depth": depth_rest}

    (depth_result, depth_samples), (trade_result, trade_samples) = await asyncio.gather(
        ws_probe("depth", DEPTH_WS_URL, "depthUpdate", DEPTH_FIELDS, 100),
        ws_probe("aggTrade", TRADE_WS_URL, "aggTrade", TRADE_FIELDS, 10),
    )
    report["websocket"] = {
        "depth": depth_result,
        "aggTrade": trade_result,
    }

    write_jsonl(DEPTH_SAMPLE_PATH, depth_samples)
    write_jsonl(TRADE_SAMPLE_PATH, trade_samples)
    report["artifacts"] = {
        "depth_samples": str(DEPTH_SAMPLE_PATH),
        "aggtrade_samples": str(TRADE_SAMPLE_PATH),
    }
    report["utc_completed"] = utc_now()
    report["status"] = "RUNTIME_VERIFIED" if is_success(report) else "FAILED"
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "RUNTIME_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
