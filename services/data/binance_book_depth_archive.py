"""Offline parser for checksum-verified Binance USD-M book-depth archives.

Historical Data Vision files are retrospective deliveries.  Their event time
describes the exchange snapshot, while ``knowledge_time_ms`` is always the
actual fetch time.  This module never backdates knowledge and has no network,
credential, account, broker, or order capability.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import math
import statistics
import zipfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


DAY_MS = 86_400_000
HOUR_MS = 3_600_000
DATASET = "binance_usdm_book_depth_curve_hourly_v2"
STAGING_DATASET = "binance_usdm_book_depth_curve_hourly_unaligned_staging"
DEPTH_RADII = (0.2, 1.0, 2.0, 3.0, 4.0, 5.0)
_EXPECTED_LEVELS = frozenset((*(-value for value in DEPTH_RADII), *DEPTH_RADII))


class BinanceBookDepthArchiveError(ValueError):
    """One archive cannot be admitted without fabricating depth evidence."""


class _InvertedSnapshotError(BinanceBookDepthArchiveError):
    """One otherwise-structural snapshot contains a crossed implicit curve."""


def _finite_positive(value: object, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BinanceBookDepthArchiveError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or result <= 0:
        raise BinanceBookDepthArchiveError(f"{field} must be positive and finite")
    return result


def _sha256_text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value.lower() != value
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise BinanceBookDepthArchiveError(f"{field} is invalid")
    return value


def _event_time_ms(value: object) -> int:
    if not isinstance(value, str):
        raise BinanceBookDepthArchiveError("timestamp is invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise BinanceBookDepthArchiveError("timestamp is invalid") from exc
    return int(parsed.timestamp() * 1000)


def _nearest_rank(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise BinanceBookDepthArchiveError("hourly depth sample is empty")
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


def _radius_key(radius: float) -> str:
    return str(int(radius)) if radius.is_integer() else str(radius)


def _validate_snapshot(
    rows: Mapping[float, tuple[float, float]],
) -> dict[float, dict[str, float]]:
    if frozenset(rows) != _EXPECTED_LEVELS:
        raise BinanceBookDepthArchiveError(
            "snapshot does not contain the exact symmetric depth levels"
        )
    result: dict[float, dict[str, float]] = {}
    previous_bid_depth = previous_ask_depth = 0.0
    previous_bid_notional = previous_ask_notional = 0.0
    for radius in DEPTH_RADII:
        bid_depth, bid_notional = rows[-radius]
        ask_depth, ask_notional = rows[radius]
        if (
            bid_depth < previous_bid_depth
            or ask_depth < previous_ask_depth
            or bid_notional < previous_bid_notional
            or ask_notional < previous_ask_notional
        ):
            raise BinanceBookDepthArchiveError(
                "cumulative depth is not monotonic by radius"
            )
        bid_price = bid_notional / bid_depth
        ask_price = ask_notional / ask_depth
        if not 0 < bid_price < ask_price:
            raise _InvertedSnapshotError(
                "implicit bid/ask prices are inverted"
            )
        result[radius] = {
            "bid_depth": bid_depth,
            "bid_notional": bid_notional,
            "ask_depth": ask_depth,
            "ask_notional": ask_notional,
            "bid_implicit_price": bid_price,
            "ask_implicit_price": ask_price,
        }
        previous_bid_depth, previous_ask_depth = bid_depth, ask_depth
        previous_bid_notional, previous_ask_notional = bid_notional, ask_notional
    return result


def parse_book_depth_archive(
    raw: bytes,
    *,
    instrument_id: str,
    expected_day_ms: int,
    observed_at_ms: int,
    archive_sha256: str,
    minimum_snapshot_count: int = 1,
    minimum_hourly_snapshot_count: int = 1,
    require_all_hours: bool = False,
    max_inverted_snapshot_ratio: float = 0.0,
) -> tuple[dict[str, Any], ...]:
    """Parse one verified daily ZIP into deterministic hourly curve summaries."""
    symbol = str(instrument_id).upper().strip()
    if not symbol.endswith("USDT"):
        raise BinanceBookDepthArchiveError("instrument_id must be a USDT symbol")
    if (
        isinstance(expected_day_ms, bool)
        or not isinstance(expected_day_ms, int)
        or expected_day_ms < 0
        or expected_day_ms % DAY_MS
    ):
        raise BinanceBookDepthArchiveError("expected day is invalid")
    if (
        isinstance(observed_at_ms, bool)
        or not isinstance(observed_at_ms, int)
        or observed_at_ms < expected_day_ms + DAY_MS
    ):
        raise BinanceBookDepthArchiveError(
            "knowledge time must be after the archived day closes"
        )
    digest = _sha256_text(archive_sha256, "archive_sha256")
    if (
        isinstance(minimum_snapshot_count, bool)
        or not isinstance(minimum_snapshot_count, int)
        or minimum_snapshot_count < 1
        or isinstance(minimum_hourly_snapshot_count, bool)
        or not isinstance(minimum_hourly_snapshot_count, int)
        or minimum_hourly_snapshot_count < 1
        or not isinstance(require_all_hours, bool)
        or isinstance(max_inverted_snapshot_ratio, bool)
        or not isinstance(max_inverted_snapshot_ratio, (int, float))
        or not math.isfinite(float(max_inverted_snapshot_ratio))
        or not 0 <= float(max_inverted_snapshot_ratio) <= 0.002
    ):
        raise BinanceBookDepthArchiveError("snapshot quality policy is invalid")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = [name for name in archive.namelist() if name.endswith(".csv")]
            expected_name = (
                f"{symbol}-bookDepth-"
                f"{datetime.fromtimestamp(expected_day_ms / 1000, timezone.utc):%Y-%m-%d}.csv"
            )
            if names != [expected_name]:
                raise BinanceBookDepthArchiveError(
                    "archive must contain the exact expected CSV"
                )
            text = archive.read(names[0]).decode("utf-8-sig")
    except BinanceBookDepthArchiveError:
        raise
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise BinanceBookDepthArchiveError("book-depth ZIP is invalid") from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != ["timestamp", "percentage", "depth", "notional"]:
        raise BinanceBookDepthArchiveError("book-depth CSV header is invalid")
    snapshots: dict[int, dict[float, tuple[float, float]]] = defaultdict(dict)
    for row_number, row in enumerate(reader, start=2):
        event_time = _event_time_ms(row.get("timestamp"))
        if not expected_day_ms <= event_time < expected_day_ms + DAY_MS:
            raise BinanceBookDepthArchiveError("snapshot is outside the expected UTC day")
        try:
            percentage = float(row.get("percentage", ""))
        except (TypeError, ValueError) as exc:
            raise BinanceBookDepthArchiveError("percentage is not numeric") from exc
        if percentage not in _EXPECTED_LEVELS:
            raise BinanceBookDepthArchiveError(
                f"row {row_number} has an unsupported percentage level"
            )
        if percentage in snapshots[event_time]:
            raise BinanceBookDepthArchiveError("snapshot contains a duplicate level")
        snapshots[event_time][percentage] = (
            _finite_positive(row.get("depth"), "depth"),
            _finite_positive(row.get("notional"), "notional"),
        )
    if not snapshots:
        raise BinanceBookDepthArchiveError("book-depth CSV has no observations")
    if len(snapshots) < minimum_snapshot_count:
        raise BinanceBookDepthArchiveError(
            "book-depth archive is below the minimum snapshot count"
        )

    hourly: dict[int, list[dict[float, dict[str, float]]]] = defaultdict(list)
    quarantined_by_hour: dict[int, list[int]] = defaultdict(list)
    for event_time, rows in sorted(snapshots.items()):
        hour_start = (event_time // HOUR_MS) * HOUR_MS
        try:
            hourly[hour_start].append(_validate_snapshot(rows))
        except _InvertedSnapshotError:
            quarantined_by_hour[hour_start].append(event_time)
    quarantined_count = sum(len(values) for values in quarantined_by_hour.values())
    if quarantined_count / len(snapshots) > float(max_inverted_snapshot_ratio):
        raise BinanceBookDepthArchiveError(
            "implicit bid/ask prices inversion exceeds bounded quarantine policy"
        )
    if not hourly:
        raise BinanceBookDepthArchiveError(
            "all book-depth snapshots were quarantined"
        )
    expected_hours = {
        expected_day_ms + hour * HOUR_MS for hour in range(24)
    }
    if require_all_hours and set(hourly) != expected_hours:
        raise BinanceBookDepthArchiveError(
            "book-depth archive does not cover all 24 UTC hours"
        )
    if any(
        len(samples) < minimum_hourly_snapshot_count
        for samples in hourly.values()
    ):
        raise BinanceBookDepthArchiveError(
            "book-depth hour is below the minimum accepted snapshot count"
        )

    observations: list[dict[str, Any]] = []
    for hour_start, samples in sorted(hourly.items()):
        level_summary: dict[str, dict[str, float]] = {}
        midpoint_estimates: list[float] = []
        for radius in DEPTH_RADII:
            entries = [sample[radius] for sample in samples]
            bid_prices = [entry["bid_implicit_price"] for entry in entries]
            ask_prices = [entry["ask_implicit_price"] for entry in entries]
            midpoint_estimates.extend(
                (
                    bid / (1.0 - radius / 100.0)
                    + ask / (1.0 + radius / 100.0)
                )
                / 2.0
                for bid, ask in zip(bid_prices, ask_prices, strict=True)
            )
            level_summary[_radius_key(radius)] = {
                "bid_depth_p10": _nearest_rank(
                    [entry["bid_depth"] for entry in entries], 0.10
                ),
                "ask_depth_p10": _nearest_rank(
                    [entry["ask_depth"] for entry in entries], 0.10
                ),
                "bid_notional_p10": _nearest_rank(
                    [entry["bid_notional"] for entry in entries], 0.10
                ),
                "ask_notional_p10": _nearest_rank(
                    [entry["ask_notional"] for entry in entries], 0.10
                ),
                "bid_implicit_price_median": statistics.median(bid_prices),
                "ask_implicit_price_median": statistics.median(ask_prices),
            }
        observations.append(
            {
                "schema_version": 1,
                "provider": "BINANCE_DATA_VISION_PUBLIC_USDM",
                "dataset": STAGING_DATASET,
                "instrument_id": symbol,
                "event_time_ms": min(hour_start + HOUR_MS, expected_day_ms + DAY_MS),
                "knowledge_time_ms": observed_at_ms,
                "snapshot_count": len(samples),
                "archive_snapshot_count": len(snapshots),
                "archive_snapshot_coverage_ratio": len(snapshots) / 2_880.0,
                "archive_quarantined_snapshot_count": quarantined_count,
                "hour_quarantined_snapshot_count": len(
                    quarantined_by_hour.get(hour_start, ())
                ),
                "quality_verdict": (
                    "DEGRADED_BOUNDED_INVERTED_SNAPSHOT_QUARANTINE"
                    if quarantined_count
                    else "PASS"
                ),
                "coverage_policy": {
                    "minimum_archive_snapshot_count": minimum_snapshot_count,
                    "minimum_hourly_accepted_snapshot_count": (
                        minimum_hourly_snapshot_count
                    ),
                    "all_24_utc_hours_required": require_all_hours,
                    "expected_nominal_snapshot_count": 2_880,
                    "calibration": "UNCALIBRATED_PRINCIPLED_P10_COVERAGE",
                },
                "external_price_alignment": "UNASSESSED",
                "quarantine_policy": {
                    "reason": "IMPLICIT_BID_ASK_INVERSION",
                    "maximum_ratio": float(max_inverted_snapshot_ratio),
                    "archive_ratio": quarantined_count / len(snapshots),
                    "event_times_ms": tuple(
                        sorted(
                            event_time
                            for event_times in quarantined_by_hour.values()
                            for event_time in event_times
                        )
                    ),
                },
                "levels": level_summary,
                "implicit_mid_price_median": statistics.median(midpoint_estimates),
                "source_archive_sha256": digest,
                "provenance": "RETROSPECTIVE_ARCHIVE_OBSERVED_AT_FETCH_V1",
                "forward_eligible_from_ms": observed_at_ms,
                "historical_events_are_not_forward_vintages": True,
            }
        )
    return tuple(observations)


def parse_mark_price_1m_archive(
    raw: bytes,
    *,
    instrument_id: str,
    expected_day_ms: int,
) -> dict[int, float]:
    """Parse one exact completed-day 1m mark-price archive by hour end."""
    symbol = str(instrument_id).upper().strip()
    if not symbol.endswith("USDT"):
        raise BinanceBookDepthArchiveError("instrument_id must be a USDT symbol")
    day = datetime.fromtimestamp(expected_day_ms / 1000, timezone.utc)
    expected_name = f"{symbol}-1m-{day:%Y-%m-%d}.csv"
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = [name for name in archive.namelist() if name.endswith(".csv")]
            if names != [expected_name]:
                raise BinanceBookDepthArchiveError(
                    "mark-price archive must contain the exact expected CSV"
                )
            text = archive.read(names[0]).decode("utf-8-sig")
    except BinanceBookDepthArchiveError:
        raise
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise BinanceBookDepthArchiveError("mark-price ZIP is invalid") from exc

    reader = csv.DictReader(io.StringIO(text))
    expected_header = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "count", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore",
    ]
    if reader.fieldnames != expected_header:
        raise BinanceBookDepthArchiveError("mark-price CSV header is invalid")
    closes_by_hour: dict[int, list[float]] = defaultdict(list)
    seen_open_times: set[int] = set()
    for row_number, row in enumerate(reader, start=2):
        try:
            open_time = int(row["open_time"])
            close_time = int(row["close_time"])
            open_price = _finite_positive(row["open"], "mark open")
            high = _finite_positive(row["high"], "mark high")
            low = _finite_positive(row["low"], "mark low")
            close = _finite_positive(row["close"], "mark close")
        except (KeyError, TypeError, ValueError) as exc:
            raise BinanceBookDepthArchiveError(
                f"mark-price row {row_number} is invalid"
            ) from exc
        expected_open = expected_day_ms + (row_number - 2) * 60_000
        if open_time != expected_open or close_time != open_time + 59_999:
            raise BinanceBookDepthArchiveError(
                "mark-price rows must form the exact completed 1m UTC grid"
            )
        if open_time in seen_open_times:
            raise BinanceBookDepthArchiveError("mark-price open_time is duplicated")
        if high < max(open_price, close, low) or low > min(open_price, close, high):
            raise BinanceBookDepthArchiveError("mark-price OHLC ordering is invalid")
        seen_open_times.add(open_time)
        hour_end = ((open_time // HOUR_MS) + 1) * HOUR_MS
        closes_by_hour[hour_end].append(close)
    if len(seen_open_times) != 1_440:
        raise BinanceBookDepthArchiveError(
            "mark-price archive must contain exactly 1440 completed minutes"
        )
    if set(closes_by_hour) != {
        expected_day_ms + hour * HOUR_MS for hour in range(1, 25)
    } or any(len(values) != 60 for values in closes_by_hour.values()):
        raise BinanceBookDepthArchiveError(
            "mark-price archive must contain exactly 60 minutes per hour"
        )
    return {
        hour_end: statistics.median(values)
        for hour_end, values in sorted(closes_by_hour.items())
    }


def align_with_mark_price(
    observations: Sequence[Mapping[str, Any]],
    hourly_mark_prices: Mapping[int, float],
    *,
    mark_archive_sha256: str,
    maximum_deviation_ratio: float = 0.01,
) -> tuple[dict[str, Any], ...]:
    """Bind every hourly curve to an independent official mark-price hour."""
    mark_digest = _sha256_text(mark_archive_sha256, "mark_archive_sha256")
    if (
        isinstance(maximum_deviation_ratio, bool)
        or not isinstance(maximum_deviation_ratio, (int, float))
        or not math.isfinite(float(maximum_deviation_ratio))
        or not 0 < float(maximum_deviation_ratio) <= 0.01
    ):
        raise BinanceBookDepthArchiveError("price-alignment policy is invalid")
    if not observations:
        raise BinanceBookDepthArchiveError("price alignment has no depth observations")
    aligned: list[dict[str, Any]] = []
    for observation in observations:
        event_time = int(observation["event_time_ms"])
        try:
            reference = _finite_positive(
                hourly_mark_prices[event_time], "hourly mark reference"
            )
            implicit_mid = _finite_positive(
                observation["implicit_mid_price_median"], "implicit midpoint"
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BinanceBookDepthArchiveError(
                "price alignment is missing an hourly mark reference"
            ) from exc
        deviation = abs(implicit_mid - reference) / reference
        if deviation > float(maximum_deviation_ratio):
            raise BinanceBookDepthArchiveError(
                "implicit midpoint exceeds the mark-price alignment bound"
            )
        aligned.append(
            {
                **dict(observation),
                "dataset": DATASET,
                "external_price_alignment": "PASS",
                "external_price_reference": {
                    "dataset": "BINANCE_DATA_VISION_USDM_MARK_PRICE_KLINES_1M",
                    "hourly_statistic": "MEDIAN_60_COMPLETED_1M_CLOSES",
                    "reference_price": reference,
                    "deviation_ratio": deviation,
                    "maximum_deviation_ratio": float(maximum_deviation_ratio),
                    "calibration": "UNCALIBRATED_PRINCIPLED",
                    "source_archive_sha256": mark_digest,
                },
            }
        )
    return tuple(aligned)


__all__ = [
    "BinanceBookDepthArchiveError",
    "DATASET",
    "DAY_MS",
    "DEPTH_RADII",
    "HOUR_MS",
    "STAGING_DATASET",
    "align_with_mark_price",
    "parse_book_depth_archive",
    "parse_mark_price_1m_archive",
]
