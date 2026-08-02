"""K4b Autonomy Kernel 门面测试:共库装配 / leader 门 / 恢复报告 / 重开。"""
from __future__ import annotations

import sqlite3

import pytest

from quantos.contracts.alpha import Actor, ActorKind
from services.orchestration import (
    CommandLogError,
    WorkflowDefinition,
    WorkflowStep,
    open_kernel,
)

T0 = 1_700_000_000_000
ACTOR = Actor(actor_id="kernel-node", kind=ActorKind.SYSTEM)


def test_open_kernel_shares_one_file_for_all_stores(tmp_path) -> None:
    path = str(tmp_path / "kernel.db")
    kernel = open_kernel(path, create=True)
    # 五个 store 的表全在同一个文件里。
    kernel.tasks.register(
        "job", kind="ONE_SHOT", run_at_ms=T0, payload={}, now_ms=T0
    )
    kernel.workflow_engine.register_workflow(
        WorkflowDefinition(
            workflow_type="wf", version="1",
            steps=(WorkflowStep(step_id="s1", command_type="c"),),
        ),
        now_ms=T0,
    )
    kernel.leases.acquire("res", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    kernel.close()

    raw = sqlite3.connect(path)
    tables = {
        row[0] for row in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    raw.close()
    assert {
        "command_log",
        "leader_leases",
        "scheduler_tasks",
        "scheduler_runs",
        "workflow_definitions",
        "workflow_instances",
        "workflow_step_runs",
        "event_log",
    } <= tables


def test_run_once_goes_through_leader_gate(tmp_path) -> None:
    path = str(tmp_path / "kernel.db")
    kernel = open_kernel(path, create=True)
    kernel.tasks.register("a", kind="ONE_SHOT", run_at_ms=T0, payload={}, now_ms=T0)
    kernel.tasks.register("b", kind="ONE_SHOT", run_at_ms=T0, payload={}, now_ms=T0)

    calls: list[str] = []
    summary = kernel.run_once(
        owner_id="node-a", actor=ACTOR, now_ms=T0, lease_ms=60_000,
        executor=lambda env: calls.append(env.command_type),
    )
    assert summary["leader"] is True and summary["completed"] == 2
    assert calls == ["a", "b"]

    # 租约未到期:另一个 owner 被 leader 门挡下,零执行。
    kernel.tasks.register("c", kind="ONE_SHOT", run_at_ms=T0, payload={}, now_ms=T0)
    denied = kernel.run_once(
        owner_id="node-b", actor=ACTOR, now_ms=T0 + 1, lease_ms=60_000,
        executor=lambda env: calls.append(env.command_type),
    )
    assert denied["leader"] is False and denied["skipped_not_leader"] == 1
    assert calls == ["a", "b"]
    kernel.close()


def test_recovery_report_aggregates(tmp_path) -> None:
    path = str(tmp_path / "kernel.db")
    kernel = open_kernel(path, create=True)
    kernel.tasks.register("dead", kind="INTERVAL", interval_ms=1_000, payload={},
                          max_attempts=1, now_ms=T0)
    kernel.tasks.record_run("dead", slot_ms=T0 + 1_000, fencing_token=1,
                            outcome="FAILED", detail="X", now_ms=T0 + 1_000)
    kernel.leases.acquire("res", owner_id="node-a", now_ms=T0, lease_ms=10_000)
    report = kernel.recovery_report(now_ms=T0 + 2_000)
    assert [t.task_id for t in report.dead_tasks] == ["dead"]
    assert [lease.resource for lease in report.active_leases] == ["res"]
    assert report.in_flight_commands == ()
    assert report.running_workflows == ()
    kernel.close()


def test_close_and_reopen_create_false(tmp_path) -> None:
    path = str(tmp_path / "kernel.db")
    kernel = open_kernel(path, create=True)
    kernel.tasks.register(
        "job", kind="INTERVAL", interval_ms=1_000, payload={}, now_ms=T0
    )
    kernel.run_once(owner_id="node-a", actor=ACTOR, now_ms=T0 + 1_000,
                    lease_ms=60_000, executor=lambda env: "ok")
    kernel.close()

    reopened = open_kernel(path)  # create=False,文件必须存在
    assert reopened.tasks.get("job").next_due_ms == T0 + 2_000
    assert reopened.tasks.chain_valid()
    assert reopened.command_log.chain_valid()
    assert reopened.leases.chain_valid()
    assert reopened.workflows.chain_valid()
    reopened.close()


def test_missing_file_is_fail_closed(tmp_path) -> None:
    with pytest.raises(CommandLogError):
        open_kernel(str(tmp_path / "absent.db"))
