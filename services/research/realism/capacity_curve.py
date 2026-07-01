"""容量衰减曲线(现实主义引擎 RE-1)—— 给定一条 book 的逐期毛收益 + 逐期逐名换手 + 每名 ADV,扫 AUM 网格,
逐档扣冲击成本得净收益 → 净夏普,找 edge 归零(净夏普跌破 floor)的临界 AUM。冲击系数无法校准 → 扫 [lo,hi]
给区间带,不报假单点。纯函数、确定性、无 IO/RNG/墙钟。"""
from __future__ import annotations

import math
import statistics
from dataclasses import replace
from typing import Optional, Sequence

from services.research.realism.impact import AlmgrenChrissImpact

# 每期换手:该期各名的 (|Δnotional_frac|, adv_notional) 列表
Turnover = Sequence[Sequence[tuple]]


def net_return_series_at_aum(gross_returns: Sequence[float], turnover_by_period: Turnover,
                             aum: float, impact: AlmgrenChrissImpact) -> list:
    """逐期净收益 = 毛 - 冲击/aum;冲击 = Σ_i impact.total_cost_notional(frac_i*aum, adv_i)。"""
    if aum <= 0:
        raise ValueError("aum must be positive")
    net = []
    for t, g in enumerate(gross_returns):
        legs = turnover_by_period[t] if t < len(turnover_by_period) else []
        cost = sum(impact.total_cost_notional(frac * aum, adv) for frac, adv in legs)
        net.append(g - cost / aum)
    return net


def annualized_sharpe(returns: Sequence[float], ppy: int = 52) -> float:
    r = [x for x in returns]
    if len(r) < 2:
        return 0.0
    sd = statistics.pstdev(r)
    if sd == 0:
        return 0.0
    return statistics.fmean(r) / sd * math.sqrt(ppy)


def _total_return(returns: Sequence[float]) -> float:
    cum = 1.0
    for r in returns:
        cum *= (1.0 + r)
    return cum - 1.0


def _max_drawdown(returns: Sequence[float]) -> float:
    peak, eq, mdd = 1.0, 1.0, 0.0
    for r in returns:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    return mdd


def capacity_curve(gross_returns, turnover_by_period, aum_grid, impact, ppy: int = 52) -> list:
    out = []
    for aum in aum_grid:
        net = net_return_series_at_aum(gross_returns, turnover_by_period, aum, impact)
        out.append({"aum": aum, "net_total_return": _total_return(net),
                    "net_sharpe": annualized_sharpe(net, ppy), "net_max_drawdown": _max_drawdown(net)})
    return out


def critical_aum(curve: list, sharpe_floor: float = 0.3) -> Optional[float]:
    """净夏普首次跌破 floor 的 AUM(相邻档线性插值)。全程在 floor 以上 → None(容量 > 网格上限)。"""
    for i in range(len(curve)):
        if curve[i]["net_sharpe"] < sharpe_floor:
            if i == 0:
                return curve[0]["aum"]
            a0, s0 = curve[i - 1]["aum"], curve[i - 1]["net_sharpe"]
            a1, s1 = curve[i]["aum"], curve[i]["net_sharpe"]
            if s0 == s1:
                return a1
            frac = (s0 - sharpe_floor) / (s0 - s1)
            return a0 + frac * (a1 - a0)
    return None


def capacity_band(gross_returns, turnover_by_period, aum_grid, impact_coef_lo_hi,
                  ppy: int = 52, sharpe_floor: float = 0.3,
                  base_impact: Optional[AlmgrenChrissImpact] = None) -> dict:
    """扫冲击系数 [lo, hi](保守→激进)→ critical_aum 的区间带。激进系数容量更小 = 下界。"""
    base = base_impact or AlmgrenChrissImpact()
    lo_coef, hi_coef = impact_coef_lo_hi
    curves = {}
    crit = {}
    for tag, coef in (("conservative", lo_coef), ("aggressive", hi_coef)):
        imp = replace(base, temp_coef=coef, perm_coef=coef / 2.0)
        c = capacity_curve(gross_returns, turnover_by_period, aum_grid, imp, ppy)
        curves[tag] = c
        crit[tag] = critical_aum(c, sharpe_floor)
    # 激进(大系数)→ 容量小 = aum_low;保守 → aum_high
    return {"aum_low": crit["aggressive"], "aum_high": crit["conservative"],
            "curves": curves, "provenance": "UNCALIBRATED_PRINCIPLED"}
