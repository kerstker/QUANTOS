from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from services.governance.artifact_admission import (
    ArtifactAdmissionPolicy,
    ArtifactVerificationReceipt,
    VerifiedSigstoreFacts,
)
from services.governance.artifact_admission_store import (
    POLICY_REGISTRY_KIND,
    RECEIPT_AUTHORITY_REGISTRY_KIND,
    ArtifactAdmissionStore,
)
from services.governance.artifact_receipt_auth import (
    ReceiptAuthorityKey,
    SignedArtifactVerificationReceipt,
    artifact_receipt_signing_payload,
)
from tools import verify_artifact_admission


ADMISSION_AT_MS = 1_700_000_000_000
VERIFIER_ID = "sigstore-python-4.4.0-offline"
REVOCATION_SOURCE = "operator-approved-fulcio-status-v1"
RECEIPT_AUTHORITY_ID = "external-receipt-authority"
RECEIPT_KEY_ID = "receipt-key-2026-q3"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _test_private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(b"\x01" * 32)


def _signed_receipt(
    receipt: ArtifactVerificationReceipt,
) -> SignedArtifactVerificationReceipt:
    signature = _test_private_key().sign(
        artifact_receipt_signing_payload(
            authority_id=RECEIPT_AUTHORITY_ID,
            key_id=RECEIPT_KEY_ID,
            algorithm="Ed25519",
            receipt=receipt,
        )
    )
    return SignedArtifactVerificationReceipt(
        schema_version=1,
        authority_id=RECEIPT_AUTHORITY_ID,
        key_id=RECEIPT_KEY_ID,
        algorithm="Ed25519",
        receipt=receipt,
        signature_base64url=_b64url(signature),
    )


