from lobcap.binance_protocol import (
    event_is_older_than_snapshot,
    first_event_covers_snapshot,
    futures_contiguous,
    payload,
)


def ev(U, u, pu=None):
    p = {"U": U, "u": u}
    if pu is not None:
        p["pu"] = pu
    return {"schema_version": 2, "payload": p}


def test_payload_v2():
    assert payload({"payload": {"x": 1}})["x"] == 1


def test_snapshot_overlap_futures_rule():
    assert first_event_covers_snapshot(ev(100, 110, 99), 105)
    assert first_event_covers_snapshot(ev(105, 105, 99), 105)
    assert not first_event_covers_snapshot(ev(106, 110, 105), 105)


def test_drop_rule_is_strict_less_than():
    assert event_is_older_than_snapshot(ev(100, 104), 105)
    assert not event_is_older_than_snapshot(ev(100, 105), 105)


def test_futures_continuity_uses_pu_not_U():
    # U can differ from previous_u + 1; pu is the Futures continuity field.
    e = ev(950, 1100, pu=1000)
    assert futures_contiguous(1000, e)
    assert not futures_contiguous(999, e)


def test_missing_pu_is_gap_after_sync():
    assert not futures_contiguous(1000, ev(1001, 1002))
