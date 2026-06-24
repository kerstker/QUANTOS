import sqlite3
import pytest
from quantos.contracts.data import Bar
from services.data.persistent_store import SqlitePITStore, DataStoreError


def _bar(inst, et, kt, close):
    # knowledge_time >= event_time (bitemporal invariant: a bar is known at/after it occurs)
    return Bar(instrument_id=inst, event_time=et, knowledge_time=kt,
               open=close, high=close + 1, low=close - 1, close=close, volume=100)


def _store():
    return SqlitePITStore(":memory:", create=True)


def test_as_of_excludes_future_knowledge_then_includes_it():
    s = _store()
    s.add("X", _bar("X", 10, 10, 100.0))     # known at t=10
    s.add("X", _bar("X", 20, 50, 101.0))     # restated/known only at t=50
    early = s.as_of("X", as_of_time=30)
    assert [b.event_time for b in early] == [10]            # the kt=50 bar is hidden
    late = s.as_of("X", as_of_time=60)
    assert [b.event_time for b in late] == [10, 20]


def test_restatement_kept_and_latest_as_of_is_newest_knowledge():
    s = _store()
    s.add("X", _bar("X", 10, 10, 100.0))
    s.add("X", _bar("X", 10, 40, 105.0))     # restatement of the same event, later knowledge
    rows = s.as_of("X", as_of_time=60)
    assert len(rows) == 2
    assert s.latest_as_of("X", as_of_time=60).close == 105.0


def test_dedup_identical_bar():
    s = _store()
    assert s.add("X", _bar("X", 10, 10, 100.0)) is True
    assert s.add("X", _bar("X", 10, 10, 100.0)) is False
    assert len(s.as_of("X", as_of_time=60)) == 1


def test_persists_across_connections(tmp_path):
    p = str(tmp_path / "lake.db")
    s1 = SqlitePITStore(p, create=True)
    s1.add("X", _bar("X", 10, 10, 100.0))
    s2 = SqlitePITStore(p)
    assert [b.event_time for b in s2.as_of("X", as_of_time=60)] == [10]


def test_fetch_bars_window_and_keys():
    s = _store()
    s.add_many("X", [_bar("X", t, t, 100.0 + t) for t in (10, 20, 30)])
    assert [b.event_time for b in s.fetch_bars("X", 15, 25)] == [20]
    assert s.keys() == ["X"]


def test_uninitialized_store_fails_closed(tmp_path):
    p = str(tmp_path / "empty.db")
    sqlite3.connect(p).close()
    with pytest.raises(DataStoreError):
        SqlitePITStore(p, create=False).as_of("X", as_of_time=10)
