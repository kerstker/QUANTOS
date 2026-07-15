from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.governance.l2_restore_checkpoint import (
    L2RestoreCheckpoint,
    SignedL2RestoreCheckpoint,
    TrustedL2RestoreCheckpointKey,
    l2_restore_checkpoint_signing_payload,
)
from services.governance.l2_restore_history import (
    L2_RESTORE_HISTORY_STORE_KIND,
    L2_RESTORE_HISTORY_STORE_SCHEMA_VERSION,
    L2RestoreHistoryStore,
    RestoreAttempt,
    RestoreOutcome,
    RestoreProof,
)
from services.governance.top_tier_assessment import assess_repository


ISSUER = "operator-owned-restore-checkpoint-authority"
KEY_ID = "restore-checkpoint-2026-q3"


def _contract() -> dict:
    return {
        "version": 2,
        "sources": [
            {
                "relative_path": "data/cache/l2_restore_history.db",
                "required": True,
                "role": "l2_restore_history_authority",
            }
        ],
    }


def _contract_sha256() -> str:
    canonical = json.dumps(_contract(), sort_keys=True, separators=(",", ":")) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _requirement() -> dict:
    return {
        "id": "OPS.RESTORE.DRILL",
        "domain": "reliability_operations",
        "tier": "B",
        "target_maturity": "M4",
        "classification": "L2_SAFE_NOW",
        "description": "Recurring external L2 restores remain healthy",
        "required_evidence": [
            "implementation",
            "verification",
            "operation",
            "governance",
            "reproducibility",
        ],
        "red_line": True,
        "checker": "l2_restore_history_evidence",
        "checker_args": {
            "history_path": "data/cache/l2_restore_history.db",
            "checkpoint_path": "evidence/l2_restore_history_checkpoint.json",
            "trust_registry_path": "config/l2_restore_checkpoint_trust_registry.json",
            "implementation_paths": [],
            "expected_source_contract": _contract(),
            "max_attempt_age_ms": 100,
            "max_success_age_ms": 100,
            "max_attempt_gap_ms": 150,
            "max_success_gap_ms": 150,
            "minimum_history_span_ms": 200,
            "minimum_successes": 3,
            "clean_lookback_ms": 300,
            "attempt_timeout_ms": 50,
            "required_checkpoint_issuer": ISSUER,
            "required_checkpoint_key_id": KEY_ID,
            "max_checkpoint_future_skew_ms": 5,
            "max_checkpoint_age_ms": 100,
            "minimum_checkpoint_retention_ms": 1_000,
        },
        "next_action": "Retain external signed checkpoints",
    }


def _write_registry(root: Path) -> Path:
    path = root / "requirements.json"
    path.write_text(
        json.dumps({"schema_version": 1, "requirements": [_requirement()]}),
        encoding="utf-8",
    )
    return path


def _proof() -> RestoreProof:
    return RestoreProof(
        source_contract_sha256=_contract_sha256(),
        destination_class="SEPARATE_VOLUME",
        external_destination_verified=True,
        encrypted=True,
        manifest_verified=True,
        source_contract_verified=True,
        restore_verified=True,
        sqlite_integrity_verified=True,
        restore_elapsed_ms=50,
        source_age_ms=75,
        rto_met=True,
        rpo_met=True,
        batch_sha256="d" * 64,
    )


