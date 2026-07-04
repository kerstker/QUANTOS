"""仓位/回撤风险监控(Stage 1.5)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §9。

分两级(与 `live/governance/mandate.py` 分级授权同一哲学——安全响应可以自动,但新的下单
触发必须由人另外签署授权才激活):
① `evaluate_risk_state` + `respond_to_breach`:纯计算越限判断 + 越限即
   `KillSwitch.arm()`(既有、已测试、"任何 actor 皆可触发"的安全机制——halt 只会让系统更
   保守,监控发现越限就可以直接自动用,不引入任何新的风险)。
② `compute_flatten_orders`:纯函数,算出把敞口带回目标范围所需的 REDUCE-ONLY 数量(每条腿
   只朝 0 方向缩放,绝不反手、绝不新增仓位)。**这只是计算,不接自动提交**——kill-switch 一旦
   触发,既有 `PreTradeRiskGate.check_kill_switch` 会挡下包括 reduce-only 在内的所有意图
   (`services/risk/pre_trade/checks.py`),要让减仓单在紧急状态下还能送出,需要一条不经过
   共用 gate 的独立安全路径,这是需要人工明确拍板的架构决定,本文件故意不做这个决定。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional, Tuple

from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.trading import OrderSide
from services.risk.pre_trade.kill_switch import KillSwitch

_RISK_MONITOR_ACTOR = Actor(actor_id="risk_monitor", kind=ActorKind.SYSTEM)


@dataclass(frozen=True)
class RiskBreach:
    breached: bool
    reasons: Tuple[str, ...]
    drawdown_pct: float
    gross_exposure: float
    net_exposure: float


def evaluate_risk_state(*, equity: float, peak_equity: float, positions: Mapping[str, float],
                        prices: Mapping[str, float], max_drawdown_pct: float,
                        max_gross_exposure: float, max_net_exposure: float) -> RiskBreach:
    """纯函数:根据真实权益/仓位/价格算回撤 + 总/净敞口,判断是否越过阈值。fail-closed:
    peak_equity<=0 或某腿缺价导致无法估值 -> 记进 reasons(本身就算一次越限,不是静默跳过——
    "数据不完整、无法确认安全"和"确认越限"在响应上应该一样保守)。"""
    reasons: List[str] = []
    drawdown_pct = 0.0
    if peak_equity > 0:
        drawdown_pct = max(0.0, (peak_equity - equity) / peak_equity)
        if drawdown_pct > max_drawdown_pct:
            reasons.append(f"drawdown_exceeded:{drawdown_pct:.4f}>{max_drawdown_pct}")
    else:
        reasons.append("peak_equity_unknown")

    gross = 0.0
    net = 0.0
    missing_price = []
    for inst, qty in positions.items():
        px = prices.get(inst)
        if px is None or px <= 0:
            missing_price.append(inst)
            continue
        notional = qty * px
        gross += abs(notional)
        net += notional
    if missing_price:
        reasons.append(f"missing_price_for:{','.join(sorted(missing_price))}")
    if gross > max_gross_exposure:
        reasons.append(f"gross_exposure_exceeded:{gross:.2f}>{max_gross_exposure}")
    if abs(net) > max_net_exposure:
        reasons.append(f"net_exposure_exceeded:{net:.2f}>{max_net_exposure}")

    return RiskBreach(breached=bool(reasons), reasons=tuple(reasons), drawdown_pct=drawdown_pct,
                      gross_exposure=gross, net_exposure=net)


def respond_to_breach(*, kill_switch: KillSwitch, breach: RiskBreach, at: int,
                      audit_sink: object = None) -> dict:
    """越限 -> arm kill switch(全局、非人 SYSTEM actor,诚实标注是自动触发)。已经 tripped
    时幂等(不重复 arm、不制造审计噪音)——`KillSwitch.arm` 本身允许重复调用,但监控循环每次
    发现同一个持续存在的越限就重新 arm 会掩盖"这是第几次独立事件"这个信息,所以这里显式检查。
    """
    if not breach.breached:
        return {"action": "none", "breach": breach}
    if kill_switch.state.tripped:
        return {"action": "already_tripped", "breach": breach,
               "incident_id": kill_switch.state.incident_id}
    state = kill_switch.arm(_RISK_MONITOR_ACTOR, f"risk_monitor: {','.join(breach.reasons)}",
                            at=at, audit_sink=audit_sink)
    return {"action": "kill_switch_armed", "breach": breach, "incident_id": state.incident_id}


def compute_flatten_orders(positions: Mapping[str, float], prices: Mapping[str, float], *,
                           target_gross_exposure: float) -> List[Tuple[str, OrderSide, float]]:
    """给定当前仓位 + 目标 gross 敞口上限,算出等比例缩减各腿所需的 REDUCE-ONLY 数量(每条腿
    只朝 0 方向移动一部分,绝不改变方向、绝不新增仓位)。当前 gross 已在目标以下 -> 空列表
    (无需减仓)。返回形状与 `live/execution/filters.py::apply_exchange_filters` 的 `desired`
    参数一致,便于复用同一套交易所过滤器(尚未接自动提交,见模块 docstring)。"""
    gross = sum(abs(qty * prices.get(inst, 0.0)) for inst, qty in positions.items()
               if prices.get(inst) is not None and prices[inst] > 0)
    if gross <= target_gross_exposure or gross <= 0:
        return []
    scale = target_gross_exposure / gross
    orders: List[Tuple[str, OrderSide, float]] = []
    for inst, qty in positions.items():
        px = prices.get(inst)
        if px is None or px <= 0 or qty == 0:
            continue
        delta = qty * scale - qty              # 朝 0 缩放:|new_qty| < |qty|,同号但绝对值更小
        if delta == 0:
            continue
        side = OrderSide.SELL if delta < 0 else OrderSide.BUY
        orders.append((inst, side, abs(delta)))
    return orders
