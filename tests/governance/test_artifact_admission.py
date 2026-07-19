from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.governance.artifact_admission import (
    ArtifactAdmissionDecision,
    ArtifactVerificationReceipt,
    ArtifactAdmissionPolicy,
    MAX_ARTIFACT_AGE_MS,
    MAX_CLOCK_SKEW_MS,
    MAX_EVIDENCE_AGE_MS,
    VerifiedReceiptAuthentication,
    VerifiedSigstoreFacts,
    artifact_verification_receipt_sha256,
    evaluate_verified_provenance,
)


PAYLOAD_TYPE = "application/vnd.in-toto+json"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
ADMISSION_AT_MS = 1_700_000_000_000
INTEGRATED_AT_MS = ADMISSION_AT_MS - 3 * 60_000
VERIFIED_AT_MS = ADMISSION_AT_MS - 60_000
CHECKPOINT_AT_MS = ADMISSION_AT_MS - 2 * 60_000
REVOCATION_AT_MS = ADMISSION_AT_MS - 30_000
VERIFIER_ID = "sigstore-python-4.4.0-offline"
REVOCATION_SOURCE = "operator-approved-fulcio-status-v1"
POLICY_SHA256 = "e" * 64
POLICY_REGISTRY_SHA256 = "f" * 64
POLICY_REGISTRY_GENERATION = 7
POLICY_REGISTRY_EVENT_SEQ = 19
POLICY_REGISTRY_EVENT_HASH = "4" * 64
RECEIPT_AUTHORITY_REGISTRY_SHA256 = "1" * 64
RECEIPT_AUTHORITY_REGISTRY_GENERATION = 11
RECEIPT_AUTHORITY_REGISTRY_EVENT_SEQ = 23
RECEIPT_AUTHORITY_REGISTRY_EVENT_HASH = "5" * 64
RECEIPT_AUTHORITY_ID = "external-receipt-authority"
RECEIPT_KEY_ID = "receipt-key-2026-q3"
RECEIPT_PUBLIC_KEY_SHA256 = "2" * 64


def _receipt_for_artifact(
    artifact: bytes = b"quantos-oci-archive",
) -> ArtifactVerificationReceipt:
    return ArtifactVerificationReceipt(
        schema_version=1,
        receipt_id="receipt-release-1",
        verifier_id=VERIFIER_ID,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
        bundle_sha256="c" * 64,
        trusted_root_sha256="a" * 64,
        certificate_sha256="d" * 64,
        verified_at_ms=VERIFIED_AT_MS,
        transparency_checkpoint_at_ms=CHECKPOINT_AT_MS,
        revocation_status="GOOD",
        revocation_checked_at_ms=REVOCATION_AT_MS,
        revocation_source=REVOCATION_SOURCE,
    )


def _policy(artifact: bytes = b"quantos-oci-archive") -> ArtifactAdmissionPolicy:
    return ArtifactAdmissionPolicy(
        schema_version=3,
        policy_id="quantos-console-admission-v1",
        artifact_name="quantos-console.oci.tar",
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
        certificate_identity=("https://github.com/example/quantos/.github/workflows/validate.yml@refs/heads/main"),
        certificate_oidc_issuer="https://token.actions.githubusercontent.com",
        trusted_root_sha256="a" * 64,
        payload_type=PAYLOAD_TYPE,
        statement_type=STATEMENT_TYPE,
        predicate_type=PREDICATE_TYPE,
        subject_name="quantos-console.oci.tar",
        builder_id="https://github.com/actions/runner",
        build_type="https://quantos.example/build/console-oci/v1",
        source_uri="git+https://github.com/example/quantos",
        source_digest_algorithm="gitCommit",
        source_digest="b" * 40,
        required_receipt_authority_id=RECEIPT_AUTHORITY_ID,
        required_receipt_key_id=RECEIPT_KEY_ID,
        required_receipt_public_key_sha256=RECEIPT_PUBLIC_KEY_SHA256,
        required_verifier_id=VERIFIER_ID,
        required_revocation_source=REVOCATION_SOURCE,
        max_artifact_age_ms=7 * 86_400_000,
        max_verification_age_ms=5 * 60_000,
        max_checkpoint_age_ms=5 * 60_000,
        max_revocation_age_ms=5 * 60_000,
        clock_skew_ms=30_000,
    )


