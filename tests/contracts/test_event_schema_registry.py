import pytest

from quantos.contracts.event_schemas import (
    CANONICAL_EVENT_SCHEMAS,
    EventSchemaCompatibilityError,
    EventSchemaError,
)
from quantos.contracts.events import (
    EVENT_DEPLOYMENT_RECORDED,
    EVENT_DRY_RUN_COMPLETED,
    EVENT_TIMESTAMP_UNIT_MICROSECONDS,
    EVENT_TIMESTAMP_UNIT_MILLISECONDS,
    Event,
)


def _completed(*, version: str, include_run_hash: bool) -> Event:
    payload = {
        "audit_root": "audit-root",
        "n_approved": 2,
        "n_fills": 2,
        "n_intents": 2,
        "n_rejected": 0,
        "run_id": "run-1",
        "strategy_id": "carry",
    }
    if include_run_hash:
        payload["run_repro_hash"] = "a" * 64
    return Event(
        event_id=f"completed-{version}",
        event_type=EVENT_DRY_RUN_COMPLETED,
        event_version=version,
        timestamp=1,
        source="services.execution.dry_run_pipeline",
        domain="execution",
        criticality="C2",
        payload=payload,
    )


def _deployment(*, version: str, timestamp: int) -> Event:
    return Event(
        event_id=f"deployment-{version}",
        event_type=EVENT_DEPLOYMENT_RECORDED,
        event_version=version,
        timestamp=timestamp,
        source="services.deployment.gate",
        domain="governance",
        criticality="C1",
        payload={
            "actor_id": "alice",
            "allowed": True,
            "environment": "PAPER",
            "reasons": [],
            "subject": "candidate-1",
            "version_ids": ["v1"],
        },
    )


def test_registered_event_payload_is_strictly_validated():
    event = _completed(version="1.1.0", include_run_hash=True)

    assert CANONICAL_EVENT_SCHEMAS.validate(event) == event

    malformed = event.model_copy(
        update={"payload": {**event.payload, "n_fills": "2"}}
    )
    with pytest.raises(EventSchemaError, match="payload"):
        CANONICAL_EVENT_SCHEMAS.validate(malformed)


def test_unknown_event_type_and_version_fail_closed():
    unknown_type = _completed(version="1.1.0", include_run_hash=True).model_copy(
        update={"event_type": "execution.unknown"}
    )
    with pytest.raises(EventSchemaError, match="unregistered event type"):
        CANONICAL_EVENT_SCHEMAS.validate(unknown_type)

    with pytest.raises(EventSchemaError, match="unregistered event version"):
        CANONICAL_EVENT_SCHEMAS.validate(
            _completed(version="2.0.0", include_run_hash=True)
        )


def test_compatibility_is_explicit_not_inferred_from_semver():
    old = _completed(version="1.0.0", include_run_hash=False)
    current = _completed(version="1.1.0", include_run_hash=True)

    assert (
        CANONICAL_EVENT_SCHEMAS.validate(old, consumer_version="1.1.0")
        == old
    )
    with pytest.raises(EventSchemaCompatibilityError, match="not compatible"):
        CANONICAL_EVENT_SCHEMAS.validate(
            current,
            consumer_version="1.0.0",
        )


def test_schema_manifest_is_deterministic_and_reports_latest_version():
    first = CANONICAL_EVENT_SCHEMAS.manifest()
    second = CANONICAL_EVENT_SCHEMAS.manifest()

    assert first == second
    assert first[EVENT_DRY_RUN_COMPLETED]["latest_version"] == "1.1.0"
    assert first[EVENT_DRY_RUN_COMPLETED]["versions"] == ["1.0.0", "1.1.0"]
    assert first[EVENT_DEPLOYMENT_RECORDED]["latest_version"] == "1.1.0"
    assert [
        definition["timestamp_unit"]
        for definition in first[EVENT_DEPLOYMENT_RECORDED]["definitions"]
    ] == [
        EVENT_TIMESTAMP_UNIT_MILLISECONDS,
        EVENT_TIMESTAMP_UNIT_MICROSECONDS,
    ]


def test_timestamp_unit_is_versioned_and_mixed_history_normalizes_without_guessing():
    timestamp_ms = 1_784_016_000_000
    legacy = _deployment(version="1.0.0", timestamp=timestamp_ms)
    current = _deployment(version="1.1.0", timestamp=timestamp_ms * 1_000)

    assert CANONICAL_EVENT_SCHEMAS.timestamp_unit(legacy) == (
        EVENT_TIMESTAMP_UNIT_MILLISECONDS
    )
    assert CANONICAL_EVENT_SCHEMAS.timestamp_unit(current) == (
        EVENT_TIMESTAMP_UNIT_MICROSECONDS
    )
    assert CANONICAL_EVENT_SCHEMAS.timestamp_milliseconds(legacy) == timestamp_ms
    assert CANONICAL_EVENT_SCHEMAS.timestamp_milliseconds(current) == timestamp_ms
    assert CANONICAL_EVENT_SCHEMAS.timestamp_unix_seconds(legacy) == timestamp_ms // 1_000
    assert CANONICAL_EVENT_SCHEMAS.timestamp_unix_seconds(current) == timestamp_ms // 1_000
    assert CANONICAL_EVENT_SCHEMAS.validate(
        legacy,
        consumer_version="1.1.0",
    ) == legacy
    with pytest.raises(EventSchemaCompatibilityError, match="not compatible"):
        CANONICAL_EVENT_SCHEMAS.validate(
            current,
            consumer_version="1.0.0",
        )
