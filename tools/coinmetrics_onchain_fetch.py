"""Coin Metrics Community on-chain history (public, keyless, research-only).

The Community API is accessed through its official HTTPS host and is licensed
for non-commercial use.  This fetcher is therefore an L2 research data source,
not a production-commercial entitlement.  It never reads credentials and has
no account, broker, order, transfer, or withdrawal capability.

Only completed UTC daily rows are emitted.  Every response page can be archived
with its observation time; pagination is restricted to the exact official
Community API endpoint to prevent an untrusted ``next_page_url`` redirect.
"""
from __future__ import annotations

import argparse
import datetime
import math
import os
import time
import urllib.error
import urllib.parse

from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.cache_io import atomic_write_json
from tools.coingecko_fetch import _http_get_json


DAY_MS = 86_400_000
HOST = "https://community-api.coinmetrics.io"
ENDPOINT = "/v4/timeseries/asset-metrics"
DEFAULT_OUTPUT = os.path.join("data", "cache", "coinmetrics_onchain.json")
# This is the *Community entitlement* returned by catalog-v2/asset-metrics,
# not the broader product catalog returned by catalog-all-v2.  Keep the source
# contract explicit so a product-only metric fails locally before any request.
COMMUNITY_ENTITLED_ASSETS = (
    "btc", "eth", "bch", "ltc", "doge", "etc", "ada", "xrp", "xlm",
)
COMMUNITY_ENTITLED_METRICS = ("AdrActCnt", "TxCnt", "CapMVRVCur")
DEFAULT_ASSETS = COMMUNITY_ENTITLED_ASSETS
DEFAULT_METRICS = COMMUNITY_ENTITLED_METRICS
SOURCE_NAME = "Coin Metrics Community API v4 asset metrics"
LICENSE_BOUNDARY = "CC BY-NC 4.0 / non-commercial research only"


class CoinMetricsFetchError(RuntimeError):
    """The Community API response cannot satisfy the source contract."""


def _day_ms(value: object) -> int:
    if not isinstance(value, str) or len(value) < 20 or not value.endswith("Z"):
        raise ValueError("daily Coin Metrics timestamp must be an ISO-8601 UTC value")
    day = datetime.date.fromisoformat(value[:10])
    return int(
        datetime.datetime(
            day.year,
            day.month,
            day.day,
            tzinfo=datetime.timezone.utc,
        ).timestamp()
        * 1000
    )


def parse_asset_metric_rows(
    rows,
    *,
    metrics,
    now_ms: int,
    diagnostics=None,
) -> dict[str, dict[str, dict[int, float]]]:
    """Normalize daily rows; metric absence stays missing and is never imputed."""
    current_day = (int(now_ms) // DAY_MS) * DAY_MS
    result: dict[str, dict[str, dict[int, float]]] = {}
    rejected_rows = 0
    rejected_values = 0
    duplicate_values = 0
    accepted_values = 0
    source_rows = rows if isinstance(rows, list) else []
    for row in source_rows:
        try:
            asset = str(row["asset"]).strip().upper()
            timestamp = _day_ms(row["time"])
        except (KeyError, TypeError, ValueError):
            rejected_rows += 1
            continue
        if not asset or timestamp < 0 or timestamp >= current_day:
            rejected_rows += 1
            continue
        asset_metrics = result.setdefault(asset, {metric: {} for metric in metrics})
        for metric in metrics:
            if metric not in row or row[metric] is None:
                continue
            try:
                value = float(row[metric])
            except (TypeError, ValueError):
                rejected_values += 1
                continue
            if not math.isfinite(value) or value <= 0:
                rejected_values += 1
                continue
            if timestamp in asset_metrics[metric]:
                duplicate_values += 1
            else:
                accepted_values += 1
            asset_metrics[metric][timestamp] = value
    if diagnostics is not None:
        diagnostics.update(
            {
                "status": "ACCEPTED",
                "input_rows": len(source_rows),
                "accepted_values": accepted_values,
                "rejected_rows": rejected_rows,
                "rejected_values": rejected_values,
                "duplicate_values": duplicate_values,
            }
        )
    return result


def _trusted_page_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "community-api.coinmetrics.io"
        and parsed.path == ENDPOINT
        and not parsed.username
        and not parsed.password
    )


