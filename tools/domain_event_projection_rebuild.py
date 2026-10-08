"""Build a verified projection candidate without touching the live authority.

This is an explicit L2 recovery aid.  It copies the event authority into a
temporary SQLite file, replays that copy into a temporary projection, verifies
the result, and only then publishes a *new* candidate file.  The canonical
projection is never replaced automatically and the source consumer journal is
never advanced.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from services.monitoring.domain_event_runtime import (
    DOMAIN_EVENT_DB,
    DOMAIN_EVENT_PROJECTION_DB,
    open_domain_event_bus,
)
from services.monitoring.event_authority_consistency import (
    inspect_event_authority_consistency,
)
from services.monitoring.event_projection import (
    DomainEventProjection,
    _projection_hash,
)
from tools.cache_io import atomic_write_json


DEFAULT_OUTPUT = os.path.join(
    "data", "cache", "domain_event_projection_candidate.db"
)
DEFAULT_REPORT = os.path.join(
    "data", "cache", "domain_event_projection_rebuild.json"
)


def _backup_authority(source_path: str, target_path: str) -> None:
    source_uri = f"{Path(source_path).resolve().as_uri()}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _materialize_projection(
    projection: DomainEventProjection, target_path: str
) -> None:
    """Materialize an in-memory projection into a fresh, verified SQLite file.

    ``sqlite3.Connection.backup`` can report success for an in-memory source
    while leaving a destination whose b-tree fails ``PRAGMA integrity_check``
    under larger append-only histories.  Recreate the schema through the
    projection boundary and copy rows in one transaction instead; the target
    is then checked before it is ever eligible for promotion.
    """
    target = DomainEventProjection(
        target_path,
        schema_registry=CANONICAL_EVENT_SCHEMAS,
    )
    try:
        rows = projection._c.execute(  # noqa: SLF001 - controlled local snapshot
            "SELECT event_seq,event_id,event_type,event_version,timestamp,source,"
            "domain,criticality,correlation_id,causation_id,payload_json,repro_hash,"
            "prev_record_hash,record_hash FROM projected_events ORDER BY projection_seq"
        ).fetchall()
        target._c.executemany(  # noqa: SLF001 - controlled local snapshot
            "INSERT INTO projected_events"
            " (event_seq,event_id,event_type,event_version,timestamp,source,domain,"
            " criticality,correlation_id,causation_id,payload_json,repro_hash,"
            " prev_record_hash,record_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        target._c.commit()  # noqa: SLF001 - controlled local snapshot
        if not target.chain_valid():
            raise RuntimeError("materialized projection chain is invalid")
        integrity = target._c.execute("PRAGMA integrity_check").fetchone()[0]  # noqa: SLF001
        if integrity != "ok":
            raise RuntimeError(f"materialized projection sqlite integrity failed: {integrity}")
    finally:
        target.close()


def _projection_replay(
    *,
 authority_copy: str,
 ) -> tuple[DomainEventProjection, dict[str, Any]]:
    bus = open_domain_event_bus(authority_copy, create=False)
    projection = DomainEventProjection(
        ":memory:",
        schema_registry=CANONICAL_EVENT_SCHEMAS,
    )
    try:
        records = bus.records()
        # ``DomainEventProjection.apply`` intentionally verifies the complete
        # chain before every append.  That is ideal for runtime delivery but is
        # quadratic for a rebuild.  A rebuild has one trusted, already-verified
        # source sequence, so populate the new in-memory table in one batch and
        # run the same full verifier once at the end.
        previous = ""
        rows: list[tuple[Any, ...]] = []
        for record in records:
            event = record["event"]
            projection._validate_event_schema(event)  # noqa: SLF001
            event_seq = int(record["seq"])
            digest = _projection_hash(
                event_seq=event_seq,
                event=event,
                correlation_id=record["correlation_id"],
                causation_id=record["causation_id"],
                prev_record_hash=previous,
            )
            rows.append(
                (
                    event_seq,
                    event.event_id,
                    event.event_type,
                    event.event_version,
                    event.timestamp,
                    event.source,
                    event.domain,
                    event.criticality,
                    record["correlation_id"],
                    record["causation_id"],
                    json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
                    event.reproducibility_hash,
                    previous,
                    digest,
                )
            )
            previous = digest
        projection._c.executemany(  # noqa: SLF001
            "INSERT INTO projected_events"
            " (event_seq,event_id,event_type,event_version,timestamp,source,domain,"
            " criticality,correlation_id,causation_id,payload_json,repro_hash,"
            " prev_record_hash,record_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        projection._c.commit()  # noqa: SLF001
        if not projection.chain_valid():
            raise RuntimeError("rebuilt projection chain is invalid")
        projected_records = projection.records()
        if len(projected_records) != len(records):
            raise RuntimeError("rebuilt projection record count mismatch")
        for source, rebuilt in zip(records, projected_records, strict=True):
            if (
                int(source["seq"]) != int(rebuilt["event_seq"])
                or source["event"].event_id != rebuilt["event"].event_id
            ):
                raise RuntimeError("rebuilt projection event identity mismatch")
        return projection, {
            "authority_records": len(records),
            "projection_records": len(projected_records),
            "projected": len(projected_records),
            "idempotent_replays": 0,
        }
    except Exception:
        projection.close()
        raise
    finally:
        bus.close()
        gc.collect()


def rebuild_projection_candidate(
    *,
    event_path: str = DOMAIN_EVENT_DB,
    output_projection_path: str = DEFAULT_OUTPUT,
    report_path: str = DEFAULT_REPORT,
) -> dict[str, Any]:
    """Create a new candidate projection, refusing to overwrite an existing file."""
    base = {
        "schema_version": 1,
        "mode": "L2_EXPLICIT_PROJECTION_REBUILD_CANDIDATE",
        "source_authority": event_path,
        "candidate_projection": output_projection_path,
        "canonical_projection": DOMAIN_EVENT_PROJECTION_DB,
        "safety": {
            "canonical_projection_replaced": False,
            "source_authority_mutated": False,
            "source_consumer_journal_advanced": False,
            "order_submission": False,
            "broker_connectivity": False,
            "credential_access": False,
        },
    }
    if not os.path.isfile(event_path):
        document = {**base, "verdict": "UNAVAILABLE", "reason": "EVENT_AUTHORITY_UNAVAILABLE"}
        atomic_write_json(report_path, document, indent=2)
        return document
    if os.path.exists(output_projection_path):
        document = {**base, "verdict": "BLOCKED", "reason": "OUTPUT_ALREADY_EXISTS"}
        atomic_write_json(report_path, document, indent=2)
        return document

    parent = os.path.dirname(output_projection_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="quantos-event-rebuild-") as temp_dir:
        authority_copy = os.path.join(temp_dir, "authority-copy.db")
        projection = None
        output_created = False
        try:
            _backup_authority(event_path, authority_copy)
            projection, replay = _projection_replay(
                authority_copy=authority_copy,
            )
            _materialize_projection(projection, output_projection_path)
            output_created = True
            projection.close()
            projection = None
            gc.collect()
            consistency = inspect_event_authority_consistency(
                event_path=event_path,
                projection_path=output_projection_path,
                require_consumer_offset=False,
                require_consumer_healthy=False,
            )
            if consistency.get("verdict") != "PASS":
                if output_created:
                    os.unlink(output_projection_path)
                    output_created = False
                document = {
                    **base,
                    "verdict": "BREACH",
                    "reason": "CANDIDATE_CONSISTENCY_FAILED",
                    "replay": replay,
                    "consistency": consistency,
                }
                atomic_write_json(report_path, document, indent=2)
                return document
            document = {
                **base,
                "verdict": "PASS",
                "replay": replay,
                "consistency": consistency,
                "promotion": "CANDIDATE_CREATED_REQUIRES_HUMAN_REVIEW",
            }
        except Exception as exc:
            if projection is not None:
                projection.close()
                projection = None
            if output_created and os.path.exists(output_projection_path):
                try:
                    os.unlink(output_projection_path)
                except OSError:
                    pass
            document = {
                **base,
                "verdict": "BREACH",
                "reason": "REBUILD_FAILED",
                "error_type": type(exc).__name__,
            }
    atomic_write_json(report_path, document, indent=2)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a non-canonical domain-event projection candidate"
    )
    parser.add_argument("--event-path", default=DOMAIN_EVENT_DB)
    parser.add_argument("--output-projection", default=DEFAULT_OUTPUT)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    document = rebuild_projection_candidate(
        event_path=args.event_path,
        output_projection_path=args.output_projection,
        report_path=args.report,
    )
    print(json.dumps(document, ensure_ascii=False, indent=2))
    print(f"-> {args.report}")
    return 0 if document["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_OUTPUT",
    "DEFAULT_REPORT",
    "rebuild_projection_candidate",
]
