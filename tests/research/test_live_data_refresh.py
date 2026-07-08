"""增量数据刷新(tools/live_data_refresh)可离线测的核心:水位、缺口、合并、staleness、fail-soft。
网络部分注入 fake fetch;不触真网。设计:docs/superpowers/specs/2026-07-04-forward-track-live-design.md §2.2。"""
import json
import os
import sqlite3

from tools.live_data_refresh import (_gap_days, merge_funding_days, price_watermarks,
                                     refresh_funding, refresh_prices, staleness_days)

_DAY = 86_400_000
_T0 = (1_780_000_000_000 // _DAY) * _DAY          # 对齐日网格的真实纪元毫秒


def _seed_price_db(path, tickers, last_day):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE bars (key TEXT NOT NULL, event_time INTEGER NOT NULL, "
                 "knowledge_time INTEGER NOT NULL, payload_json TEXT NOT NULL, "
                 "PRIMARY KEY (key, event_time, knowledge_time))")
    for tk in tickers:
        for d in range(3):
            t = last_day - d * _DAY
            conn.execute("INSERT INTO bars VALUES (?,?,?,?)",
                         (tk, t, t, json.dumps({"instrument_id": tk, "event_time": t,
                                                "open": 1, "high": 1, "low": 1, "close": 1,
                                                "volume": 1, "knowledge_time": t})))
    conn.commit()
    conn.close()


def test_gap_days_and_watermarks(tmp_path):
    db = str(tmp_path / "px.db")
    _seed_price_db(db, ["BTC", "ETH"], _T0)
    wm = price_watermarks(db)
    assert wm == {"BTC": _T0, "ETH": _T0}
    now = _T0 + 8 * _DAY + 3600_000
    assert _gap_days(_T0, now) == 8
    assert price_watermarks(str(tmp_path / "missing.db")) == {}       # 不存在 → 空(诚实)


def test_refresh_prices_skips_fresh_and_fails_soft(tmp_path):
    db = str(tmp_path / "px.db")
    now = _T0 + 8 * _DAY
    _seed_price_db(db, ["BTC", "ETH"], _T0)
    # BTC/ETH 落后 8 天 → 都该刷;BTC 的 fetch 抛异常 → fail-soft 记录,ETH 照常
    calls = []

    def fake_fetch(tk, *, days, now_ms):
        calls.append((tk, days))
        if tk == "BTC":
            raise RuntimeError("boom")
        return [{"instrument_id": tk, "event_time": _T0 + (i + 1) * _DAY, "open": 1.0, "high": 1.0,
                 "low": 1.0, "close": 1.0, "volume": 1.0, "knowledge_time": _T0 + (i + 1) * _DAY}
                for i in range(7)]

    rep = refresh_prices(["BTC", "ETH"], now_ms=now, fetch=fake_fetch, db_path=db, sleep=0)
    assert [c[0] for c in calls] == ["BTC", "ETH"]
    assert calls[0][1] >= 8                                           # 缺口 + pad
    assert rep["refreshed"] == 1 and len(rep["failed"]) == 1 and "BTC" in rep["failed"][0]
    assert rep["ingested_rows"] == 7
    assert price_watermarks(db)["ETH"] == _T0 + 7 * _DAY              # 水位前移
    # 再跑一轮:ETH 已新鲜(gap<=1)则跳过 —— 幂等
    rep2 = refresh_prices(["ETH"], now_ms=_T0 + 8 * _DAY, fetch=fake_fetch, db_path=db, sleep=0)
    assert rep2["skipped"] == 1 and rep2["refreshed"] == 0


def test_merge_funding_days_never_overwrites():
    existing = {str(_T0): 0.001}
    n = merge_funding_days(existing, {_T0: 999.0, _T0 + _DAY: 0.002})
    assert n == 1
    assert existing[str(_T0)] == 0.001                                # 历史事实不改写
    assert existing[str(_T0 + _DAY)] == 0.002


def test_refresh_funding_merges_and_reports(tmp_path):
    fp = str(tmp_path / "fund.json")
    json.dump({"BTC": {str(_T0): 0.001}}, open(fp, "w"))
    now = _T0 + 5 * _DAY

    def fake_fund(tk, *, days, now_ms):
        return {_T0 + i * _DAY: 0.0001 for i in range(1, 5)}

    rep = refresh_funding(["BTC"], now_ms=now, fetch=fake_fund, fund_path=fp, sleep=0)
    assert rep["refreshed"] == 1 and rep["new_days"] == 4
    saved = json.load(open(fp))
    assert len(saved["BTC"]) == 5
    assert rep["watermark_after"] == _T0 + 4 * _DAY


def test_staleness_summary_delisted_does_not_poison(tmp_path):
    db = str(tmp_path / "px.db")
    # BTC 新鲜、DEADCOIN 退市 800 天:survivorship-free 库的正常形态——
    # freshest 必须是 1(源还在更新),worst=800 与 n_stale=1 只是信息,不是"整源停更"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE bars (key TEXT NOT NULL, event_time INTEGER NOT NULL, "
                 "knowledge_time INTEGER NOT NULL, payload_json TEXT NOT NULL, "
                 "PRIMARY KEY (key, event_time, knowledge_time))")
    conn.execute("INSERT INTO bars VALUES (?,?,?,?)", ("BTC", _T0, _T0, "{}"))
    conn.execute("INSERT INTO bars VALUES (?,?,?,?)",
                 ("DEADCOIN", _T0 - 800 * _DAY, _T0 - 800 * _DAY, "{}"))
    conn.commit()
    conn.close()
    fp = str(tmp_path / "fund.json")
    json.dump({"BTC": {str(_T0 - 2 * _DAY): 0.001}}, open(fp, "w"))
    oip = str(tmp_path / "oi.json")
    json.dump({"_meta": {"BTC": {"start": 0, "through": _T0 - 5 * _DAY}}, "data": {}}, open(oip, "w"))
    s = staleness_days(_T0 + _DAY, db_path=db, fund_path=fp, oi_path=oip)
    assert s["prices"] == {"freshest_days": 1, "worst_days": 801, "n_stale": 1, "n_total": 2}
    assert s["funding"]["freshest_days"] == 3 and s["funding"]["n_total"] == 1
    assert s["oi_metrics"]["freshest_days"] == 6
    s2 = staleness_days(_T0, db_path=str(tmp_path / "no.db"), fund_path=str(tmp_path / "no.json"),
                        oi_path=str(tmp_path / "no2.json"))
    for src in ("prices", "funding", "oi_metrics"):                   # 缺源 → None(诚实)
        assert s2[src]["freshest_days"] is None and s2[src]["n_total"] == 0
