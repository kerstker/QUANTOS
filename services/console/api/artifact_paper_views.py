"""Read-only paper and research routes backed by offline artifact snapshots."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import time
from typing import Any, Dict

from fastapi import APIRouter

from quantos.contracts.hashing import stable_hash
from quantos.runtime_paths import runtime_state_root
from services.execution.paper_tca_history import build_paper_tca_history
from services.execution.dry_run_store import DryRunStore
from .artifact_diagnostic_views import router as diagnostic_router


router = APIRouter()

_REPO_ROOT = runtime_state_root()
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
_FACTORY_LOOP_STATUS = os.path.join(
    _REPO_ROOT, "data", "cache", "crypto_factory_loop.json"
)
_CANDIDATE_ADMISSION_STATUS = os.path.join(
    _REPO_ROOT, "data", "cache", "alpha_candidate_admission.json"
)
_FORWARD_CANDIDATE_EVALUATIONS = os.path.join(
    _REPO_ROOT, "data", "cache", "forward_alpha_evaluations.json"
)
_ALPHA_PRODUCTION_WORKFLOW = os.path.join(
    _REPO_ROOT, "data", "cache", "alpha_production_workflow.json"
)
_PAPER_RESEARCH_RISK_BUNDLE = os.path.join(
    _REPO_ROOT, "data", "cache", "paper_research_risk_inputs.json"
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
_PAPER_MULTIVENUE_SOR = os.path.join(
    _REPO_ROOT, "data", "cache", "paper_multivenue_sor_status.json"
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
_PAPER_OPERATIONAL_EVIDENCE = os.path.join(
    _REPO_ROOT, "data", "cache", "paper_operational_evidence.json"
)
_REALTIME_ALPHA_DISCOVERY = os.path.join(
    _REPO_ROOT, "data", "cache", "realtime_alpha_discovery.json"
)
_REALTIME_ALPHA_FORWARD_SHADOW = os.path.join(
    _REPO_ROOT, "data", "cache", "realtime_alpha_forward_shadow.json"
)
_REALTIME_ALPHA_EXECUTION_PAPER = os.path.join(
    _REPO_ROOT, "data", "cache", "realtime_alpha_execution_paper.json"
)
_EVIDENCE_GATE_WEEKS = 8
_LEGACY_DIAGNOSTIC_PAPER_IDS = frozenset(
    {"carry_oi_majors", "crowd_contra", "top_contra"}
)

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


_REALTIME_ALPHA_SAFE_BOUNDARY = {
    "paper_only": True,
    "live_trading_enabled": False,
    "real_broker_enabled": False,
    "allow_order_submission": False,
    "allow_ai_direct_trading": False,
    "promotion_eligible": False,
    "credentials_accessed": False,
}

_REALTIME_FORWARD_SHADOW_SAFE_BOUNDARY = {
    "paper_only": True,
    "public_market_data_only": True,
    "separate_from_official_paper": True,
    "credentials_accessed": False,
    "broker_connectivity": False,
    "broker_order_submission": False,
    "allow_ai_direct_trading": False,
}

_REALTIME_EXECUTION_PAPER_SAFE_BOUNDARY = {
    "paper_only": True,
    "public_market_data_only": True,
    "credentials_accessed": False,
    "private_stream": False,
    "broker_connectivity": False,
    "broker_order_submission": False,
    "allow_ai_direct_trading": False,
}


def _realtime_alpha_forward_shadow_payload(path: str | None = None) -> Dict:
    artifact = _load_json_object(path or _REALTIME_ALPHA_FORWARD_SHADOW)
    empty = {
        "snapshot_present": False,
        "boundary_valid": False,
        "status": "NO_SNAPSHOT",
        "mode": "AUTONOMOUS_REALTIME_ENGINEERING_SHADOW",
        "generated_at_ms": None,
        "eligible_candidates": [],
        "admitted_candidates": [],
        "handoff": {"admission_blockers": [], "graduation_blockers": []},
        "feature_input": {"ready": False},
        "cycle": {
            "new_admissions": 0,
            "new_decisions": 0,
            "new_realizations": 0,
        },
        "tracks": {},
        "errors": {},
        "ledger": {"append_only": True, "chain_valid": False, "event_count": 0},
        "verdict": {
            "forward_shadow_running": False,
            "engineering_shadow_running": False,
            "paper_observation_started_without_maturity_wait": False,
            "promotion_eligible": False,
            "forward_evidence_credit": False,
            "deployment_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "blockers": ["REALTIME_FORWARD_SHADOW_SNAPSHOT_MISSING"],
        "safety": dict(_REALTIME_FORWARD_SHADOW_SAFE_BOUNDARY),
    }
    if artifact is None:
        return empty
    blockers: list[str] = []
    safety = artifact.get("safety")
    if not isinstance(safety, dict) or any(
        safety.get(field) is not expected
        for field, expected in _REALTIME_FORWARD_SHADOW_SAFE_BOUNDARY.items()
    ):
        blockers.append("REALTIME_FORWARD_SHADOW_SAFETY_INVALID")
    ledger = artifact.get("ledger")
    if (
        not isinstance(ledger, dict)
        or ledger.get("append_only") is not True
        or ledger.get("chain_valid") is not True
    ):
        blockers.append("REALTIME_FORWARD_SHADOW_LEDGER_INVALID")
    verdict = artifact.get("verdict")
    if not isinstance(verdict, dict) or any(
        verdict.get(field) is not False
        for field in (
            "promotion_eligible",
            "forward_evidence_credit",
            "deployment_eligible",
            "live_allowed",
            "order_submission_allowed",
        )
    ):
        blockers.append("REALTIME_FORWARD_SHADOW_AUTHORITY_INVALID")
    if blockers:
        blocked = dict(empty)
        blocked.update(
            {
                "snapshot_present": True,
                "status": "SAFE_BLOCKED",
                "generated_at_ms": artifact.get("generated_at_ms"),
                "blockers": blockers,
            }
        )
        return blocked
    return {
        "snapshot_present": True,
        "boundary_valid": True,
        "status": artifact.get("status", "SAFE_BLOCKED"),
        "mode": artifact.get("mode", "AUTONOMOUS_REALTIME_ENGINEERING_SHADOW"),
        "generated_at_ms": artifact.get("generated_at_ms"),
        "eligible_candidates": artifact.get("eligible_candidates", []),
        "admitted_candidates": artifact.get("admitted_candidates", []),
        "handoff": artifact.get("handoff", {}),
        "feature_input": artifact.get("feature_input", {}),
        "cycle": artifact.get("cycle", {}),
        "tracks": artifact.get("tracks", {}),
        "errors": artifact.get("errors", {}),
        "ledger": {
            "append_only": True,
            "chain_valid": True,
            "event_count": ledger.get("event_count", 0),
        },
        "verdict": {
            "forward_shadow_running": bool(
                verdict.get("forward_shadow_running", False)
            ),
            "engineering_shadow_running": bool(
                verdict.get("engineering_shadow_running", False)
            ),
            "paper_observation_started_without_maturity_wait": bool(
                verdict.get("paper_observation_started_without_maturity_wait", False)
            ),
            "promotion_eligible": False,
            "forward_evidence_credit": False,
            "deployment_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "blockers": [],
        "safety": dict(_REALTIME_FORWARD_SHADOW_SAFE_BOUNDARY),
    }


def _realtime_alpha_execution_paper_payload(path: str | None = None) -> Dict:
    """Project the isolated execution ledger without widening its authority."""
    artifact = _load_json_object(path or _REALTIME_ALPHA_EXECUTION_PAPER)
    empty = {
        "snapshot_present": False,
        "boundary_valid": False,
        "status": "NO_SNAPSHOT",
        "mode": "AUTONOMOUS_REALTIME_ENGINEERING_EXECUTION_PAPER",
        "execution_profile": None,
        "generated_at_ms": None,
        "candidate_count": 0,
        "cycle": {
            "new_admissions": 0,
            "new_executions": 0,
            "new_no_actions": 0,
            "new_missed_execution_windows": 0,
            "waiting_count": 0,
        },
        "routing": {},
        "lot_authority": {"instrument_count": 0, "spec_hash": None},
        "tracks": {},
        "waiting": {},
        "errors": {},
        "ledger": {
            "append_only": True,
            "chain_valid": False,
            "source_shadow_chain_valid": False,
            "event_count": 0,
            "separate_from_official_paper": True,
        },
        "verdict": {
            "engineering_execution_paper_running": False,
            "production_execution_seam_exercised": False,
            "official_paper_ready": False,
            "promotion_eligible": False,
            "forward_evidence_eligible": False,
            "deployment_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "blockers": ["REALTIME_ENGINEERING_EXECUTION_PAPER_SNAPSHOT_MISSING"],
        "safety": dict(_REALTIME_EXECUTION_PAPER_SAFE_BOUNDARY),
    }
    if artifact is None:
        return empty
    blockers: list[str] = []
    if artifact.get("mode") != "AUTONOMOUS_REALTIME_ENGINEERING_EXECUTION_PAPER":
        blockers.append("REALTIME_ENGINEERING_EXECUTION_MODE_INVALID")
    safety = artifact.get("safety")
    if not isinstance(safety, dict) or any(
        safety.get(field) is not expected
        for field, expected in _REALTIME_EXECUTION_PAPER_SAFE_BOUNDARY.items()
    ):
        blockers.append("REALTIME_ENGINEERING_EXECUTION_SAFETY_INVALID")
    ledger = artifact.get("ledger")
    if (
        not isinstance(ledger, dict)
        or ledger.get("append_only") is not True
        or ledger.get("chain_valid") is not True
        or ledger.get("source_shadow_chain_valid") is not True
        or ledger.get("separate_from_official_paper") is not True
    ):
        blockers.append("REALTIME_ENGINEERING_EXECUTION_LEDGER_INVALID")
    verdict = artifact.get("verdict")
    if not isinstance(verdict, dict) or any(
        verdict.get(field) is not False
        for field in (
            "official_paper_ready",
            "promotion_eligible",
            "forward_evidence_eligible",
            "deployment_eligible",
            "live_allowed",
            "order_submission_allowed",
        )
    ):
        blockers.append("REALTIME_ENGINEERING_EXECUTION_AUTHORITY_INVALID")
    if blockers:
        blocked = dict(empty)
        blocked.update(
            {
                "snapshot_present": True,
                "status": "SAFE_BLOCKED",
                "generated_at_ms": artifact.get("generated_at_ms"),
                "blockers": blockers,
            }
        )
        return blocked
    raw_tracks = artifact.get("tracks")
    tracks = {}
    if isinstance(raw_tracks, dict):
        for candidate_id, row in raw_tracks.items():
            if not isinstance(candidate_id, str) or not isinstance(row, dict):
                continue
            tracks[candidate_id] = {
                field: row.get(field)
                for field in (
                    "state",
                    "n_executions",
                    "n_missed_execution_windows",
                    "n_no_actions",
                    "n_orders",
                    "n_fill_slices",
                    "execution_cost",
                    "last_decision_time_ms",
                    "last_missed_decision_time_ms",
                    "last_no_action_decision_time_ms",
                )
            }
    return {
        "snapshot_present": True,
        "boundary_valid": True,
        "status": artifact.get("status", "SAFE_BLOCKED"),
        "mode": artifact.get("mode"),
        "execution_profile": artifact.get("execution_profile"),
        "generated_at_ms": artifact.get("generated_at_ms"),
        "candidate_count": artifact.get("candidate_count", 0),
        "cycle": artifact.get("cycle", {}),
        "routing": artifact.get("routing", {}),
        "lot_authority": artifact.get("lot_authority", {}),
        "tracks": tracks,
        "waiting": artifact.get("waiting", {}),
        "errors": artifact.get("errors", {}),
        "ledger": {
            "append_only": True,
            "chain_valid": True,
            "source_shadow_chain_valid": True,
            "event_count": ledger.get("event_count", 0),
            "missed_execution_window_count": ledger.get(
                "missed_execution_window_count", 0
            ),
            "separate_from_official_paper": True,
        },
        "verdict": {
            "engineering_execution_paper_running": bool(
                verdict.get("engineering_execution_paper_running", False)
            ),
            "production_execution_seam_exercised": bool(
                verdict.get("production_execution_seam_exercised", False)
            ),
            "official_paper_ready": False,
            "promotion_eligible": False,
            "forward_evidence_eligible": False,
            "deployment_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "blockers": [],
        "safety": dict(_REALTIME_EXECUTION_PAPER_SAFE_BOUNDARY),
    }


def _realtime_alpha_candidate_view(row: Any) -> Dict | None:
    """Return the bounded, read-only candidate fields needed by the console."""
    if not isinstance(row, dict) or not isinstance(row.get("candidate_id"), str):
        return None
    fields = (
        "candidate_id",
        "family_id",
        "mechanism",
        "horizon_ms",
        "evaluation_status",
        "status",
        "n_periods",
        "mean_breadth",
        "mean_ic",
        "ic_t_stat",
        "net_sharpe",
        "holdout_net_sharpe",
        "net_return",
        "max_drawdown",
        "walk_forward_positive_frac",
        "spread_regime_positive_frac",
        "diagnostic_lead",
        "gate_pass",
        "gate_reasons",
        "deflated_sharpe",
        "pbo",
    )
    result = {field: row.get(field) for field in fields}
    # Console projection can never widen the worker's authority.
    result["promotion_eligible"] = False
    result["live_allowed"] = False
    return result


def _realtime_alpha_autonomy_payload(path: str | None = None) -> Dict:
    """Project autonomous realtime research evidence through a fail-closed seam."""
    artifact = _load_json_object(path or _REALTIME_ALPHA_DISCOVERY)
    empty = {
        "source": "AUTONOMOUS_REALTIME_ALPHA_DISCOVERY_ARTIFACT",
        "read_only": True,
        "snapshot_present": False,
        "boundary_valid": False,
        "status": "NO_SNAPSHOT",
        "mode": "L2_AUTONOMOUS_REALTIME_ALPHA_RESEARCH",
        "generated_at_ms": None,
        "dataset_version": None,
        "search_id": None,
        "autonomy": {
            "worker": "tools.realtime_alpha_discovery",
            "scheduler_task": "QUANTOS_realtime_alpha_discovery",
            "autonomous_search_running": False,
            "requires_developer_ai": False,
            "next_cycle_expected_ms": None,
        },
        "data_admission": {
            "status": "NO_SNAPSHOT",
            "common_span_ms": 0,
            "feature_age_ms": None,
            "common_instruments": [],
            "exploration_allowed": False,
            "formal_validation_allowed": False,
            "shadow_admission_allowed": False,
            "reasons": ["autonomous realtime discovery snapshot is unavailable"],
        },
        "feature_quality": {"status": "NO_SNAPSHOT", "drift_status": None},
        "executable_depth_capacity": {
            "status": "NO_SNAPSHOT",
            "passed": False,
            "evidence_ready": False,
            "target_aum_passed": False,
            "shadow_sizing_ready": False,
            "history_span_ms": 0,
            "max_supported_aum_usd": None,
            "target_aum_usd": 1_000_000.0,
            "shadow_aum_usd": None,
            "reasons": ["EXECUTABLE_DEPTH_CAPACITY_UNAVAILABLE"],
        },
        "funnel": {
            "pre_registered": 0,
            "evaluated": 0,
            "diagnostic_leads": 0,
            "formal_passes": 0,
            "shadow_candidates": 0,
        },
        "families": [],
        "top_candidates": [],
        "global_trial_count": None,
        "registry": {"chain_valid": False, "event_count": 0},
        "remaining_gates": [
            "30D_COMMON_HISTORY_FOR_FORMAL_VALIDATION",
            "90D_COMMON_HISTORY_FOR_FORWARD_SHADOW",
            "EXECUTABLE_DEPTH_CAPACITY_CALIBRATION",
        ],
        "blockers": ["AUTONOMY_SNAPSHOT_MISSING"],
        "forward_shadow": _realtime_alpha_forward_shadow_payload(),
        "execution_paper": _realtime_alpha_execution_paper_payload(),
        "safety": dict(_REALTIME_ALPHA_SAFE_BOUNDARY),
    }
    if artifact is None:
        return empty

    artifact_safety = artifact.get("safety")
    registry = artifact.get("registry")
    autonomy = artifact.get("autonomy")
    blockers: list[str] = []
    if artifact.get("mode") != "L2_AUTONOMOUS_REALTIME_ALPHA_RESEARCH":
        blockers.append("AUTONOMY_MODE_INVALID")
    if not isinstance(artifact_safety, dict):
        blockers.append("AUTONOMY_SAFETY_BOUNDARY_MISSING")
    else:
        for field, expected in _REALTIME_ALPHA_SAFE_BOUNDARY.items():
            if artifact_safety.get(field) is not expected:
                blockers.append(f"AUTONOMY_SAFETY_{field.upper()}_INVALID")
    if not isinstance(registry, dict) or registry.get("chain_valid") is not True:
        blockers.append("AUTONOMY_REGISTRY_CHAIN_INVALID")
    if not isinstance(autonomy, dict) or autonomy.get("requires_developer_ai") is not False:
        blockers.append("AUTONOMY_RUNTIME_IDENTITY_INVALID")

    if blockers:
        blocked = dict(empty)
        blocked.update(
            {
                "snapshot_present": True,
                "status": "SAFE_BLOCKED",
                "generated_at_ms": artifact.get("generated_at_ms"),
                "dataset_version": artifact.get("dataset_version"),
                "search_id": artifact.get("search_id"),
                "blockers": blockers,
                "forward_shadow": _realtime_alpha_forward_shadow_payload(),
                "execution_paper": _realtime_alpha_execution_paper_payload(),
            }
        )
        return blocked

    data_admission = artifact.get("data_admission")
    feature_quality = artifact.get("feature_quality")
    drift = feature_quality.get("drift") if isinstance(feature_quality, dict) else None
    funnel = artifact.get("funnel")
    capacity = artifact.get("executable_depth_capacity")
    families = artifact.get("families")
    top_candidates = artifact.get("top_candidates")
    candidate_views = [
        view
        for view in (
            _realtime_alpha_candidate_view(row)
            for row in (top_candidates if isinstance(top_candidates, list) else [])[:8]
        )
        if view is not None
    ]
    return {
        "source": "AUTONOMOUS_REALTIME_ALPHA_DISCOVERY_ARTIFACT",
        "read_only": True,
        "snapshot_present": True,
        "boundary_valid": True,
        "status": artifact.get("status", "SAFE_BLOCKED"),
        "mode": artifact.get("mode"),
        "generated_at_ms": artifact.get("generated_at_ms"),
        "dataset_version": artifact.get("dataset_version"),
        "search_id": artifact.get("search_id"),
        "autonomy": {
            "worker": autonomy.get("worker"),
            "scheduler_task": autonomy.get("scheduler_task"),
            "autonomous_search_running": bool(
                autonomy.get("autonomous_search_running", False)
            ),
            "requires_developer_ai": False,
            "next_cycle_expected_ms": autonomy.get("next_cycle_expected_ms"),
        },
        "data_admission": data_admission if isinstance(data_admission, dict) else {},
        "feature_quality": {
            "status": feature_quality.get("status")
            if isinstance(feature_quality, dict)
            else None,
            "generated_at_ms": feature_quality.get("generated_at_ms")
            if isinstance(feature_quality, dict)
            else None,
            "drift_status": drift.get("status") if isinstance(drift, dict) else None,
        },
        "executable_depth_capacity": (
            {
                field: capacity.get(field)
                for field in (
                    "status",
                    "passed",
                    "evidence_ready",
                    "target_aum_passed",
                    "shadow_sizing_ready",
                    "history_span_ms",
                    "feature_age_ms",
                    "instrument_count",
                    "conservative_depth_usd",
                    "max_supported_aum_usd",
                    "target_aum_usd",
                    "shadow_aum_usd",
                    "participation_rate",
                    "legs_per_side",
                    "reasons",
                )
            }
            if isinstance(capacity, dict)
            else {}
        ),
        "funnel": funnel if isinstance(funnel, dict) else {},
        "families": families if isinstance(families, list) else [],
        "top_candidates": candidate_views,
        "global_trial_count": artifact.get("global_trial_count"),
        "registry": {
            "chain_valid": True,
            "event_count": registry.get("event_count"),
        },
        "remaining_gates": artifact.get("remaining_gates", []),
        "blockers": [],
        "forward_shadow": _realtime_alpha_forward_shadow_payload(),
        "execution_paper": _realtime_alpha_execution_paper_payload(),
        "safety": dict(_REALTIME_ALPHA_SAFE_BOUNDARY),
    }


def _dry_run_watch_paths() -> tuple[str, ...]:
    """Files whose durable changes should wake the dry-run event stream."""
    return (
        _DRY_RUN_DB,
        _FORWARD_TRACK_STATUS,
        _FACTORY_LOOP_STATUS,
        _CANDIDATE_ADMISSION_STATUS,
        _FORWARD_CANDIDATE_EVALUATIONS,
        _ALPHA_PRODUCTION_WORKFLOW,
        _PAPER_RESEARCH_RISK_BUNDLE,
        _LIVE_REFRESH_STATUS,
        _SCHEDULER_WATCHDOG_STATUS,
        _PAPER_ORDER_PREVIEW,
        _L2_PAPER_OPERATOR,
        _PAPER_REALTIME,
        _PAPER_MULTIVENUE_SOR,
        _PAPER_HEALTH,
        _PAPER_10K_REALTIME,
        _PAPER_10K_HEALTH,
        _PAPER_SHADOW_AUTO,
        _PAPER_SHADOW_REALTIME,
        _PAPER_OPERATIONAL_EVIDENCE,
        _REALTIME_ALPHA_DISCOVERY,
        _REALTIME_ALPHA_FORWARD_SHADOW,
        _REALTIME_ALPHA_EXECUTION_PAPER,
    )


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


def _alpha_pipeline_payload(
    forward_track: Dict,
    paper_realtime: Dict,
) -> Dict:
    """Expose the actual discovery-to-paper funnel without collapsing stages.

    Forward-paper challengers and execution-grade paper books are intentionally
    different populations.  This projection makes the current hand-off break
    machine-readable: the scheduled factory produces research evidence, but
    its retained artifact explicitly does not drive lifecycle admission.
    """
    factory = _load_json_object(_FACTORY_LOOP_STATUS) or {}
    admission = _load_json_object(_CANDIDATE_ADMISSION_STATUS) or {}
    evaluations = _load_json_object(_FORWARD_CANDIDATE_EVALUATIONS) or {}
    evaluation_rows = evaluations.get("alphas")
    evaluation_rows = evaluation_rows if isinstance(evaluation_rows, dict) else {}
    risk_bundle = _load_json_object(_PAPER_RESEARCH_RISK_BUNDLE) or {}
    risk_rows = risk_bundle.get("alphas")
    risk_rows = risk_rows if isinstance(risk_rows, dict) else {}
    workflow = _load_json_object(_ALPHA_PRODUCTION_WORKFLOW) or {}
    workflow_rows_raw = workflow.get("candidates")
    workflow_rows_raw = workflow_rows_raw if isinstance(workflow_rows_raw, list) else []
    workflow_rows = {
        str(row.get("candidate_id")): row
        for row in workflow_rows_raw
        if isinstance(row, dict) and isinstance(row.get("candidate_id"), str)
    }
    active_workflow_ids = workflow.get("active_candidate_ids")
    workflow_verified = bool(
        workflow.get("schema_version") == 1
        and workflow.get("authority") == "ALPHA_PRODUCTION_WORKFLOW"
        and workflow.get("status") == "VERIFIED"
        and isinstance(active_workflow_ids, list)
        and active_workflow_ids
        and active_workflow_ids == list(dict.fromkeys(active_workflow_ids))
        and all(
            isinstance(alpha_id, str)
            and isinstance(workflow_rows.get(alpha_id), dict)
            and isinstance(workflow_rows[alpha_id].get("identity"), dict)
            and workflow_rows[alpha_id].get("identity_hash")
            == stable_hash(workflow_rows[alpha_id]["identity"])
            for alpha_id in active_workflow_ids
        )
        and isinstance(workflow.get("safety"), dict)
        and workflow["safety"].get("live_allowed") is False
        and workflow["safety"].get("order_submission_allowed") is False
    )
    discovered = factory.get("discovered_chain")
    discovered = discovered if isinstance(discovered, list) else []
    raw_forward = forward_track.get("alphas")
    forward_rows = raw_forward if isinstance(raw_forward, list) else []
    if workflow_verified:
        forward_by_id = {
            str(row.get("alpha_id")): row
            for row in forward_rows
            if isinstance(row, dict) and isinstance(row.get("alpha_id"), str)
        }
        forward_rows = [
            {
                "alpha_id": alpha_id,
                **dict(forward_by_id.get(alpha_id) or {}),
            }
            for alpha_id in active_workflow_ids
        ]
    realtime_alphas = paper_realtime.get("alphas")
    realtime_alphas = realtime_alphas if isinstance(realtime_alphas, dict) else {}
    # The realtime artifact also carries visible FORWARD_PENDING challenger
    # rows.  Only a persisted mark row is an execution-paper account; presence
    # in the candidate map alone must never inflate execution counts.
    execution_ids = {
        str(alpha_id)
        for alpha_id, realtime_row in realtime_alphas.items()
        if isinstance(realtime_row, dict)
        and realtime_row.get("status") == "PAPER_MARKED"
    }

    candidates: list[dict[str, Any]] = []
    for row in forward_rows:
        if not isinstance(row, dict) or not isinstance(row.get("alpha_id"), str):
            continue
        alpha_id = str(row["alpha_id"])
        workflow_row = workflow_rows.get(alpha_id) if workflow_verified else None
        workflow_bundle = (
            workflow_row.get("workflow") if isinstance(workflow_row, dict) else None
        )
        workflow_bundle = workflow_bundle if isinstance(workflow_bundle, dict) else {}
        workflow_gates = {
            str(item.get("gate")): item
            for item in workflow_bundle.get("gates", [])
            if isinstance(item, dict) and isinstance(item.get("gate"), str)
        }
        workflow_stage = workflow_bundle.get("stage")
        execution_evidence = workflow_gates.get("EXECUTION_PAPER", {})
        if workflow_verified:
            execution_paper = bool(
                workflow_stage == "EXECUTION_PAPER"
                or execution_evidence.get("status")
                in {"PAPER_MARKED", "LEGACY_DIAGNOSTIC"}
            )
            legacy_diagnostic_paper = bool(
                workflow_bundle.get("legacy_diagnostic") is True
            )
        else:
            execution_paper = alpha_id in execution_ids
            legacy_diagnostic_paper = (
                execution_paper and alpha_id in _LEGACY_DIAGNOSTIC_PAPER_IDS
            )
        evaluation_row = evaluation_rows.get(alpha_id)
        evaluation_row = evaluation_row if isinstance(evaluation_row, dict) else {}
        evaluation_ref = evaluation_row.get("evaluation")
        evaluation_ref = evaluation_ref if isinstance(evaluation_ref, dict) else {}
        risk_row = risk_rows.get(alpha_id)
        risk_row = risk_row if isinstance(risk_row, dict) else {}
        maturity = (
            workflow_bundle.get("observed_forward_periods")
            if workflow_verified
            else row.get("n_forward_periods")
        )
        try:
            maturity_periods = int(maturity) if maturity is not None else None
        except (TypeError, ValueError):
            maturity_periods = None
        if workflow_verified:
            blockers = list(workflow_bundle.get("blockers") or [])
        else:
            blockers = []
            if legacy_diagnostic_paper:
                blockers.append("LEGACY_DIAGNOSTIC_NOT_DEPLOYMENT_ELIGIBLE")
            elif not execution_paper:
                if evaluation_row.get("status") != "VERIFIED" or evaluation_ref.get("verified") is not True:
                    blockers.append("INDEPENDENT_EVALUATION_PENDING")
                if evaluation_ref.get("verdict") != "PASS":
                    blockers.append("INDEPENDENT_EVALUATION_NOT_PASS")
                if maturity_periods is None or maturity_periods < _EVIDENCE_GATE_WEEKS:
                    blockers.append("FORWARD_EVIDENCE_MATURITY_PENDING")
                if risk_row.get("status") != "READY":
                    blockers.append("FULL_NINE_ENGINE_RISK_PACKET_PENDING")
                if not blockers:
                    blockers.append("NOT_IN_EXECUTION_PAPER_UNIVERSE")
        candidates.append(
            {
                **row,
                "stage": (
                    "LEGACY_DIAGNOSTIC_PAPER"
                    if legacy_diagnostic_paper
                    else str(workflow_stage)
                    if workflow_verified and workflow_stage
                    else "EXECUTION_PAPER" if execution_paper
                    else "FORWARD_PAPER"
                ),
                "paper_class": (
                    "LEGACY_DIAGNOSTIC_PAPER"
                    if legacy_diagnostic_paper
                    else "CANDIDATE_EXECUTION_PAPER"
                    if execution_paper
                    else "FORWARD_CHALLENGER"
                ),
                "execution_paper": execution_paper,
                "deployment_eligible": False,
                "blockers": blockers,
                "workflow_identity_hash": (
                    workflow_row.get("identity_hash")
                    if isinstance(workflow_row, dict)
                    else None
                ),
                "workflow_bundle_hash": workflow_bundle.get("bundle_hash"),
                "execution_paper_eligible": bool(
                    workflow_bundle.get("eligible_for_execution_paper") is True
                ),
                "independent_evaluation": {
                    "status": evaluation_row.get("status", "NOT_AVAILABLE"),
                    "verified": evaluation_ref.get("verified") is True,
                    "verdict": evaluation_ref.get("verdict"),
                    "run_id": evaluation_ref.get("run_id"),
                },
                "risk_packet": {
                    "status": risk_row.get("status", "NOT_AVAILABLE"),
                    "decision_basis": (
                        risk_row.get("provenance", {}).get("decision_basis")
                        if isinstance(risk_row.get("provenance"), dict)
                        else None
                    ),
                    "input_evidence_status": risk_row.get(
                        "input_evidence_status",
                        "READY" if risk_row.get("status") == "READY" else "PENDING",
                    ),
                    "input_fields": sorted(
                        str(field)
                        for field in (
                            risk_row.get("inputs", {}).keys()
                            if isinstance(risk_row.get("inputs"), dict)
                            else ()
                        )
                    ),
                    "admission_blockers": list(
                        risk_row.get("admission_blockers") or []
                    ),
                    "missing": list(risk_row.get("missing_inputs") or []),
                    "reasons": dict(risk_row.get("missing_reasons") or {}),
                },
                "maturity": {
                    "observed_periods": maturity_periods,
                    "required_periods": _EVIDENCE_GATE_WEEKS,
                },
            }
        )

    factory_generated = factory.get("generated_at")
    if isinstance(factory_generated, (int, float)) and not isinstance(factory_generated, bool):
        factory_generated_ms = int(factory_generated)
        if factory_generated_ms < 10_000_000_000:
            factory_generated_ms *= 1000
    else:
        factory_generated_ms = None
    containment = str(factory.get("containment", ""))
    admission_registry = admission.get("registry")
    admission_registry = admission_registry if isinstance(admission_registry, dict) else {}
    handoff_connected = bool(
        factory.get("schema_version") == 2
        and factory.get("handoff_contract") == "VERIFIED_FACTORY_PASS_TO_FORWARD_PAPER"
        and admission.get("status") == "PASS"
        and admission_registry.get("status") == "VERIFIED"
    )
    forward_count = len(candidates)
    execution_count = sum(row["execution_paper"] for row in candidates)
    legacy_execution_count = sum(
        row["paper_class"] == "LEGACY_DIAGNOSTIC_PAPER" for row in candidates
    )
    candidate_execution_count = execution_count - legacy_execution_count
    return {
        "source": "BACKEND_COMPUTED_ALPHA_PIPELINE",
        "read_only": True,
        "factory": {
            "snapshot_present": bool(factory),
            "generated_at": factory_generated_ms,
            "rounds": factory.get("rounds"),
            "dry_limit": factory.get("dry_limit"),
            "data_dimensions": len(factory.get("fields", []))
            if isinstance(factory.get("fields"), list)
            else 0,
            "survivors": len(discovered),
            "verdict": factory.get("verdict"),
            "containment": containment or None,
        },
        "counts": {
            "forward_paper": forward_count,
            "execution_paper": execution_count,
            "candidate_execution_paper": candidate_execution_count,
            "legacy_diagnostic_paper": legacy_execution_count,
            "forward_only": max(0, forward_count - execution_count),
            "factory_survivors": len(discovered),
        },
        "candidates": candidates,
        "workflow": {
            "authority_present": bool(workflow),
            "verified": workflow_verified,
            "population_count": workflow.get("population_count"),
            "event_count": workflow.get("event_count"),
            "execution_paper_eligible_count": len(
                workflow.get("execution_paper_eligible_ids") or []
            )
            if workflow_verified
            else 0,
            "release_commit": (
                (workflow.get("reconciliation") or {}).get("release_commit")
                if isinstance(workflow.get("reconciliation"), dict)
                else None
            ),
            "source_as_of_ms": (
                (workflow.get("reconciliation") or {}).get("source_as_of_ms")
                if isinstance(workflow.get("reconciliation"), dict)
                else None
            ),
        },
        "handoff": {
            "factory_to_forward_registry_connected": handoff_connected,
            "forward_to_execution_dynamic": (
                workflow_verified
                or (forward_count == candidate_execution_count and forward_count > 0)
            ),
            "status": (
                "CONNECTED"
                if handoff_connected
                and (
                    workflow_verified
                    or forward_count == candidate_execution_count
                )
                else "GAPPED"
            ),
            "gap_codes": [
                code
                for code, present in (
                    ("FACTORY_TO_FORWARD_REGISTRY_NOT_VERIFIED", bool(factory) and not handoff_connected),
                    (
                        "FORWARD_UNIVERSE_NOT_CONSUMED_BY_EXECUTION_PAPER",
                        not workflow_verified and forward_count > candidate_execution_count,
                    ),
                    (
                        "UNIQUE_ALPHA_WORKFLOW_NOT_VERIFIED",
                        not workflow_verified,
                    ),
                )
                if present
            ],
            "boundary_codes": [
                code
                for code, present in (
                    (
                        "LEGACY_DIAGNOSTIC_PAPER_QUARANTINED",
                        legacy_execution_count > 0,
                    ),
                )
                if present
            ],
        },
        "safety": {
            "paper_only": True,
            "deployment_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
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


def _paper_operational_payload(path: str | None = None) -> Dict:
    """Project the append-only Production Paper window without widening authority."""
    snapshot = _load_json_object(path or _PAPER_OPERATIONAL_EVIDENCE)
    empty = {
        "snapshot_present": False,
        "status": "NO_SNAPSHOT",
        "chain_valid": False,
        "window": {
            "engineering_smoke_ready": False,
            "production_window_ready": False,
            "accepted_cycle_count": 0,
            "history_span_ms": 0,
            "success_ratio": 0.0,
            "drills": {},
            "planned_restart_recoveries": [],
            "blockers": ["PAPER_OPERATIONAL_EVIDENCE_MISSING"],
        },
        "safety": {
            "paper_only": True,
            "fault_injection_isolated": True,
            "credentials_accessed": False,
            "broker_connectivity": False,
            "broker_order_submission": False,
        },
    }
    if snapshot is None:
        return empty
    safety = snapshot.get("safety")
    verdict = snapshot.get("verdict")
    window = snapshot.get("window")
    boundary_valid = bool(
        snapshot.get("flow") == "PRODUCTION_PAPER_OPERATIONAL_EVIDENCE"
        and snapshot.get("chain_valid") is True
        and isinstance(window, dict)
        and isinstance(safety, dict)
        and safety.get("paper_only") is True
        and safety.get("fault_injection_isolated") is True
        and safety.get("credentials_accessed") is False
        and safety.get("broker_connectivity") is False
        and safety.get("broker_order_submission") is False
        and isinstance(verdict, dict)
        and verdict.get("promotion_eligible") is False
        and verdict.get("live_allowed") is False
        and verdict.get("order_submission_allowed") is False
    )
    if not boundary_valid:
        blocked = dict(empty)
        blocked.update({"snapshot_present": True, "status": "SAFE_BLOCKED"})
        blocked["window"] = dict(empty["window"])
        blocked["window"]["blockers"] = ["PAPER_OPERATIONAL_EVIDENCE_INVALID"]
        return blocked
    return {
        "snapshot_present": True,
        "generated_at_ms": snapshot.get("generated_at_ms"),
        "status": str(window.get("status", "MATURING")),
        "chain_valid": True,
        "policy": snapshot.get("policy", {}),
        "window": {
            key: window.get(key)
            for key in (
                "engineering_smoke_ready",
                "production_window_ready",
                "cycle_count",
                "accepted_cycle_count",
                "success_ratio",
                "history_span_ms",
                "max_gap_ms",
                "latest_age_ms",
                "drills",
                "planned_restart_recoveries",
                "blockers",
            )
        },
        "safety": dict(empty["safety"]),
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


def _paper_shadow_payload(
    *,
    auto_path: str = _PAPER_SHADOW_AUTO,
    realtime_path: str = _PAPER_SHADOW_REALTIME,
) -> Dict:
    """Expose the isolated, non-promotable automated shadow-paper account."""
    auto = _load_json_object(auto_path)
    realtime = _load_json_object(realtime_path)
    auto_verdict = auto.get("verdict", {}) if auto else {}
    realtime_verdict = realtime.get("verdict", {}) if realtime else {}
    auto_safety = auto.get("safety", {}) if auto else {}
    realtime_safety = realtime.get("safety", {}) if realtime else {}
    persistence = realtime.get("persistence", {}) if realtime else {}
    ledger = realtime.get("paper_ledger", {}) if realtime else {}
    alphas = realtime.get("alphas", {}) if realtime else {}
    previews = auto.get("alphas", {}) if auto else {}
    history = realtime.get("history", []) if realtime else []
    auto_universe = auto.get("candidate_universe", {}) if auto else {}
    realtime_universe = realtime.get("candidate_universe", {}) if realtime else {}
    auto_expected = (
        auto_universe.get("expected", []) if isinstance(auto_universe, dict) else []
    )
    realtime_expected = (
        realtime_universe.get("expected", [])
        if isinstance(realtime_universe, dict)
        else []
    )
    candidate_coverage_valid = bool(
        isinstance(auto_expected, list)
        and auto_expected
        and isinstance(realtime_expected, list)
        and set(auto_expected) == set(realtime_expected)
        and len(auto_expected) == len(set(auto_expected))
        and isinstance(alphas, dict)
        and set(alphas) == set(auto_expected)
        and isinstance(previews, dict)
        and set(previews) == set(auto_expected)
        and auto_verdict.get("candidate_coverage_complete") is True
        and realtime_verdict.get("candidate_coverage_complete") is True
    )
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
        and candidate_coverage_valid
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
            "candidate_coverage_complete": candidate_coverage_valid,
            "candidate_count": len(auto_expected) if candidate_coverage_valid else 0,
            "official_paper_ready": False,
            "promotion_eligible": False,
            "forward_evidence_eligible": False,
            "deployment_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "alphas": alphas if isinstance(alphas, dict) else {},
        "previews": previews if isinstance(previews, dict) else {},
        "candidate_universe": {
            "authority": "ALPHA_PRODUCTION_WORKFLOW",
            "expected": auto_expected if isinstance(auto_expected, list) else [],
            "expected_count": len(auto_expected)
            if isinstance(auto_expected, list)
            else 0,
            "running_count": len(alphas) if isinstance(alphas, dict) else 0,
            "coverage_complete": candidate_coverage_valid,
        },
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
    try:
        route_tca_history = build_paper_tca_history(runs)
    except Exception:
        route_tca_history = {
            "schema_version": 1,
            "simulation_only": True,
            "status": "INVALID_RETAINED_EVIDENCE",
            "run_count": 0,
            "record_count": 0,
            "venue_count": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "weighted_shortfall_bps": None,
            "points": [],
            "venues": [],
        }
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
            "workflow_admission_valid": bool(
                paper_verdict.get("workflow_admission_valid", False)
            ),
            "workflow_authorized_ids": [
                str(alpha_id)
                for alpha_id in paper_verdict.get("workflow_authorized_ids", [])
                if isinstance(alpha_id, str) and alpha_id
            ],
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
        "route_tca_history": route_tca_history,
        "paper_multivenue_sor": _load_json_object(_PAPER_MULTIVENUE_SOR),
        "paper_order_preview": paper_preview,
        "paper_operator": paper_operator,
        "paper_realtime": paper_realtime,
        "alpha_pipeline": _alpha_pipeline_payload(forward_track, paper_realtime),
        "realtime_alpha_autonomy": _realtime_alpha_autonomy_payload(),
        "paper_shadow": _paper_shadow_payload(),
        "paper_operational": _paper_operational_payload(),
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


def _forward_track_from_evidence(
    now_ms: int,
    *,
    evidence_path: str = _FORWARD_EVIDENCE_BASELINE,
) -> Dict:
    """Expose only the attested baseline; never fall back to the legacy status cache."""
    evidence = _load_json_object(evidence_path)
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


def _live_trading_payload(
    now_ms: int | None = None,
    *,
    forward_evidence_path: str = _FORWARD_EVIDENCE_BASELINE,
) -> Dict:
    """Aggregate forward-paper and testnet status as read-only diagnostics."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    forward_track = _forward_track_from_evidence(
        now,
        evidence_path=forward_evidence_path,
    )
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
