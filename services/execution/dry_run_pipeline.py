"""端到端干跑流水线编排器(薄、确定性)。

数据流:权重 → 目标整数手数 → build_intents → 逐意图过预交易门(运行时 chokepoint,fail-closed)+
kill_switch → 干跑适配器受理(不真发)→ 纸面成交 → reconcile/PnL → 哈希链审计 → DryRunRun。

容器化(L2,不可让):五个 LIVE 标志恒 false、门 fail-closed、kill_switch 先查、干跑适配器无券商 SDK/
不真发、无网络 import、编排器绝不进 research_runner、核心无 RNG/无墙钟(timestamp/seed 由调用方边界注入)。
RiskContext 由注入的 ctx_factory 装配(核心不硬编码账户/安全装配),保持可测、可复用。
实盘 = 同接口换真券商适配器 + 人工批准 = 显式非目标。
"""
from __future__ import annotations

from typing import Callable, Dict, Sequence

from quantos.contracts.risk import RiskDecisionType


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


def _reason_str(r) -> str:
    return getattr(r, "value", None) or str(r)


def screen_intents(intents: Sequence, *, gate, ctx_factory: Callable) -> dict:
    """运行时 chokepoint:逐意图装配 RiskContext → gate.evaluate → fail-closed 分流。

    仅门 APPROVED 的意图进 approved(带其 intent-bound risk ref);任何非批准 / ctx 装配异常 → rejected
    (附原因)。门本身已 fail-closed(内部异常恒返回 REJECTED);外层 try 再兜 ctx_factory 异常。返回
    {"approved": [(intent, ref), ...], "rejected": [(intent, [reason, ...]), ...]}。
    """
    approved, rejected = [], []
    for intent in intents:
        try:
            ctx = ctx_factory(intent)
            decision = gate.evaluate(intent, ctx)
            if getattr(decision, "decision", None) == RiskDecisionType.APPROVED:
                approved.append((intent, gate.issue_ref(decision, intent, ctx)))
            else:
                reasons = [_reason_str(r) for r in (getattr(decision, "reasons", ()) or ())]
                rejected.append((intent, reasons or ["REJECTED"]))
        except Exception as exc:  # fail-closed:ctx 装配 / 评估异常 -> 拒绝,绝不放行
            rejected.append((intent, [f"GATE_ERROR:{type(exc).__name__}"]))
    return {"approved": approved, "rejected": rejected}
