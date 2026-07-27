"""Autonomy kernel (docs/QUANTOS_727.md §2.5) — deterministic command orchestration.

727 §2.5 自治内核:把分散的命令侧幂等(JobQueue invocation_id、LiveLedger 预记账)
收敛为统一的命令总线 + 持久命令日志,并建立「命令 → 事件」因果链。

Honest boundary(诚实边界):
- 单节点编排,**不是**跨主机共识/选主——那是 post-L2(Phase 14)的显式非目标。
- 确定性核心:无墙钟、无随机数;所有时间戳均为调用方传入的整数。
- 无网络、无券商 SDK、不触碰凭证与五个安全旗标;L2_DRY_RUN_ONLY 不变。

K1 = command_envelope + idempotency(持久命令日志)+ command_bus;
K2 = leader_lease(单实例 Leader + fencing token + 任务租约);
K3 = scheduler(持久化确定性调度器:leader 门控 + 幂等补跑 + 错过合并 + DLQ)。
workflow_engine / recovery 等见设计文档 K 系列切片计划,本包尚未提供。
"""
from __future__ import annotations

from services.orchestration.command_bus import (
    CommandBus,
    CommandBusError,
    StateVersionConflictError,
)
from services.orchestration.command_envelope import (
    CommandEnvelope,
    CommandEnvelopeError,
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
from services.orchestration.scheduler import (
    ScheduledTask,
    Scheduler,
    SchedulerConflictError,
    SchedulerError,
    SchedulerRun,
    SqliteSchedulerStore,
)

__all__ = [
    "CommandBus",
    "CommandBusError",
    "CommandConflictError",
    "CommandEnvelope",
    "CommandEnvelopeError",
    "CommandLogError",
    "CommandRecord",
    "CommandStateError",
    "LeaderLease",
    "LeaderLeaseBusyError",
    "LeaderLeaseError",
    "LeaderLeaseFencedError",
    "ScheduledTask",
    "Scheduler",
    "SchedulerConflictError",
    "SchedulerError",
    "SchedulerRun",
    "SqliteCommandLog",
    "SqliteLeaderLeaseStore",
    "SqliteSchedulerStore",
    "StateVersionConflictError",
]
