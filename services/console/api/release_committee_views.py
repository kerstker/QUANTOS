"""Read-only release-committee HTTP route."""

from __future__ import annotations

from typing import Dict

from fastapi import APIRouter

from services.console.api.common import short_hash
from services.console.api.model_evidence_views import (
    _evidence_ledger_payload,
)
from services.console.api.promotion_views import _promotion_control_payload
from services.console.api.review_access_views import (
    _access_control_payload,
    _reviewer_workflow_payload,
)
from services.console.api.system_views import _phase3a_readiness_payload


_short_hash = short_hash


def _release_committee_payload() -> Dict:
    promotion = _promotion_control_payload()
    reviewer_workflow = _reviewer_workflow_payload()
    evidence_ledger = _evidence_ledger_payload()
    access_control = _access_control_payload()
    phase3a = _phase3a_readiness_payload()["readiness"]

    seats = [
        {
            "seat_id": "RC-CHAIR",
            "role": "Committee Chair",
            "required": True,
            "member": "CTO Delegate",
            "domain": "Research Governance",
            "vote_weight": 1.0,
        },
        {
            "seat_id": "RC-RISK",
            "role": "Independent Risk",
            "required": True,
            "member": "Risk Reviewer",
            "domain": "Risk",
            "vote_weight": 1.0,
        },
        {
            "seat_id": "RC-MODEL",
            "role": "Model Validation",
            "required": True,
            "member": "Model Validator",
            "domain": "Model Risk",
            "vote_weight": 1.0,
        },
        {
            "seat_id": "RC-DATA",
            "role": "Data Governance",
            "required": True,
            "member": "Data Steward",
            "domain": "Data",
            "vote_weight": 1.0,
        },
        {
            "seat_id": "RC-COMPLIANCE",
            "role": "Compliance",
            "required": True,
            "member": "Compliance Reviewer",
            "domain": "Compliance",
            "vote_weight": 1.0,
        },
        {
            "seat_id": "RC-PLATFORM",
            "role": "Research Platform",
            "required": False,
            "member": "Platform Reviewer",
            "domain": "Platform",
            "vote_weight": 0.5,
        },
    ]
    agenda = []
    votes = []
    required_attestations = [
        {
            "attestation_id": "ATT-VALIDATION",
            "name": "Independent validation evidence complete",
            "owner_role": "Model Validation",
            "status": "MISSING",
            "evidence_ref": "MRM-I01",
            "blocks_release": True,
        },
        {
            "attestation_id": "ATT-RISK",
            "name": "Risk challenge complete",
            "owner_role": "Independent Risk",
            "status": "MISSING",
            "evidence_ref": "EV-SCENARIO-STRESS-001",
            "blocks_release": True,
        },
        {
            "attestation_id": "ATT-DATA",
            "name": "Point-in-time data lineage reviewed",
            "owner_role": "Data Governance",
            "status": "MISSING",
            "evidence_ref": "MRM-I02",
            "blocks_release": True,
        },
        {
            "attestation_id": "ATT-COMPLIANCE",
            "name": "Phase 3A blockers closed",
            "owner_role": "Compliance",
            "status": "BLOCKED",
            "evidence_ref": "EV-PHASE3A-001",
            "blocks_release": True,
        },
        {
            "attestation_id": "ATT-ACCESS",
            "name": "Access and export controls verified",
            "owner_role": "Security / Compliance",
            "status": "READY_FOR_REVIEW",
            "evidence_ref": _short_hash("access-review", access_control["summary"]["permission_rows"]),
            "blocks_release": False,
        },
        {
            "attestation_id": "ATT-LEDGER",
            "name": "Evidence root hash verified",
            "owner_role": "Research Platform",
            "status": "READY_FOR_REVIEW",
            "evidence_ref": evidence_ledger["root_hash"],
            "blocks_release": False,
        },
    ]
    blocking_attestations = [
        item for item in required_attestations if item["blocks_release"] and item["status"] != "SIGNED"
    ]
    open_blocking_tasks = [
        task
        for task in reviewer_workflow["tasks"]
        if task["blocks_live_authorization"] and task["state"] != "SIGNED_OFF"
    ]

    for idx, record in enumerate(promotion["promotions"]):
        gate_failures = record["gates_failed"]
        blocker_count = len(record["blocking_gates"]) + len(blocking_attestations)
        if record["decision"] == "BLOCKED" or blocker_count > 0 or phase3a["decision"] == "NO_GO":
            recommendation = "NO_GO"
            release_decision = "BLOCKED"
        elif record["decision"].startswith("APPROVED_FOR_DRY_RUN"):
            recommendation = "DRY_RUN_ONLY"
            release_decision = "APPROVED_FOR_DRY_RUN_ONLY"
        else:
            recommendation = "HOLD"
            release_decision = "PENDING_REVIEW"
        agenda_item = {
            "agenda_id": f"RC-AGENDA-{idx + 1:03d}",
            "promotion_id": record["promotion_id"],
            "alpha_id": record["alpha_id"],
            "requested_stage": record["requested_stage"],
            "recommendation": recommendation,
            "release_decision": release_decision,
            "readiness_score": record["readiness_score"],
            "gate_failures": gate_failures,
            "watch_gates": record["gates_watch"],
            "blocking_attestations": len(blocking_attestations),
            "open_blocking_tasks": len(open_blocking_tasks),
            "evidence_package_hash": _short_hash(
                "committee", record["candidate_hash"], evidence_ledger["root_hash"], recommendation
            ),
            "reason": record["decision_reason"]
            if recommendation != "DRY_RUN_ONLY"
            else "Dry-run replay may continue; release committee blocks broker/live progression",
            "next_action": "Close blocker attestations and reviewer tasks before committee can vote GO",
            "broker_test_env_allowed": False,
            "live_release_allowed": False,
        }
        agenda.append(agenda_item)
        for seat_idx, seat in enumerate(seats):
            if recommendation == "NO_GO":
                vote = "REJECT"
                rationale = "Blocking gates, attestations, or Phase 3A NO_GO prevent release"
            elif seat["required"]:
                vote = "ABSTAIN"
                rationale = "Required signed attestation is not persisted"
            else:
                vote = "OBSERVE"
                rationale = "Non-voting observer seat"
            votes.append(
                {
                    "vote_id": f"RC-VOTE-{idx + 1:03d}-{seat_idx + 1:02d}",
                    "agenda_id": agenda_item["agenda_id"],
                    "seat_id": seat["seat_id"],
                    "role": seat["role"],
                    "vote": vote,
                    "rationale": rationale,
                    "signed": False,
                    "signature_hash": None,
                    "vote_weight": seat["vote_weight"],
                }
            )

    required_seats = [seat for seat in seats if seat["required"]]
    signed_required_votes = [
        vote
        for vote in votes
        if vote["signed"] and any(seat["seat_id"] == vote["seat_id"] and seat["required"] for seat in seats)
    ]
    quorum_required = len(required_seats)
    quorum_present = len({vote["seat_id"] for vote in signed_required_votes})
    reject_votes = sum(1 for vote in votes if vote["vote"] == "REJECT")
    abstain_votes = sum(1 for vote in votes if vote["vote"] == "ABSTAIN")
    no_go_items = sum(1 for item in agenda if item["recommendation"] == "NO_GO")
    return {
        "committee_mode": "READ_ONLY_RELEASE_COMMITTEE",
        "meeting_id": "RC-2026W24-DRYRUN-READONLY",
        "current_level": "L2_DRY_RUN_ONLY",
        "phase3a_decision": "NO_GO",
        "committee_decision": "NO_GO",
        "summary": {
            "agenda_items": len(agenda),
            "no_go_items": no_go_items,
            "dry_run_only_items": sum(1 for item in agenda if item["recommendation"] == "DRY_RUN_ONLY"),
            "required_seats": len(required_seats),
            "quorum_required": quorum_required,
            "quorum_present": quorum_present,
            "quorum_met": quorum_present >= quorum_required,
            "votes": len(votes),
            "reject_votes": reject_votes,
            "abstain_votes": abstain_votes,
            "signed_votes": sum(1 for vote in votes if vote["signed"]),
            "blocking_attestations": len(blocking_attestations),
            "open_blocking_tasks": len(open_blocking_tasks),
            "broker_test_env_allowed": 0,
            "live_release_allowed": 0,
        },
        "seats": seats,
        "agenda": agenda,
        "votes": votes,
        "required_attestations": required_attestations,
        "blockers": [
            {
                "blocker_id": f"RC-BLOCK-{idx + 1:03d}",
                "source": "ATTESTATION" if idx < len(blocking_attestations) else "REVIEWER_WORKFLOW",
                "ref": item["attestation_id"] if idx < len(blocking_attestations) else item["task_id"],
                "owner": item["owner_role"] if idx < len(blocking_attestations) else item["assigned_reviewer"],
                "reason": item["name"] if idx < len(blocking_attestations) else item["title"],
                "blocks_release": True,
            }
            for idx, item in enumerate(blocking_attestations + open_blocking_tasks)
        ],
        "committee_policy": {
            "read_only": True,
            "requires_quorum": True,
            "requires_required_seat_signatures": True,
            "requires_signed_attestations": True,
            "requires_promotion_go": True,
            "requires_phase3a_go": True,
            "mutable_votes": False,
            "external_signing_enabled": False,
            "broker_test_env_enabled": False,
            "live_release_enabled": False,
            "authorizes_live_trading": False,
        },
    }


router = APIRouter()


@router.get("/api/v1/console/release-committee")
async def release_committee() -> Dict:
    payload = _release_committee_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }
