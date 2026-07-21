"""Produce a fail-closed, read-only report of the remaining live-trading gates.

This is an operator checklist, not an authorization mechanism.  It proves that
the current repository is still L2 and records the evidence that would have to
change in a separately approved post-L2 program.  It never flips safety flags,
loads credentials, constructs a live broker, or submits an order.
"""
from __future__ import annotations

import json
import os
from typing import Any

from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS
from services.deployment.gate import PLATFORM_READINESS
from tools.cache_io import atomic_write_json


OUTPUT = os.path.join("evidence", "live_readiness_report.json")
REQUIRED_FORWARD_PERIODS = 8
FORWARD_ALPHA_IDS = ("carry_oi_majors", "crowd_contra", "top_contra")


def _load(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def build_report() -> dict[str, Any]:
    cycle = _load("evidence/alpha_paper_cycle.json")
    # The cycle artifact is a research summary and can legitimately lag the
    # append-only forward ledger.  Read the official driver's current status
    # first so this checklist never reports an older 0/8 clock after a real
    # paper period has been recorded.  The cycle remains a CI/recovery
    # fallback when no runtime status exists.
    forward_status = _load("data/cache/forward_track_status.json")
    top_tier = _load("evidence/top_tier_assessment.json")
    identity = _load("evidence/strong_identity_baseline.json")
    admission = _load("evidence/data_quality_admission.json")
    event_consistency = _load("data/cache/domain_event_consistency.json")
    event_health = _load("data/cache/domain_event_health.json")
    projection_recovery = _load(
        "data/cache/domain_event_projection_consumer_recovery.json"
    )
    gonogo = _load("data/cache/gonogo_report.json")

    safety = RESEARCH_SAFE_DEFAULTS
    safety_ok = (
        not safety.live_trading_enabled
        and not safety.real_broker_enabled
        and not safety.allow_order_submission
        and not safety.allow_ai_direct_trading
        and safety.require_human_approval
    )
    status_rows = (
        forward_status.get("alphas")
        if isinstance(forward_status.get("alphas"), dict)
        else {}
    )
    cycle_rows = (
        cycle.get("alphas") if isinstance(cycle.get("alphas"), dict) else {}
    )
    if status_rows:
        forward_source = "data/cache/forward_track_status.json"
        forward_periods = {
            alpha_id: (
                status_rows.get(alpha_id, {}).get("n_forward_periods")
                if isinstance(status_rows.get(alpha_id), dict)
                else None
            )
            for alpha_id in FORWARD_ALPHA_IDS
        }
        pending_decisions = {
            alpha_id: (
                status_rows.get(alpha_id, {}).get("pending_decisions")
                if isinstance(status_rows.get(alpha_id), dict)
                else None
            )
            for alpha_id in FORWARD_ALPHA_IDS
        }
        forward_ran_at = forward_status.get("ran_at")
    else:
        forward_source = "evidence/alpha_paper_cycle.json"
        forward_periods = {
            alpha_id: (
                (cycle_rows.get(alpha_id, {}).get("forward") or {}).get(
                    "n_forward_periods"
                )
                if isinstance(cycle_rows.get(alpha_id), dict)
                else None
            )
            for alpha_id in FORWARD_ALPHA_IDS
        }
        pending_decisions = {
            alpha_id: (
                (cycle_rows.get(alpha_id, {}).get("forward") or {}).get(
                    "pending_decisions"
                )
                if isinstance(cycle_rows.get(alpha_id), dict)
                else None
            )
            for alpha_id in FORWARD_ALPHA_IDS
        }
        forward_ran_at = cycle.get("generated_at")
    forward_ok = bool(forward_periods) and all(
        isinstance(periods, int) and periods >= REQUIRED_FORWARD_PERIODS
        for periods in forward_periods.values()
    )

    comparison = event_consistency.get("comparison")
    comparison = comparison if isinstance(comparison, dict) else {}
    projection_exact = (
        isinstance(comparison.get("prefix_mismatch_count"), int)
        and comparison.get("prefix_mismatch_count") == 0
        and comparison.get("authority_event_records")
        == comparison.get("projection_event_records")
        and comparison.get("consumer_offset")
        == comparison.get("authority_max_seq")
    )
    recovery_preflight = projection_recovery.get("preflight")
    recovery_preflight = (
        recovery_preflight if isinstance(recovery_preflight, dict) else {}
    )
    recovery_status = (
        "PROMOTED_AND_RECOVERED"
        if projection_recovery.get("verdict") == "PASS"
        and recovery_preflight.get("verdict") == "PASS"
        and projection_exact
        else "REQUIRES_REVIEW"
    )
    consumer_evidence = (event_consistency.get("authority") or {}).get(
        "consumer", {}
    )
    historical_failures = int(
        consumer_evidence.get(
            "historical_failed_deliveries",
            consumer_evidence.get("failed_deliveries", 0),
        )
        or 0
    )

    checks = {
        "safety_flags": {
            "status": "PASS" if safety_ok else "BLOCK",
            "readiness": "L2_DRY_RUN_ONLY",
            "live_trading_armed": bool(safety.live_trading_armed),
        },
        "platform_readiness": {
            "status": "BLOCK",
            "current": PLATFORM_READINESS.value,
            "required_for_live": "L4_LIVE_APPROVED",
        },
        "alpha_forward_clock": {
            "status": "PASS" if forward_ok else "BLOCK",
            "required_periods_per_alpha": REQUIRED_FORWARD_PERIODS,
            "realized_periods": forward_periods,
            "pending_decisions": pending_decisions,
            "source": forward_source,
            "ran_at": forward_ran_at,
        },
        "data_admission": {
            "status": admission.get("verdict", "MISSING"),
            "quality": admission.get("quality_verdict"),
            "lineage": admission.get("lineage_verdict"),
            "block_reasons": (admission.get("block_reasons") or [])[:8],
        },
        "event_authority": {
            # Keep the live gate fail-closed while distinguishing a current
            # projection mismatch from retained historical delivery failures.
            "status": "PASS"
            if event_consistency.get("verdict") == "PASS"
            and event_health.get("verdict") == "PASS"
            else "BLOCK",
            "consistency": event_consistency.get("verdict", "MISSING"),
            "health": event_health.get("verdict", "MISSING"),
            "projection_rebuild": recovery_status,
            "projection_exact": projection_exact,
            "historical_failed_deliveries": historical_failures,
        },
        "go_no_go": {
            "status": "PASS" if (gonogo.get("forward_evidence") or {}).get("present") is True else "BLOCK",
            "forward_evidence": gonogo.get("forward_evidence"),
        },
        "strong_identity": {
            "status": identity.get("verdict", "MISSING"),
            "external_authority": identity.get("external_authority"),
        },
        "top_tier": {
            "status": top_tier.get("verdict", "MISSING"),
            "summary": top_tier.get("summary"),
        },
        "real_broker_adapter": {
            "status": "POST_L2_NOT_IMPLEMENTED",
            "live_order_submission": False,
            "note": "only the structural rejecting seam exists; no broker SDK or credentials are present",
        },
        "process_isolation_and_signing": {
            "status": "POST_L2_NOT_IMPLEMENTED",
            "note": "out-of-process signing authority and deployment isolation are design seams, not live authorization",
        },
    }
    blockers = ["L2_READINESS_PINNED"]
    if checks["real_broker_adapter"]["live_order_submission"] is False:
        blockers.append("REAL_BROKER_ADAPTER_AND_ORDER_PATH_MISSING")
    if checks["strong_identity"]["status"] != "PASS":
        blockers.append("MULTI_PARTY_EXTERNAL_APPROVAL_AND_STRONG_IDENTITY_NOT_COMPLETE")
    if checks["process_isolation_and_signing"]["status"] != "PASS":
        blockers.append("PROCESS_ISOLATION_AND_OUT_OF_PROCESS_SIGNING_NOT_DEPLOYED")
    if not forward_ok:
        blockers.append("FORWARD_PAPER_EVIDENCE_NOT_COMPLETE")
    if checks["data_admission"]["status"] != "PASS":
        blockers.append("DATA_ADMISSION_NOT_PASS")
    if checks["event_authority"]["status"] != "PASS":
        blockers.append("EVENT_AUTHORITY_NOT_HEALTHY_OR_CONSISTENT")
    if checks["go_no_go"]["status"] != "PASS":
        blockers.append("GONOGO_FORWARD_EVIDENCE_ABSENT")
    if checks["top_tier"]["status"] != "TOP_TIER":
        blockers.append("TOP_TIER_RED_LINES_OPEN")
    next_safe_actions = ["keep the three candidates in the official forward-paper clock"]
    if checks["event_authority"]["status"] != "PASS":
        next_safe_actions.append(
            "resolve retained event delivery dead letters and keep the projection recovery evidence attached to the authority head"
        )
    if checks["data_admission"]["status"] != "PASS":
        next_safe_actions.append(
            "restore a clean recent-data admission with matching raw-lineage backfill"
        )
    next_safe_actions.append(
        "complete the missing external identity, supply-chain, broker, process-isolation, compliance, and capital-route controls in a separately approved post-L2 program"
    )
    return {
        "schema_version": 1,
        "report_kind": "READ_ONLY_LIVE_READINESS_CHECKLIST",
        "verdict": "BLOCKED_POST_L2_REQUIREMENTS",
        "live_allowed": False,
        "order_submission_allowed": False,
        "readiness": "L2_DRY_RUN_ONLY",
        "checks": checks,
        "blockers": blockers,
        "next_safe_actions": next_safe_actions,
    }


def main() -> None:
    report = build_report()
    atomic_write_json(OUTPUT, report, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"-> {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
