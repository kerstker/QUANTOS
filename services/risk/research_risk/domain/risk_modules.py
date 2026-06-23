"""Research risk modules.

Each engine computes one report used by RiskApprovalGate. The module is
deterministic and has no trading side effects.
"""
from __future__ import annotations

import math
import statistics
from itertools import combinations
from typing import Dict, Iterable, List

from .models import (
    CapacityRiskReport,
    ConcentrationRiskReport,
    CorrelationRiskReport,
    DrawdownRiskReport,
    LiquidityRiskReport,
    MarketRiskReport,
    ModelAlphaRiskReport,
    PortfolioStressReport,
    RegimeRiskReport,
    RiskConfig,
)


def _clean(values: Iterable[float]) -> List[float]:
    return [float(value) for value in values if value is not None and math.isfinite(float(value))]


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(math.floor((len(ordered) - 1) * pct))))
    return ordered[index]


def _corr(left: List[float], right: List[float]) -> float:
    n = min(len(left), len(right))
    if n < 2:
        return 0.0
    x = left[:n]
    y = right[:n]
    mx = statistics.fmean(x)
    my = statistics.fmean(y)
    vx = sum((v - mx) ** 2 for v in x)
    vy = sum((v - my) ** 2 for v in y)
    if vx <= 0 or vy <= 0:
        return 0.0
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return cov / math.sqrt(vx * vy)


class MarketRiskEngine:
    def evaluate(self, returns: List[float], config: RiskConfig) -> MarketRiskReport:
        series = _clean(returns)
        if not series:
            return MarketRiskReport(passed=False)
        volatility = statistics.pstdev(series) * math.sqrt(252) if len(series) > 1 else 0.0
        var_95 = abs(min(0.0, _percentile(series, 0.05)))
        tail = [value for value in series if value <= _percentile(series, 0.05)]
        cvar_95 = abs(statistics.fmean(tail)) if tail else 0.0
        downside = [min(0.0, value) for value in series]
        downside_volatility = statistics.pstdev(downside) * math.sqrt(252) if len(downside) > 1 else 0.0
        tail_loss_99 = abs(min(0.0, _percentile(series, 0.01)))
        max_daily_loss = abs(min(0.0, min(series)))
        passed = (
            volatility <= config.max_portfolio_volatility
            and var_95 <= config.max_var_95
            and cvar_95 <= config.max_cvar_95
        )
        return MarketRiskReport(
            passed=passed,
            volatility=volatility,
            var_95=var_95,
            cvar_95=cvar_95,
            max_daily_loss=max_daily_loss,
            downside_volatility=downside_volatility,
            tail_loss_99=tail_loss_99,
        )


class LiquidityRiskEngine:
    def evaluate(
        self,
        weights: Dict[str, float],
        capacity_cny: Dict[str, float],
        daily_volume_cny: float,
        config: RiskConfig,
    ) -> LiquidityRiskReport:
        if not weights:
            return LiquidityRiskReport(passed=False)   # no book -> not evaluable, fail closed
        gross = sum(abs(value) for value in weights.values())
        min_capacity = min(capacity_cny.values()) if capacity_cny else 0.0
        avg_daily_volume_usage = gross / max(daily_volume_cny / 100_000_000, 1.0)
        participation_rate = gross / max(len(weights), 1)
        liquidation_days = int(math.ceil((gross * 100_000_000) / max(min_capacity, 1.0))) if gross else 0
        cost_to_liquidate_bps = participation_rate * 100
        liquidity_stress_score = avg_daily_volume_usage + participation_rate + liquidation_days / 100
        passed = (
            avg_daily_volume_usage <= config.max_liquidity_usage
            and participation_rate <= config.max_participation_rate
            and liquidation_days <= config.max_liquidation_days
        )
        return LiquidityRiskReport(
            passed=passed,
            avg_daily_volume_usage=avg_daily_volume_usage,
            participation_rate=participation_rate,
            liquidation_days=liquidation_days,
            cost_to_liquidate_bps=cost_to_liquidate_bps,
            liquidity_stress_score=liquidity_stress_score,
        )


