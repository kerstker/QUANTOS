"""Fetch causal Binance USD-M funding-settlement research history.

Only public market-data endpoints are used.  The exact funding event is kept
instead of being collapsed into a daily sum.  Each event is paired with the
close of the first completed mark-price hour *after* settlement, so a later
research driver cannot trade on the final funding rate before it is known.

This tool has no credential, account, broker, or order endpoint.
"""
from __future__ import annotations

import argparse
import math
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode

from quantos.contracts.hashing import stable_hash
from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from services.research.funding_settlement import HOUR_MS, SETTLEMENT_INTERVAL_MS
from tools.cache_io import atomic_write_json
from tools.coingecko_fetch import _http_get_json


FAPI = "https://fapi.binance.com"
DAY_MS = 24 * HOUR_MS
MAX_EVENT_JITTER_MS = 30_000
DEFAULT_LOOKBACK_DAYS = 1_095
DEFAULT_OUTPUT = Path("data/cache/binance_funding_settlement_history.json")
DEFAULT_UNIVERSE = (
    "ADAUSDT",
    "ATOMUSDT",
    "AVAXUSDT",
    "BCHUSDT",
    "BNBUSDT",
    "BTCUSDT",
    "DOGEUSDT",
    "DOTUSDT",
    "ETHUSDT",
    "LINKUSDT",
    "LTCUSDT",
    "SOLUSDT",
    "UNIUSDT",
    "XRPUSDT",
)


class FundingSettlementFetchError(ValueError):
    """A public response cannot support causal settlement evidence."""


