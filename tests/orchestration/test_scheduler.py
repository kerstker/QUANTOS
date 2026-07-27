"""K3 持久化确定性调度器测试。

钉死的核心语义:只有 leader 兑现;按计划时刻推进不漂移;错过补跑合并且
诚实留痕;同 (task,槽位,attempts) 重跑 = K1 重放零重复副作用;确定性退避
+ DLQ 只上面;未知结果(CommandBusError)大声抛出不记账。
"""
from __future__ import annotations

import sqlite3

import pytest

from quantos.contracts.alpha import Actor, ActorKind
from services.orchestration import (
    CommandBus,
    CommandBusError,
    Scheduler,
    SchedulerConflictError,
    SchedulerError,
    SqliteCommandLog,
    SqliteLeaderLeaseStore,
    SqliteSchedulerStore,
)

T0 = 1_700_000_000_000
ACTOR = Actor(actor_id="sched-node", kind=ActorKind.SYSTEM)
RESOURCE = "scheduler:tick"


def _stack(**store_kwargs):
    kwargs = {"backoff_base_ms": 100, "backoff_cap_ms": 1_000}
    kwargs.update(store_kwargs)
    log = SqliteCommandLog(":memory:", create=True)
    leases = SqliteLeaderLeaseStore(":memory:", create=True)
    tasks = SqliteSchedulerStore(":memory:", create=True, **kwargs)
    sched = Scheduler(tasks=tasks, command_bus=CommandBus(log), leases=leases)
    return tasks, leases, sched


def _run(sched, *, owner="node-a", now=T0, executor=lambda env: "ok", lease_ms=60_000):
    return sched.run_once(
        resource=RESOURCE,
        owner_id=owner,
        actor=ACTOR,
        now_ms=now,
        lease_ms=lease_ms,
        executor=executor,
    )


# ---------------------------------------------------------------- 注册


def test_register_idempotent_and_conflict() -> None:
    tasks, _, _ = _stack()
    first = tasks.register(
        "job", kind="INTERVAL", interval_ms=1_000, payload={"x": 1}, now_ms=T0
    )
    assert first.next_due_ms == T0 + 1_000
    again = tasks.register(
        "job", kind="INTERVAL", interval_ms=1_000, payload={"x": 1}, now_ms=T0 + 500
    )
    assert again == first  # 全字段一致 → 幂等
    with pytest.raises(SchedulerConflictError):
        tasks.register(
            "job", kind="INTERVAL", interval_ms=2_000, payload={"x": 1}, now_ms=T0
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"task_id": "", "kind": "INTERVAL", "interval_ms": 1, "payload": {}},
        {"task_id": "t", "kind": "INTERVAL", "payload": {}},                 # 缺 interval
        {"task_id": "t", "kind": "INTERVAL", "interval_ms": 0, "payload": {}},
        {"task_id": "t", "kind": "ONE_SHOT", "payload": {}},                 # 缺 run_at
        {"task_id": "t", "kind": "ONE_SHOT", "run_at_ms": -1, "payload": {}},
        {"task_id": "t", "kind": "CRON", "interval_ms": 1, "payload": {}},
        {"task_id": "t", "kind": "INTERVAL", "interval_ms": 1, "payload": {},
         "max_attempts": 0},
    ],
)
def test_register_validation(kwargs) -> None:
    tasks, _, _ = _stack()
    task_id = kwargs.pop("task_id")
    with pytest.raises(ValueError):
        tasks.register(task_id, now_ms=T0, **kwargs)


def test_due_tasks_deterministic_order() -> None:
    tasks, _, _ = _stack()
    tasks.register("c", kind="ONE_SHOT", run_at_ms=T0 + 100, payload={}, now_ms=T0)
    tasks.register("a", kind="ONE_SHOT", run_at_ms=T0 + 100, payload={}, now_ms=T0)
    tasks.register("b", kind="ONE_SHOT", run_at_ms=T0 + 50, payload={}, now_ms=T0)
    tasks.register("z", kind="ONE_SHOT", run_at_ms=T0 + 9_999, payload={}, now_ms=T0)
    due = tasks.due_tasks(T0 + 100)
    assert [t.task_id for t in due] == ["b", "a", "c"]  # (next_due, task_id) 序


