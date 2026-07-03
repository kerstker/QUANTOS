"""Read-only QUANTOS Research Console API.

This service exposes computed research snapshots for the frontend. It does not
connect to brokers, place orders, store credentials, or mutate trading state.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import statistics
from typing import Dict, List

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from services.execution.dry_run_store import DryRunStore
from services.research.factor_engine.domain.model import compute_ic, compute_max_drawdown, compute_sharpe
from services.research.factor_set import select_synergistic_set
from services.research.paper_trading.domain.pnl import PnLAttributor
from services.risk.research_risk.domain.gate import RiskApprovalGate
from services.risk.research_risk.domain.models import RiskConfig, RiskRun


app = FastAPI(title="QUANTOS Research Console API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3100", "http://localhost:3100"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


ALPHAS = [
    {"candidate_id": "alpha-1", "expression": "(ts_mean :close 5)", "turnover": 0.34, "capacity_estimate": 84_000_000, "lifecycle_state": "ACTIVE"},
    {"candidate_id": "alpha-2", "expression": "(ts_rank :volume 20)", "turnover": 0.58, "capacity_estimate": 56_000_000, "lifecycle_state": "VALIDATING"},
    {"candidate_id": "alpha-3", "expression": "(zscore :returns 10)", "turnover": 0.72, "capacity_estimate": 42_000_000, "lifecycle_state": "DECAYING"},
    {"candidate_id": "alpha-4", "expression": "(ts_delta :vwap 30)", "turnover": 0.45, "capacity_estimate": 110_000_000, "lifecycle_state": "ACTIVE"},
]
ALPHA_WINDOWS = [5, 10, 20, 30, 60, 90]
ALPHA_OPERATORS = ["ts_mean", "ts_rank", "zscore", "ts_delta", "ts_std", "rank_decay"]
ALPHA_FIELDS = ["close", "volume", "returns", "vwap", "turnover", "amount"]
ALPHA_STATES = ["ACTIVE", "VALIDATING", "DECAYING", "RETIRED"]
PHASE3A_CRITERIA = [
    {"id": "E01", "criterion": "Phase 2H governance approved", "category": "Governance", "owner": "CTO", "status": "PENDING", "evidence": "Governance signoff register"},
    {"id": "E02", "criterion": "Broker test env account confirmed", "category": "Broker", "owner": "Broker Relationship Owner", "status": "PENDING", "evidence": "Broker response intake"},
    {"id": "E03", "criterion": "Legal/compliance approval", "category": "Compliance", "owner": "Compliance Officer", "status": "PENDING", "evidence": "Legal/compliance checklist"},
    {"id": "E04", "criterion": "Test credentials in Vault", "category": "Credentials", "owner": "Security Officer", "status": "PENDING", "evidence": "Vault path and access policy"},
    {"id": "E05", "criterion": "No credentials in code", "category": "Security", "owner": "Security Officer", "status": "APPROVED", "evidence": "Governance scanner report"},
    {"id": "E06", "criterion": "Network access reviewed", "category": "Network", "owner": "Security Officer", "status": "PENDING", "evidence": "Network/IP whitelist review"},
    {"id": "E07", "criterion": "Broker readiness checklist", "category": "Operations", "owner": "Operations Lead", "status": "APPROVED", "evidence": "Broker readiness checklist"},
    {"id": "E08", "criterion": "Kill switch tested", "category": "Risk", "owner": "Trading System Owner", "status": "APPROVED", "evidence": "Kill switch drill report"},
    {"id": "E09", "criterion": "Rollback tested", "category": "Risk", "owner": "Trading System Owner", "status": "APPROVED", "evidence": "Rollback drill report"},
    {"id": "E10", "criterion": "DR runbooks approved", "category": "DR", "owner": "Operations Lead", "status": "APPROVED", "evidence": "DR dry-run report"},
    {"id": "E11", "criterion": "UAT plan approved", "category": "UAT", "owner": "Operations Lead", "status": "APPROVED", "evidence": "UAT dry-run report"},
    {"id": "E12", "criterion": "Incident response approved", "category": "IR", "owner": "Operations Lead", "status": "APPROVED", "evidence": "Incident response plan"},
    {"id": "E13", "criterion": "No production credentials", "category": "Security", "owner": "Security Officer", "status": "APPROVED", "evidence": "Governance scanner report"},
    {"id": "E14", "criterion": "No production trading", "category": "Security", "owner": "Risk Officer", "status": "APPROVED", "evidence": "Governance scanner report"},
    {"id": "E15", "criterion": "Phase 2G.1 tests pass", "category": "Technical", "owner": "Trading System Owner", "status": "APPROVED", "evidence": "Dry-run adapter gate evidence"},
]
PHASE3A_BLOCKERS = [
    {"id": "E01", "blocker": "Governance signatures incomplete", "owner": "CTO", "dependency": "None", "next_action": "Collect required role signatures"},
    {"id": "E02", "blocker": "Broker test environment not confirmed", "owner": "Broker Relationship Owner", "dependency": "Broker response", "next_action": "Send broker request package"},
    {"id": "E03", "blocker": "Legal/compliance approval incomplete", "owner": "Compliance Officer", "dependency": "E02", "next_action": "Review broker agreement and test scope"},
    {"id": "E04", "blocker": "Test credentials not provisioned in Vault", "owner": "Security Officer", "dependency": "E02", "next_action": "Prepare Vault-only intake workflow"},
    {"id": "E06", "blocker": "Network access not reviewed", "owner": "Security Officer", "dependency": "E02", "next_action": "Collect broker endpoint and whitelist details"},
]

LINEAGE = [
    {"id": "IC", "layer": "Alpha", "formula": "corr(factor_values, forward_returns)", "backend": "services/research/factor_engine/domain/model.py", "inputs": ["factor_values", "forward_returns"], "output": "information_coefficient"},
    {"id": "RANK_IC", "layer": "Alpha", "formula": "corr(rank(factor_values), rank(forward_returns))", "backend": "services/research/factor_engine/domain/model.py", "inputs": ["ranked_factor_values", "ranked_forward_returns"], "output": "rank_information_coefficient"},
    {"id": "SHARPE", "layer": "Paper", "formula": "mean(daily_returns) / std(daily_returns) * sqrt(252)", "backend": "services/research/paper_trading/domain/pnl.py", "inputs": ["equity_curve"], "output": "annualized_sharpe"},
    {"id": "MAX_DD", "layer": "Paper", "formula": "max((peak_equity - equity) / peak_equity)", "backend": "services/research/factor_engine/domain/model.py", "inputs": ["equity_curve"], "output": "max_drawdown"},
    {"id": "RISK_GATE", "layer": "Risk", "formula": "all(required_gates.pass) && fail_closed_on_unknown", "backend": "services/risk/research_risk/domain/gate.py", "inputs": ["returns", "weights", "capacity", "metadata"], "output": "risk_decision"},
]


def _alpha_index(alpha_id: str) -> int:
    for idx, alpha in enumerate(ALPHAS):
        if alpha["candidate_id"] == alpha_id:
            return idx
    return 0


def _factor_values(idx: int, n: int = 160) -> List[float]:
    return [
        math.sin(i / (5.0 + idx)) + math.cos(i / 17.0) * 0.35 + (idx + 1) * 0.015
        for i in range(n)
    ]


def _forward_returns(factors: List[float], idx: int) -> List[float]:
    real = _real_factor(idx)
    if real is not None:
        return list(real["returns"])                          # REAL per-period net returns (Phase 1B)
    returns: List[float] = []
    for i, value in enumerate(factors):
        signal = 0.0015 * value
        cycle = math.sin(i / 11.0 + idx) * 0.0012
        shock = -0.004 if 54 + idx * 4 < i < 63 + idx * 4 else 0.0
        recovery = 0.002 if 76 < i < 90 else 0.0
        returns.append(signal + cycle + shock + recovery)
    return returns


def _equity_curve(returns: List[float], initial: float = 10_000_000.0) -> List[Dict[str, float]]:
    equity = initial
    points = []
    for day, daily_return in enumerate(returns[:120]):
        equity *= 1 + daily_return
        points.append({"x": day, "y": round(equity, 2), "equity": round(equity, 2)})
    return points


def _drawdown_curve(equity_points: List[Dict[str, float]]) -> List[Dict[str, float]]:
    peak = equity_points[0]["equity"] if equity_points else 0.0
    worst = 0.0
    curve = []
    for point in equity_points:
        peak = max(peak, point["equity"])
        dd = (peak - point["equity"]) / peak if peak > 0 else 0.0
        worst = max(worst, dd)
        curve.append({"x": point["x"], "y": round(dd, 6), "worst": round(worst, 6)})
    return curve


def _risk_decision(alpha_id: str, returns: List[float], equity_points: List[Dict[str, float]], capacity: float) -> Dict:
    weights = {f"{alpha_id}-{i}": 0.1 for i in range(10)}
    capacity_cny = {name: capacity for name in weights}
    alpha_returns = {name: [r * (0.8 + i * 0.04) for r in returns[:90]] for i, name in enumerate(weights)}
    metadata = [{"validation_confidence": 0.78, "paper_confidence": 0.72, "age_days": 45, "decay_flag": False, "overfit_flag": False, "leakage_flag": False}]
    run = RiskRun(portfolio_id=f"portfolio-{alpha_id}", config=RiskConfig())
    decision = RiskApprovalGate().evaluate(
        run=run,
        returns=returns[:120],
        weights=weights,
        equity_curve=[p["equity"] for p in equity_points],
        capacity_cny=capacity_cny,
        alpha_returns=alpha_returns,
        regime_labels=["normal" if i % 5 else "stress" for i in range(len(returns[:120]))],
        alpha_metadata=metadata,
        daily_volume_cny=1_000_000_000,
        total_aum=100_000_000,
    )
    # Rank #7: surface the stress engine's FULL report (provenance + per-scenario losses).
    # This console path has no per-period timestamps yet, so the engine's labeled
    # SYNTHETIC_MULTIPLIER_UNCALIBRATED fallback is what honestly shows here; callers with
    # timestamps (e.g. dry-run books) get REAL_HISTORICAL_REPLAY through the same field.
    stress_report = run.gate_reports.get("stress")
    return {
        "run_id": run.run_id,
        "portfolio_id": run.portfolio_id,
        "decision": decision.decision.value,
        "gates_passed": decision.gates_passed,
        "gates_total": decision.gates_total,
        "gate_results": run.gate_results,
        "reasons": decision.reasons,
        "reproducibility_hash": run.reproducibility_hash,
        "stress": dataclasses.asdict(stress_report) if stress_report is not None else None,
    }


def _computed_alpha(idx: int) -> Dict:
    real = _real_factor(idx)
    if real is not None:
        # REAL factor: real cross-sectional IC / rank-IC / turnover / state from the snapshot, realised Sharpe
        # & maxDD from the REAL net-return equity, info-ratio via the fundamental law (IR ≈ IC·√breadth).
        returns = list(real["returns"])
        equity_points = _equity_curve(returns)
        raw = [(equity_points[i]["equity"] - equity_points[i - 1]["equity"]) / equity_points[i - 1]["equity"]
               for i in range(1, len(equity_points))] if len(equity_points) > 1 else [0.0]
        sharpe = compute_sharpe(raw) or (real["net_sharpe"] or 0.0)
        ic_mean = real["ic"] if real["ic"] is not None else 0.0
        breadth = real.get("breadth") or 1.0
        turnover = round(real["turnover"] or 0.0, 3)
        composite = max(0.0, min(1.0, (sharpe / 3.0) * 0.35 + ic_mean * 3.0 - turnover * 0.08))
        return {
            "candidate_id": f"alpha-{idx + 1}",
            "expression": real["name"],
            "ic": round(ic_mean, 4),
            "rank_ic": round(real["rank_ic"] or 0.0, 4),
            "icir": round(ic_mean * math.sqrt(max(1.0, breadth)), 4),   # IR ≈ IC·√breadth (fundamental law)
            "sharpe": round(sharpe, 3),
            "turnover": turnover,
            "decay_half_life": max(1, int(20 * (1.0 - min(1.0, turnover)))),   # turnover-derived half-life proxy
            "capacity_estimate": int(max(1.0, breadth) * 12_000_000),          # breadth-scaled capacity proxy
            "correlation_penalty": round(real.get("pbo") or 0.0, 3),           # PBO ~ overfit/redundancy proxy
            "composite_score": round(composite, 3),
            "lifecycle_state": real.get("state") or "ACTIVE",
        }
    factors = _factor_values(idx, n=160)
    returns = _forward_returns(factors, idx)
    ic = compute_ic(factors, returns)
    equity_points = _equity_curve(returns)
    raw_returns = [
        (equity_points[i]["equity"] - equity_points[i - 1]["equity"]) / equity_points[i - 1]["equity"]
        for i in range(1, len(equity_points))
    ]
    sharpe = compute_sharpe(raw_returns) or 0.0
    turnover = round(0.24 + (idx % 5) * 0.105 + (idx // 5) * 0.035, 3)
    capacity = 32_000_000 + (idx % 6) * 13_500_000 + (idx // 6) * 8_000_000
    composite = max(0.0, min(1.0, (sharpe / 3.0) * 0.35 + (ic["ic_mean"] or 0.0) * 3.0 - turnover * 0.08))
    return {
        "candidate_id": f"alpha-{idx + 1}",
        "expression": f"({ALPHA_OPERATORS[idx % len(ALPHA_OPERATORS)]} :{ALPHA_FIELDS[idx % len(ALPHA_FIELDS)]} {ALPHA_WINDOWS[idx % len(ALPHA_WINDOWS)]})",
        "ic": round(ic["ic_mean"] or 0.0, 4),
        "rank_ic": round(ic["rank_ic"] or 0.0, 4),
        "icir": round(ic["icir"] or 0.0, 4),
        "sharpe": round(sharpe, 3),
        "turnover": turnover,
        "decay_half_life": 6 + (idx * 7) % 31,
        "capacity_estimate": capacity,
        "correlation_penalty": round(0.04 + (idx % 4) * 0.055, 3),
        "composite_score": round(composite, 3),
        "lifecycle_state": ALPHA_STATES[idx % len(ALPHA_STATES)],
    }


def _alpha_candidates(count: int = 12) -> List[Dict]:
    return [_computed_alpha(idx) for idx in range(count)]


def _validation_decision(alpha: Dict) -> str:
    if alpha["ic"] < 0.05:
        return "REJECTED_LOW_CAPACITY"
    if alpha["turnover"] > 0.75:
        return "REJECTED_UNSTABLE"
    if alpha["correlation_penalty"] > 0.16:
        return "REJECTED_HIGH_CORRELATION"
    if alpha["sharpe"] > 0.7:
        return "APPROVED"
    return "NEEDS_MORE_DATA"


SCREEN_REASONS = {
    "APPROVED": "Screen passed",
    "NEEDS_MORE_DATA": "Needs more samples",
    "REJECTED_LOW_CAPACITY": "IC below research gate",
    "REJECTED_UNSTABLE": "Turnover too high",
    "REJECTED_HIGH_CORRELATION": "Correlation penalty too high",
}


def _operator_name(expression: str) -> str:
    if not expression.startswith("("):
        return "unknown"
    return expression[1:].split(" ", 1)[0].split(":", 1)[0]


def _alpha_screening_payload(candidates: List[Dict]) -> Dict:
    buckets = {
        "APPROVED": 0,
        "NEEDS_MORE_DATA": 0,
        "REJECTED_LOW_CAPACITY": 0,
        "REJECTED_UNSTABLE": 0,
        "REJECTED_HIGH_CORRELATION": 0,
    }
    enriched = []
    for alpha in candidates:
        decision = _validation_decision(alpha)
        buckets[decision] = buckets.get(decision, 0) + 1
        enriched.append({
            "candidate_id": alpha["candidate_id"],
            "decision": decision,
            "reason": SCREEN_REASONS.get(decision, "Research screen decision"),
            "composite_score": alpha["composite_score"],
            "expression": alpha["expression"],
        })
    return {
        "buckets": buckets,
        "top_candidates": sorted(enriched, key=lambda item: item["composite_score"], reverse=True)[:5],
    }


def _alpha_correlation_matrix(candidates: List[Dict], limit: int = 8) -> Dict:
    matrix_candidates = candidates[:limit]
    rows = []
    for row_idx, row in enumerate(matrix_candidates):
        values = []
        for col_idx, col in enumerate(matrix_candidates):
            if row["candidate_id"] == col["candidate_id"]:
                value = 1.0
            else:
                same_operator_bonus = 0.28 if _operator_name(row["expression"]) == _operator_name(col["expression"]) else 0.0
                penalty = (row["correlation_penalty"] + col["correlation_penalty"]) / 2
                score_distance = abs(row["composite_score"] - col["composite_score"])
                proximity = max(0.0, 0.16 - abs(row_idx - col_idx) * 0.015)
                value = max(0.05, min(0.95, 0.18 + same_operator_bonus + penalty + proximity - score_distance * 0.35))
            values.append(round(value, 3))
        rows.append(values)
    return {
        "method": "BACKEND_RESEARCH_PROXY_EXPRESSION_SCORE_PENALTY",
        "ids": [alpha["candidate_id"] for alpha in matrix_candidates],
        "matrix": rows,
    }


def _overview_payload() -> Dict:
    candidates = _alpha_candidates()
    approved = [alpha for alpha in candidates if _validation_decision(alpha) == "APPROVED"]
    paper_passed = [alpha for alpha in approved if alpha["turnover"] <= 0.62]
    portfolio_candidates = [alpha for alpha in paper_passed if alpha["capacity_estimate"] >= 55_000_000]
    risk_approved = [alpha for alpha in portfolio_candidates if alpha["correlation_penalty"] <= 0.12]
    rejection_reasons = {
        "REJECTED_LOW_CAPACITY": sum(1 for alpha in candidates if _validation_decision(alpha) == "REJECTED_LOW_CAPACITY"),
        "REJECTED_UNSTABLE": sum(1 for alpha in candidates if _validation_decision(alpha) == "REJECTED_UNSTABLE"),
        "REJECTED_HIGH_CORRELATION": sum(1 for alpha in candidates if _validation_decision(alpha) == "REJECTED_HIGH_CORRELATION"),
        "NEEDS_MORE_DATA": sum(1 for alpha in candidates if _validation_decision(alpha) == "NEEDS_MORE_DATA"),
    }
    stats = {
        "total_hypotheses": _snapshot_summary().get("n_evaluated") or 142,   # real evaluated count when present
        "alpha_candidates": len(candidates),
        "validation_pass_rate": round(len(approved) / max(len(candidates), 1), 3),
        "paper_pass_rate": round(len(paper_passed) / max(len(approved), 1), 3),
        "portfolio_candidates": len(portfolio_candidates),
        "risk_approved": len(risk_approved),
        "active_runs": _overview_active_runs(),                              # real queued/running from registry
        "rejection_reasons": rejection_reasons,
    }
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": _input_provenance(),
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
            {"agent_id": "agent-1", "name": "Macro Analyst", "state": "RUNNING", "runs_completed": 42, "alpha_approved": 7, "performance_score": 0.78, "budget_spent_cny": 150},
            {"agent_id": "agent-2", "name": "Factor Hunter", "state": "REFLECTING", "runs_completed": 28, "alpha_approved": 3, "performance_score": 0.62, "budget_spent_cny": 280},
            {"agent_id": "agent-3", "name": "Momentum Specialist", "state": "IDLE", "runs_completed": 15, "alpha_approved": 2, "performance_score": 0.55, "budget_spent_cny": 95},
        ],
    }


def _real_leakage_pass(idx: int) -> bool:
    """Compute leakage_pass with the REAL leakage detector (services/features),
    not a modulo hack. The index deterministically decides whether to inject a
    future-peeking feature; the detector then independently catches it."""
    from quantos.contracts.data import Bar as _Bar
    from quantos.contracts.feature import FeatureValue as _FV
    from services.features.leakage import detect_leakage as _detect

    bars = [_Bar(instrument_id="X", event_time=i + 1, knowledge_time=i + 1,
                 open=10.0 + i, high=11.0 + i, low=9.0 + i, close=10.0 + i, volume=100)
            for i in range(6)]
    inject_leak = idx % 5 == 2

    def compute(rows):
        rows = sorted(rows, key=lambda b: b.event_time)
        out = []
        for j, b in enumerate(rows):
            val = rows[j + 1].close if (inject_leak and j + 1 < len(rows)) else b.close
            out.append(_FV(feature_id=f"f{idx}", instrument_id=b.instrument_id,
                           event_time=b.event_time, knowledge_time=b.knowledge_time, value=float(val)))
        return out

    return _detect(f"f{idx}", compute, bars, target_event_time=3).passed


def _validation_reports() -> List[Dict]:
    reports = []
    for idx, alpha in enumerate(_alpha_candidates(count=6)):
        leakage_pass = _real_leakage_pass(idx)
        walk_forward_pass = alpha["sharpe"] >= 0.35
        multiple_testing_pass = alpha["correlation_penalty"] <= 0.17
        costs_pass = alpha["turnover"] <= 0.75
        capacity_pass = alpha["capacity_estimate"] >= 40_000_000
        decay_pass = alpha["decay_half_life"] >= 8
        gates = [leakage_pass, walk_forward_pass, multiple_testing_pass, costs_pass, capacity_pass, decay_pass]
        reasons = []
        if not leakage_pass:
            reasons.append("Leakage check failed")
        if not walk_forward_pass:
            reasons.append("Walk-forward Sharpe below threshold")
        if not multiple_testing_pass:
            reasons.append("High correlation penalty after multiple testing")
        if not costs_pass:
            reasons.append("Turnover too high after cost adjustment")
        if not capacity_pass:
            reasons.append("Capacity below research threshold")
        if not decay_pass:
            reasons.append("Half-life below decay threshold")
        net_sharpe = alpha["sharpe"] - alpha["turnover"] * 0.18
        decision = "APPROVED" if all(gates) and net_sharpe > 0.5 else _validation_decision(alpha)
        reports.append({
            "run_id": f"val-{alpha['candidate_id']}",
            "alpha_id": alpha["candidate_id"],
            "decision": decision,
            "gates_passed": sum(1 for gate in gates if gate),
            "gates_total": len(gates),
            "reasons": reasons,
            "leakage_pass": leakage_pass,
            "walk_forward_pass": walk_forward_pass,
            "multiple_testing_pass": multiple_testing_pass,
            "costs_pass": costs_pass,
            "capacity_pass": capacity_pass,
            "decay_pass": decay_pass,
            "oos_sharpe": round(alpha["sharpe"], 3),
            "oos_ic": round(alpha["ic"], 4),
            "net_sharpe": round(net_sharpe, 3),
        })
    return reports


def _paper_run(alpha_id: str) -> Dict:
    idx = _alpha_index(alpha_id)
    alpha = _computed_alpha(idx)
    # echo the RESOLVED candidate id, not the raw query: an unknown id resolves to
    # idx 0, so labelling the run with the raw id would mislabel alpha-1's data as
    # the requested (non-existent) alpha. Stay consistent with risk-dashboard /
    # computation-lab, which both echo the resolved id.
    resolved_id = alpha["candidate_id"]
    returns = _forward_returns(_factor_values(idx), idx)
    equity_points = _equity_curve(returns)
    metrics = PnLAttributor.compute_metrics(
        [{"equity": point["equity"]} for point in equity_points],
        equity_points[0]["equity"],
        total_turnover=alpha["turnover"] * equity_points[-1]["equity"],
    )
    status = "PAPER_PASSED" if metrics.get("sharpe", 0) > 0.5 and metrics.get("max_drawdown", 100) < 30 else "PAPER_RUNNING"
    return {
        "run_id": f"paper-{resolved_id}",
        "alpha_id": resolved_id,
        "status": status,
        "metrics": metrics,
        "equity_curve": [{"timestamp": point["x"], "equity": point["equity"]} for point in equity_points],
        "total_trades": 28 + idx * 9,
        "final_equity": equity_points[-1]["equity"],
    }


def _portfolio_drawdown_curve(equity_curve: List[Dict[str, float]]) -> List[Dict[str, float]]:
    peak = equity_curve[0]["equity"] if equity_curve else 0.0
    worst = 0.0
    drawdowns = []
    for point in equity_curve:
        peak = max(peak, point["equity"])
        drawdown = (peak - point["equity"]) / peak if peak > 0 else 0.0
        worst = max(worst, drawdown)
        drawdowns.append({
            "day": point["day"],
            "drawdown": round(drawdown, 6),
            "worst": round(worst, 6),
        })
    return drawdowns


def _return_cov_matrix(rows: List[List[float]], n: int) -> Optional[List[List[float]]]:
    """Sample covariance across the alpha return streams over their common length n (pure stdlib).
    None when too short or any stream is degenerate (zero variance) -> caller fails closed."""
    if n < 2 or len(rows) < 2:
        return None
    means = [statistics.fmean(r[:n]) for r in rows]
    k = len(rows)
    cov = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(i, k):
            c = sum((rows[i][t] - means[i]) * (rows[j][t] - means[j]) for t in range(n)) / (n - 1)
            cov[i][j] = cov[j][i] = c
    if any(cov[i][i] <= 0 for i in range(k)):
        return None
    return cov


def _optimizer_weights(candidates, return_matrix, n_days):
    """REAL portfolio construction: risk-parity weights from the real return covariance (services/portfolio
    optimizer). Fail-closed to equal-weight when the covariance is degenerate / the optimizer is unavailable
    (CI / synthetic) so the page never breaks — returns (weights_dict, weights_are_real)."""
    eq = {c["candidate_id"]: round(1.0 / len(candidates), 4) for c in candidates}
    cov = _return_cov_matrix(return_matrix, n_days)
    if cov is None:
        return eq, False
    try:
        from services.portfolio.optimizer import risk_parity_weights
        wl = risk_parity_weights(cov)
        if not wl or len(wl) != len(candidates) or abs(sum(wl)) < 1e-9:
            return eq, False
        return {c["candidate_id"]: round(wl[i], 4) for i, c in enumerate(candidates)}, True
    except Exception:
        return eq, False


def _portfolio_simulation() -> Dict:
    candidates = sorted(_alpha_candidates(), key=lambda alpha: alpha["composite_score"], reverse=True)[:4]
    return_matrix = []
    for idx, _alpha in enumerate(candidates):
        return_matrix.append(_forward_returns(_factor_values(idx), idx)[:120])
    n_days = min((len(r) for r in return_matrix), default=0)   # real series can be shorter than 120
    # REAL portfolio construction: risk-parity weights on the real return covariance (was hardcoded
    # equal-weight, which bypassed the optimizer). Fail-closed to equal-weight when degenerate / synthetic.
    weights, weights_real = _optimizer_weights(candidates, return_matrix, n_days)
    w = [weights[alpha["candidate_id"]] for alpha in candidates]
    portfolio_returns = [
        sum(return_matrix[col][day] * w[col] for col in range(len(candidates)))
        for day in range(n_days)
    ]
    equity = 100_000_000.0
    equity_curve = []
    for day, daily_return in enumerate(portfolio_returns):
        equity *= 1 + daily_return
        equity_curve.append({"day": day, "equity": round(equity, 2)})
    raw_returns = [
        (equity_curve[i]["equity"] - equity_curve[i - 1]["equity"]) / equity_curve[i - 1]["equity"]
        for i in range(1, len(equity_curve))
    ]
    sharpe = compute_sharpe(raw_returns) or 0.0
    max_dd = compute_max_drawdown([point["equity"] for point in equity_curve]) or 0.0
    total_return = (equity_curve[-1]["equity"] / equity_curve[0]["equity"] - 1) * 100
    volatility = statistics.stdev(raw_returns) * math.sqrt(252) * 100 if len(raw_returns) > 1 else 0.0
    risk_raw = []
    attribution_raw = []
    for idx, alpha in enumerate(candidates):
        alpha_id = alpha["candidate_id"]
        alpha_returns = return_matrix[idx]
        alpha_vol = statistics.stdev(alpha_returns) * math.sqrt(252) if len(alpha_returns) > 1 else 0.0
        weight = weights[alpha_id]
        growth = 1.0
        for daily_return in alpha_returns:
            growth *= 1 + daily_return
        alpha_total_return = (growth - 1) * 100
        risk_raw.append({
            "alpha_id": alpha_id,
            "weight": weight,
            "annualized_volatility": alpha_vol,
            "raw_contribution": abs(weight) * alpha_vol,
        })
        attribution_raw.append({
            "alpha_id": alpha_id,
            "weight": weight,
            "alpha_total_return": alpha_total_return,
            "contribution_return": weight * alpha_total_return,
        })
    total_risk = sum(item["raw_contribution"] for item in risk_raw) or 1.0
    return {
        "simulation": {
            "run_id": "sim-backend-001",
            "status": "COMPLETED",
            "metrics": {
                "sharpe": round(sharpe, 3),
                "total_return": round(total_return, 2),
                "max_drawdown": round(max_dd * 100, 2),
                "volatility": round(volatility, 2),
            },
            "equity_curve": equity_curve,
        },
        "drawdown_curve": _portfolio_drawdown_curve(equity_curve),
        "weights": {
            "weights_id": "weights-backend-001",
            "weights": weights,
            "gross_exposure": round(sum(abs(value) for value in weights.values()), 4),
            "expected_sharpe": round(sharpe, 3),
            "expected_ic": round(statistics.mean(alpha["ic"] for alpha in candidates), 4),
            "method": "RISK_PARITY" if weights_real else "EQUAL_WEIGHT_FALLBACK",
            "optimizer_real": weights_real,
        },
        "risk_contributions": [
            {
                "alpha_id": item["alpha_id"],
                "weight": item["weight"],
                "annualized_volatility": round(item["annualized_volatility"], 6),
                "contribution": round(item["raw_contribution"] / total_risk, 6),
            }
            for item in risk_raw
        ],
        "return_attribution": [
            {
                "alpha_id": item["alpha_id"],
                "weight": item["weight"],
                "alpha_total_return": round(item["alpha_total_return"], 4),
                "contribution_return": round(item["contribution_return"], 4),
            }
            for item in attribution_raw
        ],
        "analytics_method": "BACKEND_RESEARCH_SIMULATION_ATTRIBUTION",
    }


def _real_monitoring_snapshot() -> Dict:
    """Rank #26: the REAL integrity snapshot via monitoring.build_snapshot — it verifies the durable
    governance audit chain (Rank #20) and reports the kill-switch state, replacing a purely hardcoded
    health view. The service latencies below stay ILLUSTRATIVE (there is no real service mesh at L2);
    this block is the verifiable health signal. Fail-closed to an honest marker if the chain is absent."""
    try:
        from services.monitoring.health import build_snapshot
        from services.monitoring.audit_store import DurableAuditLog
        from services.risk.pre_trade.kill_switch import KillSwitch
        audit_db = os.path.join("data", "cache", "governance_audit.db")
        if not os.path.exists(audit_db):
            return {"provenance": "NO_DURABLE_AUDIT_CHAIN", "audit_chain_valid": None,
                    "kill_switch_active": False, "healthy": None, "anomalies": [], "audit_events": 0}
        audit = DurableAuditLog(audit_db)
        snap = build_snapshot(timestamp=0, audit_log=audit, kill_switch=KillSwitch())
        return {"provenance": "REAL_MONITORING_SNAPSHOT", "audit_chain_valid": snap.audit_chain_valid,
                "kill_switch_active": snap.kill_switch_active, "healthy": snap.healthy,
                "anomalies": list(snap.anomalies), "audit_events": len(audit.events)}
    except Exception:
        return {"provenance": "MONITORING_ERROR", "audit_chain_valid": False, "healthy": False,
                "kill_switch_active": True, "anomalies": ["MONITORING_ERROR"], "audit_events": 0}


def _system_health_payload() -> Dict:
    return {
        "monitoring": _real_monitoring_snapshot(),
        "latency_provenance": "ILLUSTRATIVE_NOT_MEASURED",
        "services": [
            {"service": "research-console-api", "status": "healthy", "latency_ms": 3.4, "error_rate": 0.0},
            {"service": "market-data-ingestor", "status": "healthy", "latency_ms": 4.8, "error_rate": 0.001},
            {"service": "alpha-discovery-engine", "status": "healthy", "latency_ms": 41.2, "error_rate": 0.008},
            {"service": "alpha-validation", "status": "healthy", "latency_ms": 13.7, "error_rate": 0.002},
            {"service": "paper-trading", "status": "healthy", "latency_ms": 8.9, "error_rate": 0.0},
            {"service": "portfolio-research", "status": "healthy", "latency_ms": 16.4, "error_rate": 0.001},
            {"service": "research-risk", "status": "degraded", "latency_ms": 82.5, "error_rate": 0.027},
        ],
        "gates": [
            {"g": "Phase 1G.1 Research Pipeline", "s": "PASSED"},
            {"g": "Phase 1H.1 Paper Trading", "s": "PASSED"},
            {"g": "Phase 1I.1 Portfolio", "s": "READY"},
            {"g": "Phase 1J.1 Risk", "s": "READY"},
            {"g": "Phase 1K.1 Frontend", "s": "READY"},
        ],
        "safety_checks": [
            "No OMS/EMS imports",
            "No broker connectivity",
            "No CTP/FIX gateways",
            "No live trading controls",
            "Research-only",
        ],
    }


def _agents_payload() -> Dict:
    candidates = _alpha_candidates()
    approved_count = sum(1 for alpha in candidates if _validation_decision(alpha) == "APPROVED")
    agents = [
        {
            "agent_id": "agent-1",
            "name": "Macro Analyst",
            "state": "RUNNING",
            "runs_completed": 42,
            "alpha_approved": approved_count,
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
    ]
    return {
        "summary": {
            "active": sum(1 for agent in agents if agent["state"] == "RUNNING"),
            "total_runs": sum(agent["runs_completed"] for agent in agents),
            "approved": sum(agent["alpha_approved"] for agent in agents),
            "budget_spent_cny": sum(agent["budget_spent_cny"] for agent in agents),
        },
        "agents": agents,
    }


def _events_payload() -> Dict:
    """Read-only audit/event stream. Real path = the run_registry's research runs (each run IS a real
    audit event: a triggered/finished research backtest/search/refresh); deterministic synthetic fallback
    when the registry is empty (CI / fresh checkout). No order/broker/credential event ever appears."""
    try:
        runs = _runs_registry().list(limit=50)
    except Exception:
        runs = []
    if runs:
        events = []
        for r in runs:
            metrics = r.metrics or {}
            hk = next(iter(metrics), None)
            events.append({
                "event_id": f"evt-{r.run_id[4:16]}",
                "event_type": f"research.run.{r.status}",
                "timestamp": r.finished_ts or r.created_ts,
                "source": r.kind,
                "correlation_id": r.run_id,
                "payload": {"read_only": True, "title": r.title, "verdict": (r.verdict or "")[:80],
                            "headline_metric": hk, "headline_value": metrics.get(hk) if hk else None},
            })
        events.sort(key=lambda e: e["timestamp"], reverse=True)
        return {"events": events, "data_provenance": "RESEARCH_RUN_REGISTRY"}
    event_types = ["agent.created", "loop.completed", "alpha.approved", "paper.passed", "risk.approved",
                   "portfolio.simulated", "phase3a.blocked"]
    sources = ["research-agent-loop", "alpha-factory", "alpha-validation", "paper-trading", "research-risk",
               "portfolio-research", "phase3a-governance"]
    base_timestamp = 1_781_222_400
    return {
        "events": [
            {"event_id": f"evt-{1000 + idx}", "event_type": f"research.{event_types[idx % len(event_types)]}",
             "timestamp": base_timestamp - idx * 300, "source": sources[idx % len(sources)],
             "correlation_id": f"corr-{2000 + idx}",
             "payload": {"read_only": True, "sequence": idx, "lineage": "BACKEND_COMPUTED_SNAPSHOT"}}
            for idx in range(12)
        ],
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
    }


def _phase3a_readiness_payload() -> Dict:
    approved_count = sum(1 for criterion in PHASE3A_CRITERIA if criterion["status"] == "APPROVED")
    return {
        "readiness": {
            "current_level": "L2_DRY_RUN_ONLY",
            "target_level": "L3_BROKER_TEST_ENV_READY",
            "decision": "NO_GO",
            "phase_status": "BLOCKED",
            "implementation_authorized": False,
            "approved_count": approved_count,
            "total_count": len(PHASE3A_CRITERIA),
            "criteria": PHASE3A_CRITERIA,
            "blockers": PHASE3A_BLOCKERS,
        }
    }


def _repo_inventory() -> Dict:
    """REAL read-only introspection of the checked-out repo — grounds the curated module/maturity map in
    hard counts: per-domain module + LOC counts under services/, test files/functions, console endpoints/pages.
    Deterministic given the code on disk; no network; read-only. Fail-safe: returns {} on any FS error."""
    import os
    try:
        def _count_py(base):
            n = loc = 0
            for dirpath, _dirs, files in os.walk(base):
                if "__pycache__" in dirpath:
                    continue
                for f in files:
                    if f.endswith(".py"):
                        n += 1
                        try:
                            with open(os.path.join(dirpath, f), encoding="utf-8", errors="ignore") as fh:
                                loc += sum(1 for _ in fh)
                        except OSError:
                            pass
            return n, loc

        svc = os.path.join(_REPO_ROOT, "services")
        domains = []
        if os.path.isdir(svc):
            for name in sorted(os.listdir(svc)):
                p = os.path.join(svc, name)
                if os.path.isdir(p) and not name.startswith("__"):
                    n, loc = _count_py(p)
                    if n:
                        domains.append({"domain": name, "n_modules": n, "loc": loc})
        n_test_files = n_test_fns = 0
        tests_dir = os.path.join(_REPO_ROOT, "tests")
        for dirpath, _dirs, files in os.walk(tests_dir):
            for f in files:
                if f.startswith("test_") and f.endswith(".py"):
                    n_test_files += 1
                    try:
                        with open(os.path.join(dirpath, f), encoding="utf-8", errors="ignore") as fh:
                            n_test_fns += sum(1 for ln in fh if ln.lstrip().startswith("def test_"))
                    except OSError:
                        pass
        pages_dir = os.path.join(_REPO_ROOT, "apps", "research-console", "src", "pages")
        n_pages = len([f for f in os.listdir(pages_dir) if f.endswith(".tsx")]) if os.path.isdir(pages_dir) else 0
        n_endpoints = len([r for r in app.routes
                           if "GET" in getattr(r, "methods", set()) and str(getattr(r, "path", "")).startswith("/api/v1/console/")])
        return {
            "domains": domains,
            "total_services_modules": sum(d["n_modules"] for d in domains),
            "total_services_loc": sum(d["loc"] for d in domains),
            "n_test_files": n_test_files, "n_test_functions": n_test_fns,
            "n_console_pages": n_pages, "n_console_endpoints": n_endpoints,
        }
    except Exception:
        return {}


def _architecture_payload() -> Dict:
    modules = [
        {
            "id": "frontend-console",
            "name": "Research Console",
            "layer": "Frontend",
            "status": "READY",
            "maturity": 0.72,
            "owner": "Research Platform",
            "capability": "Bilingual read-only dashboards, backend source status, responsive visualizations",
            "next_gap": "Role-aware workflows and richer drill-down navigation",
        },
        {
            "id": "console-api",
            "name": "Console API",
            "layer": "API",
            "status": "READY",
            "maturity": 0.68,
            "owner": "Research Platform",
            "capability": "FastAPI read-only computed snapshots for research, risk, portfolio, governance",
            "next_gap": "Versioned contracts, auth, pagination, and persisted snapshot history",
        },
        {
            "id": "data-catalog",
            "name": "Data Catalog / Feature Store",
            "layer": "Research Data",
            "status": "PARTIAL",
            "maturity": 0.34,
            "owner": "Research Platform",
            "capability": "Read-only dataset versions, quality checks, feature lineage, point-in-time policy",
            "next_gap": "Persist catalog metadata, feature materialization jobs, and dataset access controls",
        },
        {
            "id": "alpha-engine",
            "name": "Alpha / Factor Engine",
            "layer": "Research Compute",
            "status": "READY",
            "maturity": 0.46,
            "owner": "Alpha Research",
            "capability": "IC, rank IC, ICIR, deterministic candidate scoring, screening buckets",
            "next_gap": "Full alpha DAG, persisted feature values, and richer expression lineage",
        },
        {
            "id": "validation-engine",
            "name": "Validation Engine",
            "layer": "Research Compute",
            "status": "PARTIAL",
            "maturity": 0.38,
            "owner": "Alpha Research",
            "capability": "Leakage, walk-forward, cost, capacity, decay gates in snapshot form",
            "next_gap": "Full backtest engine, nested CV, multiple testing controls, artifact storage",
        },
        {
            "id": "paper-trading",
            "name": "Paper Trading",
            "layer": "Simulation",
            "status": "READY",
            "maturity": 0.52,
            "owner": "Trading Research",
            "capability": "Paper PnL, equity curves, metrics, dry-run adapter gate",
            "next_gap": "Persistent event replay, fill model variants, market impact model",
        },
        {
            "id": "portfolio-research",
            "name": "Portfolio Research",
            "layer": "Simulation",
            "status": "READY",
            "maturity": 0.48,
            "owner": "Portfolio Research",
            "capability": "Weights, simulated equity, drawdown, risk contribution, return attribution",
            "next_gap": "Optimizer, constraints, factor risk model, scenario stress library",
        },
        {
            "id": "scenario-stress",
            "name": "Scenario Stress Library",
            "layer": "Risk",
            "status": "PARTIAL",
            "maturity": 0.36,
            "owner": "Risk / Portfolio Research",
            "capability": "Read-only volatility, liquidity, cost, crowding, and regime break stress replays",
            "next_gap": "Persist governed scenario catalog, independent calibration, and stress approval workflow",
        },
        {
            "id": "risk-gate",
            "name": "Research Risk Gate",
            "layer": "Risk",
            "status": "READY",
            "maturity": 0.58,
            "owner": "Risk",
            "capability": "Fail-closed risk gates, reproducibility hash, gate reasons",
            "next_gap": "Independent risk service deployment, policy versioning, model risk workflow",
        },
        {
            "id": "model-risk-review",
            "name": "Model Risk Review",
            "layer": "Governance",
            "status": "PARTIAL",
            "maturity": 0.33,
            "owner": "Risk / Model Governance",
            "capability": "Read-only model inventory, validation dimensions, issue register, residual risk and evidence sources",
            "next_gap": "Persist independent validation workflow, reviewer signoff, and monitored model limitations",
        },
        {
            "id": "evidence-ledger",
            "name": "Immutable Evidence Ledger",
            "layer": "Governance",
            "status": "PARTIAL",
            "maturity": 0.37,
            "owner": "Model Governance / Research Platform",
            "capability": "Read-only hash-chained evidence snapshots across experiments, data, backtests, stress, and model risk",
            "next_gap": "Persist append-only evidence store, signed reviewer attestations, and access-controlled audit exports",
        },
        {
            "id": "reviewer-workflow",
            "name": "Reviewer Workflow",
            "layer": "Governance",
            "status": "PARTIAL",
            "maturity": 0.35,
            "owner": "Model Governance / Compliance",
            "capability": "Read-only reviewer tasks, approval gates, SLA aging, blocking conditions, and workload visibility",
            "next_gap": "Persist signed attestations, role-based access control, workflow mutations, and audit export approvals",
        },
        {
            "id": "access-control",
            "name": "Access Control / Audit Export",
            "layer": "Governance",
            "status": "PARTIAL",
            "maturity": 0.32,
            "owner": "Security / Compliance",
            "capability": "Read-only RBAC matrix, forbidden capability registry, access reviews, and audit export metadata",
            "next_gap": "Implement real identity provider integration, signed export approvals, and access-change audit trail",
        },
        {
            "id": "monitoring-drift",
            "name": "Monitoring & Drift",
            "layer": "Operations",
            "status": "PARTIAL",
            "maturity": 0.34,
            "owner": "Operations / Research Platform",
            "capability": "Read-only alpha decay, feature drift, data freshness, model risk aging, reviewer SLA, and breach watchlists",
            "next_gap": "Persist drift metrics, threshold policies, alert routing, and incident workflow integration",
        },
        {
            "id": "incident-response",
            "name": "Incident Response",
            "layer": "Operations",
            "status": "PARTIAL",
            "maturity": 0.31,
            "owner": "Operations / Risk / Model Governance",
            "capability": "Read-only incident routing, SLA breach tracking, escalation state, runbook mapping, and evidence-linked timelines",
            "next_gap": "Persist incident workflow state, signed postmortems, external notification routing, and operational audit trails",
        },
        {
            "id": "promotion-control",
            "name": "Promotion Control",
            "layer": "Governance",
            "status": "PARTIAL",
            "maturity": 0.33,
            "owner": "Research Governance / Risk / Operations",
            "capability": "Read-only candidate promotion gates across alpha screen, validation, paper replay, risk, model risk, monitoring, incidents, evidence, and access",
            "next_gap": "Persist signed promotion decisions, stage transition history, and independent release committee approvals",
        },
        {
            "id": "release-committee",
            "name": "Release Committee",
            "layer": "Governance",
            "status": "PARTIAL",
            "maturity": 0.30,
            "owner": "Release Committee / Compliance / Risk",
            "capability": "Read-only committee agenda, quorum, vote state, required attestations, and release decision blockers",
            "next_gap": "Persist signed votes, meeting minutes, quorum audit, and committee-approved release packages",
        },
        {
            "id": "phase3a-governance",
            "name": "Phase 3A Governance",
            "layer": "Governance",
            "status": "BLOCKED",
            "maturity": 0.42,
            "owner": "CTO / Compliance / Security",
            "capability": "Readiness matrix, blockers, safety boundary, governance scanner",
            "next_gap": "Close E01/E02/E03/E04/E06 before broker test environment work",
        },
    ]
    data_flows = [
        {"from": "frontend-console", "to": "console-api", "label": "GET read-only snapshots", "mode": "READ_ONLY"},
        {"from": "console-api", "to": "data-catalog", "label": "dataset versions, feature specs, quality checks", "mode": "COMPUTED_SNAPSHOT"},
        {"from": "data-catalog", "to": "alpha-engine", "label": "point-in-time feature definitions and lineage", "mode": "OFFLINE_RESEARCH"},
        {"from": "console-api", "to": "alpha-engine", "label": "factor metrics and candidates", "mode": "COMPUTED_SNAPSHOT"},
        {"from": "alpha-engine", "to": "validation-engine", "label": "candidate validation reports", "mode": "COMPUTED_SNAPSHOT"},
        {"from": "validation-engine", "to": "paper-trading", "label": "approved research candidates", "mode": "DRY_RUN_ONLY"},
        {"from": "paper-trading", "to": "portfolio-research", "label": "paper curves and candidate weights", "mode": "SIMULATION"},
        {"from": "portfolio-research", "to": "scenario-stress", "label": "candidate returns, liquidity, capacity, and stress haircuts", "mode": "STRESS_REPLAY"},
        {"from": "scenario-stress", "to": "risk-gate", "label": "scenario decisions and factor stress contributions", "mode": "RISK_REVIEW"},
        {"from": "portfolio-research", "to": "risk-gate", "label": "portfolio returns, weights, capacity", "mode": "RISK_REVIEW"},
        {"from": "risk-gate", "to": "model-risk-review", "label": "model evidence, residual risk, and issue register", "mode": "MODEL_GOVERNANCE"},
        {"from": "scenario-stress", "to": "model-risk-review", "label": "stress calibration evidence and limitations", "mode": "MODEL_GOVERNANCE"},
        {"from": "model-risk-review", "to": "evidence-ledger", "label": "sealed model risk evidence and open issues", "mode": "HASH_CHAINED_EVIDENCE"},
        {"from": "phase3a-governance", "to": "evidence-ledger", "label": "NO_GO blockers and readiness evidence", "mode": "HASH_CHAINED_EVIDENCE"},
        {"from": "evidence-ledger", "to": "reviewer-workflow", "label": "evidence refs, blockers, root hash, and review tasks", "mode": "READ_ONLY_WORKFLOW"},
        {"from": "model-risk-review", "to": "reviewer-workflow", "label": "model issues and independent validation tasks", "mode": "READ_ONLY_WORKFLOW"},
        {"from": "evidence-ledger", "to": "access-control", "label": "audit export metadata and root hash", "mode": "READ_ONLY_RBAC"},
        {"from": "reviewer-workflow", "to": "access-control", "label": "reviewer roles, access reviews, and segregation checks", "mode": "READ_ONLY_RBAC"},
        {"from": "data-catalog", "to": "monitoring-drift", "label": "feature quality, dataset freshness, point-in-time policy signals", "mode": "DRIFT_MONITORING"},
        {"from": "model-risk-review", "to": "monitoring-drift", "label": "issue aging, residual risk, and model validation status", "mode": "DRIFT_MONITORING"},
        {"from": "reviewer-workflow", "to": "monitoring-drift", "label": "SLA aging and blocking task status", "mode": "DRIFT_MONITORING"},
        {"from": "monitoring-drift", "to": "incident-response", "label": "BREACH/WATCH triggers, owners, thresholds, and evidence refs", "mode": "READ_ONLY_INCIDENT_ROUTING"},
        {"from": "access-control", "to": "incident-response", "label": "role boundaries and export restrictions for incident evidence", "mode": "READ_ONLY_INCIDENT_ROUTING"},
        {"from": "incident-response", "to": "reviewer-workflow", "label": "postmortem and independent review requirements", "mode": "READ_ONLY_WORKFLOW"},
        {"from": "validation-engine", "to": "promotion-control", "label": "validation decisions, gate pass counts, and rejection reasons", "mode": "READ_ONLY_PROMOTION_GATING"},
        {"from": "paper-trading", "to": "promotion-control", "label": "paper replay status, PnL metrics, and dry-run evidence", "mode": "READ_ONLY_PROMOTION_GATING"},
        {"from": "risk-gate", "to": "promotion-control", "label": "risk approvals, gate results, and reproducibility hashes", "mode": "READ_ONLY_PROMOTION_GATING"},
        {"from": "model-risk-review", "to": "promotion-control", "label": "model intended-use restrictions and open issue register", "mode": "READ_ONLY_PROMOTION_GATING"},
        {"from": "monitoring-drift", "to": "promotion-control", "label": "candidate drift status and blocking monitors", "mode": "READ_ONLY_PROMOTION_GATING"},
        {"from": "incident-response", "to": "promotion-control", "label": "active incidents, SLA breaches, and containment actions", "mode": "READ_ONLY_PROMOTION_GATING"},
        {"from": "evidence-ledger", "to": "promotion-control", "label": "evidence root hash and immutable snapshot refs", "mode": "READ_ONLY_PROMOTION_GATING"},
        {"from": "access-control", "to": "promotion-control", "label": "least-privilege and forbidden-capability enforcement", "mode": "READ_ONLY_PROMOTION_GATING"},
        {"from": "promotion-control", "to": "phase3a-governance", "label": "NO_GO release decision and dry-run-only promotion evidence", "mode": "GOVERNANCE"},
        {"from": "promotion-control", "to": "release-committee", "label": "candidate promotion decisions, blocking gates, and evidence refs", "mode": "READ_ONLY_COMMITTEE_REVIEW"},
        {"from": "reviewer-workflow", "to": "release-committee", "label": "required reviewer roles, open tasks, and approval gate status", "mode": "READ_ONLY_COMMITTEE_REVIEW"},
        {"from": "evidence-ledger", "to": "release-committee", "label": "root hash, blockers, and sealed evidence package metadata", "mode": "READ_ONLY_COMMITTEE_REVIEW"},
        {"from": "access-control", "to": "release-committee", "label": "segregation of duties and export restrictions", "mode": "READ_ONLY_COMMITTEE_REVIEW"},
        {"from": "release-committee", "to": "phase3a-governance", "label": "committee NO_GO recommendation and unmet attestation requirements", "mode": "GOVERNANCE"},
        {"from": "risk-gate", "to": "phase3a-governance", "label": "gate evidence and safety status", "mode": "GOVERNANCE"},
    ]
    gaps = [
        {"id": "G01", "area": "Market Data Lake", "severity": "HIGH", "status": "PARTIAL", "next_action": "Persist versioned market data snapshots and quality checks outside console snapshots"},
        {"id": "G02", "area": "Feature Store", "severity": "HIGH", "status": "PARTIAL", "next_action": "Add materialized offline features, feature serving contracts, and access controls"},
        {"id": "G03", "area": "Backtest Engine", "severity": "HIGH", "status": "PARTIAL", "next_action": "Add event-driven backtests, costs, slippage, and replayable artifacts"},
        {"id": "G04", "area": "Experiment Registry", "severity": "MEDIUM", "status": "PARTIAL", "next_action": "Persist immutable experiment history, approvals, and artifact storage"},
        {"id": "G05", "area": "Risk Model", "severity": "MEDIUM", "status": "PARTIAL", "next_action": "Add factor covariance, independent scenario calibration, and stress governance workflow"},
        {"id": "G06", "area": "Model Risk Management", "severity": "MEDIUM", "status": "PARTIAL", "next_action": "Persist independent validation workflow, reviewer signoff, and ongoing monitoring"},
        {"id": "G07", "area": "Immutable Evidence Ledger", "severity": "MEDIUM", "status": "PARTIAL", "next_action": "Persist append-only evidence ledger and signed attestations"},
        {"id": "G08", "area": "Reviewer Workflow", "severity": "MEDIUM", "status": "PARTIAL", "next_action": "Persist reviewer tasks, signed attestations, and role-based workflow controls"},
        {"id": "G09", "area": "Access Control", "severity": "MEDIUM", "status": "PARTIAL", "next_action": "Integrate real identity provider, signed exports, and immutable access-change audit trail"},
        {"id": "G10", "area": "Monitoring & Drift", "severity": "MEDIUM", "status": "PARTIAL", "next_action": "Persist drift metrics, alert routing, and incident workflow integration"},
        {"id": "G11", "area": "Incident Response", "severity": "MEDIUM", "status": "PARTIAL", "next_action": "Persist incident workflow state, external notification routing, and signed postmortems"},
        {"id": "G12", "area": "Promotion Control", "severity": "MEDIUM", "status": "PARTIAL", "next_action": "Persist signed promotion decisions, stage transition history, and release committee approvals"},
        {"id": "G13", "area": "Release Committee", "severity": "MEDIUM", "status": "PARTIAL", "next_action": "Persist signed votes, meeting minutes, quorum audit, and release package approvals"},
    ]
    return {
        "current_level": "L2_DRY_RUN_ONLY",
        "target_level": "L3_BROKER_TEST_ENV_READY",
        "phase3a_decision": "NO_GO",
        "implementation_authorized": False,
        "architecture_score": round(statistics.mean(module["maturity"] for module in modules), 3),
        "inventory": _repo_inventory(),
        "modules": modules,
        "data_flows": data_flows,
        "gaps": gaps,
        "safety_boundary": {
            "read_only_console": True,
            "execution_boundary": "LIVE_TRADING_DISALLOWED",
            "forbidden_capabilities": [
                "No real CTP/FIX implementation",
                "No broker SDK imports",
                "No socket connections",
                "No credential files",
                "No live order APIs",
            ],
        },
    }


def _short_hash(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _degradation_thresholds_snapshot() -> Dict:
    """Read-only snapshot of the REAL DegradationThresholds the live-vs-backtest monitor would apply."""
    try:
        from services.alpha.degradation_monitor import DegradationThresholds
        th = DegradationThresholds()
        return {
            "min_ic_retention": th.min_ic_retention, "max_return_gap": th.max_return_gap,
            "retire_return_gap": th.retire_return_gap, "max_slippage_mult": th.max_slippage_mult,
            "max_turnover_mult": th.max_turnover_mult, "max_drawdown": th.max_drawdown,
            "max_capacity_breach_frac": th.max_capacity_breach_frac,
        }
    except Exception:
        return {"min_ic_retention": 0.3, "max_return_gap": 0.6, "retire_return_gap": 1.0,
                "max_slippage_mult": 2.0, "max_turnover_mult": 2.0, "max_drawdown": 0.3,
                "max_capacity_breach_frac": 0.5}


def _lifecycle_rows_real(st) -> List[Dict]:
    """One lifecycle row per recorded alpha, from the REAL experiment_registry. backtest_ic / sharpe /
    pvalue / net_sharpe / deflated_sharpe / verdict are real immutable evaluation artifacts. live_ic is
    the REAL accumulated out-of-sample paper IC from the forward paper-track store when a track exists
    AND has per-period IC; the current offline source has per-period portfolio returns but no
    cross-sectional IC panel, so live_ic is honestly None even for paper-tracked alphas. The recommendation
    is driven by the REAL held-out paper Sharpe vs the train-head Sharpe (apples-to-apples), with the
    monitor's "missing IC" data-alert stripped (a metric we never compute must not masquerade as decay):
    PAPER_RETURN_ONLY = paper return shows no degradation but IC isn't tracked; DEGRADED/RETIRED = a real
    Sharpe drop; NO_LIVE_DATA = no paper track at all. The paper track is a research-only record: it feeds
    the degradation engine's recommendation but NEVER unlocks any automatic promotion (auto_approved stays
    0; non-human never APPROVED). days_in_state is None because evaluation as_of is a single batch
    timestamp, not a per-alpha tenure. generator is derived from the heuristic operator (no real lineage)."""
    def _r(v, n):
        return round(v, n) if isinstance(v, (int, float)) else None
    try:
        alphas = st.all_alphas()
    except Exception:
        return []
    pts = _load_paper_track_store()
    rows: List[Dict] = []
    for a in sorted(alphas, key=lambda x: (x.get("sharpe") if x.get("sharpe") is not None else -1e9),
                    reverse=True):
        aid = a.get("alpha_id")
        try:
            evs = st.list_evaluations(aid)
        except Exception:
            evs = []
        ej: Dict = {}
        if evs:
            try:
                ej = json.loads(evs[-1].get("evaluation_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                ej = {}
        try:
            dj = json.loads(a.get("definition_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            dj = {}
        operator = (dj.get("params") or {}).get("operator")
        # Live side from the REAL forward paper-track store: live_ic is the accumulated out-of-sample
        # paper IC (None when this alpha has no paper track, or — as with the current offline source —
        # when no per-period cross-sectional IC was recorded). When a paper track exists, the degradation
        # engine compares the held-out paper Sharpe to the train-head Sharpe (like-for-like, never a
        # Sharpe-vs-cumulative-return mismatch); otherwise the recommendation stays NO_LIVE_DATA.
        t = pts.track(aid) if pts is not None else None
        live_ic = t["paper_ic"] if (t and t.get("paper_ic") is not None) else None
        if t and isinstance(t.get("paper_sharpe"), (int, float)):
            from services.alpha.degradation_monitor import monitor_alpha_health
            health = monitor_alpha_health(
                {"return": t.get("backtest_sharpe")},
                {"ic": live_ic, "return": t.get("paper_sharpe")})
            # Judge only on signals we actually have: strip the monitor's "missing input" data-alert so a
            # metric we never compute (per-period IC) cannot masquerade as a degradation. A real return/
            # Sharpe drop still surfaces as DEGRADED/RETIRED; otherwise PAPER_RETURN_ONLY (paper return
            # holds up, but IC is not tracked so we do not certify full health).
            real_alerts = [al for al in health["alerts"] if al.get("metric") != "data"]
            if real_alerts:
                _ORD = {"OK": 0, "DEGRADED": 1, "PAUSED": 2, "RETIRED": 3}
                recommendation = max((al["severity"] for al in real_alerts), key=lambda s: _ORD[s])
            else:
                recommendation = "PAPER_RETURN_ONLY"
        else:
            recommendation = "NO_LIVE_DATA"
        rows.append({
            "alpha_id": aid,
            "state": str(a.get("state") or "UNKNOWN"),
            "generator": f"heuristic:{operator}" if operator else "heuristic_sweep",
            "days_in_state": None,
            "backtest_ic": _r(ej.get("ic_mean"), 5),
            "sharpe": _r(a.get("sharpe"), 4),
            "pvalue": _r(a.get("pvalue"), 4),
            "net_sharpe": _r(ej.get("net_sharpe"), 4),
            "deflated_sharpe": _r(ej.get("deflated_sharpe"), 4),
            "turnover": _r(ej.get("turnover"), 4),
            "verdict": str(ej.get("verdict") or "—"),
            "live_ic": live_ic,
            "recommendation": recommendation,
            "alerts": [],
        })
    return rows


def _lifecycle_rows_synthetic() -> List[Dict]:
    """Deterministic fallback (CI / fresh checkout with no experiment_registry). Rotates lifecycle states so
    the page shape stays exercised; live_ic stays None (no live feed is ever real)."""
    states = ["TESTING", "VALIDATION", "TESTING", "VALIDATION"]
    rows: List[Dict] = []
    for idx, c in enumerate(_alpha_candidates()):
        rows.append({
            "alpha_id": c["candidate_id"],
            "state": states[idx % len(states)],
            "generator": "heuristic_sweep",
            "days_in_state": None,
            "backtest_ic": round(c.get("ic", 0.0), 5),
            "sharpe": round(c.get("sharpe", 0.0), 4) if isinstance(c.get("sharpe"), (int, float)) else None,
            "pvalue": None,
            "net_sharpe": round(c.get("sharpe", 0.0), 4) if isinstance(c.get("sharpe"), (int, float)) else None,
            "deflated_sharpe": None,
            "turnover": round(c.get("turnover", 0.0), 4) if isinstance(c.get("turnover"), (int, float)) else None,
            "verdict": "REQUIRES_HUMAN_REVIEW",
            "live_ic": None,
            "recommendation": "NO_LIVE_DATA",
            "alerts": [],
        })
    return rows


def _lifecycle_summary(rows: List[Dict]) -> Dict:
    state_counts: Dict[str, int] = {}
    for r in rows:
        state_counts[r["state"]] = state_counts.get(r["state"], 0) + 1
    ics = [r["backtest_ic"] for r in rows if isinstance(r["backtest_ic"], (int, float))]
    sharpes = [r["sharpe"] for r in rows if isinstance(r["sharpe"], (int, float))]
    awaiting = sum(1 for r in rows if any(t in str(r["verdict"]).upper() for t in ("REVIEW", "REQUIRE")))
    return {
        "total": len(rows),
        "state_counts": state_counts,
        "live_tracked": sum(1 for r in rows if r["live_ic"] is not None),
        "paper_tracked": sum(1 for r in rows if r.get("recommendation") not in (None, "NO_LIVE_DATA")),
        "awaiting_review": awaiting,
        "needs_action": awaiting,
        "positive_sharpe": sum(1 for s in sharpes if s > 0),
        "avg_backtest_ic": round(sum(ics) / len(ics), 5) if ics else 0.0,
    }


def _lifecycle_monitor_payload() -> Dict:
    """Alpha lifecycle monitor: REAL backtest-side artifacts from experiment_registry; honest NO_LIVE_DATA
    on the live side (L2 has no live/paper IC). Fail-closed to synthetic when the registry is absent."""
    st = _load_experiment_store()
    rows: List[Dict] = []
    provenance = "SYNTHETIC_DETERMINISTIC"
    if st is not None:
        rows = _lifecycle_rows_real(st)
        if rows:
            provenance = "EXPERIMENT_REGISTRY"
    if not rows:
        rows = _lifecycle_rows_synthetic()
        provenance = "SYNTHETIC_DETERMINISTIC"
    return {
        "monitoring_mode": "READ_ONLY_LIFECYCLE_MONITOR",
        "data_provenance": provenance,
        "summary": _lifecycle_summary(rows),
        "rows": rows,
        "thresholds": _degradation_thresholds_snapshot(),
        "lifecycle_policy": {
            "recommends_only": True,
            "promotion_human_only": True,
            "live_trading_built": False,
            "live_ic_available": False,
        },
    }


def _experiments_payload() -> Dict:
    st = _load_experiment_store()
    if st is not None:
        experiments = _real_experiment_rows(st)
        if experiments:
            return {
                "registry_mode": "READ_ONLY_RESEARCH_REGISTRY",
                "data_provenance": "EXPERIMENT_REGISTRY",
                "summary": {
                    "total_experiments": len(experiments),
                    "approved": sum(1 for e in experiments if e["decision"] == "APPROVED"),
                    "rejected": sum(1 for e in experiments if str(e["decision"]).startswith("REJECTED")),
                    "needs_more_data": sum(1 for e in experiments if e["decision"] == "NEEDS_MORE_DATA"),
                    "artifact_count": sum(len(e["artifacts"]) for e in experiments),
                    "reproducible": sum(1 for e in experiments if e["reproducibility_hash"]),
                },
                "experiments": experiments,
                "stage_counts": {s: sum(1 for e in experiments if e["stage"] == s) for s in _EXP_STAGES},
                "lineage_policy": {"requires_config_hash": True, "requires_data_snapshot": True,
                                   "requires_metric_artifact": True, "mutable_records": False},
            }
    candidates = _alpha_candidates()
    agents = ["Macro Analyst", "Factor Hunter", "Momentum Specialist", "Portfolio Scout"]
    stages = ["DISCOVERY", "VALIDATION", "PAPER_REPLAY", "RISK_REVIEW", "ARCHIVED"]
    experiments = []
    for idx, alpha in enumerate(candidates[:10]):
        decision = _validation_decision(alpha)
        seed = 20260612 + idx * 17
        data_snapshot_id = f"md-snapshot-2026w{20 + idx % 4:02d}"
        config_hash = _short_hash(alpha["candidate_id"], alpha["expression"], seed, data_snapshot_id)
        reproducibility_hash = _short_hash("experiment", config_hash, alpha["ic"], alpha["sharpe"], alpha["turnover"])
        artifact_base = f"artifact://research/{alpha['candidate_id']}/{config_hash}"
        artifacts = [
            {
                "artifact_id": f"art-{idx + 1:02d}-config",
                "kind": "CONFIG",
                "status": "AVAILABLE",
                "uri": f"{artifact_base}/config.json",
                "content_hash": _short_hash(config_hash, "config"),
            },
            {
                "artifact_id": f"art-{idx + 1:02d}-metrics",
                "kind": "METRICS",
                "status": "AVAILABLE",
                "uri": f"{artifact_base}/metrics.json",
                "content_hash": _short_hash(config_hash, "metrics", alpha["composite_score"]),
            },
            {
                "artifact_id": f"art-{idx + 1:02d}-lineage",
                "kind": "LINEAGE",
                "status": "AVAILABLE",
                "uri": f"{artifact_base}/lineage.json",
                "content_hash": _short_hash(config_hash, "lineage", alpha["expression"]),
            },
        ]
        if decision != "APPROVED":
            artifacts.append({
                "artifact_id": f"art-{idx + 1:02d}-review",
                "kind": "REVIEW_NOTE",
                "status": "AVAILABLE",
                "uri": f"{artifact_base}/review.md",
                "content_hash": _short_hash(config_hash, "review", decision),
            })
        experiments.append({
            "experiment_id": f"exp-{alpha['candidate_id']}-v{idx % 3 + 1:03d}",
            "alpha_id": alpha["candidate_id"],
            "expression": alpha["expression"],
            "owner_agent": agents[idx % len(agents)],
            "stage": stages[idx % len(stages)],
            "decision": decision,
            "created_at": 1_781_222_400 - idx * 7_200,
            "execution_mode": "L2_DRY_RUN_ONLY",
            "config": {
                "universe": "CN_A_SHARE_RESEARCH",
                "horizon_days": ALPHA_WINDOWS[idx % len(ALPHA_WINDOWS)],
                "cost_bps": 18 + idx % 5 * 3,
                "seed": seed,
                "data_snapshot_id": data_snapshot_id,
                "config_hash": config_hash,
            },
            "metrics": {
                "ic": alpha["ic"],
                "rank_ic": alpha["rank_ic"],
                "sharpe": alpha["sharpe"],
                "turnover": alpha["turnover"],
                "capacity_estimate": alpha["capacity_estimate"],
                "composite_score": alpha["composite_score"],
            },
            "reproducibility_hash": reproducibility_hash,
            "artifacts": artifacts,
            "lineage": {
                "upstream": ["factor_engine", "validation_engine"],
                "downstream": ["paper_trading", "portfolio_research", "risk_gate"] if decision == "APPROVED" else ["validation_review"],
            },
        })
    return {
        "registry_mode": "READ_ONLY_RESEARCH_REGISTRY",
        "summary": {
            "total_experiments": len(experiments),
            "approved": sum(1 for item in experiments if item["decision"] == "APPROVED"),
            "rejected": sum(1 for item in experiments if str(item["decision"]).startswith("REJECTED")),
            "needs_more_data": sum(1 for item in experiments if item["decision"] == "NEEDS_MORE_DATA"),
            "artifact_count": sum(len(item["artifacts"]) for item in experiments),
            "reproducible": sum(1 for item in experiments if item["reproducibility_hash"]),
        },
        "experiments": experiments,
        "stage_counts": {
            stage: sum(1 for item in experiments if item["stage"] == stage)
            for stage in stages
        },
        "lineage_policy": {
            "requires_config_hash": True,
            "requires_data_snapshot": True,
            "requires_metric_artifact": True,
            "mutable_records": False,
        },
    }


_CRYPTO_DAILY_DB = os.path.join("data", "cache", "crypto_daily.db")
_REAL_DATASETS_CACHE: Optional[List[Dict]] = None


def _enumerate_real_datasets() -> List[Dict]:
    """Enumerate the REAL on-disk PIT store (crypto_daily.db) as catalog datasets — one row per instrument
    with a real content hash (build_dataset_version over the actual bars), real row_count, coverage derived
    from the event-time span, relative-snapshot freshness, and PIT-safety derived from the bars
    (knowledge_time >= event_time, i.e. no future knowledge). Module-cached (hashing every instrument is not
    free). Returns [] when the db is absent (CI / fresh checkout — data/cache/*.db is gitignored) so the
    catalog fail-closes to the synthetic fallback. We default to crypto_daily.db only (42 instruments); the
    heavier stores (binance.db = 336) are intentionally not enumerated to keep the hash pass fast."""
    global _REAL_DATASETS_CACHE
    if _REAL_DATASETS_CACHE is not None:
        return _REAL_DATASETS_CACHE
    if not os.path.exists(_CRYPTO_DAILY_DB):
        _REAL_DATASETS_CACHE = []
        return _REAL_DATASETS_CACHE
    rows: List[Dict] = []
    try:
        from services.data.persistent_store import SqlitePITStore
        from services.data.versioning import build_dataset_version
        st = SqlitePITStore(_CRYPTO_DAILY_DB, create=False)
        collected = []
        global_max_kt = 0
        for key in st.keys():
            try:
                bars = st.fetch_bars(key, 0, 9_999_999_999_999)
            except Exception:
                continue
            if not bars:
                continue
            global_max_kt = max(global_max_kt, max(b.knowledge_time for b in bars))
            collected.append((key, bars))
        for key, bars in collected:
            ets = [b.event_time for b in bars]
            kts = [b.knowledge_time for b in bars]
            span_days = (max(ets) - min(ets)) / 86_400_000.0
            coverage = min(1.0, len(bars) / (span_days + 1.0)) if span_days > 0 else 1.0
            pit_safe = all(k >= e for e, k in zip(ets, kts))
            freshness_hours = round(max(0.0, (global_max_kt - max(kts)) / 3_600_000.0), 1)
            quality = round(coverage if pit_safe else coverage * 0.5, 3)
            dv = build_dataset_version(f"crypto_daily.{key}", "crypto_daily", bars, created_at=0)
            rows.append({
                "dataset_id": f"crypto_daily.{key}",
                "name": key,
                "domain": "CRYPTO_MARKET_DATA",
                "version": "crypto_daily",
                "snapshot_id": "crypto_daily-snapshot",
                "row_count": int(getattr(dv, "row_count", len(bars))),
                "coverage": round(coverage, 3),
                "freshness_hours": freshness_hours,   # RELATIVE to the snapshot's newest knowledge_time
                "quality_score": quality,
                "point_in_time_safe": pit_safe,
                "owner": "Crypto Research",
                "storage_uri": f"sqlite://data/cache/crypto_daily.db#{key}",
                "content_hash": dv.content_hash,
            })
        rows.sort(key=lambda d: d["dataset_id"])
    except Exception:
        rows = []
    _REAL_DATASETS_CACHE = rows
    return rows


def _dataset_checks(dataset: Dict) -> List[Dict]:
    return [
        {"check_id": f"{dataset['dataset_id']}-coverage", "name": "coverage", "status": "PASS" if dataset["coverage"] >= 0.94 else "WARN", "metric": dataset["coverage"], "threshold": 0.94},
        {"check_id": f"{dataset['dataset_id']}-freshness", "name": "freshness_hours", "status": "PASS" if dataset["freshness_hours"] <= 24 else "WARN", "metric": dataset["freshness_hours"], "threshold": 24},
        {"check_id": f"{dataset['dataset_id']}-pit", "name": "point_in_time_safe", "status": "PASS" if dataset["point_in_time_safe"] else "FAIL", "metric": 1 if dataset["point_in_time_safe"] else 0, "threshold": 1},
    ]


_SYNTHETIC_CATALOG_DATASETS = [
        {
            "dataset_id": "ds-market-daily-bars",
            "name": "CN A-share daily bars",
            "domain": "MARKET_DATA",
            "version": "2026.06.12.001",
            "snapshot_id": "md-snapshot-2026w20",
            "row_count": 18_420_000,
            "coverage": 0.992,
            "freshness_hours": 3.2,
            "quality_score": 0.981,
            "point_in_time_safe": True,
            "owner": "Market Data",
            "storage_uri": "artifact://data-lake/market/daily-bars/2026w20",
        },
        {
            "dataset_id": "ds-corporate-actions",
            "name": "Corporate actions adjusted events",
            "domain": "REFERENCE_DATA",
            "version": "2026.06.12.001",
            "snapshot_id": "ca-snapshot-2026w20",
            "row_count": 214_800,
            "coverage": 0.987,
            "freshness_hours": 8.5,
            "quality_score": 0.963,
            "point_in_time_safe": True,
            "owner": "Reference Data",
            "storage_uri": "artifact://data-lake/reference/corporate-actions/2026w20",
        },
        {
            "dataset_id": "ds-index-membership",
            "name": "Point-in-time index membership",
            "domain": "REFERENCE_DATA",
            "version": "2026.06.12.001",
            "snapshot_id": "idx-snapshot-2026w20",
            "row_count": 1_322_600,
            "coverage": 0.978,
            "freshness_hours": 12.0,
            "quality_score": 0.956,
            "point_in_time_safe": True,
            "owner": "Reference Data",
            "storage_uri": "artifact://data-lake/reference/index-membership/2026w20",
        },
        {
            "dataset_id": "ds-fundamentals-ttm",
            "name": "Fundamentals TTM normalized",
            "domain": "FUNDAMENTAL_DATA",
            "version": "2026.06.11.002",
            "snapshot_id": "fund-snapshot-2026w20",
            "row_count": 4_860_000,
            "coverage": 0.941,
            "freshness_hours": 22.4,
            "quality_score": 0.927,
            "point_in_time_safe": True,
            "owner": "Fundamental Data",
            "storage_uri": "artifact://data-lake/fundamental/ttm/2026w20",
        },
        {
            "dataset_id": "ds-liquidity-profile",
            "name": "Liquidity and capacity profile",
            "domain": "LIQUIDITY_DATA",
            "version": "2026.06.12.001",
            "snapshot_id": "liq-snapshot-2026w20",
            "row_count": 5_520_000,
            "coverage": 0.969,
            "freshness_hours": 4.1,
            "quality_score": 0.948,
            "point_in_time_safe": True,
            "owner": "Trading Research",
            "storage_uri": "artifact://data-lake/liquidity/profile/2026w20",
        },
    ]


def _data_catalog_payload() -> Dict:
    from services.data.catalog import DATA_BACKEND, dataset_content_hash, verify_point_in_time_safe
    # DATASETS upgrade to REAL: enumerate the on-disk PIT store (crypto_daily.db) — real content hash,
    # row_count, coverage, PIT-safety. FEATURES / lineage stay SYNTHETIC (no persisted FeatureRegistry yet)
    # and are honestly marked. Fail-closed to the synthetic dataset fallback when no db is present (CI).
    real_datasets = _enumerate_real_datasets()
    if real_datasets:
        datasets = [dict(d) for d in real_datasets]
        datasets_provenance = "REAL_PIT_DATASETS"
        for dataset in datasets:
            dataset["checks"] = _dataset_checks(dataset)   # content_hash + PIT already real from the bars
    else:
        datasets = [dict(d) for d in _SYNTHETIC_CATALOG_DATASETS]
        datasets_provenance = "SYNTHETIC_DETERMINISTIC"
        for dataset in datasets:
            dataset["content_hash"] = dataset_content_hash(dataset["dataset_id"], dataset["version"])
            dataset["point_in_time_safe"] = verify_point_in_time_safe(dataset["dataset_id"])
            dataset["checks"] = _dataset_checks(dataset)

    feature_specs = [
        ("feat-ts-mean-close-5", "ts_mean_close_5", "PRICE_MOMENTUM", "(ts_mean :close 5)", "ds-market-daily-bars", 5, ["exp-alpha-1-v001", "exp-alpha-7-v001"]),
        ("feat-ts-rank-volume-20", "ts_rank_volume_20", "LIQUIDITY", "(ts_rank :volume 20)", "ds-market-daily-bars", 20, ["exp-alpha-2-v002", "exp-alpha-8-v002"]),
        ("feat-zscore-returns-10", "zscore_returns_10", "REVERSAL", "(zscore :returns 10)", "ds-market-daily-bars", 10, ["exp-alpha-3-v003"]),
        ("feat-ts-delta-vwap-30", "ts_delta_vwap_30", "EXECUTION_PRICE", "(ts_delta :vwap 30)", "ds-market-daily-bars", 30, ["exp-alpha-4-v001"]),
        ("feat-turnover-std-60", "ts_std_turnover_60", "LIQUIDITY", "(ts_std :turnover 60)", "ds-liquidity-profile", 60, ["exp-alpha-5-v002"]),
        ("feat-rank-decay-amount-90", "rank_decay_amount_90", "CAPACITY", "(rank_decay :amount 90)", "ds-liquidity-profile", 90, ["exp-alpha-6-v003"]),
        ("feat-index-member-lag", "index_member_lag_safe", "UNIVERSE", "(asof_join :index_member 1)", "ds-index-membership", 1, ["exp-alpha-1-v001", "exp-alpha-4-v001"]),
        ("feat-fundamental-yield-ttm", "fundamental_yield_ttm", "FUNDAMENTAL", "(point_in_time :earnings_yield_ttm)", "ds-fundamentals-ttm", 120, ["exp-alpha-9-v003"]),
    ]
    dataset_quality = {dataset["dataset_id"]: dataset["quality_score"] for dataset in datasets}
    real_dataset_ids = [d["dataset_id"] for d in datasets]
    features = []
    for idx, (feature_id, name, family, expression, dataset_id, lookback_days, experiments) in enumerate(feature_specs):
        # Features are illustrative/synthetic (no persisted FeatureRegistry yet); when datasets are REAL their
        # hardcoded source ids don't exist, so attach each feature to a real dataset node (round-robin) to keep
        # the lineage graph connected. The feature values themselves remain synthetic and are marked as such.
        if dataset_id not in dataset_quality:
            dataset_id = real_dataset_ids[idx % len(real_dataset_ids)]
        quality_score = max(0.0, min(1.0, dataset_quality[dataset_id] - (idx % 3) * 0.006))
        features.append({
            "feature_id": feature_id,
            "name": name,
            "family": family,
            "expression": expression,
            "source_dataset_id": dataset_id,
            "version": f"v{idx % 4 + 1}",
            "owner": "Alpha Research",
            "lookback_days": lookback_days,
            "point_in_time_policy": {
                "event_timestamp_column": "event_ts",
                "as_of_join": True,
                "max_source_lag_days": 1 if lookback_days <= 30 else 2,
                "leakage_guard": "STRICT_EVENT_TIME_UPPER_BOUND",
            },
            "quality_score": round(quality_score, 3),
            "lineage_hash": _short_hash(feature_id, dataset_id, expression, lookback_days),
            "used_by_experiments": experiments,
        })

    lineage_edges = []
    for feature in features:
        lineage_edges.append({"from": feature["source_dataset_id"], "to": feature["feature_id"], "kind": "DATASET_TO_FEATURE"})
        for experiment_id in feature["used_by_experiments"]:
            lineage_edges.append({"from": feature["feature_id"], "to": experiment_id, "kind": "FEATURE_TO_EXPERIMENT"})

    quality_checks = [check for dataset in datasets for check in dataset["checks"]]
    pit_safe_features = sum(1 for feature in features if feature["point_in_time_policy"]["as_of_join"])
    return {
        "catalog_mode": "READ_ONLY_DATA_CATALOG",
        "feature_store_mode": "OFFLINE_POINT_IN_TIME_RESEARCH",
        "data_backend": DATA_BACKEND,
        # REAL_PIT_DATASETS when datasets came from the on-disk PIT store; the feature/lineage block is still
        # synthetic regardless — honest partial. SYNTHETIC_DETERMINISTIC when no db (CI / fresh checkout).
        "data_provenance": datasets_provenance,
        "datasets_real": bool(real_datasets),
        "features_synthetic": True,
        "summary": {
            "datasets": len(datasets),
            "features": len(features),
            "quality_checks": len(quality_checks),
            "quality_passed": sum(1 for check in quality_checks if check["status"] == "PASS"),
            "point_in_time_safe_features": pit_safe_features,
            "lineage_edges": len(lineage_edges),
            "avg_dataset_quality": round(statistics.mean(dataset["quality_score"] for dataset in datasets), 3),
            "avg_feature_quality": round(statistics.mean(feature["quality_score"] for feature in features), 3),
        },
        "datasets": datasets,
        "features": features,
        "lineage_edges": lineage_edges,
        "quality_policy": {
            "requires_dataset_hash": True,
            "requires_event_timestamp": True,
            "requires_asof_join": True,
            "blocks_future_leakage": True,
            "mutable_raw_inputs": False,
        },
    }


def _segment_returns(raw_returns: List[float], segment_size: int = 20) -> List[Dict]:
    segments = []
    for start in range(0, len(raw_returns), segment_size):
        segment = raw_returns[start:start + segment_size]
        if not segment:
            continue
        cumulative = math.prod(1 + value for value in segment) - 1
        win_rate = sum(1 for value in segment if value > 0) / len(segment)
        segments.append({
            "segment_id": f"S{len(segments) + 1:02d}",
            "start_day": start,
            "end_day": start + len(segment) - 1,
            "return": round(cumulative, 5),
            "sharpe": round(compute_sharpe(segment) or 0.0, 3),
            "win_rate": round(win_rate, 3),
            "volatility": round(statistics.pstdev(segment) * math.sqrt(252), 4) if len(segment) > 1 else 0.0,
        })
    return segments


def _drawdown_periods(drawdowns: List[Dict[str, float]], limit: int = 5) -> List[Dict]:
    local_worst = sorted(drawdowns, key=lambda point: point["y"], reverse=True)[:limit]
    periods = []
    for rank, point in enumerate(local_worst, start=1):
        start_day = max(0, int(point["x"]) - 8)
        trough_day = int(point["x"])
        recovery_day = min(drawdowns[-1]["x"] if drawdowns else trough_day, trough_day + 12)
        periods.append({
            "rank": rank,
            "start_day": start_day,
            "trough_day": trough_day,
            "recovery_day": recovery_day,
            "depth": round(point["y"], 5),
            "duration_days": recovery_day - start_day,
        })
    return periods


def _monthly_returns(raw_returns: List[float], month_size: int = 21) -> List[Dict]:
    months = []
    for start in range(0, len(raw_returns), month_size):
        values = raw_returns[start:start + month_size]
        if not values:
            continue
        cumulative = math.prod(1 + value for value in values) - 1
        months.append({
            "month": f"2026-{len(months) + 1:02d}",
            "return": round(cumulative, 5),
            "positive_days": sum(1 for value in values if value > 0),
            "negative_days": sum(1 for value in values if value <= 0),
        })
    return months


def _backtest_explainability_payload(alpha_id: str = "alpha-1") -> Dict:
    idx = _alpha_index(alpha_id)
    alpha = _computed_alpha(idx)
    factors = _factor_values(idx)
    forward_returns = _forward_returns(factors, idx)
    equity_points = _equity_curve(forward_returns)
    raw_returns = [
        (equity_points[i]["equity"] - equity_points[i - 1]["equity"]) / equity_points[i - 1]["equity"]
        for i in range(1, len(equity_points))
    ]
    drawdowns = _drawdown_curve(equity_points)
    gross_return = (equity_points[-1]["equity"] / equity_points[0]["equity"] - 1) if equity_points else 0.0
    turnover_cost = alpha["turnover"] * 0.0018
    slippage_cost = (0.0009 + idx * 0.00008) * (1 + alpha["turnover"] * 0.2)
    borrow_cost = 0.0004 if idx % 3 == 0 else 0.0001
    market_impact = min(0.006, alpha["turnover"] * 0.004)
    net_return = gross_return - turnover_cost - slippage_cost - borrow_cost - market_impact
    monthly = _monthly_returns(raw_returns)
    segments = _segment_returns(raw_returns)
    segment_passes = sum(1 for segment in segments if segment["return"] > 0)
    regimes = [
        {"regime": "normal", "days": 72, "contribution": round(sum(raw_returns[i] for i in range(0, min(len(raw_returns), 72)) if i % 5 != 0), 5), "hit_rate": 0.61},
        {"regime": "stress", "days": 24, "contribution": round(sum(raw_returns[i] for i in range(0, len(raw_returns), 5)), 5), "hit_rate": 0.46},
        {"regime": "recovery", "days": 23, "contribution": round(sum(raw_returns[i] for i in range(76, min(len(raw_returns), 99))), 5), "hit_rate": 0.68},
    ]
    trade_distribution = [
        {"bucket": "< -1%", "count": 2 + idx % 2, "avg_return": -0.014},
        {"bucket": "-1% to 0%", "count": 7 + idx, "avg_return": -0.004},
        {"bucket": "0% to 1%", "count": 13 + idx * 2, "avg_return": 0.004},
        {"bucket": "> 1%", "count": 5 + idx, "avg_return": 0.017},
    ]
    decision_drivers = [
        {"driver": "Information coefficient", "impact": "POSITIVE", "score": round(alpha["ic"], 4), "explanation": "Forward-return correlation remains positive across most segments"},
        {"driver": "Drawdown containment", "impact": "POSITIVE" if max(point["y"] for point in drawdowns) < 0.08 else "NEGATIVE", "score": round(max(point["y"] for point in drawdowns), 4), "explanation": "Worst underwater period remains inside research threshold"},
        {"driver": "Transaction cost drag", "impact": "NEGATIVE" if turnover_cost + slippage_cost > 0.002 else "NEUTRAL", "score": round(turnover_cost + slippage_cost, 5), "explanation": "Cost model subtracts turnover, slippage, borrow, and market impact"},
        {"driver": "Segment stability", "impact": "POSITIVE" if segment_passes >= 4 else "NEUTRAL", "score": round(segment_passes / max(len(segments), 1), 3), "explanation": "Positive return segments indicate robustness across replay windows"},
    ]
    return {
        "explainability_mode": "READ_ONLY_BACKTEST_EXPLAINABILITY",
        "selected_alpha_id": alpha["candidate_id"],
        "run": {
            "run_id": f"bt-{alpha['candidate_id']}-explain-001",
            "alpha_id": alpha["candidate_id"],
            "execution_mode": "L2_DRY_RUN_ONLY",
            "engine": "QUANTOS_DETERMINISTIC_REPLAY",
            "data_snapshot_id": f"md-snapshot-2026w{20 + idx % 4:02d}",
            "config_hash": _short_hash("backtest", alpha["candidate_id"], alpha["expression"], idx),
            "reproducibility_hash": _short_hash("backtest-explain", alpha["candidate_id"], gross_return, net_return),
        },
        "alpha": alpha,
        "summary": {
            "gross_return": round(gross_return, 5),
            "net_return": round(net_return, 5),
            "gross_sharpe": round(compute_sharpe(raw_returns) or 0.0, 3),
            "max_drawdown": round(max(point["y"] for point in drawdowns), 5),
            "win_rate": round(sum(1 for value in raw_returns if value > 0) / max(len(raw_returns), 1), 3),
            "turnover": alpha["turnover"],
            "cost_drag": round(turnover_cost + slippage_cost + borrow_cost + market_impact, 5),
            "segments_passed": segment_passes,
            "segments_total": len(segments),
        },
        "equity_curve": [{"x": point["x"], "y": point["equity"]} for point in equity_points],
        "drawdown_curve": [{"x": point["x"], "y": point["y"]} for point in drawdowns],
        "segments": segments,
        "monthly_returns": monthly,
        "cost_breakdown": [
            {"component": "turnover_cost", "value": round(turnover_cost, 5), "method": "turnover * 18bps"},
            {"component": "slippage_cost", "value": round(slippage_cost, 5), "method": "volume proxy + turnover adjustment"},
            {"component": "borrow_cost", "value": round(borrow_cost, 5), "method": "research borrow proxy"},
            {"component": "market_impact", "value": round(market_impact, 5), "method": "turnover capacity proxy"},
        ],
        "drawdown_periods": _drawdown_periods(drawdowns),
        "trade_distribution": trade_distribution,
        "regime_attribution": regimes,
        "decision_drivers": decision_drivers,
        "assumptions": [
            "Offline deterministic replay only",
            "No broker venue routing",
            "No live market connectivity",
            "Costs are research proxies, not broker-confirmed fees",
            "Past simulated performance is not a production approval",
        ],
        "explainability_policy": {
            "requires_equity_curve": True,
            "requires_cost_model": True,
            "requires_drawdown_periods": True,
            "requires_regime_attribution": True,
            "mutable_results": False,
        },
    }


def _scenario_stress_payload(alpha_id: str = "alpha-1") -> Dict:
    idx = _alpha_index(alpha_id)
    alpha = _computed_alpha(idx)
    base_returns = _forward_returns(_factor_values(idx), idx)[:120]
    base_equity = _equity_curve(base_returns)
    base_raw_returns = [
        (base_equity[i]["equity"] - base_equity[i - 1]["equity"]) / base_equity[i - 1]["equity"]
        for i in range(1, len(base_equity))
    ]
    base_total_return = base_equity[-1]["equity"] / base_equity[0]["equity"] - 1 if base_equity else 0.0
    base_max_drawdown = compute_max_drawdown([point["equity"] for point in base_equity]) or 0.0
    scenario_specs = [
        {
            "scenario_id": "SCN-VOL-2026-001",
            "name": "Volatility spike",
            "name_zh": "波动率冲击",
            "severity": "HIGH",
            "return_multiplier": 0.62,
            "daily_cost_shift": 0.00055,
            "liquidity_haircut": 0.12,
            "correlation_shift": 0.18,
            "shock_window": [28, 54],
        },
        {
            "scenario_id": "SCN-LIQ-2026-002",
            "name": "Liquidity freeze",
            "name_zh": "流动性冻结",
            "severity": "CRITICAL",
            "return_multiplier": 0.48,
            "daily_cost_shift": 0.00085,
            "liquidity_haircut": 0.28,
            "correlation_shift": 0.22,
            "shock_window": [44, 72],
        },
        {
            "scenario_id": "SCN-CROWD-2026-003",
            "name": "Crowded factor unwind",
            "name_zh": "拥挤因子反转",
            "severity": "HIGH",
            "return_multiplier": 0.42,
            "daily_cost_shift": 0.00035,
            "liquidity_haircut": 0.16,
            "correlation_shift": 0.36,
            "shock_window": [52, 83],
        },
        {
            "scenario_id": "SCN-COST-2026-004",
            "name": "Transaction cost widening",
            "name_zh": "交易成本扩大",
            "severity": "MEDIUM",
            "return_multiplier": 0.78,
            "daily_cost_shift": 0.00105,
            "liquidity_haircut": 0.08,
            "correlation_shift": 0.1,
            "shock_window": [0, 119],
        },
        {
            "scenario_id": "SCN-HALT-2026-005",
            "name": "Suspension / limit-down cluster",
            "name_zh": "停牌与跌停簇",
            "severity": "CRITICAL",
            "return_multiplier": 0.36,
            "daily_cost_shift": 0.00065,
            "liquidity_haircut": 0.34,
            "correlation_shift": 0.31,
            "shock_window": [60, 88],
        },
        {
            "scenario_id": "SCN-REGIME-2026-006",
            "name": "Regime break",
            "name_zh": "市场状态切换",
            "severity": "HIGH",
            "return_multiplier": 0.5,
            "daily_cost_shift": 0.00045,
            "liquidity_haircut": 0.18,
            "correlation_shift": 0.27,
            "shock_window": [18, 110],
        },
    ]
    scenarios = []
    for spec in scenario_specs:
        shock_start, shock_end = spec["shock_window"]
        stressed_returns = []
        for day, daily_return in enumerate(base_returns):
            in_window = shock_start <= day <= shock_end
            shock_multiplier = spec["return_multiplier"] if in_window else 0.92
            liquidity_drag = abs(daily_return) * spec["liquidity_haircut"] if in_window else 0.0
            stressed_returns.append(daily_return * shock_multiplier - spec["daily_cost_shift"] - liquidity_drag)
        stressed_equity = _equity_curve(stressed_returns)
        stressed_values = [point["equity"] for point in stressed_equity]
        stressed_total_return = stressed_values[-1] / stressed_values[0] - 1 if stressed_values else 0.0
        stressed_drawdown = compute_max_drawdown(stressed_values) or 0.0
        stressed_raw_returns = [
            (stressed_values[i] - stressed_values[i - 1]) / stressed_values[i - 1]
            for i in range(1, len(stressed_values))
        ]
        stressed_sharpe = compute_sharpe(stressed_raw_returns) or 0.0
        loss_delta = base_total_return - stressed_total_return
        liquidity_buffer_used = min(1.0, alpha["turnover"] * spec["liquidity_haircut"] * 2.1)
        if stressed_drawdown <= 0.08 and stressed_sharpe > 0.3:
            decision = "PASS"
        elif stressed_drawdown <= 0.16 and stressed_sharpe > -0.2:
            decision = "WATCH"
        else:
            decision = "FAIL"
        scenarios.append({
            "scenario_id": spec["scenario_id"],
            "name": spec["name"],
            "name_zh": spec["name_zh"],
            "severity": spec["severity"],
            "decision": decision,
            "shock_window": {"start_day": shock_start, "end_day": shock_end},
            "shocks": {
                "return_multiplier": spec["return_multiplier"],
                "daily_cost_shift": spec["daily_cost_shift"],
                "liquidity_haircut": spec["liquidity_haircut"],
                "correlation_shift": spec["correlation_shift"],
            },
            "metrics": {
                "stressed_return": round(stressed_total_return, 5),
                "loss_delta": round(loss_delta, 5),
                "stressed_sharpe": round(stressed_sharpe, 3),
                "stressed_max_drawdown": round(stressed_drawdown, 5),
                "liquidity_buffer_used": round(liquidity_buffer_used, 3),
                "capacity_after_haircut": round(alpha["capacity_estimate"] * (1 - spec["liquidity_haircut"]), 2),
            },
            "curve": [{"x": point["x"], "y": point["equity"]} for point in stressed_equity[::6]],
            "drivers": [
                {"driver": "return_shock", "impact": round(base_total_return * (1 - spec["return_multiplier"]), 5)},
                {"driver": "cost_shift", "impact": round(spec["daily_cost_shift"] * len(base_returns), 5)},
                {"driver": "liquidity_haircut", "impact": round(alpha["turnover"] * spec["liquidity_haircut"], 5)},
                {"driver": "correlation_shift", "impact": round(alpha["correlation_penalty"] + spec["correlation_shift"], 5)},
            ],
        })

    worst = max(scenarios, key=lambda scenario: scenario["metrics"]["loss_delta"])
    fails = sum(1 for scenario in scenarios if scenario["decision"] == "FAIL")
    watches = sum(1 for scenario in scenarios if scenario["decision"] == "WATCH")
    exposure_buckets = [
        {"factor": "market_beta", "exposure": 0.22 + idx * 0.01, "shock": -0.08, "estimated_pnl": -0.017 - idx * 0.001},
        {"factor": "liquidity", "exposure": alpha["turnover"], "shock": -0.18, "estimated_pnl": round(-alpha["turnover"] * 0.022, 5)},
        {"factor": "crowding", "exposure": alpha["correlation_penalty"], "shock": -0.24, "estimated_pnl": round(-alpha["correlation_penalty"] * 0.045, 5)},
        {"factor": "cost_model", "exposure": 0.18, "shock": -0.12, "estimated_pnl": -0.0064},
    ]
    return {
        "stress_mode": "READ_ONLY_SCENARIO_STRESS",
        "selected_alpha_id": alpha["candidate_id"],
        "run": {
            "run_id": f"stress-{alpha['candidate_id']}-001",
            "execution_mode": "L2_DRY_RUN_ONLY",
            "engine": "QUANTOS_SCENARIO_REPLAY",
            "scenario_set_id": "scenario-library-2026w24",
            "data_snapshot_id": f"md-snapshot-2026w{20 + idx % 4:02d}",
            "config_hash": _short_hash("scenario-stress", alpha["candidate_id"], alpha["expression"], len(scenarios)),
            "reproducibility_hash": _short_hash("scenario-stress", alpha["candidate_id"], worst["scenario_id"], worst["metrics"]["loss_delta"]),
        },
        "alpha": alpha,
        "baseline": {
            "total_return": round(base_total_return, 5),
            "sharpe": round(compute_sharpe(base_raw_returns) or 0.0, 3),
            "max_drawdown": round(base_max_drawdown, 5),
            "turnover": alpha["turnover"],
            "capacity_estimate": alpha["capacity_estimate"],
        },
        "summary": {
            "scenarios": len(scenarios),
            "passes": sum(1 for scenario in scenarios if scenario["decision"] == "PASS"),
            "watches": watches,
            "fails": fails,
            "worst_scenario_id": worst["scenario_id"],
            "worst_loss_delta": worst["metrics"]["loss_delta"],
            "average_stressed_drawdown": round(statistics.mean(scenario["metrics"]["stressed_max_drawdown"] for scenario in scenarios), 5),
            "average_liquidity_buffer_used": round(statistics.mean(scenario["metrics"]["liquidity_buffer_used"] for scenario in scenarios), 3),
        },
        "scenarios": scenarios,
        "exposure_buckets": exposure_buckets,
        "risk_factor_contributions": sorted(exposure_buckets, key=lambda item: abs(item["estimated_pnl"]), reverse=True),
        "scenario_policy": {
            "requires_multi_scenario_replay": True,
            "requires_liquidity_haircut": True,
            "requires_cost_widening": True,
            "requires_correlation_shift": True,
            "mutable_results": False,
            "authorizes_live_trading": False,
        },
        "assumptions": [
            "Research-only deterministic scenario replay",
            "No live market connectivity",
            "No order routing or broker integration",
            "Haircuts are conservative research proxies",
            "Stress pass is not production approval",
        ],
    }


def _model_risk_review_payload() -> Dict:
    experiments = _experiments_payload()["experiments"]
    data_catalog = _data_catalog_payload()
    stress_payload = _scenario_stress_payload("alpha-1")
    backtest_payload = _backtest_explainability_payload("alpha-1")
    models = []
    model_specs = [
        ("MRM-ALPHA-001", "Alpha factor alpha-1", "ALPHA_MODEL", "alpha-1", "HIGH", "Factor Hunter", "VALIDATION_REQUIRED"),
        ("MRM-ALPHA-002", "Alpha factor alpha-2", "ALPHA_MODEL", "alpha-2", "MEDIUM", "Macro Analyst", "VALIDATION_REQUIRED"),
        ("MRM-ALPHA-003", "Alpha factor alpha-3", "ALPHA_MODEL", "alpha-3", "HIGH", "Momentum Specialist", "RESTRICTED_USE"),
        ("MRM-PORT-001", "Portfolio research weighting", "PORTFOLIO_MODEL", "portfolio-research", "HIGH", "Portfolio Research", "VALIDATION_REQUIRED"),
        ("MRM-RISK-001", "Research risk gate", "RISK_MODEL", "risk-gate", "CRITICAL", "Risk", "INDEPENDENT_REVIEW_REQUIRED"),
        ("MRM-STRESS-001", "Scenario stress replay", "STRESS_MODEL", "scenario-stress", "HIGH", "Risk", "VALIDATION_REQUIRED"),
        ("MRM-DATA-001", "Point-in-time feature generation", "DATA_MODEL", "feature-store", "HIGH", "Research Platform", "VALIDATION_REQUIRED"),
    ]
    for idx, (model_id, name, model_type, linked_component, tier, owner, status) in enumerate(model_specs):
        conceptual = 0.76 - idx * 0.025
        implementation = 0.82 - idx * 0.018
        outcomes = 0.68 - idx * 0.02
        monitoring = 0.63 - idx * 0.015
        stress_failure_penalty = 0.08 if linked_component in {"risk-gate", "scenario-stress"} and stress_payload["summary"]["fails"] > 0 else 0.0
        residual_risk = max(0.05, min(0.95, 1.0 - statistics.mean([conceptual, implementation, outcomes, monitoring]) + stress_failure_penalty))
        findings = []
        if status != "APPROVED_FOR_RESEARCH":
            findings.append("Independent validation evidence incomplete")
        if linked_component in {"feature-store", "alpha-3"}:
            findings.append("Point-in-time and data lineage require deeper sample audit")
        if linked_component in {"risk-gate", "scenario-stress"}:
            findings.append("Stress calibration requires independent challenge")
        if model_type == "ALPHA_MODEL" and idx < len(experiments):
            experiment = experiments[idx]
            if experiment["decision"] != "APPROVED":
                findings.append(f"Linked experiment decision is {experiment['decision']}")
        models.append({
            "model_id": model_id,
            "name": name,
            "model_type": model_type,
            "linked_component": linked_component,
            "owner": owner,
            "risk_tier": tier,
            "review_status": status,
            "intended_use": "Research ranking, validation, portfolio simulation, and risk review only",
            "prohibited_use": "Live trading authorization, broker routing, credential handling, or production order decisions",
            "validation_scores": {
                "conceptual_soundness": round(conceptual, 3),
                "implementation_verification": round(implementation, 3),
                "outcomes_analysis": round(outcomes, 3),
                "ongoing_monitoring": round(monitoring, 3),
            },
            "residual_risk": round(residual_risk, 3),
            "evidence": {
                "experiment_id": experiments[idx]["experiment_id"] if idx < len(experiments) else None,
                "config_hash": experiments[idx]["config"]["config_hash"] if idx < len(experiments) else backtest_payload["run"]["config_hash"],
                "data_snapshot_id": experiments[idx]["config"]["data_snapshot_id"] if idx < len(experiments) else stress_payload["run"]["data_snapshot_id"],
                "stress_run_id": stress_payload["run"]["run_id"] if linked_component in {"risk-gate", "scenario-stress"} else None,
                "backtest_run_id": backtest_payload["run"]["run_id"] if model_type in {"ALPHA_MODEL", "PORTFOLIO_MODEL"} else None,
            },
            "limitations": [
                "Offline deterministic replay, not broker-confirmed production behavior",
                "Proxy costs and haircuts require independent calibration",
                "Research feature catalog is snapshot-based and not a persisted governed store",
            ],
            "findings": findings,
            "compensating_controls": [
                "Read-only console",
                "L2 dry-run execution boundary",
                "Immutable reproducibility hashes",
                "Phase 3A NO_GO gate blocks broker test work",
            ],
        })

    validation_dimensions = [
        {
            "dimension": "Conceptual soundness",
            "dimension_zh": "概念合理性",
            "score": round(statistics.mean(model["validation_scores"]["conceptual_soundness"] for model in models), 3),
            "status": "NEEDS_REVIEW",
            "evidence": "Alpha formulas, feature lineage, cost assumptions, and stress scenario definitions",
        },
        {
            "dimension": "Implementation verification",
            "dimension_zh": "实现核验",
            "score": round(statistics.mean(model["validation_scores"]["implementation_verification"] for model in models), 3),
            "status": "PARTIAL",
            "evidence": "Console API contract tests, TypeScript contracts, dry-run integration gate",
        },
        {
            "dimension": "Outcomes analysis",
            "dimension_zh": "结果分析",
            "score": round(statistics.mean(model["validation_scores"]["outcomes_analysis"] for model in models), 3),
            "status": "NEEDS_REVIEW",
            "evidence": "Backtest explainability, segment stability, drawdown periods, monthly returns",
        },
        {
            "dimension": "Ongoing monitoring",
            "dimension_zh": "持续监控",
            "score": round(statistics.mean(model["validation_scores"]["ongoing_monitoring"] for model in models), 3),
            "status": "MISSING_PERSISTENCE",
            "evidence": "System health snapshots exist; persistent drift monitoring is not implemented",
        },
    ]
    issue_register = [
        {"issue_id": "MRM-I01", "severity": "HIGH", "status": "OPEN", "owner": "Risk", "description": "Independent model validation workflow is not persisted", "linked_models": ["MRM-RISK-001", "MRM-STRESS-001"]},
        {"issue_id": "MRM-I02", "severity": "HIGH", "status": "OPEN", "owner": "Research Platform", "description": "Feature store is read-only snapshot, not governed persisted infrastructure", "linked_models": ["MRM-DATA-001"]},
        {"issue_id": "MRM-I03", "severity": "MEDIUM", "status": "OPEN", "owner": "Alpha Research", "description": "Alpha limitations and intended-use approvals need reviewer signoff", "linked_models": ["MRM-ALPHA-001", "MRM-ALPHA-002", "MRM-ALPHA-003"]},
        {"issue_id": "MRM-I04", "severity": "MEDIUM", "status": "MITIGATED_BY_GATE", "owner": "Governance", "description": "Live trading authorization is explicitly blocked by Phase 3A NO_GO", "linked_models": ["MRM-PORT-001", "MRM-RISK-001"]},
    ]
    high_risk_models = sum(1 for model in models if model["risk_tier"] in {"HIGH", "CRITICAL"})
    open_high_issues = sum(1 for issue in issue_register if issue["severity"] == "HIGH" and issue["status"] == "OPEN")
    avg_residual_risk = round(statistics.mean(model["residual_risk"] for model in models), 3)
    return {
        "model_risk_mode": "READ_ONLY_MODEL_RISK_REVIEW",
        "review_standard": "PRINCIPLES_BASED_MODEL_RISK_MANAGEMENT",
        "summary": {
            "models": len(models),
            "high_risk_models": high_risk_models,
            "open_issues": sum(1 for issue in issue_register if issue["status"] == "OPEN"),
            "open_high_issues": open_high_issues,
            "avg_residual_risk": avg_residual_risk,
            "independent_validation_complete": False,
            "live_use_authorized": False,
        },
        "models": models,
        "validation_dimensions": validation_dimensions,
        "issue_register": issue_register,
        "evidence_sources": [
            {"source_id": "EXP_REGISTRY", "name": "Experiment Registry", "records": len(experiments), "read_only": True},
            {"source_id": "DATA_CATALOG", "name": "Data Catalog / Feature Store", "records": data_catalog["summary"]["features"], "read_only": True},
            {"source_id": "BACKTEST_EXPLAIN", "name": "Backtest Explainability", "records": len(backtest_payload["segments"]), "read_only": True},
            {"source_id": "SCENARIO_STRESS", "name": "Scenario Stress Library", "records": stress_payload["summary"]["scenarios"], "read_only": True},
        ],
        "governance_policy": {
            "requires_independent_validation": True,
            "requires_intended_use_statement": True,
            "requires_limitations_disclosure": True,
            "requires_issue_tracking": True,
            "requires_ongoing_monitoring": True,
            "mutable_reviews": False,
            "authorizes_live_trading": False,
        },
    }


def _evidence_ledger_payload() -> Dict:
    experiments_payload = _experiments_payload()
    data_catalog_payload = _data_catalog_payload()
    backtest_payload = _backtest_explainability_payload("alpha-1")
    stress_payload = _scenario_stress_payload("alpha-1")
    model_risk_payload = _model_risk_review_payload()
    architecture_payload = _architecture_payload()
    phase3a_payload = _phase3a_readiness_payload()["readiness"]

    evidence_specs = [
        {
            "evidence_id": "EV-EXP-REGISTRY-001",
            "source_module": "Experiment Registry",
            "source_endpoint": "/api/v1/console/experiments",
            "artifact_type": "EXPERIMENT_REGISTRY",
            "record_count": experiments_payload["summary"]["total_experiments"],
            "status": "SEALED",
            "owner": "Alpha Research",
            "content_hash": _short_hash("experiments", experiments_payload["summary"], experiments_payload["stage_counts"]),
            "blocks_live_authorization": False,
        },
        {
            "evidence_id": "EV-DATA-CATALOG-001",
            "source_module": "Data Catalog / Feature Store",
            "source_endpoint": "/api/v1/console/data-catalog",
            "artifact_type": "DATA_LINEAGE",
            "record_count": data_catalog_payload["summary"]["features"],
            "status": "SEALED",
            "owner": "Research Platform",
            "content_hash": _short_hash("data-catalog", data_catalog_payload["summary"], data_catalog_payload["quality_policy"]),
            "blocks_live_authorization": False,
        },
        {
            "evidence_id": "EV-BACKTEST-EXPLAIN-001",
            "source_module": "Backtest Explainability",
            "source_endpoint": "/api/v1/console/backtest-explainability",
            "artifact_type": "BACKTEST_TEAR_SHEET",
            "record_count": len(backtest_payload["segments"]),
            "status": "SEALED",
            "owner": "Alpha Research",
            "content_hash": backtest_payload["run"]["reproducibility_hash"],
            "blocks_live_authorization": False,
        },
        {
            "evidence_id": "EV-SCENARIO-STRESS-001",
            "source_module": "Scenario Stress Library",
            "source_endpoint": "/api/v1/console/scenario-stress",
            "artifact_type": "STRESS_REPLAY",
            "record_count": stress_payload["summary"]["scenarios"],
            "status": "SEALED",
            "owner": "Risk",
            "content_hash": stress_payload["run"]["reproducibility_hash"],
            "blocks_live_authorization": stress_payload["summary"]["fails"] > 0,
        },
        {
            "evidence_id": "EV-MODEL-RISK-001",
            "source_module": "Model Risk Review",
            "source_endpoint": "/api/v1/console/model-risk-review",
            "artifact_type": "MODEL_RISK_REVIEW",
            "record_count": model_risk_payload["summary"]["models"],
            "status": "SEALED",
            "owner": "Model Governance",
            "content_hash": _short_hash("model-risk", model_risk_payload["summary"], model_risk_payload["issue_register"]),
            "blocks_live_authorization": not model_risk_payload["summary"]["independent_validation_complete"],
        },
        {
            "evidence_id": "EV-ARCHITECTURE-001",
            "source_module": "System Architecture",
            "source_endpoint": "/api/v1/console/architecture",
            "artifact_type": "ARCHITECTURE_SNAPSHOT",
            "record_count": len(architecture_payload["modules"]),
            "status": "SEALED",
            "owner": "Research Platform",
            "content_hash": _short_hash("architecture", architecture_payload["architecture_score"], architecture_payload["gaps"]),
            "blocks_live_authorization": architecture_payload["phase3a_decision"] == "NO_GO",
        },
        {
            "evidence_id": "EV-PHASE3A-001",
            "source_module": "Phase 3A Governance",
            "source_endpoint": "/api/v1/console/phase-3a-readiness",
            "artifact_type": "GO_NO_GO_EVIDENCE",
            "record_count": phase3a_payload["total_count"],
            "status": "SEALED",
            "owner": "Governance",
            "content_hash": _short_hash("phase3a", phase3a_payload["decision"], phase3a_payload["blockers"]),
            "blocks_live_authorization": phase3a_payload["decision"] == "NO_GO",
        },
    ]

    entries = []
    previous_hash = "GENESIS"
    base_timestamp = 1_781_222_400
    for idx, spec in enumerate(evidence_specs):
        ledger_hash = _short_hash(
            spec["evidence_id"],
            spec["source_module"],
            spec["artifact_type"],
            spec["content_hash"],
            previous_hash,
            spec["record_count"],
        )
        entries.append({
            **spec,
            "sequence": idx + 1,
            "sealed_at": base_timestamp + idx * 300,
            "previous_hash": previous_hash,
            "ledger_hash": ledger_hash,
            "immutability_status": "IMMUTABLE_RESEARCH_SNAPSHOT",
            "read_only": True,
            "execution_mode": "L2_DRY_RUN_ONLY",
            "verification": {
                "hash_chain_valid": True,
                "content_hash_present": bool(spec["content_hash"]),
                "source_read_only": True,
                "no_live_authorization": True,
            },
        })
        previous_hash = ledger_hash

    # REAL integrity: recompute the whole hash chain from GENESIS and compare to the stored ledger hashes
    # (was a hardcoded True). A tampered entry/content/order now flips this to False.
    chain_valid, _prev = True, "GENESIS"
    for entry in entries:
        expect = _short_hash(entry["evidence_id"], entry["source_module"], entry["artifact_type"],
                             entry["content_hash"], _prev, entry["record_count"])
        if expect != entry["ledger_hash"]:
            chain_valid = False
        _prev = entry["ledger_hash"]
    for entry in entries:
        entry["verification"]["hash_chain_valid"] = chain_valid

    blockers = [entry for entry in entries if entry["blocks_live_authorization"]]
    return {
        "ledger_mode": "READ_ONLY_IMMUTABLE_EVIDENCE_LEDGER",
        "data_provenance": "EXPERIMENT_REGISTRY" if _load_experiment_store() else "SYNTHETIC_DETERMINISTIC",
        "chain_id": "quantos-research-ledger-2026w24",
        "root_hash": entries[-1]["ledger_hash"] if entries else "GENESIS",
        "summary": {
            "entries": len(entries),
            "sealed_entries": sum(1 for entry in entries if entry["status"] == "SEALED"),
            "blocking_entries": len(blockers),
            "hash_chain_valid": all(entry["verification"]["hash_chain_valid"] for entry in entries),
            "read_only_entries": sum(1 for entry in entries if entry["read_only"]),
            "live_authorization_allowed": False,
        },
        "entries": entries,
        "blockers": [
            {
                "evidence_id": entry["evidence_id"],
                "source_module": entry["source_module"],
                "reason": "Evidence blocks live authorization under current governance state",
                "ledger_hash": entry["ledger_hash"],
            }
            for entry in blockers
        ],
        "ledger_policy": {
            "append_only": True,
            "requires_previous_hash": True,
            "requires_content_hash": True,
            "requires_source_endpoint": True,
            "requires_read_only_source": True,
            "mutable_entries": False,
            "authorizes_live_trading": False,
        },
    }


def _reviewer_workflow_payload() -> Dict:
    model_risk = _model_risk_review_payload()
    ledger = _evidence_ledger_payload()
    phase3a = _phase3a_readiness_payload()["readiness"]
    reviewers = [
        {"reviewer_id": "rev-risk-001", "name": "Risk Reviewer", "role": "Independent Risk", "required": True, "capacity": 7, "open_tasks": 3},
        {"reviewer_id": "rev-model-001", "name": "Model Validator", "role": "Model Validation", "required": True, "capacity": 6, "open_tasks": 4},
        {"reviewer_id": "rev-data-001", "name": "Data Steward", "role": "Data Governance", "required": True, "capacity": 5, "open_tasks": 2},
        {"reviewer_id": "rev-compliance-001", "name": "Compliance Reviewer", "role": "Compliance", "required": True, "capacity": 4, "open_tasks": 3},
        {"reviewer_id": "rev-platform-001", "name": "Platform Reviewer", "role": "Research Platform", "required": False, "capacity": 8, "open_tasks": 2},
    ]
    task_specs = [
        ("RW-001", "Validate model risk issue register", "MRM-I01", "rev-model-001", "HIGH", "BLOCKING", 42, "MODEL_RISK_REVIEW"),
        ("RW-002", "Challenge stress scenario calibration", "EV-SCENARIO-STRESS-001", "rev-risk-001", "HIGH", "BLOCKING", 36, "SCENARIO_STRESS"),
        ("RW-003", "Review point-in-time feature lineage", "MRM-I02", "rev-data-001", "HIGH", "BLOCKING", 28, "DATA_CATALOG"),
        ("RW-004", "Confirm Phase 3A governance NO_GO blockers", "EV-PHASE3A-001", "rev-compliance-001", "CRITICAL", "BLOCKING", 54, "PHASE3A"),
        ("RW-005", "Review alpha limitation disclosures", "MRM-I03", "rev-model-001", "MEDIUM", "NON_BLOCKING", 18, "EXPERIMENT_REGISTRY"),
        ("RW-006", "Verify evidence ledger hash chain", ledger["root_hash"], "rev-platform-001", "MEDIUM", "NON_BLOCKING", 9, "EVIDENCE_LEDGER"),
        ("RW-007", "Check dry-run safety evidence", "EV-ARCHITECTURE-001", "rev-risk-001", "HIGH", "BLOCKING", 21, "ARCHITECTURE"),
        ("RW-008", "Review compliance evidence checklist", "E03", "rev-compliance-001", "HIGH", "BLOCKING", 63, "PHASE3A"),
    ]
    tasks = []
    for idx, (task_id, title, evidence_ref, reviewer_id, severity, gate_type, age_hours, source_area) in enumerate(task_specs):
        reviewer = next(item for item in reviewers if item["reviewer_id"] == reviewer_id)
        sla_hours = 48 if severity == "CRITICAL" else 36 if severity == "HIGH" else 72
        if age_hours > sla_hours:
            state = "OVERDUE"
        elif gate_type == "BLOCKING":
            state = "PENDING_REVIEW"
        else:
            state = "QUEUED"
        tasks.append({
            "task_id": task_id,
            "title": title,
            "evidence_ref": evidence_ref,
            "source_area": source_area,
            "assigned_reviewer_id": reviewer_id,
            "assigned_reviewer": reviewer["name"],
            "role": reviewer["role"],
            "severity": severity,
            "gate_type": gate_type,
            "state": state,
            "age_hours": age_hours,
            "sla_hours": sla_hours,
            "due_in_hours": sla_hours - age_hours,
            "blocks_live_authorization": gate_type == "BLOCKING",
            "requires_independent_signoff": reviewer["required"],
            "task_hash": _short_hash(task_id, evidence_ref, reviewer_id, severity, gate_type),
            "created_at": 1_781_222_400 + idx * 600,
        })
    approval_gates = [
        {"gate_id": "GATE-INDEPENDENT-VALIDATION", "name": "Independent validation complete", "status": "BLOCKED", "required_tasks": ["RW-001", "RW-005"], "owner_role": "Model Validation"},
        {"gate_id": "GATE-STRESS-CHALLENGE", "name": "Stress challenge complete", "status": "BLOCKED", "required_tasks": ["RW-002"], "owner_role": "Independent Risk"},
        {"gate_id": "GATE-DATA-LINEAGE", "name": "Point-in-time lineage reviewed", "status": "BLOCKED", "required_tasks": ["RW-003"], "owner_role": "Data Governance"},
        {"gate_id": "GATE-PHASE3A", "name": "Phase 3A blockers closed", "status": "BLOCKED", "required_tasks": ["RW-004", "RW-008"], "owner_role": "Compliance"},
        {"gate_id": "GATE-EVIDENCE-INTEGRITY", "name": "Evidence hash chain verified", "status": "PENDING", "required_tasks": ["RW-006", "RW-007"], "owner_role": "Research Platform"},
    ]
    blocking_tasks = [task for task in tasks if task["blocks_live_authorization"]]
    overdue_tasks = [task for task in tasks if task["state"] == "OVERDUE"]
    workload = [
        {
            **reviewer,
            "utilization": round(reviewer["open_tasks"] / max(reviewer["capacity"], 1), 3),
            "required_open_blocking": sum(1 for task in blocking_tasks if task["assigned_reviewer_id"] == reviewer["reviewer_id"]),
        }
        for reviewer in reviewers
    ]
    return {
        "workflow_mode": "READ_ONLY_REVIEWER_WORKFLOW",
        "workflow_id": "reviewer-workflow-2026w24",
        "summary": {
            "tasks": len(tasks),
            "blocking_tasks": len(blocking_tasks),
            "overdue_tasks": len(overdue_tasks),
            "reviewers": len(reviewers),
            "approval_gates": len(approval_gates),
            "blocked_gates": sum(1 for gate in approval_gates if gate["status"] == "BLOCKED"),
            "independent_validation_complete": model_risk["summary"]["independent_validation_complete"],
            "phase3a_decision": phase3a["decision"],
            "live_authorization_allowed": False,
        },
        "reviewers": reviewers,
        "tasks": tasks,
        "approval_gates": approval_gates,
        "workload": workload,
        "evidence_links": [
            {"source": "Evidence Ledger", "ref": ledger["root_hash"], "records": ledger["summary"]["entries"], "blocking": ledger["summary"]["blocking_entries"]},
            {"source": "Model Risk Review", "ref": model_risk["model_risk_mode"], "records": model_risk["summary"]["models"], "blocking": model_risk["summary"]["open_high_issues"]},
            {"source": "Phase 3A", "ref": phase3a["decision"], "records": phase3a["total_count"], "blocking": len(phase3a["blockers"])},
        ],
        "workflow_policy": {
            "read_only": True,
            "requires_independent_reviewer": True,
            "requires_blocking_task_closure": True,
            "requires_phase3a_go": True,
            "requires_signed_attestation": True,
            "mutable_tasks": False,
            "authorizes_live_trading": False,
        },
    }


def _access_control_payload() -> Dict:
    ledger = _evidence_ledger_payload()
    workflow = _reviewer_workflow_payload()
    phase3a = _phase3a_readiness_payload()["readiness"]
    roles = [
        {
            "role_id": "role-research-viewer",
            "name": "Research Viewer",
            "name_zh": "研究只读",
            "scope": "READ_ONLY_DASHBOARDS",
            "users": 6,
            "permissions": ["read:overview", "read:alpha", "read:experiments", "read:backtest", "read:stress"],
            "denied_capabilities": ["write:any", "approve:any", "export:signed", "trade:any", "credential:any"],
        },
        {
            "role_id": "role-risk-reviewer",
            "name": "Risk Reviewer",
            "name_zh": "风险复核",
            "scope": "READ_ONLY_RISK_REVIEW",
            "users": 3,
            "permissions": ["read:risk", "read:stress", "read:model-risk", "read:evidence-ledger", "review:metadata"],
            "denied_capabilities": ["write:risk-policy", "approve:live", "trade:any", "credential:any"],
        },
        {
            "role_id": "role-model-validator",
            "name": "Model Validator",
            "name_zh": "模型验证",
            "scope": "READ_ONLY_MODEL_GOVERNANCE",
            "users": 2,
            "permissions": ["read:model-risk", "read:reviewer-workflow", "read:evidence-ledger", "review:metadata"],
            "denied_capabilities": ["write:model", "approve:production", "trade:any", "credential:any"],
        },
        {
            "role_id": "role-compliance",
            "name": "Compliance Reviewer",
            "name_zh": "合规复核",
            "scope": "READ_ONLY_GOVERNANCE",
            "users": 2,
            "permissions": ["read:phase3a", "read:evidence-ledger", "read:reviewer-workflow", "read:audit-export"],
            "denied_capabilities": ["write:phase3a", "approve:broker", "trade:any", "credential:any"],
        },
        {
            "role_id": "role-platform-admin-readonly",
            "name": "Platform Admin Read-only",
            "name_zh": "平台只读管理员",
            "scope": "READ_ONLY_PLATFORM",
            "users": 1,
            "permissions": ["read:system", "read:architecture", "read:evidence-ledger", "read:audit-export"],
            "denied_capabilities": ["write:runtime", "deploy:production", "trade:any", "credential:any"],
        },
    ]
    permission_matrix = []
    permission_catalog = [
        "read:overview",
        "read:alpha",
        "read:experiments",
        "read:data-catalog",
        "read:backtest",
        "read:stress",
        "read:model-risk",
        "read:evidence-ledger",
        "read:reviewer-workflow",
        "read:audit-export",
        "review:metadata",
        "approve:live",
        "trade:any",
        "credential:any",
    ]
    for role in roles:
        for permission in permission_catalog:
            allowed = permission in role["permissions"]
            if permission in {"approve:live", "trade:any", "credential:any"}:
                allowed = False
            permission_matrix.append({
                "role_id": role["role_id"],
                "permission": permission,
                "allowed": allowed,
                "reason": "Forbidden by L2 dry-run safety boundary" if not allowed and permission in {"approve:live", "trade:any", "credential:any"} else "Role scoped read-only permission",
            })
    export_packages = [
        {
            "package_id": "EXP-AUDIT-2026W24-001",
            "name": "Model risk evidence pack",
            "name_zh": "模型风险证据包",
            "status": "READY_FOR_REVIEW",
            "records": ledger["summary"]["entries"],
            "source_root_hash": ledger["root_hash"],
            "requested_by_role": "role-compliance",
            "export_mode": "METADATA_ONLY_READ_ONLY",
            "content_hash": _short_hash("export", "model-risk", ledger["root_hash"], workflow["summary"]),
            "contains_credentials": False,
            "contains_live_orders": False,
            "download_enabled": False,
        },
        {
            "package_id": "EXP-AUDIT-2026W24-002",
            "name": "Phase 3A blocker pack",
            "name_zh": "Phase 3A 阻断证据包",
            "status": "BLOCKED_BY_NO_GO",
            "records": len(phase3a["blockers"]),
            "source_root_hash": ledger["root_hash"],
            "requested_by_role": "role-compliance",
            "export_mode": "METADATA_ONLY_READ_ONLY",
            "content_hash": _short_hash("export", "phase3a", phase3a["decision"], phase3a["blockers"]),
            "contains_credentials": False,
            "contains_live_orders": False,
            "download_enabled": False,
        },
        {
            "package_id": "EXP-AUDIT-2026W24-003",
            "name": "Research reproducibility pack",
            "name_zh": "研究可复现证据包",
            "status": "READY_FOR_REVIEW",
            "records": ledger["summary"]["sealed_entries"],
            "source_root_hash": ledger["root_hash"],
            "requested_by_role": "role-model-validator",
            "export_mode": "METADATA_ONLY_READ_ONLY",
            "content_hash": _short_hash("export", "reproducibility", ledger["chain_id"], ledger["root_hash"]),
            "contains_credentials": False,
            "contains_live_orders": False,
            "download_enabled": False,
        },
    ]
    access_reviews = [
        {"review_id": "AR-001", "role_id": "role-risk-reviewer", "status": "PENDING_ATTESTATION", "owner": "Security Officer", "age_hours": 18, "finding": "Review risk reviewer membership"},
        {"review_id": "AR-002", "role_id": "role-model-validator", "status": "PENDING_ATTESTATION", "owner": "Model Governance", "age_hours": 26, "finding": "Confirm independent validator separation"},
        {"review_id": "AR-003", "role_id": "role-compliance", "status": "BLOCKED_BY_PHASE3A_NO_GO", "owner": "Compliance", "age_hours": 42, "finding": "Cannot approve broker-facing access while Phase 3A is NO_GO"},
        {"review_id": "AR-004", "role_id": "role-platform-admin-readonly", "status": "APPROVED_READ_ONLY", "owner": "Research Platform", "age_hours": 9, "finding": "Read-only platform scope confirmed"},
    ]
    forbidden_capabilities = [
        {"capability": "trade:any", "status": "DENIED", "reason": "L2_DRY_RUN_ONLY boundary"},
        {"capability": "broker:connect", "status": "DENIED", "reason": "Phase 3A NO_GO"},
        {"capability": "credential:read", "status": "DENIED", "reason": "No credentials in research console"},
        {"capability": "export:signed-download", "status": "DENIED", "reason": "Signed export workflow not implemented"},
        {"capability": "permission:write", "status": "DENIED", "reason": "RBAC snapshot is read-only"},
    ]
    allowed_read_permissions = sum(1 for item in permission_matrix if item["allowed"])
    denied_dangerous_permissions = sum(1 for item in permission_matrix if not item["allowed"] and item["permission"] in {"approve:live", "trade:any", "credential:any"})
    return {
        "access_mode": "READ_ONLY_RBAC_AUDIT_EXPORT",
        "summary": {
            "roles": len(roles),
            "users": sum(role["users"] for role in roles),
            "permission_rows": len(permission_matrix),
            "allowed_read_permissions": allowed_read_permissions,
            "denied_dangerous_permissions": denied_dangerous_permissions,
            "export_packages": len(export_packages),
            "access_reviews": len(access_reviews),
            "live_authorization_allowed": False,
        },
        "roles": roles,
        "permission_matrix": permission_matrix,
        "export_packages": export_packages,
        "access_reviews": access_reviews,
        "forbidden_capabilities": forbidden_capabilities,
        "access_policy": {
            "read_only_console": True,
            "least_privilege": True,
            "segregation_of_duties_required": True,
            "signed_export_required": True,
            "mutable_permissions": False,
            "downloads_enabled": False,
            "authorizes_live_trading": False,
        },
    }


def _monitoring_drift_payload() -> Dict:
    candidates = _alpha_candidates()
    data_catalog = _data_catalog_payload()
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
    _REC_BAND = {"OK": (0.02, 0.16), "DEGRADED": (_WARN, 0.30),
                 "PAUSED": (0.34, 0.48), "RETIRED": (0.50, 0.62)}
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
        health = monitor_alpha_health(backtest, live)          # REAL degradation-monitor engine
        rec = health["recommendation"]
        status = _REC_TO_STATUS.get(rec, "OK")
        # measured IC drift → 0..1 wiggle, then clamp into the verdict's band (coherent with status)
        ic_drift = abs(live_ic - baseline) / max(abs(baseline), 0.01)
        lo, hi = _REC_BAND[rec]
        drift_score = round(lo + (hi - lo) * min(1.0, ic_drift / 0.5), 3)
        remediation = (f"[degradation_monitor:{rec}] {health['alerts'][0]['detail']}"
                       if health["alerts"] else "Continue monitoring")
        monitors.append({
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
        })

    for idx, feature in enumerate(data_catalog["features"][:8]):
        baseline = feature["quality_score"]
        current = round(max(0.0, baseline - 0.012 * (idx % 4)), 3)
        drift_score = round(max(0.0, baseline - current), 3)
        status = status_from_score(drift_score, 0.02, 0.04)
        monitors.append({
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
            "remediation": "Run feature distribution audit and point-in-time sample check" if status != "OK" else "Continue monitoring",
        })

    # Cap DATA_FRESHNESS to the staleest few so the page stays readable when the catalog enumerates many
    # real datasets (crypto_daily = 42); rank by freshness so any actually-stale dataset still surfaces.
    for idx, dataset in enumerate(sorted(data_catalog["datasets"],
                                         key=lambda d: d["freshness_hours"], reverse=True)[:8]):
        current = dataset["freshness_hours"]
        drift_score = round(current / 24.0, 3)
        status = "BREACH" if current > 24 else "WATCH" if current > 12 else "OK"
        monitors.append({
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
            "remediation": "Refresh source snapshot and rerun data quality checks" if status != "OK" else "Continue monitoring",
        })

    for idx, issue in enumerate(model_risk["issue_register"]):
        age_hours = 36 + idx * 24
        status = "BREACH" if issue["severity"] == "HIGH" and age_hours >= 72 else "WATCH" if issue["status"] == "OPEN" else "OK"
        monitors.append({
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
            "remediation": "Escalate model risk issue owner and update reviewer workflow" if status != "OK" else "Continue monitoring",
        })

    for idx, task in enumerate(reviewer_workflow["tasks"]):
        overdue_hours = max(0, -task["due_in_hours"])
        status = "BREACH" if overdue_hours > 0 else "WATCH" if task["blocks_live_authorization"] else "OK"
        monitors.append({
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
            "remediation": "Escalate overdue reviewer task" if status == "BREACH" else "Track blocking task closure",
        })

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
        },
    }


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
    incidents = []
    timeline = []
    for idx, monitor in enumerate(source_monitors):
        rule = route_by_category[monitor["category"]]
        if monitor["status"] == "BREACH" and monitor["drift_score"] >= 0.5:
            severity = "CRITICAL"
        elif monitor["status"] == "BREACH":
            severity = "HIGH"
        else:
            severity = "MEDIUM"
        age_minutes = 45 + idx * 19 + (90 if monitor["status"] == "BREACH" else 0) + int(min(monitor["drift_score"], 1.0) * 60)
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
            "incident_commander": rule["escalation_owner"] if escalation_level in {"L2", "L3"} else rule["primary_owner"],
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
            "blocks_live_authorization": monitor["status"] == "BREACH" or monitor["category"] in {"MODEL_RISK_AGING", "REVIEWER_SLA"},
        }
        incidents.append(incident)
        timeline.extend([
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
        ])

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


def _promotion_control_payload() -> Dict:
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
        model["linked_component"]: model
        for model in model_risk["models"]
        if model["model_type"] == "ALPHA_MODEL"
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

        def add_gate(stage: str, status: str, owner: str, evidence_ref: str, score: float, reason: str, blocks: bool) -> None:
            gate = {
                "gate_id": f"PG-{alpha_id.upper()}-{len(gates) + 1:02d}",
                "alpha_id": alpha_id,
                "stage": stage,
                "status": status,
                "owner": owner,
                "evidence_ref": evidence_ref,
                "score": round(score, 3),
                "reason": reason,
                "blocks_paper_replay": blocks and stage in {"ALPHA_SCREEN", "VALIDATION", "PAPER_REPLAY", "RISK_REVIEW"},
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
            gate_status(risk_report["decision"] == "RISK_APPROVED", risk_report["decision"] == "RISK_APPROVED_WITH_LIMITS"),
            "Risk",
            risk_report["reproducibility_hash"],
            risk_report["gates_passed"] / max(risk_report["gates_total"], 1),
            "; ".join(risk_report["reasons"]) or f"Risk decision {risk_report['decision']}",
            not risk_report["decision"].startswith("RISK_APPROVED"),
        )
        model_findings = alpha_model["findings"] if alpha_model else ["No alpha model record found"]
        model_status = "PASS" if alpha_model and alpha_model["review_status"] == "APPROVED_FOR_RESEARCH" else "WATCH"
        if alpha_model and alpha_model["risk_tier"] == "HIGH" and alpha_model["review_status"] != "APPROVED_FOR_RESEARCH":
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
            monitor_status = "FAIL" if worst_monitor["status"] == "BREACH" else "WATCH" if worst_monitor["status"] == "WATCH" else "PASS"
            monitor_reason = f"{worst_monitor['category']} {worst_monitor['status']} drift={worst_monitor['drift_score']}"
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
            _short_hash("access", access_control["summary"]["permission_rows"], access_control["summary"]["denied_dangerous_permissions"]),
            1.0 if access_clean else 0.0,
            "Dangerous permissions denied; exports remain metadata-only" if access_clean else "Access policy incomplete",
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
        promotion_records.append({
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
        })

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
            "approved_for_dry_run": sum(1 for record in promotion_records if record["decision"].startswith("APPROVED_FOR_DRY_RUN")),
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


def _release_committee_payload() -> Dict:
    promotion = _promotion_control_payload()
    reviewer_workflow = _reviewer_workflow_payload()
    evidence_ledger = _evidence_ledger_payload()
    access_control = _access_control_payload()
    phase3a = _phase3a_readiness_payload()["readiness"]

    seats = [
        {"seat_id": "RC-CHAIR", "role": "Committee Chair", "required": True, "member": "CTO Delegate", "domain": "Research Governance", "vote_weight": 1.0},
        {"seat_id": "RC-RISK", "role": "Independent Risk", "required": True, "member": "Risk Reviewer", "domain": "Risk", "vote_weight": 1.0},
        {"seat_id": "RC-MODEL", "role": "Model Validation", "required": True, "member": "Model Validator", "domain": "Model Risk", "vote_weight": 1.0},
        {"seat_id": "RC-DATA", "role": "Data Governance", "required": True, "member": "Data Steward", "domain": "Data", "vote_weight": 1.0},
        {"seat_id": "RC-COMPLIANCE", "role": "Compliance", "required": True, "member": "Compliance Reviewer", "domain": "Compliance", "vote_weight": 1.0},
        {"seat_id": "RC-PLATFORM", "role": "Research Platform", "required": False, "member": "Platform Reviewer", "domain": "Platform", "vote_weight": 0.5},
    ]
    agenda = []
    votes = []
    required_attestations = [
        {"attestation_id": "ATT-VALIDATION", "name": "Independent validation evidence complete", "owner_role": "Model Validation", "status": "MISSING", "evidence_ref": "MRM-I01", "blocks_release": True},
        {"attestation_id": "ATT-RISK", "name": "Risk challenge complete", "owner_role": "Independent Risk", "status": "MISSING", "evidence_ref": "EV-SCENARIO-STRESS-001", "blocks_release": True},
        {"attestation_id": "ATT-DATA", "name": "Point-in-time data lineage reviewed", "owner_role": "Data Governance", "status": "MISSING", "evidence_ref": "MRM-I02", "blocks_release": True},
        {"attestation_id": "ATT-COMPLIANCE", "name": "Phase 3A blockers closed", "owner_role": "Compliance", "status": "BLOCKED", "evidence_ref": "EV-PHASE3A-001", "blocks_release": True},
        {"attestation_id": "ATT-ACCESS", "name": "Access and export controls verified", "owner_role": "Security / Compliance", "status": "READY_FOR_REVIEW", "evidence_ref": _short_hash("access-review", access_control["summary"]["permission_rows"]), "blocks_release": False},
        {"attestation_id": "ATT-LEDGER", "name": "Evidence root hash verified", "owner_role": "Research Platform", "status": "READY_FOR_REVIEW", "evidence_ref": evidence_ledger["root_hash"], "blocks_release": False},
    ]
    blocking_attestations = [item for item in required_attestations if item["blocks_release"] and item["status"] != "SIGNED"]
    open_blocking_tasks = [task for task in reviewer_workflow["tasks"] if task["blocks_live_authorization"] and task["state"] != "SIGNED_OFF"]

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
            "evidence_package_hash": _short_hash("committee", record["candidate_hash"], evidence_ledger["root_hash"], recommendation),
            "reason": record["decision_reason"] if recommendation != "DRY_RUN_ONLY" else "Dry-run replay may continue; release committee blocks broker/live progression",
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
            votes.append({
                "vote_id": f"RC-VOTE-{idx + 1:03d}-{seat_idx + 1:02d}",
                "agenda_id": agenda_item["agenda_id"],
                "seat_id": seat["seat_id"],
                "role": seat["role"],
                "vote": vote,
                "rationale": rationale,
                "signed": False,
                "signature_hash": None,
                "vote_weight": seat["vote_weight"],
            })

    required_seats = [seat for seat in seats if seat["required"]]
    signed_required_votes = [
        vote for vote in votes
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


def _system_completeness_payload() -> Dict:
    modules = [
        {
            "id": "overview",
            "name": "Research Overview",
            "name_zh": "研究总览",
            "route": "/research",
            "endpoint": "/api/v1/console/overview",
            "layer": "Console",
            "status": "BACKEND_BACKED",
            "backend_contract": "Computed read-only research funnel snapshot",
            "frontend_status": "ROUTED",
            "completion_score": 0.78,
            "gap": "Needs persisted experiment history and role-aware drilldowns",
        },
        {
            "id": "alpha-factory",
            "name": "Alpha Factory",
            "name_zh": "因子工厂",
            "route": "/research/alpha-factory",
            "endpoint": "/api/v1/console/alpha-factory",
            "layer": "Research Compute",
            "status": "BACKEND_BACKED",
            "backend_contract": "Backend factor metrics, screening, and correlation matrix",
            "frontend_status": "ROUTED",
            "completion_score": 0.7,
            "gap": "Needs persisted alpha DAG, feature store integration, and capacity research workflow",
        },
        {
            "id": "experiments",
            "name": "Experiment Registry",
            "name_zh": "实验注册表",
            "route": "/research/experiments",
            "endpoint": "/api/v1/console/experiments",
            "layer": "Research Governance",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only experiment registry with artifacts and lineage policy",
            "frontend_status": "ROUTED",
            "completion_score": 0.68,
            "gap": "Needs durable artifact storage, search, approval workflow, and reproducibility replay",
        },
        {
            "id": "data-catalog",
            "name": "Data Catalog",
            "name_zh": "数据目录",
            "route": "/research/data-catalog",
            "endpoint": "/api/v1/console/data-catalog",
            "layer": "Data Platform",
            "status": "BACKEND_BACKED",
            "backend_contract": "Point-in-time dataset, feature, quality, and lineage snapshot",
            "frontend_status": "ROUTED",
            "completion_score": 0.62,
            "gap": "Needs real market data lake, corporate actions, entitlements, SLA monitoring, and schema registry",
        },
        {
            "id": "validation",
            "name": "Validation Dashboard",
            "name_zh": "验证看板",
            "route": "/research/validation",
            "endpoint": "/api/v1/console/validation",
            "layer": "Validation",
            "status": "BACKEND_BACKED",
            "backend_contract": "Backend validation reports and alpha lineage",
            "frontend_status": "ROUTED",
            "completion_score": 0.67,
            "gap": "Needs statistical test suite expansion, multiple-testing controls, and independent validation signoff",
        },
        {
            "id": "backtest-explainability",
            "name": "Backtest Explainability",
            "name_zh": "回测可解释性",
            "route": "/research/backtest-explainability",
            "endpoint": "/api/v1/console/backtest-explainability",
            "layer": "Simulation",
            "status": "BACKEND_BACKED",
            "backend_contract": "Backend deterministic replay with equity, drawdown, cost, segment, and regime attribution",
            "frontend_status": "ROUTED",
            "completion_score": 0.72,
            "gap": "Needs event-driven execution simulation, order-book slippage model, and persisted report artifacts",
        },
        {
            "id": "scenario-stress",
            "name": "Scenario Stress",
            "name_zh": "情景压力",
            "route": "/research/scenario-stress",
            "endpoint": "/api/v1/console/scenario-stress",
            "layer": "Risk Simulation",
            "status": "BACKEND_BACKED",
            "backend_contract": "Multi-scenario replay, liquidity haircut, cost widening, and correlation shift",
            "frontend_status": "ROUTED",
            "completion_score": 0.7,
            "gap": "Needs stress scenario library governance and external macro/regime scenario feeds",
        },
        {
            "id": "model-risk-review",
            "name": "Model Risk Review",
            "name_zh": "模型风险复核",
            "route": "/research/model-risk-review",
            "endpoint": "/api/v1/console/model-risk-review",
            "layer": "Model Risk",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only model inventory, limitations, findings, and compensating controls",
            "frontend_status": "ROUTED",
            "completion_score": 0.66,
            "gap": "Needs independent reviewer workflow completion and signed evidence packets",
        },
        {
            "id": "evidence-ledger",
            "name": "Evidence Ledger",
            "name_zh": "证据链",
            "route": "/research/evidence-ledger",
            "endpoint": "/api/v1/console/evidence-ledger",
            "layer": "Governance",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only hash-chain evidence ledger and blockers",
            "frontend_status": "ROUTED",
            "completion_score": 0.74,
            "gap": "Needs externalized append-only storage and signature verification",
        },
        {
            "id": "reviewer-workflow",
            "name": "Reviewer Workflow",
            "name_zh": "复核流程",
            "route": "/research/reviewer-workflow",
            "endpoint": "/api/v1/console/reviewer-workflow",
            "layer": "Governance",
            "status": "BACKEND_BACKED",
            "backend_contract": "Reviewer capacity, tasks, gates, evidence links, and policy snapshot",
            "frontend_status": "ROUTED",
            "completion_score": 0.64,
            "gap": "Needs identity-backed assignment, SLA enforcement, comments, and signed attestations",
        },
        {
            "id": "access-control",
            "name": "Access Control",
            "name_zh": "权限控制",
            "route": "/research/access-control",
            "endpoint": "/api/v1/console/access-control",
            "layer": "Security",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only RBAC matrix, denied dangerous capabilities, and export packages",
            "frontend_status": "ROUTED",
            "completion_score": 0.61,
            "gap": "Needs actual auth provider, session enforcement, audit logging, and export signing",
        },
        {
            "id": "monitoring-drift",
            "name": "Monitoring Drift",
            "name_zh": "漂移监控",
            "route": "/research/monitoring-drift",
            "endpoint": "/api/v1/console/monitoring-drift",
            "layer": "Monitoring",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only drift monitors, thresholds, breaches, and remediation links",
            "frontend_status": "ROUTED",
            "completion_score": 0.63,
            "gap": "Needs scheduler, metric persistence, alert routing, and incident automation",
        },
        {
            "id": "incident-response",
            "name": "Incident Response",
            "name_zh": "事件响应",
            "route": "/research/incident-response",
            "endpoint": "/api/v1/console/incident-response",
            "layer": "Operations",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only incidents, routing rules, runbooks, and timelines",
            "frontend_status": "ROUTED",
            "completion_score": 0.62,
            "gap": "Needs paging integration, mutable incident lifecycle, and postmortem workflow after governance approval",
        },
        {
            "id": "promotion-control",
            "name": "Promotion Control",
            "name_zh": "晋级控制",
            "route": "/research/promotion-control",
            "endpoint": "/api/v1/console/promotion-control",
            "layer": "Release Governance",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only promotion gates, blockers, and safety boundary",
            "frontend_status": "ROUTED",
            "completion_score": 0.69,
            "gap": "Needs signed approvals and broker test environment evidence before any L3 movement",
        },
        {
            "id": "release-committee",
            "name": "Release Committee",
            "name_zh": "发布委员会",
            "route": "/research/release-committee",
            "endpoint": "/api/v1/console/release-committee",
            "layer": "Release Governance",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only committee agenda, votes, attestations, and blockers",
            "frontend_status": "ROUTED",
            "completion_score": 0.64,
            "gap": "Needs quorum workflow, cryptographic signatures, and compliance approvals",
        },
        {
            "id": "agents",
            "name": "Research Agents",
            "name_zh": "研究智能体",
            "route": "/research/agents",
            "endpoint": "/api/v1/console/agents",
            "layer": "AI Research Ops",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only agent run status, approvals, score, and budget snapshot",
            "frontend_status": "ROUTED",
            "completion_score": 0.58,
            "gap": "Needs real agent planner, sandboxed tool execution, budget controls, and experiment write path",
        },
        {
            "id": "paper-trading",
            "name": "Paper Trading",
            "name_zh": "纸面交易",
            "route": "/research/paper-trading",
            "endpoint": "/api/v1/console/paper-trading",
            "layer": "Simulation",
            "status": "BACKEND_BACKED",
            "backend_contract": "Backend paper run metrics and equity curve snapshot",
            "frontend_status": "ROUTED",
            "completion_score": 0.61,
            "gap": "Needs broker test environment adapter before L3; live trading remains disabled",
        },
        {
            "id": "portfolio",
            "name": "Portfolio Research",
            "name_zh": "组合研究",
            "route": "/research/portfolio",
            "endpoint": "/api/v1/console/portfolio",
            "layer": "Portfolio",
            "status": "BACKEND_BACKED",
            "backend_contract": "Backend portfolio simulation, drawdown, risk contribution, and return attribution",
            "frontend_status": "ROUTED",
            "completion_score": 0.65,
            "gap": "Needs optimizer, constraints engine, factor risk model, and transaction cost-aware rebalance",
        },
        {
            "id": "risk",
            "name": "Risk Dashboard",
            "name_zh": "风险看板",
            "route": "/research/risk",
            "endpoint": "/api/v1/console/risk-dashboard",
            "layer": "Risk",
            "status": "BACKEND_BACKED",
            "backend_contract": "Backend fail-closed risk gate and lineage snapshot",
            "frontend_status": "ROUTED",
            "completion_score": 0.69,
            "gap": "Needs policy versioning, production risk engine separation, and independent risk approval history",
        },
        {
            "id": "events",
            "name": "Events",
            "name_zh": "事件",
            "route": "/research/events",
            "endpoint": "/api/v1/console/events",
            "layer": "Observability",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only research event stream snapshot",
            "frontend_status": "ROUTED",
            "completion_score": 0.56,
            "gap": "Needs durable event bus, trace IDs across services, retention policy, and replay tooling",
        },
        {
            "id": "computation-lab",
            "name": "Computation Lab",
            "name_zh": "计算实验室",
            "route": "/research/computation-lab",
            "endpoint": "/api/v1/console/computation-lab",
            "layer": "Research Compute",
            "status": "BACKEND_BACKED",
            "backend_contract": "Parameterized backend computation for alpha, cost, stress, risk, and lineage",
            "frontend_status": "ROUTED",
            "completion_score": 0.73,
            "gap": "Needs persisted scenario runs, comparison workspaces, and exportable research packets",
        },
        {
            "id": "architecture",
            "name": "System Architecture",
            "name_zh": "系统架构",
            "route": "/research/architecture",
            "endpoint": "/api/v1/console/architecture",
            "layer": "Architecture",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only module map, data flows, gaps, and safety boundary",
            "frontend_status": "ROUTED",
            "completion_score": 0.66,
            "gap": "Needs deployment topology, data contracts, ownership registry, and runtime dependency graph",
        },
        {
            "id": "phase-3a",
            "name": "Phase 3A Readiness",
            "name_zh": "3A 准入",
            "route": "/research/phase-3a",
            "endpoint": "/api/v1/console/phase-3a-readiness",
            "layer": "Governance",
            "status": "BLOCKED",
            "backend_contract": "Read-only Phase 3A criteria and blocker matrix",
            "frontend_status": "ROUTED",
            "completion_score": 0.52,
            "gap": "External broker, legal, compliance, credentials, and network approvals remain open",
        },
        {
            "id": "system-health",
            "name": "System Health",
            "name_zh": "系统健康",
            "route": "/research/system-health",
            "endpoint": "/api/v1/console/system-health",
            "layer": "Operations",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only service health, integration gates, and safety checks",
            "frontend_status": "ROUTED",
            "completion_score": 0.6,
            "gap": "Needs real probes, uptime SLOs, dependency health, and CI status integration",
        },
        {
            "id": "system-completeness",
            "name": "System Completeness Audit",
            "name_zh": "系统完整度审查",
            "route": "/research/system-completeness",
            "endpoint": "/api/v1/console/system-completeness",
            "layer": "Governance",
            "status": "BACKEND_BACKED",
            "backend_contract": "Read-only cross-module coverage, test evidence, and gap roadmap",
            "frontend_status": "ROUTED",
            "completion_score": 0.76,
            "gap": "Needs automated coverage extraction from route manifests, OpenAPI, and CI evidence",
        },
    ]
    gaps = [
        {
            "id": "G01",
            "area": "Institutional Data Platform",
            "severity": "CRITICAL",
            "status": "OPEN",
            "impact": "No real market data lake, survivorship-bias controls, corporate actions, or vendor entitlement workflow yet",
            "next_action": "Implement versioned point-in-time market data ingestion, validation, and feature store storage",
        },
        {
            "id": "G02",
            "area": "Research Execution Engine",
            "severity": "HIGH",
            "status": "PARTIAL",
            "impact": "Backtests are deterministic computed snapshots, not a full event-driven execution simulator",
            "next_action": "Add event-driven backtest kernel, transaction cost models, slippage, borrow, and artifact persistence",
        },
        {
            "id": "G03",
            "area": "AI Agent Platform",
            "severity": "HIGH",
            "status": "PARTIAL",
            "impact": "Agent dashboards exist, but planner, sandboxed tool execution, budget limits, and experiment write paths are not integrated",
            "next_action": "Build governed research-agent execution service with budget, audit, and reproducibility controls",
        },
        {
            "id": "G04",
            "area": "Security And Access",
            "severity": "HIGH",
            "status": "PARTIAL",
            "impact": "RBAC is represented as a read-only audit view rather than enforced auth/session middleware",
            "next_action": "Add identity provider integration, session enforcement, signed exports, and immutable access audit logs",
        },
        {
            "id": "G05",
            "area": "Production Trading Boundary",
            "severity": "CRITICAL",
            "status": "INTENTIONALLY_BLOCKED",
            "impact": "No broker test environment, credentials, order routing, or live trading capability is authorized",
            "next_action": "Close Phase 3A E01/E02/E03/E04/E06 before any broker-test-environment development",
        },
        {
            "id": "G06",
            "area": "Observability And Operations",
            "severity": "HIGH",
            "status": "PARTIAL",
            "impact": "Monitoring, incidents, and system health are snapshots, not live scheduler-backed operational workflows",
            "next_action": "Add metrics persistence, service probes, alert routing, incident mutation workflow, and postmortem evidence",
        },
        {
            "id": "G07",
            "area": "CI, Release, And Evidence Automation",
            "severity": "HIGH",
            "status": "PARTIAL",
            "impact": "Tests exist, but full CI provenance, signed release packs, and automated evidence capture are not complete",
            "next_action": "Wire unit, integration, contract, browser, security, and governance gates into reproducible CI evidence",
        },
    ]
    checks = [
        {
            "id": "CHK-001",
            "name": "Frontend route inventory",
            "status": "PASS",
            "evidence": "25 research console routes are represented in the navigation and router after this audit page",
        },
        {
            "id": "CHK-002",
            "name": "Backend endpoint inventory",
            "status": "PASS",
            "evidence": "25 FastAPI read-only console endpoints plus /health are represented",
        },
        {
            "id": "CHK-003",
            "name": "Execution safety boundary",
            "status": "PASS",
            "evidence": "All console payloads are read_only and live trading remains disallowed",
        },
        {
            "id": "CHK-004",
            "name": "Top-tier institutional readiness",
            "status": "FAIL",
            "evidence": "The platform is a strong L2/L2.5 research control plane, not yet a top-tier AI quantitative trading system",
        },
    ]
    open_high_gaps = sum(1 for gap in gaps if gap["severity"] in {"HIGH", "CRITICAL"} and gap["status"] != "CLOSED")
    backend_backed = sum(1 for module in modules if module["status"] in {"BACKEND_BACKED", "BLOCKED"})
    read_only = len(modules)
    readiness_score = round(statistics.mean(module["completion_score"] for module in modules), 3)
    inv = _repo_inventory()  # REAL repo introspection — grounds the curated completeness map in hard counts
    return {
        "audit_mode": "READ_ONLY_SYSTEM_COMPLETENESS_AUDIT",
        "current_level": "L2_5_RESEARCH_CONTROL_PLANE",
        "institutional_target": "TOP_TIER_AI_QUANT_TRADING_SYSTEM",
        "readiness_score": readiness_score,
        "inventory": inv,
        "summary": {
            "frontend_routes": inv.get("n_console_pages", len(modules)),
            "api_endpoints": inv.get("n_console_endpoints", len(modules)),
            "backend_modules": inv.get("total_services_modules", 0),
            "test_functions": inv.get("n_test_functions", 0),
            "backend_backed_modules": backend_backed,
            "fully_operational_modules": sum(1 for module in modules if module["completion_score"] >= 0.72),
            "read_only_modules": read_only,
            "open_high_gaps": open_high_gaps,
            "live_trading_authorized": False,
        },
        "modules": modules,
        "checks": checks,
        "gaps": gaps,
        "roadmap": [
            {
                "phase": "Phase A",
                "objective": "Harden read-only research console and API contracts",
                "exit_criteria": "Typed contracts, tests, browser smoke checks, route inventory, and gap dashboard are stable",
                "status": "IN_PROGRESS",
            },
            {
                "phase": "Phase B",
                "objective": "Build institutional research data and feature platform",
                "exit_criteria": "Point-in-time market data, feature store, lineage, and quality gates are persisted",
                "status": "NOT_STARTED",
            },
            {
                "phase": "Phase C",
                "objective": "Upgrade simulation, validation, portfolio, and risk engines",
                "exit_criteria": "Event-driven backtests, cost models, optimizer, risk model, and stress libraries are governed",
                "status": "NOT_STARTED",
            },
            {
                "phase": "Phase D",
                "objective": "Add governed AI research-agent execution",
                "exit_criteria": "Agents can create reproducible experiments under budget, sandbox, and reviewer controls",
                "status": "NOT_STARTED",
            },
            {
                "phase": "Phase E",
                "objective": "Prepare broker test environment only after Phase 3A approvals",
                "exit_criteria": "Broker, legal, compliance, credentials, security, and network evidence are approved",
                "status": "BLOCKED",
            },
        ],
        "safety_boundary": {
            "read_only_console": True,
            "execution_boundary": "LIVE_TRADING_DISALLOWED",
            "authorized_level": "L2_DRY_RUN_ONLY",
            "blocked_until": ["E01", "E02", "E03", "E04", "E06"],
        },
    }


# repo-root-relative path to the REAL-crypto research snapshot (gitignored data/cache).
# services/research/console_api/main.py -> up 4 -> repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_CRYPTO_SNAPSHOT_PATH = os.path.join(_REPO_ROOT, "data", "cache", "crypto_research_snapshot.json")
_FX_RESEARCH_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "fx_research_snapshot.json")
_FX_GAUNTLET_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "fx_gauntlet_snapshot.json")
_CARRY_STRATEGY_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "crypto_carry_strategy.json")
_CARRY_WALKFWD_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "crypto_carry_walkforward.json")
_CARRY_CAPACITY_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "crypto_carry_capacity.json")
_IC_PANEL_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "ic_panel_snapshot.json")
_DRY_RUN_DB = os.path.join(_REPO_ROOT, "data", "cache", "dry_run.db")
_CAPACITY_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "capacity_snapshot.json")
_MACRO_REGIME_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "macro_regime_snapshot.json")
_COMBO_BOOK_SNAPSHOT = os.path.join(_REPO_ROOT, "data", "cache", "combo_book_snapshot.json")


# --- Phase 1B: REAL per-factor return series (offline crypto research snapshot) -----------------------
# tools/crypto_research_offline.py persists, for the top real factors, their REAL per-period net return
# series under `factor_series`. When present, the read-only research/risk/PnL engines run on REAL data
# instead of the synthetic sin/cos roots (_factor_values/_forward_returns); absent (CI / fresh checkout)
# everything fails closed to synthetic. Names resolve at call time so the earlier root functions may use
# these even though they are defined here. Cached once per process (the snapshot is static per process).
_REAL_FACTORS_CACHE = None


def _load_real_factors() -> List[Dict]:
    global _REAL_FACTORS_CACHE
    if _REAL_FACTORS_CACHE is not None:
        return _REAL_FACTORS_CACHE
    out: List[Dict] = []
    try:
        with open(_CRYPTO_SNAPSHOT_PATH, encoding="utf-8") as fh:
            snap = json.load(fh)
        by_id = {f.get("alpha_id"): f for f in snap.get("factors", [])}
        for aid, s in (snap.get("factor_series") or {}).items():
            rs = [float(x) for x in (s.get("returns") or [])]
            if len(rs) < 60:                                   # need enough periods to run the engines honestly
                continue
            rs = rs[-120:]                                     # cap at the most-recent 120 (shorter series kept as-is)
            f = by_id.get(aid, {})
            out.append({
                "alpha_id": aid, "name": s.get("name", aid), "returns": rs,
                "ic": f.get("ic"), "rank_ic": f.get("rank_ic"), "net_sharpe": f.get("net_sharpe"),
                "turnover": f.get("turnover"), "deflated_sharpe": f.get("deflated_sharpe"),
                "capacity_adj_sharpe": f.get("capacity_adj_sharpe"), "pbo": f.get("pbo"),
                "breadth": f.get("breadth"), "state": f.get("state"), "verdict": f.get("verdict"),
            })
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        out = []
    _REAL_FACTORS_CACHE = out
    return out


def _real_factor(idx: int):
    rf = _load_real_factors()
    return rf[idx % len(rf)] if rf else None


def _input_provenance() -> str:
    """REAL_CRYPTO_SERIES when the snapshot's real per-factor return series back the engines, else the
    deterministic synthetic fallback. Surfaced per endpoint so the UI can honestly badge real-vs-synthetic."""
    return "REAL_CRYPTO_SERIES" if _load_real_factors() else "SYNTHETIC_DETERMINISTIC"


def _snapshot_summary() -> Dict:
    """The research snapshot's summary block (n_evaluated / trial_count / …), or {} when absent."""
    try:
        with open(_CRYPTO_SNAPSHOT_PATH, encoding="utf-8") as fh:
            return json.load(fh).get("summary", {}) or {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _overview_active_runs() -> int:
    """Count of currently non-terminal research runs (queued/running) from the registry; 0 fail-closed."""
    try:
        return sum(1 for r in _runs_registry().list(limit=200) if r.status in ("queued", "running"))
    except Exception:
        return 0


_EXP_DB = os.path.join(_REPO_ROOT, "data", "cache", "experiment_registry.db")
_EXP_STAGE_MAP = {"DRAFT": "DISCOVERY", "TESTING": "VALIDATION", "VALIDATED": "VALIDATION",
                  "PAPER_TRADING": "PAPER_REPLAY", "SHADOW": "PAPER_REPLAY", "APPROVED": "RISK_REVIEW",
                  "DEGRADED": "ARCHIVED", "PAUSED": "ARCHIVED", "RETIRED": "ARCHIVED", "REJECTED": "ARCHIVED"}
_EXP_STAGES = ["DISCOVERY", "VALIDATION", "PAPER_REPLAY", "RISK_REVIEW", "ARCHIVED"]


def _load_experiment_store():
    """Read-only handle on the offline experiment_registry (immutable evaluation artifacts), or None when
    absent (CI / fresh checkout) -> the Experiments/EvidenceLedger pages fail closed to synthetic."""
    try:
        from services.research.experiment_registry.store import SqliteExperimentStore
        return SqliteExperimentStore(_EXP_DB, create=False) if os.path.exists(_EXP_DB) else None
    except Exception:
        return None


_PAPER_TRACK_DB = os.path.join(_REPO_ROOT, "data", "cache", "paper_track.db")


def _load_paper_track_store():
    """Read-only handle on the offline forward paper-track store (real out-of-sample paper P&L / IC), or
    None when absent (CI / fresh checkout) -> the live side fails closed to null / NO_LIVE_DATA."""
    try:
        from services.research.paper_trading.store import PaperTrackStore
        return PaperTrackStore(_PAPER_TRACK_DB, create=False) if os.path.exists(_PAPER_TRACK_DB) else None
    except Exception:
        return None


def _decision_from_verdict(v) -> str:
    v = str(v or "").upper()
    if "APPROV" in v or "PROMOT" in v or v == "PASS":
        return "APPROVED"
    if "REJECT" in v or "FAIL" in v:
        return "REJECTED_VALIDATION"
    return "NEEDS_MORE_DATA"


def _real_experiment_rows(st) -> List[Dict]:
    """One ResearchExperiment per recorded alpha (top by net Sharpe), built from the REAL immutable
    evaluation artifacts (reproducibility hash, IC, net Sharpe, deflated Sharpe, turnover, verdict)."""
    try:
        alphas = st.all_alphas()
    except Exception:
        return []
    rows = []
    for a in sorted(alphas, key=lambda x: (x.get("sharpe") if x.get("sharpe") is not None else -1e9), reverse=True)[:30]:
        aid = a.get("alpha_id")
        try:
            evs = st.list_evaluations(aid)
        except Exception:
            evs = []
        if not evs:
            continue
        ev = evs[-1]
        try:
            ej = json.loads(ev.get("evaluation_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            ej = {}
        chash = ev.get("content_hash") or ""
        breadth = ej.get("mean_breadth") or 1.0
        try:
            expr_label = json.loads(a.get("definition_json") or "{}").get("name") or aid
        except (json.JSONDecodeError, TypeError):
            expr_label = aid
        rows.append({
            "experiment_id": ev.get("run_id"), "alpha_id": aid,
            "expression": expr_label, "owner_agent": "ResearchAgentLoop",
            "stage": _EXP_STAGE_MAP.get(str(a.get("state")), "VALIDATION"),
            "decision": _decision_from_verdict(ev.get("verdict")),
            "created_at": ev.get("recorded_ts") or 0, "execution_mode": "L2_DRY_RUN_ONLY",
            "config": {"universe": "CRYPTO", "horizon_days": 1, "cost_bps": 10, "seed": 0,
                       "data_snapshot_id": ev.get("dataset_version"), "config_hash": chash[:16]},
            "metrics": {"ic": round(ej.get("ic_mean") or 0.0, 4), "rank_ic": round(ej.get("ic_mean") or 0.0, 4),
                        "sharpe": round(ej.get("net_sharpe") if ej.get("net_sharpe") is not None else (a.get("sharpe") or 0.0), 3),
                        "turnover": round(ej.get("turnover") or 0.0, 4),
                        "capacity_estimate": int(max(1.0, breadth) * 12_000_000),
                        "composite_score": round(max(0.0, min(1.0, ej.get("deflated_sharpe") or 0.0)), 3)},
            "reproducibility_hash": ej.get("reproducibility_hash") or chash,
            "artifacts": [
                {"artifact_id": f"{ev.get('run_id')}-eval", "kind": "EVALUATION", "status": "SEALED",
                 "uri": f"experiment://{ev.get('run_id')}", "content_hash": chash},
                {"artifact_id": f"{ev.get('run_id')}-defn", "kind": "DEFINITION", "status": "SEALED",
                 "uri": f"experiment://{ev.get('run_id')}/definition", "content_hash": chash[:24]},
                {"artifact_id": f"{ev.get('run_id')}-metrics", "kind": "METRICS", "status": "SEALED",
                 "uri": f"experiment://{ev.get('run_id')}/metrics", "content_hash": chash[8:32] or chash},
            ],
            "lineage": {"upstream": ["factor_engine", "session"],
                        "downstream": ["validation", "experiment_registry"]},
        })
    return rows


def _safety_primitives_payload() -> Dict:
    """READ-ONLY status of the REAL safety primitives: the latched KillSwitch + the pre-trade gate's
    CHECK_REGISTRY + the authoritative SafetyConfig flags. No order intent is built, nothing is armed or
    reset (those are human-only); this surfaces the live/disallowed posture so it is visible, not hidden."""
    from services.risk.pre_trade.kill_switch import KillSwitch
    from services.risk.pre_trade.checks import CHECK_REGISTRY
    from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS as _cfg
    ks = KillSwitch()
    checks = [{"name": n, "stage": s} for n, s, _fn in CHECK_REGISTRY]
    return {
        "safety_mode": "L2_DRY_RUN_ONLY",
        "kill_switch": {"is_active": ks.is_active(), "trip_count": ks.state.trip_count,
                        "fail_closed": True, "human_only_reset": True, "auto_reset": False},
        "pre_trade_gate": {"checks_registered": len(checks), "checks": checks,
                           "fail_closed_on_unknown": True, "refuses_unsafe_build": True},
        "safety_config": {"live_trading_enabled": _cfg.live_trading_enabled,
                          "real_broker_enabled": _cfg.real_broker_enabled,
                          "allow_order_submission": _cfg.allow_order_submission,
                          "allow_ai_direct_trading": _cfg.allow_ai_direct_trading,
                          "require_human_approval": _cfg.require_human_approval},
        "live_authorization_allowed": False,
    }


def _corr(a: List[float], b: List[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a, b = a[:n], b[:n]
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / (va ** 0.5 * vb ** 0.5) if va > 0 and vb > 0 else 0.0


def _inverse_vol_weights(ids: List[str], rbi: Dict[str, List[float]]) -> Dict[str, float]:
    inv = {a: 1.0 / (statistics.pstdev(rbi[a]) or 1e-9) for a in ids}
    tot = sum(inv.values()) or 1.0
    return {a: inv[a] / tot for a in ids}


def _weighted_max_dd(ids: List[str], rbi: Dict[str, List[float]]) -> float:
    """Max drawdown of the inverse-vol-weighted portfolio of `ids` (real per-factor net returns)."""
    if not ids:
        return 0.0
    w = _inverse_vol_weights(ids, rbi)
    n = min(len(rbi[a]) for a in ids)
    equity, curve = 1.0, []
    for d in range(n):
        equity *= 1.0 + sum(w[a] * rbi[a][d] for a in ids)
        curve.append(equity)
    return compute_max_drawdown(curve) or 0.0


def _population_optimizer_payload() -> Dict:
    """De-redundant SET selection (synergistic forward-selection by incremental IR) + inverse-vol weights +
    marginal IR / marginal drawdown + excluded-with-reasons + diversity (1−mean|corr|). Runs on the REAL
    per-factor return series when present; fail-closed to a deterministic synthetic set otherwise."""
    rf = _load_real_factors()
    if rf:
        rbi = {f["alpha_id"]: f["returns"] for f in rf}
        sel = select_synergistic_set(rbi, max_k=6, min_incremental_ir=0.0)
        ids = sel["selected_ids"]
        weights = _inverse_vol_weights(ids, rbi) if ids else {}
        selected, prev_dd, acc = [], 0.0, []
        for s in sel["selected"]:
            acc.append(s["id"])
            dd = _weighted_max_dd(acc, rbi)
            selected.append({"alpha_id": s["id"], "weight": round(weights.get(s["id"], 0.0), 4),
                             "marginal_ir": round(s["incremental_ir"], 4),
                             "marginal_dd": round(dd - prev_dd, 4)})   # Δ set max-DD as this factor is added
            prev_dd = dd
        pairs = [(ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))]
        diversity = 1.0 - (statistics.fmean([abs(_corr(rbi[a], rbi[b])) for a, b in pairs]) if pairs else 0.0)
        excluded = [{"alpha_id": aid, "reason": "REDUNDANT_CORRELATED"} for aid in sel["dropped_redundant"]]
        return {"selected": selected, "excluded": excluded, "diversity": round(diversity, 3),
                "gross": round(sum(abs(s["weight"]) for s in selected), 2),
                "method": sel["method"], "n_candidates": sel["n_candidates"]}
    # fail-closed synthetic (CI / no snapshot): deterministic from the synthetic candidate pool
    cands = sorted(_alpha_candidates(), key=lambda a: a["composite_score"], reverse=True)
    sel_c, exc_c = cands[:4], cands[4:7]
    tot = sum(c["composite_score"] for c in sel_c) or 1.0
    selected = [{"alpha_id": c["candidate_id"], "weight": round(c["composite_score"] / tot, 4),
                 "marginal_ir": round(c["icir"], 4),
                 "marginal_dd": round((c["correlation_penalty"] - 0.1) * 0.3, 4)} for c in sel_c]
    excluded = [{"alpha_id": c["candidate_id"],
                 "reason": "REDUNDANT_CORRELATED" if c["correlation_penalty"] > 0.1 else "DROPPED_LOW_INCREMENTAL_IR"}
                for c in exc_c]
    return {"selected": selected, "excluded": excluded, "diversity": 0.71,
            "gross": round(sum(abs(s["weight"]) for s in selected), 2),
            "method": "FORWARD_SELECTION_INCREMENTAL_IR", "n_candidates": len(cands)}


def _short_factor_label(aid: str) -> str:
    s = aid
    for p in ("hx_product_", "hx_sum_", "hx_", "h_"):
        if s.startswith(p):
            s = s[len(p):]
            break
    s = s.replace("_price-", "·").replace("price-", "").replace("_", "·")
    return s[:18]


def _expr_fields(expr) -> List[str]:
    out: set = set()

    def walk(t):
        if isinstance(t, tuple) and t:
            if t[0] == "field" and len(t) > 1:
                out.add(t[1])
            for a in t[1:]:
                walk(a)
    walk(expr)
    return sorted(out)


def _dsl_explorer_payload() -> Dict:
    """Cross-sectional alpha expressions with their REAL static analysis: the dsl_checker type/dimension
    verdict, the operator-weighted complexity, and the machine-proof PIT-leakage result (the real
    expression_leakage detector perturbs strictly-future rows). Verdicts are COMPUTED by the real engines,
    not hand-authored — the deterministic panel is only the substrate the leakage detector perturbs."""
    import services.research.alpha_expression as ax
    from services.research.dsl_checker import check_expression
    from services.research.complexity import complexity as _cx
    from services.research import expression_leakage as el
    insts, T = ["A", "B", "C", "D"], 16

    def _mat(base):
        return [[base + 0.5 * t + 0.1 * j for j in range(len(insts))] for t in range(T)]
    panel = ax.FieldPanel(insts, list(range(T)),
                          {"close": _mat(100.0), "volume": _mat(10.0), "funding": _mat(0.01)})
    EXPRS = [
        (("cs_zscore", ("neg", ("decay_linear", ("field", "funding"), 7))), "资金费 carry 原语 / funding carry"),
        (("cs_rank", ("ts_delta", ("field", "close"), 5)), "价格动量 / price momentum"),
        (("cs_residualize", ("field", "close"), ("field", "volume")), "对量能中性化 / size-neutralized"),
        (("cs_zscore", ("ts_mean", ("field", "close"), 5)), "均值回复 / mean-reversion"),
        (("cs_rank", ("ts_lead", ("field", "close"), 1)), "读未来 → 检查器拒绝 / future-reading: rejected"),
    ]
    rows = []
    for expr, note in EXPRS:
        chk = check_expression(expr)
        try:
            cx_val = round(_cx(expr), 2)
        except Exception:
            cx_val = 0.0
        try:
            leak = el.compute_leakage_ok(expr, panel)
        except Exception:
            leak = None
        rows.append({"expr": ax.serialize(expr), "valid": bool(chk.ok), "complexity": cx_val,
                     "leakage_ok": leak, "deps": _expr_fields(expr), "note": note,
                     "errors": list(chk.errors)[:3]})
    return {"exprs": rows, "analysis_backend": "REAL_DSL_CHECKER+LEAKAGE+COMPLEXITY"}


# The full multi-dimensional factor-analysis schema surfaced by the Alpha Zoo. Each entry is one real
# dimension of an alpha's immutable evaluation artifact (experiment_registry), grouped for the UI. `fmt`
# tells the frontend how to render it; `good` is the direction that is better (for conditional colouring).
_ZOO_DIMENSIONS = [
    {"key": "ic", "zh": "IC", "en": "IC", "group": "预测力", "fmt": "signed3", "good": "high"},
    {"key": "icir", "zh": "ICIR", "en": "ICIR", "group": "预测力", "fmt": "signed2", "good": "high"},
    {"key": "ic_tstat", "zh": "IC t值", "en": "IC t-stat", "group": "预测力", "fmt": "signed2", "good": "high"},
    {"key": "hit_rate", "zh": "命中率", "en": "Hit rate", "group": "预测力", "fmt": "pct", "good": "high"},
    {"key": "net_sharpe", "zh": "净夏普", "en": "Net Sharpe", "group": "业绩", "fmt": "signed2", "good": "high"},
    {"key": "deflated_sharpe", "zh": "紧缩夏普", "en": "Deflated SR", "group": "过拟合", "fmt": "signed2", "good": "high"},
    {"key": "pbo", "zh": "PBO", "en": "PBO", "group": "过拟合", "fmt": "pct", "good": "low"},
    {"key": "turnover", "zh": "换手", "en": "Turnover", "group": "成本", "fmt": "signed2", "good": "low"},
    {"key": "marginal_ir", "zh": "增量IR", "en": "Marginal IR", "group": "正交", "fmt": "signed2", "good": "high"},
    {"key": "crowding_score", "zh": "拥挤度", "en": "Crowding", "group": "拥挤/容量", "fmt": "signed2", "good": "low"},
    {"key": "capacity_adj_sharpe", "zh": "容量调整夏普", "en": "Cap-adj SR", "group": "拥挤/容量", "fmt": "signed2", "good": "high"},
    {"key": "mean_breadth", "zh": "广度", "en": "Breadth", "group": "广度", "fmt": "num1", "good": "high"},
    {"key": "paper_ic", "zh": "纸面IC", "en": "Paper IC", "group": "时间/活体征", "fmt": "signed3", "good": "high"},
    {"key": "paper_sharpe", "zh": "纸面夏普", "en": "Paper SR", "group": "时间/活体征", "fmt": "signed2", "good": "high"},
    {"key": "perf_gap", "zh": "回测-纸面差", "en": "BT-paper gap", "group": "时间/活体征", "fmt": "signed2", "good": "low"},
    {"key": "n_paper_periods", "zh": "纸面期数", "en": "Paper periods", "group": "时间/活体征", "fmt": "num1", "good": "high"},
]
_ZOO_DIM_KEYS = tuple(d["key"] for d in _ZOO_DIMENSIONS)


def _zoo_tags(operator: str, ev: Dict) -> List[str]:
    """Honest, dimension-derived descriptors (not decoration): the operator family + factual flags."""
    tags = [operator] if operator else []
    tn = ev.get("turnover")
    if isinstance(tn, (int, float)) and tn < 0.3:
        tags.append("low-turnover")
    pb = ev.get("pbo")
    if isinstance(pb, (int, float)) and pb > 0.5:
        tags.append("overfit-risk")
    mir = ev.get("marginal_ir")
    if isinstance(mir, (int, float)) and mir > 0.3:
        tags.append("orthogonal")
    if (ev.get("mean_breadth") or 0) < 5:
        tags.append("thin")
    return tags


def _alpha_zoo_rows_real(st) -> List[Dict]:
    """One Zoo row per recorded alpha, carrying its FULL multi-dimensional evaluation (IC/ICIR/t-stat/
    hit-rate/turnover/PBO/deflated-SR/marginal-IR/crowding/capacity/breadth/verdict) from the immutable
    experiment_registry artifacts — the same source LifecycleMonitor reads. Generator + cluster + tags are
    derived from the (real) heuristic operator. Every numeric dimension is the real persisted value or None."""
    def _r(v, n):
        return round(v, n) if isinstance(v, (int, float)) else None
    try:
        alphas = st.all_alphas()
    except Exception:
        return []
    pts = _load_paper_track_store()
    rows: List[Dict] = []
    for a in sorted(alphas, key=lambda x: (x.get("sharpe") if x.get("sharpe") is not None else -1e9),
                    reverse=True):
        aid = a.get("alpha_id")
        try:
            evs = st.list_evaluations(aid)
            ej = json.loads(evs[-1].get("evaluation_json") or "{}") if evs else {}
        except (json.JSONDecodeError, TypeError, Exception):
            ej = {}
        try:
            dj = json.loads(a.get("definition_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            dj = {}
        operator = (dj.get("params") or {}).get("operator") or ""
        row = {
            "alpha_id": aid, "name": dj.get("name") or _short_factor_label(aid or ""),
            "generator": f"heuristic:{operator}" if operator else "heuristic_sweep",
            "status": str(a.get("state") or "TESTING"),
            "cluster_id": f"cl:{operator}" if operator else "cl:base",
            "verdict": str(ej.get("verdict") or "—"),
            "tags": _zoo_tags(operator, ej),
            # full multi-dimensional evaluation (real persisted values)
            "ic": _r(ej.get("ic_mean"), 4), "icir": _r(ej.get("icir"), 3),
            "ic_tstat": _r(ej.get("ic_tstat"), 2), "hit_rate": _r(ej.get("hit_rate"), 3),
            "net_sharpe": _r(ej.get("net_sharpe"), 3), "deflated_sharpe": _r(ej.get("deflated_sharpe"), 3),
            "pbo": _r(ej.get("pbo"), 3), "turnover": _r(ej.get("turnover"), 3),
            "marginal_ir": _r(ej.get("marginal_ir"), 3), "crowding_score": _r(ej.get("crowding_score"), 3),
            "capacity_adj_sharpe": _r(ej.get("capacity_adj_sharpe"), 3), "mean_breadth": _r(ej.get("mean_breadth"), 1),
        }
        # 时间/活体征 dimensions from the REAL forward paper-track store (out-of-sample paper P&L / IC);
        # None when this alpha has no paper track (fail-closed). These are a research-only record and
        # NEVER unlock any automatic promotion (auto_approved stays 0; non-human never APPROVED).
        t = pts.track(aid) if pts is not None else None
        row["paper_ic"] = t.get("paper_ic") if t else None
        row["paper_sharpe"] = t.get("paper_sharpe") if t else None
        row["perf_gap"] = t.get("perf_gap") if t else None
        row["n_paper_periods"] = (t.get("n_periods") if t else None)
        rows.append(row)
    return rows


def _alpha_zoo_rows_synthetic() -> List[Dict]:
    """Deterministic fallback (CI / no registry): the candidate set with plausible per-dimension values so
    the multi-dimensional page shape stays exercised. Marked SYNTHETIC at the endpoint."""
    gens = ["cs_genome", "gp", "mcts", "evolutionary"]
    rows: List[Dict] = []
    for i, c in enumerate(_alpha_candidates()):
        ic = round(c.get("ic", 0.0), 4)
        rows.append({
            "alpha_id": c["candidate_id"], "name": c["expression"], "generator": gens[i % len(gens)],
            "status": c["lifecycle_state"], "cluster_id": f"cl_{i % 3}", "verdict": "REQUIRES_HUMAN_REVIEW",
            "tags": ["synthetic"],
            "ic": ic, "icir": round(c.get("icir", 0.0), 3), "ic_tstat": round(ic * 30, 2),
            "hit_rate": round(0.5 + ic, 3), "net_sharpe": round(c.get("sharpe", 0.0), 3),
            "deflated_sharpe": round(min(1.0, c.get("composite_score", 0.0)), 3),
            "pbo": round((i % 5) * 0.1, 3), "turnover": round(c.get("turnover", 0.3), 3),
            "marginal_ir": round(c.get("rank_ic", 0.0), 3), "crowding_score": round((i % 4) * 0.15, 3),
            "capacity_adj_sharpe": round(c.get("sharpe", 0.0) * 0.9, 3),
            "mean_breadth": float(10 + i % 8),
            # 时间/活体征: synthetic fallback has no real forward paper track -> honest None
            "paper_ic": None, "paper_sharpe": None, "perf_gap": None, "n_paper_periods": None,
        })
    return rows


def _alpha_zoo_payload() -> Dict:
    """The searchable, multi-dimensional Alpha catalog. Real path reads the immutable experiment_registry
    (full per-alpha evaluation across ~12 dimensions); fail-closed to a deterministic synthetic set."""
    st = _load_experiment_store()
    rows, provenance = [], "SYNTHETIC_DETERMINISTIC"
    if st is not None:
        rows = _alpha_zoo_rows_real(st)
        if rows:
            provenance = "EXPERIMENT_REGISTRY"
    if not rows:
        rows = _alpha_zoo_rows_synthetic()
        provenance = "SYNTHETIC_DETERMINISTIC"
    return {"rows": rows, "dimensions": _ZOO_DIMENSIONS, "data_provenance": provenance}


def _alpha_graph_payload() -> Dict:
    """Real correlation network: nodes = factors, edges = |return correlation|, clusters = connected
    components above RHO_CUT (redundancy groups). Uses the real services/alpha correlation_graph engine on
    the REAL per-factor return series when present, else on the synthetic candidate streams (same engine)."""
    from services.alpha.alpha_graph import correlation_graph
    rho_cut, edge_min = 0.7, 0.3
    rf = _load_real_factors()
    if rf:
        rbi = {f["alpha_id"]: tuple(f["returns"]) for f in rf}
    else:
        rbi = {f"alpha-{i + 1}": tuple(_forward_returns(_factor_values(i), i)) for i in range(7)}
    g = correlation_graph(rbi)
    comps = sorted(g.clusters(rho_cut), key=lambda c: sorted(c)[0])
    cluster_of = {n: ci for ci, comp in enumerate(comps) for n in comp}
    n_cl = max(1, len(comps))
    nodes = []
    for ci, comp in enumerate(comps):
        cx = 300 + 200 * math.cos(2 * math.pi * ci / n_cl)
        cy = 180 + 130 * math.sin(2 * math.pi * ci / n_cl)
        members = sorted(comp)
        for mi, mid in enumerate(members):
            ang = 2 * math.pi * mi / max(1, len(members))
            r = 0.0 if len(members) == 1 else 44.0
            nodes.append({"id": mid, "label": _short_factor_label(mid),
                          "x": round(cx + r * math.cos(ang), 1), "y": round(cy + r * math.sin(ang), 1),
                          "cluster": f"cl_{cluster_of[mid]}"})
    edges = [{"a": a, "b": b, "corr": round(w, 3)} for a, b, w in g.near_duplicate_pairs(edge_min)]
    n_near = sum(1 for e in edges if e["corr"] >= rho_cut)
    return {"nodes": nodes, "edges": edges,
            "clusters": [{"name": f"cl_{i}", "count": len(c)} for i, c in enumerate(comps)],
            "rho_cut": rho_cut, "n_near_dup": n_near}

_CRYPTO_EMPTY_META = {
    "provider": "", "asset_class": "CRYPTO", "vs_currency": "", "days": 0, "granularity": "",
    "fwd_horizon": 0, "n_instruments": 0, "n_periods": 0, "coins": [], "search_mode": "",
    "evo_generations": 0, "evo_population": 0, "evo_elite": 0, "operators": [], "windows": [],
}
_CRYPTO_EMPTY_SUMMARY = {
    "n_evaluated": 0, "trial_count": 0, "auto_approved": 0, "requires_human_review": 0,
    "rejected": 0, "max_net_sharpe": None, "max_deflated_sharpe": None, "top_alpha_id": None,
}


def _crypto_live_payload() -> Dict:
    """Serve the persisted REAL-crypto research snapshot (generated OUT OF GATE by
    tools/crypto_research_run.py from read-only CoinGecko public OHLC). This endpoint
    NEVER fetches: the no-network-import ban inside services/ is machine-enforced
    (tests/governance/test_crypto_data_boundary.py); it only reads the local artifact.

    Fail-closed: when the snapshot is absent (the gate / CI never commits raw research
    output — data/cache is gitignored) or corrupt, it returns a well-formed EMPTY
    payload so the frontend contract still holds and the page shows an honest
    'no live snapshot yet' state instead of 500-ing or rendering undefined.

    Provenance is labelled REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT — distinct from the
    SYNTHETIC_DETERMINISTIC demo endpoints — so the honesty marker stays truthful."""
    snap = None
    if os.path.exists(_CRYPTO_SNAPSHOT_PATH):
        try:
            with open(_CRYPTO_SNAPSHOT_PATH, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict) and isinstance(loaded.get("factors"), list):
                snap = loaded
        except (ValueError, OSError):
            snap = None        # corrupt artifact -> honest empty, never crash the read-only API
    present = snap is not None
    summary = snap.get("summary", _CRYPTO_EMPTY_SUMMARY) if present else _CRYPTO_EMPTY_SUMMARY
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT",
        "crypto_mode": "READ_ONLY_OFFLINE_SNAPSHOT",
        "snapshot_present": present,
        "generated_at": snap.get("generated_at") if present else None,
        "generated_at_iso": snap.get("generated_at_iso", "") if present else "",
        "dataset_version": snap.get("dataset_version", "") if present else "",
        "meta": snap.get("meta", _CRYPTO_EMPTY_META) if present else _CRYPTO_EMPTY_META,
        "summary": summary,
        "factors": snap.get("factors", []) if present else [],
        # out-of-sample (no-peek walk-forward) validation block, merged by
        # tools/crypto_oos_validation.py; None until that has been run.
        "oos": snap.get("oos") if present else None,
        # de-redundant best set (incremental-IR forward selection); None until a GP/evo run
        # that emits it has been written.
        "synergistic_set": snap.get("synergistic_set") if present else None,
        # top-factor diagnostics for the factor-factory dashboard: cost-stress curve +
        # regime split (merged by tools/crypto_daily_research.py); None until a daily run.
        "cost_stress": snap.get("cost_stress") if present else None,
        "regime": snap.get("regime") if present else None,
        "safety": {
            "network_in_services": False,
            "fetch_path": "OUT_OF_GATE tools/coingecko_fetch.py",
            "auto_approved": summary.get("auto_approved", 0),   # containment: must stay 0
            "agent_can_reach_approved": False,
            "live_trading": "DISALLOWED",
        },
    }


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "healthy", "service": "research-console-api", "mode": "READ_ONLY"}


@app.get("/api/v1/console/overview")
async def overview() -> Dict:
    return _overview_payload()


@app.get("/api/v1/console/agents")
async def agents_dashboard() -> Dict:
    payload = _agents_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/events")
async def events_dashboard() -> Dict:
    payload = _events_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


# ---- research-ops console: dynamic, registry-backed run list + generic detail (read-only) -----------
# These serve REAL research-run results from the run_registry (the same records the research_runner
# writes). Read-only like every other endpoint; the registry is seeded from existing cached result
# JSONs on first read using a FIXED timestamp (deterministic — no wall-clock in this read path).
_RUNS_DB = os.path.join("data", "cache", "research_runs.db")
_RUNS_INDEX_TS = 1_782_700_000


def _runs_registry():
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.indexer import index_existing
    store = ResearchRunStore(_RUNS_DB)
    reg = RunRegistry(store)
    if not store.list(limit=1):                                  # empty -> seed from existing results
        index_existing(os.path.join("data", "cache"), reg, now=_RUNS_INDEX_TS)
    return reg


def _run_summary(r) -> Dict:
    metrics = r.metrics or {}
    hk = next(iter(metrics), None)                               # first metric = headline (set by adapters)
    return {"run_id": r.run_id, "kind": r.kind, "title": r.title, "status": r.status,
            "created_ts": r.created_ts,
            "headline": {"label": hk, "value": metrics.get(hk)},
            "verdict": r.verdict,
            # history retention at a glance: which params produced this run + the code (git sha) it ran on
            "params": r.params or {}, "code_version": r.code_version}


@app.get("/api/v1/console/research-runs")
async def research_runs(kind: str = Query(None)) -> Dict:
    reg = _runs_registry()
    return {"source": "BACKEND_COMPUTED_SNAPSHOT", "read_only": True, "api_version": "0.1.0",
            "data_provenance": "RESEARCH_RUN_REGISTRY",
            "runs": [_run_summary(r) for r in reg.list(kind=kind)]}


@app.get("/api/v1/console/research-run")
async def research_run_detail(run_id: str = Query(...)) -> Dict:
    reg = _runs_registry()
    r = reg.get(run_id)
    return {"source": "BACKEND_COMPUTED_SNAPSHOT", "read_only": True, "api_version": "0.1.0",
            "data_provenance": "RESEARCH_RUN_REGISTRY",
            "run": r.to_dict() if r else None}


def _latest_done_run(reg, kind: str):
    """Latest DONE run of a kind (by created_ts), or None. run_id is a content hash (re-runs mint a new id),
    so we select by kind+recency — robust to re-running research — instead of pinning a fragile id."""
    try:
        cands = [r for r in reg.list(kind=kind) if r.status == "done"]
    except Exception:
        return None
    if not cands:
        return None
    return sorted(cands, key=lambda r: r.created_ts or 0, reverse=True)[0]


def _backtest_results_payload() -> Dict:
    """Aggregate the REAL run_registry results that back the Backtest Results page: one done run per role
    (carry / combo / oi_research / survivorship) plus the factory runs for the honest discovery map. Each
    block carries the run's real metrics + series + verdict. Fail-closed: provenance SYNTHETIC_DETERMINISTIC
    and all blocks None when the registry is absent (CI / fresh checkout) — the page then renders its
    OBSERVED constants. The few page figures with no run field (Carry per-year, R² vs carry, the 2.09
    headline) stay OBSERVED on the frontend regardless; we never fabricate a run field for them here."""
    def block(r):
        if r is None:
            return None
        return {"run_id": r.run_id, "kind": r.kind, "verdict": r.verdict or "",
                "status": r.status, "metrics": r.metrics or {}, "series": r.series or {}}
    try:
        reg = _runs_registry()
        carry = _latest_done_run(reg, "carry_strategy")
        combo = _latest_done_run(reg, "combo_book")
        oi = _latest_done_run(reg, "oi_research")
        surv = _latest_done_run(reg, "survivorship")
        floop = _latest_done_run(reg, "factory_loop")
        forth = _latest_done_run(reg, "factory_orthogonal")
    except Exception:
        carry = combo = oi = surv = floop = forth = None
    have_real = any(x is not None for x in (carry, combo, oi, surv))
    return {
        "results_mode": "READ_ONLY_BACKTEST_RESULTS",
        "data_provenance": "RESEARCH_RUN_REGISTRY" if have_real else "SYNTHETIC_DETERMINISTIC",
        "carry": block(carry),
        "combo": block(combo),
        "oi_research": block(oi),
        "survivorship": block(surv),
        "factory": {"loop": block(floop), "orthogonal": block(forth)},
    }


@app.get("/api/v1/console/backtest-results")
async def backtest_results() -> Dict:
    payload = _backtest_results_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/phase-3a-readiness")
async def phase3a_readiness() -> Dict:
    payload = _phase3a_readiness_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/architecture")
async def architecture() -> Dict:
    payload = _architecture_payload()
    # The module/maturity map is a CURATED roadmap; the `inventory` block is REAL repo introspection.
    # Mark REAL when the inventory actually populated (it always does outside FS errors), else honest synthetic.
    real_inv = bool(payload.get("inventory", {}).get("domains"))
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_REPO_INVENTORY" if real_inv else "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/experiments")
async def experiments() -> Dict:
    payload = _experiments_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/data-catalog")
async def data_catalog() -> Dict:
    payload = _data_catalog_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/backtest-explainability")
async def backtest_explainability(alpha_id: str = Query("alpha-1")) -> Dict:
    payload = _backtest_explainability_payload(alpha_id)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/scenario-stress")
async def scenario_stress(alpha_id: str = Query("alpha-1")) -> Dict:
    payload = _scenario_stress_payload(alpha_id)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/model-risk-review")
async def model_risk_review() -> Dict:
    payload = _model_risk_review_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/evidence-ledger")
async def evidence_ledger() -> Dict:
    payload = _evidence_ledger_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/reviewer-workflow")
async def reviewer_workflow() -> Dict:
    payload = _reviewer_workflow_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/access-control")
async def access_control() -> Dict:
    payload = _access_control_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/lifecycle-monitor")
async def lifecycle_monitor() -> Dict:
    payload = _lifecycle_monitor_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/monitoring-drift")
async def monitoring_drift() -> Dict:
    payload = _monitoring_drift_payload()
    # ALPHA_DECAY's baseline + degradation verdict come from the REAL degradation_monitor on real factor
    # backtests when the snapshot is present; the live-drift side stays synthetic (L2 has no live/paper feed).
    # Honest partial marker — never claim fully real.
    prov = "PARTIAL_ALPHA_DECAY_REAL" if _load_real_factors() else "SYNTHETIC_DETERMINISTIC"
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": prov,
        **payload,
    }


@app.get("/api/v1/console/incident-response")
async def incident_response() -> Dict:
    payload = _incident_response_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/promotion-control")
async def promotion_control() -> Dict:
    payload = _promotion_control_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/release-committee")
async def release_committee() -> Dict:
    payload = _release_committee_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/alpha-factory")
async def alpha_factory() -> Dict:
    candidates = _alpha_candidates()
    screening = _alpha_screening_payload(candidates)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": _input_provenance(),
        "summary": {
            "candidates": len(candidates),
            "avg_ic": round(statistics.mean(alpha["ic"] for alpha in candidates), 4),
            "avg_sharpe": round(statistics.mean(alpha["sharpe"] for alpha in candidates), 3),
            "active": sum(1 for alpha in candidates if alpha["lifecycle_state"] == "ACTIVE"),
        },
        "candidates": candidates,
        "screening": screening,
        "correlation_matrix": _alpha_correlation_matrix(candidates),
        "lineage": [item for item in LINEAGE if item["layer"] in {"Alpha", "Paper"}],
    }


@app.get("/api/v1/console/risk-dashboard")
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
        "api_version": "0.1.0", "data_provenance": _input_provenance(),
        "selected_alpha_id": alpha["candidate_id"],
        "alpha": alpha,
        "primary_report": primary_report,
        "reports": [primary_report, control_report],
        "lineage": [item for item in LINEAGE if item["layer"] == "Risk"],
    }


@app.get("/api/v1/console/validation")
async def validation_dashboard() -> Dict:
    reports = _validation_reports()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "validation_backend": "REAL_LEAKAGE_DETECTOR_v1",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": _input_provenance(),
        "selected_report": reports[0],
        "reports": reports,
        "lineage": [item for item in LINEAGE if item["layer"] == "Alpha"],
    }


@app.get("/api/v1/console/paper-trading")
async def paper_trading(alpha_id: str = Query("alpha-1")) -> Dict:
    run = _paper_run(alpha_id)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": _input_provenance(),
        "selected_alpha_id": run["alpha_id"],   # resolved id — matches the rendered data
        "run": run,
        "lineage": [item for item in LINEAGE if item["layer"] == "Paper"],
    }


@app.get("/api/v1/console/portfolio")
async def portfolio_dashboard() -> Dict:
    payload = _portfolio_simulation()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": _input_provenance(),
        **payload,
        "lineage": [item for item in LINEAGE if item["layer"] in {"Alpha", "Paper", "Risk"}],
    }


@app.get("/api/v1/console/safety-primitives")
async def safety_primitives() -> Dict:
    payload = _safety_primitives_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "REAL_SAFETY_PRIMITIVES",
        **payload,
    }


@app.get("/api/v1/console/population-optimizer")
async def population_optimizer() -> Dict:
    payload = _population_optimizer_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": _input_provenance(),
        **payload,
        "lineage": [item for item in LINEAGE if item["layer"] == "Alpha"],
    }


@app.get("/api/v1/console/dsl-explorer")
async def dsl_explorer() -> Dict:
    payload = _dsl_explorer_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
        "lineage": [item for item in LINEAGE if item["layer"] == "Alpha"],
    }


@app.get("/api/v1/console/alpha-zoo")
async def alpha_zoo() -> Dict:
    payload = _alpha_zoo_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": _input_provenance(),
        **payload,
        "lineage": [item for item in LINEAGE if item["layer"] == "Alpha"],
    }


@app.get("/api/v1/console/alpha-graph")
async def alpha_graph() -> Dict:
    payload = _alpha_graph_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": _input_provenance(),
        **payload,
        "lineage": [item for item in LINEAGE if item["layer"] == "Alpha"],
    }


@app.get("/api/v1/console/system-health")
async def system_health() -> Dict:
    payload = _system_health_payload()
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0", "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/system-completeness")
async def system_completeness() -> Dict:
    payload = _system_completeness_payload()
    real_inv = bool(payload.get("inventory", {}).get("domains"))  # curated map + REAL repo introspection
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_REPO_INVENTORY" if real_inv else "SYNTHETIC_DETERMINISTIC",
        **payload,
    }


@app.get("/api/v1/console/computation-lab")
async def computation_lab(
    alpha_id: str = Query("alpha-1"),
    cost_bps: float = Query(18.0, ge=0.0, le=100.0),
    stress: float = Query(1.2, ge=1.0, le=3.0),
) -> Dict:
    idx = _alpha_index(alpha_id)
    alpha = dict(ALPHAS[idx])
    factors = _factor_values(idx)
    returns = _forward_returns(factors, idx)
    ic = compute_ic(factors, returns)
    equity_points = _equity_curve(returns)
    drawdowns = _drawdown_curve(equity_points)
    raw_returns = [(equity_points[i]["equity"] - equity_points[i - 1]["equity"]) / equity_points[i - 1]["equity"] for i in range(1, len(equity_points))]
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

    alpha.update({
        "ic": round(ic["ic_mean"] or 0.0, 4),
        "rank_ic": round(ic["rank_ic"] or 0.0, 4),
        "icir": round(ic["icir"] or 0.0, 4),
        "sharpe": round(gross_sharpe, 3),
        "composite_score": round(max(0.0, min(1.0, (gross_sharpe / 3.0) * 0.35 + (ic["ic_mean"] or 0.0) * 3.0)), 3),
    })

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
        "api_version": "0.1.0", "data_provenance": _input_provenance(),
        "selected_alpha_id": alpha["candidate_id"],
        "alpha_candidates": ALPHAS,
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
        "lineage": LINEAGE,
    }


@app.get("/api/v1/console/crypto-live")
async def crypto_live() -> Dict:
    return _crypto_live_payload()


def _fx_research_payload() -> Dict:
    """Serve the persisted REAL FX research + tradable-gauntlet snapshots (generated OUT OF GATE by
    tools/fx_research.py + tools/fx_gauntlet.py from KEYLESS ECB reference rates via Frankfurter). Read-only;
    NEVER fetches (the no-network-import ban inside services/ is machine-enforced). Fail-closed: when a
    snapshot is absent (data/cache is gitignored — CI / fresh checkout) or corrupt it returns a well-formed
    EMPTY payload + SYNTHETIC provenance, so the page shows an honest 'no FX snapshot yet' state.

    HONESTY: the full-universe OOS can be inflated by pegged / pre-Euro-legacy / non-executable currencies;
    the TRADABLE-universe + cost gauntlet is the arbiter. verdict reflects the gauntlet, not the raw +OOS."""
    def _load(path):
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    d = json.load(fh)
                return d if isinstance(d, dict) else None
            except (ValueError, OSError):
                return None
        return None

    full = _load(_FX_RESEARCH_SNAPSHOT)
    gaunt = _load(_FX_GAUNTLET_SNAPSHOT)
    present = full is not None or gaunt is not None
    sweep = (gaunt or {}).get("cost_sweep") or []
    # tradable gauntlet verdict: refuted iff every assessed cost level has non-positive OOS Sharpe.
    oos_vals = [c.get("oos_mean_sharpe") for c in sweep if isinstance(c.get("oos_mean_sharpe"), (int, float))]
    if not present:
        verdict = "NO_SNAPSHOT"
    elif oos_vals and all(v <= 0 for v in oos_vals):
        verdict = "REFUTED_NO_TRADABLE_EDGE"
    elif oos_vals:
        verdict = "SURVIVES_TRADABLE_GAUNTLET"
    else:
        verdict = "LEAD_UNGATED"
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_FX_ECB_OFFLINE_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": present,
        "verdict": verdict,
        "full_universe": full or {},
        "tradable_gauntlet": gaunt or {},
        "honesty_note": ("ECB daily reference fixings (NOT executable quotes). The full-universe OOS can be "
                         "inflated by pegged / pre-Euro-legacy / capital-controlled currencies; the tradable "
                         "universe + cost sweep is the arbiter. Research-only, no broker/order path."),
    }


@app.get("/api/v1/console/fx-research")
async def fx_research() -> Dict:
    return _fx_research_payload()


def _carry_readiness_payload() -> Dict:
    """Funder-facing carry-readiness summary: the ONE signal that survived the full gauntlet (funding carry),
    packaged across the dimensions a capital allocator asks about — economic basis, design-robustness (simplicity
    ladder), regime-robustness, realistic-cost survival, capacity, crowding decay — PLUS the honest remaining
    deployment gaps. Reads three OUT-OF-GATE snapshots (crypto_carry_strategy / _walkforward / _capacity), all
    generated offline from the local funding store. Read-only, never trades. Fail-closed: missing snapshots ->
    well-formed empty + SYNTHETIC provenance + verdict NO_SNAPSHOT (CI / fresh checkout). HONESTY: surfaces the
    crowding decay (funding premium compressed ~5x since 2020) so the headline Sharpe is never sold as forward."""
    def _load(path):
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    d = json.load(fh)
                return d if isinstance(d, dict) else None
            except (ValueError, OSError):
                return None
        return None

    strat = _load(_CARRY_STRATEGY_SNAPSHOT)
    robust = _load(_CARRY_WALKFWD_SNAPSHOT)
    cap = _load(_CARRY_CAPACITY_SNAPSHOT)
    present = any(x is not None for x in (strat, robust, cap))
    rob_verdict = (robust or {}).get("verdict")
    if not present:
        verdict = "NO_SNAPSHOT"
    elif rob_verdict == "DESIGN_ROBUST":
        verdict = "VALIDATED_NOT_LIVE_READY"          # passed every research gate; deployment gaps remain
    elif rob_verdict in ("COMPOSITE_DEPENDENT", "ROBUST_BUT_RAW_WEAKER"):
        verdict = "PARTIAL_OVERFIT_RISK"
    else:
        verdict = "REFUTED" if rob_verdict else "INCOMPLETE"
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_CARRY_OFFLINE_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": present,
        "verdict": verdict,
        "strategy": strat or {},
        "robustness": robust or {},
        "capacity": cap or {},
        "deployment_gaps": [
            "实盘执行机制未建模:永续空腿强平、对手方/交易所风险、真实资金费结算时点",
            "回撤尾巴未控:最大回撤 25–30%、最差单日约 −8%(需 vol-target 压尾)",
            "溢价衰减:资金费 5 年压缩约 5×,前向预期 < 全历史头条夏普",
            "容量有上限:约 $25M–$250M(取决于冲击假设),超峰值后边际收益转负",
            "拥挤风险:funding carry 是公开拥挤交易,需持续监控溢价",
        ],
        "honesty_note": ("唯一过了全套关卡的真信号(资金费 carry,经济学溢价、非过拟合、扛过 2022 熊市、真实滑点下存活)。"
                         "但头条夏普被肥沃的 2020–21 抬高——溢价已显著衰减,前向预期更低;且未上实盘、回撤尾巴未控。"
                         "研究只读、零下单/券商/凭证路径;L2_DRY_RUN_ONLY,实盘人工专属。"),
    }


@app.get("/api/v1/console/carry-readiness")
async def carry_readiness() -> Dict:
    return _carry_readiness_payload()


def _dry_run_payload() -> Dict:
    """End-to-end DRY-RUN pipeline runs, surfaced read-only: the pre-trade gate as a RUNTIME chokepoint.

    Each DryRunRun is one continuous flow — 信号/权重 → 意图 → 逐意图过门(fail-closed,kill-switch latched→全拒)
    → 干跑适配器纸面成交(绝不真发)→ 平均成本对账 → 哈希链审计. Reads the append-only DryRunStore
    (`data/cache/dry_run.db`, written OUT-OF-GATE by `tools/dry_run_carry.py`). Read-only, never trades.
    Fail-closed: no db / unreadable -> well-formed empty + SYNTHETIC provenance + verdict NO_RUN (CI / fresh
    checkout). This is the proof the gate is wired into a runtime path, not tested dead code."""
    runs = []
    present = os.path.exists(_DRY_RUN_DB)
    if present:
        try:
            runs = DryRunStore(_DRY_RUN_DB, create=False).all_runs()
        except Exception:
            present, runs = False, []
    has = bool(present and runs)
    latest = runs[-1] if runs else {}
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_DRY_RUN_OFFLINE_SNAPSHOT" if has else "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": has,
        "verdict": "DRY_RUN_OK" if has else "NO_RUN",
        "n_runs": len(runs),
        "latest": latest,
        "runs": runs,
        "safety_note": ("端到端干跑:预交易风控门是运行时 chokepoint(fail-closed,kill-switch latched 全拒),"
                        "干跑适配器纸面成交、绝不真发,平均成本对账恒等式残差≈0,哈希链审计。只读、零下单/券商/凭证;"
                        "L2_DRY_RUN_ONLY。实盘 = 同接口换真券商适配器 + 人工批准 = 非目标。"),
    }


@app.get("/api/v1/console/dry-run")
async def dry_run() -> Dict:
    return _dry_run_payload()


def _ic_panel_payload() -> Dict:
    """截面 IC 诊断面板:读 tools/ic_panel_research.py 写的快照。只读、fail-closed。
    诚实标注:这是初筛诊断,不是晋级门禁——真正的门禁是 evaluator 的 DSR/PBO/walk-forward。"""
    present, rows, universes = False, [], {}
    if os.path.exists(_IC_PANEL_SNAPSHOT):
        try:
            with open(_IC_PANEL_SNAPSHOT, encoding="utf-8") as fh:
                snap = json.load(fh)
            rows, universes = snap.get("rows", []), snap.get("universes", {})
            present = bool(rows)
        except Exception:
            present, rows, universes = False, [], {}
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_IC_PANEL_OFFLINE_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": present,
        "verdict": "IC_PANEL_OK" if present else "NO_SNAPSHOT",
        "rows": rows,
        "universes": universes,
        "honesty_note": ("截面 IC 面板是初筛诊断工具,不是晋级门禁——真正的门禁是 services/alpha/evaluator "
                         "的 DSR/PBO/walk-forward。面板结果不驱动任何自动晋级。"),
    }


@app.get("/api/v1/console/ic-panel")
async def ic_panel() -> Dict:
    return _ic_panel_payload()


def _load_json_snapshot(path: str):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None
    return None


def _research_findings_payload() -> Dict:
    """现实主义/宏观/组合三件研究成果聚合(只读):容量带(RE-1)、宏观 regime 条件化(FRED)、组合分散(carry+crowd)。
    读 tools 写的离线快照,fail-closed 到 SYNTHETIC。诚实标注:诊断非门禁,gross/regime 口径按各快照自带 note。"""
    capacity = _load_json_snapshot(_CAPACITY_SNAPSHOT)
    macro = _load_json_snapshot(_MACRO_REGIME_SNAPSHOT)
    combo = _load_json_snapshot(_COMBO_BOOK_SNAPSHOT)
    present = bool(capacity or macro or combo)
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_RESEARCH_FINDINGS" if present else "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": present,
        "verdict": "FINDINGS_OK" if present else "NO_SNAPSHOT",
        "capacity": capacity or {},
        "macro_regime": macro or {},
        "combo": combo or {},
        "honesty_note": ("研究诊断聚合,非晋级门禁:容量为 UNCALIBRATED_PRINCIPLED 区间(冲击系数未校准)、"
                         "regime 标签为全样本分位(回顾性非 PIT 择时)、组合为 gross 收益流层面(RELATIVE 比较)。"
                         "数字为准,均不驱动自动晋级/下单。"),
    }


@app.get("/api/v1/console/research-findings")
async def research_findings() -> Dict:
    return _research_findings_payload()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8400)
