"""Autonomy Kernel 门面(727 §2.5,K4b 收官切片)— 组合根,不是新逻辑。

把 K1–K4a 的组件装进一个对象,共开一个 sqlite 文件,提供两个最常用的转发
(run_once / recovery_report)。所有语义都在各组件里;本模块只做装配。

共库说明:五个 store(command_log / leader_leases / scheduler / workflows /
event_bus)各自 `CREATE TABLE IF NOT EXISTS`(K1–K4a 已验证),依次以同一
create 标志打开同一文件即可——表名互不冲突,WAL + busy_timeout 保证多连接
安全。event_bus 一并共库(任务书规格列四个 store;加 event bus 是显式偏差:
让 executor 产出的事件有处可去,kernel.router 可 replay_from 同一总线)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from quantos.contracts.alpha import Actor

from services.monitoring.event_bus import DurableEventBus
from services.orchestration.command_bus import CommandBus
from services.orchestration.command_envelope import CommandEnvelope
from services.orchestration.event_router import EventRouter
from services.orchestration.idempotency import SqliteCommandLog
from services.orchestration.leader_lease import SqliteLeaderLeaseStore
from services.orchestration.policy_engine import PolicyEngine
from services.orchestration.recovery import KernelRecoveryReport, inspect_kernel
from services.orchestration.scheduler import Scheduler, SqliteSchedulerStore
from services.orchestration.workflow_engine import (
    SqliteWorkflowStore,
    WorkflowEngine,
)

KERNEL_RESOURCE = "autonomy-kernel"


@dataclass(frozen=True)
class AutonomyKernel:
    """Autonomy kernel 组合根:持有全部组件,转发两个常用入口。"""

    command_log: SqliteCommandLog
    leases: SqliteLeaderLeaseStore
    tasks: SqliteSchedulerStore
    workflows: SqliteWorkflowStore
    event_bus: DurableEventBus
    command_bus: CommandBus
    scheduler: Scheduler
    router: EventRouter
    policies: PolicyEngine
    workflow_engine: WorkflowEngine

    def run_once(
        self,
        *,
        owner_id: str,
        actor: Actor,
        now_ms: int,
        lease_ms: int,
        executor: Callable[[CommandEnvelope], Any],
    ) -> dict:
        """门面转发 Scheduler.run_once(resource="autonomy-kernel", ...)。"""
        return self.scheduler.run_once(
            resource=KERNEL_RESOURCE,
            owner_id=owner_id,
            actor=actor,
            now_ms=now_ms,
            lease_ms=lease_ms,
            executor=executor,
        )

    def recovery_report(self, *, now_ms: int) -> KernelRecoveryReport:
        """门面转发 recovery.inspect_kernel(纯读聚合四处悬挂面)。"""
        return inspect_kernel(
            command_log=self.command_log,
            scheduler_store=self.tasks,
            workflow_store=self.workflows,
            lease_store=self.leases,
            now_ms=now_ms,
        )

    def close(self) -> None:
        self.command_log.close()
        self.leases.close()
        self.tasks.close()
        self.workflows.close()
        self.event_bus.close()


def open_kernel(
    path: str,
    *,
    create: bool = False,
    backoff_base_ms: int = 60_000,
    backoff_cap_ms: int = 3_600_000,
) -> AutonomyKernel:
    """共开一个 sqlite 文件装配内核。create=False 且文件不存在 → 第一个
    store 即 fail-closed(与各 store 打开语义一致)。"""
    command_log = SqliteCommandLog(path, create=create)
    try:
        leases = SqliteLeaderLeaseStore(path, create=create)
        tasks = SqliteSchedulerStore(
            path,
            create=create,
            backoff_base_ms=backoff_base_ms,
            backoff_cap_ms=backoff_cap_ms,
        )
        workflows = SqliteWorkflowStore(path, create=create)
        event_bus = DurableEventBus(path, create=create)
    except Exception:
        command_log.close()
        raise
    command_bus = CommandBus(command_log, event_bus=event_bus)
    return AutonomyKernel(
        command_log=command_log,
        leases=leases,
        tasks=tasks,
        workflows=workflows,
        event_bus=event_bus,
        command_bus=command_bus,
        scheduler=Scheduler(tasks=tasks, command_bus=command_bus, leases=leases),
        router=EventRouter(),
        policies=PolicyEngine(),
        workflow_engine=WorkflowEngine(workflows, command_bus),
    )


__all__ = ["KERNEL_RESOURCE", "AutonomyKernel", "open_kernel"]
