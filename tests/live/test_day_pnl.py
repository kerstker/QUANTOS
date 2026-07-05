"""live/execution/day_pnl.py —— 日内盈亏跟踪(2026-07-05,机构审计 P1 的一部分)。

`check_daily_loss` 门此前永远拿到 day_pnl=0.0(硬编码),日内止损结构性永不触发。
本模块持久化"当日起始权益"(与 peak_equity 同款文件式状态跨进程/跨调用持久化),
day_pnl = 当前权益 - 当日起始权益;跨自然日(UTC)自动重置为 0(新的一天,新的起点,
不是"忘记了昨天亏了多少"——日内止损定义上就是当日口径)。
"""
import pytest

from live.execution.day_pnl import compute_day_pnl


def test_new_day_key_resets_to_zero_and_records_start_equity():
    day_pnl, new_state = compute_day_pnl(None, equity=1000.0, day_key="2026-07-05")
    assert day_pnl == 0.0
    assert new_state == {"day": "2026-07-05", "start_equity": 1000.0}


def test_same_day_computes_pnl_against_recorded_start():
    state = {"day": "2026-07-05", "start_equity": 1000.0}
    day_pnl, new_state = compute_day_pnl(state, equity=950.0, day_key="2026-07-05")
    assert day_pnl == pytest.approx(-50.0)
    assert new_state == state                        # 同一天,起点不变


def test_day_rollover_resets_baseline_honestly():
    # 昨天亏了很多,但今天是新的一天——日内止损口径重新起算,不是延续昨天的累计
    state = {"day": "2026-07-04", "start_equity": 1000.0}
    day_pnl, new_state = compute_day_pnl(state, equity=700.0, day_key="2026-07-05")
    assert day_pnl == 0.0
    assert new_state == {"day": "2026-07-05", "start_equity": 700.0}


def test_corrupt_or_nonpositive_start_equity_resets_honestly():
    state = {"day": "2026-07-05", "start_equity": 0.0}
    day_pnl, new_state = compute_day_pnl(state, equity=500.0, day_key="2026-07-05")
    assert day_pnl == 0.0
    assert new_state["start_equity"] == 500.0