def _facts(policy: ArtifactAdmissionPolicy) -> VerifiedSigstoreFacts:
    return VerifiedSigstoreFacts(
        schema_version=1,
        verifier_id=VERIFIER_ID,
        artifact_sha256=policy.artifact_sha256,
        bundle_sha256="c" * 64,
        trusted_root_sha256=policy.trusted_root_sha256,
        certificate_sha256="d" * 64,
        certificate_identity=policy.certificate_identity,
        certificate_oidc_issuer=policy.certificate_oidc_issuer,
        certificate_not_before_ms=INTEGRATED_AT_MS - 60_000,
        certificate_not_after_ms=INTEGRATED_AT_MS + 10 * 60_000,
        transparency_integrated_at_ms=INTEGRATED_AT_MS,
        transparency_log_verified=True,
        signed_certificate_timestamp_verified=True,
    )


def _receipt(policy: ArtifactAdmissionPolicy) -> ArtifactVerificationReceipt:
    return _receipt_for_artifact()


def _authentication(
    receipt: ArtifactVerificationReceipt,
) -> VerifiedReceiptAuthentication:
    return VerifiedReceiptAuthentication(
        schema_version=1,
        authority_id=RECEIPT_AUTHORITY_ID,
        key_id=RECEIPT_KEY_ID,
        algorithm="Ed25519",
        public_key_sha256=RECEIPT_PUBLIC_KEY_SHA256,
        envelope_sha256="3" * 64,
        receipt_sha256=artifact_verification_receipt_sha256(receipt),
        signature_verified=True,
    )


def _statement(policy: ArtifactAdmissionPolicy) -> dict[str, object]:
    return {
        "_type": STATEMENT_TYPE,
        "subject": [
            {
                "name": policy.subject_name,
                "digest": {"sha256": policy.artifact_sha256},
            }
        ],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "buildDefinition": {
                "buildType": policy.build_type,
                "externalParameters": {},
                "internalParameters": {},
                "resolvedDependencies": [
                    {
                        "uri": policy.source_uri,
                        "digest": {policy.source_digest_algorithm: policy.source_digest},
                    }
                ],
            },
            "runDetails": {
                "builder": {"id": policy.builder_id},
                "metadata": {"invocationId": "build-123"},
                "byproducts": [],
            },
        },
    }


def _evaluate(
    policy: ArtifactAdmissionPolicy,
    statement: dict[str, object] | bytes,
    *,
    payload_type: str = PAYLOAD_TYPE,
    artifact_sha256: str | None = None,
    verification: VerifiedSigstoreFacts | None | object = ...,
    receipt: ArtifactVerificationReceipt | None | object = ...,
    receipt_authentication: VerifiedReceiptAuthentication | None | object = ...,
    admission_at_ms: int | None = ADMISSION_AT_MS,
    verifier_runtime_id: str = VERIFIER_ID,
    policy_registry_event_seq: int = POLICY_REGISTRY_EVENT_SEQ,
    policy_registry_event_hash: str = POLICY_REGISTRY_EVENT_HASH,
    receipt_authority_registry_event_seq: int = (RECEIPT_AUTHORITY_REGISTRY_EVENT_SEQ),
    receipt_authority_registry_event_hash: str = (RECEIPT_AUTHORITY_REGISTRY_EVENT_HASH),
):
    payload = statement if isinstance(statement, bytes) else json.dumps(statement, sort_keys=True).encode("utf-8")
    resolved_receipt = _receipt(policy) if receipt is ... else receipt
    resolved_authentication = receipt_authentication
    if receipt_authentication is ...:
        resolved_authentication = _authentication(resolved_receipt) if resolved_receipt is not None else None
    return evaluate_verified_provenance(
        payload_type=payload_type,
        payload=payload,
        artifact_sha256=artifact_sha256 or policy.artifact_sha256,
        bundle_sha256="c" * 64,
        trusted_root_sha256=policy.trusted_root_sha256,
        policy_sha256=POLICY_SHA256,
        policy_registry_sha256=POLICY_REGISTRY_SHA256,
        policy_registry_generation=POLICY_REGISTRY_GENERATION,
        policy_registry_event_seq=policy_registry_event_seq,
        policy_registry_event_hash=policy_registry_event_hash,
        verifier_runtime_id=verifier_runtime_id,
        receipt_authority_registry_sha256=(RECEIPT_AUTHORITY_REGISTRY_SHA256),
        receipt_authority_registry_generation=(RECEIPT_AUTHORITY_REGISTRY_GENERATION),
        receipt_authority_registry_event_seq=(receipt_authority_registry_event_seq),
        receipt_authority_registry_event_hash=(receipt_authority_registry_event_hash),
        policy=policy,
        verification=(_facts(policy) if verification is ... else verification),
        receipt=resolved_receipt,
        receipt_authentication=resolved_authentication,
        admission_at_ms=admission_at_ms,
    )


