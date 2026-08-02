#!/usr/bin/env python
"""Generate deterministic strong-identity baseline evidence without key material."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
from typing import Any, Callable, Sequence

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from quantos.contracts.alpha import Actor, ActorKind  # noqa: E402
from quantos.contracts.hashing import source_file_sha256  # noqa: E402
from quantos.contracts.identity import GovernanceRole  # noqa: E402
from services.governance.approval import (  # noqa: E402
    ApprovalViolation,
    approve,
    open_request,
    review,
)
from services.governance.identity_access_review import (  # noqa: E402
    IdentityAccessReviewError,
    create_identity_access_review,
)
from services.governance.identity_authorization import (  # noqa: E402
    IdentityAuthorizationGrant,
    IdentityAuthorizationRegistry,
    empty_identity_authorization_registry,
    evaluate_authorization,
    load_identity_authorization_registry,
)
from services.governance.oidc_key_registry import (  # noqa: E402
    OidcKeyRegistry,
    OidcVerificationKey,
)
from services.governance.strong_identity import (  # noqa: E402
    OidcMtlsVerifier,
    OidcTrustPolicy,
    StrongIdentityError,
    extract_verified_client_certificate,
    require_role,
)


DEFAULT_OUTPUT = REPO / "evidence" / "strong_identity_baseline.json"
DEFAULT_RECORDED_AT = 1_783_814_400
NOW = 10_000
ISSUER = "https://identity.evidence.invalid"
AUDIENCE = "quantos-governance"

_IMPLEMENTATION_PATHS = (
    "quantos/contracts/identity.py",
    "services/governance/strong_identity.py",
    "services/governance/oidc_key_registry.py",
    "services/governance/identity_access_review.py",
    "services/governance/identity_authorization.py",
    "services/governance/identity_authorization_store.py",
    "config/identity_authorization_registry.json",
    "services/promotion_api/main.py",
    "apps/research-console/src/lib/api/promotion-api.ts",
    "apps/research-console/src/lib/api/governance-pkce.ts",
    "apps/research-console/src/pages/PromotionQueue.tsx",
    "scripts/generate_strong_identity_evidence.py",
)
_VERIFICATION_PATHS = (
    "tests/governance/test_strong_identity.py",
    "tests/governance/test_oidc_key_registry.py",
    "tests/governance/test_identity_access_review.py",
    "tests/governance/test_identity_authorization.py",
    "tests/governance/test_identity_authorization_store.py",
    "tests/governance/test_promotion_api_boundary.py",
    "tests/governance/test_promotion_api_endpoints.py",
    "apps/research-console/src/lib/api/governance-pkce.test.ts",
)
_M4_BLOCKERS = (
    "EXTERNAL_OIDC_ISSUER_OPERATIONAL_EVIDENCE_MISSING",
    "TRUSTED_MTLS_TERMINATOR_DEPLOYMENT_EVIDENCE_MISSING",
    "EXTERNAL_SIGNING_KEY_CUSTODY_AND_ROTATION_EVIDENCE_MISSING",
    "EXTERNAL_IDENTITY_LIFECYCLE_AND_ACCESS_REVIEW_EVIDENCE_MISSING",
    "AI_WORKLOAD_HUMAN_IDENTITY_EXCLUSION_NOT_EXTERNALLY_PROVEN",
)


def _sha256_file(path: Path) -> str:
    return source_file_sha256(path)


def _thumbprint(certificate_der: bytes) -> str:
    digest = hashlib.sha256(certificate_der).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _client_certificate_der() -> bytes:
    key = Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "quantos-evidence-client")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime(2026, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(datetime(2027, 1, 1, tzinfo=timezone.utc))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=True,
        )
        .sign(key, algorithm=None)
    )
    return certificate.public_bytes(serialization.Encoding.DER)


def _claims(certificate_der: bytes, *, subject: str = "alice", **updates: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": subject,
        "aud": AUDIENCE,
        "iat": NOW - 10,
        "nbf": NOW - 10,
        "exp": NOW + 120,
        "auth_time": NOW - 20,
        "jti": f"evidence-{subject}",
        "acr": "urn:quantos:aal2",
        "amr": ["pwd", "mfa"],
        "roles": [role.value for role in GovernanceRole],
        "cnf": {"x5t#S256": _thumbprint(certificate_der)},
    }
    claims.update(updates)
    return claims


def _token(
    key: Ed25519PrivateKey,
    claims: dict[str, Any],
    *,
    typ: str = "at+jwt",
    kid: str = "evidence-key-1",
) -> str:
    return jwt.encode(
        claims,
        key,
        algorithm="EdDSA",
        headers={"typ": typ, "kid": kid},
    )


def _observe(
    case_id: str,
    expected_outcome: str,
    action: Callable[[], object],
    *,
    expected_code: str = "",
    expected_exception: type[BaseException] = StrongIdentityError,
) -> dict[str, Any]:
    try:
        action()
        actual_outcome = "ALLOWED"
        error_code = ""
        exception_name = ""
    except Exception as exc:
        actual_outcome = "DENIED"
        error_code = getattr(exc, "code", "")
        exception_name = type(exc).__name__
        if not isinstance(exc, expected_exception):
            actual_outcome = "UNEXPECTED_ERROR"
    passed = actual_outcome == expected_outcome and (not expected_code or error_code == expected_code)
    return {
        "case_id": case_id,
        "expected_outcome": expected_outcome,
        "actual_outcome": actual_outcome,
        "error_code": error_code,
        "exception": exception_name,
        "passed": passed,
    }


def _three_party_flow(identities: dict[str, Any]) -> None:
    actors = {name: Actor(actor_id=identity.actor_id, kind=ActorKind.HUMAN) for name, identity in identities.items()}
    request = open_request("strong-id-flow", "alpha-promotion:a1:APPROVED", actors["alice"], at=1)
    request = review(request, actors["bob"], at=2)
    approve(request, actors["carol"], at=3)


def _duplicate_subject(identities: dict[str, Any]) -> None:
    actor = Actor(actor_id=identities["alice"].actor_id, kind=ActorKind.HUMAN)
    request = open_request("duplicate-id", "alpha-promotion:a1:APPROVED", actor, at=1)
    review(request, actor, at=2)


def build_strong_identity_evidence(
    repo_root: str | Path = REPO,
    *,
    recorded_at: int = DEFAULT_RECORDED_AT,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    if recorded_at < 0:
        raise ValueError("recorded_at must be non-negative")
    for relative in (*_IMPLEMENTATION_PATHS, *_VERIFICATION_PATHS):
        if not (root / relative).is_file():
            raise ValueError(f"strong-identity evidence source is missing: {relative}")
    default_authorization_registry = load_identity_authorization_registry(
        (root / "config/identity_authorization_registry.json").read_bytes()
    )
    if default_authorization_registry != empty_identity_authorization_registry():
        raise ValueError("the default identity authorization registry must be empty generation zero")

    idp_key = Ed25519PrivateKey.generate()
    public_pem = (
        idp_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    certificate_der = _client_certificate_der()
    policy = OidcTrustPolicy(
        issuer=ISSUER,
        audience=AUDIENCE,
        key_registry=OidcKeyRegistry(
            issuer=ISSUER,
            generation=1,
            keys=(
                OidcVerificationKey(
                    kid="evidence-key-1",
                    algorithm="EdDSA",
                    public_key_pem=public_pem,
                    status="ACTIVE",
                    not_before=NOW - 100,
                    not_after=NOW + 1_000,
                ),
            ),
        ),
        allowed_algorithms=("EdDSA",),
        required_acr_values=("urn:quantos:aal2",),
        required_amr_values=("mfa",),
        maximum_authentication_age_seconds=300,
        maximum_token_lifetime_seconds=600,
        clock_skew_seconds=0,
    )
    verifier = OidcMtlsVerifier(policy)

    def verify_claims(claims: dict[str, Any], *, cert: bytes = certificate_der):
        return verifier.verify(
            _token(idp_key, claims),
            client_certificate_der=cert,
            now=NOW,
        )

    identities = {
        subject: verify_claims(_claims(certificate_der, subject=subject)) for subject in ("alice", "bob", "carol")
    }
    valid_identity_path_passed = all(identity.assurance.value == "OIDC_MTLS_MFA" for identity in identities.values())

    def reviewed_grant(
        *,
        subject: str,
        principal_kind: str,
        roles: tuple[GovernanceRole, ...],
        enabled: bool = True,
        revoked: bool = False,
        tokens_valid_after_ms: int = 0,
        access_review_expires_at_ms: int = (NOW + 1_000) * 1_000,
    ) -> IdentityAuthorizationGrant:
        reviewer = identities["alice"] if subject != "alice" else identities["bob"]
        access_review = None
        if enabled and not revoked:
            access_review = create_identity_access_review(
                subject_issuer=ISSUER,
                subject=subject,
                principal_kind=principal_kind,
                roles=roles,
                reviewer=reviewer,
                reviewed_at_ms=(NOW - 10) * 1_000,
                expires_at_ms=access_review_expires_at_ms,
            )
        return IdentityAuthorizationGrant(
            issuer=ISSUER,
            subject=subject,
            principal_kind=principal_kind,
            enabled=enabled,
            revoked=revoked,
            roles=roles,
            tokens_valid_after_ms=tokens_valid_after_ms,
            access_review_expires_at_ms=access_review_expires_at_ms,
            access_review=access_review,
        )

    authorization_grants = {
        identity.actor_id: reviewed_grant(
            subject=identity.subject,
            principal_kind="HUMAN",
            roles=tuple(GovernanceRole),
        )
        for identity in identities.values()
    }
    authorization_registry = IdentityAuthorizationRegistry(
        generation=1,
        principals=authorization_grants,
    )

    def authorize_or_raise(
        identity,
        role: GovernanceRole,
        *,
        registry: IdentityAuthorizationRegistry = authorization_registry,
    ) -> None:
        decision = evaluate_authorization(
            registry,
            identity,
            role,
            evaluated_at_ms=NOW * 1_000,
        )
        if decision.verdict != "ALLOW":
            raise StrongIdentityError(
                decision.reason_code,
                "application authorization denied",
            )

    application_authorization_valid_path_passed = all(
        evaluate_authorization(
            authorization_registry,
            identity,
            role,
            evaluated_at_ms=NOW * 1_000,
        ).verdict
        == "ALLOW"
        for identity, role in zip(
            identities.values(),
            (
                GovernanceRole.PROMOTION_REQUESTER,
                GovernanceRole.PROMOTION_REVIEWER,
                GovernanceRole.PROMOTION_APPROVER,
            ),
            strict=True,
        )
    )

    attacker_key = Ed25519PrivateKey.generate()
    attacker_public_pem = (
        attacker_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    revoked_verifier = OidcMtlsVerifier(
        policy.model_copy(
            update={
                "key_registry": OidcKeyRegistry(
                    issuer=ISSUER,
                    generation=2,
                    keys=(
                        OidcVerificationKey(
                            kid="evidence-key-1",
                            algorithm="EdDSA",
                            public_key_pem=public_pem,
                            status="REVOKED",
                            not_before=NOW - 100,
                            not_after=NOW + 1_000,
                        ),
                        OidcVerificationKey(
                            kid="evidence-key-2",
                            algorithm="EdDSA",
                            public_key_pem=attacker_public_pem,
                            status="ACTIVE",
                            not_before=NOW - 100,
                            not_after=NOW + 1_000,
                        ),
                    ),
                )
            }
        )
    )
    reader_identity = verify_claims(
        _claims(
            certificate_der,
            subject="reader",
            roles=[GovernanceRole.PROMOTION_READER.value],
        )
    )
    self_escalator_identity = verify_claims(
        _claims(
            certificate_der,
            subject="self-escalator",
            roles=[GovernanceRole.PROMOTION_APPROVER.value],
        )
    )
    workload_identity = verify_claims(
        _claims(
            certificate_der,
            subject="research-agent",
            roles=[GovernanceRole.PROMOTION_APPROVER.value],
        )
    )

    def one_grant_registry(
        grant: IdentityAuthorizationGrant,
    ) -> IdentityAuthorizationRegistry:
        return IdentityAuthorizationRegistry(
            generation=1,
            principals={grant.actor_id: grant},
        )

    self_escalation_registry = one_grant_registry(
        reviewed_grant(
            subject="self-escalator",
            principal_kind="HUMAN",
            roles=(GovernanceRole.PROMOTION_READER,),
        )
    )
    revoked_registry = one_grant_registry(
        reviewed_grant(
            subject="self-escalator",
            principal_kind="HUMAN",
            enabled=False,
            revoked=True,
            roles=(GovernanceRole.PROMOTION_APPROVER,),
        )
    )
    stale_review_registry = one_grant_registry(
        reviewed_grant(
            subject="self-escalator",
            principal_kind="HUMAN",
            roles=(GovernanceRole.PROMOTION_APPROVER,),
            access_review_expires_at_ms=NOW * 1_000,
        )
    )
    old_token_registry = one_grant_registry(
        reviewed_grant(
            subject="self-escalator",
            principal_kind="HUMAN",
            roles=(GovernanceRole.PROMOTION_APPROVER,),
            tokens_valid_after_ms=(NOW - 5) * 1_000,
        )
    )
    workload_registry = one_grant_registry(
        reviewed_grant(
            subject="research-agent",
            principal_kind="WORKLOAD",
            roles=(GovernanceRole.PROMOTION_READER,),
        )
    )
    attack_cases = [
        _observe(
            "wrong-signature",
            "DENIED",
            lambda: verifier.verify(
                _token(attacker_key, _claims(certificate_der)),
                client_certificate_der=certificate_der,
                now=NOW,
            ),
            expected_code="INVALID_SIGNATURE",
        ),
        _observe(
            "symmetric-algorithm-confusion",
            "DENIED",
            lambda: verifier.verify(
                jwt.encode(
                    _claims(certificate_der),
                    "ephemeral-attacker-value-at-least-32-bytes",
                    algorithm="HS256",
                    headers={"typ": "at+jwt", "kid": "evidence-key-1"},
                ),
                client_certificate_der=certificate_der,
                now=NOW,
            ),
            expected_code="ALGORITHM_NOT_ALLOWED",
        ),
        _observe(
            "wrong-issuer",
            "DENIED",
            lambda: verify_claims(_claims(certificate_der, iss="https://attacker.invalid")),
            expected_code="INVALID_ISSUER",
        ),
        _observe(
            "wrong-audience",
            "DENIED",
            lambda: verify_claims(_claims(certificate_der, aud="other-service")),
            expected_code="INVALID_AUDIENCE",
        ),
        _observe(
            "expired-token",
            "DENIED",
            lambda: verify_claims(_claims(certificate_der, exp=NOW)),
            expected_code="TOKEN_EXPIRED",
        ),
        _observe(
            "stale-authentication",
            "DENIED",
            lambda: verify_claims(_claims(certificate_der, auth_time=NOW - 301)),
            expected_code="STALE_AUTHENTICATION",
        ),
        _observe(
            "missing-mfa",
            "DENIED",
            lambda: verify_claims(_claims(certificate_der, amr=["pwd"])),
            expected_code="AMR_REQUIRED",
        ),
        _observe(
            "wrong-certificate-binding",
            "DENIED",
            lambda: verify_claims(_claims(certificate_der), cert=b"different-certificate"),
            expected_code="CERTIFICATE_BINDING_MISMATCH",
        ),
        _observe(
            "missing-key-id",
            "DENIED",
            lambda: verifier.verify(
                _token(idp_key, _claims(certificate_der), kid=""),
                client_certificate_der=certificate_der,
                now=NOW,
            ),
            expected_code="OIDC_KEY_ID_REQUIRED",
        ),
        _observe(
            "unknown-key-id",
            "DENIED",
            lambda: verifier.verify(
                _token(idp_key, _claims(certificate_der), kid="unknown"),
                client_certificate_der=certificate_der,
                now=NOW,
            ),
            expected_code="OIDC_KEY_NOT_TRUSTED",
        ),
        _observe(
            "revoked-key-id",
            "DENIED",
            lambda: revoked_verifier.verify(
                _token(idp_key, _claims(certificate_der)),
                client_certificate_der=certificate_der,
                now=NOW,
            ),
            expected_code="OIDC_KEY_REVOKED",
        ),
        _observe(
            "reader-role-escalation",
            "DENIED",
            lambda: require_role(reader_identity, GovernanceRole.PROMOTION_APPROVER),
            expected_code="ROLE_REQUIRED",
        ),
        _observe(
            "token-role-self-enrollment",
            "DENIED",
            lambda: authorize_or_raise(
                self_escalator_identity,
                GovernanceRole.PROMOTION_APPROVER,
                registry=empty_identity_authorization_registry(),
            ),
            expected_code="PRINCIPAL_NOT_REGISTERED",
        ),
        _observe(
            "token-role-self-escalation",
            "DENIED",
            lambda: authorize_or_raise(
                self_escalator_identity,
                GovernanceRole.PROMOTION_APPROVER,
                registry=self_escalation_registry,
            ),
            expected_code="ROLE_NOT_GRANTED",
        ),
        _observe(
            "revoked-application-principal",
            "DENIED",
            lambda: authorize_or_raise(
                self_escalator_identity,
                GovernanceRole.PROMOTION_APPROVER,
                registry=revoked_registry,
            ),
            expected_code="PRINCIPAL_REVOKED",
        ),
        _observe(
            "self-approved-access-review",
            "DENIED",
            lambda: create_identity_access_review(
                subject_issuer=ISSUER,
                subject="alice",
                principal_kind="HUMAN",
                roles=(GovernanceRole.PROMOTION_APPROVER,),
                reviewer=identities["alice"],
                reviewed_at_ms=NOW * 1_000,
                expires_at_ms=(NOW + 1_000) * 1_000,
            ),
            expected_code="ACCESS_REVIEW_SELF_REVIEW_FORBIDDEN",
            expected_exception=IdentityAccessReviewError,
        ),
        _observe(
            "expired-application-access-review",
            "DENIED",
            lambda: authorize_or_raise(
                self_escalator_identity,
                GovernanceRole.PROMOTION_APPROVER,
                registry=stale_review_registry,
            ),
            expected_code="ACCESS_REVIEW_EXPIRED",
        ),
        _observe(
            "token-before-application-epoch",
            "DENIED",
            lambda: authorize_or_raise(
                self_escalator_identity,
                GovernanceRole.PROMOTION_APPROVER,
                registry=old_token_registry,
            ),
            expected_code="TOKEN_ISSUED_BEFORE_AUTHORIZATION",
        ),
        _observe(
            "workload-human-governance-action",
            "DENIED",
            lambda: authorize_or_raise(
                workload_identity,
                GovernanceRole.PROMOTION_APPROVER,
                registry=workload_registry,
            ),
            expected_code="WORKLOAD_HUMAN_ACTION_FORBIDDEN",
        ),
        _observe(
            "untrusted-certificate-header",
            "DENIED",
            lambda: extract_verified_client_certificate({"headers": [(b"x-client-cert", b"spoofed")]}),
            expected_code="MISSING_VERIFIED_CLIENT_CERTIFICATE",
        ),
        _observe(
            "legacy-shared-secret",
            "DENIED",
            lambda: verifier.verify(
                "not-a-jwt",
                client_certificate_der=certificate_der,
                now=NOW,
            ),
            expected_code="MALFORMED_TOKEN",
        ),
        _observe(
            "wrong-token-type",
            "DENIED",
            lambda: verifier.verify(
                _token(idp_key, _claims(certificate_der), typ="JWT"),
                client_certificate_der=certificate_der,
                now=NOW,
            ),
            expected_code="INVALID_TOKEN_TYPE",
        ),
        _observe(
            "duplicate-human-subject",
            "DENIED",
            lambda: _duplicate_subject(identities),
            expected_exception=ApprovalViolation,
        ),
    ]

    def authorized_three_party_flow() -> None:
        for identity, role in zip(
            identities.values(),
            (
                GovernanceRole.PROMOTION_REQUESTER,
                GovernanceRole.PROMOTION_REVIEWER,
                GovernanceRole.PROMOTION_APPROVER,
            ),
            strict=True,
        ):
            authorize_or_raise(identity, role)
        _three_party_flow(identities)

    three_party = _observe(
        "three-distinct-human-subjects",
        "ALLOWED",
        authorized_three_party_flow,
    )

    promotion_source = (root / "services/promotion_api/main.py").read_text(encoding="utf-8")
    frontend_source = (root / "apps/research-console/src/lib/api/promotion-api.ts").read_text(encoding="utf-8")
    pkce_source = (root / "apps/research-console/src/lib/api/governance-pkce.ts").read_text(encoding="utf-8")
    legacy_markers = (
        "X-Gov-Token",
        "X-Operator",
        "governance_token.txt",
        "_load_or_create_token",
        "token_urlsafe",
    )
    legacy_absent = not any(marker in promotion_source for marker in legacy_markers)
    self_asserted_absent = "X-Operator" not in frontend_source
    pkce_markers = (
        "code_challenge_method",
        "S256",
        "getRandomValues",
        "subtle.digest",
        "response_type",
        "state",
        "nonce",
        "PKCE_TRANSACTION_STORAGE_KEY",
        "MAX_TRANSACTION_AGE_MS",
        "storage.removeItem",
        "response = await runtime.fetch",
    )
    pkce_ready = (
        all(marker in pkce_source for marker in pkce_markers)
        and "client_secret" not in pkce_source
        and "localStorage" not in pkce_source
        and "sessionStorage" not in frontend_source
        and "localStorage" not in frontend_source
        and pkce_source.index("storage.removeItem") < pkce_source.index("response = await runtime.fetch")
    )
    all_attacks_passed = all(case["passed"] for case in attack_cases)
    if not (
        valid_identity_path_passed
        and application_authorization_valid_path_passed
        and three_party["passed"]
        and all_attacks_passed
        and legacy_absent
        and self_asserted_absent
        and pkce_ready
    ):
        raise ValueError("strong-identity baseline did not satisfy its fail-closed invariants")

    return {
        "schema_version": 1,
        "verdict": "PARTIAL",
        "mode": "OFFLINE_STRONG_IDENTITY_BASELINE",
        "current_maturity": "M3",
        "recorded_at": recorded_at,
        "authentication_profile": "OIDC_ASYMMETRIC_MTLS_MFA",
        "allowed_algorithms": ["EdDSA", "RS256"],
        "oidc_key_registry_status": (
            "KID_SELECTED_DIGEST_AND_MIN_GENERATION_PINNED_ROTATION_REVOCATION_ENFORCED"
        ),
        "token_binding_standard": "RFC8705_CNF_X5T_S256",
        "actor_source": "OIDC_ISSUER_AND_IMMUTABLE_SUBJECT",
        "governance_roles": [role.value for role in GovernanceRole],
        "valid_identity_path_passed": True,
        "application_authorization_valid_path_passed": True,
        "application_authorization_registry_status": ("EMPTY_DEFAULT_FAIL_CLOSED_GENERATION_0"),
        "application_authorization_decision_authority_status": ("LOCAL_SINGLE_SQLITE_REGISTRY_DECISION_CAS_UNSEALED"),
        "application_authorization_principal_classes": ["HUMAN", "WORKLOAD"],
        "application_authorization_token_role_mode": ("TOKEN_AND_APPLICATION_GRANT_INTERSECTION"),
        "access_review_attestation_status": (
            "HASH_BOUND_INDEPENDENT_STRONG_IDENTITY_REVIEW_ENFORCED_LOCAL_AUTHORITY"
        ),
        "three_party_identity_separation_passed": True,
        "three_party_case": three_party,
        "attack_cases": attack_cases,
        "all_attack_cases_passed": True,
        "legacy_shared_token_accepted": False,
        "self_asserted_operator_accepted": False,
        "implementation_sha256": {path: _sha256_file(root / path) for path in _IMPLEMENTATION_PATHS},
        "verification_paths": list(_VERIFICATION_PATHS),
        "dependency_versions": {
            "PyJWT": version("PyJWT"),
            "cryptography": version("cryptography"),
        },
        "external_oidc_deployment_status": "NOT_PROVIDED",
        "trusted_mtls_deployment_status": "NOT_PROVIDED",
        "browser_pkce_or_bff_status": ("AUTHORIZATION_CODE_PKCE_S256_IMPLEMENTED_EXTERNAL_IDP_NOT_PROVIDED"),
        "browser_pkce_transaction_storage": ("VERSIONED_SESSION_STORAGE_NO_ACCESS_TOKEN"),
        "browser_access_token_storage": "IN_MEMORY_ONLY",
        "browser_pkce_client_secret_present": False,
        "browser_pkce_one_time_state_nonce": True,
        "browser_pkce_max_transaction_age_seconds": 600,
        "browser_pkce_invariants_verified": True,
        "external_key_custody_status": "NOT_PROVIDED",
        "identity_lifecycle_evidence_status": (
            "LOCAL_OIDC_KEY_REVOCATION_TOKEN_EPOCH_HASH_BOUND_ACCESS_REVIEW_ENFORCED_EXTERNAL_OPERATIONS_NOT_PROVIDED"
        ),
        "ai_human_identity_exclusion_status": ("LOCAL_HUMAN_WORKLOAD_CLASS_ENFORCED_NOT_EXTERNALLY_PROVEN"),
        "live_trading_authority_granted": False,
        "credential_authority_granted": False,
        "order_submission_authority_granted": False,
        "m4_blockers": list(_M4_BLOCKERS),
    }


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic QUANTOS strong-identity baseline evidence")
    parser.add_argument("--repo-root", default=str(REPO))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--recorded-at", type=int, default=DEFAULT_RECORDED_AT)
    args = parser.parse_args(argv)
    try:
        evidence = build_strong_identity_evidence(args.repo_root, recorded_at=args.recorded_at)
        _write(Path(args.output), evidence)
    except (OSError, ValueError) as exc:
        print(f"strong-identity evidence generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"strong-identity baseline: {len(evidence['attack_cases'])} attacks; maturity={evidence['current_maturity']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
