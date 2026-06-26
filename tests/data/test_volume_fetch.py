"""CoinGecko market_chart volume fetcher — fully offline (injected http_get stub, no network)."""
import pytest
from tools.coingecko_volume_fetch import parse_market_chart, fetch_volume_rows


# hour-aligned timestamps (multiples of 3_600_000) so bucketing is identity here
_T0, _T1, _T2 = 1700002800000, 1700006400000, 1700010000000


def test_parse_market_chart_maps_real_volume():
    payload = {"prices": [[_T0, 100.0], [_T1, 101.0], [_T2, 102.0]],
               "total_volumes": [[_T0, 1e9], [_T1, 1.1e9], [_T2, 1.2e9]]}
    rows = parse_market_chart(payload, instrument_id="BTC", now_ms=_T2 + 1)
    assert len(rows) == 2                                  # trailing LIVE bucket dropped (provisional)
    assert rows[0]["instrument_id"] == "BTC" and rows[0]["close"] == 100.0
    assert rows[0]["volume"] == 1e9                        # REAL 24h volume carried through
    assert rows[0]["event_time"] == _T0                    # snapped to the hourly grid (lag 0)


def test_offset_stamps_snap_to_common_grid():
    # two coins with DIFFERENT sub-hour offsets must land on the SAME period key (else the
    # cross-section collapses to one name -> breadth 0, the bug this guards against)
    a = parse_market_chart({"prices": [[_T0 + 137, 10.0], [_T1 + 137, 11.0]],
                            "total_volumes": [[_T0 + 137, 1.0], [_T1 + 137, 1.0]]},
                           instrument_id="A", now_ms=_T2)
    b = parse_market_chart({"prices": [[_T0 + 982, 20.0], [_T1 + 982, 22.0]],
                            "total_volumes": [[_T0 + 982, 1.0], [_T1 + 982, 1.0]]},
                           instrument_id="B", now_ms=_T2)
    assert a[0]["event_time"] == b[0]["event_time"] == _T0   # aligned despite different offsets


def test_non_dict_payload_fails_closed():
    with pytest.raises(ValueError):
        parse_market_chart([1, 2, 3], instrument_id="BTC")           # error/non-dict -> raise


def test_fetch_volume_rows_offline(monkeypatch):
    monkeypatch.setenv("COINGECKO_API_KEY", "TESTKEY")
    captured = {}

    def stub(url, headers):
        captured["url"] = url
        return {"prices": [[_T0, 100.0], [_T1, 101.0]],
                "total_volumes": [[_T0, 5e8], [_T1, 5.1e8]]}

    rows = fetch_volume_rows("bitcoin", "BTC", days=30, http_get=stub, now_ms=_T1 + 1)
    assert "coins/bitcoin/market_chart" in captured["url"] and "days=30" in captured["url"]
    assert len(rows) == 1 and rows[0]["instrument_id"] == "BTC" and rows[0]["volume"] == 5e8
