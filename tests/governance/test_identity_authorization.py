from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from quantos.contracts.identity import (
    GovernanceRole,
    StrongIdentityAssurance,
    VerifiedIdentity,
)
from services.governance.identity_access_review import create_identity_access_review
from services.governance.identity_authorization import (
    IdentityAuthorizationGrant,
    IdentityAuthorizationRegistry,
    IdentityAuthorizationRegistryError,
    empty_identity_authorization_registry,
    evaluate_authorization,
    load_identity_authorization_registry,
    validate_registry_successor,
)


ISSUER = "https://identity.example.test"
AUDIENCE = "quantos-governance"
NOW_MS = 50_000


def _identity(
    *,
    subject: str = "alice",
    roles: tuple[GovernanceRole, ...] = (GovernanceRole.PROMOTION_APPROVER,),
    issued_at: int = 40,
    not_before: int = 40,
    expires_at: int = 60,
) -> VerifiedIdentity:
    return VerifiedIdentity(
        issuer=ISSUER,
        subject=subject,
        audience=AUDIENCE,
        roles=roles,
        assurance=StrongIdentityAssurance.OIDC_MTLS_MFA,
        acr="urn:quantos:aal2",
        amr=("pwd", "mfa"),
        issued_at=issued_at,
        not_before=not_before,
        authentication_time=issued_at - 1,
        expires_at=expires_at,
        token_id=f"token-{subject}",
        certificate_sha256="a" * 64,
    )


