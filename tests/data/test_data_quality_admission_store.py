import json
import sqlite3

import pytest

from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from quantos.contracts.events import EVENT_DATA_QUALITY_ADMISSION_DECIDED
from services.data.admission_store import (
    DataQualityAdmissionStore,
    DataQualityAdmissionStoreError,
)
from services.monitoring.domain_event_runtime import (
    dispatch_data_quality_admission_event_outbox,
)
from services.monitoring.event_bus import DurableEventBus


def _evidence(*, verdict: str = "PASS", generated_at_ms: int = 100) -> dict:
    blocked = verdict == "BLOCK"
    return {
        "schema_version": 2,
        "generated_at_ms": generated_at_ms,
        "readiness": "L2_DRY_RUN_ONLY",
        "purpose": "FORWARD_EVIDENCE_DATA_ADMISSION",
        "instrument_scope": ["BTC", "ETH"],
        "price_basis": "PERP_MARK_PRICE",
        "quality_verdict": "BLOCK" if blocked else "PASS",
        "lineage_verdict": "BLOCK" if blocked else "PASS",
        "verdict": verdict,
        "block_reasons": [
            "QUALITY:funding:['BTC']",
            "LINEAGE:MISSING_DATASETS:['funding']",
        ] if blocked else [],
        "sources": {
            "funding": {
                "passed": not blocked,
                "instruments": {
                    "BTC": {
                        "detail": "credential=must-not-enter-event",
                    }
                },
            }
        },
        "refresh_observation": {
            "readable": True,
            "run_id": "refresh-99",
        },
    }


def test_decision_and_strict_event_are_committed_together(tmp_path):
    path = str(tmp_path / "admission.db")
    store = DataQualityAdmissionStore(path, create=True)

    decision, inserted = store.record_evidence(
        _evidence(),
        recorded_at_ms=110,
        causation_id="refresh-99",
    )

    assert inserted is True
    assert decision.verdict == "PASS"
    assert decision.instrument_scope == ("BTC", "ETH")
    assert len(decision.evidence_sha256) == 64
    assert len(decision.decision_hash) == 64
    pending = store.pending_event_outbox()
    assert len(pending) == 1
    event = pending[0]["event"]
    assert event.event_type == EVENT_DATA_QUALITY_ADMISSION_DECIDED
    assert event.payload == {
        "admission_id": decision.admission_id,
        "block_reason_count": 0,
        "decision_hash": decision.decision_hash,
        "evidence_schema_version": 2,
        "evidence_sha256": decision.evidence_sha256,
        "instrument_count": 2,
        "lineage_verdict": "PASS",
        "price_basis": "PERP_MARK_PRICE",
        "purpose": "FORWARD_EVIDENCE_DATA_ADMISSION",
        "quality_verdict": "PASS",
        "readiness": "L2_DRY_RUN_ONLY",
        "verdict": "PASS",
    }
    assert CANONICAL_EVENT_SCHEMAS.validate(event) == event
    assert pending[0]["correlation_id"] == decision.admission_id
    assert pending[0]["causation_id"] == "refresh-99"
    store.close()

    reopened = DataQualityAdmissionStore(path, create=False)
    assert reopened.chain_valid()
    assert reopened.decisions() == (decision,)
    assert reopened.evidence(decision.admission_id) == _evidence()
    reopened.close()


def test_block_event_is_redacted_but_retains_authoritative_evidence(tmp_path):
    evidence = _evidence(verdict="BLOCK")
    store = DataQualityAdmissionStore(str(tmp_path / "admission.db"), create=True)

    decision, _ = store.record_evidence(
        evidence,
        recorded_at_ms=110,
        causation_id="refresh-99",
    )

    event_document = store.pending_event_outbox()[0]["event"].model_dump(mode="json")
    serialized = json.dumps(event_document, sort_keys=True)
    assert decision.verdict == "BLOCK"
    assert event_document["criticality"] == "C1"
    assert event_document["payload"]["block_reason_count"] == 2
    assert "credential" not in serialized
    assert "MISSING_DATASETS" not in serialized
    assert store.evidence(decision.admission_id) == evidence
    store.close()


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda value: {**value, "instrument_scope": ["ETH", "BTC"]}, "sorted"),
        (lambda value: {**value, "block_reasons": ["unexpected"]}, "PASS"),
        (lambda value: {**value, "quality_verdict": "BLOCK"}, "PASS"),
    ],
)
def test_store_rejects_semantically_invalid_evidence(tmp_path, mutate, match):
    store = DataQualityAdmissionStore(str(tmp_path / "admission.db"), create=True)

    with pytest.raises((ValueError, DataQualityAdmissionStoreError), match=match):
        store.record_evidence(mutate(_evidence()), recorded_at_ms=110)

    assert store.decisions() == ()
    assert store.pending_event_outbox() == ()
    store.close()


def test_exact_evidence_replay_is_idempotent_and_keeps_first_record_time(tmp_path):
    store = DataQualityAdmissionStore(str(tmp_path / "admission.db"), create=True)
    first, first_inserted = store.record_evidence(
        _evidence(),
        recorded_at_ms=110,
        causation_id="refresh-99",
    )

    replay, replay_inserted = store.record_evidence(
        _evidence(),
        recorded_at_ms=999,
        causation_id="refresh-99",
    )

    assert first_inserted is True
    assert replay_inserted is False
    assert replay == first
    assert replay.recorded_at_ms == 110
    assert len(store.decisions()) == 1
    store.close()


def test_tampering_is_detected_before_new_decisions_or_dispatch(tmp_path):
    path = str(tmp_path / "admission.db")
    store = DataQualityAdmissionStore(path, create=True)
    store.record_evidence(_evidence(), recorded_at_ms=110)
    store.close()

    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER data_quality_admissions_no_update")
    connection.execute(
        "UPDATE data_quality_admissions SET verdict='BLOCK' WHERE admission_seq=1"
    )
    connection.commit()
    connection.close()

    with pytest.raises(DataQualityAdmissionStoreError, match="reconstruct|hash|match"):
        DataQualityAdmissionStore(path, create=False)


def test_publish_before_ack_crash_is_recovered_without_duplicate_event(tmp_path):
    source_path = str(tmp_path / "admission.db")
    event_path = str(tmp_path / "events.db")
    store = DataQualityAdmissionStore(source_path, create=True)
    decision, _ = store.record_evidence(
        _evidence(),
        recorded_at_ms=110,
        causation_id="refresh-99",
    )
    bus = DurableEventBus(
        event_path,
        schema_registry=CANONICAL_EVENT_SCHEMAS,
    )
    pending = store.pending_event_outbox()[0]
    bus.publish_once(
        pending["event"],
        correlation_id=pending["correlation_id"],
        causation_id=pending["causation_id"],
    )

    result = dispatch_data_quality_admission_event_outbox(
        store,
        bus,
        at_ms=120,
    )

    assert result == {"dispatched": 1, "pending": 0}
    assert [record["event"].event_id for record in bus.records()] == [
        decision.admission_id
    ]
    assert store.pending_event_outbox() == ()
    bus.close()
    store.close()


def test_create_false_does_not_initialize_an_unrelated_sqlite_file(tmp_path):
    path = tmp_path / "unrelated.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker(value) VALUES ('preserve')")
    connection.commit()
    connection.close()
    before = path.read_bytes()

    with pytest.raises(DataQualityAdmissionStoreError, match="unreadable|schema"):
        DataQualityAdmissionStore(str(path), create=False)

    assert path.read_bytes() == before
