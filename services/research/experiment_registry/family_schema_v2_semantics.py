"""Pure read-only semantic verification for the inherited family-schema v2.

This private module deliberately owns no connection lifecycle, transaction,
migration, or schema-version policy.  Callers must supply an already-open
``sqlite3.Connection`` and, when needed, enforce the exact schema separately.
The legacy store consumes the same hashes and scans so activation verification
cannot drift from the public v2 writers.
"""

from __future__ import annotations

from quantos.contracts.research_registry_identity import (
    search_evaluation_hash as search_evaluation_hash,
    canonical_hash as canonical_hash, search_event_hash as search_event_hash,
    search_candidate_hash as search_candidate_hash, search_heartbeat_hash as search_heartbeat_hash,
    search_recovery_hash as search_recovery_hash,
)

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from quantos.contracts.events import Event, EventOutboxMessage

from services.research.ports.persistence import (
    EvaluationAuditIntent,
    ExperimentStoreError,
)


_SEARCH_RECOVERY_REASON = "STALE_STARTED_TIMEOUT"


@dataclass(frozen=True, slots=True)
class VerifiedSearchState:
    event_head: str
    candidate_head: str
    heartbeat_head: str
    recovery_head: str
    evaluation_link_head: str
    attempts: dict[str, dict[str, object]]


@dataclass(frozen=True, slots=True)
class VerifiedEventOutboxState:
    outbox_head: str
    messages: dict[str, EventOutboxMessage]
    dispatch_head: str


@dataclass(frozen=True, slots=True)
class VerifiedEvaluationAuditState:
    audit_head: str
    intents: dict[str, tuple[str, EvaluationAuditIntent]]
    dispatch_head: str


@dataclass(frozen=True, slots=True)
class VerifiedV2SemanticState:
    search: VerifiedSearchState
    event_outbox: VerifiedEventOutboxState
    evaluation_audit: VerifiedEvaluationAuditState


def _rows(
    connection: sqlite3.Connection,
    statement: str,
) -> list[dict[str, object]]:
    cursor = connection.execute(statement)
    columns = tuple(description[0] for description in cursor.description or ())
    return [dict(zip(columns, tuple(row), strict=True)) for row in cursor.fetchall()]














def evaluation_audit_hash(
    *,
    run_id: str,
    intent: EvaluationAuditIntent,
    evaluation_content_hash: str,
    prev_record_hash: str,
) -> str:
    return canonical_hash(
        {
            "action": intent.action,
            "actor": intent.actor,
            "evaluation_content_hash": evaluation_content_hash,
            "event_id": intent.event_id,
            "metadata": dict(intent.metadata),
            "prev_record_hash": prev_record_hash,
            "run_id": run_id,
            "target": intent.target,
            "timestamp": intent.timestamp,
        }
    )


def evaluation_audit_dispatch_hash(
    *,
    event_id: str,
    dispatched_at: int,
    audit_entry_hash: str,
    prev_record_hash: str,
) -> str:
    return canonical_hash(
        {
            "audit_entry_hash": audit_entry_hash,
            "dispatched_at": dispatched_at,
            "event_id": event_id,
            "prev_record_hash": prev_record_hash,
        }
    )


