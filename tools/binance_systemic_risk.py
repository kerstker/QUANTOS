"""Collect public Binance USD-M systemic-risk and reference-market data.

This low-frequency L2 worker covers symbol ADL risk, the rolling 30-day
perpetual basis history, index constituents, and insurance-fund snapshots.
It has no credential, account, broker, order, or live-trading capability.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from services.data.binance_systemic_store import BinanceSystemicStore
from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.cache_io import atomic_write_json


BASE_URL = "https://fapi.binance.com"
DEFAULT_STORE = os.path.join("data", "cache", "binance_systemic_risk.db")
DEFAULT_STATUS = os.path.join(
    "data", "cache", "binance_systemic_risk_status.json"
)
BASIS_PERIOD = "1h"
HOUR_MS = 60 * 60_000
DAY_MS = 24 * HOUR_MS
BASIS_LOOKBACK_MS = 30 * DAY_MS
BASIS_REFRESH_BUDGET = 4
CONSTITUENT_REFRESH_BUDGET = 7
MAX_ADL_AGE_MS = 2 * HOUR_MS
MAX_BASIS_AGE_MS = 3 * HOUR_MS
MAX_CONSTITUENT_AGE_MS = HOUR_MS
MAX_INSURANCE_AGE_MS = 2 * DAY_MS
ADL_RATING_NORMALIZATION = {
    "LOW": "LOW",
    "MIDDLE": "MEDIUM",
    "MEDIUM": "MEDIUM",
    "HIGH": "HIGH",
}
DEFAULT_REQUEST_INTERVAL_SECONDS = 3.0
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 120
# ADL and insurance are optional-symbol endpoints. Fetch each complete public
# snapshot once per cycle (weight 1 each), then bind the selected row to every
# requested symbol. Only constituents (weight 2) remains symbol-scoped; basis
# has documented IP weight 0.
SHARED_ENDPOINT_WEIGHT = 2
ENDPOINT_WEIGHT_PER_SYMBOL = 2
_BAN_UNTIL_PATTERN = re.compile(r"\bbanned\s+until\s+(\d{13})\b", re.IGNORECASE)


class BinanceSystemicRiskError(RuntimeError):
    """A public systemic-risk response is malformed, stale, or inconsistent."""


class BinanceSystemicRateLimitError(BinanceSystemicRiskError):
    """Binance rejected the IP budget or a local cooldown is active."""

    def __init__(
        self,
        status_code: int,
        retry_after_seconds: int,
        endpoint: str | None = None,
    ) -> None:
        self.status_code = int(status_code)
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        self.endpoint = endpoint
        suffix = f" at {endpoint}" if endpoint else ""
        super().__init__(
            f"Binance HTTP {self.status_code}{suffix}; public REST cooldown "
            f"{self.retry_after_seconds}s"
        )


@dataclass(frozen=True)
class _HttpResult:
    payload: object
    used_weights: Mapping[str, int]


def _http_get_json(url: str) -> _HttpResult:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "quantos-l2-public-systemic-risk",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        used_weights: dict[str, int] = {}
        for name, value in response.headers.items():
            normalized = str(name).upper()
            if not normalized.startswith("X-MBX-USED-WEIGHT-"):
                continue
            try:
                used_weights[normalized] = int(value)
            except (TypeError, ValueError):
                continue
        return _HttpResult(
            payload=json.loads(response.read().decode("utf-8")),
            used_weights=used_weights,
        )


def _rate_limit_deadline(
    error: urllib.error.HTTPError,
    *,
    now_ms: int,
) -> tuple[int, int]:
    raw = error.headers.get("Retry-After") if error.headers is not None else None
    try:
        header_seconds = max(1, int(raw))
    except (TypeError, ValueError):
        header_seconds = DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
    ban_until_ms = int(now_ms) + header_seconds * 1_000
    try:
        body = error.read().decode("utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        body = ""
    match = _BAN_UNTIL_PATTERN.search(body)
    if match is not None:
        body_deadline = int(match.group(1))
        if body_deadline > int(now_ms):
            ban_until_ms = max(ban_until_ms, body_deadline)
    retry_after_seconds = max(
        1,
        math.ceil((ban_until_ms - int(now_ms)) / 1_000),
    )
    return retry_after_seconds, ban_until_ms


class _RateLimitedHttpGet:
    """Serialize public REST calls and stop the cycle after 418/429."""

    def __init__(
        self,
        delegate: Callable[[str], object],
        *,
        min_interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock_ms: Callable[[], int] = lambda: int(time.time() * 1_000),
        initial_ban_until_ms: int | None = None,
        initial_status_code: int | None = None,
        initial_endpoint: str | None = None,
    ) -> None:
        interval = float(min_interval_seconds)
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("min_interval_seconds must be finite and non-negative")
        self._delegate = delegate
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._wall_clock_ms = wall_clock_ms
        self._lock = threading.Lock()
        self._next_request_at = 0.0
        self._blocked_until = 0.0
        self._last_status_code: int | None = initial_status_code
        self._retry_after_seconds = 0
        self._rate_limited_endpoint: str | None = initial_endpoint
        self._ban_until_ms: int | None = None
        self._max_used_weights: dict[str, int] = {}
        if (
            initial_ban_until_ms is not None
            and int(initial_ban_until_ms) > self._wall_clock_ms()
        ):
            self._ban_until_ms = int(initial_ban_until_ms)
            self._retry_after_seconds = max(
                1,
                math.ceil(
                    (self._ban_until_ms - self._wall_clock_ms()) / 1_000
                ),
            )
            self._blocked_until = (
                self._clock() + self._retry_after_seconds
            )

    def __call__(self, url: str) -> object:
        with self._lock:
            now = self._clock()
            if now < self._blocked_until:
                remaining = max(1, math.ceil(self._blocked_until - now))
                raise BinanceSystemicRateLimitError(
                    self._last_status_code or 429,
                    remaining,
                    self._rate_limited_endpoint,
                )
            delay = max(0.0, self._next_request_at - now)
            if delay:
                self._sleep(delay)
            self._next_request_at = self._clock() + self._interval
            try:
                result = self._delegate(url)
            except urllib.error.HTTPError as exc:
                if exc.code not in {418, 429}:
                    raise
                retry_after, ban_until_ms = _rate_limit_deadline(
                    exc,
                    now_ms=self._wall_clock_ms(),
                )
                self._last_status_code = int(exc.code)
                self._retry_after_seconds = retry_after
                self._rate_limited_endpoint = urlsplit(url).path
                self._ban_until_ms = ban_until_ms
                self._blocked_until = self._clock() + retry_after
                raise BinanceSystemicRateLimitError(
                    exc.code,
                    retry_after,
                    self._rate_limited_endpoint,
                ) from exc
            if isinstance(result, _HttpResult):
                for name, value in result.used_weights.items():
                    self._max_used_weights[name] = max(
                        int(value),
                        self._max_used_weights.get(name, 0),
                    )
                return result.payload
            return result

    def telemetry(self) -> dict[str, Any]:
        now = self._clock()
        return {
            "min_request_interval_ms": round(self._interval * 1_000),
            "cooldown_active": now < self._blocked_until,
            "last_status_code": self._last_status_code,
            "retry_after_seconds": self._retry_after_seconds,
            "rate_limited_endpoint": self._rate_limited_endpoint,
            "ban_until_ms": self._ban_until_ms,
            "max_used_weights": dict(sorted(self._max_used_weights.items())),
        }


def _persisted_cooldown(
    status_path: str,
    *,
    now_ms: int,
) -> tuple[int | None, int | None, str | None]:
    try:
        document = json.loads(Path(status_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None, None, None
    if (
        not isinstance(document, Mapping)
        or document.get("provider") != "BINANCE_PUBLIC_USDM"
    ):
        return None, None, None
    telemetry = document.get("request_rate_limit")
    if not isinstance(telemetry, Mapping):
        return None, None, None
    try:
        ban_until_ms = int(telemetry.get("ban_until_ms"))
    except (TypeError, ValueError):
        return None, None, None
    if ban_until_ms <= int(now_ms):
        return None, None, None
    try:
        status_code = int(telemetry.get("last_status_code"))
    except (TypeError, ValueError):
        status_code = 418
    endpoint = telemetry.get("rate_limited_endpoint")
    return (
        ban_until_ms,
        status_code,
        endpoint if isinstance(endpoint, str) and endpoint else None,
    )


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


def _timestamp(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise BinanceSystemicRiskError(f"{field} is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BinanceSystemicRiskError(f"{field} is invalid") from exc
    if result < 0:
        raise BinanceSystemicRiskError(f"{field} is invalid")
    return result


def _finite(value: object, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BinanceSystemicRiskError(f"{field} is not numeric") from exc
    if not math.isfinite(result):
        raise BinanceSystemicRiskError(f"{field} is not finite")
    return result


def _positive(value: object, field: str) -> float:
    result = _finite(value, field)
    if result <= 0:
        raise BinanceSystemicRiskError(f"{field} is not positive")
    return result


def _nonnegative(value: object, field: str) -> float:
    result = _finite(value, field)
    if result < 0:
        raise BinanceSystemicRiskError(f"{field} is negative")
    return result


def _fresh(
    event_time_ms: int,
    knowledge_time_ms: int,
    max_age_ms: int,
    field: str,
) -> None:
    age = knowledge_time_ms - event_time_ms
    if age < 0 or age > max_age_ms:
        raise BinanceSystemicRiskError(
            f"{field} timestamp is stale or in the future"
        )


def _base_observation(
    dataset: str,
    symbol: str,
    event_time_ms: int,
    knowledge_time_ms: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "provider": "BINANCE_PUBLIC_USDM",
        "venue": "binance_usdm",
        "dataset": dataset,
        "instrument_id": symbol,
        "event_time_ms": event_time_ms,
        "knowledge_time_ms": knowledge_time_ms,
        "quality": {
            "complete": True,
            "private_access": False,
            "broker_connectivity": False,
            "order_submission": False,
        },
    }


def parse_adl_risk(
    symbol: str,
    payload: object,
    *,
    knowledge_time_ms: int,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise BinanceSystemicRiskError("ADL risk response is not an object")
    if str(payload.get("symbol", "")).upper() != symbol:
        raise BinanceSystemicRiskError("ADL risk symbol mismatch")
    raw_rating = str(payload.get("adlRisk", "")).upper()
    rating = ADL_RATING_NORMALIZATION.get(raw_rating)
    if rating is None:
        raise BinanceSystemicRiskError("ADL risk rating is invalid")
    event_time_ms = _timestamp(payload.get("updateTime"), "ADL updateTime")
    _fresh(event_time_ms, knowledge_time_ms, MAX_ADL_AGE_MS, "ADL risk")
    observation = _base_observation(
        "adl_risk", symbol, event_time_ms, knowledge_time_ms
    )
    observation["rating"] = rating
    observation["raw_rating"] = raw_rating
    return observation


def parse_basis_rows(
    symbol: str,
    payloads: Sequence[object],
    *,
    knowledge_time_ms: int,
    min_event_time_ms: int | None = None,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    seen: set[int] = set()
    for payload in payloads:
        if not isinstance(payload, list):
            raise BinanceSystemicRiskError("basis response is not a list")
        for raw in payload:
            if not isinstance(raw, Mapping):
                raise BinanceSystemicRiskError("basis row is not an object")
            if str(raw.get("pair", "")).upper() != symbol:
                raise BinanceSystemicRiskError("basis pair mismatch")
            if str(raw.get("contractType", "")).upper() != "PERPETUAL":
                raise BinanceSystemicRiskError("basis contract type mismatch")
            event_time_ms = _timestamp(raw.get("timestamp"), "basis timestamp")
            if (
                min_event_time_ms is not None
                and event_time_ms < int(min_event_time_ms)
            ):
                # Binance may return rows preceding startTime. They are already
                # sealed in the append-only store and must not be replayed as
                # if they were new observations.
                continue
            # The API timestamp is the period start. Persist only completed
            # hourly observations so a later refresh cannot revise an identity.
            if event_time_ms + HOUR_MS > knowledge_time_ms:
                continue
            if event_time_ms in seen:
                continue
            seen.add(event_time_ms)
            index_price = _positive(raw.get("indexPrice"), "basis indexPrice")
            futures_price = _positive(
                raw.get("futuresPrice"), "basis futuresPrice"
            )
            basis = _finite(raw.get("basis"), "basis")
            basis_rate = _finite(raw.get("basisRate"), "basisRate")
            annualized_raw = raw.get("annualizedBasisRate")
            annualized = (
                None
                if annualized_raw in (None, "")
                else _finite(annualized_raw, "annualizedBasisRate")
            )
            observation = _base_observation(
                "basis_1h", symbol, event_time_ms, knowledge_time_ms
            )
            observation.update(
                {
                    "contract_type": "PERPETUAL",
                    "period": BASIS_PERIOD,
                    "index_price": index_price,
                    "futures_price": futures_price,
                    "basis": basis,
                    "basis_rate": basis_rate,
                    "annualized_basis_rate": annualized,
                    "recomputed_basis_rate": (
                        futures_price - index_price
                    )
                    / index_price,
                }
            )
            observations.append(observation)
    observations.sort(key=lambda row: int(row["event_time_ms"]))
    return observations


def parse_constituents(
    symbol: str,
    payload: object,
    *,
    knowledge_time_ms: int,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise BinanceSystemicRiskError("constituents response is not an object")
    if str(payload.get("symbol", "")).upper() != symbol:
        raise BinanceSystemicRiskError("constituents symbol mismatch")
    event_time_ms = _timestamp(payload.get("time"), "constituents time")
    _fresh(
        event_time_ms,
        knowledge_time_ms,
        MAX_CONSTITUENT_AGE_MS,
        "constituents",
    )
    raw_constituents = payload.get("constituents")
    if not isinstance(raw_constituents, list) or not raw_constituents:
        raise BinanceSystemicRiskError("constituents list is empty")
    constituents: list[dict[str, Any]] = []
    for raw in raw_constituents:
        if not isinstance(raw, Mapping):
            raise BinanceSystemicRiskError("constituent is not an object")
        exchange = str(raw.get("exchange", "")).strip().lower()
        venue_symbol = str(raw.get("symbol", "")).strip()
        if not exchange or not venue_symbol:
            raise BinanceSystemicRiskError("constituent identity is missing")
        constituents.append(
            {
                "exchange": exchange,
                "symbol": venue_symbol,
                "price": _positive(raw.get("price"), "constituent price"),
                "weight": _nonnegative(raw.get("weight"), "constituent weight"),
            }
        )
    total_weight = sum(row["weight"] for row in constituents)
    if abs(total_weight - 1.0) > 0.02:
        raise BinanceSystemicRiskError("constituent weights do not sum to one")
    weighted_price = sum(
        row["price"] * row["weight"] for row in constituents
    ) / total_weight
    prices = [row["price"] for row in constituents]
    observation = _base_observation(
        "index_constituents", symbol, event_time_ms, knowledge_time_ms
    )
    observation.update(
        {
            "constituents": constituents,
            "constituent_count": len(constituents),
            "weight_sum": total_weight,
            "weighted_price": weighted_price,
            "price_dispersion_bps": (
                max(prices) - min(prices)
            )
            / weighted_price
            * 10_000.0,
        }
    )
    return observation


def parse_insurance_balance(
    symbol: str,
    payload: object,
    *,
    knowledge_time_ms: int,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise BinanceSystemicRiskError("insurance response is not an object")
    symbols = payload.get("symbols")
    assets = payload.get("assets")
    normalized_symbols = (
        [str(item).upper() for item in symbols]
        if isinstance(symbols, list)
        else []
    )
    if symbol not in normalized_symbols:
        raise BinanceSystemicRiskError("insurance response does not cover symbol")
    if not isinstance(assets, list) or not assets:
        raise BinanceSystemicRiskError("insurance assets are empty")
    normalized_assets: list[dict[str, Any]] = []
    event_times: list[int] = []
    for raw in assets:
        if not isinstance(raw, Mapping):
            raise BinanceSystemicRiskError("insurance asset is not an object")
        asset = str(raw.get("asset", "")).upper()
        if not asset:
            raise BinanceSystemicRiskError("insurance asset identity is missing")
        update_time_ms = _timestamp(
            raw.get("updateTime"), "insurance updateTime"
        )
        event_times.append(update_time_ms)
        normalized_assets.append(
            {
                "asset": asset,
                "margin_balance": _nonnegative(
                    raw.get("marginBalance"), "insurance marginBalance"
                ),
                "update_time_ms": update_time_ms,
            }
        )
    event_time_ms = max(event_times)
    _fresh(
        event_time_ms,
        knowledge_time_ms,
        MAX_INSURANCE_AGE_MS,
        "insurance balance",
    )
    # ``insuranceBalance`` is a mutable point-in-time snapshot: Binance can
    # change balances while retaining the assets' coarse updateTime. Use the
    # receipt time as the observation event identity and preserve the exchange
    # timestamp separately, so every observed revision remains append-only.
    observation = _base_observation(
        "insurance_balance", symbol, knowledge_time_ms, knowledge_time_ms
    )
    observation.update(
        {
            "source_update_time_ms": event_time_ms,
            "covered_symbols": normalized_symbols,
            "assets": normalized_assets,
            "asset_count": len(normalized_assets),
        }
    )
    return observation


def _shared_adl_by_symbol(
    payload: object,
    symbols: Sequence[str],
) -> dict[str, Mapping[str, Any]]:
    rows = [payload] if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        raise BinanceSystemicRiskError("shared ADL snapshot is not a list")
    indexed: dict[str, Mapping[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise BinanceSystemicRiskError("shared ADL row is not an object")
        symbol = str(raw.get("symbol", "")).upper()
        if not symbol:
            raise BinanceSystemicRiskError("shared ADL row has no symbol")
        if symbol in indexed:
            raise BinanceSystemicRiskError(
                f"shared ADL snapshot duplicates {symbol}"
            )
        indexed[symbol] = raw
    missing = [symbol for symbol in symbols if symbol not in indexed]
    if missing:
        raise BinanceSystemicRiskError(
            "shared ADL snapshot is missing " + ", ".join(missing)
        )
    return {symbol: indexed[symbol] for symbol in symbols}


def _shared_insurance_by_symbol(
    payload: object,
    symbols: Sequence[str],
) -> dict[str, Mapping[str, Any]]:
    rows = [payload] if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        raise BinanceSystemicRiskError(
            "shared insurance snapshot is not a list"
        )
    indexed: dict[str, Mapping[str, Any]] = {}
    requested = set(symbols)
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise BinanceSystemicRiskError(
                "shared insurance row is not an object"
            )
        covered = raw.get("symbols")
        if not isinstance(covered, list):
            raise BinanceSystemicRiskError(
                "shared insurance row has no symbol list"
            )
        for item in covered:
            symbol = str(item).upper()
            if symbol not in requested:
                continue
            if symbol in indexed:
                raise BinanceSystemicRiskError(
                    f"shared insurance snapshot duplicates {symbol}"
                )
            indexed[symbol] = raw
    missing = [symbol for symbol in symbols if symbol not in indexed]
    if missing:
        raise BinanceSystemicRiskError(
            "shared insurance snapshot is missing " + ", ".join(missing)
        )
    return {symbol: indexed[symbol] for symbol in symbols}


def _fetch_shared_snapshots(
    symbols: Sequence[str],
    *,
    http_get: Callable[[str], object],
    knowledge_clock_ms: Callable[[], int],
) -> dict[str, Any]:
    adl = _get("/fapi/v1/symbolAdlRisk", None, http_get)
    adl_knowledge_time_ms = int(knowledge_clock_ms())
    insurance = _get("/fapi/v1/insuranceBalance", None, http_get)
    insurance_knowledge_time_ms = int(knowledge_clock_ms())
    return {
        "adl": adl,
        "insurance": insurance,
        "adl_knowledge_time_ms": adl_knowledge_time_ms,
        "insurance_knowledge_time_ms": insurance_knowledge_time_ms,
        "adl_by_symbol": _shared_adl_by_symbol(adl[1], symbols),
        "insurance_by_symbol": _shared_insurance_by_symbol(
            insurance[1], symbols
        ),
    }


def _fetch_basis_pages(
    symbol: str,
    *,
    start_time_ms: int,
    end_time_ms: int,
    http_get: Callable[[str], object],
) -> list[tuple[str, object]]:
    pages: list[tuple[str, object]] = []
    cursor = int(start_time_ms)
    for _ in range(3):
        url, payload = _get(
            "/futures/data/basis",
            {
                "pair": symbol,
                "contractType": "PERPETUAL",
                "period": BASIS_PERIOD,
                "limit": 500,
                "startTime": cursor,
                "endTime": end_time_ms,
            },
            http_get,
        )
        pages.append((url, payload))
        if not isinstance(payload, list) or not payload:
            break
        timestamps = [
            _timestamp(row.get("timestamp"), "basis timestamp")
            for row in payload
            if isinstance(row, Mapping)
        ]
        if not timestamps:
            break
        next_cursor = max(timestamps) + HOUR_MS
        if len(payload) < 500 or next_cursor <= cursor or next_cursor > end_time_ms:
            break
        cursor = next_cursor
    return pages


def _fetch_symbol(
    ticker: str,
    *,
    basis_start_ms: int,
    observed_at_ms: int,
    fetch_constituents: bool,
    fetch_basis: bool,
    http_get: Callable[[str], object],
    knowledge_clock_ms: Callable[[], int],
) -> dict[str, Any]:
    symbol = f"{ticker}USDT"
    constituents = (
        _get("/fapi/v1/constituents", {"symbol": symbol}, http_get)
        if fetch_constituents
        else None
    )
    constituents_knowledge_time_ms = (
        int(knowledge_clock_ms()) if fetch_constituents else None
    )
    basis = (
        _fetch_basis_pages(
            symbol,
            start_time_ms=basis_start_ms,
            end_time_ms=observed_at_ms,
            http_get=http_get,
        )
        if fetch_basis
        else []
    )
    basis_knowledge_time_ms = int(knowledge_clock_ms())
    return {
        "constituents": constituents,
        "constituents_knowledge_time_ms": constituents_knowledge_time_ms,
        "basis": basis,
        "basis_knowledge_time_ms": basis_knowledge_time_ms,
    }


def _archive_shared(
    *,
    fetched: Mapping[str, Any],
    archive_root: str,
) -> None:
    for dataset, (source_uri, payload), knowledge_time_ms in (
        ("adl_risk", fetched["adl"], int(fetched["adl_knowledge_time_ms"])),
        (
            "insurance_balance",
            fetched["insurance"],
            int(fetched["insurance_knowledge_time_ms"]),
        ),
    ):
        input_rows = len(payload) if isinstance(payload, list) else 1
        archive_json_response(
            root=archive_root,
            payload=payload,
            source_uri=source_uri,
            provider="Binance public USD-M market data",
            dataset_id=f"binance_systemic_{dataset}",
            instrument_id="ALL",
            observed_at_ms=knowledge_time_ms,
            parse_diagnostics={
                "status": "ACCEPTED",
                "input_rows": input_rows,
                "accepted_rows": input_rows,
                "rejected_rows": 0,
                "duplicate_rows": 0,
            },
        )


def _archive_symbol(
    *,
    ticker: str,
    fetched: Mapping[str, Any],
    archive_root: str,
) -> None:
    rows: list[tuple[str, str, object, int]] = []
    if fetched.get("constituents") is not None:
        rows.append(
            (
                "index_constituents",
                fetched["constituents"][0],
                fetched["constituents"][1],
                int(fetched["constituents_knowledge_time_ms"]),
            )
        )
    rows.extend(
        (
            "basis_1h",
            source_uri,
            payload,
            int(fetched["basis_knowledge_time_ms"]),
        )
        for source_uri, payload in fetched["basis"]
    )
    for dataset, source_uri, payload, knowledge_time_ms in rows:
        input_rows = len(payload) if isinstance(payload, list) else 1
        archive_json_response(
            root=archive_root,
            payload=payload,
            source_uri=source_uri,
            provider="Binance public USD-M market data",
            dataset_id=f"binance_systemic_{dataset}",
            instrument_id=f"{ticker}USDT",
            observed_at_ms=knowledge_time_ms,
            parse_diagnostics={
                "status": "ACCEPTED",
                "input_rows": input_rows,
                "accepted_rows": input_rows,
                "rejected_rows": 0,
                "duplicate_rows": 0,
            },
        )


def _append_observation(
    store: BinanceSystemicStore,
    observation: Mapping[str, Any],
) -> bool:
    dataset = str(observation["dataset"])
    instrument_id = str(observation["instrument_id"])
    event_time_ms = int(observation["event_time_ms"])
    existing = store.latest(dataset, instrument_id)
    if existing is not None and int(existing["event_time_ms"]) == event_time_ms:
        current = dict(observation)
        current["knowledge_time_ms"] = existing["knowledge_time_ms"]
        # The first operational smoke preceded preservation of Binance's raw
        # ``MIDDLE`` spelling.  LOW/HIGH rows are semantically identical when
        # raw_rating equals the normalized rating, so retain the append-only
        # row instead of rewriting history or manufacturing a conflicting ID.
        if (
            "raw_rating" in current
            and "raw_rating" not in existing
            and current.get("raw_rating") == current.get("rating")
        ):
            current.pop("raw_rating")
        if current == existing:
            return False
    return store.append(observation)


def _basis_refresh_tickers(
    starts: Mapping[str, int],
    *,
    observed_at_ms: int,
    budget: int = BASIS_REFRESH_BUDGET,
) -> tuple[str, ...]:
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        raise ValueError("basis refresh budget must be a positive integer")
    # ``start`` identifies the next hourly period start. Binance may expose
    # that open period immediately, but it is not immutable until one full hour
    # has elapsed. Request only periods that can actually pass parse_basis_rows.
    due = [
        (int(start), ticker)
        for ticker, start in starts.items()
        if int(start) + HOUR_MS <= int(observed_at_ms)
    ]
    due.sort(key=lambda item: (item[0], item[1]))
    return tuple(ticker for _, ticker in due[:budget])


def _constituent_refresh_tickers(
    last_event_times: Mapping[str, int | None],
    *,
    budget: int = CONSTITUENT_REFRESH_BUDGET,
) -> tuple[str, ...]:
    """Rotate symbol-scoped snapshots, prioritising missing and oldest data.

    Binance's constituent endpoint is the only weighted per-symbol surface in
    this worker.  Refreshing all 14 symbols every cycle is unnecessary and can
    collide with other public workers sharing the same IP.  A seven-symbol
    budget on the 30-minute schedule refreshes the full universe once per hour
    while verified cache rows keep the unselected half available.
    """
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        raise ValueError("constituent refresh budget must be a positive integer")
    ranked: list[tuple[int, int, str]] = []
    for ticker, event_time in last_event_times.items():
        if event_time is None:
            ranked.append((0, -1, str(ticker)))
            continue
        if isinstance(event_time, bool) or not isinstance(event_time, int):
            raise TypeError("constituent event times must be integers or None")
        ranked.append((1, event_time, str(ticker)))
    ranked.sort()
    return tuple(ticker for _, _, ticker in ranked[:budget])


def _latest_systemic_snapshot(
    store: BinanceSystemicStore,
    *,
    symbol: str,
    observed_at_ms: int,
) -> dict[str, Any]:
    observations = {
        # A request can finish after the cycle's ``observed_at_ms``.  The row's
        # knowledge_time_ms preserves that receipt boundary for PIT consumers;
        # this operational snapshot must still include the response obtained
        # by the current cycle.
        dataset: store.latest(dataset, symbol)
        for dataset in (
            "adl_risk",
            "basis_1h",
            "index_constituents",
            "insurance_balance",
        )
    }
    missing = [name for name, value in observations.items() if value is None]
    if missing:
        raise BinanceSystemicRiskError(
            f"systemic cache is incomplete for {symbol}: {sorted(missing)}"
        )
    adl = observations["adl_risk"]
    basis = observations["basis_1h"]
    constituents = observations["index_constituents"]
    insurance = observations["insurance_balance"]
    assert adl is not None and basis is not None
    assert constituents is not None and insurance is not None
    _fresh(
        int(adl["event_time_ms"]),
        max(observed_at_ms, int(adl["knowledge_time_ms"])),
        MAX_ADL_AGE_MS,
        "ADL risk",
    )
    _fresh(
        int(basis["event_time_ms"]) + HOUR_MS,
        max(observed_at_ms, int(basis["knowledge_time_ms"])),
        MAX_BASIS_AGE_MS,
        "basis",
    )
    _fresh(
        int(constituents["event_time_ms"]),
        max(observed_at_ms, int(constituents["knowledge_time_ms"])),
        MAX_CONSTITUENT_AGE_MS,
        "constituents",
    )
    _fresh(
        int(insurance["event_time_ms"]),
        max(observed_at_ms, int(insurance["knowledge_time_ms"])),
        MAX_INSURANCE_AGE_MS,
        "insurance balance",
    )
    return {
        "adl_risk": adl["rating"],
        "adl_event_time_ms": adl["event_time_ms"],
        "basis_event_time_ms": basis["event_time_ms"],
        "basis_rate": basis["basis_rate"],
        "constituent_count": constituents["constituent_count"],
        "constituent_dispersion_bps": constituents["price_dispersion_bps"],
        "insurance_asset_count": insurance["asset_count"],
        "insurance_event_time_ms": insurance["event_time_ms"],
    }


def run_refresh(
    *,
    tickers: Sequence[str] = FORWARD_MAJOR_TICKERS,
    store_path: str = DEFAULT_STORE,
    status_path: str = DEFAULT_STATUS,
    archive_root: str | None = DEFAULT_RAW_ARCHIVE,
    http_get: Callable[[str], object] = _http_get_json,
    now_ms: int | None = None,
    max_workers: int = 1,
    request_interval_seconds: float | None = None,
    basis_refresh_budget: int = BASIS_REFRESH_BUDGET,
    constituent_refresh_budget: int = CONSTITUENT_REFRESH_BUDGET,
    knowledge_clock_ms: Callable[[], int] | None = None,
) -> dict[str, Any]:
    selected = tuple(dict.fromkeys(str(item).upper() for item in tickers))
    if not selected or any(not ticker.isalnum() for ticker in selected):
        raise BinanceSystemicRiskError("systemic-risk universe is invalid")
    observed_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if knowledge_clock_ms is None:
        knowledge_clock_ms = (
            (lambda: int(time.time() * 1000))
            if now_ms is None
            else (lambda: observed_at_ms)
        )
    interval = (
        DEFAULT_REQUEST_INTERVAL_SECONDS
        if request_interval_seconds is None and http_get is _http_get_json
        else 0.0
        if request_interval_seconds is None
        else float(request_interval_seconds)
    )
    prior_ban_until_ms, prior_status_code, prior_endpoint = (
        _persisted_cooldown(status_path, now_ms=observed_at_ms)
    )
    limited_http_get = _RateLimitedHttpGet(
        http_get,
        min_interval_seconds=interval,
        initial_ban_until_ms=prior_ban_until_ms,
        initial_status_code=prior_status_code,
        initial_endpoint=prior_endpoint,
    )
    Path(store_path).parent.mkdir(parents=True, exist_ok=True)
    store = BinanceSystemicStore(store_path, create=True)
    try:
        starts: dict[str, int] = {}
        for ticker in selected:
            symbol = f"{ticker}USDT"
            latest = store.latest_event_time("basis_1h", symbol)
            if latest is None:
                starts[ticker] = (
                    (observed_at_ms - BASIS_LOOKBACK_MS) // HOUR_MS
                ) * HOUR_MS
            else:
                starts[ticker] = latest + HOUR_MS

        basis_refresh_tickers = _basis_refresh_tickers(
            starts,
            observed_at_ms=observed_at_ms,
            budget=basis_refresh_budget,
        )
        basis_refresh_set = set(basis_refresh_tickers)
        constituent_refresh_tickers = _constituent_refresh_tickers(
            {
                ticker: store.latest_event_time(
                    "index_constituents", f"{ticker}USDT"
                )
                for ticker in selected
            },
            budget=constituent_refresh_budget,
        )
        constituent_refresh_set = set(constituent_refresh_tickers)
        fetched: dict[str, dict[str, Any]] = {}
        refresh_errors: dict[str, dict[str, str]] = {}
        errors: dict[str, dict[str, str]] = {}
        symbols = tuple(f"{ticker}USDT" for ticker in selected)
        shared: dict[str, Any] | None = None
        try:
            shared = _fetch_shared_snapshots(
                symbols,
                http_get=limited_http_get,
                knowledge_clock_ms=knowledge_clock_ms,
            )
            if archive_root is not None:
                _archive_shared(
                    fetched=shared,
                    archive_root=archive_root,
                )
        except Exception as exc:  # noqa: BLE001 - shared snapshot is load-bearing
            for ticker in selected:
                refresh_errors[ticker] = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                }

        if shared is not None:
            symbol_fetch_tickers = tuple(
                dict.fromkeys(
                    (*constituent_refresh_tickers, *basis_refresh_tickers)
                )
            )
            with ThreadPoolExecutor(
                max_workers=max(
                    1, min(int(max_workers), max(1, len(symbol_fetch_tickers)))
                )
            ) as pool:
                futures = {
                    pool.submit(
                        _fetch_symbol,
                        ticker,
                        basis_start_ms=starts[ticker],
                        observed_at_ms=observed_at_ms,
                        fetch_constituents=ticker in constituent_refresh_set,
                        fetch_basis=ticker in basis_refresh_set,
                        http_get=limited_http_get,
                        knowledge_clock_ms=knowledge_clock_ms,
                    ): ticker
                    for ticker in symbol_fetch_tickers
                }
                for future in as_completed(futures):
                    ticker = futures[future]
                    try:
                        fetched[ticker] = future.result()
                    except Exception as exc:  # noqa: BLE001 - isolate one symbol
                        refresh_errors[ticker] = {
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:240],
                        }

        inserted_by_dataset = {
            "adl_risk": 0,
            "basis_1h": 0,
            "index_constituents": 0,
            "insurance_balance": 0,
        }
        latest: dict[str, dict[str, Any]] = {}
        for ticker in selected:
            symbol = f"{ticker}USDT"
            source = fetched.get(ticker, {})
            try:
                observations: list[dict[str, Any]] = []
                if shared is not None:
                    observations.extend(
                        (
                            parse_adl_risk(
                                symbol,
                                shared["adl_by_symbol"][symbol],
                                knowledge_time_ms=int(
                                    shared["adl_knowledge_time_ms"]
                                ),
                            ),
                            parse_insurance_balance(
                                symbol,
                                shared["insurance_by_symbol"][symbol],
                                knowledge_time_ms=int(
                                    shared["insurance_knowledge_time_ms"]
                                ),
                            ),
                        )
                    )
                if source.get("constituents") is not None:
                    observations.append(
                        parse_constituents(
                            symbol,
                            source["constituents"][1],
                            knowledge_time_ms=int(
                                source["constituents_knowledge_time_ms"]
                            ),
                        )
                    )
                basis = parse_basis_rows(
                    symbol,
                    [payload for _, payload in source.get("basis", [])],
                    knowledge_time_ms=int(
                        source.get("basis_knowledge_time_ms", observed_at_ms)
                    ),
                    min_event_time_ms=starts[ticker],
                )
                observations.extend(basis)
                if archive_root is not None and source:
                    _archive_symbol(
                        ticker=ticker,
                        fetched=source,
                        archive_root=archive_root,
                    )
                for observation in observations:
                    inserted_by_dataset[str(observation["dataset"])] += int(
                        _append_observation(store, observation)
                    )
                latest[ticker] = _latest_systemic_snapshot(
                    store,
                    symbol=symbol,
                    observed_at_ms=observed_at_ms,
                )
            except Exception as exc:  # noqa: BLE001 - fail closed per symbol
                errors[ticker] = refresh_errors.get(
                    ticker,
                    {
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:240],
                    },
                )

        chain_valid = store.chain_valid()
        row_counts = {
            dataset: store.row_count(dataset)
            for dataset in inserted_by_dataset
        }
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
        "refresh_errors": refresh_errors,
        "store": {
            "path": store_path,
            "row_count": row_count,
            "row_counts": row_counts,
            "inserted": inserted_by_dataset,
            "chain_valid": chain_valid,
        },
        "request_rate_limit": {
            "planned_ip_weight": (
                SHARED_ENDPOINT_WEIGHT
                + len(constituent_refresh_tickers) * ENDPOINT_WEIGHT_PER_SYMBOL
            ),
            "planned_request_count": (
                2 + len(constituent_refresh_tickers) + len(basis_refresh_tickers)
            ),
            "shared_endpoint_weight": SHARED_ENDPOINT_WEIGHT,
            "endpoint_weight_per_symbol": ENDPOINT_WEIGHT_PER_SYMBOL,
            "basis_refresh_budget": basis_refresh_budget,
            "basis_refresh_tickers": list(basis_refresh_tickers),
            "constituent_refresh_budget": constituent_refresh_budget,
            "constituent_refresh_tickers": list(constituent_refresh_tickers),
            "constituent_cache_reused": (
                len(selected) - len(constituent_refresh_tickers)
            ),
            "configured_max_workers": max(
                1, min(int(max_workers), len(selected))
            ),
            **limited_http_get.telemetry(),
        },
        "fields": [
            "symbol_adl_risk_30m",
            "perpetual_basis_1h_30d",
            "index_constituent_prices_weights_dispersion",
            "insurance_fund_assets_margin_balances",
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
        print(f"binance systemic risk: BLOCK ({type(exc).__name__})")
        return SCHEDULE_EXIT_FAILURE
    coverage = status["coverage"]
    print(
        "binance systemic risk:",
        status["verdict"],
        f"{coverage['complete']}/{coverage['requested']} complete",
        f"inserted={sum(status['store']['inserted'].values())}",
        f"chain={status['store']['chain_valid']}",
    )
    return (
        SCHEDULE_EXIT_SUCCESS
        if status["complete"]
        else SCHEDULE_EXIT_SAFE_BLOCKED
    )


if __name__ == "__main__":
    raise SystemExit(main())