def test_verified_slsa_statement_allows_exact_policy_match() -> None:
    policy = _policy()

    decision = _evaluate(policy, _statement(policy))

    assert decision.verdict == "ALLOW"
    assert decision.schema_version == 4
    assert decision.reason_code == "VERIFIED_PROVENANCE_POLICY_MATCH"
    assert decision.policy_id == policy.policy_id
    assert decision.policy_sha256 == POLICY_SHA256
    assert decision.policy_registry_sha256 == POLICY_REGISTRY_SHA256
    assert decision.policy_registry_generation == POLICY_REGISTRY_GENERATION
    assert decision.policy_registry_event_seq == POLICY_REGISTRY_EVENT_SEQ
    assert decision.policy_registry_event_hash == POLICY_REGISTRY_EVENT_HASH
    assert decision.artifact_sha256 == policy.artifact_sha256
    assert decision.builder_id == policy.builder_id
    assert decision.source_uri == policy.source_uri
    assert decision.verification_receipt_sha256 == (artifact_verification_receipt_sha256(_receipt(policy)))
    assert decision.verifier_id == VERIFIER_ID
    assert decision.verifier_runtime_id == VERIFIER_ID
    assert decision.receipt_authority_id == RECEIPT_AUTHORITY_ID
    assert decision.receipt_authority_registry_generation == (RECEIPT_AUTHORITY_REGISTRY_GENERATION)
    assert decision.receipt_authority_registry_event_seq == (RECEIPT_AUTHORITY_REGISTRY_EVENT_SEQ)
    assert decision.receipt_authority_registry_event_hash == (RECEIPT_AUTHORITY_REGISTRY_EVENT_HASH)
    assert decision.receipt_key_id == RECEIPT_KEY_ID
    assert decision.receipt_signature_verified is True
    assert decision.admission_at_ms == ADMISSION_AT_MS
    assert decision.policy_max_artifact_age_ms == policy.max_artifact_age_ms
    assert decision.revocation_status == "GOOD"
    assert decision.execution_authority_granted is False
    assert decision.deployment_authority_granted is False
    assert decision.live_trading_authority_granted is False


@pytest.mark.parametrize(
    ("case_id", "mutate", "reason_code"),
    [
        (
            "payload-type",
            lambda policy, statement: None,
            "PAYLOAD_TYPE_MISMATCH",
        ),
        (
            "statement-type",
            lambda policy, statement: statement.__setitem__("_type", "wrong"),
            "STATEMENT_TYPE_MISMATCH",
        ),
        (
            "statement-type-whitespace",
            lambda policy, statement: statement.__setitem__("_type", f" {STATEMENT_TYPE}"),
            "STATEMENT_TYPE_MISMATCH",
        ),
        (
            "predicate-type",
            lambda policy, statement: statement.__setitem__("predicateType", "wrong"),
            "PREDICATE_TYPE_MISMATCH",
        ),
        (
            "subject-name",
            lambda policy, statement: statement["subject"][0].__setitem__("name", "other.oci.tar"),
            "SUBJECT_MISMATCH",
        ),
        (
            "subject-digest",
            lambda policy, statement: statement["subject"][0]["digest"].__setitem__("sha256", "d" * 64),
            "SUBJECT_MISMATCH",
        ),
        (
            "builder",
            lambda policy, statement: statement["predicate"]["runDetails"]["builder"].__setitem__(
                "id", "https://untrusted.example/builder"
            ),
            "BUILDER_MISMATCH",
        ),
        (
            "build-type",
            lambda policy, statement: statement["predicate"]["buildDefinition"].__setitem__(
                "buildType", "https://untrusted.example/build"
            ),
            "BUILD_TYPE_MISMATCH",
        ),
        (
            "source-uri",
            lambda policy, statement: statement["predicate"]["buildDefinition"]["resolvedDependencies"][0].__setitem__(
                "uri", "git+https://github.com/example/other"
            ),
            "SOURCE_MISMATCH",
        ),
        (
            "source-digest",
            lambda policy, statement: statement["predicate"]["buildDefinition"]["resolvedDependencies"][0][
                "digest"
            ].__setitem__("gitCommit", "e" * 40),
            "SOURCE_MISMATCH",
        ),
    ],
)
def test_verified_statement_mismatches_fail_closed(case_id: str, mutate, reason_code: str) -> None:
    policy = _policy()
    statement = copy.deepcopy(_statement(policy))
    mutate(policy, statement)
    payload_type = "application/json" if case_id == "payload-type" else PAYLOAD_TYPE

    decision = _evaluate(policy, statement, payload_type=payload_type)

    assert decision.verdict == "DENY"
    assert decision.reason_code == reason_code
    assert decision.execution_authority_granted is False
    assert decision.deployment_authority_granted is False


