"""Small, independently specified execution probes; never used by strategies.

Expected quantities are explicit fixture constants, not calls to queue arithmetic.
The optional external check runs HftBacktest itself in an isolated environment.
It deliberately does not claim calibration to real exchange executions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal as D
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from types import SimpleNamespace

from lobcap.backtest.artifacts import MemorySink
from lobcap.backtest.execution import Execution
from lobcap.backtest.types import InstrumentSpec, L2Trade, MarketView, Quote, ReplayEvent
from lobcap.l2.fill_models import ModelSpec

HFT_VERSION = "2.4.4"


@dataclass(frozen=True)
class ContractCase:
    name: str
    queue: str
    quantity: str
    trades: tuple[tuple[str, str], ...]  # offset in ticks from order, normal qty
    expected: str
    participation: str = "1"
    hft_expected: str | None = None
    difference: str = ""


CASES = (
    ContractCase("queue_not_reached", ".010", ".003", (("0", ".002"),), "0", hft_expected="0"),
    ContractCase("queue_exactly_consumed", ".002", ".003", (("0", ".002"),), "0", hft_expected="0"),
    ContractCase("same_price_partial", ".002", ".003", (("0", ".003"),), ".001", hft_expected=".001"),
    ContractCase("same_price_full", ".002", ".003", (("0", ".006"),), ".003", hft_expected=".003"),
    ContractCase("through_clears_queue", "1", ".003", (("-1", ".001"),), ".001", hft_expected=".003",
                 difference="HftBacktest fills the whole resting order on strict price-through; lobcap bounds by observed normal trade volume."),
    ContractCase("through_full", "1", ".003", (("-1", ".004"),), ".003", hft_expected=".003"),
    ContractCase("away_price", ".002", ".003", (("1", ".009"),), "0", hft_expected="0"),
    ContractCase("sublot_queue_residual", ".0005", ".003", (("0", ".001"),), "0"),
    ContractCase("cap_merged", "0", ".003", (("0", ".010"),), ".001", participation=".1"),
    ContractCase("cap_split", "0", ".003", (("0", ".005"), ("0", ".005")), ".001", participation=".1"),
    ContractCase("cap_cannot_borrow_future", "0", ".003", (("0", ".005"),), "0", participation=".1"),
    ContractCase("cap_multiple_lots", "0", ".003", (("0", ".015"), ("0", ".015")), ".003", participation=".1"),
)


def aggressor_side(maker_side: str) -> str:
    """Trade flags use initiator side; depth flags must not use this conversion."""
    if maker_side not in ("buy", "sell"):
        raise ValueError("maker side must be buy or sell")
    return "sell" if maker_side == "buy" else "buy"


def run_case(case: ContractCase, side: str = "buy") -> dict:
    if side not in ("buy", "sell"):
        raise ValueError("invalid side")
    config = SimpleNamespace(instrument=InstrumentSpec(), entry_ms=0, cancel_ms=0,
                             max_age_ms=5000, stale_book_ms=500,
                             max_trade_participation=D(case.participation),
                             model=ModelSpec("alpha=0", D(0)))
    sink = MemorySink()
    execution = Execution(config, sink)
    price = D(100) if side == "buy" else D(101)
    market = MarketView(0, D(99), D(102), D(1), D(1), D("100.5"),
                        D("100.5"), D(0), None, 0, D(90), D(110))
    execution.submit(Quote(side, price, D(case.quantity)), 0, {})
    execution.advance(0, market, SimpleNamespace(quantity=lambda *_: D(case.queue)))
    fills = []
    per_event = []
    for index, (ticks, quantity) in enumerate(case.trades, 1):
        offset = D(ticks) * config.instrument.tick * (1 if side == "buy" else -1)
        event = ReplayEvent(index, index * 1_000_000, index * 1_000_000, "trade",
                            (f"synthetic:{case.name}:{index}",),
                            trade=L2Trade(side, price + offset, D(quantity)))
        delta = execution.on_event(event)
        fills.extend(delta)
        per_event.append(str(sum((f.quantity for f in delta), D(0))))
    actual = sum((f.quantity for f in fills), D(0))
    return {"case": case.name, "side": side, "expected": case.expected,
            "actual": str(actual), "passed": actual == D(case.expected),
            "per_event_fills": per_event,
            "all_fills_on_lot": all(f.quantity % config.instrument.lot == 0 for f in fills),
            "source_linked": all(f.source_refs for f in fills)}


def _hft_case(case: ContractCase, side: str) -> dict:
    import numpy as np
    from hftbacktest import (BacktestAsset, HashMapMarketDepthBacktest, GTX, LIMIT,
                             DEPTH_EVENT, TRADE_EVENT, BUY_EVENT, SELL_EVENT,
                             EXCH_EVENT, LOCAL_EVENT)
    from hftbacktest.types import event_dtype

    price = D(100) if side == "buy" else D(101)
    flags = EXCH_EVENT | LOCAL_EVENT
    # Real depth events bootstrap both processors before the order is sent.
    rows = [(flags | DEPTH_EVENT | BUY_EVENT, 1_000_000_000, 1_000_000_000, 99., 1., 0, 0, 0.),
            (flags | DEPTH_EVENT | SELL_EVENT, 1_000_000_000, 1_000_000_000, 102., 1., 0, 0, 0.),
            (flags | DEPTH_EVENT | (BUY_EVENT if side == "buy" else SELL_EVENT),
             1_000_000_000, 1_000_000_000, float(price), float(case.queue), 0, 0, 0.)]
    for index, (ticks, quantity) in enumerate(case.trades, 1):
        timestamp = 2_000_000_000 + index * 1_000_000
        trade_price = price + D(ticks) * D(".1") * (1 if side == "buy" else -1)
        trade_flag = BUY_EVENT if aggressor_side(side) == "buy" else SELL_EVENT
        rows.append((flags | TRADE_EVENT | trade_flag, timestamp, timestamp,
                     float(trade_price), float(quantity), 0, 0, 0.))
    rows.append((flags | DEPTH_EVENT | BUY_EVENT, 5_000_000_000, 5_000_000_000, 99., 1., 0, 0, 0.))
    data = np.array(rows, dtype=event_dtype)
    asset = (BacktestAsset().data([data]).linear_asset(1.)
             .constant_order_latency(1, 1).risk_adverse_queue_model()
             .partial_fill_exchange().trading_value_fee_model(0., 0.)
             .tick_size(.1).lot_size(.001))
    hbt = HashMapMarketDepthBacktest([asset])
    try:
        if hbt.elapse(1_000_000) != 0:
            raise RuntimeError("HftBacktest exhausted before fixture activation")
        submit = hbt.submit_buy_order if side == "buy" else hbt.submit_sell_order
        if submit(0, 1, float(price), float(case.quantity), GTX, LIMIT, False) != 0:
            raise RuntimeError("HftBacktest fixture order submission failed")
        if hbt.elapse(3_000_000_000) != 0:
            raise RuntimeError("HftBacktest exhausted before fixture observation")
        order = hbt.orders(0).get(1)
        if order is None:
            raise RuntimeError("HftBacktest lost fixture order before evidence capture")
        executed = D(str(order.qty)) - D(str(order.leaves_qty))
        position = D(str(abs(hbt.position(0))))
        return {"executed_quantity": str(executed), "reported_position_abs": str(position),
                "remaining_quantity": str(order.leaves_qty), "order_status": int(order.status),
                "last_exec_quantity": str(order.exec_qty),
                "position_reconciles": abs(executed - position) <= D("1e-12")}
    finally:
        hbt.close()


def validate_contracts(*, external: bool = False) -> dict:
    results = [run_case(case, side) for case in CASES for side in ("buy", "sell")]
    fixtures = [asdict(case) for case in CASES]
    report = {"schema": "execution-contract-validation-v1", "cases": results,
              "fixtures": fixtures,
              "fixture_sha256": hashlib.sha256(json.dumps(fixtures, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
              "passed": all(r["passed"] and r["all_fills_on_lot"] and r["source_linked"] for r in results),
              "external": {"status": "NOT_RUN", "required_version": HFT_VERSION},
              "limitations": ["Synthetic contract checks do not establish real exchange accuracy.",
                              "External scope is partial-fill exchange with risk-averse queue; not full sample parity.",
                              "No endogenous market impact is simulated by either replay engine."]}
    if not external:
        return report
    try:
        installed = version("hftbacktest")
    except PackageNotFoundError:
        report["external"]["reason"] = "optional dependency absent"
        report["passed"] = False
        return report
    if installed != HFT_VERSION:
        report["external"].update(status="VERSION_MISMATCH", installed_version=installed)
        report["passed"] = False
        return report
    comparisons = []
    for case in CASES:
        if case.hft_expected is None:
            continue
        for side in ("buy", "sell"):
            evidence = _hft_case(case, side)
            actual = D(evidence["executed_quantity"])
            own_actual = next(row["actual"] for row in results if row["case"] == case.name and row["side"] == side)
            comparison = {"case": case.name, "side": side, "hft_actual": str(actual),
                          "hft_expected": case.hft_expected, "lobcap_expected": case.expected,
                          "lobcap_actual": own_actual,
                          "engines_agree": abs(actual - D(own_actual)) <= D("1e-12"),
                          "hft_order_evidence": evidence,
                          "intentional_difference": case.difference,
                          "passed": abs(actual - D(case.hft_expected)) <= D("1e-12")}
            comparisons.append(comparison)
    accounting_ok = all(row["hft_order_evidence"]["position_reconciles"] for row in comparisons)
    report["external"] = {"status": "COMPLETED" if accounting_ok else "ACCOUNTING_DISCREPANCY",
                          "version": installed, "cases": comparisons,
                          "execution_contracts_passed": all(r["passed"] for r in comparisons),
                          "accounting_reconciled": accounting_ok,
                          "model": "PartialFillExchange / RiskAdverseQueueModel"}
    report["passed"] = report["passed"] and all(r["passed"] for r in comparisons) and accounting_ok
    return report
