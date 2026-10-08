"""Kernel Recovery(727 §2.5,K4b 切片)— 内核恢复层:上面 + 显式处置。

语义 = 「**上面 + 显式处置**」,绝不自动重试副作用:

- :func:`inspect_kernel` 把四处悬挂面(command log 在飞命令 / scheduler DLQ
  任务 / workflow RUNNING 实例 / 活动租约)聚成一份纯读报告——恢复决策的
  输入,不是恢复动作本身。
- :func:`resolve_in_flight` 是在飞命令的**唯一**处置入口,本切片只支持
  MARK_FAILED。**为什么不自动重跑**:卡在 RECEIVED 的命令其 handler 副作用
  未知(可能已部分生效)——727 §0.4.8「未知状态绝不盲目处置」;标失败是把
  「未知」显式降级为「已知失败」,之后要不要重跑 = 换新 idempotency_key
  显式重提(K1 已支持),那是调用方/人工的决定,不是 recovery 的事。
- :func:`revive_task` 是 DLQ 任务的显式复活(人工驱动),不是自动重试。

纯编排:无 IO(除注入的 store)、无墙钟(now_ms 调用方传入)、无网络。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from services.orchestration.idempotency import CommandRecord, SqliteCommandLog
from services.orchestration.leader_lease import LeaderLease, SqliteLeaderLeaseStore
from services.orchestration.scheduler import ScheduledTask, SqliteSchedulerStore
from services.orchestration.workflow_engine import (
    SqliteWorkflowStore,
    WorkflowInstance,
)

RESOLUTIONS = ("MARK_FAILED",)


class RecoveryError(RuntimeError):
    """A recovery operation was rejected (fail-closed)."""


@dataclass(frozen=True)
class KernelRecoveryReport:
    """四处悬挂面的纯读聚合(recovery 决策输入,非动作)。"""

    in_flight_commands: Tuple[CommandRecord, ...]
    dead_tasks: Tuple[ScheduledTask, ...]
    running_workflows: Tuple[WorkflowInstance, ...]
    active_leases: Tuple[LeaderLease, ...]
    generated_at_ms: int


def inspect_kernel(
    *,
    command_log: SqliteCommandLog,
    scheduler_store: SqliteSchedulerStore,
    workflow_store: SqliteWorkflowStore,
    lease_store: SqliteLeaderLeaseStore,
    now_ms: int,
) -> KernelRecoveryReport:
    """纯读聚合四处悬挂面。各 store 自带链校验(任一被篡改这里就会抛)。"""
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer")
    return KernelRecoveryReport(
        in_flight_commands=command_log.in_flight(),
        dead_tasks=scheduler_store.dead_tasks(),
        running_workflows=workflow_store.instances(status="RUNNING"),
        active_leases=lease_store.active_leases(now_ms=now_ms),
        generated_at_ms=now_ms,
    )


def resolve_in_flight(
    command_log: SqliteCommandLog,
    command_id: str,
    *,
    resolution: str,
    now_ms: int,
) -> CommandRecord:
    """在飞命令的唯一处置入口。resolution="MARK_FAILED":经
    command_log.fail(error_class="RECOVERY_MARKED") 把「未知」显式降级为
    「已知失败」;非 RECEIVED → CommandStateError(command_log 抛出)。"""
    if not isinstance(command_log, SqliteCommandLog):
        raise TypeError("command_log must be a SqliteCommandLog")
    if resolution not in RESOLUTIONS:
        raise RecoveryError(
            f"unsupported resolution {resolution!r}; supported: {RESOLUTIONS}"
        )
    return command_log.fail(
        command_id, error_class="RECOVERY_MARKED", at=now_ms
    )


def revive_task(
    scheduler_store: SqliteSchedulerStore, task_id: str, *, now_ms: int
) -> ScheduledTask:
    """DLQ 任务显式复活(人工驱动)。转发 store.revive;非 dead →
    SchedulerStateError。复活语义见 SqliteSchedulerStore.revive docstring。"""
    if not isinstance(scheduler_store, SqliteSchedulerStore):
        raise TypeError("scheduler_store must be a SqliteSchedulerStore")
    return scheduler_store.revive(task_id, now_ms=now_ms)


__all__ = [
    "RESOLUTIONS",
    "KernelRecoveryReport",
    "RecoveryError",
    "inspect_kernel",
    "resolve_in_flight",
    "revive_task",
]