# ---------------------------------------------------------------- 兑现语义


def test_interval_advances_by_slot_not_now() -> None:
    """按计划时刻推进:调度延迟不累积成周期漂移。"""
    tasks, _, sched = _stack()
    tasks.register("job", kind="INTERVAL", interval_ms=1_000, payload={}, now_ms=T0)
    summary = _run(sched, now=T0 + 1_500)  # 晚到 500ms,但 < 一个周期,不合并
    assert summary["completed"] == 1 and summary["coalesced"] == 0
    task = tasks.get("job")
    assert task.next_due_ms == T0 + 2_000  # slot(1000)+interval,不是 now+interval


def test_catchup_coalescing_records_missed_slots_honestly() -> None:
    tasks, _, sched = _stack()
    tasks.register("job", kind="INTERVAL", interval_ms=1_000, payload={}, now_ms=T0)
    calls: list[str] = []
    # 落后 3 个到期槽位(1000/2000/3000 全过)→ 2 条 COALESCED + 兑现最新 3000。
    summary = _run(sched, now=T0 + 3_500, executor=lambda env: calls.append("x"))
    assert summary["coalesced"] == 2
    assert summary["completed"] == 1 and summary["fired"] == 1
    assert calls == ["x"]  # executor 只跑最新槽位一次
    runs = tasks.runs("job")
    assert [(r.outcome, r.slot_ms) for r in runs] == [
        ("COALESCED", T0 + 1_000),
        ("COALESCED", T0 + 2_000),
        ("COMPLETED", T0 + 3_000),
    ]
    assert runs[0].detail == f"coalesced into slot {T0 + 3_000}"
    assert tasks.get("job").next_due_ms == T0 + 4_000


