from __future__ import annotations

import io
from datetime import datetime, timezone
import zipfile

import pytest

from services.data.binance_book_depth_archive import (
    BinanceBookDepthArchiveError,
    align_with_mark_price,
    parse_book_depth_archive,
    parse_mark_price_1m_archive,
)


DAY_MS = 1_786_320_000_000  # 2026-08-10 UTC
OBSERVED_MS = DAY_MS + 2 * 86_400_000
LEVELS = (-5.0, -4.0, -3.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 3.0, 4.0, 5.0)


def _archive(*, missing: float | None = None, inverted: bool = False) -> bytes:
    rows = ["timestamp,percentage,depth,notional"]
    for hour in (0, 1):
        for sample_second in (1, 31):
            for level in LEVELS:
                if level == missing:
                    continue
                radius = abs(level)
                depth = 100.0 * radius
                price = 100.0 * (1.0 + level / 100.0)
                if inverted:
                    price = 100.0 * (1.0 - level / 100.0)
                rows.append(
                    f"2026-08-10 {hour:02d}:00:{sample_second:02d},"
                    f"{level:.2f},{depth:.8f},{depth * price:.8f}"
                )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-bookDepth-2026-08-10.csv", "\n".join(rows))
    return buffer.getvalue()


def _archive_many(*, inverted_indices: set[int]) -> bytes:
    rows = ["timestamp,percentage,depth,notional"]
    for index in range(1_000):
        event_ms = DAY_MS + index * 30_000
        timestamp = datetime.fromtimestamp(
            event_ms / 1000, timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")
        for level in LEVELS:
            radius = abs(level)
            depth = 100.0 * radius
            price = 100.0 * (1.0 + level / 100.0)
            if index in inverted_indices:
                price = 100.0 * (1.0 - level / 100.0)
            rows.append(
                f"{timestamp},{level:.2f},{depth:.8f},{depth * price:.8f}"
            )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-bookDepth-2026-08-10.csv", "\n".join(rows))
    return buffer.getvalue()


def _mark_archive(*, missing_minute: int | None = None, price: float = 100.0) -> bytes:
    rows = [
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_volume,taker_buy_quote_volume,ignore"
    ]
    for minute in range(1_440):
        if minute == missing_minute:
            continue
        open_time = DAY_MS + minute * 60_000
        rows.append(
            f"{open_time},{price},{price},{price},{price},0,"
            f"{open_time + 59_999},0,60,0,0,0"
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-1m-2026-08-10.csv", "\n".join(rows))
    return buffer.getvalue()


def test_parse_archive_builds_hourly_bitemporal_observations() -> None:
    observations = parse_book_depth_archive(
        _archive(),
        instrument_id="BTCUSDT",
        expected_day_ms=DAY_MS,
        observed_at_ms=OBSERVED_MS,
        archive_sha256="a" * 64,
    )

    assert len(observations) == 2
    first = observations[0]
    assert first["dataset"] == (
        "binance_usdm_book_depth_curve_hourly_unaligned_staging"
    )
    assert first["event_time_ms"] == DAY_MS + 3_600_000
    assert first["knowledge_time_ms"] == OBSERVED_MS
    assert first["source_archive_sha256"] == "a" * 64
    assert first["snapshot_count"] == 2
    assert first["levels"]["0.2"]["bid_notional_p10"] > 0
    assert first["levels"]["0.2"]["ask_notional_p10"] > 0
    assert first["provenance"] == "RETROSPECTIVE_ARCHIVE_OBSERVED_AT_FETCH_V1"
    assert first["forward_eligible_from_ms"] == OBSERVED_MS


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (_archive(missing=0.2), "symmetric depth levels"),
        (_archive(inverted=True), "implicit bid/ask prices"),
    ],
)
def test_parse_archive_rejects_incomplete_or_inverted_snapshots(
    raw: bytes, message: str
) -> None:
    with pytest.raises(BinanceBookDepthArchiveError, match=message):
        parse_book_depth_archive(
            raw,
            instrument_id="BTCUSDT",
            expected_day_ms=DAY_MS,
            observed_at_ms=OBSERVED_MS,
            archive_sha256="b" * 64,
        )


def test_parse_archive_rejects_retroactive_knowledge_claim() -> None:
    with pytest.raises(BinanceBookDepthArchiveError, match="after the archived day"):
        parse_book_depth_archive(
            _archive(),
            instrument_id="BTCUSDT",
            expected_day_ms=DAY_MS,
            observed_at_ms=DAY_MS + 86_400_000 - 1,
            archive_sha256="c" * 64,
        )


