"""Read-only candidate promotion-control HTTP route."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Callable, Dict, List

from fastapi import APIRouter

from services.console.api.backtest_evidence import scrub_backtest_payload
from services.console.api.common import short_hash
from services.console.api.incident_views import _incident_response_payload
from services.console.api.model_evidence_views import (
    _evidence_ledger_payload,
    _model_risk_review_payload,
)
from services.console.api.monitoring_views import _monitoring_drift_payload
from services.console.api.review_access_views import (
    _access_control_payload,
)


@dataclass(frozen=True)
class PromotionViewDependencies:
    alpha_candidates: Callable[..., List[Dict]]
    validation_reports: Callable[[], List[Dict]]
    paper_run: Callable[[str], Dict]
    factor_values: Callable[[int], List[float]]
    forward_returns: Callable[[List[float], int], List[float]]
    equity_curve: Callable[[List[float]], List[Dict]]
    risk_decision: Callable[[str, List[float], List[Dict], float], Dict]
    validation_decision: Callable[[Dict], str]


class PromotionViewRuntime:
    def __init__(self) -> None:
        self._dependencies: PromotionViewDependencies | None = None

    def configure(
        self,
        *,
        alpha_candidates: Callable[..., List[Dict]],
        validation_reports: Callable[[], List[Dict]],
        paper_run: Callable[[str], Dict],
        factor_values: Callable[[int], List[float]],
        forward_returns: Callable[[List[float], int], List[float]],
        equity_curve: Callable[[List[float]], List[Dict]],
        risk_decision: Callable[[str, List[float], List[Dict], float], Dict],
        validation_decision: Callable[[Dict], str],
    ) -> None:
        dependencies = PromotionViewDependencies(
            alpha_candidates=alpha_candidates,
            validation_reports=validation_reports,
            paper_run=paper_run,
            factor_values=factor_values,
            forward_returns=forward_returns,
            equity_curve=equity_curve,
            risk_decision=risk_decision,
            validation_decision=validation_decision,
        )
        if self._dependencies is None:
            self._dependencies = dependencies
            return
        if self._dependencies != dependencies:
            raise RuntimeError("promotion view runtime is already configured")

    def require(self) -> PromotionViewDependencies:
        if self._dependencies is None:
            raise RuntimeError("promotion view runtime must be configured by the console composition root")
        return self._dependencies


promotion_view_runtime = PromotionViewRuntime()
_short_hash = short_hash


def configure_promotion_views(
    *,
    alpha_candidates: Callable[..., List[Dict]],
    validation_reports: Callable[[], List[Dict]],
    paper_run: Callable[[str], Dict],
    factor_values: Callable[[int], List[float]],
    forward_returns: Callable[[List[float], int], List[float]],
    equity_curve: Callable[[List[float]], List[Dict]],
    risk_decision: Callable[[str, List[float], List[Dict], float], Dict],
    validation_decision: Callable[[Dict], str],
) -> None:
    promotion_view_runtime.configure(
        alpha_candidates=alpha_candidates,
        validation_reports=validation_reports,
        paper_run=paper_run,
        factor_values=factor_values,
        forward_returns=forward_returns,
        equity_curve=equity_curve,
        risk_decision=risk_decision,
        validation_decision=validation_decision,
    )


def _alpha_candidates(count: int = 12) -> List[Dict]:
    return promotion_view_runtime.require().alpha_candidates(count=count)


def _validation_reports() -> List[Dict]:
    return promotion_view_runtime.require().validation_reports()


def _paper_run(alpha_id: str) -> Dict:
    return promotion_view_runtime.require().paper_run(alpha_id)


def _factor_values(idx: int) -> List[float]:
    return promotion_view_runtime.require().factor_values(idx)


def _forward_returns(factors: List[float], idx: int) -> List[float]:
    return promotion_view_runtime.require().forward_returns(factors, idx)


def _equity_curve(returns: List[float]) -> List[Dict]:
    return promotion_view_runtime.require().equity_curve(returns)


def _risk_decision(
    alpha_id: str,
    returns: List[float],
    equity_points: List[Dict],
    capacity: float,
) -> Dict:
    return promotion_view_runtime.require().risk_decision(alpha_id, returns, equity_points, capacity)


def _validation_decision(alpha: Dict) -> str:
    return promotion_view_runtime.require().validation_decision(alpha)


def _promotion_control_payload_raw() -> Dict:
    candidates = _alpha_candidates(count=6)
    validation_reports = _validation_reports()
    validation_by_alpha = {report["alpha_id"]: report for report in validation_reports}
    model_risk = _model_risk_review_payload()
    monitoring = _monitoring_drift_payload()
    incidents_payload = _incident_response_payload()
    evidence_ledger = _evidence_ledger_payload()
    access_control = _access_control_payload()

    monitors_by_subject: Dict[str, List[Dict]] = {}
    for monitor in monitoring["monitors"]:
        monitors_by_subject.setdefault(monitor["subject_id"], []).append(monitor)
    incidents_by_subject: Dict[str, List[Dict]] = {}
    for incident in incidents_payload["incidents"]:
        incidents_by_subject.setdefault(incident["subject_id"], []).append(incident)
    model_by_alpha = {
        model["linked_component"]: model for model in model_risk["models"] if model["model_type"] == "ALPHA_MODEL"
    }
    ledger_root = evidence_ledger["root_hash"]
    access_clean = (
        access_control["summary"]["denied_dangerous_permissions"] >= 5
        and access_control["access_policy"]["authorizes_live_trading"] is False
        and access_control["access_policy"]["downloads_enabled"] is False
    )

    def gate_status(passed: bool, watch: bool = False) -> str:
        if passed:
            return "PASS"
        if watch:
            return "WATCH"
        return "FAIL"

    promotion_records = []
    all_gates = []
    for idx, alpha in enumerate(candidates):
        alpha_id = alpha["candidate_id"]
        validation = validation_by_alpha[alpha_id]
        paper = _paper_run(alpha_id)
        factors = _factor_values(idx)
        returns = _forward_returns(factors, idx)
        equity_points = _equity_curve(returns)
        risk_report = _risk_decision(alpha_id, returns, equity_points, alpha["capacity_estimate"])
        alpha_monitors = monitors_by_subject.get(alpha_id, [])
        alpha_incidents = incidents_by_subject.get(alpha_id, [])
        alpha_model = model_by_alpha.get(alpha_id)
        gates = []

        def add_gate(
            stage: str, status: str, owner: str, evidence_ref: str, score: float, reason: str, blocks: bool
        ) -> None:
            gate = {
                "gate_id": f"PG-{alpha_id.upper()}-{len(gates) + 1:02d}",
                "alpha_id": alpha_id,
                "stage": stage,
                "status": status,
                "owner": owner,
                "evidence_ref": evidence_ref,
                "score": round(score, 3),
                "reason": reason,
                "blocks_paper_replay": blocks
                and stage in {"ALPHA_SCREEN", "VALIDATION", "PAPER_REPLAY", "RISK_REVIEW"},
                "blocks_broker_test_env": status != "PASS",
                "blocks_live_authorization": True,
            }
            gates.append(gate)
            all_gates.append(gate)

        screen_decision = _validation_decision(alpha)
        add_gate(
            "ALPHA_SCREEN",
            gate_status(screen_decision == "APPROVED", screen_decision == "NEEDS_MORE_DATA"),
            "Alpha Research",
            _short_hash("screen", alpha_id, alpha["expression"], screen_decision),
            alpha["composite_score"],
            f"Screen decision {screen_decision}",
            screen_decision not in {"APPROVED", "NEEDS_MORE_DATA"},
        )
        add_gate(
            "VALIDATION",
            gate_status(validation["decision"] == "APPROVED", validation["decision"] == "NEEDS_MORE_DATA"),
            "Alpha Research",
            validation["run_id"],
            validation["gates_passed"] / max(validation["gates_total"], 1),
            "; ".join(validation["reasons"]) or f"Validation decision {validation['decision']}",
            validation["decision"] not in {"APPROVED", "NEEDS_MORE_DATA"},
        )
        add_gate(
            "PAPER_REPLAY",
            gate_status(paper["status"] == "PAPER_PASSED", paper["status"] == "PAPER_RUNNING"),
            "Trading Research",
            paper["run_id"],
            min(1.0, max(0.0, paper["metrics"].get("sharpe", 0.0) / 2.0)),
            f"Paper status {paper['status']} with Sharpe {round(paper['metrics'].get('sharpe', 0.0), 3)}",
            paper["status"] not in {"PAPER_PASSED", "PAPER_RUNNING"},
        )
        add_gate(
            "RISK_REVIEW",
            gate_status(
                risk_report["decision"] == "RISK_APPROVED", risk_report["decision"] == "RISK_APPROVED_WITH_LIMITS"
            ),
            "Risk",
            risk_report["reproducibility_hash"],
            risk_report["gates_passed"] / max(risk_report["gates_total"], 1),
            "; ".join(risk_report["reasons"]) or f"Risk decision {risk_report['decision']}",
            not risk_report["decision"].startswith("RISK_APPROVED"),
        )
        model_findings = alpha_model["findings"] if alpha_model else ["No alpha model record found"]
        model_status = "PASS" if alpha_model and alpha_model["review_status"] == "APPROVED_FOR_RESEARCH" else "WATCH"
        if (
            alpha_model
            and alpha_model["risk_tier"] == "HIGH"
            and alpha_model["review_status"] != "APPROVED_FOR_RESEARCH"
        ):
            model_status = "FAIL"
        add_gate(
            "MODEL_RISK",
            model_status,
            alpha_model["owner"] if alpha_model else "Model Governance",
            alpha_model["evidence"]["experiment_id"] if alpha_model else "missing-model-record",
            1.0 - (alpha_model["residual_risk"] if alpha_model else 0.9),
            "; ".join(model_findings) or "Model risk review completed",
            model_status == "FAIL",
        )
        worst_monitor = max(alpha_monitors, key=lambda monitor: monitor["drift_score"], default=None)
        monitor_status = "PASS"
        monitor_reason = "No blocking candidate monitor"
        monitor_score = 1.0
        monitor_evidence = _short_hash("monitoring", alpha_id, "none")
        if worst_monitor:
            monitor_evidence = worst_monitor["monitor_id"]
            monitor_score = max(0.0, 1.0 - worst_monitor["drift_score"])
            monitor_status = (
                "FAIL"
                if worst_monitor["status"] == "BREACH"
                else "WATCH"
                if worst_monitor["status"] == "WATCH"
                else "PASS"
            )
            monitor_reason = (
                f"{worst_monitor['category']} {worst_monitor['status']} drift={worst_monitor['drift_score']}"
            )
        add_gate(
            "MONITORING",
            monitor_status,
            "Operations / Research Platform",
            monitor_evidence,
            monitor_score,
            monitor_reason,
            monitor_status == "FAIL",
        )
        worst_incident = max(alpha_incidents, key=lambda incident: incident["drift_score"], default=None)
        incident_status = "PASS"
        incident_reason = "No active candidate incident"
        incident_score = 1.0
        incident_evidence = _short_hash("incident", alpha_id, "none")
        if worst_incident:
            incident_evidence = worst_incident["incident_id"]
            incident_status = "FAIL" if worst_incident["severity"] in {"CRITICAL", "HIGH"} else "WATCH"
            incident_score = 0.0 if incident_status == "FAIL" else 0.5
            incident_reason = f"{worst_incident['severity']} incident {worst_incident['state']} from {worst_incident['source_monitor_id']}"
        add_gate(
            "INCIDENT_RESPONSE",
            incident_status,
            "Operations / Risk",
            incident_evidence,
            incident_score,
            incident_reason,
            incident_status == "FAIL",
        )
        add_gate(
            "EVIDENCE_LEDGER",
            "PASS" if evidence_ledger["summary"]["hash_chain_valid"] else "FAIL",
            "Model Governance / Research Platform",
            ledger_root,
            1.0 if evidence_ledger["summary"]["hash_chain_valid"] else 0.0,
            f"Evidence root hash {ledger_root}",
            not evidence_ledger["summary"]["hash_chain_valid"],
        )
        add_gate(
            "ACCESS_CONTROL",
            "PASS" if access_clean else "FAIL",
            "Security / Compliance",
            _short_hash(
                "access",
                access_control["summary"]["permission_rows"],
                access_control["summary"]["denied_dangerous_permissions"],
            ),
            1.0 if access_clean else 0.0,
            "Dangerous permissions denied; exports remain metadata-only"
            if access_clean
            else "Access policy incomplete",
            not access_clean,
        )

        hard_blocks = [gate for gate in gates if gate["status"] == "FAIL"]
        watch_gates = [gate for gate in gates if gate["status"] == "WATCH"]
        readiness_score = round(statistics.mean(gate["score"] for gate in gates), 3)
        paper_allowed = not hard_blocks and validation["decision"] in {"APPROVED", "NEEDS_MORE_DATA"}
        if hard_blocks:
            decision = "BLOCKED"
            decision_reason = f"{len(hard_blocks)} hard promotion gate(s) failed"
            next_action = "Close failed gates and attach reviewer evidence before further promotion"
        elif watch_gates:
            decision = "APPROVED_FOR_DRY_RUN_WITH_LIMITS"
            decision_reason = f"{len(watch_gates)} watch gate(s) require owner review"
            next_action = "Continue dry-run replay and collect additional evidence"
        else:
            decision = "APPROVED_FOR_DRY_RUN_REPLAY"
            decision_reason = "All read-only promotion gates passed for dry-run replay only"
            next_action = "Keep candidate inside L2 dry-run replay; Phase 3A remains NO_GO"
        promotion_records.append(
            {
                "promotion_id": f"PROM-{idx + 1:03d}",
                "alpha_id": alpha_id,
                "expression": alpha["expression"],
                "current_stage": alpha["lifecycle_state"],
                "requested_stage": "L2_DRY_RUN_REPLAY",
                "decision": decision,
                "decision_reason": decision_reason,
                "readiness_score": readiness_score,
                "gates_passed": sum(1 for gate in gates if gate["status"] == "PASS"),
                "gates_watch": len(watch_gates),
                "gates_failed": len(hard_blocks),
                "gates_total": len(gates),
                "blocking_gates": [gate["stage"] for gate in hard_blocks],
                "watch_gates": [gate["stage"] for gate in watch_gates],
                "evidence_refs": [gate["evidence_ref"] for gate in gates],
                "candidate_hash": _short_hash("promotion", alpha_id, readiness_score, decision, ledger_root),
                "paper_replay_allowed": paper_allowed,
                "broker_test_env_allowed": False,
                "live_release_allowed": False,
                "next_action": next_action,
            }
        )

    decision_counts = {
        decision: sum(1 for record in promotion_records if record["decision"] == decision)
        for decision in sorted({record["decision"] for record in promotion_records})
    }
    stage_counts = {
        stage: {
            "pass": sum(1 for gate in all_gates if gate["stage"] == stage and gate["status"] == "PASS"),
            "watch": sum(1 for gate in all_gates if gate["stage"] == stage and gate["status"] == "WATCH"),
            "fail": sum(1 for gate in all_gates if gate["stage"] == stage and gate["status"] == "FAIL"),
        }
        for stage in sorted({gate["stage"] for gate in all_gates})
    }
    return {
        "promotion_mode": "READ_ONLY_PROMOTION_CONTROL",
        "current_level": "L2_DRY_RUN_ONLY",
        "target_stage": "L2_DRY_RUN_REPLAY",
        "phase3a_decision": "NO_GO",
        "summary": {
            "candidates": len(promotion_records),
            "approved_for_dry_run": sum(
                1 for record in promotion_records if record["decision"].startswith("APPROVED_FOR_DRY_RUN")
            ),
            "blocked": sum(1 for record in promotion_records if record["decision"] == "BLOCKED"),
            "watch_gates": sum(record["gates_watch"] for record in promotion_records),
            "failed_gates": sum(record["gates_failed"] for record in promotion_records),
            "paper_replay_allowed": sum(1 for record in promotion_records if record["paper_replay_allowed"]),
            "broker_test_env_allowed": 0,
            "live_release_allowed": 0,
            "avg_readiness_score": round(statistics.mean(record["readiness_score"] for record in promotion_records), 3),
        },
        "decision_counts": decision_counts,
        "stage_counts": stage_counts,
        "promotions": promotion_records,
        "gates": all_gates,
        "release_policy": {
            "read_only": True,
            "requires_validation_approval": True,
            "requires_paper_replay": True,
            "requires_risk_approval": True,
            "requires_model_risk_review": True,
            "requires_no_blocking_incidents": True,
            "requires_evidence_root_hash": True,
            "requires_access_control": True,
            "mutable_promotions": False,
            "broker_test_env_enabled": False,
            "live_release_enabled": False,
            "authorizes_live_trading": False,
        },
        "safety_boundary": {
            "execution_boundary": "LIVE_TRADING_DISALLOWED",
            "phase3a_decision": "NO_GO",
            "forbidden_capabilities": [
                "No broker SDK imports",
                "No real CTP/FIX implementation",
                "No socket connections",
                "No credential files",
                "No live order APIs",
            ],
        },
    }


def _promotion_control_payload() -> Dict:
    """Public composition seam with no synthetic promotion authority."""

    return scrub_backtest_payload(_promotion_control_payload_raw())


router = APIRouter()


@router.get("/api/v1/console/promotion-control")
def promotion_control() -> Dict:
    payload = _promotion_control_payload()
    return scrub_backtest_payload({
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    })
