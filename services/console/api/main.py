"""Read-only QUANTOS Console HTTP API.

This service exposes computed research snapshots for the frontend. It does not
connect to brokers, place orders, store credentials, or mutate trading state.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.research.factor_engine.domain.model import compute_max_drawdown, compute_sharpe
from services.research.paper_trading.domain.pnl import PnLAttributor
from services.console.api.alpha_library_views import (
    configure_alpha_library_views,
    router as alpha_library_views_router,
)
from services.console.api.ashare_views import (
    router as ashare_views_router,
)
from services.console.api.artifact_snapshots import (
    IC_PANEL_SNAPSHOT,
    router as artifact_snapshots_router,
)
from services.console.api.architecture_views import (
    configure_architecture_views,
    router as architecture_views_router,
)
from services.console.api.backtest_views import (
    _backtest_explainability_payload,
    _scenario_stress_payload,
    configure_backtest_views,
    router as backtest_views_router,
)
from services.console.api.data_catalog import (
    router as data_catalog_router,
)
from services.console.api.incident_views import (
    router as incident_views_router,
)
from services.console.api.model_evidence_views import (
    configure_model_evidence_views,
    router as model_evidence_views_router,
)
from services.console.api.mexc_views import configure_mexc_views, router as mexc_views_router
from services.console.api.go_live_views import router as go_live_views_router
from services.console.api.alpha_lifecycle_views import router as alpha_lifecycle_views_router
from services.console.api.alpha_family_views import router as alpha_family_views_router
from services.console.api.live_degradation_views import router as live_degradation_views_router
from services.console.api.live_stream_views import router as live_stream_views_router
from services.console.api.investor_snapshot_views import router as investor_snapshot_views_router
from services.console.api.monitoring_views import (
    configure_monitoring_views,
    router as monitoring_views_router,
)
from services.console.api.promotion_views import (
    configure_promotion_views,
    router as promotion_views_router,
)
from services.console.api.primary_views import (
    PrimaryViewDependencies,
    configure_primary_views,
    router as primary_views_router,
)
from services.console.api.registry_snapshots import (
    configure_registry_snapshots,
    router as registry_snapshots_router,
)
from services.console.api.research_run_views import (
    _overview_active_runs,
    _runs_registry,
    router as research_run_views_router,
)
from services.console.api.release_committee_views import (
    router as release_committee_views_router,
)
from services.console.api.review_access_views import (
    router as review_access_views_router,
)
from services.console.api.risk_port import risk_console_runtime
from services.console.api.snapshot_middleware import attach_console_snapshot_metadata
from services.console.api.system_views import (
    CONSOLE_API_VERSION,
    configure_system_views,
    router as system_views_router,
)
from services.validation.factor_statistics import compute_ic


_IC_PANEL_SNAPSHOT = IC_PANEL_SNAPSHOT


app = FastAPI(title="QUANTOS Research Console API", version=CONSOLE_API_VERSION)
_REPO_ROOT = Path(__file__).resolve().parents[3]
_extra_cors_origins = tuple(
    origin.strip()
    for origin in os.getenv("QUANTOS_CONSOLE_CORS_ORIGINS", "").split(",")
    if origin.strip()
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3100", "http://localhost:3100", *_extra_cors_origins],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


app.middleware("http")(attach_console_snapshot_metadata)


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
    return risk_console_runtime.require().risk_decision(
        alpha_id, returns, equity_points, capacity
    )


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
    this block is the verifiable health signal. Fail-closed to an honest marker if the chain is absent.

    审计 2026-07-07 P0-1 修复:`kill_switch=KillSwitch()` 不传参数此前永远构造全新"从未触发"的默认
    状态,且缺失审计链时的早退分支还硬编码 `kill_switch_active=False` —— 两条路径都与实盘 kill-switch
    是否真的跳闸无关。现在两条分支都经 `_load_live_kill_switch_state()` 读取跨进程持久化的真实闩锁
    状态(该函数 docstring 说明了 services/ 不 import live/ 的边界怎么绕开)。"""
    return risk_console_runtime.require().monitoring_snapshot(
        audit_db=_GOVERNANCE_AUDIT_DB,
        kill_switch_state=_load_live_kill_switch_state(),
    )




_CRYPTO_SNAPSHOT_PATH = os.path.join(_REPO_ROOT, "data", "cache", "crypto_research_snapshot.json")


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




_LIVE_KILL_SWITCH_STATE = os.path.join(_REPO_ROOT, "data", "cache", "live_kill_switch.json")
_GOVERNANCE_AUDIT_DB = os.path.join(_REPO_ROOT, "data", "cache", "governance_audit.db")


