"""Read-only model and data drift monitoring HTTP route."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List

from fastapi import APIRouter

from services.console.api.data_catalog import _data_catalog_payload
from services.console.api.data_port import data_catalog_runtime
from services.console.api.model_evidence_views import (
    _model_risk_review_payload,
)
from services.console.api.review_access_views import (
    _reviewer_workflow_payload,
)
from services.data.distribution_drift import (
    BLOCK as DISTRIBUTION_BLOCK,
    PROVENANCE as DISTRIBUTION_PROVENANCE,
    WARN as DISTRIBUTION_WARN,
    DistributionDriftPolicy,
    evaluate_distribution_drift,
)


@dataclass(frozen=True)
class MonitoringViewDependencies:
    alpha_candidates: Callable[..., List[Dict]]
    has_real_factors: Callable[[], bool]


class MonitoringViewRuntime:
    def __init__(self) -> None:
        self._dependencies: MonitoringViewDependencies | None = None

    def configure(
        self,
        *,
        alpha_candidates: Callable[..., List[Dict]],
        has_real_factors: Callable[[], bool],
    ) -> None:
        dependencies = MonitoringViewDependencies(
            alpha_candidates=alpha_candidates,
            has_real_factors=has_real_factors,
        )
        if self._dependencies is None:
            self._dependencies = dependencies
            return
        if self._dependencies != dependencies:
            raise RuntimeError("monitoring view runtime is already configured")

    def require(self) -> MonitoringViewDependencies:
        if self._dependencies is None:
            raise RuntimeError("monitoring view runtime must be configured by the console composition root")
        return self._dependencies


monitoring_view_runtime = MonitoringViewRuntime()
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DISTRIBUTION_EVIDENCE = (
    _PROJECT_ROOT / "evidence" / "feature_distribution_drift_baseline.json"
)


def configure_monitoring_views(
    *,
    alpha_candidates: Callable[..., List[Dict]],
    has_real_factors: Callable[[], bool],
) -> None:
    monitoring_view_runtime.configure(
        alpha_candidates=alpha_candidates,
        has_real_factors=has_real_factors,
    )


def _alpha_candidates(count: int = 12) -> List[Dict]:
    return monitoring_view_runtime.require().alpha_candidates(count=count)


def _has_real_factors() -> bool:
    return monitoring_view_runtime.require().has_real_factors()


def _distribution_series() -> Dict[str, List[float]]:
    port = data_catalog_runtime.require()
    getter = getattr(port, "enumerate_distribution_series", None)
    if getter is None:
        return {}
    try:
        return getter()
    except Exception:
        return {}


def _distribution_evidence() -> Dict:
    """Load the tracked, reproducibly verified PIT drift assessment.

    This is a historical diagnostic fallback for clean deployments that do not
    carry the operator's local market-data cache.  It must never be presented
    as a realtime assessment or as a load-bearing admission result.
    """
    try:
        document = json.loads(_DISTRIBUTION_EVIDENCE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    assessment = document.get("assessment")
    panel = document.get("panel")
    policy = document.get("policy")
    source = document.get("source")
    if (
        document.get("schema_version") != 1
        or document.get("load_bearing") is not False
        or not isinstance(document.get("provenance"), str)
        or not isinstance(assessment, dict)
        or not isinstance(assessment.get("assessments"), dict)
        or not isinstance(panel, dict)
        or not isinstance(policy, dict)
        or not isinstance(source, dict)
    ):
        return {}
    return document


def _monitoring_drift_payload() -> Dict:
    candidates = _alpha_candidates()
    data_catalog = _data_catalog_payload()
    distribution_series = _distribution_series()
    model_risk = _model_risk_review_payload()
    reviewer_workflow = _reviewer_workflow_payload()
    monitors = []
    base_time = 1_781_222_400

    def status_from_score(score: float, warn: float, breach: float) -> str:
        if score >= breach:
            return "BREACH"
        if score >= warn:
            return "WATCH"
        return "OK"

    # ALPHA_DECAY monitors are ENGINE-COMPUTED by the real Phase-6 degradation monitor
    # (services.alpha.degradation_monitor) — it diffs live-vs-backtest IC/return/slippage/turnover/
    # drawdown HOLISTICALLY and recommends OK/DEGRADED/PAUSED/RETIRED. Inputs here are deterministic
    # (no live feed in L2), but the verdict comes from the actual engine, not a hand-coded formula. It
    # only RECOMMENDS — no state is driven; live authorization stays disallowed.
    #
    # `metric` is "health_drift" (multi-metric), and `drift_score` is a normalized health-pressure
    # gauge BANDED to the engine verdict so the panel is internally coherent: the drift bar length,
    # its status colour, the warn/breach thresholds (0.18/0.32) and the status chip never disagree
    # (OK < warn ≤ DEGRADED < breach ≤ PAUSED/RETIRED). Within each band the score still tracks the
    # real measured IC drift, so rows differ and the number stays meaningful. `current` keeps the live
    # IC as a reference — when IC is fine but status is BREACH, the remediation names the real cause
    # (e.g. slippage), which is exactly what the engine surfaces.
    from services.alpha.degradation_monitor import monitor_alpha_health

    _REC_TO_STATUS = {"OK": "OK", "DEGRADED": "WATCH", "PAUSED": "BREACH", "RETIRED": "BREACH"}
    _WARN, _BREACH = 0.18, 0.32
    _REC_BAND = {"OK": (0.02, 0.16), "DEGRADED": (_WARN, 0.30), "PAUSED": (0.34, 0.48), "RETIRED": (0.50, 0.62)}
    for idx, alpha in enumerate(candidates[:8]):
        baseline = round(alpha["ic"], 4)
        bt_return = round(baseline * 2.0, 4)
        live_ic = round(baseline - 0.006 * (idx % 4) + (0.003 if idx % 5 == 0 else 0.0), 4)
        backtest = {"ic": baseline, "return": bt_return, "slippage_bps": 5.0, "turnover": 0.3}
        live = {
            "ic": live_ic,
            "return": round(bt_return * (0.4 if idx % 4 == 3 else 0.9), 4),
            "slippage_bps": round(5.0 * (2.2 if idx % 6 == 2 else 1.1), 2),
            "turnover": round(0.3 * (1.0 + 0.2 * (idx % 3)), 3),
            "drawdown": round(0.1 + 0.06 * (idx % 5), 3),
        }
        health = monitor_alpha_health(backtest, live)  # REAL degradation-monitor engine
        rec = health["recommendation"]
        status = _REC_TO_STATUS.get(rec, "OK")
        # measured IC drift → 0..1 wiggle, then clamp into the verdict's band (coherent with status)
        ic_drift = abs(live_ic - baseline) / max(abs(baseline), 0.01)
        lo, hi = _REC_BAND[rec]
        drift_score = round(lo + (hi - lo) * min(1.0, ic_drift / 0.5), 3)
        remediation = (
            f"[degradation_monitor:{rec}] {health['alerts'][0]['detail']}"
            if health["alerts"]
            else "Continue monitoring"
        )
        monitors.append(
            {
                "monitor_id": f"MON-ALPHA-{idx + 1:03d}",
                "category": "ALPHA_DECAY",
                "subject_id": alpha["candidate_id"],
                "metric": "health_drift",
                "baseline": baseline,
                "current": live_ic,
                "drift_score": drift_score,
                "warn_threshold": _WARN,
                "breach_threshold": _BREACH,
                "status": status,
                "owner": "Alpha Research",
                "evidence_ref": f"exp-{alpha['candidate_id']}-v{idx % 3 + 1:03d}",
                "last_checked": base_time + idx * 240,
                "remediation": remediation,
            }
        )

    distribution_policy = DistributionDriftPolicy()
    distribution_status = {DISTRIBUTION_WARN: "WATCH", DISTRIBUTION_BLOCK: "BREACH"}
    for idx, (subject_id, values) in enumerate(sorted(distribution_series.items())[:8]):
        assessment = evaluate_distribution_drift(values, policy=distribution_policy)
        status = distribution_status.get(assessment.status, "OK")
        score = assessment.ks_distance if assessment.ks_distance is not None else 1.0
        monitors.append(
            {
                "monitor_id": f"MON-DISTRIBUTION-{idx + 1:03d}",
                "category": "DATA_DISTRIBUTION_DRIFT",
                "subject_id": subject_id,
                "metric": "ks_distance",
                "baseline": assessment.baseline_mean if assessment.baseline_mean is not None else 0.0,
                "current": assessment.recent_mean if assessment.recent_mean is not None else 0.0,
                "drift_score": round(score, 3),
                "warn_threshold": distribution_policy.warn_ks,
                "breach_threshold": distribution_policy.block_ks,
                "status": status,
                "owner": "Data Platform",
                "evidence_ref": f"{DISTRIBUTION_PROVENANCE}:{subject_id}:{assessment.end_index}",
                "last_checked": base_time + 1_000 + idx * 240,
                "provenance": DISTRIBUTION_PROVENANCE,
                "remediation": (
                    "Investigate PIT dataset distribution shift and rerun data quality checks"
                    if status != "OK"
                    else "Continue monitoring"
                ),
            }
        )

    distribution_evidence = _distribution_evidence() if not distribution_series else {}
    if distribution_evidence:
        evidence_assessments = distribution_evidence["assessment"]["assessments"]
        evidence_policy = distribution_evidence["policy"]
        evidence_provenance = distribution_evidence["provenance"]
        source = distribution_evidence["source"]
        evidence_time_ms = distribution_evidence["panel"].get("last_event_time")
        evidence_time = (
            evidence_time_ms // 1_000
            if isinstance(evidence_time_ms, int)
            else base_time
        )
        for idx, (subject_id, assessment) in enumerate(
            sorted(evidence_assessments.items())[:8]
        ):
            if not isinstance(assessment, dict):
                continue
            status = distribution_status.get(str(assessment.get("status")), "OK")
            score = assessment.get("ks_distance")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                continue
            monitors.append(
                {
                    "monitor_id": f"MON-DISTRIBUTION-EVIDENCE-{idx + 1:03d}",
                    "category": "DATA_DISTRIBUTION_DRIFT",
                    "subject_id": subject_id,
                    "metric": "ks_distance",
                    "baseline": assessment.get("baseline_mean", 0.0),
                    "current": assessment.get("recent_mean", 0.0),
                    "drift_score": round(float(score), 3),
                    "warn_threshold": evidence_policy.get(
                        "warn_ks", distribution_policy.warn_ks
                    ),
                    "breach_threshold": evidence_policy.get(
                        "block_ks", distribution_policy.block_ks
                    ),
                    "status": status,
                    "owner": "Data Platform",
                    "evidence_ref": (
                        f"{source.get('manifest_sha256', 'UNKNOWN')}:{subject_id}:"
                        f"{assessment.get('end_index', 'UNKNOWN')}"
                    ),
                    "last_checked": evidence_time + idx * 240,
                    "provenance": evidence_provenance,
                    "realtime": False,
                    "load_bearing": False,
                    "remediation": (
                        "Refresh the verified PIT baseline and investigate distribution shift"
                        if status != "OK"
                        else "Continue historical baseline monitoring"
                    ),
                }
            )

    for idx, feature in enumerate(data_catalog["features"][:8]):
        baseline = feature["quality_score"]
        current = round(max(0.0, baseline - 0.012 * (idx % 4)), 3)
        drift_score = round(max(0.0, baseline - current), 3)
        status = status_from_score(drift_score, 0.02, 0.04)
        monitors.append(
            {
                "monitor_id": f"MON-FEATURE-{idx + 1:03d}",
                "category": "FEATURE_DRIFT",
                "subject_id": feature["feature_id"],
                "metric": "quality_score_delta",
                "baseline": baseline,
                "current": current,
                "drift_score": drift_score,
                "warn_threshold": 0.02,
                "breach_threshold": 0.04,
                "status": status,
                "owner": "Research Platform",
                "evidence_ref": feature["lineage_hash"],
                "last_checked": base_time + 2_000 + idx * 240,
                "provenance": "SYNTHETIC_FEATURE_VALUES",
                "remediation": "Run feature distribution audit and point-in-time sample check"
                if status != "OK"
                else "Continue monitoring",
            }
        )

    # Cap DATA_FRESHNESS to the staleest few so the page stays readable when the catalog enumerates many
    # real datasets (crypto_daily = 42); rank by freshness so any actually-stale dataset still surfaces.
    for idx, dataset in enumerate(
        sorted(data_catalog["datasets"], key=lambda d: d["freshness_hours"], reverse=True)[:8]
    ):
        current = dataset["freshness_hours"]
        drift_score = round(current / 24.0, 3)
        status = "BREACH" if current > 24 else "WATCH" if current > 12 else "OK"
        monitors.append(
            {
                "monitor_id": f"MON-DATA-{idx + 1:03d}",
                "category": "DATA_FRESHNESS",
                "subject_id": dataset["dataset_id"],
                "metric": "freshness_hours",
                "baseline": 6.0,
                "current": current,
                "drift_score": drift_score,
                "warn_threshold": 12.0,
                "breach_threshold": 24.0,
                "status": status,
                "owner": dataset["owner"],
                "evidence_ref": dataset["content_hash"],
                "last_checked": base_time + 4_000 + idx * 240,
                "remediation": "Refresh source snapshot and rerun data quality checks"
                if status != "OK"
                else "Continue monitoring",
            }
        )

    for idx, issue in enumerate(model_risk["issue_register"]):
        age_hours = 36 + idx * 24
        status = (
            "BREACH"
            if issue["severity"] == "HIGH" and age_hours >= 72
            else "WATCH"
            if issue["status"] == "OPEN"
            else "OK"
        )
        monitors.append(
            {
                "monitor_id": f"MON-MRM-{idx + 1:03d}",
                "category": "MODEL_RISK_AGING",
                "subject_id": issue["issue_id"],
                "metric": "issue_age_hours",
                "baseline": 0.0,
                "current": age_hours,
                "drift_score": round(age_hours / 120.0, 3),
                "warn_threshold": 48.0,
                "breach_threshold": 72.0,
                "status": status,
                "owner": issue["owner"],
                "evidence_ref": ",".join(issue["linked_models"]),
                "last_checked": base_time + 6_000 + idx * 240,
                "remediation": "Escalate model risk issue owner and update reviewer workflow"
                if status != "OK"
                else "Continue monitoring",
            }
        )

    for idx, task in enumerate(reviewer_workflow["tasks"]):
        overdue_hours = max(0, -task["due_in_hours"])
        status = "BREACH" if overdue_hours > 0 else "WATCH" if task["blocks_live_authorization"] else "OK"
        monitors.append(
            {
                "monitor_id": f"MON-SLA-{idx + 1:03d}",
                "category": "REVIEWER_SLA",
                "subject_id": task["task_id"],
                "metric": "overdue_hours",
                "baseline": 0.0,
                "current": overdue_hours,
                "drift_score": round(overdue_hours / max(task["sla_hours"], 1), 3),
                "warn_threshold": 0.0,
                "breach_threshold": 1.0,
                "status": status,
                "owner": task["assigned_reviewer"],
                "evidence_ref": task["evidence_ref"],
                "last_checked": base_time + 8_000 + idx * 240,
                "remediation": "Escalate overdue reviewer task"
                if status == "BREACH"
                else "Track blocking task closure",
            }
        )

    status_counts = {
        "OK": sum(1 for monitor in monitors if monitor["status"] == "OK"),
        "WATCH": sum(1 for monitor in monitors if monitor["status"] == "WATCH"),
        "BREACH": sum(1 for monitor in monitors if monitor["status"] == "BREACH"),
    }
    category_counts = {
        category: sum(1 for monitor in monitors if monitor["category"] == category)
        for category in sorted({monitor["category"] for monitor in monitors})
    }
    breach_monitors = [monitor for monitor in monitors if monitor["status"] == "BREACH"]
    watch_monitors = [monitor for monitor in monitors if monitor["status"] == "WATCH"]
    return {
        "monitoring_mode": "READ_ONLY_MONITORING_DRIFT",
        "summary": {
            "monitors": len(monitors),
            "ok": status_counts["OK"],
            "watch": status_counts["WATCH"],
            "breach": status_counts["BREACH"],
            "categories": len(category_counts),
            "blocking_monitors": len(breach_monitors),
            "avg_drift_score": round(statistics.mean(monitor["drift_score"] for monitor in monitors), 3),
            "live_authorization_allowed": False,
            "alpha_decay_engine": "degradation_monitor",
        },
        "status_counts": status_counts,
        "category_counts": category_counts,
        "monitors": monitors,
        "top_breaches": sorted(breach_monitors, key=lambda item: item["drift_score"], reverse=True)[:6],
        "watchlist": sorted(watch_monitors, key=lambda item: item["drift_score"], reverse=True)[:8],
        "monitoring_policy": {
            "read_only": True,
            "requires_owner": True,
            "requires_thresholds": True,
            "requires_evidence_ref": True,
            "mutable_alerts": False,
            "authorizes_live_trading": False,
            "distribution_drift": {
                "available": bool(distribution_series or distribution_evidence),
                "provenance": (
                    DISTRIBUTION_PROVENANCE
                    if distribution_series
                    else distribution_evidence.get(
                        "provenance", "NO_HONEST_INPUT"
                    )
                    if distribution_evidence
                    else "NO_HONEST_INPUT"
                ),
                "realtime": bool(distribution_series),
                "load_bearing": False,
                "baseline_window": (
                    distribution_policy.baseline_window
                    if distribution_series
                    else distribution_evidence.get("policy", {}).get(
                        "baseline_window", distribution_policy.baseline_window
                    )
                ),
                "recent_window": (
                    distribution_policy.recent_window
                    if distribution_series
                    else distribution_evidence.get("policy", {}).get(
                        "recent_window", distribution_policy.recent_window
                    )
                ),
            },
        },
    }


router = APIRouter()


@router.get("/api/v1/console/monitoring-drift")
async def monitoring_drift() -> Dict:
    payload = _monitoring_drift_payload()
    provenance = "PARTIAL_ALPHA_DECAY_REAL" if _has_real_factors() else "SYNTHETIC_DETERMINISTIC"
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": provenance,
        **payload,
    }
