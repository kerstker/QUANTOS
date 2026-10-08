"""Outcome-free microstructure snapshots for forward market-resilience research.

The primitive compresses a committed public L2/trade interval into executable
state variables.  It deliberately has no future horizon, return, label or
promotion decision.  A deleted price level is observable, but it is not an
exchange order identity and therefore must never be called a cancellation.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any


class MarketResilienceError(ValueError):
    """A snapshot would overstate the source evidence or clock ordering."""


_IDENTIFIER = re.compile(r"[a-z0-9_]{2,64}")
_INSTRUMENT = re.compile(r"[A-Z0-9-]{2,48}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SOURCE_CHANNELS = frozenset({"orderbook_l2", "public_trade"})
_DEPTH_BANDS_BPS = (5, 10, 20)
_MAX_FUTURE_SKEW_MS = 30_000


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MarketResilienceError("snapshot is not canonical JSON") from exc


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _decimal(value: object, name: str, *, allow_zero: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise MarketResilienceError(f"{name} is invalid")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarketResilienceError(f"{name} is invalid") from exc
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        raise MarketResilienceError(f"{name} is invalid")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise MarketResilienceError(f"{name} is invalid")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise MarketResilienceError(f"{name} is invalid") from exc
    if parsed < 0:
        raise MarketResilienceError(f"{name} is invalid")
    return parsed


def _book_side(
    rows: Mapping[Decimal, Decimal],
    name: str,
) -> dict[Decimal, Decimal]:
    if not isinstance(rows, Mapping) or not rows:
        raise MarketResilienceError(f"{name} is empty")
    output: dict[Decimal, Decimal] = {}
    for raw_price, raw_quantity in rows.items():
        price = _decimal(raw_price, f"{name}.price")
        quantity = _decimal(raw_quantity, f"{name}.quantity")
        if price in output:
            raise MarketResilienceError(f"{name} repeats a price")
        output[price] = quantity
    return output


def _depth_usd(
    rows: Mapping[Decimal, Decimal],
    *,
    touch: Decimal,
    bps: int,
    is_bid: bool,
) -> Decimal:
    offset = Decimal(bps) / Decimal(10_000)
    boundary = touch * (Decimal(1) - offset if is_bid else Decimal(1) + offset)
    return sum(
        (
            price * quantity
            for price, quantity in rows.items()
            if (price >= boundary if is_bid else price <= boundary)
        ),
        Decimal(0),
    )


def _event_value(event: Mapping[str, Any], name: str) -> int:
    try:
        return _integer(event[name], name)
    except KeyError as exc:
        raise MarketResilienceError(f"source event {name} is missing") from exc


def build_market_resilience_snapshot(
    *,
    venue: str,
    instrument_id: str,
    segment_id: str,
    bids: Mapping[Decimal, Decimal],
    asks: Mapping[Decimal, Decimal],
    book_update_id: int,
    book_cross_sequence: int,
    book_event_time_ms: int,
    book_received_at_ms: int,
    source_events: Sequence[Mapping[str, Any]],
    processed_at_ms: int,
    raw_authority_head_seq: int,
    raw_authority_head_hash: str,
) -> dict[str, Any]:
    """Build one causal interval snapshot from an already reconstructed book."""

    if not isinstance(venue, str) or _IDENTIFIER.fullmatch(venue) is None:
        raise MarketResilienceError("venue is invalid")
    if not isinstance(instrument_id, str) or _INSTRUMENT.fullmatch(instrument_id) is None:
        raise MarketResilienceError("instrument_id is invalid")
    if not isinstance(segment_id, str) or _SHA256.fullmatch(segment_id) is None:
        raise MarketResilienceError("segment_id is invalid")
    if not isinstance(source_events, Sequence) or isinstance(source_events, (str, bytes)):
        raise MarketResilienceError("source events must be a sequence")
    if not source_events:
        raise MarketResilienceError("at least one source event is required")
    normalized_bids = _book_side(bids, "bids")
    normalized_asks = _book_side(asks, "asks")
    best_bid = max(normalized_bids)
    best_ask = min(normalized_asks)
    if best_bid >= best_ask:
        raise MarketResilienceError("order book is crossed")

    processed = _integer(processed_at_ms, "processed_at_ms")
    book_event = _integer(book_event_time_ms, "book_event_time_ms")
    book_received = _integer(book_received_at_ms, "book_received_at_ms")
    if book_event > book_received + _MAX_FUTURE_SKEW_MS or book_received > processed:
        raise MarketResilienceError("book clocks are invalid")
    update_id = _integer(book_update_id, "book_update_id")
    cross_sequence = _integer(book_cross_sequence, "book_cross_sequence")
    raw_head_seq = _integer(raw_authority_head_seq, "raw_authority_head_seq")
    if raw_head_seq == 0:
        raise MarketResilienceError("raw authority head is empty")
    if (
        not isinstance(raw_authority_head_hash, str)
        or _SHA256.fullmatch(raw_authority_head_hash) is None
    ):
        raise MarketResilienceError("raw authority head hash is invalid")

    source_ids: list[str] = []
    event_times: list[int] = []
    receive_times: list[int] = []
    buy_notional = Decimal(0)
    sell_notional = Decimal(0)
    trade_count = 0
    block_trade_count = 0
    rpi_trade_count = 0
    book_update_count = 0
    level_delete_count = 0
    relevant_channels: set[str] = set()
    for source in source_events:
        if not isinstance(source, Mapping):
            raise MarketResilienceError("source event must be a mapping")
        if source.get("venue") != venue or source.get("instrument_id") != instrument_id:
            raise MarketResilienceError("source event identity does not match snapshot")
        event_id = source.get("event_id")
        channel = source.get("channel")
        payload = source.get("payload")
        if not isinstance(event_id, str) or _SHA256.fullmatch(event_id) is None:
            raise MarketResilienceError("source event id is invalid")
        if event_id in source_ids:
            raise MarketResilienceError("source event id is repeated")
        if not isinstance(channel, str) or not isinstance(payload, Mapping):
            raise MarketResilienceError("source event channel or payload is invalid")
        source_processed = _event_value(source, "processed_at_ms")
        event_time = _event_value(source, "event_time_ms")
        received_at = _event_value(source, "received_at_ms")
        if (
            source_processed != processed
            or event_time > received_at + _MAX_FUTURE_SKEW_MS
            or received_at > processed
        ):
            raise MarketResilienceError("source event clocks are invalid")
        source_ids.append(event_id)
        event_times.append(event_time)
        receive_times.append(received_at)
        if channel not in _SOURCE_CHANNELS:
            continue
        relevant_channels.add(channel)
        if channel == "orderbook_l2":
            book_update_count += _integer(payload.get("update_count"), "update_count")
            level_delete_count += _integer(payload.get("delete_count"), "delete_count")
            continue
        trades = payload.get("trades")
        if not isinstance(trades, list):
            raise MarketResilienceError("trade payload is invalid")
        for row in trades:
            if not isinstance(row, Mapping):
                raise MarketResilienceError("trade row is invalid")
            side = row.get("taker_side")
            if side not in {"Buy", "Sell"}:
                raise MarketResilienceError("trade side is invalid")
            notional = _decimal(row.get("price"), "trade.price") * _decimal(
                row.get("quantity"), "trade.quantity"
            )
            if side == "Buy":
                buy_notional += notional
            else:
                sell_notional += notional
            trade_count += 1
            block_trade_count += int(row.get("block_trade") is True)
            rpi_trade_count += int(row.get("rpi_trade") is True)
    if not relevant_channels:
        raise MarketResilienceError("source interval has no book or trade event")

    source_event_time = max(event_times)
    received_at = max(receive_times)
    mid = (best_bid + best_ask) / Decimal(2)
    spread_bps = (best_ask - best_bid) / mid * Decimal(10_000)
    depths: dict[int, tuple[Decimal, Decimal]] = {
        bps: (
            _depth_usd(normalized_bids, touch=best_bid, bps=bps, is_bid=True),
            _depth_usd(normalized_asks, touch=best_ask, bps=bps, is_bid=False),
        )
        for bps in _DEPTH_BANDS_BPS
    }
    bid_20, ask_20 = depths[20]
    quote_total = bid_20 + ask_20
    taker_total = buy_notional + sell_notional
    body: dict[str, Any] = {
        "schema_version": 1,
        "venue": venue,
        "instrument_id": instrument_id,
        "segment_id": segment_id,
        "source_event_time_ms": source_event_time,
        "received_at_ms": received_at,
        "processed_at_ms": processed,
        "event_to_receive_ms": received_at - source_event_time,
        "receive_to_process_ms": processed - received_at,
        "book_event_time_ms": book_event,
        "book_received_at_ms": book_received,
        "book_update_id": update_id,
        "book_cross_sequence": cross_sequence,
        "best_bid": _decimal_text(best_bid),
        "best_ask": _decimal_text(best_ask),
        "mid_price": _decimal_text(mid),
        "spread_bps": _decimal_text(spread_bps),
        "bid_depth_usd_5bps": _decimal_text(depths[5][0]),
        "ask_depth_usd_5bps": _decimal_text(depths[5][1]),
        "bid_depth_usd_10bps": _decimal_text(depths[10][0]),
        "ask_depth_usd_10bps": _decimal_text(depths[10][1]),
        "bid_depth_usd_20bps": _decimal_text(bid_20),
        "ask_depth_usd_20bps": _decimal_text(ask_20),
        "quote_depth_imbalance_20bps": _decimal_text(
            (bid_20 - ask_20) / quote_total
        ),
        "buy_taker_notional": _decimal_text(buy_notional),
        "sell_taker_notional": _decimal_text(sell_notional),
        "signed_taker_notional": _decimal_text(buy_notional - sell_notional),
        "taker_imbalance": (
            None
            if taker_total == 0
            else _decimal_text((buy_notional - sell_notional) / taker_total)
        ),
        "trade_count": trade_count,
        "block_trade_count": block_trade_count,
        "rpi_trade_count": rpi_trade_count,
        "book_update_count": book_update_count,
        "level_delete_count": level_delete_count,
        "cancel_rate_inferred": False,
        "source_event_ids": source_ids,
        "raw_authority_head": {
            "head_seq": raw_head_seq,
            "head_hash": raw_authority_head_hash,
        },
    }
    return {"snapshot_id": _hash(body), **body}


__all__ = [
    "MarketResilienceError",
    "build_market_resilience_snapshot",
]
