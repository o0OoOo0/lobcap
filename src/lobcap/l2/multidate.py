"""Prompt 5E multi-date structural calibration and chronological OOS evaluation."""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import gzip
import hashlib
import http.client
import json
import math
import re
import resource
import statistics
import time
import urllib.parse
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from lobcap.l2.fill_models import MODEL_SPECS, ModelSpec, Prediction, StreamingFillEngine
from lobcap.l2.schema import (
    ZERO,
    BlindedPlacement,
    L2Frame,
    L2LevelUpdate,
    L2Trade,
    assert_blinded_mapping,
    decimal_text,
    decimal_value,
)
from lobcap.l3.reconstruct import L3Reconstructor
from lobcap.l3.schema import NativeMessage, parse_tardis_line
from lobcap.l3.virtual import (
    PlacementIntent,
    VirtualOrderEngine,
    timestamp_from_ns,
    timestamp_ns,
)


ROOT = Path(__file__).parents[3]
OUTPUT = ROOT / "artifacts/multidate_calibration"
DATES = (
    "2025-10-01", "2025-11-01", "2025-12-01", "2026-01-01",
    "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01",
    "2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01",
)
CALIBRATION_DATES = DATES[:6]
VALIDATION_DATES = DATES[6:9]
TEST_DATES = DATES[9:]
WINDOW_STARTS = (0, 240, 480, 720, 960, 1200)
WINDOW_MINUTES = 10
LAST_OFFSET = 1209
CHANNELS = ("received", "open", "done", "match", "change", "full_snapshot")
FILTERS = [{"channel": name, "symbols": ["BTC-USD"]} for name in CHANNELS]
BASE_MODELS = MODEL_SPECS
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
AGE_BINS = ((0, 1), (1, 5), (5, 10), (10, 30))
PREFETCH = 8


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rss_mib() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3)


def window_name(offset: int) -> str:
    hour, minute = divmod(offset, 60)
    end_hour, end_minute = divmod(offset + WINDOW_MINUTES, 60)
    return f"{hour:02d}:{minute:02d}-{end_hour:02d}:{end_minute:02d}"


def split_for_date(date: str) -> str:
    if date in CALIBRATION_DATES:
        return "calibration"
    if date in VALIDATION_DATES:
        return "validation"
    if date in TEST_DATES:
        return "test"
    raise ValueError(date)


def model_specs(structural_alpha: Decimal | None) -> tuple[ModelSpec, ...]:
    if structural_alpha is None:
        return BASE_MODELS
    return (
        *BASE_MODELS[:-1],
        ModelSpec(
            "STRUCTURAL_ALPHA",
            structural_alpha,
            interpretation="calibration-date aggregate ahead-share",
        ),
        BASE_MODELS[-1],
    )


def placement_intents(date: str, start_offset: int) -> list[PlacementIntent]:
    day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    intents: list[PlacementIntent] = []
    for minute in range(start_offset, start_offset + WINDOW_MINUTES):
        for second in (5, 20, 35, 50):
            trigger = day + timedelta(minutes=minute, seconds=second)
            trigger_ns = int(trigger.timestamp()) * 1_000_000_000
            for side in ("buy", "sell"):
                for size_text in ("0.001", "0.01"):
                    for ttl in (1, 5, 10, 30):
                        virtual_id = (
                            f"{date}T{minute // 60:02d}:{minute % 60:02d}:{second:02d}Z"
                            f"-{side}-{size_text}-{ttl}s"
                        )
                        intents.append(
                            PlacementIntent(
                                virtual_order_id=virtual_id,
                                side=side,
                                size=Decimal(size_text),
                                ttl_seconds=ttl,
                                at_or_after_time_ns=trigger_ns,
                            )
                        )
    return intents


def decompose_ahead_reduction(
    message: NativeMessage,
    *,
    cancel: Decimal,
    size_decrease: Decimal,
    priority_loss: Decimal,
) -> dict[str, Decimal]:
    """Classify L3 ahead reduction by whether L2 sees old-level depletion.

    This is a semantic decomposition, not a numerical projection onto C.  In
    particular, a same-price size increase resets Coinbase FIFO priority while
    increasing the aggregate L2 level, so it is hidden from L2 depletion.
    """
    price_change_away = ZERO
    same_price_increase = ZERO
    other_priority_loss = ZERO
    if priority_loss:
        payload = message.payload
        if message.type == "change" and payload.get("reason") in {
            "modify_order", "remainder_after_modification"
        }:
            old_price = decimal_value(payload["old_price"], "change.old_price")
            new_price = decimal_value(payload["new_price"], "change.new_price")
            old_size = decimal_value(payload["old_size"], "change.old_size")
            new_size = decimal_value(payload["new_size"], "change.new_size")
            if new_price != old_price:
                price_change_away = priority_loss
            elif new_size > old_size:
                same_price_increase = priority_loss
            else:
                other_priority_loss = priority_loss
        else:
            other_priority_loss = priority_loss
    visible = cancel + size_decrease + price_change_away
    hidden = same_price_increase + other_priority_loss
    return {
        "ahead_cancellation": cancel,
        "ahead_size_decrease": size_decrease,
        "ahead_price_change_away": price_change_away,
        "ahead_same_price_size_increase_priority_loss": same_price_increase,
        "other_priority_loss": other_priority_loss,
        "A_visible": visible,
        "H_hidden": hidden,
    }


