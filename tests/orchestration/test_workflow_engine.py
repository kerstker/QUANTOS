"""K4b Workflow Engine 测试:定义/实例/guard 转换/断点恢复/崩溃语义/链。"""
from __future__ import annotations

import sqlite3

import pytest

from quantos.contracts.alpha import Actor, ActorKind
from services.orchestration import (
    CommandBus,
    CommandBusError,
    CommandEnvelope,
    SqliteCommandLog,
    SqliteWorkflowStore,
    WorkflowConflictError,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowError,
    WorkflowNotFoundError,
    WorkflowStateError,
    WorkflowStep,
)
from services.orchestration.recovery import resolve_in_flight

T0 = 1_700_000_000_000
ACTOR = Actor(actor_id="wf-runner", kind=ActorKind.SYSTEM)

DEFN = WorkflowDefinition(
    workflow_type="research-cycle",
    version="1.0",
    steps=(
        WorkflowStep(step_id="discover", command_type="research.discover"),
        WorkflowStep(step_id="validate", command_type="research.validate"),
        WorkflowStep(step_id="publish", command_type="research.publish"),
    ),
)


def _stack():
    log = SqliteCommandLog(":memory:", create=True)
    store = SqliteWorkflowStore(":memory:", create=True)
    engine = WorkflowEngine(store, CommandBus(log))
    return log, store, engine


def _started(engine, instance_id="inst-1", context=None):
    engine.register_workflow(DEFN, now_ms=T0)
    return engine.start(
        "research-cycle",
        instance_id=instance_id,
        context={} if context is None else context,
        actor=ACTOR,
        now_ms=T0,
    )


# ---------------------------------------------------------------- 定义与启动


def test_register_idempotent_and_version_conflict() -> None:
    _, _, engine = _stack()
    engine.register_workflow(DEFN, now_ms=T0)
    engine.register_workflow(DEFN, now_ms=T0 + 1)  # 全字段一致 → 幂等
    changed = WorkflowDefinition(
        workflow_type="research-cycle", version="2.0", steps=DEFN.steps
    )
    with pytest.raises(WorkflowConflictError):
        engine.register_workflow(changed, now_ms=T0)


def test_definition_validation() -> None:
    with pytest.raises(ValueError):
        WorkflowDefinition(workflow_type="x", version="1", steps=())  # 至少 1 步
    with pytest.raises(ValueError):
        WorkflowDefinition(
            workflow_type="x",
            version="1",
            steps=(
                WorkflowStep(step_id="dup", command_type="a"),
                WorkflowStep(step_id="dup", command_type="b"),
            ),
        )


def test_start_unknown_type_is_fail_closed() -> None:
    _, _, engine = _stack()
    with pytest.raises(WorkflowNotFoundError):
        engine.start("ghost", instance_id="i", context={}, actor=ACTOR, now_ms=T0)


def test_start_idempotent_and_conflict() -> None:
    _, _, engine = _stack()
    first = _started(engine)
    assert first.status == "PENDING"
    again = engine.start(
        "research-cycle", instance_id="inst-1", context={}, actor=ACTOR, now_ms=T0 + 1
    )
    assert again == first  # 幂等
    with pytest.raises(WorkflowConflictError):
        engine.start(
            "research-cycle",
            instance_id="inst-1",
            context={"different": True},
            actor=ACTOR,
            now_ms=T0,
        )


# ---------------------------------------------------------------- 推进与恢复


def test_step_by_step_to_completed() -> None:
    _, _, engine = _stack()
    _started(engine)
    calls: list[str] = []

    def executor(env):
        calls.append(env.command_type)
        return {"step": env.command_type}

    first = engine.run_next("inst-1", actor=ACTOR, executor=executor, now_ms=T0 + 1)
    assert first == {
        "instance_id": "inst-1",
        "status": "RUNNING",
        "step_id": "discover",
        "advanced": True,
    }
    engine.run_next("inst-1", actor=ACTOR, executor=executor, now_ms=T0 + 2)
    third = engine.run_next("inst-1", actor=ACTOR, executor=executor, now_ms=T0 + 3)
    assert third["status"] == "COMPLETED"
    assert calls == ["research.discover", "research.validate", "research.publish"]
    instance = engine.get("inst-1")
    assert instance.status == "COMPLETED" and instance.current_step_idx == 3
    with pytest.raises(WorkflowStateError):
        engine.run_next("inst-1", actor=ACTOR, executor=executor, now_ms=T0 + 4)


def test_step_failure_marks_failed_and_isolates_instances() -> None:
    _, _, engine = _stack()
    _started(engine, "inst-a")
    _started(engine, "inst-b")

    def flaky(env):
        raise ValueError("data gap")

    result = engine.run_next("inst-a", actor=ACTOR, executor=flaky, now_ms=T0 + 1)
    assert result["status"] == "FAILED" and result["advanced"] is False
    instance = engine.get("inst-a")
    assert instance.status == "FAILED" and instance.error == "ValueError"
    assert [(r.step_id, r.outcome, r.detail) for r in engine._store.step_runs("inst-a")] == [
        ("discover", "FAILED", "ValueError")
    ]
    # 实例级隔离:inst-b 照常推进。
    ok = engine.run_next("inst-b", actor=ACTOR, executor=lambda env: "ok", now_ms=T0 + 2)
    assert ok["status"] == "RUNNING" and ok["advanced"] is True


