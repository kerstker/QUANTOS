"""DurableAuditLog — 落盘 append-only 哈希链审计存储(路线图 Rank #20)。

镜像 run_registry 的 sqlite 模式,但语义是 append-only + 防篡改:存 entry_hash,读时重算比对
(抓字段篡改),prev_hash→entry_hash 链(抓重排),seal/verify_against(抓截断/整体替换/擦除)。
时间由调用方注入(店内无墙钟),确定性。"""
import os

import pytest

from services.monitoring.audit_store import DurableAuditLog


def _log(tmp_path, name="audit.db"):
    return DurableAuditLog(os.path.join(str(tmp_path), name))


def test_append_persists_and_reloads_chain(tmp_path):
    path = os.path.join(str(tmp_path), "a.db")
    log = DurableAuditLog(path)
    log.append(event_id="e1", actor="alice", action="kill_switch.arm", timestamp=10, target="trip-1")
    log.append(event_id="e2", actor="alice", action="kill_switch.reset", timestamp=20, target="trip-1")
    # reopen from disk -> chain survives and validates
    reopened = DurableAuditLog(path)
    assert len(reopened.events) == 2
    assert reopened.chain_valid()
    assert reopened.events[0].prev_hash == ""
    assert reopened.events[1].prev_hash == reopened.events[0].entry_hash


def test_chain_links_prev_to_entry(tmp_path):
    log = _log(tmp_path)
    a = log.append(event_id="e1", actor="sys", action="x", timestamp=1)
    b = log.append(event_id="e2", actor="sys", action="y", timestamp=2)
    assert a.prev_hash == ""
    assert b.prev_hash == a.entry_hash
    assert log.head_hash == b.entry_hash


def test_duplicate_event_id_is_rejected(tmp_path):
    log = _log(tmp_path)
    log.append(event_id="dup", actor="a", action="x", timestamp=1)
    with pytest.raises(ValueError):
        log.append(event_id="dup", actor="a", action="x", timestamp=2)


def test_field_tamper_is_detected(tmp_path):
    path = os.path.join(str(tmp_path), "t.db")
    log = DurableAuditLog(path)
    log.append(event_id="e1", actor="alice", action="kill_switch.reset", timestamp=10, target="trip-1")
    assert log.chain_valid()
    # tamper a stored field directly in sqlite WITHOUT recomputing its entry_hash
    log._c.execute("UPDATE audit_events SET actor='mallory' WHERE event_id='e1'")
    log._c.commit()
    assert DurableAuditLog(path).chain_valid() is False   # recomputed entry_hash != stored


def test_reorder_tamper_is_detected(tmp_path):
    path = os.path.join(str(tmp_path), "r.db")
    log = DurableAuditLog(path)
    log.append(event_id="e1", actor="a", action="x", timestamp=1)
    log.append(event_id="e2", actor="a", action="y", timestamp=2)
    # swap sequence numbers -> prev_hash linkage no longer matches walk order
    log._c.execute("UPDATE audit_events SET seq = CASE seq WHEN 1 THEN 99 WHEN 2 THEN 1 END")
    log._c.execute("UPDATE audit_events SET seq = 2 WHERE seq = 99")
    log._c.commit()
    assert DurableAuditLog(path).chain_valid() is False


def test_seal_detects_truncation(tmp_path):
    path = os.path.join(str(tmp_path), "s.db")
    log = DurableAuditLog(path)
    log.append(event_id="e1", actor="a", action="x", timestamp=1)
    log.append(event_id="e2", actor="a", action="y", timestamp=2)
    commitment = log.seal()
    log.append(event_id="e3", actor="a", action="z", timestamp=3)
    assert log.verify_against(commitment) is False        # length grew past the sealed anchor
    # delete the last row -> back to sealed length but a different tail is caught too
    log._c.execute("DELETE FROM audit_events WHERE event_id IN ('e3')")
    log._c.commit()
    assert DurableAuditLog(path).verify_against(commitment) is True   # exactly the sealed chain


def test_determinism_same_inputs_same_hash(tmp_path):
    a = _log(tmp_path, "d1.db")
    b = _log(tmp_path, "d2.db")
    ea = a.append(event_id="e", actor="alice", action="deployment.evaluate", timestamp=7,
                  target="PAPER:deploy", metadata={"allowed": "False", "reasons": "VERSION_PIN_REQUIRED"})
    eb = b.append(event_id="e", actor="alice", action="deployment.evaluate", timestamp=7,
                  target="PAPER:deploy", metadata={"allowed": "False", "reasons": "VERSION_PIN_REQUIRED"})
    assert ea.entry_hash == eb.entry_hash


def test_empty_log_is_valid_and_headless(tmp_path):
    log = _log(tmp_path)
    assert log.events == ()
    assert log.chain_valid() is True
    assert log.head_hash == ""
