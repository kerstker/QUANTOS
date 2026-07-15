"""reconcile_positions(live/execution/reconciliation.py):纯函数,只报告 drift 不自动修正。设计:
docs/engineering/specs/2026-07-04-live-execution-stack-design.md §3.5。"""
import pytest

from live.execution.reconciliation import reconcile_positions


def test_in_sync_when_positions_match_exactly():
    r = reconcile_positions({"BTC": 0.01, "ETH": -1.5}, {"BTC": 0.01, "ETH": -1.5})
    assert r["in_sync"] is True
    assert r["max_abs_drift"] == 0.0
    assert r["drift"] == {"BTC": 0.0, "ETH": 0.0}


def test_drift_detected_when_positions_differ():
    r = reconcile_positions({"BTC": 0.010}, {"BTC": 0.012})
    assert r["in_sync"] is False
    assert r["drift"]["BTC"] == pytest.approx(0.002)
    assert r["max_abs_drift"] == pytest.approx(0.002)


def test_instrument_only_in_venue_treated_as_ledger_zero():
    r = reconcile_positions({}, {"BTC": 0.01})
    assert r["drift"]["BTC"] == pytest.approx(0.01)
    assert r["in_sync"] is False


def test_instrument_only_in_ledger_treated_as_venue_zero():
    r = reconcile_positions({"BTC": 0.01}, {})
    assert r["drift"]["BTC"] == pytest.approx(-0.01)
    assert r["in_sync"] is False


def test_within_tolerance_counts_as_in_sync():
    r = reconcile_positions({"BTC": 0.01}, {"BTC": 0.0100001}, tolerance=1e-4)
    assert r["in_sync"] is True


def test_empty_both_sides_is_in_sync():
    r = reconcile_positions({}, {})
    assert r["in_sync"] is True and r["max_abs_drift"] == 0.0 and r["drift"] == {}


def test_does_not_mutate_inputs():
    ledger = {"BTC": 0.01}
    venue = {"BTC": 0.02}
    reconcile_positions(ledger, venue)
    assert ledger == {"BTC": 0.01} and venue == {"BTC": 0.02}       # 纯函数,不修改入参(不自动修正)
