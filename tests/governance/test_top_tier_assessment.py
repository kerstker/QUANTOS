import hashlib
from datetime import datetime, timezone
import gzip
import io
import os
import json
from pathlib import Path
import sqlite3
import tarfile

import pytest

from quantos.contracts.hashing import source_file_sha256, short_hash, stable_hash
from scripts.generate_model_risk_evidence import build_model_risk_evidence
from scripts.generate_model_risk_dataset import generate_bundle
from scripts.generate_strong_identity_evidence import build_strong_identity_evidence
from services.governance.model_risk_history import (
    append_model_risk_revision,
    build_model_risk_revision,
)
from services.governance.reproducibility_evidence import (
    build_container_reproducibility_evidence,
)
from scripts.evidence_binding import resolve_canonical_source_anchor
from services.governance.top_tier_assessment import RegistryError, assess_repository, load_requirements
from services.research.experiment_registry.store import SqliteExperimentStore
from services.governance.vulnerability_evidence import (
    build_actions_audit_evidence,
    build_container_audit_evidence,
    build_npm_audit_evidence,
    build_python_audit_evidence,
)
from services.intelligence.runtime import RESEARCH_EVALUATION_TEMPLATE
from tools import search_trial_ledger_history


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "config" / "top_tier_requirements.json"


def _write_registry(tmp_path: Path, requirements: list[dict]) -> Path:
    path = tmp_path / "requirements.json"
    path.write_text(json.dumps({"schema_version": 1, "requirements": requirements}), encoding="utf-8")
    return path


def _requirement(requirement_id: str, **overrides) -> dict:
    item = {
        "id": requirement_id,
        "domain": "platform_engineering",
        "tier": "A",
        "target_maturity": "M3",
        "classification": "L2_SAFE_NOW",
        "description": "test requirement",
        "required_evidence": ["implementation", "verification"],
        "red_line": False,
        "checker": "manual_evidence",
        "next_action": "provide evidence",
    }
    item.update(overrides)
    return item


def test_registry_rejects_duplicate_ids(tmp_path):
    path = _write_registry(tmp_path, [_requirement("DUP"), _requirement("DUP")])
    with pytest.raises(RegistryError, match="duplicate requirement id"):
        load_requirements(path)


def test_red_line_failure_forces_not_top_tier(tmp_path):
    path = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.MISSING", checker="paths_exist", checker_args={"paths": ["missing.file"]}, red_line=True
            )
        ],
    )
    report = assess_repository(tmp_path, path)
    assert report["verdict"] == "NOT_TOP_TIER"
    assert report["summary"]["red_lines_failed"] == 1
    assert report["requirements"][0]["status"] == "FAILED"


def test_committed_registry_covers_all_eleven_domains():
    requirements = load_requirements(REGISTRY)
    assert {item["domain"] for item in requirements} == {
        "data_moat",
        "alpha_research",
        "validation_model_risk",
        "portfolio_construction",
        "risk_management",
        "execution_microstructure",
        "ai_agents",
        "governance_security",
        "platform_engineering",
        "reliability_operations",
        "frontend_console",
    }
    assert len(requirements) >= 22


def test_paths_exist_requirements_never_pass_from_unversioned_local_cache():
    requirements = load_requirements(REGISTRY)
    for item in requirements:
        if item["checker"] != "paths_exist":
            continue
        for path in item.get("checker_args", {}).get("paths", []):
            assert not path.replace("\\", "/").startswith("data/cache/"), item["id"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("domain", "unknown"),
        ("tier", "C"),
        ("target_maturity", "M9"),
        ("classification", "MAGIC"),
        ("required_evidence", ["implementation", "hope"]),
        ("checker", "unknown_checker"),
    ],
)
def test_registry_rejects_invalid_enums(tmp_path, field, value):
    path = _write_registry(tmp_path, [_requirement("BAD", **{field: value})])
    with pytest.raises(RegistryError, match=field):
        load_requirements(path)


def test_manual_and_external_requirements_never_auto_pass(tmp_path):
    path = _write_registry(
        tmp_path,
        [
            _requirement("GOV.IDENTITY.STRONG_AUTH", checker="manual_evidence", red_line=True),
            _requirement(
                "DATA.VENDOR.TICK", checker="paths_exist", checker_args={"paths": []}, classification="NEEDS_EXTERNAL"
            ),
        ],
    )
    report = assess_repository(tmp_path, path)
    statuses = {item["id"]: item["status"] for item in report["requirements"]}
    assert statuses == {
        "GOV.IDENTITY.STRONG_AUTH": "MANUAL_EVIDENCE_REQUIRED",
        "DATA.VENDOR.TICK": "MANUAL_EVIDENCE_REQUIRED",
    }
    assert report["summary"]["passed"] == 0


def test_external_requirement_can_report_opt_in_local_readiness_but_never_pass(tmp_path):
    (tmp_path / "simulated_sor.py").write_text("SIMULATION_ONLY = True\n", encoding="utf-8")
    path = _write_registry(
        tmp_path,
        [
            _requirement(
                "EXECUTION.MULTIVENUE.SOR",
                classification="POST_L2",
                target_maturity="M4",
                checker="paths_exist",
                checker_args={
                    "paths": ["simulated_sor.py"],
                    "pass_maturity": "M2",
                },
                assess_local_readiness=True,
            )
        ],
    )

    report = assess_repository(tmp_path, path)
    requirement = report["requirements"][0]

    assert requirement["status"] == "PARTIAL"
    assert requirement["current_maturity"] == "M2"
    assert requirement["meets_target"] is False
    assert requirement["evidence"] == ["simulated_sor.py"]
    assert "external target evidence remains required" in requirement["detail"]
    assert report["summary"]["passed"] == 0


def test_local_readiness_opt_in_is_rejected_for_l2_safe_requirement(tmp_path):
    path = _write_registry(
        tmp_path,
        [
            _requirement(
                "EXECUTION.LOCAL",
                classification="L2_SAFE_NOW",
                assess_local_readiness=True,
            )
        ],
    )

    with pytest.raises(RegistryError, match="only valid for external or post-L2"):
        load_requirements(path)


def test_all_tier_a_targets_can_reach_top_tier_research(tmp_path):
    (tmp_path / "proof.txt").write_text("real evidence", encoding="utf-8")
    path = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.PROOF",
                checker="paths_exist",
                checker_args={"paths": ["proof.txt"], "pass_maturity": "M3"},
                red_line=True,
            )
        ],
    )
    report = assess_repository(tmp_path, path)
    assert report["verdict"] == "TOP_TIER_RESEARCH"
    assert report["requirements"][0]["status"] == "PASS"
    assert report["domains"][0]["current_maturity"] == "M3"


def test_assessment_is_deterministic(tmp_path):
    (tmp_path / "proof.txt").write_text("proof", encoding="utf-8")
    path = _write_registry(
        tmp_path, [_requirement("PLATFORM.PROOF", checker="paths_exist", checker_args={"paths": ["proof.txt"]})]
    )
    assert assess_repository(tmp_path, path) == assess_repository(tmp_path, path)


def test_assessment_context_accepts_fixed_as_of_and_rejects_invalid_time(tmp_path):
    (tmp_path / "proof.txt").write_text("proof", encoding="utf-8")
    path = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.PROOF",
                checker="paths_exist",
                checker_args={"paths": ["proof.txt"]},
            )
        ],
    )

    first = assess_repository(tmp_path, path, as_of_ms=1_784_044_800_000)
    second = assess_repository(tmp_path, path, as_of_ms=1_784_044_800_000)

    assert first == second
    with pytest.raises(ValueError, match="as_of_ms"):
        assess_repository(tmp_path, path, as_of_ms=True)
    with pytest.raises(ValueError, match="as_of_ms"):
        assess_repository(tmp_path, path, as_of_ms=-1)


def test_checker_pass_below_target_is_partial_and_red_line_remains_open(tmp_path):
    (tmp_path / "proof.txt").write_text("proof", encoding="utf-8")
    path = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.PARTIAL",
                target_maturity="M4",
                red_line=True,
                checker="paths_exist",
                checker_args={"paths": ["proof.txt"], "pass_maturity": "M2"},
            )
        ],
    )
    report = assess_repository(tmp_path, path)
    assert report["requirements"][0]["status"] == "PARTIAL"
    assert report["summary"]["red_lines_failed"] == 1
    assert report["verdict"] == "NOT_TOP_TIER"


def _l2_source_contract(*, version: int | float = 2) -> dict:
    contract = {
        "version": version,
        "sources": [
            {
                "relative_path": "data/cache/approvals.db",
                "required": False,
                "role": "approval_authority",
            },
            {
                "relative_path": "data/cache/domain_events.db",
                "required": True,
                "role": "domain_event_authority",
            },
        ],
    }
    if version >= 12:
        for source in contract["sources"]:
            source["snapshot_policy"] = "STANDARD"
    return contract


def _l2_source_contract_sha256(contract: dict) -> str:
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _valid_l2_backup_evidence(*, external: bool = True) -> dict:
    contract = _l2_source_contract()
    required_source = {
        key: contract["sources"][1][key]
        for key in ("relative_path", "required", "role")
    }
    return {
        "schema_version": 1,
        "verdict": "PASS",
        "mode": "L2_AUTHORITY_STATE",
        "encrypted": True,
        "manifest_verified": True,
        "source_contract": contract,
        "source_contract_sha256": _l2_source_contract_sha256(contract),
        "source_contract_verified": True,
        "backed_up_sources": [required_source],
        "missing_optional_sources": ["data/cache/approvals.db"],
        "restore_verified": True,
        "sqlite_integrity_verified": True,
        "restore_history_verified": True,
        "rto_met": True,
        "rpo_met": True,
        "observed_at_ms": 1,
        "file_count": 1,
        "required_source_count": 1,
        "restore_elapsed_ms": 10,
        "rto_ms": 100,
        "source_age_ms": 10,
        "max_rpo_ms": 100,
        "backup_elapsed_ms": 20,
        "external_destination_verified": external,
        "destination_class": "SEPARATE_VOLUME" if external else "LOCAL_ONLY",
    }


def _l2_checker_args(path: str, *, pass_maturity: str) -> dict:
    return {
        "path": path,
        "implementation_paths": [],
        "expected_source_contract": _l2_source_contract(),
        "max_rpo_ms": 100,
        "max_rto_ms": 100,
        "pass_maturity": pass_maturity,
    }


def test_restore_drill_red_line_rejects_legacy_generic_restore_evidence(tmp_path):
    evidence = tmp_path / "restore.json"
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "OPS.RESTORE.DRILL",
                domain="reliability_operations",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="l2_backup_evidence",
                checker_args=_l2_checker_args("restore.json", pass_maturity="M4"),
            )
        ],
    )

    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "verdict": "PASS",
                "mode": "ENCRYPTED",
                "integrity_verified": True,
                "rto_met": True,
                "rpo_met": True,
                "file_count": 1,
                "elapsed_ms": 10,
                "rto_ms": 100,
                "source_age_ms": 10,
                "max_rpo_ms": 86_400_000,
            }
        ),
        encoding="utf-8",
    )
    report = assess_repository(tmp_path, registry)
    assert report["requirements"][0]["status"] == "FAILED"

    evidence.write_text(json.dumps(_valid_l2_backup_evidence()), encoding="utf-8")
    report = assess_repository(tmp_path, registry)
    assert report["requirements"][0]["status"] == "PARTIAL"
    assert report["requirements"][0]["current_maturity"] == "M3"


def test_l2_backup_checker_requires_external_encrypted_restore_verified_evidence(tmp_path):
    for relative in ("tools/l2_backup.py", "tools/l2_scheduler_watchdog.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("implementation", encoding="utf-8")
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "OPS.BACKUP.ENCRYPTED",
                domain="reliability_operations",
                target_maturity="M3",
                checker="l2_backup_evidence",
                checker_args={
                    "path": "evidence/l2_backup_latest.json",
                    "implementation_paths": [
                        "tools/l2_backup.py",
                        "tools/l2_scheduler_watchdog.py",
                    ],
                    "expected_source_contract": _l2_source_contract(),
                    "max_rpo_ms": 100,
                    "max_rto_ms": 100,
                    "pass_maturity": "M3",
                },
            )
        ],
    )

    report = assess_repository(tmp_path, registry)
    assert report["requirements"][0]["status"] == "PARTIAL"

    evidence = tmp_path / "evidence" / "l2_backup_latest.json"
    evidence.parent.mkdir()
    payload = _valid_l2_backup_evidence(external=False)
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    report = assess_repository(tmp_path, registry)
    assert report["requirements"][0]["status"] == "PARTIAL"

    payload["external_destination_verified"] = True
    payload["destination_class"] = "SEPARATE_VOLUME"
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    report = assess_repository(tmp_path, registry)
    assert report["requirements"][0]["status"] == "PASS"

    payload["destination_class"] = "SEPARATE_FILESYSTEM"
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    report = assess_repository(tmp_path, registry)
    assert report["requirements"][0]["status"] == "PASS"


