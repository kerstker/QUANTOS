"""live/execution/shadow_store.py(SHADOW 观测 append-only 账本 + 第七 outbox,
727 §12.2 Phase 7 S2)单测。全部 tmp_path 本地 sqlite——零网络/零墙钟(int ms 注入)。
"""
import json
import sqlite3

import pytest

from live.contracts import BookLevel, OrderBookSnapshot
from live.execution.shadow import ShadowLeg, observe_leg
from live.execution.shadow_store import (ShadowStoreError, SqliteShadowStore,
                                         observation_payload)
from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS, EventSchemaError
from quantos.contracts.events import (EVENT_SHADOW_OBSERVED,
                                      EVENT_TIMESTAMP_UNIT_MICROSECONDS)

_NOW = 1_700_000_000_000


def _obs(instrument_id="BTC", *, decision_price=100.0, quantity=2.0, latency_ms=42):
    """经真 observe_leg 造一条可成交观测(不手拼 ShadowObservation,防测试与核心漂移)。"""
    book = OrderBookSnapshot(
        instrument_id=instrument_id,
        bids=(BookLevel(100.0, 1.0), BookLevel(99.5, 2.0)),
        asks=(BookLevel(100.5, 1.0), BookLevel(101.0, 2.0)),
        as_of_ms=_NOW - 500, source="test")
    leg = ShadowLeg(instrument_id=instrument_id, side="BUY", quantity=quantity,
                    decision_price=decision_price)
    return observe_leg(leg, book=book, latency_ms=latency_ms, now_ms=_NOW)


def _store(tmp_path, name="shadow.db", **kw):
    return SqliteShadowStore(str(tmp_path / name), **kw)


# --- 打开语义 ---

def test_create_false_on_missing_store_is_refused(tmp_path):
    with pytest.raises(ShadowStoreError):
        SqliteShadowStore(str(tmp_path / "missing.db"), create=False)


def test_create_false_on_memory_is_refused():
    with pytest.raises(ShadowStoreError):
        SqliteShadowStore(":memory:", create=False)


# --- append / 幂等 / 链 ---

def test_append_returns_content_derived_id_and_roundtrips_payload(tmp_path):
    store = _store(tmp_path, create=True)
    obs = _obs()
    observation_id, inserted = store.append(obs, run_label="batch-1", recorded_at_ms=1_100)
    assert inserted is True
    assert observation_id.startswith("shd_") and len(observation_id) == 20
    assert store.count() == 1
    rows = store.records()
    assert rows[0]["observation_id"] == observation_id
    assert rows[0]["run_label"] == "batch-1"
    assert rows[0]["recorded_at_ms"] == 1_100
    assert rows[0]["provenance"] == "SHADOW"
    assert rows[0]["payload"] == observation_payload(obs)     # 全字段往返(walk 含内)
    assert store.chain_valid()
    assert store.event_outbox_chain_valid()


def test_append_is_idempotent_on_exact_replay_even_across_run_labels(tmp_path):
    store = _store(tmp_path, create=True)
    obs = _obs()
    first, inserted1 = store.append(obs, run_label="batch-1", recorded_at_ms=1_100)
    # run_label 是批次元数据、不进身份:同一观测换标签重放仍是同一观测(裁定见模块 docstring)
    second, inserted2 = store.append(obs, run_label="batch-2", recorded_at_ms=1_200)
    assert inserted1 is True and inserted2 is False
    assert second == first
    assert store.count() == 1
    assert len(store.pending_event_outbox()) == 1              # 不产生重复事件


def test_append_conflict_same_id_different_payload_fails_closed(tmp_path):
    store = _store(tmp_path, create=True)
    obs = _obs()
    observation_id, _ = store.append(obs, run_label="b", recorded_at_ms=1)
    # 按构造不可达(id 由 payload 派生)——纵深防御分支,stub 出「同 id 异 payload」验证
    store._find_payload_hash = lambda _id: "0" * 64            # noqa: SLF001
    with pytest.raises(ShadowStoreError, match="different content"):
        store.append(obs, run_label="b", recorded_at_ms=2)


def test_append_validates_run_label_and_recorded_at_ms(tmp_path):
    store = _store(tmp_path, create=True)
    with pytest.raises(ShadowStoreError):
        store.append(_obs(), run_label="", recorded_at_ms=1)
    with pytest.raises(ShadowStoreError):
        store.append(_obs(), run_label="b", recorded_at_ms=-1)
    assert store.count() == 0


def test_two_appends_link_the_hash_chain(tmp_path):
    store = _store(tmp_path, create=True)
    store.append(_obs("BTC"), run_label="b", recorded_at_ms=1)
    store.append(_obs("ETH"), run_label="b", recorded_at_ms=2)
    rows = store.records()
    assert rows[1]["prev_hash"] == rows[0]["record_hash"]
    assert rows[0]["prev_hash"] == ""
    assert store.chain_valid() and store.event_outbox_chain_valid()


# --- 篡改检测 ---

def _tamper(path, sql):
    external = sqlite3.connect(path)
    for trigger in ("shadow_observations_no_update", "shadow_event_outbox_no_update"):
        external.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    external.execute(sql[0], sql[1])
    external.commit()
    external.close()


