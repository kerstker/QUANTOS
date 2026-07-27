"""Read-only system health, architecture, and completeness HTTP routes."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Callable, Dict, List

from fastapi import APIRouter

from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from quantos.contracts.events import (
    EVENT_ALPHA_MAINTENANCE_TRANSITION,
    EVENT_DATA_QUALITY_ADMISSION_DECIDED,
    EVENT_DATA_REFRESH_RECORDED,
    EVENT_DEPLOYMENT_RECORDED,
    EVENT_DRY_RUN_COMPLETED,
    EVENT_DRY_RUN_STARTED,
    EVENT_PRETRADE_APPROVED,
    EVENT_PRETRADE_REJECTED,
    EVENT_RESEARCH_RISK_EVALUATED,
)
from services.monitoring.domain_event_runtime import (
    DOMAIN_EVENT_DB,
    read_domain_event_bus_health,
)
from services.monitoring.event_projection import DomainEventProjection


CONSOLE_API_VERSION = os.getenv("QUANTOS_CONSOLE_API_VERSION", "0.1.0")
RUNTIME_CONTRACT_VERSION = "console-runtime.v1"


_DOMAIN_EVENT_PROJECTION_DB = os.path.join(
    "data", "cache", "domain_event_projection.db"
)
_DOMAIN_EVENT_DB = DOMAIN_EVENT_DB
_CONSOLE_DOMAIN_EVENT_TYPES = {
    EVENT_ALPHA_MAINTENANCE_TRANSITION,
    EVENT_DATA_QUALITY_ADMISSION_DECIDED,
    EVENT_DATA_REFRESH_RECORDED,
    EVENT_DEPLOYMENT_RECORDED,
    EVENT_DRY_RUN_COMPLETED,
    EVENT_DRY_RUN_STARTED,
    EVENT_PRETRADE_APPROVED,
    EVENT_PRETRADE_REJECTED,
    EVENT_RESEARCH_RISK_EVALUATED,
}
_CONSOLE_DOMAIN_EVENT_PAYLOAD_FIELDS = {
    "action",
    "admission_id",
    "actor_id",
    "alpha_id",
    "allowed",
    "approved",
    "audit_root",
    "block_reason_count",
    "config_provenance",
    "decision",
    "decision_hash",
    "duration_ms",
    "engines_run",
    "engines_skipped",
    "evidence_schema_version",
    "evidence_sha256",
    "failed_instrument_count",
    "failed_source_count",
    "gates_passed",
    "gates_run",
    "history_entry_hash",
    "instrument_id",
    "instrument_count",
    "environment",
    "mode",
    "lineage_verdict",
    "n_approved",
    "n_fills",
    "n_intents",
    "n_rejected",
    "quantity",
    "provenance",
    "price_basis",
    "purpose",
    "quality_verdict",
    "readiness",
    "reasons",
    "run_id",
    "run_repro_hash",
    "series",
    "side",
    "strategy_id",
    "source_count",
    "status_hash",
    "subject",
    "version_ids",
    "verdict",
}


@dataclass(frozen=True)
class SystemViewDependencies:
    alpha_candidates: Callable[..., List[Dict]]
    validation_decision: Callable[[Dict], str]
    runs_registry: Callable[[], object]
    monitoring_snapshot: Callable[[], Dict]


class SystemViewRuntime:
    def __init__(self) -> None:
        self._dependencies: SystemViewDependencies | None = None

    def configure(
        self,
        *,
        alpha_candidates: Callable[..., List[Dict]],
        validation_decision: Callable[[Dict], str],
        runs_registry: Callable[[], object],
        monitoring_snapshot: Callable[[], Dict],
    ) -> None:
        dependencies = SystemViewDependencies(
            alpha_candidates=alpha_candidates,
            validation_decision=validation_decision,
            runs_registry=runs_registry,
            monitoring_snapshot=monitoring_snapshot,
        )
        if self._dependencies is None:
            self._dependencies = dependencies
            return
        if self._dependencies != dependencies:
            raise RuntimeError("system view runtime is already configured")

    def require(self) -> SystemViewDependencies:
        if self._dependencies is None:
            raise RuntimeError("system view runtime must be configured by the console composition root")
        return self._dependencies


system_view_runtime = SystemViewRuntime()


def configure_system_views(
    *,
    alpha_candidates: Callable[..., List[Dict]],
    validation_decision: Callable[[Dict], str],
    runs_registry: Callable[[], object],
    monitoring_snapshot: Callable[[], Dict],
) -> None:
    system_view_runtime.configure(
        alpha_candidates=alpha_candidates,
        validation_decision=validation_decision,
        runs_registry=runs_registry,
        monitoring_snapshot=monitoring_snapshot,
    )


def _alpha_candidates(count: int = 12) -> List[Dict]:
    return system_view_runtime.require().alpha_candidates(count=count)


def _validation_decision(alpha: Dict) -> str:
    return system_view_runtime.require().validation_decision(alpha)


def _runs_registry():
    return system_view_runtime.require().runs_registry()


def _real_monitoring_snapshot() -> Dict:
    return system_view_runtime.require().monitoring_snapshot()


PHASE3A_CRITERIA = [
    {
        "id": "E01",
        "criterion": "Phase 2H governance approved",
        "category": "Governance",
        "owner": "CTO",
        "status": "PENDING",
        "evidence": "Governance signoff register",
    },
    {
        "id": "E02",
        "criterion": "Broker test env account confirmed",
        "category": "Broker",
        "owner": "Broker Relationship Owner",
        "status": "PENDING",
        "evidence": "Broker response intake",
    },
    {
        "id": "E03",
        "criterion": "Legal/compliance approval",
        "category": "Compliance",
        "owner": "Compliance Officer",
        "status": "PENDING",
        "evidence": "Legal/compliance checklist",
    },
    {
        "id": "E04",
        "criterion": "Test credentials in Vault",
        "category": "Credentials",
        "owner": "Security Officer",
        "status": "PENDING",
        "evidence": "Vault path and access policy",
    },
    {
        "id": "E05",
        "criterion": "No credentials in code",
        "category": "Security",
        "owner": "Security Officer",
        "status": "APPROVED",
        "evidence": "Governance scanner report",
    },
    {
        "id": "E06",
        "criterion": "Network access reviewed",
        "category": "Network",
        "owner": "Security Officer",
        "status": "PENDING",
        "evidence": "Network/IP whitelist review",
    },
    {
        "id": "E07",
        "criterion": "Broker readiness checklist",
        "category": "Operations",
        "owner": "Operations Lead",
        "status": "APPROVED",
        "evidence": "Broker readiness checklist",
    },
    {
        "id": "E08",
        "criterion": "Kill switch tested",
        "category": "Risk",
        "owner": "Trading System Owner",
        "status": "APPROVED",
        "evidence": "Kill switch drill report",
    },
    {
        "id": "E09",
        "criterion": "Rollback tested",
        "category": "Risk",
        "owner": "Trading System Owner",
        "status": "APPROVED",
        "evidence": "Rollback drill report",
    },
    {
        "id": "E10",
        "criterion": "DR runbooks approved",
        "category": "DR",
        "owner": "Operations Lead",
        "status": "APPROVED",
        "evidence": "DR dry-run report",
    },
    {
        "id": "E11",
        "criterion": "UAT plan approved",
        "category": "UAT",
        "owner": "Operations Lead",
        "status": "APPROVED",
        "evidence": "UAT dry-run report",
    },
    {
        "id": "E12",
        "criterion": "Incident response approved",
        "category": "IR",
        "owner": "Operations Lead",
        "status": "APPROVED",
        "evidence": "Incident response plan",
    },
    {
        "id": "E13",
        "criterion": "No production credentials",
        "category": "Security",
        "owner": "Security Officer",
        "status": "APPROVED",
        "evidence": "Governance scanner report",
    },
    {
        "id": "E14",
        "criterion": "No production trading",
        "category": "Security",
        "owner": "Risk Officer",
        "status": "APPROVED",
        "evidence": "Governance scanner report",
    },
    {
        "id": "E15",
        "criterion": "Phase 2G.1 tests pass",
        "category": "Technical",
        "owner": "Trading System Owner",
        "status": "APPROVED",
        "evidence": "Dry-run adapter gate evidence",
    },
]
PHASE3A_BLOCKERS = [
    {
        "id": "E01",
        "blocker": "Governance signatures incomplete",
        "owner": "CTO",
        "dependency": "None",
        "next_action": "Collect required role signatures",
    },
    {
        "id": "E02",
        "blocker": "Broker test environment not confirmed",
        "owner": "Broker Relationship Owner",
        "dependency": "Broker response",
        "next_action": "Send broker request package",
    },
    {
        "id": "E03",
        "blocker": "Legal/compliance approval incomplete",
        "owner": "Compliance Officer",
        "dependency": "E02",
        "next_action": "Review broker agreement and test scope",
    },
    {
        "id": "E04",
        "blocker": "Test credentials not provisioned in Vault",
        "owner": "Security Officer",
        "dependency": "E02",
        "next_action": "Prepare Vault-only intake workflow",
    },
    {
        "id": "E06",
        "blocker": "Network access not reviewed",
        "owner": "Security Officer",
        "dependency": "E02",
        "next_action": "Collect broker endpoint and whitelist details",
    },
]


def _system_health_payload() -> Dict:
    probe_started = time.perf_counter()
    try:
        monitoring = _real_monitoring_snapshot()
    except Exception as exc:  # health must remain readable and fail closed
        monitoring = {
            "provenance": "MONITORING_ERROR",
            "audit_chain_valid": False,
            "kill_switch_active": True,
            "healthy": False,
            "anomalies": [f"MONITORING_ERROR:{type(exc).__name__}"],
            "audit_events": 0,
        }
    local_latency_ms = round((time.perf_counter() - probe_started) * 1000.0, 3)
    if monitoring.get("healthy") is True:
        local_status = "healthy"
    elif monitoring.get("healthy") is False:
        local_status = "degraded"
    else:
        local_status = "unknown"
    return {
        "monitoring": monitoring,
        "latency_provenance": "MEASURED_LOCAL_MONITORING_CALL",
        "latency_scope": "PROCESS_LOCAL_ONLY",
        "error_rate_provenance": "UNMEASURED_NO_REQUEST_COUNTER",
        "services": [
            {
                "service": "research-console-api",
                "status": local_status,
                "latency_ms": local_latency_ms,
                "error_rate": None,
                "probe": "LOCAL_MONITORING_CALL",
            },
            *[
                {
                    "service": service,
                    "status": "unknown",
                    "latency_ms": None,
                    "error_rate": None,
                    "probe": "NOT_CONFIGURED",
                }
                for service in (
                    "market-data-ingestor",
                    "alpha-discovery-engine",
                    "alpha-validation",
                    "paper-trading",
                    "portfolio-research",
                    "research-risk",
                )
            ],
        ],
        "gates_provenance": "STATIC_READINESS_DECLARATION_NOT_LIVENESS",
        "gates": [
            {"g": "Phase 1G.1 Research Pipeline", "s": "PASSED"},
            {"g": "Phase 1H.1 Paper Trading", "s": "PASSED"},
            {"g": "Phase 1I.1 Portfolio", "s": "READY"},
            {"g": "Phase 1J.1 Risk", "s": "READY"},
            {"g": "Phase 1K.1 Frontend", "s": "READY"},
        ],
        "safety_checks": [
            "No OMS/EMS imports",
            "No broker connectivity",
            "No CTP/FIX gateways",
            "No live trading controls",
            "Research-only",
        ],
    }


def _runtime_contract_payload() -> Dict:
    """Return the read-only runtime handshake consumed by the console shell.

    A HTTP 200 alone cannot prove that a browser is talking to the intended
    backend.  This payload binds the running process to the console API
    version, the canonical event-schema catalog and the L2 safety posture.
    Build SHA and instance id are deployment inputs; when they are not pinned
    we report that fact instead of manufacturing a release identity.
    """
    build_sha = os.getenv("QUANTOS_BUILD_SHA", "").strip()
    build_sha_known = bool(build_sha and build_sha.lower() not in {"unknown", "dirty"})
    instance_id = os.getenv("QUANTOS_CONSOLE_INSTANCE_ID", "").strip()
    if not instance_id:
        instance_id = f"pid-{os.getpid()}"
    manifest = CANONICAL_EVENT_SCHEMAS.manifest()
    canonical_manifest = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "contract_version": RUNTIME_CONTRACT_VERSION,
        "service": "research-console-api",
        "instance_id": instance_id,
        "process_id": os.getpid(),
        "build_sha": build_sha or "UNKNOWN",
        "build_sha_known": build_sha_known,
        "event_schema_catalog": {
            "sha256": hashlib.sha256(canonical_manifest).hexdigest(),
            "event_type_count": len(manifest),
            "latest_versions": CANONICAL_EVENT_SCHEMAS.latest_consumer_versions(),
        },
        "safety_boundary": {
            "read_only_console": True,
            "authorized_level": "L2_DRY_RUN_ONLY",
            "live_trading_authorized": False,
            "order_submission_allowed": False,
            "broker_connectivity": False,
        },
        "generated_at_ms": int(time.time() * 1000),
    }


def _agents_payload() -> Dict:
    candidates = _alpha_candidates()
    approved_count = sum(1 for alpha in candidates if _validation_decision(alpha) == "APPROVED")
    agents = [
        {
            "agent_id": "agent-1",
            "name": "Macro Analyst",
            "state": "RUNNING",
            "runs_completed": 42,
            "alpha_approved": approved_count,
            "performance_score": 0.78,
            "budget_spent_cny": 150,
        },
        {
            "agent_id": "agent-2",
            "name": "Factor Hunter",
            "state": "REFLECTING",
            "runs_completed": 28,
            "alpha_approved": 3,
            "performance_score": 0.62,
            "budget_spent_cny": 280,
        },
        {
            "agent_id": "agent-3",
            "name": "Momentum Specialist",
            "state": "IDLE",
            "runs_completed": 15,
            "alpha_approved": 2,
            "performance_score": 0.55,
            "budget_spent_cny": 95,
        },
    ]
    return {
        "summary": {
            "active": sum(1 for agent in agents if agent["state"] == "RUNNING"),
            "total_runs": sum(agent["runs_completed"] for agent in agents),
            "approved": sum(agent["alpha_approved"] for agent in agents),
            "budget_spent_cny": sum(agent["budget_spent_cny"] for agent in agents),
        },
        "agents": agents,
    }


def _domain_event_projection_payload() -> Dict | None:
    projection = None
    try:
        projection = DomainEventProjection(
            _DOMAIN_EVENT_PROJECTION_DB, create=False
        )
        records = projection.records(limit=50)
    except Exception:
        return None
    finally:
        if projection is not None:
            projection.close()

    events = []
    for record in reversed(records):
        event = record["event"]
        if event.event_type not in _CONSOLE_DOMAIN_EVENT_TYPES:
            continue
        safe_payload = {
            key: value
            for key, value in event.payload.items()
            if key in _CONSOLE_DOMAIN_EVENT_PAYLOAD_FIELDS
        }
        events.append(
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "timestamp": CANONICAL_EVENT_SCHEMAS.timestamp_unix_seconds(event),
                "source": event.source,
                "correlation_id": record["correlation_id"],
                "payload": {
                    **safe_payload,
                    "causation_id": record["causation_id"],
                    "domain": event.domain,
                    "event_seq": record["event_seq"],
                    "read_only": True,
                },
            }
        )
    if not events:
        return None
    return {"events": events, "data_provenance": "DOMAIN_EVENT_PROJECTION"}


def _events_payload() -> Dict:
    """Read-only event stream with projection, run-registry, then labeled fallback."""
    event_bus_health = read_domain_event_bus_health(
        now_ms=int(time.time() * 1_000),
        path=_DOMAIN_EVENT_DB,
    )
    projected = _domain_event_projection_payload()
    if projected is not None:
        return {**projected, "event_bus_health": event_bus_health}
    try:
        runs = _runs_registry().list(limit=50)
    except Exception:
        runs = []
    if runs:
        events = []
        for r in runs:
            metrics = r.metrics or {}
            hk = next(iter(metrics), None)
            events.append(
                {
                    "event_id": f"evt-{r.run_id[4:16]}",
                    "event_type": f"research.run.{r.status}",
                    "timestamp": r.finished_ts or r.created_ts,
                    "source": r.kind,
                    "correlation_id": r.run_id,
                    "payload": {
                        "read_only": True,
                        "title": r.title,
                        "verdict": (r.verdict or "")[:80],
                        "headline_metric": hk,
                        "headline_value": metrics.get(hk) if hk else None,
                    },
                }
            )
        events.sort(key=lambda e: e["timestamp"], reverse=True)
        return {
            "events": events,
            "data_provenance": "RESEARCH_RUN_REGISTRY",
            "event_bus_health": event_bus_health,
        }
    event_types = [
        "agent.created",
        "loop.completed",
        "alpha.approved",
        "paper.passed",
        "risk.approved",
        "portfolio.simulated",
        "phase3a.blocked",
    ]
    sources = [
        "research-agent-loop",
        "alpha-factory",
        "alpha-validation",
        "paper-trading",
        "research-risk",
        "portfolio-research",
        "phase3a-governance",
    ]
    base_timestamp = 1_781_222_400
    return {
        "events": [
            {
                "event_id": f"evt-{1000 + idx}",
                "event_type": f"research.{event_types[idx % len(event_types)]}",
                "timestamp": base_timestamp - idx * 300,
                "source": sources[idx % len(sources)],
                "correlation_id": f"corr-{2000 + idx}",
                "payload": {"read_only": True, "sequence": idx, "lineage": "BACKEND_COMPUTED_SNAPSHOT"},
            }
            for idx in range(12)
        ],
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        "event_bus_health": event_bus_health,
    }


def _phase3a_readiness_payload() -> Dict:
    approved_count = sum(1 for criterion in PHASE3A_CRITERIA if criterion["status"] == "APPROVED")
    return {
        "readiness": {
            "current_level": "L2_DRY_RUN_ONLY",
            "target_level": "L3_BROKER_TEST_ENV_READY",
            "decision": "NO_GO",
            "phase_status": "BLOCKED",
            "implementation_authorized": False,
            "approved_count": approved_count,
            "total_count": len(PHASE3A_CRITERIA),
            "criteria": PHASE3A_CRITERIA,
            "blockers": PHASE3A_BLOCKERS,
        }
    }


router = APIRouter()


@router.get("/health")
async def health() -> Dict[str, str]:
    return {
        "status": "healthy",
        "service": "research-console-api",
        "mode": "READ_ONLY",
    }


def _snapshot(payload: Dict) -> Dict:
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": CONSOLE_API_VERSION,
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@router.get("/api/v1/console/agents")
async def agents_dashboard() -> Dict:
    return _snapshot(_agents_payload())


@router.get("/api/v1/console/events")
async def events_dashboard() -> Dict:
    return _snapshot(_events_payload())


@router.get("/api/v1/console/phase-3a-readiness")
async def phase3a_readiness() -> Dict:
    return _snapshot(_phase3a_readiness_payload())


@router.get("/api/v1/console/system-health")
async def system_health() -> Dict:
    return _snapshot(_system_health_payload())


@router.get("/api/v1/console/runtime-contract")
async def runtime_contract() -> Dict:
    """Read-only frontend/backend version and safety handshake."""
    return _snapshot(
        {
            **_runtime_contract_payload(),
            "data_provenance": "REAL_RUNTIME_CONTRACT",
        }
    )
