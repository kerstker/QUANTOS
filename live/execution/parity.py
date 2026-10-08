"""live-paper 对账(2026-07-05)。设计:
docs/engineering/specs/2026-07-05-institutional-live-stack-design.md §④。

回答的问题:**测试网真实执行离 Stage 0 纸面决策有多远?** 纸面侧用前向台账里**记录在案**的
目标权重(`forward_decisions.weights_json`,不是重算的信号——重算会掩盖两条路径间的信号
漂移,恰恰是对账要抓的东西之一);live 侧用本地账本的真实仓位 + 订单归因。逐周落
`data/cache/live_parity.jsonl`,是 8 周前向证据的一部分:如果 live 一直贴不住纸面
(mandate 拦截之外的系统性缺口),Go/No-Go 评审必须看到。

纯函数,无 IO/网络/墙钟(ForwardLedger/LiveLedger 的读取在驱动器边界完成后把行数据传进来;
`paper_weights_from_forward_ledger` 只做提取+解析,接受已构造的 ledger 实例)。

诚实边界:preview 模式下 live 侧没有真实成交(mandate 缺失 → 零提交),报告的缺口=
"什么都没执行"——`mode` 字段如实标注口径,读数时不能把 preview 的缺口当执行质量问题。
"""
from __future__ import annotations

import json
from typing import Mapping, Optional, Sequence

from live.execution.filters import decision_prefix


def paper_weights_from_forward_ledger(forward_ledger, alpha_id: str,
                                      decided_ts: int) -> Optional[dict]:
    """从 Stage 0 前向台账提取指定 (alpha_id, decided_ts) 的记录在案权重。找不到 -> None
    (调用方自行决定回退口径并诚实标注来源)。"""
    for dec in forward_ledger.decisions(alpha_id):
        if int(dec["decided_ts"]) == int(decided_ts):
            try:
                return {k: float(v) for k, v in json.loads(dec["weights_json"]).items()}
            except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
                return None
    return None


def parity_report(*, paper_weights: Mapping[str, float], paper_source: str, aum: float,
                  prices: Mapping[str, float], ledger_positions: Mapping[str, float],
                  ledger_orders: Sequence[Mapping], strategy_id: str, decided_ts: int,
                  mode: str) -> dict:
    """纸面目标 vs live 实际,按标的对齐。

    - 目标名义额 = weight × aum(带符号);实际名义额 = 账本仓位 × 现价(带符号);
      gap = 实际 − 目标(缺价 -> None,诚实缺失,不进聚合统计)。
    - 订单归因:同一决策腿的所有订单共享确定性的 16 位 decision_prefix(2026-07-05 起订单
      ID 还包含方向+数量,一条腿可能有多笔订单——取最近提交的那笔做状态归因)——
      "no_order" 意味着这条腿从未提交(mandate/过滤器拦截或 preview),订单状态则透露
      超时撤单/拒绝等执行事故。
    - live 有仓位但纸面没有这条腿 -> unexpected_live_positions(遗留测试仓位/对账缺口)。
    """
    per_instrument = []
    gaps = []
    n_with_order = 0
    for inst in sorted(paper_weights):
        w = float(paper_weights[inst])
        target_notional = w * aum
        px = prices.get(inst)
        pos_qty = float(ledger_positions.get(inst, 0.0))
        actual_notional = pos_qty * px if (px is not None and px > 0) else None
        gap = (actual_notional - target_notional) if actual_notional is not None else None
        prefix = decision_prefix(strategy_id, decided_ts, inst)
        leg_orders = [o for o in ledger_orders if o["client_order_id"].startswith(prefix)]
        order = (max(leg_orders, key=lambda o: o.get("submitted_at", 0))
                 if leg_orders else None)
        if order is not None:
            n_with_order += 1
        if gap is not None:
            gaps.append(abs(gap))
        per_instrument.append({
            "instrument_id": inst,
            "target_weight": w,
            "target_notional": target_notional,
            "actual_qty": pos_qty,
            "actual_notional": actual_notional,
            "gap_notional": gap,
            "order_status": order["status"] if order is not None else "no_order",
        })

    unexpected = sorted(inst for inst, qty in ledger_positions.items()
                        if qty != 0.0 and inst not in paper_weights)
    return {
        "schema": "live_parity.v1",
        "mode": mode,
        "paper_source": paper_source,
        "strategy_id": strategy_id,
        "decided_ts": int(decided_ts),
        "aum": float(aum),
        "n_paper_legs": len(paper_weights),
        "n_with_live_order": n_with_order,
        "max_abs_gap_notional": max(gaps) if gaps else 0.0,
        "total_abs_gap_notional": sum(gaps) if gaps else 0.0,
        "unexpected_live_positions": unexpected,
        "per_instrument": per_instrument,
    }
