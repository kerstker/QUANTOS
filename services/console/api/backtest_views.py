"""Read-only backtest explainability and scenario-stress HTTP views."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Callable, Dict, List

from fastapi import APIRouter, Query

from services.console.api.backtest_evidence import scrub_backtest_payload
from services.console.api.common import short_hash as _short_hash
from services.research.factor_engine.domain.model import (
    compute_max_drawdown,
    compute_sharpe,
)


@dataclass(frozen=True)
class BacktestViewDependencies:
    alpha_index: Callable[[str], int]
    computed_alpha: Callable[[int], Dict]
    factor_values: Callable[[int], List[float]]
    forward_returns: Callable[[List[float], int], List[float]]
    equity_curve: Callable[[List[float]], List[Dict[str, float]]]
    drawdown_curve: Callable[[List[Dict[str, float]]], List[Dict[str, float]]]


class BacktestViewRuntime:
    def __init__(self) -> None:
        self._dependencies: BacktestViewDependencies | None = None

    def configure(
        self,
        *,
        alpha_index: Callable[[str], int],
        computed_alpha: Callable[[int], Dict],
        factor_values: Callable[[int], List[float]],
        forward_returns: Callable[[List[float], int], List[float]],
        equity_curve: Callable[[List[float]], List[Dict[str, float]]],
        drawdown_curve: Callable[[List[Dict[str, float]]], List[Dict[str, float]]],
    ) -> None:
        dependencies = BacktestViewDependencies(
            alpha_index=alpha_index,
            computed_alpha=computed_alpha,
            factor_values=factor_values,
            forward_returns=forward_returns,
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
        )
        if self._dependencies is None:
            self._dependencies = dependencies
            return
        if self._dependencies != dependencies:
            raise RuntimeError("backtest view runtime is already configured")

    def require(self) -> BacktestViewDependencies:
        if self._dependencies is None:
            raise RuntimeError("backtest view runtime must be configured by the console composition root")
        return self._dependencies


backtest_view_runtime = BacktestViewRuntime()


def configure_backtest_views(
    *,
    alpha_index: Callable[[str], int],
    computed_alpha: Callable[[int], Dict],
    factor_values: Callable[[int], List[float]],
    forward_returns: Callable[[List[float], int], List[float]],
    equity_curve: Callable[[List[float]], List[Dict[str, float]]],
    drawdown_curve: Callable[[List[Dict[str, float]]], List[Dict[str, float]]],
) -> None:
    backtest_view_runtime.configure(
        alpha_index=alpha_index,
        computed_alpha=computed_alpha,
        factor_values=factor_values,
        forward_returns=forward_returns,
        equity_curve=equity_curve,
        drawdown_curve=drawdown_curve,
    )


def _alpha_index(alpha_id: str) -> int:
    return backtest_view_runtime.require().alpha_index(alpha_id)


def _computed_alpha(idx: int) -> Dict:
    return backtest_view_runtime.require().computed_alpha(idx)


def _factor_values(idx: int) -> List[float]:
    return backtest_view_runtime.require().factor_values(idx)


def _forward_returns(factors: List[float], idx: int) -> List[float]:
    return backtest_view_runtime.require().forward_returns(factors, idx)


def _equity_curve(returns: List[float]) -> List[Dict[str, float]]:
    return backtest_view_runtime.require().equity_curve(returns)


def _drawdown_curve(
    equity_points: List[Dict[str, float]],
) -> List[Dict[str, float]]:
    return backtest_view_runtime.require().drawdown_curve(equity_points)


def _segment_returns(raw_returns: List[float], segment_size: int = 20) -> List[Dict]:
    segments = []
    for start in range(0, len(raw_returns), segment_size):
        segment = raw_returns[start : start + segment_size]
        if not segment:
            continue
        cumulative = math.prod(1 + value for value in segment) - 1
        win_rate = sum(1 for value in segment if value > 0) / len(segment)
        segments.append(
            {
                "segment_id": f"S{len(segments) + 1:02d}",
                "start_day": start,
                "end_day": start + len(segment) - 1,
                "return": round(cumulative, 5),
                "sharpe": round(compute_sharpe(segment) or 0.0, 3),
                "win_rate": round(win_rate, 3),
                "volatility": round(statistics.pstdev(segment) * math.sqrt(252), 4) if len(segment) > 1 else 0.0,
            }
        )
    return segments


def _drawdown_periods(drawdowns: List[Dict[str, float]], limit: int = 5) -> List[Dict]:
    local_worst = sorted(drawdowns, key=lambda point: point["y"], reverse=True)[:limit]
    periods = []
    for rank, point in enumerate(local_worst, start=1):
        start_day = max(0, int(point["x"]) - 8)
        trough_day = int(point["x"])
        recovery_day = min(drawdowns[-1]["x"] if drawdowns else trough_day, trough_day + 12)
        periods.append(
            {
                "rank": rank,
                "start_day": start_day,
                "trough_day": trough_day,
                "recovery_day": recovery_day,
                "depth": round(point["y"], 5),
                "duration_days": recovery_day - start_day,
            }
        )
    return periods


def _monthly_returns(raw_returns: List[float], month_size: int = 21) -> List[Dict]:
    months = []
    for start in range(0, len(raw_returns), month_size):
        values = raw_returns[start : start + month_size]
        if not values:
            continue
        cumulative = math.prod(1 + value for value in values) - 1
        months.append(
            {
                "month": f"2026-{len(months) + 1:02d}",
                "return": round(cumulative, 5),
                "positive_days": sum(1 for value in values if value > 0),
                "negative_days": sum(1 for value in values if value <= 0),
            }
        )
    return months


def _backtest_explainability_payload_raw(alpha_id: str = "alpha-1") -> Dict:
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
        {
            "regime": "normal",
            "days": 72,
            "contribution": round(sum(raw_returns[i] for i in range(0, min(len(raw_returns), 72)) if i % 5 != 0), 5),
            "hit_rate": 0.61,
        },
        {
            "regime": "stress",
            "days": 24,
            "contribution": round(sum(raw_returns[i] for i in range(0, len(raw_returns), 5)), 5),
            "hit_rate": 0.46,
        },
        {
            "regime": "recovery",
            "days": 23,
            "contribution": round(sum(raw_returns[i] for i in range(76, min(len(raw_returns), 99))), 5),
            "hit_rate": 0.68,
        },
    ]
    trade_distribution = [
        {"bucket": "< -1%", "count": 2 + idx % 2, "avg_return": -0.014},
        {"bucket": "-1% to 0%", "count": 7 + idx, "avg_return": -0.004},
        {"bucket": "0% to 1%", "count": 13 + idx * 2, "avg_return": 0.004},
        {"bucket": "> 1%", "count": 5 + idx, "avg_return": 0.017},
    ]
    decision_drivers = [
        {
            "driver": "Information coefficient",
            "impact": "POSITIVE",
            "score": round(alpha["ic"], 4),
            "explanation": "Forward-return correlation remains positive across most segments",
        },
        {
            "driver": "Drawdown containment",
            "impact": "POSITIVE" if max(point["y"] for point in drawdowns) < 0.08 else "NEGATIVE",
            "score": round(max(point["y"] for point in drawdowns), 4),
            "explanation": "Worst underwater period remains inside research threshold",
        },
        {
            "driver": "Transaction cost drag",
            "impact": "NEGATIVE" if turnover_cost + slippage_cost > 0.002 else "NEUTRAL",
            "score": round(turnover_cost + slippage_cost, 5),
            "explanation": "Cost model subtracts turnover, slippage, borrow, and market impact",
        },
        {
            "driver": "Segment stability",
            "impact": "POSITIVE" if segment_passes >= 4 else "NEUTRAL",
            "score": round(segment_passes / max(len(segments), 1), 3),
            "explanation": "Positive return segments indicate robustness across replay windows",
        },
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
            {
                "component": "slippage_cost",
                "value": round(slippage_cost, 5),
                "method": "volume proxy + turnover adjustment",
            },
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


def _backtest_explainability_payload(alpha_id: str = "alpha-1") -> Dict:
    """Public composition seam; synthetic replay is never load-bearing."""

    return scrub_backtest_payload(_backtest_explainability_payload_raw(alpha_id))


def _scenario_stress_payload_raw(alpha_id: str = "alpha-1") -> Dict:
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
        scenarios.append(
            {
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
                    {
                        "driver": "correlation_shift",
                        "impact": round(alpha["correlation_penalty"] + spec["correlation_shift"], 5),
                    },
                ],
            }
        )

    worst = max(scenarios, key=lambda scenario: scenario["metrics"]["loss_delta"])
    fails = sum(1 for scenario in scenarios if scenario["decision"] == "FAIL")
    watches = sum(1 for scenario in scenarios if scenario["decision"] == "WATCH")
    exposure_buckets = [
        {"factor": "market_beta", "exposure": 0.22 + idx * 0.01, "shock": -0.08, "estimated_pnl": -0.017 - idx * 0.001},
        {
            "factor": "liquidity",
            "exposure": alpha["turnover"],
            "shock": -0.18,
            "estimated_pnl": round(-alpha["turnover"] * 0.022, 5),
        },
        {
            "factor": "crowding",
            "exposure": alpha["correlation_penalty"],
            "shock": -0.24,
            "estimated_pnl": round(-alpha["correlation_penalty"] * 0.045, 5),
        },
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
            "reproducibility_hash": _short_hash(
                "scenario-stress", alpha["candidate_id"], worst["scenario_id"], worst["metrics"]["loss_delta"]
            ),
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
            "average_stressed_drawdown": round(
                statistics.mean(scenario["metrics"]["stressed_max_drawdown"] for scenario in scenarios), 5
            ),
            "average_liquidity_buffer_used": round(
                statistics.mean(scenario["metrics"]["liquidity_buffer_used"] for scenario in scenarios), 3
            ),
        },
        "scenarios": scenarios,
        "exposure_buckets": exposure_buckets,
        "risk_factor_contributions": sorted(
            exposure_buckets, key=lambda item: abs(item["estimated_pnl"]), reverse=True
        ),
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


def _scenario_stress_payload(alpha_id: str = "alpha-1") -> Dict:
    """Public composition seam; synthetic stress output is diagnostic-only."""

    return scrub_backtest_payload(_scenario_stress_payload_raw(alpha_id))


router = APIRouter()


@router.get("/api/v1/console/backtest-explainability")
def backtest_explainability(alpha_id: str = Query("alpha-1")) -> Dict:
    payload = _backtest_explainability_payload(alpha_id)
    return scrub_backtest_payload({
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    })


@router.get("/api/v1/console/scenario-stress")
def scenario_stress(alpha_id: str = Query("alpha-1")) -> Dict:
    payload = _scenario_stress_payload(alpha_id)
    return scrub_backtest_payload({
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        **payload,
    })