def test_artifact_and_trusted_root_digests_are_policy_bound() -> None:
    policy = _policy()

    wrong_artifact = _evaluate(
        policy,
        _statement(policy),
        artifact_sha256="f" * 64,
    )
    wrong_root = evaluate_verified_provenance(
        payload_type=PAYLOAD_TYPE,
        payload=json.dumps(_statement(policy)).encode("utf-8"),
        artifact_sha256=policy.artifact_sha256,
        bundle_sha256="c" * 64,
        trusted_root_sha256="0" * 64,
        policy_sha256=POLICY_SHA256,
        policy_registry_sha256=POLICY_REGISTRY_SHA256,
        policy_registry_generation=POLICY_REGISTRY_GENERATION,
        policy_registry_event_seq=POLICY_REGISTRY_EVENT_SEQ,
        policy_registry_event_hash=POLICY_REGISTRY_EVENT_HASH,
        verifier_runtime_id=VERIFIER_ID,
        receipt_authority_registry_sha256=(RECEIPT_AUTHORITY_REGISTRY_SHA256),
        receipt_authority_registry_generation=(RECEIPT_AUTHORITY_REGISTRY_GENERATION),
        receipt_authority_registry_event_seq=(RECEIPT_AUTHORITY_REGISTRY_EVENT_SEQ),
        receipt_authority_registry_event_hash=(RECEIPT_AUTHORITY_REGISTRY_EVENT_HASH),
        policy=policy,
        verification=_facts(policy),
        receipt=_receipt(policy),
        receipt_authentication=_authentication(_receipt(policy)),
        admission_at_ms=ADMISSION_AT_MS,
    )

    assert wrong_artifact.reason_code == "ARTIFACT_DIGEST_MISMATCH"
    assert wrong_root.reason_code == "TRUSTED_ROOT_DIGEST_MISMATCH"


@pytest.mark.parametrize(
    ("updates", "reason_code"),
    [
        (
            {"policy_registry_event_seq": -1},
            "POLICY_REGISTRY_EVENT_SEQ_INVALID",
        ),
        (
            {"policy_registry_event_seq": True},
            "POLICY_REGISTRY_EVENT_SEQ_INVALID",
        ),
        (
            {"policy_registry_event_hash": "A" * 64},
            "POLICY_REGISTRY_EVENT_HASH_INVALID",
        ),
        (
            {"receipt_authority_registry_event_seq": -1},
            "RECEIPT_AUTHORITY_REGISTRY_EVENT_SEQ_INVALID",
        ),
        (
            {"receipt_authority_registry_event_seq": True},
            "RECEIPT_AUTHORITY_REGISTRY_EVENT_SEQ_INVALID",
        ),
        (
            {"receipt_authority_registry_event_hash": "not-a-head-hash"},
            "RECEIPT_AUTHORITY_REGISTRY_EVENT_HASH_INVALID",
        ),
    ],
)
def test_registry_authority_heads_are_strictly_validated(updates: dict[str, object], reason_code: str) -> None:
    policy = _policy()

    decision = _evaluate(policy, _statement(policy), **updates)

    assert decision.verdict == "DENY"
    assert decision.reason_code == reason_code
    assert decision.schema_version == 4


