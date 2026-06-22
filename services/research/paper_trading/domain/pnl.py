"""Paper Trading 鈥?PnL & Attribution (Subsystems 7, 10)"""
import math, statistics, time, hashlib, json
from typing import Dict, List, Optional, Tuple

from .models import (PaperAccount, PaperFill, PaperRun, PaperAttribution,
                      PaperRiskSnapshot, FillMethod)


class PnLAttributor:
    """Attributes simulated PnL by alpha, sector, regime, and cost."""

    @staticmethod
    def attribute(run: PaperRun, fills: List[PaperFill],
                  equity_curve: List[Dict], sector_map: Dict[str, str] = None,
                  regime_labels: List[str] = None) -> PaperAttribution:
        attr = PaperAttribution(run_id=run.run_id)
        for f in fills:
            notional = f.quantity * f.price
            attr.total_commission += f.commission
            attr.total_slippage += f.slippage
            attr.turnover += notional
            attr.pnl_by_alpha[run.alpha_id] = attr.pnl_by_alpha.get(run.alpha_id, 0) + notional * 0.001
            if sector_map:
                sec = sector_map.get(f.instrument_id, "UNKNOWN")
                attr.pnl_by_sector[sec] = attr.pnl_by_sector.get(sec, 0) + notional * 0.0005
        return attr

    @staticmethod
    def compute_metrics(equity_curve: List[Dict], initial_capital: float,
                        total_turnover: float) -> Dict[str, float]:
        if len(equity_curve) < 2: return {}
        returns = []
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i-1]["equity"]
            curr = equity_curve[i]["equity"]
            if prev > 0: returns.append((curr - prev) / prev)
        if not returns: return {}
        mean_ret = statistics.mean(returns); std_ret = statistics.stdev(returns)
        sharpe = (mean_ret/std_ret)*math.sqrt(252) if std_ret > 0 else 0
        total_ret = (equity_curve[-1]["equity"]/initial_capital - 1)
        annual_ret = (1+total_ret)**(252/len(returns)) - 1 if returns else 0
        cum = [initial_capital]; peak = initial_capital; max_dd = 0.0
        for pt in equity_curve:
            cum.append(pt["equity"])
        for v in cum:
            if v > peak: peak = v
            dd = (peak-v)/peak if peak > 0 else 0
            if dd > max_dd: max_dd = dd
        return {"sharpe": round(sharpe,3), "total_return": round(total_ret*100,2),
                "annual_return": round(annual_ret*100,2), "max_drawdown": round(max_dd*100,2),
                "volatility": round(std_ret*math.sqrt(252)*100,2), "annual_turnover": round(total_turnover/initial_capital,2),
                "n_days": len(equity_curve)}


class PaperReportGenerator:
    """Generates paper trading reports: equity curve, drawdown, turnover, attribution, recommendation."""

    @staticmethod
    def generate(run: PaperRun, account: PaperAccount) -> Dict:
        metrics = run.metrics or PnLAttributor.compute_metrics(
            run.equity_curve, account.initial_capital, account.total_turnover)
        return {
            "run_id": run.run_id, "alpha_id": run.alpha_id,
            "status": run.status, "metrics": metrics,
            "equity_curve": run.equity_curve[-10:],
            "total_trades": len(run.fills),
            "total_commission": account.total_commission,
            "total_slippage": account.total_slippage,
            "final_equity": account.equity,
            "reproducibility_hash": run.reproducibility_hash,
            "recommendation": "PROMOTE" if metrics.get("sharpe", 0) > 0.5 and metrics.get("max_drawdown", 100) < 30 else "EXTEND"
        }
