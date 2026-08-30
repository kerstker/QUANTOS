"""Read-only repository architecture and completeness HTTP routes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import statistics
import time
from typing import Callable, Dict, Sequence

from fastapi import APIRouter

from services.console.api.snapshot_cache import SingleFlightTtlCache
from services.governance.top_tier_assessment import assess_repository


_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOP_TIER_REQUIREMENTS = _REPO_ROOT / "config" / "top_tier_requirements.json"
_TOP_TIER_ASSESSMENT = _REPO_ROOT / "evidence" / "top_tier_assessment.json"
_ARCHITECTURE_CACHE = SingleFlightTtlCache[Dict](300)
_SYSTEM_COMPLETENESS_CACHE = SingleFlightTtlCache[Dict](300)


@dataclass(frozen=True)
class ArchitectureViewDependencies:
    console_paths: Callable[[], Sequence[str]]


class ArchitectureViewRuntime:
    def __init__(self) -> None:
        self._dependencies: ArchitectureViewDependencies | None = None

    def configure(self, *, console_paths: Callable[[], Sequence[str]]) -> None:
        dependencies = ArchitectureViewDependencies(console_paths=console_paths)
        if self._dependencies is None:
            self._dependencies = dependencies
            return
        if self._dependencies != dependencies:
            raise RuntimeError("architecture view runtime is already configured")

    def require(self) -> ArchitectureViewDependencies:
        if self._dependencies is None:
            raise RuntimeError("architecture view runtime must be configured by the console composition root")
        return self._dependencies


architecture_view_runtime = ArchitectureViewRuntime()


def configure_architecture_views(*, console_paths: Callable[[], Sequence[str]]) -> None:
    architecture_view_runtime.configure(console_paths=console_paths)


def _console_paths() -> Sequence[str]:
    return architecture_view_runtime.require().console_paths()


def _repo_inventory() -> Dict:
    """REAL read-only introspection of the checked-out repo — grounds the curated module/maturity map in
    hard counts: per-domain module + LOC counts under services/, test files/functions, console endpoints/pages.
    Deterministic given the code on disk; no network; read-only. Fail-safe: returns {} on any FS error."""
    import os

    try:

        def _count_py(base):
            n = loc = 0
            for dirpath, _dirs, files in os.walk(base):
                if "__pycache__" in dirpath:
                    continue
                for f in files:
                    if f.endswith(".py"):
                        n += 1
                        try:
                            with open(os.path.join(dirpath, f), encoding="utf-8", errors="ignore") as fh:
                                loc += sum(1 for _ in fh)
                        except OSError:
                            pass
            return n, loc

        svc = os.path.join(_REPO_ROOT, "services")
        domains = []
        if os.path.isdir(svc):
            for name in sorted(os.listdir(svc)):
                p = os.path.join(svc, name)
                if os.path.isdir(p) and not name.startswith("__"):
                    n, loc = _count_py(p)
                    if n:
                        domains.append({"domain": name, "n_modules": n, "loc": loc})
        n_test_files = n_test_fns = 0
        tests_dir = os.path.join(_REPO_ROOT, "tests")
        for dirpath, _dirs, files in os.walk(tests_dir):
            for f in files:
                if f.startswith("test_") and f.endswith(".py"):
                    n_test_files += 1
                    try:
                        with open(os.path.join(dirpath, f), encoding="utf-8", errors="ignore") as fh:
                            n_test_fns += sum(1 for ln in fh if ln.lstrip().startswith("def test_"))
                    except OSError:
                        pass
        pages_dir = os.path.join(_REPO_ROOT, "apps", "research-console", "src", "pages")
        n_pages = len([f for f in os.listdir(pages_dir) if f.endswith(".tsx")]) if os.path.isdir(pages_dir) else 0
        n_endpoints = sum(1 for path in _console_paths() if path.startswith("/api/v1/console/"))
        return {
            "domains": domains,
            "total_services_modules": sum(d["n_modules"] for d in domains),
            "total_services_loc": sum(d["loc"] for d in domains),
            "n_test_files": n_test_files,
            "n_test_functions": n_test_fns,
            "n_console_pages": n_pages,
            "n_console_endpoints": n_endpoints,
        }
    except Exception:
        return {}


def _architecture_payload() -> Dict:
    modules = [
        {
            "id": "frontend-console",
            "name": "Research Console",
            "layer": "Frontend",
            "status": "READY",
            "maturity": 0.72,
            "owner": "Research Platform",
            "capability": "Bilingual read-only dashboards, backend source status, responsive visualizations",
            "next_gap": "Role-aware workflows and richer drill-down navigation",
        },
        {
            "id": "console-api",
            "name": "Console API",
            "layer": "API",
            "status": "READY",
            "maturity": 0.68,
            "owner": "Research Platform",
            "capability": "FastAPI read-only computed snapshots for research, risk, portfolio, governance",
            "next_gap": "Versioned contracts, auth, pagination, and persisted snapshot history",
        },
        {
            "id": "data-catalog",
            "name": "Data Catalog / Feature Store",
            "layer": "Research Data",
            "status": "PARTIAL",
            "maturity": 0.34,
            "owner": "Research Platform",
            "capability": "Read-only dataset versions, quality checks, feature lineage, point-in-time policy",
            "next_gap": "Persist catalog metadata, feature materialization jobs, and dataset access controls",
        },
        {
            "id": "alpha-engine",
            "name": "Alpha / Factor Engine",
            "layer": "Research Compute",
            "status": "READY",
            "maturity": 0.46,
            "owner": "Alpha Research",
            "capability": "IC, rank IC, ICIR, deterministic candidate scoring, screening buckets",
            "next_gap": "Full alpha DAG, persisted feature values, and richer expression lineage",
        },
        {
            "id": "validation-engine",
            "name": "Validation Engine",
            "layer": "Research Compute",
            "status": "PARTIAL",
            "maturity": 0.38,
            "owner": "Alpha Research",
            "capability": "Leakage, walk-forward, cost, capacity, decay gates in snapshot form",
            "next_gap": "Full backtest engine, nested CV, multiple testing controls, artifact storage",
        },
        {
            "id": "paper-trading",
            "name": "Paper Trading",
            "layer": "Simulation",
            "status": "READY",
            "maturity": 0.52,
            "owner": "Trading Research",
            "capability": "Paper PnL, equity curves, metrics, dry-run adapter gate",
            "next_gap": "Persistent event replay, fill model variants, market impact model",
        },
        {
            "id": "portfolio-research",
            "name": "Portfolio Research",
            "layer": "Simulation",
            "status": "READY",
            "maturity": 0.48,
            "owner": "Portfolio Research",
            "capability": "Weights, simulated equity, drawdown, risk contribution, return attribution",
            "next_gap": "Optimizer, constraints, factor risk model, scenario stress library",
        },
        {
            "id": "scenario-stress",
            "name": "Scenario Stress Library",
            "layer": "Risk",
            "status": "PARTIAL",
            "maturity": 0.36,
            "owner": "Risk / Portfolio Research",
            "capability": "Read-only volatility, liquidity, cost, crowding, and regime break stress replays",
            "next_gap": "Persist governed scenario catalog, independent calibration, and stress approval workflow",
        },
        {
            "id": "risk-gate",
            "name": "Research Risk Gate",
            "layer": "Risk",
            "status": "READY",
            "maturity": 0.58,
            "owner": "Risk",
            "capability": "Fail-closed risk gates, reproducibility hash, gate reasons",
            "next_gap": "Independent risk service deployment, policy versioning, model risk workflow",
        },
        {
            "id": "model-risk-review",
            "name": "Model Risk Review",
            "layer": "Governance",
            "status": "PARTIAL",
            "maturity": 0.33,
            "owner": "Risk / Model Governance",
            "capability": "Read-only model inventory, validation dimensions, issue register, residual risk and evidence sources",
            "next_gap": "Persist independent validation workflow, reviewer signoff, and monitored model limitations",
        },
        {
            "id": "evidence-ledger",
            "name": "Immutable Evidence Ledger",
            "layer": "Governance",
            "status": "PARTIAL",
            "maturity": 0.37,
            "owner": "Model Governance / Research Platform",
            "capability": "Read-only hash-chained evidence snapshots across experiments, data, backtests, stress, and model risk",
            "next_gap": "Persist append-only evidence store, signed reviewer attestations, and access-controlled audit exports",
        },
        {
            "id": "reviewer-workflow",
            "name": "Reviewer Workflow",
            "layer": "Governance",
            "status": "PARTIAL",
            "maturity": 0.35,
            "owner": "Model Governance / Compliance",
            "capability": "Read-only reviewer tasks, approval gates, SLA aging, blocking conditions, and workload visibility",
            "next_gap": "Persist signed attestations, role-based access control, workflow mutations, and audit export approvals",
        },
        {
            "id": "access-control",
            "name": "Access Control / Audit Export",
            "layer": "Governance",
            "status": "PARTIAL",
            "maturity": 0.32,
            "owner": "Security / Compliance",
            "capability": "Read-only RBAC matrix, forbidden capability registry, access reviews, and audit export metadata",
            "next_gap": "Implement real identity provider integration, signed export approvals, and access-change audit trail",
        },
        {
            "id": "monitoring-drift",
            "name": "Monitoring & Drift",
            "layer": "Operations",
            "status": "PARTIAL",
            "maturity": 0.34,
            "owner": "Operations / Research Platform",
            "capability": "Read-only alpha decay, feature drift, data freshness, model risk aging, reviewer SLA, and breach watchlists",
            "next_gap": "Persist drift metrics, threshold policies, alert routing, and incident workflow integration",
        },
        {
            "id": "incident-response",
            "name": "Incident Response",
            "layer": "Operations",
            "status": "PARTIAL",
            "maturity": 0.31,
            "owner": "Operations / Risk / Model Governance",
            "capability": "Read-only incident routing, SLA breach tracking, escalation state, runbook mapping, and evidence-linked timelines",
            "next_gap": "Persist incident workflow state, signed postmortems, external notification routing, and operational audit trails",
        },
        {
            "id": "promotion-control",
            "name": "Promotion Control",
            "layer": "Governance",
            "status": "PARTIAL",
            "maturity": 0.33,
            "owner": "Research Governance / Risk / Operations",
            "capability": "Read-only candidate promotion gates across alpha screen, validation, paper replay, risk, model risk, monitoring, incidents, evidence, and access",
            "next_gap": "Persist signed promotion decisions, stage transition history, and independent release committee approvals",
        },
        {
            "id": "release-committee",
            "name": "Release Committee",
            "layer": "Governance",
            "status": "PARTIAL",
            "maturity": 0.30,
            "owner": "Release Committee / Compliance / Risk",
            "capability": "Read-only committee agenda, quorum, vote state, required attestations, and release decision blockers",
            "next_gap": "Persist signed votes, meeting minutes, quorum audit, and committee-approved release packages",
        },
        {
            "id": "phase3a-governance",
            "name": "Phase 3A Governance",
            "layer": "Governance",
            "status": "BLOCKED",
            "maturity": 0.42,
            "owner": "CTO / Compliance / Security",
            "capability": "Readiness matrix, blockers, safety boundary, governance scanner",
            "next_gap": "Close E01/E02/E03/E04/E06 before broker test environment work",
        },
    ]
    data_flows = [
        {"from": "frontend-console", "to": "console-api", "label": "GET read-only snapshots", "mode": "READ_ONLY"},
        {
            "from": "console-api",
            "to": "data-catalog",
            "label": "dataset versions, feature specs, quality checks",
            "mode": "COMPUTED_SNAPSHOT",
        },
        {
            "from": "data-catalog",
            "to": "alpha-engine",
            "label": "point-in-time feature definitions and lineage",
            "mode": "OFFLINE_RESEARCH",
        },
        {
            "from": "console-api",
            "to": "alpha-engine",
            "label": "factor metrics and candidates",
            "mode": "COMPUTED_SNAPSHOT",
        },
        {
            "from": "alpha-engine",
            "to": "validation-engine",
            "label": "candidate validation reports",
            "mode": "COMPUTED_SNAPSHOT",
        },
        {
            "from": "validation-engine",
            "to": "paper-trading",
            "label": "approved research candidates",
            "mode": "DRY_RUN_ONLY",
        },
        {
            "from": "paper-trading",
            "to": "portfolio-research",
            "label": "paper curves and candidate weights",
            "mode": "SIMULATION",
        },
        {
            "from": "portfolio-research",
            "to": "scenario-stress",
            "label": "candidate returns, liquidity, capacity, and stress haircuts",
            "mode": "STRESS_REPLAY",
        },
        {
            "from": "scenario-stress",
            "to": "risk-gate",
            "label": "scenario decisions and factor stress contributions",
            "mode": "RISK_REVIEW",
        },
        {
            "from": "portfolio-research",
            "to": "risk-gate",
            "label": "portfolio returns, weights, capacity",
            "mode": "RISK_REVIEW",
        },
        {
            "from": "risk-gate",
            "to": "model-risk-review",
            "label": "model evidence, residual risk, and issue register",
            "mode": "MODEL_GOVERNANCE",
        },
        {
            "from": "scenario-stress",
            "to": "model-risk-review",
            "label": "stress calibration evidence and limitations",
            "mode": "MODEL_GOVERNANCE",
        },
        {
            "from": "model-risk-review",
            "to": "evidence-ledger",
            "label": "sealed model risk evidence and open issues",
            "mode": "HASH_CHAINED_EVIDENCE",
        },
        {
            "from": "phase3a-governance",
            "to": "evidence-ledger",
            "label": "NO_GO blockers and readiness evidence",
            "mode": "HASH_CHAINED_EVIDENCE",
        },
        {
            "from": "evidence-ledger",
            "to": "reviewer-workflow",
            "label": "evidence refs, blockers, root hash, and review tasks",
            "mode": "READ_ONLY_WORKFLOW",
        },
        {
            "from": "model-risk-review",
            "to": "reviewer-workflow",
            "label": "model issues and independent validation tasks",
            "mode": "READ_ONLY_WORKFLOW",
        },
        {
            "from": "evidence-ledger",
            "to": "access-control",
            "label": "audit export metadata and root hash",
            "mode": "READ_ONLY_RBAC",
        },
        {
            "from": "reviewer-workflow",
            "to": "access-control",
            "label": "reviewer roles, access reviews, and segregation checks",
            "mode": "READ_ONLY_RBAC",
        },
        {
            "from": "data-catalog",
            "to": "monitoring-drift",
            "label": "feature quality, dataset freshness, point-in-time policy signals",
            "mode": "DRIFT_MONITORING",
        },
        {
            "from": "model-risk-review",
            "to": "monitoring-drift",
            "label": "issue aging, residual risk, and model validation status",
            "mode": "DRIFT_MONITORING",
        },
        {
            "from": "reviewer-workflow",
            "to": "monitoring-drift",
            "label": "SLA aging and blocking task status",
            "mode": "DRIFT_MONITORING",
        },
        {
            "from": "monitoring-drift",
            "to": "incident-response",
            "label": "BREACH/WATCH triggers, owners, thresholds, and evidence refs",
            "mode": "READ_ONLY_INCIDENT_ROUTING",
        },
        {
            "from": "access-control",
            "to": "incident-response",
            "label": "role boundaries and export restrictions for incident evidence",
            "mode": "READ_ONLY_INCIDENT_ROUTING",
        },
        {
            "from": "incident-response",
            "to": "reviewer-workflow",
            "label": "postmortem and independent review requirements",
            "mode": "READ_ONLY_WORKFLOW",
        },
        {
            "from": "validation-engine",
            "to": "promotion-control",
            "label": "validation decisions, gate pass counts, and rejection reasons",
            "mode": "READ_ONLY_PROMOTION_GATING",
        },
        {
            "from": "paper-trading",
            "to": "promotion-control",
            "label": "paper replay status, PnL metrics, and dry-run evidence",
            "mode": "READ_ONLY_PROMOTION_GATING",
        },
        {
            "from": "risk-gate",
            "to": "promotion-control",
            "label": "risk approvals, gate results, and reproducibility hashes",
            "mode": "READ_ONLY_PROMOTION_GATING",
        },
        {
            "from": "model-risk-review",
            "to": "promotion-control",
            "label": "model intended-use restrictions and open issue register",
            "mode": "READ_ONLY_PROMOTION_GATING",
        },
        {
            "from": "monitoring-drift",
            "to": "promotion-control",
            "label": "candidate drift status and blocking monitors",
            "mode": "READ_ONLY_PROMOTION_GATING",
        },
        {
            "from": "incident-response",
            "to": "promotion-control",
            "label": "active incidents, SLA breaches, and containment actions",
            "mode": "READ_ONLY_PROMOTION_GATING",
        },
        {
            "from": "evidence-ledger",
            "to": "promotion-control",
            "label": "evidence root hash and immutable snapshot refs",
            "mode": "READ_ONLY_PROMOTION_GATING",
        },
        {
            "from": "access-control",
            "to": "promotion-control",
            "label": "least-privilege and forbidden-capability enforcement",
            "mode": "READ_ONLY_PROMOTION_GATING",
        },
        {
            "from": "promotion-control",
            "to": "phase3a-governance",
            "label": "NO_GO release decision and dry-run-only promotion evidence",
            "mode": "GOVERNANCE",
        },
        {
            "from": "promotion-control",
            "to": "release-committee",
            "label": "candidate promotion decisions, blocking gates, and evidence refs",
            "mode": "READ_ONLY_COMMITTEE_REVIEW",
        },
        {
            "from": "reviewer-workflow",
            "to": "release-committee",
            "label": "required reviewer roles, open tasks, and approval gate status",
            "mode": "READ_ONLY_COMMITTEE_REVIEW",
        },
        {
            "from": "evidence-ledger",
            "to": "release-committee",
            "label": "root hash, blockers, and sealed evidence package metadata",
            "mode": "READ_ONLY_COMMITTEE_REVIEW",
        },
        {
            "from": "access-control",
            "to": "release-committee",
            "label": "segregation of duties and export restrictions",
            "mode": "READ_ONLY_COMMITTEE_REVIEW",
        },
        {
            "from": "release-committee",
            "to": "phase3a-governance",
            "label": "committee NO_GO recommendation and unmet attestation requirements",
            "mode": "GOVERNANCE",
        },
        {
            "from": "risk-gate",
            "to": "phase3a-governance",
            "label": "gate evidence and safety status",
            "mode": "GOVERNANCE",
        },
    ]
    gaps = [
        {
            "id": "G01",
            "area": "Market Data Lake",
            "severity": "HIGH",
            "status": "PARTIAL",
            "next_action": "Persist versioned market data snapshots and quality checks outside console snapshots",
        },
        {
            "id": "G02",
            "area": "Feature Store",
            "severity": "HIGH",
            "status": "PARTIAL",
            "next_action": "Add materialized offline features, feature serving contracts, and access controls",
        },
        {
            "id": "G03",
            "area": "Backtest Engine",
            "severity": "HIGH",
            "status": "PARTIAL",
            "next_action": "Add event-driven backtests, costs, slippage, and replayable artifacts",
        },
        {
            "id": "G04",
            "area": "Experiment Registry",
            "severity": "MEDIUM",
            "status": "PARTIAL",
            "next_action": "Persist immutable experiment history, approvals, and artifact storage",
        },
        {
            "id": "G05",
            "area": "Risk Model",
            "severity": "MEDIUM",
            "status": "PARTIAL",
            "next_action": "Add factor covariance, independent scenario calibration, and stress governance workflow",
        },
        {
            "id": "G06",
            "area": "Model Risk Management",
            "severity": "MEDIUM",
            "status": "PARTIAL",
            "next_action": "Persist independent validation workflow, reviewer signoff, and ongoing monitoring",
        },
        {
            "id": "G07",
            "area": "Immutable Evidence Ledger",
            "severity": "MEDIUM",
            "status": "PARTIAL",
            "next_action": "Persist append-only evidence ledger and signed attestations",
        },
        {
            "id": "G08",
            "area": "Reviewer Workflow",
            "severity": "MEDIUM",
            "status": "PARTIAL",
            "next_action": "Persist reviewer tasks, signed attestations, and role-based workflow controls",
        },
        {
            "id": "G09",
            "area": "Access Control",
            "severity": "MEDIUM",
            "status": "PARTIAL",
            "next_action": "Integrate real identity provider, signed exports, and immutable access-change audit trail",
        },
        {
            "id": "G10",
            "area": "Monitoring & Drift",
            "severity": "MEDIUM",
            "status": "PARTIAL",
            "next_action": "Persist drift metrics, alert routing, and incident workflow integration",
        },
        {
            "id": "G11",
            "area": "Incident Response",
            "severity": "MEDIUM",
            "status": "PARTIAL",
            "next_action": "Persist incident workflow state, external notification routing, and signed postmortems",
        },
        {
            "id": "G12",
            "area": "Promotion Control",
            "severity": "MEDIUM",
            "status": "PARTIAL",
            "next_action": "Persist signed promotion decisions, stage transition history, and release committee approvals",
        },
        {
            "id": "G13",
            "area": "Release Committee",
            "severity": "MEDIUM",
            "status": "PARTIAL",
            "next_action": "Persist signed votes, meeting minutes, quorum audit, and release package approvals",
        },
    ]
    return {
        "current_level": "L2_DRY_RUN_ONLY",
        "target_level": "L3_BROKER_TEST_ENV_READY",
        "phase3a_decision": "NO_GO",
        "implementation_authorized": False,
        "architecture_score": round(statistics.mean(module["maturity"] for module in modules), 3),
        "inventory": _repo_inventory(),
        "modules": modules,
        "data_flows": data_flows,
        "gaps": gaps,
        "safety_boundary": {
            "read_only_console": True,
            "execution_boundary": "LIVE_TRADING_DISALLOWED",
            "forbidden_capabilities": [
                "No real CTP/FIX implementation",
                "No broker SDK imports",
                "No socket connections",
                "No credential files",
                "No live order APIs",
            ],
        },
    }


def _system_completeness_payload() -> Dict:
    try:
        assessment = json.loads(_TOP_TIER_ASSESSMENT.read_text(encoding="utf-8"))
        if not isinstance(assessment, dict):
            raise ValueError("persisted assessment is not an object")
        if not isinstance(assessment.get("requirements"), list):
            raise ValueError("persisted assessment requirements are absent")
        if assessment.get("summary", {}).get("total") != len(assessment["requirements"]):
            raise ValueError("persisted assessment summary is inconsistent")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        assessment = assess_repository(
            _REPO_ROOT,
            _TOP_TIER_REQUIREMENTS,
            as_of_ms=time.time_ns() // 1_000_000,
        )
    red_lines = [item for item in assessment["requirements"] if item["red_line"] and item["status"] != "PASS"]
    return {
        "assessment": {"schema_version": assessment["schema_version"], "verdict": assessment["verdict"]},
        "domains": assessment["domains"],
        "requirements": assessment["requirements"],
        "red_lines": red_lines,
        "summary": assessment["summary"],
        "inventory": _repo_inventory(),
        "safety_boundary": {
            "read_only_console": True,
            "execution_boundary": "LIVE_TRADING_DISALLOWED",
            "authorized_level": "L2_DRY_RUN_ONLY",
            "live_trading_authorized": False,
        },
    }


router = APIRouter()


@router.get("/api/v1/console/architecture")
async def architecture() -> Dict:
    payload = await asyncio.to_thread(_ARCHITECTURE_CACHE.get, _architecture_payload)
    real_inventory = bool(payload.get("inventory", {}).get("domains"))
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_REPO_INVENTORY" if real_inventory else "SYNTHETIC_DETERMINISTIC"),
        **payload,
    }


@router.get("/api/v1/console/system-completeness")
async def system_completeness() -> Dict:
    payload = await asyncio.to_thread(
        _SYSTEM_COMPLETENESS_CACHE.get,
        _system_completeness_payload,
    )
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "TOP_TIER_EVIDENCE_ASSESSMENT",
        **payload,
    }
