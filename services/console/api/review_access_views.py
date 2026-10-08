"""Read-only reviewer workflow and access-control HTTP routes."""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter

from services.console.api.common import short_hash
from services.console.api.model_evidence_views import (
    _evidence_ledger_payload,
    _model_risk_review_payload,
)
from services.console.api.system_views import _phase3a_readiness_payload


_short_hash = short_hash


def _reviewer_workflow_payload() -> Dict:
    model_risk = _model_risk_review_payload()
    ledger = _evidence_ledger_payload()
    phase3a = _phase3a_readiness_payload()["readiness"]
    reviewers = [
        {
            "reviewer_id": "rev-risk-001",
            "name": "Risk Reviewer",
            "role": "Independent Risk",
            "required": True,
            "capacity": 7,
            "open_tasks": 3,
        },
        {
            "reviewer_id": "rev-model-001",
            "name": "Model Validator",
            "role": "Model Validation",
            "required": True,
            "capacity": 6,
            "open_tasks": 4,
        },
        {
            "reviewer_id": "rev-data-001",
            "name": "Data Steward",
            "role": "Data Governance",
            "required": True,
            "capacity": 5,
            "open_tasks": 2,
        },
        {
            "reviewer_id": "rev-compliance-001",
            "name": "Compliance Reviewer",
            "role": "Compliance",
            "required": True,
            "capacity": 4,
            "open_tasks": 3,
        },
        {
            "reviewer_id": "rev-platform-001",
            "name": "Platform Reviewer",
            "role": "Research Platform",
            "required": False,
            "capacity": 8,
            "open_tasks": 2,
        },
    ]
    task_specs = [
        (
            "RW-001",
            "Validate model risk issue register",
            "MRM-I01",
            "rev-model-001",
            "HIGH",
            "BLOCKING",
            42,
            "MODEL_RISK_REVIEW",
        ),
        (
            "RW-002",
            "Challenge stress scenario calibration",
            "EV-SCENARIO-STRESS-001",
            "rev-risk-001",
            "HIGH",
            "BLOCKING",
            36,
            "SCENARIO_STRESS",
        ),
        (
            "RW-003",
            "Review point-in-time feature lineage",
            "MRM-I02",
            "rev-data-001",
            "HIGH",
            "BLOCKING",
            28,
            "DATA_CATALOG",
        ),
        (
            "RW-004",
            "Confirm Phase 3A governance NO_GO blockers",
            "EV-PHASE3A-001",
            "rev-compliance-001",
            "CRITICAL",
            "BLOCKING",
            54,
            "PHASE3A",
        ),
        (
            "RW-005",
            "Review alpha limitation disclosures",
            "MRM-I03",
            "rev-model-001",
            "MEDIUM",
            "NON_BLOCKING",
            18,
            "EXPERIMENT_REGISTRY",
        ),
        (
            "RW-006",
            "Verify evidence ledger hash chain",
            ledger["root_hash"],
            "rev-platform-001",
            "MEDIUM",
            "NON_BLOCKING",
            9,
            "EVIDENCE_LEDGER",
        ),
        (
            "RW-007",
            "Check dry-run safety evidence",
            "EV-ARCHITECTURE-001",
            "rev-risk-001",
            "HIGH",
            "BLOCKING",
            21,
            "ARCHITECTURE",
        ),
        (
            "RW-008",
            "Review compliance evidence checklist",
            "E03",
            "rev-compliance-001",
            "HIGH",
            "BLOCKING",
            63,
            "PHASE3A",
        ),
    ]
    tasks = []
    for idx, (task_id, title, evidence_ref, reviewer_id, severity, gate_type, age_hours, source_area) in enumerate(
        task_specs
    ):
        reviewer = next(item for item in reviewers if item["reviewer_id"] == reviewer_id)
        sla_hours = 48 if severity == "CRITICAL" else 36 if severity == "HIGH" else 72
        if age_hours > sla_hours:
            state = "OVERDUE"
        elif gate_type == "BLOCKING":
            state = "PENDING_REVIEW"
        else:
            state = "QUEUED"
        tasks.append(
            {
                "task_id": task_id,
                "title": title,
                "evidence_ref": evidence_ref,
                "source_area": source_area,
                "assigned_reviewer_id": reviewer_id,
                "assigned_reviewer": reviewer["name"],
                "role": reviewer["role"],
                "severity": severity,
                "gate_type": gate_type,
                "state": state,
                "age_hours": age_hours,
                "sla_hours": sla_hours,
                "due_in_hours": sla_hours - age_hours,
                "blocks_live_authorization": gate_type == "BLOCKING",
                "requires_independent_signoff": reviewer["required"],
                "task_hash": _short_hash(task_id, evidence_ref, reviewer_id, severity, gate_type),
                "created_at": 1_781_222_400 + idx * 600,
            }
        )
    approval_gates = [
        {
            "gate_id": "GATE-INDEPENDENT-VALIDATION",
            "name": "Independent validation complete",
            "status": "BLOCKED",
            "required_tasks": ["RW-001", "RW-005"],
            "owner_role": "Model Validation",
        },
        {
            "gate_id": "GATE-STRESS-CHALLENGE",
            "name": "Stress challenge complete",
            "status": "BLOCKED",
            "required_tasks": ["RW-002"],
            "owner_role": "Independent Risk",
        },
        {
            "gate_id": "GATE-DATA-LINEAGE",
            "name": "Point-in-time lineage reviewed",
            "status": "BLOCKED",
            "required_tasks": ["RW-003"],
            "owner_role": "Data Governance",
        },
        {
            "gate_id": "GATE-PHASE3A",
            "name": "Phase 3A blockers closed",
            "status": "BLOCKED",
            "required_tasks": ["RW-004", "RW-008"],
            "owner_role": "Compliance",
        },
        {
            "gate_id": "GATE-EVIDENCE-INTEGRITY",
            "name": "Evidence hash chain verified",
            "status": "PENDING",
            "required_tasks": ["RW-006", "RW-007"],
            "owner_role": "Research Platform",
        },
    ]
    blocking_tasks = [task for task in tasks if task["blocks_live_authorization"]]
    overdue_tasks = [task for task in tasks if task["state"] == "OVERDUE"]
    workload = [
        {
            **reviewer,
            "utilization": round(reviewer["open_tasks"] / max(reviewer["capacity"], 1), 3),
            "required_open_blocking": sum(
                1 for task in blocking_tasks if task["assigned_reviewer_id"] == reviewer["reviewer_id"]
            ),
        }
        for reviewer in reviewers
    ]
    return {
        "workflow_mode": "READ_ONLY_REVIEWER_WORKFLOW",
        "workflow_id": "reviewer-workflow-2026w24",
        "summary": {
            "tasks": len(tasks),
            "blocking_tasks": len(blocking_tasks),
            "overdue_tasks": len(overdue_tasks),
            "reviewers": len(reviewers),
            "approval_gates": len(approval_gates),
            "blocked_gates": sum(1 for gate in approval_gates if gate["status"] == "BLOCKED"),
            "independent_validation_complete": model_risk["summary"]["independent_validation_complete"],
            "phase3a_decision": phase3a["decision"],
            "live_authorization_allowed": False,
        },
        "reviewers": reviewers,
        "tasks": tasks,
        "approval_gates": approval_gates,
        "workload": workload,
        "evidence_links": [
            {
                "source": "Evidence Ledger",
                "ref": ledger["root_hash"],
                "records": ledger["summary"]["entries"],
                "blocking": ledger["summary"]["blocking_entries"],
            },
            {
                "source": "Model Risk Review",
                "ref": model_risk["model_risk_mode"],
                "records": model_risk["summary"]["models"],
                "blocking": model_risk["summary"]["open_high_issues"],
            },
            {
                "source": "Phase 3A",
                "ref": phase3a["decision"],
                "records": phase3a["total_count"],
                "blocking": len(phase3a["blockers"]),
            },
        ],
        "workflow_policy": {
            "read_only": True,
            "requires_independent_reviewer": True,
            "requires_blocking_task_closure": True,
            "requires_phase3a_go": True,
            "requires_signed_attestation": True,
            "mutable_tasks": False,
            "authorizes_live_trading": False,
        },
    }


