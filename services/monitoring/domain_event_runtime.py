"""Shared offline runtime for the canonical domain-event log and projection."""
from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import sqlite3
import time

from quantos.contracts.event_schemas import (
    CANONICAL_EVENT_SCHEMAS,
    EventSchemaRegistry,
)
from services.monitoring.event_bus import DurableEventBus, EventBusIntegrityError
from services.monitoring.consumer_group import (
    EventConsumerGroupConfigurationError,
    PROCESS_DECLARED_WORKER_ASSURANCE,
)
from services.monitoring.event_health import (
    EventBusHealthPolicy,
    evaluate_event_bus_health,
)
from services.monitoring.event_projection import DomainEventProjection


DOMAIN_EVENT_DB = os.path.join("data", "cache", "domain_events.db")
DOMAIN_EVENT_PROJECTION_DB = os.path.join(
    "data", "cache", "domain_event_projection.db"
)
DOMAIN_EVENT_PROJECTION_CONSUMER = "domain-event-projection-v1"
DOMAIN_EVENT_PROJECTION_PARTITIONS = 1
DOMAIN_EVENT_PROJECTION_SESSION_TIMEOUT_MS = 120_000
_GROUP_CLOCK_RETRY_LIMIT = 4
_MIN_REAL_MS = 946684800000
_EVENT_AUTHORITY_TABLES = frozenset(
    {
        "event_log",
        "event_consumer_journal",
        "event_consumer_group_journal",
    }
)


def _require_existing_event_authority(path: str) -> None:
    if path == ":memory:":
        raise EventBusIntegrityError("in-memory event authority cannot be reopened")
    try:
        uri = f"{Path(path).resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise EventBusIntegrityError("event authority is unreadable") from exc
    present = {str(row[0]) for row in rows}
    missing = sorted(_EVENT_AUTHORITY_TABLES - present)
    if missing:
        raise EventBusIntegrityError(
            f"event authority schema is incomplete; missing tables: {missing}"
        )


def open_domain_event_bus(
    path: str = DOMAIN_EVENT_DB,
    *,
    create: bool = True,
) -> DurableEventBus:
    if not create:
        _require_existing_event_authority(path)
    return DurableEventBus(
        path,
        create=create,
        schema_registry=CANONICAL_EVENT_SCHEMAS,
    )


def _unavailable_event_bus_health(
    *,
    now_ms: int,
    group_id: str,
    policy: EventBusHealthPolicy,
    code: str,
    verdict: str,
    severity: str,
) -> dict:
    return {
        "schema_version": 1,
        "verdict": verdict,
        "checked_at_ms": int(now_ms),
        "group_id": group_id,
        "consumer_mode": "SINGLE_NODE_LEASED_PARTITIONS",
        "integrity": {
            "event_chain_valid": None,
            "consumer_chain_valid": None,
            "coordination_chain_valid": None,
        },
        "active_workers": [],
        "active_worker_identities": [],
        "worker_identity_policy": PROCESS_DECLARED_WORKER_ASSURANCE,
        "partition_count": 0,
        "pending_events": None,
        "oldest_pending_age_ms": None,
        "dead_letter_count": None,
        "event_records": None,
        "consumer_journal_records": None,
        "coordination_journal_records": None,
        "allocated_bytes": None,
        "retention_mode": "APPEND_ONLY_ALERT_ONLY",
        "thresholds": asdict(policy),
        "alerts": [
            {
                "code": code,
                "observed": None,
                "severity": severity,
                "threshold": None,
            }
        ],
    }


def read_domain_event_bus_health(
    *,
    now_ms: int,
    path: str = DOMAIN_EVENT_DB,
    group_id: str = DOMAIN_EVENT_PROJECTION_CONSUMER,
    policy: EventBusHealthPolicy | None = None,
) -> dict:
    """Read verified bus health without creating or mutating a missing authority."""
    resolved_policy = policy or EventBusHealthPolicy()
    if not os.path.isfile(path):
        return _unavailable_event_bus_health(
            now_ms=now_ms,
            group_id=group_id,
            policy=resolved_policy,
            code="EVENT_AUTHORITY_UNAVAILABLE",
            verdict="UNAVAILABLE",
            severity="WARNING",
        )
    event_bus = None
    try:
        event_bus = open_domain_event_bus(path, create=False)
        snapshot = event_bus.operational_snapshot(group_id, now_ms=now_ms)
        return evaluate_event_bus_health(snapshot, resolved_policy)
    except EventConsumerGroupConfigurationError:
        return _unavailable_event_bus_health(
            now_ms=now_ms,
            group_id=group_id,
            policy=resolved_policy,
            code="CONSUMER_GROUP_NOT_REGISTERED",
            verdict="UNAVAILABLE",
            severity="WARNING",
        )
    except Exception:
        return _unavailable_event_bus_health(
            now_ms=now_ms,
            group_id=group_id,
            policy=resolved_policy,
            code="EVENT_AUTHORITY_UNREADABLE",
            verdict="BREACH",
            severity="CRITICAL",
        )
    finally:
        if event_bus is not None:
            event_bus.close()


