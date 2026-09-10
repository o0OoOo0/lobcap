"""Immutable public contracts. No execution queue state crosses these views."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from lobcap.l2.schema import L2LevelUpdate, L2Trade

ZERO = Decimal(0)
NS = 1_000_000_000
NORMALIZATION_VERSION = "binance-net-depletion-v1"
ARRIVAL_NORMALIZATION_VERSION = "binance-net-depletion-arrival-v2"
EXECUTION_CONTRACT_VERSION = "binance-own-orders-v2"
ASSUMPTIONS = (
    "Exchange-time simulation; zero feed and fill-notification latency.",
    "Separate Binance depth/trade streams have no shared exchange sequence; ties are modeled.",
    "Net visible depletion cannot recover hidden cancellations or intra-batch replenishment.",
    "Initial queue uses the latest published depth and may be stale.",
    "L2 fills are approximations; Coinbase-tested arithmetic is transferred to Binance.",
    "Alpha is a queue-depletion assumption, not a fill probability or OOS-calibrated truth.",
    "TOUCH_FILL does not bound strategy PnL.",
    "No market impact, funding, hedge, margin liquidation or terminal liquidation.",
    "Cash is a synthetic linear-futures trading ledger, not an exchange wallet.",
    "Net PnL includes modeled maker fees; taker fees are inactive in maker-only v1.",
    "Bounded sample results are not evidence of live profitability or ETF LP economics.",
)


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(v) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    venue: str = "binance"
    market: str = "futures"
    symbol: str = "btcusdt"
    tick: Decimal = Decimal("0.10")
    lot: Decimal = Decimal("0.001")
    base: str = "BTC"
    quote: str = "USDT"


@dataclass(frozen=True, slots=True)
class SampleSpec:
    sample_id: str
    start_ns: int
    end_ns: int
    warmup_ns: int = 30 * NS
    schema_version: int = 1
    normalization_version: str = NORMALIZATION_VERSION


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    time_ns: int
    sequence: int
    coverage_bid: Decimal
    coverage_ask: Decimal


@dataclass(frozen=True, slots=True)
class Depletion:
    side: str
    price: Decimal
    visible: Decimal
    trades: Decimal
    nontrade: Decimal
    excess_trade: Decimal


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    replay_index: int
    time_ns: int
    receive_ns: int
    kind: str
    source_refs: tuple[str, ...] = ()
    source_sequences: tuple[int, ...] = ()
    updates: tuple[L2LevelUpdate, ...] = ()
    trade: L2Trade | None = None
    interval_start_ns: int | None = None
    depletion: tuple[Depletion, ...] = ()
    total_trade_quantity: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class MarketView:
    time_ns: int
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    mid: Decimal
    microprice: Decimal
    imbalance: Decimal
    volatility_bps: float | None
    book_age_ns: int
    coverage_bid: Decimal
    coverage_ask: Decimal
    trade_imbalance: Decimal | None = None
    trade_volume: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class PortfolioView:
    cash: Decimal
    inventory: Decimal
    equity: Decimal
    buy_exposure: Decimal = ZERO
    sell_exposure: Decimal = ZERO
    order_statuses: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Quote:
    side: str
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class DesiredQuotes:
    bid: Quote | None
    ask: Quote | None
    reason: str = "quote"


@dataclass(frozen=True, slots=True)
class OrderRecord:
    order_id: str
    side: str
    price: Decimal
    quantity: Decimal
    remaining: Decimal
    status: str
    submitted_ns: int
    activation_ns: int | None = None
    cancel_requested_ns: int | None = None
    terminal_ns: int | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class FillObservation:
    event: ReplayEvent


@dataclass(frozen=True, slots=True)
class FillEvent:
    order_id: str
    time_ns: int
    replay_index: int
    side: str
    price: Decimal
    quantity: Decimal
    source_refs: tuple[str, ...]
    role: str = "maker"
    reason: str = "trade"
    cancel_pending: bool = False
    decision: tuple[tuple[str, Any], ...] = ()
    decision_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    status: str
    summary: dict[str, Any]
    invariants: dict[str, Any]
    digest: str
    output_dir: Path | None = None
