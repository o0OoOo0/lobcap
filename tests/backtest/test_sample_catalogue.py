import pytest

from lobcap.backtest.research_flow.sample_catalogue import (
    DEVELOPMENT, PILOT_OOS_LOCKED, assert_outcomes_allowed, excluded_window,
    fixed_pilot_windows, seal_catalogue,
)


def test_fixed_catalogue_is_preregistered_before_outcomes():
    windows = fixed_pilot_windows()
    assert len(windows) == 16
    assert len({row.window_id for row in windows}) == 16
    assert sum(row.role == DEVELOPMENT for row in windows) == 8
    assert sum(row.role == PILOT_OOS_LOCKED for row in windows) == 4
    assert all(row.end_ns-row.start_ns == 20*60*1_000_000_000 for row in windows)


def test_locked_roles_fail_closed_for_outcome_analysis():
    locked = next(row for row in fixed_pilot_windows() if row.role == PILOT_OOS_LOCKED)
    with pytest.raises(PermissionError, match="outcomes remain sealed"):
        assert_outcomes_allowed(locked)
    development = next(row for row in fixed_pilot_windows() if row.role == DEVELOPMENT)
    assert_outcomes_allowed(development)


def test_catalogue_retains_every_exclusion_and_reason():
    rows = [excluded_window(window, "synthetic quality failure") for window in fixed_pilot_windows()]
    catalogue = seal_catalogue(rows)
    assert len(catalogue["windows"]) == 16
    assert len(catalogue["catalogue_sha256"]) == 64
    with pytest.raises(ValueError, match="every preregistered"):
        seal_catalogue(rows[:-1])
