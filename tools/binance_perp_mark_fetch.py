"""Read-only Binance USD-M perpetual mark-price daily cache for forward evidence."""
from __future__ import annotations

import io
import json
import math
import os
import time
import urllib.request
import zipfile

from services.data.raw_archive import (DEFAULT_RAW_ARCHIVE,
                                       archive_response)
from tools.binance_oi_metrics_fetch import DEFAULT_TICKERS
from tools.cache_io import atomic_write_json
from tools.source_failures import classify_source_failure, source_failure_detail

_HOST = "https://data.binance.vision"
_DAY_MS = 86_400_000
_QUALITY_LOOKBACK_DAYS = 8
# Binance Data Vision daily archives can publish the latest closed day with a
# one-day delay.  Keep this explicit at the caller boundary: it is not a
# freshness waiver (the refresh SLO still caps mark prices at three days).
PUBLICATION_LAG_DAYS = 2
# A decision may be eight days old when admitted, then remain pending until the
# next weekly realization. Sixteen closed days preserve both price endpoints.
_FORWARD_COVERAGE_DAYS = 16
_CACHE = os.path.join("data", "cache", "crypto_perp_mark_prices.json")


class MarkPriceDataError(ValueError):
    pass


def day_string(day_ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(day_ms / 1000))


def mark_price_url(ticker: str, day_ms: int) -> str:
    symbol = ticker.upper() + "USDT"
    date = day_string(day_ms)
    return (
        f"/data/futures/um/daily/markPriceKlines/{symbol}/1d/"
        f"{symbol}-1d-{date}.zip"
    )


