"""多资产摄取接缝 + CoinGecko 只读抓取:全离线(桩 http_get、env 测试 key,绝不连网)。"""
import pytest
from services.data.persistent_store import SqlitePITStore
from services.data.market_provider import ingest_provider_rows, MarketDataProvider
from tools.coingecko_fetch import parse_ohlc, fetch_ohlc, fetch_btc_rows, _read_key


def _row(inst, et, o, h, l, c, kt=None, v=0.0):
    return {"instrument_id": inst, "event_time": et, "knowledge_time": kt if kt is not None else et,
            "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_ingest_provider_rows_into_store():
    store = SqlitePITStore(":memory:", create=True)
    rows = [_row("BTC", 1700000000000, 100, 101, 99, 100.5, kt=1700000000000 + 1800000),
            _row("BTC", 1700001800000, 100.5, 102, 100, 101.0, kt=1700001800000 + 1800000)]
    rep = ingest_provider_rows(rows, store, dataset_id="coingecko-btc")
    assert rep.rows_read == 2 and rep.bars_ingested == 2 and rep.rejected == ()
    got = store.as_of("BTC", as_of_time=9_999_999_999_999)
    assert [b.event_time for b in got] == [1700000000000, 1700001800000]
    assert rep.content_hash


def test_ingest_rejects_bad_ohlc_not_crash():
    store = SqlitePITStore(":memory:", create=True)
    rows = [_row("BTC", 1700000000000, 100, 101, 99, 100.5, kt=1700000000000 + 1800000),
            _row("BTC", 1700001800000, 100, 90, 99, 100.5, kt=1700001800000 + 1800000)]  # high<low
    rep = ingest_provider_rows(rows, store)
    assert rep.bars_ingested == 1 and len(rep.rejected) == 1     # 失败关闭,不崩


def test_ingest_idempotent_same_hash():
    store = SqlitePITStore(":memory:", create=True)
    rows = [_row("BTC", 1700000000000, 100, 101, 99, 100.5, kt=1700000000000 + 1800000)]
    a = ingest_provider_rows(rows, store)
    b = ingest_provider_rows(rows, store)
    assert a.bars_ingested == 1 and b.bars_ingested == 0 and b.duplicates == 1
    assert a.content_hash == b.content_hash


def test_parse_ohlc_pure():
    payload = [[1700000000000, 100.0, 101.0, 99.0, 100.5],
               [1700001800000, 100.5, 102.0, 100.0, 101.0]]
    rows = parse_ohlc(payload, instrument_id="BTC", interval_ms=1800000)
    assert rows[0]["event_time"] == 1700000000000
    assert rows[0]["knowledge_time"] == 1700000000000 + 1800000   # bar 收盘才可知(PIT)
    assert rows[0]["open"] == 100.0 and rows[0]["close"] == 100.5 and rows[0]["volume"] == 0.0


def test_fetch_ohlc_sends_demo_header_offline(monkeypatch):
    monkeypatch.setenv("COINGECKO_API_KEY", "TESTKEY")
    captured = {}

    def stub(url, headers):
        captured["url"] = url
        captured["headers"] = headers
        return [[1700000000000, 100.0, 101.0, 99.0, 100.5]]      # 桩 payload,无网络

    payload = fetch_ohlc("bitcoin", days=1, http_get=stub)
    assert captured["headers"]["x-cg-demo-api-key"] == "TESTKEY"  # 用了只读数据 key,但测试不含真 key
    assert "coins/bitcoin/ohlc" in captured["url"] and "days=1" in captured["url"]
    assert payload[0][0] == 1700000000000


def test_fetch_btc_rows_offline(monkeypatch):
    monkeypatch.setenv("COINGECKO_API_KEY", "TESTKEY")
    stub = lambda url, headers: [[1700000000000, 100.0, 101.0, 99.0, 100.5]]
    rows = fetch_btc_rows(days=1, http_get=stub)
    assert rows[0]["instrument_id"] == "BTC"
    assert rows[0]["knowledge_time"] == 1700000000000 + 30 * 60 * 1000   # days=1 → 30 分钟颗粒度


def test_read_key_env_and_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("COINGECKO_API_KEY", "  ENVKEY  ")
    assert _read_key() == "ENVKEY"                                # strip
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)                                  # 无 env、无 key 文件
    with pytest.raises(RuntimeError):
        _read_key()


def test_provider_protocol_is_runtime_checkable():
    class _Dummy:
        asset_class = "CRYPTO"
        def fetch_ohlc(self, instrument_id, *, days, vs_currency="usd"):
            return []
    assert isinstance(_Dummy(), MarketDataProvider)             # 多资产接缝可被任意 provider 实现
