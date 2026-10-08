"""Produce the BacktestResult contract (and capacity estimate) from an engine run."""
from __future__ import annotations

from quantos.contracts.hashing import short_hash
from quantos.contracts.research import BacktestResult as BacktestResultContract

from .engine import BacktestResult as EngineResult
from .metrics import metrics


def capacity_estimate(adv_notional: float, max_participation: float = 0.10) -> float:
    """Crude capacity: the notional you can trade within a participation cap of ADV."""
    if adv_notional < 0 or not (0 < max_participation <= 1):
        raise ValueError("invalid capacity inputs")
    return adv_notional * max_participation


def to_backtest_result(request_id: str, result: EngineResult) -> BacktestResultContract:
    m = metrics(result.equity_curve)
    return BacktestResultContract(
        request_id=request_id,
        sharpe=round(m["sharpe"], 4),
        total_return=round(m["total_return"], 6),
        max_drawdown=round(m["max_drawdown"], 6),
        turnover=round(result.turnover, 2),
        net_of_cost=True,
        reproducibility_hash=short_hash({
            "equity": [round(e, 6) for e in result.equity_curve],
            "cost": round(result.total_cost, 6),
        }),
    )