def test_tampered_observation_payload_blocks_reopen_and_append(tmp_path):
    path = str(tmp_path / "shadow.db")
    store = SqliteShadowStore(path, create=True)
    store.append(_obs(), run_label="b", recorded_at_ms=1)
    store.close()
    _tamper(path, ("UPDATE shadow_observations SET payload_json=? WHERE obs_seq=1",
                   (json.dumps({"forged": True}),)))
    with pytest.raises(ShadowStoreError):
        SqliteShadowStore(path, create=False)                  # 打开即全链校验,篡改即拒
    store2 = SqliteShadowStore(path, create=True)              # 打开不验,append 前全验
    assert not store2.chain_valid()
    assert not store2.event_outbox_chain_valid()
    with pytest.raises(ShadowStoreError):
        store2.append(_obs("ETH"), run_label="b", recorded_at_ms=2)


def test_tampered_outbox_event_is_detected(tmp_path):
    path = str(tmp_path / "shadow.db")
    store = SqliteShadowStore(path, create=True)
    store.append(_obs(), run_label="b", recorded_at_ms=1)
    store.close()
    _tamper(path, ("UPDATE shadow_event_outbox SET event_json=? WHERE outbox_seq=1",
                   (json.dumps({"event_id": "forged"}),)))
    store2 = SqliteShadowStore(path, create=True)
    assert store2.chain_valid()                                # 观测链本身未动
    assert not store2.event_outbox_chain_valid()               # outbox 链抓到篡改
    with pytest.raises(ShadowStoreError):
        store2.pending_event_outbox()


def test_provenance_check_constraint_rejects_non_shadow(tmp_path):
    path = str(tmp_path / "shadow.db")
    store = SqliteShadowStore(path, create=True)
    store.close()
    external = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError):
        external.execute(
            "INSERT INTO shadow_observations"
            " (observation_id,run_label,payload_json,payload_sha256,provenance,"
            " as_of_ms,recorded_at_ms,prev_hash,record_hash)"
            " VALUES ('x','b','{}','h','PAPER',NULL,1,'','r')")
    external.close()


# --- 只读查询 ---

def test_records_filter_run_label_and_since_seq(tmp_path):
    store = _store(tmp_path, create=True)
    store.append(_obs("BTC"), run_label="week-1", recorded_at_ms=1)
    store.append(_obs("ETH"), run_label="week-2", recorded_at_ms=2)
    store.append(_obs("SOL"), run_label="week-2", recorded_at_ms=3)
    assert [r["run_label"] for r in store.records(run_label="week-2")] == ["week-2"] * 2
    assert [r["payload"]["instrument_id"] for r in store.records(since_seq=1)] == ["ETH", "SOL"]
    assert store.records(run_label="week-2", since_seq=2)[0]["payload"]["instrument_id"] == "SOL"


# --- outbox 同事务 + 事件形状 ---

def test_observation_and_canonical_event_share_one_transaction(tmp_path):
    store = _store(tmp_path, create=True)
    obs = _obs()
    observation_id, _ = store.append(obs, run_label="week-1", recorded_at_ms=1_100)
    pending = store.pending_event_outbox()
    assert len(pending) == 1
    message = pending[0]
    event = message["event"]
    assert event.event_type == EVENT_SHADOW_OBSERVED
    assert event.event_version == "1.0.0"
    assert event.timestamp == 1_100_000                        # µs(新类型现行约定)
    assert CANONICAL_EVENT_SCHEMAS.timestamp_unit(event) == EVENT_TIMESTAMP_UNIT_MICROSECONDS
    assert event.domain == "execution"
    assert event.source == "live.execution.shadow_store"
    assert event.criticality == "C2"
    # 脱敏摘要:含结论字段,绝不含盘口簿/walk 明细
    assert event.payload == {
        "observation_id": observation_id,
        "run_label": "week-1",
        "instrument_id": "BTC",
        "side": "BUY",
        "quantity": 2.0,
        "decision_price": 100.0,
        "executable_vwap": 100.75,
        "slippage_bps_vs_decision": 75.0,
        "book_age_ms": 500,
        "as_of_ms": _NOW - 500,
        "provenance": "SHADOW",
    }
    assert "walk" not in event.payload and "book_mid" not in event.payload
    assert message["correlation_id"] == "week-1"
    assert message["causation_id"] == ""
    CANONICAL_EVENT_SCHEMAS.validate(event)


