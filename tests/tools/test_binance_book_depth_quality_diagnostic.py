from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import zipfile
import pytest

from services.data.binance_book_depth_archive import DAY_MS
from tools import binance_book_depth_quality_diagnostic as diagnostic
from tools.binance_book_depth_fetch import book_depth_url


DAY = 1_786_320_000_000


def test_rejected_archive_is_counted_without_hiding_other_instruments():
    good = _archive('ETHUSDT', (2,) * 24)
    bad = _archive('BTCUSDT', (2,) * 24)
    with zipfile.ZipFile(io.BytesIO(bad)) as source:
        name = source.namelist()[0]
        rows = list(csv.DictReader(io.StringIO(source.read(name).decode())))
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=('timestamp', 'percentage', 'depth', 'notional'))
    writer.writeheader()
    for row in rows:
        if float(row['percentage']) < 0:
            row['notional'] = str(float(row['depth']) * 102)
        writer.writerow(row)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(name, stream.getvalue())
    archives = {book_depth_url('BTCUSDT', DAY): buffer.getvalue(), book_depth_url('ETHUSDT', DAY): good}
    def get(path):
        if path.endswith('.CHECKSUM'):
            return (hashlib.sha256(archives[path[:-9]]).hexdigest() + ' archive.zip').encode()
        return archives[path]
    report = diagnostic.diagnose_days(days=(DAY,), observed_at_ms=DAY+DAY_MS+1,
        universe=('BTCUSDT', 'ETHUSDT'), http_get_bytes=get, max_workers=1)
    assert report['status'] == 'FAIL'
    assert report['rejected_archive_count'] == 1
    assert report['days'][0]['rejected_instrument_count'] == 1
    rejected = report['archives'][0]
    assert rejected['checksum_verified'] is True
    assert rejected['reason_code'] == 'INVERTED_SNAPSHOT_LIMIT_EXCEEDED'
    assert rejected['quality_details']['quarantined_snapshot_count'] == 48
    assert rejected['quality_details']['archive_snapshot_count'] == 48
    assert report['archives'][1]['status'] == 'PASS'
    assert report['mark_prices_accessed'] is False


def test_checksum_mismatch_still_raises():
    raw = _archive('BTCUSDT', (1,))
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        diagnostic.diagnose_archive(instrument_id='BTCUSDT', day_ms=DAY,
            observed_at_ms=DAY+DAY_MS+1,
            http_get_bytes=lambda path: b'0'*64+b' archive.zip' if path.endswith('.CHECKSUM') else raw)


def _archive(symbol: str, snapshots_by_hour: tuple[int, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(("timestamp", "percentage", "depth", "notional"))
    for hour, count in enumerate(snapshots_by_hour):
        for index in range(count):
            timestamp = datetime.fromtimestamp(
                (DAY + hour * 3_600_000 + index * 1_000) / 1_000,
                tz=timezone.utc,
            ).strftime("%Y-%m-%d %H:%M:%S")
            for level in (-0.2, -1, -2, -3, -4, -5, 0.2, 1, 2, 3, 4, 5):
                depth = 100 + abs(level)
                price = 99 if level < 0 else 101
                writer.writerow((timestamp, level, depth, depth * price))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            f"{symbol}-bookDepth-"
            f"{datetime.fromtimestamp(DAY / 1_000, timezone.utc):%Y-%m-%d}.csv",
            stream.getvalue(),
        )
    return buffer.getvalue()


def _mark_archive(symbol: str, price: float = 100.0) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        (
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "count", "taker_buy_volume",
            "taker_buy_quote_volume", "ignore",
        )
    )
    for minute in range(1_440):
        open_time = DAY + minute * 60_000
        writer.writerow(
            (
                open_time, price, price, price, price, 0,
                open_time + 59_999, 0, 0, 0, 0, 0,
            )
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            f"{symbol}-1m-"
            f"{datetime.fromtimestamp(DAY / 1_000, timezone.utc):%Y-%m-%d}.csv",
            stream.getvalue(),
        )
    return buffer.getvalue()


def test_quality_diagnostic_reports_daily_and_hourly_coverage_separately() -> None:
    symbol = "BTCUSDT"
    raw = _archive(symbol, (2,) * 23 + (1,))
    digest = hashlib.sha256(raw).hexdigest()
    path = book_depth_url(symbol, DAY)

    def get(requested: str) -> bytes:
        if requested == path:
            return raw
        if requested == path + ".CHECKSUM":
            return f"{digest}  archive.zip\n".encode()
        raise AssertionError(requested)

    report = diagnostic.diagnose_days(
        days=(DAY,),
        observed_at_ms=DAY + DAY_MS + 1,
        universe=(symbol,),
        max_workers=1,
        http_get_bytes=get,
    )

    assert report["confirmation_outcome_accessed"] is False
    assert report["mark_prices_accessed"] is False
    assert report["candidate_signals_accessed"] is False
    assert report["candidate_returns_accessed"] is False
    assert report["safety"]["credentials_accessed"] is False
    day = report["days"][0]
    assert day["minimum_archive_snapshot_count"] == 47
    assert day["instruments_missing_any_hour"] == 0
    assert day["minimum_hour_snapshot_count"] == 1
    assert day["instruments_with_hour_below_floor"] == 1
    assert day["total_hours_below_floor"] == 24


def test_quality_diagnostic_can_measure_mark_alignment_without_candidate_data() -> None:
    symbol = "BTCUSDT"
    raw = _archive(symbol, (1,) * 24)
    mark_raw = _mark_archive(symbol)
    depth_digest = hashlib.sha256(raw).hexdigest()
    mark_digest = hashlib.sha256(mark_raw).hexdigest()
    depth_path = book_depth_url(symbol, DAY)
    from tools.binance_book_depth_fetch import mark_price_1m_url

    mark_path = mark_price_1m_url(symbol, DAY)

    def get(requested: str) -> bytes:
        values = {
            depth_path: raw,
            depth_path + ".CHECKSUM": f"{depth_digest} file.zip\n".encode(),
            mark_path: mark_raw,
            mark_path + ".CHECKSUM": f"{mark_digest} file.zip\n".encode(),
        }
        return values[requested]

    report = diagnostic.diagnose_days(
        days=(DAY,),
        observed_at_ms=DAY + DAY_MS + 1,
        universe=(symbol,),
        max_workers=1,
        http_get_bytes=get,
        include_mark_alignment=True,
    )

    assert report["mark_prices_accessed"] is True
    assert report["candidate_signals_accessed"] is False
    assert report["candidate_returns_accessed"] is False
    row = report["archives"][0]
    assert row["mark_price_checksum_verified"] is True
    assert row["hours_above_mark_alignment_bound"] == 0
    assert len(row["hourly_mark_alignment"]) == 24