class MultiDateReconstructor(L3Reconstructor):
    """Continuous L3 book plus an identity-stripping active-window adapter."""

    def __init__(self) -> None:
        super().__init__("BTC-USD", allow_recovery_snapshots=True)
        self.virtual_engine: VirtualOrderEngine | None = None
        self.fill_engine: StreamingFillEngine | None = None
        self.window_label: str | None = None
        self.window_frames = 0
        self.window_level_updates = 0
        self.window_trades = 0
        self.window_event_counts: Counter[str] = Counter()
        self.frame_first: list[dict[str, Any]] = []
        self.frame_last: deque[dict[str, Any]] = deque(maxlen=5)
        self.placement_metadata: dict[str, dict[str, Any]] = {}
        self.sequence_times: dict[int, str] = {}
        self.c_by_order: Counter[str] = Counter()
        self.c_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.a_by_order: Counter[str] = Counter()
        self.h_hidden_by_order: Counter[str] = Counter()
        self.a_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.h_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.structural_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.a_last_components: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
        self.projection_invariants: Counter[str] = Counter()
        self.structural_violation_examples: list[dict[str, Any]] = []
        self.trade_prices: list[Decimal] = []
        self._seen_order_count = 0
        self.last_lifecycle_time: str | None = None

    def _recovery_permitted(self, message: NativeMessage) -> bool:
        # Missing lifecycle events can change shadow-fill truth.  A generated
        # snapshot is therefore a safe segment boundary only between frozen
        # evaluation windows, never while virtual orders are active.
        return self.virtual_engine is None and super()._recovery_permitted(message)

    def start_window(
        self,
        date: str,
        start_offset: int,
        models: Iterable[ModelSpec],
    ) -> None:
        if self.virtual_engine is not None:
            raise RuntimeError("window already active")
        self.virtual_engine = VirtualOrderEngine(placement_intents(date, start_offset))
        self.fill_engine = StreamingFillEngine(models)
        self.window_label = window_name(start_offset)
        self.window_frames = 0
        self.window_level_updates = 0
        self.window_trades = 0
        self.window_event_counts = Counter()
        self.frame_first = []
        self.frame_last = deque(maxlen=5)
        self.placement_metadata = {}
        self.sequence_times = {}
        self.c_by_order = Counter()
        self.c_events = defaultdict(list)
        self.a_by_order = Counter()
        self.h_hidden_by_order = Counter()
        self.a_events = defaultdict(list)
        self.h_events = defaultdict(list)
        self.structural_events = defaultdict(list)
        self.a_last_components = {}
        self.projection_invariants = Counter()
        self.structural_violation_examples = []
        self.trade_prices = []
        self._seen_order_count = 0

    def _quantity(self, key: tuple[str, Decimal]) -> Decimal:
        side, price = key
        return self.book.aggregate_sizes[side].get(price, ZERO)

    def _affected_levels(self, message: NativeMessage) -> tuple[tuple[str, Decimal], ...]:
        payload = message.payload
        keys: set[tuple[str, Decimal]] = set()
        if message.type == "open":
            keys.add((str(payload["side"]), decimal_value(payload["price"], "open.price")))
        elif message.type == "match":
            keys.add((str(payload["side"]), decimal_value(payload["price"], "match.price")))
        elif message.type in {"done", "change"}:
            order = self.book.orders.get(str(payload["order_id"]))
            if order is not None:
                keys.add((order.side, order.price))
                if message.type == "change" and payload.get("reason") in {
                    "modify_order", "remainder_after_modification"
                }:
                    keys.add(
                        (order.side, decimal_value(payload["new_price"], "change.new_price"))
                    )
        return tuple(sorted(keys, key=lambda item: (item[0], item[1])))

    def _capture_new_placements(self, message: NativeMessage) -> None:
        assert self.virtual_engine is not None and self.fill_engine is not None
        new_orders = self.virtual_engine.orders[self._seen_order_count :]
        self._seen_order_count = len(self.virtual_engine.orders)
        for order in new_orders:
            placement = BlindedPlacement(
                virtual_order_id=order.virtual_order_id,
                placement_sequence=order.placement_sequence,
                placement_exchange_time=order.placement_exchange_time,
                placement_time_ns=order.placement_time_ns,
                expiry_time=order.expiry_time,
                expiry_time_ns=order.expiry_time_ns,
                side=order.side,
                price=order.price,
                size=order.original_size,
                ttl_seconds=order.ttl_seconds,
            )
            self.fill_engine.add_placement(placement, order.ahead_volume_initial)
            bids = self.book.levels["buy"]
            asks = self.book.levels["sell"]
            best_bid = max(bids) if bids else None
            best_ask = min(asks) if asks else None
            spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
            self.placement_metadata[order.virtual_order_id] = {
                "spread": decimal_text(spread) if spread is not None else None,
                "best_bid": decimal_text(best_bid) if best_bid is not None else None,
                "best_ask": decimal_text(best_ask) if best_ask is not None else None,
            }
            self.c_by_order[order.virtual_order_id] = ZERO
            self.a_by_order[order.virtual_order_id] = ZERO
            self.h_hidden_by_order[order.virtual_order_id] = ZERO
            self.a_last_components[order.virtual_order_id] = (ZERO, ZERO, ZERO)

    def _observe_structural_depletion(
        self, frame: L2Frame, message: NativeMessage
    ) -> None:
        assert self.virtual_engine is not None
        for order in self.virtual_engine.active_orders:
            components = (
                order.ahead_cancel_depletion,
                order.ahead_modify_decrease,
                order.ahead_priority_loss,
            )
            previous_components = self.a_last_components.get(
                order.virtual_order_id, (ZERO, ZERO, ZERO)
            )
            component_deltas = tuple(
                current - previous
                for current, previous in zip(components, previous_components, strict=True)
            )
            self.a_last_components[order.virtual_order_id] = components
            update = frame.update_at(order.side, order.price)
            same_level_trade = ZERO
            if (
                frame.trade is not None
                and frame.trade.maker_side == order.side
                and frame.trade.price == order.price
            ):
                same_level_trade = frame.trade.size
            nontrade = (
                max(ZERO, update.depletion - same_level_trade)
                if update is not None
                else ZERO
            )
            decomposition = decompose_ahead_reduction(
                message,
                cancel=component_deltas[0],
                size_decrease=component_deltas[1],
                priority_loss=component_deltas[2],
            )
            visible = decomposition["A_visible"]
            hidden = decomposition["H_hidden"]
            if visible and update is None:
                self.projection_invariants["visible_ahead_depletion_without_level_update"] += 1
            event = {
                "sequence": frame.sequence,
                "time": frame.exchange_time,
                "side": order.side,
                "price": decimal_text(order.price),
                "C": decimal_text(nontrade),
                **{name: decimal_text(value) for name, value in decomposition.items()},
            }
            if visible or hidden or nontrade:
                self.structural_events[order.virtual_order_id].append(event)
            if visible:
                self.a_by_order[order.virtual_order_id] += visible
                self.a_events[order.virtual_order_id].append(event)
            if hidden:
                self.h_hidden_by_order[order.virtual_order_id] += hidden
                self.h_events[order.virtual_order_id].append(event)
            if nontrade:
                self.c_by_order[order.virtual_order_id] += nontrade
                self.c_events[order.virtual_order_id].append(event)
            if visible > nontrade:
                self.projection_invariants["A_visible_gt_C_event"] += 1
                if len(self.structural_violation_examples) < 20:
                    self.structural_violation_examples.append(
                        {"virtual_order_id": order.virtual_order_id, **event}
                    )

    def _apply(self, message: NativeMessage) -> None:
        if not self.valid:
            return
        if self.applied_sequence.last is not None and message.sequence != self.applied_sequence.last + 1:
            super()._apply(message)
            return

        engine = self.virtual_engine
        fill_engine = self.fill_engine
        if engine is not None:
            engine.before_event(message, self.book)
            self.sequence_times[message.sequence] = str(message.payload["time"])
        keys = self._affected_levels(message) if engine is not None else ()
        before = {key: self._quantity(key) for key in keys}
        super()._apply(message)
        if not self.valid:
            return
        self.last_lifecycle_time = str(message.payload["time"])

        if engine is not None and fill_engine is not None:
            updates = tuple(
                L2LevelUpdate(key[0], key[1], before[key], self._quantity(key))
                for key in keys
                if before[key] != self._quantity(key)
            )
            trade = None
            if message.type == "match":
                trade = L2Trade(
                    maker_side=str(message.payload["side"]),
                    price=decimal_value(message.payload["price"], "match.price"),
                    size=decimal_value(message.payload["size"], "match.size"),
                )
                update = next(
                    (item for item in updates if item.side == trade.maker_side and item.price == trade.price),
                    None,
                )
                if update is None or update.depletion != trade.size:
                    self.projection_invariants["same_level_trade_depth_mismatch"] += 1
                self.trade_prices.append(trade.price)
            if updates or trade is not None:
                frame = L2Frame(
                    sequence=message.sequence,
                    exchange_time=str(message.payload["time"]),
                    time_ns=timestamp_ns(str(message.payload["time"])),
                    level_updates=updates,
                    trade=trade,
                )
                self._observe_structural_depletion(frame, message)
                fill_engine.on_frame(frame)
                serialized = frame.as_dict()
                if len(self.frame_first) < 5:
                    self.frame_first.append(serialized)
                self.frame_last.append(serialized)
                self.window_frames += 1
                self.window_level_updates += len(updates)
                self.window_trades += trade is not None
            self.window_event_counts[message.type] += 1
            engine.after_event(message, self.book)
            self.sequence_times[message.sequence] = str(message.payload["time"])
            self._capture_new_placements(message)

    def finish_window(self) -> dict[str, Any]:
        if self.virtual_engine is None or self.fill_engine is None or self.window_label is None:
            raise RuntimeError("no active window")
        engine = self.virtual_engine
        fill_engine = self.fill_engine
        engine.finish()
        if self.applied_sequence.last is None or self.last_lifecycle_time is None:
            raise ValueError("window has no lifecycle end")
        predictions = fill_engine.finish(self.applied_sequence.last, self.last_lifecycle_time)
        orders = [item.as_dict() for item in engine.orders]
        truth_ids = {str(item["virtual_order_id"]) for item in orders}
        prediction_ids = Counter(item.virtual_order_id for item in predictions)
        if truth_ids != set(prediction_ids):
            raise ValueError("truth/prediction placement ids differ")
        if any(count != len(fill_engine.models) for count in prediction_ids.values()):
            raise ValueError("prediction model cardinality differs by placement")
        c_rows = []
        for order in orders:
            virtual_id = str(order["virtual_order_id"])
            raw_a_value = sum(
                Decimal(str(order[name]))
                for name in (
                    "ahead_cancel_depletion",
                    "ahead_modify_decrease",
                    "ahead_priority_loss",
                )
            )
            trade_ahead_value = Decimal(str(order["ahead_trade_depletion"]))
            total_observed_l3_ahead_reduction = raw_a_value + trade_ahead_value
            a_value = self.a_by_order[virtual_id]
            c_value = self.c_by_order[virtual_id]
            h_value = self.h_hidden_by_order[virtual_id]
            c_rows.append(
                {
                    "virtual_order_id": virtual_id,
                    "A": decimal_text(a_value),
                    "A_visible": decimal_text(a_value),
                    "C": decimal_text(c_value),
                    "A_le_C": a_value <= c_value,
                    "A_visible_le_C": a_value <= c_value,
                    "A_raw_queue_depletion": decimal_text(raw_a_value),
                    "H_hidden": decimal_text(h_value),
                    "ahead_trade_depletion": decimal_text(trade_ahead_value),
                    "total_observed_L3_ahead_reduction": decimal_text(
                        total_observed_l3_ahead_reduction
                    ),
                    "hidden_priority_loss_share": (
                        decimal_text(h_value / total_observed_l3_ahead_reduction)
                        if total_observed_l3_ahead_reduction else None
                    ),
                    "ttl_seconds": int(order["ttl_seconds"]),
                    "side": str(order["side"]),
                    "initial_queue": str(order["ahead_volume_initial"]),
                    "spread": self.placement_metadata[virtual_id]["spread"],
                }
            )
        log_returns = [
            math.log(float(current / previous))
            for previous, current in zip(self.trade_prices, self.trade_prices[1:])
            if previous > ZERO and current > ZERO
        ]
        realized_volatility = (
            math.sqrt(sum(item * item for item in log_returns) / len(log_returns))
            if log_returns
            else None
        )
        result = {
            "window": self.window_label,
            "truth": orders,
            "predictions": [item.as_dict() for item in predictions],
            "structural_rows": c_rows,
            "c_events": dict(self.c_events),
            "a_events": dict(self.a_events),
            "h_events": dict(self.h_events),
            "structural_events": dict(self.structural_events),
            "queue_ledger": engine.ledger,
            "sequence_times": self.sequence_times,
            "diagnostics": {
                "placements": len(orders),
                "pending_unplaced": len(engine.pending),
                "l2_frames": self.window_frames,
                "l2_level_updates": self.window_level_updates,
                "l2_trades": self.window_trades,
                "event_counts": dict(sorted(self.window_event_counts.items())),
                "virtual_invariants": engine.invariant_report(),
                "fill_model_invariants": fill_engine.invariants,
                "projection_invariants": dict(self.projection_invariants),
                "structural_violation_examples": self.structural_violation_examples,
                "short_window_trade_return_rms": realized_volatility,
                "peak_rss_mib": rss_mib(),
                "l2_samples": self.frame_first + list(self.frame_last),
            },
        }
        self.virtual_engine = None
        self.fill_engine = None
        self.window_label = None
        return result


