"""Collect public Binance USD-M systemic-risk and reference-market data.

This low-frequency L2 worker covers symbol ADL risk, the rolling 30-day
perpetual basis history, index constituents, and insurance-fund snapshots.
It has no credential, account, broker, order, or live-trading capability.
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
DEFAULT_STORE = os.path.join("data", "cache", "binance_systemic_risk.db")
DEFAULT_STATUS = os.path.join(
    "data", "cache", "binance_systemic_risk_status.json"
)
BASIS_PERIOD = "1h"
HOUR_MS = 60 * 60_000
DAY_MS = 24 * HOUR_MS
BASIS_LOOKBACK_MS = 30 * DAY_MS
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


class BinanceSystemicRiskError(RuntimeError):
    """A public systemic-risk response is malformed, stale, or inconsistent."""


def _http_get_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "quantos-l2-public-systemic-risk",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
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
    observation = _base_observation(
        "insurance_balance", symbol, event_time_ms, knowledge_time_ms
    )
    observation.update(
        {
            "covered_symbols": normalized_symbols,
            "assets": normalized_assets,
            "asset_count": len(normalized_assets),
        }
    )
    return observation


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
    http_get: Callable[[str], object],
) -> dict[str, Any]:
    symbol = f"{ticker}USDT"
    adl = _get("/fapi/v1/symbolAdlRisk", {"symbol": symbol}, http_get)
    constituents = _get(
        "/fapi/v1/constituents", {"symbol": symbol}, http_get
    )
    insurance = _get(
        "/fapi/v1/insuranceBalance", {"symbol": symbol}, http_get
    )
    basis = _fetch_basis_pages(
        symbol,
        start_time_ms=basis_start_ms,
        end_time_ms=observed_at_ms,
        http_get=http_get,
    )
    return {
        "adl": adl,
        "constituents": constituents,
        "insurance": insurance,
        "basis": basis,
    }


def _archive(
    *,
    ticker: str,
    observed_at_ms: int,
    fetched: Mapping[str, Any],
    archive_root: str,
) -> None:
    rows: list[tuple[str, str, object]] = [
        ("adl_risk", fetched["adl"][0], fetched["adl"][1]),
        (
            "index_constituents",
            fetched["constituents"][0],
            fetched["constituents"][1],
        ),
        (
            "insurance_balance",
            fetched["insurance"][0],
            fetched["insurance"][1],
        ),
    ]
    rows.extend(
        ("basis_1h", source_uri, payload)
        for source_uri, payload in fetched["basis"]
    )
    for dataset, source_uri, payload in rows:
        input_rows = len(payload) if isinstance(payload, list) else 1
        archive_json_response(
            root=archive_root,
            payload=payload,
            source_uri=source_uri,
            provider="Binance public USD-M market data",
            dataset_id=f"binance_systemic_{dataset}",
            instrument_id=f"{ticker}USDT",
            observed_at_ms=observed_at_ms,
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
    if not selected or any(not ticker.isalnum() for ticker in selected):
        raise BinanceSystemicRiskError("systemic-risk universe is invalid")
    observed_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
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

        fetched: dict[str, dict[str, Any]] = {}
        errors: dict[str, dict[str, str]] = {}
        with ThreadPoolExecutor(
            max_workers=max(1, min(int(max_workers), len(selected)))
        ) as pool:
            futures = {
                pool.submit(
                    _fetch_symbol,
                    ticker,
                    basis_start_ms=starts[ticker],
                    observed_at_ms=observed_at_ms,
                    http_get=http_get,
                ): ticker
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

        inserted_by_dataset = {
            "adl_risk": 0,
            "basis_1h": 0,
            "index_constituents": 0,
            "insurance_balance": 0,
        }
        latest: dict[str, dict[str, Any]] = {}
        for ticker in selected:
            if ticker in errors:
                continue
            symbol = f"{ticker}USDT"
            source = fetched[ticker]
            try:
                adl = parse_adl_risk(
                    symbol,
                    source["adl"][1],
                    knowledge_time_ms=observed_at_ms,
                )
                constituents = parse_constituents(
                    symbol,
                    source["constituents"][1],
                    knowledge_time_ms=observed_at_ms,
                )
                insurance = parse_insurance_balance(
                    symbol,
                    source["insurance"][1],
                    knowledge_time_ms=observed_at_ms,
                )
                basis = parse_basis_rows(
                    symbol,
                    [payload for _, payload in source["basis"]],
                    knowledge_time_ms=observed_at_ms,
                )
                if archive_root is not None:
                    _archive(
                        ticker=ticker,
                        observed_at_ms=observed_at_ms,
                        fetched=source,
                        archive_root=archive_root,
                    )
                for observation in [adl, *basis, constituents, insurance]:
                    inserted_by_dataset[str(observation["dataset"])] += int(
                        _append_observation(store, observation)
                    )
                latest_basis = store.latest("basis_1h", symbol)
                if latest_basis is None:
                    raise BinanceSystemicRiskError(
                        "basis history has no completed row"
                    )
                _fresh(
                    int(latest_basis["event_time_ms"]) + HOUR_MS,
                    observed_at_ms,
                    MAX_BASIS_AGE_MS,
                    "basis",
                )
                latest[ticker] = {
                    "adl_risk": adl["rating"],
                    "adl_event_time_ms": adl["event_time_ms"],
                    "basis_event_time_ms": latest_basis["event_time_ms"],
                    "basis_rate": latest_basis["basis_rate"],
                    "constituent_count": constituents["constituent_count"],
                    "constituent_dispersion_bps": constituents[
                        "price_dispersion_bps"
                    ],
                    "insurance_asset_count": insurance["asset_count"],
                    "insurance_event_time_ms": insurance["event_time_ms"],
                }
            except Exception as exc:  # noqa: BLE001 - fail closed per symbol
                errors[ticker] = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                }

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
        "store": {
            "path": store_path,
            "row_count": row_count,
            "row_counts": row_counts,
            "inserted": inserted_by_dataset,
            "chain_valid": chain_valid,
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
    return SCHEDULE_EXIT_SUCCESS if status["complete"] else SCHEDULE_EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
