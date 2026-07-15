"""live/ 树的 venue-agnostic 值类型(Stage 1)。设计:
docs/engineering/specs/2026-07-04-live-execution-stack-design.md §3.1/§3.4。

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

import math
from dataclasses import dataclass, field
from typing import Optional

from quantos.contracts.trading import OrderSide

_VALID_ORDER_TYPES = frozenset({"MARKET", "LIMIT"})


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
    """已过交易所过滤器取整的、随时可提交的浮点数量订单。设计:
    docs/engineering/specs/2026-07-06-limit-order-execution-design.md §1(LIMIT 扩展)。

    reduce_only(2026-07-05 紧急减仓通道):True 时适配器把交易所原生 reduceOnly 参数一并传出,
    交易所侧保证该单只能减仓、不能开新仓——是 `live/execution/emergency.py` 结构校验之外的
    第二重保证。普通订单恒 False(适配器此时不带该参数,请求形状与已真实联调验证的完全一致)。

    order_type/limit_price/post_only(2026-07-06 战线②,默认路径字节不变):
    - `order_type`(默认 "MARKET"):不传 == 改动前的构造调用完全等价。"LIMIT" 是唯一的
      另一个合法值——本设计的最小可用版是可成交限价(marketable limit),不是做市。
    - `limit_price`:仅 LIMIT 使用,构造期 fail-closed 校验(finite ∧ >0),已按 tick 取整
      的真实价格。MARKET 携带非 None 值是构造错误(fail-closed 最前沿,后续 mandate/adapter
      只是纵深防御)。
    - `post_only`:仅 LIMIT 使用(Binance GTX / Bybit PostOnly),字段预留、驱动器不接。
    """
    instrument_id: str
    side: OrderSide
    quantity: float
    client_order_id: str
    reduce_only: bool = False
    order_type: str = "MARKET"
    limit_price: Optional[float] = None
    post_only: bool = False

    def __post_init__(self) -> None:
        if self.order_type not in _VALID_ORDER_TYPES:
            raise ValueError(f"unknown order_type {self.order_type!r} (expected one of "
                            f"{sorted(_VALID_ORDER_TYPES)})")
        if self.order_type == "LIMIT":
            if (self.limit_price is None or not math.isfinite(self.limit_price)
                    or self.limit_price <= 0):
                raise ValueError(
                    f"LIMIT order requires a finite positive limit_price, got {self.limit_price!r}")
        else:
            if self.limit_price is not None or self.post_only:
                raise ValueError(
                    "MARKET order must not carry limit_price/post_only "
                    f"(got limit_price={self.limit_price!r}, post_only={self.post_only!r})")
