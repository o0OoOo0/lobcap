"""Comparable multi-policy scorecards for one frozen sample and scenario."""
from __future__ import annotations

from decimal import Decimal, localcontext

from .workbench_model import reviewed_metrics
from lobcap.backtest.workspace.resolve import SCENARIO_FIELDS


ZERO = Decimal(0)


def _d(value, default=ZERO):
    return default if value is None else Decimal(str(value))


def loss_driver(summary, reviewed):
    """Classify the largest accounting drag without claiming causality."""
    if reviewed.get("fill_events", 0) == 0:
        return "NO_EXECUTION"
    net, gross = _d(summary.get("net_pnl")), _d(summary.get("gross_pnl"))
    if net >= 0:
        return "POSITIVE_IN_SAMPLE"
    contributions = {
        "SPREAD_AT_FILL": _d(summary.get("spread_capture")),
        "INVENTORY_MTM": _d(summary.get("inventory_mtm")),
        "FEES": -_d(summary.get("fees")),
    }
    if gross >= 0 and contributions["FEES"] < 0:
        return "FEES"
    return min(contributions, key=lambda key: (contributions[key], key))


def _dominates(left, right):
    """Higher net PnL, lower drawdown and lower inventory exposure dominate."""
    a = (_d(left["net_pnl"]), -_d(left["max_drawdown"]), -abs(_d(left["max_abs_inventory"])))
    b = (_d(right["net_pnl"]), -_d(right["max_drawdown"]), -abs(_d(right["max_abs_inventory"])))
    return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))


def build_scorecard(rows, minimum_filled_orders=1):
    """Build an auditable development scorecard; never emits paper-ready status."""
    if type(minimum_filled_orders) is not int or minimum_filled_orders < 1:
        raise ValueError("minimum_filled_orders must be a positive integer")
    completed = [row for row in rows if row.get("rankable") and row.get("summary") and row.get("output_dir")]
    if not completed:
        return []
    samples = {row.get("sample_sha256") or row.get("manifest", {}).get("sample_sha256") for row in completed}
    if None in samples or len(samples) != 1:
        raise ValueError("strategy scorecard requires one identical sample")
    contracts = {row.get("execution_contract_version") or row.get("config", {}).get("execution_contract_version")
                 for row in completed}
    if None in contracts or len(contracts) != 1:
        raise ValueError("strategy scorecard requires one known execution contract")
    scenarios = {tuple((key, str(row.get("config", {}).get(key))) for key in sorted(SCENARIO_FIELDS))
                 for row in completed}
    if len(scenarios) != 1:
        raise ValueError("strategy scorecard requires identical execution and cost assumptions")
    result = []
    with localcontext() as context:
        context.prec = 50
        for row in completed:
            summary, config = row["summary"], row["config"]
            reviewed = reviewed_metrics(row["output_dir"])
            execution = summary.get("execution", {})
            filled_orders = int(execution.get("orders_with_fill", 0))
            activated = int(execution.get("activated", 0))
            control = config.get("strategy") == "no_trade"
            evidence_ok = bool(reviewed.get("available") and reviewed.get("reconciled"))
            comparable = evidence_ok and not control and filled_orders >= minimum_filled_orders
            markout = summary.get("markouts", {}).get("1000", {})
            result.append({
                "run_id": row["run_id"], "strategy": config.get("strategy"),
                "net_pnl": _d(summary.get("net_pnl")),
                "gross_pnl": _d(summary.get("gross_pnl")),
                "fees": _d(summary.get("fees")),
                "spread_capture": _d(summary.get("spread_capture")),
                "inventory_mtm": _d(summary.get("inventory_mtm")),
                "max_drawdown": _d(summary.get("max_drawdown")),
                "max_abs_inventory": _d(summary.get("max_abs_inventory")),
                "ending_inventory": _d(summary.get("ending_inventory")),
                "fill_rate": summary.get("fill_rate"),
                "quantity_fill_rate": summary.get("quantity_fill_rate"),
                "activated_orders": activated, "filled_orders": filled_orders,
                "fill_events": reviewed.get("fill_events", 0),
                "turnover": reviewed.get("turnover", ZERO),
                "break_even_fee_bps": reviewed.get("break_even_fee_bps"),
                "markout_1s_coverage": markout.get("coverage"),
                "markout_1s_drift": markout.get("volume_weighted_midpoint_drift"),
                "loss_driver": loss_driver(summary, reviewed),
                "reconciled": evidence_ok, "comparable": comparable,
                "control": control, "pareto": False, "development_rank": None,
                "paper_eligibility": "INSUFFICIENT_EVIDENCE",
            })
    candidates = [row for row in result if row["comparable"]]
    for candidate in candidates:
        candidate["pareto"] = not any(_dominates(other, candidate) for other in candidates if other is not candidate)
    ordered = sorted(candidates, key=lambda row: (-row["net_pnl"], row["max_drawdown"],
                                                  abs(row["max_abs_inventory"]), row["run_id"]))
    for rank, row in enumerate(ordered, 1):
        row["development_rank"] = rank
    return sorted(result, key=lambda row: (row["development_rank"] is None,
                                           row["development_rank"] or 10**9, row["strategy"], row["run_id"]))


def suite_note(scorecard):
    eligible = [row for row in scorecard if row["development_rank"] is not None]
    if not eligible:
        return "체결 및 원장 검증 기준을 충족한 전략이 없습니다."
    leader = min(eligible, key=lambda row: row["development_rank"])
    return (f"이 10분 개발 표본의 1위는 {leader['strategy']}입니다. "
            "이는 paper trading 후보 선정이 아니며 다른 날짜·비용·지연에서 재검증해야 합니다.")
