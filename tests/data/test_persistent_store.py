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


def test_same_identity_with_changed_payload_fails_closed():
    s = _store()
    assert s.add("X", _bar("X", 10, 10, 100.0)) is True

    with pytest.raises(DataStoreError, match="identity conflict"):
        s.add("X", _bar("X", 10, 10, 200.0))

    rows = s.as_of("X", as_of_time=60)
    assert len(rows) == 1
    assert rows[0].close == 100.0


def test_add_rejects_key_and_payload_instrument_mismatch():
    s = _store()

    with pytest.raises(DataStoreError, match="bar.instrument_id"):
        s.add("BTC", _bar("ETH", 10, 10, 100.0))

    assert s.keys() == []


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


def test_fetch_bars_pit_excludes_late_knowledge_includes_boundary():
    s = _store()
    s.add("X", _bar("X", 10, 10, 100.0))   # kt == et: knowable on its own event date -> kept
    s.add("X", _bar("X", 20, 50, 101.0))   # kt > et: NOT knowable by its event date -> dropped
    s.add("X", _bar("X", 30, 30, 102.0))
    out = s.fetch_bars_pit("X", 0, 100)
    assert [(b.event_time, b.knowledge_time) for b in out] == [(10, 10), (30, 30)]


def test_fetch_bars_pit_respects_window_bounds():
    s = _store()
    s.add_many("X", [_bar("X", t, t, 100.0 + t) for t in (10, 20, 30)])
    assert [b.event_time for b in s.fetch_bars_pit("X", 15, 25)] == [20]
    assert s.fetch_bars_pit("X", 31, 40) == []
    assert s.fetch_bars_pit("NOPE", 0, 100) == []


def test_fetch_bars_pit_parity_with_fetch_bars_on_clean_data():
    s = _store()
    s.add_many("X", [_bar("X", t, t, 100.0 + t) for t in (10, 20, 30)])
    s.add_many("Y", [_bar("Y", t, t, 50.0 + t) for t in (5, 15)])
    for key in ("X", "Y"):
        for lo, hi in ((0, 100), (10, 20), (12, 18)):
            assert s.fetch_bars_pit(key, lo, hi) == s.fetch_bars(key, lo, hi)


def test_uninitialized_store_fails_closed(tmp_path):
    p = str(tmp_path / "empty.db")
    sqlite3.connect(p).close()
    with pytest.raises(DataStoreError):
        SqlitePITStore(p, create=False).as_of("X", as_of_time=10)


def test_atomic_batch_rolls_back_every_row_on_database_failure():
    store = _store()
    store._conn.execute(
        "CREATE TRIGGER reject_bad BEFORE INSERT ON bars "
        "WHEN NEW.key='BAD' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    with pytest.raises(DataStoreError, match="atomic batch insert failed"):
        store.add_batch_atomic([
            _bar("X", 10, 10, 100.0),
            _bar("BAD", 20, 20, 101.0),
        ])
    assert store.as_of("X", as_of_time=60) == []
    assert store.as_of("BAD", as_of_time=60) == []


def test_atomic_batch_conflict_rolls_back_new_rows_and_preserves_original():
    store = _store()
    store.add("X", _bar("X", 10, 10, 100.0))

    with pytest.raises(DataStoreError, match="identity conflict"):
        store.add_batch_atomic(
            [
                _bar("Y", 20, 20, 50.0),
                _bar("X", 10, 10, 200.0),
            ]
        )

    assert store.as_of("Y", as_of_time=60) == []
    assert store.latest_as_of("X", as_of_time=60).close == 100.0


def test_atomic_batch_rejects_internal_identity_conflict_before_writing():
    store = _store()

    with pytest.raises(DataStoreError, match="conflicting content"):
        store.add_batch_atomic(
            [
                _bar("X", 10, 10, 100.0),
                _bar("X", 10, 10, 200.0),
            ]
        )

    assert store.keys() == []
