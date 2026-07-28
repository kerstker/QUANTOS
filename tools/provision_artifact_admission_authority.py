#!/usr/bin/env python
"""Provision or verify the fixed fail-closed artifact-admission authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services.governance.artifact_admission_store import (  # noqa: E402
    POLICY_REGISTRY_KIND,
    RECEIPT_AUTHORITY_REGISTRY_KIND,
    ArtifactAdmissionStore,
    ArtifactAdmissionStoreError,
)


DEFAULT_POLICY_REGISTRY = REPO / "config" / "artifact_admission_policy_registry.json"
DEFAULT_RECEIPT_AUTHORITY_REGISTRY = (
    REPO / "config" / "artifact_receipt_authority_registry.json"
)
DEFAULT_STORE = REPO / "data" / "cache" / "artifact_admission.db"
_MAX_REGISTRY_BYTES = 1024 * 1024


def _read_registry(path: Path) -> bytes:
    if path.is_symlink():
        raise ValueError("registry mirrors cannot be symlinks")
    with path.open("rb") as handle:
        before = handle.seek(0, 2)
        handle.seek(0)
        if before <= 0 or before > _MAX_REGISTRY_BYTES:
            raise ValueError("registry mirror size is invalid")
        descriptor = handle.fileno()
        metadata_before = os.fstat(descriptor)
        if not stat.S_ISREG(metadata_before.st_mode):
            raise ValueError("registry mirror must be a regular file")
        content = handle.read()
        metadata_after = os.fstat(descriptor)
    if (
        metadata_before.st_dev,
        metadata_before.st_ino,
        metadata_before.st_size,
        metadata_before.st_mtime_ns,
    ) != (
        metadata_after.st_dev,
        metadata_after.st_ino,
        metadata_after.st_size,
        metadata_after.st_mtime_ns,
    ):
        raise ValueError("registry mirror changed while it was read")
    return content


def _head_document(head: Any) -> dict[str, Any]:
    return {
        "authority_event_seq": head.authority_event_seq,
        "event_hash": head.event_hash,
        "generation": head.generation,
        "registry_sha256": head.registry_sha256,
    }


def _verify_exact(
    store_path: Path,
    *,
    policy_registry_bytes: bytes,
    receipt_authority_registry_bytes: bytes,
) -> dict[str, Any]:
    if store_path.is_symlink():
        raise ValueError("artifact-admission authority cannot be a symlink")
    store = ArtifactAdmissionStore(str(store_path), create=False)
    try:
        heads = store.registry_heads()
        expected = {
            POLICY_REGISTRY_KIND: policy_registry_bytes,
            RECEIPT_AUTHORITY_REGISTRY_KIND: receipt_authority_registry_bytes,
        }
        if set(heads) != set(expected):
            raise ValueError("artifact-admission authority registry heads are incomplete")
        for kind, content in expected.items():
            head = heads[kind]
            if (
                head.registry_bytes != content
                or head.registry_sha256 != hashlib.sha256(content).hexdigest()
            ):
                raise ValueError(f"{kind} authority head does not match its mirror")
        decisions = store.decisions()
        return {
            "authority_time_floor_ms": store.authority_time_floor(),
            "decision_count": len(decisions),
            "policy_registry_head": _head_document(heads[POLICY_REGISTRY_KIND]),
            "receipt_authority_registry_head": _head_document(
                heads[RECEIPT_AUTHORITY_REGISTRY_KIND]
            ),
        }
    finally:
        store.close()


def provision_artifact_admission_authority(
    *,
    policy_registry_path: str | Path = DEFAULT_POLICY_REGISTRY,
    receipt_authority_registry_path: str | Path = DEFAULT_RECEIPT_AUTHORITY_REGISTRY,
    store_path: str | Path = DEFAULT_STORE,
    observed_at_ms: int,
    check_only: bool = False,
) -> dict[str, Any]:
    if (
        isinstance(observed_at_ms, bool)
        or not isinstance(observed_at_ms, int)
        or observed_at_ms < 0
    ):
        raise ValueError("observed_at_ms must be a non-negative integer")
    policy_bytes = _read_registry(Path(policy_registry_path))
    receipt_bytes = _read_registry(Path(receipt_authority_registry_path))
    destination = Path(store_path)
    if destination.is_symlink():
        raise ValueError("artifact-admission authority cannot be a symlink")

    inserted: dict[str, bool] = {}
    if not check_only:
        store = ArtifactAdmissionStore(
            str(destination),
            create=not destination.exists(),
        )
        try:
            mirrors = (
                (POLICY_REGISTRY_KIND, policy_bytes),
                (RECEIPT_AUTHORITY_REGISTRY_KIND, receipt_bytes),
            )
            for kind, content in mirrors:
                heads = store.registry_heads()
                current = heads.get(kind)
                _head, was_inserted = store.append_registry_snapshot(
                    kind,
                    content,
                    recorded_at_ms=max(
                        observed_at_ms,
                        store.authority_time_floor() or observed_at_ms,
                    ),
                    expected_head=current,
                )
                inserted[kind] = was_inserted
        finally:
            store.close()

    verified = _verify_exact(
        destination,
        policy_registry_bytes=policy_bytes,
        receipt_authority_registry_bytes=receipt_bytes,
    )
    return {
        "schema_version": 1,
        "status": "PASS",
        "mode": "CHECK_ONLY" if check_only else "PROVISION_AND_VERIFY",
        "store_path": str(destination.resolve()),
        "inserted": inserted,
        "signing_capability_present": False,
        "execution_authority_granted": False,
        "deployment_authority_granted": False,
        "live_trading_authority_granted": False,
        **verified,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Provision or verify the fixed artifact-admission authority"
    )
    parser.add_argument("--policy-registry", default=str(DEFAULT_POLICY_REGISTRY))
    parser.add_argument(
        "--receipt-authority-registry",
        default=str(DEFAULT_RECEIPT_AUTHORITY_REGISTRY),
    )
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--observed-at-ms", type=int)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    observed_at_ms = (
        args.observed_at_ms
        if args.observed_at_ms is not None
        else time.time_ns() // 1_000_000
    )
    try:
        result = provision_artifact_admission_authority(
            policy_registry_path=args.policy_registry,
            receipt_authority_registry_path=args.receipt_authority_registry,
            store_path=args.store,
            observed_at_ms=observed_at_ms,
            check_only=args.check,
        )
    except (ArtifactAdmissionStoreError, OSError, TypeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
