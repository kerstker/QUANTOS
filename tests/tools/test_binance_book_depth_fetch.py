from __future__ import annotations

import hashlib
import io
import urllib.error
import zipfile

import pytest

from services.data.binance_systemic_store import BinanceSystemicStore
from tools.binance_book_depth_fetch import (
    BinanceBookDepthArchiveUnavailable,
    BinanceBookDepthFetchError,
    book_depth_url,
    mark_price_1m_url,
    refresh_latest_available_days,
    refresh_book_depth_days,
)


DAY_MS = 1_786_320_000_000
OBSERVED_MS = DAY_MS + 2 * 86_400_000
LEVELS = (-5.0, -4.0, -3.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 3.0, 4.0, 5.0)


def _archive() -> bytes:
    rows = ["timestamp,percentage,depth,notional"]
    for level in LEVELS:
        depth = 100.0 * abs(level)
        price = 100.0 * (1.0 + level / 100.0)
        rows.append(
            f"2026-08-10 00:00:01,{level:.2f},{depth:.8f},{depth * price:.8f}"
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-bookDepth-2026-08-10.csv", "\n".join(rows))
    return buffer.getvalue()


def _mark_archive() -> bytes:
    rows = [
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_volume,taker_buy_quote_volume,ignore"
    ]
    for minute in range(1_440):
        open_time = DAY_MS + minute * 60_000
        rows.append(
            f"{open_time},100,100,100,100,0,{open_time + 59_999},0,60,0,0,0"
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-1m-2026-08-10.csv", "\n".join(rows))
    return buffer.getvalue()


def _successful_get(path: str, *, depth_raw: bytes, mark_raw: bytes) -> bytes:
    selected = mark_raw if "markPriceKlines" in path else depth_raw
    digest = hashlib.sha256(selected).hexdigest()
    return f"{digest}  file.zip\n".encode() if path.endswith(".CHECKSUM") else selected


def test_refresh_verifies_checksum_archives_source_and_is_idempotent(tmp_path) -> None:
    raw = _archive()
    mark_raw = _mark_archive()

    def get(path: str) -> bytes:
        return _successful_get(path, depth_raw=raw, mark_raw=mark_raw)

    database = tmp_path / "book_depth.db"
    report = refresh_book_depth_days(
        ("BTCUSDT",),
        (DAY_MS,),
        observed_at_ms=OBSERVED_MS,
        store_path=str(database),
        archive_root=str(tmp_path / "raw"),
        http_get_bytes=get,
        max_workers=1,
        minimum_snapshots_per_archive=1,
    )
    replay = refresh_book_depth_days(
        ("BTCUSDT",),
        (DAY_MS,),
        observed_at_ms=OBSERVED_MS + 1,
        store_path=str(database),
        archive_root=str(tmp_path / "raw"),
        http_get_bytes=get,
        max_workers=1,
        minimum_snapshots_per_archive=1,
    )

    assert report["status"] == "PASS"
    assert report["accepted_archives"] == 1
    assert report["inserted_hours"] == 1
    assert replay["inserted_hours"] == 0
    store = BinanceSystemicStore(str(database))
    try:
        row = store.latest(
            "binance_usdm_book_depth_curve_hourly_v2", "BTCUSDT"
        )
        assert row["knowledge_time_ms"] == OBSERVED_MS
        assert store.chain_valid() is True
    finally:
        store.close()
    assert any((tmp_path / "raw" / "observations" / "sha256").rglob("*.json"))


def test_refresh_rejects_checksum_mismatch_before_parsing(tmp_path) -> None:
    raw = _archive()
    mark_raw = _mark_archive()

    def get(path: str) -> bytes:
        if path.endswith(".CHECKSUM") and "bookDepth" in path:
            return ("0" * 64 + "  file.zip\n").encode()
        return _successful_get(path, depth_raw=raw, mark_raw=mark_raw)

    with pytest.raises(BinanceBookDepthFetchError, match="checksum mismatch"):
        refresh_book_depth_days(
            ("BTCUSDT",),
            (DAY_MS,),
            observed_at_ms=OBSERVED_MS,
            store_path=str(tmp_path / "book_depth.db"),
            archive_root=str(tmp_path / "raw"),
            http_get_bytes=get,
            max_workers=1,
            minimum_snapshots_per_archive=1,
        )


def test_refresh_rejects_mark_price_checksum_mismatch_before_alignment(tmp_path) -> None:
    raw = _archive()
    mark_raw = _mark_archive()

    def get(path: str) -> bytes:
        if path.endswith(".CHECKSUM") and "markPriceKlines" in path:
            return ("0" * 64 + "  file.zip\n").encode()
        return _successful_get(path, depth_raw=raw, mark_raw=mark_raw)

    with pytest.raises(BinanceBookDepthFetchError, match="mark-price checksum"):
        refresh_book_depth_days(
            ("BTCUSDT",),
            (DAY_MS,),
            observed_at_ms=OBSERVED_MS,
            store_path=str(tmp_path / "book_depth.db"),
            archive_root=str(tmp_path / "raw"),
            http_get_bytes=get,
            max_workers=1,
            minimum_snapshots_per_archive=1,
        )


def test_book_depth_url_is_fixed_to_public_usdm_archive() -> None:
    assert book_depth_url("BTCUSDT", DAY_MS) == (
        "/data/futures/um/daily/bookDepth/BTCUSDT/"
        "BTCUSDT-bookDepth-2026-08-10.zip"
    )
    assert mark_price_1m_url("BTCUSDT", DAY_MS) == (
        "/data/futures/um/daily/markPriceKlines/BTCUSDT/1m/"
        "BTCUSDT-1m-2026-08-10.zip"
    )


def test_latest_available_refresh_falls_back_only_on_archive_unavailable(tmp_path) -> None:
    raw = _archive()
    mark_raw = _mark_archive()

    def get(path: str) -> bytes:
        if "2026-08-11" in path:
            raise BinanceBookDepthArchiveUnavailable("not published")
        return _successful_get(path, depth_raw=raw, mark_raw=mark_raw)

    report = refresh_latest_available_days(
        ("BTCUSDT",),
        closed_day_ms=DAY_MS + 86_400_000,
        day_count=1,
        availability_lookback_days=3,
        observed_at_ms=OBSERVED_MS,
        store_path=str(tmp_path / "book_depth.db"),
        archive_root=str(tmp_path / "raw"),
        http_get_bytes=get,
        max_workers=1,
        minimum_snapshots_per_archive=1,
    )

    assert report["archive_days_ms"] == [DAY_MS]
    assert report["publication_lag_days"] == 1
    assert report["availability_lookback_days"] == 3


def test_latest_available_refresh_does_not_mask_checksum_failure(tmp_path) -> None:
    raw = _archive()
    mark_raw = _mark_archive()

    def get(path: str) -> bytes:
        if path.endswith(".CHECKSUM") and "bookDepth" in path:
            return ("0" * 64 + "  file.zip\n").encode()
        return _successful_get(path, depth_raw=raw, mark_raw=mark_raw)

    with pytest.raises(BinanceBookDepthFetchError, match="checksum mismatch"):
        refresh_latest_available_days(
            ("BTCUSDT",),
            closed_day_ms=DAY_MS,
            day_count=1,
            availability_lookback_days=3,
            observed_at_ms=OBSERVED_MS,
            store_path=str(tmp_path / "book_depth.db"),
            archive_root=str(tmp_path / "raw"),
            http_get_bytes=get,
            max_workers=1,
            minimum_snapshots_per_archive=1,
        )


def test_refresh_retries_transient_transport_failures_with_a_bound(tmp_path) -> None:
    raw = _archive()
    mark_raw = _mark_archive()
    attempts = {"archive": 0}

    def get(path: str) -> bytes:
        if path.endswith(".CHECKSUM"):
            return _successful_get(path, depth_raw=raw, mark_raw=mark_raw)
        if "markPriceKlines" in path:
            return mark_raw
        attempts["archive"] += 1
        if attempts["archive"] < 3:
            raise urllib.error.URLError(TimeoutError("temporary TLS timeout"))
        return raw

    report = refresh_book_depth_days(
        ("BTCUSDT",),
        (DAY_MS,),
        observed_at_ms=OBSERVED_MS,
        store_path=str(tmp_path / "book_depth.db"),
        archive_root=str(tmp_path / "raw"),
        http_get_bytes=get,
        max_workers=1,
        max_attempts=3,
        retry_sleep_seconds=0,
        minimum_snapshots_per_archive=1,
    )

    assert report["status"] == "PASS"
    assert attempts["archive"] == 3


def test_refresh_fails_after_bounded_transient_retries(tmp_path) -> None:
    attempts = {"count": 0}

    def get(_path: str) -> bytes:
        attempts["count"] += 1
        raise urllib.error.URLError(TimeoutError("temporary TLS timeout"))

    with pytest.raises(BinanceBookDepthFetchError, match="failed closed"):
        refresh_book_depth_days(
            ("BTCUSDT",),
            (DAY_MS,),
            observed_at_ms=OBSERVED_MS,
            store_path=str(tmp_path / "book_depth.db"),
            archive_root=str(tmp_path / "raw"),
            http_get_bytes=get,
            max_workers=1,
            max_attempts=3,
            retry_sleep_seconds=0,
            minimum_snapshots_per_archive=1,
        )

    assert attempts["count"] == 3
