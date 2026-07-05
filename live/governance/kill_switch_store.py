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