def _inputs(tmp_path: Path) -> tuple[list[str], bytes]:
    artifact = b"quantos-oci-archive"
    artifact_path = tmp_path / "quantos.oci.tar"
    bundle_path = tmp_path / "bundle.json"
    root_path = tmp_path / "trusted-root.json"
    policy_path = tmp_path / "policy.json"
    receipt_path = tmp_path / "verification-receipt.json"
    registry_path = tmp_path / "policy-registry.json"
    receipt_authority_registry_path = tmp_path / "receipt-authority-registry.json"
    decision_path = tmp_path / "decision.json"
    decision_store_path = tmp_path / "artifact-admission.db"
    ArtifactAdmissionStore(str(decision_store_path), create=True).close()
    artifact_path.write_bytes(artifact)
    bundle_path.write_text("{}", encoding="utf-8")
    root_path.write_text("{}", encoding="utf-8")
    artifact_sha = hashlib.sha256(artifact).hexdigest()
    bundle_sha = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    root_sha = hashlib.sha256(root_path.read_bytes()).hexdigest()
    receipt = ArtifactVerificationReceipt(
        schema_version=1,
        receipt_id="receipt-release-1",
        verifier_id=VERIFIER_ID,
        artifact_sha256=artifact_sha,
        bundle_sha256=bundle_sha,
        trusted_root_sha256=root_sha,
        certificate_sha256="d" * 64,
        verified_at_ms=ADMISSION_AT_MS - 60_000,
        transparency_checkpoint_at_ms=ADMISSION_AT_MS - 2 * 60_000,
        revocation_status="GOOD",
        revocation_checked_at_ms=ADMISSION_AT_MS - 30_000,
        revocation_source=REVOCATION_SOURCE,
    )
    receipt_path.write_text(_signed_receipt(receipt).model_dump_json(), encoding="utf-8")
    public_key = (
        _test_private_key()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    authority_key = ReceiptAuthorityKey(
        schema_version=1,
        authority_id=RECEIPT_AUTHORITY_ID,
        key_id=RECEIPT_KEY_ID,
        algorithm="Ed25519",
        public_key_base64url=_b64url(public_key),
        public_key_sha256=hashlib.sha256(public_key).hexdigest(),
        status="ACTIVE",
    )
    receipt_authority_registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "readiness": "L2_DRY_RUN_ONLY",
                "generation": 0,
                "keys": {RECEIPT_KEY_ID: authority_key.model_dump(mode="json")},
            }
        ),
        encoding="utf-8",
    )
    policy = ArtifactAdmissionPolicy(
        schema_version=3,
        policy_id="quantos-console-admission-v1",
        artifact_name="quantos.oci.tar",
        artifact_sha256=artifact_sha,
        certificate_identity=("https://github.com/example/quantos/.github/workflows/validate.yml@refs/heads/main"),
        certificate_oidc_issuer="https://token.actions.githubusercontent.com",
        trusted_root_sha256=root_sha,
        payload_type="application/vnd.in-toto+json",
        statement_type="https://in-toto.io/Statement/v1",
        predicate_type="https://slsa.dev/provenance/v1",
        subject_name="quantos.oci.tar",
        builder_id="https://github.com/actions/runner",
        build_type="https://quantos.example/build/console-oci/v1",
        source_uri="git+https://github.com/example/quantos",
        source_digest_algorithm="gitCommit",
        source_digest="b" * 40,
        required_receipt_authority_id=RECEIPT_AUTHORITY_ID,
        required_receipt_key_id=RECEIPT_KEY_ID,
        required_receipt_public_key_sha256=authority_key.public_key_sha256,
        required_verifier_id=VERIFIER_ID,
        required_revocation_source=REVOCATION_SOURCE,
        max_artifact_age_ms=7 * 86_400_000,
        max_verification_age_ms=5 * 60_000,
        max_checkpoint_age_ms=5 * 60_000,
        max_revocation_age_ms=5 * 60_000,
        clock_skew_ms=30_000,
    )
    policy_path.write_text(policy.model_dump_json(by_alias=True), encoding="utf-8")
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "readiness": "L2_DRY_RUN_ONLY",
                "generation": 0,
                "policies": {
                    policy.policy_id: {
                        "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
                        "status": "ACTIVE",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    statement = {
        "_type": policy.statement_type,
        "subject": [{"name": policy.subject_name, "digest": {"sha256": artifact_sha}}],
        "predicateType": policy.predicate_type,
        "predicate": {
            "buildDefinition": {
                "buildType": policy.build_type,
                "resolvedDependencies": [
                    {
                        "uri": policy.source_uri,
                        "digest": {policy.source_digest_algorithm: policy.source_digest},
                    }
                ],
            },
            "runDetails": {"builder": {"id": policy.builder_id}},
        },
    }
    args = [
        "--artifact",
        str(artifact_path),
        "--bundle",
        str(bundle_path),
        "--trusted-root",
        str(root_path),
        "--policy",
        str(policy_path),
        "--verification-receipt",
        str(receipt_path),
        "--decision-output",
        str(decision_path),
    ]
    store = ArtifactAdmissionStore(str(decision_store_path), create=False)
    store.append_registry_snapshot(
        POLICY_REGISTRY_KIND,
        registry_path.read_bytes(),
        recorded_at_ms=ADMISSION_AT_MS - 10 * 60_000,
        expected_head=None,
    )
    store.append_registry_snapshot(
        RECEIPT_AUTHORITY_REGISTRY_KIND,
        receipt_authority_registry_path.read_bytes(),
        recorded_at_ms=ADMISSION_AT_MS - 10 * 60_000,
        expected_head=None,
    )
    store.close()
    return args, json.dumps(statement, sort_keys=True).encode("utf-8")


def _run_main(tmp_path: Path, args: list[str]) -> int:
    return verify_artifact_admission.main(
        args,
        policy_registry_path=tmp_path / "policy-registry.json",
        receipt_authority_registry_path=(tmp_path / "receipt-authority-registry.json"),
        decision_store_path=tmp_path / "artifact-admission.db",
    )


def _trust_current_policy(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    policy = ArtifactAdmissionPolicy.model_validate_json(policy_path.read_bytes())
    current_registry = json.loads((tmp_path / "policy-registry.json").read_text(encoding="utf-8"))
    registry_path = tmp_path / "policy-registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "readiness": "L2_DRY_RUN_ONLY",
                "generation": current_registry["generation"] + 1,
                "policies": {
                    policy.policy_id: {
                        "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
                        "status": "ACTIVE",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=False)
    current_head = store.registry_head(POLICY_REGISTRY_KIND)
    latest = store.latest()
    recorded_at_ms = max(
        ADMISSION_AT_MS - 1,
        current_head.recorded_at_ms,
        latest.recorded_at_ms if latest is not None else 0,
    )
    store.append_registry_snapshot(
        POLICY_REGISTRY_KIND,
        registry_path.read_bytes(),
        recorded_at_ms=recorded_at_ms,
        expected_head=current_head,
    )
    store.close()


def _append_registry_snapshot(
    tmp_path: Path,
    registry_name: str,
    *,
    recorded_at_ms: int | None = None,
) -> None:
    registry_kind = POLICY_REGISTRY_KIND if registry_name == "policy-registry.json" else RECEIPT_AUTHORITY_REGISTRY_KIND
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=False)
    current_head = store.registry_head(registry_kind)
    latest = store.latest()
    resolved_recorded_at_ms = (
        max(
            ADMISSION_AT_MS - 1,
            current_head.recorded_at_ms,
            latest.recorded_at_ms if latest is not None else 0,
        )
        if recorded_at_ms is None
        else recorded_at_ms
    )
    store.append_registry_snapshot(
        registry_kind,
        (tmp_path / registry_name).read_bytes(),
        recorded_at_ms=resolved_recorded_at_ms,
        expected_head=current_head,
    )
    store.close()


def _verified_facts(tmp_path: Path) -> VerifiedSigstoreFacts:
    receipt = SignedArtifactVerificationReceipt.model_validate_json(
        (tmp_path / "verification-receipt.json").read_bytes()
    ).receipt
    policy = ArtifactAdmissionPolicy.model_validate_json((tmp_path / "policy.json").read_bytes())
    return VerifiedSigstoreFacts(
        schema_version=1,
        verifier_id=VERIFIER_ID,
        artifact_sha256=receipt.artifact_sha256,
        bundle_sha256=receipt.bundle_sha256,
        trusted_root_sha256=receipt.trusted_root_sha256,
        certificate_sha256=receipt.certificate_sha256,
        certificate_identity=policy.certificate_identity,
        certificate_oidc_issuer=policy.certificate_oidc_issuer,
        certificate_not_before_ms=ADMISSION_AT_MS - 10 * 60_000,
        certificate_not_after_ms=ADMISSION_AT_MS + 10 * 60_000,
        transparency_integrated_at_ms=ADMISSION_AT_MS - 3 * 60_000,
        transparency_log_verified=True,
        signed_certificate_timestamp_verified=True,
    )


def test_cli_allows_only_after_sigstore_returns_verified_dsse_payload(tmp_path: Path, monkeypatch) -> None:
    args, payload = _inputs(tmp_path)
    monkeypatch.setattr(
        verify_artifact_admission.time,
        "time",
        lambda: ADMISSION_AT_MS / 1_000,
    )
    monkeypatch.setattr(
        verify_artifact_admission,
        "_verify_sigstore_dsse",
        lambda **kwargs: (
            "application/vnd.in-toto+json",
            payload,
            _verified_facts(tmp_path),
        ),
    )

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 0
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["verdict"] == "ALLOW"
    assert decision["reason_code"] == "VERIFIED_PROVENANCE_POLICY_MATCH"
    assert decision["verification_receipt_sha256"]
    assert decision["receipt_envelope_sha256"]
    assert decision["receipt_authority_id"] == RECEIPT_AUTHORITY_ID
    assert decision["receipt_key_id"] == RECEIPT_KEY_ID
    assert decision["receipt_signature_verified"] is True
    assert decision["revocation_status"] == "GOOD"
    assert decision["admission_at_ms"] == ADMISSION_AT_MS
    assert decision["authority_decision_id"].startswith("artifact-admission-")
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=False)
    policy_head = store.registry_head(POLICY_REGISTRY_KIND)
    receipt_head = store.registry_head(RECEIPT_AUTHORITY_REGISTRY_KIND)
    assert decision["policy_registry_event_seq"] == policy_head.authority_event_seq
    assert decision["policy_registry_event_hash"] == policy_head.event_hash
    assert decision["receipt_authority_registry_event_seq"] == (receipt_head.authority_event_seq)
    assert decision["receipt_authority_registry_event_hash"] == (receipt_head.event_hash)
    assert store.latest().decision.model_dump(mode="json") == decision
    assert store.chain_valid()
    store.close()


def test_cli_denies_invalid_sigstore_bundle_without_writing_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    args, _ = _inputs(tmp_path)
    monkeypatch.setattr(
        verify_artifact_admission.time,
        "time",
        lambda: ADMISSION_AT_MS / 1_000,
    )

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["verdict"] == "DENY"
    assert decision["reason_code"] == "SIGSTORE_BUNDLE_INVALID"
    assert decision["execution_authority_granted"] is False
    assert decision["deployment_authority_granted"] is False
    assert decision["live_trading_authority_granted"] is False
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=False)
    assert store.latest().decision.reason_code == "SIGSTORE_BUNDLE_INVALID"
    store.close()


def test_cli_denies_trusted_root_drift_before_bundle_verification(tmp_path: Path, monkeypatch) -> None:
    args, payload = _inputs(tmp_path)
    monkeypatch.setattr(
        verify_artifact_admission.time,
        "time",
        lambda: ADMISSION_AT_MS / 1_000,
    )
    policy_path = tmp_path / "policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["trusted_root_sha256"] = "0" * 64
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    _trust_current_policy(tmp_path)
    called = False

    def unexpected_verify(**kwargs):
        nonlocal called
        called = True
        return (
            "application/vnd.in-toto+json",
            payload,
            _verified_facts(tmp_path),
        )

    monkeypatch.setattr(
        verify_artifact_admission,
        "_verify_sigstore_dsse",
        unexpected_verify,
    )

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    assert called is False
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["reason_code"] == "TRUSTED_ROOT_DIGEST_MISMATCH"


def test_cli_denies_policy_drift_before_bundle_verification(tmp_path: Path, monkeypatch) -> None:
    args, payload = _inputs(tmp_path)
    monkeypatch.setattr(
        verify_artifact_admission.time,
        "time",
        lambda: ADMISSION_AT_MS / 1_000,
    )
    policy_path = tmp_path / "policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["certificate_identity"] = "https://attacker.example/workflow"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    called = False

    def unexpected_verify(**kwargs):
        nonlocal called
        called = True
        return (
            "application/vnd.in-toto+json",
            payload,
            _verified_facts(tmp_path),
        )

    monkeypatch.setattr(
        verify_artifact_admission,
        "_verify_sigstore_dsse",
        unexpected_verify,
    )

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    assert called is False
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["reason_code"] == "POLICY_DIGEST_MISMATCH"


def test_cli_denies_stale_revocation_receipt_after_sigstore_verification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    args, payload = _inputs(tmp_path)
    monkeypatch.setattr(
        verify_artifact_admission.time,
        "time",
        lambda: ADMISSION_AT_MS / 1_000,
    )
    receipt_path = tmp_path / "verification-receipt.json"
    receipt = SignedArtifactVerificationReceipt.model_validate_json(receipt_path.read_bytes()).receipt.model_copy(
        update={
            "revocation_checked_at_ms": ADMISSION_AT_MS - 6 * 60_000,
        }
    )
    receipt_path.write_text(_signed_receipt(receipt).model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        verify_artifact_admission,
        "_verify_sigstore_dsse",
        lambda **kwargs: (
            "application/vnd.in-toto+json",
            payload,
            _verified_facts(tmp_path),
        ),
    )

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["verdict"] == "DENY"
    assert decision["reason_code"] == "REVOCATION_EVIDENCE_STALE"


def test_cli_denies_unregistered_or_revoked_policy_before_bundle_verification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    args, payload = _inputs(tmp_path)
    called = False

    def unexpected_verify(**kwargs):
        nonlocal called
        called = True
        return (
            "application/vnd.in-toto+json",
            payload,
            _verified_facts(tmp_path),
        )

    monkeypatch.setattr(
        verify_artifact_admission,
        "_verify_sigstore_dsse",
        unexpected_verify,
    )
    registry_path = tmp_path / "policy-registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["policies"] = {}
    registry["generation"] += 1
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    _append_registry_snapshot(tmp_path, "policy-registry.json")

    missing_exit = _run_main(tmp_path, args)
    missing = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))

    assert missing_exit == 1
    assert missing["reason_code"] == "POLICY_NOT_TRUSTED"
    assert called is False

    _trust_current_policy(tmp_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    policy_id = next(iter(registry["policies"]))
    registry["policies"][policy_id]["status"] = "REVOKED"
    registry["generation"] += 1
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    _append_registry_snapshot(tmp_path, "policy-registry.json")

    revoked_exit = _run_main(tmp_path, args)
    revoked = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))

    assert revoked_exit == 1
    assert revoked["reason_code"] == "POLICY_REVOKED"
    assert called is False


