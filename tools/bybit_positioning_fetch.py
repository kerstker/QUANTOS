"""Bybit linear-perpetual positioning history (public, read-only, no key).

The fetcher consumes only the official V5 public market endpoints:

* ``/v5/market/open-interest``
* ``/v5/market/account-ratio``

Network access stays at the ``tools/`` boundary.  The emitted artifact is a
point-in-time daily history with source event timestamps and a wall-clock
observation timestamp.  The unfinished UTC day is always excluded.  This
module has no account, credential, broker, order, transfer, or withdrawal
surface and must never be used to infer Testnet eligibility by itself.
"""
from __future__ import annotations

import argparse
import math
import os
import time
import urllib.error

from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.bybit_klines_fetch import DEFAULT_TICKERS, _get
from tools.cache_io import atomic_write_json
from tools.coingecko_fetch import _http_get_json


DAY_MS = 86_400_000
DEFAULT_OUTPUT = os.path.join("data", "cache", "bybit_positioning_metrics.json")
SOURCE_NAME = "Bybit public linear-perpetual positioning history"
OPEN_INTEREST_ENDPOINT = "/v5/market/open-interest"
ACCOUNT_RATIO_ENDPOINT = "/v5/market/account-ratio"


class BybitPositioningFetchError(RuntimeError):
    """The public response cannot satisfy the positioning source contract."""


def _page(
    endpoint: str,
    *,
    symbol: str,
    interval_parameter: str,
    limit: int = 200,
    cursor: str | None = None,
    http_get=_http_get_json,
    source_sink=None,
) -> tuple[list, str]:
    if not 1 <= int(limit) <= 200:
        raise ValueError("Bybit positioning page limit must be in [1, 200]")
    path = (
        f"{endpoint}?category=linear&symbol={symbol}"
        f"&{interval_parameter}=1d&limit={int(limit)}"
    )
    if cursor:
        path += f"&cursor={cursor}"
    result = _get(path, http_get=http_get, source_sink=source_sink)
    rows = result.get("list")
    if not isinstance(rows, list):
        raise BybitPositioningFetchError("Bybit positioning response has no list")
    next_cursor = result.get("nextPageCursor", "")
    if next_cursor is None:
        next_cursor = ""
    if not isinstance(next_cursor, str):
        raise BybitPositioningFetchError("Bybit positioning cursor is not a string")
    return rows, next_cursor


def fetch_open_interest_page(
    symbol: str = "BTCUSDT",
    *,
    limit: int = 200,
    cursor: str | None = None,
    http_get=_http_get_json,
    source_sink=None,
) -> tuple[list, str]:
    return _page(
        OPEN_INTEREST_ENDPOINT,
        symbol=symbol,
        interval_parameter="intervalTime",
        limit=limit,
        cursor=cursor,
        http_get=http_get,
        source_sink=source_sink,
    )


def fetch_account_ratio_page(
    symbol: str = "BTCUSDT",
    *,
    limit: int = 200,
    cursor: str | None = None,
    http_get=_http_get_json,
    source_sink=None,
) -> tuple[list, str]:
    return _page(
        ACCOUNT_RATIO_ENDPOINT,
        symbol=symbol,
        interval_parameter="period",
        limit=limit,
        cursor=cursor,
        http_get=http_get,
        source_sink=source_sink,
    )


def _diagnostics(target, *, input_rows, accepted, rejected, duplicates) -> None:
    if target is not None:
        target.update(
            {
                "status": "ACCEPTED",
                "input_rows": int(input_rows),
                "accepted_rows": int(accepted),
                "rejected_rows": int(rejected),
                "duplicate_rows": int(duplicates),
            }
        )