def _http_get_bytes(path: str) -> bytes:
    url = path if path.startswith("http") else _HOST + path
    request = urllib.request.Request(
        url, headers={"User-Agent": "quantos-research"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def parse_mark_price_zip(raw: bytes, *, expected_day: int) -> float:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        names = [name for name in archive.namelist() if name.endswith(".csv")]
        if len(names) != 1:
            raise MarkPriceDataError("mark-price archive must contain exactly one CSV")
        lines = [
            line for line in archive.read(names[0]).decode("utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise MarkPriceDataError(f"invalid mark-price archive: {exc}") from exc
    if len(lines) != 2:
        raise MarkPriceDataError("mark-price CSV must contain one header and one daily row")
    fields = lines[1].split(",")
    if len(fields) < 7:
        raise MarkPriceDataError("mark-price row has too few columns")
    try:
        open_time = int(fields[0])
        open_price, high, low, close = (float(fields[index]) for index in range(1, 5))
        close_time = int(fields[6])
    except (TypeError, ValueError) as exc:
        raise MarkPriceDataError("mark-price row has invalid numeric fields") from exc
    if open_time != int(expected_day):
        raise MarkPriceDataError(
            f"mark-price row {open_time} does not match expected day {expected_day}"
        )
    if close_time != open_time + _DAY_MS - 1:
        raise MarkPriceDataError("mark-price close_time does not close the UTC day")
    values = (open_price, high, low, close)
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise MarkPriceDataError("mark-price OHLC must be positive and finite")
    if high < max(open_price, close, low) or low > min(open_price, close, high):
        raise MarkPriceDataError("mark-price OHLC ordering is invalid")
    return close


def fetch_mark_days(ticker: str, days: list[int], *,
                    http_get_bytes=_http_get_bytes, max_workers: int = 4,
                    max_attempts: int = 3, retry_sleep=time.sleep,
                    failure_sink=None, archive_root=None,
                    observed_at_ms=None) -> dict:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")

    def one(day):
        raw = None
        path = mark_price_url(ticker, day)
        for attempt in range(max_attempts):
            try:
                raw = http_get_bytes(path)
                break
            except Exception as exc:
                if (classify_source_failure(exc) == "TRANSIENT"
                        and attempt + 1 < max_attempts):
                    retry_sleep(0.2 * (2 ** attempt))
                    continue
                return day, None, source_failure_detail(
                    ticker, exc, day_ms=day, phase="mark_price"
                )
        try:
            close = parse_mark_price_zip(
                raw, expected_day=day
            )
            if archive_root is not None:
                archive_response(
                    root=archive_root,
                    payload=raw,
                    source_uri=_HOST + path,
                    provider="Binance Data Vision USD-M mark-price klines",
                    dataset_id="perp_mark_prices",
                    instrument_id=ticker,
                    observed_at_ms=int(observed_at_ms),
                    representation="HTTP_BODY_BYTES",
                    parse_diagnostics={
                        "status": "ACCEPTED", "input_rows": 1,
                        "accepted_rows": 1, "rejected_rows": 0,
                        "duplicate_rows": 0, "event_time_ms": day,
                    },
                )
            return day, close, None
        except Exception as exc:
            if archive_root is not None and raw is not None:
                try:
                    archive_response(
                        root=archive_root,
                        payload=raw,
                        source_uri=_HOST + path,
                        provider="Binance Data Vision USD-M mark-price klines",
                        dataset_id="perp_mark_prices",
                        instrument_id=ticker,
                        observed_at_ms=int(observed_at_ms),
                        representation="HTTP_BODY_BYTES",
                        parse_diagnostics={
                            "status": "REJECTED", "input_rows": 1,
                            "accepted_rows": 0, "rejected_rows": 1,
                            "duplicate_rows": 0, "event_time_ms": day,
                            "error_type": type(exc).__name__,
                        },
                    )
                except Exception as archive_exc:
                    exc = archive_exc
            return day, None, source_failure_detail(
                ticker, exc, day_ms=day, phase="mark_price"
            )

    out = {}
    if max_workers <= 1:
        results = (one(day) for day in days)
        for day, close, failure in results:
            if failure is not None and failure_sink is not None:
                failure_sink.append(failure)
            if close is not None:
                out[day] = close
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for day, close, failure in executor.map(one, days):
                if failure is not None and failure_sink is not None:
                    failure_sink.append(failure)
                if close is not None:
                    out[day] = close
    return out


def _load_cache(path: str) -> dict:
    if not os.path.exists(path):
        return {"_meta": {}, "data": {}}
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("data", {}), dict):
        raise MarkPriceDataError("mark-price cache has an invalid root schema")
    payload.setdefault("_meta", {})
    payload.setdefault("data", {})
    return payload


def _expected_end(now_ms: int, publication_lag_days: int) -> int:
    if (
        isinstance(publication_lag_days, bool)
        or not isinstance(publication_lag_days, int)
        or publication_lag_days < 1
    ):
        raise ValueError("publication_lag_days must be a positive integer")
    return (int(now_ms) // _DAY_MS) * _DAY_MS - publication_lag_days * _DAY_MS


def mark_price_quality_failures(tickers, *, now_ms: int,
                                cache_path: str = _CACHE,
                                lookback_days: int = _QUALITY_LOOKBACK_DAYS,
                                publication_lag_days: int = 1) -> list:
    try:
        cache = _load_cache(cache_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return sorted(set(tickers))
    expected_end = _expected_end(now_ms, publication_lag_days)
    expected = {
        expected_end - offset * _DAY_MS for offset in range(lookback_days)
    }
    failures = []
    for ticker in tickers:
        raw = cache["data"].get(ticker, {})
        try:
            values = {int(day): value for day, value in raw.items()}
        except (AttributeError, TypeError, ValueError):
            failures.append(ticker)
            continue
        if set(values) & expected != expected:
            failures.append(ticker)
            continue
        if any(
            isinstance(values[day], bool)
            or not isinstance(values[day], (int, float))
            or not math.isfinite(float(values[day]))
            or float(values[day]) <= 0
            for day in expected
        ):
            failures.append(ticker)
    return sorted(failures)


def refresh_mark_prices(tickers=None, *, now_ms: int | None = None,
                        cache_path: str = _CACHE,
                        http_get_bytes=_http_get_bytes,
                        lookback_days: int = _FORWARD_COVERAGE_DAYS,
                        publication_lag_days: int = 1,
                        max_workers: int = 4, archive_root=None,
                        observed_at_ms=None) -> dict:
    selected = list(tickers or DEFAULT_TICKERS)
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    if lookback_days < _QUALITY_LOOKBACK_DAYS:
        raise ValueError(
            f"lookback_days must be at least {_QUALITY_LOOKBACK_DAYS}"
        )
    cache = _load_cache(cache_path)
    meta, data = cache["_meta"], cache["data"]
    expected_end = _expected_end(now_ms, publication_lag_days)
    expected_start = expected_end - (lookback_days - 1) * _DAY_MS
    report = {
        "source": "perp_mark_prices",
        "refreshed": 0,
        "skipped": 0,
        "new_days": 0,
        "failed": [],
        "failure_details": [],
        "failed_tickers": [],
        "quality_failed": [],
        "publication_lag_days": publication_lag_days,
        "expected_end": expected_end,
    }
    attempted = set()
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    for ticker in selected:
        ticker_data = data.setdefault(ticker, {})
        present = {int(day) for day in ticker_data}
        missing = [
            day for day in range(expected_start, expected_end + 1, _DAY_MS)
            if day not in present
        ]
        if missing:
            attempted.add(ticker)
            fresh = fetch_mark_days(
                ticker,
                missing,
                http_get_bytes=http_get_bytes,
                max_workers=max_workers,
                failure_sink=report["failure_details"],
                archive_root=archive_root,
                observed_at_ms=observed_at_ms,
            )
            for day, close in fresh.items():
                ticker_data[str(day)] = close
            report["new_days"] += len(fresh)
        actual = sorted(int(day) for day in ticker_data)
        meta[ticker] = {"through": actual[-1] if actual else None}
        atomic_write_json(cache_path, cache)

    quality_failed = mark_price_quality_failures(
        selected,
        now_ms=now_ms,
        cache_path=cache_path,
        publication_lag_days=publication_lag_days,
    )
    report["quality_failed"] = quality_failed
    existing_failure_tickers = {
        item["ticker"] for item in report["failure_details"]
        if item.get("kind") != "NOT_FOUND"
    }
    for ticker in quality_failed:
        report["failure_details"].append({
            "ticker": ticker,
            "kind": "QUALITY",
            "error_type": "MarkPriceQualityError",
            "message": "recent mark-price cache is incomplete or invalid",
        })
    report["failed_tickers"] = sorted(existing_failure_tickers | set(quality_failed))
    report["failed"] = [
        f"{item['ticker']}: {item['kind']}: {item['message']}"
        for item in report["failure_details"]
    ]
    failed_set = set(report["failed_tickers"])
    report["refreshed"] = sum(
        1 for ticker in selected if ticker in attempted and ticker not in failed_set
    )
    report["skipped"] = sum(
        1 for ticker in selected if ticker not in attempted and ticker not in failed_set
    )
    return report


def load_mark_prices(cache_path: str = _CACHE) -> dict:
    cache = _load_cache(cache_path)
    return {
        ticker: {int(day): float(close) for day, close in values.items()}
        for ticker, values in cache["data"].items()
    }


def main() -> None:
    now_ms = int(time.time() * 1000)
    report = refresh_mark_prices(
        now_ms=now_ms,
        archive_root=DEFAULT_RAW_ARCHIVE,
        observed_at_ms=now_ms,
        publication_lag_days=PUBLICATION_LAG_DAYS,
    )
    print(
        f"perp mark prices: refreshed={report['refreshed']} "
        f"new_days={report['new_days']} failed={len(report['failed_tickers'])}"
    )
    print(f"-> {_CACHE}")


if __name__ == "__main__":
    main()
