"""live/execution/watchdog_check.py —— 死人开关(dead-man's switch,2026-07-05 机构审计 P0)。

背景:schtasks 调度静默失败(机器休眠/任务被系统重启打断/依赖缺失)不会有任何人知道——
8 周前向证据钟可能因此永久丢失一整周而没人发现。本模块是纯函数:给定某个任务最后一次
成功运行的时间戳,判断是否"过期未运行",不涉及任何 IO(读文件/发告警是调用方的事)。
"""
import pytest

from live.execution.watchdog_check import check_staleness


def test_recent_run_is_not_stale():
    out = check_staleness(1_000_000, now=1_000_000 + 3600_000, max_age_ms=86_400_000, label="x")
    assert out is None


def test_overdue_run_is_flagged():
    out = check_staleness(0, now=10 * 86_400_000, max_age_ms=9 * 86_400_000, label="forward_track")
    assert out is not None
    assert "forward_track" in out


def test_missing_ran_at_is_flagged_as_never_ran():
    out = check_staleness(None, now=1_000_000, max_age_ms=86_400_000, label="live_track")
    assert out is not None
    assert "never" in out.lower() or "从未" in out


def test_exactly_at_threshold_is_not_yet_stale():
    out = check_staleness(0, now=86_400_000, max_age_ms=86_400_000, label="x")
    assert out is None                                  # > 不是 >=,边界不误报


def test_future_timestamp_does_not_crash_or_misreport():
    # 时钟漂移/时区错误导致 ran_at 看起来在"未来"——不该产出负的过期时长或崩溃
    out = check_staleness(2_000_000, now=1_000_000, max_age_ms=86_400_000, label="x")
    assert out is None
