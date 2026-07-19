"""tools/live_kill_switch_reset.py —— 只走三方强路径的人工复位工具(2026-07-05)。"""
import sys

import pytest

from live.governance.kill_switch_store import (
    kill_switch_db_path,
    load_kill_switch_state,
    save_kill_switch_state,
)
from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.events import (
    EVENT_KILL_SWITCH_ACTIVATED,
    EVENT_KILL_SWITCH_RESET,
)
from quantos.contracts.killswitch import KillSwitchState
from services.monitoring.domain_event_runtime import open_domain_event_bus
from services.risk.pre_trade.kill_switch import KillSwitch
from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore
from tools import live_kill_switch_reset as reset_tool


def _tripped_state(incident_id="trip-1"):
    ks = KillSwitch()
    ks.arm(Actor(actor_id="sentinel", kind=ActorKind.SYSTEM), "test breach", at=1000)
    # 强制成已知 incident_id,方便测试断言
    return ks.state.model_copy(update={"incident_id": incident_id})


def test_reset_requires_three_distinct_people(tmp_path, monkeypatch):
    path = str(tmp_path / "ks.json")
    save_kill_switch_state(path, _tripped_state())
    monkeypatch.setattr(reset_tool, "_venue_paths", lambda suffix: {"kill_switch": path})
    monkeypatch.setattr(sys, "argv", [
        "prog", "--venue", "binance", "--incident-id", "trip-1",
        "--requester", "alice", "--reviewer", "alice", "--approver", "bob",
    ])
    with pytest.raises(SystemExit, match="DISTINCT"):
        reset_tool.main()


def test_reset_rejects_incident_id_mismatch(tmp_path, monkeypatch):
    path = str(tmp_path / "ks.json")
    save_kill_switch_state(path, _tripped_state(incident_id="trip-real"))
    monkeypatch.setattr(reset_tool, "_venue_paths", lambda suffix: {"kill_switch": path})
    monkeypatch.setattr(sys, "argv", [
        "prog", "--venue", "binance", "--incident-id", "trip-wrong",
        "--requester", "alice", "--reviewer", "bob", "--approver", "carol",
    ])
    with pytest.raises(SystemExit, match="does not match"):
        reset_tool.main()


def test_reset_succeeds_with_three_distinct_people_and_matching_incident(tmp_path, monkeypatch):
    path = str(tmp_path / "ks.json")
    save_kill_switch_state(path, _tripped_state(incident_id="trip-1"))
    monkeypatch.setattr(reset_tool, "_venue_paths", lambda suffix: {"kill_switch": path})
    monkeypatch.setattr(sys, "argv", [
        "prog", "--venue", "binance", "--incident-id", "trip-1",
        "--requester", "alice", "--reviewer", "bob", "--approver", "carol",
    ])
    reset_tool.main()
    result = load_kill_switch_state(path)
    assert result.tripped is False
    assert result.reset_by.actor_id == "carol"          # executor 默认同 approver

    store = KillSwitchStateStore(kill_switch_db_path(path), create=False)
    try:
        assert store.load_state() == result
        assert store.pending_event_outbox() == ()
        assert store.chain_valid()
    finally:
        store.close()

    bus = open_domain_event_bus(str(tmp_path / "domain_events.db"))
    try:
        records = bus.records()
        assert [row["event"].event_type for row in records] == [
            EVENT_KILL_SWITCH_ACTIVATED,
            EVENT_KILL_SWITCH_RESET,
        ]
        assert records[1]["causation_id"] == records[0]["event"].event_id
        assert bus.chain_valid()
    finally:
        bus.close()


def test_reset_is_a_noop_when_not_tripped(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "ks.json")
    save_kill_switch_state(path, KillSwitchState())      # 从未触发
    monkeypatch.setattr(reset_tool, "_venue_paths", lambda suffix: {"kill_switch": path})
    monkeypatch.setattr(sys, "argv", [
        "prog", "--venue", "binance", "--incident-id", "trip-1",
        "--requester", "alice", "--reviewer", "bob", "--approver", "carol",
    ])
    reset_tool.main()
    assert "nothing to reset" in capsys.readouterr().out


def test_reset_tool_never_exposes_the_weak_single_party_path():
    # 结构性钉死:本工具的源码里不该出现弱路径 reset( 的调用(只走 reset_with_grant)
    import inspect
    src = inspect.getsource(reset_tool)
    assert "reset_with_grant" in src
    assert ".reset(" not in src
