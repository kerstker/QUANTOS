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
from services.data.binance_realtime_store import BinanceRealtimeStore
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.cache_io import atomic_write_json


BASE_WSS = "wss://fstream.binance.com"
DEFAULT_STORE = os.path.join("data", "cache", "binance_realtime_stream.db")
DEFAULT_STATUS = os.path.join("data", "cache", "binance_realtime_stream_status.json")
DEFAULT_DURATION_SECONDS = 23 * 60 * 60
FLUSH_SECONDS = 5.0
MAX_STREAM_LAG_MS = 10_000
CHAIN_VERIFY_INTERVAL_SECONDS = 5 * 60.0


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


class StreamState:
    """In-memory reduction of documented frames into bounded snapshots."""

    def __init__(self, tickers: Sequence[str]) -> None:
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
        event = str(data.get("e", ""))
        if stream == "!forceOrder@arr" or event == "forceOrder":
            self._handle_liquidation(data)
            self._count("forceOrder")
            self.last_received_at_ms = received_at_ms
            return
        if stream == "!contractInfo" or event == "contractInfo":
            self._handle_contract_info(data)
            self._count("contractInfo")
            self.last_received_at_ms = received_at_ms
            return
        symbol = str(data.get("s", "")).upper()
        if symbol not in self.symbols:
            return
        slot = self.latest[symbol]
        if "@rpiDepth" in stream:
            self._handle_rpi_depth(symbol, data)
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
            slot["depth20"] = {
                "event_time_ms": _integer(data.get("E"), "depth.event_time"),
                "transaction_time_ms": _integer(data.get("T"), "depth.transaction_time"),
                "first_update_id": _integer(data.get("U"), "depth.first_update_id"),
                "final_update_id": _integer(data.get("u"), "depth.final_update_id"),
                "previous_final_update_id": _integer(
                    data.get("pu"), "depth.previous_final_update_id"
                ),
                "bids": _depth_levels(data.get("b"), "depth.bids"),
                "asks": _depth_levels(data.get("a"), "depth.asks"),
            }
            self._count("depth20")
        elif "@markPrice" in stream or event == "markPriceUpdate":
            slot["mark"] = {
                "event_time_ms": _integer(data.get("E"), "mark.event_time"),
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
                    "last_trade_time_ms": 0,
                },
            )
            notional = price * quantity
            bucket["count"] = int(bucket["count"]) + 1
            bucket["quantity"] = float(bucket["quantity"]) + quantity
            bucket["notional"] = float(bucket["notional"]) + notional
            side = "sell_taker_notional" if bool(data.get("m")) else "buy_taker_notional"
            bucket[side] = float(bucket[side]) + notional
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
        continuity_break = previous is not None and previous_final_update_id != previous
        bucket = self.rpi_delta_buckets.setdefault(
            symbol,
            {
                "event_count": 0,
                "bid_update_count": 0,
                "ask_update_count": 0,
                "bid_delete_count": 0,
                "ask_delete_count": 0,
                "continuity_breaks": 0,
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
            "transaction_time_ms": transaction_time,
            "first_update_id": first_update_id,
            "final_update_id": final_update_id,
            "previous_final_update_id": previous_final_update_id,
            "continuity_ok": not continuity_break,
            "rpi_orders_included": True,
            "book_reconstruction": False,
            "paper_fill_eligible": False,
        }

    def _handle_contract_info(self, data: Mapping[str, Any]) -> None:
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

    def _handle_liquidation(self, data: Mapping[str, Any]) -> None:
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
            if not times:
                continue
            output[symbol] = {
                "schema_version": 1,
                "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
                "venue": "binance_usdm",
                "instrument_id": symbol,
                "event_time_ms": max(times),
                "knowledge_time_ms": int(observed_at_ms),
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
    return {
        "schema_version": 1,
        "generated_at_ms": observed_at_ms,
        "provider": "BINANCE_PUBLIC_USDM_WEBSOCKET",
        "verdict": "PASS" if healthy else "WARMING_OR_RECONNECTING",
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
        },
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


def _verify_store_chain(store_path: str) -> bool:
    """Verify the full append-only chain on an isolated read connection."""
    verifier = BinanceRealtimeStore(store_path)
    try:
        return verifier.chain_valid()
    finally:
        verifier.close()


async def run_stream(
    *,
    tickers: Sequence[str] = FORWARD_MAJOR_TICKERS,
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    flush_seconds: float = FLUSH_SECONDS,
    store_path: str = DEFAULT_STORE,
    status_path: str = DEFAULT_STATUS,
) -> dict[str, Any]:
    if duration_seconds <= 0 or flush_seconds <= 0:
        raise ValueError("duration and flush interval must be positive")
    selected = tuple(dict.fromkeys(str(item).upper() for item in tickers))
    PathLike = os.path.dirname(store_path)
    if PathLike:
        os.makedirs(PathLike, exist_ok=True)
    store = BinanceRealtimeStore(store_path, create=True)
    state = StreamState(selected)
    urls = stream_urls(selected)
    stop = asyncio.Event()
    deadline = time.monotonic() + duration_seconds
    latest_status: dict[str, Any] = {}
    chain_valid: bool | None = None
    chain_verified_at_ms: int | None = None

    async def flush_loop() -> None:
        nonlocal latest_status
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(min(flush_seconds, max(0.0, deadline - time.monotonic())))
                observed_at_ms = int(time.time() * 1000)
                snapshots = state.snapshots(observed_at_ms)
                for snapshot in snapshots.values():
                    store.append_snapshot(snapshot)
                latest_status = _status(
                    state,
                    observed_at_ms=observed_at_ms,
                    snapshots=snapshots,
                    store_path=store_path,
                    row_count=store.row_count(),
                    chain_valid=chain_valid,
                    chain_verified_at_ms=chain_verified_at_ms,
                )
                atomic_write_json(status_path, latest_status, indent=2)
        finally:
            stop.set()

    async def verify_chain_loop() -> None:
        nonlocal chain_valid, chain_verified_at_ms
        while not stop.is_set() and time.monotonic() < deadline:
            chain_valid = await asyncio.to_thread(_verify_store_chain, store_path)
            chain_verified_at_ms = int(time.time() * 1_000)
            remaining = max(0.0, deadline - time.monotonic())
            wait_seconds = min(CHAIN_VERIFY_INTERVAL_SECONDS, remaining)
            if wait_seconds <= 0:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait_seconds)
            except TimeoutError:
                continue

    try:
        await asyncio.gather(
            _consume("public", urls["public"], state=state, stop=stop, deadline=deadline),
            _consume("market", urls["market"], state=state, stop=stop, deadline=deadline),
            flush_loop(),
            verify_chain_loop(),
        )
    finally:
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
    args = parser.parse_args(argv)
    try:
        status = asyncio.run(
            run_stream(
                duration_seconds=args.duration_seconds,
                flush_seconds=args.flush_seconds,
                store_path=args.store,
                status_path=args.status,
            )
        )
    except KeyboardInterrupt:
        print("binance realtime stream: stopped by operator")
        return SCHEDULE_EXIT_SUCCESS
    except Exception as exc:
        print(f"binance realtime stream: BLOCK ({type(exc).__name__})")
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
        if coverage.get("complete_core") == coverage.get("requested")
        else SCHEDULE_EXIT_FAILURE
    )


if __name__ == "__main__":
    raise SystemExit(main())