@pytest.mark.parametrize(
    ("registry_mutation", "reason_code"),
    [
        ("missing", "RECEIPT_AUTHORITY_KEY_NOT_TRUSTED"),
        ("revoked", "RECEIPT_AUTHORITY_KEY_REVOKED"),
    ],
)
def test_cli_denies_untrusted_receipt_authority_before_sigstore_verification(
    tmp_path: Path,
    monkeypatch,
    registry_mutation: str,
    reason_code: str,
) -> None:
    args, _ = _inputs(tmp_path)
    registry_path = tmp_path / "receipt-authority-registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if registry_mutation == "missing":
        registry["keys"] = {}
    else:
        registry["keys"][RECEIPT_KEY_ID]["status"] = "REVOKED"
    registry["generation"] += 1
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    _append_registry_snapshot(tmp_path, "receipt-authority-registry.json")
    called = False

    def unexpected_verify(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("Sigstore verifier must not run")

    monkeypatch.setattr(verify_artifact_admission, "_verify_sigstore_dsse", unexpected_verify)

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    assert called is False
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["reason_code"] == reason_code


def test_cli_denies_tampered_signed_receipt_before_sigstore_verification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    args, _ = _inputs(tmp_path)
    receipt_path = tmp_path / "verification-receipt.json"
    envelope = SignedArtifactVerificationReceipt.model_validate_json(receipt_path.read_bytes())
    signature = bytearray(base64.urlsafe_b64decode(envelope.signature_base64url + "=="))
    signature[-1] ^= 1
    receipt_path.write_text(
        envelope.model_copy(update={"signature_base64url": _b64url(bytes(signature))}).model_dump_json(),
        encoding="utf-8",
    )
    called = False

    def unexpected_verify(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("Sigstore verifier must not run")

    monkeypatch.setattr(verify_artifact_admission, "_verify_sigstore_dsse", unexpected_verify)

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    assert called is False
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["reason_code"] == "RECEIPT_SIGNATURE_INVALID"


def test_dynamic_signed_receipt_rotates_without_policy_registry_churn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    args, payload = _inputs(tmp_path)
    policy_path = tmp_path / "policy.json"
    registry_path = tmp_path / "policy-registry.json"
    policy_before = policy_path.read_bytes()
    registry_before = registry_path.read_bytes()
    receipt_path = tmp_path / "verification-receipt.json"
    envelope = SignedArtifactVerificationReceipt.model_validate_json(receipt_path.read_bytes())
    rotated = envelope.receipt.model_copy(update={"receipt_id": "receipt-release-2"})
    receipt_path.write_text(_signed_receipt(rotated).model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        verify_artifact_admission.time,
        "time",
        lambda: ADMISSION_AT_MS / 1_000,
    )
    monkeypatch.setattr(
        verify_artifact_admission,
        "_verify_sigstore_dsse",
        lambda **kwargs: (
            "application/vnd.in-toto+json",
            payload,
            _verified_facts(tmp_path),
        ),
    )

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 0
    assert policy_path.read_bytes() == policy_before
    assert registry_path.read_bytes() == registry_before
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["receipt_id"] == "receipt-release-2"


@pytest.mark.parametrize(
    ("registry_name", "reason_code", "expected_generation"),
    [
        (
            "policy-registry.json",
            "POLICY_REGISTRY_CHANGED_DURING_ADMISSION",
            1,
        ),
        (
            "receipt-authority-registry.json",
            "RECEIPT_AUTHORITY_REGISTRY_CHANGED_DURING_ADMISSION",
            1,
        ),
    ],
)
def test_cli_denies_registry_revocation_during_sigstore_verification(
    tmp_path: Path,
    monkeypatch,
    registry_name: str,
    reason_code: str,
    expected_generation: int,
) -> None:
    args, payload = _inputs(tmp_path)
    monkeypatch.setattr(
        verify_artifact_admission.time,
        "time",
        lambda: ADMISSION_AT_MS / 1_000,
    )

    def verify_then_revoke(**kwargs):
        registry_path = tmp_path / registry_name
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["generation"] += 1
        if registry_name == "policy-registry.json":
            policy_id = next(iter(registry["policies"]))
            registry["policies"][policy_id]["status"] = "REVOKED"
        else:
            registry["keys"][RECEIPT_KEY_ID]["status"] = "REVOKED"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        _append_registry_snapshot(
            tmp_path,
            registry_name,
            recorded_at_ms=ADMISSION_AT_MS,
        )
        return (
            "application/vnd.in-toto+json",
            payload,
            _verified_facts(tmp_path),
        )

    monkeypatch.setattr(
        verify_artifact_admission,
        "_verify_sigstore_dsse",
        verify_then_revoke,
    )

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["verdict"] == "DENY"
    assert decision["reason_code"] == reason_code
    generation_field = (
        "policy_registry_generation"
        if registry_name == "policy-registry.json"
        else "receipt_authority_registry_generation"
    )
    assert decision[generation_field] == expected_generation
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=False)
    assert all(record.decision.verdict == "DENY" for record in store.decisions())
    store.close()


def test_cli_denies_same_generation_registry_digest_change_during_verification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    args, payload = _inputs(tmp_path)

    def verify_then_rewrite_registry(**kwargs):
        registry_path = tmp_path / "policy-registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry_path.write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return (
            "application/vnd.in-toto+json",
            payload,
            _verified_facts(tmp_path),
        )

    monkeypatch.setattr(
        verify_artifact_admission,
        "_verify_sigstore_dsse",
        verify_then_rewrite_registry,
    )

    assert _run_main(tmp_path, args) == 1
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["reason_code"] == ("POLICY_REGISTRY_CHANGED_DURING_ADMISSION")
    assert decision["policy_registry_generation"] == 0