def test_replay_same_slot_zero_side_effects() -> None:
    """崩溃窗口(bus 已 complete、record_run 未落)→ 重放:executor 计数恒 1。"""
    tasks, _, sched = _stack()
    tasks.register("job", kind="ONE_SHOT", run_at_ms=T0 + 1_000, payload={}, now_ms=T0)
    executor_calls: list[str] = []

    original = tasks.record_run
    state = {"n": 0}

    def flaky(*args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("simulated crash after bus commit")
        return original(*args, **kwargs)

    tasks.record_run = flaky  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        _run(sched, now=T0 + 1_000, executor=lambda env: executor_calls.append("x"))
    tasks.record_run = original  # type: ignore[method-assign]

    # 恢复后重跑同一槽位:K1 重放,executor 不再跑;record_run 原子性 → 恰一条 run。
    summary = _run(sched, now=T0 + 1_000, executor=lambda env: executor_calls.append("x"))
    assert summary["completed"] == 1
    assert executor_calls == ["x"]
    runs = tasks.runs("job")
    assert [(r.outcome, r.slot_ms) for r in runs] == [("COMPLETED", T0 + 1_000)]
    assert tasks.get("job").enabled is False


def test_one_shot_fires_once_then_disabled() -> None:
    tasks, _, sched = _stack()
    tasks.register("once", kind="ONE_SHOT", run_at_ms=T0 + 500, payload={}, now_ms=T0)
    assert _run(sched, now=T0 + 499)["fired"] == 0  # 未到期
    assert _run(sched, now=T0 + 500)["completed"] == 1
    task = tasks.get("once")
    assert task.enabled is False
    assert _run(sched, now=T0 + 999_999)["fired"] == 0  # 不重跑


# ---------------------------------------------------------------- 退避与 DLQ


def test_failure_backoff_is_deterministic() -> None:
    tasks, _, sched = _stack()  # base=100, cap=1000
    tasks.register("job", kind="INTERVAL", interval_ms=1_000, payload={}, now_ms=T0)

    def boom(env):
        raise ValueError("bad factor")

    first = _run(sched, now=T0 + 1_000, executor=boom)
    assert first["failed"] == 1 and first["dlq"] == 0
    task = tasks.get("job")
    assert task.attempts == 1
    assert task.next_due_ms == T0 + 1_000 + min(100 * 2**1, 1_000)  # 1200

    second = _run(sched, now=T0 + 1_200, executor=boom)
    assert second["failed"] == 1
    task = tasks.get("job")
    assert task.attempts == 2
    assert task.next_due_ms == T0 + 1_200 + min(100 * 2**2, 1_000)  # 1600
    runs = tasks.runs("job")
    assert [r.outcome for r in runs] == ["FAILED", "FAILED"]
    assert runs[0].detail == "ValueError"


def test_dlq_after_max_attempts_and_surfaced() -> None:
    tasks, _, sched = _stack()
    tasks.register(
        "job", kind="INTERVAL", interval_ms=1_000, payload={}, max_attempts=2, now_ms=T0
    )

    def boom(env):
        raise KeyError("nope")

    _run(sched, now=T0 + 1_000, executor=boom)          # attempts=1 → 退避
    summary = _run(sched, now=T0 + 1_200, executor=boom)  # attempts=2 → DLQ
    assert summary["dlq"] == 1
    task = tasks.get("job")
    assert task.dead is True and task.attempts == 2
    assert [r.outcome for r in tasks.runs("job")] == ["FAILED", "FAILED", "DLQ"]
    assert "attempts exhausted (2>=2)" in tasks.runs("job")[-1].detail
    assert [t.task_id for t in tasks.dead_tasks()] == ["job"]  # 只上面
    assert tasks.due_tasks(T0 + 99_999) == ()  # 不再到期,绝不自动重试


def test_one_task_failure_does_not_break_round() -> None:
    tasks, _, sched = _stack()
    tasks.register("aaa", kind="ONE_SHOT", run_at_ms=T0, payload={}, now_ms=T0)
    tasks.register("bbb", kind="ONE_SHOT", run_at_ms=T0, payload={}, now_ms=T0)

    def executor(env):
        if env.command_type == "aaa":
            raise ValueError("first task fails")
        return "ok"

    summary = _run(sched, now=T0, executor=executor)
    assert summary["failed"] == 1 and summary["completed"] == 1
    assert tasks.get("bbb").enabled is False  # 第二个照常完成


def test_unknown_outcome_aborts_loudly_without_bookkeeping() -> None:
    """CommandBusError(结果未知)→ 大声抛出,不记 FAILED,任务原样留待人工。"""
    tasks, _, sched = _stack()
    tasks.register("job", kind="ONE_SHOT", run_at_ms=T0, payload={}, now_ms=T0)

    def confused(env):
        raise CommandBusError("simulated in-flight conflict")

    with pytest.raises(CommandBusError):
        _run(sched, now=T0, executor=confused)
    assert tasks.runs("job") == ()  # 未知结果绝不记账
    task = tasks.get("job")
    assert task.attempts == 0 and task.dead is False


# ---------------------------------------------------------------- leader 门控


def test_non_leader_records_skipped_and_never_executes() -> None:
    tasks, leases, sched = _stack()
    tasks.register("job", kind="ONE_SHOT", run_at_ms=T0, payload={}, now_ms=T0)
    leases.acquire(RESOURCE, owner_id="node-a", now_ms=T0, lease_ms=60_000)
    calls: list[str] = []
    summary = _run(sched, owner="node-b", now=T0 + 1, executor=lambda env: calls.append("x"))
    assert summary["leader"] is False
    assert summary["skipped_not_leader"] == 1
    assert calls == []  # 非 leader 零执行
    runs = tasks.runs("job")
    assert [(r.outcome, r.fencing_token) for r in runs] == [("SKIPPED_NOT_LEADER", 1)]
    assert runs[0].detail == "held_by:node-a"


def test_new_leader_after_expiry_takes_over_and_fires() -> None:
    tasks, leases, sched = _stack()
    tasks.register("job", kind="ONE_SHOT", run_at_ms=T0 + 1_000, payload={}, now_ms=T0)
    leases.acquire(RESOURCE, owner_id="node-a", now_ms=T0, lease_ms=5_000)
    # node-a 崩溃,租约过期;node-b 接管(token+1)并兑现。
    summary = _run(sched, owner="node-b", now=T0 + 6_000)
    assert summary["leader"] is True
    assert summary["fencing_token"] == 2
    assert summary["completed"] == 1
    runs = tasks.runs("job")
    assert [(r.outcome, r.fencing_token) for r in runs] == [("COMPLETED", 2)]


def test_set_enabled_toggle() -> None:
    tasks, _, sched = _stack()
    tasks.register("job", kind="ONE_SHOT", run_at_ms=T0, payload={}, now_ms=T0)
    tasks.set_enabled("job", enabled=False, now_ms=T0)
    assert tasks.due_tasks(T0) == ()
    assert _run(sched, now=T0)["fired"] == 0
    tasks.set_enabled("job", enabled=True, now_ms=T0)
    assert [t.task_id for t in tasks.due_tasks(T0)] == ["job"]
    with pytest.raises(SchedulerError):
        tasks.set_enabled("ghost", enabled=True, now_ms=T0)


# ---------------------------------------------------------------- 链与持久化


def test_chain_valid_after_mixed_operations() -> None:
    tasks, _, sched = _stack()
    tasks.register("a", kind="INTERVAL", interval_ms=1_000, payload={}, now_ms=T0)
    tasks.register("b", kind="ONE_SHOT", run_at_ms=T0 + 500, payload={}, now_ms=T0)
    _run(sched, now=T0 + 1_500, executor=lambda env: "ok")
    tasks.set_enabled("a", enabled=False, now_ms=T0 + 2_000)
    tasks.set_enabled("a", enabled=True, now_ms=T0 + 2_500)
    _run(sched, now=T0 + 2_000, executor=lambda env: (_ for _ in ()).throw(ValueError()))
    assert tasks.chain_valid()


def test_chain_tamper_detected_via_raw_sql(tmp_path) -> None:
    path = str(tmp_path / "sched.db")
    tasks = SqliteSchedulerStore(path, create=True)
    tasks.register("job", kind="INTERVAL", interval_ms=1_000, payload={}, now_ms=T0)
    tasks.close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA busy_timeout=5000")
    raw.execute("UPDATE scheduler_tasks SET next_due_ms=0 WHERE task_id='job'")
    raw.commit()
    raw.close()
    with pytest.raises(SchedulerError):
        SqliteSchedulerStore(path)  # fail-closed:篡改库打开即拒


def test_persisted_across_reopen_shared_file(tmp_path) -> None:
    """三 store 共库(不同表):崩溃重启后注册任务还在,补跑继续。"""
    path = str(tmp_path / "kernel.db")

    def open_stack():
        log = SqliteCommandLog(path)
        leases = SqliteLeaderLeaseStore(path)
        tasks = SqliteSchedulerStore(path)
        sched = Scheduler(tasks=tasks, command_bus=CommandBus(log), leases=leases)
        return log, leases, tasks, sched

    log = SqliteCommandLog(path, create=True)
    leases = SqliteLeaderLeaseStore(path, create=True)
    tasks = SqliteSchedulerStore(path, create=True)
    sched = Scheduler(tasks=tasks, command_bus=CommandBus(log), leases=leases)
    tasks.register("job", kind="INTERVAL", interval_ms=1_000, payload={"k": 1}, now_ms=T0)
    calls: list[int] = []
    sched.run_once(
        resource=RESOURCE, owner_id="node-a", actor=ACTOR, now_ms=T0 + 1_500,
        lease_ms=60_000, executor=lambda env: calls.append(1),
    )
    assert tasks.get("job").next_due_ms == T0 + 2_000
    for store in (log, leases, tasks):
        store.close()

    # 重启:全部 create=False 重开,补跑下一槽位。
    log2, leases2, tasks2, sched2 = open_stack()
    assert tasks2.get("job").next_due_ms == T0 + 2_000
    assert tasks2.chain_valid()
    summary = sched2.run_once(
        resource=RESOURCE, owner_id="node-a", actor=ACTOR, now_ms=T0 + 2_000,
        lease_ms=60_000, executor=lambda env: calls.append(2),
    )
    assert summary["completed"] == 1
    assert calls == [1, 2]  # 两个不同槽位各兑现一次
    assert tasks2.get("job").next_due_ms == T0 + 3_000
    for store in (log2, leases2, tasks2):
        store.close()


def test_missing_store_is_fail_closed(tmp_path) -> None:
    with pytest.raises(SchedulerError):
        SqliteSchedulerStore(str(tmp_path / "absent.db"))
    with pytest.raises(SchedulerError):
        SqliteSchedulerStore(":memory:")
