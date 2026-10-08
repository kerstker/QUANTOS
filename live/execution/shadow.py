"""SHADOW 比较纯核(727 §12.2,Phase 7 S1,2026-07-27)。设计:
docs/engineering/specs/2026-07-27-shadow-phase7-design.md。

回答的问题(727 §12.2「必须比较」清单的机器落地):**同一条腿,纸面(paper)语义说它
在决策价成交,真实盘口此刻实际能给它什么价?** ——理论成交 / Paper fill / 实时可成交价
(VWAP)/ 盘口深度 / Spread / Slippage / Latency / Expected impact,八项一次观测全出。

本模块是纯函数、无 IO/网络/墙钟/RNG——时间一律调用方 int ms 注入;`OrderBookSnapshot`
由调用方(IO 边界,S3 的 tools/ 驱动器)经 venue 适配器 `depth()` 采集后传入。

**「paper fill = 决策价」语义缺口(本模块存在的理由,诚实注记)**:当前 paper/干跑语义
把成交价钉死在决策价上——不含任何冲击、点差、深度约束。SHADOW 观测**不修复**这个语义,
它量化这个缺口:`slippage_bps_vs_decision` 就是「纸面说的价 vs 盘口实际给的价」的
带方向差额。paper_fill_price 字段显式记录 = decision_price(当前语义,如实),留待
后续 paper 语义升级时对照。

诚实纪律(与 live/execution/degradation.py 同款 UNKNOWN 纪律):
- 盘口拿不到(None)→ 全部盘口派生字段 None + reason=BOOK_UNAVAILABLE,**绝不**拿
  决策价/中间价冒充可成交价。
- 盘口过旧(age > max_book_age_ms)→ 可执行字段 None + BOOK_STALE;盘口时间戳在
  未来 → BOOK_FROM_FUTURE(镜像 tca.validate_arrival_snapshot 的 fail-closed 姿态)。
  过期/未来簿的 mid/spread 仍如实报告(它们描述的是「那份旧簿长什么样」),只有
  「拿它当此刻可成交价用」的字段被关死。
- 深度不足 → executable=False + unfilled_quantity + INSUFFICIENT_DEPTH,**绝不**为未
  成交部分编造价格(WalkBookResult.vwap 只在全额成交时给出)。
- expected_impact_bps 只在调用方同时给 impact_model **和** adv 时计算;缺任一 →
  None(UNCALIBRATED 输入绝不编造)。adv 非正 → None。
- provenance 恒 "SHADOW"(727 §18.2 provenance 标签),构造期 fail-closed 校验。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple

from live.contracts import OrderBookSnapshot
from live.execution.tca import slippage_bps as _tca_slippage_bps
from quantos.contracts.trading import OrderSide

PROVENANCE_SHADOW = "SHADOW"          # 727 §18.2 provenance 标签,每条观测必带
_SIDES = ("BUY", "SELL")
DEFAULT_MAX_BOOK_AGE_MS = 60_000      # 盘口新鲜度默认阈(UNCALIBRATED 工程默认,调用方可覆盖)


@dataclass(frozen=True)
class ShadowLeg:
    """一条待观测的腿(venue 提交口径:浮点数量,与 LiveOrderRequest 同单位制——
    不是 quantos/contracts/paper.py 的整数手数风控口径)。"""
    instrument_id: str
    side: str                # "BUY" | "SELL"
    quantity: float
    decision_price: float

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not self.instrument_id.strip():
            raise ValueError("ShadowLeg instrument_id must be non-empty")
        if self.side not in _SIDES:
            raise ValueError(f"ShadowLeg side must be one of {_SIDES}, got {self.side!r}")
        if isinstance(self.quantity, bool) or not math.isfinite(float(self.quantity)) \
                or self.quantity <= 0:
            raise ValueError(f"ShadowLeg quantity must be finite and positive, "
                             f"got {self.quantity!r}")
        if isinstance(self.decision_price, bool) or not math.isfinite(float(self.decision_price)) \
                or self.decision_price <= 0:
            raise ValueError(f"ShadowLeg decision_price must be finite and positive, "
                             f"got {self.decision_price!r}")


@dataclass(frozen=True)
class WalkBookResult:
    """沿盘口逐档吃掉需求量的结果。`vwap` 只在 executable=True(全额成交)时给出——
    深度不足时绝不把「已吃掉部分的均价」冒充整腿成交价(未成交部分没有价格,
    编造它就是编造成交)。"""
    instrument_id: str
    executable: bool
    vwap: Optional[float]
    filled_quantity: float
    unfilled_quantity: float
    levels_consumed: int
    reason: str              # "" = 全额成交;"EMPTY_BOOK"|"CROSSED_BOOK"|"INSUFFICIENT_DEPTH"
                             # |"BOOK_UNAVAILABLE"|"BOOK_STALE"|"BOOK_FROM_FUTURE"


def walk_the_book(book: OrderBookSnapshot, *, side: str, quantity: float) -> WalkBookResult:
    """沿盘口逐档成交:BUY 吃 asks、SELL 吃 bids,按快照给出的 venue 原序(最优在前,
    **不重排**——重排会掩盖 venue 报序异常,确定性核心只消费 venue 报的事实)。

    - 目标侧空簿 → EMPTY_BOOK;两侧都在且 best_ask < best_bid → CROSSED_BOOK
      (瞬时交叉簿不可作为成交依据,fail-closed);
    - 深度不足 → INSUFFICIENT_DEPTH + filled/unfilled 拆账,vwap=None;
    - 全额成交 → executable=True + vwap(消费档的量加权均价)。
    side 非 BUY/SELL、quantity 非有限正数 → ValueError(fail-closed 构造错误,不是
    市场状态,绝不静默成行)。
    """
    if side not in _SIDES:
        raise ValueError(f"side must be one of {_SIDES}, got {side!r}")
    if isinstance(quantity, bool) or not math.isfinite(float(quantity)) or quantity <= 0:
        raise ValueError(f"quantity must be finite and positive, got {quantity!r}")
    levels = book.asks if side == "BUY" else book.bids
    if not levels:
        return WalkBookResult(instrument_id=book.instrument_id, executable=False, vwap=None,
                              filled_quantity=0.0, unfilled_quantity=float(quantity),
                              levels_consumed=0, reason="EMPTY_BOOK")
    if book.bids and book.asks and book.asks[0].price < book.bids[0].price:
        return WalkBookResult(instrument_id=book.instrument_id, executable=False, vwap=None,
                              filled_quantity=0.0, unfilled_quantity=float(quantity),
                              levels_consumed=0, reason="CROSSED_BOOK")
    remaining = float(quantity)
    cost = 0.0
    filled = 0.0
    consumed = 0
    for lvl in levels:
        take = min(lvl.size, remaining)
        cost += take * lvl.price
        filled += take
        remaining -= take
        consumed += 1
        if remaining <= 0.0:
            break
    if remaining > 0.0:
        return WalkBookResult(instrument_id=book.instrument_id, executable=False, vwap=None,
                              filled_quantity=filled, unfilled_quantity=remaining,
                              levels_consumed=consumed, reason="INSUFFICIENT_DEPTH")
    return WalkBookResult(instrument_id=book.instrument_id, executable=True,
                          vwap=cost / filled, filled_quantity=filled, unfilled_quantity=0.0,
                          levels_consumed=consumed, reason="")


@dataclass(frozen=True)
class ShadowObservation:
    """一条腿的完整 SHADOW 观测(727 §12.2 八项比较的一行):

    - `paper_fill_price` = `decision_price`(当前 paper 语义,如实记录,见模块 docstring);
    - `executable_vwap` = 此刻盘口实际给的全额成交价(None = 不可成交,walk.reason 有因);
    - `slippage_bps_vs_decision` = tca.slippage_bps 同语义(executable_vwap 相对决策价,
      买贵/卖贱为正 = 成本,负 = 改善)——**这就是「paper fill = 决策价」缺口的量化**;
    - `expected_impact_bps` 只在 impact_model + adv 双给时计算(模型鸭子类型:
      temporary_bps(notional, adv) + permanent_bps(notional, adv),
      services/execution/impact.py::AlmgrenChrissImpact 满足;本模块不 import services/,
      保持 live/execution 纯核零外依);
    - `latency_ms` 调用方实测(IO 边界),int;
    - `book_age_ms` = now_ms − as_of_ms;as_of_ms 缺失 → None(无来源时间不可判龄,
      诚实缺失)。
    """
    instrument_id: str
    side: str
    quantity: float
    decision_price: float
    paper_fill_price: float
    book_mid: Optional[float]
    book_spread_bps: Optional[float]
    executable_vwap: Optional[float]
    slippage_bps_vs_decision: Optional[float]
    walk: WalkBookResult
    expected_impact_bps: Optional[float]
    latency_ms: int
    book_age_ms: Optional[int]
    as_of_ms: Optional[int]
    provenance: str = PROVENANCE_SHADOW

    def __post_init__(self) -> None:
        if self.provenance != PROVENANCE_SHADOW:
            raise ValueError(f"provenance must be {PROVENANCE_SHADOW!r} (727 §18.2), "
                             f"got {self.provenance!r}")


def _require_int_ms(name: str, value: int, *, allow_negative: bool = False) -> int:
    if isinstance(value, bool) or int(value) != value:
        raise ValueError(f"{name} must be an integer ms timestamp, got {value!r}")
    value = int(value)
    if not allow_negative and value < 0:
        raise ValueError(f"{name} must be non-negative, got {value!r}")
    return value


def _unexecutable_observation(leg: ShadowLeg, reason: str, *, latency_ms: int,
                              book: Optional[OrderBookSnapshot],
                              book_age_ms: Optional[int],
                              book_mid: Optional[float],
                              book_spread_bps: Optional[float]) -> ShadowObservation:
    walk = WalkBookResult(instrument_id=leg.instrument_id, executable=False, vwap=None,
                          filled_quantity=0.0, unfilled_quantity=float(leg.quantity),
                          levels_consumed=0, reason=reason)
    return ShadowObservation(
        instrument_id=leg.instrument_id, side=leg.side, quantity=float(leg.quantity),
        decision_price=float(leg.decision_price), paper_fill_price=float(leg.decision_price),
        book_mid=book_mid, book_spread_bps=book_spread_bps, executable_vwap=None,
        slippage_bps_vs_decision=None, walk=walk, expected_impact_bps=None,
        latency_ms=latency_ms, book_age_ms=book_age_ms,
        as_of_ms=None if book is None else book.as_of_ms)


def _top_of_book(book: OrderBookSnapshot) -> Tuple[Optional[float], Optional[float]]:
    """(mid, spread_bps);任一侧空簿 → (None, None)(单边簿没有良定义的中价/点差)。"""
    if not book.bids or not book.asks:
        return None, None
    best_bid, best_ask = book.bids[0].price, book.asks[0].price
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0:
        return None, None
    return mid, (best_ask - best_bid) / mid * 1e4


def observe_leg(leg: ShadowLeg, *, book: Optional[OrderBookSnapshot], latency_ms: int,
                now_ms: int, impact_model=None, adv: Optional[float] = None,
                max_book_age_ms: int = DEFAULT_MAX_BOOK_AGE_MS) -> ShadowObservation:
    """单腿观测。fail-closed:book 缺失/过旧/来自未来 → 可执行字段全 None + 机器可读
    reason(BOOK_UNAVAILABLE/BOOK_STALE/BOOK_FROM_FUTURE),绝不拿决策价冒充可成交价。
    latency_ms/now_ms/max_book_age_ms 为调用方注入的 int ms(非负;latency 是 IO 边界实测,
    本核心不读墙钟)。"""
    latency_ms = _require_int_ms("latency_ms", latency_ms)
    now_ms = _require_int_ms("now_ms", now_ms)
    max_book_age_ms = _require_int_ms("max_book_age_ms", max_book_age_ms)
    if not isinstance(leg, ShadowLeg):
        raise ValueError(f"leg must be a ShadowLeg, got {type(leg).__name__}")

    if book is None:
        return _unexecutable_observation(leg, "BOOK_UNAVAILABLE", latency_ms=latency_ms,
                                         book=None, book_age_ms=None,
                                         book_mid=None, book_spread_bps=None)

    book_age = None if book.as_of_ms is None else now_ms - book.as_of_ms
    mid, spread_bps = _top_of_book(book)
    if book_age is not None and book_age < 0:
        return _unexecutable_observation(leg, "BOOK_FROM_FUTURE", latency_ms=latency_ms,
                                         book=book, book_age_ms=book_age,
                                         book_mid=mid, book_spread_bps=spread_bps)
    if book_age is not None and book_age > max_book_age_ms:
        return _unexecutable_observation(leg, "BOOK_STALE", latency_ms=latency_ms,
                                         book=book, book_age_ms=book_age,
                                         book_mid=mid, book_spread_bps=spread_bps)

    walk = walk_the_book(book, side=leg.side, quantity=leg.quantity)
    executable_vwap = walk.vwap if walk.executable else None
    slippage = None
    if executable_vwap is not None:
        slippage = _tca_slippage_bps(OrderSide.BUY if leg.side == "BUY" else OrderSide.SELL,
                                     decision_price=float(leg.decision_price),
                                     fill_price=executable_vwap)
    impact_bps = None
    if impact_model is not None and adv is not None:
        try:
            adv_value = float(adv)
        except (TypeError, ValueError, OverflowError):
            adv_value = float("nan")
        if math.isfinite(adv_value) and adv_value > 0:
            notional = float(leg.quantity) * float(leg.decision_price)
            impact_bps = (float(impact_model.temporary_bps(notional, adv_value))
                          + float(impact_model.permanent_bps(notional, adv_value)))
    return ShadowObservation(
        instrument_id=leg.instrument_id, side=leg.side, quantity=float(leg.quantity),
        decision_price=float(leg.decision_price), paper_fill_price=float(leg.decision_price),
        book_mid=mid, book_spread_bps=spread_bps, executable_vwap=executable_vwap,
        slippage_bps_vs_decision=slippage, walk=walk, expected_impact_bps=impact_bps,
        latency_ms=latency_ms, book_age_ms=book_age, as_of_ms=book.as_of_ms)


def observe_batch(legs: Sequence[ShadowLeg], books: Mapping[str, Optional[OrderBookSnapshot]],
                  *, latencies_ms: Mapping[str, int], now_ms: int, impact_model=None,
                  adv: Optional[Mapping[str, float]] = None,
                  max_book_age_ms: int = DEFAULT_MAX_BOOK_AGE_MS) -> List[ShadowObservation]:
    """整批观测,输出顺序 = 输入 legs 顺序(确定性,逐位可复现)。

    books 缺某标的 → 该腿 BOOK_UNAVAILABLE(与 observe_leg 同纪律,per-leg fail-closed,
    不因一条腿缺簿拖垮整批);latencies_ms 缺某标的 → ValueError(latency 是观测的承重
    字段,缺了宁可整批不开跑也不记一条没有 latency 的观测)。adv 按标的键取,
    缺失标的 → 该腿 expected_impact_bps=None(UNKNOWN,不编造)。"""
    out: List[ShadowObservation] = []
    for leg in legs:
        if leg.instrument_id not in latencies_ms:
            raise ValueError(f"latencies_ms missing instrument {leg.instrument_id!r} — "
                             "latency is load-bearing for a SHADOW observation, refusing "
                             "to record without it")
        adv_i = None if adv is None else adv.get(leg.instrument_id)
        out.append(observe_leg(leg, book=books.get(leg.instrument_id),
                               latency_ms=latencies_ms[leg.instrument_id], now_ms=now_ms,
                               impact_model=impact_model, adv=adv_i,
                               max_book_age_ms=max_book_age_ms))
    return out
