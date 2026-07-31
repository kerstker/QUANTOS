import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
_ACTION = re.compile(r"uses:\s*([^\s#]+)")
_SHA_PIN = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def test_ci_actions_are_immutable_sha_pinned():
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
    actions = _ACTION.findall(workflow)
    assert actions
    assert all(_SHA_PIN.fullmatch(action) for action in actions), actions


def test_supply_chain_manifest_self_binds_exact_byte_portability_controls():
    from scripts.generate_supply_chain_evidence import _MANIFESTS

    assert {
        ".gitattributes",
        "tests/governance/test_evidence_checkout_portability.py",
    } <= set(_MANIFESTS)


def test_supply_chain_manifest_binds_external_model_provider_governance_surface():
    from scripts.generate_supply_chain_evidence import _MANIFESTS

    assert {
        "config/model_provider_registry.json",
        "quantos/contracts/external_model_provider.py",
        "services/governance/external_model_provider_evidence.py",
        "services/intelligence/external_provider_runtime.py",
        "services/intelligence/provider_selection.py",
        "tools/external_model_provider.py",
        "tools/external_model_provider_probe.py",
        "tests/governance/test_external_model_provider_boundary.py",
        "tests/governance/test_external_model_provider_evidence.py",
        "tests/intelligence/test_external_provider_runtime.py",
        "tests/tools/test_external_model_provider.py",
    } <= set(_MANIFESTS)


def test_supply_chain_manifest_binds_catalog_pbo_evidence_surface():
    from scripts.generate_supply_chain_evidence import _MANIFESTS

    assert {
        "quantos/contracts/alpha.py",
        "services/alpha/evaluator.py",
        "services/alpha/factory.py",
        "services/alpha/guards.py",
        "services/alpha/lifecycle.py",
        "services/research/experiment_registry/registry.py",
        "services/validation/pbo_evidence.py",
        "tests/alpha/test_pbo_evidence.py",
    } <= set(_MANIFESTS)