def _is_group_clock_rollback(exc: BaseException) -> bool:
    """Return whether a shared worker lost a race on the persisted group clock."""
    return (
        isinstance(exc, ValueError)
        and str(exc) == "consumer-group time must be monotonic"
    )


def _refresh_group_clock(
    event_bus: DurableEventBus,
    *,
    group_id: str,
    requested_ms: int,
) -> int:
    """Choose a timestamp that is current for the shared authority.

    ``project_pending_domain_events`` is used by multiple L2 workers.  A worker
    can capture ``now_ms`` and then wait behind another SQLite transaction that
    appends a later heartbeat.  Direct consumer-group APIs must still reject an
    explicitly regressed clock; this helper is deliberately local to the
    cooperative projection worker and only advances its retry timestamp.
    """
    candidate = int(requested_ms)
    if candidate >= _MIN_REAL_MS:
        candidate = max(candidate, int(time.time() * 1000))
    try:
        status = event_bus.consumer_group_status(
            group_id,
            now_ms=candidate,
        )
    except Exception:
        # Preserve the original failure if the authority cannot be read; the
        # caller will surface the integrity/availability error fail-closed.
        return candidate
    last_at_ms = status.get("last_at_ms") if isinstance(status, dict) else None
    if isinstance(last_at_ms, int) and not isinstance(last_at_ms, bool):
        candidate = max(candidate, last_at_ms)
    return candidate


def _heartbeat_projection_worker(
    event_bus: DurableEventBus,
    *,
    worker_id: str,
    now_ms: int,
    partition_count: int,
    session_timeout_ms: int,
):
    candidate = int(now_ms)
    for attempt in range(_GROUP_CLOCK_RETRY_LIMIT):
        try:
            return event_bus.heartbeat_consumer_group(
                DOMAIN_EVENT_PROJECTION_CONSUMER,
                worker_id=worker_id,
                now_ms=candidate,
                partition_count=partition_count,
                session_timeout_ms=session_timeout_ms,
            )
        except ValueError as exc:
            if not _is_group_clock_rollback(exc) or attempt + 1 >= _GROUP_CLOCK_RETRY_LIMIT:
                raise
            candidate = _refresh_group_clock(
                event_bus,
                group_id=DOMAIN_EVENT_PROJECTION_CONSUMER,
                requested_ms=candidate,
            )
    raise AssertionError("unreachable group heartbeat retry state")


def _leave_projection_worker(
    event_bus: DurableEventBus,
    *,
    worker_id: str,
    now_ms: int,
):
    candidate = int(now_ms)
    for attempt in range(_GROUP_CLOCK_RETRY_LIMIT):
        try:
            return event_bus.leave_consumer_group(
                DOMAIN_EVENT_PROJECTION_CONSUMER,
                worker_id=worker_id,
                now_ms=candidate,
            )
        except ValueError as exc:
            if not _is_group_clock_rollback(exc) or attempt + 1 >= _GROUP_CLOCK_RETRY_LIMIT:
                raise
            candidate = _refresh_group_clock(
                event_bus,
                group_id=DOMAIN_EVENT_PROJECTION_CONSUMER,
                requested_ms=candidate,
            )
    raise AssertionError("unreachable group leave retry state")


