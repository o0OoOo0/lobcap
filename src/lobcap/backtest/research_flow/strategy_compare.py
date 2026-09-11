"""Comparable multi-policy scorecards for one frozen sample and scenario."""
from __future__ import annotations

from decimal import Decimal, localcontext

from .workbench_model import reviewed_metrics
from .catalog import comparison_guard
from lobcap.backtest.workspace.resolve import SCENARIO_FIELDS


ZERO = Decimal(0)


def _d(value, default=ZERO):
    result = default if value is None else Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite scorecard metric")
    return result


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
    if len({row['run_id'] for row in completed}) != len(completed):
        raise ValueError("duplicate scorecard run IDs")
    for row in completed:
        guard = comparison_guard(completed[0], row)
        if not guard['comparable']:
            raise ValueError('; '.join(guard['reasons']))
    result = []
    with localcontext() as context:
        context.prec = 50
        for row in completed:
            summary, config = row["summary"], row["config"]
            reviewed = reviewed_metrics(row["output_dir"])
            required = ('net_pnl','gross_pnl','fees','spread_capture','inventory_mtm',
                        'max_drawdown','max_abs_inventory','ending_inventory')
            if any(summary.get(key) is None for key in required):
                raise ValueError('missing required summary metrics')
            for key in required:
                _d(summary[key])
            execution = summary.get("execution", {})
            filled_orders = int(execution.get("orders_with_fill", 0))
            activated = int(execution.get("activated", 0))
            control = config.get("strategy") == "no_trade"
            evidence_ok = bool(reviewed.get("available") and reviewed.get("reconciled")
                and all(reviewed.get(key) is not None and _d(summary[key]) == _d(reviewed[key])
                        for key in required)
                and _d(summary['spread_capture']) + _d(summary['inventory_mtm']) == _d(summary['gross_pnl'])
                and _d(summary['gross_pnl']) - _d(summary['fees']) == _d(summary['net_pnl']))
            comparable = (evidence_ok and not control and filled_orders >= minimum_filled_orders
                          and reviewed.get('fill_events', 0) >= filled_orders
                          and activated >= filled_orders and _d(reviewed.get('turnover')) > 0)
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
    previous = None
    for position, row in enumerate(ordered, 1):
        score = (row['net_pnl'],row['max_drawdown'],abs(row['max_abs_inventory']))
        if score != previous:
            rank = position
        row['development_rank'] = rank
        previous = score
    return sorted(result, key=lambda row: (row["development_rank"] is None,
                                           row["development_rank"] or 10**9, row["strategy"], row["run_id"]))


def suite_note(scorecard):
    eligible = [row for row in scorecard if row["development_rank"] is not None]
    if not eligible:
        return "체결 및 원장 검증 기준을 충족한 전략이 없습니다."
    leaders = [row for row in eligible if row['development_rank']==1]
    names = ', '.join(row['strategy'] for row in leaders)
    negative = '최상위 실행도 순손실입니다. ' if leaders[0]['net_pnl'] < 0 else ''
    return (f"개발 표본의 {'공동 ' if len(leaders)>1 else ''}1위: {names}. {negative}"
            "이는 paper trading 후보 선정이 아니며 다른 날짜·비용·지연에서 재검증해야 합니다.")