def _request(url: str, *, http_get, max_attempts: int, retry_sleep: float):
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    for attempt in range(max_attempts):
        try:
            payload = http_get(url, {"accept": "application/json"})
            if not isinstance(payload, dict):
                raise CoinMetricsFetchError("Coin Metrics response is not an object")
            return payload
        except urllib.error.HTTPError:
            # Rate-limit and policy responses must not be amplified.
            raise
        except (TimeoutError, urllib.error.URLError, ConnectionError):
            if attempt + 1 >= max_attempts:
                raise
            if retry_sleep:
                time.sleep(retry_sleep * (2**attempt))
    raise AssertionError("unreachable retry loop")


def fetch_history(
    *,
    assets=DEFAULT_ASSETS,
    metrics=DEFAULT_METRICS,
    days: int = 2400,
    now_ms: int | None = None,
    http_get=_http_get_json,
    sleep: float = 0.70,
    retry_sleep: float = 1.0,
    max_attempts: int = 3,
    max_pages: int = 20,
    archive_root=None,
    observed_at_ms: int | None = None,
) -> dict[str, dict[str, dict[int, float]]]:
    """Fetch a bounded, cursor-paginated daily history from the Community host."""
    if days <= 0:
        raise ValueError("days must be positive")
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    assets = tuple(str(asset).strip().lower() for asset in assets if str(asset).strip())
    metrics = tuple(str(metric).strip() for metric in metrics if str(metric).strip())
    if not assets or not metrics:
        raise ValueError("at least one asset and metric are required")
    unsupported_assets = sorted(set(assets) - set(COMMUNITY_ENTITLED_ASSETS))
    unsupported_metrics = sorted(set(metrics) - set(COMMUNITY_ENTITLED_METRICS))
    if unsupported_assets or unsupported_metrics:
        raise CoinMetricsFetchError(
            "request exceeds the pinned Community entitlement: "
            f"assets={unsupported_assets}, metrics={unsupported_metrics}"
        )

    start_ms = max(0, int(now_ms) - int(days) * DAY_MS)
    params = urllib.parse.urlencode(
        {
            "assets": ",".join(assets),
            "metrics": ",".join(metrics),
            "frequency": "1d",
            "start_time": time.strftime("%Y-%m-%d", time.gmtime(start_ms / 1000)),
            "end_time": time.strftime("%Y-%m-%d", time.gmtime(now_ms / 1000)),
            "paging_from": "start",
            "page_size": 10000,
        }
    )
    url = f"{HOST}{ENDPOINT}?{params}"
    seen_urls: set[str] = set()
    combined: dict[str, dict[str, dict[int, float]]] = {
        asset.upper(): {metric: {} for metric in metrics} for asset in assets
    }

    complete = False
    for _ in range(max_pages):
        if url in seen_urls:
            raise CoinMetricsFetchError("next_page_url repeated")
        seen_urls.add(url)
        payload = _request(
            url,
            http_get=http_get,
            max_attempts=max_attempts,
            retry_sleep=retry_sleep,
        )
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise CoinMetricsFetchError("Coin Metrics response has no data list")
        diagnostics = {}
        parsed = parse_asset_metric_rows(
            rows,
            metrics=metrics,
            now_ms=now_ms,
            diagnostics=diagnostics,
        )
        for asset, by_metric in parsed.items():
            target = combined.setdefault(asset, {metric: {} for metric in metrics})
            for metric, values in by_metric.items():
                overlap = len(set(target[metric]).intersection(values))
                diagnostics["duplicate_values"] += overlap
                diagnostics["accepted_values"] -= overlap
                target[metric].update(values)
        if archive_root is not None:
            archive_json_response(
                root=archive_root,
                payload=rows,
                # The response itself and observation time are archived; cursor
                # tokens are transport state and the raw archive deliberately
                # forbids token-shaped query parameters.
                source_uri=f"{HOST}{ENDPOINT}",
                provider=SOURCE_NAME,
                dataset_id="asset_metrics_1d",
                instrument_id=",".join(asset.upper() for asset in assets),
                observed_at_ms=int(observed_at_ms),
                parse_diagnostics=diagnostics,
            )

        next_url = payload.get("next_page_url") or ""
        if not next_url:
            complete = True
            break
        if not isinstance(next_url, str) or not _trusted_page_url(next_url):
            raise CoinMetricsFetchError("untrusted next_page_url")
        url = next_url
        if sleep:
            time.sleep(sleep)
    if not complete:
        raise CoinMetricsFetchError(f"history exceeded max_pages={max_pages}")
    return {
        asset: {
            metric: dict(sorted(values.items()))
            for metric, values in by_metric.items()
        }
        for asset, by_metric in sorted(combined.items())
    }


