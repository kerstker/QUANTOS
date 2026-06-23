"""Portfolio construction (Phase 7, step 3) — ranked alpha streams -> RebalancePlan.

The thin orchestrator: covariance -> shrink -> risk-parity weights -> concentration
cap -> cost-aware rebalance vs the prior book -> a RebalancePlan whose targets are
budget weights per alpha_id, with realized turnover. Deterministic; no live surface.
"""
from __future__ import annotations

from typing import Optional, Sequence

from quantos.contracts.portfolio import RebalancePlan, TargetPosition

from .config import PortfolioConfig
from .optimizer import cap_weights, cost_aware_rebalance, risk_parity_weights
from .risk_model import covariance_matrix, percent_risk_contributions, shrink_covariance


def build_portfolio(
    plan_id: str,
    portfolio_id: str,
    returns_by_alpha: dict[str, Sequence[float]],
    *,
    config: Optional[PortfolioConfig] = None,
    prev_weights: Optional[dict[str, float]] = None,
) -> RebalancePlan:
    config = config or PortfolioConfig()
    ids, cov = covariance_matrix(returns_by_alpha, min_overlap=config.min_overlap)
    cov = shrink_covariance(cov, intensity=config.shrink_intensity)
    weights = cap_weights(risk_parity_weights(cov), max_weight=config.max_weight)
    target = {ids[i]: weights[i] for i in range(len(ids))}

    if prev_weights is not None:
        target, turnover = cost_aware_rebalance(prev_weights, target, no_trade_band=config.no_trade_band)
    else:
        turnover = sum(target.values())   # initial build from a flat book

    targets = tuple(
        TargetPosition(instrument_id=aid, target_weight=round(target[aid], 10))
        for aid in sorted(target)
    )
    return RebalancePlan(plan_id=plan_id, portfolio_id=portfolio_id,
                         targets=targets, turnover=round(max(turnover, 0.0), 10))


def risk_budget_report(returns_by_alpha: dict[str, Sequence[float]],
                       weights: dict[str, float],
                       config: Optional[PortfolioConfig] = None) -> dict[str, float]:
    """Percent risk contribution per alpha for a given weighting (explainability)."""
    config = config or PortfolioConfig()
    ids, cov = covariance_matrix(returns_by_alpha, min_overlap=config.min_overlap)
    cov = shrink_covariance(cov, intensity=config.shrink_intensity)
    w = [weights.get(aid, 0.0) for aid in ids]
    prc = percent_risk_contributions(w, cov)
    return {ids[i]: prc[i] for i in range(len(ids))}
