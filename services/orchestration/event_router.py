"""Event Router(727 §2.5,K4a 切片)— 类型→处理器的声明式路由表。

DurableEventBus 已有 append/replay/subscribe/消费组;本模块补的是**声明式
路由**:哪个 event_type 由哪些 handler 处理,注册表驱动、确定性序,而不是
散在各处的 if/elif。

at-least-once 边界(与事件侧一致,诚实声明):

- handler 抛异常 → :class:`RouterDispatchError`(携带第一个异常与**已调用
  计数**),**已调用的 handler 不回滚**;dispatch/replay 立即中止,绝不跳过
  失败假装成功。
- 重放(:meth:`replay_from`)因此可能对同一事件重复调用 handler——**幂等是
  handler 自己的责任**(惯用法:按 event_id 去重,见测试示例)。路由层不替
  handler 去重,正如 bus 不替 consumer 去重。
- 无匹配 handler = 返回 0,**不是错误**——路由是声明式过滤,未订阅的类型
  安静流过。

handler 收到的 event_view 就是 bus ``records()`` 返回的 record dict
(``{seq, event, correlation_id, causation_id}``),原样传递、不重新包装;
直接 :meth:`dispatch` 单个 Event 时,seq/correlation/causation 无从得知,
以 None/"" 填入并在 docstring 声明——绝不伪造。

纯编排:无 IO(除注入的 bus)、无墙钟、无随机、无网络。
"""
from __future__ import annotations

from typing import Callable, Optional

from quantos.contracts.events import Event

from services.monitoring.event_bus import DurableEventBus


class RouterError(RuntimeError):
    """Base class for event-router errors."""


class RouterConflictError(RouterError):
    """The same event_type was routed to a different handler."""


class RouterDispatchError(RouterError):
    """A handler raised mid-dispatch; already-called handlers are NOT rolled back."""

    def __init__(
        self, *, event_type: str, handlers_called: int, original: Exception
    ) -> None:
        self.event_type = event_type
        self.handlers_called = handlers_called  # 含抛异常的那个
        self.original = original
        super().__init__(
            f"dispatch failed for {event_type} after {handlers_called} "
            f"handler call(s): {type(original).__name__}: {original}"
        )


# handler 接收 bus records() 的 record dict(原样),返回 None。
Handler = Callable[[dict], None]


class EventRouter:
    """Declarative event_type → handlers routing table (deterministic order)."""

    def __init__(self) -> None:
        self._routes: dict[str, list[Handler]] = {}

    def route(self, event_type: str, handler: Handler) -> None:
        """注册路由。同 event_type 可挂多个 handler(注册序即分发序);重复注册
        **同一个** handler(id 相同)幂等;同名(__name__ 相同)但身份不同的
        handler → RouterConflictError(防「同名函数被静默替换」的代码漂移;
        真正的叠加用不同名 handler,显式可见)。

        与任务书规格的显式偏差:规格写「不同 handler → RouterConflictError」,
        但其测试清单同时要求「多 handler 注册序」且 RouterDispatchError 的
        「已调用计数」只在多 handler 下有意义——故采用多 handler 设计,冲突
        收窄为同名不同身份。"""
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must be a non-empty string")
        if not callable(handler):
            raise TypeError("handler must be callable")
        handlers = self._routes.setdefault(event_type, [])
        for existing in handlers:
            if existing is handler:
                return  # 幂等
        name = getattr(handler, "__name__", "")
        if name and any(
            getattr(existing, "__name__", None) == name for existing in handlers
        ):
            raise RouterConflictError(
                f"event_type {event_type} already has a handler named {name!r}; "
                "a same-named different handler is a silent replacement"
            )
        handlers.append(handler)

    def handlers_for(self, event_type: str) -> tuple[Handler, ...]:
        """该类型的 handler,注册序(确定性)。"""
        return tuple(self._routes.get(event_type, ()))

    def dispatch(self, event: Event) -> int:
        """向匹配 handler 分发单个事件,返回调用数(无匹配 = 0,非错误)。

        直接 dispatch 时行级 seq/correlation/causation 无从得知 → None/""/
        "" 填入(诚实缺省);要完整因果视图请走 replay_from。
        """
        if not isinstance(event, Event):
            raise TypeError("event must be a quantos.contracts.events.Event")
        record = {
            "seq": None,
            "event": event,
            "correlation_id": "",
            "causation_id": "",
        }
        return self._dispatch_record(record)

    def _dispatch_record(self, record: dict) -> int:
        event = record["event"]
        handlers = self._routes.get(event.event_type, ())
        called = 0
        for handler in handlers:
            called += 1
            try:
                handler(record)
            except Exception as exc:
                raise RouterDispatchError(
                    event_type=event.event_type,
                    handlers_called=called,
                    original=exc,
                ) from exc
        return called

    def replay_from(
        self, bus: DurableEventBus, *, since_seq: Optional[int] = None
    ) -> dict:
        """经 bus.records()(bus 自带先验链再返回)逐条 dispatch,确定性重放。

        handler 中途抛异常 → RouterDispatchError 原样中止(已调用不回滚);
        恢复层用返回的 last_seq 对账后重跑——at-least-once,见模块 docstring。
        """
        if not isinstance(bus, DurableEventBus):
            raise TypeError("bus must be a DurableEventBus")
        records = bus.records(since_seq=since_seq)
        dispatched = 0
        last_seq = since_seq if since_seq is not None else 0
        for record in records:
            dispatched += self._dispatch_record(record)
            last_seq = record["seq"]
        return {
            "dispatched": dispatched,
            "scanned": len(records),
            "last_seq": last_seq,
        }


__all__ = [
    "EventRouter",
    "RouterConflictError",
    "RouterDispatchError",
    "RouterError",
]
