"""CommandBus (727 §2.5/§4.2, K1 切片) — 统一命令执行流。

流程(:meth:`CommandBus.execute`)::

    乐观版本检查(写在日志之前)
      → log.begin(幂等受理)
      → 已完成重放?直接回存证结果,handler 不再运行   ← 「重复命令不产生重复副作用」
      → handler(envelope) → (result, events)
      → publish_once(每个事件,causation_id=command_id)   ← 命令→事件因果链
      → log.complete(落结果)
      → handler 抛异常 → log.fail(落 error_class) 并原样 re-raise

崩溃语义(诚实边界,at-least-once):
- 事件已 publish 但 complete 未落库(崩溃窗口)→ 命令停在 RECEIVED,由
  :meth:`SqliteCommandLog.in_flight` 上面给人工/恢复层。**绝不盲目重试**:
  重跑 handler 可能重复副作用;事件已 publish_once,恢复层重放同一确定性事件
  是幂等的,但「命令是否算完成」必须经恢复层裁决(K 系列后续切片)。
- 同 (command_type, idempotency_key) 且状态 FAILED → CommandStateError:
  失败命令不自动重跑,换 key 显式重提。
- 同 key 且状态 RECEIVED(他处崩溃/并发在飞)→ CommandBusError,同样不自动重跑。
- 结果必须 JSON 可往返(json.dumps/loads):重放路径返回的是 json.loads 后的
  值,tuple 会变成 list 等——这是重放保真度的显式边界。

纯编排:除两个注入的 store(command_log / event_bus)外无任何 I/O;
无墙钟、无随机、无网络、无券商路径。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional, Tuple

from quantos.contracts.events import Event

from services.orchestration.command_envelope import CommandEnvelope
from services.orchestration.idempotency import (
    CommandStateError,
    SqliteCommandLog,
)

if TYPE_CHECKING:  # 仅类型标注;保持本模块 import 轻量
    from services.monitoring.event_bus import DurableEventBus


class CommandBusError(RuntimeError):
    """Command execution cannot proceed safely (in-flight conflict, orphan events, …)."""


class StateVersionConflictError(RuntimeError):
    """The caller's expected_state_version no longer matches the subject's version."""


# handler 返回 result 或 (result, events);events 为 Event 可迭代。
HandlerResult = Any
Handler = Callable[[CommandEnvelope], HandlerResult]


class CommandBus:
    """Deterministic single-node command bus over a durable command log."""

    def __init__(
        self,
        command_log: SqliteCommandLog,
        *,
        event_bus: "Optional[DurableEventBus]" = None,
    ) -> None:
        if not isinstance(command_log, SqliteCommandLog):
            raise TypeError("command_log must be a SqliteCommandLog")
        self._log = command_log
        self._event_bus = event_bus

    def execute(
        self,
        envelope: CommandEnvelope,
        handler: Handler,
        *,
        state_version: Optional[int] = None,
        correlation_id: str = "",
    ) -> Any:
        """执行命令,幂等、可重放、fail-closed。返回 handler 的 result。"""
        if not isinstance(envelope, CommandEnvelope):
            raise TypeError("envelope must be a CommandEnvelope")
        if not callable(handler):
            raise TypeError("handler must be callable")

        # 1) 乐观锁:版本不符在写日志**之前**拒绝(不留任何命令痕迹)。
        if (
            envelope.expected_state_version is not None
            and state_version != envelope.expected_state_version
        ):
            raise StateVersionConflictError(
                f"expected state version {envelope.expected_state_version}, "
                f"got {state_version}"
            )

        # 2) 幂等受理。先读一次既有行:区分「已完成重放 / 失败终态 / 在飞冲突」。
        pre_existing = self._log.find(envelope.command_type, envelope.idempotency_key)
        record = self._log.begin(envelope, recorded_at=envelope.requested_at)

        if record.status == "COMPLETED":
            # 核心不变量:重复命令不产生重复副作用——handler 不再运行。
            return json.loads(record.result_json)  # type: ignore[arg-type]
        if record.status == "FAILED":
            raise CommandStateError(
                f"command already FAILED, resubmit with a new idempotency key: "
                f"{envelope.command_type}/{envelope.idempotency_key}"
            )
        if pre_existing is not None:
            # 状态 RECEIVED 且非本次新建 = 他处崩溃在飞或并发在飞。上面,不重跑。
            raise CommandBusError(
                f"command already in flight (crashed or concurrent): "
                f"{envelope.command_type}/{envelope.idempotency_key}"
            )

        # 3) 运行 handler + 发布事件;任何失败都先记 FAILED 再抛出。
        try:
            result, events = self._split_handler_output(handler(envelope))
            if events:
                if self._event_bus is None:
                    # 事件无处可去 = 部分状态风险;宁可记 FAILED 也不丢因果链。
                    raise CommandBusError(
                        "handler produced events but no event_bus is configured"
                    )
                for event in events:
                    self._event_bus.publish_once(
                        event,
                        causation_id=envelope.command_id,
                        correlation_id=correlation_id or envelope.command_id,
                    )
        except Exception as exc:
            # fail() 自身抛错(库存根损坏)时让新异常携带 __context__ 链出,
            # 绝不静默吞掉 handler 的原始异常。
            self._log.fail(
                envelope.command_id,
                error_class=type(exc).__name__,
                at=envelope.requested_at,
            )
            raise

        # 4) 落结果。若此处崩溃(事件已发布、状态仍 RECEIVED),in_flight() 上面,
        #    恢复交人工/恢复层——见模块 docstring 的 at-least-once 边界。
        self._log.complete(
            envelope.command_id,
            result_json=json.dumps(result, sort_keys=True, default=str),
            at=envelope.requested_at,
        )
        return result

    @staticmethod
    def _split_handler_output(output: HandlerResult) -> Tuple[Any, Tuple[Event, ...]]:
        """handler 可返回 result 或 (result, events);events 逐个校验为 Event。"""
        if (
            isinstance(output, tuple)
            and len(output) == 2
            and isinstance(output[1], Iterable)
            and not isinstance(output[1], (str, bytes, dict))
        ):
            result, raw_events = output
            events = tuple(raw_events)
            for event in events:
                if not isinstance(event, Event):
                    raise CommandBusError(
                        "handler events must be quantos.contracts.events.Event"
                    )
            return result, events
        return output, ()


__all__ = ["CommandBus", "CommandBusError", "StateVersionConflictError"]
