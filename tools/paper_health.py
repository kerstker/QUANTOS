"""Fail-closed health check for the L2 real-time paper workflow.

The scheduler can report a successful process exit while an old, incomplete,
or unsafe artifact is still on disk.  This small read-only operator checks the
semantic paper contract: fresh public marks, three reconciled paper accounts,
the three authoritative dry-run references, an idempotent paper preview, and
explicitly disabled live/order flags.  It never fetches data, reads
credentials, starts a service, or submits an order.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections.abc import Mapping
from typing import Any

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from tools.cache_io import atomic_write_json
from tools.paper_order_preview import _full_research_risk_approved


ALPHA_IDS = ("carry_oi_majors", "crowd_contra", "top_contra")
DEFAULT_REALTIME_STATUS = os.path.join(
    "data", "cache", "paper_realtime_status.json"
)
DEFAULT_ORDER_PREVIEW = os.path.join("data", "cache", "paper_order_preview.json")
DEFAULT_OUTPUT = os.path.join("data", "cache", "paper_health.json")
DEFAULT_FORWARD_TRACK_STATUS = os.path.join(
    "data", "cache", "forward_track_status.json"
)
DEFAULT_MAX_AGE_MS = 5 * 60 * 1000
RECONCILIATION_TOLERANCE = 1e-6


def _load_json(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _safe_age(now_ms: int, observed_at: object) -> tuple[int | None, str | None]:
    if isinstance(observed_at, bool) or not isinstance(observed_at, int):
        return None, "PAPER_MARK_TIMESTAMP_MISSING_OR_INVALID"
    age = now_ms - observed_at
    if age < 0:
        return age, "PAPER_MARK_TIMESTAMP_IN_FUTURE"
    return age, None


def _explicitly_false(value: object) -> bool:
    return value is False


def _forward_evidence_summary(status: Mapping[str, Any] | None) -> dict[str, Any]:
    """Summarize forward evidence without making it a paper-health blocker."""
    snapshot = status if isinstance(status, Mapping) else {}
    raw_alphas = snapshot.get("alphas")
    raw_alphas = raw_alphas if isinstance(raw_alphas, Mapping) else {}
    required = snapshot.get("evidence_gate_weeks", 8)
    try:
        required_periods = int(required)
    except (TypeError, ValueError):
        required_periods = 8
    rows: dict[str, dict[str, Any]] = {}
    for alpha_id in ALPHA_IDS:
        raw = raw_alphas.get(alpha_id)
        raw = raw if isinstance(raw, Mapping) else {}
        periods = raw.get("n_forward_periods")
        try:
            periods_value = int(periods) if periods is not None else None
        except (TypeError, ValueError):
            periods_value = None
        rows[alpha_id] = {
            "n_forward_periods": periods_value,
            "periods_to_gate": (
                max(0, required_periods - (periods_value or 0))
                if periods_value is not None
                else None
            ),
            "pending_decisions": raw.get("pending_decisions"),
            "attested_pending_decisions": raw.get("attested_pending_decisions"),
            "evidence_state": raw.get("evidence_state"),
            "paper_sharpe": raw.get("paper_sharpe"),
        }
    complete = bool(rows) and all(
        row["n_forward_periods"] is not None
        and row["n_forward_periods"] >= required_periods
        for row in rows.values()
    )
    return {
        "present": bool(status),
        "ran_at": snapshot.get("ran_at"),
        "evidence_gate_periods": required_periods,
        "complete_for_all_three": complete,
        "quality_evidence_current": snapshot.get("quality_evidence_current"),
        "data_quality_admission": (
            {
                "verdict": (snapshot.get("data_quality_admission") or {}).get("verdict"),
                "quality_verdict": (snapshot.get("data_quality_admission") or {}).get("quality_verdict"),
                "lineage_verdict": (snapshot.get("data_quality_admission") or {}).get("lineage_verdict"),
            }
            if isinstance(snapshot.get("data_quality_admission"), Mapping)
            else {}
        ),
        "alphas": rows,
        "alerts": snapshot.get("alerts", []) if isinstance(snapshot.get("alerts", []), list) else [],
    }


def _readiness_class(
    *,
    paper_ready: bool,
    paper_mark_ready: bool,
    preview_ready: bool,
    realtime_verdict: Mapping[str, Any],
    operator_mode: object,
) -> tuple[str, str]:
    """Return a machine-readable state and the only safe next action.

    ``paper_mark_ready`` is deliberately a weaker observation state: it can be
    true while the worker is replaying an immutable legacy ledger and while the
    complete nine-engine risk bundle is still pending.  Keeping this distinction
    in the health artifact prevents a UI/automation consumer from treating a
    mark cycle as an order-admission signal.
    """
    if paper_ready:
        return "PAPER_READY", "continue_forward_paper_track"
    if paper_mark_ready and operator_mode == "EXISTING_FILLED_PAPER_LEDGER_REPLAY":
        return "PAPER_MARKS_ONLY_REPLAY", "resolve_research_risk_and_create_new_decision"
    if paper_mark_ready and not preview_ready:
        return "PAPER_MARKS_READY_PENDING_RISK", "complete_full_nine_engine_risk_bundle"
    if paper_mark_ready:
        return "PAPER_MARKS_READY_PENDING_HEALTH", "inspect_health_blockers"
    if realtime_verdict.get("cycle_completed") is True:
        return "PAPER_CYCLE_BLOCKED", "inspect_health_blockers"
    return "PAPER_BLOCKED", "repair_realtime_inputs_and_rerun"


def evaluate_paper_health(
    realtime_status: Mapping[str, Any],
    order_preview: Mapping[str, Any],
    *,
    now_ms: int,
    max_age_ms: int = DEFAULT_MAX_AGE_MS,
    forward_track: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate paper artifacts without IO or mutation.

    The result is deliberately independent of the Windows scheduler.  A task
    may have exited with code zero and still fail this semantic contract.
    """
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer")
    if (
        isinstance(max_age_ms, bool)
        or not isinstance(max_age_ms, int)
        or max_age_ms <= 0
    ):
        raise ValueError("max_age_ms must be a positive integer")

    blockers: list[str] = []

    def block(code: str) -> None:
        if code not in blockers:
            blockers.append(code)

    realtime_verdict = (
        realtime_status.get("verdict")
        if isinstance(realtime_status.get("verdict"), Mapping)
        else {}
    )
    preview_verdict = (
        order_preview.get("verdict")
        if isinstance(order_preview.get("verdict"), Mapping)
        else {}
    )
    realtime_safety = (
        realtime_status.get("safety")
        if isinstance(realtime_status.get("safety"), Mapping)
        else {}
    )
    preview_safety = (
        order_preview.get("safety")
        if isinstance(order_preview.get("safety"), Mapping)
        else {}
    )

    if not realtime_status:
        block("PAPER_REALTIME_ARTIFACT_MISSING_OR_INVALID")
    if not order_preview:
        block("PAPER_ORDER_PREVIEW_ARTIFACT_MISSING_OR_INVALID")

    age_ms, age_error = _safe_age(now_ms, realtime_status.get("observed_at"))
    if age_error:
        block(age_error)
    elif age_ms is not None and age_ms > max_age_ms:
        block("PAPER_MARKS_STALE")

    if realtime_verdict.get("cycle_completed") is not True:
        block("PAPER_REALTIME_CYCLE_NOT_COMPLETE")
    if realtime_verdict.get("paper_mark_ready") is not True:
        block("PAPER_MARKS_NOT_READY")
    if realtime_verdict.get("backtest_reference_present_for_all_three") is not True:
        block("BACKTEST_REFERENCES_INCOMPLETE")
    if realtime_status.get("errors") != {}:
        block("PAPER_REALTIME_REPORTED_ERRORS")

    persistence = (
        realtime_status.get("persistence")
        if isinstance(realtime_status.get("persistence"), Mapping)
        else {}
    )
    if persistence.get("chain_valid") is not True:
        block("PAPER_REALTIME_CHAIN_INVALID")

    realtime_alphas = (
        realtime_status.get("alphas")
        if isinstance(realtime_status.get("alphas"), Mapping)
        else {}
    )
    if set(realtime_alphas) != set(ALPHA_IDS):
        block("PAPER_REALTIME_ALPHA_SET_INCOMPLETE")
    alpha_report: dict[str, dict[str, Any]] = {}
    for alpha_id in ALPHA_IDS:
        row = realtime_alphas.get(alpha_id)
        row = row if isinstance(row, Mapping) else {}
        residual = _finite_number(row.get("reconciliation_residual"))
        row_blockers: list[str] = []
        if row.get("status") != "PAPER_MARKED":
            row_blockers.append("PAPER_MARK_NOT_RECORDED")
        if not isinstance(row.get("n_fills"), int) or row.get("n_fills", 0) <= 0:
            row_blockers.append("PAPER_FILLS_MISSING")
        if residual is None or abs(residual) > RECONCILIATION_TOLERANCE:
            row_blockers.append("PAPER_RECONCILIATION_FAILED")
        if row_blockers:
            block(f"{alpha_id}:" + ",".join(row_blockers))
        alpha_report[alpha_id] = {
            "status": row.get("status"),
            "n_fills": row.get("n_fills"),
            "equity": row.get("equity"),
            "net_pnl": row.get("net_pnl"),
            "funding_pnl": row.get("funding_pnl"),
            "funding_events": row.get("funding_events"),
            "funding_data_complete": row.get("funding_data_complete"),
            "reconciliation_residual": residual,
            "blockers": row_blockers,
        }

    backtests = (
        realtime_status.get("backtests")
        if isinstance(realtime_status.get("backtests"), Mapping)
        else {}
    )
    if set(backtests) != set(ALPHA_IDS):
        block("BACKTEST_ALPHA_SET_INCOMPLETE")
    for alpha_id in ALPHA_IDS:
        row = backtests.get(alpha_id)
        row = row if isinstance(row, Mapping) else {}
        if row.get("provenance") != "REAL_AUTHORITATIVE_L2_DRY_RUN":
            block(f"{alpha_id}:BACKTEST_PROVENANCE_INVALID")

    if preview_verdict.get("paper_preview_ready") is not True:
        block("PAPER_ORDER_PREVIEW_NOT_READY")
    if order_preview.get("errors") != {}:
        block("PAPER_ORDER_PREVIEW_REPORTED_ERRORS")
    preview_alphas = (
        order_preview.get("alphas")
        if isinstance(order_preview.get("alphas"), Mapping)
        else {}
    )
    if set(preview_alphas) != set(ALPHA_IDS):
        block("PAPER_ORDER_ALPHA_SET_INCOMPLETE")
    preview_research_risk_complete = True
    research_risk_bundles: dict[str, dict[str, Any]] = {}
    for alpha_id in ALPHA_IDS:
        row = preview_alphas.get(alpha_id)
        row = row if isinstance(row, Mapping) else {}
        bundle = row.get("research_risk_bundle")
        bundle = bundle if isinstance(bundle, Mapping) else {}
        research_risk_bundles[alpha_id] = {
            "status": bundle.get("status", "NOT_PROVIDED"),
            "provenance": bundle.get("provenance"),
            "path": bundle.get("path"),
            "bundle_sha256": bundle.get("bundle_sha256"),
            "missing": bundle.get("missing", []),
            "reasons": bundle.get("reasons", []),
        }
        residual = _finite_number(row.get("reconciliation_residual"))
        if row.get("paper_execution") != "PAPER_FILLED":
            block(f"{alpha_id}:PAPER_EXECUTION_NOT_FILLED")
        if not (
            isinstance(row.get("n_intents"), int)
            and isinstance(row.get("n_approved"), int)
            and isinstance(row.get("n_paper_fills"), int)
            and row["n_intents"] == row["n_approved"] == row["n_paper_fills"]
            and row["n_intents"] > 0
        ):
            block(f"{alpha_id}:PAPER_INTENT_FILL_COUNT_MISMATCH")
        if row.get("n_rejected") != 0:
            block(f"{alpha_id}:PAPER_ORDER_REJECTED")
        if residual is None or abs(residual) > RECONCILIATION_TOLERANCE:
            block(f"{alpha_id}:PAPER_ORDER_RECONCILIATION_FAILED")
        if not _full_research_risk_approved(row.get("research_risk")):
            preview_research_risk_complete = False
            block(f"{alpha_id}:PAPER_RESEARCH_RISK_NOT_FULL")

    if realtime_safety.get("paper_only") is not True:
        block("REALTIME_PAPER_ONLY_FLAG_NOT_TRUE")
    if realtime_safety.get("public_market_data_only") is not True:
        block("REALTIME_PUBLIC_MARKET_DATA_FLAG_NOT_TRUE")
    if preview_safety.get("paper_only") is not True:
        block("PREVIEW_PAPER_ONLY_FLAG_NOT_TRUE")
    for name in (
        "live_allowed",
        "order_submission_allowed",
    ):
        if not _explicitly_false(realtime_verdict.get(name)):
            block(f"REALTIME_{name.upper()}_NOT_FALSE")
        if not _explicitly_false(preview_verdict.get(name)):
            block(f"PREVIEW_{name.upper()}_NOT_FALSE")
    for source, safety in (("REALTIME", realtime_safety), ("PREVIEW", preview_safety)):
        for name in (
            "broker_connectivity",
            "broker_order_submission",
            "credentials_accessed",
            "private_binance_access",
        ):
            if safety.get(name) is not False:
                block(f"{source}_{name.upper()}_ENABLED")

    ready = not blockers
    forward_evidence = _forward_evidence_summary(forward_track)
    research_risk_bundle_complete = bool(research_risk_bundles) and all(
        row.get("status") == "READY" for row in research_risk_bundles.values()
    )
    funding_complete = bool(alpha_report) and all(
        row.get("funding_data_complete") is True for row in alpha_report.values()
    )
    paper_mark_ready = realtime_verdict.get("paper_mark_ready") is True
    paper_preview_ready = (
        preview_verdict.get("paper_preview_ready") is True
        and preview_research_risk_complete
    )
    readiness_class, next_action = _readiness_class(
        paper_ready=ready,
        paper_mark_ready=paper_mark_ready,
        preview_ready=paper_preview_ready,
        realtime_verdict=realtime_verdict,
        operator_mode=realtime_status.get("operator_mode"),
    )
    return {
        "schema_version": 1,
        "flow": "PAPER_HEALTH_CHECK",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at": now_ms,
        "observed_at": realtime_status.get("observed_at"),
        "age_ms": age_ms,
        "max_age_ms": max_age_ms,
        "verdict": {
            "paper_ready": ready,
            "paper_mark_ready": paper_mark_ready,
            "paper_preview_ready": paper_preview_ready,
            "paper_research_risk_complete": preview_research_risk_complete,
            "paper_research_risk_bundle_complete": research_risk_bundle_complete,
            "backtest_reference_present_for_all_three": (
                realtime_verdict.get("backtest_reference_present_for_all_three") is True
            ),
            "funding_data_complete_for_all_three": funding_complete,
            "live_allowed": False,
            "order_submission_allowed": False,
            "human_review_required": True,
        },
        "readiness_class": readiness_class,
        "next_action": next_action,
        "alphas": alpha_report,
        "research_risk_bundles": research_risk_bundles,
        "research_risk_bundle_complete": research_risk_bundle_complete,
        "forward_evidence": forward_evidence,
        "blockers": blockers,
        "safety": {
            "paper_only": True,
            "public_market_data_only": True,
            "broker_connectivity": False,
            "credentials_accessed": False,
            "broker_order_submission": False,
        },
    }