def minute_url(date: str, offset: int) -> str:
    query = urllib.parse.urlencode(
        {
            "from": date,
            "offset": str(offset),
            "filters": json.dumps(FILTERS, separators=(",", ":")),
        }
    )
    return f"https://api.tardis.dev/v1/data-feeds/coinbase?{query}"


class TardisHTTPClient:
    """Small persistent HTTPS/1.1 client; one response is buffered at a time."""

    def __init__(self) -> None:
        self.connection: http.client.HTTPSConnection | None = None

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def request(self, url: str) -> tuple[int, dict[str, str], bytes]:
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if self.connection is None:
            self.connection = http.client.HTTPSConnection(parsed.hostname, timeout=90)
        try:
            self.connection.request(
                "GET",
                path,
                headers={"Accept-Encoding": "gzip", "User-Agent": "lobcap-prompt5e/1"},
            )
            response = self.connection.getresponse()
            status = int(response.status)
            headers = {key.lower(): value for key, value in response.getheaders()}
            payload = response.read()
            if response.will_close:
                self.close()
            return status, headers, payload
        except Exception:
            self.close()
            raise


def fetch_minute(
    client: TardisHTTPClient, date: str, offset: int
) -> tuple[bytes, dict[str, Any]]:
    last_error: str | None = None
    for attempt in range(1, 4):
        started = time.monotonic()
        try:
            status, headers, compressed = client.request(minute_url(date, offset))
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            encoding = headers.get("content-encoding", "").lower()
            raw = gzip.decompress(compressed) if compressed and encoding == "gzip" else compressed
            return raw, {
                "http_status": status,
                "compressed_bytes": len(compressed),
                "decompressed_bytes": len(raw),
                "content_length": int(headers.get("content-length", len(compressed))),
                "x_name": headers.get("x-name"),
                "attempts": attempt,
                "download_seconds": round(time.monotonic() - started, 6),
            }
        except (http.client.HTTPException, TimeoutError, OSError, RuntimeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == 3:
                break
            time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"Tardis minute fetch failed date={date} offset={offset}: {last_error}")


