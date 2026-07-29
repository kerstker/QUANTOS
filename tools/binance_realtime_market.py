"""Collect the complete public Binance USD-M snapshot used by L2 paper research.

The worker is public-data only.  It never accepts credentials, account data, or
an order-capable client.  Every complete ticker snapshot is normalized with
source timestamps and appended to a hash-chained SQLite store.
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
from services.data.binance_realtime_store import BinanceRealtimeStore
from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.cache_io import atomic_write_json


BASE_URL = "https://fapi.binance.com"
DEFAULT_STORE = os.path.join("data", "cache", "binance_realtime_market.db")
DEFAULT_STATUS = os.path.join("data", "cache", "binance_realtime_market_status.json")
PERIOD = "5m"
MAX_BOOK_AGE_MS = 5 * 60_000
MAX_CANDLE_AGE_MS = 5 * 60_000
MAX_ROLLING_TICKER_AGE_MS = 5 * 60_000
MAX_POSITIONING_AGE_MS = 15 * 60_000
MAX_FUNDING_AGE_MS = 12 * 60 * 60_000


class BinanceRealtimeDataError(RuntimeError):
    """A public Binance response is missing, malformed, stale, or inconsistent."""


def _http_get_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "quantos-l2-public-market-data",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # public data only
        return json.loads(response.read().decode("utf-8"))


def _url(path: str, params: Mapping[str, object] | None = None) -> str:
    suffix = "" if not params else f"?{urlencode(params)}"
    return f"{BASE_URL}{path}{suffix}"


def _get(
    path: str,
    params: Mapping[str, object] | None,
    http_get: Callable[[str], object],
) -> tuple[str, object]:
    url = _url(path, params)
    return url, http_get(url)


def _positive(value: object, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BinanceRealtimeDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or result <= 0:
        raise BinanceRealtimeDataError(f"{field} is not positive")
    return result


def _finite(value: object, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BinanceRealtimeDataError(f"{field} is not numeric") from exc
    if not math.isfinite(result):
        raise BinanceRealtimeDataError(f"{field} is not finite")
    return result


def _nonnegative(value: object, field: str) -> float:
    result = _finite(value, field)
    if result < 0:
        raise BinanceRealtimeDataError(f"{field} is negative")
    return result


def _timestamp(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise BinanceRealtimeDataError(f"{field} is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BinanceRealtimeDataError(f"{field} is invalid") from exc
    if result < 0:
        raise BinanceRealtimeDataError(f"{field} is invalid")
    return result


def _latest_row(payload: object, field: str) -> Mapping[str, Any]:
    if not isinstance(payload, list) or not payload or not isinstance(payload[-1], Mapping):
        raise BinanceRealtimeDataError(f"{field} response has no rows")
    return payload[-1]


def _symbol_rows(payload: object, tickers: Sequence[str], field: str) -> dict[str, Mapping[str, Any]]:
    rows = payload if isinstance(payload, list) else [payload]
    wanted = {f"{ticker}USDT": ticker for ticker in tickers}
    selected: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ticker = wanted.get(str(row.get("symbol", "")).upper())
        if ticker is not None:
            selected[ticker] = row
    missing = sorted(set(tickers) - set(selected))
    if missing:
        raise BinanceRealtimeDataError(f"{field} response is missing {missing}")
    return selected


def _fresh(source_time: int, observed_at_ms: int, max_age_ms: int, field: str) -> None:
    age = observed_at_ms - source_time
    if age < 0 or age > max_age_ms:
        raise BinanceRealtimeDataError(f"{field} timestamp is stale or in the future")


def _closed_kline(payload: object, observed_at_ms: int) -> Sequence[object]:
    if not isinstance(payload, list):
        raise BinanceRealtimeDataError("kline response is not a list")
    candidates = [
        row
        for row in payload
        if isinstance(row, list)
        and len(row) >= 11
        and _timestamp(row[6], "kline_close_time") <= observed_at_ms
    ]
    if not candidates:
        raise BinanceRealtimeDataError("kline response has no closed candle")
    return candidates[-1]


def _reference_kline(
    payload: object,
    observed_at_ms: int,
    field: str,
    *,
    positive: bool,
) -> dict[str, Any]:
    """Parse one closed mark/index/premium kline without inventing volume."""
    if not isinstance(payload, list):
        raise BinanceRealtimeDataError(f"{field} response is not a list")
    candidates = [
        row
        for row in payload
        if isinstance(row, list)
        and len(row) >= 7
        and _timestamp(row[6], f"{field}_close_time") <= observed_at_ms
    ]
    if not candidates:
        raise BinanceRealtimeDataError(f"{field} response has no closed candle")
    row = candidates[-1]
    parse_value = _positive if positive else _finite
    open_value = parse_value(row[1], f"{field}_open")
    high_value = parse_value(row[2], f"{field}_high")
    low_value = parse_value(row[3], f"{field}_low")
    close_value = parse_value(row[4], f"{field}_close")
    if (
        high_value < max(open_value, close_value)
        or low_value > min(open_value, close_value)
        or high_value < low_value
    ):
        raise BinanceRealtimeDataError(f"{field} OHLC is invalid")
    close_time = _timestamp(row[6], f"{field}_close_time")
    _fresh(close_time, observed_at_ms, MAX_CANDLE_AGE_MS, field)
    return {
        "open_time_ms": _timestamp(row[0], f"{field}_open_time"),
        "close_time_ms": close_time,
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close_value,
    }


def _funding_policy(
    row: Mapping[str, Any] | None,
    symbol: str,
) -> dict[str, Any]:
    """Normalize Binance's adjustment-only funding policy response."""
    if row is None:
        return {
            "adjusted": False,
            "adjusted_rate_cap": None,
            "adjusted_rate_floor": None,
            "interval_hours": None,
        }
    if str(row.get("symbol", "")).upper() != symbol:
        raise BinanceRealtimeDataError("funding policy symbol mismatch")
    cap = _finite(row.get("adjustedFundingRateCap"), "funding_rate_cap")
    floor = _finite(row.get("adjustedFundingRateFloor"), "funding_rate_floor")
    interval_hours = _timestamp(
        row.get("fundingIntervalHours"), "funding_interval_hours"
    )
    if cap <= floor or interval_hours <= 0 or interval_hours > 24:
        raise BinanceRealtimeDataError("funding policy is invalid")
    return {
        "adjusted": True,
        "adjusted_rate_cap": cap,
        "adjusted_rate_floor": floor,
        "interval_hours": interval_hours,
    }


