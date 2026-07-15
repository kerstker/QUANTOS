"""本地账本 vs 交易所仓位对账(Stage 1)。设计:
docs/engineering/specs/2026-07-04-live-execution-stack-design.md §3.5;账户级口径裁定:
docs/engineering/specs/2026-07-06-strategy-position-isolation-design.md §2。

纯函数——只报告 drift,**不自动修正**。drift 是留给人/ops 流程处理的信号(核对成交回报是否
漏记、是否有账外仓位变动),不是本层的决策权;`live/pipeline.py` 从不据此自动下单调整仓位。

**对账永远是账户级的(2026-07-06 战线③裁定,勿"改进"成 per-strategy):** venue 仓位是账户级
事实——Binance /fapi positionRisk、Bybit V5 position list 都不知道策略(Hedge Mode 的
positionIdx 区分的是多空方向,不是策略)。**明令禁止**任何 "per-strategy vs venue" 对账:
venue 侧没有这个信息,拿单策略分账去对账户级 venue 仓位必然报假 drift(其它策略的持仓全成了
"漂移"),而拿本地分解对本地分解则是自己对自己、伪造独立性。调用方必须喂
`ledger.all_positions()`(无参 = 账户级)。策略级一致性的真实保证 = 账本分区恒等式
(Σ per-strategy == 账户级,结构性成立)+ 本文件的账户级外部核对,两者合起来:账户级 in_sync
∧ 分区恒等式成立 ⇒ 每个策略分账的可信度等于账户账本的可信度。
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


def preflight_check(ledger_positions: Mapping[str, float], venue_positions: Mapping[str, float],
                    *, tolerance: float = 1e-6) -> dict:
    """调仓前置检查:核对本地账本与交易所真实仓位。存在超出容忍度的 drift -> `proceed=False`
    (HARD_STOP)——不该在一个已知不一致的账本基础上继续计算/提交新调仓,那会用错误的
    current_positions 算出错误的目标增量,可能放大孤儿仓位或重复下单(2026-07-04 对抗式审查
    发现 #7:`reconcile_positions` 此前零调用方,drift 永远不会被任何代码路径发现)。这是驱动器
    在真正调用 `submit_live_batch` 之前应该跑的第一步。"""
    r = reconcile_positions(ledger_positions, venue_positions, tolerance=tolerance)
    return {**r, "proceed": r["in_sync"]}