def event_json(event: Event) -> str:
    return json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def outbox_hash(message: EventOutboxMessage, prev_record_hash: str) -> str:
    canonical = json.dumps(
        {
            "causation_id": message.causation_id,
            "correlation_id": message.correlation_id,
            "event": message.event.model_dump(mode="json"),
            "outbox_id": message.outbox_id,
            "prev_record_hash": prev_record_hash,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def dispatch_hash(
    *,
    outbox_id: str,
    dispatched_at: int,
    event_repro_hash: str,
    prev_record_hash: str,
) -> str:
    canonical = json.dumps(
        {
            "dispatched_at": dispatched_at,
            "event_repro_hash": event_repro_hash,
            "outbox_id": outbox_id,
            "prev_record_hash": prev_record_hash,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def message_from_row(row: Mapping[str, object]) -> EventOutboxMessage:
    try:
        event = Event.model_validate_json(row["event_json"])
    except Exception as exc:
        raise ExperimentStoreError(
            f"experiment outbox event {row['outbox_id']} cannot be reconstructed"
        ) from exc
    if event.reproducibility_hash != row["repro_hash"]:
        raise ExperimentStoreError(
            f"experiment outbox event {row['outbox_id']} payload hash mismatch"
        )
    message = EventOutboxMessage(
        event=event,
        correlation_id=row["correlation_id"],
        causation_id=row["causation_id"],
    )
    if message.outbox_id != row["outbox_id"]:
        raise ExperimentStoreError(
            f"experiment outbox event {row['outbox_id']} id mismatch"
        )
    return message


def verify_outbox_rows(
    rows: Sequence[Mapping[str, object]],
) -> tuple[str, dict[str, EventOutboxMessage]]:
    previous = ""
    messages: dict[str, EventOutboxMessage] = {}
    for row in rows:
        message = message_from_row(row)
        if row["prev_record_hash"] != previous:
            raise ExperimentStoreError(
                f"experiment outbox row {row['outbox_seq']} previous hash mismatch"
            )
        expected = outbox_hash(message, previous)
        if row["record_hash"] != expected:
            raise ExperimentStoreError(
                f"experiment outbox row {row['outbox_seq']} record hash mismatch"
            )
        previous = expected
        messages[message.outbox_id] = message
    return previous, messages


def verify_dispatch_rows(
    rows: Sequence[Mapping[str, object]],
    messages: Mapping[str, EventOutboxMessage],
) -> str:
    previous = ""
    for row in rows:
        message = messages.get(str(row["outbox_id"]))
        if (
            message is None
            or message.event.reproducibility_hash != row["event_repro_hash"]
        ):
            raise ExperimentStoreError(
                f"experiment dispatch row {row['dispatch_seq']} reference mismatch"
            )
        if row["prev_record_hash"] != previous:
            raise ExperimentStoreError(
                f"experiment dispatch row {row['dispatch_seq']} previous hash mismatch"
            )
        expected = dispatch_hash(
            outbox_id=row["outbox_id"],
            dispatched_at=row["dispatched_at"],
            event_repro_hash=row["event_repro_hash"],
            prev_record_hash=previous,
        )
        if row["record_hash"] != expected:
            raise ExperimentStoreError(
                f"experiment dispatch row {row['dispatch_seq']} record hash mismatch"
            )
        previous = expected
    return previous


def evaluation_audit_intent_from_row(
    row: Mapping[str, object],
) -> EvaluationAuditIntent:
    try:
        metadata = json.loads(row["metadata_json"])
    except Exception as exc:
        raise ExperimentStoreError(
            f"evaluation audit {row['event_id']} metadata is corrupt"
        ) from exc
    if not isinstance(metadata, dict):
        raise ExperimentStoreError(
            f"evaluation audit {row['event_id']} metadata is not an object"
        )
    return EvaluationAuditIntent(
        event_id=row["event_id"],
        actor=row["actor"],
        action=row["action"],
        timestamp=row["timestamp"],
        target=row["target"],
        metadata=metadata,
    )


def verify_search_state(connection: sqlite3.Connection) -> VerifiedSearchState:
    event_rows = _rows(
        connection,
        "SELECT event_seq,search_id,search_kind,source,dataset_version,status,"
        " recorded_at,prev_event_hash,event_hash"
        " FROM search_attempt_events ORDER BY event_seq",
    )
    candidate_rows = _rows(
        connection,
        "SELECT candidate_seq,search_id,config_hash,status,registered_at,"
        " prev_candidate_hash,candidate_hash"
        " FROM search_candidates ORDER BY candidate_seq",
    )
    heartbeat_rows = _rows(
        connection,
        "SELECT heartbeat_seq,search_id,recorded_at,prev_heartbeat_hash,"
        " heartbeat_hash FROM search_attempt_heartbeats ORDER BY heartbeat_seq",
    )
    recovery_rows = _rows(
        connection,
        "SELECT recovery_seq,search_id,reason,timeout_ms,last_activity_at,"
        " recovered_at,terminal_event_hash,prev_recovery_hash,recovery_hash"
        " FROM search_attempt_recoveries ORDER BY recovery_seq",
    )
    evaluation_link_rows = _rows(
        connection,
        "SELECT link_seq,search_id,config_hash,run_id,recorded_at,"
        " evaluation_content_hash,prev_link_hash,link_hash"
        " FROM search_evaluations ORDER BY link_seq",
    )

    event_head = ""
    search_start_high_water = -1
    attempts: dict[str, dict[str, object]] = {}
    for row in event_rows:
        if row["prev_event_hash"] != event_head:
            raise ExperimentStoreError(
                f"search event {row['event_seq']} previous hash mismatch"
            )
        expected = search_event_hash(
            search_id=row["search_id"],
            search_kind=row["search_kind"],
            source=row["source"],
            dataset_version=row["dataset_version"],
            status=row["status"],
            recorded_at=row["recorded_at"],
            prev_event_hash=event_head,
        )
        if row["event_hash"] != expected:
            raise ExperimentStoreError(
                f"search event {row['event_seq']} hash mismatch"
            )
        search_id = row["search_id"]
        metadata = (
            row["search_kind"],
            row["source"],
            row["dataset_version"],
        )
        prior = attempts.get(search_id)
        if row["status"] == "STARTED":
            if prior is not None or row["recorded_at"] < search_start_high_water:
                raise ExperimentStoreError(
                    f"search attempt {search_id} has invalid STARTED provenance"
                )
            attempts[search_id] = {
                "metadata": metadata,
                "status": "STARTED",
                "started_at": row["recorded_at"],
                "last_activity_at": row["recorded_at"],
                "terminal_at": None,
                "terminal_event_hash": None,
            }
            search_start_high_water = row["recorded_at"]
        elif (
            prior is None
            or prior["status"] != "STARTED"
            or prior["metadata"] != metadata
            or row["recorded_at"] < prior["started_at"]
        ):
            raise ExperimentStoreError(
                f"search attempt {search_id} has an invalid terminal transition"
            )
        else:
            prior["status"] = row["status"]
            prior["terminal_at"] = row["recorded_at"]
            prior["terminal_event_hash"] = expected
        event_head = expected

    candidate_head = ""
    seen_candidates: set[tuple[str, str]] = set()
    latest_candidate_times: dict[str, int] = {}
    trial_hashes = {
        row["config_hash"]
        for row in _rows(connection, "SELECT config_hash FROM trials")
    }
    for row in candidate_rows:
        if row["prev_candidate_hash"] != candidate_head:
            raise ExperimentStoreError(
                f"search candidate {row['candidate_seq']} previous hash mismatch"
            )
        expected = search_candidate_hash(
            search_id=row["search_id"],
            config_hash=row["config_hash"],
            status=row["status"],
            registered_at=row["registered_at"],
            prev_candidate_hash=candidate_head,
        )
        if row["candidate_hash"] != expected:
            raise ExperimentStoreError(
                f"search candidate {row['candidate_seq']} hash mismatch"
            )
        attempt = attempts.get(row["search_id"])
        identity = (row["search_id"], row["config_hash"])
        prior_candidate_time = latest_candidate_times.get(row["search_id"], -1)
        if (
            attempt is None
            or identity in seen_candidates
            or row["config_hash"] not in trial_hashes
            or row["registered_at"] < attempt["started_at"]
            or row["registered_at"] < prior_candidate_time
            or (
                attempt["terminal_at"] is not None
                and row["registered_at"] > attempt["terminal_at"]
            )
        ):
            raise ExperimentStoreError(
                f"search candidate {row['candidate_seq']} has invalid provenance"
            )
        seen_candidates.add(identity)
        latest_candidate_times[row["search_id"]] = row["registered_at"]
        attempt["last_activity_at"] = max(
            int(attempt["last_activity_at"]), row["registered_at"]
        )
        candidate_head = expected

    heartbeat_head = ""
    latest_heartbeat_times: dict[str, int] = {}
    for row in heartbeat_rows:
        if row["prev_heartbeat_hash"] != heartbeat_head:
            raise ExperimentStoreError(
                f"search heartbeat {row['heartbeat_seq']} previous hash mismatch"
            )
        expected = search_heartbeat_hash(
            search_id=row["search_id"],
            recorded_at=row["recorded_at"],
            prev_heartbeat_hash=heartbeat_head,
        )
        if row["heartbeat_hash"] != expected:
            raise ExperimentStoreError(
                f"search heartbeat {row['heartbeat_seq']} hash mismatch"
            )
        attempt = attempts.get(row["search_id"])
        previous_at = latest_heartbeat_times.get(row["search_id"], -1)
        if (
            attempt is None
            or row["recorded_at"] < attempt["started_at"]
            or row["recorded_at"] < previous_at
            or (
                attempt["terminal_at"] is not None
                and row["recorded_at"] > attempt["terminal_at"]
            )
        ):
            raise ExperimentStoreError(
                f"search heartbeat {row['heartbeat_seq']} has invalid provenance"
            )
        latest_heartbeat_times[row["search_id"]] = row["recorded_at"]
        attempt["last_activity_at"] = max(
            int(attempt["last_activity_at"]), row["recorded_at"]
        )
        heartbeat_head = expected

    recovery_head = ""
    for row in recovery_rows:
        if row["prev_recovery_hash"] != recovery_head:
            raise ExperimentStoreError(
                f"search recovery {row['recovery_seq']} previous hash mismatch"
            )
        expected = search_recovery_hash(
            search_id=row["search_id"],
            reason=row["reason"],
            timeout_ms=row["timeout_ms"],
            last_activity_at=row["last_activity_at"],
            recovered_at=row["recovered_at"],
            terminal_event_hash=row["terminal_event_hash"],
            prev_recovery_hash=recovery_head,
        )
        if row["recovery_hash"] != expected:
            raise ExperimentStoreError(
                f"search recovery {row['recovery_seq']} hash mismatch"
            )
        recovery_head = expected

    evaluation_link_head = ""
    latest_link_times: dict[str, int] = {}
    registered_candidates = {
        (row["search_id"], row["config_hash"])
        for row in _rows(
            connection,
            "SELECT search_id,config_hash FROM search_candidates",
        )
    }
    evaluations = {
        row["run_id"]: row
        for row in _rows(
            connection,
            "SELECT run_id,dataset_version,recorded_ts,content_hash FROM evaluations",
        )
    }
    seen_links: set[tuple[str, str]] = set()
    for row in evaluation_link_rows:
        if row["prev_link_hash"] != evaluation_link_head:
            raise ExperimentStoreError(
                f"search evaluation link {row['link_seq']} previous hash mismatch"
            )
        expected = search_evaluation_hash(
            search_id=row["search_id"],
            config_hash=row["config_hash"],
            run_id=row["run_id"],
            recorded_at=row["recorded_at"],
            evaluation_content_hash=row["evaluation_content_hash"],
            prev_link_hash=evaluation_link_head,
        )
        if row["link_hash"] != expected:
            raise ExperimentStoreError(
                f"search evaluation link {row['link_seq']} hash mismatch"
            )
        attempt = attempts.get(row["search_id"])
        evaluation = evaluations.get(row["run_id"])
        prior_link_time = latest_link_times.get(row["search_id"], -1)
        link_identity = (row["search_id"], row["run_id"])
        if (
            attempt is None
            or link_identity in seen_links
            or (row["search_id"], row["config_hash"])
            not in registered_candidates
            or evaluation is None
            or evaluation["dataset_version"] != attempt["metadata"][2]
            or evaluation["content_hash"] != row["evaluation_content_hash"]
            or row["recorded_at"] < attempt["started_at"]
            or row["recorded_at"] < prior_link_time
            or (
                attempt["terminal_at"] is not None
                and row["recorded_at"] > attempt["terminal_at"]
            )
        ):
            raise ExperimentStoreError(
                f"search evaluation link {row['link_seq']} has invalid provenance"
            )
        attempt["last_activity_at"] = max(
            int(attempt["last_activity_at"]), row["recorded_at"]
        )
        seen_links.add(link_identity)
        evaluation_link_head = expected
        latest_link_times[row["search_id"]] = row["recorded_at"]

    for row in recovery_rows:
        attempt = attempts.get(row["search_id"])
        if (
            attempt is None
            or attempt["status"] != "FAILED"
            or row["reason"] != _SEARCH_RECOVERY_REASON
            or row["last_activity_at"] != attempt["last_activity_at"]
            or row["recovered_at"] != attempt["terminal_at"]
            or row["terminal_event_hash"] != attempt["terminal_event_hash"]
            or row["timeout_ms"] <= 0
            or row["last_activity_at"] + row["timeout_ms"]
            > row["recovered_at"]
        ):
            raise ExperimentStoreError(
                f"search recovery {row['recovery_seq']} has invalid provenance"
            )

    return VerifiedSearchState(
        event_head=event_head,
        candidate_head=candidate_head,
        heartbeat_head=heartbeat_head,
        recovery_head=recovery_head,
        evaluation_link_head=evaluation_link_head,
        attempts=attempts,
    )


def verify_event_outbox_state(
    connection: sqlite3.Connection,
) -> VerifiedEventOutboxState:
    outbox_rows = _rows(
        connection,
        "SELECT outbox_seq,outbox_id,event_json,correlation_id,causation_id,"
        " repro_hash,prev_record_hash,record_hash"
        " FROM experiment_event_outbox ORDER BY outbox_seq",
    )
    dispatch_rows = _rows(
        connection,
        "SELECT dispatch_seq,outbox_id,dispatched_at,event_repro_hash,"
        " prev_record_hash,record_hash"
        " FROM experiment_event_dispatch ORDER BY dispatch_seq",
    )
    outbox_head, messages = verify_outbox_rows(outbox_rows)
    dispatch_head = verify_dispatch_rows(dispatch_rows, messages)
    return VerifiedEventOutboxState(
        outbox_head=outbox_head,
        messages=messages,
        dispatch_head=dispatch_head,
    )


def verify_evaluation_audit_state(
    connection: sqlite3.Connection,
) -> VerifiedEvaluationAuditState:
    audit_rows = _rows(
        connection,
        "SELECT audit_seq,run_id,event_id,actor,action,timestamp,target,"
        " metadata_json,evaluation_content_hash,prev_record_hash,record_hash"
        " FROM evaluation_audit_outbox ORDER BY audit_seq",
    )
    dispatch_rows = _rows(
        connection,
        "SELECT dispatch_seq,event_id,dispatched_at,audit_entry_hash,"
        " prev_record_hash,record_hash"
        " FROM evaluation_audit_dispatch ORDER BY dispatch_seq",
    )
    evaluations = {
        row["run_id"]: row["content_hash"]
        for row in _rows(connection, "SELECT run_id,content_hash FROM evaluations")
    }

    previous = ""
    intents: dict[str, tuple[str, EvaluationAuditIntent]] = {}
    for row in audit_rows:
        intent = evaluation_audit_intent_from_row(row)
        if (
            row["prev_record_hash"] != previous
            or evaluations.get(row["run_id"])
            != row["evaluation_content_hash"]
        ):
            raise ExperimentStoreError(
                f"evaluation audit row {row['audit_seq']} has invalid provenance"
            )
        expected = evaluation_audit_hash(
            run_id=row["run_id"],
            intent=intent,
            evaluation_content_hash=row["evaluation_content_hash"],
            prev_record_hash=previous,
        )
        if row["record_hash"] != expected:
            raise ExperimentStoreError(
                f"evaluation audit row {row['audit_seq']} hash mismatch"
            )
        intents[intent.event_id] = (row["run_id"], intent)
        previous = expected

    dispatch_head = ""
    for row in dispatch_rows:
        if (
            row["event_id"] not in intents
            or row["prev_record_hash"] != dispatch_head
        ):
            raise ExperimentStoreError(
                f"evaluation audit dispatch {row['dispatch_seq']} has invalid provenance"
            )
        expected = evaluation_audit_dispatch_hash(
            event_id=row["event_id"],
            dispatched_at=row["dispatched_at"],
            audit_entry_hash=row["audit_entry_hash"],
            prev_record_hash=dispatch_head,
        )
        if row["record_hash"] != expected:
            raise ExperimentStoreError(
                f"evaluation audit dispatch {row['dispatch_seq']} hash mismatch"
            )
        dispatch_head = expected
    return VerifiedEvaluationAuditState(
        audit_head=previous,
        intents=intents,
        dispatch_head=dispatch_head,
    )


def verify_v2_semantics(connection: sqlite3.Connection) -> VerifiedV2SemanticState:
    return VerifiedV2SemanticState(
        search=verify_search_state(connection),
        event_outbox=verify_event_outbox_state(connection),
        evaluation_audit=verify_evaluation_audit_state(connection),
    )


__all__ = [
    "VerifiedEvaluationAuditState",
    "VerifiedEventOutboxState",
    "VerifiedSearchState",
    "VerifiedV2SemanticState",
    "canonical_hash",
    "dispatch_hash",
    "evaluation_audit_dispatch_hash",
    "evaluation_audit_hash",
    "evaluation_audit_intent_from_row",
    "event_json",
    "message_from_row",
    "outbox_hash",
    "search_candidate_hash",
    "search_evaluation_hash",
    "search_event_hash",
    "search_heartbeat_hash",
    "search_recovery_hash",
    "verify_dispatch_rows",
    "verify_evaluation_audit_state",
    "verify_event_outbox_state",
    "verify_outbox_rows",
    "verify_search_state",
    "verify_v2_semantics",
]
