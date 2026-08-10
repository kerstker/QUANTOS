"""Hyperliquid public perpetual-market data fetcher (keyless, read-only).

The module talks only to the documented ``POST /info`` market-data surface. It
does not accept wallet addresses, private keys or signatures and contains no
exchange/action endpoint. Historical funding events are aggregated only when a
completed UTC day contains all 24 distinct hourly settlements; incomplete days
are omitted rather than imputed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.error
import urllib.request

from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.cache_io import atomic_write_json


HOST = "https://api.hyperliquid.xyz"
INFO_ENDPOINT = f"{HOST}/info"
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
PAGE_SIZE = 500
DEFAULT_OUTPUT = os.path.join("data", "cache", "hyperliquid_public_market.json")
DEFAULT_TICKERS = (
    "ADA", "ATOM", "AVAX", "BCH", "BNB", "BTC", "DOGE", "DOT",
    "ETH", "LINK", "LTC", "SOL", "UNI", "XRP",
)
SOURCE_NAME = "Hyperliquid public perpetual info API"
SOURCE_BOUNDARY = "Hyperliquid POST /info market data / no authentication"


class HyperliquidFetchError(RuntimeError):
    """The public response cannot satisfy the declared market-data contract."""


def _number(value, field: str, *, positive: bool = False) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HyperliquidFetchError(f"invalid {field}") from exc
    if not math.isfinite(out) or (positive and out <= 0.0):
        raise HyperliquidFetchError(f"invalid {field}")
    return out


def _optional_number(value, field: str, *, positive: bool = False) -> float | None:
    if value in (None, ""):
        return None
    return _number(value, field, positive=positive)


def _http_post_json(url: str, payload: dict, headers: dict):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _request(payload: dict, *, http_post, max_attempts: int, retry_sleep: float):
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "user-agent": "QUANTOS-L2-Public-Research/1.0",
    }
    for attempt in range(max_attempts):
        try:
            return http_post(INFO_ENDPOINT, payload, headers)
        except urllib.error.HTTPError:
            # Policy and rate-limit responses are not amplified by retries.
            raise
        except (TimeoutError, urllib.error.URLError, ConnectionError):
            if attempt + 1 >= max_attempts:
                raise
            if retry_sleep:
                time.sleep(retry_sleep * (2**attempt))
    raise AssertionError("unreachable retry loop")


def parse_meta_and_asset_contexts(payload, *, observed_at_ms: int) -> list[dict]:
    if not isinstance(payload, list) or len(payload) != 2:
        raise HyperliquidFetchError("metaAndAssetCtxs response must have two parts")
    meta, contexts = payload
    universe = meta.get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(contexts, list):
        raise HyperliquidFetchError("metaAndAssetCtxs response has invalid shape")
    if len(universe) != len(contexts):
        raise HyperliquidFetchError("meta/context length mismatch")
    rows = []
    for definition, context in zip(universe, contexts):
        if not isinstance(definition, dict) or not isinstance(context, dict):
            raise HyperliquidFetchError("meta/context row is not an object")
        coin = str(definition.get("name", "")).strip().upper()
        if not coin:
            raise HyperliquidFetchError("empty perpetual coin")
        rows.append(
            {
                "schema_version": 1,
                "venue": "hyperliquid_perp",
                "instrument_id": coin,
                "event_time_ms": int(observed_at_ms),
                "knowledge_time_ms": int(observed_at_ms),
                "observed_at_ms": int(observed_at_ms),
                "mark_price": _optional_number(context.get("markPx"), "markPx", positive=True),
                "oracle_price": _optional_number(
                    context.get("oraclePx"), "oraclePx", positive=True
                ),
                "mid_price": _optional_number(context.get("midPx"), "midPx", positive=True),
                "funding_rate": _optional_number(context.get("funding"), "funding"),
                "premium": _optional_number(context.get("premium"), "premium"),
                "open_interest": _optional_number(
                    context.get("openInterest"), "openInterest"
                ),
                "day_notional_volume": _optional_number(
                    context.get("dayNtlVlm"), "dayNtlVlm"
                ),
                "source_uri": INFO_ENDPOINT,
            }
        )
    return rows


def parse_funding_rows(rows, *, expected_coin: str) -> list[dict]:
    if not isinstance(rows, list):
        raise HyperliquidFetchError("fundingHistory response is not a list")
    expected = expected_coin.strip().upper()
    by_time: dict[int, dict] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise HyperliquidFetchError("funding row is not an object")
        coin = str(raw.get("coin", "")).strip().upper()
        if coin != expected:
            raise HyperliquidFetchError(
                f"funding coin mismatch: expected {expected}, received {coin or '<empty>'}"
            )
        try:
            timestamp = int(raw["time"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise HyperliquidFetchError("invalid funding time") from exc
        if timestamp < 0:
            raise HyperliquidFetchError("invalid funding time")
        by_time[timestamp] = {
            "coin": coin,
            "time_ms": timestamp,
            "funding_rate": _number(raw.get("fundingRate"), "fundingRate"),
            "premium": _number(raw.get("premium"), "premium"),
        }
    return [by_time[timestamp] for timestamp in sorted(by_time)]


def fetch_funding_history(
    coin: str,
    *,
    start_ms: int,
    end_ms: int,
    http_post=_http_post_json,
    sleep: float = 2.5,
    max_pages: int = 100,
    max_attempts: int = 3,
    retry_sleep: float = 1.0,
    archive_root=None,
    observed_at_ms: int | None = None,
) -> list[dict]:
    if start_ms < 0 or end_ms < start_ms:
        raise ValueError("invalid funding history interval")
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    coin = coin.strip().upper()
    cursor = int(start_ms)
    by_time: dict[int, dict] = {}
    for page_index in range(max_pages):
        request_payload = {
            "type": "fundingHistory",
            "coin": coin,
            "startTime": cursor,
            "endTime": int(end_ms),
        }
        raw = _request(
            request_payload,
            http_post=http_post,
            max_attempts=max_attempts,
            retry_sleep=retry_sleep,
        )
        parsed = parse_funding_rows(raw, expected_coin=coin)
        in_range = [row for row in parsed if start_ms <= row["time_ms"] <= end_ms]
        for row in in_range:
            by_time[row["time_ms"]] = row
        if archive_root is not None:
            archive_source = (
                f"{INFO_ENDPOINT}?type=fundingHistory&coin={coin}"
                f"&startTime={cursor}&endTime={int(end_ms)}"
            )
            archive_json_response(
                root=archive_root,
                payload=raw,
                source_uri=archive_source,
                provider=SOURCE_NAME,
                dataset_id="hyperliquid_funding_history_1h",
                instrument_id=coin,
                observed_at_ms=int(observed_at_ms),
                parse_diagnostics={
                    "status": "ACCEPTED",
                    "input_rows": len(raw) if isinstance(raw, list) else 0,
                    "accepted_rows": len(in_range),
                    "rejected_rows": len(parsed) - len(in_range),
                    "duplicate_rows": len(in_range) - len({r["time_ms"] for r in in_range}),
                    "page_index": page_index,
                },
            )
        if not parsed or len(raw) < PAGE_SIZE:
            break
        last_time = parsed[-1]["time_ms"]
        if last_time >= end_ms:
            break
        next_cursor = last_time + 1
        if next_cursor <= cursor:
            raise HyperliquidFetchError("funding pagination did not advance")
        cursor = next_cursor
        if sleep:
            time.sleep(sleep)
    else:
        raise HyperliquidFetchError("funding pagination exceeded max_pages")
    return [by_time[timestamp] for timestamp in sorted(by_time)]


def aggregate_daily_funding(rows, *, now_ms: int) -> dict[int, dict]:
    current_day = (int(now_ms) // DAY_MS) * DAY_MS
    by_day: dict[int, dict[int, dict]] = {}
    for row in rows:
        timestamp = int(row["time_ms"])
        day = (timestamp // DAY_MS) * DAY_MS
        if day >= current_day:
            continue
        hour = timestamp // HOUR_MS
        by_day.setdefault(day, {})[hour] = row
    out = {}
    for day, by_hour in sorted(by_day.items()):
        expected_hours = set(range(day // HOUR_MS, day // HOUR_MS + 24))
        if set(by_hour) != expected_hours:
            continue
        ordered = [by_hour[hour] for hour in sorted(by_hour)]
        out[day] = {
            "funding_sum": float(sum(row["funding_rate"] for row in ordered)),
            "premium_mean": float(sum(row["premium"] for row in ordered) / 24.0),
            "observation_count": 24,
        }
    return out


_PREDICTED_VENUES = {
    "BinPerp": "binance_perp",
    "HlPerp": "hyperliquid_perp",
    "BybitPerp": "bybit_perp",
}


def parse_predicted_fundings(payload, *, observed_at_ms: int) -> list[dict]:
    if not isinstance(payload, list):
        raise HyperliquidFetchError("predictedFundings response is not a list")
    rows = []
    for asset_row in payload:
        if not isinstance(asset_row, list) or len(asset_row) != 2:
            raise HyperliquidFetchError("predicted funding asset row has invalid shape")
        coin, venues = str(asset_row[0]).strip().upper(), asset_row[1]
        if not coin or not isinstance(venues, list):
            raise HyperliquidFetchError("predicted funding asset row has invalid shape")
        for venue_row in venues:
            if not isinstance(venue_row, list) or len(venue_row) != 2:
                raise HyperliquidFetchError("predicted funding venue row has invalid shape")
            raw_venue, values = venue_row
            if raw_venue not in _PREDICTED_VENUES or not isinstance(values, dict):
                continue
            try:
                next_time = int(values["nextFundingTime"])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise HyperliquidFetchError("invalid predicted funding timing") from exc
            raw_interval = values.get("fundingIntervalHours")
            try:
                interval = None if raw_interval is None else int(raw_interval)
            except (TypeError, ValueError, OverflowError) as exc:
                raise HyperliquidFetchError("invalid predicted funding timing") from exc
            # The public aggregate occasionally includes an unavailable venue
            # placeholder with ``nextFundingTime=0``.  It is absence, not a
            # prediction; omit it without fabricating a time or interval.
            if next_time <= 0:
                continue
            if interval is not None and interval <= 0:
                raise HyperliquidFetchError("invalid predicted funding timing")
            rows.append(
                {
                    "instrument_id": coin,
                    "venue": _PREDICTED_VENUES[raw_venue],
                    "observed_at_ms": int(observed_at_ms),
                    "knowledge_time_ms": int(observed_at_ms),
                    "funding_rate": _number(values.get("fundingRate"), "fundingRate"),
                    "next_funding_time_ms": next_time,
                    "funding_interval_hours": interval,
                }
            )
    return rows


def build_snapshot(
    *,
    daily_by_coin: dict[str, dict[int, dict]],
    current_rows: list[dict],
    predicted_rows: list[dict],
    fetched_at_ms: int,
    errors: dict[str, str] | None = None,
) -> dict:
    assets = {}
    series = {}
    for coin, days in sorted(daily_by_coin.items()):
        timestamps = sorted(days)
        assets[coin] = {
            "complete_days": len(days),
            "first_event_time_ms": timestamps[0] if timestamps else None,
            "last_event_time_ms": timestamps[-1] if timestamps else None,
        }
        series[coin] = {
            str(day): {
                "funding_sum": float(values["funding_sum"]),
                "premium_mean": float(values["premium_mean"]),
                "observation_count": int(values["observation_count"]),
            }
            for day, values in sorted(days.items())
        }
    return {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "source_uri": INFO_ENDPOINT,
        "source_boundary": SOURCE_BOUNDARY,
        "provenance": "REAL_HYPERLIQUID_PUBLIC_MARKET_HISTORY",
        "funding_semantics": (
            "daily sum of exactly 24 immutable hourly funding settlements on a UTC grid; "
            "incomplete and current days are omitted without imputation"
        ),
        "premium_semantics": "daily mean of the same 24 hourly funding-event premiums",
        "pit_admissible_for_research": True,
        "promotion_eligible": False,
        "credentials_accessed": False,
        "account_data_accessed": False,
        "order_capability": False,
        "fetched_at_ms": int(fetched_at_ms),
        "assets": assets,
        "series": series,
        "current_asset_contexts": list(current_rows),
        "predicted_fundings": list(predicted_rows),
        "errors": dict(sorted((errors or {}).items())),
    }


def merge_snapshots(previous: dict, current: dict) -> dict:
    """Append immutable completed days while replacing only current snapshots."""
    for name, snapshot in (("previous", previous), ("current", current)):
        if not isinstance(snapshot, dict) or (
            snapshot.get("provenance") != "REAL_HYPERLIQUID_PUBLIC_MARKET_HISTORY"
            or snapshot.get("pit_admissible_for_research") is not True
        ):
            raise HyperliquidFetchError(f"unexpected {name} snapshot contract")
    merged_days: dict[str, dict[int, dict]] = {}
    for snapshot in (previous, current):
        series = snapshot.get("series")
        if not isinstance(series, dict):
            raise HyperliquidFetchError("snapshot series is invalid")
        for raw_coin, raw_days in series.items():
            coin = str(raw_coin).strip().upper()
            if not coin or not isinstance(raw_days, dict):
                raise HyperliquidFetchError("snapshot series is invalid")
            target = merged_days.setdefault(coin, {})
            for raw_day, raw_values in raw_days.items():
                try:
                    day = int(raw_day)
                    values = {
                        "funding_sum": float(raw_values["funding_sum"]),
                        "premium_mean": float(raw_values["premium_mean"]),
                        "observation_count": int(raw_values["observation_count"]),
                    }
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    raise HyperliquidFetchError("snapshot daily row is invalid") from exc
                if (
                    day < 0 or day % DAY_MS != 0
                    or values["observation_count"] != 24
                    or not math.isfinite(values["funding_sum"])
                    or not math.isfinite(values["premium_mean"])
                ):
                    raise HyperliquidFetchError("snapshot daily row is invalid")
                existing = target.get(day)
                if existing is not None and existing != values:
                    raise HyperliquidFetchError(
                        f"historical conflict for {coin} at {day}"
                    )
                target[day] = values
    return build_snapshot(
        daily_by_coin=merged_days,
        current_rows=list(current.get("current_asset_contexts") or []),
        predicted_rows=list(current.get("predicted_fundings") or []),
        fetched_at_ms=int(current["fetched_at_ms"]),
        errors=dict(current.get("errors") or {}),
    )


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(
        description="Fetch Hyperliquid public funding/premium/OI market data (no key)"
    )
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--out", default=DEFAULT_OUTPUT)
    parser.add_argument("--archive-root", default=DEFAULT_RAW_ARCHIVE)
    parser.add_argument("--sleep", type=float, default=2.5)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument(
        "--merge-existing", action="store_true",
        help="Append new immutable completed days to an existing output snapshot",
    )
    args = parser.parse_args(argv)
    if args.days <= 0:
        raise ValueError("days must be positive")
    now_ms = int(time.time() * 1000)
    current_day = (now_ms // DAY_MS) * DAY_MS
    start_ms = max(0, current_day - args.days * DAY_MS)
    end_ms = current_day - 1
    tickers = tuple(dict.fromkeys(
        token.strip().upper() for token in args.tickers.split(",") if token.strip()
    ))
    if not tickers:
        raise ValueError("tickers must not be empty")

    meta_raw = _request(
        {"type": "metaAndAssetCtxs"}, http_post=_http_post_json,
        max_attempts=3, retry_sleep=1.0,
    )
    predicted_raw = _request(
        {"type": "predictedFundings"}, http_post=_http_post_json,
        max_attempts=3, retry_sleep=1.0,
    )
    current_rows = parse_meta_and_asset_contexts(meta_raw, observed_at_ms=now_ms)
    predicted_rows = parse_predicted_fundings(predicted_raw, observed_at_ms=now_ms)
    known = {row["instrument_id"] for row in current_rows}
    missing = [coin for coin in tickers if coin not in known]
    if missing:
        raise HyperliquidFetchError("requested coins are absent from meta: " + ", ".join(missing))

    if args.archive_root:
        for dataset_id, raw in (
            ("hyperliquid_meta_and_asset_contexts", meta_raw),
            ("hyperliquid_predicted_fundings", predicted_raw),
        ):
            archive_json_response(
                root=args.archive_root,
                payload=raw,
                source_uri=INFO_ENDPOINT,
                provider=SOURCE_NAME,
                dataset_id=dataset_id,
                instrument_id="ALL",
                observed_at_ms=now_ms,
                parse_diagnostics={
                    "status": "ACCEPTED", "input_rows": len(raw),
                    "accepted_rows": len(raw), "rejected_rows": 0,
                    "duplicate_rows": 0,
                },
            )

    daily_by_coin = {}
    errors = {}
    for coin in tickers:
        try:
            rows = fetch_funding_history(
                coin,
                start_ms=start_ms,
                end_ms=end_ms,
                sleep=args.sleep,
                max_pages=args.max_pages,
                archive_root=args.archive_root or None,
                observed_at_ms=now_ms,
            )
            daily_by_coin[coin] = aggregate_daily_funding(rows, now_ms=now_ms)
            print(f"{coin}: hourly={len(rows)} complete_days={len(daily_by_coin[coin])}", flush=True)
        except Exception as exc:  # per-asset fail-soft; the snapshot exposes every failure
            errors[coin] = f"{type(exc).__name__}: {exc}"
            daily_by_coin[coin] = {}
            print(f"{coin}: BLOCK {errors[coin]}", flush=True)

    snapshot = build_snapshot(
        daily_by_coin=daily_by_coin,
        current_rows=[row for row in current_rows if row["instrument_id"] in tickers],
        predicted_rows=[row for row in predicted_rows if row["instrument_id"] in tickers],
        fetched_at_ms=now_ms,
        errors=errors,
    )
    if args.merge_existing and os.path.isfile(args.out):
        try:
            with open(args.out, encoding="utf-8") as handle:
                previous = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise HyperliquidFetchError("existing snapshot cannot be read") from exc
        snapshot = merge_snapshots(previous, snapshot)
    atomic_write_json(args.out, snapshot, indent=2)
    print(f"snapshot -> {args.out}; errors={len(errors)}", flush=True)
    return snapshot


if __name__ == "__main__":
    main()


__all__ = [
    "DAY_MS", "DEFAULT_OUTPUT", "DEFAULT_TICKERS", "HOUR_MS",
    "HyperliquidFetchError", "aggregate_daily_funding", "build_snapshot",
    "fetch_funding_history", "merge_snapshots", "parse_funding_rows",
    "parse_meta_and_asset_contexts", "parse_predicted_fundings",
]