def test_registry_authority_heads_are_bound_into_denials() -> None:
    policy = _policy()
    statement = _statement(policy)
    statement["predicate"]["runDetails"]["builder"]["id"] = "untrusted"

    decision = _evaluate(policy, statement)

    assert decision.reason_code == "BUILDER_MISMATCH"
    assert decision.policy_registry_event_seq == POLICY_REGISTRY_EVENT_SEQ
    assert decision.policy_registry_event_hash == POLICY_REGISTRY_EVENT_HASH
    assert decision.receipt_authority_registry_event_seq == (RECEIPT_AUTHORITY_REGISTRY_EVENT_SEQ)
    assert decision.receipt_authority_registry_event_hash == (RECEIPT_AUTHORITY_REGISTRY_EVENT_HASH)


def test_decision_contract_rejects_v3_and_invalid_registry_head_fields() -> None:
    policy = _policy()
    raw = _evaluate(policy, _statement(policy)).model_dump(mode="python")

    with pytest.raises(ValidationError):
        ArtifactAdmissionDecision.model_validate({**raw, "schema_version": 3})
    with pytest.raises(ValidationError):
        ArtifactAdmissionDecision.model_validate({**raw, "policy_registry_event_seq": -1})
    with pytest.raises(ValidationError):
        ArtifactAdmissionDecision.model_validate({**raw, "receipt_authority_registry_event_hash": "F" * 64})


def test_malformed_or_ambiguous_statement_fails_closed() -> None:
    policy = _policy()
    ambiguous = _statement(policy)
    ambiguous["subject"].append(copy.deepcopy(ambiguous["subject"][0]))

    malformed = _evaluate(policy, b"not-json")
    duplicate = _evaluate(policy, ambiguous)

    assert malformed.verdict == "DENY"
    assert malformed.reason_code == "PROVENANCE_PAYLOAD_INVALID"
    assert duplicate.verdict == "DENY"
    assert duplicate.reason_code == "SUBJECT_MISMATCH"

    conflicting_source = _statement(policy)
    conflicting_source["predicate"]["buildDefinition"]["resolvedDependencies"].append(
        {
            "uri": policy.source_uri,
            "digest": {policy.source_digest_algorithm: "e" * 40},
        }
    )
    conflict = _evaluate(policy, conflicting_source)
    assert conflict.verdict == "DENY"
    assert conflict.reason_code == "SOURCE_MISMATCH"

    duplicate_key = _evaluate(
        policy,
        (b'{"_type":"https://in-toto.io/Statement/v1","_type":"https://in-toto.io/Statement/v1"}'),
    )
    assert duplicate_key.reason_code == "PROVENANCE_PAYLOAD_INVALID"


@pytest.mark.parametrize(
    (
        "verification",
        "receipt",
        "receipt_authentication",
        "admission_at_ms",
        "reason_code",
    ),
    [
        (None, ..., ..., ADMISSION_AT_MS, "SIGSTORE_FACTS_REQUIRED"),
        (..., None, ..., ADMISSION_AT_MS, "VERIFICATION_RECEIPT_REQUIRED"),
        (..., ..., None, ADMISSION_AT_MS, "RECEIPT_AUTHENTICATION_REQUIRED"),
        (..., ..., ..., None, "ADMISSION_TIME_REQUIRED"),
    ],
)
def test_missing_verification_evidence_fails_closed(
    verification,
    receipt,
    receipt_authentication,
    admission_at_ms,
    reason_code,
) -> None:
    policy = _policy()

    decision = _evaluate(
        policy,
        _statement(policy),
        verification=verification,
        receipt=receipt,
        receipt_authentication=receipt_authentication,
        admission_at_ms=admission_at_ms,
    )

    assert decision.verdict == "DENY"
    assert decision.reason_code == reason_code


def test_core_requires_runtime_verifier_to_match_policy_and_verified_facts() -> None:
    policy = _policy()

    runtime_mismatch = _evaluate(
        policy,
        _statement(policy),
        verifier_runtime_id="sigstore-python-9.9.9-offline",
    )
    fact_mismatch = _evaluate(
        policy,
        _statement(policy),
        verification=_facts(policy).model_copy(update={"verifier_id": "sigstore-python-9.9.9-offline"}),
    )

    assert runtime_mismatch.reason_code == "VERIFIER_RUNTIME_MISMATCH"
    assert fact_mismatch.reason_code == "VERIFIER_ID_MISMATCH"