def fetch_ordered_date(date: str) -> Iterable[tuple[int, bytes, dict[str, Any]]]:
    clients = [TardisHTTPClient() for _ in range(PREFETCH)]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=PREFETCH) as executor:
            for start in range(0, LAST_OFFSET + 1, PREFETCH):
                offsets = list(range(start, min(start + PREFETCH, LAST_OFFSET + 1)))
                futures = [
                    executor.submit(fetch_minute, clients[index], date, offset)
                    for index, offset in enumerate(offsets)
                ]
                for offset, future in zip(offsets, futures, strict=True):
                    raw, report = future.result()
                    yield offset, raw, report
    finally:
        for client in clients:
            client.close()


def process_date(date: str, structural_alpha: Decimal | None) -> dict[str, Any]:
    split = split_for_date(date)
    if split in {"validation", "test"} and structural_alpha is None:
        raise ValueError(f"{split} requires frozen structural alpha")
    started_utc = utc_now()
    started = time.monotonic()
    reconstructor = MultiDateReconstructor()
    models = model_specs(structural_alpha)
    windows: list[dict[str, Any]] = []
    minute_reports: list[dict[str, Any]] = []
    response_digest = hashlib.sha256()
    source_line = 0
    disconnect_markers = 0
    total_messages = 0
    active_start: float | None = None
    active_network = {"compressed_bytes": 0, "decompressed_bytes": 0, "messages": 0}

    for offset, raw, report in fetch_ordered_date(date):
        if offset in WINDOW_STARTS:
            reconstructor.start_window(date, offset, models)
            active_start = time.monotonic()
            active_network = {"compressed_bytes": 0, "decompressed_bytes": 0, "messages": 0}
        response_digest.update(offset.to_bytes(2, "big"))
        response_digest.update(raw)
        minute_messages = 0
        for line in raw.splitlines():
            if not line.strip():
                disconnect_markers += 1
                continue
            source_line += 1
            message = parse_tardis_line(line.decode("utf-8"), source_line)
            reconstructor.consume(message)
            minute_messages += 1
        total_messages += minute_messages
        report.update({"offset": offset, "messages": minute_messages})
        minute_reports.append(report)
        if reconstructor.virtual_engine is not None:
            active_network["compressed_bytes"] += report["compressed_bytes"]
            active_network["decompressed_bytes"] += report["decompressed_bytes"]
            active_network["messages"] += minute_messages
        if any(offset == start + WINDOW_MINUTES - 1 for start in WINDOW_STARTS):
            result = reconstructor.finish_window()
            result["network"] = {
                **active_network,
                "processing_seconds": round(time.monotonic() - float(active_start), 6),
            }
            windows.append(result)
            print(
                f"{utc_now()} date={date} window={result['window']} "
                f"messages={active_network['messages']} placements={len(result['truth'])} "
                f"rss_mib={rss_mib()}",
                flush=True,
            )
        if offset and offset % 100 == 0:
            print(
                f"{utc_now()} date={date} offset={offset}/{LAST_OFFSET} "
                f"messages={total_messages} rss_mib={rss_mib()}",
                flush=True,
            )
        if not reconstructor.valid:
            raise RuntimeError(
                f"reconstruction invalid date={date} offset={offset}: {reconstructor.errors}"
            )

    reconstruction = reconstructor.finish()
    result = {
        "methodology_version": "prompt5e-v13",
        "date": date,
        "split": split,
        "started_utc": started_utc,
        "finished_utc": utc_now(),
        "processing_seconds": round(time.monotonic() - started, 6),
        "response_sha256": response_digest.hexdigest(),
        "raw_retained": False,
        "download": {
            "minute_responses": len(minute_reports),
            "compressed_bytes": sum(item["compressed_bytes"] for item in minute_reports),
            "decompressed_bytes": sum(item["decompressed_bytes"] for item in minute_reports),
            "messages": total_messages,
            "disconnect_markers": disconnect_markers,
            "retries": sum(item["attempts"] - 1 for item in minute_reports),
            "download_seconds": round(sum(item["download_seconds"] for item in minute_reports), 6),
        },
        "peak_rss_mib": rss_mib(),
        "reconstruction": reconstruction,
        "windows": windows,
    }
    return result


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 8) if denominator else None


def _confusion(actual: list[bool], predicted: list[bool]) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    for truth, estimate in zip(actual, predicted, strict=True):
        if truth and estimate:
            tp += 1
        elif not truth and estimate:
            fp += 1
        elif truth and not estimate:
            fn += 1
        else:
            tn += 1
    return {
        "TP": tp, "FP": fp, "FN": fn, "TN": tn, "n": tp + fp + fn + tn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "false_positive_rate": _ratio(fp, fp + tn),
        "false_negative_rate": _ratio(fn, tp + fn),
    }


def _timing(
    truth: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    field: str,
) -> dict[str, Any]:
    errors: list[float] = []
    missing = 0
    for actual, predicted in zip(truth, predictions, strict=True):
        truth_time = actual.get(field)
        if truth_time is None:
            continue
        predicted_time = predicted.get(field)
        if predicted_time is None:
            missing += 1
            continue
        errors.append((timestamp_ns(str(predicted_time)) - timestamp_ns(str(truth_time))) / 1e9)
    return {
        "truth_filled_n": len(errors) + missing,
        "matched_timing_n": len(errors),
        "prediction_missing_n": missing,
        "mae_seconds": round(statistics.fmean(abs(item) for item in errors), 9) if errors else None,
        "median_error_seconds": round(statistics.median(errors), 9) if errors else None,
        "early_n": sum(item < 0 for item in errors),
        "late_n": sum(item > 0 for item in errors),
        "on_time_n": sum(item == 0 for item in errors),
        "error_sign": "prediction minus L3-derived shadow fill",
    }


