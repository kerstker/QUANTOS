"""交易日历子系统:持久化、BYO 接入、双向对账(missing/ghost/coverage)、跨市场对齐。"""
import csv
import pytest
from quantos.contracts.data import Bar
from services.data.persistent_store import SqlitePITStore
from services.data.calendar_store import (
    SqliteCalendarStore, ingest_calendar_csv, reconcile_calendar, align_calendars)
from services.data.persistent_store import DataStoreError

SESS = [20240102, 20240103, 20240104, 20240105]


def _bar(inst, t, close=10.0):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t,
               open=close, high=close + 1, low=close - 1, close=close, volume=1000)


def test_store_roundtrip_and_fail_closed():
    s = SqliteCalendarStore(":memory:", create=True)
    assert s.add_sessions("XSHG", SESS) == 4
    assert s.add_session("XSHG", 20240102) is False           # PK 去重
    assert s.sessions("XSHG") == SESS
    assert s.sessions_in("XSHG", 20240103, 20240104) == [20240103, 20240104]
    assert s.is_session("XSHG", 20240105) and not s.is_session("XSHG", 20240106)
    with pytest.raises(DataStoreError):
        SqliteCalendarStore(":memory:").sessions("XSHG")      # 未初始化


def test_ingest_good_bad_and_guard(tmp_path):
    p = tmp_path / "cal.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date"])
        w.writerow(["2024-01-02"])
        w.writerow(["2024-01-03"])
        w.writerow(["not-a-date"])                            # 坏 → rejected
    s = SqliteCalendarStore(":memory:", create=True)
    rep = ingest_calendar_csv(str(p), s, "XSHG")
    assert rep.rows_read == 3 and rep.added == 2 and len(rep.rejected) == 1
    assert s.sessions("XSHG") == [20240102, 20240103]
    rep2 = ingest_calendar_csv(str(p), s, "XSHG")
    assert rep2.added == 0 and rep2.duplicates == 2           # 幂等
    s2 = SqliteCalendarStore(":memory:", create=True)
    with pytest.raises(ValueError):
        ingest_calendar_csv(str(p), s2, "XSHG", date_format="%y-%m-%d")   # %y 守卫


def test_reconcile_missing_ghost_coverage():
    cal = SqliteCalendarStore(":memory:", create=True)
    cal.add_sessions("XSHG", SESS)                            # 4 个交易日
    store = SqlitePITStore(":memory:", create=True)
    # AAA 全覆盖;BBB 缺 20240104(missing=1);CCC 全覆盖 + 20240106 幽灵 bar(非交易日)
    for t in SESS:
        store.add("AAA", _bar("AAA", t))
        store.add("CCC", _bar("CCC", t))
    for t in (20240102, 20240103, 20240105):
        store.add("BBB", _bar("BBB", t))
    store.add("CCC", _bar("CCC", 20240106))                  # ghost
    rep = reconcile_calendar(store, cal, ["AAA", "BBB", "CCC"], "XSHG", start=20240101, end=20240131)
    assert rep["total_sessions"] == 4
    assert rep["missing"] == {"AAA": 0, "BBB": 1, "CCC": 0}
    assert rep["ghost"] == {"AAA": 0, "BBB": 0, "CCC": 1}
    assert rep["coverage"]["AAA"] == pytest.approx(1.0)
    assert rep["coverage"]["BBB"] == pytest.approx(3 / 4)


def test_align_calendars():
    s = SqliteCalendarStore(":memory:", create=True)
    s.add_sessions("XSHG", [20240102, 20240103, 20240104])
    s.add_sessions("XNYS", [20240103, 20240104, 20240105])   # 1/2 中国假?对方有 1/5
    out = align_calendars(s, "XSHG", "XNYS")
    assert out["common"] == [20240103, 20240104]
    assert out["only_a"] == [20240102]
    assert out["only_b"] == [20240105]


def test_reconcile_deterministic():
    cal = SqliteCalendarStore(":memory:", create=True)
    cal.add_sessions("XSHG", SESS)
    store = SqlitePITStore(":memory:", create=True)
    for t in SESS:
        store.add("AAA", _bar("AAA", t))
    a = reconcile_calendar(store, cal, ["AAA"], "XSHG", start=20240101, end=20240131)
    b = reconcile_calendar(store, cal, ["AAA"], "XSHG", start=20240101, end=20240131)
    assert a == b
