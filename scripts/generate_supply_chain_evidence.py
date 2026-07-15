#!/usr/bin/env python
"""Generate deterministic offline SBOM and supply-chain baseline evidence."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any, Sequence
from urllib.parse import quote


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services.governance.vulnerability_evidence import (  # noqa: E402
    verify_actions_audit_evidence,
    verify_container_audit_evidence,
    verify_npm_audit_evidence,
    verify_python_audit_evidence,
)
from services.governance.artifact_admission import (  # noqa: E402
    load_json_without_duplicate_keys,
)
from services.governance.reproducibility_evidence import (  # noqa: E402
    verify_container_reproducibility_evidence,
)


DEFAULT_SBOM = REPO / "evidence" / "sbom.cdx.json"
DEFAULT_EVIDENCE = REPO / "evidence" / "supply_chain_baseline.json"
DEFAULT_NPM_AUDIT = REPO / "evidence" / "vulnerability" / "npm_audit_latest.json"
_PINNED_REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)$")
_ACTION = re.compile(r"uses:\s*([^\s#]+)")
_ACTION_SHA = re.compile(r"^([^@]+)@([0-9a-f]{40})$")
_MANIFESTS = (
    ".github/workflows/validate.yml",
    "apps/research-console/package-lock.json",
    "apps/research-console/package.json",
    "config/artifact_admission_policy_registry.json",
    "config/artifact_receipt_authority_registry.json",
    "config/identity_authorization_registry.json",
    "config/service_import_policy.json",
    "config/top_tier_requirements.json",
    "deploy/console.Dockerfile",
    "evidence/service_import_graph.json",
    "evidence/event_schema_catalog.json",
    "pyproject.toml",
    "quantos/contracts/event_schemas.py",
    "quantos/contracts/events.py",
    "quantos/contracts/identity.py",
    "quantos/contracts/paper.py",
    "requirements-dev.txt",
    "requirements.txt",
    "services/console/main.py",
    "services/console/risk_console.py",
    "services/console/api/risk_port.py",
    "services/console/api/system_views.py",
    "services/alpha/events.py",
    "services/alpha/maintenance_events.py",
    "services/alpha/maintenance_store.py",
    "services/alpha/promotion.py",
    "services/deployment/events.py",
    "services/deployment/gate.py",
    "services/deployment/runtime.py",
    "services/deployment/store.py",
    "services/data/events.py",
    "services/data/refresh_history.py",
    "services/governance/artifact_admission.py",
    "services/governance/artifact_admission_store.py",
    "services/governance/artifact_receipt_auth.py",
    "services/governance/identity_authorization.py",
    "services/governance/identity_authorization_store.py",
    "services/governance/service_import_graph.py",
    "services/governance/strong_identity.py",
    "services/governance/top_tier_assessment.py",
    "services/execution/impact.py",
    "services/execution/impact_calibration.py",
    "services/execution/paper_reconciliation.py",
    "services/execution/dry_run_store.py",
    "services/execution/events.py",
    "services/execution/dry_run_pipeline.py",
    "services/monitoring/domain_event_runtime.py",
    "services/monitoring/consumer_group.py",
    "services/monitoring/event_bus.py",
    "services/monitoring/event_health.py",
    "services/monitoring/event_projection.py",
    "services/promotion_api/main.py",
    "services/research/experiment_registry/registry.py",
    "services/research/experiment_registry/store.py",
    "services/research/ports/persistence.py",
    "services/risk/pre_trade/events.py",
    "services/risk/pre_trade/kill_switch.py",
    "services/risk/pre_trade/kill_switch_runtime.py",
    "services/risk/pre_trade/kill_switch_store.py",
    "services/risk/research_risk/events.py",
    "live/execution/risk_monitor.py",
    "live/governance/kill_switch_store.py",
    "tools/kill_switch_events.py",
    "tools/l2_backup.py",
    "tools/l2_scheduler_watchdog.py",
    "tools/live_backup.py",
    "tools/live_kill_switch_reset.py",
    "tools/live_risk_sentinel.py",
    "tools/risk_recovery_drill.py",
    "tools/restore_drill.py",
    "tools/alpha_maintenance_sweep.py",
    "tools/domain_event_bus.py",
    "tools/domain_event_health.py",
    "tools/domain_event_ops.py",
    "tools/domain_event_seal.py",
    "tools/domain_event_owner_migration.py",
    "tools/domain_event_outbox_drain.py",
    "tools/live_data_refresh.py",
    "tools/deployment_events.py",
    "tools/live_track_advance.py",
    "tools/external_heartbeat.py",
    "tools/verify_artifact_admission.py",
    "tests/governance/test_artifact_admission.py",
    "tests/governance/test_artifact_admission_cli.py",
    "tests/governance/test_artifact_admission_store.py",
    "tests/governance/test_artifact_receipt_auth.py",
    "tests/governance/test_identity_authorization.py",
    "tests/governance/test_identity_authorization_store.py",
    "tests/governance/test_promotion_api_boundary.py",
    "tests/governance/test_promotion_api_endpoints.py",
    "tests/governance/test_strong_identity.py",
    "tests/governance/test_strong_identity_evidence.py",
    "evidence/vulnerability/npm_audit_latest.json",
    "evidence/vulnerability/python_audit_latest.json",
    "evidence/vulnerability/actions_osv_latest.json",
    "evidence/vulnerability/container_trivy_latest.json",
    "evidence/reproducibility/container_build_latest.json",
    "services/governance/reproducibility_evidence.py",
    "services/governance/vulnerability_evidence.py",
    "scripts/generate_npm_audit_evidence.py",
    "scripts/generate_python_audit_evidence.py",
    "scripts/generate_actions_audit_evidence.py",
    "scripts/generate_container_audit_evidence.py",
    "scripts/generate_container_reproducibility_evidence.py",
    "scripts/generate_service_import_graph.py",
    "scripts/generate_strong_identity_evidence.py",
    "scripts/generate_event_schema_catalog.py",
    "scripts/register_schedules.ps1",
    "scripts/generate_supply_chain_evidence.py",
    "scripts/validate.py",
    "tests/governance/test_service_import_graph.py",
    "tests/governance/test_event_schema_catalog.py",
    "tests/governance/test_l2_schedule_boundary.py",
    "tests/governance/test_l2_operations_boundary.py",
    "tests/governance/test_top_tier_assessment.py",
    "tests/governance/test_supply_chain_evidence.py",
    "tests/contracts/test_paper_execution_contract.py",
    "tests/contracts/test_event_schema_registry.py",
    "tests/alpha/test_maintenance_store.py",
    "tests/execution/test_impact_ownership.py",
    "tests/execution/test_dry_run_store.py",
    "tests/deployment/test_deployment_store.py",
    "tests/governance/test_console_composition_boundary.py",
    "tests/governance/test_event_bus_wiring.py",
    "tests/governance/test_external_operations_boundary.py",
    "tests/monitoring/test_governance_decision_audit.py",
    "tests/monitoring/test_event_consumer.py",
    "tests/monitoring/test_event_consumer_group.py",
    "tests/monitoring/test_event_bus_health.py",
    "tests/monitoring/test_event_projection.py",
    "tests/research/test_experiment_event_outbox.py",
    "tests/research/test_console_event_projection.py",
    "tests/risk/test_kill_switch_state_store.py",
    "tests/risk/test_research_risk_events.py",
    "tests/live/test_kill_switch_reset_cli.py",
    "tests/live/test_live_risk_sentinel.py",
    "tests/live/test_risk_recovery_drill.py",
    "tests/governance/test_risk_recovery_drill_boundary.py",
    "tests/tools/test_alpha_maintenance_sweep.py",
    "tests/tools/test_domain_event_bus.py",
    "tests/tools/test_domain_event_health.py",
    "tests/tools/test_domain_event_ops.py",
    "tests/tools/test_domain_event_seal.py",
    "tests/tools/test_domain_event_owner_migration.py",
    "tests/tools/test_domain_event_outbox_drain.py",
    "tests/tools/test_l2_backup.py",
    "tests/tools/test_l2_scheduler_watchdog.py",
    "tests/tools/test_external_heartbeat.py",
    "tests/data/test_refresh_event_outbox.py",
    "tests/tools/test_deployment_events.py",
)


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _requirement_entries(path: Path, seen: set[Path] | None = None) -> list[str]:
    seen = set() if seen is None else seen
    resolved = path.resolve()
    if resolved in seen:
        return []
    seen.add(resolved)
    entries: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r ") or line.startswith("--requirement "):
            included = line.split(maxsplit=1)[1]
            entries.extend(_requirement_entries(path.parent / included, seen))
            continue
        entries.append(line)
    return entries


def _python_components(repo_root: Path) -> tuple[list[dict[str, Any]], bool, list[str]]:
    runtime_entries = _requirement_entries(repo_root / "requirements.txt")
    development_entries = _requirement_entries(repo_root / "requirements-dev.txt")
    runtime_names = {
        match.group(1).lower().replace("_", "-")
        for entry in runtime_entries
        if (match := _PINNED_REQUIREMENT.fullmatch(entry))
    }
    records: dict[str, dict[str, Any]] = {}
    all_pinned = True
    for entry in development_entries:
        match = _PINNED_REQUIREMENT.fullmatch(entry)
        if match is None:
            all_pinned = False
            continue
        name, version = match.groups()
        normalized = name.lower().replace("_", "-")
        scope = "required" if normalized in runtime_names else "optional"
        records[normalized] = {
            "type": "library",
            "bom-ref": f"pypi:{normalized}@{version}",
            "name": normalized,
            "version": version,
            "scope": scope,
            "purl": f"pkg:pypi/{quote(normalized)}@{quote(version)}",
            "properties": [
                {"name": "quantos:ecosystem", "value": "pypi"},
                {
                    "name": "quantos:manifest",
                    "value": "requirements.txt" if scope == "required" else "requirements-dev.txt",
                },
            ],
        }

    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    unresolved: list[str] = []
    optional = pyproject.get("project", {}).get("optional-dependencies", {})
    for group, entries in sorted(optional.items()):
        for entry in entries:
            if _PINNED_REQUIREMENT.fullmatch(entry):
                continue
            unresolved.append(f"{group}:{entry}")
            name = re.split(r"[<>=!~\[]", entry, maxsplit=1)[0].strip()
            if not name:
                continue
            normalized = name.lower().replace("_", "-")
            records.setdefault(
                normalized,
                {
                    "type": "library",
                    "bom-ref": f"pypi:{normalized}:unresolved",
                    "name": normalized,
                    "scope": "optional",
                    "purl": f"pkg:pypi/{quote(normalized)}",
                    "properties": [
                        {"name": "quantos:ecosystem", "value": "pypi"},
                        {"name": "quantos:manifest", "value": "pyproject.toml"},
                        {"name": "quantos:version-status", "value": "UNRESOLVED"},
                    ],
                },
            )
    return sorted(records.values(), key=lambda item: item["bom-ref"]), all_pinned, unresolved


def _npm_name(package_path: str, metadata: dict[str, Any]) -> str:
    explicit = metadata.get("name")
    if isinstance(explicit, str) and explicit:
        return explicit
    return package_path.rsplit("node_modules/", 1)[-1]


def _npm_components(
    repo_root: Path,
) -> tuple[list[dict[str, Any]], int, int, int]:
    lock = json.loads((repo_root / "apps" / "research-console" / "package-lock.json").read_text(encoding="utf-8"))
    components: list[dict[str, Any]] = []
    integrity_count = 0
    for package_path, metadata in lock.get("packages", {}).items():
        if not package_path or not isinstance(metadata, dict):
            continue
        version = metadata.get("version")
        if not isinstance(version, str) or not version:
            continue
        name = _npm_name(package_path, metadata)
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": f"npm:{name}@{version}:{package_path}",
            "name": name,
            "version": version,
            "scope": "optional" if metadata.get("dev") is True else "required",
            "purl": f"pkg:npm/{quote(name, safe='/')}@{quote(version)}",
            "properties": [
                {"name": "quantos:ecosystem", "value": "npm"},
                {"name": "quantos:manifest", "value": "package-lock.json"},
            ],
        }
        integrity = metadata.get("integrity")
        if isinstance(integrity, str) and integrity.startswith("sha512-"):
            try:
                decoded = base64.b64decode(integrity.split("-", 1)[1], validate=True)
            except (ValueError, base64.binascii.Error):
                decoded = b""
            if decoded:
                component["hashes"] = [{"alg": "SHA-512", "content": decoded.hex()}]
                integrity_count += 1
        components.append(component)
    components.sort(key=lambda item: item["bom-ref"])
    return components, int(lock.get("lockfileVersion", 0)), len(components), integrity_count


def _workflow_actions(repo_root: Path) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    workflows = repo_root / ".github" / "workflows"
    for path in sorted((*workflows.glob("*.yml"), *workflows.glob("*.yaml"))):
        relative = path.relative_to(repo_root).as_posix()
        for reference in _ACTION.findall(path.read_text(encoding="utf-8")):
            match = _ACTION_SHA.fullmatch(reference)
            actions.append(
                {
                    "workflow": relative,
                    "reference": reference,
                    "sha_pinned": match is not None,
                }
            )
    return actions


def build_supply_chain_artifacts(
    repo_root: str | Path = REPO,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(repo_root).resolve()
    python, python_pinned, unresolved_optional = _python_components(root)
    npm, lockfile_version, npm_count, npm_integrity = _npm_components(root)
    actions = _workflow_actions(root)
    npm_audit_path = root / "evidence/vulnerability/npm_audit_latest.json"
    npm_audit_bytes = npm_audit_path.read_bytes()
    npm_audit_document = json.loads(npm_audit_bytes.decode("utf-8"))
    npm_audit_summary = verify_npm_audit_evidence(npm_audit_document, root)
    npm_zero_known = npm_audit_summary["verdict"] == "PASS" and npm_audit_summary["vulnerability_count"] == 0
    python_audit_path = root / "evidence/vulnerability/python_audit_latest.json"
    python_audit_bytes = python_audit_path.read_bytes()
    python_audit_document = json.loads(python_audit_bytes.decode("utf-8"))
    python_audit_summary = verify_python_audit_evidence(python_audit_document, root)
    python_zero_known = python_audit_summary["verdict"] == "PASS" and python_audit_summary["vulnerability_count"] == 0
    actions_audit_path = root / "evidence/vulnerability/actions_osv_latest.json"
    actions_audit_bytes = actions_audit_path.read_bytes()
    actions_audit_document = json.loads(actions_audit_bytes.decode("utf-8"))
    actions_audit_summary = verify_actions_audit_evidence(actions_audit_document, root)
    actions_zero_known = (
        actions_audit_summary["verdict"] == "PASS" and actions_audit_summary["vulnerability_count"] == 0
    )
    container_audit_path = root / "evidence/vulnerability/container_trivy_latest.json"
    container_audit_bytes = container_audit_path.read_bytes()
    container_audit_document = json.loads(container_audit_bytes.decode("utf-8"))
    container_audit_summary = verify_container_audit_evidence(container_audit_document, root)
    container_policy_passed = (
        container_audit_summary["verdict"] == "PASS" and container_audit_summary["runtime_vulnerability_count"] == 0
    )
    reproducibility_path = root / "evidence/reproducibility/container_build_latest.json"
    reproducibility_bytes = reproducibility_path.read_bytes()
    reproducibility_document = json.loads(reproducibility_bytes.decode("utf-8"))
    reproducibility_summary = verify_container_reproducibility_evidence(reproducibility_document, root)
    reproducibility_passed = reproducibility_summary["verdict"] == "PASS"
    components = sorted((*python, *npm), key=lambda item: item["bom-ref"])
    sigstore_version = next(
        (component["version"] for component in python if component["name"] == "sigstore"),
        "",
    )
    policy_registry_ref = "config/artifact_admission_policy_registry.json"
    policy_registry_bytes = (root / policy_registry_ref).read_bytes()
    policy_registry = load_json_without_duplicate_keys(policy_registry_bytes)
    if (
        not isinstance(policy_registry, dict)
        or set(policy_registry) != {"schema_version", "readiness", "generation", "policies"}
        or policy_registry.get("schema_version") != 2
        or policy_registry.get("readiness") != "L2_DRY_RUN_ONLY"
        or type(policy_registry.get("generation")) is not int
        or policy_registry["generation"] < 0
        or not isinstance(policy_registry.get("policies"), dict)
    ):
        raise ValueError("artifact admission policy registry is invalid")
    active_policy_count = sum(
        1
        for entry in policy_registry["policies"].values()
        if isinstance(entry, dict) and entry.get("status") == "ACTIVE"
    )
    receipt_authority_registry_ref = "config/artifact_receipt_authority_registry.json"
    receipt_authority_registry_bytes = (root / receipt_authority_registry_ref).read_bytes()
    receipt_authority_registry = load_json_without_duplicate_keys(receipt_authority_registry_bytes)
    if (
        not isinstance(receipt_authority_registry, dict)
        or set(receipt_authority_registry) != {"schema_version", "readiness", "generation", "keys"}
        or receipt_authority_registry.get("schema_version") != 2
        or receipt_authority_registry.get("readiness") != "L2_DRY_RUN_ONLY"
        or type(receipt_authority_registry.get("generation")) is not int
        or receipt_authority_registry["generation"] < 0
        or not isinstance(receipt_authority_registry.get("keys"), dict)
    ):
        raise ValueError("artifact receipt authority registry is invalid")
    active_receipt_key_count = sum(
        1
        for entry in receipt_authority_registry["keys"].values()
        if isinstance(entry, dict) and entry.get("status") == "ACTIVE"
    )
    admission_store_provisioned = (root / "data/cache/artifact_admission.db").is_file()
    admission_implementations = (
        "services/governance/artifact_admission.py",
        "services/governance/artifact_admission_store.py",
        "services/governance/artifact_receipt_auth.py",
        "tools/verify_artifact_admission.py",
    )
    admission_verification = (
        "tests/governance/test_artifact_admission.py",
        "tests/governance/test_artifact_admission_cli.py",
        "tests/governance/test_artifact_admission_store.py",
        "tests/governance/test_artifact_receipt_auth.py",
    )
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": "application:quantos",
                "name": "QUANTOS",
                "version": "L2_DRY_RUN_ONLY",
            }
        },
        "components": components,
    }
    sbom_sha256 = _sha256_bytes(_canonical_bytes(sbom))
    manifests = {path: _sha256_file(root / path) for path in _MANIFESTS}
    validate_source = (root / "scripts" / "validate.py").read_text(encoding="utf-8")
    workflow_source = (root / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
    actions_pinned = bool(actions) and all(item["sha_pinned"] for item in actions)
    evidence = {
        "schema_version": 1,
        "verdict": "PARTIAL",
        "mode": "OFFLINE_REPOSITORY_BASELINE",
        "sbom_format": "CycloneDX",
        "sbom_spec_version": "1.6",
        "sbom_path": "evidence/sbom.cdx.json",
        "sbom_sha256": sbom_sha256,
        "component_count": len(components),
        "python_component_count": len(python),
        "python_requirements_all_pinned": python_pinned,
        "unresolved_python_optional_dependencies": unresolved_optional,
        "npm_lockfile_version": lockfile_version,
        "npm_locked_components": npm_count,
        "npm_integrity_components": npm_integrity,
        "actions": actions,
        "actions_all_sha_pinned": actions_pinned,
        "manifest_sha256": manifests,
        "sast_gate_wired": ("governance-scanner" in validate_source and "scripts/validate.py" in workflow_source),
        "reproducible_inputs": (python_pinned and lockfile_version == 3 and actions_pinned),
        "vulnerability_scan_status": (
            "PARTIAL_EXTERNAL_NPM_PYTHON_ACTIONS_CONTAINER_VERIFIED"
            if (npm_zero_known and python_zero_known and actions_zero_known and container_policy_passed)
            else "EXTERNAL_FINDINGS_PRESENT"
        ),
        "npm_vulnerability_evidence_status": (
            "VERIFIED_EXTERNAL_ZERO_KNOWN" if npm_zero_known else "VERIFIED_EXTERNAL_FINDINGS_PRESENT"
        ),
        "npm_vulnerability_evidence_ref": ("evidence/vulnerability/npm_audit_latest.json"),
        "npm_vulnerability_evidence_sha256": _sha256_bytes(npm_audit_bytes),
        "npm_vulnerability_observed_at": npm_audit_summary["observed_at"],
        "npm_vulnerability_count": npm_audit_summary["vulnerability_count"],
        "python_vulnerability_evidence_status": (
            "VERIFIED_EXTERNAL_ZERO_KNOWN" if python_zero_known else "VERIFIED_EXTERNAL_FINDINGS_PRESENT"
        ),
        "python_vulnerability_evidence_ref": ("evidence/vulnerability/python_audit_latest.json"),
        "python_vulnerability_evidence_sha256": _sha256_bytes(python_audit_bytes),
        "python_vulnerability_observed_at": python_audit_summary["observed_at"],
        "python_vulnerability_count": python_audit_summary["vulnerability_count"],
        "actions_vulnerability_evidence_status": (
            "VERIFIED_EXTERNAL_ZERO_KNOWN" if actions_zero_known else "VERIFIED_EXTERNAL_FINDINGS_PRESENT"
        ),
        "actions_vulnerability_evidence_ref": ("evidence/vulnerability/actions_osv_latest.json"),
        "actions_vulnerability_evidence_sha256": _sha256_bytes(actions_audit_bytes),
        "actions_vulnerability_observed_at": actions_audit_summary["observed_at"],
        "actions_vulnerability_count": actions_audit_summary["vulnerability_count"],
        "container_vulnerability_evidence_status": (
            "VERIFIED_EXTERNAL_BASE_REMEDIATED_RUNTIME_ZERO_KNOWN"
            if container_policy_passed
            else "VERIFIED_EXTERNAL_FINDINGS_PRESENT"
        ),
        "container_vulnerability_evidence_ref": ("evidence/vulnerability/container_trivy_latest.json"),
        "container_vulnerability_evidence_sha256": _sha256_bytes(container_audit_bytes),
        "container_vulnerability_observed_at": container_audit_summary["observed_at"],
        "container_base_vulnerability_count": container_audit_summary["base_vulnerability_count"],
        "container_runtime_vulnerability_count": container_audit_summary["runtime_vulnerability_count"],
        "container_runtime_image_digest": container_audit_summary["runtime_image_digest"],
        "container_reproducibility_evidence_status": (
            "VERIFIED_LOCAL_TWO_COLD_BUILDS_BYTE_IDENTICAL"
            if reproducibility_passed
            else "LOCAL_REPRODUCIBILITY_EVIDENCE_FAILED"
        ),
        "container_reproducibility_evidence_ref": ("evidence/reproducibility/container_build_latest.json"),
        "container_reproducibility_evidence_sha256": _sha256_bytes(reproducibility_bytes),
        "container_reproducibility_observed_at": reproducibility_summary["observed_at"],
        "container_reproducible_archive_sha256": reproducibility_summary["archive_sha256"],
        "container_reproducible_manifest_digest": reproducibility_summary["manifest_digest"],
        "container_reproducibility_buildkit_version": reproducibility_summary["buildkit_version"],
        "artifact_signature_status": "VERIFIER_READY_EXTERNAL_SIGNATURE_MISSING",
        "artifact_admission_verifier_status": (
            "OFFLINE_SIGSTORE_REKOR_V1_POLICY_V3_DECISION_V4_SIGNED_RECEIPT_GATED_CODE_VERIFIED_NO_VALID_BUNDLE"
        ),
        "artifact_admission_contract_versions": {
            "policy": 3,
            "decision": 4,
            "verification_receipt": 1,
            "signed_receipt_envelope": 1,
            "policy_registry": 2,
            "receipt_authority_registry": 2,
            "decision_store": 4,
        },
        "artifact_admission_policy_registry_ref": policy_registry_ref,
        "artifact_admission_policy_registry_sha256": _sha256_bytes(policy_registry_bytes),
        "artifact_admission_policy_registry_generation": (policy_registry["generation"]),
        "artifact_admission_policy_registry_status": (
            "L2_REPOSITORY_PINNED_FAIL_CLOSED_NO_ACTIVE_POLICIES"
            if not policy_registry["policies"]
            else "L2_REPOSITORY_PINNED_POLICIES_PRESENT"
        ),
        "artifact_admission_active_policy_count": active_policy_count,
        "artifact_admission_receipt_authority_registry_ref": (receipt_authority_registry_ref),
        "artifact_admission_receipt_authority_registry_sha256": (_sha256_bytes(receipt_authority_registry_bytes)),
        "artifact_admission_receipt_authority_registry_generation": (receipt_authority_registry["generation"]),
        "artifact_admission_receipt_authority_registry_status": (
            "L2_REPOSITORY_PINNED_FAIL_CLOSED_NO_ACTIVE_KEYS"
            if not receipt_authority_registry["keys"]
            else "L2_REPOSITORY_PINNED_KEYS_PRESENT"
        ),
        "artifact_admission_active_receipt_key_count": active_receipt_key_count,
        "artifact_admission_library": "sigstore",
        "artifact_admission_library_version": sigstore_version,
        "artifact_admission_trusted_root_mode": "CALLER_SUPPLIED_SHA256_PINNED",
        "artifact_admission_identity_policy": "EXACT_OIDC_ISSUER_AND_SUBJECT",
        "artifact_admission_transparency_verification": (
            "SIGSTORE_SCT_REKOR_V1_INCLUSION_SIGNED_RECEIPT_ATTESTS_CHECKPOINT_TIME"
        ),
        "artifact_admission_freshness_revocation_status": (
            "LOCAL_FAIL_CLOSED_ED25519_SIGNED_RECEIPT_ENFORCED_SOURCE_NATIVE_PROOFS_NOT_VERIFIED"
        ),
        "artifact_admission_receipt_authenticity_status": (
            "VERIFY_ONLY_ED25519_AUTHORITY_REGISTRY_EMPTY_NO_EXTERNAL_RECEIPT"
        ),
        "artifact_admission_decision_store_status": (
            "WIRED_LOCAL_PROVISIONED_FIXED_PATH_HASH_CHAINED_SQLITE"
            if admission_store_provisioned
            else "WIRED_FAIL_CLOSED_UNPROVISIONED_FIXED_PATH_HASH_CHAINED_SQLITE"
        ),
        "artifact_admission_decision_store_path": ("data/cache/artifact_admission.db"),
        "artifact_admission_decision_store_provisioning_status": (
            "LOCAL_PROVISIONED_UNSEALED" if admission_store_provisioned else "NOT_PROVIDED"
        ),
        "artifact_admission_decision_history_external_seal_status": ("NOT_PROVIDED"),
        "artifact_admission_decision_store_worm_status": ("NOT_WORM_LOCAL_FILE_REPLACEABLE"),
        "artifact_admission_registry_history_status": (
            "LOCAL_SINGLE_SQLITE_LINEARIZABLE_REGISTRY_DECISION_CAS_UNSEALED"
        ),
        "artifact_admission_registry_mirror_mode": (
            "REPOSITORY_JSON_EXPLICIT_PROVISIONING_MIRROR_NOT_RUNTIME_AUTHORITY"
        ),
        "artifact_admission_allow_cas_status": ("EXACT_POLICY_AND_RECEIPT_AUTHORITY_HEADS_TRANSACTIONALLY_ENFORCED"),
        "artifact_admission_authority_clock_status": (
            "LOCAL_MONOTONIC_GUARDED_WALL_CLOCK_FUTURE_SKEW_NO_EXTERNAL_TIME_AUTHORITY"
        ),
        "artifact_admission_read_consistency_status": ("SINGLE_SQLITE_TRANSACTIONAL_READ_SNAPSHOT"),
        "artifact_admission_valid_bundle_fixture_status": "NOT_PROVIDED",
        "artifact_admission_signing_capability_present": False,
        "artifact_admission_external_bundle_status": "NOT_PROVIDED",
        "artifact_admission_independent_deployment_status": "NOT_PROVIDED",
        "artifact_admission_implementation_sha256": {
            path: _sha256_file(root / path) for path in admission_implementations
        },
        "artifact_admission_verification_paths": list(admission_verification),
        "m4_blockers": [
            "CROSS_HOST_CONTAINER_REPRODUCIBILITY_NOT_PROVEN",
            "EXTERNAL_ARTIFACT_SIGNATURE_IDENTITY_MISSING",
            "SIGNED_PROVENANCE_BUNDLE_NOT_RETAINED",
            "INDEPENDENT_ADMISSION_DEPLOYMENT_NOT_PROVEN",
            "EXTERNAL_RECEIPT_AUTHORITY_AND_SIGNED_RECEIPT_NOT_PROVIDED",
            "SOURCE_NATIVE_CHECKPOINT_REVOCATION_PROOFS_NOT_VERIFIED",
            "ARTIFACT_ADMISSION_FIXED_AUTHORITY_STORE_NOT_PROVISIONED",
            "ARTIFACT_ADMISSION_DECISION_HISTORY_NOT_EXTERNALLY_SEALED",
            *(["CONTAINER_VULNERABILITY_POLICY_FAILED"] if not container_policy_passed else []),
            *(["PYPROJECT_OPTIONAL_DEPENDENCIES_UNPINNED"] if unresolved_optional else []),
        ],
    }
    return sbom, evidence


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_canonical_bytes(document))
    temporary.replace(path)


def _require_current(path: Path, document: dict[str, Any], *, label: str) -> None:
    expected = _canonical_bytes(document)
    try:
        actual = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{label} artifact unavailable: {exc}") from exc
    if actual != expected:
        raise ValueError(f"{label} artifact drift: regenerate {path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic QUANTOS supply-chain baseline evidence")
    parser.add_argument("--repo-root", default=str(REPO))
    parser.add_argument("--sbom", default=str(DEFAULT_SBOM))
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        sbom, evidence = build_supply_chain_artifacts(args.repo_root)
        if args.check:
            _require_current(Path(args.sbom), sbom, label="SBOM")
            _require_current(Path(args.evidence), evidence, label="supply-chain baseline")
            print(
                "supply-chain baseline: current; "
                f"components={evidence['component_count']}; "
                f"signature={evidence['artifact_signature_status']}"
            )
            return 0
        _write(Path(args.sbom), sbom)
        _write(Path(args.evidence), evidence)
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        print(f"supply-chain evidence generation failed: {exc}", file=sys.stderr)
        return 1
    print(
        "supply-chain baseline: "
        f"{evidence['component_count']} components; "
        f"signature={evidence['artifact_signature_status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