def project_pending_domain_events(
    event_bus: DurableEventBus,
    *,
    now_ms: int,
    projection_path: str = DOMAIN_EVENT_PROJECTION_DB,
    worker_id: str = "scheduled-domain-event-projection",
    limit: int = 1_000,
    lease_ms: int = 60_000,
    session_timeout_ms: int = DOMAIN_EVENT_PROJECTION_SESSION_TIMEOUT_MS,
    max_attempts: int = 3,
    schema_registry: EventSchemaRegistry | None = CANONICAL_EVENT_SCHEMAS,
) -> dict:
    """Advance the shared read model with durable at-least-once checkpoints."""
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    if limit < 1:
        raise ValueError("limit must be positive")

    # Floor now_ms to wall-clock when called with a real (historical) timestamp
    # that predates the consumer group's last recorded time.  Test-only tiny
    # values (< 2000-01-01) are left untouched so unit tests still work.
    if now_ms >= _MIN_REAL_MS:
        now_ms = max(now_ms, int(time.time() * 1000))

    projection = DomainEventProjection(
        projection_path,
        schema_registry=schema_registry,
    )
    assignment = _heartbeat_projection_worker(
        event_bus,
        worker_id=worker_id,
        now_ms=now_ms,
        partition_count=DOMAIN_EVENT_PROJECTION_PARTITIONS,
        session_timeout_ms=session_timeout_ms,
    )
    projected = 0
    idempotent_replays = 0
    try:
        if assignment.partitions:
            partition_id = assignment.partitions[0]
            for _ in range(limit):
                delivery = event_bus.claim_partition(
                    assignment,
                    partition_id,
                    now_ms=now_ms,
                    lease_ms=lease_ms,
                )
                if delivery is None:
                    break
                try:
                    inserted = projection.apply(delivery)
                except Exception as exc:
                    event_bus.fail_delivery(
                        delivery,
                        exc,
                        at_ms=now_ms,
                        max_attempts=max_attempts,
                    )
                    raise AssertionError("fail_delivery must raise") from exc
                event_bus.ack_delivery(delivery, at_ms=now_ms)
                if inserted:
                    projected += 1
                else:
                    idempotent_replays += 1
        return {
            "assigned_partitions": list(assignment.partitions),
            "consumer_id": DOMAIN_EVENT_PROJECTION_CONSUMER,
            "consumer_group_mode": "SINGLE_NODE_LEASED_PARTITIONS",
            "dead_letter_count": len(
                event_bus.dead_letters(
                    consumer_id=DOMAIN_EVENT_PROJECTION_CONSUMER
                )
            ),
            "group_generation": assignment.generation,
            "idempotent_replays": idempotent_replays,
            "offset": event_bus.consumer_offset(
                DOMAIN_EVENT_PROJECTION_CONSUMER
            ),
            "partition_count": assignment.partition_count,
            "projected": projected,
        }
    finally:
        projection.close()
        _leave_projection_worker(
            event_bus,
            worker_id=worker_id,
            now_ms=now_ms,
        )


def dispatch_experiment_event_outbox(
    registry,
    event_bus: DurableEventBus,
    *,
    at_ms: int,
    limit: int = 1_000,
) -> dict:
    """Idempotently transfer registry events, then append dispatch acknowledgements."""
    pending = registry.pending_event_outbox(limit=limit)
    dispatched = 0
    for record in pending:
        event = record["event"]
        CANONICAL_EVENT_SCHEMAS.validate(event)
        event_bus.publish_once(
            event,
            correlation_id=record["correlation_id"],
            causation_id=record["causation_id"],
        )
        registry.mark_event_dispatched(
            record["outbox_id"],
            dispatched_at=at_ms,
            event_repro_hash=event.reproducibility_hash,
        )
        dispatched += 1
    return {
        "dispatched": dispatched,
        "pending": len(registry.pending_event_outbox(limit=limit)),
    }


def dispatch_maintenance_event_outbox(
    store,
    event_bus: DurableEventBus,
    *,
    at_ms: int,
    limit: int = 1_000,
) -> dict:
    """Idempotently transfer maintenance snapshot events into the shared log."""
    pending = store.pending_event_outbox(limit=limit)
    dispatched = 0
    for record in pending:
        event = record["event"]
        CANONICAL_EVENT_SCHEMAS.validate(event)
        event_bus.publish_once(
            event,
            correlation_id=record["correlation_id"],
            causation_id=record["causation_id"],
        )
        store.mark_event_dispatched(
            record["outbox_id"],
            dispatched_at=at_ms,
            event_repro_hash=event.reproducibility_hash,
        )
        dispatched += 1
    return {
        "dispatched": dispatched,
        "pending": len(store.pending_event_outbox(limit=limit)),
    }


def dispatch_kill_switch_event_outbox(
    store,
    event_bus: DurableEventBus,
    *,
    at_ms: int,
    limit: int = 1_000,
) -> dict:
    """Idempotently transfer authoritative kill-switch transition events."""
    pending = store.pending_event_outbox(limit=limit)
    dispatched = 0
    for record in pending:
        event = record["event"]
        CANONICAL_EVENT_SCHEMAS.validate(event)
        event_bus.publish_once(
            event,
            correlation_id=record["correlation_id"],
            causation_id=record["causation_id"],
        )
        store.mark_event_dispatched(
            record["outbox_id"],
            dispatched_at=at_ms,
            event_repro_hash=event.reproducibility_hash,
        )
        dispatched += 1
    return {
        "dispatched": dispatched,
        "pending": len(store.pending_event_outbox(limit=limit)),
    }