def test_l2_backup_checker_accepts_v2_through_v14_and_rejects_v15(tmp_path):
    """The checker must accept every released source-contract version.

    v14 is current. A future unreviewed version must still fail closed instead
    of being accepted by a broad integer check.
    """
    for relative in ("tools/l2_backup.py", "tools/l2_scheduler_watchdog.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("implementation", encoding="utf-8")
    evidence = tmp_path / "evidence" / "l2_backup_latest.json"
    evidence.parent.mkdir()
    payload = _valid_l2_backup_evidence()

    def assess_version(version: int) -> dict:
        contract = _l2_source_contract(version=version)
        registry = _write_registry(
            tmp_path,
            [
                _requirement(
                    "OPS.BACKUP.ENCRYPTED",
                    domain="reliability_operations",
                    target_maturity="M3",
                    checker="l2_backup_evidence",
                    checker_args={
                        "path": "evidence/l2_backup_latest.json",
                        "implementation_paths": [
                            "tools/l2_backup.py",
                            "tools/l2_scheduler_watchdog.py",
                        ],
                        "expected_source_contract": contract,
                        "max_rpo_ms": 100,
                        "max_rto_ms": 100,
                        "pass_maturity": "M3",
                    },
                )
            ],
        )
        payload["source_contract"] = contract
        payload["source_contract_sha256"] = _l2_source_contract_sha256(contract)
        evidence.write_text(json.dumps(payload), encoding="utf-8")
        return assess_repository(tmp_path, registry)["requirements"][0]

    for version in range(2, 16):
        assert assess_version(version)["status"] == "PASS", f"v{version}"

    future = assess_version(16)
    assert future["status"] == "UNVERIFIED"
    assert "source contract version" in future["detail"]

    non_integer = assess_version(14.0)
    assert non_integer["status"] == "UNVERIFIED"
    assert "source contract version" in non_integer["detail"]


def test_l2_backup_checker_allows_ashare_sources_only_from_v14(tmp_path):
    contract = _l2_source_contract(version=13)
    contract["sources"].append(
        {
            "relative_path": "data/stocks/stocks.db",
            "required": True,
            "role": "ashare_market_data_authority",
            "snapshot_policy": "STANDARD",
        }
    )
    contract["sources"].sort(key=lambda source: source["relative_path"])
    checker_args = _l2_checker_args("evidence.json", pass_maturity="M3")
    checker_args["expected_source_contract"] = contract
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "OPS.BACKUP.ENCRYPTED",
                checker="l2_backup_evidence",
                checker_args=checker_args,
            )
        ],
    )

    rejected = assess_repository(tmp_path, registry)["requirements"][0]
    assert rejected["status"] == "UNVERIFIED"
    assert "version 14" in rejected["detail"]

    contract["version"] = 14
    checker_args["expected_source_contract"] = contract
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "OPS.BACKUP.ENCRYPTED",
                checker="l2_backup_evidence",
                checker_args=checker_args,
            )
        ],
    )
    payload = _valid_l2_backup_evidence()
    payload["source_contract"] = contract
    payload["source_contract_sha256"] = _l2_source_contract_sha256(contract)
    payload["backed_up_sources"] = [
        {
            key: source[key]
            for key in ("relative_path", "required", "role")
        }
        for source in contract["sources"]
    ]
    payload["file_count"] = len(contract["sources"])
    payload["required_source_count"] = sum(
        source["required"] for source in contract["sources"]
    )
    payload["missing_optional_sources"] = []
    (tmp_path / "evidence.json").write_text(json.dumps(payload), encoding="utf-8")

    accepted = assess_repository(tmp_path, registry)["requirements"][0]
    assert accepted["status"] == "PASS"


def test_l2_backup_checker_accepts_policy_bound_v13_evidence_sources(tmp_path):
    contract = _l2_source_contract(version=13)
    contract["sources"].append(
        {
            "relative_path": "evidence/preregistration.json",
            "required": False,
            "role": "frozen_preregistration",
            "snapshot_policy": "STANDARD",
        }
    )
    contract["sources"].sort(key=lambda source: source["relative_path"])
    checker_args = _l2_checker_args("evidence.json", pass_maturity="M3")
    checker_args["expected_source_contract"] = contract
    restore_args = {
        "history_path": "data/cache/l2_restore_history.db",
        "checkpoint_path": "evidence/l2_restore_history_checkpoint.json",
        "trust_registry_path": "config/l2_restore_checkpoint_trust_registry.json",
        "implementation_paths": [],
        "expected_source_contract": contract,
        "max_attempt_age_ms": 100,
        "max_success_age_ms": 100,
        "max_attempt_gap_ms": 150,
        "max_success_gap_ms": 150,
        "minimum_history_span_ms": 200,
        "minimum_successes": 3,
        "clean_lookback_ms": 300,
        "attempt_timeout_ms": 50,
        "required_checkpoint_issuer": "external-restore-authority",
        "required_checkpoint_key_id": "restore-key",
        "max_checkpoint_future_skew_ms": 5,
        "max_checkpoint_age_ms": 100,
        "minimum_checkpoint_retention_ms": 1_000,
    }
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "OPS.BACKUP.ENCRYPTED",
                checker="l2_backup_evidence",
                checker_args=checker_args,
            ),
            _requirement(
                "OPS.RESTORE.DRILL",
                domain="reliability_operations",
                target_maturity="M4",
                checker="l2_restore_history_evidence",
                checker_args=restore_args,
            ),
        ],
    )
    payload = _valid_l2_backup_evidence()
    payload["source_contract"] = contract
    payload["source_contract_sha256"] = _l2_source_contract_sha256(contract)
    payload["missing_optional_sources"] = [
        "data/cache/approvals.db",
        "evidence/preregistration.json",
    ]
    (tmp_path / "evidence.json").write_text(json.dumps(payload), encoding="utf-8")

    results = {
        item["id"]: item
        for item in assess_repository(tmp_path, registry)["requirements"]
    }

    assert results["OPS.BACKUP.ENCRYPTED"]["status"] == "PASS"
    assert results["OPS.RESTORE.DRILL"]["status"] == "PARTIAL"
    assert results["OPS.RESTORE.DRILL"]["current_maturity"] == "M3"
    assert "not provisioned" in results["OPS.RESTORE.DRILL"]["detail"]


@pytest.mark.parametrize(
    ("case", "detail"),
    [
        ("missing_policy", "source entries for version 13"),
        ("invalid_policy", "source snapshot policies"),
        ("wal_policy_on_json", "WAL-pinned source snapshot policies"),
        ("legacy_policy_field", "source entries for version 11"),
        ("outside_allowed_roots", "data/cache, data/stocks"),
    ],
)
def test_l2_backup_checker_rejects_invalid_versioned_source_contracts(
    tmp_path, case, detail
):
    version = 11 if case == "legacy_policy_field" else 13
    contract = _l2_source_contract(version=version)
    source = contract["sources"][0]
    if case == "missing_policy":
        source.pop("snapshot_policy")
    elif case == "invalid_policy":
        source["snapshot_policy"] = "BEST_EFFORT"
    elif case == "wal_policy_on_json":
        source["relative_path"] = "evidence/approvals.json"
        source["snapshot_policy"] = "SQLITE_WAL_PINNED"
        contract["sources"].sort(key=lambda item: item["relative_path"])
    elif case == "legacy_policy_field":
        source["snapshot_policy"] = "STANDARD"
    elif case == "outside_allowed_roots":
        source["relative_path"] = "config/approvals.db"
        contract["sources"].sort(key=lambda item: item["relative_path"])
    checker_args = _l2_checker_args("evidence.json", pass_maturity="M3")
    checker_args["expected_source_contract"] = contract
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "OPS.BACKUP.ENCRYPTED",
                checker="l2_backup_evidence",
                checker_args=checker_args,
            )
        ],
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "UNVERIFIED"
    assert detail in result["detail"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_contract_verified", False),
        ("source_contract_sha256", "0" * 64),
        ("backed_up_sources", []),
        ("missing_optional_sources", []),
        ("manifest_verified", False),
        ("sqlite_integrity_verified", False),
        ("restore_history_verified", False),
        ("file_count", True),
        ("required_source_count", 0),
        ("rto_ms", 101),
        ("max_rpo_ms", 101),
    ],
)
def test_l2_backup_checker_rejects_contract_partition_and_threshold_drift(
    tmp_path, field, value
):
    evidence = tmp_path / "evidence.json"
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "OPS.BACKUP.ENCRYPTED",
                checker="l2_backup_evidence",
                checker_args=_l2_checker_args("evidence.json", pass_maturity="M3"),
            )
        ],
    )
    payload = _valid_l2_backup_evidence()
    payload[field] = value
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"


def test_l2_backup_checker_rejects_same_count_source_contract_drift(tmp_path):
    evidence = tmp_path / "evidence.json"
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "OPS.BACKUP.ENCRYPTED",
                checker="l2_backup_evidence",
                checker_args=_l2_checker_args("evidence.json", pass_maturity="M3"),
            )
        ],
    )
    payload = _valid_l2_backup_evidence()
    payload["source_contract"]["sources"][1]["role"] = "wrong_authority"
    payload["source_contract_sha256"] = _l2_source_contract_sha256(
        payload["source_contract"]
    )
    payload["backed_up_sources"] = [payload["source_contract"]["sources"][1]]
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"


def test_l2_backup_checker_rejects_duplicate_json_members(tmp_path):
    evidence = tmp_path / "evidence.json"
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "OPS.BACKUP.ENCRYPTED",
                checker="l2_backup_evidence",
                checker_args=_l2_checker_args("evidence.json", pass_maturity="M3"),
            )
        ],
    )
    content = json.dumps(_valid_l2_backup_evidence())
    evidence.write_text(
        content.replace(
            '"verdict": "PASS"',
            '"verdict": "FAILED", "verdict": "PASS"',
            1,
        ),
        encoding="utf-8",
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "duplicate JSON key" in result["detail"]


def _valid_risk_recovery_evidence():
    hashes = {
        "audit.db": "a" * 64,
        "kill_switch.db": "b" * 64,
        "kill_switch.json": "c" * 64,
        "ledger.db": "d" * 64,
    }
    ledger = {
        "orders": [{"client_order_id": "drill-order-1"}],
        "fills": [{"fill_id": "drill-fill-1"}],
        "positions": {"AAA": 2.0},
    }
    return {
        "schema_version": 2,
        "verdict": "PASS",
        "drill_mode": "ISOLATED_OFFLINE",
        "observed_at_ms": 1_783_734_400_000,
        "elapsed_ms": 10,
        "rto_ms": 100,
        "rto_met": True,
        "artifact_names": [
            "audit.db",
            "kill_switch.db",
            "kill_switch.json",
            "ledger.db",
        ],
        "recovered_state_count": 4,
        "pre_restart_sha256": hashes,
        "post_restart_sha256": hashes,
        "ledger_before": ledger,
        "ledger_after": ledger,
        "kill_switch_persisted": True,
        "kill_switch_chain_verified": True,
        "kill_switch_event_recoverable": True,
        "kill_switch_outbox_pending_count": 1,
        "gate_rejected_while_tripped": True,
        "gate_rejection_code": "KILL_SWITCH_ACTIVE",
        "ledger_recovered": True,
        "audit_chain_verified": True,
        "corrupt_kill_switch_failed_closed": True,
        "tampered_kill_switch_detected": True,
        "tampered_audit_detected": True,
        "active_state_untouched": True,
    }


def test_risk_recovery_checker_requires_all_recovery_and_fault_invariants(tmp_path):
    evidence = tmp_path / "risk-recovery.json"
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "RISK.RECOVERY.DRILL",
                domain="risk_management",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="risk_recovery_evidence",
                checker_args={"path": "risk-recovery.json", "pass_maturity": "M4"},
            )
        ],
    )
    payload = _valid_risk_recovery_evidence()
    payload["tampered_audit_detected"] = False
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    assert assess_repository(tmp_path, registry)["requirements"][0]["status"] == "FAILED"

    evidence.write_text(json.dumps(_valid_risk_recovery_evidence()), encoding="utf-8")
    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "PASS", result["detail"]
    assert result["current_maturity"] == "M4"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verdict", "FAILED"),
        ("rto_met", False),
        ("elapsed_ms", 101),
        ("observed_at_ms", True),
        ("artifact_names", ["ledger.db"]),
    ],
)
def test_risk_recovery_checker_rejects_invalid_evidence_fields(tmp_path, field, value):
    evidence = tmp_path / "risk-recovery.json"
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "RISK.RECOVERY.DRILL",
                domain="risk_management",
                tier="B",
                target_maturity="M4",
                checker="risk_recovery_evidence",
                checker_args={"path": "risk-recovery.json", "pass_maturity": "M4"},
            )
        ],
    )
    payload = _valid_risk_recovery_evidence()
    payload[field] = value
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"


def test_risk_recovery_checker_rejects_digest_and_ledger_mismatch(tmp_path):
    evidence = tmp_path / "risk-recovery.json"
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "RISK.RECOVERY.DRILL",
                domain="risk_management",
                tier="B",
                target_maturity="M4",
                checker="risk_recovery_evidence",
                checker_args={"path": "risk-recovery.json", "pass_maturity": "M4"},
            )
        ],
    )
    payload = _valid_risk_recovery_evidence()
    payload["post_restart_sha256"] = {
        **payload["post_restart_sha256"],
        "ledger.db": "e" * 64,
    }
    payload["ledger_after"] = {
        **payload["ledger_after"],
        "positions": {"AAA": 1.0},
    }
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "ledger_summary" in result["detail"]
    assert "state_digests" in result["detail"]


def test_risk_recovery_checker_requires_declared_cadence_markers(tmp_path):
    evidence = tmp_path / "risk-recovery.json"
    evidence.write_text(json.dumps(_valid_risk_recovery_evidence()), encoding="utf-8")
    scripts = tmp_path / "scripts"
    workflows = tmp_path / ".github" / "workflows"
    scripts.mkdir()
    workflows.mkdir(parents=True)
    gate = scripts / "validate.py"
    schedule = workflows / "validate.yml"
    gate.write_text("missing cadence", encoding="utf-8")
    schedule.write_text("schedule:\n  - cron: weekly\n", encoding="utf-8")
    cadence = {
        "scripts/validate.py": ["risk-recovery-drill:fresh"],
        ".github/workflows/validate.yml": ["schedule:", "cron:"],
    }
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "RISK.RECOVERY.DRILL",
                domain="risk_management",
                tier="B",
                target_maturity="M4",
                checker="risk_recovery_evidence",
                checker_args={
                    "path": "risk-recovery.json",
                    "pass_maturity": "M4",
                    "continuous_markers": cadence,
                },
            )
        ],
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "continuous_marker" in result["detail"]

    gate.write_text("risk-recovery-drill:fresh", encoding="utf-8")
    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "PASS"
    assert set(result["evidence"]) == {
        "risk-recovery.json",
        "scripts/validate.py",
        ".github/workflows/validate.yml",
    }