def parse_open_interest_rows(batch, *, now_ms: int, diagnostics=None) -> dict[int, float]:
    """Normalize raw OI rows to completed UTC-day buckets."""
    current_day = (int(now_ms) // DAY_MS) * DAY_MS
    out: dict[int, float] = {}
    rejected = 0
    duplicates = 0
    rows = batch if isinstance(batch, list) else []
    for row in rows:
        try:
            timestamp = int(row["timestamp"])
            value = float(row["openInterest"])
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        bucket = (timestamp // DAY_MS) * DAY_MS
        if timestamp < 0 or bucket >= current_day or not math.isfinite(value) or value < 0:
            rejected += 1
            continue
        if bucket in out:
            duplicates += 1
        out[bucket] = value
    _diagnostics(
        diagnostics,
        input_rows=len(rows),
        accepted=len(out),
        rejected=rejected,
        duplicates=duplicates,
    )
    return out


def parse_account_ratio_rows(batch, *, now_ms: int, diagnostics=None) -> dict[int, dict[str, float]]:
    """Normalize account ratios and derive the long/short ratio explicitly."""
    current_day = (int(now_ms) // DAY_MS) * DAY_MS
    out: dict[int, dict[str, float]] = {}
    rejected = 0
    duplicates = 0
    rows = batch if isinstance(batch, list) else []
    for row in rows:
        try:
            timestamp = int(row["timestamp"])
            buy = float(row["buyRatio"])
            sell = float(row["sellRatio"])
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        bucket = (timestamp // DAY_MS) * DAY_MS
        if (
            timestamp < 0
            or bucket >= current_day
            or not all(math.isfinite(value) for value in (buy, sell))
            or buy < 0
            or sell <= 0
        ):
            rejected += 1
            continue
        if bucket in out:
            duplicates += 1
        out[bucket] = {
            "buy_ratio": buy,
            "sell_ratio": sell,
            "long_short_ratio": buy / sell,
        }
    _diagnostics(
        diagnostics,
        input_rows=len(rows),
        accepted=len(out),
        rejected=rejected,
        duplicates=duplicates,
    )
    return out


def _fetch_history(
    *,
    ticker: str,
    dataset_id: str,
    page_fetch,
    parser,
    days: int,
    now_ms: int,
    http_get,
    sleep: float,
    retry_sleep: float,
    max_attempts: int,
    max_pages: int,
    archive_root,
    observed_at_ms: int | None,
) -> dict:
    if days <= 0:
        raise ValueError("days must be positive")
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")

    start_floor = max(0, int(now_ms) - int(days) * DAY_MS)
    symbol = ticker.upper() + "USDT"
    values: dict = {}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    complete = False

    for _ in range(max_pages):
        source_uris: list[str] = []
        for attempt in range(max_attempts):
            try:
                batch, next_cursor = page_fetch(
                    symbol,
                    limit=200,
                    cursor=cursor,
                    http_get=http_get,
                    source_sink=source_uris,
                )
                break
            except urllib.error.HTTPError:
                # 429/4xx is policy or rate-limit state, not a transient transport
                # failure.  Do not amplify it with retries.
                raise
            except (TimeoutError, urllib.error.URLError, ConnectionError):
                if attempt + 1 >= max_attempts:
                    raise
                if retry_sleep:
                    time.sleep(retry_sleep * (2**attempt))
        diagnostics = {}
        parsed = parser(batch, now_ms=now_ms, diagnostics=diagnostics)
        overlap = len(set(values).intersection(parsed))
        if overlap:
            diagnostics["duplicate_rows"] += overlap
            diagnostics["accepted_rows"] -= overlap
        values.update(parsed)

        if archive_root is not None:
            archive_json_response(
                root=archive_root,
                payload=batch,
                source_uri=source_uris[-1],
                provider=SOURCE_NAME,
                dataset_id=dataset_id,
                instrument_id=ticker,
                observed_at_ms=int(observed_at_ms),
                parse_diagnostics=diagnostics,
            )

        if not batch:
            complete = True
            break
        if not parsed:
            raise BybitPositioningFetchError(
                f"{dataset_id} page had no valid completed observations"
            )
        if min(parsed) <= start_floor or not next_cursor:
            complete = True
            break
        if next_cursor in seen_cursors:
            raise BybitPositioningFetchError(f"{dataset_id} cursor repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if sleep:
            time.sleep(sleep)

    if not complete:
        raise BybitPositioningFetchError(
            f"{dataset_id} history exceeded max_pages={max_pages}"
        )
    return {timestamp: value for timestamp, value in values.items() if timestamp >= start_floor}


def fetch_daily_positioning(
    ticker: str = "BTC",
    *,
    days: int = 2400,
    now_ms: int | None = None,
    http_get=_http_get_json,
    sleep: float = 0.20,
    retry_sleep: float = 1.0,
    max_attempts: int = 3,
    max_pages: int = 20,
    archive_root=None,
    observed_at_ms: int | None = None,
) -> dict[str, dict]:
    """Fetch OI and broad-account positioning on the same completed-day grid."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    oi = _fetch_history(
        ticker=ticker,
        dataset_id="open_interest_1d",
        page_fetch=fetch_open_interest_page,
        parser=parse_open_interest_rows,
        days=days,
        now_ms=now_ms,
        http_get=http_get,
        sleep=sleep,
        retry_sleep=retry_sleep,
        max_attempts=max_attempts,
        max_pages=max_pages,
        archive_root=archive_root,
        observed_at_ms=observed_at_ms,
    )
    ratios = _fetch_history(
        ticker=ticker,
        dataset_id="account_ratio_1d",
        page_fetch=fetch_account_ratio_page,
        parser=parse_account_ratio_rows,
        days=days,
        now_ms=now_ms,
        http_get=http_get,
        sleep=sleep,
        retry_sleep=retry_sleep,
        max_attempts=max_attempts,
        max_pages=max_pages,
        archive_root=archive_root,
        observed_at_ms=observed_at_ms,
    )
    return {
        "open_interest": dict(sorted(oi.items())),
        "buy_ratio": {timestamp: row["buy_ratio"] for timestamp, row in sorted(ratios.items())},
        "sell_ratio": {timestamp: row["sell_ratio"] for timestamp, row in sorted(ratios.items())},
        "long_short_ratio": {
            timestamp: row["long_short_ratio"] for timestamp, row in sorted(ratios.items())
        },
    }


def _utc_date(timestamp: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(timestamp / 1000))


def build_snapshot(positioning_by_ticker, *, fetched_at_ms: int) -> dict:
    metadata = {}
    series = {}
    for ticker, values in sorted(positioning_by_ticker.items()):
        oi = values.get("open_interest", {})
        ratios = values.get("long_short_ratio", {})
        common = sorted(set(oi).intersection(ratios))
        metadata[ticker] = {
            "open_interest_days": len(oi),
            "account_ratio_days": len(ratios),
            "common_days": len(common),
            "first_common_date": _utc_date(common[0]) if common else None,
            "last_common_date": _utc_date(common[-1]) if common else None,
        }
        series[ticker] = {
            field: {str(timestamp): value for timestamp, value in sorted(values.get(field, {}).items())}
            for field in (
                "open_interest",
                "buy_ratio",
                "sell_ratio",
                "long_short_ratio",
            )
        }
    return {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "endpoints": [OPEN_INTEREST_ENDPOINT, ACCOUNT_RATIO_ENDPOINT],
        "provenance": "REAL_BYBIT_PUBLIC_POSITIONING_HISTORY",
        "knowledge_time_semantics": (
            "event_time is the completed UTC-day bucket; fetched_at_ms is the observation time; "
            "the unfinished current UTC day is excluded"
        ),
        "fields": [
            "open_interest",
            "buy_ratio",
            "sell_ratio",
            "long_short_ratio",
        ],
        "fetched_at_ms": int(fetched_at_ms),
        "fetched_at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(fetched_at_ms / 1000)
        ),
        "tickers": metadata,
        "series": series,
    }


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(
        description="Fetch Bybit public OI and account-ratio history (read-only, no key)"
    )
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--days", type=int, default=2400)
    parser.add_argument("--out", default=DEFAULT_OUTPUT)
    parser.add_argument("--archive-root", default=DEFAULT_RAW_ARCHIVE)
    parser.add_argument("--sleep", type=float, default=0.20)
    args = parser.parse_args(argv)

    tickers = tuple(
        ticker.strip().upper() for ticker in args.tickers.split(",") if ticker.strip()
    )
    if not tickers:
        raise ValueError("at least one ticker is required")
    now_ms = int(time.time() * 1000)
    results = {}
    for ticker in tickers:
        results[ticker] = fetch_daily_positioning(
            ticker,
            days=args.days,
            now_ms=now_ms,
            sleep=args.sleep,
            archive_root=args.archive_root,
            observed_at_ms=now_ms,
        )
        common = set(results[ticker]["open_interest"]).intersection(
            results[ticker]["long_short_ratio"]
        )
        print(f"{ticker}USDT: {len(common)} common completed days", flush=True)

    snapshot = build_snapshot(results, fetched_at_ms=now_ms)
    atomic_write_json(args.out, snapshot, indent=2)
    print(f"snapshot -> {args.out}", flush=True)
    return snapshot


if __name__ == "__main__":
    main()
