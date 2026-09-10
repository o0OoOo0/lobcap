"""Bounded artifact explanations and reference-only terminal inventory valuation."""
from __future__ import annotations

import gzip
import json
from decimal import Decimal as D, ROUND_FLOOR, localcontext
from functools import wraps
from itertools import zip_longest
from pathlib import Path

from .catalog import comparison_guard, read_object, run_record

ZERO = D(0)


def _engine_precision(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with localcontext() as context:
            context.prec = 50
            return function(*args, **kwargs)
    return wrapped


def iter_rows(run_dir, channel):
    path = Path(run_dir) / f"{channel}.jsonl.gz"
    if not path.exists():
        return
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for index, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("record is not an object")
                yield row
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid {channel} evidence at row {index}: {exc}") from exc


@_engine_precision
def explain_run(run_dir, limit=200):
    if not 1 <= limit <= 1000:
        raise ValueError("analysis limit must be 1..1000")
    record = run_record(run_dir)
    totals = {"spread_capture": ZERO, "fees": ZERO, "inventory_mtm": ZERO}
    selected = []
    representative = {}
    fill_count = ledger_count = 0
    first = last = previous = None
    inventory_area = ZERO
    near_limit_ns = 0
    config = record["config"]
    inventory_limit = D(str(config["max_inventory"])) if config.get("max_inventory") is not None else None
    for row in iter_rows(run_dir, "fills"):
        fill_count += 1
        for source, target in (("spread_contribution", "spread_capture"), ("fee", "fees")):
            if source not in row:
                raise ValueError(f"Fill evidence missing {source}")
            totals[target] += D(str(row[source]))
        selected.append({k: row.get(k) for k in ("order_id", "side", "quantity", "spread_contribution", "fee", "reason", "time_ns")})
        candidate = dict(selected[-1])
        spread = D(str(row["spread_contribution"]))
        for category, eligible in (("favorable_spread_at_fill", spread > 0), ("adverse_spread_at_fill", spread < 0),
                                   ("cancel_pending_fill", bool(row.get("cancel_pending")))):
            if eligible:
                previous_candidate = representative.get(category)
                if previous_candidate is None or (category != "cancel_pending_fill" and abs(spread) > abs(D(str(previous_candidate["spread_contribution"])))):
                    representative[category] = {**candidate, "selection_reason": category}
        # Representative means largest absolute spread contribution, not a random sample.
        selected.sort(key=lambda r: (-abs(D(str(r["spread_contribution"]))), r["time_ns"], r["order_id"]))
        del selected[min(limit, 8):]
    for row in iter_rows(run_dir, "ledger"):
        ledger_count += 1
        if first is None:
            first = row
        if previous is not None:
            totals["inventory_mtm"] += D(str(previous["inventory"])) * (D(str(row["mark_price"])) - D(str(previous["mark_price"])))
            elapsed = row["time_ns"] - previous["time_ns"]
            if elapsed < 0:
                raise ValueError("ledger timestamps regress")
            held = abs(D(str(previous["inventory"])))
            inventory_area += held * elapsed
            if inventory_limit is not None and held >= inventory_limit * D(".8"):
                near_limit_ns += elapsed
        previous = last = row
    end = config.get("sample", {}).get("end_ns")
    duration = None
    if first is not None and last is not None and end is not None:
        if end < last["time_ns"]:
            raise ValueError("ledger extends beyond trading bounds")
        elapsed = end - last["time_ns"]
        held = abs(D(str(last["inventory"])))
        inventory_area += held * elapsed
        if inventory_limit is not None and held >= inventory_limit * D(".8"):
            near_limit_ns += elapsed
        duration = end - first["time_ns"]
    warnings = list(record["warnings"])
    if first is None or last is None:
        warnings.append("Ledger evidence missing; contribution reconciliation unavailable.")
        net = residual = explained = None
    else:
        net = D(str(last["equity"])) - D(str(first["equity"]))
        explained = totals["spread_capture"] + totals["inventory_mtm"] - totals["fees"]
        residual = net - explained
    contributions = [{"component": "spread_capture", "amount": str(totals["spread_capture"]), "label": "체결 시 중간가격 대비 기여"},
                     {"component": "inventory_mtm", "amount": str(totals["inventory_mtm"]), "label": "재고 보유 중 가격변동 기여"},
                     {"component": "fees", "amount": str(-totals["fees"]), "label": "수수료·리베이트 기여"}]
    findings = ["손익 분해는 원장의 산술 기여이며 전략 성과의 인과관계를 증명하지 않습니다."]
    if net is not None:
        worst = min(contributions, key=lambda r: D(r["amount"]))
        if D(worst["amount"]) < 0:
            findings.append(f"가장 큰 음수 항목은 {worst['label']} ({worst['amount']} USDT)입니다.")
        if residual:
            warnings.append("Ledger and fill contributions do not reconcile; investigate artifacts before interpreting PnL.")
    findings.append("대표 주문은 절대 스프레드 기여가 큰 체결에서 선택합니다. 전체 거래의 대표 표본은 아닙니다.")
    selected = list(representative.values()) or [{**r, "selection_reason": "largest_absolute_spread_at_fill"} for r in selected[:1]]
    terminal_types = set()
    for row in iter_rows(run_dir, "orders"):
        status = row.get("status")
        if row.get("event") == "terminal":
            category = "rejected_order" if status == "REJECTED" else "unfilled_terminal_order" if (row.get("quantity") is not None and D(str(row["quantity"])) == D(str(row.get("remaining", "-1")))) else None
            if category is not None and category not in terminal_types:
                terminal_types.add(category)
                selected.append({"order_id": row["order_id"], "side": row.get("side"),
                                 "quantity": row.get("quantity"), "spread_contribution": None, "fee": None,
                                 "reason": row.get("reason"), "time_ns": row.get("time_ns"), "status": status,
                                 "selection_reason": category})
    if terminal_types:
        findings.append("취소·거절·표본 종료 주문도 상태별 첫 사례를 추가해 미체결 이유를 추적할 수 있습니다.")
    return {"summary": record["summary"], "contributions": contributions,
            "identity": {"net_pnl": str(net) if net is not None else None,
                         "explained_net_pnl": str(explained) if explained is not None else None,
                         "residual": str(residual) if residual is not None else None,
                         "reconciled": residual == ZERO if residual is not None else False},
            "findings": findings, "representative_orders": selected,
            "trace_status": "available" if (Path(run_dir) / "decision_traces.jsonl.gz").exists() else "legacy_missing",
            "counts": {"fills": fill_count, "ledger_rows": ledger_count}, "warnings": warnings,
            "inventory_exposure": {"time_weighted_absolute_inventory": str(inventory_area / duration) if duration else None,
                                   "near_limit_threshold_fraction": "0.8", "near_limit_duration_ns": near_limit_ns if inventory_limit is not None else None,
                                   "near_limit_time_fraction": str(D(near_limit_ns) / duration) if duration and inventory_limit is not None else None,
                                   "duration_ns": duration}}


def order_drilldown(run_dir, order_id, limit=200):
    if not 1 <= limit <= 1000:
        raise ValueError("timeline limit must be 1..1000")
    rows, missing, matched = [], [], 0
    decision_ids = set()
    for row in iter_rows(run_dir, "orders"):
        if row.get("order_id") == order_id and row.get("decision_id"):
            decision_ids.add(row["decision_id"])
    for channel in ("decision_traces", "orders", "execution", "fills", "timeline"):
        if not (Path(run_dir) / f"{channel}.jsonl.gz").exists():
            missing.append(channel)
            continue
        for row in iter_rows(run_dir, channel):
            linked = row.get("order_id") == order_id
            if channel == "decision_traces":
                linked = row.get("decision_id") in decision_ids or any(a.get("order_id") == order_id for a in row.get("actions", []))
            if linked:
                matched += 1
                rows.append({**row, "channel": channel})
                rows.sort(key=lambda r: (r.get("time_ns", 0), ("decision_traces", "orders", "execution", "fills", "timeline").index(r["channel"])))
                del rows[limit:]
    return {"order_id": order_id, "timeline": rows, "truncated": matched > limit,
            "total_matching_rows": matched, "missing": missing,
            "trace_status": "legacy_missing" if "decision_traces" in missing else "available"}


def _behavior(value, key):
    if key == "actions":
        return [{k: action.get(k) for k in ("side", "action", "price", "quantity") if k in action} for action in (value or [])]
    if value is None:
        return None
    result = {}
    for side in ("bid", "ask"):
        quote = value.get(side)
        if isinstance(quote, dict):
            result[side] = {k: str(D(str(quote[k])).normalize()) for k in ("price", "quantity") if k in quote}
        else:
            result[side] = quote
    return result


def _first_divergence(left_dir, right_dir, key):
    for left, right in zip_longest(iter_rows(left_dir, "decision_traces"), iter_rows(right_dir, "decision_traces")):
        a = {"time_ns": left.get("time_ns"), key: left.get(key)} if left is not None else None
        b = {"time_ns": right.get("time_ns"), key: right.get(key)} if right is not None else None
        behavior_a = {"time_ns": a["time_ns"], key: _behavior(a[key], key)} if a is not None else None
        behavior_b = {"time_ns": b["time_ns"], key: _behavior(b[key], key)} if b is not None else None
        if behavior_a != behavior_b:
            return {"time_ns": min(x["time_ns"] for x in (a, b) if x is not None), "left": a, "right": b,
                    "left_behavior": behavior_a, "right_behavior": behavior_b}
    return None


@_engine_precision
def compare_runs(left_dir, right_dir, allow_scenario_change=False):
    guard = comparison_guard(run_record(left_dir), run_record(right_dir), allow_scenario_change)
    if not guard["comparable"]:
        return {"guard": guard, "pnl_delta_identity": None, "contribution_rows": [],
                "first_desired_divergence": None, "first_action_divergence": None, "warnings": guard["reasons"]}
    left, right = explain_run(left_dir), explain_run(right_dir)
    rows = []
    for a, b in zip(left["contributions"], right["contributions"]):
        rows.append({"component": a["component"], "left": a["amount"], "right": b["amount"], "delta": str(D(b["amount"]) - D(a["amount"]))})
    delta = None if left["identity"]["net_pnl"] is None or right["identity"]["net_pnl"] is None else D(right["identity"]["net_pnl"]) - D(left["identity"]["net_pnl"])
    residual = delta - sum((D(r["delta"]) for r in rows), ZERO) if delta is not None else None
    traces = left["trace_status"] == right["trace_status"] == "available"
    warnings = left["warnings"] + right["warnings"]
    if not traces:
        warnings.append("Legacy decision traces missing: earliest behavioral divergence cannot be reconstructed.")
    return {"guard": guard, "contribution_rows": rows,
            "pnl_delta_identity": {"net_delta": str(delta) if delta is not None else None,
                                   "spread_delta": rows[0]["delta"], "inventory_mtm_delta": rows[1]["delta"],
                                   "fee_contribution_delta": rows[2]["delta"],
                                   "residual": str(residual) if residual is not None else None,
                                   "reconciled": residual == ZERO if residual is not None else False},
            "first_desired_divergence": _first_divergence(left_dir, right_dir, "risk_limited_target") if traces else None,
            "first_action_divergence": _first_divergence(left_dir, right_dir, "actions") if traces else None,
            "warnings": warnings}


@_engine_precision
def terminal_liquidation_reference(inventory, mid, book, now_ns, taker_bps, lot, max_age_ms=500):
    inventory, mid, taker_bps, lot = (D(str(x)) for x in (inventory, mid, taker_bps, lot))
    if any(not x.is_finite() for x in (inventory, mid, taker_bps, lot)) or lot <= 0 or mid <= 0:
        raise ValueError("invalid reference valuation inputs")
    amount = abs(inventory)
    result = {"reference_only": True, "status": "UNAVAILABLE", "requested_quantity": str(amount),
              "executable_quantity": "0", "residual_quantity": str(amount), "vwap": None,
              "fee": None, "midpoint_adjustment": None, "net_pnl_reference": None,
              "coverage": "0", "reason": None, "levels": []}
    if amount == 0:
        result.update(status="COMPLETE", fee="0", midpoint_adjustment="0", coverage="1", reason="no_inventory")
        return result
    age = now_ns - book["time_ns"]
    if age < 0 or age > max_age_ms * 1_000_000:
        result["reason"] = "stale_or_future_book"
        return result
    all_levels = [(D(str(p)), D(str(q))) for side in ("bids", "asks") for p, q in book[side]]
    if any(not p.is_finite() or not q.is_finite() or p <= 0 or q < 0 for p, q in all_levels):
        raise ValueError("invalid terminal reference book level")
    bids = sorted(((D(str(p)), D(str(q))) for p, q in book["bids"] if D(str(q)) > 0), reverse=True)
    asks = sorted(((D(str(p)), D(str(q))) for p, q in book["asks"] if D(str(q)) > 0))
    if not bids or not asks or bids[0][0] >= asks[0][0]:
        result["reason"] = "empty_or_crossed_book"
        return result
    remaining = (amount / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
    filled = notional = ZERO
    for price, available in bids if inventory > 0 else asks:
        if not D(str(book.get("coverage_bid", bids[-1][0]))) <= price <= D(str(book.get("coverage_ask", asks[-1][0]))):
            continue
        quantity = (min(remaining, available) / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
        if quantity:
            filled += quantity
            notional += quantity * price
            remaining -= quantity
            result["levels"].append({"price": str(price), "quantity": str(quantity)})
        if remaining == 0:
            break
    fee = notional * taker_bps / 10000
    adjustment = (notional - mid * filled) * (1 if inventory > 0 else -1) - fee
    residual = amount - filled
    result.update(status="COMPLETE" if residual == 0 else "PARTIAL", executable_quantity=str(filled),
                  residual_quantity=str(residual), vwap=str(notional / filled) if filled else None,
                  fee=str(fee), midpoint_adjustment=str(adjustment), coverage=str(filled / amount),
                  reason="complete_visible_depth_reference" if residual == 0 else "insufficient_depth_or_sub_lot_residual")
    return result


def final_book(sample_dir, end_ns, clock_mode="exchange"):
    from lobcap.backtest.replay import load_sample, iter_events
    from lobcap.backtest.engine import MarketBook
    initial, manifest = load_sample(sample_dir)
    market = MarketBook(initial)
    if clock_mode not in {"exchange", "recorded"}:
        raise ValueError("unknown reference clock")
    receive_boundary = manifest.get("bootstrap_receive_ns", initial.time_ns)
    if clock_mode == "recorded" and receive_boundary > end_ns:
        raise ValueError("sample bootstrap not observed by reference time")
    for event in iter_events(sample_dir):
        if event.time_ns >= end_ns:
            break
        if event.receive_ns < event.time_ns:
            raise ValueError("negative capture delay in reference sample")
        if event.kind != "depth":
            continue
        receive_boundary = max(receive_boundary, event.receive_ns)
        if clock_mode == "recorded" and receive_boundary >= end_ns:
            continue
        market.apply(event)
    return {"bids": tuple(market.bids.items()), "asks": tuple(market.asks.items()), "time_ns": market.time_ns,
            "coverage_bid": initial.coverage_bid, "coverage_ask": initial.coverage_ask,
            "sample_sha256": manifest["sample_sha256"], "clock_mode": clock_mode}


@_engine_precision
def reference_from_run(run_dir, sample_dir):
    record = run_record(run_dir)
    config = record["config"]
    last = None
    for row in iter_rows(run_dir, "ledger"):
        last = row
    if last is None:
        return {"status": "UNAVAILABLE", "reference_only": True, "reason": "missing_ledger"}
    end = config["sample"]["end_ns"]
    sample_manifest = read_object(Path(sample_dir) / "sample_manifest.json")
    if not record["sample_sha256"] or sample_manifest.get("sample_sha256") != record["sample_sha256"]:
        raise ValueError("terminal reference sample differs from original execution")
    book = final_book(sample_dir, end, config.get("clock_mode", "exchange"))
    if not record["sample_sha256"] or book["sample_sha256"] != record["sample_sha256"]:
        raise ValueError("terminal reference sample differs from original execution")
    result = terminal_liquidation_reference(last["inventory"], last["mark_price"], book, end,
                                            config["taker_bps"], config["instrument"]["lot"])
    if result["status"] == "COMPLETE":
        result["net_pnl_reference"] = str(D(str(last["net_pnl"])) + D(result["midpoint_adjustment"]))
    result["original_net_pnl"] = last["net_pnl"]
    result["book_time_ns"] = book["time_ns"]
    result["valuation_time_ns"] = end
    return result
