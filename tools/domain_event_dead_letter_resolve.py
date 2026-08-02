"""Resolve projection dead letters only after an exact read-only proof.

The projection was rebuilt from the append-only event authority after an old
projection hash mismatch.  This tool does not rebuild or mutate that
projection.  It verifies that every currently unresolved dead-letter event is
already present at the exact authority/projection sequence, then appends a
new CLAIMED/ACKED pair to the consumer journal.  Historical FAILED and
DEAD_LETTERED rows remain untouched and therefore auditable.

No broker, credentials, order path, or retention mutation is reachable here.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

from services.monitoring.domain_event_runtime import (
    DOMAIN_EVENT_DB,
    DOMAIN_EVENT_PROJECTION_CONSUMER,
    DOMAIN_EVENT_PROJECTION_DB,
    open_domain_event_bus,
)
from services.monitoring.event_authority_consistency import (
    inspect_event_authority_consistency,
)
from tools.cache_io import atomic_write_json


DEFAULT_OUTPUT = os.path.join(
    "data", "cache", "domain_event_dead_letter_recovery.json"
)
DEFAULT_WORKER_ID = "approved-dead-letter-redrive"
DEFAULT_LEASE_MS = 60_000


def _read_projection_event_ids(
    projection_path: str, event_sequences: tuple[int, ...]
) -> dict[int, str]:
    if not event_sequences:
        return {}
    uri = f"{Path(projection_path).resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        placeholders = ",".join("?" for _ in event_sequences)
        rows = connection.execute(
            "SELECT event_seq,event_id FROM projected_events "
            f"WHERE event_seq IN ({placeholders})",
            event_sequences,
        ).fetchall()
        return {int(seq): str(event_id) for seq, event_id in rows}
    finally:
        connection.close()


def _base_report(
    *,
    event_path: str,
    projection_path: str,
    consumer_id: str,
    worker_id: str,
    preflight: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "APPROVED_APPEND_ONLY_DEAD_LETTER_REDRIVE",
        "readiness": "L2_DRY_RUN_ONLY",
        "event_authority": event_path,
        "projection": projection_path,
        "consumer_id": consumer_id,
        "worker_id": worker_id,
        "preflight": preflight,
        "safety": {
            "projection_mutated": False,
            "authority_events_mutated": False,
            "consumer_journal_append_only": True,
            "historical_failures_deleted": False,
            "retention_mutation": False,
            "order_submission": False,
            "broker_connectivity": False,
            "credential_access": False,
        },
    }


def resolve_dead_letters(
    *,
    event_path: str = DOMAIN_EVENT_DB,
    projection_path: str = DOMAIN_EVENT_PROJECTION_DB,
    consumer_id: str = DOMAIN_EVENT_PROJECTION_CONSUMER,
    worker_id: str = DEFAULT_WORKER_ID,
    now_ms: int | None = None,
    lease_ms: int = DEFAULT_LEASE_MS,
    check_only: bool = False,
) -> dict[str, Any]:
    """Redrive only unresolved dead letters whose projection is exact."""
    resolved_now = int(time.time() * 1000) if now_ms is None else now_ms
    if isinstance(resolved_now, bool) or not isinstance(resolved_now, int) or resolved_now < 0:
        raise ValueError("now_ms must be a non-negative integer")
    if isinstance(lease_ms, bool) or not isinstance(lease_ms, int) or lease_ms <= 0:
        raise ValueError("lease_ms must be a positive integer")
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("worker_id must be a non-empty string")

    preflight = inspect_event_authority_consistency(
        event_path=event_path,
        projection_path=projection_path,
        consumer_id=consumer_id,
        require_consumer_offset=False,
        require_consumer_healthy=False,
    )
    report = _base_report(
        event_path=event_path,
        projection_path=projection_path,
        consumer_id=consumer_id,
        worker_id=worker_id,
        preflight=preflight,
    )
    if preflight.get("verdict") != "PASS":
        return {
            **report,
            "verdict": "BLOCKED",
            "reason": "PREFLIGHT_AUTHORITY_PROJECTION_NOT_EXACT",
        }

    bus = open_domain_event_bus(event_path, create=False)
    try:
        with bus._lock:  # noqa: SLF001 - bounded append-only recovery transaction
            bus._refresh_integrity_locked(force=True)  # noqa: SLF001
            bus._refresh_consumer_integrity_locked()  # noqa: SLF001
            dead_rows = bus._c.execute(  # noqa: SLF001
                "SELECT dead.journal_seq,dead.event_seq,dead.event_id,dead.attempt "
                "FROM event_consumer_journal dead "
                "WHERE dead.consumer_id=? AND dead.action='DEAD_LETTERED' "
                "AND NOT EXISTS ("
                " SELECT 1 FROM event_consumer_journal newer"
                " WHERE newer.consumer_id=dead.consumer_id"
                " AND newer.event_seq=dead.event_seq"
                " AND newer.journal_seq>dead.journal_seq"
                ") ORDER BY dead.event_seq,dead.journal_seq",
                (consumer_id,),
            ).fetchall()
            event_sequences = tuple(int(row[1]) for row in dead_rows)
            projection_ids = _read_projection_event_ids(
                projection_path, event_sequences
            )
            missing_or_mismatched = [
                {
                    "event_seq": int(seq),
                    "event_id": str(event_id),
                    "projection_event_id": projection_ids.get(int(seq)),
                }
                for _journal_seq, seq, event_id, _attempt in dead_rows
                if projection_ids.get(int(seq)) != str(event_id)
            ]
            if missing_or_mismatched:
                return {
                    **report,
                    "verdict": "BLOCKED",
                    "reason": "PROJECTION_EVENT_MISSING_OR_MISMATCHED",
                    "events": missing_or_mismatched,
                }
            if check_only:
                return {
                    **report,
                    "verdict": "PASS",
                    "check_only": True,
                    "unresolved_dead_letters": len(dead_rows),
                    "events": [
                        {
                            "journal_seq": int(journal_seq),
                            "event_seq": int(seq),
                            "event_id": str(event_id),
                            "previous_attempt": int(attempt),
                        }
                        for journal_seq, seq, event_id, attempt in dead_rows
                    ],
                }

            bus._c.execute("BEGIN IMMEDIATE")  # noqa: SLF001
            resolved_events: list[dict[str, Any]] = []
            try:
                lease_until = int(resolved_now) + int(lease_ms)
                for journal_seq, event_seq, event_id, attempt in dead_rows:
                    latest = bus._latest_consumer_record_locked(  # noqa: SLF001
                        consumer_id, int(event_seq)
                    )
                    if latest is None or latest[4] != "DEAD_LETTERED":
                        raise RuntimeError(
                            "dead-letter state changed during recovery preflight"
                        )
                    next_attempt = int(attempt) + 1
                    _claim_sequence, claim_head = bus._append_consumer_record_locked(  # noqa: SLF001
                        consumer_id=consumer_id,
                        event_seq=int(event_seq),
                        event_id=str(event_id),
                        action="CLAIMED",
                        attempt=next_attempt,
                        worker_id=worker_id,
                        at_ms=int(resolved_now),
                        lease_until_ms=lease_until,
                    )
                    ack_sequence, ack_head = bus._append_consumer_record_locked(  # noqa: SLF001
                        consumer_id=consumer_id,
                        event_seq=int(event_seq),
                        event_id=str(event_id),
                        action="ACKED",
                        attempt=next_attempt,
                        worker_id=worker_id,
                        at_ms=int(resolved_now),
                        lease_until_ms=lease_until,
                        prev_record_hash=claim_head,
                    )
                    resolved_events.append(
                        {
                            "journal_seq": int(journal_seq),
                            "event_seq": int(event_seq),
                            "event_id": str(event_id),
                            "previous_attempt": int(attempt),
                            "resolution_attempt": next_attempt,
                        }
                    )
                if resolved_events:
                    staged = bus._stage_frontier_locked(  # noqa: SLF001
                        "consumer",
                        last_sequence=ack_sequence,
                        last_record_hash=ack_head,
                        row_increment=2 * len(resolved_events),
                    )
                    bus._commit_staged_locked({"consumer": staged})  # noqa: SLF001
                else:
                    bus._commit_staged_locked({})  # noqa: SLF001
            except Exception:
                if bus._c.in_transaction:  # noqa: SLF001
                    bus._c.rollback()  # noqa: SLF001
                raise
            end_offset = bus._consumer_offset_locked(consumer_id)  # noqa: SLF001
    finally:
        bus.close()

    return {
        **report,
        "verdict": "PASS",
        "check_only": False,
        "resolved_at_ms": int(resolved_now),
        "resolved_count": len(resolved_events),
        "end_offset": int(end_offset),
        "events": resolved_events,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-path", default=DOMAIN_EVENT_DB)
    parser.add_argument("--projection-path", default=DOMAIN_EVENT_PROJECTION_DB)
    parser.add_argument("--consumer-id", default=DOMAIN_EVENT_PROJECTION_CONSUMER)
    parser.add_argument("--worker-id", default=DEFAULT_WORKER_ID)
    parser.add_argument("--now-ms", type=int)
    parser.add_argument("--lease-ms", type=int, default=DEFAULT_LEASE_MS)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        report = resolve_dead_letters(
            event_path=args.event_path,
            projection_path=args.projection_path,
            consumer_id=args.consumer_id,
            worker_id=args.worker_id,
            now_ms=args.now_ms,
            lease_ms=args.lease_ms,
            check_only=args.check_only,
        )
    except Exception as exc:
        report = {
            "schema_version": 1,
            "mode": "APPROVED_APPEND_ONLY_DEAD_LETTER_REDRIVE",
            "verdict": "BREACH",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "safety": {
                "projection_mutated": False,
                "authority_events_mutated": False,
                "consumer_journal_append_only": True,
                "historical_failures_deleted": False,
                "retention_mutation": False,
                "order_submission": False,
                "broker_connectivity": False,
                "credential_access": False,
            },
        }
    atomic_write_json(args.output, report, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"-> {args.output}")
    return 0 if report.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DEFAULT_OUTPUT", "resolve_dead_letters"]
