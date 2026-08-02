"""tools/deribit_fetch.py 离线测试 —— 假 transport:DVOL 解析/翻页、已结算合约处理、
水位线幂等、未来数据守卫、期权 skew 快照。全程无网络(http_get 注入假实现)。"""
from __future__ import annotations

import json

import pytest

from services.data.market_provider import ingest_provider_rows
from services.data.persistent_store import SqlitePITStore
from tools import deribit_fetch as df

_DAY = 86_400_000
_T0 = (1_700_000_000_000 // _DAY) * _DAY
_NOW = _T0 + 3000 * _DAY


def _dvol_row(day_index: int, *, close: float = 54.0):
    return [_T0 + day_index * _DAY, close - 1, close + 1, close - 2, close]


def _chart(days, *, base: float = 100.0):
    ticks, opens, highs, lows, closes = [], [], [], [], []
    for i in days:
        ticks.append(_T0 + i * _DAY)
        opens.append(base)
        highs.append(base + 1)
        lows.append(base - 1)
        closes.append(base)
    return {"status": "ok", "ticks": ticks, "open": opens, "high": highs,
            "low": lows, "close": closes, "volume": [1.0] * len(ticks),
            "cost": [1.0] * len(ticks)}


def _envelope(result):
    return {"jsonrpc": "2.0", "result": result}


# --- 信封 / 错误体 ---

def test_get_unwraps_result_and_raises_on_error_body():
    ok = df._get("/public/x", http_get=lambda url, headers: _envelope({"a": 1}))
    assert ok == {"a": 1}

    def bad(url, headers):
        return {"jsonrpc": "2.0", "error": {"code": 11050, "message": "Instrument not found"}}
    with pytest.raises(df.DeribitFetchError):
        df._get("/public/y", http_get=bad)
    with pytest.raises(df.DeribitFetchError):
        df._get("/public/z", http_get=lambda url, headers: ["not", "an", "envelope"])


def test_get_translates_http_error_to_deribit_error():
    import urllib.error

    def boom(url, headers):
        raise urllib.error.HTTPError(url, 400, "Bad Request", None, None)
    with pytest.raises(df.DeribitFetchError, match="HTTP 400"):
        df._get("/public/q", http_get=boom)


# --- parse_dvol / fetch_dvol(翻页 + continuation)---

def test_parse_dvol_aligns_day_grid_and_counts_diagnostics():
    result = {"data": [_dvol_row(0), _dvol_row(1), ["bad"], [1, 2, 3, 4, "x"]],
              "continuation": None}
    diag = {}
    parsed = df.parse_dvol(result, currency="BTC", diagnostics=diag)
    assert sorted(parsed) == [_T0, _T0 + _DAY]
    assert parsed[_T0]["sym"] == "DVOL-BTC" and parsed[_T0]["c"] == 54.0
    assert diag["accepted_rows"] == 2 and diag["rejected_rows"] == 2


def test_fetch_dvol_paginates_backward_via_continuation_and_drops_today():
    today_idx = (_NOW - _T0) // _DAY
    page1 = {"data": [_dvol_row(today_idx - 2), _dvol_row(today_idx - 1), _dvol_row(today_idx)],
             "continuation": _T0 + (today_idx - 3) * _DAY}
    page2 = {"data": [_dvol_row(today_idx - 4), _dvol_row(today_idx - 3)], "continuation": None}
    calls = {"n": 0, "urls": []}

    def http_get(url, headers):
        calls["n"] += 1
        calls["urls"].append(url)
        return _envelope(page1 if calls["n"] == 1 else page2)

    rows = df.fetch_dvol("BTC", http_get=http_get, now_ms=_NOW, sleep=0)
    assert calls["n"] == 2
    assert f"end_timestamp={_T0 + (today_idx - 3) * _DAY}" in calls["urls"][1]  # continuation 翻页
    ets = [r["event_time"] for r in rows]
    assert ets == sorted(ets) and len(ets) == 4               # 今天的未收盘当根被丢
    assert rows[-1]["event_time"] == _T0 + (today_idx - 1) * _DAY
    assert rows[0]["knowledge_time"] == rows[0]["event_time"]  # kt=et(EOD 口径)


def test_fetch_dvol_stops_at_watermark():
    today_idx = (_NOW - _T0) // _DAY
    page = {"data": [_dvol_row(today_idx - 2), _dvol_row(today_idx - 1)],
            "continuation": _T0 + (today_idx - 3) * _DAY}

    def http_get(url, headers):
        assert "start_timestamp=0&" not in url                # 带了增量起点
        return _envelope(page)

    rows = df.fetch_dvol("BTC", since_ms=_T0 + (today_idx - 3) * _DAY,
                         http_get=http_get, now_ms=_NOW, sleep=0)
    assert [r["event_time"] for r in rows] == [_T0 + (today_idx - 2) * _DAY,
                                               _T0 + (today_idx - 1) * _DAY]


# --- parse_chart / 已结算合约 ---

def test_parse_chart_settled_instrument_history():
    parsed = df.parse_chart(_chart(range(0, 10)), instrument="BTC-25MAR22")
    assert len(parsed) == 10
    assert parsed[_T0]["sym"] == "BTC-25MAR22"
    diag = {}
    assert df.parse_chart({"status": "error"}, instrument="X", diagnostics=diag) == {}
    assert diag["status"] == "REJECTED"


def test_fetch_chart_rows_raises_for_unknown_instrument():
    def http_get(url, headers):
        return {"jsonrpc": "2.0", "error": {"code": 11050, "message": "Instrument not found"}}
    with pytest.raises(df.DeribitFetchError):
        df.fetch_chart_rows("BTC-31DEC99", http_get=http_get, now_ms=_NOW)


def _chart_varying_then_flat(n_real, n_flat, fill_delta=7.0):
    """前 n_real 根真实变动,后 n_flat 根常数前填(fill = 最后真实收盘 + fill_delta,
    与真实段可区分;结算价填充的污染形态见 trim_constant_tail docstring)。"""
    ticks, closes = [], []
    for i in range(n_real + n_flat):
        ticks.append(_T0 + i * _DAY)
        closes.append(100.0 + i if i < n_real else 100.0 + n_real - 1 + fill_delta)
    n = len(ticks)
    return {"status": "ok", "ticks": ticks, "open": closes, "high": closes,
            "low": closes, "close": closes, "volume": [1.0] * n, "cost": [1.0] * n}


def test_fetch_chart_rows_trims_flat_tail_and_cuts_past_expiry():
    # 30 根真实 + 2000 根填充(填充从 expiry-1 起,延伸到到期后多年)
    expiry = _T0 + 31 * _DAY
    payload = _chart_varying_then_flat(30, 2000)
    rows = df.fetch_chart_rows("BTC-25MAR22", expiry_day_ms=expiry,
                               http_get=lambda u, h: _envelope(payload), now_ms=_NOW)
    assert len(rows) == 30                                    # 填充全去,真实段完整保留
    assert rows[-1]["event_time"] == _T0 + 29 * _DAY
    assert all(r["event_time"] <= expiry for r in rows)       # 到期后零行


def test_fetch_chart_rows_all_constant_means_never_traded():
    payload = _chart_varying_then_flat(0, 100, fill_delta=0.0)  # 全常数
    rows = df.fetch_chart_rows("BTC-25MAR22", expiry_day_ms=_T0 + 99 * _DAY,
                               http_get=lambda u, h: _envelope(payload), now_ms=_NOW)
    assert rows == []


def test_trim_constant_tail_drops_whole_trailing_constant_run():
    # 语义:尾部与末值相同的整段都视为填充(含首个出现——实测首个填充行即与结算价同值,
    # 无法与真实行区分,保守全去)。[1,2,3,3,3] → [1,2];[1,2,3,4,4] → [1,2,3]。
    assert df.trim_constant_tail([{"c": 1.0}, {"c": 2.0}, {"c": 3.0},
                                  {"c": 3.0}, {"c": 3.0}]) == [{"c": 1.0}, {"c": 2.0}]
    assert df.trim_constant_tail([{"c": 1.0}, {"c": 2.0}, {"c": 3.0},
                                  {"c": 4.0}, {"c": 4.0}]) == [{"c": 1.0}, {"c": 2.0}, {"c": 3.0}]
    assert df.trim_constant_tail([]) == []
    assert df.trim_constant_tail([{"c": 5.0}, {"c": 5.0}]) == []


# --- 合约名生成/解析 ---

def test_monthly_future_names_are_last_fridays():
    names = df.monthly_future_names("BTC", 2022, 1, _T0 + 400 * _DAY)
    by_name = dict(names)
    assert "BTC-28JAN22" in by_name          # 2022-01-28 是一月最后周五(Phase 0 实测名)
    assert "BTC-25MAR22" in by_name          # 季度合约(Phase 0 实测名)
    assert "BTC-31DEC21" not in by_name      # 起点 2022-01 之后
    for name, expiry_ms in names:
        weekday = (expiry_ms // _DAY + 3) % 7
        assert weekday == 4                  # 全部是周五(Mon=0)


def test_parse_instrument_name_variants():
    assert df.parse_instrument_name("BTC-PERPETUAL") == ("BTC", None, None, None)
    ccy, exp, strike, is_call = df.parse_instrument_name("BTC-25MAR22")
    assert (ccy, strike, is_call) == ("BTC", None, None)
    assert df._d(exp) == "2022-03-25"
    ccy, exp, strike, is_call = df.parse_instrument_name("ETH-28JUL26-3000-P")
    assert (ccy, strike, is_call) == ("ETH", 3000.0, False)
    assert df.parse_instrument_name("BTC-3SEP26-70000-C")[3] is True   # 单位数日
    assert df.parse_instrument_name("garbage") is None
    assert df.parse_instrument_name("BTC-25XXX22") is None


# --- 期权快照:skew + 幂等落盘 ---

def _opt(name, strike, iv, fwd, is_call):
    return {"instrument_name": name, "mark_iv": iv, "underlying_price": fwd,
            "bid_price": 0.001, "ask_price": 0.002, "volume": 1.0, "open_interest": 5.0}


def test_build_skew_snapshot_computes_delta_and_skew():
    # 一个到期:dte ~30 天,forward 100;构造 put/call 使 delta 接近 ±0.25
    expiry_ms = _NOW + 30 * _DAY
    import services.research.vol_factors as vf
    dte = (expiry_ms + 8 * 3_600_000 - _NOW) / (365 * _DAY)
    k_put, k_call = 90.0, 112.0
    iv_put, iv_call = 60.0, 50.0
    book = [
        _opt("BTC-28AUG26-90-P", 90.0, iv_put, 100.0, False),
        _opt("BTC-28AUG26-112-C", 112.0, iv_call, 100.0, True),
    ]
    entries = df.parse_option_entries(book, currency="BTC")
    for e in entries:
        e["expiry_day_ms"] = expiry_ms             # 对齐构造的到期(名字解析独立测过)
    snap = df.build_skew_snapshot(entries, now_ms=_NOW)
    assert len(snap["expiries"]) == 1
    s = snap["expiries"][0]
    d_put = vf.bs_delta(100.0, k_put, dte, iv_put / 100, False)
    d_call = vf.bs_delta(100.0, k_call, dte, iv_call / 100, True)
    assert abs(abs(s["put_delta"]) - 0.25) < 0.20 and abs(s["call_delta"] - 0.25) < 0.20
    assert s["skew"] == pytest.approx(iv_put - iv_call)
    assert s["n_entries"] == 2 and s["forward"] == 100.0
    assert d_put == pytest.approx(s["put_delta"]) and d_call == pytest.approx(s["call_delta"])


def test_build_skew_snapshot_skips_expired_expiry():
    entries = [{"instrument_name": "BTC-25MAR22-40000-P", "expiry": "25MAR22",
                "expiry_day_ms": _NOW - 10 * _DAY, "strike": 40000.0, "is_call": False,
                "mark_iv": 60.0, "underlying_price": 44000.0, "bid_price": None,
                "ask_price": None, "volume": 0.0, "open_interest": 0.0}]
    snap = df.build_skew_snapshot(entries, now_ms=_NOW)
    assert snap["expiries"] == []                  # dte<=0 不外推
    assert snap["entries"][0]["delta"] is None


def test_update_options_snapshot_is_idempotent_per_day(tmp_path):
    path = str(tmp_path / "opts.json")
    ccy_snaps = {"BTC": {"expiries": [{"expiry": "28AUG26", "skew": 5.0}], "entries": []}}
    doc1 = df.update_options_snapshot(path, ccy_snaps, now_ms=_NOW)
    doc2 = df.update_options_snapshot(path, {"BTC": {"expiries": [], "entries": []}},
                                      now_ms=_NOW)                     # 同日重跑覆盖同 key
    assert len(doc2["snapshots"]) == 1
    nxt = df.update_options_snapshot(path, ccy_snaps, now_ms=_NOW + _DAY)
    assert len(nxt["snapshots"]) == 2                                  # 次日向前积累
    on_disk = json.loads(open(path, encoding="utf-8").read())
    assert on_disk["provenance"] == "REAL_DERIBIT_OPTIONS_BOOK"
    assert doc1["snapshots"] != doc2["snapshots"]                      # 覆盖生效


# --- 端到端 main:水位线幂等 + 未来数据守卫 ---

def _fake_book(url, headers):
    return [_opt("BTC-28AUG26-90000-P", 90000, 60.0, 100.0, False)]


def test_main_watermark_idempotent_second_run(tmp_path, monkeypatch):
    db = str(tmp_path / "deribit_vol.db")
    out = str(tmp_path / "snap.json")
    opts = str(tmp_path / "opts.json")
    today_idx = (_NOW - _T0) // _DAY
    monkeypatch.setattr(df.time, "time", lambda: _NOW / 1000)
    monkeypatch.setattr(df, "fetch_option_book", lambda ccy, http_get=None: [])
    monkeypatch.setattr(df, "fetch_dvol",
                        lambda ccy, **kw: df.normalize_records(
                            df.parse_dvol({"data": [_dvol_row(today_idx - 2),
                                                    _dvol_row(today_idx - 1)]},
                                          currency=ccy).values(), df.DERIBIT_SPEC))

    def fake_chart(instrument, **kw):
        if instrument == "BTC-PERPETUAL":
            rows = df.parse_chart(_chart([today_idx - 2, today_idx - 1]),
                                  instrument=instrument)
            return df.normalize_records(rows.values(), df.DERIBIT_SPEC)
        raise df.DeribitFetchError("Instrument not found")

    monkeypatch.setattr(df, "fetch_chart_rows", fake_chart)

    snap1 = df.main(["--currencies", "BTC", "--db", db, "--out", out,
                     "--options-out", opts, "--sleep", "0"])
    assert snap1["datasets"]["DVOL-BTC"]["ingested"] == 2
    assert snap1["datasets"]["BTC-PERPETUAL"]["ingested"] == 2
    assert snap1["unavailable"]                                      # 未上市月度诚实记录

    snap2 = df.main(["--currencies", "BTC", "--db", db, "--out", out,
                     "--options-out", opts, "--sleep", "0"])
    assert snap2["datasets"]["DVOL-BTC"]["ingested"] == 0            # 水位幂等:零新行
    assert snap2["datasets"]["BTC-PERPETUAL"]["ingested"] == 0
    con = __import__("sqlite3").connect(db)
    n = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    con.close()
    assert n == 4


def test_main_skips_settled_future_once_complete(tmp_path, monkeypatch):
    db = str(tmp_path / "deribit_vol.db")
    out = str(tmp_path / "snap.json")
    today_idx = (_NOW - _T0) // _DAY
    monkeypatch.setattr(df.time, "time", lambda: _NOW / 1000)
    monkeypatch.setattr(df, "fetch_option_book", lambda ccy, http_get=None: [])
    monkeypatch.setattr(df, "fetch_dvol", lambda *a, **k: [])
    # 已结算合约:数据已覆盖到到期日 → 永久跳过(不发请求)
    settled = ("BTC-25MAR22", df._ymd_to_day_ms(2022, 3, 25))
    store = SqlitePITStore(db, create=True)
    row = {"sym": settled[0], "ts": settled[1], "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0}
    rows = df.normalize_records([row], df.DERIBIT_SPEC)
    ingest_provider_rows(rows, store, dataset_id="seed", now=_NOW, time_unit="ms")

    calls = []

    def fake_fetch(instrument, **kw):
        calls.append(instrument)
        return []

    monkeypatch.setattr(df, "fetch_chart_rows", fake_fetch)
    monkeypatch.setattr(df, "monthly_future_names", lambda *a, **k: [settled])
    monkeypatch.setattr(df, "_http_get_json", None)
    df.main(["--currencies", "BTC", "--db", db, "--out", out,
             "--options-out", str(tmp_path / "o.json"), "--sleep", "0"])
    assert settled[0] not in calls                                   # 已结算完整 → 跳过
    assert "BTC-PERPETUAL" in calls                                  # 永续永远增量


def test_ingest_future_guard_rejects_late_rows(tmp_path):
    rows = df.normalize_records(
        [{"sym": "DVOL-BTC", "ts": _NOW + 5 * _DAY, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0}],
        df.DERIBIT_SPEC)
    store = SqlitePITStore(str(tmp_path / "d.db"), create=True)
    rep = ingest_provider_rows(rows, store, dataset_id="x", now=_NOW, time_unit="ms")
    assert rep.bars_ingested == 0 and len(rep.rejected) == 1         # 未来行被拒
