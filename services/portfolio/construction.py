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
from .risk_model import (covariance_matrix, percent_risk_contributions, shrink_covariance,
                         marginal_risk_contributions)

from quantos.contracts.factor import FactorRiskModel
from .constrained_optimizer import min_variance_weights
from .constraints import PortfolioConstraints
from .factor_risk import risk_decomposition, total_covariance


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


def return_attribution(returns_by_alpha: dict[str, Sequence[float]],
                       weights: dict[str, float], *, horizon: Optional[int] = None) -> dict:
    """Realized return attribution: each alpha's contribution = w_i × R_i, where R_i is
    the SIMPLE cumulative return (sum of period returns) over the trailing `horizon`.
    Simple (not compound) is chosen so contributions are exactly additive: Σ w_i·R_i
    equals the portfolio's cumulative simple return. Fail-closed: total==0 → pct all 0;
    a weighted alpha with no return series contributes 0 (no crash)."""
    contribution: dict[str, float] = {}
    for aid, w in weights.items():
        series = returns_by_alpha.get(aid, [])
        r = sum(series[-horizon:] if horizon is not None else series)
        contribution[aid] = w * r
    total = sum(contribution.values())
    pct = {aid: (c / total if total != 0 else 0.0) for aid, c in contribution.items()}
    return {"contribution": contribution, "total": total, "pct": pct}


def attribution_report(returns_by_alpha: dict[str, Sequence[float]],
                       weights: dict[str, float],
                       config: Optional[PortfolioConfig] = None) -> dict:
    """Complete risk + return attribution for a weighting: per-alpha realized return
    contribution (and %), percent risk contribution, and marginal risk. Risk side uses
    the shrunk sample covariance (fail-closed below min_overlap)."""
    config = config or PortfolioConfig()
    ids, cov = covariance_matrix(returns_by_alpha, min_overlap=config.min_overlap)
    cov = shrink_covariance(cov, intensity=config.shrink_intensity)
    w = [weights.get(aid, 0.0) for aid in ids]
    prc = percent_risk_contributions(w, cov)
    mrc = marginal_risk_contributions(w, cov)
    ret = return_attribution(returns_by_alpha, weights)
    return {
        "return_contribution": ret["contribution"],
        "pct_return": ret["pct"],
        "risk_contribution_pct": {ids[i]: prc[i] for i in range(len(ids))},
        "marginal_risk": {ids[i]: mrc[i] for i in range(len(ids))},
    }


# --- factor risk model + constrained optimizer (audit roadmap) ---------------

def build_factor_portfolio(plan_id: str, portfolio_id: str, model: FactorRiskModel,
                           alpha_ids, constraints: PortfolioConstraints, *,
                           prev_weights: Optional[dict] = None,
                           config: Optional[PortfolioConfig] = None) -> RebalancePlan:
    """Constrained minimum-variance plan over a factor risk model. Long-short:
    a budget-0 / factor-neutral book yields negative target weights. Existing
    build_portfolio is unchanged."""
    cfg = config or PortfolioConfig()
    ids = list(alpha_ids)
    cov = total_covariance(model, ids)
    B, _ = model.aligned(ids)
    w = min_variance_weights(cov, ids, constraints, loadings=B, factor_ids=list(model.factor_ids))
    target = {ids[i]: w[i] for i in range(len(ids))}
    if prev_weights is not None:
        target, turnover = cost_aware_rebalance(prev_weights, target, no_trade_band=cfg.no_trade_band)
    else:
        turnover = sum(abs(v) for v in target.values())
    return RebalancePlan(
        plan_id=plan_id, portfolio_id=portfolio_id,
        targets=tuple(TargetPosition(instrument_id=a, target_weight=round(target[a], 10))
                      for a in ids if a in target),
        turnover=round(max(turnover, 0.0), 10),
    )


def factor_risk_report(model: FactorRiskModel, alpha_ids, weights) -> dict:
    """Factor-vs-specific variance decomposition + exposures for a weighting."""
    return risk_decomposition(list(weights), model, list(alpha_ids))
