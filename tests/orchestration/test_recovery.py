"""K4b Recovery 测试:四面聚合报告 + 显式处置(MARK_FAILED / revive)。"""
from __future__ import annotations

import json

import pytest

from quantos.contracts.alpha import Actor, ActorKind
from services.orchestration import (
    CommandBus,
    CommandEnvelope,
    CommandStateError,
    SchedulerStateError,
    SqliteCommandLog,
    SqliteLeaderLeaseStore,
    SqliteSchedulerStore,
    SqliteWorkflowStore,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowStep,
)
from services.orchestration.recovery import (
    RecoveryError,
    inspect_kernel,
    resolve_in_flight,
    revive_task,
)

T0 = 1_700_000_000_000
ACTOR = Actor(actor_id="recovery-ops", kind=ActorKind.HUMAN)


def _stores():
    log = SqliteCommandLog(":memory:", create=True)
    leases = SqliteLeaderLeaseStore(":memory:", create=True)
    tasks = SqliteSchedulerStore(
        ":memory:", create=True, backoff_base_ms=100, backoff_cap_ms=1_000
    )
    workflows = SqliteWorkflowStore(":memory:", create=True)
    return log, leases, tasks, workflows


def _report(log, leases, tasks, workflows, now=T0):
    return inspect_kernel(
        command_log=log,
        scheduler_store=tasks,
        workflow_store=workflows,
        lease_store=leases,
        now_ms=now,
    )


def test_inspect_aggregates_all_four_surfaces() -> None:
    log, leases, tasks, workflows = _stores()
    # 悬挂面 1:在飞命令。
    env = CommandEnvelope.create(
        command_id="c1", command_type="t", requested_at=T0, actor=ACTOR,
        subject="s", idempotency_key="k1", payload={},
    )
    log.begin(env, recorded_at=T0)
    # 悬挂面 2:DLQ 任务(max_attempts=1 → 一次失败即 dead)。
    tasks.register("dead-job", kind="INTERVAL", interval_ms=1_000, payload={},
                   max_attempts=1, now_ms=T0)
    tasks.record_run("dead-job", slot_ms=T0 + 1_000, fencing_token=1,
                     outcome="FAILED", detail="ValueError", now_ms=T0 + 1_000)
    # 悬挂面 3:RUNNING 工作流实例。
    engine = WorkflowEngine(workflows, CommandBus(log))
    engine.register_workflow(
        WorkflowDefinition(
            workflow_type="wf", version="1",
            steps=(WorkflowStep(step_id="s1", command_type="c"),),
        ),
        now_ms=T0,
    )
    engine.start("wf", instance_id="wi", context={}, actor=ACTOR, now_ms=T0)
    workflows.mark_running("wi", now_ms=T0)
    # 悬挂面 4:活动租约。
    leases.acquire("res", owner_id="node-a", now_ms=T0, lease_ms=10_000)

    report = _report(log, leases, tasks, workflows)
    assert [r.command_id for r in report.in_flight_commands] == ["c1"]
    assert [t.task_id for t in report.dead_tasks] == ["dead-job"]
    assert [w.instance_id for w in report.running_workflows] == ["wi"]
    assert [lease.resource for lease in report.active_leases] == ["res"]
    assert report.generated_at_ms == T0


def test_inspect_empty_kernel_is_deterministic() -> None:
    log, leases, tasks, workflows = _stores()
    first = _report(log, leases, tasks, workflows)
    second = _report(log, leases, tasks, workflows)
    assert first == second
    assert first.in_flight_commands == ()
    assert first.dead_tasks == ()
    assert first.running_workflows == ()
    assert first.active_leases == ()


def test_resolve_in_flight_marks_failed() -> None:
    log, _, _, _ = _stores()
    env = CommandEnvelope.create(
        command_id="c1", command_type="t", requested_at=T0, actor=ACTOR,
        subject="s", idempotency_key="k1", payload={},
    )
    log.begin(env, recorded_at=T0)
    resolved = resolve_in_flight(log, "c1", resolution="MARK_FAILED", now_ms=T0 + 1)
    assert resolved.status == "FAILED"
    assert json.loads(resolved.result_json) == {"error_class": "RECOVERY_MARKED"}
    assert log.in_flight() == ()  # 处置后离开悬挂面


def test_resolve_rejects_non_received_and_bad_resolution() -> None:
    log, _, _, _ = _stores()
    env = CommandEnvelope.create(
        command_id="c1", command_type="t", requested_at=T0, actor=ACTOR,
        subject="s", idempotency_key="k1", payload={},
    )
    log.begin(env, recorded_at=T0)
    log.complete("c1", result_json="{}", at=T0 + 1)
    with pytest.raises(CommandStateError):
        resolve_in_flight(log, "c1", resolution="MARK_FAILED", now_ms=T0 + 2)
    with pytest.raises(RecoveryError):
        resolve_in_flight(log, "c1", resolution="AUTO_RETRY", now_ms=T0 + 2)


def test_revive_task_interval_and_one_shot() -> None:
    _, _, tasks, _ = _stores()
    tasks.register("iv", kind="INTERVAL", interval_ms=1_000, payload={},
                   max_attempts=1, now_ms=T0)
    tasks.record_run("iv", slot_ms=T0 + 1_000, fencing_token=1, outcome="FAILED",
                     detail="X", now_ms=T0 + 1_000)
    assert tasks.due_tasks(T0 + 99_999) == ()  # dead 不到期
    revived = revive_task(tasks, "iv", now_ms=T0 + 5_000)
    assert revived.dead is False and revived.attempts == 0
    assert revived.next_due_ms == T0 + 6_000  # now + interval
    assert [t.task_id for t in tasks.due_tasks(T0 + 6_000)] == ["iv"]

    tasks.register("os", kind="ONE_SHOT", run_at_ms=T0, payload={},
                   max_attempts=1, now_ms=T0)
    tasks.record_run("os", slot_ms=T0, fencing_token=1, outcome="FAILED",
                     detail="X", now_ms=T0)
    revived_os = revive_task(tasks, "os", now_ms=T0 + 7_000)
    assert revived_os.enabled is True and revived_os.next_due_ms == T0 + 7_000
    assert tasks.chain_valid()


def test_revive_rejects_live_task() -> None:
    _, _, tasks, _ = _stores()
    tasks.register("live-job", kind="INTERVAL", interval_ms=1_000, payload={}, now_ms=T0)
    with pytest.raises(SchedulerStateError):
        revive_task(tasks, "live-job", now_ms=T0)