def _grant(
    *,
    subject: str = "alice",
    principal_kind: str = "HUMAN",
    enabled: bool = True,
    revoked: bool = False,
    roles: tuple[GovernanceRole, ...] = (GovernanceRole.PROMOTION_APPROVER,),
    tokens_valid_after_ms: int = 30_000,
    access_review_expires_at_ms: int = 60_000,
) -> IdentityAuthorizationGrant:
    access_review = None
    if enabled and not revoked:
        access_review = create_identity_access_review(
            subject_issuer=ISSUER,
            subject=subject,
            principal_kind=principal_kind,
            roles=roles,
            reviewer=_identity(
                subject="independent-reviewer",
                roles=(GovernanceRole.PROMOTION_REVIEWER,),
                issued_at=10,
                not_before=10,
                expires_at=60,
            ),
            reviewed_at_ms=20_000,
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


def _registry(
    grant: IdentityAuthorizationGrant | None = None,
    *,
    generation: int = 1,
) -> IdentityAuthorizationRegistry:
    resolved = grant or _grant()
    return IdentityAuthorizationRegistry(
        generation=generation,
        principals={resolved.actor_id: resolved},
    )


def test_empty_default_denies_without_granting_external_authority() -> None:
    registry = empty_identity_authorization_registry()

    decision = evaluate_authorization(
        registry,
        _identity(),
        GovernanceRole.PROMOTION_APPROVER,
        evaluated_at_ms=NOW_MS,
    )

    assert registry.generation == 0
    assert registry.principals == {}
    assert decision.verdict == "DENY"
    assert decision.reason_code == "PRINCIPAL_NOT_REGISTERED"
    assert decision.execution_authority_granted is False
    assert decision.deployment_authority_granted is False
    assert decision.live_trading_authority_granted is False
    assert decision.credential_authority_granted is False
    assert decision.order_submission_authority_granted is False


def test_exact_human_grant_and_token_role_are_both_required() -> None:
    registry = _registry()

    allowed = evaluate_authorization(
        registry,
        _identity(),
        GovernanceRole.PROMOTION_APPROVER,
        evaluated_at_ms=NOW_MS,
    )
    token_self_escalation = evaluate_authorization(
        _registry(
            _grant(roles=(GovernanceRole.PROMOTION_READER,)),
        ),
        _identity(roles=(GovernanceRole.PROMOTION_APPROVER,)),
        GovernanceRole.PROMOTION_APPROVER,
        evaluated_at_ms=NOW_MS,
    )
    missing_token_role = evaluate_authorization(
        registry,
        _identity(roles=(GovernanceRole.PROMOTION_READER,)),
        GovernanceRole.PROMOTION_APPROVER,
        evaluated_at_ms=NOW_MS,
    )

    assert allowed.verdict == "ALLOW"
    assert allowed.reason_code == "GOVERNANCE_ROLE_AUTHORIZED"
    assert allowed.actor_id == f"{ISSUER}|alice"
    assert allowed.registry_generation == 1
    assert allowed.requested_role == GovernanceRole.PROMOTION_APPROVER
    assert allowed.token_id == "token-alice"
    assert allowed.certificate_sha256 == "a" * 64
    assert allowed.execution_authority_granted is False
    assert allowed.deployment_authority_granted is False
    assert allowed.live_trading_authority_granted is False
    assert token_self_escalation.reason_code == "ROLE_NOT_GRANTED"
    assert missing_token_role.reason_code == "TOKEN_ROLE_REQUIRED"


@pytest.mark.parametrize(
    ("grant", "reason_code"),
    [
        (_grant(enabled=False), "PRINCIPAL_DISABLED"),
        (_grant(enabled=False, revoked=True), "PRINCIPAL_REVOKED"),
        (
            _grant(access_review_expires_at_ms=NOW_MS),
            "ACCESS_REVIEW_EXPIRED",
        ),
        (
            _grant(tokens_valid_after_ms=40_001),
            "TOKEN_ISSUED_BEFORE_AUTHORIZATION",
        ),
    ],
)
def test_lifecycle_state_is_fail_closed(
    grant: IdentityAuthorizationGrant,
    reason_code: str,
) -> None:
    decision = evaluate_authorization(
        _registry(grant),
        _identity(),
        GovernanceRole.PROMOTION_APPROVER,
        evaluated_at_ms=NOW_MS,
    )

    assert decision.verdict == "DENY"
    assert decision.reason_code == reason_code


def test_workload_cannot_request_a_human_governance_action() -> None:
    grant = _grant(
        subject="research-agent",
        principal_kind="WORKLOAD",
        roles=(GovernanceRole.PROMOTION_READER,),
    )
    identity = _identity(
        subject="research-agent",
        roles=(
            GovernanceRole.PROMOTION_READER,
            GovernanceRole.PROMOTION_APPROVER,
        ),
    )

    denied = evaluate_authorization(
        _registry(grant),
        identity,
        GovernanceRole.PROMOTION_APPROVER,
        evaluated_at_ms=NOW_MS,
    )
    reader = evaluate_authorization(
        _registry(grant),
        identity,
        GovernanceRole.PROMOTION_READER,
        evaluated_at_ms=NOW_MS,
    )

    assert denied.verdict == "DENY"
    assert denied.reason_code == "WORKLOAD_HUMAN_ACTION_FORBIDDEN"
    assert reader.verdict == "ALLOW"


def test_verified_identity_time_window_is_rechecked_at_authorization() -> None:
    registry = _registry()

    future_issued = evaluate_authorization(
        registry,
        _identity(issued_at=51, not_before=51),
        GovernanceRole.PROMOTION_APPROVER,
        evaluated_at_ms=NOW_MS,
    )
    not_yet_valid = evaluate_authorization(
        registry,
        _identity(not_before=51),
        GovernanceRole.PROMOTION_APPROVER,
        evaluated_at_ms=NOW_MS,
    )
    expired = evaluate_authorization(
        registry,
        _identity(expires_at=50),
        GovernanceRole.PROMOTION_APPROVER,
        evaluated_at_ms=NOW_MS,
    )

    assert future_issued.reason_code == "TOKEN_ISSUED_IN_FUTURE"
    assert not_yet_valid.reason_code == "TOKEN_NOT_YET_VALID"
    assert expired.reason_code == "TOKEN_EXPIRED"


def test_registry_contract_is_strict_and_actor_keys_are_canonical() -> None:
    grant = _grant()
    registry = _registry(grant)
    raw = registry.model_dump(mode="python")

    with pytest.raises(TypeError):
        registry.principals["mutated"] = grant

    invalid_documents = (
        {**raw, "generation": True},
        {**raw, "unexpected": "field"},
        {
            **raw,
            "principals": {"wrong-key": grant.model_dump(mode="python")},
        },
        {
            **raw,
            "principals": {
                grant.actor_id: {
                    **grant.model_dump(mode="python"),
                    "roles": [
                        GovernanceRole.PROMOTION_APPROVER,
                        GovernanceRole.PROMOTION_APPROVER,
                    ],
                }
            },
        },
    )
    for invalid in invalid_documents:
        with pytest.raises(ValidationError):
            IdentityAuthorizationRegistry.model_validate(invalid)

    with pytest.raises(ValidationError):
        IdentityAuthorizationGrant(
            **{
                **grant.model_dump(mode="python"),
                "issuer": "https://identity.example.test|collision",
            }
        )
    with pytest.raises(ValidationError):
        _grant(
            subject="research-agent",
            principal_kind="WORKLOAD",
            roles=(GovernanceRole.PROMOTION_APPROVER,),
        )


def test_registry_loader_rejects_duplicate_json_keys() -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_identity_authorization_registry(
            '{"schema_version":1,"schema_version":1,"readiness":"L2_DRY_RUN_ONLY","generation":0,"principals":{}}'
        )

    encoded = json.dumps(empty_identity_authorization_registry().model_dump(mode="json"))
    assert load_identity_authorization_registry(encoded) == (empty_identity_authorization_registry())


def test_registry_generation_transition_is_monotonic_and_idempotent() -> None:
    current = empty_identity_authorization_registry()
    successor = _registry(generation=1)

    assert validate_registry_successor(current, current) == current
    assert validate_registry_successor(current, successor) == successor

    conflict = IdentityAuthorizationRegistry(
        generation=0,
        principals={_grant().actor_id: _grant()},
    )
    with pytest.raises(IdentityAuthorizationRegistryError) as conflict_error:
        validate_registry_successor(current, conflict)
    assert conflict_error.value.code == "REGISTRY_GENERATION_CONFLICT"

    with pytest.raises(IdentityAuthorizationRegistryError) as rollback_error:
        validate_registry_successor(successor, current)
    assert rollback_error.value.code == "REGISTRY_GENERATION_ROLLBACK"

    gap = _registry(generation=3)
    with pytest.raises(IdentityAuthorizationRegistryError) as gap_error:
        validate_registry_successor(current, gap)
    assert gap_error.value.code == "REGISTRY_GENERATION_GAP"
