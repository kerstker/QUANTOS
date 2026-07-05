"""死人开关(dead-man's switch)判断(2026-07-05,机构审计 P0)。设计:
docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md 补充。

纯函数、无 IO。schtasks 调度静默失败(机器休眠/任务被系统重启打断)不会有任何人知道——
8 周前向证据钟可能因此永久丢失一整周而没人发现。这里只判断"是否过期未运行",不负责
读状态文件或发告警(那是调用方 `tools/watchdog.py` 的事)。
"""
from __future__ import annotations

from typing import Optional


def check_staleness(last_ran_at: Optional[int], *, now: int, max_age_ms: int,
                    label: str) -> Optional[str]:
    """last_ran_at=None(从未运行过,或状态文件缺失/损坏)-> 诚实标记"从未运行",而不是
    悄悄跳过。`now` 早于 `last_ran_at`(时钟漂移/时区问题)-> 不产出负的过期时长,视为
    未过期(fail-soft:宁可漏报一次真的过期,也不要因为一次时钟异常就制造假警报)。"""
    if last_ran_at is None:
        return f"WATCHDOG: {label} has never run (no status recorded)"
    age_ms = now - last_ran_at
    if age_ms < 0:
        return None
    if age_ms > max_age_ms:
        return (f"WATCHDOG: {label} last ran {age_ms / 86_400_000:.1f}d ago "
               f"(expected within {max_age_ms / 86_400_000:.1f}d) — scheduler may have failed silently")
    return None
