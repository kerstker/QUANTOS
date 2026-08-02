"""Read-only consistency checks for the domain-event authority and projection.

The canonical event log and its read projection are append-only authorities.  A
projection may be rebuilt into a separately named file, but this module never
mutates either database.  That distinction is important when an old projection
is accidentally paired with a newer authority: silently truncating the old
projection would destroy audit evidence.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from quantos.contracts.events import Event
from services.monitoring.event_bus import (
    _consumer_record_hash,
    _record_hash,
)
from services.monitoring.event_projection import _projection_hash


DEFAULT_CONSUMER_ID = "domain-event-projection-v1"


class EventAuthorityConsistencyError(RuntimeError):
    """Raised only for an unreadable authority or projection schema/history."""


def _connect_read_only(path: str) -> sqlite3.Connection:
    if not isinstance(path, str) or not path:
        raise ValueError("database path must be a non-empty string")
    if path == ":memory:" or not os.path.isfile(path):
        raise EventAuthorityConsistencyError(f"database unavailable: {path}")
    # mode=ro prevents writes; query_only adds a second guard inside SQLite.
    uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection
    except sqlite3.Error as exc:
        raise EventAuthorityConsistencyError(
            f"database unreadable: {Path(path).name}"
        ) from exc


def _event_from_authority_row(row: tuple[Any, ...]) -> Event:
    try:
        event = Event(
            event_id=row[1],
            event_type=row[2],
            event_version=row[3],
            timestamp=row[4],
            source=row[5],
            domain=row[6],
            criticality=row[7],
            payload=json.loads(row[10]),
        )
    except Exception as exc:
        raise EventAuthorityConsistencyError(
            f"event authority row {row[0]} cannot be reconstructed"
        ) from exc
    if event.reproducibility_hash != row[11]:
        raise EventAuthorityConsistencyError(
            f"event authority row {row[0]} payload hash mismatch"
        )
    return event


def _event_from_projection_row(row: tuple[Any, ...]) -> Event:
    try:
        event = Event(
            event_id=row[2],
            event_type=row[3],
            event_version=row[4],
            timestamp=row[5],
            source=row[6],
            domain=row[7],
            criticality=row[8],
            payload=json.loads(row[11]),
        )
    except Exception as exc:
        raise EventAuthorityConsistencyError(
            f"projection row {row[0]} cannot be reconstructed"
        ) from exc
    if event.reproducibility_hash != row[12]:
        raise EventAuthorityConsistencyError(
            f"projection row {row[0]} payload hash mismatch"
        )
    return event


def _verify_authority_rows(rows: list[tuple[Any, ...]]) -> tuple[bool, str]:
    previous = ""
    try:
        for index, row in enumerate(rows, start=1):
            event = _event_from_authority_row(row)
            if row[0] != index:
                # This branch is only a defensive check; the query is ordered by
                # seq and the explicit comparison makes gaps visible in output.
                return False, f"event sequence is not contiguous at {row[0]}"
            if row[12] != previous:
                return False, f"event row {row[0]} previous hash mismatch"
            expected = _record_hash(
                event,
                correlation_id=row[8],
                causation_id=row[9],
                prev_record_hash=previous,
            )
            if row[13] != expected:
                return False, f"event row {row[0]} record hash mismatch"
            previous = expected
    except EventAuthorityConsistencyError as exc:
        return False, str(exc)
    return True, ""


def _verify_projection_rows(rows: list[tuple[Any, ...]]) -> tuple[bool, str]:
    previous = ""
    try:
        for index, row in enumerate(rows, start=1):
            event = _event_from_projection_row(row)
            if row[0] != index:
                return False, f"projection sequence is not contiguous at {row[0]}"
            if row[13] != previous:
                return False, f"projection row {row[0]} previous hash mismatch"
            expected = _projection_hash(
                event_seq=row[1],
                event=event,
                correlation_id=row[9],
                causation_id=row[10],
                prev_record_hash=previous,
            )
            if row[14] != expected:
                return False, f"projection row {row[0]} record hash mismatch"
            previous = expected
    except EventAuthorityConsistencyError as exc:
        return False, str(exc)
    return True, ""


def _verify_consumer_rows(
    rows: list[tuple[Any, ...]], event_ids: dict[int, str]
) -> tuple[bool, str]:
    previous = ""
    for row in rows:
        if event_ids.get(int(row[2])) != row[3]:
            return False, f"consumer row {row[0]} references unknown event"
        if row[11] != previous:
            return False, f"consumer row {row[0]} previous hash mismatch"
        expected = _consumer_record_hash(
            consumer_id=row[1],
            event_seq=row[2],
            event_id=row[3],
            action=row[4],
            attempt=row[5],
            worker_id=row[6],
            at_ms=row[7],
            lease_until_ms=row[8],
            error_type=row[9],
            error_fingerprint=row[10],
            prev_record_hash=previous,
        )
        if row[12] != expected:
            return False, f"consumer row {row[0]} record hash mismatch"
        previous = expected
    return True, ""


def _authority_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    try:
        event_rows = connection.execute(
            "SELECT seq,event_id,event_type,event_version,timestamp,source,domain,"
            "criticality,correlation_id,causation_id,payload_json,repro_hash,"
            "prev_record_hash,record_hash FROM event_log ORDER BY seq"
        ).fetchall()
        consumer_rows = connection.execute(
            "SELECT journal_seq,consumer_id,event_seq,event_id,action,attempt,"
            "worker_id,at_ms,lease_until_ms,error_type,error_fingerprint,"
            "prev_record_hash,record_hash FROM event_consumer_journal "
            "ORDER BY journal_seq"
        ).fetchall()
        coordination = connection.execute(
            "SELECT COUNT(*),COALESCE(MAX(coordination_seq),0) "
            "FROM event_consumer_group_journal"
        ).fetchone()
    except sqlite3.Error as exc:
        raise EventAuthorityConsistencyError("event authority schema is incomplete") from exc

    event_chain_valid, event_chain_error = _verify_authority_rows(event_rows)
    event_ids = {int(row[0]): str(row[1]) for row in event_rows}
    consumer_chain_valid, consumer_chain_error = _verify_consumer_rows(
        consumer_rows, event_ids
    )
    consumer_id = DEFAULT_CONSUMER_ID
    consumer_records = [row for row in consumer_rows if row[1] == consumer_id]
    acknowledged = [
        int(row[2])
        for row in consumer_records
        if row[4] in {"ACKED", "DEAD_LETTERED"}
    ]
    latest_by_event: dict[int, tuple] = {}
    for row in consumer_records:
        latest_by_event[int(row[2])] = row
    historical_failed = [row for row in consumer_records if row[4] == "FAILED"]
    unresolved = [
        row for row in latest_by_event.values()
        if row[4] in {"FAILED", "DEAD_LETTERED"}
    ]
    unresolved_dead_letters = [
        row for row in unresolved if row[4] == "DEAD_LETTERED"
    ]
    head = event_rows[-1] if event_rows else None
    return {
        "path": str(Path(connection.execute("PRAGMA database_list").fetchone()[2]).resolve()),
        "event_records": len(event_rows),
        "event_max_seq": int(head[0]) if head else 0,
        "head": {
            "seq": int(head[0]),
            "event_id": str(head[1]),
            "repro_hash": str(head[11]),
            "record_hash": str(head[13]),
        }
        if head
        else None,
        "event_chain_valid": event_chain_valid,
        "event_chain_error": event_chain_error or None,
        "consumer_journal_records": len(consumer_rows),
        "consumer_chain_valid": consumer_chain_valid,
        "consumer_chain_error": consumer_chain_error or None,
        "consumer": {
            "id": consumer_id,
            "journal_records": len(consumer_records),
            "offset": max(acknowledged, default=0),
            # ``failed_deliveries`` is the current unresolved terminal state.
            # Historical failures remain visible separately and are never
            # deleted; an explicit append-only redrive may move an event to
            # ACKED after its projection has been independently proven exact.
            "failed_deliveries": len(unresolved),
            "unresolved_dead_letters": len(unresolved_dead_letters),
            "historical_failed_deliveries": len(historical_failed),
            "last_action": consumer_records[-1][4] if consumer_records else None,
        },
        "coordination_journal": {
            "records": int(coordination[0]),
            "max_seq": int(coordination[1]),
        },
        "_rows": event_rows,
    }


def _projection_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    try:
        rows = connection.execute(
            "SELECT projection_seq,event_seq,event_id,event_type,event_version,"
            "timestamp,source,domain,criticality,correlation_id,causation_id,"
            "payload_json,repro_hash,prev_record_hash,record_hash "
            "FROM projected_events ORDER BY projection_seq"
        ).fetchall()
    except sqlite3.Error as exc:
        raise EventAuthorityConsistencyError("projection schema is incomplete") from exc
    chain_valid, chain_error = _verify_projection_rows(rows)
    head = rows[-1] if rows else None
    return {
        "event_records": len(rows),
        "event_max_seq": int(head[1]) if head else 0,
        "projection_max_seq": int(head[0]) if head else 0,
        "head": {
            "projection_seq": int(head[0]),
            "event_seq": int(head[1]),
            "event_id": str(head[2]),
            "repro_hash": str(head[12]),
            "record_hash": str(head[14]),
        }
        if head
        else None,
        "chain_valid": chain_valid,
        "chain_error": chain_error or None,
        "rows": rows,
    }


def inspect_event_authority_consistency(
    *,
    event_path: str,
    projection_path: str,
    consumer_id: str = DEFAULT_CONSUMER_ID,
    require_consumer_offset: bool = True,
    require_consumer_healthy: bool = True,
) -> dict[str, Any]:
    """Compare the two authorities without opening either database for writes."""
    if consumer_id != DEFAULT_CONSUMER_ID:
        raise ValueError("unsupported projection consumer")
    if not isinstance(require_consumer_offset, bool) or not isinstance(
        require_consumer_healthy, bool
    ):
        raise TypeError("consumer consistency controls must be booleans")
    reasons: list[str] = []
    authority: dict[str, Any] = {}
    projection: dict[str, Any] = {}

    try:
        event_connection = _connect_read_only(event_path)
    except (EventAuthorityConsistencyError, sqlite3.Error) as exc:
        return {
            "schema_version": 1,
            "mode": "READ_ONLY_AUTHORITY_PROJECTION_CONSISTENCY",
            "verdict": "UNAVAILABLE",
            "reasons": ["EVENT_AUTHORITY_UNAVAILABLE"],
            "error_type": type(exc).__name__,
            "paths": {"event_authority": event_path, "projection": projection_path},
            "safety": {"database_writes": False, "order_submission": False},
        }
    try:
        try:
            authority = _authority_snapshot(event_connection)
        except (EventAuthorityConsistencyError, sqlite3.Error) as exc:
            reasons.append("EVENT_AUTHORITY_UNREADABLE")
            authority = {"error_type": type(exc).__name__}
    finally:
        event_connection.close()

    try:
        projection_connection = _connect_read_only(projection_path)
    except (EventAuthorityConsistencyError, sqlite3.Error) as exc:
        reasons.append("PROJECTION_UNAVAILABLE")
        projection = {"error_type": type(exc).__name__}
    else:
        try:
            try:
                projection = _projection_snapshot(projection_connection)
            except (EventAuthorityConsistencyError, sqlite3.Error) as exc:
                reasons.append("PROJECTION_UNREADABLE")
                projection = {"error_type": type(exc).__name__}
        finally:
            projection_connection.close()

    authority_rows = authority.get("event_records", 0)
    projection_rows = projection.get("event_records", 0)
    authority_max = authority.get("event_max_seq", 0)
    projection_max = projection.get("event_max_seq", 0)

    if authority.get("event_chain_valid") is False:
        reasons.append("EVENT_AUTHORITY_CHAIN_INVALID")
    if authority.get("consumer_chain_valid") is False:
        reasons.append("CONSUMER_JOURNAL_CHAIN_INVALID")
    if projection.get("chain_valid") is False:
        reasons.append("PROJECTION_CHAIN_INVALID")

    authority_ids = {
        int(row[0]): (str(row[1]), str(row[11]))
        for row in authority.get("_rows", ())
    }
    # The raw authority rows are intentionally kept internal to the comparison
    # and removed from the public report below; projection rows are treated the
    # same way.  The snapshots above remain compact and human-readable.
    projection_ids = {
        int(row[1]): (str(row[2]), str(row[12]))
        for row in projection.get("rows", ())
    }
    common = sorted(set(authority_ids) & set(projection_ids))
    mismatches = [seq for seq in common if authority_ids[seq] != projection_ids[seq]]
    if mismatches:
        reasons.append("PROJECTION_PREFIX_MISMATCH")
    if projection_rows > authority_rows or projection_max > authority_max:
        reasons.append("PROJECTION_AHEAD_OF_AUTHORITY")
    elif projection_rows < authority_rows or projection_max < authority_max:
        reasons.append("PROJECTION_BEHIND_AUTHORITY")
    if authority_rows == projection_rows and authority_max == projection_max:
        if authority.get("head") and projection.get("head"):
            if authority["head"]["event_id"] != projection["head"]["event_id"]:
                reasons.append("AUTHORITY_PROJECTION_HEAD_MISMATCH")

    consumer = authority.get("consumer") or {}
    offset = consumer.get("offset", 0)
    if require_consumer_offset and projection_max != offset:
        reasons.append(
            "PROJECTION_AHEAD_OF_CONSUMER_OFFSET"
            if projection_max > offset
            else "CONSUMER_OFFSET_AHEAD_OF_PROJECTION"
        )
    if require_consumer_healthy and consumer.get("failed_deliveries", 0):
        reasons.append("CONSUMER_DELIVERY_FAILED")

    unique_reasons = list(dict.fromkeys(reasons))
    verdict = "PASS" if not unique_reasons else "BREACH"
    public_projection = {
        key: value for key, value in projection.items() if key != "rows"
    }
    # Keep only summary data in the artifact.  The full rows remain in the
    # append-only authorities and are never copied into a report.
    authority_public = {
        key: value for key, value in authority.items() if key != "_rows"
    }
    return {
        "schema_version": 1,
        "mode": "READ_ONLY_AUTHORITY_PROJECTION_CONSISTENCY",
        "verdict": verdict,
        "reasons": unique_reasons,
        "paths": {"event_authority": event_path, "projection": projection_path},
        "authority": authority_public,
        "projection": public_projection,
        "comparison": {
            "common_event_sequences": len(common),
            "prefix_mismatch_count": len(mismatches),
            "first_prefix_mismatch_seq": mismatches[0] if mismatches else None,
            "authority_event_records": authority_rows,
            "projection_event_records": projection_rows,
            "authority_max_seq": authority_max,
            "projection_max_seq": projection_max,
            "consumer_offset": offset,
        },
        "safety": {
            "database_writes": False,
            "order_submission": False,
            "broker_connectivity": False,
            "credential_access": False,
            "retention_mutation": False,
        },
    }


__all__ = [
    "DEFAULT_CONSUMER_ID",
    "EventAuthorityConsistencyError",
    "inspect_event_authority_consistency",
]
