"""K1 自治内核测试:CommandEnvelope / SqliteCommandLog / CommandBus。

钉死的核心不变量:**重复命令不产生重复副作用**(重放返回存证结果,
handler 调用计数恒为 1);链篡改必被抓;终态不可变;在飞命令只上面不重试。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.events import Event
from quantos.contracts.hashing import stable_hash
from services.monitoring.event_bus import DurableEventBus
from services.orchestration import (
    CommandBus,
    CommandBusError,
    CommandConflictError,
    CommandEnvelope,
    CommandEnvelopeError,
    CommandStateError,
    SqliteCommandLog,
    StateVersionConflictError,
)

ACTOR = Actor(actor_id="agent-1", kind=ActorKind.AGENT)


def _envelope(**overrides) -> CommandEnvelope:
    kwargs = {
        "command_id": "cmd-1",
        "command_type": "research.backtest.run",
        "requested_at": 1_700_000_000_000,
        "actor": ACTOR,
        "subject": "alpha:crowd_contra",
        "idempotency_key": "key-1",
        "payload": {"alpha": "crowd_contra", "window": 52},
    }
    kwargs.update(overrides)
    return CommandEnvelope.create(**kwargs)


def _memory_log() -> SqliteCommandLog:
    return SqliteCommandLog(":memory:", create=True)


# ---------------------------------------------------------------- envelope


def test_envelope_create_computes_payload_hash() -> None:
    env = _envelope()
    assert env.payload_hash == stable_hash(dict(env.payload))
    # 存为不可变 Mapping:改写载荷被拒。
    with pytest.raises(TypeError):
        env.payload["alpha"] = "carry"  # type: ignore[index]


@pytest.mark.parametrize(
    "field",
    ["command_id", "command_type", "subject", "idempotency_key"],
)
def test_envelope_rejects_empty_required_fields(field: str) -> None:
    with pytest.raises(CommandEnvelopeError):
        _envelope(**{field: ""})


def test_envelope_rejects_negative_requested_at() -> None:
    with pytest.raises(CommandEnvelopeError):
        _envelope(requested_at=-1)


def test_envelope_rejects_payload_hash_mismatch() -> None:
    env = _envelope()
    with pytest.raises(CommandEnvelopeError):
        CommandEnvelope(
            command_id=env.command_id,
            command_type=env.command_type,
            requested_at=env.requested_at,
            actor=env.actor,
            subject=env.subject,
            idempotency_key=env.idempotency_key,
            payload_hash=stable_hash({"tampered": True}),
            payload=env.payload,
        )


# ---------------------------------------------------------------- idempotency log


def test_begin_is_retry_safe_for_identical_replay() -> None:
    log = _memory_log()
    first = log.begin(_envelope(), recorded_at=1_700_000_000_000)
    # 同 (command_type, idempotency_key) + 同 payload、不同 command_id(典型重试)。
    second = log.begin(
        _envelope(command_id="cmd-retry"), recorded_at=1_700_000_000_001
    )
    assert second.command_id == first.command_id
    assert second.record_hash == first.record_hash
    assert log.count() == 1
    assert log.chain_valid()


def test_begin_conflict_same_key_different_payload() -> None:
    log = _memory_log()
    log.begin(_envelope(), recorded_at=1_700_000_000_000)
    with pytest.raises(CommandConflictError):
        log.begin(
            _envelope(command_id="cmd-2", payload={"alpha": "carry"}),
            recorded_at=1_700_000_000_001,
        )


def test_begin_conflict_same_command_id_different_key() -> None:
    log = _memory_log()
    log.begin(_envelope(), recorded_at=1_700_000_000_000)
    with pytest.raises(CommandConflictError):
        log.begin(_envelope(idempotency_key="key-2"), recorded_at=1_700_000_000_001)


# ---------------------------------------------------------------- command bus


def test_execute_runs_handler_exactly_once_on_replay() -> None:
    """THE 核心不变量:重复命令不产生重复副作用。"""
    log = _memory_log()
    bus = CommandBus(log)
    calls: list[str] = []

    def handler(env: CommandEnvelope) -> dict:
        calls.append(env.command_id)
        return {"ok": True, "n": len(calls)}

    first = bus.execute(_envelope(), handler)
    assert first == {"ok": True, "n": 1}
    # 重试:同 key 同 payload,不同 command_id。
    replay = bus.execute(_envelope(command_id="cmd-retry"), handler)
    assert replay == {"ok": True, "n": 1}  # 存证结果原样返回
    assert calls == ["cmd-1"]  # handler 只跑过一次
    assert log.count() == 1


def test_expected_state_version_mismatch_raises_before_any_log_write() -> None:
    log = _memory_log()
    bus = CommandBus(log)
    env = _envelope(expected_state_version=7)
    with pytest.raises(StateVersionConflictError):
        bus.execute(env, lambda e: None, state_version=8)
    assert log.count() == 0  # 乐观锁:日志零写入
    assert bus.execute(env, lambda e: "done", state_version=7) == "done"


def test_handler_exception_marks_failed_and_reraises() -> None:
    log = _memory_log()
    bus = CommandBus(log)

    def boom(env: CommandEnvelope) -> None:
        raise KeyError("missing factor")

    with pytest.raises(KeyError):
        bus.execute(_envelope(), boom)
    record = log.get("cmd-1")
    assert record is not None and record.status == "FAILED"
    assert json.loads(record.result_json) == {"error_class": "KeyError"}
    # 失败命令不自动重跑:同 key 重提被拒,需换 key 显式重提。
    with pytest.raises(CommandStateError):
        bus.execute(_envelope(command_id="cmd-1b"), lambda e: "retry")
    assert log.chain_valid()


def test_terminal_states_are_immutable() -> None:
    log = _memory_log()
    bus = CommandBus(log)
    bus.execute(_envelope(), lambda e: {"v": 1})
    with pytest.raises(CommandStateError):
        log.complete("cmd-1", result_json="{}", at=1_700_000_000_002)
    with pytest.raises(CommandStateError):
        log.fail("cmd-1", error_class="X", at=1_700_000_000_002)
    # fail 后 complete 同样被拒。
    log.begin(
        _envelope(command_id="cmd-9", idempotency_key="key-9"),
        recorded_at=1_700_000_000_000,
    )
    log.fail("cmd-9", error_class="ValueError", at=1_700_000_000_001)
    with pytest.raises(CommandStateError):
        log.complete("cmd-9", result_json="{}", at=1_700_000_000_002)


def test_in_flight_surfaces_received_and_blocks_rerun() -> None:
    log = _memory_log()
    bus = CommandBus(log)
    log.begin(_envelope(), recorded_at=1_700_000_000_000)  # 模拟崩溃在飞
    in_flight = log.in_flight()
    assert [r.command_id for r in in_flight] == ["cmd-1"]
    # 727 §0.4.8 镜像:在飞命令绝不自动重试。
    with pytest.raises(CommandBusError):
        bus.execute(_envelope(command_id="cmd-1b"), lambda e: "rerun")
    # 完成另一条后 in_flight 收缩。
    bus.execute(
        _envelope(command_id="cmd-2", idempotency_key="key-2"), lambda e: "ok"
    )
    assert [r.command_id for r in log.in_flight()] == ["cmd-1"]


def test_events_published_with_causation_and_correlation() -> None:
    log = _memory_log()
    event_bus = DurableEventBus(":memory:", create=True)
    bus = CommandBus(log, event_bus=event_bus)
    event = Event(
        event_id="evt-1",
        event_type="research.backtest.completed",
        timestamp=1_700_000_000_000_000,  # µs(schema 现行约定)
        source="tests.orchestration",
        domain="research",
        payload={"alpha": "crowd_contra"},
    )

    def handler(env: CommandEnvelope):
        return {"done": True}, [event]

    result = bus.execute(_envelope(), handler, correlation_id="corr-42")
    assert result == {"done": True}
    records = event_bus.records()
    assert len(records) == 1
    assert records[0]["causation_id"] == "cmd-1"       # 命令→事件因果链
    assert records[0]["correlation_id"] == "corr-42"
    # correlation_id 缺省回落到 command_id。
    bus.execute(
        _envelope(command_id="cmd-2", idempotency_key="key-2"),
        lambda e: ("r", [Event(
            event_id="evt-2",
            event_type="research.backtest.completed",
            timestamp=1_700_000_000_100_000,
            source="tests.orchestration",
            payload={},
        )]),
    )
    assert event_bus.records()[1]["correlation_id"] == "cmd-2"


def test_events_without_event_bus_raise_and_mark_failed() -> None:
    log = _memory_log()
    bus = CommandBus(log)  # 无 event_bus
    event = Event(
        event_id="evt-1",
        event_type="research.backtest.completed",
        timestamp=1_700_000_000_000_000,
        source="tests.orchestration",
        payload={},
    )
    with pytest.raises(CommandBusError):
        bus.execute(_envelope(), lambda e: ("r", [event]))
    # 事件无处可去 = 部分状态风险 → 记 FAILED,不假装成功。
    assert log.get("cmd-1").status == "FAILED"  # type: ignore[union-attr]


def test_chain_valid_after_mixed_operations() -> None:
    log = _memory_log()
    bus = CommandBus(log)
    bus.execute(_envelope(), lambda e: {"v": 1})
    bus.execute(_envelope(command_id="cmd-2", idempotency_key="k2"), lambda e: "x")
    with pytest.raises(RuntimeError):
        bus.execute(
            _envelope(command_id="cmd-3", idempotency_key="k3"),
            lambda e: (_ for _ in ()).throw(RuntimeError("mid-chain failure")),
        )
    log.begin(
        _envelope(command_id="cmd-4", idempotency_key="k4"),
        recorded_at=1_700_000_000_000,
    )
    assert log.count() == 4
    assert log.chain_valid()
    # 重链正确性:中间行 FAILED 迁移后,前后行链值仍自洽(打开时全链复核)。
    assert [r.command_id for r in log.in_flight()] == ["cmd-4"]


def test_chain_tamper_detected_via_raw_sql(tmp_path) -> None:
    path = str(tmp_path / "commands.db")
    log = SqliteCommandLog(path, create=True)
    bus = CommandBus(log)
    bus.execute(_envelope(), lambda e: {"v": 1})
    log.close()
    # 绕过本类直接改库(篡改 subject)。
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA busy_timeout=5000")
    raw.execute(
        "UPDATE command_log SET subject='alpha:carry' WHERE command_id='cmd-1'"
    )
    raw.commit()
    raw.close()
    # fail-closed:篡改后的库在打开时全链校验即拒绝(与 kill_switch_store 同法)。
    from services.orchestration import CommandLogError

    with pytest.raises(CommandLogError):
        SqliteCommandLog(path)


def test_persisted_across_reopen(tmp_path) -> None:
    path = str(tmp_path / "commands.db")
    log = SqliteCommandLog(path, create=True)
    bus = CommandBus(log)
    bus.execute(_envelope(), lambda e: {"v": 42})
    log.begin(
        _envelope(command_id="cmd-2", idempotency_key="k2"),
        recorded_at=1_700_000_000_000,
    )
    log.close()

    reopened = SqliteCommandLog(path)  # create=False,文件必须存在
    assert reopened.count() == 2
    completed = reopened.get("cmd-1")
    assert completed is not None and completed.status == "COMPLETED"
    assert json.loads(completed.result_json) == {"v": 42}  # type: ignore[arg-type]
    assert [r.command_id for r in reopened.in_flight()] == ["cmd-2"]
    assert reopened.chain_valid()
    # 重开后重放依旧不跑 handler。
    calls: list[str] = []
    replayed = CommandBus(reopened).execute(
        _envelope(command_id="cmd-retry"),
        lambda e: calls.append(e.command_id),
    )
    assert replayed == {"v": 42}
    assert calls == []
    reopened.close()


def test_missing_store_is_fail_closed(tmp_path) -> None:
    from services.orchestration import CommandLogError

    with pytest.raises(CommandLogError):
        SqliteCommandLog(str(tmp_path / "absent.db"))  # create=False 且文件不存在
    with pytest.raises(CommandLogError):
        SqliteCommandLog(":memory:")  # 内存库不 create 无意义,拒绝