class ConcentrationRiskEngine:
    def evaluate(self, weights: Dict[str, float], clusters, config: RiskConfig) -> ConcentrationRiskReport:
        if not weights:
            return ConcentrationRiskReport(passed=False)   # no book -> fail closed
        abs_weights = [abs(value) for value in weights.values()]
        hhi = sum(value * value for value in abs_weights)
        effective_n = 1 / hhi if hhi > 0 else 0.0
        max_single = max(abs_weights) if abs_weights else 0.0
        max_cluster = max_single
        passed = (
            max_single <= config.max_single_alpha_weight
            and max_cluster <= config.max_cluster_weight
            and effective_n >= config.min_effective_number_of_bets
        )
        return ConcentrationRiskReport(
            passed=passed,
            hhi=hhi,
            effective_n=effective_n,
            max_single_alpha=max_single,
            max_cluster=max_cluster,
        )


class CorrelationRiskEngine:
    def evaluate(self, alpha_returns: Dict[str, List[float]], config: RiskConfig) -> CorrelationRiskReport:
        if len(alpha_returns) < 2:
            return CorrelationRiskReport(passed=False)   # <2 alphas -> not evaluable, fail closed
        pairs = [_corr(left, right) for left, right in combinations(alpha_returns.values(), 2)]
        abs_pairs = [abs(value) for value in pairs]
        avg_correlation = statistics.fmean(abs_pairs) if abs_pairs else 0.0
        max_pairwise = max(abs_pairs) if abs_pairs else 0.0
        diversification_loss = avg_correlation * max(len(alpha_returns) - 1, 0) / max(len(alpha_returns), 1)
        passed = (
            avg_correlation <= config.max_average_correlation
            and max_pairwise <= config.max_pairwise_correlation
        )
        return CorrelationRiskReport(
            passed=passed,
            avg_correlation=avg_correlation,
            max_pairwise=max_pairwise,
            diversification_loss=diversification_loss,
        )


class DrawdownRiskEngine:
    def evaluate(self, equity_curve: List[float], config: RiskConfig) -> DrawdownRiskReport:
        curve = _clean(equity_curve)
        if not curve:
            return DrawdownRiskReport(passed=False)
        peak = curve[0]
        max_drawdown = 0.0
        current_duration = 0
        max_duration = 0
        drawdowns = []
        for value in curve:
            if value >= peak:
                peak = value
                current_duration = 0
            else:
                current_duration += 1
            drawdown = (peak - value) / peak if peak else 0.0
            drawdowns.append(drawdown)
            max_drawdown = max(max_drawdown, drawdown)
            max_duration = max(max_duration, current_duration)
        average_drawdown = statistics.fmean(drawdowns) if drawdowns else 0.0
        recovery_days = max_duration
        pain_index = sum(drawdowns) / len(drawdowns) if drawdowns else 0.0
        passed = max_drawdown <= config.max_drawdown and max_duration <= config.max_drawdown_duration
        return DrawdownRiskReport(
            passed=passed,
            max_drawdown=max_drawdown,
            average_drawdown=average_drawdown,
            max_duration=max_duration,
            recovery_days=recovery_days,
            pain_index=pain_index,
        )


class RegimeRiskEngine:
    def evaluate(self, returns: List[float], regime_labels: List[str], config: RiskConfig) -> RegimeRiskReport:
        series = _clean(returns)
        if not series:
            return RegimeRiskReport(passed=False)
        labels = regime_labels[: len(series)] or ["default"] * len(series)
        buckets: Dict[str, List[float]] = {}
        for label, value in zip(labels, series):
            buckets.setdefault(label, []).append(value)
        totals = {label: sum(values) for label, values in buckets.items()}
        worst_regime = min(totals, key=totals.get)
        best_regime = max(totals, key=totals.get)
        worst_loss = abs(min(0.0, totals[worst_regime]))
        profitable = sum(1 for value in totals.values() if value > 0)
        return RegimeRiskReport(
            passed=worst_loss <= config.max_worst_regime_loss,
            worst_regime=worst_regime,
            worst_regime_loss=worst_loss,
            best_regime=best_regime,
            regimes_profitable=profitable,
            total_regimes=len(totals),
        )


