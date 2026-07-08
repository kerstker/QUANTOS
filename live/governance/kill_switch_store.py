"""kill-switch 跨进程持久化(2026-07-05,机构审计发现)。设计:
docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md 补充。

**发现的缺口(比审计原文更根本):** `tools/live_track_advance.py`/`tools/live_risk_sentinel.py`
此前每次运行都 `KillSwitch()` 全新构造——latch 的核心承诺"halt 直到人工复位"在跨进程场景下
从未真正生效:本轮触发的闩锁,下一次调度(下周的驱动器、下小时的哨兵)完全看不到,自动
恢复成"未触发"。这个模块把 `KillSwitchState`(既有 pydantic 契约,原生 JSON 可序列化)
存成文件,让驱动器/哨兵在同一个 venue 上共享同一份闩锁真相。

fail-closed:文件损坏/无法解析 -> 读成已触发(安全状态损坏绝不能被解读为"安全",镜像
`KillSwitch.is_active()` 自身的同款哲学)。
"""
from __future__ import annotations

import json
import os

from quantos.contracts.killswitch import KillSwitchState


def load_kill_switch_state(path: str) -> KillSwitchState:
    if not os.path.exists(path):
        return KillSwitchState()                        # 从未触发过,合法的默认起点
    try:
        return KillSwitchState.model_validate_json(open(path, encoding="utf-8").read())
    except Exception:
        return KillSwitchState(tripped=True, incident_id="corrupt-state-fail-closed",
                               reason="kill-switch state file unreadable/corrupt — failing closed")


def save_kill_switch_state(path: str, state: KillSwitchState) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(state.model_dump_json())


def save_kill_switch_state_if_tripped(path: str, state: KillSwitchState) -> None:
    """**2026-07-05 安全审查 CRITICAL 修复**:driver 与哨兵共享同一个 kill-switch 状态文件、
    各自独立进程运行、彼此之间无锁——纯粹的读-改-写竞态:进程 A 读到 untripped,进程 B
    并发把文件写成 tripped(真实越限),A 自己这一轮评估也是 untripped,若无条件写回会用
    A 内存里的旧 untripped 值覆盖 B 刚落盘的真实熔断,静默撤销刚触发的保护——恰好发生在
    最需要保护生效的时刻(市场剧烈波动时哨兵和驱动器最可能同时活跃)。

    修复用"只加不减"语义:只在**这次评估后状态确实是 tripped** 时才写盘;untripped 从不
    写盘(磁盘没有文件本身就是"从未触发"的合法默认状态,见 `load_kill_switch_state`)。
    这样任何进程都不可能用自己的 untripped 视角覆盖别的进程刚写入的 tripped——复位只能
    通过专门的 `tools/live_kill_switch_reset.py`(强制三方审批)显式进行。"""
    if state.tripped:
        save_kill_switch_state(path, state)
