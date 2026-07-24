"""Fast-forward the projection consumer after an approved full projection rebuild.

The projection candidate is already a verified copy of the complete event
authority.  Replaying it through ``DomainEventProjection.apply`` one event at a
time would re-verify the full hash chain for every row (quadratic work).  This
tool therefore performs only the missing *consumer checkpoints* in one
append-only transaction, after a read-only authority/projection comparison has
proved that every event is already present and identical in the projection.

It never edits the projection, authority events, or historical failed/dead
letter rows.  It cannot submit orders, access credentials, or connect to a
broker.  A non-empty checkpoint for an event beyond the current offset is
treated as a breach rather than guessed over.
"""
from __future__ import annotations

import argparse
import json
import os
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
    "data", "cache", "domain_event_projection_consumer_recovery.json"
)


def recover_projection_consumer(
    *,
    event_path: str = DOMAIN_EVENT_DB,
    projection_path: str = DOMAIN_EVENT_PROJECTION_DB,
    consumer_id: str = DOMAIN_EVENT_PROJECTION_CONSUMER,
    worker_id: str = "approved-projection-recovery",
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Append missing CLAIMED/ACKED checkpoints for an already verified projection."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer")

    proof = inspect_event_authority_consistency(
        event_path=event_path,
        projection_path=projection_path,
        consumer_id=consumer_id,
        require_consumer_offset=False,
        require_consumer_healthy=False,
    )
    base: dict[str, Any] = {
        "schema_version": 1,
        "mode": "APPROVED_APPEND_ONLY_PROJECTION_CONSUMER_RECOVERY",
        "event_authority": event_path,
        "projection": projection_path,
        "consumer_id": consumer_id,
        "safety": {
            "projection_mutated": False,
            "authority_events_mutated": False,
            "consumer_journal_append_only": True,
            "historical_failures_deleted": False,
            "order_submission": False,
            "broker_connectivity": False,
            "credential_access": False,
        },
        "preflight": proof,
    }
    if proof.get("verdict") != "PASS":
        return {**base, "verdict": "BLOCKED", "reason": "PREFLIGHT_CONSISTENCY_FAILED"}

    authority_max = int((proof.get("authority") or {}).get("event_max_seq") or 0)
    projection_max = int((proof.get("projection") or {}).get("projection_max_seq") or 0)
    if authority_max != projection_max:
        return {**base, "verdict": "BLOCKED", "reason": "PROJECTION_NOT_AT_AUTHORITY_HEAD"}

    bus = open_domain_event_bus(event_path, create=False)
    appended = 0
    start_offset = 0
    end_offset = 0
    try:
        with bus._lock:  # noqa: SLF001 - controlled recovery transaction
            bus._c.execute("BEGIN IMMEDIATE")  # noqa: SLF001
            try:
                bus._refresh_integrity_locked(force=True)  # noqa: SLF001
                bus._refresh_consumer_integrity_locked()  # noqa: SLF001
                start_offset = bus._consumer_offset_locked(consumer_id)  # noqa: SLF001
                rows = bus._c.execute(  # noqa: SLF001
                    "SELECT seq,event_id FROM event_log WHERE seq>? ORDER BY seq",
                    (start_offset,),
                ).fetchall()
                existing = bus._c.execute(  # noqa: SLF001
                    "SELECT event_seq,action FROM event_consumer_journal "
                    "WHERE consumer_id=? AND event_seq>? ORDER BY event_seq,journal_seq",
                    (consumer_id, start_offset),
                ).fetchall()
                if existing:
                    raise RuntimeError(
                        "consumer has an unresolved checkpoint beyond its offset"
                    )
                if rows and int(rows[-1][0]) != authority_max:
                    raise RuntimeError("authority sequence is not contiguous at its head")
                lease_until = int(now_ms) + 60_000
                for event_seq, event_id in rows:
                    bus._append_consumer_record_locked(  # noqa: SLF001
                        consumer_id=consumer_id,
                        event_seq=int(event_seq),
                        event_id=str(event_id),
                        action="CLAIMED",
                        attempt=1,
                        worker_id=worker_id,
                        at_ms=int(now_ms),
                        lease_until_ms=lease_until,
                    )
                    bus._append_consumer_record_locked(  # noqa: SLF001
                        consumer_id=consumer_id,
                        event_seq=int(event_seq),
                        event_id=str(event_id),
                        action="ACKED",
                        attempt=1,
                        worker_id=worker_id,
                        at_ms=int(now_ms),
                        lease_until_ms=lease_until,
                    )
                    appended += 1
                bus._c.commit()  # noqa: SLF001
                bus._record_connection_state_locked()  # noqa: SLF001
            except Exception:
                if bus._c.in_transaction:  # noqa: SLF001
                    bus._c.rollback()  # noqa: SLF001
                raise
        end_offset = bus.consumer_offset(consumer_id)
    finally:
        bus.close()

    return {
        **base,
        "verdict": "PASS",
        "start_offset": start_offset,
        "end_offset": end_offset,
        "authority_head": authority_max,
        "checkpoints_appended": appended,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-path", default=DOMAIN_EVENT_DB)
    parser.add_argument("--projection-path", default=DOMAIN_EVENT_PROJECTION_DB)
    parser.add_argument("--consumer-id", default=DOMAIN_EVENT_PROJECTION_CONSUMER)
    parser.add_argument("--worker-id", default="approved-projection-recovery")
    parser.add_argument("--now-ms", type=int)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        report = recover_projection_consumer(
            event_path=args.event_path,
            projection_path=args.projection_path,
            consumer_id=args.consumer_id,
            worker_id=args.worker_id,
            now_ms=args.now_ms,
        )
    except Exception as exc:
        report = {
            "schema_version": 1,
            "mode": "APPROVED_APPEND_ONLY_PROJECTION_CONSUMER_RECOVERY",
            "verdict": "BREACH",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "safety": {
                "projection_mutated": False,
                "authority_events_mutated": False,
                "consumer_journal_append_only": True,
                "historical_failures_deleted": False,
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


__all__ = ["DEFAULT_OUTPUT", "recover_projection_consumer"]
