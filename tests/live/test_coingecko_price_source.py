"""tools/coingecko_fetch.py::fetch_simple_prices —— 跨源核价的第二数据源(2026-07-05)。
全部注入假 http_get——零真网络,镜像既有 fetch_ohlc 测试风格。
"""
import pytest

from tools.coingecko_fetch import (
    SYMBOL_TO_COINGECKO_ID,
    fetch_simple_price_observations,
    fetch_simple_prices,
)


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setenv("COINGECKO_API_KEY", "test-key-not-real")


def test_fetch_simple_prices_maps_ids_back_to_symbols():
    def fake_get(url, headers):
        assert "bitcoin" in url and "ethereum" in url
        assert "include_last_updated_at" not in url
        return {"bitcoin": {"usd": 60000.0}, "ethereum": {"usd": 3000.0}}
    out = fetch_simple_prices(["BTC", "ETH"], http_get=fake_get)
    assert out == {"BTC": 60000.0, "ETH": 3000.0}


def test_unmapped_instrument_is_silently_excluded_not_queried():
    def fake_get(url, headers):
        assert "ghostcoin" not in url
        return {"bitcoin": {"usd": 60000.0}}
    out = fetch_simple_prices(["BTC", "GHOSTCOIN"], http_get=fake_get)
    assert out == {"BTC": 60000.0}
    assert "GHOSTCOIN" not in out


def test_no_mapped_instruments_returns_empty_without_network_call():
    calls = []
    def fake_get(url, headers):
        calls.append(url)
        return {}
    out = fetch_simple_prices(["GHOSTCOIN"], http_get=fake_get)
    assert out == {}
    assert calls == []                                # 零查询,不是空结果的查询


def test_missing_price_field_in_response_is_dropped_honestly():
    def fake_get(url, headers):
        return {"bitcoin": {}}                          # 响应里没有 usd 字段
    out = fetch_simple_prices(["BTC"], http_get=fake_get)
    assert out == {}


def test_all_current_mandate_universe_symbols_have_a_mapping():
    # 2026-07-05 mandate universe(24 币)—— 核价必须覆盖当前实盘全部标的,否则新增的
    # 每个标的都会被跨源核价判成 unverifiable,防线名存实亡
    universe = {"AAVE", "ADA", "APT", "ATOM", "AVAX", "BCH", "BNB", "BTC", "DOGE", "DOT",
               "ETC", "ETH", "FIL", "ICP", "INJ", "LINK", "LTC", "NEAR", "OP", "SOL",
               "TRX", "UNI", "XLM", "XRP"}
    assert universe <= set(SYMBOL_TO_COINGECKO_ID)


def test_fetch_simple_price_observations_include_source_timestamp():
    def fake_get(url, headers):
        assert "include_last_updated_at=true" in url
        return {"bitcoin": {"usd": 60000.0, "last_updated_at": 1700000000}}

    out = fetch_simple_price_observations(["BTC"], http_get=fake_get)
    assert out == {"BTC": {"price": 60000.0, "as_of_ms": 1_700_000_000_000}}


def test_price_observation_without_source_timestamp_is_dropped():
    def fake_get(url, headers):
        return {"bitcoin": {"usd": 60000.0}}

    assert fetch_simple_price_observations(["BTC"], http_get=fake_get) == {}


@pytest.mark.parametrize(
    "row",
    [
        {"usd": 0.0, "last_updated_at": 1700000000},
        {"usd": float("nan"), "last_updated_at": 1700000000},
        {"usd": 60000.0, "last_updated_at": 0},
        {"usd": 60000.0, "last_updated_at": float("inf")},
    ],
)
def test_invalid_price_observation_is_dropped(row):
    def fake_get(url, headers):
        return {"bitcoin": row}

    assert fetch_simple_price_observations(["BTC"], http_get=fake_get) == {}
