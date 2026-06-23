"""Phase 13 — global tamper-evident audit log: chains across components; tamper breaks it."""
from quantos.contracts.governance import AuditEvent
from services.monitoring.audit_log import GlobalAuditLog


def test_append_chains_and_validates():
    log = GlobalAuditLog()
    log.append(event_id="e1", actor="risk", action="RISK_DECISION", timestamp=1)
    log.append(event_id="e2", actor="alpha", action="ALPHA_TRANSITION_TESTING", timestamp=2)
    assert len(log.events) == 2 and log.chain_valid()
    assert log.events[1].prev_hash == log.events[0].entry_hash      # chained across sources


def test_ingest_rechains_and_keeps_source_hash():
    log = GlobalAuditLog()
    src = AuditEvent(event_id="x", actor="risk", action="A", timestamp=1, prev_hash="local-prev")
    ing = log.ingest(src)
    assert ing.metadata["source_hash"] == src.entry_hash           # provenance preserved
    assert ing.prev_hash == "" and log.chain_valid()               # first global entry


def test_tamper_breaks_chain():
    log = GlobalAuditLog()
    for i in (1, 2, 3):
        log.append(event_id=f"e{i}", actor=f"a{i}", action="ACT", timestamp=i)
    assert log.chain_valid()
    log.events[1] = log.events[1].model_copy(update={"action": "TAMPERED"})  # mutate the middle
    assert not log.chain_valid()
