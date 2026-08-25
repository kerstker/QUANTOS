"""Credential-free Bybit linear event/L2 forward collector.

Only the public linear WebSocket is reachable.  Source event time, local receive
time and local persistence time remain separate.  The collector never imports
an exchange SDK, reads credentials, connects to a private stream or submits an
order.
"""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any

from websockets.asyncio.client import connect

from services.data.public_market_event_store import PublicMarketEventStore
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.cache_io import atomic_write_json


class BybitRealtimeStreamError(RuntimeError):
    """A public Bybit frame could not be admitted as research evidence."""


PUBLIC_LINEAR_URL = "wss://stream.bybit.com/v5/public/linear"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DEFAULT_DEPTH = 200
DEFAULT_STORE = os.path.join("data", "cache", "bybit_linear_events.db")
DEFAULT_STATUS = os.path.join("data", "cache", "bybit_realtime_stream_status.json")
DEFAULT_CHAIN_CHECKPOINT = os.path.join(
    "data", "cache", "bybit_linear_events_chain.json"
)
MIN_FREE_STORAGE_BYTES = 200 * 1024**3
MAX_FUTURE_SKEW_MS = 30_000
_VENUE = "bybit_linear"
_PROVIDER = "BYBIT_PUBLIC_LINEAR_WEBSOCKET"
_CHANNEL_BY_PREFIX = {
    "orderbook": "orderbook_l2",
    "publicTrade": "public_trade",
    "allLiquidation": "all_liquidation",
    "tickers": "ticker",
}


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
        raise BybitRealtimeStreamError("frame is not canonical JSON") from exc


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise BybitRealtimeStreamError(f"{name} is invalid")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise BybitRealtimeStreamError(f"{name} is invalid") from exc
    if parsed < 0:
        raise BybitRealtimeStreamError(f"{name} is invalid")
    return parsed