def _filter_map(contract: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = contract.get("filters")
    if not isinstance(raw, list):
        raise BinanceRealtimeDataError("exchange filters are missing")
    result: dict[str, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        filter_type = str(item.get("filterType", ""))
        if filter_type:
            result[filter_type] = item
    return result


def _parse_contract(contract: Mapping[str, Any], symbol: str) -> dict[str, Any]:
    if str(contract.get("symbol", "")).upper() != symbol:
        raise BinanceRealtimeDataError("contract symbol mismatch")
    if str(contract.get("status", "")).upper() != "TRADING":
        raise BinanceRealtimeDataError("contract is not trading")
    if str(contract.get("contractType", "")).upper() != "PERPETUAL":
        raise BinanceRealtimeDataError("contract is not perpetual")
    if str(contract.get("quoteAsset", "")).upper() != "USDT":
        raise BinanceRealtimeDataError("contract quote asset is not USDT")

    filters = _filter_map(contract)
    lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE")
    price = filters.get("PRICE_FILTER")
    notional = filters.get("MIN_NOTIONAL")
    if lot is None or price is None or notional is None:
        raise BinanceRealtimeDataError("required exchange filter is missing")
    return {
        "status": "TRADING",
        "contract_type": "PERPETUAL",
        "base_asset": str(contract.get("baseAsset", "")).upper(),
        "quote_asset": "USDT",
        "margin_asset": str(contract.get("marginAsset", "")).upper(),
        "onboard_time_ms": _timestamp(contract.get("onboardDate"), "onboard_date"),
        "delivery_time_ms": _timestamp(contract.get("deliveryDate"), "delivery_date"),
        "price_precision": _timestamp(
            contract.get("pricePrecision"), "price_precision"
        ),
        "quantity_precision": _timestamp(
            contract.get("quantityPrecision"), "quantity_precision"
        ),
        "tick_size": _positive(price.get("tickSize"), "tick_size"),
        "step_size": _positive(lot.get("stepSize"), "step_size"),
        "min_qty": _positive(lot.get("minQty"), "min_qty"),
        "max_qty": _positive(lot.get("maxQty"), "max_qty"),
        "min_notional": _positive(
            notional.get("notional"), "min_notional"
        ),
    }


def _parse_rolling_24h(
    row: Mapping[str, Any],
    symbol: str,
    observed_at_ms: int,
) -> tuple[dict[str, Any], int]:
    if str(row.get("symbol", "")).upper() != symbol:
        raise BinanceRealtimeDataError("24h ticker symbol mismatch")
    close_time = _timestamp(row.get("closeTime"), "24h_close_time")
    _fresh(
        close_time,
        observed_at_ms,
        MAX_ROLLING_TICKER_AGE_MS,
        "24h_ticker",
    )
    return {
        "open_time_ms": _timestamp(row.get("openTime"), "24h_open_time"),
        "close_time_ms": close_time,
        "open": _positive(row.get("openPrice"), "24h_open"),
        "high": _positive(row.get("highPrice"), "24h_high"),
        "low": _positive(row.get("lowPrice"), "24h_low"),
        "last": _positive(row.get("lastPrice"), "24h_last"),
        "weighted_average_price": _positive(
            row.get("weightedAvgPrice"), "24h_weighted_average_price"
        ),
        "price_change": _finite(row.get("priceChange"), "24h_price_change"),
        "price_change_percent": _finite(
            row.get("priceChangePercent"), "24h_price_change_percent"
        ),
        "base_volume": _nonnegative(row.get("volume"), "24h_base_volume"),
        "quote_volume": _nonnegative(
            row.get("quoteVolume"), "24h_quote_volume"
        ),
        "trade_count": _timestamp(row.get("count"), "24h_trade_count"),
    }, close_time


def _fetch_ticker(
    ticker: str,
    *,
    http_get: Callable[[str], object],
) -> dict[str, tuple[str, object]]:
    symbol = f"{ticker}USDT"
    requests = {
        "kline_1m": ("/fapi/v1/klines", {"symbol": symbol, "interval": "1m", "limit": 2}),
        "mark_kline_1m": (
            "/fapi/v1/markPriceKlines",
            {"symbol": symbol, "interval": "1m", "limit": 2},
        ),
        "index_kline_1m": (
            "/fapi/v1/indexPriceKlines",
            {"pair": symbol, "interval": "1m", "limit": 2},
        ),
        "premium_kline_1m": (
            "/fapi/v1/premiumIndexKlines",
            {"symbol": symbol, "interval": "1m", "limit": 2},
        ),
        "funding": ("/fapi/v1/fundingRate", {"symbol": symbol, "limit": 3}),
        "oi_current": ("/fapi/v1/openInterest", {"symbol": symbol}),
        "oi": (
            "/futures/data/openInterestHist",
            {"symbol": symbol, "period": PERIOD, "limit": 3},
        ),
        "global_ls": (
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol, "period": PERIOD, "limit": 3},
        ),
        "top_ls": (
            "/futures/data/topLongShortPositionRatio",
            {"symbol": symbol, "period": PERIOD, "limit": 3},
        ),
        "top_account_ls": (
            "/futures/data/topLongShortAccountRatio",
            {"symbol": symbol, "period": PERIOD, "limit": 3},
        ),
        "taker": (
            "/futures/data/takerlongshortRatio",
            {"symbol": symbol, "period": PERIOD, "limit": 3},
        ),
    }
    return {
        name: _get(path, params, http_get)
        for name, (path, params) in requests.items()
    }


def parse_snapshot(
    ticker: str,
    *,
    mark: Mapping[str, Any],
    book: Mapping[str, Any],
    contract: Mapping[str, Any],
    rolling_24h: Mapping[str, Any],
    funding_policy: Mapping[str, Any] | None,
    payloads: Mapping[str, tuple[str, object]],
    observed_at_ms: int,
) -> dict[str, Any]:
    """Normalize one complete ticker observation; any missing field is a BLOCK."""
    symbol = f"{ticker}USDT"
    if str(mark.get("symbol", "")).upper() != symbol:
        raise BinanceRealtimeDataError("mark symbol mismatch")
    if str(book.get("symbol", "")).upper() != symbol:
        raise BinanceRealtimeDataError("book symbol mismatch")

    mark_time = _timestamp(mark.get("time"), "mark_time")
    book_time = _timestamp(book.get("time"), "book_time")
    _fresh(mark_time, observed_at_ms, MAX_BOOK_AGE_MS, "mark")
    _fresh(book_time, observed_at_ms, MAX_BOOK_AGE_MS, "book")

    bid = _positive(book.get("bidPrice"), "bid")
    ask = _positive(book.get("askPrice"), "ask")
    if ask < bid:
        raise BinanceRealtimeDataError("ask is below bid")
    mid = (bid + ask) / 2.0
    mark_price = _positive(mark.get("markPrice"), "mark_price")
    index_price = _positive(mark.get("indexPrice"), "index_price")
    contract_rules = _parse_contract(contract, symbol)
    rolling_stats, rolling_time = _parse_rolling_24h(
        rolling_24h, symbol, observed_at_ms
    )

    kline = _closed_kline(payloads["kline_1m"][1], observed_at_ms)
    kline_time = _timestamp(kline[6], "kline_close_time")
    _fresh(kline_time, observed_at_ms, MAX_CANDLE_AGE_MS, "kline")
    mark_kline = _reference_kline(
        payloads["mark_kline_1m"][1],
        observed_at_ms,
        "mark_kline_1m",
        positive=True,
    )
    index_kline = _reference_kline(
        payloads["index_kline_1m"][1],
        observed_at_ms,
        "index_kline_1m",
        positive=True,
    )
    premium_kline = _reference_kline(
        payloads["premium_kline_1m"][1],
        observed_at_ms,
        "premium_kline_1m",
        positive=False,
    )
    funding = _latest_row(payloads["funding"][1], "funding")
    oi_current = payloads["oi_current"][1]
    if not isinstance(oi_current, Mapping):
        raise BinanceRealtimeDataError("current oi response is not an object")
    if str(oi_current.get("symbol", "")).upper() != symbol:
        raise BinanceRealtimeDataError("current oi symbol mismatch")
    oi = _latest_row(payloads["oi"][1], "oi")
    global_ls = _latest_row(payloads["global_ls"][1], "global_ls")
    top_ls = _latest_row(payloads["top_ls"][1], "top_ls")
    top_account_ls = _latest_row(
        payloads["top_account_ls"][1], "top_account_ls"
    )
    taker = _latest_row(payloads["taker"][1], "taker")

    source_times = {
        "book": book_time,
        "mark": mark_time,
        "kline_1m": kline_time,
        "mark_kline_1m": mark_kline["close_time_ms"],
        "index_kline_1m": index_kline["close_time_ms"],
        "premium_kline_1m": premium_kline["close_time_ms"],
        "funding": _timestamp(funding.get("fundingTime"), "funding_time"),
        "oi_current": _timestamp(
            oi_current.get("time"), "oi_current_time"
        ),
        "oi": _timestamp(oi.get("timestamp"), "oi_time"),
        "global_ls": _timestamp(global_ls.get("timestamp"), "global_ls_time"),
        "top_ls": _timestamp(top_ls.get("timestamp"), "top_ls_time"),
        "top_account_ls": _timestamp(
            top_account_ls.get("timestamp"), "top_account_ls_time"
        ),
        "taker": _timestamp(taker.get("timestamp"), "taker_time"),
        "rolling_24h": rolling_time,
    }
    _fresh(source_times["funding"], observed_at_ms, MAX_FUNDING_AGE_MS, "funding")
    _fresh(
        source_times["oi_current"],
        observed_at_ms,
        MAX_BOOK_AGE_MS,
        "oi_current",
    )
    for field in ("oi", "global_ls", "top_ls", "top_account_ls", "taker"):
        _fresh(source_times[field], observed_at_ms, MAX_POSITIONING_AGE_MS, field)

    next_funding_time = _timestamp(mark.get("nextFundingTime"), "next_funding_time")
    return {
        "schema_version": 2,
        "provider": "BINANCE_PUBLIC_USDM",
        "venue": "binance_usdm",
        "instrument_id": symbol,
        "event_time_ms": max(source_times.values()),
        "knowledge_time_ms": int(observed_at_ms),
        "observed_at_ms": int(observed_at_ms),
        "source_times_ms": source_times,
        "book": {
            "bid": bid,
            "bid_qty": _positive(book.get("bidQty"), "bid_qty"),
            "ask": ask,
            "ask_qty": _positive(book.get("askQty"), "ask_qty"),
            "mid": mid,
            "spread_bps": (ask - bid) / mid * 10_000.0,
        },
        "contract": contract_rules,
        "rolling_24h": rolling_stats,
        "perpetual": {
            "mark_price": mark_price,
            "index_price": index_price,
            "basis_bps": (mark_price - index_price) / index_price * 10_000.0,
            "estimated_settle_price": _nonnegative(
                mark.get("estimatedSettlePrice"), "estimated_settle_price"
            ),
            "interest_rate": _finite(mark.get("interestRate"), "interest_rate"),
            "last_funding_rate": _finite(
                mark.get("lastFundingRate"), "last_funding_rate"
            ),
            "next_funding_time_ms": next_funding_time,
            "current_open_interest": _positive(
                oi_current.get("openInterest"), "open_interest"
            ),
            "open_interest": _positive(
                oi.get("sumOpenInterest"), "open_interest"
            ),
            "open_interest_value": _positive(
                oi.get("sumOpenInterestValue"), "open_interest_value"
            ),
        },
        "positioning": {
            "global_long_short_ratio": _positive(
                global_ls.get("longShortRatio"), "global_long_short_ratio"
            ),
            "top_position_long_short_ratio": _positive(
                top_ls.get("longShortRatio"), "top_position_long_short_ratio"
            ),
            "top_account_long_short_ratio": _positive(
                top_account_ls.get("longShortRatio"),
                "top_account_long_short_ratio",
            ),
            "taker_buy_sell_ratio": _nonnegative(
                taker.get("buySellRatio"), "taker_buy_sell_ratio"
            ),
            "taker_buy_volume": _nonnegative(taker.get("buyVol"), "taker_buy_volume"),
            "taker_sell_volume": _nonnegative(taker.get("sellVol"), "taker_sell_volume"),
        },
        "last_settled_funding": {
            "time_ms": source_times["funding"],
            "rate": _finite(funding.get("fundingRate"), "settled_funding_rate"),
            "mark_price": _positive(
                funding.get("markPrice"), "settled_funding_mark_price"
            ),
        },
        "funding_policy": _funding_policy(funding_policy, symbol),
        "reference_1m": {
            "mark": mark_kline,
            "index": index_kline,
            "premium": premium_kline,
            "mark_index_basis_bps": (
                (mark_kline["close"] - index_kline["close"])
                / index_kline["close"]
                * 10_000.0
            ),
        },
        "closed_1m": {
            "open_time_ms": _timestamp(kline[0], "kline_open_time"),
            "close_time_ms": kline_time,
            "open": _positive(kline[1], "kline_open"),
            "high": _positive(kline[2], "kline_high"),
            "low": _positive(kline[3], "kline_low"),
            "close": _positive(kline[4], "kline_close"),
            "volume": _finite(kline[5], "kline_volume"),
            "trade_count": _timestamp(kline[8], "kline_trade_count"),
            "taker_buy_base_volume": _finite(
                kline[9], "kline_taker_buy_base_volume"
            ),
            "taker_buy_quote_volume": _finite(
                kline[10], "kline_taker_buy_quote_volume"
            ),
        },
        "quality": {
            "complete": True,
            "private_access": False,
            "broker_connectivity": False,
            "order_submission": False,
        },
    }


def _archive_payloads(
    ticker: str,
    *,
    observed_at_ms: int,
    mark_url: str,
    mark: Mapping[str, Any],
    book_url: str,
    book: Mapping[str, Any],
    payloads: Mapping[str, tuple[str, object]],
    archive_root: str,
) -> None:
    rows = {
        "mark": (mark_url, mark),
        "book": (book_url, book),
        **payloads,
    }
    for dataset, (source_uri, payload) in rows.items():
        count = len(payload) if isinstance(payload, list) else 1
        archive_json_response(
            root=archive_root,
            payload=payload,
            source_uri=source_uri,
            provider="Binance public USD-M market data",
            dataset_id=f"binance_realtime_{dataset}",
            instrument_id=f"{ticker}USDT",
            observed_at_ms=int(observed_at_ms),
            parse_diagnostics={
                "status": "ACCEPTED",
                "input_rows": count,
                "accepted_rows": count,
                "rejected_rows": 0,
                "duplicate_rows": 0,
            },
        )


def run_refresh(
    *,
    tickers: Sequence[str] = FORWARD_MAJOR_TICKERS,
    store_path: str = DEFAULT_STORE,
    status_path: str = DEFAULT_STATUS,
    archive_root: str | None = DEFAULT_RAW_ARCHIVE,
    http_get: Callable[[str], object] = _http_get_json,
    now_ms: int | None = None,
    max_workers: int = 8,
) -> dict[str, Any]:
    selected = tuple(dict.fromkeys(str(item).upper() for item in tickers))
    if not selected:
        raise BinanceRealtimeDataError("realtime universe is empty")
    for ticker in selected:
        if not ticker.isalnum():
            raise BinanceRealtimeDataError(f"invalid ticker {ticker!r}")

    mark_url, mark_payload = _get("/fapi/v1/premiumIndex", None, http_get)
    book_url, book_payload = _get("/fapi/v1/ticker/bookTicker", None, http_get)
    exchange_url, exchange_payload = _get("/fapi/v1/exchangeInfo", None, http_get)
    rolling_url, rolling_payload = _get("/fapi/v1/ticker/24hr", None, http_get)
    funding_info_url, funding_info_payload = _get(
        "/fapi/v1/fundingInfo", None, http_get
    )
    marks = _symbol_rows(mark_payload, selected, "mark")
    books = _symbol_rows(book_payload, selected, "book")
    if not isinstance(exchange_payload, Mapping):
        raise BinanceRealtimeDataError("exchange info response is not an object")
    contracts = _symbol_rows(exchange_payload.get("symbols"), selected, "exchange info")
    rolling_rows = _symbol_rows(rolling_payload, selected, "24h ticker")
    if not isinstance(funding_info_payload, list):
        raise BinanceRealtimeDataError("funding info response is not a list")
    funding_policies: dict[str, Mapping[str, Any]] = {}
    selected_symbols = {f"{ticker}USDT" for ticker in selected}
    for item in funding_info_payload:
        if not isinstance(item, Mapping):
            raise BinanceRealtimeDataError("funding info row is not an object")
        symbol = str(item.get("symbol", "")).upper()
        if symbol in selected_symbols:
            funding_policies[symbol] = item

    fetched: dict[str, dict[str, tuple[str, object]]] = {}
    errors: dict[str, dict[str, str]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), len(selected)))) as pool:
        futures = {
            pool.submit(_fetch_ticker, ticker, http_get=http_get): ticker
            for ticker in selected
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                fetched[ticker] = future.result()
            except Exception as exc:  # noqa: BLE001 - isolated per ticker
                errors[ticker] = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                }

    observed_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    Path(store_path).parent.mkdir(parents=True, exist_ok=True)
    store = BinanceRealtimeStore(store_path, create=True)
    inserted = 0
    snapshots: dict[str, dict[str, Any]] = {}
    try:
        for ticker in selected:
            if ticker in errors:
                continue
            try:
                snapshot = parse_snapshot(
                    ticker,
                    mark=marks[ticker],
                    book=books[ticker],
                    contract=contracts[ticker],
                    rolling_24h=rolling_rows[ticker],
                    funding_policy=funding_policies.get(f"{ticker}USDT"),
                    payloads=fetched[ticker],
                    observed_at_ms=observed_at_ms,
                )
                if archive_root is not None:
                    _archive_payloads(
                        ticker,
                        observed_at_ms=observed_at_ms,
                        mark_url=mark_url,
                        mark=marks[ticker],
                        book_url=book_url,
                        book=books[ticker],
                        payloads=fetched[ticker],
                        archive_root=archive_root,
                    )
                    archive_json_response(
                        root=archive_root,
                        payload=contracts[ticker],
                        source_uri=exchange_url,
                        provider="Binance public USD-M market data",
                        dataset_id="binance_realtime_exchange_info",
                        instrument_id=f"{ticker}USDT",
                        observed_at_ms=observed_at_ms,
                        parse_diagnostics={
                            "status": "ACCEPTED",
                            "input_rows": 1,
                            "accepted_rows": 1,
                            "rejected_rows": 0,
                            "duplicate_rows": 0,
                        },
                    )
                    archive_json_response(
                        root=archive_root,
                        payload=funding_policies.get(f"{ticker}USDT", []),
                        source_uri=funding_info_url,
                        provider="Binance public USD-M market data",
                        dataset_id="binance_realtime_funding_info",
                        instrument_id=f"{ticker}USDT",
                        observed_at_ms=observed_at_ms,
                        parse_diagnostics={
                            "status": "ACCEPTED",
                            "input_rows": int(
                                f"{ticker}USDT" in funding_policies
                            ),
                            "accepted_rows": int(
                                f"{ticker}USDT" in funding_policies
                            ),
                            "rejected_rows": 0,
                            "duplicate_rows": 0,
                        },
                    )
                    archive_json_response(
                        root=archive_root,
                        payload=rolling_rows[ticker],
                        source_uri=rolling_url,
                        provider="Binance public USD-M market data",
                        dataset_id="binance_realtime_ticker_24h",
                        instrument_id=f"{ticker}USDT",
                        observed_at_ms=observed_at_ms,
                        parse_diagnostics={
                            "status": "ACCEPTED",
                            "input_rows": 1,
                            "accepted_rows": 1,
                            "rejected_rows": 0,
                            "duplicate_rows": 0,
                        },
                    )
                inserted += int(store.append_snapshot(snapshot))
                snapshots[ticker] = snapshot
            except Exception as exc:  # noqa: BLE001 - fail closed per ticker
                errors[ticker] = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                }
        chain_valid = store.chain_valid()
        row_count = store.row_count()
    finally:
        store.close()

    complete = len(snapshots) == len(selected) and not errors and chain_valid
    status = {
        "schema_version": 2,
        "generated_at_ms": observed_at_ms,
        "provider": "BINANCE_PUBLIC_USDM",
        "universe": list(selected),
        "verdict": "PASS" if complete else "BLOCK",
        "complete": complete,
        "coverage": {
            "requested": len(selected),
            "complete": len(snapshots),
            "inserted": inserted,
            "failed": len(errors),
        },
        "latest": snapshots,
        "errors": errors,
        "store": {
            "path": store_path,
            "row_count": row_count,
            "chain_valid": chain_valid,
        },
        "fields": [
            "mark/index/basis/funding",
            "closed_1m_mark_index_premium_reference_ohlc",
            "funding_cap_floor_interval_adjustments",
            "best_bid_ask_and_qty",
            "closed_1m_ohlcv",
            "contract_lifecycle_and_exchange_filters",
            "rolling_24h_price_volume_and_trade_count",
            "current_open_interest",
            "open_interest_and_value_5m",
            "global_long_short_ratio",
            "top_position_long_short_ratio",
            "top_account_long_short_ratio",
            "taker_buy_sell_volume",
            "last_settled_funding",
        ],
        "safety": {
            "public_market_data_only": True,
            "credentials": False,
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
            "schema_version": 2,
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
                "broker_connectivity": False,
                "order_submission": False,
                "live_trading": False,
            },
        }
        atomic_write_json(DEFAULT_STATUS, status, indent=2)
        print(f"binance realtime market: BLOCK ({type(exc).__name__})")
        return SCHEDULE_EXIT_FAILURE
    coverage = status["coverage"]
    print(
        "binance realtime market:",
        status["verdict"],
        f"{coverage['complete']}/{coverage['requested']} complete",
        f"inserted={coverage['inserted']}",
        f"chain={status['store']['chain_valid']}",
    )
    return SCHEDULE_EXIT_SUCCESS if status["complete"] else SCHEDULE_EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
