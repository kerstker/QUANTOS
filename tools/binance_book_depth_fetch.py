"""Checksum-verified Binance USD-M historical book-depth collector.

The collector performs public, keyless HTTPS GETs against Data Vision only.
It cannot access an account, credential, broker, Testnet, Mainnet, or order
endpoint.  Historical rows retain their exchange event time but become known
to QUANTOS only at the actual ``observed_at_ms`` fetch time.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
from typing import Callable, Iterable, Sequence

from services.data.binance_book_depth_archive import (
    BinanceBookDepthArchiveError,
    DATASET,
    DAY_MS,
    align_with_mark_price,
    parse_book_depth_archive,
    parse_mark_price_1m_archive,
)
from services.data.binance_systemic_store import BinanceSystemicStore
from services.data.raw_archive import archive_response
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.cache_io import atomic_write_json


HOST = "https://data.binance.vision"
DEFAULT_STORE = os.path.join("data", "cache", "binance_book_depth_archive.db")
DEFAULT_STATUS = os.path.join("data", "cache", "binance_book_depth_archive_status.json")
DEFAULT_RAW_ARCHIVE = os.path.join("data", "cache", "raw_market_data")
EXPECTED_SNAPSHOTS_PER_DAY = 2_880
MINIMUM_SNAPSHOTS_PER_DAY = 2_592
MINIMUM_HOURLY_SNAPSHOTS = 100
MAX_INVERTED_SNAPSHOT_RATIO = 0.001


class BinanceBookDepthFetchError(RuntimeError):
    """One requested public archive could not be verified and admitted."""


class BinanceBookDepthArchiveUnavailable(BinanceBookDepthFetchError):
    """The requested completed-day archive has not been published."""


def _day_string(day_ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(day_ms / 1000))


def book_depth_url(instrument_id: str, day_ms: int) -> str:
    symbol = str(instrument_id).upper().strip()
    if not symbol.endswith("USDT") or not symbol[:-4].isalnum():
        raise ValueError("instrument_id must be an uppercase-compatible USDT symbol")
    if isinstance(day_ms, bool) or not isinstance(day_ms, int) or day_ms < 0 or day_ms % DAY_MS:
        raise ValueError("day_ms must be a UTC day boundary")
    day = _day_string(day_ms)
    return (
        f"/data/futures/um/daily/bookDepth/{symbol}/"
        f"{symbol}-bookDepth-{day}.zip"
    )


def mark_price_1m_url(instrument_id: str, day_ms: int) -> str:
    symbol = str(instrument_id).upper().strip()
    if not symbol.endswith("USDT") or not symbol[:-4].isalnum():
        raise ValueError("instrument_id must be an uppercase-compatible USDT symbol")
    if isinstance(day_ms, bool) or not isinstance(day_ms, int) or day_ms < 0 or day_ms % DAY_MS:
        raise ValueError("day_ms must be a UTC day boundary")
    day = _day_string(day_ms)
    return (
        f"/data/futures/um/daily/markPriceKlines/{symbol}/1m/"
        f"{symbol}-1m-{day}.zip"
    )


def _http_get_bytes(path: str) -> bytes:
    url = path if path.startswith("https://") else HOST + path
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "quantos-research"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise BinanceBookDepthArchiveUnavailable(
                "requested public book-depth archive is not available"
            ) from exc
        raise


def _checksum(document: bytes) -> str:
    try:
        fields = document.decode("ascii").strip().split()
    except UnicodeError as exc:
        raise BinanceBookDepthFetchError("checksum document is not ASCII") from exc
    if (
        not fields
        or len(fields[0]) != 64
        or fields[0].lower() != fields[0]
        or any(character not in "0123456789abcdef" for character in fields[0])
    ):
        raise BinanceBookDepthFetchError("checksum document is malformed")
    return fields[0]


def _request_bytes(
    path: str,
    *,
    http_get_bytes: Callable[[str], bytes],
    max_attempts: int,
    retry_sleep_seconds: float,
) -> bytes:
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts < 1
        or isinstance(retry_sleep_seconds, bool)
        or not isinstance(retry_sleep_seconds, (int, float))
        or retry_sleep_seconds < 0
    ):
        raise ValueError("request retry policy is invalid")
    for attempt in range(max_attempts):
        try:
            return http_get_bytes(path)
        except BinanceBookDepthArchiveUnavailable:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise BinanceBookDepthArchiveUnavailable(
                    "requested public book-depth archive is not available"
                ) from exc
            raise
        except (TimeoutError, urllib.error.URLError, ConnectionError):
            if attempt + 1 >= max_attempts:
                raise
            if retry_sleep_seconds:
                time.sleep(retry_sleep_seconds * (2**attempt))
    raise AssertionError("unreachable retry loop")


def refresh_book_depth_days(
    instruments: Iterable[str],
    days: Iterable[int],
    *,
    observed_at_ms: int,
    store_path: str = DEFAULT_STORE,
    archive_root: str = DEFAULT_RAW_ARCHIVE,
    http_get_bytes: Callable[[str], bytes] = _http_get_bytes,
    max_workers: int = 4,
    max_attempts: int = 4,
    retry_sleep_seconds: float = 0.5,
    minimum_snapshots_per_archive: int = MINIMUM_SNAPSHOTS_PER_DAY,
    minimum_hourly_snapshots: int = MINIMUM_HOURLY_SNAPSHOTS,
    require_all_hours: bool = True,
    max_inverted_snapshot_ratio: float = MAX_INVERTED_SNAPSHOT_RATIO,
) -> dict[str, object]:
    """Fetch, verify, parse, archive and atomically publish requested days."""
    if (
        isinstance(observed_at_ms, bool)
        or not isinstance(observed_at_ms, int)
        or observed_at_ms < 0
        or isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        raise ValueError("refresh timing or worker policy is invalid")
    selected_instruments = tuple(
        sorted({str(instrument).upper().strip() for instrument in instruments})
    )
    selected_days = tuple(sorted(set(days)))
    if not selected_instruments or not selected_days:
        raise ValueError("instruments and days must not be empty")

    def fetch_one(identity: tuple[str, int]):
        instrument_id, day_ms = identity
        path = book_depth_url(instrument_id, day_ms)
        raw = _request_bytes(
            path,
            http_get_bytes=http_get_bytes,
            max_attempts=max_attempts,
            retry_sleep_seconds=retry_sleep_seconds,
        )
        expected = _checksum(
            _request_bytes(
                path + ".CHECKSUM",
                http_get_bytes=http_get_bytes,
                max_attempts=max_attempts,
                retry_sleep_seconds=retry_sleep_seconds,
            )
        )
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise BinanceBookDepthFetchError(
                f"checksum mismatch for {instrument_id}/{_day_string(day_ms)}"
            )
        mark_path = mark_price_1m_url(instrument_id, day_ms)
        mark_raw = _request_bytes(
            mark_path,
            http_get_bytes=http_get_bytes,
            max_attempts=max_attempts,
            retry_sleep_seconds=retry_sleep_seconds,
        )
        expected_mark = _checksum(
            _request_bytes(
                mark_path + ".CHECKSUM",
                http_get_bytes=http_get_bytes,
                max_attempts=max_attempts,
                retry_sleep_seconds=retry_sleep_seconds,
            )
        )
        actual_mark = hashlib.sha256(mark_raw).hexdigest()
        if actual_mark != expected_mark:
            raise BinanceBookDepthFetchError(
                "mark-price checksum mismatch for "
                f"{instrument_id}/{_day_string(day_ms)}"
            )
        try:
            observations = parse_book_depth_archive(
                raw,
                instrument_id=instrument_id,
                expected_day_ms=day_ms,
                observed_at_ms=observed_at_ms,
                archive_sha256=actual,
                minimum_snapshot_count=minimum_snapshots_per_archive,
                minimum_hourly_snapshot_count=minimum_hourly_snapshots,
                require_all_hours=require_all_hours,
                max_inverted_snapshot_ratio=max_inverted_snapshot_ratio,
            )
            hourly_marks = parse_mark_price_1m_archive(
                mark_raw,
                instrument_id=instrument_id,
                expected_day_ms=day_ms,
            )
            observations = align_with_mark_price(
                observations,
                hourly_marks,
                mark_archive_sha256=actual_mark,
            )
        except BinanceBookDepthArchiveError as exc:
            raise BinanceBookDepthArchiveError(
                f"{instrument_id}/{_day_string(day_ms)}: {exc}"
            ) from exc
        return (
            instrument_id,
            day_ms,
            path,
            raw,
            actual,
            mark_path,
            mark_raw,
            actual_mark,
            observations,
        )

    identities = tuple(
        (instrument_id, day_ms)
        for instrument_id in selected_instruments
        for day_ms in selected_days
    )
    try:
        if max_workers == 1:
            fetched = tuple(fetch_one(identity) for identity in identities)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                fetched = tuple(executor.map(fetch_one, identities))
    except BinanceBookDepthFetchError:
        raise
    except (BinanceBookDepthArchiveError, OSError, ValueError) as exc:
        raise BinanceBookDepthFetchError(
            f"book-depth refresh failed closed: {exc}"
        ) from exc

    archived = []
    all_observations = []
    quarantined_snapshots = 0
    maximum_alignment_deviation = 0.0
    for (
        instrument_id,
        day_ms,
        path,
        raw,
        actual,
        mark_path,
        mark_raw,
        actual_mark,
        observations,
    ) in fetched:
        archive_snapshot_count = int(observations[0]["archive_snapshot_count"])
        archive_quarantined_count = int(
            observations[0]["archive_quarantined_snapshot_count"]
        )
        quarantined_snapshots += archive_quarantined_count
        archived.append(
            archive_response(
                root=archive_root,
                payload=raw,
                source_uri=HOST + path,
                provider="Binance Data Vision USD-M bookDepth",
                dataset_id=DATASET,
                instrument_id=instrument_id,
                observed_at_ms=observed_at_ms,
                representation="HTTP_BODY_BYTES",
                parse_diagnostics={
                    "status": "ACCEPTED",
                    "event_time_ms": day_ms,
                    "input_rows": archive_snapshot_count * 12,
                    "accepted_rows": (
                        archive_snapshot_count - archive_quarantined_count
                    ) * 12,
                    "rejected_rows": archive_quarantined_count * 12,
                    "duplicate_rows": 0,
                    "checksum_verified": True,
                    "source_archive_sha256": actual,
                    "historical_events_are_not_forward_vintages": True,
                },
            )
        )
        archived.append(
            archive_response(
                root=archive_root,
                payload=mark_raw,
                source_uri=HOST + mark_path,
                provider="Binance Data Vision USD-M markPriceKlines 1m",
                dataset_id="binance_usdm_mark_price_1m_alignment",
                instrument_id=instrument_id,
                observed_at_ms=observed_at_ms,
                representation="HTTP_BODY_BYTES",
                parse_diagnostics={
                    "status": "ACCEPTED",
                    "event_time_ms": day_ms,
                    "input_rows": 1_440,
                    "accepted_rows": 1_440,
                    "rejected_rows": 0,
                    "duplicate_rows": 0,
                    "checksum_verified": True,
                    "source_archive_sha256": actual_mark,
                    "alignment_consumer_dataset": DATASET,
                },
            )
        )
        maximum_alignment_deviation = max(
            maximum_alignment_deviation,
            max(
                float(observation["external_price_reference"]["deviation_ratio"])
                for observation in observations
            ),
        )
        all_observations.extend(observations)

    Path(store_path).parent.mkdir(parents=True, exist_ok=True)
    store = BinanceSystemicStore(store_path, create=True)
    try:
        inserted = store.append_many(all_observations)
        if not store.chain_valid():
            raise BinanceBookDepthFetchError("book-depth authority chain is invalid")
        total_rows = store.row_count(DATASET)
    finally:
        store.close()
    return {
        "schema_version": 1,
        "generated_at_ms": observed_at_ms,
        "status": "PASS",
        "mode": "L2_PUBLIC_BOOK_DEPTH_ARCHIVE",
        "dataset": DATASET,
        "instrument_count": len(selected_instruments),
        "day_count": len(selected_days),
        "accepted_archives": len(fetched),
        "archived_objects": len(archived),
        "inserted_hours": inserted,
        "authority_rows": total_rows,
        "quality_verdict": (
            "DEGRADED_BOUNDED_INVERTED_SNAPSHOT_QUARANTINE"
            if quarantined_snapshots
            else "PASS"
        ),
        "quarantined_snapshot_count": quarantined_snapshots,
        "knowledge_policy": "OBSERVED_AT_FETCH_NOT_RETROACTIVE",
        "formal_forward_credit": False,
        "external_price_alignment": "PASS",
        "maximum_price_alignment_deviation_ratio": maximum_alignment_deviation,
        "maximum_price_alignment_bound_ratio": 0.01,
        "price_alignment_calibration": "UNCALIBRATED_PRINCIPLED",
        "safety": {
            "paper_only": True,
            "credentials_accessed": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
        },
    }


def refresh_latest_available_days(
    instruments: Iterable[str],
    *,
    closed_day_ms: int,
    day_count: int,
    availability_lookback_days: int,
    observed_at_ms: int,
    store_path: str = DEFAULT_STORE,
    archive_root: str = DEFAULT_RAW_ARCHIVE,
    http_get_bytes: Callable[[str], bytes] = _http_get_bytes,
    max_workers: int = 4,
    max_attempts: int = 4,
    retry_sleep_seconds: float = 0.5,
    minimum_snapshots_per_archive: int = MINIMUM_SNAPSHOTS_PER_DAY,
    minimum_hourly_snapshots: int = MINIMUM_HOURLY_SNAPSHOTS,
    require_all_hours: bool = True,
    max_inverted_snapshot_ratio: float = MAX_INVERTED_SNAPSHOT_RATIO,
) -> dict[str, object]:
    """Publish the newest common consecutive archive window.

    Data Vision publication can lag the UTC close.  Only an explicit archive
    unavailability result may move the common cross-section backwards; any
    checksum, parsing, structural-quality, or non-404 transport failure remains
    a hard failure.  Every attempted day includes the full requested universe,
    so mixed-date cross-sections can never be admitted.
    """
    if (
        isinstance(day_count, bool)
        or not isinstance(day_count, int)
        or day_count < 1
        or isinstance(availability_lookback_days, bool)
        or not isinstance(availability_lookback_days, int)
        or availability_lookback_days < 0
        or availability_lookback_days > 7
    ):
        raise ValueError("archive availability policy is invalid")
    selected_instruments = tuple(instruments)
    for lag_days in range(availability_lookback_days + 1):
        latest_day = closed_day_ms - lag_days * DAY_MS
        selected_days = tuple(
            latest_day - offset * DAY_MS for offset in range(day_count)
        )
        try:
            report = refresh_book_depth_days(
                selected_instruments,
                selected_days,
                observed_at_ms=observed_at_ms,
                store_path=store_path,
                archive_root=archive_root,
                http_get_bytes=http_get_bytes,
                max_workers=max_workers,
                max_attempts=max_attempts,
                retry_sleep_seconds=retry_sleep_seconds,
                minimum_snapshots_per_archive=minimum_snapshots_per_archive,
                minimum_hourly_snapshots=minimum_hourly_snapshots,
                require_all_hours=require_all_hours,
                max_inverted_snapshot_ratio=max_inverted_snapshot_ratio,
            )
        except BinanceBookDepthArchiveUnavailable:
            continue
        return {
            **report,
            "archive_days_ms": list(sorted(selected_days)),
            "publication_lag_days": lag_days,
            "availability_lookback_days": availability_lookback_days,
        }
    raise BinanceBookDepthArchiveUnavailable(
        "no complete common book-depth archive window is available"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch checksum-verified Binance USD-M bookDepth archives"
    )
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--archive-root", default=DEFAULT_RAW_ARCHIVE)
    parser.add_argument("--status", default=DEFAULT_STATUS)
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--availability-lookback-days", type=int, default=3)
    parser.add_argument(
        "--instruments",
        default=",".join(f"{ticker}USDT" for ticker in FORWARD_MAJOR_TICKERS),
    )
    args = parser.parse_args(argv)
    if args.days < 1 or args.days > 90:
        parser.error("--days must be between 1 and 90")
    if args.availability_lookback_days < 0 or args.availability_lookback_days > 7:
        parser.error("--availability-lookback-days must be between 0 and 7")
    now_ms = int(time.time() * 1000)
    closed_day = (now_ms // DAY_MS) * DAY_MS - DAY_MS
    report = refresh_latest_available_days(
        tuple(part.strip() for part in args.instruments.split(",") if part.strip()),
        closed_day_ms=closed_day,
        day_count=args.days,
        availability_lookback_days=args.availability_lookback_days,
        observed_at_ms=now_ms,
        store_path=args.store,
        archive_root=args.archive_root,
        max_workers=args.workers,
    )
    atomic_write_json(args.status, report, indent=2)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BinanceBookDepthFetchError",
    "BinanceBookDepthArchiveUnavailable",
    "DEFAULT_RAW_ARCHIVE",
    "DEFAULT_STATUS",
    "DEFAULT_STORE",
    "EXPECTED_SNAPSHOTS_PER_DAY",
    "HOST",
    "MAX_INVERTED_SNAPSHOT_RATIO",
    "MINIMUM_HOURLY_SNAPSHOTS",
    "MINIMUM_SNAPSHOTS_PER_DAY",
    "book_depth_url",
    "mark_price_1m_url",
    "main",
    "refresh_latest_available_days",
    "refresh_book_depth_days",
]