def dispatch_deployment_event_outbox(
    store,
    event_bus: DurableEventBus,
    *,
    at_ms: int,
    limit: int = 1_000,
) -> dict:
    """Idempotently transfer authoritative deployment-evaluation events."""
    pending = store.pending_event_outbox(limit=limit)
    dispatched = 0
    for record in pending:
        event = record["event"]
        CANONICAL_EVENT_SCHEMAS.validate(event)
        event_bus.publish_once(
            event,
            correlation_id=record["correlation_id"],
            causation_id=record["causation_id"],
        )
        store.mark_event_dispatched(
            record["outbox_id"],
            dispatched_at=at_ms,
            event_repro_hash=event.reproducibility_hash,
        )
        dispatched += 1
    return {
        "dispatched": dispatched,
        "pending": len(store.pending_event_outbox(limit=limit)),
    }


def dispatch_refresh_event_outbox(
    store,
    event_bus: DurableEventBus,
    *,
    at_ms: int,
    limit: int = 1_000,
) -> dict:
    """Idempotently transfer authoritative market-data refresh events."""
    pending = store.pending_event_outbox(limit=limit)
    dispatched = 0
    for record in pending:
        event = record["event"]
        CANONICAL_EVENT_SCHEMAS.validate(event)
        event_bus.publish_once(
            event,
            correlation_id=record["correlation_id"],
            causation_id=record["causation_id"],
        )
        store.mark_event_dispatched(
            record["outbox_id"],
            dispatched_at=at_ms,
            event_repro_hash=event.reproducibility_hash,
        )
        dispatched += 1
    return {
        "dispatched": dispatched,
        "pending": len(store.pending_event_outbox(limit=limit)),
    }


def dispatch_data_quality_admission_event_outbox(
    store,
    event_bus: DurableEventBus,
    *,
    at_ms: int,
    limit: int = 1_000,
) -> dict:
    """Idempotently transfer authoritative data-quality admission events."""
    pending = store.pending_event_outbox(limit=limit)
    dispatched = 0
    for record in pending:
        event = record["event"]
        CANONICAL_EVENT_SCHEMAS.validate(event)
        event_bus.publish_once(
            event,
            correlation_id=record["correlation_id"],
            causation_id=record["causation_id"],
        )
        store.mark_event_dispatched(
            record["outbox_id"],
            dispatched_at=at_ms,
            event_repro_hash=event.reproducibility_hash,
        )
        dispatched += 1
    return {
        "dispatched": dispatched,
        "pending": len(store.pending_event_outbox(limit=limit)),
    }


def dispatch_shadow_event_outbox(
    store,
    event_bus: DurableEventBus,
    *,
    at_ms: int,
    limit: int = 1_000,
) -> dict:
    """Idempotently move shadow-observation events into the shared log.

    Same dependency-injection signature as the six existing dispatchers (the
    owner store instance is passed in): services/ never imports live/, and the
    shadow owner store lives at live/execution/shadow_store.py.
    """
    pending = store.pending_event_outbox(limit=limit)
    dispatched = 0
    for record in pending:
        event = record["event"]
        CANONICAL_EVENT_SCHEMAS.validate(event)
        event_bus.publish_once(
            event,
            correlation_id=record["correlation_id"],
            causation_id=record["causation_id"],
        )
        store.mark_event_dispatched(
            record["outbox_id"],
            dispatched_at=at_ms,
            event_repro_hash=event.reproducibility_hash,
        )
        dispatched += 1
    return {
        "dispatched": dispatched,
        "pending": len(store.pending_event_outbox(limit=limit)),
    }


__all__ = [
    "DOMAIN_EVENT_DB",
    "DOMAIN_EVENT_PROJECTION_CONSUMER",
    "DOMAIN_EVENT_PROJECTION_DB",
    "DOMAIN_EVENT_PROJECTION_PARTITIONS",
    "DOMAIN_EVENT_PROJECTION_SESSION_TIMEOUT_MS",
    "dispatch_data_quality_admission_event_outbox",
    "dispatch_deployment_event_outbox",
    "dispatch_experiment_event_outbox",
    "dispatch_kill_switch_event_outbox",
    "dispatch_maintenance_event_outbox",
    "dispatch_refresh_event_outbox",
    "dispatch_shadow_event_outbox",
    "open_domain_event_bus",
    "project_pending_domain_events",
    "read_domain_event_bus_health",
]
