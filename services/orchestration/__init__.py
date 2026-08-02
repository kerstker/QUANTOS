"""Autonomy kernel (docs/QUANTOS_727.md §2.5) — deterministic command orchestration.

727 §2.5 自治内核:把分散的命令侧幂等(JobQueue invocation_id、LiveLedger 预记账)
收敛为统一的命令总线 + 持久命令日志,并建立「命令 → 事件」因果链。

Honest boundary(诚实边界):
- 单节点编排,**不是**跨主机共识/选主——那是 post-L2(Phase 14)的显式非目标。
- 确定性核心:无墙钟、无随机数;所有时间戳均为调用方传入的整数。
- 无网络、无券商 SDK、不触碰凭证与五个安全旗标;L2_DRY_RUN_ONLY 不变。

K1 = command_envelope + idempotency(持久命令日志)+ command_bus;
K2 = leader_lease(单实例 Leader + fencing token + 任务租约);
K3 = scheduler(持久化确定性调度器:leader 门控 + 幂等补跑 + 错过合并 + DLQ);
K4a = policy_engine(统一策略评估接口)+ event_router(声明式事件路由);
K4b = workflow_engine(持久化多步工作流,崩溃可恢复)+ recovery(上面+显式处置)
     + autonomy_kernel 门面(组合根,五 store 共库)。727 Phase 2 内核完成。
"""
from __future__ import annotations

from services.orchestration.autonomy_kernel import (
    KERNEL_RESOURCE,
    AutonomyKernel,
    open_kernel,
)
from services.orchestration.command_bus import (
    CommandBus,
    CommandBusError,
    StateVersionConflictError,
)
from services.orchestration.command_envelope import (
    CommandEnvelope,
    CommandEnvelopeError,
)
from services.orchestration.event_router import (
    EventRouter,
    RouterConflictError,
    RouterDispatchError,
    RouterError,
)
from services.orchestration.idempotency import (
    CommandConflictError,
    CommandLogError,
    CommandRecord,
    CommandStateError,
    SqliteCommandLog,
)
from services.orchestration.leader_lease import (
    LeaderLease,
    LeaderLeaseBusyError,
    LeaderLeaseError,
    LeaderLeaseFencedError,
    SqliteLeaderLeaseStore,
)
from services.orchestration.policy_engine import (
    VERDICTS,
    PolicyConflictError,
    PolicyDecision,
    PolicyEngine,
    PolicyError,
    PolicyNotFoundError,
)
from services.orchestration.recovery import (
    RESOLUTIONS,
    KernelRecoveryReport,
    RecoveryError,
    inspect_kernel,
    resolve_in_flight,
    revive_task,
)
from services.orchestration.scheduler import (
    ScheduledTask,
    Scheduler,
    SchedulerConflictError,
    SchedulerError,
    SchedulerRun,
    SchedulerStateError,
    SqliteSchedulerStore,
)
from services.orchestration.workflow_engine import (
    SqliteWorkflowStore,
    WorkflowConflictError,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowError,
    WorkflowInstance,
    WorkflowNotFoundError,
    WorkflowStateError,
    WorkflowStep,
    WorkflowStepRun,
)

__all__ = [
    "AutonomyKernel",
    "CommandBus",
    "CommandBusError",
    "CommandConflictError",
    "CommandEnvelope",
    "CommandEnvelopeError",
    "CommandLogError",
    "CommandRecord",
    "CommandStateError",
    "EventRouter",
    "KERNEL_RESOURCE",
    "KernelRecoveryReport",
    "LeaderLease",
    "LeaderLeaseBusyError",
    "LeaderLeaseError",
    "LeaderLeaseFencedError",
    "PolicyConflictError",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyError",
    "PolicyNotFoundError",
    "RESOLUTIONS",
    "RecoveryError",
    "RouterConflictError",
    "RouterDispatchError",
    "RouterError",
    "ScheduledTask",
    "Scheduler",
    "SchedulerConflictError",
    "SchedulerError",
    "SchedulerRun",
    "SchedulerStateError",
    "SqliteCommandLog",
    "SqliteLeaderLeaseStore",
    "SqliteSchedulerStore",
    "SqliteWorkflowStore",
    "StateVersionConflictError",
    "VERDICTS",
    "WorkflowConflictError",
    "WorkflowDefinition",
    "WorkflowEngine",
    "WorkflowError",
    "WorkflowInstance",
    "WorkflowNotFoundError",
    "WorkflowStateError",
    "WorkflowStep",
    "WorkflowStepRun",
    "inspect_kernel",
    "open_kernel",
    "resolve_in_flight",
    "revive_task",
]
