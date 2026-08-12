"""Quality-only diagnostics for checksum-verified Binance book-depth archives.

The diagnostic never loads mark prices, candidate signals, or returns.  It is
used to decide whether daily and hourly coverage gates are logically distinct
before any confirmation outcome is accessed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from collections.abc import Callable, Iterable, Sequence

from services.data.binance_book_depth_archive import parse_book_depth_archive
from tools.binance_book_depth_fetch import (
    MAX_INVERTED_SNAPSHOT_RATIO,
    MINIMUM_HOURLY_SNAPSHOTS,
    _checksum,
    _http_get_bytes,
    book_depth_url,
)
from tools.binance_oi_metrics_fetch import FORWARD_MAJOR_TICKERS
from tools.cache_io import atomic_write_json


UNIVERSE = tuple(f"{ticker}USDT" for ticker in FORWARD_MAJOR_TICKERS)


def diagnose_archive(
    *,
    instrument_id: str,
    day_ms: int,
    observed_at_ms: int,
    http_get_bytes: Callable[[str], bytes] = _http_get_bytes,
) -> dict[str, object]:
    path = book_depth_url(instrument_id, day_ms)
    raw = http_get_bytes(path)
    expected = _checksum(http_get_bytes(path + ".CHECKSUM"))
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise RuntimeError("book-depth diagnostic checksum mismatch")
    observations = parse_book_depth_archive(
        raw,
        instrument_id=instrument_id,
        expected_day_ms=day_ms,
        observed_at_ms=observed_at_ms,
        archive_sha256=actual,
        minimum_snapshot_count=1,
        minimum_hourly_snapshot_count=1,
        require_all_hours=False,
        max_inverted_snapshot_ratio=MAX_INVERTED_SNAPSHOT_RATIO,
    )
    hourly_counts = tuple(int(row["snapshot_count"]) for row in observations)
    return {
        "day_ms": day_ms,
        "instrument_id": instrument_id,
        "checksum_verified": True,
        "archive_sha256": actual,
        "archive_snapshot_count": int(
            observations[0]["archive_snapshot_count"]
        ),
        "hour_count": len(observations),
        "minimum_hour_snapshot_count": min(hourly_counts),
        "hours_below_hourly_floor": sum(
            count < MINIMUM_HOURLY_SNAPSHOTS for count in hourly_counts
        ),
        "confirmation_outcome_accessed": False,
    }


def diagnose_days(
    *,
    days: Iterable[int],
    observed_at_ms: int,
    universe: Sequence[str] = UNIVERSE,
    max_workers: int = 4,
    http_get_bytes: Callable[[str], bytes] = _http_get_bytes,
) -> dict[str, object]:
    selected_days = tuple(sorted(set(int(day) for day in days)))
    selected_universe = tuple(sorted(set(str(value) for value in universe)))
    if not selected_days or not selected_universe:
        raise ValueError("quality diagnostic requires days and instruments")
    identities = tuple(
        (instrument_id, day_ms)
        for day_ms in selected_days
        for instrument_id in selected_universe
    )

    def run(identity: tuple[str, int]) -> dict[str, object]:
        return diagnose_archive(
            instrument_id=identity[0],
            day_ms=identity[1],
            observed_at_ms=observed_at_ms,
            http_get_bytes=http_get_bytes,
        )

    if max_workers == 1:
        archives = tuple(run(identity) for identity in identities)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            archives = tuple(executor.map(run, identities))
    day_reports = []
    for day_ms in selected_days:
        rows = tuple(row for row in archives if row["day_ms"] == day_ms)
        day_reports.append(
            {
                "day_ms": day_ms,
                "instrument_count": len(rows),
                "minimum_archive_snapshot_count": min(
                    int(row["archive_snapshot_count"]) for row in rows
                ),
                "maximum_archive_snapshot_count": max(
                    int(row["archive_snapshot_count"]) for row in rows
                ),
                "minimum_hour_snapshot_count": min(
                    int(row["minimum_hour_snapshot_count"]) for row in rows
                ),
                "instruments_missing_any_hour": sum(
                    int(row["hour_count"]) < 24 for row in rows
                ),
                "instruments_with_hour_below_floor": sum(
                    int(row["hours_below_hourly_floor"]) > 0 for row in rows
                ),
                "total_hours_below_floor": sum(
                    int(row["hours_below_hourly_floor"]) for row in rows
                ),
            }
        )
    return {
        "schema_version": 1,
        "mode": "L2_PUBLIC_ARCHIVE_QUALITY_ONLY",
        "observed_at_ms": observed_at_ms,
        "day_count": len(selected_days),
        "instrument_count": len(selected_universe),
        "hourly_floor": MINIMUM_HOURLY_SNAPSHOTS,
        "days": day_reports,
        "archives": archives,
        "mark_prices_accessed": False,
        "candidate_signals_accessed": False,
        "candidate_returns_accessed": False,
        "confirmation_outcome_accessed": False,
        "safety": {
            "public_market_data_only": True,
            "credentials_accessed": False,
            "broker_connectivity": False,
            "order_submission": False,
            "live_trading": False,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day-ms", type=int, action="append", required=True)
    parser.add_argument("--observed-at-ms", type=int, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    result = diagnose_days(
        days=args.day_ms,
        observed_at_ms=args.observed_at_ms,
        max_workers=args.workers,
    )
    if args.output:
        atomic_write_json(args.output, result, indent=2)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["diagnose_archive", "diagnose_days", "main"]