def build_report(
    *,
    realtime_status_path: str = DEFAULT_REALTIME_STATUS,
    order_preview_path: str = DEFAULT_ORDER_PREVIEW,
    forward_track_path: str = DEFAULT_FORWARD_TRACK_STATUS,
    output_path: str = DEFAULT_OUTPUT,
    now_ms: int | None = None,
    max_age_ms: int = DEFAULT_MAX_AGE_MS,
) -> dict[str, Any]:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    report = evaluate_paper_health(
        _load_json(realtime_status_path),
        _load_json(order_preview_path),
        now_ms=now,
        max_age_ms=max_age_ms,
        forward_track=_load_json(forward_track_path),
    )
    report["sources"] = {
        "realtime_status": realtime_status_path,
        "order_preview": order_preview_path,
        "forward_track": forward_track_path,
    }
    atomic_write_json(output_path, report, indent=2)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--realtime-status", default=DEFAULT_REALTIME_STATUS)
    parser.add_argument("--order-preview", default=DEFAULT_ORDER_PREVIEW)
    parser.add_argument("--forward-track", default=DEFAULT_FORWARD_TRACK_STATUS)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--max-age-ms", type=int, default=DEFAULT_MAX_AGE_MS)
    args = parser.parse_args(argv)
    try:
        report = build_report(
            realtime_status_path=args.realtime_status,
            order_preview_path=args.order_preview,
            forward_track_path=args.forward_track,
            output_path=args.output,
            max_age_ms=args.max_age_ms,
        )
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"paper_ready": False, "error": str(exc)[:240]}))
        return SCHEDULE_EXIT_FAILURE
    verdict = report["verdict"]
    print(
        json.dumps(
            {
                "paper_ready": verdict["paper_ready"],
                "age_ms": report["age_ms"],
                "blockers": report["blockers"],
                "forward_evidence_complete": report["forward_evidence"]["complete_for_all_three"],
                "live_allowed": False,
                "order_submission_allowed": False,
            },
            ensure_ascii=False,
        )
    )
    print(f"-> {args.output}")
    return (
        SCHEDULE_EXIT_SUCCESS
        if verdict["paper_ready"]
        else SCHEDULE_EXIT_SAFE_BLOCKED
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ALPHA_IDS",
    "DEFAULT_MAX_AGE_MS",
    "DEFAULT_FORWARD_TRACK_STATUS",
    "DEFAULT_OUTPUT",
    "build_report",
    "evaluate_paper_health",
]
