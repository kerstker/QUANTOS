"""端到端干跑流水线编排器(薄、确定性)。

数据流:权重 → 目标整数手数 → build_intents → 逐意图过预交易门(运行时 chokepoint,fail-closed)+
kill_switch → 干跑适配器受理(不真发)→ 纸面成交 → reconcile/PnL → 哈希链审计 → DryRunRun。

容器化(L2,不可让):五个 LIVE 标志恒 false、门 fail-closed、kill_switch 先查、干跑适配器无券商 SDK/
不真发、无网络 import、编排器绝不进 research_runner、核心无 RNG/无墙钟(timestamp/seed 由调用方边界注入)。
RiskContext 由注入的 ctx_factory 装配(核心不硬编码账户/安全装配),保持可测、可复用。
实盘 = 同接口换真券商适配器 + 人工批准 = 显式非目标。
"""
from __future__ import annotations

from typing import Dict


def plan_target_quantities(weights: Dict[str, float], prices: Dict[str, float], aum: float) -> Dict[str, int]:
    """权重 → 目标整数手数:round(w * aum / price)。价缺失/≤0 跳过(fail-soft);0 手不下意图。"""
    out: Dict[str, int] = {}
    for inst, w in weights.items():
        px = prices.get(inst)
        if px is None or px <= 0:
            continue
        q = int(round(w * aum / px))
        if q != 0:
            out[inst] = q
    return out
