"""成交质量分析(TCA,2026-07-05)。设计:
docs/engineering/specs/2026-07-05-institutional-live-stack-design.md §③。

纯函数、无 IO/网络/墙钟。每笔确认成交的滑点 = 实际成交均价相对决策价的带方向偏离
(bps;买贵/卖贱为正 = 成本,负 = 价格改善)。周度调仓节奏下"决策价"用
`submit_live_batch` 收到的 `prices`(决策时点 mark price)作为到达价代理——诚实标注:
这不是逐笔到达价(那需要下单瞬间的盘口快照,本阶段刻意不引入),但在周度低频下两者的
差主要就是我们想度量的执行延迟+冲击本身。

`tca_summary` 把累计记录对照回测的成本假设(默认 10bps)——"执行现实校验回测假设"闭环的
最后一块:如果真实加权滑点持续高于回测假设,回测里的净收益就是高估的,这个信号必须回喂
给 Go/No-Go 评审。
"""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence

from quantos.contracts.trading import OrderSide


def slippage_bps(side: OrderSide, *, decision_price: float, fill_price: float) -> Optional[float]:
    """带方向滑点(bps)。买单成交价高于决策价 / 卖单低于决策价 = 正 = 成本;反向 = 负 = 改善。
    价格非正 -> None(无法计算,诚实缺失;不硬凑 0——0 是"零滑点"的真实断言)。"""
    if decision_price <= 0 or fill_price <= 0:
        return None
    raw = (fill_price - decision_price) / decision_price * 1e4
    return raw if side == OrderSide.BUY else -raw


def _mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _median(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def tca_summary(records: Sequence[Mapping], *, assumed_cost_bps: float = 10.0) -> dict:
    """累计 TCA 记录 -> 汇总。records 形状 = `LiveLedger.tca_records()` 的行
    (须含 instrument_id/slippage_bps/fill_price/quantity;slippage_bps 为 None 的行
    只计入 n_fills,不进任何均值——诚实缺失不污染统计)。"""
    valid = [r for r in records if r.get("slippage_bps") is not None]
    slips = [float(r["slippage_bps"]) for r in valid]
    notionals = [abs(float(r["fill_price"]) * float(r["quantity"])) for r in valid]
    total_notional = sum(notionals)
    weighted = (sum(s * n for s, n in zip(slips, notionals)) / total_notional
                if total_notional > 0 else None)

    by_instrument: Dict[str, dict] = {}
    for r in valid:
        inst = r["instrument_id"]
        b = by_instrument.setdefault(inst, {"n_fills": 0, "_slips": []})
        b["n_fills"] += 1
        b["_slips"].append(float(r["slippage_bps"]))
    for inst, b in by_instrument.items():
        slips_i: List[float] = b.pop("_slips")
        b["mean_slippage_bps"] = _mean(slips_i)
        b["worst_slippage_bps"] = max(slips_i) if slips_i else None

    return {
        "n_fills": len(records),
        "n_with_slippage": len(valid),
        "mean_slippage_bps": _mean(slips),
        "median_slippage_bps": _median(slips),
        "worst_slippage_bps": max(slips) if slips else None,
        "notional_weighted_slippage_bps": weighted,
        "total_notional": total_notional,
        "assumed_cost_bps": assumed_cost_bps,
        "weighted_minus_assumed_bps": (weighted - assumed_cost_bps
                                       if weighted is not None else None),
        "by_instrument": by_instrument,
    }
