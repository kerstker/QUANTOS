"""live/governance/kill_switch_store.py —— kill-switch 跨进程持久化(2026-07-05,机构审计
发现:`KillSwitch()` 此前每次驱动器运行都全新构造,上一轮/哨兵触发的闩锁在下一次调用里
完全不存在——"halt 直到人工复位"这个latch 的核心承诺,在跨进程/跨调度场景下从未真正生效。
"""
import json

import pytest

from live.governance.kill_switch_store import load_kill_switch_state, save_kill_switch_state
from quantos.contracts.killswitch import KillSwitchState
from services.risk.pre_trade.kill_switch import KillSwitch


def test_missing_file_loads_untripped_default(tmp_path):
    state = load_kill_switch_state(str(tmp_path / "ks.json"))
    assert state.tripped is False


def test_save_then_load_round_trips(tmp_path):
    path = str(tmp_path / "ks.json")
    ks = KillSwitch()
    from quantos.contracts.alpha import Actor, ActorKind
    ks.arm(Actor(actor_id="sentinel", kind=ActorKind.SYSTEM), "test breach", at=1000)
    save_kill_switch_state(path, ks.state)
    loaded = load_kill_switch_state(path)
    assert loaded.tripped is True
    assert loaded.incident_id == ks.state.incident_id
    assert loaded.reason == "test breach"


def test_corrupt_file_fails_closed_as_tripped(tmp_path):
    # 状态文件损坏必须读成"已触发"——安全状态损坏绝不能被解读为"安全"(镜像既有
    # KillSwitch.is_active 的 fail-closed 哲学:损坏 = 更保守,不是更宽松)
    path = str(tmp_path / "ks.json")
    open(path, "w", encoding="utf-8").write("{not valid json")
    state = load_kill_switch_state(path)
    assert state.tripped is True


def test_cross_process_simulation_persisted_trip_survives_new_instance(tmp_path):
    # 模拟"哨兵进程触发 -> 驱动器下一次运行"这个真实场景:driver 的 KillSwitch(state=...)
    # 必须带着哨兵存下的闩锁状态构造,而不是从零开始
    path = str(tmp_path / "ks.json")
    from quantos.contracts.alpha import Actor, ActorKind
    sentinel_ks = KillSwitch()
    sentinel_ks.arm(Actor(actor_id="sentinel", kind=ActorKind.SYSTEM), "drawdown breach", at=500)
    save_kill_switch_state(path, sentinel_ks.state)

    driver_ks = KillSwitch(state=load_kill_switch_state(path))
    assert driver_ks.is_active() is True                # 驱动器"看得到"哨兵的触发
