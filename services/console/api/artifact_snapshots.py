"""Read-only HTTP routes backed by offline research artifact snapshots."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import time
from typing import Any, Dict

from fastapi import APIRouter

from services.execution.dry_run_store import DryRunStore
from .artifact_diagnostic_views import router as diagnostic_router


router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
IC_PANEL_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "ic_panel_snapshot.json")
_DRY_RUN_DB = os.path.join(_REPO_ROOT, "data", "cache", "dry_run.db")
_CAPACITY_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "capacity_snapshot.json")
_MACRO_REGIME_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "macro_regime_snapshot.json")
_COMBO_BOOK_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "combo_book_snapshot.json")
_LANCE_1M_VALIDATION_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "lance_1m_validation.json")
_FORWARD_EVIDENCE_BASELINE = os.path.join(_REPO_ROOT, "evidence", "forward_evidence_baseline.json")
_LIVE_TRACK_STATUS = os.path.join(_REPO_ROOT, "data", "cache", "live_track_status.json")
_LIVE_RISK_STATE = os.path.join(_REPO_ROOT, "data", "cache", "live_risk_state.json")
_GONOGO_REPORT = os.path.join(_REPO_ROOT, "data", "cache", "gonogo_report.json")
_FORWARD_TRACK_STATUS = os.path.join(
    _REPO_ROOT, "data", "cache", "forward_track_status.json"
)
_LIVE_REFRESH_STATUS = os.path.join(
    _REPO_ROOT, "data", "cache", "live_refresh_status.json"
)
_SCHEDULER_WATCHDOG_STATUS = os.path.join(
    _REPO_ROOT, "data", "cache", "scheduler_watchdog_status.json"
)
_PAPER_ORDER_PREVIEW = os.path.join(
    _REPO_ROOT, "data", "cache", "paper_order_preview.json"
)
_L2_PAPER_OPERATOR = os.path.join(
    _REPO_ROOT, "data", "cache", "l2_paper_operator.json"
)
_PAPER_REALTIME = os.path.join(
    _REPO_ROOT, "data", "cache", "paper_realtime_status.json"
)
_PAPER_HEALTH = os.path.join(_REPO_ROOT, "data", "cache", "paper_health.json")
_PAPER_10K_REALTIME = os.path.join(
    _REPO_ROOT, "data", "cache", "paper_realtime_10k_status.json"
)
_PAPER_10K_HEALTH = os.path.join(
    _REPO_ROOT, "data", "cache", "paper_health_10k.json"
)
_PAPER_SHADOW_AUTO = os.path.join(
    _REPO_ROOT, "data", "cache", "paper_shadow_auto.json"
)
_PAPER_SHADOW_REALTIME = os.path.join(
    _REPO_ROOT, "data", "cache", "paper_shadow_realtime_status.json"
)
_EVIDENCE_GATE_WEEKS = 8

# The compact diagnostic routes live in their own bounded module.  Including
# the child router keeps one public artifact surface while preserving the
# read-only, GET-only boundary checked by governance.
router.routes.extend(diagnostic_router.routes)


def _load_json_snapshot(path: str) -> Any:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None
    return None


def _load_json_object(path: str) -> Dict | None:
    snapshot = _load_json_snapshot(path)
    return snapshot if isinstance(snapshot, dict) else None


def _paper_alpha_total(alphas: Any, field: str) -> float | None:
    """Return an all-rows paper total, or ``None`` when evidence is incomplete."""
    if not isinstance(alphas, dict) or not alphas:
        return None
    values: list[float] = []
    for row in alphas.values():
        if not isinstance(row, dict):
            return None
        value = row.get(field)
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        values.append(number)
    return round(sum(values), 8)


def _forward_track_payload() -> Dict:
    """Return the real forward-paper clock as a compact read-only snapshot.

    The mark-to-market paper account can be healthy before a candidate has
    accumulated enough out-of-sample periods for promotion evidence.  Keep
    those states separate on the wire so the console never implies that a
    paper-ready account is forward-evidence-complete.
    """
    snapshot = _load_json_object(_FORWARD_TRACK_STATUS)
    if not snapshot:
        return {
            "present": False,
            "evidence_gate_weeks": _EVIDENCE_GATE_WEEKS,
            "alphas": [],
            "alerts": [],
        }
    raw_alphas = snapshot.get("alphas", {})
    alphas = raw_alphas if isinstance(raw_alphas, dict) else {}
    rows = []
    for alpha_id, row in sorted(alphas.items()):
        item = row if isinstance(row, dict) else {}
        periods = item.get("n_forward_periods")
        try:
            periods_int = int(periods) if periods is not None else None
        except (TypeError, ValueError):
            periods_int = None
        rows.append(
            {
                "alpha_id": str(alpha_id),
                "n_forward_periods": periods_int,
                "weeks_to_go_no_go": max(
                    0,
                    _EVIDENCE_GATE_WEEKS - (periods_int or 0),
                ) if periods_int is not None else None,
                "paper_sharpe": item.get("paper_sharpe"),
                "paper_ic": item.get("paper_ic"),
                "cum_return": item.get("cum_return"),
                "backtest_reference_sharpe": item.get("backtest_reference_sharpe"),
                "reference_weeks": item.get("reference_weeks"),
                "pending_decisions": item.get("pending_decisions"),
                "attested_pending_decisions": item.get("attested_pending_decisions"),
                "evidence_state": item.get("evidence_state"),
                "last_decision_ts": item.get("last_decision_ts"),
            }
        )
    admission = snapshot.get("data_quality_admission")
    admission = admission if isinstance(admission, dict) else {}
    return {
        "present": True,
        "ran_at": snapshot.get("ran_at"),
        "staleness_days": snapshot.get("staleness_days", {}),
        "evidence_gate_weeks": _EVIDENCE_GATE_WEEKS,
        "quality_evidence_current": snapshot.get("quality_evidence_current"),
        "data_quality_admission": {
            "verdict": admission.get("verdict"),
            "quality_verdict": admission.get("quality_verdict"),
            "lineage_verdict": admission.get("lineage_verdict"),
        },
        "alphas": rows,
        "alerts": snapshot.get("alerts", []) if isinstance(snapshot.get("alerts", []), list) else [],
    }


def _data_refresh_payload() -> Dict:
    """Expose the latest public-data refresh as a compact read-only matrix.

    The console must show the real ingestion outcome and source-level failures,
    not infer freshness from the paper account alone.  Only redacted counts,
    timestamps, watermarks and status hashes are surfaced; no raw response or
    credential material is returned.
    """
    snapshot = _load_json_object(_LIVE_REFRESH_STATUS)
    if not snapshot:
        return {
            "snapshot_present": False,
            "sources": [],
            "alerts": [],
        }
    reports = snapshot.get("reports", [])
    sources: list[dict[str, Any]] = []
    for report in reports if isinstance(reports, list) else []:
        if not isinstance(report, dict):
            continue
        failures = report.get("failed", [])
        failed_tickers = report.get("failed_tickers", [])
        sources.append(
            {
                "source": str(report.get("source", "unknown")),
                "outcome": report.get("outcome"),
                "duration_ms": report.get("duration_ms"),
                "refreshed": report.get("refreshed"),
                "skipped": report.get("skipped"),
                "new_days": report.get("new_days"),
                "ingested_rows": report.get("ingested_rows"),
                "failed_count": len(failures) if isinstance(failures, list) else 0,
                "failed_tickers": (
                    [str(item) for item in failed_tickers]
                    if isinstance(failed_tickers, list)
                    else []
                ),
                "watermark_after": report.get("watermark_after"),
            }
        )
    history = snapshot.get("history") if isinstance(snapshot.get("history"), dict) else {}
    slo = snapshot.get("slo") if isinstance(snapshot.get("slo"), dict) else {}
    raw_lineage = snapshot.get("raw_lineage")
    raw_lineage = raw_lineage if isinstance(raw_lineage, dict) else {}
    delivery = snapshot.get("alert_delivery")
    delivery = delivery if isinstance(delivery, dict) else {}
    alerts = snapshot.get("alerts")
    if not isinstance(alerts, list):
        alerts = delivery.get("alerts", [])
    return {
        "snapshot_present": True,
        "run_id": snapshot.get("run_id"),
        "ran_at": snapshot.get("ran_at"),
        "outcome": snapshot.get("outcome"),
        "duration_ms": snapshot.get("duration_ms"),
        "failed_total": snapshot.get("failed_total"),
        "failed_ticker_total": snapshot.get("failed_ticker_total"),
        "sources": sources,
        "staleness_after": snapshot.get("staleness_after", {}),
        "raw_lineage": {
            "status": raw_lineage.get("status"),
            "datasets": raw_lineage.get("datasets", {}),
        },
        "history": {
            "chain_valid": history.get("chain_valid"),
            "run_count": history.get("run_count"),
        },
        "slo": {
            "verdict": slo.get("verdict"),
            "violations": slo.get("violations", []),
        },
        "alerts": [str(item) for item in alerts if isinstance(item, (str, int, float))],
    }


def _scheduler_watchdog_payload() -> Dict:
    """Expose the L2 task watchdog without exposing command lines or secrets.

    Task scheduling is part of the paper-data control loop: a healthy mark
    snapshot is not enough if its refresh worker is disabled or stale.  The
    watchdog artifact is already redacted at write time; this view keeps only
    task names, status codes and bounded human-readable details for the
    read-only console.
    """
    snapshot = _load_json_object(_SCHEDULER_WATCHDOG_STATUS)
    if not snapshot:
        return {
            "snapshot_present": False,
            "verdict": "NO_SNAPSHOT",
            "summary": {},
            "checks": [],
        }
    checks = snapshot.get("checks", [])
    rows: list[dict[str, Any]] = []
    if isinstance(checks, list):
        for check in checks:
            if not isinstance(check, dict):
                continue
            task_name = check.get("task_name")
            if not isinstance(task_name, str) or not task_name:
                continue
            detail = check.get("detail")
            rows.append(
                {
                    "task_name": task_name,
                    "status": str(check.get("status", "UNKNOWN")),
                    "code": str(check.get("code", "UNKNOWN")),
                    "detail": str(detail)[:240] if detail is not None else "",
                }
            )
    summary = snapshot.get("summary")
    return {
        "snapshot_present": True,
        "observed_at_ms": snapshot.get("observed_at_ms"),
        "verdict": str(snapshot.get("verdict", "UNKNOWN")),
        "summary": summary if isinstance(summary, dict) else {},
        "checks": rows,
        "external_heartbeat_configured": bool(
            snapshot.get("external_heartbeat_configuration_ready", False)
        ),
        "event_seal_configured": bool(
            snapshot.get("event_seal_configuration_ready", False)
        ),
    }


def _paper_profile_payload(
    *,
    profile_id: str,
    aum: float,
    realtime_path: str,
    health_path: str,
) -> Dict:
    """Return a compact, read-only status for an isolated paper profile."""
    realtime = _load_json_object(realtime_path)
    health = _load_json_object(health_path)
    realtime_verdict = realtime.get("verdict", {}) if realtime else {}
    health_verdict = health.get("verdict", {}) if health else {}
    realtime_persistence = realtime.get("persistence", {}) if realtime else {}
    realtime_ledger = realtime.get("paper_ledger", {}) if realtime else {}
    realtime_safety = realtime.get("safety", {}) if realtime else {}
    realtime_funding_refresh = realtime.get("funding_refresh", {}) if realtime else {}
    health_safety = health.get("safety", {}) if health else {}
    blockers = health.get("blockers", []) if health else []
    realtime_alphas = realtime.get("alphas", {}) if realtime else {}
    realtime_history = realtime.get("history", []) if realtime else []
    return {
        "profile_id": profile_id,
        "aum": aum,
        "read_only": True,
        "snapshot_present": bool(realtime or health),
        "realtime": {
            "cycle_completed": bool(realtime_verdict.get("cycle_completed", False)),
            "paper_mark_ready": bool(realtime_verdict.get("paper_mark_ready", False)),
            "backtest_reference_present_for_all_three": bool(
                realtime_verdict.get("backtest_reference_present_for_all_three", False)
            ),
            "new_paper_orders_allowed": bool(
                realtime_verdict.get("new_paper_orders_allowed", False)
            ),
            "operator_mode": realtime.get("operator_mode") if realtime else None,
            "readiness_class": (
                realtime.get("readiness_class", "PAPER_MARKS_BLOCKED")
                if realtime
                else "PAPER_MARKS_BLOCKED"
            ),
            "observed_at": realtime.get("observed_at") if realtime else None,
            "funding_refresh": (
                realtime_funding_refresh
                if isinstance(realtime_funding_refresh, dict)
                else {}
            ),
            "chain_valid": bool(realtime_persistence.get("chain_valid", False)),
            "alphas": realtime_alphas,
            "total_equity": _paper_alpha_total(realtime_alphas, "equity"),
            "total_net_pnl": _paper_alpha_total(realtime_alphas, "net_pnl"),
            "paper_ledger": realtime_ledger if isinstance(realtime_ledger, dict) else {},
            "history": realtime_history if isinstance(realtime_history, list) else [],
        },
        "health": {
            "paper_ready": bool(health_verdict.get("paper_ready", False)),
            "paper_mark_ready": bool(health_verdict.get("paper_mark_ready", False)),
            "paper_preview_ready": bool(health_verdict.get("paper_preview_ready", False)),
            "funding_data_complete_for_all_three": bool(
                health_verdict.get("funding_data_complete_for_all_three", False)
            ),
            "readiness_class": health.get("readiness_class", "PAPER_BLOCKED")
            if health
            else "PAPER_BLOCKED",
            "next_action": health.get("next_action") if health else None,
            "research_risk_bundle_complete": bool(
                health.get("research_risk_bundle_complete", False)
            )
            if health
            else False,
            "research_risk_bundles": (
                health.get("research_risk_bundles")
                if isinstance(health, dict)
                and isinstance(health.get("research_risk_bundles"), dict)
                else {}
            ),
            "age_ms": health.get("age_ms") if health else None,
            "blockers": blockers if isinstance(blockers, list) else [],
        },
        "forward_track": _forward_track_payload(),
        "safety": {
            "paper_only": bool(
                realtime_safety.get("paper_only", health_safety.get("paper_only", True))
            ),
            "broker_connectivity": bool(
                realtime_safety.get("broker_connectivity", False)
            ),
            "credentials_accessed": bool(
                realtime_safety.get("credentials_accessed", False)
            ),
            "broker_order_submission": bool(
                realtime_safety.get("broker_order_submission", False)
            ),
            "live_allowed": bool(realtime_verdict.get("live_allowed", False)),
            "order_submission_allowed": bool(
                realtime_verdict.get("order_submission_allowed", False)
            ),
        },
    }


def _paper_shadow_payload() -> Dict:
    """Expose the isolated, non-promotable automated shadow-paper account."""
    auto = _load_json_object(_PAPER_SHADOW_AUTO)
    realtime = _load_json_object(_PAPER_SHADOW_REALTIME)
    auto_verdict = auto.get("verdict", {}) if auto else {}
    realtime_verdict = realtime.get("verdict", {}) if realtime else {}
    auto_safety = auto.get("safety", {}) if auto else {}
    realtime_safety = realtime.get("safety", {}) if realtime else {}
    persistence = realtime.get("persistence", {}) if realtime else {}
    ledger = realtime.get("paper_ledger", {}) if realtime else {}
    alphas = realtime.get("alphas", {}) if realtime else {}
    previews = auto.get("alphas", {}) if auto else {}
    history = realtime.get("history", []) if realtime else []
    boundary_valid = bool(
        auto
        and realtime
        and auto_safety.get("paper_only") is True
        and auto_safety.get("broker_connectivity") is False
        and auto_safety.get("broker_order_submission") is False
        and auto_safety.get("credentials_accessed") is False
        and realtime_safety.get("paper_only") is True
        and realtime_safety.get("broker_connectivity") is False
        and realtime_safety.get("broker_order_submission") is False
        and realtime_safety.get("credentials_accessed") is False
        and persistence.get("chain_valid") is True
    )
    running = bool(
        boundary_valid
        and auto_verdict.get("shadow_paper_running") is True
        and realtime_verdict.get("shadow_paper_running") is True
        and realtime_verdict.get("paper_mark_ready") is True
        and isinstance(alphas, dict)
        and len(alphas) == 3
    )
    return {
        "source": "AUTOMATED_SHADOW_PAPER_ARTIFACTS",
        "read_only": True,
        "snapshot_present": bool(auto or realtime),
        "execution_class": "AUTOMATED_NONPROMOTABLE_SHADOW_PAPER",
        "status": "RUNNING" if running else "SAFE_BLOCKED",
        "generated_at": auto.get("generated_at") if auto else None,
        "observed_at": realtime.get("observed_at") if realtime else None,
        "provider": realtime.get("provider") if realtime else None,
        "verdict": {
            "shadow_paper_running": running,
            "auto_rebalance_ready": bool(
                boundary_valid
                and auto_verdict.get("shadow_paper_running") is True
            ),
            "paper_mark_ready": bool(
                boundary_valid
                and realtime_verdict.get("paper_mark_ready") is True
            ),
            "boundary_valid": boundary_valid,
            "official_paper_ready": False,
            "promotion_eligible": False,
            "forward_evidence_eligible": False,
            "deployment_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "alphas": alphas if isinstance(alphas, dict) else {},
        "previews": previews if isinstance(previews, dict) else {},
        "history": history if isinstance(history, list) else [],
        "totals": {
            "equity": _paper_alpha_total(alphas, "equity"),
            "net_pnl": _paper_alpha_total(alphas, "net_pnl"),
            "funding_pnl": _paper_alpha_total(alphas, "funding_pnl"),
        },
        "paper_ledger": ledger if isinstance(ledger, dict) else {},
        "persistence": {
            "new_snapshots": int(persistence.get("new_snapshots", 0) or 0),
            "chain_valid": bool(boundary_valid),
        },
        "errors": realtime.get("errors", {}) if realtime else {},
        "safety": {
            "paper_only": True,
            "separate_from_official_paper": True,
            "public_market_data_only": True,
            "private_account_access": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
            "broker_order_submission": False,
        },
    }


def _dry_run_payload() -> Dict:
    """Surface persisted dry-run pipeline results through a read-only route."""
    runs = []
    present = os.path.exists(_DRY_RUN_DB)
    if present:
        try:
            runs = DryRunStore(_DRY_RUN_DB, create=False).all_runs()
        except Exception:
            present, runs = False, []
    has = bool(present and runs)
    latest = runs[-1] if runs else {}
    paper_artifact = _load_json_object(_PAPER_ORDER_PREVIEW)
    paper_verdict = paper_artifact.get("verdict", {}) if paper_artifact else {}
    paper_safety = paper_artifact.get("safety", {}) if paper_artifact else {}
    paper_alphas = paper_artifact.get("alphas", {}) if paper_artifact else {}
    paper_errors = paper_artifact.get("errors", {}) if paper_artifact else {}
    paper_preview = {
        "source": "PAPER_ORDER_PREVIEW_ARTIFACT",
        "read_only": True,
        "snapshot_present": bool(paper_artifact),
        "generated_at": paper_artifact.get("generated_at") if paper_artifact else None,
        "verdict": {
            "paper_preview_ready": bool(paper_verdict.get("paper_preview_ready", False)),
            "live_allowed": bool(paper_verdict.get("live_allowed", False)),
            "order_submission_allowed": bool(paper_verdict.get("order_submission_allowed", False)),
        },
        "alphas": paper_alphas if isinstance(paper_alphas, dict) else {},
        "errors": paper_errors if isinstance(paper_errors, dict) else {},
        "safety": {
            "paper_only": bool(paper_safety.get("paper_only", True)),
            "broker_connectivity": bool(paper_safety.get("broker_connectivity", False)),
            "broker_order_submission": bool(
                paper_safety.get("broker_order_submission", False)
            ),
            "credentials_accessed": bool(paper_safety.get("credentials_accessed", False)),
        },
    }
    operator_artifact = _load_json_object(_L2_PAPER_OPERATOR)
    operator_verdict = operator_artifact.get("verdict", {}) if operator_artifact else {}
    operator_safety = operator_artifact.get("safety", {}) if operator_artifact else {}
    operator_stages = operator_artifact.get("stages", {}) if operator_artifact else {}
    paper_operator = {
        "source": "L2_PAPER_OPERATOR_RECEIPT",
        "read_only": True,
        "snapshot_present": bool(operator_artifact),
        "generated_at": operator_artifact.get("finished_at") if operator_artifact else None,
        "verdict": {
            "operator_completed": bool(operator_verdict.get("operator_completed", False)),
            "paper_preview_ready": bool(operator_verdict.get("paper_preview_ready", False)),
            "research_packet_ready_for_all_three": bool(
                operator_verdict.get("research_packet_ready_for_all_three", False)
            ),
            "live_allowed": bool(operator_verdict.get("live_allowed", False)),
            "order_submission_allowed": bool(operator_verdict.get("order_submission_allowed", False)),
        },
        "stages": {
            "three_alpha_paper_flow": {
                "status": (operator_stages.get("three_alpha_paper_flow") or {}).get("status", "UNKNOWN")
                if isinstance(operator_stages, dict) else "UNKNOWN",
                "flow_completed": bool(
                    (operator_stages.get("three_alpha_paper_flow") or {}).get("flow_completed", False)
                ) if isinstance(operator_stages, dict) else False,
            },
            "paper_order_preview": {
                "status": (operator_stages.get("paper_order_preview") or {}).get("status", "UNKNOWN")
                if isinstance(operator_stages, dict) else "UNKNOWN",
                "paper_preview_ready": bool(
                    (operator_stages.get("paper_order_preview") or {}).get("paper_preview_ready", False)
                ) if isinstance(operator_stages, dict) else False,
            },
        },
        "safety": {
            "paper_only": bool(operator_safety.get("paper_only", True)),
            "private_binance_access": bool(operator_safety.get("private_binance_access", False)),
            "credential_access": bool(operator_safety.get("credential_access", False)),
            "broker_order_submission": bool(operator_safety.get("broker_order_submission", False)),
        },
    }
    realtime_artifact = _load_json_object(_PAPER_REALTIME)
    realtime_verdict = realtime_artifact.get("verdict", {}) if realtime_artifact else {}
    realtime_safety = realtime_artifact.get("safety", {}) if realtime_artifact else {}
    realtime_funding_refresh = (
        realtime_artifact.get("funding_refresh", {}) if realtime_artifact else {}
    )
    realtime_persistence = realtime_artifact.get("persistence", {}) if realtime_artifact else {}
    realtime_ledger = realtime_artifact.get("paper_ledger", {}) if realtime_artifact else {}
    realtime_history = realtime_artifact.get("history", []) if realtime_artifact else []
    paper_realtime = {
        "source": "PAPER_REALTIME_MARK_ARTIFACT",
        "read_only": True,
        "snapshot_present": bool(realtime_artifact),
        "generated_at": realtime_artifact.get("generated_at") if realtime_artifact else None,
        "observed_at": realtime_artifact.get("observed_at") if realtime_artifact else None,
        "provider": realtime_artifact.get("provider") if realtime_artifact else None,
        "funding_refresh": (
            realtime_funding_refresh if isinstance(realtime_funding_refresh, dict) else {}
        ),
        "verdict": {
            "cycle_completed": bool(realtime_verdict.get("cycle_completed", False)),
            "paper_mark_ready": bool(realtime_verdict.get("paper_mark_ready", False)),
            "backtest_reference_present_for_all_three": bool(
                realtime_verdict.get("backtest_reference_present_for_all_three", False)
            ),
            "new_paper_orders_allowed": bool(
                realtime_verdict.get("new_paper_orders_allowed", False)
            ),
            "live_allowed": bool(realtime_verdict.get("live_allowed", False)),
            "order_submission_allowed": bool(
                realtime_verdict.get("order_submission_allowed", False)
            ),
        },
        "alphas": realtime_artifact.get("alphas", {}) if realtime_artifact else {},
        "operator_mode": realtime_artifact.get("operator_mode") if realtime_artifact else None,
        "readiness_class": (
            realtime_artifact.get("readiness_class", "PAPER_MARKS_BLOCKED")
            if realtime_artifact
            else "PAPER_MARKS_BLOCKED"
        ),
        "backtests": realtime_artifact.get("backtests", {}) if realtime_artifact else {},
        "paper_ledger": realtime_ledger if isinstance(realtime_ledger, dict) else {},
        "history": realtime_history if isinstance(realtime_history, list) else [],
        "persistence": {
            "new_snapshots": int(realtime_persistence.get("new_snapshots", 0) or 0),
            "chain_valid": bool(realtime_persistence.get("chain_valid", False)),
        },
        "errors": realtime_artifact.get("errors", {}) if realtime_artifact else {},
        "safety": {
            "paper_only": bool(realtime_safety.get("paper_only", True)),
            "public_market_data_only": bool(
                realtime_safety.get("public_market_data_only", True)
            ),
            "private_binance_access": bool(
                realtime_safety.get("private_binance_access", False)
            ),
            "credentials_accessed": bool(
                realtime_safety.get("credentials_accessed", False)
            ),
            "broker_connectivity": bool(realtime_safety.get("broker_connectivity", False)),
            "broker_order_submission": bool(
                realtime_safety.get("broker_order_submission", False)
            ),
        },
    }
    forward_track = _forward_track_payload()
    forward_rows = forward_track.get("alphas", [])
    forward_required = int(forward_track.get("evidence_gate_weeks", _EVIDENCE_GATE_WEEKS))
    forward_evidence_fallback = {
        "present": bool(forward_track.get("present")),
        "ran_at": forward_track.get("ran_at"),
        "evidence_gate_periods": forward_required,
        "complete_for_all_three": bool(forward_rows)
        and all(
            isinstance(row, dict)
            and isinstance(row.get("n_forward_periods"), int)
            and row["n_forward_periods"] >= forward_required
            for row in forward_rows
        ),
        "quality_evidence_current": forward_track.get("quality_evidence_current"),
        "data_quality_admission": forward_track.get("data_quality_admission", {}),
        "alphas": {
            row["alpha_id"]: row
            for row in forward_rows
            if isinstance(row, dict) and isinstance(row.get("alpha_id"), str)
        },
        "alerts": forward_track.get("alerts", []),
    }
    health_artifact = _load_json_object(_PAPER_HEALTH)
    health_verdict = health_artifact.get("verdict", {}) if health_artifact else {}
    health_safety = health_artifact.get("safety", {}) if health_artifact else {}
    health_blockers = health_artifact.get("blockers", []) if health_artifact else []
    paper_health = {
        "source": "PAPER_HEALTH_CHECK_ARTIFACT",
        "read_only": True,
        "snapshot_present": bool(health_artifact),
        "generated_at": health_artifact.get("generated_at") if health_artifact else None,
        "observed_at": health_artifact.get("observed_at") if health_artifact else None,
        "age_ms": health_artifact.get("age_ms") if health_artifact else None,
        "max_age_ms": health_artifact.get("max_age_ms") if health_artifact else None,
        "verdict": {
            "paper_ready": bool(health_verdict.get("paper_ready", False)),
            "paper_mark_ready": bool(health_verdict.get("paper_mark_ready", False)),
            "paper_preview_ready": bool(health_verdict.get("paper_preview_ready", False)),
            "backtest_reference_present_for_all_three": bool(
                health_verdict.get("backtest_reference_present_for_all_three", False)
            ),
            "funding_data_complete_for_all_three": bool(
                health_verdict.get("funding_data_complete_for_all_three", False)
            ),
            "live_allowed": bool(health_verdict.get("live_allowed", False)),
            "order_submission_allowed": bool(
                health_verdict.get("order_submission_allowed", False)
            ),
        },
        "readiness_class": health_artifact.get("readiness_class", "PAPER_BLOCKED")
        if health_artifact
        else "PAPER_BLOCKED",
        "next_action": health_artifact.get("next_action") if health_artifact else None,
        "blockers": health_blockers if isinstance(health_blockers, list) else [],
        "research_risk_bundle_complete": bool(
            health_artifact.get("research_risk_bundle_complete", False)
        )
        if health_artifact
        else False,
        "research_risk_bundles": (
            health_artifact.get("research_risk_bundles")
            if isinstance(health_artifact, dict)
            and isinstance(health_artifact.get("research_risk_bundles"), dict)
            else {}
        ),
        "forward_evidence": (
            health_artifact.get("forward_evidence")
            if isinstance(health_artifact, dict)
            and isinstance(health_artifact.get("forward_evidence"), dict)
            and "complete_for_all_three" in health_artifact.get("forward_evidence", {})
            else forward_evidence_fallback
        ),
        "safety": {
            "paper_only": bool(health_safety.get("paper_only", True)),
            "public_market_data_only": bool(
                health_safety.get("public_market_data_only", True)
            ),
            "broker_connectivity": bool(health_safety.get("broker_connectivity", False)),
            "credentials_accessed": bool(health_safety.get("credentials_accessed", False)),
            "broker_order_submission": bool(
                health_safety.get("broker_order_submission", False)
            ),
        },
    }
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_DRY_RUN_OFFLINE_SNAPSHOT" if has else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": has,
        "verdict": "DRY_RUN_OK" if has else "NO_RUN",
        "n_runs": len(runs),
        "latest": latest,
        "runs": runs,
        "paper_order_preview": paper_preview,
        "paper_operator": paper_operator,
        "paper_realtime": paper_realtime,
        "paper_shadow": _paper_shadow_payload(),
        "paper_health": paper_health,
        "forward_track": forward_track,
        "data_refresh": _data_refresh_payload(),
        "scheduler_watchdog": _scheduler_watchdog_payload(),
        "paper_profiles": {
            "small_10k": _paper_profile_payload(
                profile_id="small_10k",
                aum=10_000.0,
                realtime_path=_PAPER_10K_REALTIME,
                health_path=_PAPER_10K_HEALTH,
            )
        },
        "safety_note": (
            "端到端干跑:预交易风控门是运行时 chokepoint(fail-closed,kill-switch latched 全拒),"
            "干跑适配器纸面成交、绝不真发,平均成本对账恒等式残差≈0,哈希链审计。只读、零下单/"
            "券商/凭证;L2_DRY_RUN_ONLY。实盘 = 同接口换真券商适配器 + 人工批准 = 非目标。"
        ),
    }


@router.get("/api/v1/console/dry-run")
async def dry_run() -> Dict:
    return _dry_run_payload()


def _ic_panel_payload() -> Dict:
    """Serve the cross-sectional IC diagnostic snapshot, not a promotion gate."""
    present, rows, universes = False, [], {}
    if os.path.exists(IC_PANEL_SNAPSHOT):
        try:
            with open(IC_PANEL_SNAPSHOT, encoding="utf-8") as fh:
                snap = json.load(fh)
            rows, universes = snap.get("rows", []), snap.get("universes", {})
            present = bool(rows)
        except Exception:
            present, rows, universes = False, [], {}
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_IC_PANEL_OFFLINE_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": present,
        "verdict": "IC_PANEL_OK" if present else "NO_SNAPSHOT",
        "rows": rows,
        "universes": universes,
        "honesty_note": (
            "截面 IC 面板是初筛诊断工具,不是晋级门禁——真正的门禁是 services/alpha/evaluator "
            "的 DSR/PBO/walk-forward。面板结果不驱动任何自动晋级。"
        ),
    }


@router.get("/api/v1/console/ic-panel")
async def ic_panel() -> Dict:
    return _ic_panel_payload()


def _research_findings_payload() -> Dict:
    """Aggregate capacity, macro-regime, and diversification diagnostics."""
    capacity = _load_json_snapshot(_CAPACITY_SNAPSHOT)
    macro = _load_json_snapshot(_MACRO_REGIME_SNAPSHOT)
    combo = _load_json_snapshot(_COMBO_BOOK_SNAPSHOT)
    present = bool(capacity or macro or combo)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_RESEARCH_FINDINGS" if present else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": present,
        "verdict": "FINDINGS_OK" if present else "NO_SNAPSHOT",
        "capacity": capacity or {},
        "macro_regime": macro or {},
        "combo": combo or {},
        "honesty_note": (
            "研究诊断聚合,非晋级门禁:容量为 UNCALIBRATED_PRINCIPLED 区间(冲击系数未校准)、"
            "regime 标签为全样本分位(回顾性非 PIT 择时)、组合为 gross 收益流层面(RELATIVE "
            "比较)。数字为准,均不驱动自动晋级/下单。"
        ),
    }


@router.get("/api/v1/console/research-findings")
async def research_findings() -> Dict:
    return _research_findings_payload()


def _lance_1m_validation_payload() -> Dict:
    """Serve the independent validation of the user-supplied 1-minute strategy."""
    snap = _load_json_snapshot(_LANCE_1M_VALIDATION_SNAPSHOT)
    present = bool(snap)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_LANCE_1M_VALIDATION" if present else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": present,
        "verdict": "REFUTED" if present else "NO_SNAPSHOT",
        "doc": (snap or {}).get("doc", "docs/test/lance_1m_strategy.md"),
        "fee_sweep": (snap or {}).get("fee_sweep", {}),
        "rolling_window_stress_1bps": (snap or {}).get("rolling_window_stress_full_dataset_1bps", {}),
        "rolling_window_stress_5bps": (snap or {}).get("rolling_window_stress_full_dataset_5bps", {}),
        "parameter_sensitivity_5bps": (snap or {}).get("parameter_sensitivity_5bps", {}),
        "ohlc_ambiguity_comparison": (snap or {}).get("ohlc_ambiguity_comparison_doc_window_1bps", {}),
        "honesty_note": (
            "文档自称夏普5.01/总收益+17.9%(1bps手续费,138,755根K线,2025-12~2026-06);用"
            "53.7万根真实1分钟BTC K线独立复核后,在文档自称窗口+文档自己最乐观的1bps假设下,"
            "实测夏普为负(约-5至-6区间,非+5.01),4种窗口切分×5档手续费×107窗口滚动压力测试×"
            "4参数±30%敏感性全部一致为负。已用合成数据验证引擎本身无bug,已剥离本模块自己的"
            "OHLC歧义保守裁决重跑确认负号不是该工程决策导致的,已用0bps分解排除成本模型单位错误"
            "的可能。诊断结论,非晋级门禁——该策略未注册进 AlphaCatalog、不驱动任何自动晋级/下单。"
        ),
    }


@router.get("/api/v1/console/lance-1m-validation")
async def lance_1m_validation() -> Dict:
    return _lance_1m_validation_payload()


def _nonnegative_int(mapping: Dict, key: str) -> int | None:
    value = mapping.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _forward_track_from_evidence(now_ms: int) -> Dict:
    """Expose only the attested baseline; never fall back to the legacy status cache."""
    evidence = _load_json_object(_FORWARD_EVIDENCE_BASELINE)
    unavailable = {
        "present": False,
        "evidence_state": "UNAVAILABLE",
        "actions_allowed": False,
    }
    if evidence is None:
        return unavailable
    counts = evidence.get("counts")
    gate = evidence.get("evidence_gate")
    admission = evidence.get("data_admission")
    observed_at_ms = _nonnegative_int(evidence, "observed_at_ms")
    max_age_ms = _nonnegative_int(evidence, "max_age_ms")
    if (
        not isinstance(counts, dict)
        or not isinstance(gate, dict)
        or not isinstance(admission, dict)
        or observed_at_ms is None
        or max_age_ms is None
        or not isinstance(gate.get("minimum_weeks"), int)
        or isinstance(gate.get("minimum_weeks"), bool)
        or not isinstance(gate.get("eligible"), bool)
    ):
        return unavailable
    parsed_counts = {key: _nonnegative_int(counts, key) for key in (
        "attested_realizations", "attested_pending", "legacy_unattested_decisions",
    )}
    if any(value is None for value in parsed_counts.values()):
        return unavailable
    state = (
        "STALE" if now_ms - observed_at_ms > max_age_ms
        else "ATTESTED" if admission.get("status") == "CURRENT"
        else "DATA_BLOCKED"
    )
    return {
        "present": True,
        "evidence_state": state,
        "attested_realizations": parsed_counts["attested_realizations"],
        "attested_pending": parsed_counts["attested_pending"],
        "legacy_unattested_decisions": parsed_counts["legacy_unattested_decisions"],
        "evidence_gate_weeks": gate["minimum_weeks"],
        "evidence_gate_eligible": gate["eligible"],
        "data_admission": admission,
        "actions_allowed": False,
    }


def _live_trading_payload(now_ms: int | None = None) -> Dict:
    """Aggregate forward-paper and testnet status as read-only diagnostics."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    forward_track = _forward_track_from_evidence(now)
    lt = _load_json_snapshot(_LIVE_TRACK_STATUS)
    risk = _load_json_snapshot(_LIVE_RISK_STATE)
    present = bool(forward_track["present"] or lt)

    execution_stack: Dict = {"present": False}
    if lt:
        execution_stack = {
            "present": True,
            "ran_at": lt.get("ran_at"),
            "mode": lt.get("mode"),
            "hard_stop": bool(lt.get("hard_stop", False)),
            "tier": lt.get("tier"),
            "n_submitted": lt.get("n_submitted"),
            "n_pending_review": lt.get("n_pending_review"),
            "n_rejected_by_gate": lt.get("n_rejected_by_gate"),
            "n_dropped_by_filters": lt.get("n_dropped_by_filters"),
            "n_submission_errors": lt.get("n_submission_errors"),
            "n_ledger_write_errors": lt.get("n_ledger_write_errors"),
            "reconciliation": lt.get("reconciliation", {}),
            "risk_breach": lt.get("risk_breach"),
            "aum": lt.get("aum"),
            "peak_equity": (risk or {}).get("peak_equity"),
            "would_submit": lt.get("would_submit", []),
            "alerts": lt.get("alerts", []),
            "venue": lt.get("venue"),
            "tca": lt.get("tca"),
            "order_stats": lt.get("order_stats"),
            "parity": lt.get("parity"),
            "emergency": lt.get("emergency"),
        }

    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_LIVE_TRADING_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": present,
        "verdict": "LIVE_TRADING_TRACKING_OK" if present else "NO_SNAPSHOT",
        "forward_track": forward_track,
        "execution_stack": execution_stack,
        "honesty_note": (
            "这是诊断展示,不驱动任何自动化或晋级;真钱 Go/No-Go 是 Phase 3A 人工评审。测试网执行"
            "(execution_stack)与真前向纸面跟踪(forward_track)都在 L2_DRY_RUN_ONLY 边界内——测试网是"
            "交易所官方沙盒零真实资本,不是真钱。"
        ),
    }


@router.get("/api/v1/console/live-trading")
async def live_trading() -> Dict:
    return _live_trading_payload()


def _gonogo_payload() -> Dict:
    """Aggregate existing evidence without making a Go/No-Go decision."""
    rep = _load_json_snapshot(_GONOGO_REPORT)
    present = bool(rep)
    rep = rep or {}
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": ("REAL_GONOGO_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC"),
        "snapshot_present": present,
        "verdict": "GONOGO_EVIDENCE_READY" if present else "NO_SNAPSHOT",
        "generated_at": rep.get("generated_at"),
        "readiness": rep.get("readiness", "L2_DRY_RUN_ONLY"),
        "forward_evidence": rep.get("forward_evidence", {"present": False}),
        "execution_evidence": rep.get("execution_evidence", {}),
        "incidents": rep.get("incidents", {}),
        "honesty_note": rep.get(
            "honesty_note",
            "GoNoGo 证据包只聚合既有真实产物,不下结论——结论是 Phase 3A 人工评审;无快照时为合成占位。",
        ),
    }


@router.get("/api/v1/console/gonogo")
async def gonogo() -> Dict:
    return _gonogo_payload()
