"""CoinGecko market_chart volume fetcher — fully offline (injected http_get stub, no network)."""
import pytest
from tools.coingecko_volume_fetch import parse_market_chart, fetch_volume_rows


def test_parse_market_chart_maps_real_volume():
    payload = {"prices": [[1700000000000, 100.0], [1700003600000, 101.0], [1700007200000, 102.0]],
               "total_volumes": [[1700000000000, 1e9], [1700003600000, 1.1e9], [1700007200000, 1.2e9]]}
    rows = parse_market_chart(payload, instrument_id="BTC", now_ms=1700007200000 + 1)
    assert len(rows) == 2                                  # trailing LIVE point dropped (provisional)
    assert rows[0]["instrument_id"] == "BTC" and rows[0]["close"] == 100.0
    assert rows[0]["volume"] == 1e9                        # REAL 24h volume carried through
    assert rows[0]["knowledge_time"] == 1700000000000      # spot obs knowable at ts (lag 0)


def test_non_dict_payload_fails_closed():
    with pytest.raises(ValueError):
        parse_market_chart([1, 2, 3], instrument_id="BTC")           # error/non-dict -> raise


def test_fetch_volume_rows_offline(monkeypatch):
    monkeypatch.setenv("COINGECKO_API_KEY", "TESTKEY")
    captured = {}

    def stub(url, headers):
        captured["url"] = url
        return {"prices": [[1700000000000, 100.0], [1700003600000, 101.0]],
                "total_volumes": [[1700000000000, 5e8], [1700003600000, 5.1e8]]}

    rows = fetch_volume_rows("bitcoin", "BTC", days=30, http_get=stub, now_ms=1700003600000 + 1)
    assert "coins/bitcoin/market_chart" in captured["url"] and "days=30" in captured["url"]
    assert len(rows) == 1 and rows[0]["instrument_id"] == "BTC" and rows[0]["volume"] == 5e8
