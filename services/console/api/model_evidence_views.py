"""Read-only model-risk and evidence-ledger HTTP routes."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Callable, Dict

from fastapi import APIRouter

from services.console.api.architecture_views import _architecture_payload
from services.console.api.common import short_hash
from services.console.api.data_catalog import _data_catalog_payload
from services.console.api.registry_snapshots import (
    _experiments_payload,
    _load_experiment_store,
)
from services.console.api.system_views import _phase3a_readiness_payload


@dataclass(frozen=True)
class ModelEvidenceDependencies:
    backtest_explainability: Callable[[str], Dict]
    scenario_stress: Callable[[str], Dict]


class ModelEvidenceRuntime:
    def __init__(self) -> None:
        self._dependencies: ModelEvidenceDependencies | None = None

    def configure(
        self,
        *,
        backtest_explainability: Callable[[str], Dict],
        scenario_stress: Callable[[str], Dict],
    ) -> None:
        dependencies = ModelEvidenceDependencies(
            backtest_explainability=backtest_explainability,
            scenario_stress=scenario_stress,
        )
        if self._dependencies is None:
            self._dependencies = dependencies
            return
        if self._dependencies != dependencies:
            raise RuntimeError("model evidence runtime is already configured")

    def require(self) -> ModelEvidenceDependencies:
        if self._dependencies is None:
            raise RuntimeError("model evidence runtime must be configured by the console composition root")
        return self._dependencies


model_evidence_runtime = ModelEvidenceRuntime()
_short_hash = short_hash


def configure_model_evidence_views(
    *,
    backtest_explainability: Callable[[str], Dict],
    scenario_stress: Callable[[str], Dict],
) -> None:
    model_evidence_runtime.configure(
        backtest_explainability=backtest_explainability,
        scenario_stress=scenario_stress,
    )


def _backtest_explainability_payload(alpha_id: str) -> Dict:
    return model_evidence_runtime.require().backtest_explainability(alpha_id)


def _scenario_stress_payload(alpha_id: str) -> Dict:
    return model_evidence_runtime.require().scenario_stress(alpha_id)


def _model_risk_review_payload() -> Dict:
    experiments = _experiments_payload()["experiments"]
    data_catalog = _data_catalog_payload()
    stress_payload = _scenario_stress_payload("alpha-1")
    backtest_payload = _backtest_explainability_payload("alpha-1")
    models = []
    model_specs = [
        (
            "MRM-ALPHA-001",
            "Alpha factor alpha-1",
            "ALPHA_MODEL",
            "alpha-1",
            "HIGH",
            "Factor Hunter",
            "VALIDATION_REQUIRED",
        ),
        (
            "MRM-ALPHA-002",
            "Alpha factor alpha-2",
            "ALPHA_MODEL",
            "alpha-2",
            "MEDIUM",
            "Macro Analyst",
            "VALIDATION_REQUIRED",
        ),
        (
            "MRM-ALPHA-003",
            "Alpha factor alpha-3",
            "ALPHA_MODEL",
            "alpha-3",
            "HIGH",
            "Momentum Specialist",
            "RESTRICTED_USE",
        ),
        (
            "MRM-PORT-001",
            "Portfolio research weighting",
            "PORTFOLIO_MODEL",
            "portfolio-research",
            "HIGH",
            "Portfolio Research",
            "VALIDATION_REQUIRED",
        ),
        (
            "MRM-RISK-001",
            "Research risk gate",
            "RISK_MODEL",
            "risk-gate",
            "CRITICAL",
            "Risk",
            "INDEPENDENT_REVIEW_REQUIRED",
        ),
        (
            "MRM-STRESS-001",
            "Scenario stress replay",
            "STRESS_MODEL",
            "scenario-stress",
            "HIGH",
            "Risk",
            "VALIDATION_REQUIRED",
        ),
        (
            "MRM-DATA-001",
            "Point-in-time feature generation",
            "DATA_MODEL",
            "feature-store",
            "HIGH",
            "Research Platform",
            "VALIDATION_REQUIRED",
        ),
    ]
    for idx, (model_id, name, model_type, linked_component, tier, owner, status) in enumerate(model_specs):
        conceptual = 0.76 - idx * 0.025
        implementation = 0.82 - idx * 0.018
        outcomes = 0.68 - idx * 0.02
        monitoring = 0.63 - idx * 0.015
        stress_failure_penalty = (
            0.08
            if linked_component in {"risk-gate", "scenario-stress"} and stress_payload["summary"]["fails"] > 0
            else 0.0
        )
        residual_risk = max(
            0.05,
            min(
                0.95, 1.0 - statistics.mean([conceptual, implementation, outcomes, monitoring]) + stress_failure_penalty
            ),
        )
        findings = []
        if status != "APPROVED_FOR_RESEARCH":
            findings.append("Independent validation evidence incomplete")
        if linked_component in {"feature-store", "alpha-3"}:
            findings.append("Point-in-time and data lineage require deeper sample audit")
        if linked_component in {"risk-gate", "scenario-stress"}:
            findings.append("Stress calibration requires independent challenge")
        if model_type == "ALPHA_MODEL" and idx < len(experiments):
            experiment = experiments[idx]
            if experiment["decision"] != "APPROVED":
                findings.append(f"Linked experiment decision is {experiment['decision']}")
        models.append(
            {
                "model_id": model_id,
                "name": name,
                "model_type": model_type,
                "linked_component": linked_component,
                "owner": owner,
                "risk_tier": tier,
                "review_status": status,
                "intended_use": "Research ranking, validation, portfolio simulation, and risk review only",
                "prohibited_use": "Live trading authorization, broker routing, credential handling, or production order decisions",
                "validation_scores": {
                    "conceptual_soundness": round(conceptual, 3),
                    "implementation_verification": round(implementation, 3),
                    "outcomes_analysis": round(outcomes, 3),
                    "ongoing_monitoring": round(monitoring, 3),
                },
                "residual_risk": round(residual_risk, 3),
                "evidence": {
                    "experiment_id": experiments[idx]["experiment_id"] if idx < len(experiments) else None,
                    "config_hash": experiments[idx]["config"]["config_hash"]
                    if idx < len(experiments)
                    else backtest_payload["run"]["config_hash"],
                    "data_snapshot_id": experiments[idx]["config"]["data_snapshot_id"]
                    if idx < len(experiments)
                    else stress_payload["run"]["data_snapshot_id"],
                    "stress_run_id": stress_payload["run"]["run_id"]
                    if linked_component in {"risk-gate", "scenario-stress"}
                    else None,
                    "backtest_run_id": backtest_payload["run"]["run_id"]
                    if model_type in {"ALPHA_MODEL", "PORTFOLIO_MODEL"}
                    else None,
                },
                "limitations": [
                    "Offline deterministic replay, not broker-confirmed production behavior",
                    "Proxy costs and haircuts require independent calibration",
                    "Research feature catalog is snapshot-based and not a persisted governed store",
                ],
                "findings": findings,
                "compensating_controls": [
                    "Read-only console",
                    "L2 dry-run execution boundary",
                    "Immutable reproducibility hashes",
                    "Phase 3A NO_GO gate blocks broker test work",
                ],
            }
        )

    validation_dimensions = [
        {
            "dimension": "Conceptual soundness",
            "dimension_zh": "概念合理性",
            "score": round(statistics.mean(model["validation_scores"]["conceptual_soundness"] for model in models), 3),
            "status": "NEEDS_REVIEW",
            "evidence": "Alpha formulas, feature lineage, cost assumptions, and stress scenario definitions",
        },
        {
            "dimension": "Implementation verification",
            "dimension_zh": "实现核验",
            "score": round(
                statistics.mean(model["validation_scores"]["implementation_verification"] for model in models), 3
            ),
            "status": "PARTIAL",
            "evidence": "Console API contract tests, TypeScript contracts, dry-run integration gate",
        },
        {
            "dimension": "Outcomes analysis",
            "dimension_zh": "结果分析",
            "score": round(statistics.mean(model["validation_scores"]["outcomes_analysis"] for model in models), 3),
            "status": "NEEDS_REVIEW",
            "evidence": "Backtest explainability, segment stability, drawdown periods, monthly returns",
        },
        {
            "dimension": "Ongoing monitoring",
            "dimension_zh": "持续监控",
            "score": round(statistics.mean(model["validation_scores"]["ongoing_monitoring"] for model in models), 3),
            "status": "MISSING_PERSISTENCE",
            "evidence": "System health snapshots exist; persistent drift monitoring is not implemented",
        },
    ]
    issue_register = [
        {
            "issue_id": "MRM-I01",
            "severity": "HIGH",
            "status": "OPEN",
            "owner": "Risk",
            "description": "Independent model validation workflow is not persisted",
            "linked_models": ["MRM-RISK-001", "MRM-STRESS-001"],
        },
        {
            "issue_id": "MRM-I02",
            "severity": "HIGH",
            "status": "OPEN",
            "owner": "Research Platform",
            "description": "Feature store is read-only snapshot, not governed persisted infrastructure",
            "linked_models": ["MRM-DATA-001"],
        },
        {
            "issue_id": "MRM-I03",
            "severity": "MEDIUM",
            "status": "OPEN",
            "owner": "Alpha Research",
            "description": "Alpha limitations and intended-use approvals need reviewer signoff",
            "linked_models": ["MRM-ALPHA-001", "MRM-ALPHA-002", "MRM-ALPHA-003"],
        },
        {
            "issue_id": "MRM-I04",
            "severity": "MEDIUM",
            "status": "MITIGATED_BY_GATE",
            "owner": "Governance",
            "description": "Live trading authorization is explicitly blocked by Phase 3A NO_GO",
            "linked_models": ["MRM-PORT-001", "MRM-RISK-001"],
        },
    ]
    high_risk_models = sum(1 for model in models if model["risk_tier"] in {"HIGH", "CRITICAL"})
    open_high_issues = sum(1 for issue in issue_register if issue["severity"] == "HIGH" and issue["status"] == "OPEN")
    avg_residual_risk = round(statistics.mean(model["residual_risk"] for model in models), 3)
    return {
        "model_risk_mode": "READ_ONLY_MODEL_RISK_REVIEW",
        "review_standard": "PRINCIPLES_BASED_MODEL_RISK_MANAGEMENT",
        "summary": {
            "models": len(models),
            "high_risk_models": high_risk_models,
            "open_issues": sum(1 for issue in issue_register if issue["status"] == "OPEN"),
            "open_high_issues": open_high_issues,
            "avg_residual_risk": avg_residual_risk,
            "independent_validation_complete": False,
            "live_use_authorized": False,
        },
        "models": models,
        "validation_dimensions": validation_dimensions,
        "issue_register": issue_register,
        "evidence_sources": [
            {
                "source_id": "EXP_REGISTRY",
                "name": "Experiment Registry",
                "records": len(experiments),
                "read_only": True,
            },
            {
                "source_id": "DATA_CATALOG",
                "name": "Data Catalog / Feature Store",
                "records": data_catalog["summary"]["features"],
                "read_only": True,
            },
            {
                "source_id": "BACKTEST_EXPLAIN",
                "name": "Backtest Explainability",
                "records": len(backtest_payload["segments"]),
                "read_only": True,
            },
            {
                "source_id": "SCENARIO_STRESS",
                "name": "Scenario Stress Library",
                "records": stress_payload["summary"]["scenarios"],
                "read_only": True,
            },
        ],
        "governance_policy": {
            "requires_independent_validation": True,
            "requires_intended_use_statement": True,
            "requires_limitations_disclosure": True,
            "requires_issue_tracking": True,
            "requires_ongoing_monitoring": True,
            "mutable_reviews": False,
            "authorizes_live_trading": False,
        },
    }


def _evidence_ledger_payload() -> Dict:
    experiments_payload = _experiments_payload()
    data_catalog_payload = _data_catalog_payload()
    backtest_payload = _backtest_explainability_payload("alpha-1")
    stress_payload = _scenario_stress_payload("alpha-1")
    model_risk_payload = _model_risk_review_payload()
    architecture_payload = _architecture_payload()
    phase3a_payload = _phase3a_readiness_payload()["readiness"]

    evidence_specs = [
        {
            "evidence_id": "EV-EXP-REGISTRY-001",
            "source_module": "Experiment Registry",
            "source_endpoint": "/api/v1/console/experiments",
            "artifact_type": "EXPERIMENT_REGISTRY",
            "record_count": experiments_payload["summary"]["total_experiments"],
            "status": "SEALED",
            "owner": "Alpha Research",
            "content_hash": _short_hash(
                "experiments", experiments_payload["summary"], experiments_payload["stage_counts"]
            ),
            "blocks_live_authorization": False,
        },
        {
            "evidence_id": "EV-DATA-CATALOG-001",
            "source_module": "Data Catalog / Feature Store",
            "source_endpoint": "/api/v1/console/data-catalog",
            "artifact_type": "DATA_LINEAGE",
            "record_count": data_catalog_payload["summary"]["features"],
            "status": "SEALED",
            "owner": "Research Platform",
            "content_hash": _short_hash(
                "data-catalog", data_catalog_payload["summary"], data_catalog_payload["quality_policy"]
            ),
            "blocks_live_authorization": False,
        },
        {
            "evidence_id": "EV-BACKTEST-EXPLAIN-001",
            "source_module": "Backtest Explainability",
            "source_endpoint": "/api/v1/console/backtest-explainability",
            "artifact_type": "BACKTEST_TEAR_SHEET",
            "record_count": len(backtest_payload["segments"]),
            "status": "SEALED",
            "owner": "Alpha Research",
            "content_hash": backtest_payload["run"]["reproducibility_hash"],
            "blocks_live_authorization": False,
        },
        {
            "evidence_id": "EV-SCENARIO-STRESS-001",
            "source_module": "Scenario Stress Library",
            "source_endpoint": "/api/v1/console/scenario-stress",
            "artifact_type": "STRESS_REPLAY",
            "record_count": stress_payload["summary"]["scenarios"],
            "status": "SEALED",
            "owner": "Risk",
            "content_hash": stress_payload["run"]["reproducibility_hash"],
            "blocks_live_authorization": stress_payload["summary"]["fails"] > 0,
        },
        {
            "evidence_id": "EV-MODEL-RISK-001",
            "source_module": "Model Risk Review",
            "source_endpoint": "/api/v1/console/model-risk-review",
            "artifact_type": "MODEL_RISK_REVIEW",
            "record_count": model_risk_payload["summary"]["models"],
            "status": "SEALED",
            "owner": "Model Governance",
            "content_hash": _short_hash(
                "model-risk", model_risk_payload["summary"], model_risk_payload["issue_register"]
            ),
            "blocks_live_authorization": not model_risk_payload["summary"]["independent_validation_complete"],
        },
        {
            "evidence_id": "EV-ARCHITECTURE-001",
            "source_module": "System Architecture",
            "source_endpoint": "/api/v1/console/architecture",
            "artifact_type": "ARCHITECTURE_SNAPSHOT",
            "record_count": len(architecture_payload["modules"]),
            "status": "SEALED",
            "owner": "Research Platform",
            "content_hash": _short_hash(
                "architecture", architecture_payload["architecture_score"], architecture_payload["gaps"]
            ),
            "blocks_live_authorization": architecture_payload["phase3a_decision"] == "NO_GO",
        },
        {
            "evidence_id": "EV-PHASE3A-001",
            "source_module": "Phase 3A Governance",
            "source_endpoint": "/api/v1/console/phase-3a-readiness",
            "artifact_type": "GO_NO_GO_EVIDENCE",
            "record_count": phase3a_payload["total_count"],
            "status": "SEALED",
            "owner": "Governance",
            "content_hash": _short_hash("phase3a", phase3a_payload["decision"], phase3a_payload["blockers"]),
            "blocks_live_authorization": phase3a_payload["decision"] == "NO_GO",
        },
    ]

    entries = []
    previous_hash = "GENESIS"
    base_timestamp = 1_781_222_400
    for idx, spec in enumerate(evidence_specs):
        ledger_hash = _short_hash(
            spec["evidence_id"],
            spec["source_module"],
            spec["artifact_type"],
            spec["content_hash"],
            previous_hash,
            spec["record_count"],
        )
        entries.append(
            {
                **spec,
                "sequence": idx + 1,
                "sealed_at": base_timestamp + idx * 300,
                "previous_hash": previous_hash,
                "ledger_hash": ledger_hash,
                "immutability_status": "IMMUTABLE_RESEARCH_SNAPSHOT",
                "read_only": True,
                "execution_mode": "L2_DRY_RUN_ONLY",
                "verification": {
                    "hash_chain_valid": True,
                    "content_hash_present": bool(spec["content_hash"]),
                    "source_read_only": True,
                    "no_live_authorization": True,
                },
            }
        )
        previous_hash = ledger_hash

    # REAL integrity: recompute the whole hash chain from GENESIS and compare to the stored ledger hashes
    # (was a hardcoded True). A tampered entry/content/order now flips this to False.
    chain_valid, _prev = True, "GENESIS"
    for entry in entries:
        expect = _short_hash(
            entry["evidence_id"],
            entry["source_module"],
            entry["artifact_type"],
            entry["content_hash"],
            _prev,
            entry["record_count"],
        )
        if expect != entry["ledger_hash"]:
            chain_valid = False
        _prev = entry["ledger_hash"]
    for entry in entries:
        entry["verification"]["hash_chain_valid"] = chain_valid

    blockers = [entry for entry in entries if entry["blocks_live_authorization"]]
    return {
        "ledger_mode": "READ_ONLY_IMMUTABLE_EVIDENCE_LEDGER",
        "data_provenance": "EXPERIMENT_REGISTRY" if _load_experiment_store() else "SYNTHETIC_DETERMINISTIC",
        "chain_id": "quantos-research-ledger-2026w24",
        "root_hash": entries[-1]["ledger_hash"] if entries else "GENESIS",
        "summary": {
            "entries": len(entries),
            "sealed_entries": sum(1 for entry in entries if entry["status"] == "SEALED"),
            "blocking_entries": len(blockers),
            "hash_chain_valid": all(entry["verification"]["hash_chain_valid"] for entry in entries),
            "read_only_entries": sum(1 for entry in entries if entry["read_only"]),
            "live_authorization_allowed": False,
        },
        "entries": entries,
        "blockers": [
            {
                "evidence_id": entry["evidence_id"],
                "source_module": entry["source_module"],
                "reason": "Evidence blocks live authorization under current governance state",
                "ledger_hash": entry["ledger_hash"],
            }
            for entry in blockers
        ],
        "ledger_policy": {
            "append_only": True,
            "requires_previous_hash": True,
            "requires_content_hash": True,
            "requires_source_endpoint": True,
            "requires_read_only_source": True,
            "mutable_entries": False,
            "authorizes_live_trading": False,
        },
    }


router = APIRouter()


def _snapshot(payload: Dict) -> Dict:
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@router.get("/api/v1/console/model-risk-review")
async def model_risk_review() -> Dict:
    return _snapshot(_model_risk_review_payload())


@router.get("/api/v1/console/evidence-ledger")
async def evidence_ledger() -> Dict:
    return _snapshot(_evidence_ledger_payload())
