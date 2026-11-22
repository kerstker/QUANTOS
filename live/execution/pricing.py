"""LIMIT 定价(2026-07-06 战线②)。设计:
docs/engineering/specs/2026-07-06-limit-order-execution-design.md §2。

纯函数、无网络/墙钟——anchor(盘口锚)由调用方(`live/pipeline.py`)装配好后传入,本文件
不碰 `venue.book_ticker()`。

**LIMIT 的经济定位(诚实):** 限价单把成本从"滑点"换成"不成交风险"。本文件实现的是
**可成交限价(marketable limit)= 市价单加价格上限**,不是做市/吃返佣;`post_only` 只是
字段预留(参数会流到 `LiveOrderRequest`,但驱动器默认不启用)。

**锚校验必须防"成功但退化"的响应,不只防网络失败(2026-07-06 对抗式批判必须修改①):**
testnet/demo 环境流动性稀薄,`book_ticker()` 完全可能返回一个形状正确、内容错误的 200
(交叉盘 ask<bid、零价/负价、或与决策价偏离离谱的天价 mid)。`resolve_anchor` 把这类响应
和网络失败同等对待——结构校验(bid>0 ∧ ask>0 ∧ ask>=bid ∧ mid 有限)+ 与决策价的偏离
容忍度门,任何一项不过 -> 回退决策价锚(不是直接判失败:决策价本身仍是可信的备用锚)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import List, Mapping, Optional, Sequence, Tuple

from live.contracts import ExchangeFilters, LiveOrderRequest
from live.execution.filters import client_order_id
from quantos.contracts.trading import OrderSide

DEFAULT_BAND_PCT = 0.002              # 工程默认 20bps,非业务风险数字(见 open questions)
# 工程占位容忍度(非业务风险容忍度)——testnet/demo 流动性稀薄,盘口偶发退化(交叉盘/零价/
# 与决策价偏离离谱)是常态,不是异常;5% 是"明显退化 vs 正常波动"的粗分界,不是精确校准值。
DEFAULT_ANCHOR_MAX_DEVIATION_PCT = 0.05


def _finite_num(value: object) -> bool:
    return (isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value)))


@dataclass(frozen=True)
class LimitPricing:
    band_pct: float = DEFAULT_BAND_PCT
    post_only: bool = False
    anchor_max_deviation_pct: float = DEFAULT_ANCHOR_MAX_DEVIATION_PCT

    def __post_init__(self) -> None:
        if not _finite_num(self.band_pct):
            raise ValueError(f"band_pct must be a finite number, got {self.band_pct!r}")
        if (not _finite_num(self.anchor_max_deviation_pct)
                or float(self.anchor_max_deviation_pct) < 0.0):
            raise ValueError(
                f"anchor_max_deviation_pct must be finite and >= 0, got {self.anchor_max_deviation_pct!r}"
            )


def round_price_to_tick(price: float, tick_size: Optional[float], side: OrderSide) -> float:
    """BUY 向下取整到 tick(绝不出比意图更高的买价)、SELL 向上取整(绝不出更低的卖价);
    tick_size None/<=0 直通。+-1e-9 epsilon 吸收浮点表示噪声(镜像 filters.round_to_step),
    不改变真实取整方向。"""
    if not _finite_num(price):
        raise ValueError(f"price must be a finite number, got {price!r}")
    if tick_size is not None and not _finite_num(tick_size):
        raise ValueError(f"tick_size must be a finite number or None, got {tick_size!r}")
    price_f = float(price)
    if tick_size is None or float(tick_size) <= 0:
        return price_f
    tick_f = float(tick_size)
    if side == OrderSide.BUY:
        steps = math.floor(price_f / tick_f + 1e-9)
    else:
        steps = math.ceil(price_f / tick_f - 1e-9)
    return round(steps * tick_f, 10)


def _validate_book_ticker(book: Optional[Mapping], *, decision_price: Optional[float],
                          max_deviation_pct: float) -> Optional[float]:
    """book_ticker 的"成功"响应本身可能退化——诚实拒绝,而不是照单全收。返回校验通过的
    mid,或 None(退化/不可用)。"""
    if book is None:
        return None
    if not _finite_num(max_deviation_pct) or float(max_deviation_pct) < 0.0:
        return None
    try:
        raw_bid = book.get("bid")
        raw_ask = book.get("ask")
    except AttributeError:
        return None
    if isinstance(raw_bid, bool) or isinstance(raw_ask, bool):
        return None
    try:
        bid = float(raw_bid)
        ask = float(raw_ask)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(bid) and math.isfinite(ask)):
        return None
    if bid <= 0 or ask <= 0 or ask < bid:            # 交叉盘/零价/负价 -> 结构退化
        return None
    mid = (bid + ask) / 2.0
    if not math.isfinite(mid) or mid <= 0:
        return None
    if _finite_num(decision_price) and float(decision_price) > 0:
        if abs(mid / float(decision_price) - 1.0) > float(max_deviation_pct):
            return None                              # 与决策价偏离过大(天价 mid)-> 视同退化
    return mid


def resolve_anchor(book: Optional[Mapping], *, decision_price: Optional[float],
                   max_deviation_pct: float = DEFAULT_ANCHOR_MAX_DEVIATION_PCT
                   ) -> Tuple[Optional[float], str]:
    """anchor 来源顺序:① book_ticker 的校验通过的 mid(首选);② 结构退化/网络失败/偏离过大
    -> 回退决策价(若其本身可用);③ 两者都不可用 -> (None, "no_anchor")(调用方据此丢弃该腿,
    不静默造出一个假锚)。"""
    mid = _validate_book_ticker(book, decision_price=decision_price,
                                max_deviation_pct=max_deviation_pct)
    if mid is not None:
        return mid, "book_ticker"
    if _finite_num(decision_price) and float(decision_price) > 0:
        return float(decision_price), "decision_price_fallback"
    return None, "no_anchor"


def limit_price_for(side: OrderSide, *, anchor: Optional[float], band_pct: float,
                    tick_size: Optional[float]) -> Optional[float]:
    """anchor×(1+band) for BUY / ×(1-band) for SELL,再按方向取整到 tick。anchor 非有限/<=0
    或取整后 <=0 -> None(诚实缺失,调用方丢弃该腿)。"""
    if not _finite_num(anchor) or float(anchor) <= 0:
        return None
    if not _finite_num(band_pct):
        return None
    if tick_size is not None and not _finite_num(tick_size):
        return None
    anchor_f = float(anchor)
    band_f = float(band_pct)
    raw = anchor_f * (1 + band_f) if side == OrderSide.BUY else anchor_f * (1 - band_f)
    if not math.isfinite(raw) or raw <= 0:
        return None
    rounded = round_price_to_tick(raw, tick_size, side)
    if not math.isfinite(rounded) or rounded <= 0:
        return None
    return rounded


def to_limit_orders(adjusted: Sequence[LiveOrderRequest], *, anchors: Mapping[str, float],
                    filters: Mapping[str, ExchangeFilters], pricing: LimitPricing,
                    strategy_id: str, decided_ts: int,
                    current_positions: Optional[Mapping[str, float]] = None
                    ) -> Tuple[List[LiveOrderRequest], List[Tuple[str, str]]]:
    """MARKET 请求 -> LIMIT 请求(逐笔 fail-soft):anchor 缺失/定价失败 -> 丢弃并附原因
    "no_limit_anchor"(**不静默回退 MARKET**——用户显式要了价格上限,悄悄摘掉它是欺骗);
    成功的腿重算 client_order_id(限价参与订单身份,见 filters.client_order_id)后
    `dataclasses.replace` 出 LIMIT 单。"""
    out: List[LiveOrderRequest] = []
    dropped: List[Tuple[str, str]] = []
    for o in adjusted:
        anchor = anchors.get(o.instrument_id)
        filt = filters.get(o.instrument_id)
        tick_size = filt.tick_size if filt is not None else None
        price = limit_price_for(o.side, anchor=anchor, band_pct=pricing.band_pct,
                                tick_size=tick_size)
        if price is None:
            dropped.append((o.instrument_id, "no_limit_anchor"))
            continue
        prior = (None if current_positions is None
                 else float(current_positions.get(o.instrument_id, 0.0)))
        cid = client_order_id(strategy_id, decided_ts, o.instrument_id, o.side, o.quantity,
                              limit_price=price, prior_qty=prior)
        out.append(replace(o, order_type="LIMIT", limit_price=price, post_only=pricing.post_only,
                           client_order_id=cid))
    return out, dropped