def _utc_date(timestamp: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(timestamp / 1000))


def build_snapshot(series, *, metrics, fetched_at_ms: int) -> dict:
    metadata = {}
    serialized = {}
    for asset, by_metric in sorted(series.items()):
        metadata[asset] = {}
        serialized[asset] = {}
        for metric in metrics:
            values = by_metric.get(metric, {})
            timestamps = sorted(values)
            metadata[asset][metric] = {
                "days": len(values),
                "first_date": _utc_date(timestamps[0]) if timestamps else None,
                "last_date": _utc_date(timestamps[-1]) if timestamps else None,
            }
            serialized[asset][metric] = {
                str(timestamp): value for timestamp, value in sorted(values.items())
            }
    return {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "source_uri": f"{HOST}{ENDPOINT}",
        "provenance": "REAL_COINMETRICS_COMMUNITY_ONCHAIN_HISTORY",
        "license_boundary": LICENSE_BOUNDARY,
        "knowledge_time_semantics": (
            "event_time is the completed UTC daily metric timestamp; fetched_at_ms "
            "is the observation time; no missing value is imputed"
        ),
        "metrics": list(metrics),
        "fetched_at_ms": int(fetched_at_ms),
        "fetched_at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(fetched_at_ms / 1000)
        ),
        "assets": metadata,
        "series": serialized,
    }


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(
        description="Fetch Coin Metrics Community daily on-chain history (no key)"
    )
    parser.add_argument("--assets", default=",".join(DEFAULT_ASSETS))
    parser.add_argument("--metrics", default=",".join(DEFAULT_METRICS))
    parser.add_argument("--days", type=int, default=2400)
    parser.add_argument("--out", default=DEFAULT_OUTPUT)
    parser.add_argument("--archive-root", default=DEFAULT_RAW_ARCHIVE)
    parser.add_argument("--sleep", type=float, default=0.70)
    args = parser.parse_args(argv)

    assets = tuple(asset.strip().lower() for asset in args.assets.split(",") if asset.strip())
    metrics = tuple(metric.strip() for metric in args.metrics.split(",") if metric.strip())
    now_ms = int(time.time() * 1000)
    series = fetch_history(
        assets=assets,
        metrics=metrics,
        days=args.days,
        now_ms=now_ms,
        sleep=args.sleep,
        archive_root=args.archive_root,
        observed_at_ms=now_ms,
    )
    snapshot = build_snapshot(series, metrics=metrics, fetched_at_ms=now_ms)
    atomic_write_json(args.out, snapshot, indent=2)
    for asset in sorted(snapshot["assets"]):
        counts = snapshot["assets"][asset]
        print(
            f"{asset}: " + ", ".join(f"{metric}={counts[metric]['days']}" for metric in metrics),
            flush=True,
        )
    print(f"snapshot -> {args.out}", flush=True)
    return snapshot


if __name__ == "__main__":
    main()
