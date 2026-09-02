"""tools/mexc_klines_fetch.py 离线测试 —— mock HTTP:分页、去重、未来数据拒绝、快照落盘。
全程无网络(http_get 注入假实现)。"""
from __future__ import annotations

import json

import pytest

from services.data.market_provider import ingest_provider_rows
from services.data.persistent_store import SqlitePITStore
from tools import mexc_klines_fetch as mexc

_DAY = 86_400_000
_T0 = (1_700_000_000_000 // _DAY) * _DAY     # 固定锚点(ms, 已对齐 UTC 日网格)
_NOW = _T0 + 3000 * _DAY                     # "现在"远在所有测试 klines 之后


def _kline(day_index: int, *, close: float = 105.0):
    """构造一根 MEXC 日线 kline 行:[openTime,o,h,l,c,v,closeTime,...]。"""
    open_ms = _T0 + day_index * _DAY
    return [open_ms, "100.0", "110.0", "90.0", str(close), "12.5",
            open_ms + _DAY - 1, "1250.0", 100, "0", "0", "0"]


def _fake_http(pages):
    """按调用顺序返回各页;pages = list of kline batches。"""
    calls = {"n": 0, "urls": []}

    def http_get(url, headers):
        calls["n"] += 1
        calls["urls"].append(url)
        if calls["n"] > len(pages):
            return []
        return pages[calls["n"] - 1]
    return http_get, calls


# --- parse_klines(纯函数)---

def test_parse_klines_buckets_by_close_time_and_counts_diagnostics():
    batch = [_kline(0), _kline(1), ["bad"], [1, "x", "2", "3", "4", "5"]]
    diag = {}
    parsed = mexc.parse_klines(batch, instrument_id="BTC", diagnostics=diag)
    assert sorted(parsed) == [_T0, _T0 + _DAY]
    assert parsed[_T0]["c"] == 105.0 and parsed[_T0]["sym"] == "BTC"
    assert diag["status"] == "ACCEPTED" and diag["input_rows"] == 4
    assert diag["accepted_rows"] == 2 and diag["rejected_rows"] == 2


def test_parse_klines_rejects_nonpositive_prices_and_non_list():
    diag = {}
    parsed = mexc.parse_klines([_kline(0, close=0.0)], instrument_id="BTC", diagnostics=diag)
    assert parsed == {} and diag["rejected_rows"] == 1
    diag2 = {}
    assert mexc.parse_klines({"code": -1, "msg": "x"}, instrument_id="BTC",
                             diagnostics=diag2) == {}
    assert diag2["status"] == "REJECTED"


def test_mexc_error_body_raises():
    def http_get(url, headers):
        return {"code": -1121, "msg": "Invalid symbol."}
    with pytest.raises(mexc.MexcFetchError):
        mexc.fetch_klines("NOPEUSDT", http_get=http_get)


def test_unsupported_interval_rejected():
    with pytest.raises(ValueError):
        mexc.fetch_klines("BTCUSDT", interval="5m", http_get=_fake_http([])[0])


# --- 分页 ---

def test_fetch_rows_paginates_backward_with_end_time_until_short_page():
    page1 = [_kline(i) for i in range(1000, 2000)]           # 最近 1000 根
    page2 = [_kline(i) for i in range(0, 500)]               # 短页 → 到历史起点,停
    http_get, calls = _fake_http([page1, page2])
    rows = mexc.fetch_rows("BTC", bars=3000, http_get=http_get, now_ms=_NOW, sleep=0)
    assert calls["n"] == 2
    assert "endTime=" in calls["urls"][1]                    # 第二页带 endTime(不用 startTime)
    assert "startTime" not in calls["urls"][0] and "startTime" not in calls["urls"][1]
    ets = [r["event_time"] for r in rows]
    assert ets == sorted(ets) and len(ets) == len(set(ets))  # 有序无重
    assert rows[0]["event_time"] == _T0                      # 最早一根
    assert rows[-1]["event_time"] == _T0 + 1999 * _DAY       # 到第 1999 根


def test_fetch_rows_drops_unclosed_current_bar():
    today_idx = (_NOW - _T0) // _DAY                          # 今天(未收盘)
    batch = [_kline(today_idx - 1), _kline(today_idx)]       # 含今天
    http_get, _ = _fake_http([batch])
    rows = mexc.fetch_rows("BTC", bars=10, http_get=http_get, now_ms=_NOW, sleep=0)
    assert [r["event_time"] for r in rows] == [_T0 + (today_idx - 1) * _DAY]


# --- 去重(跨页重叠)---

def test_fetch_rows_dedups_overlapping_pages():
    page1 = [_kline(i) for i in range(1000, 2000)]
    page2 = [_kline(i) for i in range(500, 1005)]            # 与 page1 重叠 5 根 + 更老 500 根
    http_get, _ = _fake_http([page1, page2])
    rows = mexc.fetch_rows("BTC", bars=3000, http_get=http_get, now_ms=_NOW, sleep=0)
    ets = [r["event_time"] for r in rows]
    assert len(ets) == len(set(ets))                         # 主键级无重复
    assert ets[0] == _T0 + 500 * _DAY and ets[-1] == _T0 + 1999 * _DAY


# --- 未来数据拒绝(ingest 守卫)---

def test_ingest_rejects_future_bars(tmp_path):
    batch = [_kline(0), _kline(1)]
    http_get, _ = _fake_http([batch])
    rows = mexc.fetch_rows("BTC", bars=10, http_get=http_get, now_ms=_NOW, sleep=0)
    future = dict(rows[-1])
    future["event_time"] = _NOW + 10 * _DAY                   # 未来 bar
    future["knowledge_time"] = _NOW + 10 * _DAY
    store = SqlitePITStore(str(tmp_path / "mexc.db"), create=True)
    rep = ingest_provider_rows(rows + [future], store, dataset_id="mexc-BTC",
                               now=_NOW, time_unit="ms")
    assert rep.bars_ingested == len(rows)                    # 未来行被拒,其余落库
    assert len(rep.rejected) == 1


def test_ingest_is_idempotent_on_pk(tmp_path):
    http_get, _ = _fake_http([[_kline(0), _kline(1)]])
    rows = mexc.fetch_rows("BTC", bars=10, http_get=http_get, now_ms=_NOW, sleep=0)
    store = SqlitePITStore(str(tmp_path / "mexc.db"), create=True)
    rep1 = ingest_provider_rows(rows, store, dataset_id="mexc-BTC", now=_NOW, time_unit="ms")
    rep2 = ingest_provider_rows(rows, store, dataset_id="mexc-BTC", now=_NOW, time_unit="ms")
    assert rep1.bars_ingested == len(rows)
    assert rep2.bars_ingested == 0                           # 第二次幂等,不重复写


# --- 快照落盘 ---

def test_main_writes_snapshot_with_span_and_source(tmp_path, monkeypatch):
    rows = [
        {"instrument_id": "BTC", "event_time": _T0, "knowledge_time": _T0,
         "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
        {"instrument_id": "BTC", "event_time": _T0 + _DAY, "knowledge_time": _T0 + _DAY,
         "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0, "volume": 11.0},
    ]
    monkeypatch.setattr(mexc, "fetch_rows", lambda *a, **k: rows)
    out = tmp_path / "snap.json"
    snap = mexc.main(["--ticker", "BTC", "--no-ingest", "--out", str(out)])
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == snap
    assert snap["rows"] == 2 and snap["source"].startswith("MEXC public spot klines")
    assert snap["first_event_time"] == _T0 and snap["last_event_time"] == _T0 + _DAY
    assert snap["first_date"] and snap["last_date"] and snap["fetched_at_ms"] > 0
    assert snap["provenance"] == "REAL_MEXC_PUBLIC_KLINES"
    assert snap["bars_ingested"] is None                     # --no-ingest 诚实为空


def test_main_defaults_to_canonical_snapshot_path(tmp_path, monkeypatch):
    rows = [
        {"instrument_id": "BTC", "event_time": _T0, "knowledge_time": _T0,
         "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
    ]
    expected = tmp_path / "mexc_btc_klines.json"
    monkeypatch.setattr(mexc, "fetch_rows", lambda *a, **k: rows)
    monkeypatch.setattr(mexc, "DEFAULT_SNAPSHOT", str(expected))

    snap = mexc.main(["--ticker", "BTC", "--no-ingest"])

    assert json.loads(expected.read_text(encoding="utf-8")) == snap


def test_main_ingests_into_pit_and_snapshot_reports_count(tmp_path, monkeypatch):
    rows = [
        {"instrument_id": "BTC", "event_time": _T0, "knowledge_time": _T0,
         "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
        {"instrument_id": "BTC", "event_time": _T0 + _DAY, "knowledge_time": _T0 + _DAY,
         "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0, "volume": 11.0},
    ]
    monkeypatch.setattr(mexc, "fetch_rows", lambda *a, **k: rows)
    monkeypatch.setattr(mexc.time, "time", lambda: (_T0 + 2 * _DAY) / 1000)
    db = tmp_path / "crypto_mexc.db"
    out = tmp_path / "snap.json"
    snap = mexc.main(["--ticker", "BTC", "--db", str(db), "--out", str(out)])
    assert snap["bars_ingested"] == 2 and snap["dataset_id"] == "mexc-BTC"
    assert db.exists()
