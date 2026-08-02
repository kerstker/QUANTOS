"""K4a Event Router 测试:注册/分发/异常聚合/重放(真 DurableEventBus)。"""
from __future__ import annotations

import pytest

from quantos.contracts.events import Event
from services.monitoring.event_bus import DurableEventBus
from services.orchestration import (
    EventRouter,
    RouterConflictError,
    RouterDispatchError,
)

T0_US = 1_700_000_000_000_000  # 事件时间戳用 µs(schema 现行约定)


def _event(event_id: str, event_type: str, n: int = 0) -> Event:
    return Event(
        event_id=event_id,
        event_type=event_type,
        timestamp=T0_US + n * 1_000,
        source="tests.orchestration",
        domain="research",
        payload={"n": n},
    )


# ---------------------------------------------------------------- 注册与分发


def test_route_multiple_handlers_registration_order() -> None:
    router = EventRouter()
    calls: list[str] = []

    def first(record):
        calls.append("first")

    def second(record):
        calls.append("second")

    router.route("research.x", first)
    router.route("research.x", second)
    assert router.handlers_for("research.x") == (first, second)  # 注册序
    assert router.dispatch(_event("e1", "research.x")) == 2
    assert calls == ["first", "second"]  # 分发序 = 注册序


def test_same_handler_reregister_is_idempotent() -> None:
    router = EventRouter()

    def handler(record):
        pass

    router.route("research.x", handler)
    router.route("research.x", handler)  # 同 id 幂等,不叠加
    assert router.handlers_for("research.x") == (handler,)
    assert router.dispatch(_event("e1", "research.x")) == 1


def test_same_name_different_identity_conflicts() -> None:
    """同名不同身份的 handler = 静默替换,拒绝。"""
    router = EventRouter()

    def handler(record):
        pass

    router.route("research.x", handler)

    def handler(record):  # noqa: F811 — 同名重定义(代码漂移场景)
        raise AssertionError("shadow")

    with pytest.raises(RouterConflictError):
        router.route("research.x", handler)


def test_no_matching_route_returns_zero() -> None:
    router = EventRouter()
    router.route("research.x", lambda record: None)
    assert router.dispatch(_event("e1", "unrelated.type")) == 0  # 非错误,安静流过


def test_dispatch_record_view_fields() -> None:
    router = EventRouter()
    seen: list[dict] = []
    router.route("research.x", seen.append)
    event = _event("e1", "research.x")
    router.dispatch(event)
    assert seen[0]["event"] is event
    # 直接 dispatch:行级因果无从得知,诚实缺省(不伪造)。
    assert seen[0]["seq"] is None
    assert seen[0]["correlation_id"] == ""


def test_handler_exception_aggregates_without_rollback() -> None:
    router = EventRouter()
    calls: list[str] = []

    def ok(record):
        calls.append("ok")

    def boom(record):
        calls.append("boom")
        raise KeyError("handler failed")

    router.route("research.x", ok)
    router.route("research.x", boom)
    with pytest.raises(RouterDispatchError) as exc_info:
        router.dispatch(_event("e1", "research.x"))
    assert exc_info.value.handlers_called == 2  # 含抛异常者
    assert isinstance(exc_info.value.original, KeyError)
    assert calls == ["ok", "boom"]  # 已调用的不回滚


# ---------------------------------------------------------------- 重放


def _bus_with_events() -> DurableEventBus:
    bus = DurableEventBus(":memory:", create=True)
    bus.publish(_event("e1", "research.x", 1))
    bus.publish(_event("e2", "research.y", 2))
    bus.publish(_event("e3", "research.x", 3))
    return bus


def test_replay_from_counts_and_record_view() -> None:
    router = EventRouter()
    seen: list[dict] = []
    router.route("research.x", seen.append)
    bus = _bus_with_events()
    result = router.replay_from(bus)
    assert result == {"dispatched": 2, "scanned": 3, "last_seq": 3}
    # replay 路径带完整行级因果视图(bus records 原样)。
    assert [r["event"].event_id for r in seen] == ["e1", "e3"]
    assert seen[0]["seq"] == 1
    assert "correlation_id" in seen[0] and "causation_id" in seen[0]


def test_replay_since_seq_scans_only_new_events() -> None:
    router = EventRouter()
    seen: list[str] = []
    router.route("research.x", lambda r: seen.append(r["event"].event_id))
    bus = _bus_with_events()
    first = router.replay_from(bus)
    assert first["scanned"] == 3
    bus.publish(_event("e4", "research.x", 4))
    second = router.replay_from(bus, since_seq=first["last_seq"])
    assert second["scanned"] == 1  # 增量只扫新事件
    assert second["last_seq"] == 4
    assert seen == ["e1", "e3", "e4"]


def test_idempotent_handler_survives_replay() -> None:
    """at-least-once 示例:重复重放会重复调用,幂等是 handler 的责任(按 event_id 去重)。"""
    router = EventRouter()
    applied: set[str] = set()
    handler_calls: list[str] = []

    def idempotent_handler(record):
        event_id = record["event"].event_id
        handler_calls.append(event_id)  # 调用计数暴露 at-least-once
        applied.add(event_id)           # 副作用幂等

    router.route("research.x", idempotent_handler)
    bus = _bus_with_events()
    router.replay_from(bus)
    router.replay_from(bus)  # 全量重放第二次
    assert applied == {"e1", "e3"}                      # 副作用不重复
    assert handler_calls == ["e1", "e3", "e1", "e3"]    # 调用确实发生了两次