def _access_control_payload() -> Dict:
    ledger = _evidence_ledger_payload()
    workflow = _reviewer_workflow_payload()
    phase3a = _phase3a_readiness_payload()["readiness"]
    roles = [
        {
            "role_id": "role-research-viewer",
            "name": "Research Viewer",
            "name_zh": "研究只读",
            "scope": "READ_ONLY_DASHBOARDS",
            "users": 6,
            "permissions": ["read:overview", "read:alpha", "read:experiments", "read:backtest", "read:stress"],
            "denied_capabilities": ["write:any", "approve:any", "export:signed", "trade:any", "credential:any"],
        },
        {
            "role_id": "role-risk-reviewer",
            "name": "Risk Reviewer",
            "name_zh": "风险复核",
            "scope": "READ_ONLY_RISK_REVIEW",
            "users": 3,
            "permissions": ["read:risk", "read:stress", "read:model-risk", "read:evidence-ledger", "review:metadata"],
            "denied_capabilities": ["write:risk-policy", "approve:live", "trade:any", "credential:any"],
        },
        {
            "role_id": "role-model-validator",
            "name": "Model Validator",
            "name_zh": "模型验证",
            "scope": "READ_ONLY_MODEL_GOVERNANCE",
            "users": 2,
            "permissions": ["read:model-risk", "read:reviewer-workflow", "read:evidence-ledger", "review:metadata"],
            "denied_capabilities": ["write:model", "approve:production", "trade:any", "credential:any"],
        },
        {
            "role_id": "role-compliance",
            "name": "Compliance Reviewer",
            "name_zh": "合规复核",
            "scope": "READ_ONLY_GOVERNANCE",
            "users": 2,
            "permissions": ["read:phase3a", "read:evidence-ledger", "read:reviewer-workflow", "read:audit-export"],
            "denied_capabilities": ["write:phase3a", "approve:broker", "trade:any", "credential:any"],
        },
        {
            "role_id": "role-platform-admin-readonly",
            "name": "Platform Admin Read-only",
            "name_zh": "平台只读管理员",
            "scope": "READ_ONLY_PLATFORM",
            "users": 1,
            "permissions": ["read:system", "read:architecture", "read:evidence-ledger", "read:audit-export"],
            "denied_capabilities": ["write:runtime", "deploy:production", "trade:any", "credential:any"],
        },
    ]
    permission_matrix = []
    permission_catalog = [
        "read:overview",
        "read:alpha",
        "read:experiments",
        "read:data-catalog",
        "read:backtest",
        "read:stress",
        "read:model-risk",
        "read:evidence-ledger",
        "read:reviewer-workflow",
        "read:audit-export",
        "review:metadata",
        "approve:live",
        "trade:any",
        "credential:any",
    ]
    for role in roles:
        for permission in permission_catalog:
            allowed = permission in role["permissions"]
            if permission in {"approve:live", "trade:any", "credential:any"}:
                allowed = False
            permission_matrix.append(
                {
                    "role_id": role["role_id"],
                    "permission": permission,
                    "allowed": allowed,
                    "reason": "Forbidden by L2 dry-run safety boundary"
                    if not allowed and permission in {"approve:live", "trade:any", "credential:any"}
                    else "Role scoped read-only permission",
                }
            )
    export_packages = [
        {
            "package_id": "EXP-AUDIT-2026W24-001",
            "name": "Model risk evidence pack",
            "name_zh": "模型风险证据包",
            "status": "READY_FOR_REVIEW",
            "records": ledger["summary"]["entries"],
            "source_root_hash": ledger["root_hash"],
            "requested_by_role": "role-compliance",
            "export_mode": "METADATA_ONLY_READ_ONLY",
            "content_hash": _short_hash("export", "model-risk", ledger["root_hash"], workflow["summary"]),
            "contains_credentials": False,
            "contains_live_orders": False,
            "download_enabled": False,
        },
        {
            "package_id": "EXP-AUDIT-2026W24-002",
            "name": "Phase 3A blocker pack",
            "name_zh": "Phase 3A 阻断证据包",
            "status": "BLOCKED_BY_NO_GO",
            "records": len(phase3a["blockers"]),
            "source_root_hash": ledger["root_hash"],
            "requested_by_role": "role-compliance",
            "export_mode": "METADATA_ONLY_READ_ONLY",
            "content_hash": _short_hash("export", "phase3a", phase3a["decision"], phase3a["blockers"]),
            "contains_credentials": False,
            "contains_live_orders": False,
            "download_enabled": False,
        },
        {
            "package_id": "EXP-AUDIT-2026W24-003",
            "name": "Research reproducibility pack",
            "name_zh": "研究可复现证据包",
            "status": "READY_FOR_REVIEW",
            "records": ledger["summary"]["sealed_entries"],
            "source_root_hash": ledger["root_hash"],
            "requested_by_role": "role-model-validator",
            "export_mode": "METADATA_ONLY_READ_ONLY",
            "content_hash": _short_hash("export", "reproducibility", ledger["chain_id"], ledger["root_hash"]),
            "contains_credentials": False,
            "contains_live_orders": False,
            "download_enabled": False,
        },
    ]
    access_reviews = [
        {
            "review_id": "AR-001",
            "role_id": "role-risk-reviewer",
            "status": "PENDING_ATTESTATION",
            "owner": "Security Officer",
            "age_hours": 18,
            "finding": "Review risk reviewer membership",
        },
        {
            "review_id": "AR-002",
            "role_id": "role-model-validator",
            "status": "PENDING_ATTESTATION",
            "owner": "Model Governance",
            "age_hours": 26,
            "finding": "Confirm independent validator separation",
        },
        {
            "review_id": "AR-003",
            "role_id": "role-compliance",
            "status": "BLOCKED_BY_PHASE3A_NO_GO",
            "owner": "Compliance",
            "age_hours": 42,
            "finding": "Cannot approve broker-facing access while Phase 3A is NO_GO",
        },
        {
            "review_id": "AR-004",
            "role_id": "role-platform-admin-readonly",
            "status": "APPROVED_READ_ONLY",
            "owner": "Research Platform",
            "age_hours": 9,
            "finding": "Read-only platform scope confirmed",
        },
    ]
    forbidden_capabilities = [
        {"capability": "trade:any", "status": "DENIED", "reason": "L2_DRY_RUN_ONLY boundary"},
        {"capability": "broker:connect", "status": "DENIED", "reason": "Phase 3A NO_GO"},
        {"capability": "credential:read", "status": "DENIED", "reason": "No credentials in research console"},
        {
            "capability": "export:signed-download",
            "status": "DENIED",
            "reason": "Signed export workflow not implemented",
        },
        {"capability": "permission:write", "status": "DENIED", "reason": "RBAC snapshot is read-only"},
    ]
    allowed_read_permissions = sum(1 for item in permission_matrix if item["allowed"])
    denied_dangerous_permissions = sum(
        1
        for item in permission_matrix
        if not item["allowed"] and item["permission"] in {"approve:live", "trade:any", "credential:any"}
    )
    return {
        "access_mode": "READ_ONLY_RBAC_AUDIT_EXPORT",
        "summary": {
            "roles": len(roles),
            "users": sum(role["users"] for role in roles),
            "permission_rows": len(permission_matrix),
            "allowed_read_permissions": allowed_read_permissions,
            "denied_dangerous_permissions": denied_dangerous_permissions,
            "export_packages": len(export_packages),
            "access_reviews": len(access_reviews),
            "live_authorization_allowed": False,
        },
        "roles": roles,
        "permission_matrix": permission_matrix,
        "export_packages": export_packages,
        "access_reviews": access_reviews,
        "forbidden_capabilities": forbidden_capabilities,
        "access_policy": {
            "read_only_console": True,
            "least_privilege": True,
            "segregation_of_duties_required": True,
            "signed_export_required": True,
            "mutable_permissions": False,
            "downloads_enabled": False,
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


@router.get("/api/v1/console/reviewer-workflow")
def reviewer_workflow() -> Dict:
    return _snapshot(_reviewer_workflow_payload())


@router.get("/api/v1/console/access-control")
def access_control() -> Dict:
    return _snapshot(_access_control_payload())
