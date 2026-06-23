"""Alpha capacity (Phase 6, step 7b) — how much money an alpha can actually carry.

Two grounded limits on tradeable notional, using the SAME sqrt-participation
basis as CostModel.impact_bps (so capacity is consistent with the backtest's
cost model, not a free-floating label):
  * a participation cap (max_participation x ADV), and
  * an impact-budget cap: the largest notional whose modeled sqrt market impact
    stays within impact_budget_bps. impact_bps = impact_coef*sqrt(N/ADV) <= budget
    => N <= ADV*(budget/impact_coef)^2.
The binding (smaller) limit is the capacity. Sharpe is then haircut by how much
of the target AUM the alpha can carry.
"""
from __future__ import annotations

from services.research.backtest_engine.costs import CostModel


def participation_capacity(adv_notional: float, max_participation: float = 0.10) -> float:
    if adv_notional < 0 or not (0.0 < max_participation <= 1.0):
        raise ValueError("invalid capacity inputs")
    return adv_notional * max_participation


def capacity_notional(
    adv_notional: float,
    cost: CostModel = CostModel(),
    impact_budget_bps: float = 10.0,
    max_participation: float = 0.20,
) -> float:
    """Binding capacity = min(participation cap, impact-budget cap)."""
    cap_part = participation_capacity(adv_notional, max_participation)
    if cost.impact_coef <= 0 or impact_budget_bps <= 0:
        return cap_part
    cap_impact = adv_notional * (impact_budget_bps / cost.impact_coef) ** 2
    return min(cap_part, cap_impact)


def capacity_adjusted_sharpe(net_sharpe: float, capacity_notional_value: float, target_aum: float) -> float:
    """Haircut Sharpe by the fraction of target AUM the alpha can carry."""
    if target_aum <= 0 or capacity_notional_value <= 0:
        return 0.0
    return net_sharpe * min(1.0, capacity_notional_value / target_aum)
