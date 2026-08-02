"""实时资金费交叉核对(2026-07-23)——price_check.py 的资金费镜像。

纯函数、无网络/无 IO(与 price_check 同一设计约束)。回答的问题:funding 信号策略
(如 carry_oi_majors)的边在资金费现金流,而它的信号来自**日频离线快照**
(crypto_funding_rates.json,由 tools/live_data_refresh.py 按日桶刷新)。这份快照若
陈旧(刷新断档)或被污染,跨源**价格**核对完全抓不到——价格可以毫无问题而资金费
证据已经不可信。本模块用 venue 的实时只读 premium-index 快照(BinanceFuturesAdapter
.premium_index,2026-07-23 落地)交叉核对资金费证据,单标的无法验证/偏离超阈值一律
标记(调用方据此从本轮批次冻结该标的,fail-soft per-instrument,与跨源核价同一哲学)。

**口径与容差(这是陈旧/垃圾探测器,不是精确等值断言):**
- venue `last_funding_rate` 是**当前/最近一个 8h 区间**的费率;离线快照的日值是
  **当日 3 个 8h 费率之和**。故比较 `venue_rate × (24h / funding_interval)` vs 日值。
- 8h 费率日内合法漂移(利率/基差随时变),`venue_rate × 3` 与日值**不应**精确相等。
  容差因此刻意宽松:组合限 `abs_rate_tolerance + rel_rate_tolerance × |daily|`。
  默认 abs=0.001(日化 10bps ≈ 典型日化资金费 0.03% 的 3.3 倍——只有单位错/垃圾/
  严重陈旧才会超)、rel=2.0(大费率日允许 3× 带宽)。它抓的是「数量级不对/单位错/
  快照断档」,不是「费率预测不准」。
- `next_funding_time_ms` 是 venue 侧的陈旧性自证:落后于 now 超过 grace(数据过期)
  或领先超过一个资金费区间 + grace(荒谬未来,响应畸形)都不可信。
- 离线快照自带 `day_ms`(最新日桶):超过 max_snapshot_age_ms 未刷新即陈旧
  (funding 天天计提,刷新断档 = 证据断档)。

venue 快照用鸭子类型读取(同时接受 `PremiumIndexSnapshot` 这类 dataclass 与
Mapping),本模块不 import live.contracts——注入值,不耦合类型。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Dict

_DAY_MS = 86_400_000


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _field(observation: object, name: str) -> object:
    """鸭子类型字段访问:Mapping 用 .get,dataclass/对象用 getattr。"""
    if isinstance(observation, Mapping):
        return observation.get(name)
    return getattr(observation, name, None)


def flag_unverifiable_funding_instruments(
    instrument_ids: Iterable[str],
    venue_snapshots: Mapping[str, object],
    daily_observations: Mapping[str, object],
    *,
    now_ms: int,
    funding_interval_ms: int = 8 * 3_600_000,
    next_funding_grace_ms: int = 3_600_000,
    max_snapshot_age_ms: int = 2 * _DAY_MS,
    abs_rate_tolerance: float = 0.001,
    rel_rate_tolerance: float = 2.0,
) -> Dict[str, str]:
    """Flag funding-signal target instruments whose real-time funding evidence is unsafe.

    ``venue_snapshots`` = {instrument_id: venue premium-index observation or None}
    (dataclass 或 Mapping,字段 ``last_funding_rate`` / ``next_funding_time_ms``);
    ``daily_observations`` = {instrument_id: {"rate", "day_ms"} mapping}(离线日快照的
    最新日桶,rate = 当日 8h 费率之和)。返回 {instrument_id: 冻结原因}——只列出
    **需要冻结**的标的,未出现的视为核对通过。任何缺失/畸形/陈旧/偏离证据都
    fail-closed 冻结该标的(与 price_check 的 "unverifiable 即剔除" 同一语义)。
    """
    now = _finite_number(now_ms)
    interval = _finite_number(funding_interval_ms)
    grace = _finite_number(next_funding_grace_ms)
    max_age = _finite_number(max_snapshot_age_ms)
    abs_tol = _finite_number(abs_rate_tolerance)
    rel_tol = _finite_number(rel_rate_tolerance)
    if now is None or now <= 0:
        raise ValueError("now_ms must be a positive finite timestamp")
    if interval is None or interval <= 0 or interval > _DAY_MS:
        raise ValueError("funding_interval_ms must be finite, positive and <= one day")
    if grace is None or grace < 0:
        raise ValueError("next_funding_grace_ms must be finite and non-negative")
    if max_age is None or max_age < 0:
        raise ValueError("max_snapshot_age_ms must be finite and non-negative")
    if abs_tol is None or abs_tol < 0:
        raise ValueError("abs_rate_tolerance must be finite and non-negative")
    if rel_tol is None or rel_tol < 0:
        raise ValueError("rel_rate_tolerance must be finite and non-negative")

    periods_per_day = _DAY_MS / interval
    out: Dict[str, str] = {}
    for instrument_id in instrument_ids:
        venue = venue_snapshots.get(instrument_id)
        if venue is None:
            out[instrument_id] = "unverifiable: missing venue premium-index snapshot"
            continue
        venue_rate = _finite_number(_field(venue, "last_funding_rate"))
        if venue_rate is None:
            out[instrument_id] = "unverifiable: invalid venue funding rate"
            continue
        next_ms = _finite_number(_field(venue, "next_funding_time_ms"))
        if next_ms is None or next_ms <= 0:
            out[instrument_id] = "unverifiable: invalid venue next-funding timestamp"
            continue
        if now - next_ms > grace:
            out[instrument_id] = (
                "stale venue funding schedule: next_funding_time_ms is "
                f"{now - next_ms:.0f}ms in the past (grace {grace:.0f}ms)"
            )
            continue
        if next_ms - now > interval + grace:
            out[instrument_id] = (
                "unverifiable: next_funding_time_ms is "
                f"{next_ms - now:.0f}ms ahead of local time "
                f"(> one funding interval {interval:.0f}ms + grace {grace:.0f}ms)"
            )
            continue

        daily = daily_observations.get(instrument_id)
        if not isinstance(daily, Mapping):
            out[instrument_id] = "unverifiable: no daily funding snapshot rate available"
            continue
        daily_rate = _finite_number(daily.get("rate"))
        if daily_rate is None:
            out[instrument_id] = "unverifiable: invalid daily funding snapshot rate"
            continue
        day_ms = _finite_number(daily.get("day_ms"))
        if day_ms is None or day_ms <= 0:
            out[instrument_id] = "unverifiable: invalid daily funding snapshot timestamp"
            continue
        if now - day_ms > max_age:
            out[instrument_id] = (
                "stale daily funding snapshot: latest day age_ms="
                f"{now - day_ms:.0f}, max_snapshot_age_ms={max_age:.0f}"
            )
            continue
        if day_ms - now > max_age:
            out[instrument_id] = (
                "unverifiable: daily snapshot day materially ahead of local time "
                f"(delta_ms={day_ms - now:.0f})"
            )
            continue

        venue_daily_equiv = venue_rate * periods_per_day
        deviation = abs(venue_daily_equiv - daily_rate)
        limit = abs_tol + rel_tol * abs(daily_rate)
        if deviation > limit:
            out[instrument_id] = (
                "venue funding diverges from daily snapshot "
                f"(venue_8h={venue_rate}, venue_daily_equiv={venue_daily_equiv}, "
                f"daily={daily_rate}, deviation={deviation}, limit={limit}; "
                "staleness/garbage detector — intraday drift within tolerance is legitimate)"
            )
    return out
