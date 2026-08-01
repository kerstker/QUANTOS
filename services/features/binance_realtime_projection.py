"""Pure Binance public-stream snapshot to PIT FeatureValue projection.

The projection consumes already-retained public market snapshots.  It performs
no network IO, has no credentials, and has no trading authority.  Every output
keeps the source event time and the observation/knowledge time supplied by the
WebSocket collector.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from quantos.contracts.feature import FeatureValue


FEATURE_VERSION = "binance-public-stream.v1"
MATERIALIZER_VERSION = "binance-realtime-projection.v1"
CORE_FEATURE_IDS = (
    "rt_mid_price",
    "rt_spread_bps",
    "rt_depth_imbalance_5",
    "rt_mark_basis_bps",
    "rt_funding_rate",
    "rt_taker_imbalance_5s",
    "rt_liquidation_imbalance_5s",
)
CAPACITY_FEATURE_ID = "rt_executable_depth_5_usd"
FEATURE_IDS = CORE_FEATURE_IDS + (CAPACITY_FEATURE_ID,)


class BinanceRealtimeProjectionError(ValueError):
    """A retained public snapshot cannot be projected without fabrication."""


def _finite(value: object, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise BinanceRealtimeProjectionError(f"{field} is invalid")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BinanceRealtimeProjectionError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise BinanceRealtimeProjectionError(f"{field} is outside its domain")
    return result


def _timestamp(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise BinanceRealtimeProjectionError(f"{field} is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BinanceRealtimeProjectionError(f"{field} is invalid") from exc
    if result < 0:
        raise BinanceRealtimeProjectionError(f"{field} is invalid")
    return result


def _depth_totals(rows: object, field: str) -> tuple[float, float]:
    if not isinstance(rows, list) or len(rows) < 5:
        raise BinanceRealtimeProjectionError(f"{field} has fewer than five levels")
    total = 0.0
    notional = 0.0
    for index, row in enumerate(rows[:5]):
        if (
            not isinstance(row, Sequence)
            or isinstance(row, (str, bytes))
            or len(row) < 2
        ):
            raise BinanceRealtimeProjectionError(
                f"{field}[{index}] is malformed"
            )
        price = _finite(row[0], f"{field}[{index}].price", positive=True)
        quantity = _finite(row[1], f"{field}[{index}].quantity", positive=True)
        total += quantity
        notional += price * quantity
    return total, notional


def _signed_ratio(positive: object, negative: object, field: str) -> float:
    left = _finite(positive or 0.0, f"{field}.positive")
    right = _finite(negative or 0.0, f"{field}.negative")
    if left < 0 or right < 0:
        raise BinanceRealtimeProjectionError(f"{field} volume is negative")
    denominator = left + right
    return 0.0 if denominator == 0.0 else (left - right) / denominator


def project_snapshot(snapshot: Mapping[str, Any]) -> tuple[FeatureValue, ...]:
    """Project one complete WebSocket snapshot into eight causal features."""
    if not isinstance(snapshot, Mapping):
        raise BinanceRealtimeProjectionError("snapshot must be a mapping")
    if (
        snapshot.get("provider") != "BINANCE_PUBLIC_USDM_WEBSOCKET"
        or snapshot.get("venue") != "binance_usdm"
    ):
        raise BinanceRealtimeProjectionError("snapshot provenance is invalid")
    quality = snapshot.get("quality")
    if not isinstance(quality, Mapping) or quality.get("complete_core") is not True:
        raise BinanceRealtimeProjectionError("snapshot core coverage is incomplete")
    instrument_id = str(snapshot.get("instrument_id", "")).upper()
    if not instrument_id.endswith("USDT"):
        raise BinanceRealtimeProjectionError("snapshot instrument is invalid")
    event_time = _timestamp(snapshot.get("event_time_ms"), "event_time_ms")
    knowledge_time = _timestamp(
        snapshot.get("observed_at_ms"), "observed_at_ms"
    )
    if knowledge_time < event_time:
        raise BinanceRealtimeProjectionError("snapshot violates PIT ordering")

    book = snapshot.get("book")
    depth = snapshot.get("depth20")
    mark = snapshot.get("mark")
    trades = snapshot.get("trade_window")
    liquidations = snapshot.get("liquidation_window")
    if not all(
        isinstance(value, Mapping)
        for value in (book, depth, mark, trades, liquidations)
    ):
        raise BinanceRealtimeProjectionError("snapshot channels are missing")

    mid = _finite(book.get("mid"), "book.mid", positive=True)
    spread_bps = _finite(book.get("spread_bps"), "book.spread_bps")
    if spread_bps < 0:
        raise BinanceRealtimeProjectionError("book spread is negative")
    bid_depth, bid_depth_notional = _depth_totals(depth.get("bids"), "depth.bids")
    ask_depth, ask_depth_notional = _depth_totals(depth.get("asks"), "depth.asks")
    depth_imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)
    executable_depth_usd = min(bid_depth_notional, ask_depth_notional)
    mark_price = _finite(mark.get("mark_price"), "mark.price", positive=True)
    index_price = _finite(mark.get("index_price"), "mark.index", positive=True)
    basis_bps = (mark_price - index_price) / index_price * 10_000.0
    funding_rate = _finite(mark.get("funding_rate"), "mark.funding")
    taker_imbalance = _signed_ratio(
        trades.get("buy_taker_notional"),
        trades.get("sell_taker_notional"),
        "trade_window",
    )
    liquidation_imbalance = _signed_ratio(
        liquidations.get("long_liquidation_notional"),
        liquidations.get("short_liquidation_notional"),
        "liquidation_window",
    )
    values = (
        mid,
        spread_bps,
        depth_imbalance,
        basis_bps,
        funding_rate,
        taker_imbalance,
        liquidation_imbalance,
        executable_depth_usd,
    )
    return tuple(
        FeatureValue(
            feature_id=feature_id,
            instrument_id=instrument_id,
            event_time=event_time,
            knowledge_time=knowledge_time,
            value=value,
        )
        for feature_id, value in zip(FEATURE_IDS, values, strict=True)
    )


__all__ = [
    "BinanceRealtimeProjectionError",
    "CAPACITY_FEATURE_ID",
    "CORE_FEATURE_IDS",
    "FEATURE_IDS",
    "FEATURE_VERSION",
    "MATERIALIZER_VERSION",
    "project_snapshot",
]
