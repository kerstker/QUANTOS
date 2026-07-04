"""交易所过滤器 + 冪等 client_order_id(Stage 1)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.4。

纯函数、无网络、无墙钟——输入输出皆为真实浮点数量(见 contracts.py 顶部关于两级下单类型的
说明)。`apply_exchange_filters` 是唯一把"策略想要的数量"折算成"交易所肯接受的数量"的地方:
步长取整、低于最小名义额/最小数量 fail-soft 丢弃(附原因,绝不因单笔失败中断整批)。
"""
from __future__ import annotations

import hashlib
import math
from typing import Dict, List, Mapping, Sequence, Tuple

from live.contracts import ExchangeFilters, LiveOrderRequest
from quantos.contracts.trading import OrderSide

_MAX_CLIENT_ORDER_ID_LEN = 36     # Binance newClientOrderId 硬限制


def client_order_id(strategy_id: str, decided_ts: int, instrument_id: str) -> str:
    """确定性哈希:同一决策(策略+决策时间+标的)重试产生同一 ID,交易所天然去重、不会重复下单。
    纯 hex(无特殊字符),长度 32 < 36 限制,安全裕量。"""
    key = f"{strategy_id}|{decided_ts}|{instrument_id}".encode()
    return hashlib.sha256(key).hexdigest()[:32]


def round_to_step(qty: float, step_size: float) -> float:
    """向下取整到交易所步长网格(绝不超发——宁可少买/少卖,不可超过策略意图)。step_size<=0 直通。
    +1e-9 epsilon 只为吸收浮点表示噪声(如 0.3/0.1 因二进制浮点略小于 3),不改变真实取整方向。"""
    if step_size <= 0:
        return qty
    steps = math.floor(qty / step_size + 1e-9)
    return round(steps * step_size, 10)


def apply_exchange_filters(desired: Sequence[Tuple[str, OrderSide, float]],
                           filters: Mapping[str, ExchangeFilters],
                           prices: Mapping[str, float], *,
                           strategy_id: str, decided_ts: int
                           ) -> Tuple[List[LiveOrderRequest], List[Tuple[str, str]]]:
    """`desired` = [(instrument_id, side, real_qty), ...](真实浮点数量,取整前)。

    每笔独立评估,fail-soft:任何一笔被丢弃都不影响其它笔。丢弃原因(附在 dropped 里):
    "no_filter"(交易所未提供该标的过滤器)/ "below_min_qty"(取整后低于最小数量,含取整到 0)/
    "no_price"(缺参考价,无法算名义额)/ "below_min_notional"(数量*价格低于最小名义额)。
    """
    adjusted: List[LiveOrderRequest] = []
    dropped: List[Tuple[str, str]] = []
    for instrument_id, side, qty in desired:
        filt = filters.get(instrument_id)
        if filt is None:
            dropped.append((instrument_id, "no_filter"))
            continue
        rounded = round_to_step(qty, filt.step_size)
        if rounded <= 0 or rounded < filt.min_qty:
            dropped.append((instrument_id, "below_min_qty"))
            continue
        price = prices.get(instrument_id)
        if price is None or price <= 0:
            dropped.append((instrument_id, "no_price"))
            continue
        if rounded * price < filt.min_notional:
            dropped.append((instrument_id, "below_min_notional"))
            continue
        cid = client_order_id(strategy_id, decided_ts, instrument_id)
        adjusted.append(LiveOrderRequest(instrument_id=instrument_id, side=side,
                                         quantity=rounded, client_order_id=cid))
    return adjusted, dropped
