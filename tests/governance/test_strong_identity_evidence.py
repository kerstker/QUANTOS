import json
from pathlib import Path

from scripts.generate_strong_identity_evidence import build_strong_identity_evidence


def _fixture_repo(tmp_path: Path) -> None:
    sources = {
        "quantos/contracts/identity.py": "identity contract\n",
        "services/governance/strong_identity.py": "strong verifier\n",
        "services/governance/oidc_key_registry.py": "OIDC rotating key registry\n",
        "services/governance/identity_access_review.py": "access review attestation\n",
        "services/governance/identity_authorization.py": "authorization core\n",
        "services/governance/identity_authorization_store.py": "authorization store\n",
        "config/identity_authorization_registry.json": (
            '{"schema_version":1,"readiness":"L2_DRY_RUN_ONLY","generation":0,"principals":{}}\n'
        ),
        "services/promotion_api/main.py": ("Authorization OIDC_MTLS_MFA_RBAC PROMOTION_APPROVER PROMOTION_EXECUTOR\n"),
        "apps/research-console/src/lib/api/promotion-api.ts": "Authorization Bearer\n",
        "apps/research-console/src/lib/api/governance-pkce.ts": (
            "code_challenge_method S256 getRandomValues subtle.digest response_type "
            "state nonce PKCE_TRANSACTION_STORAGE_KEY MAX_TRANSACTION_AGE_MS "
            "storage.removeItem response = await runtime.fetch access token memory only\n"
        ),
        "apps/research-console/src/pages/PromotionQueue.tsx": "OIDC mTLS\n",
        "scripts/generate_strong_identity_evidence.py": "generator\n",
        "tests/governance/test_strong_identity.py": "tests\n",
        "tests/governance/test_oidc_key_registry.py": "tests\n",
        "tests/governance/test_identity_access_review.py": "tests\n",
        "tests/governance/test_identity_authorization.py": "tests\n",
        "tests/governance/test_identity_authorization_store.py": "tests\n",
        "tests/governance/test_promotion_api_boundary.py": "tests\n",
        "tests/governance/test_promotion_api_endpoints.py": "tests\n",
        "apps/research-console/src/lib/api/governance-pkce.test.ts": "tests\n",
    }
    for relative, content in sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_strong_identity_evidence_is_deterministic_and_contains_no_key_material(tmp_path):
    _fixture_repo(tmp_path)
    first = build_strong_identity_evidence(tmp_path, recorded_at=1)
    second = build_strong_identity_evidence(tmp_path, recorded_at=1)
    assert first == second
    assert first["current_maturity"] == "M3"
    assert first["verdict"] == "PARTIAL"
    assert first["valid_identity_path_passed"] is True
    assert first["three_party_identity_separation_passed"] is True
    assert first["all_attack_cases_passed"] is True
    assert len(first["attack_cases"]) >= 16
    assert first["application_authorization_valid_path_passed"] is True
    assert first["application_authorization_registry_status"] == ("EMPTY_DEFAULT_FAIL_CLOSED_GENERATION_0")
    assert first["application_authorization_decision_authority_status"] == (
        "LOCAL_SINGLE_SQLITE_REGISTRY_DECISION_CAS_UNSEALED"
    )
    assert first["access_review_attestation_status"] == (
        "HASH_BOUND_INDEPENDENT_STRONG_IDENTITY_REVIEW_ENFORCED_LOCAL_AUTHORITY"
    )
    assert first["oidc_key_registry_status"] == (
        "KID_SELECTED_DIGEST_AND_MIN_GENERATION_PINNED_ROTATION_REVOCATION_ENFORCED"
    )
    assert first["external_oidc_deployment_status"] == "NOT_PROVIDED"
    assert first["trusted_mtls_deployment_status"] == "NOT_PROVIDED"
    assert first["browser_pkce_or_bff_status"] == ("AUTHORIZATION_CODE_PKCE_S256_IMPLEMENTED_EXTERNAL_IDP_NOT_PROVIDED")
    assert first["browser_pkce_transaction_storage"] == ("VERSIONED_SESSION_STORAGE_NO_ACCESS_TOKEN")
    assert first["browser_access_token_storage"] == "IN_MEMORY_ONLY"
    assert first["browser_pkce_client_secret_present"] is False
    assert first["browser_pkce_one_time_state_nonce"] is True
    assert "AUTHORIZATION_CODE_PKCE_OR_BFF_NOT_INTEGRATED" not in first["m4_blockers"]
    assert first["legacy_shared_token_accepted"] is False
    assert first["self_asserted_operator_accepted"] is False
    serialized = json.dumps(first, sort_keys=True)
    assert "PRIVATE KEY" not in serialized
    assert "legacy-shared-token" not in serialized


def test_strong_identity_evidence_hashes_every_load_bearing_source(tmp_path):
    _fixture_repo(tmp_path)
    evidence = build_strong_identity_evidence(tmp_path, recorded_at=1)
    assert set(evidence["implementation_sha256"]) == {
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
    }
    assert set(evidence["verification_paths"]) == {
        "tests/governance/test_strong_identity.py",
        "tests/governance/test_oidc_key_registry.py",
        "tests/governance/test_identity_access_review.py",
        "tests/governance/test_identity_authorization.py",
        "tests/governance/test_identity_authorization_store.py",
        "tests/governance/test_promotion_api_boundary.py",
        "tests/governance/test_promotion_api_endpoints.py",
        "apps/research-console/src/lib/api/governance-pkce.test.ts",
    }