def _write_reproducibility_oci(path: Path, epoch: int) -> None:
    created = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def encoded(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def digest(content: bytes) -> str:
        return "sha256:" + hashlib.sha256(content).hexdigest()

    layer_content = b"fixture-layer\n"
    layer = gzip.compress(layer_content, mtime=0)
    layer_digest = digest(layer)
    config = encoded(
        {
            "architecture": "amd64",
            "created": created,
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": [digest(layer_content)]},
        }
    )
    config_digest = digest(config)
    manifest = encoded(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "digest": layer_digest,
                    "size": len(layer),
                    "annotations": {"buildkit/rewritten-timestamp": str(epoch)},
                }
            ],
        }
    )
    manifest_digest = digest(manifest)
    index = encoded(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": manifest_digest,
                    "size": len(manifest),
                    "annotations": {"org.opencontainers.image.created": created},
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        }
    )
    members = {
        "oci-layout": encoded({"imageLayoutVersion": "1.0.0"}),
        "index.json": index,
        "blobs/sha256/" + manifest_digest.split(":", 1)[1]: manifest,
        "blobs/sha256/" + config_digest.split(":", 1)[1]: config,
        "blobs/sha256/" + layer_digest.split(":", 1)[1]: layer,
    }
    with tarfile.open(path, mode="w") as archive:
        for name, content in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mtime = epoch
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(content))