def _policy_for_receipt(
    policy: ArtifactAdmissionPolicy,
    receipt: ArtifactVerificationReceipt,
) -> ArtifactAdmissionPolicy:
    return policy


@pytest.mark.parametrize(
    ("updates", "reason_code"),
    [
        (
            {"verified_at_ms": ADMISSION_AT_MS - 6 * 60_000},
            "VERIFICATION_RECEIPT_STALE",
        ),
        (
            {"verified_at_ms": ADMISSION_AT_MS + 31_000},
            "VERIFICATION_RECEIPT_FUTURE",
        ),
        (
            {"transparency_checkpoint_at_ms": ADMISSION_AT_MS - 6 * 60_000},
            "TRANSPARENCY_CHECKPOINT_STALE",
        ),
        (
            {"transparency_checkpoint_at_ms": ADMISSION_AT_MS + 31_000},
            "TRANSPARENCY_CHECKPOINT_FUTURE",
        ),
        (
            {"transparency_checkpoint_at_ms": VERIFIED_AT_MS + 31_000},
            "VERIFICATION_PRECEDES_TRANSPARENCY_CHECKPOINT",
        ),
        (
            {"revocation_checked_at_ms": ADMISSION_AT_MS - 6 * 60_000},
            "REVOCATION_EVIDENCE_STALE",
        ),
        (
            {"revocation_checked_at_ms": ADMISSION_AT_MS + 31_000},
            "REVOCATION_EVIDENCE_FUTURE",
        ),
        ({"revocation_status": "REVOKED"}, "CERTIFICATE_REVOKED"),
        ({"revocation_status": "UNKNOWN"}, "REVOCATION_STATUS_UNKNOWN"),
        ({"bundle_sha256": "e" * 64}, "RECEIPT_BUNDLE_MISMATCH"),
    ],
)
def test_receipt_time_revocation_and_binding_fail_closed(
    updates: dict[str, object],
    reason_code: str,
) -> None:
    base_policy = _policy()
    receipt = _receipt(base_policy).model_copy(update=updates)
    policy = _policy_for_receipt(base_policy, receipt)

    decision = _evaluate(
        policy,
        _statement(policy),
        receipt=receipt,
    )

    assert decision.verdict == "DENY"
    assert decision.reason_code == reason_code


def test_receipt_digest_and_verified_material_are_both_bound() -> None:
    policy = _policy()
    receipt = _receipt(policy)
    tampered = receipt.model_copy(update={"receipt_id": "tampered"})
    facts = _facts(policy).model_copy(update={"certificate_sha256": "e" * 64})

    digest_mismatch = _evaluate(
        policy,
        _statement(policy),
        receipt=tampered,
        receipt_authentication=_authentication(receipt),
    )
    material_mismatch = _evaluate(
        policy,
        _statement(policy),
        verification=facts,
    )

    assert digest_mismatch.reason_code == "RECEIPT_AUTHENTICATION_DIGEST_MISMATCH"
    assert material_mismatch.reason_code == "RECEIPT_CERTIFICATE_MISMATCH"


def test_signed_receipt_rotates_without_changing_stable_policy() -> None:
    policy = _policy()
    rotated = _receipt(policy).model_copy(update={"receipt_id": "receipt-release-2"})

    decision = _evaluate(
        policy,
        _statement(policy),
        receipt=rotated,
        receipt_authentication=_authentication(rotated),
    )

    assert decision.verdict == "ALLOW"
    assert decision.receipt_id == "receipt-release-2"
    assert not hasattr(policy, "verification_receipt_sha256")


def test_receipt_authentication_must_match_stable_policy_authority() -> None:
    policy = _policy()
    receipt = _receipt(policy)
    wrong_authority = _authentication(receipt).model_copy(update={"authority_id": "other-authority"})

    decision = _evaluate(
        policy,
        _statement(policy),
        receipt=receipt,
        receipt_authentication=wrong_authority,
    )

    assert decision.reason_code == "RECEIPT_AUTHENTICATION_POLICY_MISMATCH"


def test_known_revocation_dominates_stale_revocation_evidence() -> None:
    base_policy = _policy()
    receipt = _receipt(base_policy).model_copy(
        update={
            "revocation_status": "REVOKED",
            "revocation_checked_at_ms": ADMISSION_AT_MS - 6 * 60_000,
        }
    )
    policy = _policy_for_receipt(base_policy, receipt)

    decision = _evaluate(policy, _statement(policy), receipt=receipt)

    assert decision.reason_code == "CERTIFICATE_REVOKED"