class StressTestEngine:
    def evaluate(self, returns: List[float], config: RiskConfig) -> PortfolioStressReport:
        series = _clean(returns)
        if not series:
            return PortfolioStressReport(passed=False)   # no returns -> cannot stress, fail closed
        base_loss = abs(min(0.0, sum(value for value in series if value < 0)))
        scenarios = {
            "market_gap": base_loss * 1.2,
            "liquidity_shock": base_loss * 1.5,
            "vol_spike": base_loss * 1.1,
            "correlation_break": base_loss * 1.3,
        }
        worst_scenario = max(scenarios, key=scenarios.get)
        worst_loss = scenarios[worst_scenario] if scenarios else 0.0
        survived = sum(1 for value in scenarios.values() if value <= config.max_stress_loss)
        total = len(scenarios)
        return PortfolioStressReport(
            passed=survived == total,
            worst_scenario=worst_scenario,
            worst_stress_loss=worst_loss,
            scenarios_survived=survived,
            total_scenarios=total,
        )


class CapacityRiskEngine:
    def evaluate(
        self,
        weights: Dict[str, float],
        capacity_cny: Dict[str, float],
        total_aum: float,
        config: RiskConfig,
    ) -> CapacityRiskReport:
        if not weights:
            return CapacityRiskReport(passed=False)   # no book -> fail closed
        usages = {
            key: abs(weight) * total_aum / max(capacity_cny.get(key, config.min_capacity), 1.0)
            for key, weight in weights.items()
        }
        bottleneck = max(usages, key=usages.get) if usages else ""
        capacity_usage = usages.get(bottleneck, 0.0)
        capacity_adjusted_sharpe = max(0.0, 1.0 - capacity_usage)
        return CapacityRiskReport(
            passed=capacity_usage <= config.max_capacity_usage,
            capacity_usage=capacity_usage,
            bottleneck_alpha=bottleneck,
            capacity_adjusted_sharpe=capacity_adjusted_sharpe,
        )


class ModelAlphaRiskEngine:
    def evaluate(self, alpha_metadata: List[Dict], config: RiskConfig) -> ModelAlphaRiskReport:
        if not alpha_metadata:
            return ModelAlphaRiskReport(passed=False)
        ages = [float(item.get("age_days", 0.0)) for item in alpha_metadata]
        validation_conf = [float(item.get("validation_confidence", 0.0)) for item in alpha_metadata]
        paper_conf = [float(item.get("paper_confidence", 0.0)) for item in alpha_metadata]
        decay = sum(1 for item in alpha_metadata if item.get("decay_flag"))
        overfit = sum(1 for item in alpha_metadata if item.get("overfit_flag"))
        leakage = sum(1 for item in alpha_metadata if item.get("leakage_flag"))
        min_validation = min(validation_conf) if validation_conf else 0.0
        min_paper = min(paper_conf) if paper_conf else 0.0
        passed = (
            decay == 0
            and overfit == 0
            and leakage == 0
            and min_validation >= config.min_validation_confidence
            and min_paper >= config.min_paper_confidence
        )
        return ModelAlphaRiskReport(
            passed=passed,
            avg_alpha_age_days=statistics.fmean(ages) if ages else 0.0,
            alphas_with_decay=decay,
            alphas_with_overfitting_flags=overfit,
            alphas_with_leakage_flags=leakage,
            min_validation_confidence=min_validation,
        )