@pytest.mark.parametrize(
    ("registry_name", "registry_kind", "reason_code"),
    [
        (
            "policy-registry.json",
            POLICY_REGISTRY_KIND,
            "POLICY_REGISTRY_HEAD_CHANGED",
        ),
        (
            "receipt-authority-registry.json",
            RECEIPT_AUTHORITY_REGISTRY_KIND,
            "RECEIPT_AUTHORITY_REGISTRY_HEAD_CHANGED",
        ),
    ],
)
def test_cli_persists_deny_when_registry_revokes_after_evaluation_before_record(
    tmp_path: Path,
    monkeypatch,
    registry_name: str,
    registry_kind: str,
    reason_code: str,
) -> None:
    args, payload = _inputs(tmp_path)
    monkeypatch.setattr(
        verify_artifact_admission.time,
        "time",
        lambda: ADMISSION_AT_MS / 1_000,
    )
    monkeypatch.setattr(
        verify_artifact_admission,
        "_verify_sigstore_dsse",
        lambda **kwargs: (
            "application/vnd.in-toto+json",
            payload,
            _verified_facts(tmp_path),
        ),
    )
    original_evaluate = verify_artifact_admission.evaluate_verified_provenance

    def evaluate_then_revoke(**kwargs):
        decision = original_evaluate(**kwargs)
        registry_path = tmp_path / registry_name
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["generation"] += 1
        if registry_kind == POLICY_REGISTRY_KIND:
            policy_id = next(iter(registry["policies"]))
            registry["policies"][policy_id]["status"] = "REVOKED"
        else:
            registry["keys"][RECEIPT_KEY_ID]["status"] = "REVOKED"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        _append_registry_snapshot(
            tmp_path,
            registry_name,
            recorded_at_ms=ADMISSION_AT_MS,
        )
        return decision

    monkeypatch.setattr(
        verify_artifact_admission,
        "evaluate_verified_provenance",
        evaluate_then_revoke,
    )

    assert _run_main(tmp_path, args) == 1

    emitted = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert emitted["verdict"] == "DENY"
    assert emitted["reason_code"] == reason_code
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=False)
    revocation = store.registry_head(registry_kind)
    retained = store.latest()
    assert retained is not None
    assert retained.decision.model_dump(mode="json") == emitted
    assert revocation.authority_event_seq < retained.authority_event_seq
    assert all(record.decision.verdict == "DENY" for record in store.decisions())
    assert store.chain_valid()
    store.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", 0),
        ("1700000000", 1_700_000_000),
        (str((1 << 64) - 1), (1 << 64) - 1),
    ],
)
def test_rekor_integrated_time_accepts_canonical_protobuf_uint64_strings(
    value: str,
    expected: int,
) -> None:
    assert verify_artifact_admission._protobuf_uint64(value) == expected


