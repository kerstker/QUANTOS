"""Export a create-once external anchor for artifact-admission authority.

The authority is copied through SQLite's backup API, verified from the isolated
snapshot, and reduced to a content-addressed chain commitment.  A separate
volume is useful independent evidence, but is not represented as WORM storage
or as an externally signed receipt.
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

from services.governance.artifact_admission_store import (
    ARTIFACT_ADMISSION_STORE_KIND,
    ARTIFACT_ADMISSION_STORE_SCHEMA_VERSION,
    ArtifactAdmissionStore,
)
from tools.cache_io import atomic_write_json
from tools.domain_event_seal import (
    DomainEventSealError,
    _canonical_bytes,
    _assert_destination_boundary,
    _sha256,
    _validate_destination,
)


DESTINATION_ENV = "QUANTOS_EVENT_SEAL_DEST"
DEFAULT_AUTHORITY = os.path.join("data", "cache", "artifact_admission.db")
DEFAULT_EVIDENCE = os.path.join(
    "evidence", "artifact_admission_seal_latest.json"
)


class ArtifactAdmissionSealError(RuntimeError):
    """The authority could not be safely anchored or verified."""


def _overall_hash(commitment: dict) -> str:
    body = {
        key: value
        for key, value in commitment.items()
        if key != "overall_hash"
    }
    return hashlib.sha256(_canonical_bytes(body)).hexdigest()


def _validate_commitment(value: object) -> dict:
    required = {
        "schema_version",
        "store_kind",
        "store_schema_version",
        "event_count",
        "event_head_hash",
        "decision_count",
        "registry_snapshot_count",
        "overall_hash",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ArtifactAdmissionSealError("authority commitment contract is invalid")
    if (
        value.get("schema_version") != 1
        or value.get("store_kind") != ARTIFACT_ADMISSION_STORE_KIND
        or value.get("store_schema_version")
        != ARTIFACT_ADMISSION_STORE_SCHEMA_VERSION
    ):
        raise ArtifactAdmissionSealError("authority commitment identity is invalid")
    for field in ("event_count", "decision_count", "registry_snapshot_count"):
        if type(value.get(field)) is not int or value[field] < 0:
            raise ArtifactAdmissionSealError("authority commitment count is invalid")
    head = value.get("event_head_hash")
    if value["event_count"] == 0:
        if head != "":
            raise ArtifactAdmissionSealError("empty authority has a chain head")
    elif (
        not isinstance(head, str)
        or len(head) != 64
        or any(char not in "0123456789abcdef" for char in head)
    ):
        raise ArtifactAdmissionSealError("authority commitment head is invalid")
    if value.get("overall_hash") != _overall_hash(value):
        raise ArtifactAdmissionSealError("authority commitment digest is invalid")
    return value


def _snapshot_state(authority_path: Path) -> tuple[dict, tuple[str, ...]]:
    if not authority_path.is_file() or authority_path.is_symlink():
        raise ArtifactAdmissionSealError("artifact-admission authority is unavailable")
    try:
        with tempfile.TemporaryDirectory(
            prefix="quantos-artifact-admission-seal-"
        ) as temporary:
            snapshot = Path(temporary) / "authority.db"
            source_uri = authority_path.resolve().as_uri() + "?mode=ro"
            with closing(
                sqlite3.connect(source_uri, uri=True, timeout=30)
            ) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
            store = ArtifactAdmissionStore(str(snapshot), create=False)
            try:
                if not store.chain_valid():
                    raise ArtifactAdmissionSealError(
                        "artifact-admission authority chain is invalid"
                    )
            finally:
                store.close()
            with closing(sqlite3.connect(snapshot)) as connection:
                event_hashes = tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT event_hash FROM artifact_admission_authority_events "
                        "ORDER BY event_seq"
                    )
                )
                decision_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM artifact_admission_decisions"
                    ).fetchone()[0]
                )
                registry_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM artifact_admission_registry_snapshots"
                    ).fetchone()[0]
                )
    except ArtifactAdmissionSealError:
        raise
    except Exception as exc:
        raise ArtifactAdmissionSealError(
            f"artifact-admission checkpoint failed ({type(exc).__name__})"
        ) from exc
    commitment = {
        "schema_version": 1,
        "store_kind": ARTIFACT_ADMISSION_STORE_KIND,
        "store_schema_version": ARTIFACT_ADMISSION_STORE_SCHEMA_VERSION,
        "event_count": len(event_hashes),
        "event_head_hash": event_hashes[-1] if event_hashes else "",
        "decision_count": decision_count,
        "registry_snapshot_count": registry_count,
    }
    commitment["overall_hash"] = _overall_hash(commitment)
    return commitment, event_hashes


def verify_artifact_admission_seal(
    *, authority_path: str | Path, anchor_path: str | Path
) -> dict:
    anchor = Path(anchor_path)
    if not anchor.is_file() or anchor.is_symlink():
        raise ArtifactAdmissionSealError("artifact-admission anchor is unavailable")
    try:
        anchor_bytes = anchor.read_bytes()
        document = json.loads(anchor_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactAdmissionSealError(
            "artifact-admission anchor is unreadable"
        ) from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "mode", "commitment"}
        or document.get("schema_version") != 1
        or document.get("mode") != "ARTIFACT_ADMISSION_AUTHORITY_COMMITMENT"
        or _canonical_bytes(document) != anchor_bytes
    ):
        raise ArtifactAdmissionSealError(
            "artifact-admission anchor contract is invalid"
        )
    commitment = _validate_commitment(document["commitment"])
    expected = (
        "artifact-admission-authority-"
        f"{commitment['overall_hash']}.json"
    )
    if anchor.name != expected:
        raise ArtifactAdmissionSealError(
            "artifact-admission anchor filename is not content-addressed"
        )
    current, event_hashes = _snapshot_state(Path(authority_path))
    count = commitment["event_count"]
    extends = bool(
        current["event_count"] >= count
        and current["decision_count"] >= commitment["decision_count"]
        and current["registry_snapshot_count"]
        >= commitment["registry_snapshot_count"]
        and (
            count == 0
            or event_hashes[count - 1] == commitment["event_head_hash"]
        )
    )
    return {
        "schema_version": 1,
        "verdict": "PASS" if extends else "BREACH",
        "authority_extends_anchor": extends,
        "anchor_sha256": _sha256(anchor_bytes),
        "commitment": commitment,
    }


def export_artifact_admission_seal(
    *,
    repo_root: str | Path,
    authority_path: str | Path,
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
    authority = Path(authority_path)
    if not authority.is_absolute():
        authority = root / authority
    commitment, _event_hashes = _snapshot_state(authority)
    try:
        destination, destination_class = _validate_destination(
            repo_root=root,
            destination_root=Path(destination_root),
            allow_local_destination=allow_local_destination,
        )
    except DomainEventSealError as exc:
        raise ArtifactAdmissionSealError(str(exc)) from exc
    document = {
        "schema_version": 1,
        "mode": "ARTIFACT_ADMISSION_AUTHORITY_COMMITMENT",
        "commitment": commitment,
    }
    anchor_bytes = _canonical_bytes(document)
    anchor_name = (
        "artifact-admission-authority-"
        f"{commitment['overall_hash']}.json"
    )
    try:
        _assert_destination_boundary(
            repo_root=root,
            destination=destination,
            expected_class=destination_class,
        )
    except DomainEventSealError as exc:
        raise ArtifactAdmissionSealError(str(exc)) from exc
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
            raise ArtifactAdmissionSealError(
                "existing artifact-admission anchor is unreadable"
            ) from exc
        if existing != anchor_bytes:
            raise ArtifactAdmissionSealError(
                "create-once artifact-admission anchor collision detected"
            )
    try:
        _assert_destination_boundary(
            repo_root=root,
            destination=destination,
            expected_class=destination_class,
        )
    except DomainEventSealError as exc:
        raise ArtifactAdmissionSealError(str(exc)) from exc
    result = {
        "schema_version": 1,
        "operation_verdict": "PASS",
        "institutional_seal_status": "PARTIAL_EXTERNAL_WORM_UNVERIFIED",
        "observed_at_ms": observed_at_ms,
        "data_provenance": "VERIFIED_ARTIFACT_ADMISSION_AUTHORITY_SNAPSHOT",
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a verified create-once artifact-admission commitment"
    )
    parser.add_argument("--repo-root", default=os.getcwd())
    parser.add_argument("--authority-path", default=DEFAULT_AUTHORITY)
    parser.add_argument("--dest", default=os.environ.get(DESTINATION_ENV))
    parser.add_argument("--evidence", default=DEFAULT_EVIDENCE)
    parser.add_argument("--verify-anchor")
    parser.add_argument("--allow-local-destination", action="store_true")
    args = parser.parse_args()
    if args.verify_anchor:
        result = verify_artifact_admission_seal(
            authority_path=args.authority_path,
            anchor_path=args.verify_anchor,
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["verdict"] == "PASS" else 1
    if not args.dest:
        print(f"{DESTINATION_ENV} is not configured", file=sys.stderr)
        return 2
    result = export_artifact_admission_seal(
        repo_root=args.repo_root,
        authority_path=args.authority_path,
        destination_root=args.dest,
        evidence_path=args.evidence,
        allow_local_destination=args.allow_local_destination,
    )
    print(json.dumps(result, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ArtifactAdmissionSealError",
    "export_artifact_admission_seal",
    "verify_artifact_admission_seal",
]