def test_event_schema_rejects_malformed_shadow_payload():
    event = {
        "event_id": "x", "event_type": EVENT_SHADOW_OBSERVED, "event_version": "1.0.0",
        "timestamp": 1, "source": "live.execution.shadow_store", "domain": "execution",
        "criticality": "C2",
    }
    from quantos.contracts.events import Event
    bad_provenance = Event(**event, payload={
        "observation_id": "shd_" + "0" * 16, "run_label": "b", "instrument_id": "BTC",
        "side": "BUY", "quantity": 1.0, "decision_price": 100.0, "executable_vwap": None,
        "slippage_bps_vs_decision": None, "book_age_ms": None, "as_of_ms": None,
        "provenance": "PAPER"})
    with pytest.raises(EventSchemaError):
        CANONICAL_EVENT_SCHEMAS.validate(bad_provenance)
    bad_id = Event(**event, payload={
        "observation_id": "not-a-shadow-id", "run_label": "b", "instrument_id": "BTC",
        "side": "BUY", "quantity": 1.0, "decision_price": 100.0, "executable_vwap": None,
        "slippage_bps_vs_decision": None, "book_age_ms": None, "as_of_ms": None,
        "provenance": "SHADOW"})
    with pytest.raises(EventSchemaError):
        CANONICAL_EVENT_SCHEMAS.validate(bad_id)


# --- pending / mark 语义 ---

def test_mark_dispatched_semantics(tmp_path):
    store = _store(tmp_path, create=True)
    store.append(_obs(), run_label="b", recorded_at_ms=1)
    pending = store.pending_event_outbox()
    outbox_id = pending[0]["outbox_id"]
    repro = pending[0]["event"].reproducibility_hash
    assert store.mark_event_dispatched(outbox_id, dispatched_at=2_000,
                                       event_repro_hash=repro) is True
    assert store.pending_event_outbox() == ()
    # 完全相同的确认重放 -> 幂等 False;不同 dispatched_at -> 冲突大声抛
    assert store.mark_event_dispatched(outbox_id, dispatched_at=2_000,
                                       event_repro_hash=repro) is False
    with pytest.raises(ShadowStoreError, match="acknowledgement conflict"):
        store.mark_event_dispatched(outbox_id, dispatched_at=3_000,
                                    event_repro_hash=repro)
    with pytest.raises(ShadowStoreError, match="reference mismatch"):
        store.mark_event_dispatched("shd-nonexistent", dispatched_at=1,
                                    event_repro_hash=repro)
    with pytest.raises(ShadowStoreError, match="reference mismatch"):
        store.mark_event_dispatched(outbox_id, dispatched_at=1,
                                    event_repro_hash="0" * 64)
    assert store.event_outbox_chain_valid()


def test_reopen_persists_pending_outbox(tmp_path):
    path = str(tmp_path / "shadow.db")
    store = SqliteShadowStore(path, create=True)
    store.append(_obs(), run_label="b", recorded_at_ms=1)
    expected = store.pending_event_outbox()[0]["event"]
    store.close()
    reopened = SqliteShadowStore(path, create=False)
    assert reopened.pending_event_outbox()[0]["event"] == expected
    assert reopened.event_outbox_chain_valid()


# --- dispatch(第七 outbox)+ drain e2e ---

def test_shadow_outbox_recovers_publish_before_dispatch_ack(tmp_path):
    from services.monitoring.domain_event_runtime import dispatch_shadow_event_outbox
    from services.monitoring.event_bus import DurableEventBus

    store = _store(tmp_path, create=True)
    store.append(_obs(), run_label="week-1", recorded_at_ms=2_000)
    pending = store.pending_event_outbox()
    bus = DurableEventBus(str(tmp_path / "events.db"))
    bus.publish_once(pending[0]["event"], correlation_id=pending[0]["correlation_id"])
    assert store.pending_event_outbox()                        # 崩在 publish 后 ack 前
    summary = dispatch_shadow_event_outbox(store, bus, at_ms=2_200)
    assert summary == {"dispatched": 1, "pending": 0}
    assert bus.count() == 1                                    # publish_once 幂等,不重复
    assert store.pending_event_outbox() == ()
    assert store.event_outbox_chain_valid()


def test_real_sqlite_shadow_store_drains_into_shared_projection(tmp_path):
    from services.monitoring.domain_event_runtime import dispatch_shadow_event_outbox
    from services.monitoring.event_bus import DurableEventBus
    from services.monitoring.event_projection import DomainEventProjection
    from tools.domain_event_outbox_drain import (OutboxSource,
                                                 drain_domain_event_outboxes)

    source_path = str(tmp_path / "shadow.db")
    store = SqliteShadowStore(source_path, create=True)
    observation_id, _ = store.append(_obs(), run_label="week-1", recorded_at_ms=1_000)
    store.close()

    event_path = str(tmp_path / "events.db")
    projection_path = str(tmp_path / "projection.db")
    document = drain_domain_event_outboxes(
        now_ms=2_000, event_path=event_path, projection_path=projection_path,
        output_path=str(tmp_path / "drain.json"),
        sources=(OutboxSource(
            "shadow", source_path,
            lambda path: SqliteShadowStore(path, create=False),
            dispatch_shadow_event_outbox),))

    assert document["verdict"] == "PASS"
    assert document["sources"]["shadow"] == {
        "status": "PASS", "path": source_path, "dispatched": 1, "pending": 0}
    bus = DurableEventBus(event_path, create=False)
    events = bus.records()
    assert [r["event"].payload["observation_id"] for r in events] == [observation_id]
    bus.close()
    projection = DomainEventProjection(projection_path, create=False)
    assert [r["event"].payload["observation_id"] for r in projection.records()] == [
        observation_id]
    projection.close()