def _write_valid_supply_chain_evidence(tmp_path):
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "components": [{"type": "library", "name": "demo", "version": "1.0"}],
    }
    sbom_text = json.dumps(sbom, indent=2, sort_keys=True) + "\n"
    (tmp_path / "sbom.json").write_bytes(sbom_text.encode("utf-8"))
    manifest = tmp_path / "requirements.txt"
    manifest.write_bytes(b"demo==1.0\n")
    workflow = tmp_path / ".github/workflows/validate.yml"
    workflow.parent.mkdir(parents=True)
    checkout_sha = "a" * 40
    workflow.write_text(
        f"jobs:\n  test:\n    steps:\n      - uses: actions/checkout@{checkout_sha}\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements-dev.txt").write_text(
        "-r requirements.txt\npip-audit==2.10.1\nsigstore==4.4.0\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["demo==1.0"]\n'
        "[project.optional-dependencies]\n"
        'supply-chain = ["pip-audit==2.10.1", "sigstore==4.4.0"]\n',
        encoding="utf-8",
    )
    admission_sources = {
        "services/governance/artifact_admission.py": (
            "MAX_ARTIFACT_AGE_MS max_verification_age_ms "
            "max_checkpoint_age_ms max_revocation_age_ms CERTIFICATE_REVOKED "
            "REVOCATION_STATUS_UNKNOWN load_json_without_duplicate_keys "
            "VERIFIER_RUNTIME_MISMATCH required_receipt_authority_id "
            "required_receipt_public_key_sha256 "
            "RECEIPT_AUTHENTICATION_REQUIRED\n"
            "def evaluate_verified_provenance(): pass\n"
        ),
        "services/governance/artifact_admission_store.py": (
            "ArtifactAdmissionStore BEGIN IMMEDIATE PRAGMA journal_mode=WAL "
            "PRAGMA synchronous=FULL append-only "
            "ArtifactAdmissionStoreClockRollback "
            "ArtifactAdmissionStoreRegistryRollback "
            "ArtifactAdmissionStoreRegistryConflict "
            "artifact_admission_authority_events append_registry_snapshot "
            "ArtifactAdmissionStoreRegistryHeadChanged "
            "_validate_allow_registry_binding "
            "ARTIFACT_ADMISSION_MAX_FUTURE_SKEW_MS "
            "_local_authority_clock_ms _read_verified_locked "
            "authority_time_floor\n"
        ),
        "services/governance/artifact_receipt_auth.py": (
            "Ed25519PublicKey SignedArtifactVerificationReceipt "
            "artifact_receipt_signing_payload verify_signed_artifact_receipt "
            "RECEIPT_SIGNATURE_INVALID\n"
        ),
        "services/governance/external_sigstore_evidence.py": (
            "def verify_external_sigstore_evidence(): pass\n"
        ),
        "services/governance/artifact_admission_external_seal.py": (
            "def verify_artifact_admission_external_seal(): pass\n"
        ),
        "services/governance/json_integrity.py": (
            "def load_json_without_duplicate_keys(): pass\n"
        ),
        "services/governance/cross_host_container_reproducibility.py": (
            "def verify_signed_cross_host_container_evidence(): pass\n"
        ),
        "tools/verify_artifact_admission.py": (
            "TrustedRoot.from_file Verifier(trusted_root= Identity( "
            "verify_dsse POLICY_REGISTRY_PATH _policy_registry_decision "
            "--verification-receipt POLICY_NOT_TRUSTED POLICY_REVOKED "
            "DECISION_STORE_PATH ArtifactAdmissionStore create=False "
            "_persist_and_emit _runtime_verifier_id "
            "RECEIPT_AUTHORITY_REGISTRY_PATH "
            "_receipt_authority_registry_decision verify_signed_artifact_receipt "
            "POLICY_REGISTRY_CHANGED_DURING_ADMISSION "
            "RECEIPT_AUTHORITY_REGISTRY_CHANGED_DURING_ADMISSION "
            "expected_policy_registry_head "
            "expected_receipt_authority_registry_head "
            "_registry_mirror_reason "
            "denied_admission_decision\n"
        ),
        "tools/provision_artifact_admission_authority.py": (
            "ArtifactAdmissionStore create=not destination.exists() "
            "append_registry_snapshot _verify_exact check_only "
            "POLICY_REGISTRY_KIND RECEIPT_AUTHORITY_REGISTRY_KIND "
            '"signing_capability_present": False '
            '"live_trading_authority_granted": False\n'
        ),
        "tools/artifact_admission_seal.py": (
            "ArtifactAdmissionStore create=False "
            "verify_artifact_admission_seal create_once\n"
        ),
        "tests/governance/test_artifact_admission.py": "def test_core(): pass\n",
        "tests/governance/test_artifact_admission_cli.py": "def test_cli(): pass\n",
        "tests/governance/test_artifact_admission_store.py": ("def test_store(): pass\n"),
        "tests/governance/test_artifact_receipt_auth.py": ("def test_receipt_auth(): pass\n"),
        "tests/governance/test_artifact_admission_provisioning.py": (
            "def test_provisioning(): pass\n"
        ),
        "tests/governance/test_external_sigstore_evidence.py": (
            "def test_external_sigstore(): pass\n"
        ),
        "tests/governance/test_artifact_admission_external_seal.py": (
            "def test_external_chain_head(): pass\n"
        ),
        "tests/tools/test_artifact_admission_seal.py": (
            "def test_artifact_admission_seal(): pass\n"
        ),
    }
    for relative, content in admission_sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    policy_registry_path = tmp_path / "config/artifact_admission_policy_registry.json"
    policy_registry_path.parent.mkdir(parents=True, exist_ok=True)
    policy_registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "readiness": "L2_DRY_RUN_ONLY",
                "generation": 0,
                "policies": {},
            }
        ),
        encoding="utf-8",
    )
    receipt_authority_registry_path = tmp_path / "config/artifact_receipt_authority_registry.json"
    receipt_authority_registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "readiness": "L2_DRY_RUN_ONLY",
                "generation": 0,
                "keys": {},
            }
        ),
        encoding="utf-8",
    )
    console = tmp_path / "apps/research-console"
    console.mkdir(parents=True)
    (console / "package.json").write_text(json.dumps({"name": "fixture"}), encoding="utf-8")
    (console / "package-lock.json").write_text(
        json.dumps({"name": "fixture", "lockfileVersion": 3, "packages": {}}),
        encoding="utf-8",
    )
    audit_report = {
        "auditReportVersion": 2,
        "vulnerabilities": {},
        "metadata": {
            "vulnerabilities": {
                "info": 0,
                "low": 0,
                "moderate": 0,
                "high": 0,
                "critical": 0,
                "total": 0,
            },
            "dependencies": {
                "prod": 0,
                "dev": 0,
                "optional": 0,
                "peer": 0,
                "peerOptional": 0,
                "total": 0,
            },
        },
    }
    npm_audit = build_npm_audit_evidence(tmp_path, report=audit_report, npm_version="11.8.0", observed_at=1)
    npm_audit_path = tmp_path / "npm-audit.json"
    npm_audit_path.write_text(json.dumps(npm_audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    python_report = {
        "dependencies": [
            {"name": "demo", "version": "1.0", "vulns": []},
            {"name": "pip-audit", "version": "2.10.1", "vulns": []},
            {"name": "sigstore", "version": "4.4.0", "vulns": []},
        ],
        "fixes": [],
    }
    python_audit = build_python_audit_evidence(
        tmp_path,
        report=python_report,
        scanner_version="2.10.1",
        observed_at=1,
    )
    python_audit_path = tmp_path / "python-audit.json"
    python_audit_path.write_text(
        json.dumps(python_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    actions_audit = build_actions_audit_evidence(tmp_path, responses={checkout_sha: {}}, observed_at=1)
    actions_audit_path = tmp_path / "actions-audit.json"
    actions_audit_path.write_text(
        json.dumps(actions_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    base_digest = "sha256:" + "a" * 64
    base_id = "sha256:" + "b" * 64
    runtime_digest = "sha256:" + "c" * 64
    base_ref = f"public.ecr.aws/docker/library/python@{base_digest}"
    dockerfile = tmp_path / "deploy/console.Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text(
        f"ARG PYTHON_IMAGE={base_ref}\nARG SOURCE_DATE_EPOCH=1\nFROM ${{PYTHON_IMAGE}}\n",
        encoding="utf-8",
    )
    (tmp_path / ".dockerignore").write_text(".git/\n", encoding="utf-8")

    def container_report(
        artifact: str,
        image_id: str,
        vulnerabilities: list[dict[str, str]] | None,
    ) -> dict[str, object]:
        repo_digest = artifact if "@" in artifact else f"quantos-console@{image_id}"
        return {
            "SchemaVersion": 2,
            "CreatedAt": "1970-01-01T00:00:01Z",
            "ArtifactName": artifact,
            "ArtifactType": "container_image",
            "Metadata": {
                "ImageID": image_id,
                "RepoDigests": [repo_digest],
                "OS": {"Family": "alpine", "Name": "3.24.1"},
            },
            "Results": [
                {
                    "Target": f"{artifact} (alpine 3.24.1)",
                    "Class": "os-pkgs",
                    "Type": "alpine",
                    "Vulnerabilities": vulnerabilities,
                }
            ],
        }

    container_audit = build_container_audit_evidence(
        tmp_path,
        base_report=container_report(
            base_ref,
            base_id,
            [
                {
                    "VulnerabilityID": "CVE-REMEDIATED",
                    "PkgName": "pip",
                    "InstalledVersion": "1.0",
                    "FixedVersion": "2.0",
                    "Status": "fixed",
                    "Severity": "MEDIUM",
                }
            ],
        ),
        runtime_report=container_report("quantos-console:hardened", runtime_digest, None),
        observed_at=1,
        database_updated_at="1970-01-01T00:00:00Z",
        database_next_update="1970-01-02T00:00:00Z",
        database_downloaded_at="1970-01-01T00:00:00Z",
    )
    container_audit_path = tmp_path / "container-audit.json"
    container_audit_path.write_text(
        json.dumps(container_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    first_oci = tmp_path / "first.oci.tar"
    second_oci = tmp_path / "second.oci.tar"
    _write_reproducibility_oci(first_oci, 1)
    second_oci.write_bytes(first_oci.read_bytes())
    reproducibility = build_container_reproducibility_evidence(
        tmp_path,
        archive_a=first_oci,
        archive_b=second_oci,
        observed_at=1,
        build_tool={
            "builder_name": "desktop-linux",
            "builder_driver": "docker",
            "buildx_version": ("github.com/docker/buildx v0.30.1-desktop.1 " + "d" * 40),
            "buildkit_version": "v0.26.2",
            "docker_client_version": "29.1.3",
            "docker_server_version": "29.1.3",
        },
    )
    reproducibility_path = tmp_path / "reproducibility.json"
    reproducibility_path.write_text(
        json.dumps(reproducibility, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evidence = {
        "schema_version": 1,
        "verdict": "PARTIAL",
        "mode": "OFFLINE_REPOSITORY_BASELINE",
        "sbom_format": "CycloneDX",
        "sbom_spec_version": "1.6",
        "sbom_path": "sbom.json",
        "sbom_sha256": hashlib.sha256(sbom_text.encode("utf-8")).hexdigest(),
        "component_count": 1,
        "python_component_count": 1,
        "python_requirements_all_pinned": True,
        "unresolved_python_optional_dependencies": [],
        "npm_lockfile_version": 3,
        "npm_locked_components": 1,
        "npm_integrity_components": 1,
        "actions": [
            {
                "workflow": ".github/workflows/validate.yml",
                "reference": "actions/checkout@" + checkout_sha,
                "sha_pinned": True,
            }
        ],
        "actions_all_sha_pinned": True,
        "manifest_sha256": {"requirements.txt": source_file_sha256(manifest)},
        "sast_gate_wired": True,
        "reproducible_inputs": True,
        "vulnerability_scan_status": ("PARTIAL_EXTERNAL_NPM_PYTHON_ACTIONS_CONTAINER_VERIFIED"),
        "npm_vulnerability_evidence_status": "VERIFIED_EXTERNAL_ZERO_KNOWN",
        "npm_vulnerability_evidence_ref": "npm-audit.json",
        "npm_vulnerability_evidence_sha256": hashlib.sha256(npm_audit_path.read_bytes()).hexdigest(),
        "npm_vulnerability_observed_at": 1,
        "npm_vulnerability_count": 0,
        "python_vulnerability_evidence_status": "VERIFIED_EXTERNAL_ZERO_KNOWN",
        "python_vulnerability_evidence_ref": "python-audit.json",
        "python_vulnerability_evidence_sha256": hashlib.sha256(python_audit_path.read_bytes()).hexdigest(),
        "python_vulnerability_observed_at": 1,
        "python_vulnerability_count": 0,
        "actions_vulnerability_evidence_status": "VERIFIED_EXTERNAL_ZERO_KNOWN",
        "actions_vulnerability_evidence_ref": "actions-audit.json",
        "actions_vulnerability_evidence_sha256": hashlib.sha256(actions_audit_path.read_bytes()).hexdigest(),
        "actions_vulnerability_observed_at": 1,
        "actions_vulnerability_count": 0,
        "container_vulnerability_evidence_status": ("VERIFIED_EXTERNAL_BASE_REMEDIATED_RUNTIME_ZERO_KNOWN"),
        "container_vulnerability_evidence_ref": "container-audit.json",
        "container_vulnerability_evidence_sha256": hashlib.sha256(container_audit_path.read_bytes()).hexdigest(),
        "container_vulnerability_observed_at": 1,
        "container_base_vulnerability_count": 1,
        "container_runtime_vulnerability_count": 0,
        "container_runtime_image_digest": runtime_digest,
        "container_reproducibility_evidence_status": ("VERIFIED_LOCAL_TWO_COLD_BUILDS_BYTE_IDENTICAL"),
        "container_reproducibility_evidence_ref": "reproducibility.json",
        "container_reproducibility_evidence_sha256": hashlib.sha256(reproducibility_path.read_bytes()).hexdigest(),
        "container_reproducibility_observed_at": 1,
        "container_reproducible_archive_sha256": reproducibility["builds"][0]["archive_sha256"],
        "container_reproducible_manifest_digest": reproducibility["image"]["manifest_digest"],
        "container_reproducibility_buildkit_version": "v0.26.2",
        "cross_host_container_reproducibility_status": "NOT_PROVIDED",
        "cross_host_container_reproducibility_evidence_ref": None,
        "cross_host_container_reproducibility_evidence": None,
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
        "artifact_admission_policy_registry_ref": ("config/artifact_admission_policy_registry.json"),
        "artifact_admission_policy_registry_sha256": source_file_sha256(
            policy_registry_path
        ),
        "artifact_admission_policy_registry_status": ("L2_REPOSITORY_PINNED_FAIL_CLOSED_NO_ACTIVE_POLICIES"),
        "artifact_admission_policy_registry_generation": 0,
        "artifact_admission_active_policy_count": 0,
        "artifact_admission_receipt_authority_registry_ref": ("config/artifact_receipt_authority_registry.json"),
        "artifact_admission_receipt_authority_registry_sha256": source_file_sha256(
            receipt_authority_registry_path
        ),
        "artifact_admission_receipt_authority_registry_status": ("L2_REPOSITORY_PINNED_FAIL_CLOSED_NO_ACTIVE_KEYS"),
        "artifact_admission_receipt_authority_registry_generation": 0,
        "artifact_admission_active_receipt_key_count": 0,
        "artifact_admission_library": "sigstore",
        "artifact_admission_library_version": "4.4.0",
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
        "artifact_admission_decision_store_status": ("WIRED_FAIL_CLOSED_UNPROVISIONED_FIXED_PATH_HASH_CHAINED_SQLITE"),
        "artifact_admission_decision_store_path": ("data/cache/artifact_admission.db"),
        "artifact_admission_decision_store_provisioning_status": "NOT_PROVIDED",
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
            relative: source_file_sha256(tmp_path / relative)
            for relative in (
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
            )
        },
        "artifact_admission_verification_paths": [
            "tests/governance/test_artifact_admission.py",
            "tests/governance/test_artifact_admission_cli.py",
            "tests/governance/test_artifact_admission_store.py",
            "tests/governance/test_artifact_receipt_auth.py",
            "tests/governance/test_artifact_admission_provisioning.py",
            "tests/governance/test_external_sigstore_evidence.py",
            "tests/governance/test_artifact_admission_external_seal.py",
            "tests/tools/test_artifact_admission_seal.py",
        ],
        "m4_blockers": [
            "CROSS_HOST_CONTAINER_REPRODUCIBILITY_NOT_PROVEN",
            "EXTERNAL_ARTIFACT_SIGNATURE_IDENTITY_MISSING",
            "SIGNED_PROVENANCE_BUNDLE_NOT_RETAINED",
            "INDEPENDENT_ADMISSION_DEPLOYMENT_NOT_PROVEN",
            "EXTERNAL_RECEIPT_AUTHORITY_AND_SIGNED_RECEIPT_NOT_PROVIDED",
            "SOURCE_NATIVE_CHECKPOINT_REVOCATION_PROOFS_NOT_VERIFIED",
            "ARTIFACT_ADMISSION_FIXED_AUTHORITY_STORE_NOT_PROVISIONED",
            "ARTIFACT_ADMISSION_DECISION_HISTORY_NOT_EXTERNALLY_SEALED",
        ],
    }
    path = tmp_path / "supply-chain.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    return path


def test_supply_chain_checker_verifies_baseline_but_keeps_m4_red_line_open(tmp_path):
    evidence = _write_valid_supply_chain_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.SUPPLY_CHAIN",
                domain="platform_engineering",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="supply_chain_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "PARTIAL", result["detail"]
    assert result["current_maturity"] == "M3"

    (tmp_path / "requirements.txt").write_text("tampered\n", encoding="utf-8")
    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "manifest_digest" in result["detail"]


def test_supply_chain_checker_accepts_honest_nonportable_external_anchor_as_partial(
    tmp_path,
):
    evidence = _write_valid_supply_chain_evidence(tmp_path)
    document = json.loads(evidence.read_text(encoding="utf-8"))
    document.update(
        {
            "artifact_admission_decision_history_external_seal_status": (
                "DECLARED_EXTERNAL_ANCHOR_NOT_PORTABLY_VERIFIED"
            ),
            "artifact_admission_decision_history_external_seal_evidence_ref": (
                "evidence/artifact_admission_seal_latest.json"
            ),
            "artifact_admission_decision_history_external_seal_evidence_sha256": (
                "a" * 64
            ),
            "artifact_admission_decision_history_external_anchor_sha256": (
                "b" * 64
            ),
            "artifact_admission_decision_history_external_destination_class": (
                "SEPARATE_VOLUME"
            ),
            "artifact_admission_decision_history_external_signature_verified": False,
            "artifact_admission_decision_history_external_worm_verified": False,
        }
    )
    evidence.write_text(json.dumps(document), encoding="utf-8")
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.SUPPLY_CHAIN",
                domain="platform_engineering",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="supply_chain_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "PARTIAL", result["detail"]
    assert result["current_maturity"] == "M3"
    assert "does not meet target maturity" in result["detail"]


def test_supply_chain_checker_rejects_tampered_npm_audit_evidence(tmp_path):
    evidence = _write_valid_supply_chain_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.SUPPLY_CHAIN",
                domain="platform_engineering",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="supply_chain_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )
    audit = tmp_path / "npm-audit.json"
    audit.write_text(audit.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "npm_vulnerability_evidence" in result["detail"]


def test_supply_chain_checker_rejects_tampered_python_audit_evidence(tmp_path):
    evidence = _write_valid_supply_chain_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.SUPPLY_CHAIN",
                domain="platform_engineering",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="supply_chain_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )
    audit = tmp_path / "python-audit.json"
    audit.write_text(audit.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "python_vulnerability_evidence" in result["detail"]


def test_supply_chain_checker_rejects_tampered_actions_audit_evidence(tmp_path):
    evidence = _write_valid_supply_chain_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.SUPPLY_CHAIN",
                domain="platform_engineering",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="supply_chain_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )
    audit = tmp_path / "actions-audit.json"
    audit.write_text(audit.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "actions_vulnerability_evidence" in result["detail"]


def test_supply_chain_checker_rejects_tampered_container_audit_evidence(tmp_path):
    evidence = _write_valid_supply_chain_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.SUPPLY_CHAIN",
                domain="platform_engineering",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="supply_chain_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )
    audit = tmp_path / "container-audit.json"
    audit.write_text(audit.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "container_vulnerability_evidence" in result["detail"]


def test_supply_chain_checker_rejects_tampered_reproducibility_evidence(tmp_path):
    evidence = _write_valid_supply_chain_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.SUPPLY_CHAIN",
                domain="platform_engineering",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="supply_chain_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )
    retained = tmp_path / "reproducibility.json"
    retained.write_text(retained.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "container_reproducibility_evidence" in result["detail"]


def test_supply_chain_checker_rejects_admission_verifier_tamper(tmp_path):
    evidence = _write_valid_supply_chain_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.SUPPLY_CHAIN",
                domain="platform_engineering",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="supply_chain_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )
    (tmp_path / "tools/verify_artifact_admission.py").write_text("tampered\n", encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "artifact_admission_implementation_digest" in result["detail"]


def test_supply_chain_checker_rejects_policy_registry_tamper(tmp_path):
    evidence = _write_valid_supply_chain_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.SUPPLY_CHAIN",
                domain="platform_engineering",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="supply_chain_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )
    policy_registry = tmp_path / "config/artifact_admission_policy_registry.json"
    policy_registry.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "readiness": "L2_DRY_RUN_ONLY",
                "generation": 0,
                "policies": {"unreviewed": {"status": "ACTIVE"}},
            }
        ),
        encoding="utf-8",
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "artifact_admission_policy_registry" in result["detail"]


def test_supply_chain_checker_rejects_receipt_authority_registry_tamper(
    tmp_path,
):
    evidence = _write_valid_supply_chain_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.SUPPLY_CHAIN",
                domain="platform_engineering",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="supply_chain_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )
    authority_registry = tmp_path / "config/artifact_receipt_authority_registry.json"
    authority_registry.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "readiness": "L2_DRY_RUN_ONLY",
                "generation": 0,
                "keys": {"unreviewed": {"status": "ACTIVE"}},
            }
        ),
        encoding="utf-8",
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "artifact_admission_receipt_authority_registry" in result["detail"]


def test_supply_chain_checker_rejects_missing_store_verification(tmp_path):
    evidence = _write_valid_supply_chain_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.SUPPLY_CHAIN",
                domain="platform_engineering",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="supply_chain_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )
    (tmp_path / "tests/governance/test_artifact_admission_store.py").unlink()

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "artifact_admission_verification_missing" in result["detail"]


@pytest.mark.parametrize(
    ("field", "overclaim"),
    [
        (
            "artifact_admission_receipt_authenticity_status",
            "EXTERNALLY_AUTHENTICATED",
        ),
        ("artifact_admission_decision_store_worm_status", "EXTERNAL_WORM"),
    ],
)
def test_supply_chain_checker_rejects_external_assurance_overclaims(
    tmp_path,
    field,
    overclaim,
):
    evidence = _write_valid_supply_chain_evidence(tmp_path)
    document = json.loads(evidence.read_text(encoding="utf-8"))
    document[field] = overclaim
    evidence.write_text(json.dumps(document), encoding="utf-8")
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "PLATFORM.SUPPLY_CHAIN",
                domain="platform_engineering",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="supply_chain_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"


def _write_valid_model_risk_evidence(
    tmp_path: Path,
    *,
    with_bundle: bool = False,
    longitudinal_v2: bool = False,
    release_version: str = "v6",
) -> Path:
    for relative, content in {
        "services/intelligence/agent_loop.py": b"agent-loop-fixture\n",
        "services/intelligence/guardrails.py": b"guardrails-fixture\n",
        "services/intelligence/runtime.py": (
            f"RESEARCH_EVALUATION_TEMPLATE = {RESEARCH_EVALUATION_TEMPLATE!r}\n"
        ).encode("utf-8"),
        "services/research/ml/stacker.py": b"stacker-fixture\n",
        "tools/ml_stack_research.py": b"driver-fixture\n",
        "quantos/contracts/model_risk.py": b"contracts-fixture\n",
        "services/governance/model_risk.py": b"decisions-fixture\n",
        "services/governance/model_risk_dataset.py": b"dataset-fixture\n",
        "services/governance/model_risk_history.py": b"history-fixture\n",
        "services/governance/model_risk_registry.py": b"registry-fixture\n",
        "scripts/generate_model_risk_dataset.py": b"dataset-generator-fixture\n",
        "scripts/generate_model_risk_evidence.py": b"generator-fixture\n",
        "scripts/update_model_risk_history.py": b"history-generator-fixture\n",
    }.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    snapshot = {
        "asset": "fx",
        "cost_bps": 5.0,
        "factors": ["momentum_120"],
        "feature_rows": 123882,
        "fwd_horizon": 5,
        "single_best_name": "momentum_120",
        "single_best_oos_sharpe": -2.0389,
        "stack_gbm_folds": 24,
        "stack_gbm_oos_sharpe": -1.1173,
        "stack_ridge_folds": 24,
        "stack_ridge_oos_sharpe": -0.4035,
        "trials": 8,
        "universe_size": 18,
        "verdict": "REFUTED_NO_EDGE",
    }
    snapshot_path = tmp_path / "data/cache/ml_stack_snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    dataset_path = tmp_path / "data/cache/fx.db"
    connection = sqlite3.connect(dataset_path)
    connection.execute(
        "CREATE TABLE bars (key TEXT NOT NULL, event_time INTEGER NOT NULL, "
        "knowledge_time INTEGER NOT NULL, payload_json TEXT NOT NULL, "
        "PRIMARY KEY (key, event_time, knowledge_time))"
    )
    connection.executemany(
        "INSERT INTO bars VALUES (?, ?, ?, ?)",
        [
            ("AUD", 1, 1, '{"close":1}'),
            ("AUD", 2, 2, '{"close":2}'),
            ("EUR", 1, 1, '{"close":3}'),
        ],
    )
    connection.commit()
    connection.close()
    verification = tmp_path / "tests/governance/test_model_risk_registry.py"
    verification.parent.mkdir(parents=True, exist_ok=True)
    verification.write_text("def test_model_risk(): pass\n", encoding="utf-8")
    bundle_path = tmp_path / "evidence/datasets/fx.jsonl.gz"
    manifest_path = tmp_path / "evidence/datasets/fx.manifest.json"
    if with_bundle:
        generate_bundle(
            dataset_path,
            bundle_path,
            manifest_path,
            keys=("AUD", "EUR"),
            repo_root=tmp_path,
        )
    evidence = build_model_risk_evidence(
        tmp_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        dataset_bundle_path=bundle_path,
        dataset_manifest_path=manifest_path,
        baseline_recorded_at=1,
        recorded_at=2,
        include_longitudinal_v2=longitudinal_v2,
        release_version=release_version,
    )
    path = tmp_path / "model-risk.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    return path


def _model_risk_history_for_current(tmp_path: Path, current: dict[str, object]) -> dict[str, object]:
    baseline = build_model_risk_evidence(
        tmp_path,
        snapshot_path=tmp_path / "data/cache/ml_stack_snapshot.json",
        dataset_path=tmp_path / "data/cache/fx.db",
        dataset_bundle_path=tmp_path / "evidence/datasets/fx.jsonl.gz",
        dataset_manifest_path=tmp_path / "evidence/datasets/fx.manifest.json",
        baseline_recorded_at=1,
        recorded_at=2,
        include_longitudinal_v2=False,
    )
    history, _ = append_model_risk_revision(None, baseline)
    history, _ = append_model_risk_revision(history, current)
    return history


def test_model_risk_checker_verifies_baseline_but_keeps_m3_open(tmp_path):
    evidence = _write_valid_model_risk_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "AI.MODEL.REGISTRY.REDTEAM",
                domain="ai_agents",
                target_maturity="M3",
                checker="model_risk_baseline_evidence",
                checker_args={
                    "path": evidence.name,
                    "pass_maturity": "M2",
                    "verification_paths": ["tests/governance/test_model_risk_registry.py"],
                },
            )
        ],
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "PARTIAL", result["detail"]
    assert result["current_maturity"] == "M2"
    assert "negative OOS" in result["detail"]

    document = json.loads(evidence.read_text(encoding="utf-8"))
    document["artifacts"][0]["payload"]["reviewer_group"] = "tampered"
    evidence.write_text(json.dumps(document), encoding="utf-8")
    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "artifact_hash" in result["detail"]


def test_committed_external_provider_stays_m2_without_passing_live_probe():
    report = assess_repository(ROOT, REGISTRY)
    results = {item["id"]: item for item in report["requirements"]}

    ai_runtime = results["AI.MODEL.REGISTRY.REDTEAM"]
    challenger_review = results["VALIDATION.CHALLENGER.MODEL_RISK"]
    assert ai_runtime["status"] == "PARTIAL", ai_runtime["detail"]
    assert ai_runtime["current_maturity"] == "M2"
    assert "evidence/external_model_provider_baseline.json" not in ai_runtime["evidence"]
    assert "no passing live HTTPS probe" in ai_runtime["detail"]
    assert challenger_review["status"] == "PARTIAL"
    assert challenger_review["current_maturity"] == "M2"
    assert "strong reviewer identity remains open" in challenger_review["detail"]


def test_model_risk_checker_promotes_ai_only_after_external_provider_verifier(
    tmp_path, monkeypatch
):
    evidence = _write_valid_model_risk_evidence(tmp_path)
    external = tmp_path / "external-provider.json"
    external.write_text("{}", encoding="utf-8")
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "AI.MODEL.REGISTRY.REDTEAM",
                domain="ai_agents",
                target_maturity="M3",
                checker="model_risk_baseline_evidence",
                checker_args={
                    "path": evidence.name,
                    "pass_maturity": "M2",
                    "external_provider_path": external.name,
                    "maturity_profile": "EXTERNAL_PROVIDER_RUNTIME_M3",
                },
            )
        ],
    )
    monkeypatch.setattr(
        "services.governance.top_tier_assessment.verify_external_model_provider_evidence",
        lambda document, repo_root: {
            "provider_id": "sushuo_gpt_5_6_sol",
            "current_maturity": "M3",
            "external_provider_exercised": True,
            "review_identity_assurance": "PROCESS_ROLE_ONLY",
            "evidence_paths": ["config/model_provider_registry.json"],
        },
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "PASS", result["detail"]
    assert result["current_maturity"] == "M3"
    assert "live HTTPS probe" in result["detail"]


def test_model_risk_checker_keeps_challenger_review_m2_after_provider_probe(
    tmp_path, monkeypatch
):
    evidence = _write_valid_model_risk_evidence(tmp_path)
    external = tmp_path / "external-provider.json"
    external.write_text("{}", encoding="utf-8")
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "VALIDATION.CHALLENGER.MODEL_RISK",
                domain="validation_model_risk",
                target_maturity="M3",
                checker="model_risk_baseline_evidence",
                checker_args={
                    "path": evidence.name,
                    "pass_maturity": "M2",
                    "external_provider_path": external.name,
                    "maturity_profile": "EXTERNAL_REVIEW_M3",
                },
            )
        ],
    )
    monkeypatch.setattr(
        "services.governance.top_tier_assessment.verify_external_model_provider_evidence",
        lambda document, repo_root: {
            "provider_id": "sushuo_gpt_5_6_sol",
            "current_maturity": "M3",
            "external_provider_exercised": True,
            "review_identity_assurance": "PROCESS_ROLE_ONLY",
            "evidence_paths": ["config/model_provider_registry.json"],
        },
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "PARTIAL", result["detail"]
    assert result["current_maturity"] == "M2"
    assert "strong external reviewer" in result["detail"]


def test_model_risk_checker_recomputes_implementation_hashes(tmp_path):
    evidence = _write_valid_model_risk_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "VALIDATION.CHALLENGER.MODEL_RISK",
                domain="validation_model_risk",
                target_maturity="M3",
                checker="model_risk_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M2"},
            )
        ],
    )
    (tmp_path / "services/research/ml/stacker.py").write_text("tampered\n", encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "implementation_digest" in result["detail"]


def test_model_risk_checker_rejects_runtime_history_tamper(tmp_path):
    evidence = _write_valid_model_risk_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "AI.MODEL.REGISTRY.REDTEAM",
                domain="ai_agents",
                target_maturity="M3",
                checker="model_risk_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M2"},
            )
        ],
    )
    document = json.loads(evidence.read_text(encoding="utf-8"))
    document["runtime_events"][0]["event_hash"] = "0" * 64
    evidence.write_text(json.dumps(document), encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "runtime_event_hash" in result["detail"]


def test_model_risk_checker_verifies_versioned_dataset_bundle(tmp_path):
    evidence = _write_valid_model_risk_evidence(tmp_path, with_bundle=True)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "VALIDATION.CHALLENGER.MODEL_RISK",
                domain="validation_model_risk",
                target_maturity="M3",
                checker="model_risk_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M2"},
            )
        ],
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "PARTIAL", result["detail"]
    assert "evidence/datasets/fx.jsonl.gz" in result["evidence"]
    assert "versioned PIT evaluation subset" in result["detail"]
    assert "raw-data retention" not in result["detail"]
    bundle = tmp_path / "evidence/datasets/fx.jsonl.gz"
    bundle.write_bytes(bundle.read_bytes() + b"tamper")
    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "dataset_bundle_verification" in result["detail"]


