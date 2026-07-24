"""services/console/api/mexc_views.py 的 mexc-market `bars` 载荷(2026-07-23 前端
CandleChart 接线):PIT 库逐日 OHLCV(最近 N 根,t=秒),字段名镜像前端
`MexcMarketPayload.bars` / `Candle` 契约;库缺/坏行 → 诚实空数组。零网络。"""
import os

import pytest

from services.console.api import mexc_views
from services.data.market_provider import ingest_provider_rows
from services.data.persistent_store import SqlitePITStore

_DAY = 86_400_000
_T0 = (1_700_000_000_000 // _DAY) * _DAY


def _seed_db(path, n=130):
    store = SqlitePITStore(path, create=True)
    rows = [{
        "instrument_id": "BTC", "event_time": _T0 + i * _DAY,
        "knowledge_time": _T0 + i * _DAY,
        "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i,
        "close": 100.5 + i, "volume": 2.5,
    } for i in range(n)]
    ingest_provider_rows(rows, store, dataset_id="mexc-BTC",
                         now=_T0 + n * _DAY, time_unit="ms", strict=False)


def test_ohlcv_bars_shape_and_cap(tmp_path):
    db = str(tmp_path / "crypto_mexc.db")
    _seed_db(db, n=130)
    bars = mexc_views._ohlcv_bars_from_pit_db(db)
    assert len(bars) == 120                                # 默认尾部 120 根
    first, last = bars[0], bars[-1]
    assert [b["t"] for b in bars] == sorted(b["t"] for b in bars)   # 时间升序
    assert last["t"] == pytest.approx((_T0 + 129 * _DAY) / 1000.0)
    assert first["t"] == pytest.approx((_T0 + 10 * _DAY) / 1000.0)
    for bar in bars:
        assert set(bar) == {"t", "open", "high", "low", "close", "volume"}
        assert bar["high"] >= bar["low"] and bar["close"] > 0
    assert last["open"] == pytest.approx(100.0 + 129)
    assert last["volume"] == pytest.approx(2.5)


def test_ohlcv_bars_missing_db_is_honest_empty(tmp_path):
    assert mexc_views._ohlcv_bars_from_pit_db(str(tmp_path / "nope.db")) == []


def test_ohlcv_bars_skips_bad_rows(tmp_path):
    db = str(tmp_path / "crypto_mexc.db")
    store = SqlitePITStore(db, create=True)
    good = {"instrument_id": "BTC", "event_time": _T0, "knowledge_time": _T0,
            "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}
    bad = {"instrument_id": "BTC", "event_time": _T0 + _DAY, "knowledge_time": _T0 + _DAY,
           "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0.0}
    ingest_provider_rows([good, bad], store, dataset_id="mexc-BTC",
                         now=_T0 + 2 * _DAY, time_unit="ms", strict=False)
    bars = mexc_views._ohlcv_bars_from_pit_db(db)
    assert len(bars) == 1 and bars[0]["close"] == pytest.approx(1.5)
    assert "volume" in bars[0]


def test_market_payload_carries_bars_key_even_without_data():
    # 契约形状:无数据时 bars 仍存在且为空数组(契约测试钉 required key)
    payload = mexc_views._mexc_market_payload()
    assert "bars" in payload and isinstance(payload["bars"], list)