def test_parse_archive_quarantines_one_bounded_inverted_snapshot() -> None:
    observations = parse_book_depth_archive(
        _archive_many(inverted_indices={500}),
        instrument_id="BTCUSDT",
        expected_day_ms=DAY_MS,
        observed_at_ms=OBSERVED_MS,
        archive_sha256="d" * 64,
        minimum_snapshot_count=1_000,
        max_inverted_snapshot_ratio=0.001,
    )

    assert sum(item["snapshot_count"] for item in observations) == 999
    assert observations[0]["archive_snapshot_count"] == 1_000
    assert observations[0]["archive_quarantined_snapshot_count"] == 1
    assert observations[0]["quality_verdict"] == (
        "DEGRADED_BOUNDED_INVERTED_SNAPSHOT_QUARANTINE"
    )
    assert observations[0]["external_price_alignment"] == "UNASSESSED"
    assert observations[0]["quarantine_policy"]["event_times_ms"] == (
        DAY_MS + 500 * 30_000,
    )


def test_parse_archive_rejects_inversion_above_quarantine_bound() -> None:
    with pytest.raises(BinanceBookDepthArchiveError, match="bounded quarantine"):
        parse_book_depth_archive(
            _archive_many(inverted_indices={500, 501}),
            instrument_id="BTCUSDT",
            expected_day_ms=DAY_MS,
            observed_at_ms=OBSERVED_MS,
            archive_sha256="e" * 64,
            minimum_snapshot_count=1_000,
            max_inverted_snapshot_ratio=0.001,
        )


def test_parse_mark_archive_and_align_every_depth_hour() -> None:
    observations = parse_book_depth_archive(
        _archive(),
        instrument_id="BTCUSDT",
        expected_day_ms=DAY_MS,
        observed_at_ms=OBSERVED_MS,
        archive_sha256="f" * 64,
    )
    references = parse_mark_price_1m_archive(
        _mark_archive(),
        instrument_id="BTCUSDT",
        expected_day_ms=DAY_MS,
    )
    aligned = align_with_mark_price(
        observations,
        references,
        mark_archive_sha256="1" * 64,
    )

    assert len(references) == 24
    assert aligned[0]["external_price_alignment"] == "PASS"
    assert aligned[0]["dataset"] == "binance_usdm_book_depth_curve_hourly_v2"
    assert aligned[0]["external_price_reference"]["deviation_ratio"] == 0.0
    assert aligned[0]["external_price_reference"]["source_archive_sha256"] == (
        "1" * 64
    )


def test_parse_mark_archive_rejects_incomplete_minute_grid() -> None:
    with pytest.raises(BinanceBookDepthArchiveError, match="exact completed 1m"):
        parse_mark_price_1m_archive(
            _mark_archive(missing_minute=500),
            instrument_id="BTCUSDT",
            expected_day_ms=DAY_MS,
        )


def test_alignment_rejects_midpoint_outside_principled_bound() -> None:
    observations = parse_book_depth_archive(
        _archive(),
        instrument_id="BTCUSDT",
        expected_day_ms=DAY_MS,
        observed_at_ms=OBSERVED_MS,
        archive_sha256="2" * 64,
    )
    references = parse_mark_price_1m_archive(
        _mark_archive(price=90.0),
        instrument_id="BTCUSDT",
        expected_day_ms=DAY_MS,
    )

    with pytest.raises(BinanceBookDepthArchiveError, match="alignment bound"):
        align_with_mark_price(
            observations,
            references,
            mark_archive_sha256="3" * 64,
        )


def test_archive_requires_all_hours_and_minimum_hourly_coverage() -> None:
    with pytest.raises(BinanceBookDepthArchiveError, match="all 24 UTC hours"):
        parse_book_depth_archive(
            _archive(),
            instrument_id="BTCUSDT",
            expected_day_ms=DAY_MS,
            observed_at_ms=OBSERVED_MS,
            archive_sha256="4" * 64,
            require_all_hours=True,
        )
    with pytest.raises(BinanceBookDepthArchiveError, match="minimum accepted"):
        parse_book_depth_archive(
            _archive(),
            instrument_id="BTCUSDT",
            expected_day_ms=DAY_MS,
            observed_at_ms=OBSERVED_MS,
            archive_sha256="5" * 64,
            minimum_hourly_snapshot_count=3,
        )
