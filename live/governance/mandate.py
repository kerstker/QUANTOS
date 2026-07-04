"""分级授权 mandate(Stage 1)。设计:docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.6。

`PreTradeRiskGate`(风险**权限**)批准之后的第二层——只有已获风控许可的订单才会进入这里评估;
mandate 从不越过 gate 直接放行任何东西,它回答的是"这批已获许可的订单,机械执行需要多少人工
在场"。fail-closed 到底:mandate 缺失/过期/venue 不匹配/任何评估异常 → 恒
`NEEDS_HUMAN_REVIEW`,绝不默认 `AUTO`——这是本文件唯一的安全不变量。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

from live.contracts import LiveOrderRequest
from quantos.contracts.trading import OrderSide

AUTO = "AUTO"
NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


@dataclass(frozen=True)
class Mandate:
    mandate_id: str
    venue: str
    allowed_instruments: frozenset            # 空集 = fail-closed 拒绝一切(同 RiskLimits 惯例)
    # exchange(2026-07-05):mandate 的交易所域。两家交易所的 testnet 都叫 "testnet"——只靠
    # venue 匹配,给 Binance 签的 mandate 会被 Bybit 误认。空串 = 未签署交易所域 -> 恒升级人工
    # (fail-closed;文件加载器把它列为必填)。字段带默认值仅为 dataclass 字段排序,不是许可。
    exchange: str = ""
    max_single_order_notional: float
    max_gross_notional: float
    max_net_notional: float
    max_single_order_notional: float = 0.0
    max_gross_notional: float = 0.0
    max_net_notional: float = 0.0
    per_instrument_max_notional: Tuple[Tuple[str, float], ...] = ()
    approved_by: Tuple[str, ...] = ()         # 签署 mandate 的人工 actor_id(留痕)
    issued_at: int = 0
    expires_at: Optional[int] = None


@dataclass(frozen=True)
class MandateDecision:
    tier: str                                 # AUTO | NEEDS_HUMAN_REVIEW
    reasons: Tuple[str, ...] = ()


def evaluate_mandate(orders: Sequence[LiveOrderRequest], prices: Mapping[str, float],
                     mandate: Optional[Mandate], *, venue: str, exchange: str, now: int,
                     current_positions: Optional[Mapping[str, float]] = None) -> MandateDecision:
    try:
        if mandate is None:
            return MandateDecision(NEEDS_HUMAN_REVIEW, ("mandate_missing",))
        if mandate.expires_at is not None and now > mandate.expires_at:
            return MandateDecision(NEEDS_HUMAN_REVIEW, ("mandate_expired",))
        if venue != mandate.venue:
            return MandateDecision(NEEDS_HUMAN_REVIEW, ("venue_mismatch",))
        # 交易所域(2026-07-05):两家的 testnet 都叫 "testnet",只靠 venue 匹配,给 Binance 签
        # 的 mandate 会被 Bybit 误认——mandate 必须显式签署它授权的交易所。
        if not mandate.exchange:
            return MandateDecision(NEEDS_HUMAN_REVIEW, ("exchange_unset",))
        if exchange != mandate.exchange:
            return MandateDecision(NEEDS_HUMAN_REVIEW, ("exchange_mismatch",))
        if not orders:
            return MandateDecision(AUTO, ())          # 空批次,没什么好审的

        current_positions = current_positions or {}
        per_inst_caps = dict(mandate.per_instrument_max_notional)
        reasons = []
        gross = 0.0
        net = 0.0
        for o in orders:
            if o.instrument_id not in mandate.allowed_instruments:
                reasons.append(f"instrument_not_allowed:{o.instrument_id}")
                continue
            price = prices.get(o.instrument_id)
            if price is None or price <= 0:
                reasons.append(f"no_price:{o.instrument_id}")
                continue
            notional = o.quantity * price
            if notional > mandate.max_single_order_notional:
                reasons.append(f"single_order_notional_exceeded:{o.instrument_id}")
            signed = notional if o.side == OrderSide.BUY else -notional
            gross += abs(notional)
            net += signed
            cap = per_inst_caps.get(o.instrument_id)
            if cap is not None:
                existing_notional = current_positions.get(o.instrument_id, 0.0) * price
                projected = abs(existing_notional + signed)
                if projected > cap:
                    reasons.append(f"per_instrument_cap_exceeded:{o.instrument_id}")

        if gross > mandate.max_gross_notional:
            reasons.append("gross_notional_exceeded")
        if abs(net) > mandate.max_net_notional:
            reasons.append("net_notional_exceeded")

        if reasons:
            return MandateDecision(NEEDS_HUMAN_REVIEW, tuple(reasons))
        return MandateDecision(AUTO, ())
    except Exception as exc:
        # fail-closed:评估过程任何异常 -> 升级人工,绝不默认 AUTO(镜像 PreTradeRiskGate.evaluate
        # 的"异常即拒"哲学,只是这里"拒" = 升级人工而非直接否决)。
        return MandateDecision(NEEDS_HUMAN_REVIEW, (f"evaluation_error:{type(exc).__name__}",))
