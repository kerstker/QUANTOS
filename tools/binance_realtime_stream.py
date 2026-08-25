"""Resilient public Binance USD-M WebSocket stream for L2 paper research.

The worker follows Binance's routed WebSocket split:
``/public`` carries book/depth/RPI-depth and ``/market`` carries trades, marks,
klines, liquidation events, and contract-status changes.  It accepts no
credentials and has no order methods.
High-frequency events are reduced into deterministic five-second snapshots so
the durable store remains useful instead of becoming an unbounded packet dump.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

from websockets.asyncio.client import connect

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SUCCESS,
)
from services.data.binance_realtime_store import (
    MAX_FUTURE_SKEW_MS,
    BinanceRealtimeStore,
)
from services.data.binance_stream_cursor import (
    StreamCursorRecord,
    StreamCursorState,
    StreamCursorStore,
)
from services.features.realtime_feature_batch_store import (
    trusted_cursor_chain_checkpoint,
    trusted_source_chain_checkpoint,
)
from tools import (
    binance_cursor_chain_audit_process,
    binance_depth_snapshot_process,
    binance_store_chain_audit_process,
)
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.binance_rest_gap_fill import RestGapFillError, fetch_depth_snapshot
from tools.cache_io import atomic_write_json


BASE_WSS = "wss://fstream.binance.com"
DEFAULT_STORE = os.path.join("data", "cache", "binance_realtime_stream.db")
DEFAULT_STATUS = os.path.join("data", "cache", "binance_realtime_stream_status.json")
DEFAULT_BATCH_STORE = os.path.join(
    "data", "cache", "binance_realtime_feature_batches.db"
)
DEFAULT_DURATION_SECONDS = 23 * 60 * 60
FLUSH_SECONDS = 5.0
MAX_STREAM_LAG_MS = 10_000
CHAIN_VERIFY_INTERVAL_SECONDS = 5 * 60.0
FULL_CHAIN_AUDIT_INTERVAL_MS = 24 * 60 * 60 * 1_000
CURSOR_FULL_CHAIN_AUDIT_INTERVAL_MS = 24 * 60 * 60 * 1_000
# DGX ARM64 evidence on 2026-08-26: the 45.9M-event cursor authority exceeded
# 90 minutes and the source authority approached the feature worker's 45-minute
# service ceiling.  Start resident renewal four hours before proof expiry; the
# isolated verifiers retain a three-hour hard ceiling, leaving one hour of
# explicit scheduling margin instead of renewing only after the proof is stale.
SOURCE_FULL_AUDIT_LEAD_MS = 4 * 60 * 60 * 1_000
CURSOR_FULL_AUDIT_LEAD_MS = 4 * 60 * 60 * 1_000


class BinanceStreamDataError(RuntimeError):
    """A stream frame violates the documented public market-data contract."""


def stream_urls(tickers: Sequence[str]) -> dict[str, str]:
    selected = tuple(dict.fromkeys(str(item).lower() for item in tickers))
    public = [
        stream
        for ticker in selected
        for stream in (
            f"{ticker}usdt@bookTicker",
            f"{ticker}usdt@depth20@500ms",
            f"{ticker}usdt@rpiDepth@500ms",
        )
    ]
    market = [
        stream
        for ticker in selected
        for stream in (
            f"{ticker}usdt@markPrice@1s",
            f"{ticker}usdt@aggTrade",
            f"{ticker}usdt@kline_1m",
        )
    ]
    market.extend(("!forceOrder@arr", "!contractInfo"))
    return {
        "public": f"{BASE_WSS}/public/stream?streams={'/'.join(public)}",
        "market": f"{BASE_WSS}/market/stream?streams={'/'.join(market)}",
    }


def _finite(value: object, field: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BinanceStreamDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise BinanceStreamDataError(f"{field} is invalid")
    return result


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise BinanceStreamDataError(f"{field} is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BinanceStreamDataError(f"{field} is invalid") from exc
    if result < 0:
        raise BinanceStreamDataError(f"{field} is invalid")
    return result


def _depth_levels(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> list[list[float]]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise BinanceStreamDataError(f"{field} has no levels")
    output = []
    for row in value:
        if not isinstance(row, list) or len(row) < 2:
            raise BinanceStreamDataError(f"{field} level is malformed")
        price = _finite(row[0], f"{field}.price", positive=True)
        quantity = _finite(row[1], f"{field}.quantity")
        if quantity < 0:
            raise BinanceStreamDataError(f"{field}.quantity is negative")
        output.append([price, quantity])
    return output


def _cursor_fields(record: StreamCursorRecord) -> dict[str, object]:
    return {
        "cursor_state": record.state.value,
        "cursor_update_id": record.cursor_update_id,
        "cursor_event_time_ms": record.event_time_ms,
        "cursor_reason_code": record.reason_code,
        "cursor_row_hash": record.row_hash,
    }


def _recovery_error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "REST_TIMEOUT"
    if getattr(exc, "code", None) == 429 or "429" in str(exc):
        return "HTTP_429"
    if "empty" in str(exc).lower():
        return "EMPTY_REST_RESULT"
    if isinstance(exc, (RestGapFillError, ValueError, TypeError, KeyError)):
        return "REST_GAP_FILL_INVALID"
    return "REST_GAP_FILL_ERROR"


def _recover_depth_cursor(
    cursor_store: StreamCursorStore,
    symbol: str,
    *,
    observed_at_ms: int,
    rest_depth_fetch=fetch_depth_snapshot,
) -> StreamCursorRecord:
    """Attempt one explicit public-REST recovery; every error stays BLOCKED."""
    cursor_store.begin_recovery(
        "depth20",
        symbol,
        observed_at_ms=int(observed_at_ms),
    )
    try:
        snapshot = rest_depth_fetch(symbol, observed_at_ms=int(observed_at_ms))
        if not isinstance(snapshot, Mapping) or not snapshot:
            raise ValueError("empty result")
        return cursor_store.complete_recovery(
            "depth20",
            symbol,
            snapshot_update_id=int(snapshot["last_update_id"]),
            event_time_ms=int(snapshot["event_time_ms"]),
            observed_at_ms=int(observed_at_ms),
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed recovery boundary
        current = cursor_store.latest("depth20", symbol)
        if current is not None and current.state is StreamCursorState.RECOVERING:
            return cursor_store.fail_recovery(
                "depth20",
                symbol,
                observed_at_ms=int(observed_at_ms),
                error_code=_recovery_error_code(exc),
            )
        raise


async def _recover_depth_cursor_async(
    cursor_store: StreamCursorStore,
    symbol: str,
    *,
    observed_at_ms: int,
    rest_depth_fetch=fetch_depth_snapshot,
) -> StreamCursorRecord:
    """Keep SQLite ownership on the event loop while REST runs off-thread."""
    cursor_store.begin_recovery(
        "depth20",
        symbol,
        observed_at_ms=int(observed_at_ms),
    )
    try:
        snapshot = (
            await _fetch_depth_snapshot_subprocess_async(
                symbol,
                observed_at_ms=observed_at_ms,
            )
            if rest_depth_fetch is fetch_depth_snapshot
            else await asyncio.to_thread(
                rest_depth_fetch,
                symbol,
                observed_at_ms=observed_at_ms,
            )
        )
        if not isinstance(snapshot, Mapping) or not snapshot:
            raise ValueError("empty result")
        return cursor_store.complete_recovery(
            "depth20",
            symbol,
            snapshot_update_id=int(snapshot["last_update_id"]),
            event_time_ms=int(snapshot["event_time_ms"]),
            observed_at_ms=int(observed_at_ms),
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed recovery boundary
        current = cursor_store.latest("depth20", symbol)
        if current is not None and current.state is StreamCursorState.RECOVERING:
            return cursor_store.fail_recovery(
                "depth20",
                symbol,
                observed_at_ms=int(observed_at_ms),
                error_code=_recovery_error_code(exc),
            )
        raise


async def _recover_depth_cursors_async(
    cursor_store: StreamCursorStore,
    symbols: Sequence[str],
    *,
    rest_depth_fetch=fetch_depth_snapshot,
) -> list[tuple[str, StreamCursorRecord]]:
    """Recover independent symbol cursors concurrently on one event-loop owner.

    Public REST calls run in independent children/threads.  The synchronous
    journal transitions before and after each await remain serialized on this
    event loop, preserving the store's single-writer contract.
    """

    async def recover(symbol: str) -> tuple[str, StreamCursorRecord]:
        return (
            symbol,
            await _recover_depth_cursor_async(
                cursor_store,
                symbol,
                observed_at_ms=int(time.time() * 1000),
                rest_depth_fetch=rest_depth_fetch,
            ),
        )

    return list(await asyncio.gather(*(recover(symbol) for symbol in symbols)))


class StreamState:
    """In-memory reduction of documented frames into bounded snapshots."""

    def __init__(
        self,
        tickers: Sequence[str],
        *,
        cursor_store: StreamCursorStore | None = None,
    ) -> None:
        self.symbols = {f"{str(ticker).upper()}USDT" for ticker in tickers}
        self.latest: dict[str, dict[str, Any]] = {
            symbol: {} for symbol in sorted(self.symbols)
        }
        self.trade_buckets: dict[str, dict[str, float | int]] = {}
        self.liquidation_buckets: dict[str, dict[str, float | int]] = {}
        self.rpi_delta_buckets: dict[str, dict[str, Any]] = {}
        self.rpi_last_update_ids: dict[str, int] = {}
        self.event_counts: dict[str, int] = {}
        self.rejected_frames = 0
        self.last_received_at_ms: int | None = None
        self.connections = {"public": False, "market": False}
        self.reconnects = {"public": 0, "market": 0}
        self.last_errors: dict[str, str] = {}
        self.cursor_store = cursor_store

    def _count(self, channel: str) -> None:
        self.event_counts[channel] = self.event_counts.get(channel, 0) + 1

    def handle(self, raw: object, *, received_at_ms: int) -> None:
        if not isinstance(raw, Mapping):
            raise BinanceStreamDataError("combined stream frame is not an object")
        stream = str(raw.get("stream", ""))
        data = raw.get("data")
        if not stream or not isinstance(data, Mapping):
            raise BinanceStreamDataError("combined stream wrapper is malformed")
        if data.get("st") not in (None, 1):
            return
        received_at_ms = _integer(received_at_ms, "received_at_ms")
        frame_event_time = data.get("E")
        if frame_event_time is None and "@bookTicker" in stream:
            frame_event_time = data.get("T")
        if frame_event_time is not None:
            event_time_ms = _integer(frame_event_time, "event_time")
            if event_time_ms > received_at_ms + MAX_FUTURE_SKEW_MS:
                raise BinanceStreamDataError(
                    "exchange event time exceeds bounded future skew"
                )
        event = str(data.get("e", ""))
        if stream == "!forceOrder@arr" or event == "forceOrder":
            self._handle_liquidation(data, received_at_ms=received_at_ms)
            self._count("forceOrder")
            self.last_received_at_ms = received_at_ms
            return
        if stream == "!contractInfo" or event == "contractInfo":
            self._handle_contract_info(data, received_at_ms=received_at_ms)
            self._count("contractInfo")
            self.last_received_at_ms = received_at_ms
            return
        symbol = str(data.get("s", "")).upper()
        if symbol not in self.symbols:
            return
        slot = self.latest[symbol]
        if "@rpiDepth" in stream:
            self._handle_rpi_depth(symbol, data, received_at_ms=received_at_ms)
            self._count("rpiDepth")
        elif "@bookTicker" in stream:
            event_time = _integer(data.get("E", data.get("T")), "book.event_time")
            bid = _finite(data.get("b"), "book.bid", positive=True)
            ask = _finite(data.get("a"), "book.ask", positive=True)
            if ask < bid:
                raise BinanceStreamDataError("book ask is below bid")
            mid = (bid + ask) / 2.0
            slot["book"] = {
                "event_time_ms": event_time,
                "received_at_ms": received_at_ms,
                "transaction_time_ms": _integer(
                    data.get("T", event_time), "book.transaction_time"
                ),
                "update_id": _integer(data.get("u"), "book.update_id"),
                "bid": bid,
                "bid_qty": _finite(data.get("B"), "book.bid_qty"),
                "ask": ask,
                "ask_qty": _finite(data.get("A"), "book.ask_qty"),
                "mid": mid,
                "spread_bps": (ask - bid) / mid * 10_000.0,
            }
            self._count("bookTicker")
        elif "@depth20" in stream:
            event_time = _integer(data.get("E"), "depth.event_time")
            transaction_time = _integer(data.get("T"), "depth.transaction_time")
            first_update_id = _integer(data.get("U"), "depth.first_update_id")
            final_update_id = _integer(data.get("u"), "depth.final_update_id")
            previous_final_update_id = _integer(
                data.get("pu"), "depth.previous_final_update_id"
            )
            bids = _depth_levels(data.get("b"), "depth.bids")
            asks = _depth_levels(data.get("a"), "depth.asks")
            cursor = (
                None
                if self.cursor_store is None
                else self.cursor_store.observe(
                    channel="depth20",
                    instrument_id=symbol,
                    first_update_id=first_update_id,
                    final_update_id=final_update_id,
                    previous_final_update_id=previous_final_update_id,
                    event_time_ms=event_time,
                    observed_at_ms=received_at_ms,
                )
            )
            slot["depth20"] = {
                "event_time_ms": event_time,
                "received_at_ms": received_at_ms,
                "transaction_time_ms": transaction_time,
                "first_update_id": first_update_id,
                "final_update_id": final_update_id,
                "previous_final_update_id": previous_final_update_id,
                "bids": bids,
                "asks": asks,
                **({} if cursor is None else _cursor_fields(cursor)),
            }
            self._count("depth20")
        elif "@markPrice" in stream or event == "markPriceUpdate":
            slot["mark"] = {
                "event_time_ms": _integer(data.get("E"), "mark.event_time"),
                "received_at_ms": received_at_ms,
                "mark_price": _finite(data.get("p"), "mark.price", positive=True),
                "index_price": _finite(data.get("i"), "mark.index", positive=True),
                "estimated_settle_price": _finite(
                    data.get("P"), "mark.estimated_settle"
                ),
                "funding_rate": _finite(data.get("r"), "mark.funding"),
                "next_funding_time_ms": _integer(
                    data.get("T"), "mark.next_funding_time"
                ),
            }
            self._count("markPrice")
        elif "@kline_1m" in stream or event == "kline":
            kline = data.get("k")
            if not isinstance(kline, Mapping):
                raise BinanceStreamDataError("kline payload is missing")
            slot["kline_1m"] = {
                "event_time_ms": _integer(data.get("E"), "kline.event_time"),
                "received_at_ms": received_at_ms,
                "open_time_ms": _integer(kline.get("t"), "kline.open_time"),
                "close_time_ms": _integer(kline.get("T"), "kline.close_time"),
                "open": _finite(kline.get("o"), "kline.open", positive=True),
                "high": _finite(kline.get("h"), "kline.high", positive=True),
                "low": _finite(kline.get("l"), "kline.low", positive=True),
                "close": _finite(kline.get("c"), "kline.close", positive=True),
                "volume": _finite(kline.get("v"), "kline.volume"),
                "quote_volume": _finite(kline.get("q"), "kline.quote_volume"),
                "trade_count": _integer(kline.get("n"), "kline.trade_count"),
                "taker_buy_base_volume": _finite(
                    kline.get("V"), "kline.taker_buy_base_volume"
                ),
                "taker_buy_quote_volume": _finite(
                    kline.get("Q"), "kline.taker_buy_quote_volume"
                ),
                "closed": bool(kline.get("x")),
            }
            self._count("kline_1m")
        elif "@aggTrade" in stream or event == "aggTrade":
            price = _finite(data.get("p"), "trade.price", positive=True)
            quantity = _finite(data.get("q"), "trade.quantity", positive=True)
            bucket = self.trade_buckets.setdefault(
                symbol,
                {
                    "count": 0,
                    "quantity": 0.0,
                    "notional": 0.0,
                    "buy_taker_notional": 0.0,
                    "sell_taker_notional": 0.0,
                    "first_event_time_ms": _integer(
                        data.get("E"), "trade.event_time"
                    ),
                    "last_event_time_ms": 0,
                    "first_received_at_ms": received_at_ms,
                    "last_received_at_ms": 0,
                    "last_trade_time_ms": 0,
                },
            )
            notional = price * quantity
            bucket["count"] = int(bucket["count"]) + 1
            bucket["quantity"] = float(bucket["quantity"]) + quantity
            bucket["notional"] = float(bucket["notional"]) + notional
            side = "sell_taker_notional" if bool(data.get("m")) else "buy_taker_notional"
            bucket[side] = float(bucket[side]) + notional
            bucket["last_event_time_ms"] = _integer(
                data.get("E"), "trade.event_time"
            )
            bucket["last_received_at_ms"] = received_at_ms
            bucket["last_trade_time_ms"] = _integer(
                data.get("T"), "trade.trade_time"
            )
            self._count("aggTrade")
        else:
            raise BinanceStreamDataError(f"unsupported stream {stream}")
        self.last_received_at_ms = received_at_ms

    def _handle_rpi_depth(
        self,
        symbol: str,
        data: Mapping[str, Any],
        *,
        received_at_ms: int,
    ) -> None:
        event_time = _integer(data.get("E"), "rpi_depth.event_time")
        transaction_time = _integer(
            data.get("T"), "rpi_depth.transaction_time"
        )
        first_update_id = _integer(
            data.get("U"), "rpi_depth.first_update_id"
        )
        final_update_id = _integer(
            data.get("u"), "rpi_depth.final_update_id"
        )
        previous_final_update_id = _integer(
            data.get("pu"), "rpi_depth.previous_final_update_id"
        )
        if final_update_id < first_update_id:
            raise BinanceStreamDataError("RPI depth update range is invalid")
        bids = _depth_levels(
            data.get("b"), "rpi_depth.bids", allow_empty=True
        )
        asks = _depth_levels(
            data.get("a"), "rpi_depth.asks", allow_empty=True
        )
        previous = self.rpi_last_update_ids.get(symbol)
        cursor = (
            None
            if self.cursor_store is None
            else self.cursor_store.observe(
                channel="rpiDepth",
                instrument_id=symbol,
                first_update_id=first_update_id,
                final_update_id=final_update_id,
                previous_final_update_id=previous_final_update_id,
                event_time_ms=event_time,
                observed_at_ms=received_at_ms,
            )
        )
        continuity_break = (
            not cursor.publishable
            if cursor is not None
            else previous is not None and previous_final_update_id != previous
        )
        bucket = self.rpi_delta_buckets.setdefault(
            symbol,
            {
                "event_count": 0,
                "bid_update_count": 0,
                "ask_update_count": 0,
                "bid_delete_count": 0,
                "ask_delete_count": 0,
                "continuity_breaks": 0,
                "first_received_at_ms": received_at_ms,
                "last_received_at_ms": 0,
            },
        )
        bucket["event_count"] = int(bucket["event_count"]) + 1
        bucket["bid_update_count"] = int(bucket["bid_update_count"]) + len(bids)
        bucket["ask_update_count"] = int(bucket["ask_update_count"]) + len(asks)
        bucket["bid_delete_count"] = int(bucket["bid_delete_count"]) + sum(
            1 for _, quantity in bids if quantity == 0
        )
        bucket["ask_delete_count"] = int(bucket["ask_delete_count"]) + sum(
            1 for _, quantity in asks if quantity == 0
        )
        bucket["continuity_breaks"] = int(bucket["continuity_breaks"]) + int(
            continuity_break
        )
        bucket.update(
            {
                "event_time_ms": event_time,
                "last_received_at_ms": received_at_ms,
                "transaction_time_ms": transaction_time,
                "first_update_id": first_update_id,
                "final_update_id": final_update_id,
                "previous_final_update_id": previous_final_update_id,
                "latest_bid_updates": bids,
                "latest_ask_updates": asks,
            }
        )
        self.rpi_last_update_ids[symbol] = final_update_id
        self.latest[symbol]["rpi_depth"] = {
            "event_time_ms": event_time,
            "received_at_ms": received_at_ms,
            "transaction_time_ms": transaction_time,
            "first_update_id": first_update_id,
            "final_update_id": final_update_id,
            "previous_final_update_id": previous_final_update_id,
            "continuity_ok": not continuity_break,
            "rpi_orders_included": True,
            "book_reconstruction": False,
            "paper_fill_eligible": False,
            **({} if cursor is None else _cursor_fields(cursor)),
        }

    def _handle_contract_info(
        self,
        data: Mapping[str, Any],
        *,
        received_at_ms: int,
    ) -> None:
        symbol = str(data.get("s", "")).upper()
        if symbol not in self.symbols:
            return
        status = str(data.get("cs", "")).upper()
        contract_type = str(data.get("ct", "")).upper()
        if not status or not contract_type:
            raise BinanceStreamDataError("contract info identity is incomplete")
        brackets_payload = data.get("bks", [])
        if not isinstance(brackets_payload, list):
            raise BinanceStreamDataError("contract brackets are malformed")
        brackets: list[dict[str, float | int]] = []
        for raw in brackets_payload:
            if not isinstance(raw, Mapping):
                raise BinanceStreamDataError("contract bracket is malformed")
            floor = _finite(raw.get("bnf"), "contract.bracket.floor")
            cap = _finite(raw.get("bnc"), "contract.bracket.cap")
            ratio = _finite(raw.get("mmr"), "contract.bracket.maintenance_ratio")
            if floor < 0 or cap < floor or ratio < 0:
                raise BinanceStreamDataError("contract bracket range is invalid")
            brackets.append(
                {
                    "bracket": _integer(raw.get("bs"), "contract.bracket.number"),
                    "floor_notional": floor,
                    "cap_notional": cap,
                    "maintenance_margin_ratio": ratio,
                    "cum": _finite(raw.get("cf"), "contract.bracket.cum"),
                    "min_leverage": _integer(
                        raw.get("mi"), "contract.bracket.min_leverage"
                    ),
                    "max_leverage": _integer(
                        raw.get("ma"), "contract.bracket.max_leverage"
                    ),
                }
            )
        self.latest[symbol]["contract_info"] = {
            "event_time_ms": _integer(data.get("E"), "contract.event_time"),
            "received_at_ms": received_at_ms,
            "contract_type": contract_type,
            "delivery_time_ms": _integer(
                data.get("dt"), "contract.delivery_time"
            ),
            "onboard_time_ms": _integer(
                data.get("ot"), "contract.onboard_time"
            ),
            "contract_status": status,
            "notional_brackets": brackets,
            "brackets_updated": bool(brackets),
            "paper_eligible": contract_type == "PERPETUAL" and status == "TRADING",
        }

    def _handle_liquidation(
        self,
        data: Mapping[str, Any],
        *,
        received_at_ms: int,
    ) -> None:
        order = data.get("o")
        if not isinstance(order, Mapping):
            raise BinanceStreamDataError("liquidation order is missing")
        symbol = str(order.get("s", "")).upper()
        if symbol not in self.symbols:
            return
        quantity = _finite(order.get("z", order.get("q")), "liquidation.quantity")
        price = _finite(
            order.get("ap") or order.get("p"), "liquidation.price", positive=True
        )
        bucket = self.liquidation_buckets.setdefault(
            symbol,
            {
                "count": 0,
                "notional": 0.0,
                "long_liquidation_notional": 0.0,
                "short_liquidation_notional": 0.0,
                "largest_notional": 0.0,
                "first_event_time_ms": _integer(
                    data.get("E"), "liquidation.event_time"
                ),
                "last_event_time_ms": 0,
                "first_received_at_ms": received_at_ms,
                "last_received_at_ms": 0,
                "last_trade_time_ms": 0,
            },
        )
        notional = quantity * price
        bucket["count"] = int(bucket["count"]) + 1
        bucket["notional"] = float(bucket["notional"]) + notional
        # A SELL forced order liquidates a long; BUY liquidates a short.
        side = (
            "long_liquidation_notional"
            if str(order.get("S", "")).upper() == "SELL"
            else "short_liquidation_notional"
        )
        bucket[side] = float(bucket[side]) + notional
        bucket["largest_notional"] = max(float(bucket["largest_notional"]), notional)
        bucket["last_event_time_ms"] = _integer(
            data.get("E"), "liquidation.event_time"
        )
        bucket["last_received_at_ms"] = received_at_ms
        bucket["last_trade_time_ms"] = _integer(
            order.get("T", data.get("E")), "liquidation.trade_time"
        )

    def snapshots(self, observed_at_ms: int) -> dict[str, dict[str, Any]]:
        output = {}
        for symbol, latest in self.latest.items():
            missing = [
                channel
                for channel in (
                    "book",
                    "depth20",
                    "rpi_depth",
                    "mark",
                    "kline_1m",
                )
                if channel not in latest
            ]
            times = [
                int(value["event_time_ms"])
                for value in latest.values()
                if isinstance(value, Mapping) and "event_time_ms" in value
            ]
            windows = (
                self.trade_buckets.get(symbol, {}),
                self.liquidation_buckets.get(symbol, {}),
                self.rpi_delta_buckets.get(symbol, {}),
            )
            times.extend(
                int(value["last_event_time_ms"])
                for value in windows
                if "last_event_time_ms" in value
            )
            if not times:
                continue
            source_event_time_ms = max(times)
            receive_times = [
                int(value["received_at_ms"])
                for value in latest.values()
                if isinstance(value, Mapping) and "received_at_ms" in value
            ]
            receive_times.extend(
                int(value["last_received_at_ms"])
                for value in windows
                if "last_received_at_ms" in value
            )
            if not receive_times:
                raise BinanceStreamDataError("snapshot has no exact receive clock")
            source_received_at_ms = max(receive_times)
            future_skew_ms = max(0, source_event_time_ms - int(observed_at_ms))
            if future_skew_ms > MAX_FUTURE_SKEW_MS:
                raise BinanceStreamDataError(
                    "exchange event time exceeds bounded future skew"
                )
            knowledge_time_ms = max(int(observed_at_ms), source_event_time_ms)
            depth = latest.get("depth20", {})
            sequence_contiguous = (
                not isinstance(depth, Mapping)
                or "cursor_state" not in depth
                or depth.get("cursor_state")
                in {
                    StreamCursorState.CONTIGUOUS.value,
                    StreamCursorState.RECOVERED.value,
                }
            )
            output[symbol] = {
                "schema_version": 1,
                "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
                "venue": "binance_usdm",
                "instrument_id": symbol,
                "event_time_ms": source_event_time_ms,
                "knowledge_time_ms": knowledge_time_ms,
                "received_at_ms": source_received_at_ms,
                "processed_at_ms": int(observed_at_ms),
                "observed_at_ms": int(observed_at_ms),
                **latest,
                "trade_window": dict(self.trade_buckets.get(symbol, {})),
                "liquidation_window": dict(
                    self.liquidation_buckets.get(symbol, {})
                ),
                "rpi_depth_window": dict(
                    self.rpi_delta_buckets.get(symbol, {})
                ),
                "quality": {
                    "complete_core": not missing,
                    "missing_channels": missing,
                    "sequence_contiguous": sequence_contiguous,
                    "exchange_clock_skew_ms": future_skew_ms,
                    "future_skew_bound_ms": MAX_FUTURE_SKEW_MS,
                    "rpi_diagnostic_only": True,
                    "private_access": False,
                    "broker_connectivity": False,
                    "order_submission": False,
                },
            }
        self.trade_buckets.clear()
        self.liquidation_buckets.clear()
        self.rpi_delta_buckets.clear()
        return output


async def _consume(
    endpoint: str,
    url: str,
    *,
    state: StreamState,
    stop: asyncio.Event,
    deadline: float,
) -> None:
    backoff = 1.0
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            async with connect(
                url,
                open_timeout=20,
                close_timeout=5,
                ping_interval=120,
                ping_timeout=60,
                max_queue=4096,
            ) as websocket:
                state.connections[endpoint] = True
                state.last_errors.pop(endpoint, None)
                backoff = 1.0
                while not stop.is_set() and time.monotonic() < deadline:
                    timeout = min(5.0, max(0.1, deadline - time.monotonic()))
                    try:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                    except TimeoutError:
                        continue
                    received_at_ms = int(time.time() * 1000)
                    try:
                        state.handle(json.loads(raw), received_at_ms=received_at_ms)
                    except (ValueError, TypeError, json.JSONDecodeError, BinanceStreamDataError):
                        state.rejected_frames += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconnect boundary
            state.last_errors[endpoint] = f"{type(exc).__name__}:{str(exc)[:160]}"
            state.reconnects[endpoint] += 1
        finally:
            state.connections[endpoint] = False
        if stop.is_set() or time.monotonic() >= deadline:
            break
        await asyncio.sleep(min(backoff, max(0.0, deadline - time.monotonic())))
        backoff = min(backoff * 2.0, 30.0)


def _status(
    state: StreamState,
    *,
    observed_at_ms: int,
    snapshots: Mapping[str, Mapping[str, Any]],
    store_path: str,
    row_count: int,
    chain_valid: bool | None,
    chain_verified_at_ms: int | None,
    cursor_status: Mapping[str, Any] | None = None,
    chain_verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    complete = sum(
        1
        for snapshot in snapshots.values()
        if snapshot.get("quality", {}).get("complete_core") is True
    )
    lag_ms = (
        None
        if state.last_received_at_ms is None
        else max(0, observed_at_ms - state.last_received_at_ms)
    )
    healthy = (
        complete == len(state.symbols)
        and all(state.connections.values())
        and lag_ms is not None
        and lag_ms <= MAX_STREAM_LAG_MS
        and chain_valid is True
    )
    if chain_valid is False:
        verdict = "BLOCKED_STORE_CHAIN"
    elif cursor_status is not None and cursor_status.get("publishable") is not True:
        verdict = "BLOCKED_SEQUENCE_GAP"
    else:
        verdict = "PASS" if healthy else "WARMING_OR_RECONNECTING"
    return {
        "schema_version": 1,
        "generated_at_ms": observed_at_ms,
        "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
        "verdict": verdict,
        "streaming": any(state.connections.values()),
        "connections": dict(state.connections),
        "reconnects": dict(state.reconnects),
        "last_errors": dict(state.last_errors),
        "last_received_at_ms": state.last_received_at_ms,
        "lag_ms": lag_ms,
        "coverage": {
            "requested": len(state.symbols),
            "complete_core": complete,
            "persisted_this_flush": len(snapshots),
        },
        "event_counts": dict(state.event_counts),
        "rejected_frames": state.rejected_frames,
        "latest": dict(snapshots),
        "store": {
            "path": store_path,
            "row_count": row_count,
            "chain_valid": chain_valid,
            "chain_verified_at_ms": chain_verified_at_ms,
            "chain_verification": (
                None if chain_verification is None else dict(chain_verification)
            ),
        },
        "cursor": None if cursor_status is None else dict(cursor_status),
        "routes": {
            "public": f"{BASE_WSS}/public",
            "market": f"{BASE_WSS}/market",
        },
        "safety": {
            "public_market_data_only": True,
            "credentials": False,
            "private_stream": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
        },
    }


def _verify_store_chain(
    store_path: str,
    *,
    checkpoint_seq: int = 0,
    checkpoint_row_hash: str = "",
) -> dict[str, Any]:
    """Verify one chain suffix on an isolated read connection."""
    verifier = BinanceRealtimeStore(store_path)
    try:
        return verifier.verify_chain_from(
            checkpoint_seq=checkpoint_seq,
            checkpoint_row_hash=checkpoint_row_hash,
        )
    finally:
        verifier.close()


def _verify_store_chain_subprocess(
    store_path: str,
    *,
    checkpoint_seq: int = 0,
    checkpoint_row_hash: str = "",
) -> dict[str, Any]:
    """Run a potentially full snapshot audit outside the resident process."""

    try:
        return binance_store_chain_audit_process.run_store_chain_audit(
            store_path,
            checkpoint_seq=checkpoint_seq,
            checkpoint_row_hash=checkpoint_row_hash,
        )
    except binance_store_chain_audit_process.StoreChainAuditProcessError as exc:
        raise BinanceStreamDataError(str(exc)) from exc


def _verify_cursor_chain_local(
    store_path: str,
    *,
    checkpoint_seq: int = 0,
    checkpoint_row_hash: str = "",
) -> dict[str, Any]:
    verifier = StreamCursorStore(store_path)
    try:
        return dict(
            verifier.verify_chain_from(
                checkpoint_seq=checkpoint_seq,
                checkpoint_row_hash=checkpoint_row_hash,
            )
        )
    finally:
        verifier.close()


def _verify_cursor_chain_subprocess(
    store_path: str,
    *,
    checkpoint_seq: int = 0,
    checkpoint_row_hash: str = "",
) -> dict[str, Any]:
    """Run a potentially full cursor audit outside the WebSocket process GIL."""
    try:
        return binance_cursor_chain_audit_process.run_cursor_chain_audit(
            store_path,
            checkpoint_seq=checkpoint_seq,
            checkpoint_row_hash=checkpoint_row_hash,
        )
    except binance_cursor_chain_audit_process.CursorChainAuditProcessError as exc:
        raise BinanceStreamDataError(str(exc)) from exc


async def _verify_cursor_chain_subprocess_async(
    store_path: str,
    *,
    checkpoint_seq: int = 0,
    checkpoint_row_hash: str = "",
) -> dict[str, Any]:
    """Run the fixed verifier with cancellation that kills and reaps its child."""

    command = binance_cursor_chain_audit_process.cursor_chain_audit_command(
        store_path,
        checkpoint_seq=checkpoint_seq,
        checkpoint_row_hash=checkpoint_row_hash,
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=os.path.dirname(
            os.path.dirname(
                os.path.abspath(binance_cursor_chain_audit_process.__file__)
            )
        ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    communication = asyncio.create_task(process.communicate())
    try:
        stdout, _stderr = await asyncio.wait_for(
            asyncio.shield(communication),
            timeout=(
                binance_cursor_chain_audit_process.CURSOR_CHAIN_VERIFY_TIMEOUT_SECONDS
            ),
        )
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await process.wait()
        await asyncio.shield(communication)
        raise
    except TimeoutError as exc:
        if process.returncode is None:
            process.kill()
        await process.wait()
        await asyncio.shield(communication)
        raise BinanceStreamDataError("cursor chain verifier timed out") from exc
    try:
        return binance_cursor_chain_audit_process.parse_cursor_chain_audit_result(
            stdout=stdout.decode("utf-8"),
            returncode=int(process.returncode),
        )
    except binance_cursor_chain_audit_process.CursorChainAuditProcessError as exc:
        raise BinanceStreamDataError(str(exc)) from exc


async def _run_fixed_subprocess(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    module_file: str,
) -> tuple[str, int]:
    """Run one fixed child; cancellation and timeout always kill and reap it."""

    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(module_file))),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    communication = asyncio.create_task(process.communicate())
    try:
        stdout, _stderr = await asyncio.wait_for(
            asyncio.shield(communication),
            timeout=timeout_seconds,
        )
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await process.wait()
        await asyncio.shield(communication)
        raise
    except TimeoutError:
        if process.returncode is None:
            process.kill()
        await process.wait()
        await asyncio.shield(communication)
        raise
    return stdout.decode("utf-8"), int(process.returncode)


async def _verify_store_chain_subprocess_async(
    store_path: str,
    *,
    checkpoint_seq: int = 0,
    checkpoint_row_hash: str = "",
) -> dict[str, Any]:
    """Run the snapshot verifier with a cancellation-safe process boundary."""

    command = binance_store_chain_audit_process.store_chain_audit_command(
        store_path,
        checkpoint_seq=checkpoint_seq,
        checkpoint_row_hash=checkpoint_row_hash,
    )
    try:
        stdout, returncode = await _run_fixed_subprocess(
            command,
            timeout_seconds=(
                binance_store_chain_audit_process.STORE_CHAIN_VERIFY_TIMEOUT_SECONDS
            ),
            module_file=binance_store_chain_audit_process.__file__,
        )
    except TimeoutError as exc:
        raise BinanceStreamDataError("store chain verifier timed out") from exc
    try:
        return binance_store_chain_audit_process.parse_store_chain_audit_result(
            stdout=stdout,
            returncode=returncode,
        )
    except binance_store_chain_audit_process.StoreChainAuditProcessError as exc:
        raise BinanceStreamDataError(str(exc)) from exc


async def _fetch_depth_snapshot_subprocess_async(
    symbol: str,
    *,
    observed_at_ms: int,
) -> dict[str, Any]:
    """Fetch credential-free REST depth without leaving executor threads behind."""

    command = binance_depth_snapshot_process.depth_snapshot_command(
        symbol,
        observed_at_ms=observed_at_ms,
    )
    try:
        stdout, returncode = await _run_fixed_subprocess(
            command,
            timeout_seconds=binance_depth_snapshot_process.DEPTH_SNAPSHOT_TIMEOUT_SECONDS,
            module_file=binance_depth_snapshot_process.__file__,
        )
    except TimeoutError as exc:
        raise TimeoutError("depth snapshot process timed out") from exc
    try:
        return binance_depth_snapshot_process.parse_depth_snapshot_result(
            stdout=stdout,
            returncode=returncode,
        )
    except binance_depth_snapshot_process.DepthSnapshotProcessError as exc:
        raise RestGapFillError(str(exc)) from exc


async def run_stream(
    *,
    tickers: Sequence[str] = FORWARD_MAJOR_TICKERS,
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    flush_seconds: float = FLUSH_SECONDS,
    store_path: str = DEFAULT_STORE,
    status_path: str = DEFAULT_STATUS,
    batch_store_path: str = DEFAULT_BATCH_STORE,
    rest_depth_fetch=fetch_depth_snapshot,
) -> dict[str, Any]:
    if duration_seconds <= 0 or flush_seconds <= 0:
        raise ValueError("duration and flush interval must be positive")
    selected = tuple(dict.fromkeys(str(item).upper() for item in tickers))
    PathLike = os.path.dirname(store_path)
    if PathLike:
        os.makedirs(PathLike, exist_ok=True)
    store = BinanceRealtimeStore(store_path, create=True)
    cursor_store = StreamCursorStore(store_path, create=True)
    state = StreamState(selected, cursor_store=cursor_store)
    urls = stream_urls(selected)
    stop = asyncio.Event()
    deadline = time.monotonic() + duration_seconds
    latest_status: dict[str, Any] = {}
    chain_valid: bool | None = None
    chain_verified_at_ms: int | None = None
    chain_verification: dict[str, Any] | None = None
    chain_checkpoint = trusted_source_chain_checkpoint(
        batch_store_path,
        now_ms=int(time.time() * 1_000),
        full_chain_audit_interval_ms=FULL_CHAIN_AUDIT_INTERVAL_MS,
    )
    cursor_chain_checkpoint = trusted_cursor_chain_checkpoint(
        batch_store_path,
        now_ms=int(time.time() * 1_000),
        full_chain_audit_interval_ms=CURSOR_FULL_CHAIN_AUDIT_INTERVAL_MS,
    )
    cursor_chain_verification: dict[str, Any] = {
        "valid": False,
        "anchor_seq": 0,
        "verified_suffix_events": 0,
        "verified_seq": 0,
        "verified_row_hash": "",
        "verification_mode": "FULL_SUBPROCESS_AUDIT_PENDING",
        "error": "CURSOR_CHAIN_AUDIT_PENDING",
    }

    async def flush_loop() -> None:
        nonlocal cursor_chain_checkpoint, cursor_chain_verification
        nonlocal latest_status
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(min(flush_seconds, max(0.0, deadline - time.monotonic())))
                blocked_symbols: list[str] = []
                for symbol in sorted(state.symbols):
                    cursor = cursor_store.latest("depth20", symbol)
                    if cursor is not None and cursor.state in {
                        StreamCursorState.GAP_DETECTED,
                        # A process can stop after RECOVERY_STARTED but before
                        # the REST result is journaled.  begin_recovery is
                        # idempotent for this persisted state, so a new worker
                        # must resume it instead of blocking forever.
                        StreamCursorState.RECOVERING,
                        StreamCursorState.BLOCKED,
                    }:
                        blocked_symbols.append(symbol)
                recovered_cursors = await _recover_depth_cursors_async(
                    cursor_store,
                    blocked_symbols,
                    rest_depth_fetch=rest_depth_fetch,
                )
                for symbol, recovered in recovered_cursors:
                    depth = state.latest[symbol].get("depth20")
                    if isinstance(depth, dict):
                        depth.update(_cursor_fields(recovered))
                # WebSocket consumers continue to advance while the public
                # REST recoveries above await I/O.  Refresh the observation
                # clock immediately before reducing their current state;
                # otherwise valid frames received during a slow recovery are
                # misclassified as future-skewed against an old flush clock.
                observed_at_ms = int(time.time() * 1000)
                if cursor_chain_verification.get("valid") is True:
                    previous_cursor_verification = cursor_chain_verification
                    cursor_chain_verification = _verify_cursor_chain_local(
                        store_path,
                        checkpoint_seq=int(
                            cursor_chain_checkpoint["checkpoint_seq"]
                        ),
                        checkpoint_row_hash=str(
                            cursor_chain_checkpoint["checkpoint_row_hash"]
                        ),
                    )
                    cursor_chain_verification["verification_mode"] = (
                        "INCREMENTAL_ON_FLUSH"
                    )
                    cursor_chain_verification["bootstrap_verification_mode"] = (
                        previous_cursor_verification.get(
                            "bootstrap_verification_mode",
                            previous_cursor_verification.get(
                                "verification_mode"
                            ),
                        )
                    )
                    cursor_chain_verification["batch_authority"] = (
                        previous_cursor_verification.get("batch_authority")
                    )
                    cursor_chain_verification["full_verified_at_ms"] = (
                        previous_cursor_verification.get(
                            "full_verified_at_ms"
                        )
                    )
                    cursor_chain_verification["verified_at_ms"] = int(
                        time.time() * 1000
                    )
                    if cursor_chain_verification.get("valid") is True:
                        cursor_chain_checkpoint = {
                            **cursor_chain_checkpoint,
                            "checkpoint_seq": int(
                                cursor_chain_verification["verified_seq"]
                            ),
                            "checkpoint_row_hash": str(
                                cursor_chain_verification["verified_row_hash"]
                            ),
                            "verification_mode": (
                                "INCREMENTAL_FROM_STREAM_CHECKPOINT"
                            ),
                        }
                # Cursor verification may decode a sizeable append-only suffix
                # while both WebSocket consumers keep advancing.  Snapshot the
                # state against the post-verification clock, not the clock from
                # before that work began.
                observed_at_ms = int(time.time() * 1000)
                snapshots = state.snapshots(observed_at_ms)
                for snapshot in snapshots.values():
                    store.append_snapshot(snapshot)
                row_count = store.row_count()
                cursor_status = cursor_store.trading_watermark(
                    (("depth20", symbol) for symbol in sorted(state.symbols)),
                    chain_verification=cursor_chain_verification,
                )
                # generated_at_ms is the semantic receipt publication clock.
                # Refresh it only after every status dependency is complete so
                # a slow audit cannot publish an already-stale heartbeat.
                published_at_ms = int(time.time() * 1000)
                latest_status = _status(
                    state,
                    observed_at_ms=published_at_ms,
                    snapshots=snapshots,
                    store_path=store_path,
                    row_count=row_count,
                    chain_valid=chain_valid,
                    chain_verified_at_ms=chain_verified_at_ms,
                    chain_verification=chain_verification,
                    cursor_status=cursor_status,
                )
                atomic_write_json(status_path, latest_status, indent=2)
        finally:
            stop.set()

    async def verify_chain_loop() -> None:
        """Verify suffixes and renew the source proof before it expires.

        Full source audits are deliberately resident background work.  The
        short-lived feature worker consumes this status attestation and checks
        only the local suffix; it must never start a multi-million-row genesis
        scan inside a five-minute schedule.
        """
        nonlocal chain_checkpoint, chain_valid, chain_verification
        nonlocal chain_verified_at_ms
        force_full_audit = False
        while not stop.is_set() and time.monotonic() < deadline:
            audit_checkpoint = dict(chain_checkpoint)
            if force_full_audit:
                audit_checkpoint = {
                    **audit_checkpoint,
                    "checkpoint_seq": 0,
                    "checkpoint_row_hash": "",
                    "verification_mode": "FULL_PERIODIC_AUDIT",
                }
                force_full_audit = False
            checkpoint_mode = str(audit_checkpoint["verification_mode"])
            verification = await _verify_store_chain_subprocess_async(
                store_path,
                checkpoint_seq=int(audit_checkpoint["checkpoint_seq"]),
                checkpoint_row_hash=str(audit_checkpoint["checkpoint_row_hash"]),
            )
            chain_verified_at_ms = int(time.time() * 1_000)
            chain_valid = verification.get("valid") is True
            verification_mode = {
                "FULL_BOOTSTRAP": "FULL_SUBPROCESS_BOOTSTRAP",
                "FULL_PERIODIC_AUDIT": "FULL_PERIODIC_SUBPROCESS_AUDIT",
            }.get(checkpoint_mode, checkpoint_mode)
            previous_bootstrap_mode = (
                chain_verification.get("bootstrap_verification_mode")
                if isinstance(chain_verification, Mapping)
                else None
            )
            bootstrap_mode = (
                verification_mode
                if checkpoint_mode in {"FULL_BOOTSTRAP", "FULL_PERIODIC_AUDIT"}
                else previous_bootstrap_mode or verification_mode
            )
            chain_verification = {
                **verification,
                "verification_mode": verification_mode,
                "bootstrap_verification_mode": bootstrap_mode,
                "verified_at_ms": chain_verified_at_ms,
                "batch_authority": audit_checkpoint["batch_authority"],
                "full_verified_at_ms": (
                    chain_verified_at_ms
                    if int(verification.get("anchor_seq", -1)) == 0
                    and chain_valid
                    else audit_checkpoint["full_verified_at_ms"]
                ),
            }
            if not chain_valid:
                return
            chain_checkpoint = {
                "checkpoint_seq": int(verification["verified_seq"]),
                "checkpoint_row_hash": str(verification["verified_row_hash"]),
                "full_verified_at_ms": chain_verification[
                    "full_verified_at_ms"
                ],
                "verification_mode": "INCREMENTAL_FROM_STREAM_CHECKPOINT",
                "batch_authority": audit_checkpoint["batch_authority"],
            }
            full_verified_at_ms = chain_checkpoint["full_verified_at_ms"]
            if type(full_verified_at_ms) is not int:
                return
            renewal_at_ms = (
                full_verified_at_ms
                + FULL_CHAIN_AUDIT_INTERVAL_MS
                - SOURCE_FULL_AUDIT_LEAD_MS
            )
            now_ms = int(time.time() * 1_000)
            until_renewal_seconds = (renewal_at_ms - now_ms) / 1_000
            if until_renewal_seconds <= 0:
                force_full_audit = True
                continue
            remaining = max(0.0, deadline - time.monotonic())
            wait_seconds = min(
                CHAIN_VERIFY_INTERVAL_SECONDS,
                until_renewal_seconds,
                remaining,
            )
            if wait_seconds <= 0:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait_seconds)
            except TimeoutError:
                if int(time.time() * 1_000) >= renewal_at_ms:
                    force_full_audit = True
                continue

    async def cursor_chain_audit_loop() -> None:
        """Audit once at boot, then renew the full proof before it expires."""
        nonlocal cursor_chain_checkpoint, cursor_chain_verification
        force_full_audit = False
        while not stop.is_set() and time.monotonic() < deadline:
            audit_checkpoint = dict(cursor_chain_checkpoint)
            if force_full_audit:
                # This genesis checkpoint belongs only to the subprocess audit.
                # Publishing it through the shared incremental checkpoint makes
                # the event loop re-read the entire cursor history on its next
                # flush and can freeze resident health for minutes.
                audit_checkpoint = {
                    **audit_checkpoint,
                    "checkpoint_seq": 0,
                    "checkpoint_row_hash": "",
                    "verification_mode": "FULL_PERIODIC_AUDIT",
                }
                force_full_audit = False
            checkpoint_mode = str(audit_checkpoint["verification_mode"])
            verification = await _verify_cursor_chain_subprocess_async(
                store_path,
                checkpoint_seq=int(audit_checkpoint["checkpoint_seq"]),
                checkpoint_row_hash=str(
                    audit_checkpoint["checkpoint_row_hash"]
                ),
            )
            verified_at_ms = int(time.time() * 1_000)
            verification["verification_mode"] = {
                "FULL_BOOTSTRAP": "FULL_SUBPROCESS_BOOTSTRAP",
                "FULL_PERIODIC_AUDIT": "FULL_PERIODIC_SUBPROCESS_AUDIT",
            }.get(checkpoint_mode, checkpoint_mode)
            verification["bootstrap_verification_mode"] = verification[
                "verification_mode"
            ]
            verification["verified_at_ms"] = verified_at_ms
            verification["batch_authority"] = audit_checkpoint[
                "batch_authority"
            ]
            verification["full_verified_at_ms"] = (
                verified_at_ms
                if int(verification.get("anchor_seq", -1)) == 0
                and verification.get("valid") is True
                else audit_checkpoint["full_verified_at_ms"]
            )
            if verification.get("valid") is not True:
                cursor_chain_verification = verification
                return
            current_checkpoint = dict(cursor_chain_checkpoint)
            current_seq = int(current_checkpoint["checkpoint_seq"])
            audited_seq = int(verification["verified_seq"])
            if (
                current_seq > audited_seq
                and cursor_chain_verification.get("valid") is True
            ):
                # Flushes may advance while the subprocess scans a stable SQLite
                # snapshot.  Preserve that newer independently-verified suffix,
                # while renewing its genesis-to-head full-audit provenance.
                cursor_chain_verification = {
                    **cursor_chain_verification,
                    "bootstrap_verification_mode": verification[
                        "bootstrap_verification_mode"
                    ],
                    "batch_authority": verification["batch_authority"],
                    "full_verified_at_ms": verification[
                        "full_verified_at_ms"
                    ],
                }
                cursor_chain_checkpoint = {
                    **current_checkpoint,
                    "full_verified_at_ms": verification[
                        "full_verified_at_ms"
                    ],
                    "verification_mode": (
                        "INCREMENTAL_FROM_STREAM_CHECKPOINT"
                    ),
                }
            else:
                cursor_chain_verification = verification
                cursor_chain_checkpoint = {
                    **audit_checkpoint,
                    "checkpoint_seq": audited_seq,
                    "checkpoint_row_hash": str(
                        verification["verified_row_hash"]
                    ),
                    "full_verified_at_ms": verification[
                        "full_verified_at_ms"
                    ],
                    "verification_mode": (
                        "INCREMENTAL_FROM_STREAM_CHECKPOINT"
                    ),
                }
            full_verified_at_ms = cursor_chain_checkpoint[
                "full_verified_at_ms"
            ]
            if type(full_verified_at_ms) is not int:
                return
            renewal_at_ms = (
                full_verified_at_ms
                + CURSOR_FULL_CHAIN_AUDIT_INTERVAL_MS
                - CURSOR_FULL_AUDIT_LEAD_MS
            )
            wait_seconds = max(
                0.0,
                (renewal_at_ms - int(time.time() * 1_000)) / 1_000,
            )
            wait_seconds = min(
                wait_seconds,
                max(0.0, deadline - time.monotonic()),
            )
            if wait_seconds <= 0:
                force_full_audit = True
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait_seconds)
                return
            except TimeoutError:
                if time.monotonic() >= deadline:
                    return
                force_full_audit = True

    try:
        await asyncio.gather(
            _consume("public", urls["public"], state=state, stop=stop, deadline=deadline),
            _consume("market", urls["market"], state=state, stop=stop, deadline=deadline),
            flush_loop(),
            verify_chain_loop(),
            cursor_chain_audit_loop(),
        )
    finally:
        cursor_store.close()
        store.close()
    return latest_status


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--duration-seconds", type=float, default=DEFAULT_DURATION_SECONDS
    )
    parser.add_argument("--flush-seconds", type=float, default=FLUSH_SECONDS)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--status", default=DEFAULT_STATUS)
    parser.add_argument("--batch-store", default=DEFAULT_BATCH_STORE)
    parser.add_argument("--verify-cursor-chain", action="store_true")
    parser.add_argument("--verify-store-chain", action="store_true")
    parser.add_argument("--cursor-checkpoint-seq", type=int, default=0)
    parser.add_argument("--cursor-checkpoint-row-hash", default="")
    args = parser.parse_args(argv)
    if args.verify_cursor_chain:
        verification = _verify_cursor_chain_local(
            args.store,
            checkpoint_seq=args.cursor_checkpoint_seq,
            checkpoint_row_hash=args.cursor_checkpoint_row_hash,
        )
        print(json.dumps(verification, sort_keys=True, separators=(",", ":")))
        return (
            SCHEDULE_EXIT_SUCCESS
            if verification.get("valid") is True
            else SCHEDULE_EXIT_FAILURE
        )
    if args.verify_store_chain:
        verification = _verify_store_chain(
            args.store,
            checkpoint_seq=args.cursor_checkpoint_seq,
            checkpoint_row_hash=args.cursor_checkpoint_row_hash,
        )
        print(json.dumps(verification, sort_keys=True, separators=(",", ":")))
        return (
            SCHEDULE_EXIT_SUCCESS
            if verification.get("valid") is True
            else SCHEDULE_EXIT_FAILURE
        )
    try:
        status = asyncio.run(
            run_stream(
                duration_seconds=args.duration_seconds,
                flush_seconds=args.flush_seconds,
                store_path=args.store,
                status_path=args.status,
                batch_store_path=args.batch_store,
            )
        )
    except KeyboardInterrupt:
        print("binance realtime stream: stopped by operator")
        return SCHEDULE_EXIT_SUCCESS
    except Exception as exc:
        detail = str(exc).replace("\n", " ")[:160]
        print(
            f"binance realtime stream: BLOCK ({type(exc).__name__})"
            f" {detail}"
        )
        return SCHEDULE_EXIT_FAILURE
    coverage = status.get("coverage", {})
    print(
        "binance realtime stream:",
        status.get("verdict"),
        f"{coverage.get('complete_core', 0)}/{coverage.get('requested', 0)} complete",
        f"events={sum(status.get('event_counts', {}).values())}",
    )
    return (
        SCHEDULE_EXIT_SUCCESS
        if (
            status.get("verdict") == "PASS"
            and coverage.get("complete_core") == coverage.get("requested")
        )
        else SCHEDULE_EXIT_FAILURE
    )


if __name__ == "__main__":
    raise SystemExit(main())