def _decimal(value: object, name: str, *, allow_zero: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise BybitRealtimeStreamError(f"{name} is invalid")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BybitRealtimeStreamError(f"{name} is invalid") from exc
    if not parsed.is_finite() or parsed < 0 or (parsed == 0 and not allow_zero):
        raise BybitRealtimeStreamError(f"{name} is invalid")
    return parsed


def subscription_topics(
    symbols: Sequence[str],
    *,
    depth: int = DEFAULT_DEPTH,
) -> tuple[str, ...]:
    normalized = tuple(str(symbol).upper() for symbol in symbols)
    if not normalized or len(set(normalized)) != len(normalized):
        raise ValueError("symbols must be non-empty and unique")
    if depth not in {50, 200}:
        raise ValueError("depth must be 50 or 200")
    for symbol in normalized:
        if not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            raise ValueError("symbols must be uppercase USDT contracts")
    topics: list[str] = []
    for symbol in normalized:
        topics.extend(
            (
                f"orderbook.{depth}.{symbol}",
                f"publicTrade.{symbol}",
                f"allLiquidation.{symbol}",
                f"tickers.{symbol}",
            )
        )
    return tuple(topics)


def subscribe_request(
    symbols: Sequence[str],
    *,
    depth: int = DEFAULT_DEPTH,
) -> dict[str, object]:
    return {
        "req_id": "quantos-public-linear-v1",
        "op": "subscribe",
        "args": list(subscription_topics(symbols, depth=depth)),
    }


def _checkpoint_body(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "store_path": str(value["store_path"]),
        "full_verified_at_ms": int(value["full_verified_at_ms"]),
        "verified_at_ms": int(value["verified_at_ms"]),
        "verified_seq": int(value["verified_seq"]),
        "verified_row_hash": str(value["verified_row_hash"]),
    }


def _write_chain_checkpoint(path: str, value: Mapping[str, Any]) -> dict[str, Any]:
    body = _checkpoint_body(value)
    output = {**body, "checkpoint_hash": _hash(body)}
    atomic_write_json(path, output, indent=2)
    return output


def _initial_chain_checkpoint(
    store: PublicMarketEventStore,
    *,
    store_path: str,
    checkpoint_path: str,
    now_ms: int,
) -> dict[str, Any]:
    resolved_store = str(Path(store_path).resolve())
    existing: dict[str, Any] | None = None
    try:
        parsed = load_json_without_duplicate_keys(Path(checkpoint_path).read_bytes())
        if isinstance(parsed, dict):
            body = _checkpoint_body(parsed)
            if (
                parsed.get("checkpoint_hash") == _hash(body)
                and body["store_path"] == resolved_store
                and body["schema_version"] == 1
                and body["full_verified_at_ms"] >= 0
                and body["verified_at_ms"] >= body["full_verified_at_ms"]
                and body["verified_seq"] >= 0
            ):
                existing = body
    except (KeyError, OSError, TypeError, ValueError):
        existing = None
    if existing is None:
        checkpoint_seq = 0
        checkpoint_hash = ""
        full_verified_at_ms = now_ms
        mode = "FULL_FROM_GENESIS"
    else:
        checkpoint_seq = int(existing["verified_seq"])
        checkpoint_hash = str(existing["verified_row_hash"])
        full_verified_at_ms = int(existing["full_verified_at_ms"])
        mode = "INCREMENTAL_FROM_TRUSTED_LOCAL_CHECKPOINT"
    verification = store.verify_chain_from(
        checkpoint_seq=checkpoint_seq,
        checkpoint_row_hash=checkpoint_hash,
    )
    if not verification.get("valid"):
        raise BybitRealtimeStreamError("public event chain verification failed")
    value = {
        "schema_version": 1,
        "store_path": resolved_store,
        "full_verified_at_ms": full_verified_at_ms,
        "verified_at_ms": now_ms,
        "verified_seq": verification["verified_seq"],
        "verified_row_hash": verification["verified_row_hash"],
        "verification_mode": mode,
        "verified_suffix_events": verification["verified_events"],
    }
    written = _write_chain_checkpoint(checkpoint_path, value)
    return {**value, "checkpoint_hash": written["checkpoint_hash"]}


def _levels(value: object, name: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise BybitRealtimeStreamError(f"{name} is invalid")
    output: list[tuple[str, str]] = []
    seen: set[Decimal] = set()
    for index, row in enumerate(value):
        if not isinstance(row, list) or len(row) != 2:
            raise BybitRealtimeStreamError(f"{name}[{index}] is invalid")
        price = _decimal(row[0], f"{name}[{index}].price")
        size = _decimal(row[1], f"{name}[{index}].size", allow_zero=True)
        if price in seen:
            raise BybitRealtimeStreamError(f"{name} repeats a price")
        seen.add(price)
        output.append((str(row[0]), str(row[1])))
    return tuple(output)


class BybitLinearState:
    """Pure frame parser plus deterministic local order-book reconstruction."""

    def __init__(self, symbols: Sequence[str], *, depth: int = DEFAULT_DEPTH) -> None:
        self.symbols = tuple(str(symbol).upper() for symbol in symbols)
        self.depth = depth
        self.topics = subscription_topics(self.symbols, depth=depth)
        self._allowed = frozenset(self.topics)
        self._books: dict[str, dict[str, Any]] = {}
        self._ticker: dict[str, dict[str, Any]] = {}
        self._pending: list[dict[str, Any]] = []
        self.event_counts = {channel: 0 for channel in _CHANNEL_BY_PREFIX.values()}
        self.rejected_frames = 0
        self.last_received_at_ms: int | None = None
        self.last_error: str | None = None
        self.latest: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _topic(raw: Mapping[str, Any]) -> tuple[str, str, str]:
        topic = raw.get("topic")
        if not isinstance(topic, str):
            raise BybitRealtimeStreamError("topic is invalid")
        parts = topic.split(".")
        if len(parts) not in {2, 3}:
            raise BybitRealtimeStreamError("topic is invalid")
        prefix = parts[0]
        symbol = parts[-1]
        if prefix not in _CHANNEL_BY_PREFIX:
            raise BybitRealtimeStreamError("topic is not allowlisted")
        return topic, prefix, symbol

    @staticmethod
    def _event(
        *,
        topic: str,
        channel: str,
        symbol: str,
        event_time_ms: int,
        received_at_ms: int,
        source_sequence: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        event_id = _hash(
            {
                "venue": _VENUE,
                "topic": topic,
                "symbol": symbol,
                "source_sequence": source_sequence,
                "received_at_ms": received_at_ms,
            }
        )
        return {
            "event_id": event_id,
            "venue": _VENUE,
            "channel": channel,
            "instrument_id": symbol,
            "event_time_ms": event_time_ms,
            "received_at_ms": received_at_ms,
            "source_sequence": source_sequence,
            "payload": dict(payload),
        }

    def _book_event(
        self,
        raw: Mapping[str, Any],
        *,
        topic: str,
        symbol: str,
        received_at_ms: int,
        source_generated_at_ms: int,
    ) -> dict[str, Any]:
        message_type = raw.get("type")
        data = raw.get("data")
        if message_type not in {"snapshot", "delta"} or not isinstance(data, Mapping):
            raise BybitRealtimeStreamError("orderbook frame is invalid")
        if str(data.get("s", "")) != symbol:
            raise BybitRealtimeStreamError("orderbook symbol mismatch")
        bids = _levels(data.get("b"), "orderbook.bids")
        asks = _levels(data.get("a"), "orderbook.asks")
        update_id = _integer(data.get("u"), "orderbook.update_id")
        cross_sequence = _integer(data.get("seq"), "orderbook.cross_sequence")
        event_time_ms = _integer(raw.get("cts"), "orderbook.event_time_ms")
        if event_time_ms > received_at_ms + MAX_FUTURE_SKEW_MS:
            raise BybitRealtimeStreamError("orderbook event is future-dated")
        current = self._books.get(symbol)
        if message_type == "delta" and current is None:
            raise BybitRealtimeStreamError("orderbook delta arrived before snapshot")
        if message_type == "snapshot":
            bid_map: dict[Decimal, Decimal] = {}
            ask_map: dict[Decimal, Decimal] = {}
        else:
            assert current is not None
            if (
                update_id <= int(current["update_id"])
                or cross_sequence <= int(current["cross_sequence"])
            ):
                raise BybitRealtimeStreamError("orderbook sequence is not increasing")
            bid_map = dict(current["bids"])
            ask_map = dict(current["asks"])
        delete_count = 0
        update_count = 0
        for side, rows in ((bid_map, bids), (ask_map, asks)):
            for raw_price, raw_size in rows:
                price = Decimal(raw_price)
                size = Decimal(raw_size)
                if size == 0:
                    delete_count += int(price in side)
                    side.pop(price, None)
                else:
                    update_count += 1
                    side[price] = size
        if not bid_map or not ask_map or max(bid_map) >= min(ask_map):
            raise BybitRealtimeStreamError("orderbook reconstruction is crossed or empty")
        book = {
            "bids": bid_map,
            "asks": ask_map,
            "update_id": update_id,
            "cross_sequence": cross_sequence,
            "event_time_ms": event_time_ms,
            "received_at_ms": received_at_ms,
        }
        self._books[symbol] = book
        self.latest.setdefault(symbol, {})["book"] = {
            "best_bid": float(max(bid_map)),
            "best_ask": float(min(ask_map)),
            "bid_levels": len(bid_map),
            "ask_levels": len(ask_map),
            "update_id": update_id,
            "cross_sequence": cross_sequence,
            "event_time_ms": event_time_ms,
            "received_at_ms": received_at_ms,
            "message_type": message_type,
        }
        payload = {
            "provider": _PROVIDER,
            "topic": topic,
            "message_type": message_type,
            "source_generated_at_ms": source_generated_at_ms,
            "update_id": update_id,
            "cross_sequence": cross_sequence,
            "bids": [list(row) for row in bids],
            "asks": [list(row) for row in asks],
            "update_count": update_count,
            "delete_count": delete_count,
        }
        return self._event(
            topic=topic,
            channel="orderbook_l2",
            symbol=symbol,
            event_time_ms=event_time_ms,
            received_at_ms=received_at_ms,
            source_sequence=f"{update_id}:{cross_sequence}:{message_type}",
            payload=payload,
        )

    def _trade_event(
        self,
        raw: Mapping[str, Any],
        *,
        topic: str,
        symbol: str,
        received_at_ms: int,
        source_generated_at_ms: int,
    ) -> dict[str, Any]:
        rows = raw.get("data")
        if raw.get("type") != "snapshot" or not isinstance(rows, list) or not rows:
            raise BybitRealtimeStreamError("trade frame is invalid")
        normalized: list[dict[str, Any]] = []
        trade_ids: list[str] = []
        event_times: list[int] = []
        buy_notional = Decimal("0")
        sell_notional = Decimal("0")
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping) or str(row.get("s", "")) != symbol:
                raise BybitRealtimeStreamError("trade row is invalid")
            trade_id = row.get("i")
            side = row.get("S")
            if not isinstance(trade_id, str) or not trade_id or side not in {"Buy", "Sell"}:
                raise BybitRealtimeStreamError("trade identity is invalid")
            event_time_ms = _integer(row.get("T"), f"trade[{index}].event_time_ms")
            if event_time_ms > received_at_ms + MAX_FUTURE_SKEW_MS:
                raise BybitRealtimeStreamError("trade event is future-dated")
            price = _decimal(row.get("p"), f"trade[{index}].price")
            quantity = _decimal(row.get("v"), f"trade[{index}].quantity")
            notional = price * quantity
            if side == "Buy":
                buy_notional += notional
            else:
                sell_notional += notional
            trade_ids.append(trade_id)
            event_times.append(event_time_ms)
            normalized.append(
                {
                    "trade_id": trade_id,
                    "event_time_ms": event_time_ms,
                    "taker_side": side,
                    "price": str(row["p"]),
                    "quantity": str(row["v"]),
                    "tick_direction": row.get("L"),
                    "block_trade": bool(row.get("BT", False)),
                    "rpi_trade": bool(row.get("RPI", False)),
                    "cross_sequence": _integer(row.get("seq"), f"trade[{index}].seq"),
                }
            )
        if len(set(trade_ids)) != len(trade_ids):
            raise BybitRealtimeStreamError("trade frame repeats an id")
        payload = {
            "provider": _PROVIDER,
            "topic": topic,
            "message_type": "snapshot",
            "source_generated_at_ms": source_generated_at_ms,
            "trades": normalized,
            "buy_taker_notional": float(buy_notional),
            "sell_taker_notional": float(sell_notional),
        }
        latest = self.latest.setdefault(symbol, {})
        latest["trade"] = {
            "count": len(normalized),
            "last_event_time_ms": max(event_times),
            "received_at_ms": received_at_ms,
            "buy_taker_notional": float(buy_notional),
            "sell_taker_notional": float(sell_notional),
        }
        return self._event(
            topic=topic,
            channel="public_trade",
            symbol=symbol,
            event_time_ms=max(event_times),
            received_at_ms=received_at_ms,
            source_sequence=_hash(trade_ids),
            payload=payload,
        )

    def _liquidation_event(
        self,
        raw: Mapping[str, Any],
        *,
        topic: str,
        symbol: str,
        received_at_ms: int,
        source_generated_at_ms: int,
    ) -> dict[str, Any]:
        rows = raw.get("data")
        if raw.get("type") != "snapshot" or not isinstance(rows, list) or not rows:
            raise BybitRealtimeStreamError("liquidation frame is invalid")
        normalized: list[dict[str, Any]] = []
        event_times: list[int] = []
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping) or str(row.get("s", "")) != symbol:
                raise BybitRealtimeStreamError("liquidation row is invalid")
            side = row.get("S")
            if side not in {"Buy", "Sell"}:
                raise BybitRealtimeStreamError("liquidation side is invalid")
            event_time_ms = _integer(
                row.get("T"), f"liquidation[{index}].event_time_ms"
            )
            if event_time_ms > received_at_ms + MAX_FUTURE_SKEW_MS:
                raise BybitRealtimeStreamError("liquidation event is future-dated")
            _decimal(row.get("p"), f"liquidation[{index}].price")
            _decimal(row.get("v"), f"liquidation[{index}].quantity")
            event_times.append(event_time_ms)
            normalized.append(
                {
                    "event_time_ms": event_time_ms,
                    "liquidated_position_side": side,
                    "bankruptcy_price": str(row["p"]),
                    "quantity": str(row["v"]),
                }
            )
        payload = {
            "provider": _PROVIDER,
            "topic": topic,
            "message_type": "snapshot",
            "source_generated_at_ms": source_generated_at_ms,
            "liquidations": normalized,
        }
        self.latest.setdefault(symbol, {})["liquidation"] = {
            "count": len(normalized),
            "last_event_time_ms": max(event_times),
            "received_at_ms": received_at_ms,
        }
        return self._event(
            topic=topic,
            channel="all_liquidation",
            symbol=symbol,
            event_time_ms=max(event_times),
            received_at_ms=received_at_ms,
            source_sequence=_hash(normalized),
            payload=payload,
        )

    def _ticker_event(
        self,
        raw: Mapping[str, Any],
        *,
        topic: str,
        symbol: str,
        received_at_ms: int,
        source_generated_at_ms: int,
    ) -> dict[str, Any]:
        message_type = raw.get("type")
        data = raw.get("data")
        if message_type not in {"snapshot", "delta"} or not isinstance(data, Mapping):
            raise BybitRealtimeStreamError("ticker frame is invalid")
        if str(data.get("symbol", "")) != symbol:
            raise BybitRealtimeStreamError("ticker symbol mismatch")
        cross_sequence = _integer(raw.get("cs"), "ticker.cross_sequence")
        event_time_ms = source_generated_at_ms
        current = self._ticker.get(symbol)
        if message_type == "delta" and current is None:
            raise BybitRealtimeStreamError("ticker delta arrived before snapshot")
        merged = {} if message_type == "snapshot" else dict(current or {})
        merged.update({str(key): value for key, value in data.items()})
        for name in (
            "lastPrice",
            "markPrice",
            "indexPrice",
            "openInterest",
            "openInterestValue",
            "fundingRate",
            "bid1Price",
            "bid1Size",
            "ask1Price",
            "ask1Size",
        ):
            if name in merged and merged[name] not in {None, ""}:
                _decimal(merged[name], f"ticker.{name}", allow_zero=True)
        self._ticker[symbol] = merged
        self.latest.setdefault(symbol, {})["ticker"] = {
            "cross_sequence": cross_sequence,
            "event_time_ms": event_time_ms,
            "received_at_ms": received_at_ms,
            "last_price": merged.get("lastPrice"),
            "mark_price": merged.get("markPrice"),
            "index_price": merged.get("indexPrice"),
            "open_interest": merged.get("openInterest"),
            "funding_rate": merged.get("fundingRate"),
        }
        payload = {
            "provider": _PROVIDER,
            "topic": topic,
            "message_type": message_type,
            "source_generated_at_ms": source_generated_at_ms,
            "cross_sequence": cross_sequence,
            "delta": dict(data),
        }
        return self._event(
            topic=topic,
            channel="ticker",
            symbol=symbol,
            event_time_ms=event_time_ms,
            received_at_ms=received_at_ms,
            source_sequence=f"{cross_sequence}:{source_generated_at_ms}:{message_type}",
            payload=payload,
        )

    def handle(self, raw: object, *, received_at_ms: int) -> bool:
        if not isinstance(raw, Mapping):
            raise BybitRealtimeStreamError("frame must be a mapping")
        received = _integer(received_at_ms, "received_at_ms")
        if raw.get("op") in {"subscribe", "ping", "pong"}:
            if raw.get("success") is False:
                raise BybitRealtimeStreamError("Bybit control frame reports failure")
            return False
        topic, prefix, symbol = self._topic(raw)
        if topic not in self._allowed or symbol not in self.symbols:
            raise BybitRealtimeStreamError("topic is not subscribed")
        source_generated_at_ms = _integer(raw.get("ts"), "source_generated_at_ms")
        if source_generated_at_ms > received + MAX_FUTURE_SKEW_MS:
            raise BybitRealtimeStreamError("source frame is future-dated")
        if prefix == "orderbook":
            event = self._book_event(
                raw,
                topic=topic,
                symbol=symbol,
                received_at_ms=received,
                source_generated_at_ms=source_generated_at_ms,
            )
        elif prefix == "publicTrade":
            event = self._trade_event(
                raw,
                topic=topic,
                symbol=symbol,
                received_at_ms=received,
                source_generated_at_ms=source_generated_at_ms,
            )
        elif prefix == "allLiquidation":
            event = self._liquidation_event(
                raw,
                topic=topic,
                symbol=symbol,
                received_at_ms=received,
                source_generated_at_ms=source_generated_at_ms,
            )
        else:
            event = self._ticker_event(
                raw,
                topic=topic,
                symbol=symbol,
                received_at_ms=received,
                source_generated_at_ms=source_generated_at_ms,
            )
        self._pending.append(event)
        self.event_counts[str(event["channel"])] += 1
        self.last_received_at_ms = received
        self.last_error = None
        return True

    def reject(self, error: Exception) -> None:
        self.rejected_frames += 1
        self.last_error = str(error)

    def drain(self, *, processed_at_ms: int) -> list[dict[str, Any]]:
        processed = _integer(processed_at_ms, "processed_at_ms")
        output = []
        for item in self._pending:
            if int(item["received_at_ms"]) > processed:
                raise BybitRealtimeStreamError("processed_at_ms precedes receive time")
            event = dict(item)
            event["processed_at_ms"] = processed
            output.append(event)
        self._pending.clear()
        return output

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def status(
        self,
        *,
        generated_at_ms: int,
        connected: bool,
        reconnects: int,
        store_head: Mapping[str, Any],
    ) -> dict[str, Any]:
        generated = _integer(generated_at_ms, "generated_at_ms")
        return {
            "schema_version": 1,
            "provider": _PROVIDER,
            "venue": _VENUE,
            "generated_at_ms": generated,
            "connected": bool(connected),
            "streaming": bool(connected and self.last_received_at_ms is not None),
            "verdict": (
                "PASS"
                if connected and self.last_received_at_ms is not None
                else (
                    "COLLECTED"
                    if self.last_received_at_ms is not None
                    else "INITIALIZING"
                )
            ),
            "symbols": list(self.symbols),
            "depth": self.depth,
            "topics": list(self.topics),
            "event_counts": dict(sorted(self.event_counts.items())),
            "pending_count": self.pending_count,
            "rejected_frames": self.rejected_frames,
            "last_error": self.last_error,
            "last_received_at_ms": self.last_received_at_ms,
            "lag_ms": (
                None
                if self.last_received_at_ms is None
                else max(0, generated - self.last_received_at_ms)
            ),
            "reconnects": reconnects,
            "latest": self.latest,
            "store": dict(store_head),
            "safety": {
                "public_market_data_only": True,
                "credentials": False,
                "private_stream": False,
                "broker_connectivity": False,
                "order_submission": False,
                "live_trading": False,
            },
        }


async def _flush(
    state: BybitLinearState,
    store: PublicMarketEventStore,
    *,
    store_path: str,
    checkpoint_path: str,
    chain_checkpoint: dict[str, Any],
    status_path: str,
    connected: bool,
    reconnects: int,
) -> dict[str, Any]:
    processed_at_ms = int(time.time() * 1000)
    free_bytes = shutil.disk_usage(Path(store_path).resolve().parent).free
    if free_bytes < MIN_FREE_STORAGE_BYTES:
        raise BybitRealtimeStreamError("free storage is below the fixed safety guard")
    events = state.drain(processed_at_ms=processed_at_ms)
    head = store.append_events(events) if events else store.head()
    verification = store.verify_chain_from(
        checkpoint_seq=int(chain_checkpoint["verified_seq"]),
        checkpoint_row_hash=str(chain_checkpoint["verified_row_hash"]),
    )
    if (
        not verification.get("valid")
        or verification.get("verified_seq") != head["head_seq"]
        or verification.get("verified_row_hash") != head["head_hash"]
    ):
        raise BybitRealtimeStreamError("public event chain suffix verification failed")
    chain_checkpoint.update(
        {
            "verified_at_ms": processed_at_ms,
            "verified_seq": verification["verified_seq"],
            "verified_row_hash": verification["verified_row_hash"],
            "verification_mode": "INCREMENTAL_FROM_TRUSTED_LOCAL_CHECKPOINT",
            "verified_suffix_events": verification["verified_events"],
        }
    )
    written = _write_chain_checkpoint(checkpoint_path, chain_checkpoint)
    chain_checkpoint["checkpoint_hash"] = written["checkpoint_hash"]
    head["chain"] = dict(chain_checkpoint)
    head["free_storage_bytes"] = free_bytes
    status = state.status(
        generated_at_ms=processed_at_ms,
        connected=connected,
        reconnects=reconnects,
        store_head=head,
    )
    atomic_write_json(status_path, status, indent=2)
    return status


async def run_stream(
    *,
    symbols: Sequence[str],
    depth: int,
    store_path: str,
    status_path: str,
    checkpoint_path: str = DEFAULT_CHAIN_CHECKPOINT,
    run_seconds: int = 0,
) -> dict[str, Any]:
    if isinstance(run_seconds, bool) or not isinstance(run_seconds, int) or run_seconds < 0:
        raise ValueError("run_seconds must be a non-negative integer")
    state = BybitLinearState(symbols, depth=depth)
    os.makedirs(os.path.dirname(store_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(status_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(checkpoint_path) or ".", exist_ok=True)
    store = PublicMarketEventStore(store_path, create=True)
    chain_checkpoint = _initial_chain_checkpoint(
        store,
        store_path=store_path,
        checkpoint_path=checkpoint_path,
        now_ms=int(time.time() * 1000),
    )
    reconnects = 0
    started = time.monotonic()
    deadline = None if run_seconds == 0 else started + run_seconds
    status: dict[str, Any] = {}
    try:
        while deadline is None or time.monotonic() < deadline:
            try:
                async with connect(
                    PUBLIC_LINEAR_URL,
                    open_timeout=15,
                    close_timeout=5,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=16 * 1024 * 1024,
                ) as websocket:
                    await websocket.send(_canonical(subscribe_request(symbols, depth=depth)))
                    next_flush = time.monotonic() + 1.0
                    next_app_ping = time.monotonic() + 20.0
                    while deadline is None or time.monotonic() < deadline:
                        now = time.monotonic()
                        timeout = min(
                            1.0,
                            max(0.01, next_flush - now),
                            max(0.01, next_app_ping - now),
                        )
                        if deadline is not None:
                            timeout = min(timeout, max(0.01, deadline - now))
                        try:
                            encoded = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                        except TimeoutError:
                            encoded = None
                        if encoded is not None:
                            received_at_ms = int(time.time() * 1000)
                            try:
                                raw = json.loads(encoded)
                                state.handle(raw, received_at_ms=received_at_ms)
                            except (json.JSONDecodeError, BybitRealtimeStreamError) as exc:
                                state.reject(exc)
                        now = time.monotonic()
                        if now >= next_app_ping:
                            await websocket.send(_canonical({"op": "ping"}))
                            next_app_ping = now + 20.0
                        if now >= next_flush:
                            status = await _flush(
                                state,
                                store,
                                store_path=store_path,
                                checkpoint_path=checkpoint_path,
                                chain_checkpoint=chain_checkpoint,
                                status_path=status_path,
                                connected=True,
                                reconnects=reconnects,
                            )
                            next_flush = now + 1.0
                    break
            except Exception as exc:
                state.reject(exc)
                reconnects += 1
                status = await _flush(
                    state,
                    store,
                    store_path=store_path,
                    checkpoint_path=checkpoint_path,
                    chain_checkpoint=chain_checkpoint,
                    status_path=status_path,
                    connected=False,
                    reconnects=reconnects,
                )
                if deadline is not None and time.monotonic() >= deadline:
                    break
                await asyncio.sleep(min(30.0, float(2 ** min(reconnects, 4))))
        status = await _flush(
            state,
            store,
            store_path=store_path,
            checkpoint_path=checkpoint_path,
            chain_checkpoint=chain_checkpoint,
            status_path=status_path,
            connected=False,
            reconnects=reconnects,
        )
        return status
    finally:
        store.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect credential-free Bybit linear events with three clocks"
    )
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--depth", type=int, choices=(50, 200), default=DEFAULT_DEPTH)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--status", default=DEFAULT_STATUS)
    parser.add_argument("--checkpoint", default=DEFAULT_CHAIN_CHECKPOINT)
    parser.add_argument("--run-seconds", type=int, default=0)
    args = parser.parse_args(argv)
    symbols = tuple(part.strip().upper() for part in args.symbols.split(",") if part.strip())
    status = asyncio.run(
        run_stream(
            symbols=symbols,
            depth=args.depth,
            store_path=args.store,
            status_path=args.status,
            checkpoint_path=args.checkpoint,
            run_seconds=args.run_seconds,
        )
    )
    print(
        json.dumps(
            {
                "event_counts": status["event_counts"],
                "rejected_frames": status["rejected_frames"],
                "reconnects": status["reconnects"],
                "store": status["store"],
                "verdict": status["verdict"],
            },
            sort_keys=True,
        )
    )
    return 0 if status["last_received_at_ms"] is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BybitLinearState",
    "BybitRealtimeStreamError",
    "DEFAULT_DEPTH",
    "DEFAULT_CHAIN_CHECKPOINT",
    "DEFAULT_STATUS",
    "DEFAULT_STORE",
    "DEFAULT_SYMBOLS",
    "PUBLIC_LINEAR_URL",
    "run_stream",
    "subscribe_request",
    "subscription_topics",
]