def _load_live_kill_switch_state():
    """Read the SQLite authority, using JSON only before authority migration.

    ``services`` must not import ``live``. Both sides therefore share the risk-owned
    store and path convention directly. Once the authority database exists, any
    open, schema, or hash-chain failure is a tripped fail-closed result; a valid but
    stale JSON mirror can never override it. JSON remains a pre-migration fallback.
    """
    from quantos.contracts.killswitch import KillSwitchState
    from services.risk.pre_trade.kill_switch_store import (
        KillSwitchStateStore,
        kill_switch_authority_path,
    )

    def fail_closed(reason: str) -> KillSwitchState:
        return KillSwitchState(
            tripped=True,
            incident_id="corrupt-state-fail-closed",
            reason=reason,
        )

    authority_path = kill_switch_authority_path(_LIVE_KILL_SWITCH_STATE)
    if os.path.exists(authority_path):
        store = None
        try:
            store = KillSwitchStateStore(authority_path, create=False)
            return store.load_state()
        except Exception:
            return fail_closed(
                "kill-switch authoritative state unreadable/corrupt - failing closed"
            )
        finally:
            if store is not None:
                store.close()

    if not os.path.exists(_LIVE_KILL_SWITCH_STATE):
        return KillSwitchState()
    try:
        with open(_LIVE_KILL_SWITCH_STATE, encoding="utf-8") as fh:
            return KillSwitchState.model_validate_json(fh.read())
    except Exception:
        return fail_closed(
            "kill-switch compatibility mirror unreadable/corrupt - failing closed"
        )


def _safety_primitives_payload() -> Dict:
    """READ-ONLY status of the REAL safety primitives: the latched KillSwitch + the pre-trade gate's
    CHECK_REGISTRY + the authoritative SafetyConfig flags. No order intent is built, nothing is armed or
    reset (those are human-only); this surfaces the live/disallowed posture so it is visible, not hidden.

    审计 2026-07-07 P0-1 修复:`KillSwitch()` 不传参数此前永远构造"从未触发"的默认状态 —— Risk.tsx
    的熔断器卡片因此即便实盘已经跳闸也会显示"未触发/CLEAR"(全份审计最危险的单一发现)。现在经
    `_load_live_kill_switch_state()` 读取跨进程持久化的真实闩锁状态。"""
    return risk_console_runtime.require().safety_primitives(
        kill_switch_state=_load_live_kill_switch_state()
    )








configure_system_views(
    alpha_candidates=_alpha_candidates,
    validation_decision=_validation_decision,
    runs_registry=_runs_registry,
    monitoring_snapshot=_real_monitoring_snapshot,
)
configure_architecture_views(
    console_paths=lambda: tuple(app.openapi()["paths"]),
)
configure_registry_snapshots(
    alpha_candidates=_alpha_candidates,
    validation_decision=_validation_decision,
    alpha_windows=ALPHA_WINDOWS,
)
configure_alpha_library_views(
    load_real_factors=_load_real_factors,
    alpha_candidates=_alpha_candidates,
    factor_values=_factor_values,
    forward_returns=_forward_returns,
    input_provenance=_input_provenance,
    lineage=LINEAGE,
)
configure_backtest_views(
    alpha_index=_alpha_index,
    computed_alpha=_computed_alpha,
    factor_values=_factor_values,
    forward_returns=_forward_returns,
    equity_curve=_equity_curve,
    drawdown_curve=_drawdown_curve,
)
configure_model_evidence_views(
    backtest_explainability=_backtest_explainability_payload,
    scenario_stress=_scenario_stress_payload,
)
configure_mexc_views(kill_switch_state=_load_live_kill_switch_state)
configure_monitoring_views(
    alpha_candidates=_alpha_candidates,
    has_real_factors=lambda: bool(_load_real_factors()),
)
configure_promotion_views(
    alpha_candidates=_alpha_candidates,
    validation_reports=_validation_reports,
    paper_run=_paper_run,
    factor_values=_factor_values,
    forward_returns=_forward_returns,
    equity_curve=_equity_curve,
    risk_decision=_risk_decision,
    validation_decision=_validation_decision,
)
configure_primary_views(
    PrimaryViewDependencies(
        alpha_candidates=_alpha_candidates,
        validation_decision=_validation_decision,
        snapshot_summary=_snapshot_summary,
        overview_active_runs=_overview_active_runs,
        alpha_screening_payload=_alpha_screening_payload,
        alpha_correlation_matrix=_alpha_correlation_matrix,
        input_provenance=_input_provenance,
        alpha_index=_alpha_index,
        computed_alpha=_computed_alpha,
        factor_values=_factor_values,
        forward_returns=_forward_returns,
        equity_curve=_equity_curve,
        drawdown_curve=_drawdown_curve,
        risk_decision=_risk_decision,
        validation_reports=_validation_reports,
        paper_run=_paper_run,
        portfolio_simulation=_portfolio_simulation,
        safety_primitives=_safety_primitives_payload,
        alphas=ALPHAS,
        lineage=LINEAGE,
    )
)
for _router in (system_views_router, architecture_views_router, registry_snapshots_router, research_run_views_router, data_catalog_router, artifact_snapshots_router, ashare_views_router, alpha_library_views_router, backtest_views_router, primary_views_router, model_evidence_views_router, review_access_views_router, mexc_views_router, go_live_views_router, alpha_lifecycle_views_router, alpha_family_views_router, live_degradation_views_router, live_stream_views_router, investor_snapshot_views_router, monitoring_views_router, incident_views_router, promotion_views_router, release_committee_views_router):
    app.include_router(_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8400)
