"""分级授权 mandate(Stage 1)。设计:docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.6。

`PreTradeRiskGate`(风险**权限**)批准之后的第二层——只有已获风控许可的订单才会进入这里评估;
mandate 从不越过 gate 直接放行任何东西,它回答的是"这批已获许可的订单,机械执行需要多少人工
在场"。fail-closed 到底:mandate 缺失/过期/venue 不匹配/任何评估异常 → 恒
`NEEDS_HUMAN_REVIEW`,绝不默认 `AUTO`——这是本文件唯一的安全不变量。
"""
from __future__ import annotations

import math
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
        # 2026-07-05 安全审查 HIGH 修复,纵深防御:mandate_store.load_mandate 已经在加载时
        # 拒绝 NaN/Infinity 阈值,但 Mandate 也可能被直接构造(不经过那道关卡)——NaN 参与
        # `>`/`abs()>` 比较恒为 False,会让"任意大小订单都未超限"静默成立且不触发下面的
        # fail-closed 分支。这里不假设调用方已经把关。
        for _name, _val in (("max_single_order_notional", mandate.max_single_order_notional),
                            ("max_gross_notional", mandate.max_gross_notional),
                            ("max_net_notional", mandate.max_net_notional)):
            if not math.isfinite(_val):
                return MandateDecision(NEEDS_HUMAN_REVIEW, (f"non_finite_mandate_field:{_name}",))

        if not orders:
            return MandateDecision(AUTO, ())          # 空批次,没什么好审的

        current_positions = current_positions or {}
        per_inst_caps = dict(mandate.per_instrument_max_notional)
        reasons = []
        gross = 0.0
        net = 0.0
        for o in orders:
            # 未知 order_type 恒升人工(2026-07-06 战线②,§5 纵深防御——只收紧不放宽):
            # 真实构造路径已被 `LiveOrderRequest.__post_init__` 挡死(fail-closed 最前沿),
            # 这里防的是"万一有别的构造路径/duck-typed 调用方"绕过契约校验。
            order_type = getattr(o, "order_type", "MARKET")
            if order_type not in ("MARKET", "LIMIT"):
                reasons.append(f"unknown_order_type:{o.instrument_id}")
                continue
            if o.instrument_id not in mandate.allowed_instruments:
                reasons.append(f"instrument_not_allowed:{o.instrument_id}")
                continue
            price = prices.get(o.instrument_id)
            # NaN/Infinity 参与 `price <= 0` 比较恒为 False——必须显式检查有限性,否则一个
            # 坏掉的行情源(NaN/inf 价格)会被当成"价格合法"直接放行(2026-07-05 安全审查)。
            if price is None or not math.isfinite(price) or price <= 0:
                reasons.append(f"no_price:{o.instrument_id}")
                continue
            # LIMIT 名义额口径(2026-07-06 战线②引入,2026-07-06 对抗式复审 CONFIRMED 修复):
            # 偏差源是锚(实时盘口 mid)相对决策价的漂移(决策价可能滞后 1-3 天,是百分点级而非
            # band 的数十 bps 级),不是 band 本身。**恒取 max(决策价,限价)——两侧同一公式,
            # 不按方向分支。** 名义额是无符号的"经济敞口大小"(quantity 恒为正,方向由 side
            # 单独表达),"往名义额更大的方向保守评估"这个设计目标本身就与买卖方向无关——取
            # 两个候选价里更大的那个,才是唯一在两个方向上都不放松的选择。此前 SELL 分支写成
            # min(决策价,限价)是一个方向性 bug:当市场在决策之后上涨(限价随实时盘口走高,
            # 高于滞后的决策价)时,min() 会捡回那个更小、更过时的决策价,把真实更大的经济
            # 敞口算小,恰好让本该升人工的超限 SELL 单被误判 AUTO——三个独立维度的对抗式复审
            # 各自独立复现了这个反例并全票确认。§2 的锚容忍度门(`resolve_anchor`)兜住锚本身
            # 离谱失控的情形,这里是纵深防御第二层,与方向无关。
            eval_price = price
            if order_type == "LIMIT":
                limit_price = getattr(o, "limit_price", None)
                # 真实构造路径已被 `LiveOrderRequest.__post_init__` 保证 LIMIT 恒带合法
                # limit_price——这里遇到缺失/非法值只可能来自绕过契约的 duck-typed 调用方,
                # 与"未知 order_type"同一性质的可疑输入,同样恒升人工(fail-closed,不悄悄
                # 退化成按决策价评估——那会让一个本该显眼的异常状态被无声吞掉)。
                if limit_price is None or not math.isfinite(limit_price) or limit_price <= 0:
                    reasons.append(f"limit_price_missing:{o.instrument_id}")
                    continue
                eval_price = max(price, limit_price)
            notional = o.quantity * eval_price
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
