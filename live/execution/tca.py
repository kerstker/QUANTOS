"""成交质量分析(TCA,2026-07-05)。设计:
docs/engineering/specs/2026-07-05-institutional-live-stack-design.md §③。

纯函数、无 IO/网络/墙钟。每笔确认成交的滑点 = 实际成交均价相对决策价的带方向偏离
(bps;买贵/卖贱为正 = 成本,负 = 价格改善)。周度调仓节奏下"决策价"用
`submit_live_batch` 收到的 `prices`(决策时点 mark price)作为到达价代理——诚实标注:
这不是逐笔到达价(那需要下单瞬间的盘口快照,本阶段刻意不引入),但在周度低频下两者的
差主要就是我们想度量的执行延迟+冲击本身。

`tca_summary` 把累计记录对照回测的成本假设(默认 10bps)——"执行现实校验回测假设"闭环的
最后一块:如果真实加权滑点持续高于回测假设,回测里的净收益就是高估的,这个信号必须回喂
给 Go/No-Go 评审。
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, List, Mapping, Optional, Sequence

from quantos.contracts.trading import OrderSide


@dataclass(frozen=True)
class ArrivalSnapshot:
    """Immutable contemporaneous price evidence used as a TCA arrival baseline.

    ``price`` is the price observed at the decision boundary.  When a two-sided
    quote is available, ``bid``/``ask`` preserve the raw book evidence rather
    than forcing downstream code to reconstruct it from a midpoint.  This value
    object is intentionally free of IO and wall-clock calls; the caller owns
    collection and supplies both the observation and its timestamps.
    """

    price: float
    source: str
    captured_at_ms: int
    as_of_ms: Optional[int] = None
    bid: Optional[float] = None
    ask: Optional[float] = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("arrival snapshot source must be non-empty")
        try:
            captured_is_integer = (not isinstance(self.captured_at_ms, bool)
                                   and int(self.captured_at_ms) == self.captured_at_ms)
        except (TypeError, ValueError, OverflowError):
            captured_is_integer = False
        if not captured_is_integer:
            raise ValueError("arrival snapshot captured_at_ms must be an integer")
        if self.captured_at_ms < 0:
            raise ValueError("arrival snapshot captured_at_ms must be non-negative")
        if self.as_of_ms is not None:
            try:
                as_of_is_integer = (not isinstance(self.as_of_ms, bool)
                                    and int(self.as_of_ms) == self.as_of_ms)
            except (TypeError, ValueError, OverflowError):
                as_of_is_integer = False
            if not as_of_is_integer:
                raise ValueError("arrival snapshot as_of_ms must be an integer")
            if self.as_of_ms <= 0:
                raise ValueError("arrival snapshot as_of_ms must be positive")
        if not math.isfinite(float(self.price)) or self.price <= 0:
            raise ValueError("arrival snapshot price must be finite and positive")
        if (self.bid is None) != (self.ask is None):
            raise ValueError("arrival snapshot bid and ask must be supplied together")
        if self.bid is not None and self.ask is not None:
            if (not math.isfinite(float(self.bid)) or not math.isfinite(float(self.ask))
                    or self.bid <= 0 or self.ask <= 0 or self.ask < self.bid):
                raise ValueError("arrival snapshot bid/ask must be a valid non-crossed quote")
            midpoint = (float(self.bid) + float(self.ask)) / 2.0
            if not math.isclose(float(self.price), midpoint, rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError("arrival snapshot price must equal bid/ask midpoint")

    @classmethod
    def from_book(cls, book: Mapping, *, source: str, captured_at_ms: int) -> "ArrivalSnapshot":
        """Build a snapshot from a venue ``book_ticker``-shaped mapping.

        Missing, malformed, crossed, or non-positive quotes are rejected.  A
        provider source timestamp is optional because some testnet endpoints
        omit it; when present it is retained for freshness checks and audit.
        """
        if not isinstance(book, Mapping):
            raise ValueError("arrival book must be a mapping")
        try:
            bid = float(book["bid"])
            ask = float(book["ask"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("arrival book requires numeric bid and ask") from exc
        raw_as_of = book.get("as_of_ms")
        if isinstance(raw_as_of, bool):
            raise ValueError("arrival book as_of_ms must be an integer")
        as_of = None if raw_as_of is None else int(raw_as_of)
        return cls(price=(bid + ask) / 2.0, source=source,
                   captured_at_ms=captured_at_ms, as_of_ms=as_of,
                   bid=bid, ask=ask)


def validate_arrival_snapshot(snapshot: Optional[ArrivalSnapshot], *, now_ms: int,
                              max_age_ms: Optional[int] = None,
                              expected_source: Optional[str] = None) -> Optional[str]:
    """Return a fail-closed reason, or ``None`` when snapshot evidence is usable.

    This is a pure validator so drivers and tests can apply the same policy
    without invoking a venue or consulting a wall clock.  ``None`` is not a
    valid snapshot when the caller explicitly requested snapshot mode.
    """
    if snapshot is None:
        return "arrival_snapshot_missing"
    try:
        now_is_integer = (not isinstance(now_ms, bool) and int(now_ms) == now_ms)
    except (TypeError, ValueError, OverflowError):
        now_is_integer = False
    if not now_is_integer or now_ms < 0:
        return "arrival_snapshot_invalid_now"
    if expected_source is not None and snapshot.source != expected_source:
        return "arrival_snapshot_source_mismatch"
    if max_age_ms is not None:
        try:
            max_age_is_integer = (not isinstance(max_age_ms, bool)
                                  and int(max_age_ms) == max_age_ms)
        except (TypeError, ValueError, OverflowError):
            max_age_is_integer = False
        if not max_age_is_integer or max_age_ms < 0:
            return "arrival_snapshot_invalid_max_age"
        captured_age = now_ms - snapshot.captured_at_ms
        if captured_age < 0:
            return "arrival_snapshot_from_future"
        if captured_age > max_age_ms:
            return "arrival_snapshot_stale"
        if snapshot.as_of_ms is not None:
            if snapshot.as_of_ms > snapshot.captured_at_ms or snapshot.as_of_ms > now_ms:
                return "arrival_snapshot_provider_time_from_future"
            provider_age = now_ms - snapshot.as_of_ms
            if provider_age > max_age_ms:
                return "arrival_snapshot_provider_stale"
    return None


def _is_finite_num(value: object) -> bool:
    return (isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value)))


def slippage_bps(side: OrderSide, *, decision_price: float, fill_price: float) -> Optional[float]:
    """带方向滑点(bps)。买单成交价高于决策价 / 卖单低于决策价 = 正 = 成本;反向 = 负 = 改善。
    价格非正或非有限 -> None(无法计算,诚实缺失;不硬凑 0——0 是"零滑点"的真实断言)。"""
    if not (_is_finite_num(decision_price) and _is_finite_num(fill_price)):
        return None
    dec = float(decision_price)
    fill = float(fill_price)
    if dec <= 0 or fill <= 0:
        return None
    raw = (fill - dec) / dec * 1e4
    if not math.isfinite(raw):
        return None
    return raw if side == OrderSide.BUY else -raw


def _mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _median(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def tca_summary(records: Sequence[Mapping], *, assumed_cost_bps: float = 10.0) -> dict:
    """累计 TCA 记录 -> 汇总。records 形状 = `LiveLedger.tca_records()` 的行
    (须含 instrument_id/slippage_bps/fill_price/quantity;slippage_bps 为 None 或非有限的行
    只计入 n_fills,不进任何均值——诚实缺失不污染统计)。"""
    if not _is_finite_num(assumed_cost_bps):
        raise ValueError(f"assumed_cost_bps must be a finite number, got {assumed_cost_bps!r}")
    assumed = float(assumed_cost_bps)
    valid = [r for r in records if _is_finite_num(r.get("slippage_bps"))]
    slips = [float(r["slippage_bps"]) for r in valid]
    snapshot_records = [r for r in records if _is_finite_num(r.get("arrival_price"))]
    arrival_sources: Dict[str, int] = {}
    for record in snapshot_records:
        source = record.get("arrival_source")
        if source:
            arrival_sources[str(source)] = arrival_sources.get(str(source), 0) + 1
    weighted_num = 0.0
    total_notional = 0.0
    for r, s in zip(valid, slips):
        fp = r.get("fill_price")
        qty = r.get("quantity")
        if not (_is_finite_num(fp) and _is_finite_num(qty)):
            continue
        notional = abs(float(fp) * float(qty))
        if not math.isfinite(notional) or notional <= 0.0:
            continue
        total_notional += notional
        weighted_num += s * notional
    weighted = (weighted_num / total_notional
                if total_notional > 0 and math.isfinite(weighted_num) else None)

    by_instrument: Dict[str, dict] = {}
    for r in valid:
        inst = r["instrument_id"]
        b = by_instrument.setdefault(inst, {"n_fills": 0, "_slips": []})
        b["n_fills"] += 1
        b["_slips"].append(float(r["slippage_bps"]))
    for inst, b in by_instrument.items():
        slips_i: List[float] = b.pop("_slips")
        b["mean_slippage_bps"] = _mean(slips_i)
        b["worst_slippage_bps"] = max(slips_i) if slips_i else None

    return {
        "n_fills": len(records),
        "n_with_slippage": len(valid),
        "n_with_arrival_snapshot": len(snapshot_records),
        "arrival_sources": arrival_sources,
        "mean_slippage_bps": _mean(slips),
        "median_slippage_bps": _median(slips),
        "worst_slippage_bps": max(slips) if slips else None,
        "notional_weighted_slippage_bps": weighted,
        "total_notional": total_notional,
        "assumed_cost_bps": assumed,
        "weighted_minus_assumed_bps": (weighted - assumed
                                       if weighted is not None else None),
        "by_instrument": by_instrument,
    }
