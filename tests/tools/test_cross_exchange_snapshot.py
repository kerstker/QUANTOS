import json

import pytest

from tools import cross_exchange_snapshot as cs


def _binance_book():
    return {"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "101"}


def _binance_premium():
    return {"symbol": "BTCUSDT", "markPrice": "100.5", "lastFundingRate": "0.0001"}


def _binance_oi():
    return {"symbol": "BTCUSDT", "openInterest": "42"}


def _bybit():
    return {"retCode": 0, "result": {"list": [{
        "symbol": "BTCUSDT", "bid1Price": "99.5", "ask1Price": "100.5",
        "lastPrice": "100", "fundingRate": "0.0002", "openInterest": "41",
    }]}}


def test_parsers_build_independent_venue_rows():
    bn = cs.parse_binance_snapshot(_binance_book(), _binance_premium(), _binance_oi(),
                                   observed_at_ms=1_700_000_000_000)
    bb = cs.parse_bybit_snapshot(_bybit(), observed_at_ms=1_700_000_000_000)
    assert bn["venue"] == "binance_usdm" and bb["venue"] == "bybit_linear"
    assert bn["spread_bps"] == pytest.approx(100 / 100.5 * 100)
    assert bn["funding_rate"] == pytest.approx(0.0001)
    assert bb["open_interest"] == pytest.approx(41)
    assert bn["knowledge_time_ms"] == bb["knowledge_time_ms"]


def test_capture_snapshot_is_atomic_two_venue_unit():
    responses = [_binance_book(), _binance_premium(), _binance_oi(), _bybit()]

    def fake_get(url, headers):
        return responses.pop(0)

    rows = cs.capture_snapshot("BTC", observed_at_ms=1_700_000_000_000,
                               http_get=fake_get)
    assert len(rows) == 2
    assert {row["venue"] for row in rows} == {"binance_usdm", "bybit_linear"}


def test_capture_snapshot_rejects_incomplete_bybit_response():
    responses = [_binance_book(), _binance_premium(), _binance_oi(),
                 {"retCode": 0, "result": {"list": []}}]

    with pytest.raises(cs.CrossExchangeDataError):
        cs.capture_snapshot("BTC", observed_at_ms=1_700_000_000_000,
                            http_get=lambda *a: responses.pop(0))


def test_append_snapshots_deduplicates_and_keeps_both_venues(tmp_path):
    path = str(tmp_path / "snap.json")
    responses = [_binance_book(), _binance_premium(), _binance_oi(), _bybit()]
    rows = cs.capture_snapshot(
        "BTC", observed_at_ms=1_700_000_000_000,
        http_get=lambda *a: responses.pop(0),
    )
    first = cs.append_snapshots(path, rows=rows)
    second = cs.append_snapshots(path, rows=rows)
    assert first["row_count"] == second["row_count"] == 2
    assert json.loads(open(path, encoding="utf-8").read())["new_rows"] == 0
