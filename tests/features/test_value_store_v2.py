"""Schema v2 (727 §6.2 FactorObservation) for SqliteFeatureValueStore: lazy migration,
NULL-backfilled reads on v1 stores, v2 write path, and v1-path bit-identity."""
import sqlite3

import pytest

from quantos.contracts.feature import FeatureValue
from services.features.value_store import _V2_COLUMNS, SqliteFeatureValueStore

_V1_SCHEMA = """
CREATE TABLE feature_values (
    feature_id      TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    instrument_id   TEXT NOT NULL,
    event_time      INTEGER NOT NULL,
    knowledge_time  INTEGER NOT NULL,
    value           REAL,
    PRIMARY KEY (feature_id, feature_version, instrument_id, event_time, knowledge_time)
);
"""


def _fv(fid="f", inst="BTC", et=100, kt=100, value=1.5):
    return FeatureValue(feature_id=fid, instrument_id=inst,
                        event_time=et, knowledge_time=kt, value=value)


def _v1_db(tmp_path):
    """A hand-built v1 store (no v2 columns) with one historical row."""
    path = str(tmp_path / "v1.db")
    conn = sqlite3.connect(path)
    conn.executescript(_V1_SCHEMA)
    conn.execute(
        "INSERT INTO feature_values VALUES (?,?,?,?,?,?)",
        ("old", "v1", "BTC", 100, 100, 9.0),
    )
    conn.commit()
    conn.close()
    return path


def test_v1_store_reads_back_null_v2_columns(tmp_path):
    store = SqliteFeatureValueStore(_v1_db(tmp_path))      # create=False, no migration yet
    obs = store.as_of_observations("old", "v1", "BTC", 100)
    assert len(obs) == 1
    assert obs[0]["value"] == 9.0
    for column in _V2_COLUMNS:
        assert column in obs[0] and obs[0][column] is None  # honest "materialized before v2"


def test_first_v2_write_triggers_migration(tmp_path):
    store = SqliteFeatureValueStore(_v1_db(tmp_path))
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(feature_values)")}
    assert not (_V2_COLUMNS.keys() & cols)                  # still v1 before any v2 write
    store.add(_fv(), version="v2", computed_at_ms=5000, dataset_version="ds-1",
              materializer_version="1.0.0", quality_verdict="OK")
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(feature_values)")}
    assert _V2_COLUMNS.keys() <= cols
    old = store.as_of_observations("old", "v1", "BTC", 100)[0]
    assert all(old[c] is None for c in _V2_COLUMNS)         # history NOT backfilled
    new = store.as_of_observations("f", "v2", "BTC", 100)[0]
    assert new["computed_at_ms"] == 5000
    assert new["dataset_version"] == "ds-1"
    assert new["materializer_version"] == "1.0.0"
    assert new["quality_verdict"] == "OK"


def test_migration_is_idempotent_across_writes_and_reopen(tmp_path):
    path = _v1_db(tmp_path)
    store = SqliteFeatureValueStore(path)
    store.add(_fv(), version="v2", computed_at_ms=1, dataset_version="d",
              materializer_version="m", quality_verdict="OK")
    store.add(_fv(et=101, kt=101), version="v2", computed_at_ms=2, dataset_version="d",
              materializer_version="m", quality_verdict="OK")   # second v2 write: no-op migration
    store2 = SqliteFeatureValueStore(path)                        # reopen, already v2
    store2.add(_fv(et=102, kt=102), version="v2", computed_at_ms=3, dataset_version="d",
               materializer_version="m", quality_verdict="OK")
    assert len(store2.as_of_observations("f", "v2", "BTC", 10**15)) == 3


def test_v1_write_path_never_migrates_and_stays_bit_identical(tmp_path):
    store = SqliteFeatureValueStore(_v1_db(tmp_path))
    store.add(_fv(), version="v1x")                          # no v2 kwargs -> old 6-col insert
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(feature_values)")}
    assert not (_V2_COLUMNS.keys() & cols)                   # no migration happened
    fv = store.latest_as_of("f", "v1x", "BTC", 100)
    assert fv == _fv()


def test_v2_metadata_validation():
    store = SqliteFeatureValueStore(":memory:", create=True)
    with pytest.raises(ValueError):
        store.add(_fv(), version="v", computed_at_ms=-1)
    with pytest.raises(ValueError):
        store.add(_fv(), version="v", computed_at_ms=True)
    with pytest.raises(ValueError):
        store.add(_fv(), version="v", dataset_version="")
    with pytest.raises(ValueError):
        store.add(_fv(), version="v", quality_verdict=123)


def test_v2_write_idempotent_insert_or_ignore():
    store = SqliteFeatureValueStore(":memory:", create=True)
    kw = dict(computed_at_ms=7, dataset_version="d", materializer_version="m",
              quality_verdict="OK")
    assert store.add(_fv(), version="v", **kw) is True
    assert store.add(_fv(), version="v", **kw) is False      # same PK -> ignored
    assert len(store.as_of_observations("f", "v", "BTC", 100)) == 1


def test_as_of_observations_pit_filter_and_order():
    store = SqliteFeatureValueStore(":memory:", create=True)
    store.add_many([_fv(et=100, kt=100), _fv(et=101, kt=105)], version="v",
                   computed_at_ms=1, dataset_version="d",
                   materializer_version="m", quality_verdict="OK")
    assert [o["event_time"] for o in store.as_of_observations("f", "v", "BTC", 104)] == [100]
    assert [o["event_time"] for o in store.as_of_observations("f", "v", "BTC", 105)] == [100, 101]
    with pytest.raises(ValueError):
        store.as_of_observations("f", "v", "BTC", -1)
