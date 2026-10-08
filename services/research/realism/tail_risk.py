"""尾部风险(现实主义引擎 RE-2)—— 历史 VaR/CVaR(期望损失)+ Hill 尾指数(尾多肥)+ 经验下尾依赖(崩盘 beta)。
纯函数、确定性、无 IO/RNG/墙钟。"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence


def historical_var_cvar(returns: Sequence[float], alpha: float = 0.95) -> dict:
    """losses = -returns;VaR = losses 的 alpha 分位;CVaR = 超过 VaR 的损失均值(期望损失)。"""
    if not (0.0 < alpha < 1.0) or len(returns) < 2:
        return {"var": 0.0, "cvar": 0.0, "alpha": alpha}
    losses = sorted(-r for r in returns)
    idx = min(len(losses) - 1, int(math.ceil(alpha * len(losses))) - 1)
    var = losses[idx]
    tail = [x for x in losses if x >= var]
    cvar = statistics.fmean(tail) if tail else var
    return {"var": var, "cvar": cvar, "alpha": alpha}


def hill_tail_index(returns: Sequence[float], tail_q: float = 0.10) -> Optional[float]:
    """Hill 估计损失右尾尾指数 α̂ = 1 / mean(ln(x_(i)/x_(k)))(取最大的 k 个损失,x_(k)=阈值)。越小=尾越肥。"""
    losses = sorted((-r for r in returns), reverse=True)
    losses = [x for x in losses if x > 0]
    k = int(len(losses) * tail_q)
    if k < 2 or len(losses) <= k or losses[k] <= 0:
        return None
    thr = losses[k]
    s = sum(math.log(losses[i] / thr) for i in range(k) if losses[i] > 0)
    return None if s <= 0 else k / s


def lower_tail_dependence(x: Sequence[float], y: Sequence[float], q: float = 0.10) -> Optional[float]:
    """经验下尾依赖 λ_L ≈ #(x<Q_x(q) ∧ y<Q_y(q)) / #(y<Q_y(q))。x,y 长度不齐或太短 → None。"""
    n = min(len(x), len(y))
    if n < 10 or not (0.0 < q < 1.0):
        return None
    x, y = list(x[:n]), list(y[:n])
    qx = sorted(x)[max(0, int(q * n) - 1)]
    qy = sorted(y)[max(0, int(q * n) - 1)]
    y_low = [i for i in range(n) if y[i] <= qy]
    if not y_low:
        return None
    both = sum(1 for i in y_low if x[i] <= qx)
    return both / len(y_low)