def test_model_risk_checker_accepts_retained_bundle_without_local_source_db(tmp_path):
    evidence = _write_valid_model_risk_evidence(tmp_path, with_bundle=True)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "VALIDATION.CHALLENGER.MODEL_RISK",
                domain="validation_model_risk",
                target_maturity="M3",
                checker="model_risk_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M2"},
            )
        ],
    )
    document = json.loads(evidence.read_text(encoding="utf-8"))
    document["source_dataset_ref"] = "data/cache/source-not-retained.db"
    evidence.write_text(json.dumps(document), encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "PARTIAL", result["detail"]
    assert "versioned PIT evaluation subset" in result["detail"]


def test_model_risk_checker_accepts_retained_bundle_after_source_db_advances(tmp_path):
    evidence = _write_valid_model_risk_evidence(tmp_path, with_bundle=True)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "VALIDATION.CHALLENGER.MODEL_RISK",
                domain="validation_model_risk",
                target_maturity="M3",
                checker="model_risk_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M2"},
            )
        ],
    )
    source = tmp_path / "data/cache/fx.db"
    source.write_bytes(source.read_bytes() + b"new-source-revision")

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "PARTIAL", result["detail"]
    assert "versioned PIT evaluation subset" in result["detail"]


def test_model_risk_checker_accepts_all_retained_release_lineage(tmp_path):
    evidence_path = _write_valid_model_risk_evidence(
        tmp_path,
        with_bundle=True,
        longitudinal_v2=True,
        release_version="v5",
    )
    previous = json.loads(evidence_path.read_text(encoding="utf-8"))
    previous_snapshot = build_model_risk_revision(previous)["snapshot"]
    (tmp_path / "services/intelligence/guardrails.py").write_text(
        "guardrails-v6-fixture\n", encoding="utf-8"
    )
    current = build_model_risk_evidence(
        tmp_path,
        snapshot_path=tmp_path / "data/cache/ml_stack_snapshot.json",
        dataset_path=tmp_path / "data/cache/fx.db",
        dataset_bundle_path=tmp_path / "evidence/datasets/fx.jsonl.gz",
        dataset_manifest_path=tmp_path / "evidence/datasets/fx.manifest.json",
        baseline_recorded_at=3,
        recorded_at=4,
        include_longitudinal_v2=True,
        prior_snapshot=previous_snapshot,
        release_version="v6",
    )
    evidence_path.write_text(json.dumps(current), encoding="utf-8")
    history, _ = append_model_risk_revision(None, previous)
    history, _ = append_model_risk_revision(history, current)
    history_path = tmp_path / "model-risk-history.json"
    history_path.write_text(json.dumps(history), encoding="utf-8")
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "VALIDATION.CHALLENGER.MODEL_RISK",
                domain="validation_model_risk",
                target_maturity="M3",
                checker="model_risk_baseline_evidence",
                checker_args={
                    "path": evidence_path.name,
                    "history_path": history_path.name,
                    "pass_maturity": "M2",
                },
            )
        ],
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "PARTIAL", result["detail"]
    assert "multi-revision history" in result["detail"]


def test_model_risk_checker_requires_current_revision_history_when_declared(tmp_path):
    evidence = _write_valid_model_risk_evidence(tmp_path, with_bundle=True)
    document = json.loads(evidence.read_text(encoding="utf-8"))
    history, _ = append_model_risk_revision(None, document)
    history_path = tmp_path / "model-risk-history.json"
    history_path.write_text(json.dumps(history), encoding="utf-8")
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "AI.MODEL.REGISTRY.REDTEAM",
                domain="ai_agents",
                target_maturity="M3",
                checker="model_risk_baseline_evidence",
                checker_args={
                    "path": evidence.name,
                    "history_path": history_path.name,
                    "pass_maturity": "M2",
                },
            )
        ],
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "PARTIAL", result["detail"]
    assert history_path.name in result["evidence"]
    assert "single-revision history" in result["detail"]

    history["revisions"][0]["entry_hash"] = "0" * 64
    history_path.write_text(json.dumps(history), encoding="utf-8")
    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "revision_history" in result["detail"]


def test_model_risk_checker_requires_real_multi_revision_history_for_v2(tmp_path):
    evidence = _write_valid_model_risk_evidence(
        tmp_path,
        with_bundle=True,
        longitudinal_v2=True,
    )
    document = json.loads(evidence.read_text(encoding="utf-8"))
    history = _model_risk_history_for_current(tmp_path, document)
    history_path = tmp_path / "model-risk-history.json"
    history_path.write_text(json.dumps(history), encoding="utf-8")
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "AI.MODEL.REGISTRY.REDTEAM",
                domain="ai_agents",
                target_maturity="M3",
                checker="model_risk_baseline_evidence",
                checker_args={
                    "path": evidence.name,
                    "history_path": history_path.name,
                    "pass_maturity": "M2",
                },
            )
        ],
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "PARTIAL", result["detail"]
    assert result["current_maturity"] == "M2"
    assert "multi-revision history" in result["detail"]

    single_revision, _ = append_model_risk_revision(None, document)
    history_path.write_text(json.dumps(single_revision), encoding="utf-8")
    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "revision_history_longitudinal" in result["detail"]


def test_model_risk_checker_requires_all_current_runtime_red_team_cases(tmp_path):
    evidence = _write_valid_model_risk_evidence(
        tmp_path,
        with_bundle=True,
        longitudinal_v2=True,
    )
    document = json.loads(evidence.read_text(encoding="utf-8"))
    active_report = next(
        record
        for record in document["artifacts"]
        if record["artifact_id"]
        == "red-team-report:research-agent-runtime-boundary-v7"
    )
    payload = active_report["payload"]
    payload["required_case_ids"] = [
        case_id for case_id in payload["required_case_ids"] if case_id != "runtime-sensitive-output"
    ]
    payload["cases"] = [case for case in payload["cases"] if case["case_id"] != "runtime-sensitive-output"]
    active_report["content_hash"] = stable_hash(payload)
    evidence.write_text(json.dumps(document), encoding="utf-8")

    history = _model_risk_history_for_current(tmp_path, document)
    history_path = tmp_path / "model-risk-history.json"
    history_path.write_text(json.dumps(history), encoding="utf-8")
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "AI.MODEL.REGISTRY.REDTEAM",
                domain="ai_agents",
                target_maturity="M3",
                checker="model_risk_baseline_evidence",
                checker_args={
                    "path": evidence.name,
                    "history_path": history_path.name,
                    "pass_maturity": "M2",
                },
            )
        ],
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "red_team_cases" in result["detail"]


