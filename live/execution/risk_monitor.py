"""仓位/回撤风险监控(Stage 1.5)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §9。

分两级(与 `live/governance/mandate.py` 分级授权同一哲学——安全响应可以自动,但新的下单
触发必须由人另外签署授权才激活):
① `evaluate_risk_state` + `respond_to_breach`:纯计算越限判断 + 越限即
   `KillSwitch.arm()`(既有、已测试、"任何 actor 皆可触发"的安全机制——halt 只会让系统更
   保守,监控发现越限就可以直接自动用,不引入任何新的风险)。
② `compute_flatten_orders`:纯函数,算出把敞口带回目标范围所需的 REDUCE-ONLY 数量(每条腿
   只朝 0 方向缩放,绝不反手、绝不新增仓位)。自动提交曾因架构冲突留白(kill-switch 触发后
   共用 gate 会挡下包括 reduce-only 在内的所有意图);**2026-07-05 用户拍板"独立紧急通道"
   方案后已接线**——`live/execution/emergency.py::submit_emergency_flatten`(不经过共用
   gate,自带结构性"只能减仓"窄校验 + 强制 reduce-only,见该模块 docstring)。
"""
from __future__ import annotations

import math
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


def _finite(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def evaluate_risk_state(*, equity: float, peak_equity: float, positions: Mapping[str, float],
                        prices: Mapping[str, float], max_drawdown_pct: float,
                        max_gross_exposure: float, max_net_exposure: float,
                        min_margin_ratio: Optional[float] = None,
                        maint_margin_balance: Optional[float] = None,
                        margin_maint_amount: Optional[float] = None) -> RiskBreach:
    """纯函数:根据真实权益/仓位/价格算回撤 + 总/净敞口,判断是否越过阈值。fail-closed:
    peak_equity<=0 或某腿缺价导致无法估值 -> 记进 reasons(本身就算一次越限,不是静默跳过——
    "数据不完整、无法确认安全"和"确认越限"在响应上应该一样保守)。

    维持保证金比率地板(Phase 5 item 2,加法可选,默认 None -> 旧行为字节等价):`min_margin_ratio`
    非 None 时,比率 = maint_margin_balance / margin_maint_amount(如 Binance totalMarginBalance /
    totalMaintMargin)< 地板 -> 记 'margin_ratio_below_floor'。margin_maint_amount<=0(无维持要求)
    -> 安全不报;开启但余额/维持额缺或非有限 -> fail-closed 记 'margin_data_incomplete'。地板是用户签
    的风险数字——默认 None(不绑定)。"""
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

    # 维持保证金比率地板(加法,默认 None 不绑定 -> 上面逻辑完全不变)
    if min_margin_ratio is not None:
        if not _finite(margin_maint_amount):
            reasons.append("margin_data_incomplete:maint_amount")
        elif float(margin_maint_amount) > 0:                 # 有维持保证金要求才有比率可言
            if not _finite(maint_margin_balance):
                reasons.append("margin_data_incomplete:balance")
            else:
                ratio = float(maint_margin_balance) / float(margin_maint_amount)
                if ratio < float(min_margin_ratio):
                    reasons.append(f"margin_ratio_below_floor:{ratio:.4f}<{float(min_margin_ratio)}")
        # margin_maint_amount<=0 -> 无维持要求 -> 安全,不报

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
