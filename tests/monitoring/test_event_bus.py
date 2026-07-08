"""Phase 6 item 1 (P2-5):落盘事件总线。存 Event 规范事件包 + correlation/causation ID(总线列,
不改 Event 契约),按因果组回放 + 进程内同步订阅。fail-closed:非 Event 拒绝、重复 event_id 防重放。
纯确定(时间由 event 自带)。与 DurableAuditLog 互补(审计链防篡改 vs 总线捕因果+可重放)。"""
import pytest

from quantos.contracts.events import Event
from services.monitoring.event_bus import DurableEventBus


def _ev(eid: str, *, etype="research.alpha.promoted", ts=1000, payload=None) -> Event:
    return Event(event_id=eid, event_type=etype, timestamp=ts, source="test",
                 domain="research", payload=payload or {})


def test_publish_then_replay_returns_in_seq_order():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("e1"))
    bus.publish(_ev("e2"))
    out = bus.replay()
    assert [e.event_id for e in out] == ["e1", "e2"]
    assert all(isinstance(e, Event) for e in out)


def test_replay_filters_by_correlation_group():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("a1"), correlation_id="C1")
    bus.publish(_ev("b1"), correlation_id="C2")
    bus.publish(_ev("a2"), correlation_id="C1")
    assert [e.event_id for e in bus.replay(correlation_id="C1")] == ["a1", "a2"]
    assert [e.event_id for e in bus.replay(correlation_id="C2")] == ["b1"]


def test_records_expose_correlation_and_causation_roundtrip():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("e1"), correlation_id="C1", causation_id="")
    bus.publish(_ev("e2"), correlation_id="C1", causation_id="e1")
    recs = bus.records(correlation_id="C1")
    assert [r["event"].event_id for r in recs] == ["e1", "e2"]
    assert recs[1]["causation_id"] == "e1"      # 因果链:e2 由 e1 引发
    assert recs[0]["correlation_id"] == "C1"
    assert recs[0]["seq"] < recs[1]["seq"]


def test_duplicate_event_id_raises_anti_replay():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("dup"))
    with pytest.raises(ValueError):
        bus.publish(_ev("dup"))


def test_non_event_rejected_fail_closed():
    bus = DurableEventBus(":memory:")
    with pytest.raises(TypeError):
        bus.publish({"event_id": "x"})            # dict 不是 Event


def test_subscribe_handler_invoked_synchronously_on_publish():
    bus = DurableEventBus(":memory:")
    seen = []
    bus.subscribe(lambda e: seen.append(e.event_id))
    bus.publish(_ev("e1"))
    bus.publish(_ev("e2"))
    assert seen == ["e1", "e2"]                    # 同步、按发布顺序


def test_subscriber_sees_durable_event_persistence_first():
    # 订阅回调里能读到已落库的事件(persistence-first:先 commit 再 notify)
    bus = DurableEventBus(":memory:")
    counts = []
    bus.subscribe(lambda e: counts.append(bus.count()))
    bus.publish(_ev("e1"))
    assert counts == [1]                           # notify 时该事件已在库


def test_since_seq_filter_returns_only_newer():
    bus = DurableEventBus(":memory:")
    bus.publish(_ev("e1"))
    bus.publish(_ev("e2"))
    all_recs = bus.records()
    first_seq = all_recs[0]["seq"]
    assert [e.event_id for e in bus.replay(since_seq=first_seq)] == ["e2"]


def test_deterministic_same_input_same_records():
    b1, b2 = DurableEventBus(":memory:"), DurableEventBus(":memory:")
    for b in (b1, b2):
        b.publish(_ev("e1", ts=5, payload={"k": 1}), correlation_id="C")
        b.publish(_ev("e2", ts=6, payload={"k": 2}), correlation_id="C", causation_id="e1")
    r1, r2 = b1.records(), b2.records()
    assert [(r["seq"], r["event"].event_id, r["event"].reproducibility_hash, r["causation_id"]) for r in r1] \
        == [(r["seq"], r["event"].event_id, r["event"].reproducibility_hash, r["causation_id"]) for r in r2]