def _write_valid_strong_identity_evidence(tmp_path: Path) -> Path:
    sources = {
        "quantos/contracts/identity.py": "identity contract\n",
        "services/governance/strong_identity.py": (
            "jwt.decode x5t#S256 client_cert_chain_verified required_amr_values "
            "QUANTOS_OIDC_KEY_REGISTRY_SHA256 QUANTOS_OIDC_KEY_REGISTRY_MIN_GENERATION "
            "select_oidc_verification_key\n"
        ),
        "services/governance/oidc_key_registry.py": (
            "OIDC_KEY_REACTIVATION_FORBIDDEN OIDC_KEY_REGISTRY_ROLLBACK "
            "OIDC_KEY_REVOKED load_pem_public_key\n"
        ),
        "services/governance/identity_access_review.py": (
            "ACCESS_REVIEW_SELF_REVIEW_FORBIDDEN MAX_ACCESS_REVIEW_VALIDITY_MS "
            "reviewer_certificate_sha256 stable_hash\n"
        ),
        "services/governance/identity_authorization.py": (
            "evaluate_authorization tokens_valid_after_ms "
            "access_review_expires_at_ms access_review_content_sha256 "
            "WORKLOAD_HUMAN_ACTION_FORBIDDEN "
            "TOKEN_ROLE_REQUIRED\n"
        ),
        "services/governance/identity_authorization_store.py": (
            "BEGIN IMMEDIATE append_registry_snapshot expected_registry_head identity_authorization_events\n"
        ),
        "services/governance/l2_identity_deployment.py": "L2 deployment contract\n",
        "config/identity_authorization_registry.json": (
            '{"schema_version":1,"readiness":"L2_DRY_RUN_ONLY","generation":0,"principals":{}}\n'
        ),
        "services/promotion_api/main.py": (
            "Authorization extract_verified_client_certificate PROMOTION_APPROVER "
            "PROMOTION_EXECUTOR evaluate_authorization "
            "IdentityAuthorizationStore\n"
        ),
        "services/promotion_api/trusted_mtls_proxy.py": "trusted Envoy UDS adapter\n",
        "apps/research-console/src/lib/api/promotion-api.ts": "Authorization Bearer\n",
        "apps/research-console/src/lib/api/governance-pkce.ts": (
            "code_challenge_method S256 getRandomValues subtle.digest response_type "
            "state nonce PKCE_TRANSACTION_STORAGE_KEY MAX_TRANSACTION_AGE_MS "
            "storage.removeItem response = await runtime.fetch access token memory only\n"
        ),
        "apps/research-console/src/workbench/CommandReviewPanel.tsx": "OIDC mTLS\n",
        "scripts/generate_strong_identity_evidence.py": "generator\n",
        "tests/governance/test_strong_identity.py": "tests\n",
        "tests/governance/test_oidc_key_registry.py": "tests\n",
        "tests/governance/test_identity_access_review.py": "tests\n",
        "tests/governance/test_identity_authorization.py": "tests\n",
        "tests/governance/test_identity_authorization_store.py": "tests\n",
        "tests/governance/test_l2_identity_deployment.py": "tests\n",
        "tests/governance/test_promotion_api_boundary.py": "tests\n",
        "tests/governance/test_promotion_api_endpoints.py": "tests\n",
        "tests/governance/test_trusted_mtls_proxy.py": "tests\n",
        "apps/research-console/src/lib/api/governance-pkce.test.ts": "tests\n",
    }
    for relative, content in sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("PyJWT==2.13.0\ncryptography==50.0.0\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        'dependencies = ["PyJWT==2.13.0", "cryptography==50.0.0"]\n',
        encoding="utf-8",
    )
    evidence = build_strong_identity_evidence(tmp_path, recorded_at=1)
    path = tmp_path / "strong-identity.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    return path


def test_strong_identity_checker_verifies_m3_but_keeps_m4_red_line_open(tmp_path):
    evidence = _write_valid_strong_identity_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "GOV.IDENTITY.STRONG_AUTH",
                domain="governance_security",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="strong_identity_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )

    report = assess_repository(tmp_path, registry)
    result = report["requirements"][0]
    assert result["status"] == "PARTIAL", result["detail"]
    assert result["current_maturity"] == "M3"
    assert report["summary"]["red_lines_failed"] == 1
    assert "external OIDC" in result["detail"]
    assert "PKCE S256" in result["detail"]

    document = json.loads(evidence.read_text(encoding="utf-8"))
    document["attack_cases"][0]["passed"] = False
    evidence.write_text(json.dumps(document), encoding="utf-8")
    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "attack_case" in result["detail"]


def test_strong_identity_checker_recomputes_load_bearing_source_hashes(tmp_path):
    evidence = _write_valid_strong_identity_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "GOV.IDENTITY.STRONG_AUTH",
                domain="governance_security",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="strong_identity_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )
    (tmp_path / "services/governance/strong_identity.py").write_text("tampered\n", encoding="utf-8")
    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "implementation_digest" in result["detail"]


def test_strong_identity_checker_rejects_pkce_invariant_removal_with_updated_hash(tmp_path):
    evidence = _write_valid_strong_identity_evidence(tmp_path)
    registry = _write_registry(
        tmp_path,
        [
            _requirement(
                "GOV.IDENTITY.STRONG_AUTH",
                domain="governance_security",
                tier="B",
                target_maturity="M4",
                red_line=True,
                checker="strong_identity_baseline_evidence",
                checker_args={"path": evidence.name, "pass_maturity": "M3"},
            )
        ],
    )
    pkce_path = tmp_path / "apps/research-console/src/lib/api/governance-pkce.ts"
    pkce_path.write_text(
        pkce_path.read_text(encoding="utf-8").replace("storage.removeItem", "storage.keepItem"),
        encoding="utf-8",
    )
    document = json.loads(evidence.read_text(encoding="utf-8"))
    document["implementation_sha256"]["apps/research-console/src/lib/api/governance-pkce.ts"] = hashlib.sha256(
        pkce_path.read_bytes()
    ).hexdigest()
    evidence.write_text(json.dumps(document), encoding="utf-8")

    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "FAILED"
    assert "pkce_one_time_transaction" in result["detail"]


def _alpha_trial_store_wiring_source(
    *,
    include_evaluation_context: bool,
    split_evaluation_connection: bool,
    split_evaluation_commit: bool,
    omit_registered_candidate_check: bool,
    omit_locked_helper_lease_guard: bool,
    bypass_locked_helper: bool,
) -> str:
    helper_connection = "self._other_conn" if split_evaluation_connection else "conn"
    helper_lease_guard = (
        ""
        if omit_locked_helper_lease_guard
        else "    self._require_write_lock(conn, lease)\n"
    )
    candidate_check = (
        ""
        if omit_registered_candidate_check
        else """
    candidate = conn.execute(
        "SELECT config_hash FROM search_candidates WHERE search_id=? AND config_hash=?"
    ).fetchone()
    if candidate is None:
        raise ExperimentStoreError("unregistered candidate")
"""
    )
    split_commit = "    conn.commit()\n" if split_evaluation_commit else ""
    wrapper_body = (
        """
        self._require_write_lock(conn, lease)
        candidate = conn.execute(
            "SELECT config_hash FROM search_candidates WHERE search_id=? AND config_hash=?"
        ).fetchone()
        if candidate is None:
            raise ExperimentStoreError("unregistered candidate")
        conn.execute("INSERT INTO evaluations")
        conn.execute("INSERT INTO search_evaluations")
        conn.execute("INSERT INTO evaluation_audit_outbox")
        return True
"""
        if bypass_locked_helper
        else f"""
        return self._insert_evaluation_locked(
            {helper_connection},
            lease,
            write,
            search_id=search_id,
            config_hash=config_hash,
        )
"""
    )
    save_evaluation_context = (
        """

def save_evaluation_catalog_snapshot(self):
    pass

def evaluation_context_snapshot(self):
    self._conn.execute("BEGIN")
    try:
        trials = self.all_trial_hashes()
        alphas = self.all_alphas()
        self._conn.commit()
        return trials, alphas
    except Exception:
        self._conn.rollback()
        raise
"""
        if include_evaluation_context
        else ""
    )
    return f"""
TRIAL_INSERT = "INSERT OR IGNORE INTO trials(config_hash, first_seen_ts) VALUES (?, ?)"
IMMUTABLE_COLLISION = "ImmutableArtifactError"
EVALUATION_DIGEST = "content_hash"
SEARCH_EVALUATION_SCHEMA = "CREATE TABLE IF NOT EXISTS search_evaluations UNIQUE(search_id, run_id)"

class _WriteLease:
    def __init__(self, *, connection, generation, owner_thread):
        self.connection = connection
        self.generation = generation
        self.owner_thread = owner_thread

def _search_evaluation_hash(*, config_hash):
    return {{"config_hash": config_hash}}

@contextmanager
def _immediate_transaction(self):
    self._reject_write_ownership_conflict()
    if self._active_write_lease is not None or self._conn.in_transaction:
        raise ExperimentStoreError("nested store-owned transaction")
    self._conn.execute("BEGIN IMMEDIATE")
    lease = _WriteLease(
        connection=self._conn,
        generation=self._write_generation,
        owner_thread=get_ident(),
    )
    self._active_write_lease = lease
    try:
        yield self._conn, lease
        self._conn.commit()
    except BaseException:
        if self._conn.in_transaction:
            self._conn.rollback()
        raise
    finally:
        self._active_write_lease = None

def _require_write_lock(self, conn, lease):
    if not isinstance(lease, _WriteLease):
        raise ExperimentStoreError("forged write lease")
    if lease.owner_thread != get_ident():
        raise ExperimentStoreError("wrong write-lease thread")
    if (
        conn is not self._conn
        or lease is not self._active_write_lease
        or lease.connection is not conn
        or lease.generation != self._write_generation
        or not conn.in_transaction
    ):
        raise ExperimentStoreError("invalid write lease")

def register_search_candidates(self):
    with self._immediate_transaction() as (conn, lease):
        self._require_write_lock(conn, lease)
        conn.execute("INSERT INTO search_candidates")
        conn.execute("INSERT OR IGNORE INTO trials")

def insert_evaluation(self):
    write = EvaluationArtifactWrite()
    search_id = "search-1"
    config_hash = "1" * 64
    with self._immediate_transaction() as (conn, lease):
{wrapper_body}

def _insert_evaluation_locked(
    self,
    conn,
    lease,
    write,
    search_id=None,
    config_hash=None,
):
{helper_lease_guard}{candidate_check}    conn.execute("INSERT INTO evaluations")
{split_commit}    conn.execute("INSERT INTO search_evaluations")
    conn.execute("INSERT INTO evaluation_audit_outbox")
    return True
{save_evaluation_context}
"""