@pytest.mark.parametrize(
    "value",
    [1_700_000_000, True, "", "01", "+1", "-1", str(1 << 64)],
)
def test_rekor_integrated_time_rejects_noncanonical_or_out_of_range_values(
    value: object,
) -> None:
    with pytest.raises(ValueError):
        verify_artifact_admission._protobuf_uint64(value)


def test_cli_rejects_non_string_policy_digest_and_replaces_stale_allow(
    tmp_path: Path,
) -> None:
    args, _ = _inputs(tmp_path)
    decision_path = tmp_path / "decision.json"
    decision_path.write_text('{"verdict":"ALLOW","reason_code":"STALE"}', encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["artifact_sha256"] = 123
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["verdict"] == "DENY"
    assert decision["reason_code"] == "ADMISSION_POLICY_INVALID"
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=False)
    assert store.latest().decision.reason_code == "ADMISSION_POLICY_INVALID"
    store.close()


def test_cli_rejects_boolean_receipt_timestamp_without_verifier_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    args, _ = _inputs(tmp_path)
    receipt_path = tmp_path / "verification-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["receipt"]["verified_at_ms"] = True
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    called = False

    def unexpected_verify(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("verifier must not run")

    monkeypatch.setattr(verify_artifact_admission, "_verify_sigstore_dsse", unexpected_verify)

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    assert called is False
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["reason_code"] == "SIGNED_VERIFICATION_RECEIPT_INVALID"


def test_cli_rejects_duplicate_registry_keys_without_verifier_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    args, _ = _inputs(tmp_path)
    (tmp_path / "policy-registry.json").write_text(
        '{"schema_version":2,"schema_version":2,"readiness":"L2_DRY_RUN_ONLY","generation":0,"policies":{}}',
        encoding="utf-8",
    )
    called = False

    def unexpected_verify(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("verifier must not run")

    monkeypatch.setattr(verify_artifact_admission, "_verify_sigstore_dsse", unexpected_verify)

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    assert called is False
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["reason_code"] == "POLICY_REGISTRY_MIRROR_INVALID"


def test_cli_rejects_receipt_bundle_mismatch_before_verifier_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    args, _ = _inputs(tmp_path)
    receipt_path = tmp_path / "verification-receipt.json"
    receipt = SignedArtifactVerificationReceipt.model_validate_json(receipt_path.read_bytes()).receipt.model_copy(
        update={"bundle_sha256": "e" * 64}
    )
    receipt_path.write_text(_signed_receipt(receipt).model_dump_json(), encoding="utf-8")
    called = False

    def unexpected_verify(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("verifier must not run")

    monkeypatch.setattr(verify_artifact_admission, "_verify_sigstore_dsse", unexpected_verify)

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    assert called is False
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["reason_code"] == "RECEIPT_BUNDLE_MISMATCH"


def test_cli_uses_post_verification_wall_clock_for_freshness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    args, payload = _inputs(tmp_path)
    current_ms = [ADMISSION_AT_MS]
    monkeypatch.setattr(
        verify_artifact_admission.time,
        "time",
        lambda: current_ms[0] / 1_000,
    )

    def delayed_verify(**kwargs):
        current_ms[0] += 10 * 60_000
        return (
            "application/vnd.in-toto+json",
            payload,
            _verified_facts(tmp_path),
        )

    monkeypatch.setattr(verify_artifact_admission, "_verify_sigstore_dsse", delayed_verify)

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["admission_at_ms"] == ADMISSION_AT_MS + 10 * 60_000
    assert decision["reason_code"] == "VERIFICATION_RECEIPT_STALE"


def test_cli_denies_when_preprovisioned_authority_is_unavailable(
    tmp_path: Path,
) -> None:
    args, _ = _inputs(tmp_path)
    (tmp_path / "artifact-admission.db").unlink()

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["verdict"] == "DENY"
    assert decision["reason_code"] == ("ADMISSION_AUTHORITY_UNAVAILABLE_UNRECORDED")


def test_cli_persists_deny_when_registry_authority_is_unprovisioned(
    tmp_path: Path,
    monkeypatch,
) -> None:
    args, _ = _inputs(tmp_path)
    store_path = tmp_path / "artifact-admission.db"
    store_path.unlink()
    ArtifactAdmissionStore(str(store_path), create=True).close()
    called = False

    def unexpected_verify(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("Sigstore verifier must not run")

    monkeypatch.setattr(
        verify_artifact_admission,
        "_verify_sigstore_dsse",
        unexpected_verify,
    )

    assert _run_main(tmp_path, args) == 1

    emitted = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert called is False
    assert emitted["verdict"] == "DENY"
    assert emitted["reason_code"] == "POLICY_REGISTRY_UNPROVISIONED"
    store = ArtifactAdmissionStore(str(store_path), create=False)
    assert store.latest() is not None
    assert store.latest().decision.model_dump(mode="json") == emitted
    assert store.registry_snapshots() == ()
    store.close()


def test_cli_rejects_wall_clock_rollback_against_authority_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    args, payload = _inputs(tmp_path)
    current_ms = [ADMISSION_AT_MS]
    monkeypatch.setattr(
        verify_artifact_admission.time,
        "time",
        lambda: current_ms[0] / 1_000,
    )
    monkeypatch.setattr(
        verify_artifact_admission,
        "_verify_sigstore_dsse",
        lambda **kwargs: (
            "application/vnd.in-toto+json",
            payload,
            _verified_facts(tmp_path),
        ),
    )

    assert _run_main(tmp_path, args) == 0
    current_ms[0] = ADMISSION_AT_MS - 1

    exit_code = _run_main(tmp_path, args)

    assert exit_code == 1
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))
    assert decision["reason_code"] == "ADMISSION_CLOCK_ROLLBACK"
    store = ArtifactAdmissionStore(str(tmp_path / "artifact-admission.db"), create=False)
    retained = store.decisions()
    assert len(retained) == 2
    assert retained[0].decision.verdict == "ALLOW"
    assert retained[1].decision.model_dump(mode="json") == decision
    assert retained[1].decision.verdict == "DENY"
    assert retained[1].recorded_at_ms == ADMISSION_AT_MS
    store.close()


def test_argument_error_clears_explicit_stale_decision_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "decision.json"
    output.write_text('{"verdict":"ALLOW","reason_code":"STALE"}', encoding="utf-8")

    with pytest.raises(SystemExit):
        verify_artifact_admission.main(
            ["--decision-output", str(output)],
            policy_registry_path=tmp_path / "policy-registry.json",
            decision_store_path=tmp_path / "artifact-admission.db",
        )

    assert not output.exists()
