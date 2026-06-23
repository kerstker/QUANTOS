"""Phase 13 — global tamper-evident audit log: chains, dedups, and (via a sealed
commitment) detects truncation / replacement / erasure."""
import pytest

from quantos.contracts.governance import AuditEvent
from services.monitoring.audit_log import GlobalAuditLog


def _filled(n=3):
    log = GlobalAuditLog()
    for i in range(1, n + 1):
        log.append(event_id=f"e{i}", actor=f"a{i}", action="ACT", timestamp=i)
    return log


def test_append_chains_and_validates():
    log = _filled(2)
    assert len(log.events) == 2 and log.chain_valid()
    assert log.events[1].prev_hash == log.events[0].entry_hash


def test_ingest_rechains_and_keeps_source_hash():
    log = GlobalAuditLog()
    src = AuditEvent(event_id="x", actor="risk", action="A", timestamp=1, prev_hash="local-prev")
    ing = log.ingest(src)
    assert ing.metadata["source_hash"] == src.entry_hash and ing.prev_hash == "" and log.chain_valid()


def test_duplicate_event_id_rejected():
    log = _filled(1)
    with pytest.raises(ValueError):
        log.append(event_id="e1", actor="x", action="REPLAY", timestamp=9)   # replay fork blocked


def test_tamper_breaks_chain():
    log = _filled(3)
    assert log.chain_valid()
    log._events[1] = log._events[1].model_copy(update={"action": "TAMPERED"})
    assert not log.chain_valid()


def test_events_view_is_read_only():
    log = _filled(2)
    with pytest.raises((AttributeError, TypeError)):
        log.events[0] = log.events[1]            # tuple view cannot be mutated
    with pytest.raises(AttributeError):
        log.events = ()                          # property cannot be reassigned


def test_commitment_detects_truncation_replacement_and_erasure():
    log = _filled(5)
    seal = log.seal()
    assert log.verify_against(seal)              # unchanged -> ok
    # truncation
    trunc = _filled(5)
    trunc._events.pop()
    assert not trunc.verify_against(seal)        # length mismatch
    # full erasure
    erased = _filled(5)
    erased._events.clear()
    assert not erased.verify_against(seal)
    # wholesale replacement with a self-consistent forged chain
    forged = GlobalAuditLog()
    for i in range(1, 6):
        forged.append(event_id=f"f{i}", actor="attacker", action="FORGED", timestamp=i)
    assert forged.chain_valid() and not forged.verify_against(seal)   # head hash mismatch