def _default_http_get(url: str) -> object:
    last_error: BaseException | None = None
    for attempt in range(4):
        try:
            return _http_get_json(url, {"accept": "application/json"})
        except (TimeoutError, URLError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(0.5 * (2**attempt))
    raise FundingSettlementFetchError(
        "Binance public data request failed after bounded retries"
    ) from last_error


def _finite(value: object, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise FundingSettlementFetchError(f"{field} is invalid")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise FundingSettlementFetchError(f"{field} is invalid") from exc
    if not math.isfinite(result) or (positive and result <= 0.0):
        raise FundingSettlementFetchError(f"{field} is invalid")
    return result


def _archive_page(
    *,
    archive_root: str | Path | None,
    payload: object,
    source_uri: str,
    dataset_id: str,
    instrument_id: str,
    observed_at_ms: int | None,
) -> None:
    if archive_root is None:
        return
    if observed_at_ms is None:
        raise FundingSettlementFetchError(
            "observed_at_ms is required when raw archive is enabled"
        )
    archive_json_response(
        root=archive_root,
        payload=payload,
        source_uri=source_uri,
        provider="Binance public USD-M market data",
        dataset_id=dataset_id,
        instrument_id=instrument_id,
        observed_at_ms=int(observed_at_ms),
        parse_diagnostics={
            "status": "ACCEPTED",
            "input_rows": len(payload) if isinstance(payload, list) else 0,
            "accepted_rows": len(payload) if isinstance(payload, list) else 0,
            "rejected_rows": 0,
            "duplicate_rows": 0,
        },
    )


def fetch_funding_events(
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    http_get: Callable[[str], object] = _default_http_get,
    archive_root: str | Path | None = None,
    observed_at_ms: int | None = None,
) -> list[tuple[int, float]]:
    """Return exact ``(fundingTime, fundingRate)`` rows, earliest first."""
    cursor = int(start_ms)
    result: list[tuple[int, float]] = []
    for _page in range(40):
        query = urlencode(
            {
                "symbol": symbol,
                "startTime": cursor,
                "endTime": int(end_ms),
                "limit": 1000,
            }
        )
        url = f"{FAPI}/fapi/v1/fundingRate?{query}"
        payload = http_get(url)
        if not isinstance(payload, list):
            raise FundingSettlementFetchError(f"{symbol}: funding response is not a list")
        _archive_page(
            archive_root=archive_root,
            payload=payload,
            source_uri=url,
            dataset_id="binance_funding_settlement_events",
            instrument_id=symbol,
            observed_at_ms=observed_at_ms,
        )
        if not payload:
            break
        page: list[tuple[int, float]] = []
        for index, row in enumerate(payload):
            try:
                if not isinstance(row, Mapping):
                    raise TypeError
                timestamp = int(row["fundingTime"])
                rate = _finite(row["fundingRate"], "fundingRate")
            except (KeyError, TypeError, ValueError) as exc:
                raise FundingSettlementFetchError(
                    f"{symbol}: malformed funding row {index}"
                ) from exc
            if timestamp < start_ms or timestamp > end_ms or abs(rate) > 0.10:
                raise FundingSettlementFetchError(
                    f"{symbol}: malformed funding row {index}"
                )
            if page and timestamp <= page[-1][0]:
                raise FundingSettlementFetchError(
                    f"{symbol}: funding rows must be strictly increasing"
                )
            if result and timestamp <= result[-1][0]:
                raise FundingSettlementFetchError(
                    f"{symbol}: funding pages must be strictly increasing"
                )
            page.append((timestamp, rate))
        result.extend(page)
        if len(payload) < 1000:
            break
        cursor = result[-1][0] + 1
    else:
        raise FundingSettlementFetchError(f"{symbol}: funding pagination limit exceeded")
    return result


def fetch_mark_hours(
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    http_get: Callable[[str], object] = _default_http_get,
    archive_root: str | Path | None = None,
    observed_at_ms: int | None = None,
) -> dict[int, tuple[int, float]]:
    """Return ``open_time -> (close_time, close_mark)`` for completed 1h bars."""
    cursor = int(start_ms)
    result: dict[int, tuple[int, float]] = {}
    previous_open = -1
    for _page in range(40):
        query = urlencode(
            {
                "symbol": symbol,
                "interval": "1h",
                "startTime": cursor,
                "endTime": int(end_ms),
                "limit": 1500,
            }
        )
        url = f"{FAPI}/fapi/v1/markPriceKlines?{query}"
        payload = http_get(url)
        if not isinstance(payload, list):
            raise FundingSettlementFetchError(f"{symbol}: mark response is not a list")
        _archive_page(
            archive_root=archive_root,
            payload=payload,
            source_uri=url,
            dataset_id="binance_funding_settlement_mark_1h",
            instrument_id=symbol,
            observed_at_ms=observed_at_ms,
        )
        if not payload:
            break
        page_count = 0
        for index, row in enumerate(payload):
            if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) < 7:
                raise FundingSettlementFetchError(f"{symbol}: malformed mark row {index}")
            try:
                open_time = int(row[0])
                close_time = int(row[6])
                close_mark = _finite(row[4], "mark.close", positive=True)
            except (TypeError, ValueError) as exc:
                raise FundingSettlementFetchError(
                    f"{symbol}: malformed mark row {index}"
                ) from exc
            if open_time <= previous_open:
                raise FundingSettlementFetchError(
                    f"{symbol}: mark rows must be strictly increasing"
                )
            if close_time != open_time + HOUR_MS - 1:
                raise FundingSettlementFetchError(f"{symbol}: malformed mark hour")
            previous_open = open_time
            if start_ms <= open_time <= end_ms:
                result[open_time] = (close_time, close_mark)
            page_count += 1
        if len(payload) < 1500:
            break
        cursor = previous_open + HOUR_MS
        if page_count == 0:
            raise FundingSettlementFetchError(f"{symbol}: mark pagination made no progress")
    else:
        raise FundingSettlementFetchError(f"{symbol}: mark pagination limit exceeded")
    return result


def build_settlement_series(
    symbol: str,
    funding_events: Sequence[tuple[int, float]],
    mark_hours: Mapping[int, tuple[int, float]],
) -> list[dict[str, int | float | str]]:
    """Bind exact events to the first completed post-settlement mark hour."""
    result: list[dict[str, int | float | str]] = []
    previous_scheduled = -1
    for source_time, raw_rate in funding_events:
        scheduled = int(round(int(source_time) / HOUR_MS)) * HOUR_MS
        if abs(int(source_time) - scheduled) > MAX_EVENT_JITTER_MS:
            raise FundingSettlementFetchError(
                f"{symbol}: funding event is not near an hour boundary"
            )
        if scheduled <= previous_scheduled:
            raise FundingSettlementFetchError(
                f"{symbol}: canonical funding times must be strictly increasing"
            )
        mark = mark_hours.get(scheduled)
        if mark is None:
            raise FundingSettlementFetchError(
                f"{symbol}: missing completed post-settlement mark hour"
            )
        close_time, close_mark = mark
        if int(close_time) != scheduled + HOUR_MS - 1:
            raise FundingSettlementFetchError(
                f"{symbol}: post-settlement mark hour is malformed"
            )
        rate = _finite(raw_rate, "settled_rate")
        result.append(
            {
                "instrument_id": symbol,
                "source_funding_time_ms": int(source_time),
                "settlement_time_ms": scheduled,
                "decision_time_ms": scheduled + HOUR_MS,
                "settled_rate": rate,
                "decision_mark_price": _finite(
                    close_mark, "decision_mark_price", positive=True
                ),
                "mark_kline_close_time_ms": int(close_time),
            }
        )
        previous_scheduled = scheduled
    return result


def fetch_history(
    *,
    universe: Sequence[str] = DEFAULT_UNIVERSE,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    observed_at_ms: int | None = None,
    archive_root: str | Path | None = DEFAULT_RAW_ARCHIVE,
    max_workers: int = 4,
    http_get: Callable[[str], object] = _default_http_get,
) -> dict[str, Any]:
    observed_at = int(observed_at_ms if observed_at_ms is not None else time.time_ns() // 1_000_000)
    if lookback_days < 365:
        raise FundingSettlementFetchError("lookback_days must be at least 365")
    # The decision mark is known one hour after settlement.  Exclude an event
    # until that complete bar is observable.
    complete_end = ((observed_at - HOUR_MS) // SETTLEMENT_INTERVAL_MS) * SETTLEMENT_INTERVAL_MS
    start = complete_end - int(lookback_days) * DAY_MS
    selected = tuple(sorted({str(symbol).upper() for symbol in universe}))
    if len(selected) < 10 or any(not symbol.endswith("USDT") for symbol in selected):
        raise FundingSettlementFetchError("universe must contain at least ten USDT contracts")

    def one(symbol: str) -> tuple[str, list[dict[str, int | float | str]]]:
        events = fetch_funding_events(
            symbol,
            start,
            complete_end + MAX_EVENT_JITTER_MS,
            http_get=http_get,
            archive_root=archive_root,
            observed_at_ms=observed_at,
        )
        marks = fetch_mark_hours(
            symbol,
            start,
            complete_end,
            http_get=http_get,
            archive_root=archive_root,
            observed_at_ms=observed_at,
        )
        rows = build_settlement_series(symbol, events, marks)
        if len(rows) < 365 * 3:
            raise FundingSettlementFetchError(
                f"{symbol}: fewer than 365 days of 8h-equivalent events"
            )
        return symbol, rows

    series: dict[str, list[dict[str, int | float | str]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), 4))) as pool:
        for symbol, rows in pool.map(one, selected):
            series[symbol] = rows
    dataset_hash = stable_hash(series)
    return {
        "schema_version": "binance_funding_settlement_history.v1",
        "provenance": "REAL_BINANCE_PUBLIC_FUNDING_SETTLEMENT_HISTORY",
        "observed_at_ms": observed_at,
        "requested_start_ms": start,
        "complete_through_settlement_ms": complete_end,
        "lookback_days": int(lookback_days),
        "universe": list(selected),
        "series": dict(sorted(series.items())),
        "dataset_hash": dataset_hash,
        "source_contract": {
            "funding": "/fapi/v1/fundingRate exact settled events",
            "mark": "/fapi/v1/markPriceKlines 1h completed post-settlement close",
            "signal_knowledge": "settled rate at t, decision mark from completed [t,t+1h) bar",
        },
        "safety": {
            "public_market_data_only": True,
            "credentials": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--archive-root", default=str(DEFAULT_RAW_ARCHIVE))
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()
    snapshot = fetch_history(
        lookback_days=args.lookback_days,
        archive_root=args.archive_root,
        max_workers=args.max_workers,
    )
    atomic_write_json(args.output, snapshot, indent=2)
    print(
        f"funding settlement history: {len(snapshot['universe'])} instruments, "
        f"dataset={snapshot['dataset_hash']}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_OUTPUT",
    "DEFAULT_UNIVERSE",
    "FundingSettlementFetchError",
    "build_settlement_series",
    "fetch_funding_events",
    "fetch_history",
    "fetch_mark_hours",
]
