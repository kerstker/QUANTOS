"""本地账本 vs 交易所仓位对账(Stage 1)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.5。

纯函数——只报告 drift,**不自动修正**。drift 是留给人/ops 流程处理的信号(核对成交回报是否
漏记、是否有账外仓位变动),不是本层的决策权;`live/pipeline.py` 从不据此自动下单调整仓位。
"""
from __future__ import annotations

from typing import Dict, Mapping


def reconcile_positions(ledger_positions: Mapping[str, float], venue_positions: Mapping[str, float],
                        *, tolerance: float = 0.0) -> dict:
    """union(标的) 上逐个算 drift = venue − ledger;任一 |drift| > tolerance 则整体 not in_sync。
    某标的只在一侧出现时,缺失侧按 0 处理(平仓在两种表示里都等价于"不出现"或"值为 0")。"""
    instruments = set(ledger_positions) | set(venue_positions)
    drift: Dict[str, float] = {}
    for inst in instruments:
        drift[inst] = venue_positions.get(inst, 0.0) - ledger_positions.get(inst, 0.0)
    max_abs = max((abs(d) for d in drift.values()), default=0.0)
    return {"drift": drift, "in_sync": max_abs <= tolerance, "max_abs_drift": max_abs}
