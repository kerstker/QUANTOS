"""preflight_check(live/execution/reconciliation.py):调仓前置检查——drift 超容忍度就 HARD_STOP,
不在已知不一致的账本基础上继续计算/提交新调仓。2026-07-04 对抗式审查发现 #7:reconcile_positions
此前零调用方,drift 永远不会被发现。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.5 延伸。"""
import pytest

from live.execution.reconciliation import preflight_check


def test_in_sync_proceeds():
    r = preflight_check({"BTC": 0.01}, {"BTC": 0.01})
    assert r["proceed"] is True and r["in_sync"] is True


def test_drift_beyond_tolerance_hard_stops():
    r = preflight_check({"BTC": 0.0}, {"BTC": 0.002}, tolerance=1e-6)
    assert r["proceed"] is False and r["in_sync"] is False
    assert r["max_abs_drift"] == pytest.approx(0.002)
    assert r["drift"]["BTC"] == pytest.approx(0.002)


def test_drift_within_tolerance_proceeds():
    r = preflight_check({"BTC": 0.010000}, {"BTC": 0.0100001}, tolerance=1e-4)
    assert r["proceed"] is True


def test_empty_both_sides_proceeds():
    r = preflight_check({}, {})
    assert r["proceed"] is True and r["max_abs_drift"] == 0.0
