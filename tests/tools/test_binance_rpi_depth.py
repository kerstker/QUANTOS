from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from services.data.binance_systemic_store import BinanceSystemicStore
from tools import binance_rpi_depth as rpi


_NOW = 1_800_000_000_000


def _fake_http(url: str):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.path == "/fapi/v1/rpiDepth"
    assert query == {"symbol": ["BTCUSDT"], "limit": ["1000"]}
    return {
        "lastUpdateId": 12345,
        "E": _NOW - 900,
        "T": _NOW - 1000,
        "bids": [["100", "2"], ["99", "3"]],
        "asks": [["101", "4"], ["102", "5"]],
    }


def test_refresh_persists_complete_rpi_response_and_metrics(tmp_path):
    store_path = str(tmp_path / "rpi.db")
    status_path = str(tmp_path / "status.json")
    status = rpi.run_refresh(
        tickers=("BTC",),
        store_path=store_path,
        status_path=status_path,
        archive_root=str(tmp_path / "raw"),
        http_get=_fake_http,
        now_ms=_NOW,
        max_workers=1,
    )

    assert status["verdict"] == "PASS"
    assert status["coverage"] == {"requested": 1, "complete": 1, "failed": 0}
    assert status["store"] == {
        "path": store_path,
        "row_count": 1,
        "inserted": 1,
        "chain_valid": True,
    }
    assert status["request_policy"]["official_ip_weight_per_symbol"] == 20
    assert status["latest"]["BTC"]["bid_level_count"] == 2
    assert status["latest"]["BTC"]["ask_level_count"] == 2
    assert status["latest"]["BTC"]["spread_bps"] == pytest.approx(
        99.5024875622
    )
    assert json.loads((tmp_path / "status.json").read_text())["complete"] is True

    store = BinanceSystemicStore(store_path)
    try:
        row = store.latest("rpi_depth_1000", "BTCUSDT")
        assert row["bids"] == [[100.0, 2.0], [99.0, 3.0]]
        assert row["asks"] == [[101.0, 4.0], [102.0, 5.0]]
        assert row["quality"]["rpi_orders_included"] is True
    finally:
        store.close()

    repeated = rpi.run_refresh(
        tickers=("BTC",),
        store_path=store_path,
        status_path=status_path,
        archive_root=None,
        http_get=_fake_http,
        now_ms=_NOW,
        max_workers=1,
    )
    assert repeated["store"]["inserted"] == 0
    assert repeated["store"]["row_count"] == 1


def test_refresh_blocks_crossed_book_without_writing(tmp_path):
    def crossed_http(url: str):
        payload = _fake_http(url)
        payload["asks"][0][0] = "100"
        return payload

    status = rpi.run_refresh(
        tickers=("BTC",),
        store_path=str(tmp_path / "rpi.db"),
        status_path=str(tmp_path / "status.json"),
        archive_root=None,
        http_get=crossed_http,
        now_ms=_NOW,
        max_workers=1,
    )

    assert status["verdict"] == "BLOCK"
    assert "crossed or locked" in status["errors"]["BTC"]["message"]
    assert status["store"]["row_count"] == 0


def test_parser_blocks_unsorted_or_stale_depth():
    unsorted = _fake_http(
        "https://fapi.binance.com/fapi/v1/rpiDepth?"
        "symbol=BTCUSDT&limit=1000"
    )
    unsorted["bids"][1][0] = "101"
    with pytest.raises(rpi.BinanceRpiDepthError, match="strictly sorted"):
        rpi.parse_snapshot("BTCUSDT", unsorted, knowledge_time_ms=_NOW)

    stale = _fake_http(
        "https://fapi.binance.com/fapi/v1/rpiDepth?"
        "symbol=BTCUSDT&limit=1000"
    )
    stale["T"] = _NOW - rpi.MAX_AGE_MS - 1
    stale["E"] = stale["T"]
    with pytest.raises(rpi.BinanceRpiDepthError, match="stale"):
        rpi.parse_snapshot("BTCUSDT", stale, knowledge_time_ms=_NOW)


def test_parser_accepts_bounded_exchange_clock_lead_without_pit_backdating():
    payload = _fake_http(
        "https://fapi.binance.com/fapi/v1/rpiDepth?"
        "symbol=BTCUSDT&limit=1000"
    )
    payload["T"] = _NOW + 2_000
    payload["E"] = _NOW + 2_005

    row = rpi.parse_snapshot("BTCUSDT", payload, knowledge_time_ms=_NOW)

    assert row["event_time_ms"] == _NOW + 2_000
    assert row["knowledge_time_ms"] == _NOW + 2_005

    payload["T"] = _NOW + rpi.MAX_FUTURE_SKEW_MS + 1
    payload["E"] = payload["T"]
    with pytest.raises(rpi.BinanceRpiDepthError, match="future"):
        rpi.parse_snapshot("BTCUSDT", payload, knowledge_time_ms=_NOW)
