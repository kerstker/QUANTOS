"""Primary read-only research, validation, portfolio, and safety HTTP views."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Callable, Dict, List

from fastapi import APIRouter, Query

from services.research.factor_engine.domain.model import (
    compute_max_drawdown,
    compute_sharpe,
)
from services.research.paper_trading.domain.pnl import PnLAttributor
from services.validation.factor_statistics import compute_ic


@dataclass(frozen=True)
class PrimaryViewDependencies:
    alpha_candidates: Callable[..., List[Dict]]
    validation_decision: Callable[[Dict], str]
    snapshot_summary: Callable[[], Dict]
    overview_active_runs: Callable[[], int]
    alpha_screening_payload: Callable[[List[Dict]], Dict]
    alpha_correlation_matrix: Callable[[List[Dict]], Dict]
    input_provenance: Callable[[], str]
    alpha_index: Callable[[str], int]
    computed_alpha: Callable[[int], Dict]
    factor_values: Callable[[int], List[float]]
    forward_returns: Callable[[List[float], int], List[float]]
    equity_curve: Callable[[List[float]], List[Dict[str, float]]]
    drawdown_curve: Callable[[List[Dict[str, float]]], List[Dict[str, float]]]
    risk_decision: Callable[[str, List[float], List[Dict[str, float]], float], Dict]
    validation_reports: Callable[[], List[Dict]]
    paper_run: Callable[[str], Dict]
    portfolio_simulation: Callable[[], Dict]
    safety_primitives: Callable[[], Dict]
    alphas: List[Dict]
    lineage: List[Dict]


class PrimaryViewRuntime:
    def __init__(self) -> None:
        self._dependencies: PrimaryViewDependencies | None = None

    def configure(self, dependencies: PrimaryViewDependencies) -> None:
        if self._dependencies is None:
            self._dependencies = dependencies
            return
        if self._dependencies != dependencies:
            raise RuntimeError("primary view runtime is already configured")

    def require(self) -> PrimaryViewDependencies:
        if self._dependencies is None:
            raise RuntimeError("primary view runtime must be configured by the console composition root")
        return self._dependencies


primary_view_runtime = PrimaryViewRuntime()


def configure_primary_views(dependencies: PrimaryViewDependencies) -> None:
    primary_view_runtime.configure(dependencies)


def _alpha_candidates(count: int = 12) -> List[Dict]:
    return primary_view_runtime.require().alpha_candidates(count=count)


def _validation_decision(alpha: Dict) -> str:
    return primary_view_runtime.require().validation_decision(alpha)


def _snapshot_summary() -> Dict:
    return primary_view_runtime.require().snapshot_summary()


def _overview_active_runs() -> int:
    return primary_view_runtime.require().overview_active_runs()


def _alpha_screening_payload(candidates: List[Dict]) -> Dict:
    return primary_view_runtime.require().alpha_screening_payload(candidates)


def _alpha_correlation_matrix(candidates: List[Dict]) -> Dict:
    return primary_view_runtime.require().alpha_correlation_matrix(candidates)


def _input_provenance() -> str:
    return primary_view_runtime.require().input_provenance()


def _alpha_index(alpha_id: str) -> int:
    return primary_view_runtime.require().alpha_index(alpha_id)


def _computed_alpha(idx: int) -> Dict:
    return primary_view_runtime.require().computed_alpha(idx)


def _factor_values(idx: int) -> List[float]:
    return primary_view_runtime.require().factor_values(idx)


def _forward_returns(factors: List[float], idx: int) -> List[float]:
    return primary_view_runtime.require().forward_returns(factors, idx)


def _equity_curve(returns: List[float]) -> List[Dict[str, float]]:
    return primary_view_runtime.require().equity_curve(returns)


def _drawdown_curve(
    equity_points: List[Dict[str, float]],
) -> List[Dict[str, float]]:
    return primary_view_runtime.require().drawdown_curve(equity_points)


def _risk_decision(
    alpha_id: str,
    returns: List[float],
    equity_points: List[Dict[str, float]],
    capacity: float,
) -> Dict:
    return primary_view_runtime.require().risk_decision(alpha_id, returns, equity_points, capacity)


def _validation_reports() -> List[Dict]:
    return primary_view_runtime.require().validation_reports()


def _paper_run(alpha_id: str) -> Dict:
    return primary_view_runtime.require().paper_run(alpha_id)


def _portfolio_simulation() -> Dict:
    return primary_view_runtime.require().portfolio_simulation()


def _safety_primitives_payload() -> Dict:
    return primary_view_runtime.require().safety_primitives()


def _alphas() -> List[Dict]:
    return primary_view_runtime.require().alphas


def _lineage() -> List[Dict]:
    return primary_view_runtime.require().lineage


def _overview_payload() -> Dict:
    candidates = _alpha_candidates()
    approved = [alpha for alpha in candidates if _validation_decision(alpha) == "APPROVED"]
    paper_passed = [alpha for alpha in approved if alpha["turnover"] <= 0.62]
    portfolio_candidates = [alpha for alpha in paper_passed if alpha["capacity_estimate"] >= 55_000_000]
    risk_approved = [alpha for alpha in portfolio_candidates if alpha["correlation_penalty"] <= 0.12]
    rejection_reasons = {
        "REJECTED_LOW_CAPACITY": sum(
            1 for alpha in candidates if _validation_decision(alpha) == "REJECTED_LOW_CAPACITY"
        ),
        "REJECTED_UNSTABLE": sum(1 for alpha in candidates if _validation_decision(alpha) == "REJECTED_UNSTABLE"),
        "REJECTED_HIGH_CORRELATION": sum(
            1 for alpha in candidates if _validation_decision(alpha) == "REJECTED_HIGH_CORRELATION"
        ),
        "NEEDS_MORE_DATA": sum(1 for alpha in candidates if _validation_decision(alpha) == "NEEDS_MORE_DATA"),
    }
    stats = {
        "total_hypotheses": _snapshot_summary().get("n_evaluated") or 142,  # real evaluated count when present
        "alpha_candidates": len(candidates),
        "validation_pass_rate": round(len(approved) / max(len(candidates), 1), 3),
        "paper_pass_rate": round(len(paper_passed) / max(len(approved), 1), 3),
        "portfolio_candidates": len(portfolio_candidates),
        "risk_approved": len(risk_approved),
        "active_runs": _overview_active_runs(),  # real queued/running from registry
        "rejection_reasons": rejection_reasons,
    }
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": _input_provenance(),
        "stats": stats,
        "funnel": [
            {"id": "hypotheses", "label": "Hypotheses", "value": stats["total_hypotheses"]},
            {"id": "candidates", "label": "Candidates", "value": stats["alpha_candidates"]},
            {"id": "validated", "label": "Validated", "value": len(approved)},
            {"id": "paper_passed", "label": "Paper Passed", "value": len(paper_passed)},
            {"id": "portfolio_candidates", "label": "Portfolio Candidates", "value": len(portfolio_candidates)},
            {"id": "risk_approved", "label": "Risk Approved", "value": len(risk_approved)},
        ],
        "agents": [
            {
                "agent_id": "agent-1",
                "name": "Macro Analyst",
                "state": "RUNNING",
                "runs_completed": 42,
                "alpha_approved": 7,
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
        ],
    }


router = APIRouter()


@router.get("/api/v1/console/overview")
async def overview() -> Dict:
    return _overview_payload()


# ---- research-ops console: dynamic, registry-backed run list + generic detail (read-only) -----------
# These serve REAL research-run results from the run_registry (the same records the research_runner
# writes). Read-only like every other endpoint; the registry is seeded from existing cached result
# JSONs on first read using a FIXED timestamp (deterministic — no wall-clock in this read path).


@router.get("/api/v1/console/alpha-factory")
async def alpha_factory() -> Dict:
    candidates = _alpha_candidates()
    screening = _alpha_screening_payload(candidates)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": _input_provenance(),
        "summary": {
            "candidates": len(candidates),
            "avg_ic": round(statistics.mean(alpha["ic"] for alpha in candidates), 4),
            "avg_sharpe": round(statistics.mean(alpha["sharpe"] for alpha in candidates), 3),
            "active": sum(1 for alpha in candidates if alpha["lifecycle_state"] == "ACTIVE"),
        },
        "candidates": candidates,
        "screening": screening,
        "correlation_matrix": _alpha_correlation_matrix(candidates),
        "lineage": [item for item in _lineage() if item["layer"] in {"Alpha", "Paper"}],
    }


@router.get("/api/v1/console/risk-dashboard")
async def risk_dashboard(alpha_id: str = Query("alpha-1")) -> Dict:
    idx = _alpha_index(alpha_id)
    alpha = _computed_alpha(idx)
    factors = _factor_values(idx)
    returns = _forward_returns(factors, idx)
    equity_points = _equity_curve(returns)
    primary_report = _risk_decision(alpha["candidate_id"], returns, equity_points, alpha["capacity_estimate"])
    control_report = {
        "run_id": "risk-control-001",
        "portfolio_id": "control-portfolio",
        "decision": "RISK_APPROVED",
        "gates_passed": 9,
        "gates_total": 9,
        "reasons": [],
        "gate_results": {gate: True for gate in primary_report["gate_results"]},
    }
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": _input_provenance(),
        "selected_alpha_id": alpha["candidate_id"],
        "alpha": alpha,
        "primary_report": primary_report,
        "reports": [primary_report, control_report],
        "lineage": [item for item in _lineage() if item["layer"] == "Risk"],
    }


@router.get("/api/v1/console/validation")
async def validation_dashboard() -> Dict:
    reports = _validation_reports()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "validation_backend": "REAL_LEAKAGE_DETECTOR_v1",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": _input_provenance(),
        "selected_report": reports[0],
        "reports": reports,
        "lineage": [item for item in _lineage() if item["layer"] == "Alpha"],
    }


@router.get("/api/v1/console/paper-trading")
async def paper_trading(alpha_id: str = Query("alpha-1")) -> Dict:
    run = _paper_run(alpha_id)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": _input_provenance(),
        "selected_alpha_id": run["alpha_id"],  # resolved id — matches the rendered data
        "run": run,
        "lineage": [item for item in _lineage() if item["layer"] == "Paper"],
    }


@router.get("/api/v1/console/portfolio")
async def portfolio_dashboard() -> Dict:
    payload = _portfolio_simulation()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": _input_provenance(),
        **payload,
        "lineage": [item for item in _lineage() if item["layer"] in {"Alpha", "Paper", "Risk"}],
    }


@router.get("/api/v1/console/safety-primitives")
async def safety_primitives() -> Dict:
    payload = _safety_primitives_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_SAFETY_PRIMITIVES",
        **payload,
    }


@router.get("/api/v1/console/computation-lab")
async def computation_lab(
    alpha_id: str = Query("alpha-1"),
    cost_bps: float = Query(18.0, ge=0.0, le=100.0),
    stress: float = Query(1.2, ge=1.0, le=3.0),
) -> Dict:
    idx = _alpha_index(alpha_id)
    alpha = dict(_alphas()[idx])
    factors = _factor_values(idx)
    returns = _forward_returns(factors, idx)
    ic = compute_ic(factors, returns)
    equity_points = _equity_curve(returns)
    drawdowns = _drawdown_curve(equity_points)
    raw_returns = [
        (equity_points[i]["equity"] - equity_points[i - 1]["equity"]) / equity_points[i - 1]["equity"]
        for i in range(1, len(equity_points))
    ]
    gross_sharpe = compute_sharpe(raw_returns) or 0.0
    max_dd = compute_max_drawdown([p["equity"] for p in equity_points]) or 0.0
    total_return = (equity_points[-1]["equity"] / equity_points[0]["equity"] - 1) * 100 if equity_points else 0.0
    cost_drag = cost_bps / 100.0
    stress_penalty = max(0.0, stress - 1.0) * 0.18
    net_sharpe = gross_sharpe - cost_drag - stress_penalty
    paper_metrics = PnLAttributor.compute_metrics(
        [{"equity": p["equity"]} for p in equity_points],
        equity_points[0]["equity"],
        total_turnover=alpha["turnover"] * equity_points[-1]["equity"],
    )

    alpha.update(
        {
            "ic": round(ic["ic_mean"] or 0.0, 4),
            "rank_ic": round(ic["rank_ic"] or 0.0, 4),
            "icir": round(ic["icir"] or 0.0, 4),
            "sharpe": round(gross_sharpe, 3),
            "composite_score": round(max(0.0, min(1.0, (gross_sharpe / 3.0) * 0.35 + (ic["ic_mean"] or 0.0) * 3.0)), 3),
        }
    )

    validation = {
        "run_id": f"val-{alpha['candidate_id']}",
        "alpha_id": alpha["candidate_id"],
        "decision": "APPROVED" if net_sharpe > 0.5 and max_dd * stress < 0.25 else "NEEDS_MORE_DATA",
        "gates_passed": 8 if net_sharpe > 0.5 else 6,
        "gates_total": 9,
        "oos_sharpe": round(gross_sharpe, 3),
        "oos_ic": round(ic["ic_mean"] or 0.0, 4),
        "net_sharpe": round(net_sharpe, 3),
        "reasons": [] if net_sharpe > 0.5 else ["Net Sharpe below approval threshold after cost/stress adjustment"],
    }

    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": _input_provenance(),
        "selected_alpha_id": alpha["candidate_id"],
        "alpha_candidates": _alphas(),
        "alpha": alpha,
        "validation": validation,
        "metrics": {
            "gross_sharpe": round(gross_sharpe, 3),
            "net_sharpe": round(net_sharpe, 3),
            "max_drawdown": round(max_dd * stress, 4),
            "total_return": round(total_return, 2),
            "cost_bps": round(cost_bps, 2),
            "stress_multiplier": round(stress, 2),
            "paper_metrics": paper_metrics,
        },
        "equity_curve": [{"x": p["x"], "y": p["y"]} for p in equity_points],
        "drawdown_curve": drawdowns,
        "risk_report": _risk_decision(alpha["candidate_id"], returns, equity_points, alpha["capacity_estimate"]),
        "lineage": _lineage(),
    }
