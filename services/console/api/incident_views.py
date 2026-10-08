"""Read-only incident routing and response HTTP route."""

from __future__ import annotations

import statistics
from typing import Dict

from fastapi import APIRouter

from services.console.api.common import short_hash
from services.console.api.monitoring_views import _monitoring_drift_payload


_short_hash = short_hash


def _incident_response_payload() -> Dict:
    monitoring = _monitoring_drift_payload()
    routing_rules = [
        {
            "route_id": "ROUTE-ALPHA-DECAY",
            "category": "ALPHA_DECAY",
            "queue": "Alpha Research Incident Desk",
            "primary_owner": "Alpha Research",
            "escalation_owner": "Head of Alpha Research",
            "sla_minutes": 180,
            "escalation_after_minutes": 120,
            "runbook_id": "RB-ALPHA-DECAY",
            "notification_mode": "READ_ONLY_NO_EXTERNAL_SEND",
        },
        {
            "route_id": "ROUTE-FEATURE-DRIFT",
            "category": "FEATURE_DRIFT",
            "queue": "Research Platform Data Desk",
            "primary_owner": "Research Platform",
            "escalation_owner": "Data Platform Lead",
            "sla_minutes": 120,
            "escalation_after_minutes": 90,
            "runbook_id": "RB-FEATURE-DRIFT",
            "notification_mode": "READ_ONLY_NO_EXTERNAL_SEND",
        },
        {
            "route_id": "ROUTE-DATA-FRESHNESS",
            "category": "DATA_FRESHNESS",
            "queue": "Data Operations",
            "primary_owner": "Data Platform",
            "escalation_owner": "Research Platform",
            "sla_minutes": 90,
            "escalation_after_minutes": 60,
            "runbook_id": "RB-DATA-FRESHNESS",
            "notification_mode": "READ_ONLY_NO_EXTERNAL_SEND",
        },
        {
            "route_id": "ROUTE-MODEL-RISK",
            "category": "MODEL_RISK_AGING",
            "queue": "Model Governance",
            "primary_owner": "Model Governance",
            "escalation_owner": "Chief Risk Officer",
            "sla_minutes": 240,
            "escalation_after_minutes": 180,
            "runbook_id": "RB-MODEL-RISK",
            "notification_mode": "READ_ONLY_NO_EXTERNAL_SEND",
        },
        {
            "route_id": "ROUTE-REVIEWER-SLA",
            "category": "REVIEWER_SLA",
            "queue": "Reviewer Coordination",
            "primary_owner": "Compliance",
            "escalation_owner": "Model Governance / Compliance",
            "sla_minutes": 60,
            "escalation_after_minutes": 45,
            "runbook_id": "RB-REVIEWER-SLA",
            "notification_mode": "READ_ONLY_NO_EXTERNAL_SEND",
        },
    ]
    route_by_category = {rule["category"]: rule for rule in routing_rules}
    runbook_steps = {
        "RB-ALPHA-DECAY": [
            "Freeze candidate promotion",
            "Compare latest IC drift against validation window",
            "Request alpha owner retirement or retraining decision",
            "Attach experiment and validation evidence",
        ],
        "RB-FEATURE-DRIFT": [
            "Freeze affected feature materialization",
            "Run point-in-time distribution audit",
            "Check source schema and lineage hash",
            "Attach feature quality evidence",
        ],
        "RB-DATA-FRESHNESS": [
            "Confirm upstream snapshot timestamp",
            "Rerun data quality checks",
            "Block dependent validation refresh until freshness is restored",
            "Attach dataset content hash",
        ],
        "RB-MODEL-RISK": [
            "Escalate independent model validation owner",
            "Confirm linked model limitations",
            "Update issue register remediation plan",
            "Require reviewer workflow task before any promotion",
        ],
        "RB-REVIEWER-SLA": [
            "Escalate overdue reviewer assignment",
            "Confirm blocking gate ownership",
            "Record reviewer decision requirement",
            "Keep Phase 3A authorization blocked",
        ],
    }
    runbooks = [
        {
            "runbook_id": rule["runbook_id"],
            "category": rule["category"],
            "title": f"{rule['category']} response playbook",
            "objective": "Contain research quality, governance, or operational drift before any promotion decision",
            "steps": runbook_steps[rule["runbook_id"]],
            "requires_postmortem": rule["category"] in {"MODEL_RISK_AGING", "REVIEWER_SLA"},
            "mutable_runbook": False,
        }
        for rule in routing_rules
    ]

    source_monitors = monitoring["top_breaches"] + monitoring["watchlist"]
    category_aliases = {"DATA_DISTRIBUTION_DRIFT": "FEATURE_DRIFT"}
    incidents = []
    timeline = []
    for idx, monitor in enumerate(source_monitors):
        rule = route_by_category[
            category_aliases.get(monitor["category"], monitor["category"])
        ]
        if monitor["status"] == "BREACH" and monitor["drift_score"] >= 0.5:
            severity = "CRITICAL"
        elif monitor["status"] == "BREACH":
            severity = "HIGH"
        else:
            severity = "MEDIUM"
        age_minutes = (
            45 + idx * 19 + (90 if monitor["status"] == "BREACH" else 0) + int(min(monitor["drift_score"], 1.0) * 60)
        )
        sla_breached = age_minutes > rule["sla_minutes"]
        if sla_breached and severity == "CRITICAL":
            state = "ESCALATED"
        elif sla_breached:
            state = "SLA_BREACHED"
        elif monitor["status"] == "BREACH":
            state = "TRIAGE"
        else:
            state = "WATCH_REVIEW"
        opened_at = monitor["last_checked"] + 90
        due_at = opened_at + rule["sla_minutes"] * 60
        escalation_level = "L3" if severity == "CRITICAL" else "L2" if monitor["status"] == "BREACH" else "L1"
        incident = {
            "incident_id": f"INC-{idx + 1:03d}",
            "source_monitor_id": monitor["monitor_id"],
            "category": monitor["category"],
            "subject_id": monitor["subject_id"],
            "severity": severity,
            "state": state,
            "trigger_status": monitor["status"],
            "trigger_metric": monitor["metric"],
            "drift_score": monitor["drift_score"],
            "owner": rule["primary_owner"],
            "incident_commander": rule["escalation_owner"]
            if escalation_level in {"L2", "L3"}
            else rule["primary_owner"],
            "queue": rule["queue"],
            "opened_at": opened_at,
            "due_at": due_at,
            "age_minutes": age_minutes,
            "sla_minutes": rule["sla_minutes"],
            "due_in_minutes": rule["sla_minutes"] - age_minutes,
            "sla_breached": sla_breached,
            "escalation_level": escalation_level,
            "route_id": rule["route_id"],
            "runbook_id": rule["runbook_id"],
            "evidence_ref": monitor["evidence_ref"],
            "evidence_packet_hash": _short_hash("incident", monitor["monitor_id"], monitor["evidence_ref"], severity),
            "containment_action": "Block candidate promotion and require reviewer evidence before any phase advancement",
            "next_action": monitor["remediation"],
            "external_alert_sent": False,
            "blocks_live_authorization": monitor["status"] == "BREACH"
            or monitor["category"] in {"MODEL_RISK_AGING", "REVIEWER_SLA"},
        }
        incidents.append(incident)
        timeline.extend(
            [
                {
                    "event_id": f"ITE-{idx + 1:03d}-01",
                    "incident_id": incident["incident_id"],
                    "timestamp": opened_at,
                    "event_type": "DETECTED",
                    "actor": "Monitoring & Drift",
                    "detail": f"{monitor['category']} {monitor['status']} trigger on {monitor['subject_id']}",
                    "evidence_ref": monitor["evidence_ref"],
                },
                {
                    "event_id": f"ITE-{idx + 1:03d}-02",
                    "incident_id": incident["incident_id"],
                    "timestamp": opened_at + 60,
                    "event_type": "ROUTED",
                    "actor": "Read-only incident router",
                    "detail": f"Routed to {rule['queue']} with {rule['notification_mode']}",
                    "evidence_ref": incident["evidence_packet_hash"],
                },
                {
                    "event_id": f"ITE-{idx + 1:03d}-03",
                    "incident_id": incident["incident_id"],
                    "timestamp": opened_at + 120,
                    "event_type": "SLA_EVALUATED",
                    "actor": "Incident Response",
                    "detail": f"SLA breached={sla_breached}; escalation={escalation_level}",
                    "evidence_ref": incident["evidence_packet_hash"],
                },
            ]
        )

    severity_counts = {
        "CRITICAL": sum(1 for incident in incidents if incident["severity"] == "CRITICAL"),
        "HIGH": sum(1 for incident in incidents if incident["severity"] == "HIGH"),
        "MEDIUM": sum(1 for incident in incidents if incident["severity"] == "MEDIUM"),
    }
    state_counts = {
        state: sum(1 for incident in incidents if incident["state"] == state)
        for state in sorted({incident["state"] for incident in incidents})
    }
    routing_counts = {
        route["category"]: sum(1 for incident in incidents if incident["category"] == route["category"])
        for route in routing_rules
    }
    return {
        "incident_mode": "READ_ONLY_INCIDENT_RESPONSE",
        "summary": {
            "incidents": len(incidents),
            "open_incidents": sum(1 for incident in incidents if incident["state"] != "CLOSED"),
            "critical": severity_counts["CRITICAL"],
            "high": severity_counts["HIGH"],
            "medium": severity_counts["MEDIUM"],
            "sla_breaches": sum(1 for incident in incidents if incident["sla_breached"]),
            "escalated": sum(1 for incident in incidents if incident["escalation_level"] in {"L2", "L3"}),
            "avg_age_minutes": round(statistics.mean(incident["age_minutes"] for incident in incidents), 1),
            "external_alerts_sent": 0,
            "live_authorization_allowed": False,
        },
        "severity_counts": severity_counts,
        "state_counts": state_counts,
        "routing_counts": routing_counts,
        "incidents": incidents,
        "routing_rules": routing_rules,
        "runbooks": runbooks,
        "timeline": timeline,
        "incident_policy": {
            "read_only": True,
            "requires_evidence_ref": True,
            "requires_sla": True,
            "requires_owner": True,
            "requires_postmortem_for_critical": True,
            "external_notifications_enabled": False,
            "mutable_incidents": False,
            "authorizes_live_trading": False,
        },
    }


router = APIRouter()


@router.get("/api/v1/console/incident-response")
def incident_response() -> Dict:
    payload = _incident_response_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }
