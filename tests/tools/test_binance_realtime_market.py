import json
from urllib.parse import parse_qs, urlparse

import pytest

from services.data.binance_realtime_store import BinanceRealtimeStore
from tools import binance_realtime_market as realtime


_NOW = 1_800_000_000_000


def _symbol(url):
    return parse_qs(urlparse(url).query).get("symbol", [""])[0]


def _fake_http(url):
    path = urlparse(url).path
    symbol = _symbol(url)
    if path == "/fapi/v1/premiumIndex":
        return [
            {
                "symbol": "BTCUSDT",
                "markPrice": "100.0",
                "indexPrice": "99.5",
                "estimatedSettlePrice": "99.8",
                "lastFundingRate": "0.0001",
                "interestRate": "0.0001",
                "nextFundingTime": _NOW + 8 * 60 * 60_000,
                "time": _NOW - 1_000,
            }
        ]
    if path == "/fapi/v1/ticker/bookTicker":
        return [
            {
                "symbol": "BTCUSDT",
                "bidPrice": "99.9",
                "bidQty": "5",
                "askPrice": "100.1",
                "askQty": "4",
                "time": _NOW - 2_000,
            }
        ]
    if path == "/fapi/v1/exchangeInfo":
        return {
            "serverTime": _NOW - 1_000,
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "onboardDate": 1_600_000_000_000,
                    "deliveryDate": 4_133_404_800_000,
                    "pricePrecision": 1,
                    "quantityPrecision": 3,
                    "filters": [
                        {
                            "filterType": "PRICE_FILTER",
                            "tickSize": "0.1",
                        },
                        {
                            "filterType": "MARKET_LOT_SIZE",
                            "minQty": "0.001",
                            "maxQty": "1000",
                            "stepSize": "0.001",
                        },
                        {
                            "filterType": "MIN_NOTIONAL",
                            "notional": "5",
                        },
                    ],
                }
            ],
        }
    if path == "/fapi/v1/ticker/24hr":
        return [
            {
                "symbol": "BTCUSDT",
                "priceChange": "2",
                "priceChangePercent": "2.0",
                "weightedAvgPrice": "100",
                "lastPrice": "101",
                "openPrice": "99",
                "highPrice": "103",
                "lowPrice": "97",
                "volume": "1000",
                "quoteVolume": "100000",
                "openTime": _NOW - 24 * 60 * 60_000,
                "closeTime": _NOW - 1_000,
                "count": 1234,
            }
        ]
    assert symbol == "BTCUSDT"
    if path == "/fapi/v1/klines":
        return [
            [
                _NOW - 120_000,
                "99",
                "101",
                "98",
                "100",
                "10",
                _NOW - 60_001,
                "1000",
                20,
                "6",
                "600",
                "0",
            ],
            [
                _NOW - 60_000,
                "100",
                "102",
                "99",
                "101",
                "12",
                _NOW - 1,
                "1200",
                22,
                "7",
                "700",
                "0",
            ],
        ]
    if path == "/fapi/v1/fundingRate":
        return [
            {
                "symbol": symbol,
                "fundingTime": _NOW - 8 * 60 * 60_000,
                "fundingRate": "0.0002",
                "markPrice": "98",
            }
        ]
    if path == "/fapi/v1/openInterest":
        return {
            "symbol": symbol,
            "openInterest": "205",
            "time": _NOW - 1_500,
        }
    if path == "/futures/data/openInterestHist":
        return [
            {
                "symbol": symbol,
                "sumOpenInterest": "200",
                "sumOpenInterestValue": "20000",
                "timestamp": _NOW - 5 * 60_000,
            }
        ]
    if path == "/futures/data/globalLongShortAccountRatio":
        return [{"symbol": symbol, "longShortRatio": "1.4", "timestamp": _NOW - 5 * 60_000}]
    if path == "/futures/data/topLongShortPositionRatio":
        return [{"symbol": symbol, "longShortRatio": "1.2", "timestamp": _NOW - 5 * 60_000}]
    if path == "/futures/data/topLongShortAccountRatio":
        return [{"symbol": symbol, "longShortRatio": "1.3", "timestamp": _NOW - 5 * 60_000}]
    if path == "/futures/data/takerlongshortRatio":
        return [
            {
                "buySellRatio": "1.1",
                "buyVol": "11",
                "sellVol": "10",
                "timestamp": _NOW - 5 * 60_000,
            }
        ]
    raise AssertionError(url)


def test_refresh_persists_complete_public_snapshot_and_status(tmp_path):
    store_path = str(tmp_path / "market.db")
    status_path = str(tmp_path / "status.json")
    status = realtime.run_refresh(
        tickers=("BTC",),
        store_path=store_path,
        status_path=status_path,
        archive_root=str(tmp_path / "raw"),
        http_get=_fake_http,
        now_ms=_NOW,
        max_workers=1,
    )

    assert status["verdict"] == "PASS"
    assert status["coverage"] == {
        "requested": 1,
        "complete": 1,
        "inserted": 1,
        "failed": 0,
    }
    row = status["latest"]["BTC"]
    assert row["book"]["spread_bps"] == pytest.approx(20.0)
    assert row["contract"]["step_size"] == 0.001
    assert row["contract"]["min_notional"] == 5.0
    assert row["rolling_24h"]["quote_volume"] == 100_000.0
    assert row["rolling_24h"]["trade_count"] == 1234
    assert row["perpetual"]["basis_bps"] > 0
    assert row["perpetual"]["current_open_interest"] == 205.0
    assert row["perpetual"]["open_interest"] == 200.0
    assert row["perpetual"]["open_interest_value"] == 20_000.0
    assert row["positioning"]["global_long_short_ratio"] == 1.4
    assert row["positioning"]["top_position_long_short_ratio"] == 1.2
    assert row["positioning"]["top_account_long_short_ratio"] == 1.3
    assert row["last_settled_funding"]["rate"] == 0.0002
    assert row["closed_1m"]["trade_count"] == 22
    assert row["quality"]["order_submission"] is False
    assert json.loads((tmp_path / "status.json").read_text())["complete"] is True

    store = BinanceRealtimeStore(store_path)
    try:
        assert store.row_count() == 1
        assert store.chain_valid() is True
    finally:
        store.close()


def test_refresh_blocks_stale_positioning_without_writing_a_row(tmp_path):
    def stale_http(url):
        payload = _fake_http(url)
        if urlparse(url).path == "/futures/data/globalLongShortAccountRatio":
            payload[0]["timestamp"] = _NOW - 16 * 60_000
        return payload

    status = realtime.run_refresh(
        tickers=("BTC",),
        store_path=str(tmp_path / "market.db"),
        status_path=str(tmp_path / "status.json"),
        archive_root=None,
        http_get=stale_http,
        now_ms=_NOW,
        max_workers=1,
    )

    assert status["verdict"] == "BLOCK"
    assert status["coverage"]["complete"] == 0
    assert "timestamp is stale" in status["errors"]["BTC"]["message"]


def test_refresh_blocks_missing_execution_filter(tmp_path):
    def missing_filter_http(url):
        payload = _fake_http(url)
        if urlparse(url).path == "/fapi/v1/exchangeInfo":
            payload["symbols"][0]["filters"] = [
                item
                for item in payload["symbols"][0]["filters"]
                if item["filterType"] != "MIN_NOTIONAL"
            ]
        return payload

    status = realtime.run_refresh(
        tickers=("BTC",),
        store_path=str(tmp_path / "market.db"),
        status_path=str(tmp_path / "status.json"),
        archive_root=None,
        http_get=missing_filter_http,
        now_ms=_NOW,
        max_workers=1,
    )

    assert status["verdict"] == "BLOCK"
    assert "required exchange filter is missing" in status["errors"]["BTC"]["message"]
