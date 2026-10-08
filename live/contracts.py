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

    def __post_init__(self) -> None:
        # P1-8:成交数量/均价的有限性守卫。0.0 合法(未成交、以及 PREVIEW 路径
        # tools/live_track_advance.py::PreviewVenue 如实报 0.0);inf/nan 或负值不是
        # "venue 报了什么"的合法读数,记进账本会污染后续求和与名义额比较。
        for name in ("filled_qty", "avg_price"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative, got {value!r}")


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
    # MEXC Phase 8 M1(2026-07-27,加法扩展):交易所按 symbol 报的成交费率(小数,
    # 如 0.0 = 零手续费;MEXC exchangeInfo 携带 makerCommission/takerCommission)。
    # 其他 venue 不解析 → 诚实 None(缺费率证据,不编造 0)。只读诊断输入,
    # 不在下单/定价路径上消费。
    maker_commission: Optional[float] = None
    taker_commission: Optional[float] = None


@dataclass(frozen=True)
class PremiumIndexSnapshot:
    """Binance `GET /fapi/v1/premiumIndex` 的只读快照(标记价/指数价/最新资金费)——
    定价与诊断输入,不在下单路径上。时间均为 ms epoch int;价格/费率为 float(finite 校验)。"""
    instrument_id: str
    mark_price: float
    index_price: float
    last_funding_rate: float
    next_funding_time_ms: int
    time_ms: int

    def __post_init__(self) -> None:
        for name in ("mark_price", "index_price", "last_funding_rate"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite, got {getattr(self, name)!r}")


@dataclass(frozen=True)
class BookLevel:
    """订单簿一档(价格 + 挂单量,SHADOW 深度读数的原子单位,2026-07-27 Phase 7 S1)。

    fail-closed:价格/挂单量必须有限且 > 0——REST 深度快照里的零量/负量/非有限档是
    垃圾数据(深度快照的「删除档」语义只存在于增量推送流,REST 快照不带),宁可整簿
    fail-soft None 也不把它交进比较核(适配器据此整体返回 None)。"""
    price: float
    size: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.price) or self.price <= 0:
            raise ValueError(f"BookLevel price must be finite and positive, got {self.price!r}")
        if not math.isfinite(self.size) or self.size <= 0:
            raise ValueError(f"BookLevel size must be finite and positive, got {self.size!r}")


@dataclass(frozen=True)
class OrderBookSnapshot:
    """一份 venue REST 深度快照(bids/asks 各自按 venue 给出的最优在前顺序,SHADOW
    比较核的输入,2026-07-27 Phase 7 S1)。

    - `as_of_ms`:venue 载荷自带的时间戳(如 Binance 的 `E`);载荷不带 → None,
      **绝不自造**(调用方拿不到来源时间就不能做 staleness 判定,诚实缺失)。
    - `source`:venue 标签常量("mexc"/"binance"/"bybit"),构造期 fail-closed 非空。
    - **刻意不拒绝瞬时交叉盘口**(best_ask < best_bid,交易所快速行情下真实存在):
      快照只承载「venue 报了什么」,可执行性判定(crossed → CROSSED_BOOK)归
      `live/execution/shadow.py::walk_the_book`,分层单一职责。
    - 两侧允许为空簿(空簿是 venue 的合法回答,EMPTY_BOOK 判定同样在 walk_the_book);
      适配器 fail-soft 政策更严(任一侧空簿即 None),两层语义不冲突。
    """
    instrument_id: str
    bids: tuple = ()                       # tuple[BookLevel, ...],买一在前(venue 原序)
    asks: tuple = ()                       # tuple[BookLevel, ...],卖一在前(venue 原序)
    as_of_ms: Optional[int] = None
    source: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not self.instrument_id.strip():
            raise ValueError("OrderBookSnapshot instrument_id must be non-empty")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("OrderBookSnapshot source must be non-empty")
        for name in ("bids", "asks"):
            levels = getattr(self, name)
            if not isinstance(levels, tuple):
                raise ValueError(f"OrderBookSnapshot {name} must be a tuple of BookLevel")
            for lvl in levels:
                if not isinstance(lvl, BookLevel):
                    raise ValueError(f"OrderBookSnapshot {name} entries must be BookLevel, "
                                     f"got {type(lvl).__name__}")
        if self.as_of_ms is not None:
            if isinstance(self.as_of_ms, bool) or int(self.as_of_ms) != self.as_of_ms \
                    or self.as_of_ms <= 0:
                raise ValueError(f"OrderBookSnapshot as_of_ms must be a positive integer, "
                                 f"got {self.as_of_ms!r}")


@dataclass(frozen=True)
class FundingRateSnapshot:
    """Binance `GET /fapi/v1/fundingRate` 历史资金费单条记录——离线研究/干跑校准输入,
    不在下单路径上。"""
    instrument_id: str
    funding_rate: float
    funding_time_ms: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.funding_rate):
            raise ValueError(f"funding_rate must be finite, got {self.funding_rate!r}")


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
        # P1-8 身份守卫:这是 venue 提交货币,构造期 fail-closed 比适配器更前沿——
        #   - side 必须是 OrderSide 枚举(字符串 "BUY" 会在适配器取 .value 时才炸);
        #   - quantity 必须是真实 int/float(bool 是 int 子类,显式排除;Decimal/str
        #     会在下游 round_to_step/格式化的 math 运算里炸),且有限且 >0——nan 让
        #     `qty > min_qty` 恒 False,inf 让名义额比较失去意义;
        #   - 两个 ID 去空白后必须非空(空 ID 到交易所只会变成不可对账的孤儿单)。
        if not isinstance(self.side, OrderSide):
            raise ValueError(f"side must be an OrderSide, got {self.side!r}")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, (int, float)):
            raise ValueError("quantity must be a real number (int/float, not bool), got "
                             f"{type(self.quantity).__name__}: {self.quantity!r}")
        if not math.isfinite(self.quantity) or self.quantity <= 0:
            raise ValueError(f"quantity must be finite and positive, got {self.quantity!r}")
        for name in ("instrument_id", "client_order_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string, got {value!r}")