def test_supply_chain_artifacts_are_deterministic_and_honest():
    from scripts.generate_supply_chain_evidence import build_supply_chain_artifacts

    first = build_supply_chain_artifacts(ROOT)
    second = build_supply_chain_artifacts(ROOT)
    assert first == second

    sbom, evidence = first
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.6"
    assert len(sbom["components"]) == evidence["component_count"]
    assert evidence["python_requirements_all_pinned"] is True
    assert evidence["npm_lockfile_version"] == 3
    assert evidence["actions_all_sha_pinned"] is True
    assert evidence["sast_gate_wired"] is True
    assert {
        "config/service_import_policy.json",
        "config/artifact_admission_policy_registry.json",
        "config/artifact_receipt_authority_registry.json",
        "config/identity_authorization_registry.json",
        "evidence/service_import_graph.json",
        "quantos/contracts/event_schemas.py",
        "quantos/contracts/events.py",
        "quantos/contracts/identity.py",
        "quantos/contracts/paper.py",
        "scripts/generate_service_import_graph.py",
        "services/execution/impact.py",
        "services/execution/impact_calibration.py",
        "services/execution/paper_reconciliation.py",
        "services/execution/dry_run_store.py",
        "services/alpha/maintenance_store.py",
        "services/alpha/promotion.py",
        "services/deployment/events.py",
        "services/deployment/runtime.py",
        "services/deployment/store.py",
        "services/data/events.py",
        "services/data/refresh_history.py",
        "services/monitoring/domain_event_runtime.py",
        "services/monitoring/consumer_group.py",
        "services/monitoring/event_bus.py",
        "services/monitoring/event_health.py",
        "services/monitoring/event_projection.py",
        "services/research/experiment_registry/store.py",
        "services/risk/pre_trade/kill_switch.py",
        "services/risk/research_risk/events.py",
        "services/console/main.py",
        "services/console/risk_console.py",
        "services/governance/service_import_graph.py",
        "services/governance/artifact_admission_store.py",
        "services/governance/artifact_receipt_auth.py",
        "services/governance/identity_authorization.py",
        "services/governance/identity_authorization_store.py",
        "services/governance/strong_identity.py",
        "services/console/api/risk_port.py",
        "tests/contracts/test_paper_execution_contract.py",
        "tests/execution/test_impact_ownership.py",
        "tests/execution/test_dry_run_store.py",
        "tests/deployment/test_deployment_store.py",
        "tests/governance/test_console_composition_boundary.py",
        "tests/governance/test_event_bus_wiring.py",
        "tests/governance/test_event_schema_catalog.py",
        "tests/contracts/test_event_schema_registry.py",
        "tests/alpha/test_maintenance_store.py",
        "tests/governance/test_service_import_graph.py",
        "tests/governance/test_artifact_admission_store.py",
        "tests/governance/test_artifact_receipt_auth.py",
        "tests/governance/test_identity_authorization.py",
        "tests/governance/test_identity_authorization_store.py",
        "tests/governance/test_promotion_api_boundary.py",
        "tests/governance/test_promotion_api_endpoints.py",
        "tests/governance/test_strong_identity.py",
        "tests/governance/test_strong_identity_evidence.py",
        "tests/monitoring/test_event_consumer.py",
        "tests/monitoring/test_event_consumer_group.py",
        "tests/monitoring/test_event_bus_health.py",
        "tests/monitoring/test_event_projection.py",
        "tests/research/test_experiment_event_outbox.py",
        "tests/research/test_console_event_projection.py",
        "tests/tools/test_alpha_maintenance_sweep.py",
        "tests/tools/test_domain_event_bus.py",
        "tests/tools/test_domain_event_health.py",
        "tests/tools/test_domain_event_ops.py",
        "tests/tools/test_domain_event_owner_migration.py",
        "tests/tools/test_domain_event_outbox_drain.py",
        "tests/tools/test_l2_schedule_entrypoint.py",
        "tests/data/test_refresh_event_outbox.py",
        "tests/risk/test_research_risk_events.py",
        "tools/live_data_refresh.py",
        "tools/domain_event_health.py",
        "tools/domain_event_ops.py",
        "tools/domain_event_owner_migration.py",
        "tools/domain_event_outbox_drain.py",
        "tools/l2_schedule_entrypoint.py",
        "tools/domain_event_bus.py",
        "tools/deployment_events.py",
        "scripts/generate_event_schema_catalog.py",
        "scripts/generate_strong_identity_evidence.py",
        "scripts/register_schedules.ps1",
            "tests/governance/test_l2_schedule_boundary.py",
            "tests/governance/test_artifact_admission_provisioning.py",
        "tests/tools/test_deployment_events.py",
        "evidence/event_schema_catalog.json",
    } <= set(evidence["manifest_sha256"])
    assert evidence["vulnerability_scan_status"] == ("PARTIAL_EXTERNAL_NPM_PYTHON_ACTIONS_CONTAINER_VERIFIED")
    assert evidence["npm_vulnerability_evidence_status"] == ("VERIFIED_EXTERNAL_ZERO_KNOWN")
    assert evidence["npm_vulnerability_count"] == 0
    assert evidence["python_vulnerability_evidence_status"] == ("VERIFIED_EXTERNAL_ZERO_KNOWN")
    assert evidence["python_vulnerability_count"] == 0
    assert evidence["actions_vulnerability_evidence_status"] == ("VERIFIED_EXTERNAL_ZERO_KNOWN")
    assert evidence["actions_vulnerability_count"] == 0
    assert evidence["container_vulnerability_evidence_status"] == (
        "VERIFIED_EXTERNAL_BASE_REMEDIATED_RUNTIME_ZERO_KNOWN"
    )
    assert evidence["container_base_vulnerability_count"] == 5
    assert evidence["container_runtime_vulnerability_count"] == 0
    assert evidence["container_runtime_image_digest"].startswith("sha256:")
    assert evidence["container_reproducibility_evidence_status"] == ("VERIFIED_LOCAL_TWO_COLD_BUILDS_BYTE_IDENTICAL")
    assert len(evidence["container_reproducible_archive_sha256"]) == 64
    assert evidence["container_reproducible_manifest_digest"].startswith("sha256:")
    assert evidence["unresolved_python_optional_dependencies"] == []
    assert "PYPROJECT_OPTIONAL_DEPENDENCIES_UNPINNED" not in evidence["m4_blockers"]
    assert "BASE_IMAGE_VULNERABILITY_EVIDENCE_MISSING" not in evidence["m4_blockers"]
    assert "CONTAINER_BUILD_REPRODUCIBILITY_NOT_PROVEN" not in evidence["m4_blockers"]
    cross_host_verified = (
        evidence["cross_host_container_reproducibility_status"]
        == "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE_TWO_RUNNERS_BYTE_IDENTICAL"
    )
    if cross_host_verified:
        assert (
            evidence["cross_host_container_reproducibility_evidence"][
                "builders"
            ]
            == ["ubuntu-22.04", "ubuntu-24.04"]
        )
        assert (
            "CROSS_HOST_CONTAINER_REPRODUCIBILITY_NOT_PROVEN"
            not in evidence["m4_blockers"]
        )
    else:
        assert (
            evidence["cross_host_container_reproducibility_status"]
            == "NOT_PROVIDED"
        )
        assert (
            evidence["cross_host_container_reproducibility_evidence_ref"]
            is None
        )
        assert (
            evidence["cross_host_container_reproducibility_evidence"] is None
        )
        assert (
            "CROSS_HOST_CONTAINER_REPRODUCIBILITY_NOT_PROVEN"
            in evidence["m4_blockers"]
        )
    assert evidence["artifact_signature_status"] == (
        "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE_CROSS_HOST"
    )
    assert evidence["artifact_admission_verifier_status"] == (
        "OFFLINE_SIGSTORE_REKOR_V1_REAL_EXTERNAL_BUNDLE_VERIFIED_POLICY_V3_DECISION_V4_EXTERNAL_RECEIPT_STILL_GATED"
    )
    assert evidence["artifact_admission_contract_versions"] == {
        "policy": 3,
        "decision": 4,
        "verification_receipt": 1,
        "signed_receipt_envelope": 1,
        "policy_registry": 2,
        "receipt_authority_registry": 2,
        "decision_store": 4,
    }
    assert evidence["artifact_admission_policy_registry_status"] == (
        "L2_REPOSITORY_PINNED_FAIL_CLOSED_NO_ACTIVE_POLICIES"
    )
    assert evidence["artifact_admission_policy_registry_generation"] == 1
    assert evidence["artifact_admission_active_policy_count"] == 0
    assert evidence["artifact_admission_receipt_authority_registry_status"] == (
        "L2_REPOSITORY_PINNED_FAIL_CLOSED_NO_ACTIVE_KEYS"
    )
    assert evidence["artifact_admission_receipt_authority_registry_generation"] == 1
    assert evidence["artifact_admission_active_receipt_key_count"] == 0
    assert evidence["artifact_admission_library"] == "sigstore"
    assert evidence["artifact_admission_library_version"] == "4.4.0"
    assert evidence["artifact_admission_trusted_root_mode"] == ("CALLER_SUPPLIED_SHA256_PINNED")
    assert evidence["artifact_admission_identity_policy"] == ("EXACT_OIDC_ISSUER_AND_SUBJECT")
    assert evidence["artifact_admission_transparency_verification"] == (
        "SIGSTORE_SCT_REKOR_V1_INCLUSION_SIGNED_RECEIPT_ATTESTS_CHECKPOINT_TIME"
    )
    assert evidence["artifact_admission_freshness_revocation_status"] == (
        "LOCAL_FAIL_CLOSED_ED25519_SIGNED_RECEIPT_ENFORCED_SOURCE_NATIVE_PROOFS_NOT_VERIFIED"
    )
    assert evidence["artifact_admission_receipt_authenticity_status"] == (
        "VERIFY_ONLY_ED25519_AUTHORITY_REGISTRY_EMPTY_NO_EXTERNAL_RECEIPT"
    )
    assert evidence["artifact_admission_decision_store_status"] == (
        "WIRED_LOCAL_PROVISIONED_FIXED_PATH_HASH_CHAINED_SQLITE"
    )
    assert evidence["artifact_admission_decision_store_provisioning_status"] == (
        "LOCAL_PROVISIONED_VERIFIED_EMPTY_TRUST_ROOTS_UNSEALED"
    )
    assert evidence["artifact_admission_authority_snapshot"]["decision_count"] == 0
    authority_snapshot = evidence["artifact_admission_authority_snapshot"]
    assert authority_snapshot["policy_registry_event_seq"] > 0
    assert (
        authority_snapshot["receipt_authority_registry_event_seq"]
        > authority_snapshot["policy_registry_event_seq"]
    )
    assert evidence["artifact_admission_decision_store_worm_status"] == ("NOT_WORM_LOCAL_FILE_REPLACEABLE")
    assert evidence["artifact_admission_registry_history_status"] == (
        "LOCAL_SINGLE_SQLITE_LINEARIZABLE_REGISTRY_DECISION_CAS_UNSEALED"
    )
    assert evidence["artifact_admission_registry_mirror_mode"] == (
        "REPOSITORY_JSON_EXPLICIT_PROVISIONING_MIRROR_NOT_RUNTIME_AUTHORITY"
    )
    assert evidence["artifact_admission_allow_cas_status"] == (
        "EXACT_POLICY_AND_RECEIPT_AUTHORITY_HEADS_TRANSACTIONALLY_ENFORCED"
    )
    assert evidence["artifact_admission_authority_clock_status"] == (
        "LOCAL_MONOTONIC_GUARDED_WALL_CLOCK_FUTURE_SKEW_NO_EXTERNAL_TIME_AUTHORITY"
    )
    assert evidence["artifact_admission_read_consistency_status"] == ("SINGLE_SQLITE_TRANSACTIONAL_READ_SNAPSHOT")
    external_chain_head_verified = (
        evidence["artifact_admission_decision_history_external_seal_status"]
        == (
            "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE_REKOR_"
            "ARTIFACT_ADMISSION_CHAIN_HEAD"
        )
    )
    assert evidence[
        "artifact_admission_decision_history_external_seal_evidence_ref"
    ] == (
        "evidence/external_sigstore/2026-07-31-admission"
        if external_chain_head_verified
        else "evidence/artifact_admission_seal_latest.json"
    )
    assert len(
        evidence[
            "artifact_admission_decision_history_external_seal_evidence_sha256"
        ]
    ) == 64
    assert len(
        evidence[
            "artifact_admission_decision_history_external_anchor_sha256"
        ]
    ) == 64
    assert evidence[
        "artifact_admission_decision_history_external_destination_class"
    ] in (
        {"GITHUB_ACTIONS_REKOR"}
        if external_chain_head_verified
        else {"SEPARATE_VOLUME", "OFF_HOST_UNC"}
    )
    assert evidence[
        "artifact_admission_decision_history_external_signature_verified"
    ] is external_chain_head_verified
    assert evidence[
        "artifact_admission_decision_history_external_transparency_verified"
    ] is external_chain_head_verified
    assert evidence[
        "artifact_admission_decision_history_external_worm_verified"
    ] is False
    assert evidence["artifact_admission_valid_bundle_fixture_status"] == (
        "VERIFIED_RETAINED_EXTERNAL_SIGSTORE_DSSE_BUNDLE"
    )
    assert evidence["artifact_admission_signing_capability_present"] is False
    assert evidence["artifact_admission_external_bundle_status"] == (
        "VERIFIED_EXTERNAL_GITHUB_OIDC_FULCIO_SCT_REKOR_V1"
    )
    assert evidence["artifact_admission_external_bundle_evidence_ref"] == (
        "evidence/external_sigstore/2026-07-30"
    )
    assert evidence["artifact_admission_external_bundle_evidence"]["status"] == (
        "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE_CROSS_HOST"
    )
    assert evidence["artifact_admission_independent_deployment_status"] == ("NOT_PROVIDED")
    assert set(evidence["artifact_admission_implementation_sha256"]) == {
        "services/governance/artifact_admission.py",
        "services/governance/artifact_admission_store.py",
        "services/governance/artifact_receipt_auth.py",
        "services/governance/external_sigstore_evidence.py",
        "services/governance/artifact_admission_external_seal.py",
        "services/governance/json_integrity.py",
        "services/governance/cross_host_container_reproducibility.py",
        "tools/verify_artifact_admission.py",
        "tools/provision_artifact_admission_authority.py",
        "tools/artifact_admission_seal.py",
    }
    expected_admission_verification_paths = {
        "tests/governance/test_artifact_admission.py",
        "tests/governance/test_artifact_admission_cli.py",
        "tests/governance/test_artifact_admission_store.py",
        "tests/governance/test_artifact_receipt_auth.py",
        "tests/governance/test_external_sigstore_evidence.py",
        "tests/governance/test_artifact_admission_external_seal.py",
        "tests/tools/test_artifact_admission_seal.py",
        "tests/governance/test_artifact_admission_provisioning.py",
    }
    if cross_host_verified:
        expected_admission_verification_paths.add(
            "tests/governance/test_cross_host_container_sigstore_evidence.py"
        )
    assert set(evidence["artifact_admission_verification_paths"]) == (
        expected_admission_verification_paths
    )
    required_m4_blockers = {
        "INDEPENDENT_ADMISSION_DEPLOYMENT_NOT_PROVEN",
        "EXTERNAL_RECEIPT_AUTHORITY_AND_SIGNED_RECEIPT_NOT_PROVIDED",
        "SOURCE_NATIVE_CHECKPOINT_REVOCATION_PROOFS_NOT_VERIFIED",
    }
    if external_chain_head_verified:
        assert (
            "ARTIFACT_ADMISSION_DECISION_HISTORY_NOT_EXTERNALLY_SEALED"
            not in evidence["m4_blockers"]
        )
        assert evidence[
            "artifact_admission_decision_history_external_workflow_run"
        ].startswith("https://github.com/kerstker/QUANTOS/actions/runs/")
        assert len(
            evidence[
                "artifact_admission_decision_history_external_source_commit"
            ]
        ) == 40
    else:
        required_m4_blockers.add(
            "ARTIFACT_ADMISSION_DECISION_HISTORY_NOT_EXTERNALLY_SEALED"
        )
    assert required_m4_blockers <= set(evidence["m4_blockers"])
    assert (
        "EXTERNAL_ARTIFACT_SIGNATURE_IDENTITY_MISSING"
        not in evidence["m4_blockers"]
    )
    assert "SIGNED_PROVENANCE_BUNDLE_NOT_RETAINED" not in evidence["m4_blockers"]
    assert (
        "ARTIFACT_ADMISSION_FIXED_AUTHORITY_STORE_NOT_PROVISIONED"
        not in evidence["m4_blockers"]
    )

    requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "sigstore==4.4.0" in requirements
    assert "sigstore==4.4.0" in pyproject


def test_supply_chain_check_accepts_current_and_rejects_tampering(tmp_path):
    from scripts.generate_supply_chain_evidence import main

    sbom_path = tmp_path / "sbom.cdx.json"
    evidence_path = tmp_path / "supply_chain_baseline.json"
    common = [
        "--repo-root",
        str(ROOT),
        "--sbom",
        str(sbom_path),
        "--evidence",
        str(evidence_path),
    ]
    assert main(common) == 0
    assert main([*common, "--check"]) == 0

    tampered = json.loads(evidence_path.read_text(encoding="utf-8"))
    tampered["component_count"] += 1
    evidence_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert main([*common, "--check"]) == 1


def test_tracked_supply_chain_artifacts_are_current():
    from scripts.generate_supply_chain_evidence import main

    assert main(["--repo-root", str(ROOT), "--check"]) == 0


def test_unified_gate_checks_supply_chain_baseline_before_top_tier_assessment():
    source = (ROOT / "scripts" / "validate.py").read_text(encoding="utf-8")
    marker = "supply-chain-baseline:current"
    assert marker in source
    assert "generate_supply_chain_evidence.py" in source
    assert source.index(marker) < source.index("top-tier-assessment-artifacts")
