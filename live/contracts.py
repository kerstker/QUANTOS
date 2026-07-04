"""live/ 树的 venue-agnostic 值类型(Stage 1)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.1/§3.4。

**两级下单类型,刻意不合并**(单元制不同,合并会掩盖一个真实的数量级问题):
- `quantos.contracts.trading.OrderIntent`(复用,不重造):**风控门货币**,`quantity: int`——
  与既有 `PreTradeRiskGate`/`build_intents` 的整数手数假设一致(那套是为研究管线的大 AUM/大额
  头寸设计的,直接复用能拿到 9 引擎 fail-closed 风控,不必重造)。
- `LiveOrderRequest`(本文件新增):**venue 提交货币**,`quantity: float`——小资金真实分数仓位
  (如 0.015 BTC)必须是浮点,不能被迫凑整数手数(3k-5k 美元的组合里,BTC 若按整币计价会整仓
  错配几个数量级)。`live/pipeline.py` 是唯一跨越这两级的地方:先用**缩放整数**喂 gate 拿批准,
  批准后换算回真实浮点数量,经 `live/execution/filters.py::apply_exchange_filters` 按交易所
  步长/最小名义额取整,产出 `LiveOrderRequest`——这之后一路是浮点,不再回到整数手数。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from quantos.contracts.trading import OrderSide


@dataclass(frozen=True)
class VenueOrderResult:
    venue_order_id: str
    client_order_id: str
    instrument_id: str
    status: str                      # venue 原样字符串(如 "NEW"/"FILLED"/"PARTIALLY_FILLED")
    filled_qty: float = 0.0
    avg_price: float = 0.0
    raw: dict = field(default_factory=dict)   # venue 原始响应,便于排障(不做字段承诺)


@dataclass(frozen=True)
class VenuePosition:
    instrument_id: str
    quantity: float                  # 有符号(多正空负)
    entry_price: float = 0.0


@dataclass(frozen=True)
class ExchangeFilters:
    instrument_id: str
    step_size: float
    min_qty: float
    min_notional: float
    tick_size: Optional[float] = None


@dataclass(frozen=True)
class LiveOrderRequest:
    """已过交易所过滤器取整的、随时可提交的浮点数量订单(恒 MARKET——Stage 1 唯一支持的类型,
    见设计 spec §3.3/§6;不需要 order_type 字段,类型本身就排除了 LIMIT 的歧义)。"""
    instrument_id: str
    side: OrderSide
    quantity: float
    client_order_id: str
