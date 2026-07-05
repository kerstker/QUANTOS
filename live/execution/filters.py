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


def decision_prefix(strategy_id: str, decided_ts: int, instrument_id: str) -> str:
    """同一决策(策略+决策时间+标的)下所有订单共享的 16 位前缀——live-paper 对账用它把订单
    挂回决策腿(一条腿可能因调仓/重试产生多笔不同数量的订单)。"""
    key = f"{strategy_id}|{decided_ts}|{instrument_id}".encode()
    return hashlib.sha256(key).hexdigest()[:16]


def client_order_id(strategy_id: str, decided_ts: int, instrument_id: str,
                    side, quantity: float) -> str:
    """确定性订单 ID = 决策前缀 16 位 + 订单身份(方向+数量)16 位,共 32 位 hex(<36 上限,
    留切单 -sNN 后缀余量)。

    **为什么必须包含方向+数量(2026-07-05 双所真实调仓暴露的关键 bug):** 旧键只有
    (策略, 决策时间, 标的)——同一决策时间下的第二批订单(mandate 提额后的调仓增量)与第一批
    **同 ID**:Binance 允许已成交 ID 复用,新单真实执行但账本 INSERT OR IGNORE 把记账当重复
    静默吞掉(仓位漂移不报错);Bybit 直接按 orderLinkId 去重没执行,轮询还查到旧单的 FILLED
    误报成功。正确的幂等语义是"同一笔订单的重试去重",订单身份 = 决策 + 方向 + 数量。"""
    ident = f"{getattr(side, 'value', side)}|{quantity:.10f}".encode()
    return decision_prefix(strategy_id, decided_ts, instrument_id) + \
        hashlib.sha256(ident).hexdigest()[:16]


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
        cid = client_order_id(strategy_id, decided_ts, instrument_id, side, rounded)
        adjusted.append(LiveOrderRequest(instrument_id=instrument_id, side=side,
                                         quantity=rounded, client_order_id=cid))
    return adjusted, dropped