def _write_alpha_trial_wiring(
    tmp_path: Path,
    *,
    omit_session_save: bool = False,
    omit_factory_recorder: bool = False,
    omit_factory_tool_identity: bool = False,
    factory_helper_create: bool = False,
    omit_factory_helper_guard: bool = False,
    legacy_evaluation_api: bool = False,
    omit_exact_evaluation_binding: bool = False,
    split_evaluation_connection: bool = False,
    split_evaluation_commit: bool = False,
    omit_registered_candidate_check: bool = False,
    omit_locked_helper_lease_guard: bool = False,
    bypass_locked_helper: bool = False,
) -> dict:
    factory_tools = [
        "tools/crypto_factory_intraday.py",
        "tools/crypto_factory_loop.py",
        "tools/crypto_factory_orthogonal.py",
        "tools/crypto_factory_overfit_ab.py",
        "tools/crypto_factory_run.py",
    ]
    load_evaluation = "load_into" if legacy_evaluation_api else "load_for_evaluation"
    save_evaluation = "save" if legacy_evaluation_api else "save_evaluation_context"
    session_save_call = (
        ""
        if omit_session_save
        else f"registry.{save_evaluation}(catalog, now=timestamp)"
    )
    helper_guard = (
        ""
        if omit_factory_helper_guard
        else "if not os.path.isfile(PRODUCTION_EXPERIMENT_DB):\n"
        "        raise ExperimentStoreError('missing')"
    )
    registry_evaluation_methods = (
        """
    def save(self, catalog):
        self._store.save_catalog_snapshot(config_hashes=())

    def load_into(self, catalog):
        self._store.all_trial_hashes()
"""
        if legacy_evaluation_api
        else """
    def save_evaluation_context(self, catalog):
        self._store.save_evaluation_catalog_snapshot(config_hashes=())

    def load_for_evaluation(self, catalog):
        self._store.evaluation_context_snapshot()
        catalog.restore_evaluation_context()
"""
    )
    port_evaluation_method = (
        ""
        if legacy_evaluation_api
        else """
    def save_evaluation_catalog_snapshot(self): ...
"""
    )
    exact_search_argument = (
        ""
        if omit_exact_evaluation_binding
        else ", search_id='search-1', config_hash='1111111111111111111111111111111111111111111111111111111111111111'"
    )
    sources = {
        "services/intelligence/research_session.py": """
def run_research_session(*args, **kwargs):
    return _run_research_session_core(*args, **kwargs)
""",
        "services/research/session.py": f"""
def run_research_session(registry=None):
    registry.{load_evaluation}(catalog)
    registry.record_evaluation(evaluation, dataset_version=dataset_version, recorded_ts=timestamp)
    {session_save_call}

def _record_report(registry):
    registry.record_evaluation(evaluation, dataset_version=dataset_version, recorded_ts=timestamp)

def _run_evolutionary(registry):
    registry.{save_evaluation}(catalog, now=timestamp)

def _run_mcts(registry):
    registry.{save_evaluation}(catalog, now=timestamp)
""",
        "services/research/experiment_registry/registry.py": f"""
class ExperimentRegistry:
    def begin_search_attempt(self):
        return self._store.begin_search_attempt()

    def register_search_candidates(self):
        return self._store.register_search_candidates()

    def finish_search_attempt(self):
        return self._store.finish_search_attempt()

    def search_evaluations(self):
        return self._store.search_evaluations()

    def search_history_valid(self):
        return self._store.search_history_valid()

    {registry_evaluation_methods}

    def record_evaluation(self, evaluation):
        self._store.insert_evaluation(run_id='run'{exact_search_argument})

    def verify_evaluation(self, run_id):
        return stable_hash(self._store.get_evaluation(run_id))
""",
        "services/research/experiment_registry/store.py": _alpha_trial_store_wiring_source(
            include_evaluation_context=not legacy_evaluation_api,
            split_evaluation_connection=split_evaluation_connection,
            split_evaluation_commit=split_evaluation_commit,
            omit_registered_candidate_check=omit_registered_candidate_check,
            omit_locked_helper_lease_guard=omit_locked_helper_lease_guard,
            bypass_locked_helper=bypass_locked_helper,
        ),
        "services/research/ports/persistence.py": f"""
class ExperimentStorePort(Protocol):
    def save_catalog_snapshot(self): ...
{port_evaluation_method}
""",
        "services/research/alpha_factory.py": f"""
def run_alpha_factory(search_registry):
    search_registry.{load_evaluation}(catalog)
    search_registry.begin_search_attempt()
    result = _run_alpha_factory_impl(candidate_recorder=persist_candidates)
    search_registry.{save_evaluation}(catalog)
    search_registry.finish_search_attempt()
    return result

def _run_alpha_factory_impl(candidate_recorder=None):
    {'candidate_recorder(config_hashes)' if not omit_factory_recorder else ''}
    direct = _score_expr(candidate)
    parallel = executor.map(_score_expr_in_worker, candidates)
    return direct, parallel
""",
        "tools/search_trial_registry.py": f"""
PRODUCTION_EXPERIMENT_DB = 'data/cache/experiment_registry.db'

def make_search_id():
    return 'search-1'

@contextmanager
def production_experiment_registry():
    {helper_guard}
    store = SqliteExperimentStore(PRODUCTION_EXPERIMENT_DB, create={factory_helper_create})
    try:
        store.trial_count()
        yield ExperimentRegistry(store)
    finally:
        store.close()
""",
        "tests/research/test_session.py": "session persistence coverage\n",
        "tests/research/test_experiment_registry.py": "ledger integrity coverage\n",
        "tests/research/test_experiment_store.py": "evaluation definition conflict coverage\n",
        "tests/research/test_experiment_store_locked_primitives.py": "lease and atomicity coverage\n",
        "tests/research/test_search_trial_provenance.py": "factory provenance coverage\n",
        "tests/intelligence/test_registered_candidate_batch.py": "lifecycle preservation coverage\n",
    }
    for index, relative in enumerate(factory_tools):
        search_id_keyword = (
            "" if omit_factory_tool_identity and index == 0
            else "search_id=make_search_id(),"
        )
        sources[relative] = f"""
from tools.search_trial_registry import make_search_id, production_experiment_registry

def main():
    with production_experiment_registry() as registry:
        return run_alpha_factory(
            panel,
            search_registry=registry,
            {search_id_keyword}
            search_kind='ALPHA_FACTORY',
            search_source='{relative}',
            dataset_version='dataset-v1',
            timestamp=1,
        )
"""
    for relative, content in sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return {
        "evidence_path": "evidence/alpha_trial_ledger_history.json",
        "ledger_path": "data/cache/experiment_registry.db",
        "snapshot_path": "evidence/alpha_trial_ledger_snapshot.db",
        "seal_path": "evidence/alpha_trial_ledger_snapshot_seal.json",
        "entrypoint_path": "services/intelligence/research_session.py",
        "session_path": "services/research/session.py",
        "factory_path": "services/research/alpha_factory.py",
        "factory_registry_helper_path": "tools/search_trial_registry.py",
        "registry_path": "services/research/experiment_registry/registry.py",
        "persistence_port_path": "services/research/ports/persistence.py",
        "store_path": "services/research/experiment_registry/store.py",
        "official_factory_paths": factory_tools,
        "verification_paths": [
            "tests/research/test_session.py",
            "tests/research/test_experiment_registry.py",
            "tests/research/test_experiment_store.py",
            "tests/research/test_experiment_store_locked_primitives.py",
            "tests/research/test_search_trial_provenance.py",
            "tests/intelligence/test_registered_candidate_batch.py",
        ],
        "pass_maturity": "M4",
    }


def _alpha_trial_requirement(checker_args: dict) -> dict:
    return _requirement(
        "ALPHA.TRIAL.GLOBAL_LEDGER",
        domain="alpha_research",
        target_maturity="M4",
        red_line=True,
        checker="alpha_trial_ledger_evidence",
        checker_args=checker_args,
    )


def _write_valid_alpha_trial_history(tmp_path: Path, checker_args: dict) -> Path:
    ledger_path = tmp_path / checker_args["ledger_path"]
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    SqliteExperimentStore(str(ledger_path), create=True).close()
    evidence_path = tmp_path / checker_args["evidence_path"]
    for index, timestamp in ((1, 10), (2, 200)):
        dataset_version = f"dataset-v{index}"
        evaluation = {
            "alpha_id": f"alpha-{index}",
            "catalog_trial_count": index,
            "reproducibility_hash": str(index) * 64,
            "verdict": "REQUIRES_HUMAN_REVIEW",
        }
        content_hash = stable_hash(
            {"evaluation": evaluation, "dataset_version": dataset_version}
        )
        run_id = short_hash(
            {
                "alpha_id": evaluation["alpha_id"],
                "repro": evaluation["reproducibility_hash"],
                "dataset_version": dataset_version,
            }
        )
        search_id = f"session-{index}"
        store = SqliteExperimentStore(str(ledger_path), create=False)
        try:
            store.begin_search_attempt(
                search_id=search_id,
                search_kind="TEST_SELECTION_SEARCH",
                source="tests.governance.test_top_tier_assessment",
                dataset_version=dataset_version,
                recorded_at=timestamp,
            )
            store.register_search_candidates(
                search_id,
                [str(index) * 64],
                registered_at=timestamp,
            )
            store.insert_evaluation(
                run_id=run_id,
                alpha_id=f"alpha-{index}",
                dataset_version=dataset_version,
                recorded_ts=timestamp,
                verdict="REQUIRES_HUMAN_REVIEW",
                content_hash=content_hash,
                evaluation_json=json.dumps(evaluation, sort_keys=True),
                search_id=search_id,
                config_hash=str(index) * 64,
            )
            store.finish_search_attempt(
                search_id,
                status="COMPLETED",
                recorded_at=timestamp,
            )
        finally:
            store.close()
        search_trial_ledger_history.capture_history(
            session_id=search_id,
            ledger_path=ledger_path,
            history_path=evidence_path,
            ledger_path_label=checker_args["ledger_path"],
            recorded_at_ms=100 if index == 1 else 300,
        )
    search_trial_ledger_history.seal_snapshot(
        ledger_path=ledger_path,
        history_path=evidence_path,
        snapshot_path=tmp_path / checker_args["snapshot_path"],
        seal_path=tmp_path / checker_args["seal_path"],
        ledger_path_label=checker_args["ledger_path"],
        history_path_label=checker_args["evidence_path"],
        snapshot_path_label=checker_args["snapshot_path"],
        sealed_at_ms=400,
    )
    return evidence_path


_ALPHA_TRIAL_PORTABLE_TRIGGERS = [
    (f"{table}_no_{operation.lower()}", table, operation)
    for table in (
        "trials",
        "evaluations",
        "search_attempt_events",
        "search_candidates",
        "search_evaluations",
    )
    for operation in ("UPDATE", "DELETE")
]


def _refresh_alpha_trial_snapshot_hash(tmp_path: Path, checker_args: dict) -> None:
    snapshot_path = tmp_path / checker_args["snapshot_path"]
    seal_path = tmp_path / checker_args["seal_path"]
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    seal["snapshot_sha256"] = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    seal_path.write_text(json.dumps(seal), encoding="utf-8")


