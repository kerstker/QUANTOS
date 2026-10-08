import io
import json
import zipfile
from urllib.error import URLError

import pytest

from services.data.raw_archive import summarize_archive
from tools import binance_perp_mark_fetch as mark

_DAY = 86_400_000
_NOW = 21 * _DAY


def _zip_for(day_ms, close=100.0):
    date = mark.day_string(day_ms)
    text = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_volume,taker_buy_quote_volume,ignore\n"
        f"{day_ms},{close - 1},{close + 1},{close - 2},{close},0,"
        f"{day_ms + _DAY - 1},0,86400,0,0,0\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(f"BTCUSDT-1d-{date}.csv", text)
    return buf.getvalue()


def test_mark_url_and_parser_are_day_bound_and_strict():
    day = 20 * _DAY
    assert mark.mark_price_url("BTC", day).endswith(
        f"/BTCUSDT/1d/BTCUSDT-1d-{mark.day_string(day)}.zip"
    )
    assert mark.parse_mark_price_zip(_zip_for(day), expected_day=day) == 100.0

    with pytest.raises(mark.MarkPriceDataError, match="expected day"):
        mark.parse_mark_price_zip(_zip_for(day), expected_day=day - _DAY)


def test_refresh_mark_prices_builds_complete_recent_cache(tmp_path):
    path = str(tmp_path / "mark.json")
    archive = str(tmp_path / "raw")

    def fetch(url):
        for day in range(13, 21):
            ts = day * _DAY
            if mark.day_string(ts) in url:
                return _zip_for(ts, close=100.0 + day)
        raise RuntimeError("404")

    report = mark.refresh_mark_prices(
        ["BTC"],
        now_ms=_NOW,
        cache_path=path,
        http_get_bytes=fetch,
        lookback_days=8,
        max_workers=1,
        archive_root=archive,
        observed_at_ms=_NOW,
    )

    assert report["refreshed"] == 1
    assert report["new_days"] == 8
    assert report["failed_tickers"] == []
    assert mark.mark_price_quality_failures(
        ["BTC"], now_ms=_NOW, cache_path=path
    ) == []
    payload = json.load(open(path, encoding="utf-8"))
    assert len(payload["data"]["BTC"]) == 8
    assert payload["data"]["BTC"][str(20 * _DAY)] == 120.0
    lineage = summarize_archive(archive)
    assert lineage["status"] == "PASS"
    assert lineage["datasets"] == {"perp_mark_prices": 8}
    assert lineage["representations"] == ["HTTP_BODY_BYTES"]


def test_default_refresh_covers_admissible_decision_through_next_week(tmp_path):
    path = str(tmp_path / "mark.json")

    def fetch(url):
        for day in range(5, 21):
            ts = day * _DAY
            if mark.day_string(ts) in url:
                return _zip_for(ts, close=100.0 + day)
        raise RuntimeError("unexpected day")

    report = mark.refresh_mark_prices(
        ["BTC"],
        now_ms=_NOW,
        cache_path=path,
        http_get_bytes=fetch,
        max_workers=1,
    )

    assert report["failed_tickers"] == []
    assert report["new_days"] == 16
    payload = json.load(open(path, encoding="utf-8"))
    assert str(5 * _DAY) in payload["data"]["BTC"]
    assert str(20 * _DAY) in payload["data"]["BTC"]


def test_refresh_mark_prices_classifies_transient_and_keeps_cache_atomic(tmp_path):
    path = str(tmp_path / "mark.json")

    report = mark.refresh_mark_prices(
        ["BTC"],
        now_ms=_NOW,
        cache_path=path,
        http_get_bytes=lambda _url: (_ for _ in ()).throw(
            URLError("connection reset")
        ),
        lookback_days=8,
        max_workers=1,
    )

    assert report["refreshed"] == 0
    assert report["failed_tickers"] == ["BTC"]
    assert {item["kind"] for item in report["failure_details"]} == {
        "TRANSIENT",
        "QUALITY",
    }
    payload = json.load(open(path, encoding="utf-8"))
    assert payload["data"].get("BTC", {}) == {}


def test_fetch_mark_days_retries_transient_then_succeeds():
    day = 20 * _DAY
    calls = []

    def flaky(_url):
        calls.append(1)
        if len(calls) < 3:
            raise URLError("connection reset")
        return _zip_for(day)

    failures = []
    out = mark.fetch_mark_days(
        "BTC",
        [day],
        http_get_bytes=flaky,
        max_workers=1,
        max_attempts=3,
        retry_sleep=lambda _seconds: None,
        failure_sink=failures,
    )

    assert out == {day: 100.0}
    assert len(calls) == 3
    assert failures == []
