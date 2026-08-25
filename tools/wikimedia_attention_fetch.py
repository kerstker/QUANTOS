"""Wikimedia crypto-attention history and honest forward vintages.

The Analytics API is public, keyless, and read-only. Its retrospective article
history is diagnostic because QUANTOS did not observe historical revisions when
they occurred. Each refresh also stores one complete cross-section with the
actual fetch time as knowledge time; only that accumulating series may later
enter a forward experiment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from quantos.contracts.feature import FeatureValue
from services.data.fundamental_store import SqliteFundamentalStore
from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.cache_io import atomic_write_json


DAY_MS = 86_400_000
ENDPOINT_TEMPLATE = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia.org/all-access/user/{article}/daily/{start}/{end}"
)
SOURCE_NAME = "Wikimedia Analytics article pageviews"
DEFAULT_OUTPUT = os.path.join("data", "cache", "crypto_attention_pageviews.json")
DEFAULT_VINTAGE_DB = os.path.join(
    "data", "cache", "crypto_attention_vintages.db"
)
FORWARD_FEATURE_ID = "wikimedia_crypto_attention_pageviews_forward_vintage"
DEFAULT_LOOKBACK_DAYS = 120
REQUEST_INTERVAL_SECONDS = 0.25
MAX_RETRY_AFTER_SECONDS = 60.0
RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})

# Pinned canonical English-Wikipedia titles. Redirect aliases are deliberately
# excluded because Wikimedia does not attribute redirect views to the target.
ATTENTION_ARTICLES: tuple[tuple[str, str], ...] = (
    ("ADA", "Cardano (blockchain platform)"),
    ("AVAX", "Avalanche (blockchain platform)"),
    ("BCH", "Bitcoin Cash"),
    ("BTC", "Bitcoin"),
    ("DOGE", "Dogecoin"),
    ("DOT", "Polkadot (blockchain platform)"),
    ("ETC", "Ethereum Classic"),
    ("ETH", "Ethereum"),
    ("LINK", "Chainlink (blockchain oracle)"),
    ("LTC", "Litecoin"),
    ("SOL", "Solana (blockchain platform)"),
    ("TRX", "Tron (blockchain)"),
    ("XRP", "XRP Ledger"),
)


class WikimediaAttentionError(RuntimeError):
    pass


def _http_get_json(url: str, headers: dict):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def _endpoint(article: str, start_day: str, end_day: str) -> str:
    try:
        start = datetime.strptime(start_day, "%Y%m%d")
        end = datetime.strptime(end_day, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("start_day and end_day must use YYYYMMDD") from exc
    if end < start:
        raise ValueError("end_day must not precede start_day")
    encoded = urllib.parse.quote(article, safe="")
    return ENDPOINT_TEMPLATE.format(
        article=encoded, start=start_day, end=end_day
    )


def fetch_article_pageviews(
    *,
    article: str,
    start_day: str,
    end_day: str,
    http_get=_http_get_json,
    max_attempts: int = 4,
    retry_sleep: float = 5.0,
):
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    url = _endpoint(article, start_day, end_day)
    headers = {
        "accept": "application/json",
        "User-Agent": "QUANTOS-L2-Public-Research/1.0",
    }
    for attempt in range(max_attempts):
        try:
            return http_get(url, headers)
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_HTTP_STATUS or attempt + 1 >= max_attempts:
                raise
            raw_retry_after = (
                exc.headers.get("Retry-After") if exc.headers is not None else None
            )
            try:
                retry_after = float(raw_retry_after)
            except (TypeError, ValueError):
                retry_after = 0.0
            if not math.isfinite(retry_after) or retry_after < 0.0:
                retry_after = 0.0
            delay = max(retry_sleep * (2 ** attempt), retry_after)
            time.sleep(min(delay, MAX_RETRY_AFTER_SECONDS))
        except (TimeoutError, urllib.error.URLError, ConnectionError):
            if attempt + 1 >= max_attempts:
                raise
            if retry_sleep:
                time.sleep(retry_sleep * (2 ** attempt))
    raise AssertionError("unreachable retry loop")


def _article_key(value: object) -> str:
    return str(value).replace("_", " ")


def parse_pageviews(
    payload,
    *,
    expected_article: str,
    now_ms: int,
    diagnostics=None,
) -> dict[int, float]:
    current_day = (int(now_ms) // DAY_MS) * DAY_MS
    source_rows = payload.get("items", []) if isinstance(payload, dict) else []
    values: dict[int, float] = {}
    accepted = rejected = duplicates = 0
    for raw in source_rows:
        try:
            if (
                raw["project"] != "en.wikipedia"
                or _article_key(raw["article"]) != expected_article
                or raw["granularity"] != "daily"
                or raw["access"] != "all-access"
                or raw["agent"] != "user"
                or type(raw["views"]) is not int
            ):
                raise ValueError("unexpected pageview row contract")
            timestamp = int(
                datetime.strptime(raw["timestamp"], "%Y%m%d%H")
                .replace(tzinfo=timezone.utc)
                .timestamp()
                * 1000
            )
            views = float(raw["views"])
        except (KeyError, TypeError, ValueError, OverflowError):
            rejected += 1
            continue
        if (
            timestamp < 0
            or timestamp >= current_day
            or not math.isfinite(views)
            or views <= 0.0
        ):
            rejected += 1
            continue
        if timestamp in values:
            duplicates += 1
        else:
            accepted += 1
        values[timestamp] = views
    if diagnostics is not None:
        diagnostics.update(
            {
                "status": "ACCEPTED",
                "input_rows": len(source_rows),
                "accepted_rows": accepted,
                "rejected_rows": rejected,
                "duplicate_rows": duplicates,
            }
        )
    return dict(sorted(values.items()))


def build_snapshot(
    series_by_instrument: dict[str, dict[int, float]],
    *,
    article_map: dict[str, str],
    fetched_at_ms: int,
) -> dict:
    if not series_by_instrument or set(series_by_instrument) != set(article_map):
        raise WikimediaAttentionError("attention snapshot requires a mapped series")
    if any(not values for values in series_by_instrument.values()):
        raise WikimediaAttentionError("attention snapshot has an empty instrument series")
    common_days = set.intersection(
        *(set(values) for values in series_by_instrument.values())
    )
    all_days = sorted(
        {day for values in series_by_instrument.values() for day in values}
    )
    return {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "source_uri_template": ENDPOINT_TEMPLATE,
        "provenance": "REAL_WIKIMEDIA_USER_PAGEVIEW_HISTORY",
        "metric": "daily_article_pageviews",
        "agent_filter": "user",
        "revision_policy": "RETROSPECTIVE_API_NO_CAPTURED_VINTAGES",
        "knowledge_time_semantics": (
            "fetched_at_ms is the only defensible knowledge time for imported "
            "history; only separately persisted forward cross-sections are PIT"
        ),
        "pit_admissible_for_promotion": False,
        "promotion_eligible": False,
        "credentials_accessed": False,
        "account_data_accessed": False,
        "order_capability": False,
        "fetched_at_ms": int(fetched_at_ms),
        "article_map": dict(sorted(article_map.items())),
        "coverage": {
            "instrument_count": len(series_by_instrument),
            "common_days": len(common_days),
            "first_event_time_ms": all_days[0] if all_days else None,
            "last_event_time_ms": all_days[-1] if all_days else None,
            "latest_common_event_time_ms": max(common_days) if common_days else None,
        },
        "series": {
            instrument: {
                str(timestamp): float(value)
                for timestamp, value in sorted(values.items())
            }
            for instrument, values in sorted(series_by_instrument.items())
        },
    }


def forward_vintage_values(snapshot: dict) -> tuple[FeatureValue, ...]:
    if not isinstance(snapshot, dict) or (
        snapshot.get("schema_version") != 1
        or snapshot.get("provenance") != "REAL_WIKIMEDIA_USER_PAGEVIEW_HISTORY"
        or snapshot.get("revision_policy")
        != "RETROSPECTIVE_API_NO_CAPTURED_VINTAGES"
        or snapshot.get("pit_admissible_for_promotion") is not False
        or snapshot.get("promotion_eligible") is not False
    ):
        raise WikimediaAttentionError("unexpected attention snapshot contract")
    try:
        fetched_at_ms = int(snapshot["fetched_at_ms"])
        article_map = dict(snapshot["article_map"])
        raw_series = dict(snapshot["series"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise WikimediaAttentionError("invalid attention snapshot contract") from exc
    if not article_map or set(article_map) != set(raw_series):
        raise WikimediaAttentionError("invalid attention snapshot mapping")
    current_day = (fetched_at_ms // DAY_MS) * DAY_MS
    parsed: dict[str, dict[int, float]] = {}
    try:
        for instrument, values in raw_series.items():
            if not isinstance(instrument, str) or not instrument:
                raise ValueError("invalid instrument")
            instrument_series: dict[int, float] = {}
            for raw_timestamp, raw_value in dict(values).items():
                timestamp = int(raw_timestamp)
                value = float(raw_value)
                if (
                    timestamp < 0
                    or timestamp >= current_day
                    or not math.isfinite(value)
                    or value <= 0.0
                ):
                    raise ValueError("invalid observation")
                instrument_series[timestamp] = value
            parsed[instrument] = instrument_series
    except (TypeError, ValueError, OverflowError) as exc:
        raise WikimediaAttentionError("invalid attention series") from exc
    if any(not values for values in parsed.values()):
        raise WikimediaAttentionError("attention snapshot lacks a complete series")
    common_days = set.intersection(*(set(values) for values in parsed.values()))
    if not common_days:
        raise WikimediaAttentionError("attention snapshot has no common completed day")
    event_time = max(common_days)
    return tuple(
        FeatureValue(
            feature_id=FORWARD_FEATURE_ID,
            instrument_id=instrument,
            event_time=event_time,
            knowledge_time=fetched_at_ms,
            value=parsed[instrument][event_time],
        )
        for instrument in sorted(parsed)
    )


def record_forward_vintages(
    snapshot: dict, db_path: str = DEFAULT_VINTAGE_DB
) -> int:
    rows = forward_vintage_values(snapshot)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    store = SqliteFundamentalStore(db_path, create=True)
    try:
        return int(store.add_many(rows))
    finally:
        store.close()


def _default_range(now_ms: int) -> tuple[str, str]:
    current_day = datetime.fromtimestamp(
        ((now_ms // DAY_MS) * DAY_MS) / 1000, timezone.utc
    )
    end = current_day - timedelta(days=1)
    start = end - timedelta(days=DEFAULT_LOOKBACK_DAYS - 1)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(
        description="Fetch public Wikimedia crypto-attention pageviews"
    )
    parser.add_argument("--out", default=DEFAULT_OUTPUT)
    parser.add_argument("--archive-root", default=DEFAULT_RAW_ARCHIVE)
    parser.add_argument("--vintage-db", default=DEFAULT_VINTAGE_DB)
    parser.add_argument("--start-day")
    parser.add_argument("--end-day")
    args = parser.parse_args(argv)
    fetched_at_ms = int(time.time() * 1000)
    default_start, default_end = _default_range(fetched_at_ms)
    start_day = args.start_day or default_start
    end_day = args.end_day or default_end
    article_map = dict(ATTENTION_ARTICLES)
    series_by_instrument: dict[str, dict[int, float]] = {}
    for index, (instrument, article) in enumerate(ATTENTION_ARTICLES):
        raw = fetch_article_pageviews(
            article=article, start_day=start_day, end_day=end_day
        )
        diagnostics: dict[str, object] = {}
        parsed = parse_pageviews(
            raw,
            expected_article=article,
            now_ms=fetched_at_ms,
            diagnostics=diagnostics,
        )
        if not parsed:
            raise WikimediaAttentionError(
                f"Wikimedia pageview history is empty for {instrument}"
            )
        if args.archive_root:
            archive_json_response(
                root=args.archive_root,
                payload=raw,
                source_uri=_endpoint(article, start_day, end_day),
                provider=SOURCE_NAME,
                dataset_id="crypto_attention_pageviews_1d",
                instrument_id=instrument,
                observed_at_ms=fetched_at_ms,
                parse_diagnostics=diagnostics,
            )
        series_by_instrument[instrument] = parsed
        if index + 1 < len(ATTENTION_ARTICLES):
            time.sleep(REQUEST_INTERVAL_SECONDS)
    snapshot = build_snapshot(
        series_by_instrument,
        article_map=article_map,
        fetched_at_ms=fetched_at_ms,
    )
    atomic_write_json(args.out, snapshot, indent=2)
    added = record_forward_vintages(snapshot, args.vintage_db)
    print(
        "Wikimedia attention: "
        f"instruments={len(series_by_instrument)}, "
        f"common_days={snapshot['coverage']['common_days']}, "
        f"forward_rows_added={added} -> {args.out}",
        flush=True,
    )
    return snapshot


if __name__ == "__main__":
    main()


__all__ = [
    "ATTENTION_ARTICLES",
    "FORWARD_FEATURE_ID",
    "WikimediaAttentionError",
    "build_snapshot",
    "fetch_article_pageviews",
    "forward_vintage_values",
    "parse_pageviews",
    "record_forward_vintages",
]