@pytest.mark.parametrize(
    ("fact_updates", "reason_code"),
    [
        (
            {"transparency_integrated_at_ms": ADMISSION_AT_MS + 31_000},
            "TRANSPARENCY_TIME_FUTURE",
        ),
        (
            {"transparency_integrated_at_ms": (ADMISSION_AT_MS - 8 * 86_400_000)},
            "SIGNED_PROVENANCE_STALE",
        ),
        (
            {"certificate_not_after_ms": INTEGRATED_AT_MS - 31_000},
            "SIGNING_TIME_OUTSIDE_CERTIFICATE_VALIDITY",
        ),
        (
            {"transparency_log_verified": False},
            "TRANSPARENCY_VERIFICATION_REQUIRED",
        ),
        (
            {"signed_certificate_timestamp_verified": False},
            "SCT_VERIFICATION_REQUIRED",
        ),
    ],
)
def test_sigstore_fact_time_and_verification_flags_fail_closed(
    fact_updates: dict[str, object],
    reason_code: str,
) -> None:
    policy = _policy()
    facts = _facts(policy).model_copy(update=fact_updates)

    decision = _evaluate(
        policy,
        _statement(policy),
        verification=facts,
    )

    assert decision.verdict == "DENY"
    assert decision.reason_code == reason_code


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_artifact_age_ms", MAX_ARTIFACT_AGE_MS + 1),
        ("max_verification_age_ms", MAX_EVIDENCE_AGE_MS + 1),
        ("max_checkpoint_age_ms", MAX_EVIDENCE_AGE_MS + 1),
        ("max_revocation_age_ms", MAX_EVIDENCE_AGE_MS + 1),
        ("clock_skew_ms", MAX_CLOCK_SKEW_MS + 1),
    ],
)
def test_policy_cannot_disable_freshness_with_unbounded_windows(
    field: str,
    value: int,
) -> None:
    raw = _policy().model_dump(mode="python")
    raw[field] = value

    with pytest.raises(ValidationError):
        ArtifactAdmissionPolicy.model_validate(raw)


def test_trust_contracts_reject_non_string_digests_and_boolean_timestamps() -> None:
    policy_raw = _policy().model_dump(mode="python")
    policy_raw["artifact_sha256"] = 123
    receipt_raw = _receipt(_policy()).model_dump(mode="python")
    receipt_raw["verified_at_ms"] = True

    with pytest.raises(ValidationError):
        ArtifactAdmissionPolicy.model_validate(policy_raw)
    with pytest.raises(ValidationError):
        ArtifactVerificationReceipt.model_validate(receipt_raw)


def test_sigstore_facts_reject_inverted_certificate_window() -> None:
    policy = _policy()
    raw = _facts(policy).model_dump(mode="python")
    raw["certificate_not_before_ms"] = INTEGRATED_AT_MS + 1
    raw["certificate_not_after_ms"] = INTEGRATED_AT_MS

    with pytest.raises(ValidationError):
        VerifiedSigstoreFacts.model_validate(raw)


def test_admission_implementation_contains_no_signing_or_private_key_path() -> None:
    root = Path(__file__).resolve().parents[2]
    core = (root / "services/governance/artifact_admission.py").read_text(encoding="utf-8")
    cli = (root / "tools/verify_artifact_admission.py").read_text(encoding="utf-8")
    receipt_auth = (root / "services/governance/artifact_receipt_auth.py").read_text(encoding="utf-8")
    combined = core + receipt_auth + cli

    assert "sigstore.sign" not in combined
    assert "Signer(" not in combined
    assert "IdentityToken" not in combined
    assert "Ed25519PrivateKey" not in combined
    assert "PRIVATE KEY" not in combined
    assert "Verifier.production" not in combined
    assert "UnsafeNoOp" not in combined
    assert "TrustedRoot.from_file" in cli
    assert "Verifier(trusted_root=" in cli
    assert "Identity(" in cli
    assert "Ed25519PublicKey" in receipt_auth
    assert "verify_signed_artifact_receipt" in cli
    assert "--policy-sha256" not in cli
    assert "POLICY_REGISTRY_PATH" in cli
    assert "POLICY_NOT_TRUSTED" in cli