def _seed_history(root: Path) -> L2RestoreHistoryStore:
    path = root / "data" / "cache" / "l2_restore_history.db"
    store = L2RestoreHistoryStore(str(path), create=True, clock_ms=lambda: 1_000)
    for index, started_at_ms in enumerate((100, 200, 300), start=1):
        attempt = RestoreAttempt(
            attempt_id=f"restore-{index}",
            started_at_ms=started_at_ms,
            operation_kind="BACKUP_AND_ISOLATED_RESTORE",
            mode="L2_AUTHORITY_STATE",
            source_contract_sha256=_contract_sha256(),
            destination_ref_sha256="c" * 64,
            rto_ms=100,
            max_rpo_ms=200,
        )
        started, _ = store.begin_attempt(
            attempt,
            expected_head=store.authority_head(),
        )
        store.complete_attempt(
            RestoreOutcome(
                attempt_id=attempt.attempt_id,
                completed_at_ms=started_at_ms + 10,
                verdict="PASS",
                stage="COMPLETE",
                reason_code="RESTORE_VERIFIED",
                proof=_proof(),
            ),
            expected_start=started,
            expected_head=store.authority_head(),
        )
    return store


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _write_external_checkpoint(root: Path, store: L2RestoreHistoryStore) -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    head = store.authority_head()
    assert head is not None
    checkpoint = L2RestoreCheckpoint(
        store_kind=L2_RESTORE_HISTORY_STORE_KIND,
        store_schema_version=L2_RESTORE_HISTORY_STORE_SCHEMA_VERSION,
        event_count=head.event_seq,
        head_seq=head.event_seq,
        head_hash=head.event_hash,
        sealed_at_ms=340,
        object_version_id="worm-version-000001",
        retention_until_ms=2_000,
        issuer=ISSUER,
        key_id=KEY_ID,
    )
    envelope = SignedL2RestoreCheckpoint(
        checkpoint=checkpoint,
        signature_base64url=_base64url(
            private_key.sign(l2_restore_checkpoint_signing_payload(checkpoint))
        ),
    )
    checkpoint_path = root / "evidence" / "l2_restore_history_checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(
        json.dumps(envelope.model_dump(mode="json")),
        encoding="utf-8",
    )
    trust_path = root / "config" / "l2_restore_checkpoint_trust_registry.json"
    trust_path.parent.mkdir(parents=True, exist_ok=True)
    trust_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "keys": [
                    TrustedL2RestoreCheckpointKey(
                        issuer=ISSUER,
                        key_id=KEY_ID,
                        public_key_base64url=_base64url(public_key),
                        public_key_sha256=hashlib.sha256(public_key).hexdigest(),
                        status="ACTIVE",
                    ).model_dump(mode="json")
                ],
            }
        ),
        encoding="utf-8",
    )


def test_missing_history_is_partial_and_cannot_claim_m4(tmp_path: Path) -> None:
    result = assess_repository(
        tmp_path,
        _write_registry(tmp_path),
        as_of_ms=350,
    )["requirements"][0]

    assert result["status"] == "PARTIAL"
    assert result["current_maturity"] == "M3"


def test_clean_local_history_without_external_checkpoint_remains_m3_partial(
    tmp_path: Path,
) -> None:
    store = _seed_history(tmp_path)
    store.close()

    result = assess_repository(
        tmp_path,
        _write_registry(tmp_path),
        as_of_ms=350,
    )["requirements"][0]

    assert result["status"] == "PARTIAL"
    assert result["current_maturity"] == "M3"
    assert "checkpoint" in result["detail"].lower()


def test_clean_history_and_exact_external_checkpoint_reach_m4(tmp_path: Path) -> None:
    store = _seed_history(tmp_path)
    _write_external_checkpoint(tmp_path, store)
    store.close()

    result = assess_repository(
        tmp_path,
        _write_registry(tmp_path),
        as_of_ms=350,
    )["requirements"][0]

    assert result["status"] == "PASS"
    assert result["current_maturity"] == "M4"
    assert result["meets_target"] is True


def test_history_freshness_is_deterministic_at_boundary_and_fails_one_ms_after(
    tmp_path: Path,
) -> None:
    store = _seed_history(tmp_path)
    _write_external_checkpoint(tmp_path, store)
    store.close()
    registry = _write_registry(tmp_path)

    at_boundary = assess_repository(tmp_path, registry, as_of_ms=400)
    stale = assess_repository(tmp_path, registry, as_of_ms=401)

    assert at_boundary["requirements"][0]["status"] == "PASS"
    assert stale["requirements"][0]["status"] == "FAILED"
    assert "STALE" in stale["requirements"][0]["detail"]


def test_present_but_tampered_external_checkpoint_fails_closed(tmp_path: Path) -> None:
    store = _seed_history(tmp_path)
    _write_external_checkpoint(tmp_path, store)
    store.close()
    checkpoint_path = tmp_path / "evidence" / "l2_restore_history_checkpoint.json"
    document = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    document["checkpoint"]["head_hash"] = "e" * 64
    checkpoint_path.write_text(json.dumps(document), encoding="utf-8")

    result = assess_repository(
        tmp_path,
        _write_registry(tmp_path),
        as_of_ms=350,
    )["requirements"][0]

    assert result["status"] == "FAILED"
    assert result["current_maturity"] == "M2"
