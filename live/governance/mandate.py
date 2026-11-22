"""分级授权 mandate(Stage 1)。

`PreTradeRiskGate`(风险**权限**)批准之后的第二层——只有已获风控许可的订单才会进入这里评估;
mandate 从不越过 gate 直接放行任何东西,它回答的是"这批已获许可的订单,机械执行需要多少人工在场"。

fail-closed 到底:mandate 缺失/过期/venue 不匹配/任何非有限或布尔数值/任何评估异常 → 恒
`NEEDS_HUMAN_REVIEW`,绝不默认 `AUTO`——这是本文件唯一的安全不变量。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

try:
    from live.contracts import LiveOrderRequest
except Exception:  # pragma: no cover
    LiveOrderRequest = object  # type: ignore[misc,assignment]

try:
    from quantos.contracts.trading import OrderSide
except Exception:  # pragma: no cover
    from enum import Enum

    class OrderSide(str, Enum):  # type: ignore[no-redef]
        BUY = "BUY"
        SELL = "SELL"

AUTO = "AUTO"
NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


def _is_finite_real(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


@dataclass(frozen=True)
class Mandate:
    mandate_id: str
    venue: str
    allowed_instruments: frozenset            # 空集 = fail-closed 拒绝一切(同 RiskLimits 惯例)
    exchange: str = ""
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


def evaluate_mandate(
    orders: Sequence[LiveOrderRequest],
    prices: Mapping[str, float],
    mandate: Optional[Mandate],
    *,
    venue: str,
    exchange: str,
    now: int,
    current_positions: Optional[Mapping[str, float]] = None,
) -> MandateDecision:
    try:
        if mandate is None:
            return MandateDecision(NEEDS_HUMAN_REVIEW, ("mandate_missing",))
        if isinstance(now, bool) or not isinstance(now, int):
            return MandateDecision(NEEDS_HUMAN_REVIEW, ("invalid_now_timestamp",))
        if mandate.expires_at is not None:
            if isinstance(mandate.expires_at, bool) or not isinstance(mandate.expires_at, int):
                return MandateDecision(NEEDS_HUMAN_REVIEW, ("invalid_mandate_expiry",))
            if now > mandate.expires_at:
                return MandateDecision(NEEDS_HUMAN_REVIEW, ("mandate_expired",))
        if not venue or venue != mandate.venue:
            return MandateDecision(NEEDS_HUMAN_REVIEW, ("venue_mismatch",))
        if not mandate.exchange:
            return MandateDecision(NEEDS_HUMAN_REVIEW, ("exchange_unset",))
        if not exchange or exchange != mandate.exchange:
            return MandateDecision(NEEDS_HUMAN_REVIEW, ("exchange_mismatch",))

        for _name, _val in (
            ("max_single_order_notional", mandate.max_single_order_notional),
            ("max_gross_notional", mandate.max_gross_notional),
            ("max_net_notional", mandate.max_net_notional),
        ):
            if not _is_finite_real(_val) or float(_val) < 0.0:
                return MandateDecision(NEEDS_HUMAN_REVIEW, (f"non_finite_mandate_field:{_name}",))

        # Validate per_instrument_max_notional upfront so a NaN/negative/bool cap
        # can NEVER silently turn `projected > cap` into False and return AUTO.
        per_inst_caps: dict[str, float] = {}
        for _inst, _cap in mandate.per_instrument_max_notional:
            if not _is_finite_real(_cap) or float(_cap) < 0.0:
                return MandateDecision(NEEDS_HUMAN_REVIEW, (f"non_finite_per_instrument_cap:{_inst}",))
            per_inst_caps[_inst] = float(_cap)

        if not orders:
            return MandateDecision(AUTO, ())          # 空批次,没什么好审的

        current_positions = current_positions or {}
        # Validate all current_positions values upfront: a NaN/Inf/bool position
        # would otherwise poison projected/proj_gross/proj_net into NaN, causing
        # all `>` comparisons to evaluate False and falsely returning AUTO!
        validated_positions: dict[str, float] = {}
        for _inst, _pos in current_positions.items():
            if not _is_finite_real(_pos):
                return MandateDecision(NEEDS_HUMAN_REVIEW, (f"non_finite_position:{_inst}",))
            validated_positions[_inst] = float(_pos)

        reasons: list[str] = []
        gross = 0.0
        net = 0.0
        batch_signed_by_inst: dict[str, float] = {}

        for o in orders:
            order_type = getattr(o, "order_type", "MARKET")
            if order_type not in ("MARKET", "LIMIT"):
                reasons.append(f"unknown_order_type:{o.instrument_id}")
                continue
            if o.instrument_id not in mandate.allowed_instruments:
                reasons.append(f"instrument_not_allowed:{o.instrument_id}")
                continue

            qty = getattr(o, "quantity", None)
            if not _is_finite_real(qty) or float(qty) <= 0.0:
                reasons.append(f"invalid_quantity:{o.instrument_id}")
                continue
            qty_f = float(qty)

            side = getattr(o, "side", None)
            if side == OrderSide.BUY or side == "BUY":
                sign = 1.0
            elif side == OrderSide.SELL or side == "SELL":
                sign = -1.0
            else:
                reasons.append(f"unknown_order_side:{o.instrument_id}")
                continue

            price = prices.get(o.instrument_id)
            if price is None or not _is_finite_real(price) or float(price) <= 0.0:
                reasons.append(f"no_price:{o.instrument_id}")
                continue
            price_f = float(price)

            eval_price = price_f
            if order_type == "LIMIT":
                limit_price = getattr(o, "limit_price", None)
                if limit_price is None or not _is_finite_real(limit_price) or float(limit_price) <= 0.0:
                    reasons.append(f"limit_price_missing:{o.instrument_id}")
                    continue
                eval_price = max(price_f, float(limit_price))

            notional = qty_f * eval_price
            if not math.isfinite(notional):
                reasons.append(f"non_finite_notional:{o.instrument_id}")
                continue
            if notional > float(mandate.max_single_order_notional):
                reasons.append(f"single_order_notional_exceeded:{o.instrument_id}")

            signed = sign * notional
            gross += abs(notional)
            net += signed
            batch_signed_by_inst[o.instrument_id] = (
                batch_signed_by_inst.get(o.instrument_id, 0.0) + signed
            )

            cap = per_inst_caps.get(o.instrument_id)
            if cap is not None:
                existing_notional = validated_positions.get(o.instrument_id, 0.0) * price_f
                projected = abs(existing_notional + signed)
                if not math.isfinite(projected) or projected > cap:
                    reasons.append(f"per_instrument_cap_exceeded:{o.instrument_id}")

        if not math.isfinite(gross) or gross > float(mandate.max_gross_notional):
            reasons.append("gross_notional_exceeded")
        if not math.isfinite(net) or abs(net) > float(mandate.max_net_notional):
            reasons.append("net_notional_exceeded")

        # 账户级投影 gross/net(审计 P1-2)
        signed_by_inst = dict(batch_signed_by_inst)
        for _inst, _pos in validated_positions.items():
            if _pos == 0.0:
                continue
            _p = prices.get(_inst)
            if _p is None or not _is_finite_real(_p) or float(_p) <= 0.0:
                reasons.append(f"account_position_unpriced:{_inst}")
                continue
            signed_by_inst[_inst] = signed_by_inst.get(_inst, 0.0) + _pos * float(_p)

        proj_gross = sum(abs(v) for v in signed_by_inst.values())
        proj_net = sum(signed_by_inst.values())
        if not math.isfinite(proj_gross) or proj_gross > float(mandate.max_gross_notional):
            reasons.append("account_gross_notional_exceeded")
        if not math.isfinite(proj_net) or abs(proj_net) > float(mandate.max_net_notional):
            reasons.append("account_net_notional_exceeded")

        if reasons:
            return MandateDecision(NEEDS_HUMAN_REVIEW, tuple(reasons))
        return MandateDecision(AUTO, ())
    except Exception as exc:
        return MandateDecision(NEEDS_HUMAN_REVIEW, (f"evaluation_error:{type(exc).__name__}",))
