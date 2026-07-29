"""Collect complete public Binance USD-M RPI-inclusive order books.

Binance exposes this surface only as a fixed 1000-level REST snapshot with
request weight 20.  QUANTOS therefore samples it hourly as a separate L2
diagnostic channel instead of burdening the five-minute execution loop.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SUCCESS,
)
from services.data.binance_systemic_store import BinanceSystemicStore
from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.cache_io import atomic_write_json


BASE_URL = "https://fapi.binance.com"
DEFAULT_STORE = os.path.join("data", "cache", "binance_rpi_depth.db")
DEFAULT_STATUS = os.path.join("data", "cache", "binance_rpi_depth_status.json")
REQUESTED_LEVELS = 1000
MAX_AGE_MS = 5 * 60_000


class BinanceRpiDepthError(RuntimeError):
    """An RPI-inclusive public order-book response failed validation."""


def _http_get_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "quantos-l2-public-rpi-depth",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _url(symbol: str) -> str:
    return (
        f"{BASE_URL}/fapi/v1/rpiDepth?"
        f"{urlencode({'symbol': symbol, 'limit': REQUESTED_LEVELS})}"
    )


def _timestamp(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise BinanceRpiDepthError(f"{field} is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BinanceRpiDepthError(f"{field} is invalid") from exc
    if result < 0:
        raise BinanceRpiDepthError(f"{field} is invalid")
    return result


def _positive(value: object, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BinanceRpiDepthError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or result <= 0:
        raise BinanceRpiDepthError(f"{field} is not positive")
    return result


def _levels(payload: object, field: str, *, descending: bool) -> list[list[float]]:
    if not isinstance(payload, list) or not payload:
        raise BinanceRpiDepthError(f"{field} levels are empty")
    if len(payload) > REQUESTED_LEVELS:
        raise BinanceRpiDepthError(f"{field} exceeds requested depth")
    levels: list[list[float]] = []
    previous: float | None = None
    for raw in payload:
        if (
            not isinstance(raw, Sequence)
            or isinstance(raw, (str, bytes))
            or len(raw) != 2
        ):
            raise BinanceRpiDepthError(f"{field} level shape is invalid")
        price = _positive(raw[0], f"{field} price")
        quantity = _positive(raw[1], f"{field} quantity")
        if previous is not None and (
            (descending and price >= previous)
            or (not descending and price <= previous)
        ):
            raise BinanceRpiDepthError(f"{field} prices are not strictly sorted")
        levels.append([price, quantity])
        previous = price
    return levels


def parse_snapshot(
    symbol: str,
    payload: object,
    *,
    knowledge_time_ms: int,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise BinanceRpiDepthError("RPI depth response is not an object")
    output_time_ms = _timestamp(payload.get("E"), "RPI output time")
    event_time_ms = _timestamp(payload.get("T"), "RPI transaction time")
    update_id = _timestamp(payload.get("lastUpdateId"), "RPI update id")
    if output_time_ms < event_time_ms:
        raise BinanceRpiDepthError("RPI output precedes transaction time")
    age = knowledge_time_ms - event_time_ms
    if age < 0 or age > MAX_AGE_MS:
        raise BinanceRpiDepthError("RPI depth is stale or in the future")
    bids = _levels(payload.get("bids"), "RPI bids", descending=True)
    asks = _levels(payload.get("asks"), "RPI asks", descending=False)
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    if best_ask <= best_bid:
        raise BinanceRpiDepthError("RPI book is crossed or locked")
    mid = (best_bid + best_ask) / 2.0
    bid_notional = sum(price * quantity for price, quantity in bids)
    ask_notional = sum(price * quantity for price, quantity in asks)
    total_notional = bid_notional + ask_notional
    return {
        "schema_version": 1,
        "provider": "BINANCE_PUBLIC_USDM",
        "venue": "binance_usdm",
        "dataset": "rpi_depth_1000",
        "instrument_id": symbol,
        "event_time_ms": event_time_ms,
        "knowledge_time_ms": knowledge_time_ms,
        "output_time_ms": output_time_ms,
        "last_update_id": update_id,
        "requested_levels": REQUESTED_LEVELS,
        "bids": bids,
        "asks": asks,
        "bid_level_count": len(bids),
        "ask_level_count": len(asks),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": (best_ask - best_bid) / mid * 10_000.0,
        "bid_notional": bid_notional,
        "ask_notional": ask_notional,
        "notional_imbalance": (
            (bid_notional - ask_notional) / total_notional
            if total_notional > 0
            else 0.0
        ),
        "quality": {
            "complete_response": True,
            "rpi_orders_included": True,
            "crossed_levels_hidden_by_provider": True,
            "private_access": False,
            "broker_connectivity": False,
            "order_submission": False,
        },
    }


def _fetch(
    ticker: str,
    http_get: Callable[[str], object],
) -> tuple[str, object]:
    url = _url(f"{ticker}USDT")
    return url, http_get(url)


def _append_snapshot(
    store: BinanceSystemicStore,
    snapshot: Mapping[str, Any],
) -> bool:
    dataset = str(snapshot["dataset"])
    instrument_id = str(snapshot["instrument_id"])
    event_time_ms = int(snapshot["event_time_ms"])
    existing = store.latest(dataset, instrument_id)
    if existing is not None and int(existing["event_time_ms"]) == event_time_ms:
        current = dict(snapshot)
        current["knowledge_time_ms"] = existing["knowledge_time_ms"]
        if current == existing:
            return False
    return store.append(snapshot)


def run_refresh(
    *,
    tickers: Sequence[str] = FORWARD_MAJOR_TICKERS,
    store_path: str = DEFAULT_STORE,
    status_path: str = DEFAULT_STATUS,
    archive_root: str | None = DEFAULT_RAW_ARCHIVE,
    http_get: Callable[[str], object] = _http_get_json,
    now_ms: int | None = None,
    max_workers: int = 4,
) -> dict[str, Any]:
    selected = tuple(dict.fromkeys(str(item).upper() for item in tickers))
    if not selected or any(not ticker.isalnum() for ticker in selected):
        raise BinanceRpiDepthError("RPI universe is invalid")
    fixed_now_ms = None if now_ms is None else int(now_ms)
    Path(store_path).parent.mkdir(parents=True, exist_ok=True)
    fetched: dict[str, tuple[str, object]] = {}
    errors: dict[str, dict[str, str]] = {}
    with ThreadPoolExecutor(
        max_workers=max(1, min(int(max_workers), len(selected)))
    ) as pool:
        futures = {
            pool.submit(_fetch, ticker, http_get): ticker
            for ticker in selected
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                fetched[ticker] = future.result()
            except Exception as exc:  # noqa: BLE001 - isolate one symbol
                errors[ticker] = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                }

    # Knowledge time is when the complete batch has been received, not when
    # network dispatch began.  Exchange event times may legitimately advance
    # while the four request waves are in flight.
    observed_at_ms = (
        int(time.time() * 1000) if fixed_now_ms is None else fixed_now_ms
    )
    store = BinanceSystemicStore(store_path, create=True)
    inserted = 0
    latest: dict[str, dict[str, Any]] = {}
    try:
        for ticker in selected:
            if ticker in errors:
                continue
            source_uri, payload = fetched[ticker]
            try:
                snapshot = parse_snapshot(
                    f"{ticker}USDT",
                    payload,
                    knowledge_time_ms=observed_at_ms,
                )
                if archive_root is not None:
                    archive_json_response(
                        root=archive_root,
                        payload=payload,
                        source_uri=source_uri,
                        provider="Binance public USD-M market data",
                        dataset_id="binance_rpi_depth_1000",
                        instrument_id=f"{ticker}USDT",
                        observed_at_ms=observed_at_ms,
                        parse_diagnostics={
                            "status": "ACCEPTED",
                            "input_rows": (
                                snapshot["bid_level_count"]
                                + snapshot["ask_level_count"]
                            ),
                            "accepted_rows": (
                                snapshot["bid_level_count"]
                                + snapshot["ask_level_count"]
                            ),
                            "rejected_rows": 0,
                            "duplicate_rows": 0,
                        },
                    )
                inserted += int(_append_snapshot(store, snapshot))
                latest[ticker] = {
                    "event_time_ms": snapshot["event_time_ms"],
                    "last_update_id": snapshot["last_update_id"],
                    "bid_level_count": snapshot["bid_level_count"],
                    "ask_level_count": snapshot["ask_level_count"],
                    "best_bid": snapshot["best_bid"],
                    "best_ask": snapshot["best_ask"],
                    "spread_bps": snapshot["spread_bps"],
                    "notional_imbalance": snapshot["notional_imbalance"],
                }
            except Exception as exc:  # noqa: BLE001 - fail closed per symbol
                errors[ticker] = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                }
        chain_valid = store.chain_valid()
        row_count = store.row_count()
    finally:
        store.close()

    complete = len(latest) == len(selected) and not errors and chain_valid
    status = {
        "schema_version": 1,
        "generated_at_ms": observed_at_ms,
        "provider": "BINANCE_PUBLIC_USDM",
        "verdict": "PASS" if complete else "BLOCK",
        "complete": complete,
        "universe": list(selected),
        "coverage": {
            "requested": len(selected),
            "complete": len(latest),
            "failed": len(errors),
        },
        "latest": latest,
        "errors": errors,
        "store": {
            "path": store_path,
            "row_count": row_count,
            "inserted": inserted,
            "chain_valid": chain_valid,
        },
        "request_policy": {
            "cadence": "1h",
            "requested_levels_per_side": REQUESTED_LEVELS,
            "official_ip_weight_per_symbol": 20,
            "universe_weight_per_run": 20 * len(selected),
        },
        "fields": [
            "rpi_inclusive_bid_1000",
            "rpi_inclusive_ask_1000",
            "spread_bps",
            "depth_notional",
            "notional_imbalance",
        ],
        "safety": {
            "public_market_data_only": True,
            "credentials": False,
            "account_endpoints": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
        },
    }
    atomic_write_json(status_path, status, indent=2)
    return status


def main() -> int:
    try:
        status = run_refresh()
    except Exception as exc:
        status = {
            "schema_version": 1,
            "generated_at_ms": int(time.time() * 1000),
            "provider": "BINANCE_PUBLIC_USDM",
            "verdict": "BLOCK",
            "complete": False,
            "errors": {
                "__cycle__": {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                }
            },
            "safety": {
                "public_market_data_only": True,
                "credentials": False,
                "account_endpoints": False,
                "broker_connectivity": False,
                "order_submission": False,
                "live_trading": False,
            },
        }
        atomic_write_json(DEFAULT_STATUS, status, indent=2)
        print(f"binance RPI depth: BLOCK ({type(exc).__name__})")
        return SCHEDULE_EXIT_FAILURE
    coverage = status["coverage"]
    print(
        "binance RPI depth:",
        status["verdict"],
        f"{coverage['complete']}/{coverage['requested']} complete",
        f"inserted={status['store']['inserted']}",
        f"chain={status['store']['chain_valid']}",
    )
    return SCHEDULE_EXIT_SUCCESS if status["complete"] else SCHEDULE_EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
