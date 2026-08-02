"""tools/bybit_klines_fetch.py 离线测试 —— mock HTTP:分页(最新在前)、去重、未来数据拒绝、
funding 日频求和、快照落盘。全程无网络(http_get 注入假实现)。"""
from __future__ import annotations

import json

import pytest

from services.data.market_provider import ingest_provider_rows
from services.data.persistent_store import SqlitePITStore
from tools import bybit_klines_fetch as bybit

_DAY = 86_400_000
_T0 = (1_700_000_000_000 // _DAY) * _DAY     # 固定锚点(ms, 已对齐 UTC 日网格)
_NOW = _T0 + 3000 * _DAY                     # "现在"远在所有测试数据之后


def _kline(day_index: int, *, close: float = 105.0):
    """构造一根 Bybit 日线 kline 行:[start,o,h,l,c,v,turnover]。"""
    open_ms = _T0 + day_index * _DAY
    return [str(open_ms), "100.0", "110.0", "90.0", str(close), "12.5", "1250.0"]


def _ok(result_list):
    return {"retCode": 0, "retMsg": "OK", "result": {"list": result_list}}


def _fake_http(pages):
    """按调用顺序返回各页(每页已是完整 Bybit 响应体)。"""
    calls = {"n": 0, "urls": []}

    def http_get(url, headers):
        calls["n"] += 1
        calls["urls"].append(url)
        if calls["n"] > len(pages):
            return _ok([])
        return pages[calls["n"] - 1]
    return http_get, calls


# --- parse_klines(纯函数)---

def test_parse_klines_buckets_by_open_time_and_counts_diagnostics():
    batch = [_kline(0), _kline(1), ["bad"], [1, "x", "2", "3", "4", "5"]]
    diag = {}
    parsed = bybit.parse_klines(batch, instrument_id="BTC", diagnostics=diag)
    assert sorted(parsed) == [_T0, _T0 + _DAY]
    assert parsed[_T0]["c"] == 105.0 and parsed[_T0]["sym"] == "BTC"
    assert diag["status"] == "ACCEPTED" and diag["input_rows"] == 4
    assert diag["accepted_rows"] == 2 and diag["rejected_rows"] == 2


def test_parse_klines_rejects_nonpositive_and_nonfinite_prices():
    diag = {}
    assert bybit.parse_klines([_kline(0, close=0.0)], instrument_id="BTC",
                              diagnostics=diag) == {}
    assert diag["rejected_rows"] == 1
    diag2 = {}
    nan_row = [_kline(0)[0], "100", "110", "90", "nan", "12.5", "1250"]
    assert bybit.parse_klines([nan_row], instrument_id="BTC", diagnostics=diag2) == {}
    assert diag2["rejected_rows"] == 1


def test_bybit_error_body_raises():
    def http_get(url, headers):
        return {"retCode": 10001, "retMsg": "params error", "result": {}}
    with pytest.raises(bybit.BybitFetchError):
        bybit.fetch_klines("NOPEUSDT", http_get=http_get)


def test_missing_result_or_list_raises():
    def http_get(url, headers):
        return {"retCode": 0, "retMsg": "OK"}
    with pytest.raises(bybit.BybitFetchError):
        bybit.fetch_klines("BTCUSDT", http_get=http_get)


def test_unsupported_interval_rejected():
    with pytest.raises(ValueError):
        bybit.fetch_klines("BTCUSDT", interval="5m", http_get=_fake_http([])[0])


# --- 分页(响应最新在前)---

def test_fetch_rows_paginates_backward_with_end_until_short_page():
    page1 = [_kline(i) for i in range(1999, 999, -1)]          # 最新在前:1999..1000
    page2 = [_kline(i) for i in range(999, 499, -1)]           # 短页 → 到历史起点,停
    http_get, calls = _fake_http([_ok(page1), _ok(page2)])
    rows = bybit.fetch_rows("BTC", bars=3000, http_get=http_get, now_ms=_NOW, sleep=0)
    assert calls["n"] == 2
    assert "category=linear" in calls["urls"][0]
    assert "&end=" in calls["urls"][1]                          # 第二页带 end(向后翻页)
    ets = [r["event_time"] for r in rows]
    assert ets == sorted(ets) and len(ets) == len(set(ets))    # 有序无重
    assert rows[0]["event_time"] == _T0 + 500 * _DAY           # 最早一根
    assert rows[-1]["event_time"] == _T0 + 1999 * _DAY


def test_fetch_rows_drops_unclosed_current_bar():
    today_idx = (_NOW - _T0) // _DAY                            # 今天(未收盘)
    batch = [_kline(today_idx), _kline(today_idx - 1)]         # 最新在前,含今天
    http_get, _ = _fake_http([_ok(batch)])
    rows = bybit.fetch_rows("BTC", bars=10, http_get=http_get, now_ms=_NOW, sleep=0)
    assert [r["event_time"] for r in rows] == [_T0 + (today_idx - 1) * _DAY]


def test_fetch_rows_dedups_overlapping_pages():
    page1 = [_kline(i) for i in range(1999, 999, -1)]
    page2 = [_kline(i) for i in range(1004, 499, -1)]          # 与 page1 重叠 5 根 + 更老 500 根
    http_get, _ = _fake_http([_ok(page1), _ok(page2)])
    rows = bybit.fetch_rows("BTC", bars=3000, http_get=http_get, now_ms=_NOW, sleep=0)
    ets = [r["event_time"] for r in rows]
    assert len(ets) == len(set(ets))                           # 主键级无重复
    assert ets[0] == _T0 + 500 * _DAY and ets[-1] == _T0 + 1999 * _DAY


# --- funding 日频求和 ---

def _funding(ts_ms, rate="0.0001"):
    return {"symbol": "BTCUSDT", "fundingRate": rate,
            "fundingRateTimestamp": str(ts_ms)}


def test_fetch_daily_funding_sums_8h_rates_on_utc_day_grid():
    day0 = _T0
    batch = [                                            # 最新在前
        _funding(day0 + 16 * 3_600_000, "0.0003"),
        _funding(day0 + 8 * 3_600_000, "0.0002"),
        _funding(day0, "0.0001"),
    ]
    http_get, _ = _fake_http([_ok(batch)])
    out = bybit.fetch_daily_funding("BTC", http_get=http_get, now_ms=_NOW, sleep=0)
    assert out == {day0: pytest.approx(0.0006)}


def test_fetch_daily_funding_paginates_and_drops_unfinished_today():
    today = (_NOW // _DAY) * _DAY
    page1 = [_funding(today + 8 * 3_600_000, "0.0009"),          # 今天(未完结)→ 丢
             _funding(today - _DAY + 16 * 3_600_000, "0.0002")]
    page1 += [_funding(today - _DAY + 8 * 3_600_000, "0.0001")]
    http_get, _ = _fake_http([_ok(page1)])
    out = bybit.fetch_daily_funding("BTC", http_get=http_get, now_ms=_NOW, sleep=0)
    assert out == {today - _DAY: pytest.approx(0.0003)}          # 今天不在结果里


def test_fetch_daily_funding_rejects_malformed_rows():
    batch = [_funding(_T0, "0.0001"), {"symbol": "BTCUSDT"},   # 缺字段
             _funding(-5, "0.0001"),                            # 负时间戳
             _funding(_NOW + 10 * _DAY, "0.0001")]              # 未来
    http_get, _ = _fake_http([_ok(batch)])
    out = bybit.fetch_daily_funding("BTC", http_get=http_get, now_ms=_NOW, sleep=0)
    assert out == {_T0: pytest.approx(0.0001)}


# --- 未来数据拒绝(ingest 守卫)/ 幂等 ---

def _norm_rows(n=2):
    return [
        {"instrument_id": "BTC", "event_time": _T0 + i * _DAY, "knowledge_time": _T0 + i * _DAY,
         "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "volume": 12.5}
        for i in range(n)
    ]


def test_ingest_rejects_future_bars(tmp_path):
    rows = _norm_rows()
    future = dict(rows[-1])
    future["event_time"] = _NOW + 10 * _DAY                     # 未来 bar
    future["knowledge_time"] = _NOW + 10 * _DAY
    store = SqlitePITStore(str(tmp_path / "bybit.db"), create=True)
    rep = ingest_provider_rows(rows + [future], store, dataset_id="bybit-BTC",
                               now=_NOW, time_unit="ms")
    assert rep.bars_ingested == len(rows)                       # 未来行被拒,其余落库
    assert len(rep.rejected) == 1


def test_ingest_is_idempotent_on_pk(tmp_path):
    rows = _norm_rows()
    store = SqlitePITStore(str(tmp_path / "bybit.db"), create=True)
    rep1 = ingest_provider_rows(rows, store, dataset_id="bybit-BTC", now=_NOW, time_unit="ms")
    rep2 = ingest_provider_rows(rows, store, dataset_id="bybit-BTC", now=_NOW, time_unit="ms")
    assert rep1.bars_ingested == len(rows)
    assert rep2.bars_ingested == 0                              # 第二次幂等,不重复写


# --- 快照落盘 ---

def test_main_writes_snapshots_and_ingests(tmp_path, monkeypatch):
    rows = _norm_rows()
    monkeypatch.setattr(bybit, "fetch_rows", lambda *a, **k: rows)
    monkeypatch.setattr(bybit, "fetch_daily_funding",
                        lambda *a, **k: {_T0: 0.0003, _T0 + _DAY: -0.0001})
    monkeypatch.setattr(bybit.time, "time", lambda: (_T0 + 2 * _DAY) / 1000)
    db = tmp_path / "bybit_perp.db"
    out = tmp_path / "snap.json"
    fout = tmp_path / "funding.json"
    snap = bybit.main(["--tickers", "BTC", "--db", str(db), "--out", str(out),
                       "--funding-out", str(fout)])
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == snap
    assert snap["provenance"] == "REAL_BYBIT_PUBLIC_KLINES_FUNDING"
    assert snap["tickers"]["BTC"]["rows"] == 2
    assert snap["tickers"]["BTC"]["first_event_time"] == _T0
    assert snap["funding"]["BTC"]["days"] == 2
    assert snap["bars_ingested"] == 2 and snap["db_path"] == str(db)
    assert db.exists()
    funding = json.loads(fout.read_text(encoding="utf-8"))
    assert funding == {"BTC": {str(_T0): 0.0003, str(_T0 + _DAY): -0.0001}}


def test_main_no_ingest_reports_honest_nulls(tmp_path, monkeypatch):
    monkeypatch.setattr(bybit, "fetch_rows", lambda *a, **k: _norm_rows())
    monkeypatch.setattr(bybit, "fetch_daily_funding", lambda *a, **k: {})
    monkeypatch.setattr(bybit.time, "time", lambda: (_NOW) / 1000)
    out = tmp_path / "snap.json"
    snap = bybit.main(["--tickers", "BTC", "--no-ingest", "--out", str(out)])
    assert snap["bars_ingested"] is None and snap["db_path"] is None
    assert snap["funding"]["BTC"]["days"] == 0
    assert snap["funding"]["BTC"]["first_date"] is None
