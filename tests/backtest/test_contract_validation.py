"""Independent expected-value fixtures shared with the offline validation CLI."""
import pytest

from lobcap.backtest.contract_validation import CASES, aggressor_side, run_case, validate_contracts


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_execution_contract(case, side):
    result = run_case(case, side)
    assert result["passed"], result
    assert result["all_fills_on_lot"]
    assert result["source_linked"]


def test_trade_direction_is_aggressor_not_maker():
    assert aggressor_side("buy") == "sell"
    assert aggressor_side("sell") == "buy"
    with pytest.raises(ValueError):
        aggressor_side("bid")


def test_external_is_not_reported_as_passed_when_not_run():
    assert validate_contracts()["external"]["status"] == "NOT_RUN"


def test_capacity_has_no_future_credit():
    split = run_case(next(case for case in CASES if case.name == "cap_split"))
    prefix = run_case(next(case for case in CASES if case.name == "cap_cannot_borrow_future"))
    assert split["per_event_fills"][0] == prefix["per_event_fills"][0] == "0"
