"""跨源核价(2026-07-05,机构审计 P0)。设计:
docs/engineering/specs/2026-07-05-institutional-live-stack-design.md 补充。

纯函数、无网络/无 IO。回答的问题:主决策源(交易所自己的价格)一次静默滞后/错误,
会不会同时污染下单和风控评估?——用独立第二源交叉核价,单标的偏离超阈值或无法验证
一律标记(调用方据此从本轮批次剔除该标的,fail-soft per-instrument,不因一个标的的
核价失败拖垮整批)。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Dict


def flag_divergent_instruments(primary: Mapping[str, float], secondary: Mapping[str, float],
                               *, max_divergence_pct: float,
                               secondary_unavailable: bool = False) -> Dict[str, str]:
    """primary/secondary = {instrument_id: price}(两个独立来源)。返回
    {instrument_id: 剔除原因} ——只列出**需要剔除**的标的,未出现的标的视为核价通过。

    secondary_unavailable=True(第二源整体拿不到,如网络故障):fail-soft 返回空——不能把
    "我们没查到第二源"误判成"每个标的都可疑",那是两种不同性质的故障,调用方应对
    "整体不可用"单独告警,而不是让所有标的一起被剔除。
    """
    if secondary_unavailable:
        return {}
    out: Dict[str, str] = {}
    for inst, px in primary.items():
        if px is None or px <= 0:
            out[inst] = f"unverifiable: primary price non-positive ({px})"
            continue
        sec = secondary.get(inst)
        if sec is None or sec <= 0:
            out[inst] = "unverifiable: no secondary-source price available"
            continue
        divergence = abs(px - sec) / px
        if divergence > max_divergence_pct:
            out[inst] = (f"diverges {divergence:.2%} from secondary source "
                        f"(primary={px}, secondary={sec}, threshold={max_divergence_pct:.0%})")
    return out


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def flag_noncontemporaneous_instruments(
    instrument_ids: Iterable[str],
    venue_books: Mapping[str, object],
    secondary_observations: Mapping[str, object],
    *,
    now_ms: int,
    max_divergence_pct: float,
    max_age_ms: int,
    max_skew_ms: int,
) -> Dict[str, str]:
    """Flag target instruments whose current cross-source evidence is unsafe.

    A venue observation is a ``{bid, ask, as_of_ms}`` mapping and a secondary
    observation is ``{price, as_of_ms}``. Both timestamps describe the source
    observation, not the local request time. Missing, malformed, stale, or
    materially time-skewed evidence fails closed for that instrument.
    """
    now = _finite_number(now_ms)
    divergence_limit = _finite_number(max_divergence_pct)
    age_limit = _finite_number(max_age_ms)
    skew_limit = _finite_number(max_skew_ms)
    if now is None or now <= 0:
        raise ValueError("now_ms must be a positive finite timestamp")
    if divergence_limit is None or divergence_limit < 0:
        raise ValueError("max_divergence_pct must be finite and non-negative")
    if age_limit is None or age_limit < 0:
        raise ValueError("max_age_ms must be finite and non-negative")
    if skew_limit is None or skew_limit < 0:
        raise ValueError("max_skew_ms must be finite and non-negative")

    out: Dict[str, str] = {}
    for instrument_id in instrument_ids:
        book = venue_books.get(instrument_id)
        if not isinstance(book, Mapping):
            out[instrument_id] = "unverifiable: missing venue book"
            continue
        bid = _finite_number(book.get("bid"))
        ask = _finite_number(book.get("ask"))
        venue_ts = _finite_number(book.get("as_of_ms"))
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            out[instrument_id] = "unverifiable: invalid venue book"
            continue
        if venue_ts is None or venue_ts <= 0:
            out[instrument_id] = "unverifiable: invalid venue book timestamp"
            continue

        secondary = secondary_observations.get(instrument_id)
        if not isinstance(secondary, Mapping):
            out[instrument_id] = "unverifiable: missing secondary observation"
            continue
        secondary_price = _finite_number(secondary.get("price"))
        secondary_ts = _finite_number(secondary.get("as_of_ms"))
        if secondary_price is None or secondary_price <= 0:
            out[instrument_id] = "unverifiable: invalid secondary price"
            continue
        if secondary_ts is None or secondary_ts <= 0:
            out[instrument_id] = "unverifiable: invalid secondary timestamp"
            continue

        venue_age = now - venue_ts
        secondary_age = now - secondary_ts
        if venue_age > age_limit:
            out[instrument_id] = (
                f"stale venue book: age_ms={venue_age:.0f}, max_age_ms={age_limit:.0f}"
            )
            continue
        if secondary_age > age_limit:
            out[instrument_id] = (
                "stale secondary observation: "
                f"age_ms={secondary_age:.0f}, max_age_ms={age_limit:.0f}"
            )
            continue
        if venue_age < -age_limit or secondary_age < -age_limit:
            out[instrument_id] = (
                "unverifiable: source timestamp materially ahead of local time "
                f"(venue_delta_ms={-venue_age:.0f}, secondary_delta_ms={-secondary_age:.0f})"
            )
            continue

        timestamp_skew = abs(venue_ts - secondary_ts)
        if timestamp_skew > skew_limit:
            out[instrument_id] = (
                f"timestamp skew {timestamp_skew:.0f}ms exceeds {skew_limit:.0f}ms"
            )
            continue

        venue_mid = (bid + ask) / 2.0
        divergence = abs(venue_mid - secondary_price) / venue_mid
        if divergence > divergence_limit:
            out[instrument_id] = (
                f"diverges {divergence:.2%} from secondary source "
                f"(venue_mid={venue_mid}, secondary={secondary_price}, "
                f"threshold={divergence_limit:.2%})"
            )
    return out
