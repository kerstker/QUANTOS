"""Read-only HTTP routes backed by offline research artifact snapshots."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

from services.execution.dry_run_store import DryRunStore
from .artifact_diagnostic_views import router as diagnostic_router


router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
IC_PANEL_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "ic_panel_snapshot.json")
_DRY_RUN_DB = os.path.join(_REPO_ROOT, "data", "cache", "dry_run.db")
_PAPER_ORDER_PREVIEW = os.path.join(_REPO_ROOT, "data", "cache", "paper_order_preview.json")
_L2_PAPER_OPERATOR = os.path.join(_REPO_ROOT, "data", "cache", "l2_paper_operator.json")
_PAPER_REALTIME = os.path.join(_REPO_ROOT, "data", "cache", "paper_realtime_status.json")
_PAPER_HEALTH = os.path.join(_REPO_ROOT, "data", "cache", "paper_health.json")
_LIVE_REFRESH_STATUS = os.path.join(_REPO_ROOT, "data", "cache", "live_refresh_status.json")
_SCHEDULER_WATCHDOG_STATUS = os.path.join(
    _REPO_ROOT, "data", "cache", "scheduler_watchdog_status.json"
)
_PAPER_10K_REALTIME = os.path.join(
    _REPO_ROOT, "data", "cache", "paper_realtime_10k_status.json"
)
_PAPER_10K_HEALTH = os.path.join(_REPO_ROOT, "data", "cache", "paper_health_10k.json")
_FORWARD_TRACK_STATUS = os.path.join(_REPO_ROOT, "data", "cache", "forward_track_status.json")
_LIVE_TRACK_STATUS = os.path.join(_REPO_ROOT, "data", "cache", "live_track_status.json")
_LIVE_RISK_STATE = os.path.join(_REPO_ROOT, "data", "cache", "live_risk_state.json")
_GONOGO_REPORT = os.path.join(_REPO_ROOT, "data", "cache", "gonogo_report.json")
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


