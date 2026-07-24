"""Export a create-once external anchor for the verified domain-event authority.

This tool never edits or compacts the authority. It snapshots SQLite through a
read-only connection, verifies all three hash chains on the isolated snapshot,
and writes a deterministic commitment file with create-exclusive semantics.
Filesystem storage is not claimed to be WORM or externally signed.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from typing import Callable

from services.monitoring.domain_event_runtime import DOMAIN_EVENT_DB
from services.monitoring.event_bus import DurableEventBus, EventAuthorityCommitment
from services.governance.external_evidence import (
    ContinuityAssessment,
    ContinuityObservation,
    evaluate_continuity,
)
from tools.cache_io import atomic_write_json


DESTINATION_ENV = "QUANTOS_EVENT_SEAL_DEST"
DEFAULT_EVIDENCE = os.path.join("evidence", "domain_event_seal_latest.json")


class DomainEventSealError(RuntimeError):
    """A verified external authority anchor could not be created or confirmed."""


def _canonical_bytes(document: dict) -> bytes:
    return (
        json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_destination(
    *, repo_root: Path, destination_root: Path, allow_local_destination: bool
) -> tuple[Path, str]:
    if not destination_root.is_absolute():
        raise DomainEventSealError("seal destination must be absolute")
    resolved_repo = repo_root.resolve()
    destination = destination_root.resolve(strict=False)
    if _is_within(destination, resolved_repo):
        raise DomainEventSealError("seal destination must be outside the workspace")
    raw = os.fspath(destination_root)
    if raw.startswith("\\\\") or raw.startswith("//"):
        destination_class = "OFF_HOST_UNC"
    elif (
        resolved_repo.drive
        and destination.drive
        and resolved_repo.drive.casefold() != destination.drive.casefold()
    ):
        destination_class = "SEPARATE_VOLUME"
    else:
        destination_class = "LOCAL_ONLY"
    if destination_class == "LOCAL_ONLY" and not allow_local_destination:
        raise DomainEventSealError(
            "event seal requires a separate volume or UNC destination"
        )
    return destination, destination_class


def _evaluate_snapshot(event_path: Path, evaluator: Callable[[DurableEventBus], object]):
    if not event_path.is_file() or event_path.is_symlink():
        raise DomainEventSealError("event authority is unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="quantos-event-seal-") as temporary:
            snapshot = Path(temporary) / "authority.db"
            source_uri = event_path.resolve().as_uri() + "?mode=ro"
            with closing(
                sqlite3.connect(source_uri, uri=True, timeout=30)
            ) as source_connection, closing(
                sqlite3.connect(snapshot)
            ) as snapshot_connection:
                source_connection.backup(snapshot_connection)
            bus = DurableEventBus(str(snapshot), create=False)
            try:
                return evaluator(bus)
            finally:
                bus.close()
    except DomainEventSealError:
        raise
    except Exception as exc:
        raise DomainEventSealError(
            f"event authority checkpoint failed ({type(exc).__name__})"
        ) from exc


def _snapshot_commitment(event_path: Path) -> dict:
    return _evaluate_snapshot(
        event_path, lambda bus: bus.authority_commitment().to_dict()
    )


def verify_event_authority_seal(
    *, event_path: str | Path, anchor_path: str | Path
) -> dict:
    anchor = Path(anchor_path)
    if not anchor.is_file() or anchor.is_symlink():
        raise DomainEventSealError("event authority anchor is unavailable")
    try:
        anchor_bytes = anchor.read_bytes()
        document = json.loads(anchor_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise DomainEventSealError("event authority anchor is unreadable") from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "mode", "commitment"}
        or document.get("schema_version") != 1
        or document.get("mode") != "DOMAIN_EVENT_AUTHORITY_COMMITMENT"
        or _canonical_bytes(document) != anchor_bytes
    ):
        raise DomainEventSealError("event authority anchor contract is invalid")
    try:
        commitment = EventAuthorityCommitment.from_dict(document["commitment"])
    except (TypeError, ValueError) as exc:
        raise DomainEventSealError("event authority anchor commitment is invalid") from exc
    expected_name = f"event-authority-{commitment.overall_hash}.json"
    if anchor.name != expected_name:
        raise DomainEventSealError("event authority anchor filename is not content-addressed")
    extends = bool(
        _evaluate_snapshot(
            Path(event_path),
            lambda bus: bus.extends_authority_commitment(commitment),
        )
    )
    return {
        "schema_version": 1,
        "verdict": "PASS" if extends else "BREACH",
        "authority_extends_anchor": extends,
        "anchor_sha256": _sha256(anchor_bytes),
        "commitment": commitment.to_dict(),
    }


def export_event_authority_seal(
    *,
    repo_root: str | Path,
    event_path: str | Path,
    destination_root: str | Path,
    evidence_path: str | Path,
    now_ms: int | None = None,
    allow_local_destination: bool = False,
) -> dict:
    observed_at_ms = int(time.time() * 1_000) if now_ms is None else now_ms
    if isinstance(observed_at_ms, bool) or not isinstance(observed_at_ms, int):
        raise TypeError("now_ms must be an integer")
    if observed_at_ms < 0:
        raise ValueError("now_ms must be non-negative")
    root = Path(repo_root).resolve()
    authority = Path(event_path)
    if not authority.is_absolute():
        authority = root / authority
    commitment = _snapshot_commitment(authority)
    destination, destination_class = _validate_destination(
        repo_root=root,
        destination_root=Path(destination_root),
        allow_local_destination=allow_local_destination,
    )
    anchor_document = {
        "schema_version": 1,
        "mode": "DOMAIN_EVENT_AUTHORITY_COMMITMENT",
        "commitment": commitment,
    }
    anchor_bytes = _canonical_bytes(anchor_document)
    anchor_name = f"event-authority-{commitment['overall_hash']}.json"
    destination.mkdir(parents=True, exist_ok=True)
    anchor_path = destination / anchor_name
    anchor_created = False
    try:
        with anchor_path.open("xb") as output:
            output.write(anchor_bytes)
            output.flush()
            os.fsync(output.fileno())
        anchor_created = True
    except FileExistsError:
        try:
            existing = anchor_path.read_bytes()
        except OSError as exc:
            raise DomainEventSealError("existing anchor is unreadable") from exc
        if existing != anchor_bytes:
            raise DomainEventSealError(
                "create-once anchor collision or tampering detected"
            )

    result = {
        "schema_version": 1,
        "operation_verdict": "PASS",
        "institutional_seal_status": "PARTIAL_EXTERNAL_WORM_UNVERIFIED",
        "observed_at_ms": observed_at_ms,
        "data_provenance": "VERIFIED_DOMAIN_EVENT_AUTHORITY_SNAPSHOT",
        "authority_mutated": False,
        "destination_class": destination_class,
        "anchor_created": anchor_created,
        "anchor_path": os.fspath(anchor_path),
        "anchor_sha256": _sha256(anchor_bytes),
        "commitment": commitment,
        "external_signature_verified": False,
        "worm_retention_verified": False,
    }
    resolved_evidence = Path(evidence_path)
    resolved_evidence.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(os.fspath(resolved_evidence), result, indent=2)
    return result


def evaluate_event_seal_continuity(
    observations: tuple[ContinuityObservation, ...],
    *,
    now_ms: int,
    max_gap_ms: int,
    minimum_history_span_ms: int,
    minimum_passes: int,
    clean_lookback_ms: int,
) -> ContinuityAssessment:
    """Evaluate retained external-anchor receipts without touching the authority."""

    return evaluate_continuity(
        observations,
        now_ms=now_ms,
        max_gap_ms=max_gap_ms,
        minimum_history_span_ms=minimum_history_span_ms,
        minimum_passes=minimum_passes,
        clean_lookback_ms=clean_lookback_ms,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a verified create-once domain-event authority commitment"
    )
    parser.add_argument("--repo-root", default=os.getcwd())
    parser.add_argument("--event-path", default=DOMAIN_EVENT_DB)
    parser.add_argument("--dest", default=os.environ.get(DESTINATION_ENV))
    parser.add_argument("--evidence", default=DEFAULT_EVIDENCE)
    parser.add_argument("--verify-anchor")
    parser.add_argument("--allow-local-destination", action="store_true")
    args = parser.parse_args()
    if args.verify_anchor:
        result = verify_event_authority_seal(
            event_path=args.event_path,
            anchor_path=args.verify_anchor,
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["verdict"] == "PASS" else 1
    if not args.dest:
        print(f"{DESTINATION_ENV} is not configured", file=sys.stderr)
        return 2
    result = export_event_authority_seal(
        repo_root=args.repo_root,
        event_path=args.event_path,
        destination_root=args.dest,
        evidence_path=args.evidence,
        allow_local_destination=args.allow_local_destination,
    )
    print(json.dumps(result, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DomainEventSealError",
    "evaluate_event_seal_continuity",
    "export_event_authority_seal",
    "verify_event_authority_seal",
]
