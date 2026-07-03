"""宏观 regime 分类器(数据方向)—— 把宏观序列切成 regime,把一条边的逐周收益按 regime 分桶算表现。纯函数、
确定性、无 IO/RNG/墙钟/网络。

两类分类器:
- `tercile_labels`(全样本分位)= **回顾性描述**,非 PIT 可交易择时(调用方须标注)。
- `expanding_tercile_labels`(扩展窗分位)/ `sign_regime`(符号)/ `fixed_threshold_labels`(固定阈值)
  = **PIT 可交易**:每个位置只用**过去+当前**信息定标签,故对未来数据追加**不变(截断不变)**——门/优化器可合法据以行动(Rank #14)。"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence


def expanding_tercile_labels(values: Sequence[Optional[float]], min_history: int = 10) -> list:
    """PIT 三分位(Rank #14):位置 i 的三分位阈值只用 `values[:i+1]` 的有效值(仅过去+当前),因此对未来
    数据追加**不变**(截断不变:`labels(values[:k])[i] == labels(values)[i]`,i<k)。有效值 < `min_history`
    → 'mid'(暖启,样本不足以定分位);None → None。这才是可交易的 regime 分类(非全样本回顾)。"""
    out: list = []
    seen: list = []                                   # 到 i 为止(含)的有效值
    for v in values:
        if v is None:
            out.append(None)
            continue
        seen.append(v)
        if len(seen) < min_history:
            out.append("mid")
            continue
        s = sorted(seen)
        lo, hi = s[len(s) // 3], s[2 * len(s) // 3]
        out.append("low" if v <= lo else "high" if v >= hi else "mid")
    return out


def fixed_threshold_labels(values: Sequence[Optional[float]], low: float, high: float,
                           labels: tuple = ("calm", "mid", "stress")) -> list:
    """免估计固定阈值分类(如 VIX 20/30):零估计 → 天然 PIT、天然截断不变。None → None。"""
    lo_l, mid_l, hi_l = labels
    return [None if v is None else (lo_l if v < low else hi_l if v >= high else mid_l) for v in values]


def tercile_labels(values: Sequence[float]) -> list:
    """全样本三分位 → ['low'|'mid'|'high']。None 保留为 None;<3 个有效值 → 全 'mid'。"""
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return ["mid"] * len(values)
    s = sorted(vals)
    lo, hi = s[len(s) // 3], s[2 * len(s) // 3]
    out = []
    for v in values:
        if v is None:
            out.append(None)
        elif v <= lo:
            out.append("low")
        elif v >= hi:
            out.append("high")
        else:
            out.append("mid")
    return out


def sign_regime(value: Optional[float], neg_label: str = "inverted", pos_label: str = "normal") -> Optional[str]:
    if value is None:
        return None
    return neg_label if value < 0 else pos_label


def _ann_sharpe(rs: Sequence[float], ppy: int = 52) -> float:
    if len(rs) < 2:
        return 0.0
    sd = statistics.pstdev(rs)
    return 0.0 if sd == 0 else statistics.fmean(rs) / sd * math.sqrt(ppy)


def bucket_returns(returns: Sequence[float], labels: Sequence, ppy: int = 52) -> dict:
    """按 label 分桶 → {label: {n, ann_sharpe, mean_return, total_return}}。label=None 的期跳过。"""
    buckets = {}
    for r, lb in zip(returns, labels):
        if lb is None:
            continue
        buckets.setdefault(lb, []).append(r)
    out = {}
    for lb, rs in buckets.items():
        cum = 1.0
        for r in rs:
            cum *= (1.0 + r)
        out[lb] = {"n": len(rs), "ann_sharpe": round(_ann_sharpe(rs, ppy), 3),
                   "mean_return": round(statistics.fmean(rs), 6), "total_return": round(cum - 1.0, 6)}
    return out