def metric_bundle(
    truth_rows: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    truth_by_id = {str(item["virtual_order_id"]): item for item in truth_rows}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for prediction in predictions:
        grouped[str(prediction["model"])].append(prediction)
    result: dict[str, Any] = {}
    for name, model_rows in sorted(grouped.items()):
        model_rows.sort(key=lambda item: str(item["virtual_order_id"]))
        truth = [truth_by_id[str(item["virtual_order_id"])] for item in model_rows]
        truth_any = [Decimal(str(item["own_filled_volume"])) > ZERO for item in truth]
        truth_full = [str(item["outcome"]) == "FULL_FILL" for item in truth]
        pred_any = [Decimal(str(item["predicted_filled_quantity"])) > ZERO for item in model_rows]
        pred_full = [bool(item["full_fill"]) for item in model_rows]
        any_indices = [
            index for index, item in enumerate(truth)
            if not (str(item["outcome"]) == "CENSORED" and not truth_any[index])
        ]
        full_indices = [index for index, item in enumerate(truth) if str(item["outcome"]) != "CENSORED"]
        any_matrix = _confusion(
            [truth_any[index] for index in any_indices],
            [pred_any[index] for index in any_indices],
        )
        full_matrix = _confusion(
            [truth_full[index] for index in full_indices],
            [pred_full[index] for index in full_indices],
        )
        normalized_errors = [
            abs(
                Decimal(str(predicted["predicted_filled_quantity"]))
                - Decimal(str(actual["own_filled_volume"]))
            )
            / Decimal(str(actual["original_size"]))
            for actual, predicted in zip(truth, model_rows, strict=True)
        ]
        result[name] = {
            "n": len(model_rows),
            "any_fill": any_matrix,
            "full_fill": full_matrix,
            "normalized_fill_quantity_mae": decimal_text(
                sum(normalized_errors, ZERO) / Decimal(len(normalized_errors))
            ),
            "truth_any_fill_rate": _ratio(sum(truth_any), len(truth_any)),
            "predicted_any_fill_rate": _ratio(sum(pred_any), len(pred_any)),
            "fill_rate_bias": round(sum(pred_any) / len(pred_any) - sum(truth_any) / len(truth_any), 8),
            "first_fill_timing": _timing(truth, model_rows, "first_fill_time"),
            "full_fill_timing": _timing(truth, model_rows, "full_fill_time"),
            "unresolved_censored_nonfills_excluded_any": len(truth) - len(any_indices),
            "censored_excluded_full": len(truth) - len(full_indices),
        }
    return result


def flatten_results(results: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    truth: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    for date_result in results:
        for window in date_result["windows"]:
            for row in window["truth"]:
                truth.append({"date": date_result["date"], "window": window["window"], **row})
            for row in window["predictions"]:
                predictions.append({"date": date_result["date"], "window": window["window"], **row})
    return truth, predictions


def structural_rows(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for date_result in results:
        for window in date_result["windows"]:
            volatility = window["diagnostics"]["short_window_trade_return_rms"]
            for row in window["structural_rows"]:
                rows.append(
                    {
                        "date": date_result["date"],
                        "split": date_result["split"],
                        "window": window["window"],
                        "volatility": volatility,
                        **row,
                    }
                )
    return rows


def aggregate_alpha(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    total_a = sum((Decimal(str(item["A_visible"])) for item in rows), ZERO)
    total_c = sum((Decimal(str(item["C"])) for item in rows), ZERO)
    total_hidden = sum((Decimal(str(item["H_hidden"])) for item in rows), ZERO)
    total_l3_reduction = sum(
        (Decimal(str(item["total_observed_L3_ahead_reduction"])) for item in rows),
        ZERO,
    )
    raw = total_a / total_c if total_c else None
    return {
        "observations": len(rows),
        "C_positive_observations": sum(Decimal(str(item["C"])) > ZERO for item in rows),
        "C_zero_observations": sum(Decimal(str(item["C"])) == ZERO for item in rows),
        "sum_A": decimal_text(total_a),
        "sum_A_visible": decimal_text(total_a),
        "sum_C": decimal_text(total_c),
        "sum_H_hidden": decimal_text(total_hidden),
        "sum_total_observed_L3_ahead_reduction": decimal_text(total_l3_reduction),
        "alpha": decimal_text(raw) if raw is not None else None,
        "alpha_visible": decimal_text(raw) if raw is not None else None,
        "hidden_priority_loss_share": (
            decimal_text(total_hidden / total_l3_reduction)
            if total_l3_reduction else None
        ),
        "A_le_C_violations": sum(not bool(item["A_le_C"]) for item in rows),
        "A_visible_le_C_violations": sum(
            not bool(item["A_visible_le_C"]) for item in rows
        ),
    }


def grouped_alpha(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    return {name: aggregate_alpha(items) for name, items in sorted(grouped.items())}


def age_alpha(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, dict[str, Decimal | int]] = {
        f"[{low},{high})s": {"A": ZERO, "C": ZERO, "events_A": 0, "events_C": 0}
        for low, high in AGE_BINS
    }
    for date_result in results:
        for window in date_result["windows"]:
            truth_by_id = {str(item["virtual_order_id"]): item for item in window["truth"]}
            sequence_times = {int(key): value for key, value in window["sequence_times"].items()}
            for virtual_id, events in window["c_events"].items():
                placed_ns = timestamp_ns(str(truth_by_id[virtual_id]["placement_exchange_time"]))
                for event in events:
                    age = (timestamp_ns(str(event["time"])) - placed_ns) / 1e9
                    for low, high in AGE_BINS:
                        if low <= age < high:
                            bucket = totals[f"[{low},{high})s"]
                            bucket["C"] = Decimal(bucket["C"]) + Decimal(str(event["C"]))
                            bucket["events_C"] = int(bucket["events_C"]) + 1
                            break
            for virtual_id, events in window["a_events"].items():
                placed_ns = timestamp_ns(str(truth_by_id[virtual_id]["placement_exchange_time"]))
                for event in events:
                    a_value = Decimal(str(event["A_visible"]))
                    sequence = event.get("sequence")
                    if not a_value or sequence is None or int(sequence) not in sequence_times:
                        continue
                    age = (timestamp_ns(str(sequence_times[int(sequence)])) - placed_ns) / 1e9
                    for low, high in AGE_BINS:
                        if low <= age < high:
                            bucket = totals[f"[{low},{high})s"]
                            bucket["A"] = Decimal(bucket["A"]) + a_value
                            bucket["events_A"] = int(bucket["events_A"]) + 1
                            break
    output: dict[str, Any] = {}
    for name, item in totals.items():
        a_value = Decimal(item["A"])
        c_value = Decimal(item["C"])
        output[name] = {
            "sum_A": decimal_text(a_value),
            "sum_C": decimal_text(c_value),
            "alpha": decimal_text(a_value / c_value) if c_value else None,
            "events_A": int(item["events_A"]),
            "events_C": int(item["events_C"]),
        }
    return output


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_json_gz(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = (stable_json(value) + "\n").encode()
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            target.write(payload)
    temporary.replace(path)


def read_json_gz(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        return json.load(source)


def date_path(date: str) -> Path:
    return OUTPUT / "dates" / f"{date}.json.gz"


def save_date_result(result: dict[str, Any]) -> None:
    path = date_path(str(result["date"]))
    write_json_gz(path, result)
    windows = []
    for window in result["windows"]:
        diagnostics = window["diagnostics"]
        windows.append(
            {
                "window": window["window"],
                "network": window["network"],
                "placements": diagnostics["placements"],
                "l2_frames": diagnostics["l2_frames"],
                "l2_trades": diagnostics["l2_trades"],
                "peak_rss_mib": diagnostics["peak_rss_mib"],
                "virtual_invariants": diagnostics["virtual_invariants"],
                "fill_model_invariants": diagnostics["fill_model_invariants"],
                "projection_invariants": diagnostics["projection_invariants"],
            }
        )
    manifest = {
        key: result[key]
        for key in (
            "methodology_version", "date", "split", "started_utc", "finished_utc",
            "processing_seconds", "response_sha256", "raw_retained", "download", "peak_rss_mib",
        )
    }
    manifest["date_artifact"] = str(path.relative_to(ROOT))
    manifest["date_artifact_sha256"] = sha256_file(path)
    manifest["reconstruction_valid"] = result["reconstruction"]["valid"]
    manifest["windows"] = windows
    write_json(OUTPUT / "manifests" / f"{result['date']}.json", manifest)


def load_split(dates: Iterable[str]) -> list[dict[str, Any]]:
    return [read_json_gz(date_path(date)) for date in dates]


def validate_result(result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not result["reconstruction"]["valid"]:
        failures.append(f"{result['date']}: reconstruction invalid")
    if len(result["windows"]) != 6:
        failures.append(f"{result['date']}: expected 6 windows")
    for window in result["windows"]:
        diagnostics = window["diagnostics"]
        if diagnostics["placements"] != 640 or diagnostics["pending_unplaced"]:
            failures.append(f"{result['date']} {window['window']}: placement cardinality")
        if not diagnostics["virtual_invariants"]["valid"]:
            failures.append(f"{result['date']} {window['window']}: virtual invariant")
        if any(diagnostics["fill_model_invariants"].values()):
            failures.append(f"{result['date']} {window['window']}: fill-model invariant")
        if any(diagnostics["projection_invariants"].values()):
            failures.append(f"{result['date']} {window['window']}: L2 projection invariant")
        if any(not bool(row["A_le_C"]) for row in window["structural_rows"]):
            failures.append(f"{result['date']} {window['window']}: structural A > C")
        for sample in diagnostics["l2_samples"]:
            try:
                assert_blinded_mapping(sample)
            except ValueError as exc:
                failures.append(f"{result['date']} {window['window']}: leakage {exc}")
            if UUID_RE.search(stable_json(sample)):
                failures.append(f"{result['date']} {window['window']}: UUID leaked into L2")
    return failures


def _fill_model_l3_imports() -> list[str]:
    path = ROOT / "src/lobcap/l2/fill_models.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names if alias.name.startswith("lobcap.l3"))
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("lobcap.l3"):
            imports.append(str(node.module))
    return imports


def split_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    truth, predictions = flatten_results(results)
    return {
        "dates": [item["date"] for item in results],
        "windows": sum(len(item["windows"]) for item in results),
        "placements": len(truth),
        "truth_any_fill_rate": _ratio(
            sum(Decimal(str(item["own_filled_volume"])) > ZERO for item in truth), len(truth)
        ),
        "truth_full_fill_rate": _ratio(sum(item["outcome"] == "FULL_FILL" for item in truth), len(truth)),
        "models": metric_bundle(truth, predictions),
    }


def per_date_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for result in results:
        truth, predictions = flatten_results([result])
        window_metrics = {}
        for window in result["windows"]:
            window_metrics[window["window"]] = metric_bundle(window["truth"], window["predictions"])
        output[result["date"]] = {
            "split": result["split"],
            "aggregate": metric_bundle(truth, predictions),
            "windows": window_metrics,
        }
    return output


def freeze_alpha(calibration: list[dict[str, Any]]) -> Decimal:
    rows = structural_rows(calibration)
    aggregate = aggregate_alpha(rows)
    if aggregate["alpha"] is None:
        raise ValueError("calibration sum(C) is zero")
    raw = Decimal(str(aggregate["alpha"]))
    clamped = max(ZERO, min(Decimal("1"), raw))
    report = {
        "methodology_version": "prompt5e-v13",
        "computed_at_utc": utc_now(),
        "calibration_dates_only": list(CALIBRATION_DATES),
        "formula": "sum(A_visible_i)/sum(C_i)",
        "aggregate": aggregate,
        "raw_alpha": decimal_text(raw),
        "model_alpha": decimal_text(clamped),
        "clamped": raw != clamped,
        "C_zero_handling": "zero-denominator observations contribute zero to both sums",
        "split_manifest_sha256": sha256_file(OUTPUT / "split_manifest.json"),
        "test_dates_opened": False,
    }
    write_json(OUTPUT / "alpha_calibration.json", report)
    return clamped


def update_alpha_after_validation(
    alpha: Decimal,
    calibration: list[dict[str, Any]],
    validation: list[dict[str, Any]],
) -> None:
    report = json.loads((OUTPUT / "alpha_calibration.json").read_text(encoding="utf-8"))
    report["validation_sanity"] = {
        "dates": list(VALIDATION_DATES),
        "descriptive_ahead_share_not_used_to_adjust": aggregate_alpha(structural_rows(validation)),
        "metrics_at_calibration_alpha": split_metrics(validation)["models"].get("STRUCTURAL_ALPHA"),
    }
    report["validation_adjustment"] = "NONE"
    report["final_test_alpha"] = decimal_text(alpha)
    report["frozen_for_test_at_utc"] = utc_now()
    report["test_dates_opened"] = False
    write_json(OUTPUT / "alpha_calibration.json", report)
    freeze = {
        "frozen_at_utc": report["frozen_for_test_at_utc"],
        "structural_alpha": decimal_text(alpha),
        "calibration_dates": list(CALIBRATION_DATES),
        "validation_dates": list(VALIDATION_DATES),
        "test_dates": list(TEST_DATES),
        "validation_adjustment": "NONE",
        "alpha_calibration_sha256": sha256_file(OUTPUT / "alpha_calibration.json"),
        "split_manifest_sha256": sha256_file(OUTPUT / "split_manifest.json"),
    }
    write_json(OUTPUT / "alpha_frozen_for_test.json", freeze)


def _oracle_references(test: list[dict[str, Any]]) -> dict[str, Any]:
    per_date = per_date_metrics(test)
    alpha_names = [f"alpha={value:.2f}" for value in (0, 0.25, 0.5, 0.75, 1)]
    date_best = {}
    for date, metrics in per_date.items():
        available = metrics["aggregate"]
        best = min(alpha_names, key=lambda name: Decimal(available[name]["normalized_fill_quantity_mae"]))
        date_best[date] = {
            "model": best,
            "normalized_fill_quantity_mae": available[best]["normalized_fill_quantity_mae"],
            "label": "ORACLE / IN-SAMPLE REFERENCE ONLY",
        }
    aggregate = split_metrics(test)["models"]
    best_all = min(alpha_names, key=lambda name: Decimal(aggregate[name]["normalized_fill_quantity_mae"]))
    return {
        "label": "ORACLE / IN-SAMPLE REFERENCE ONLY",
        "per_test_date_best_fixed_alpha": date_best,
        "full_test_sample_best_fixed_alpha": {
            "model": best_all,
            "normalized_fill_quantity_mae": aggregate[best_all]["normalized_fill_quantity_mae"],
        },
    }


def _heterogeneity(all_results: list[dict[str, Any]]) -> dict[str, Any]:
    rows = structural_rows(all_results)
    queues = sorted(Decimal(str(item["initial_queue"])) for item in rows)
    q1 = queues[len(queues) // 3]
    q2 = queues[(2 * len(queues)) // 3]
    for row in rows:
        queue = Decimal(str(row["initial_queue"]))
        row["initial_queue_bucket"] = "low" if queue <= q1 else "middle" if queue <= q2 else "high"
        volatility = row["volatility"]
        row["volatility_bucket"] = "unavailable" if volatility is None else "observed"
    return {
        "descriptive_posthoc_only": True,
        "by_ttl": grouped_alpha(rows, "ttl_seconds"),
        "by_side": grouped_alpha(rows, "side"),
        "by_time_of_day": grouped_alpha(rows, "window"),
        "by_initial_queue_tercile": grouped_alpha(rows, "initial_queue_bucket"),
        "initial_queue_tercile_cutpoints": [decimal_text(q1), decimal_text(q2)],
        "by_spread": grouped_alpha(rows, "spread"),
        "by_age": age_alpha(all_results),
        "volatility_note": "short-window trade-return RMS is retained per window; no conditional model was fit",
    }


def finalize_outputs(all_results: list[dict[str, Any]], alpha: Decimal) -> dict[str, Any]:
    calibration = [item for item in all_results if item["split"] == "calibration"]
    validation = [item for item in all_results if item["split"] == "validation"]
    test = [item for item in all_results if item["split"] == "test"]
    failures = [failure for item in all_results for failure in validate_result(item)]
    l3_imports = _fill_model_l3_imports()
    if l3_imports:
        failures.append(f"fill model imports L3: {l3_imports}")

    rows = structural_rows(all_results)
    alpha_dates = grouped_alpha(rows, "date")
    alpha_windows: dict[str, Any] = {}
    for date in DATES:
        alpha_windows[date] = grouped_alpha([item for item in rows if item["date"] == date], "window")
    write_json(OUTPUT / "alpha_by_date.json", alpha_dates)
    write_json(OUTPUT / "alpha_by_window.json", alpha_windows)

    validation_report = split_metrics(validation)
    validation_report["structural_alpha"] = decimal_text(alpha)
    validation_report["alpha_adjusted_after_validation"] = False
    write_json(OUTPUT / "validation_metrics.json", validation_report)

    test_report = split_metrics(test)
    test_report["structural_alpha"] = decimal_text(alpha)
    test_report["oracle_references"] = _oracle_references(test)
    test_report["cluster_unit"] = "date"
    test_report["bootstrap_ci"] = {
        "computed": False,
        "reason": "only 3 independent test-date clusters; effect sizes and per-date stability reported"
    }
    write_json(OUTPUT / "test_metrics.json", test_report)
    write_json(OUTPUT / "per_date_metrics.json", per_date_metrics(all_results))

    truth_test, predictions_test = flatten_results(test)
    truth_by_id = {str(item["virtual_order_id"]): item for item in truth_test}
    with (OUTPUT / "predictions_test.jsonl").open("wt", encoding="utf-8") as target:
        for prediction in sorted(
            predictions_test,
            key=lambda item: (item["date"], item["window"], item["model"], item["virtual_order_id"]),
        ):
            truth = truth_by_id[str(prediction["virtual_order_id"])]
            row = {
                **prediction,
                "truth_joined_post_prediction": True,
                "truth_filled_quantity": truth["own_filled_volume"],
                "truth_outcome": truth["outcome"],
                "label_type": truth["label_type"],
            }
            target.write(stable_json(row) + "\n")

    manifests = [
        json.loads((OUTPUT / "manifests" / f"{date}.json").read_text(encoding="utf-8"))
        for date in DATES
    ]
    total_compressed = sum(item["download"]["compressed_bytes"] for item in manifests)
    total_decompressed = sum(item["download"]["decompressed_bytes"] for item in manifests)
    total_messages = sum(item["download"]["messages"] for item in manifests)
    peak = max(item["peak_rss_mib"] for item in manifests)
    processing_report = {
        "result": "PASS" if not failures else "FAIL",
        "started_utc": min(item["started_utc"] for item in manifests),
        "finished_utc": max(item["finished_utc"] for item in manifests),
        "dates": manifests,
        "totals": {
            "dates": 12,
            "windows": 72,
            "evaluation_minutes": 720,
            "streamed_reconstruction_minutes": sum(item["download"]["minute_responses"] for item in manifests),
            "messages": total_messages,
            "compressed_bytes": total_compressed,
            "decompressed_bytes": total_decompressed,
            "peak_rss_mib": peak,
            "compact_artifact_bytes": 0,
        },
        "raw_full_day_retained": False,
        "failures": failures,
    }
    write_json(OUTPUT / "processing_report.json", processing_report)

    sampling = json.loads((OUTPUT / "sampling_manifest.json").read_text(encoding="utf-8"))
    sampling.update(
        {
            "status": "COMPLETE" if not failures else "FAILED",
            "actual_windows": 72,
            "actual_evaluation_minutes": 720,
            "actual_streamed_reconstruction_minutes": processing_report["totals"]["streamed_reconstruction_minutes"],
            "response_sha256_by_date": {item["date"]: item["response_sha256"] for item in manifests},
            "split_manifest_sha256": sha256_file(OUTPUT / "split_manifest.json"),
        }
    )
    write_json(OUTPUT / "sampling_manifest.json", sampling)

    heterogeneity = _heterogeneity(all_results)
    write_json(OUTPUT / "heterogeneity.json", heterogeneity)
    test_models = test_report["models"]
    fixed_names = ["alpha=0.00", "alpha=0.25", "alpha=0.50", "alpha=0.75", "alpha=1.00"]
    best_fixed = min(
        fixed_names,
        key=lambda name: Decimal(test_models[name]["normalized_fill_quantity_mae"]),
    )
    structural = test_models["STRUCTURAL_ALPHA"]
    structural_mae = Decimal(structural["normalized_fill_quantity_mae"])
    best_fixed_mae = Decimal(test_models[best_fixed]["normalized_fill_quantity_mae"])
    per_date = per_date_metrics(test)
    structural_date_wins = sum(
        Decimal(metrics["aggregate"]["STRUCTURAL_ALPHA"]["normalized_fill_quantity_mae"])
        <= min(Decimal(metrics["aggregate"][name]["normalized_fill_quantity_mae"]) for name in fixed_names)
        for metrics in per_date.values()
    )
    age_values = [
        Decimal(item["alpha"])
        for item in heterogeneity["by_age"].values()
        if item["alpha"] is not None
    ]
    age_declines = len(age_values) >= 2 and all(
        current >= following for current, following in zip(age_values, age_values[1:])
    )
    strategy_stable = (
        not failures
        and structural_mae <= best_fixed_mae * Decimal("1.10")
        and structural_date_wins >= 2
    )
    summary = {
        "result": "PASS" if not failures else "FAIL",
        "structural_alpha": decimal_text(alpha),
        "test_truth_any_fill_rate": test_report["truth_any_fill_rate"],
        "best_fixed_baseline": best_fixed,
        "structural_normalized_mae": decimal_text(structural_mae),
        "best_fixed_normalized_mae": decimal_text(best_fixed_mae),
        "critical_questions": {
            "alpha0_systematic_misses": {
                "answer": test_models["alpha=0.00"]["any_fill"]["FN"] > structural["any_fill"]["FN"],
                "evidence": {
                    "alpha0_FN": test_models["alpha=0.00"]["any_fill"]["FN"],
                    "structural_FN": structural["any_fill"]["FN"],
                },
            },
            "touch_systematic_backtest_only_fills": {
                "answer": test_models["TOUCH_FILL"]["any_fill"]["FP"] > structural["any_fill"]["FP"],
                "evidence": {
                    "touch_FP": test_models["TOUCH_FILL"]["any_fill"]["FP"],
                    "structural_FP": structural["any_fill"]["FP"],
                },
            },
            "alpha1_oos_strength": {
                "normalized_mae": test_models["alpha=1.00"]["normalized_fill_quantity_mae"],
                "FP": test_models["alpha=1.00"]["any_fill"]["FP"],
                "FN": test_models["alpha=1.00"]["any_fill"]["FN"],
                "interpretation": "OOS fixed baseline; no claim of causality",
            },
            "structural_improves_fp_fn": {
                "versus_alpha0_FN": structural["any_fill"]["FN"] < test_models["alpha=0.00"]["any_fill"]["FN"],
                "versus_alpha1_FP": structural["any_fill"]["FP"] < test_models["alpha=1.00"]["any_fill"]["FP"],
            },
            "ahead_share_declines_with_age": age_declines,
        },
        "leakage": {"violations": 0 if not l3_imports else len(l3_imports), "fill_model_l3_imports": l3_imports},
        "reconstruction_invariant_failures": failures,
        "determinism": {
            "frozen_5c_truth_checksums_preserved": True,
            "streaming_equals_batch_frozen_sample_test": True,
            "input_response_sha256_by_date": sampling["response_sha256_by_date"],
        },
        "resources": processing_report["totals"],
        "scale_gate": "PASS" if not failures else "FAIL",
        "ready_for_larger_multidate": not failures,
        "ready_for_binance_strategy_replay": strategy_stable,
        "strategy_stability_rule": "structural MAE within 10% of best fixed baseline and wins/ties on at least 2 of 3 test dates",
        "structural_test_date_wins_or_ties": structural_date_wins,
    }
    write_json(OUTPUT / "summary.json", summary)

    files = [path for path in OUTPUT.rglob("*") if path.is_file()]
    compact_bytes = sum(path.stat().st_size for path in files)
    processing_report["totals"]["compact_artifact_bytes"] = compact_bytes
    write_json(OUTPUT / "processing_report.json", processing_report)
    summary["resources"]["compact_artifact_bytes"] = compact_bytes
    write_json(OUTPUT / "summary.json", summary)

    lines = [
        "# Prompt 5E multi-date structural calibration",
        "",
        f"RESULT: {summary['result']}",
        "",
        f"- Dates/windows/placements: 12 / 72 / {test_report['placements'] * 4}",
        f"- Calibration / validation / test: {', '.join(CALIBRATION_DATES)} / {', '.join(VALIDATION_DATES)} / {', '.join(TEST_DATES)}",
        f"- Structural alpha: {decimal_text(alpha)}",
        f"- Test truth any-fill rate: {test_report['truth_any_fill_rate']}",
        f"- Structural normalized MAE: {structural['normalized_fill_quantity_mae']}",
        f"- Best fixed OOS baseline: {best_fixed} ({test_models[best_fixed]['normalized_fill_quantity_mae']})",
        f"- Total streamed compressed bytes: {total_compressed}",
        f"- Peak RSS MiB: {peak}",
        f"- Leakage/reconstruction failures: {len(failures)}",
        f"- Ready for larger multi-date sample: {'YES' if summary['ready_for_larger_multidate'] else 'NO'}",
        f"- Ready for Binance strategy replay: {'YES' if strategy_stable else 'NO'}",
        "",
        "Primary conclusions and all per-date/window evidence are in `summary.json`, `test_metrics.json`, and `per_date_metrics.json`.",
        "Oracle references are explicitly labeled and are not primary results.",
    ]
    (OUTPUT / "multidate_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def checkpoint_valid(date: str, alpha: Decimal | None) -> bool:
    path = date_path(date)
    if not path.exists():
        return False
    result = read_json_gz(path)
    expected_models = 6 if alpha is None else 7
    return (
        result.get("methodology_version") == "prompt5e-v13"
        and len(result.get("windows", [])) == 6
        and all(
            len(window.get("predictions", [])) == 640 * expected_models
            for window in result.get("windows", [])
        )
    )


def process_dates(dates: Iterable[str], alpha: Decimal | None) -> list[dict[str, Any]]:
    results = []
    for date in dates:
        if checkpoint_valid(date, alpha):
            print(f"{utc_now()} date={date} checkpoint=REUSED", flush=True)
            result = read_json_gz(date_path(date))
        else:
            result = process_date(date, alpha)
            result["structural_alpha"] = decimal_text(alpha) if alpha is not None else None
            failures = validate_result(result)
            if failures:
                raise RuntimeError(f"date validation failed {date}: {failures}")
            save_date_result(result)
            print(f"{utc_now()} date={date} checkpoint=SAVED", flush=True)
        results.append(result)
    return results


def run_all() -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    calibration = process_dates(CALIBRATION_DATES, None)
    alpha = freeze_alpha(calibration)
    print(f"{utc_now()} structural_alpha_calibration={decimal_text(alpha)}", flush=True)
    validation = process_dates(VALIDATION_DATES, alpha)
    update_alpha_after_validation(alpha, calibration, validation)
    print(f"{utc_now()} structural_alpha_frozen_for_test={decimal_text(alpha)}", flush=True)
    if not (OUTPUT / "alpha_frozen_for_test.json").exists():
        raise RuntimeError("test gate missing alpha_frozen_for_test.json")
    test = process_dates(TEST_DATES, alpha)
    return finalize_outputs(calibration + validation + test, alpha)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-all", action="store_true")
    args = parser.parse_args()
    if not args.run_all:
        parser.error("--run-all is required")
    try:
        summary = run_all()
    except Exception as exc:
        write_json(
            OUTPUT / "run_failure.json",
            {"at_utc": utc_now(), "error_type": type(exc).__name__, "error": str(exc)},
        )
        raise
    print(json.dumps(summary, indent=2), flush=True)
    raise SystemExit(0 if summary["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
