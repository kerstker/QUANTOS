from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from quantos.contracts.identity import (
    GovernanceRole,
    StrongIdentityAssurance,
    VerifiedIdentity,
)
from services.governance.identity_access_review import (
    MAX_ACCESS_REVIEW_VALIDITY_MS,
    IdentityAccessReviewAttestation,
    IdentityAccessReviewError,
    create_identity_access_review,
)
from services.governance.identity_authorization import (
    IdentityAuthorizationGrant,
    IdentityAuthorizationRegistry,
    evaluate_authorization,
)


ISSUER = "https://identity.example.test"
NOW_S = 1_700_000_000
NOW_MS = NOW_S * 1_000


def _identity(
    subject: str,
    roles: tuple[GovernanceRole, ...],
    *,
    issued_at: int = NOW_S - 30,
    not_before: int = NOW_S - 30,
    expires_at: int = NOW_S + 300,
) -> VerifiedIdentity:
    return VerifiedIdentity(
        issuer=ISSUER,
        subject=subject,
        audience="quantos-governance",
        roles=roles,
        assurance=StrongIdentityAssurance.OIDC_MTLS_MFA,
        acr="urn:quantos:aal2",
        amr=("pwd", "mfa"),
        issued_at=issued_at,
        not_before=not_before,
        authentication_time=issued_at - 1,
        expires_at=expires_at,
        token_id=f"token-{subject}",
        certificate_sha256=hashlib.sha256(subject.encode("utf-8")).hexdigest(),
    )


def _review(
    *,
    subject: str = "alice",
    principal_kind: str = "HUMAN",
    roles: tuple[GovernanceRole, ...] = (GovernanceRole.PROMOTION_APPROVER,),
    reviewer: VerifiedIdentity | None = None,
    reviewed_at_ms: int = NOW_MS,
    expires_at_ms: int = NOW_MS + 86_400_000,
) -> IdentityAccessReviewAttestation:
    return create_identity_access_review(
        subject_issuer=ISSUER,
        subject=subject,
        principal_kind=principal_kind,
        roles=roles,
        reviewer=reviewer
        or _identity("reviewer", (GovernanceRole.PROMOTION_REVIEWER,)),
        reviewed_at_ms=reviewed_at_ms,
        expires_at_ms=expires_at_ms,
    )


def test_review_attestation_is_load_bearing_in_authorization_decision() -> None:
    review = _review()
    grant = IdentityAuthorizationGrant(
        issuer=ISSUER,
        subject="alice",
        principal_kind="HUMAN",
        enabled=True,
        revoked=False,
        roles=(GovernanceRole.PROMOTION_APPROVER,),
        tokens_valid_after_ms=NOW_MS - 60_000,
        access_review_expires_at_ms=review.expires_at_ms,
        access_review=review,
    )
    registry = IdentityAuthorizationRegistry(
        generation=1,
        principals={grant.actor_id: grant},
    )

    decision = evaluate_authorization(
        registry,
        _identity("alice", (GovernanceRole.PROMOTION_APPROVER,)),
        GovernanceRole.PROMOTION_APPROVER,
        evaluated_at_ms=NOW_MS + 1,
    )

    assert decision.verdict == "ALLOW"
    assert decision.access_review_id == review.review_id
    assert decision.access_review_content_sha256 == review.content_sha256
    assert decision.access_reviewed_by_actor_id == review.reviewer_actor_id


@pytest.mark.parametrize(
    ("reviewer", "reviewed_at_ms", "expected_code"),
    (
        (
            _identity("alice", (GovernanceRole.PROMOTION_REVIEWER,)),
            NOW_MS,
            "ACCESS_REVIEW_SELF_REVIEW_FORBIDDEN",
        ),
        (
            _identity("reader", (GovernanceRole.PROMOTION_READER,)),
            NOW_MS,
            "ACCESS_REVIEWER_ROLE_REQUIRED",
        ),
        (
            _identity(
                "reviewer",
                (GovernanceRole.PROMOTION_REVIEWER,),
                expires_at=NOW_S,
            ),
            NOW_MS,
            "ACCESS_REVIEWER_TOKEN_EXPIRED",
        ),
    ),
)
def test_self_review_role_escalation_and_expired_reviewer_are_denied(
    reviewer: VerifiedIdentity,
    reviewed_at_ms: int,
    expected_code: str,
) -> None:
    with pytest.raises(IdentityAccessReviewError) as error:
        _review(reviewer=reviewer, reviewed_at_ms=reviewed_at_ms)
    assert error.value.code == expected_code


def test_review_validity_is_bounded_and_content_is_tamper_evident() -> None:
    with pytest.raises(IdentityAccessReviewError) as error:
        _review(expires_at_ms=NOW_MS + MAX_ACCESS_REVIEW_VALIDITY_MS + 1)
    assert error.value.code == "ACCESS_REVIEW_VALIDITY_EXCEEDED"

    review = _review()
    with pytest.raises(ValidationError, match="content hash"):
        IdentityAccessReviewAttestation.model_validate(
            {
                **review.model_dump(mode="json"),
                "reviewer_token_id": "tampered-token",
            }
        )


def test_enabled_grant_requires_exact_independent_review_binding() -> None:
    base = {
        "issuer": ISSUER,
        "subject": "alice",
        "principal_kind": "HUMAN",
        "enabled": True,
        "revoked": False,
        "roles": (GovernanceRole.PROMOTION_APPROVER,),
        "tokens_valid_after_ms": NOW_MS - 60_000,
        "access_review_expires_at_ms": NOW_MS + 86_400_000,
    }
    with pytest.raises(ValidationError, match="access review attestation"):
        IdentityAuthorizationGrant(**base)

    wrong_roles = _review(roles=(GovernanceRole.PROMOTION_READER,))
    with pytest.raises(ValidationError, match="reviewed roles"):
        IdentityAuthorizationGrant(**base, access_review=wrong_roles)

    wrong_expiry = _review(expires_at_ms=NOW_MS + 43_200_000)
    with pytest.raises(ValidationError, match="review expiry"):
        IdentityAuthorizationGrant(**base, access_review=wrong_expiry)


def test_emergency_revocation_does_not_depend_on_a_fresh_review() -> None:
    revoked = IdentityAuthorizationGrant(
        issuer=ISSUER,
        subject="alice",
        principal_kind="HUMAN",
        enabled=False,
        revoked=True,
        roles=(GovernanceRole.PROMOTION_APPROVER,),
        tokens_valid_after_ms=NOW_MS,
        access_review_expires_at_ms=NOW_MS,
        access_review=None,
    )
    registry = IdentityAuthorizationRegistry(
        generation=2,
        principals={revoked.actor_id: revoked},
    )

    decision = evaluate_authorization(
        registry,
        _identity("alice", (GovernanceRole.PROMOTION_APPROVER,)),
        GovernanceRole.PROMOTION_APPROVER,
        evaluated_at_ms=NOW_MS,
    )

    assert decision.verdict == "DENY"
    assert decision.reason_code == "PRINCIPAL_REVOKED"