def test_resume_continues_from_breakpoint_without_rerunning() -> None:
    _, _, engine = _stack()
    _started(engine)
    calls: list[str] = []
    state = {"failed_once": False}

    def executor(env):
        calls.append(env.command_type)
        if env.command_type == "research.validate" and not state["failed_once"]:
            state["failed_once"] = True
            raise RuntimeError("transient")
        return "ok"

    engine.run_next("inst-1", actor=ACTOR, executor=executor, now_ms=T0 + 1)  # discover
    failed = engine.run_next("inst-1", actor=ACTOR, executor=executor, now_ms=T0 + 2)
    assert failed["status"] == "FAILED"
    # 显式恢复 → 从断点继续:discover 不重跑(K1 幂等),validate 以 attempt=1 新 key 重跑。
    resumed = engine.resume("inst-1", now_ms=T0 + 3)
    assert resumed.status == "RUNNING" and resumed.error == ""
    engine.run_next("inst-1", actor=ACTOR, executor=executor, now_ms=T0 + 4)
    done = engine.run_next("inst-1", actor=ACTOR, executor=executor, now_ms=T0 + 5)
    assert done["status"] == "COMPLETED"
    assert calls == [
        "research.discover",
        "research.validate",  # 失败那次
        "research.validate",  # attempt=1 重跑
        "research.publish",
    ]


def test_crash_in_flight_recovers_via_explicit_path() -> None:
    """崩溃在 CommandBus 中途(命令卡 RECEIVED)→ CommandBusError 上抛 →
    recovery MARK_FAILED → 实例 FAILED → resume → attempt+1 新 key 重跑完成。"""
    log, _, engine = _stack()
    _started(engine, context={"k": 1})
    # 模拟崩溃:直接用 engine 将用的同一 key 在 command log 里留下 RECEIVED。
    stuck = CommandEnvelope.create(
        command_id="wf:inst-1:discover:0",
        command_type="research.discover",
        requested_at=T0,
        actor=ACTOR,
        subject="workflow:inst-1",
        idempotency_key="wf:inst-1:discover:0",
        payload={"k": 1},
    )
    log.begin(stuck, recorded_at=T0)

    calls: list[str] = []
    with pytest.raises(CommandBusError):
        engine.run_next(
            "inst-1", actor=ACTOR, executor=lambda env: calls.append("x"), now_ms=T0 + 1
        )
    assert calls == []  # 绝不盲目重跑
    assert engine.get("inst-1").status == "RUNNING"  # 实例停 RUNNING 等裁决

    # recovery 显式处置:未知 → 已知失败。
    resolved = resolve_in_flight(log, stuck.command_id, resolution="MARK_FAILED", now_ms=T0 + 2)
    assert resolved.status == "FAILED"
    # 再 run_next:撞 FAILED 终态(CommandStateError)→ 实例记 FAILED。
    result = engine.run_next(
        "inst-1", actor=ACTOR, executor=lambda env: calls.append("x"), now_ms=T0 + 3
    )
    assert result["status"] == "FAILED"
    # 显式恢复,attempt=1 新 key,executor 真正首跑。
    engine.resume("inst-1", now_ms=T0 + 4)
    ok = engine.run_next(
        "inst-1", actor=ACTOR, executor=lambda env: calls.append("x"), now_ms=T0 + 5
    )
    assert ok["advanced"] is True
    assert calls == ["x"]


def test_illegal_transitions_are_rejected() -> None:
    _, _, engine = _stack()
    _started(engine)
    with pytest.raises(WorkflowStateError):
        engine.resume("inst-1", now_ms=T0)  # PENDING 不可 resume
    engine.run_next("inst-1", actor=ACTOR, executor=lambda env: "ok", now_ms=T0 + 1)
    with pytest.raises(WorkflowStateError):
        engine.resume("inst-1", now_ms=T0 + 2)  # RUNNING 不可 resume
    engine.run_next("inst-1", actor=ACTOR, executor=lambda env: "ok", now_ms=T0 + 3)
    engine.run_next("inst-1", actor=ACTOR, executor=lambda env: "ok", now_ms=T0 + 4)
    with pytest.raises(WorkflowStateError):
        engine.resume("inst-1", now_ms=T0 + 5)  # COMPLETED 终态
    with pytest.raises(WorkflowNotFoundError):
        engine.run_next("ghost", actor=ACTOR, executor=lambda env: "ok", now_ms=T0)


# ---------------------------------------------------------------- 链与持久化


def test_chain_tamper_detected_via_raw_sql(tmp_path) -> None:
    path = str(tmp_path / "wf.db")
    store = SqliteWorkflowStore(path, create=True)
    engine = WorkflowEngine(store, CommandBus(SqliteCommandLog(":memory:", create=True)))
    _started(engine)
    engine.run_next("inst-1", actor=ACTOR, executor=lambda env: "ok", now_ms=T0 + 1)
    assert engine.chain_valid()
    store.close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA busy_timeout=5000")
    raw.execute(
        "UPDATE workflow_instances SET status='COMPLETED' WHERE instance_id='inst-1'"
    )
    raw.commit()
    raw.close()
    with pytest.raises(WorkflowError):
        SqliteWorkflowStore(path)  # fail-closed:篡改库打开即拒


def test_persisted_across_reopen(tmp_path) -> None:
    path = str(tmp_path / "wf.db")
    log = SqliteCommandLog(":memory:", create=True)
    store = SqliteWorkflowStore(path, create=True)
    engine = WorkflowEngine(store, CommandBus(log))
    _started(engine)
    engine.run_next("inst-1", actor=ACTOR, executor=lambda env: "ok", now_ms=T0 + 1)
    store.close()

    reopened = SqliteWorkflowStore(path)  # create=False
    instance = reopened.get_instance("inst-1")
    assert instance.status == "RUNNING" and instance.current_step_idx == 1
    assert reopened.chain_valid()
    reopened.close()
    with pytest.raises(WorkflowError):
        SqliteWorkflowStore(str(tmp_path / "absent.db"))
