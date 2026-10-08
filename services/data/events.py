"""Canonical, redacted market-data operational events."""
from __future__ import annotations

import hashlib

from quantos.contracts.events import (
    EVENT_DATA_QUALITY_ADMISSION_DECIDED,
    EVENT_DATA_REFRESH_RECORDED,
    Event,
    event_timestamp_from_milliseconds,
)


def _event_id(*parts: object) -> str:
    digest = hashlib.sha256(
        "|".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()
    return f"data-refresh-{digest[:32]}"


def data_refresh_recorded_event(
    *,
    run_id: str,
    outcome: str,
    source_count: int,
    failed_source_count: int,
    failed_instrument_count: int,
    duration_ms: int,
    status_hash: str,
    history_entry_hash: str,
    at_ms: int,
) -> Event:
    """Describe one durable refresh without copying free-form source errors."""
    return Event(
        event_id=_event_id(run_id, status_hash, history_entry_hash, at_ms),
        event_type=EVENT_DATA_REFRESH_RECORDED,
        event_version="1.0.0",
        timestamp=event_timestamp_from_milliseconds(at_ms),
        source="services.data.refresh_history",
        domain="data",
        criticality="C2" if outcome == "PASS" else "C1",
        payload={
            "duration_ms": duration_ms,
            "failed_instrument_count": failed_instrument_count,
            "failed_source_count": failed_source_count,
            "history_entry_hash": history_entry_hash,
            "outcome": outcome,
            "run_id": run_id,
            "source_count": source_count,
            "status_hash": status_hash,
        },
    )


def data_quality_admission_decided_event(
    *,
    admission_id: str,
    verdict: str,
    quality_verdict: str,
    lineage_verdict: str,
    evidence_schema_version: int,
    evidence_sha256: str,
    decision_hash: str,
    instrument_count: int,
    block_reason_count: int,
    price_basis: str,
    readiness: str,
    purpose: str,
    at_ms: int,
) -> Event:
    """Describe a durable admission decision without copying source diagnostics."""
    return Event(
        event_id=admission_id,
        event_type=EVENT_DATA_QUALITY_ADMISSION_DECIDED,
        event_version="1.0.0",
        timestamp=event_timestamp_from_milliseconds(at_ms),
        source="services.data.admission_store",
        domain="data",
        criticality="C2" if verdict == "PASS" else "C1",
        payload={
            "admission_id": admission_id,
            "block_reason_count": block_reason_count,
            "decision_hash": decision_hash,
            "evidence_schema_version": evidence_schema_version,
            "evidence_sha256": evidence_sha256,
            "instrument_count": instrument_count,
            "lineage_verdict": lineage_verdict,
            "price_basis": price_basis,
            "purpose": purpose,
            "quality_verdict": quality_verdict,
            "readiness": readiness,
            "verdict": verdict,
        },
    )


__all__ = [
    "data_quality_admission_decided_event",
    "data_refresh_recorded_event",
]