def test_alpha_trial_ledger_checker_missing_history_is_m3_partial(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "PARTIAL"
    assert result["current_maturity"] == "M3"
    assert "history" in result["detail"]


def test_alpha_trial_ledger_checker_rejects_present_invalid_history_without_ledger(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    evidence_path = tmp_path / checker_args["evidence_path"]
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text("{}", encoding="utf-8")
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "history contract" in result["detail"]


def test_alpha_trial_ledger_checker_requires_persistent_session_save(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path, omit_session_save=True)
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "run_research_session" in result["detail"]


def test_alpha_trial_ledger_checker_rejects_legacy_only_evaluation_persistence_api(
    tmp_path,
):
    checker_args = _write_alpha_trial_wiring(
        tmp_path,
        legacy_evaluation_api=True,
    )
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert result["current_maturity"] == "M2"
    assert "load_for_evaluation" in result["detail"]
    assert "save_evaluation_context" in result["detail"]
    assert "save_evaluation_catalog_snapshot" in result["detail"]


def test_alpha_trial_ledger_checker_requires_transactional_evaluation_snapshot(
    tmp_path,
):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    registry_path = tmp_path / checker_args["registry_path"]
    registry_path.write_text(
        registry_path.read_text(encoding="utf-8").replace(
            "self._store.evaluation_context_snapshot()",
            "self._store.all_trial_hashes()\n        self._store.all_alphas()",
        ),
        encoding="utf-8",
    )
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "evaluation_context_snapshot" in result["detail"]


def test_alpha_trial_ledger_checker_requires_exact_search_evaluation_binding(
    tmp_path,
):
    checker_args = _write_alpha_trial_wiring(
        tmp_path,
        omit_exact_evaluation_binding=True,
    )
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "exact_search_candidate_binding" in result["detail"]


def test_alpha_trial_ledger_checker_accepts_owned_transaction_locked_helper_graph(
    tmp_path,
):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "PARTIAL", result["detail"]
    assert result["current_maturity"] == "M3"


def test_alpha_trial_ledger_checker_rejects_split_evaluation_connection(tmp_path):
    checker_args = _write_alpha_trial_wiring(
        tmp_path,
        split_evaluation_connection=True,
    )
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "same_connection_and_lease" in result["detail"]


def test_alpha_trial_ledger_checker_rejects_split_evaluation_commit(tmp_path):
    checker_args = _write_alpha_trial_wiring(
        tmp_path,
        split_evaluation_commit=True,
    )
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "split_commit" in result["detail"]


def test_alpha_trial_ledger_checker_rejects_unregistered_candidate_path(tmp_path):
    checker_args = _write_alpha_trial_wiring(
        tmp_path,
        omit_registered_candidate_check=True,
    )
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "registered_candidate_binding" in result["detail"]


def test_alpha_trial_ledger_checker_rejects_helper_without_valid_lease_guard(
    tmp_path,
):
    checker_args = _write_alpha_trial_wiring(
        tmp_path,
        omit_locked_helper_lease_guard=True,
    )
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "valid_lease_guard" in result["detail"]


def test_alpha_trial_ledger_checker_rejects_wrapper_bypassing_locked_helper(
    tmp_path,
):
    checker_args = _write_alpha_trial_wiring(
        tmp_path,
        bypass_locked_helper=True,
    )
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "locked_helper_delegation" in result["detail"]


def test_alpha_trial_ledger_checker_requires_candidate_recorder_before_score(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path, omit_factory_recorder=True)
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "candidate_recorder" in result["detail"]


def test_alpha_trial_ledger_checker_requires_factory_tool_search_identity(tmp_path):
    checker_args = _write_alpha_trial_wiring(
        tmp_path, omit_factory_tool_identity=True
    )
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "factory tool coverage incomplete" in result["detail"]


def test_alpha_trial_ledger_checker_requires_fail_closed_factory_registry_helper(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path, factory_helper_create=True)
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "factory registry helper" in result["detail"]


def test_alpha_trial_ledger_checker_requires_missing_file_guard_before_sqlite(tmp_path):
    checker_args = _write_alpha_trial_wiring(
        tmp_path, omit_factory_helper_guard=True
    )
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "missing_file_guard_before_connect" in result["detail"]


def test_alpha_trial_ledger_checker_passes_from_portable_bundle_without_live_store(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    (tmp_path / checker_args["ledger_path"]).unlink()
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "PASS", result["detail"]
    assert result["current_maturity"] == "M4"
    assert checker_args["snapshot_path"] in result["evidence"]
    assert checker_args["seal_path"] in result["evidence"]


@pytest.mark.parametrize("missing_key", ["snapshot_path", "seal_path"])
def test_alpha_trial_ledger_checker_is_partial_without_portable_bundle_file(
    tmp_path,
    missing_key,
):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    (tmp_path / checker_args[missing_key]).unlink()
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "PARTIAL"
    assert result["current_maturity"] == "M3"
    assert "portable snapshot evidence is unavailable" in result["detail"]


def test_alpha_trial_ledger_checker_rejects_bad_portable_seal_hash(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    seal_path = tmp_path / checker_args["seal_path"]
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    seal["snapshot_sha256"] = "0" * 64
    seal_path.write_text(json.dumps(seal), encoding="utf-8")
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert result["current_maturity"] == "M2"
    assert "portable seal failed" in result["detail"]


@pytest.mark.parametrize(
    "mutation,detail",
    [
        ("extra_table", "ledger_tables"),
        ("missing_trigger", "evaluations_no_delete_trigger"),
    ],
)
def test_alpha_trial_ledger_checker_rejects_nonminimal_or_mutable_snapshot(
    tmp_path,
    mutation,
    detail,
):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    snapshot_path = tmp_path / checker_args["snapshot_path"]
    connection = sqlite3.connect(snapshot_path)
    if mutation == "extra_table":
        connection.execute("CREATE TABLE forbidden_runtime_state(value TEXT)")
    else:
        connection.execute("DROP TRIGGER evaluations_no_delete")
    connection.commit()
    connection.close()
    seal_path = tmp_path / checker_args["seal_path"]
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    seal["snapshot_sha256"] = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    seal_path.write_text(json.dumps(seal), encoding="utf-8")
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert result["current_maturity"] == "M2"
    assert detail in result["detail"]


@pytest.mark.parametrize(
    "trigger_name,table,operation",
    _ALPHA_TRIAL_PORTABLE_TRIGGERS,
)
@pytest.mark.parametrize(
    "mutation",
    [
        "no_op_body",
        "raise_ignore",
        "raise_fail",
        "wrong_name",
        "wrong_target",
        "extra_trigger",
    ],
)
def test_alpha_trial_ledger_checker_rejects_noncanonical_append_only_trigger(
    tmp_path,
    trigger_name,
    table,
    operation,
    mutation,
):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    snapshot_path = tmp_path / checker_args["snapshot_path"]
    connection = sqlite3.connect(snapshot_path)
    if mutation == "extra_trigger":
        connection.execute(
            f"CREATE TRIGGER unexpected_{trigger_name} BEFORE {operation} ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
        )
    else:
        connection.execute(f"DROP TRIGGER {trigger_name}")
        actual_name = (
            f"{trigger_name}_renamed" if mutation == "wrong_name" else trigger_name
        )
        actual_table = (
            ("evaluations" if table != "evaluations" else "trials")
            if mutation == "wrong_target"
            else table
        )
        body = {
            "no_op_body": "SELECT 1;",
            "raise_ignore": "SELECT RAISE(IGNORE);",
            "raise_fail": f"SELECT RAISE(FAIL, '{table} is append-only');",
            "wrong_name": f"SELECT RAISE(ABORT, '{table} is append-only');",
            "wrong_target": f"SELECT RAISE(ABORT, '{table} is append-only');",
        }[mutation]
        connection.execute(
            f"CREATE TRIGGER {actual_name} BEFORE {operation} ON {actual_table} "
            f"BEGIN {body} END"
        )
    connection.commit()
    connection.close()
    _refresh_alpha_trial_snapshot_hash(tmp_path, checker_args)
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED", (trigger_name, mutation, result["detail"])
    assert result["current_maturity"] == "M2"
    assert "trigger" in result["detail"]


def test_alpha_trial_ledger_checker_rejects_unknown_portable_schema_version(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    snapshot_path = tmp_path / checker_args["snapshot_path"]
    connection = sqlite3.connect(snapshot_path)
    connection.execute("PRAGMA user_version=3")
    connection.close()
    _refresh_alpha_trial_snapshot_hash(tmp_path, checker_args)
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert result["current_maturity"] == "M2"
    assert "ledger_schema_version" in result["detail"]


def test_alpha_trial_ledger_checker_fails_closed_when_digest_read_fails(
    tmp_path,
    monkeypatch,
):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    def fail_digest_read(_path):
        raise OSError("portable digest unavailable")

    monkeypatch.setattr(
        "services.governance.top_tier_assessment._sha256_file",
        fail_digest_read,
    )

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert result["current_maturity"] == "M2"
    assert result["detail"].startswith("alpha trial-ledger portable seal failed:")


def test_alpha_trial_ledger_checker_rejects_history_that_does_not_match_live_store(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    evidence_path = _write_valid_alpha_trial_history(tmp_path, checker_args)
    document = json.loads(evidence_path.read_text(encoding="utf-8"))
    final = document["records"][-1]
    final_session_run_id = final["session_provenance"]["evaluation_links"][0][
        "run_id"
    ]
    next(
        evaluation
        for evaluation in final["evaluations"]
        if evaluation["run_id"] == final_session_run_id
    )["content_hash"] = "f" * 64
    final_without_hash = {key: value for key, value in final.items() if key != "record_hash"}
    final["record_hash"] = hashlib.sha256(
        json.dumps(final_without_hash, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    evidence_path.write_text(json.dumps(document), encoding="utf-8")
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "session_evaluation_snapshot" in result["detail"]


def test_alpha_trial_ledger_checker_rejects_self_reported_session_label(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    evidence_path = _write_valid_alpha_trial_history(tmp_path, checker_args)
    document = json.loads(evidence_path.read_text(encoding="utf-8"))
    final = document["records"][-1]
    final["session_id"] = "operator-invented-session"
    final["record_hash"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in final.items() if key != "record_hash"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    evidence_path.write_text(json.dumps(document), encoding="utf-8")
    seal_path = tmp_path / checker_args["seal_path"]
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    seal["history_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    seal["history_head_hash"] = final["record_hash"]
    seal_path.write_text(json.dumps(seal), encoding="utf-8")
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "history_session:operator-invented-session" in result["detail"]


def test_alpha_trial_ledger_checker_rejects_cross_session_candidate_link(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    evidence_path = _write_valid_alpha_trial_history(tmp_path, checker_args)
    document = json.loads(evidence_path.read_text(encoding="utf-8"))
    final = document["records"][-1]
    final["session_provenance"]["evaluation_links"][0]["config_hash"] = "1" * 64
    final["record_hash"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in final.items() if key != "record_hash"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    evidence_path.write_text(json.dumps(document), encoding="utf-8")
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "record_2_session_evaluation_link" in result["detail"]


def test_alpha_trial_ledger_checker_requires_database_append_only_guards(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    snapshot_path = tmp_path / checker_args["snapshot_path"]
    connection = sqlite3.connect(snapshot_path)
    connection.execute("DROP TRIGGER evaluations_no_delete")
    connection.commit()
    connection.close()
    seal_path = tmp_path / checker_args["seal_path"]
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    seal["snapshot_sha256"] = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    seal_path.write_text(json.dumps(seal), encoding="utf-8")
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "evaluations_no_delete_trigger" in result["detail"]


def test_alpha_trial_ledger_checker_requires_exact_link_append_only_guard(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    snapshot_path = tmp_path / checker_args["snapshot_path"]
    connection = sqlite3.connect(snapshot_path)
    connection.execute("DROP TRIGGER search_evaluations_no_delete")
    connection.commit()
    connection.close()
    seal_path = tmp_path / checker_args["seal_path"]
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    seal["snapshot_sha256"] = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    seal_path.write_text(json.dumps(seal), encoding="utf-8")
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert "search_evaluations_no_delete_trigger" in result["detail"]


def test_committed_alpha_trial_requirement_uses_semantic_checker():
    requirement = next(
        item for item in load_requirements(REGISTRY)
        if item["id"] == "ALPHA.TRIAL.GLOBAL_LEDGER"
    )

    assert requirement["checker"] == "alpha_trial_ledger_evidence"
    assert requirement["checker_args"]["evidence_path"] == (
        "evidence/alpha_trial_ledger_history.json"
    )
    assert requirement["checker_args"]["snapshot_path"] == (
        "evidence/alpha_trial_ledger_snapshot.db"
    )
    assert requirement["checker_args"]["seal_path"] == (
        "evidence/alpha_trial_ledger_snapshot_seal.json"
    )
    assert requirement["checker_args"]["persistence_port_path"] == (
        "services/research/ports/persistence.py"
    )
    assert {
        "tests/research/test_experiment_store.py",
        "tests/intelligence/test_registered_candidate_batch.py",
    } <= set(requirement["checker_args"]["verification_paths"])


def test_committed_alpha_trial_requirement_passes_with_portable_snapshot(tmp_path):
    requirement = next(
        item for item in load_requirements(REGISTRY)
        if item["id"] == "ALPHA.TRIAL.GLOBAL_LEDGER"
    )
    registry = _write_registry(tmp_path, [requirement])

    result = assess_repository(ROOT, registry)["requirements"][0]

    assert result["status"] == "PASS", result["detail"]
    assert result["current_maturity"] == "M4"
    assert requirement["checker_args"]["snapshot_path"] in result["evidence"]
    assert requirement["checker_args"]["seal_path"] in result["evidence"]


def test_committed_alpha_trial_snapshot_has_minimal_table_surface():
    snapshot = ROOT / "evidence/alpha_trial_ledger_snapshot.db"
    connection = sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()
    assert tables == {
        "sqlite_sequence",
        "trials",
        "evaluations",
        "search_attempt_events",
        "search_candidates",
        "search_evaluations",
    }


def test_canonical_refresh_check_supports_new_run_and_standalone_reuse(tmp_path, monkeypatch):
    from scripts.assess_top_tier import main
    import subprocess

    canonical_source = resolve_canonical_source_anchor(ROOT)
    json_out = tmp_path / "assessment.json"
    matrix_out = tmp_path / "assessment.md"
    args = ["--repo-root", str(ROOT), "--json-out", str(json_out), "--matrix-out", str(matrix_out), "--as-of-ms", "123"]
    monkeypatch.setenv("QUANTOS_VALIDATION_SOURCE_COMMIT", canonical_source)
    for run in ("a" * 64, "b" * 64):
        monkeypatch.setenv("QUANTOS_VALIDATION_RUN_ID", run)
        assert main(args) == 0
        assert main([*args, "--check"]) == 0
    monkeypatch.setenv("QUANTOS_VALIDATION_RUN_ID", "a" * 64)
    assert main([*args, "--check"]) == 1
    monkeypatch.delenv("QUANTOS_VALIDATION_RUN_ID")
    monkeypatch.delenv("QUANTOS_VALIDATION_SOURCE_COMMIT")
    assert main([*args, "--check"]) == 0


def test_top_tier_refresh_reuses_current_canonical_time(tmp_path, monkeypatch):
    from scripts.assess_top_tier import main

    canonical_source = "a" * 40
    monkeypatch.setattr(
        "scripts.assess_top_tier.resolve_canonical_source_anchor",
        lambda _root: canonical_source,
    )
    monkeypatch.setenv("QUANTOS_VALIDATION_RUN_ID", "a" * 64)
    monkeypatch.setenv("QUANTOS_VALIDATION_SOURCE_COMMIT", canonical_source)
    monkeypatch.setattr(
        "scripts.assess_top_tier.assert_current_binding", lambda *args, **kwargs: None
    )
    json_out = tmp_path / "assessment.json"
    matrix_out = tmp_path / "assessment.md"
    args = [
        "--repo-root",
        str(ROOT),
        "--json-out",
        str(json_out),
        "--matrix-out",
        str(matrix_out),
    ]

    assert main([*args, "--as-of-ms", "123"]) == 0
    first = json.loads(json_out.read_text(encoding="utf-8"))
    assert main(args) == 0
    second = json.loads(json_out.read_text(encoding="utf-8"))

    assert first["validation"]["generated_at"] == 123
    assert second["validation"] == first["validation"]

    assert main([*args, "--as-of-ms", "456"]) == 0
    overridden = json.loads(json_out.read_text(encoding="utf-8"))
    assert overridden["validation"]["generated_at"] == 456


def test_top_tier_check_reuses_verified_binding_with_different_explicit_time(
    tmp_path, monkeypatch
):
    from scripts.assess_top_tier import main

    canonical_source = "a" * 40
    monkeypatch.setattr(
        "scripts.assess_top_tier.resolve_canonical_source_anchor",
        lambda _root: canonical_source,
    )
    monkeypatch.setenv("QUANTOS_VALIDATION_RUN_ID", "a" * 64)
    monkeypatch.setenv("QUANTOS_VALIDATION_SOURCE_COMMIT", canonical_source)
    monkeypatch.setattr(
        "scripts.assess_top_tier.assert_current_binding", lambda *args, **kwargs: None
    )
    json_out = tmp_path / "assessment.json"
    matrix_out = tmp_path / "assessment.md"
    args = [
        "--repo-root",
        str(ROOT),
        "--json-out",
        str(json_out),
        "--matrix-out",
        str(matrix_out),
    ]

    assert main([*args, "--as-of-ms", "123"]) == 0
    before_bytes = json_out.read_bytes()
    before = json.loads(before_bytes)
    assessment = {key: value for key, value in before.items() if key != "validation"}
    monkeypatch.setattr(
        "scripts.assess_top_tier.assess_repository",
        lambda *args, **kwargs: json.loads(json.dumps(assessment)),
    )

    def unexpected_new_binding(*args, **kwargs):
        raise AssertionError("check mode regenerated validation binding")

    monkeypatch.setattr(
        "scripts.assess_top_tier.validation_binding", unexpected_new_binding
    )

    assert main([*args, "--as-of-ms", "456", "--check"]) == 0
    assert json_out.read_bytes() == before_bytes
    assert json.loads(json_out.read_text(encoding="utf-8"))["validation"] == before[
        "validation"
    ]


def test_top_tier_generator_standalone_check_validates_binding(tmp_path, monkeypatch):
    from scripts.assess_top_tier import main
    import subprocess

    canonical_source = resolve_canonical_source_anchor(ROOT)
    monkeypatch.setenv("QUANTOS_VALIDATION_RUN_ID", "a" * 64)
    monkeypatch.setenv("QUANTOS_VALIDATION_SOURCE_COMMIT", canonical_source)
    json_out = tmp_path / "assessment.json"
    matrix_out = tmp_path / "assessment.md"
    args = ["--repo-root", str(ROOT), "--json-out", str(json_out), "--matrix-out", str(matrix_out), "--as-of-ms", "123"]
    assert main(args) == 0
    assert main([*args, "--check"]) == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    payload["validation"]["current"] = False
    json_out.write_text(json.dumps(payload), encoding="utf-8")
    assert main([*args, "--check"]) == 1


def test_top_tier_generator_rejects_environment_source_not_stable_anchor(
    tmp_path, monkeypatch
):
    from scripts.assess_top_tier import main
    import subprocess

    canonical_source = resolve_canonical_source_anchor(ROOT)
    assert len(canonical_source) == 40
    monkeypatch.setenv("QUANTOS_VALIDATION_SOURCE_COMMIT", "d" * 40)
    monkeypatch.setenv("QUANTOS_VALIDATION_RUN_ID", "c" * 64)
    args = [
        "--repo-root", str(ROOT),
        "--json-out", str(tmp_path / "assessment.json"),
        "--matrix-out", str(tmp_path / "assessment.md"),
        "--as-of-ms", "123",
    ]
    assert main(args) == 1
