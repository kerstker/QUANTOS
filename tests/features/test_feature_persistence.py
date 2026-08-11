"""Rank #15 — the feature layer gains persistence (was in-memory only, lost on restart) +
a PIT feature-value materialization store (was recomputed from raw bars every run).

- SqliteFeatureRegistryStore mirrors the run_registry sqlite pattern; each version carries a
  leakage_passed flag + code_version so a cached feature is auditable.
- SqliteFeatureValueStore clones the fundamental_store bitemporal schema (+ feature_version):
  knowledge_time>=event_time blocks boundary lookahead, as_of() filters knowledge_time<=t.
Both fail-closed on an uninitialized/corrupt store."""
import os
import sqlite3

import pytest

from quantos.contracts.feature import (
    FeatureDefinition, FeatureRegistryItem, FeatureStatus, FeatureValue)
from services.data.persistent_store import DataStoreError
from services.features.registry_store import SqliteFeatureRegistryStore
from services.features import value_store as value_store_module
from services.features.value_store import SqliteFeatureValueStore


def _item(fid="mom_10", version="v1", status=FeatureStatus.DRAFT):
    return FeatureRegistryItem(
        definition=FeatureDefinition(feature_id=fid, name="10d momentum", formula="close/close[10]-1"),
        version=version, owner="research", status=status)


# ---- registry persistence ----

def test_registry_persists_across_reopen(tmp_path):
    path = os.path.join(str(tmp_path), "reg.db")
    store = SqliteFeatureRegistryStore(path, create=True)
    store.register(_item(), leakage_passed=True, code_version="abc123")
    reopened = SqliteFeatureRegistryStore(path)                # no create -> must find the schema+row
    got = reopened.get("mom_10", "v1")
    assert got.definition.feature_id == "mom_10" and got.status == FeatureStatus.DRAFT
    assert reopened.has("mom_10") and reopened.versions("mom_10") == ["v1"]
    audit = reopened.audit("mom_10", "v1")
    assert audit["leakage_passed"] is True and audit["code_version"] == "abc123"
    assert len(audit["history"]) == 1
    assert audit["history"][0]["operation"] == "REGISTER"
    assert len(audit["definition_sha256"]) == 64


def test_registry_duplicate_register_raises(tmp_path):
    store = SqliteFeatureRegistryStore(os.path.join(str(tmp_path), "r.db"), create=True)
    store.register(_item())
    with pytest.raises(ValueError):
        store.register(_item())


def test_registry_records_definition_lineage_metadata(tmp_path):
    path = os.path.join(str(tmp_path), "lineage.db")
    store = SqliteFeatureRegistryStore(path, create=True)
    store.register(
        _item(),
        leakage_passed=True,
        code_version="abc123",
        source_dataset_id="bars",
        source_version="bars-v7",
        lineage={"pit_rule": "knowledge_time<=as_of"},
        recorded_at_ms=123,
    )
    event = store.audit("mom_10", "v1")["history"][0]
    assert event["recorded_at_ms"] == 123
    assert event["metadata"] == {
        "pit_rule": "knowledge_time<=as_of",
        "source_dataset_id": "bars",
        "source_version": "bars-v7",
    }


def test_registry_history_sql_layer_is_append_only(tmp_path):
    path = os.path.join(str(tmp_path), "history.db")
    store = SqliteFeatureRegistryStore(path, create=True)
    store.register(_item())
    import sqlite3
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE feature_registry_history SET status='VALIDATED' WHERE seq=1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM feature_registry_history WHERE seq=1")
    conn.close()


def test_registry_set_status_persists(tmp_path):
    path = os.path.join(str(tmp_path), "s.db")
    store = SqliteFeatureRegistryStore(path, create=True)
    store.register(_item())
    store.set_status("mom_10", "v1", FeatureStatus.VALIDATED)
    reopened = SqliteFeatureRegistryStore(path)
    assert reopened.get("mom_10", "v1").status == FeatureStatus.VALIDATED
    history = reopened.audit("mom_10", "v1")["history"]
    assert [entry["operation"] for entry in history] == ["REGISTER", "STATUS"]
    assert history[-1]["status"] == FeatureStatus.VALIDATED.value


def test_registry_detects_current_row_tamper_against_history(tmp_path):
    path = os.path.join(str(tmp_path), "tamper.db")
    store = SqliteFeatureRegistryStore(path, create=True)
    store.register(_item(), leakage_passed=True, code_version="abc123")
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("UPDATE feature_registry SET status=? WHERE feature_id=? AND version=?",
                 (FeatureStatus.VALIDATED.value, "mom_10", "v1"))
    conn.commit()
    conn.close()
    reopened = SqliteFeatureRegistryStore(path)
    with pytest.raises(DataStoreError, match="history"):
        reopened.get("mom_10", "v1")


def test_registry_versions_are_isolated(tmp_path):
    store = SqliteFeatureRegistryStore(os.path.join(str(tmp_path), "v.db"), create=True)
    store.register(_item(version="v1"))
    store.register(_item(version="v2"))
    assert store.versions("mom_10") == ["v1", "v2"]


def test_registry_fail_closed_on_uninitialized(tmp_path):
    store = SqliteFeatureRegistryStore(os.path.join(str(tmp_path), "empty.db"))   # create=False
    with pytest.raises(DataStoreError):
        store.has("mom_10")


# ---- value materialization store (bitemporal) ----

def _fv(et, kt, val, fid="mom_10", inst="BTC"):
    return FeatureValue(feature_id=fid, instrument_id=inst, event_time=et, knowledge_time=kt, value=val)


def test_value_store_persists_and_reads_pit(tmp_path):
    path = os.path.join(str(tmp_path), "fv.db")
    store = SqliteFeatureValueStore(path, create=True)
    store.add(_fv(100, 100, 0.5), version="v1")
    store.add(_fv(200, 200, 0.7), version="v1")
    reopened = SqliteFeatureValueStore(path)
    # as_of 150 sees only the first value (kt=200 not yet knowable) — no lookahead
    seen = reopened.as_of("mom_10", "v1", "BTC", as_of_time=150)
    assert [v.value for v in seen] == [0.5]
    assert reopened.latest_as_of("mom_10", "v1", "BTC", as_of_time=250).value == 0.7


def test_value_store_as_of_truncation_invariance(tmp_path):
    store = SqliteFeatureValueStore(os.path.join(str(tmp_path), "t.db"), create=True)
    store.add_many([_fv(100, 100, 0.5), _fv(200, 200, 0.7), _fv(300, 300, 0.9)], version="v1")
    # appending future rows does not change what an earlier as_of sees
    assert [v.value for v in store.as_of("mom_10", "v1", "BTC", 200)] == [0.5, 0.7]


def test_value_store_tail_as_of_is_bounded_ordered_and_pit(tmp_path):
    store = SqliteFeatureValueStore(os.path.join(str(tmp_path), "tail.db"), create=True)
    store.add_many(
        [_fv(index, index, float(index)) for index in range(1, 11)],
        version="v1",
    )

    seen = store.tail_as_of("mom_10", "v1", "BTC", as_of_time=8, limit=3)

    assert [(value.event_time, value.value) for value in seen] == [
        (6, 6.0),
        (7, 7.0),
        (8, 8.0),
    ]
    assert store.latest_knowledge_time("mom_10", "v1", "BTC") == 10
    assert store.latest_cursor("mom_10", "v1", "BTC") == (10, 10)
    assert store.latest_knowledge_time("missing", "v1", "BTC") is None


@pytest.mark.parametrize("limit", [0, -1, True])
def test_value_store_tail_as_of_rejects_invalid_limit(limit):
    store = SqliteFeatureValueStore(":memory:", create=True)
    with pytest.raises(ValueError, match="limit"):
        store.tail_as_of("mom_10", "v1", "BTC", as_of_time=1, limit=limit)


def test_value_store_versions_are_isolated(tmp_path):
    store = SqliteFeatureValueStore(os.path.join(str(tmp_path), "vv.db"), create=True)
    store.add(_fv(100, 100, 0.5), version="v1")
    store.add(_fv(100, 100, 9.9), version="v2")
    assert store.latest_as_of("mom_10", "v1", "BTC", 100).value == 0.5
    assert store.latest_as_of("mom_10", "v2", "BTC", 100).value == 9.9


def test_value_store_rejects_boundary_lookahead():
    with pytest.raises(Exception):                       # kt < et -> BitemporalRecord refuses to construct
        _fv(200, 100, 0.5)


def test_value_store_fail_closed_on_uninitialized(tmp_path):
    store = SqliteFeatureValueStore(os.path.join(str(tmp_path), "e.db"))   # create=False
    with pytest.raises(DataStoreError):
        store.as_of("mom_10", "v1", "BTC", 100)


def test_value_store_batch_write_is_atomic_and_reproducible(tmp_path):
    path = os.path.join(str(tmp_path), "atomic.db")
    store = SqliteFeatureValueStore(path, create=True)
    values = [_fv(100, 100, 0.5), _fv(200, 200, 0.7)]
    assert store.add_many(values, version="v1") == 2
    assert store.add_many(values, version="v1") == 0
    assert [v.value for v in store.as_of("mom_10", "v1", "BTC", 999)] == [0.5, 0.7]


def test_value_store_batch_inserts_follow_primary_key_locality(tmp_path):
    path = os.path.join(str(tmp_path), "ordered.db")
    store = SqliteFeatureValueStore(path, create=True)
    store._conn.executescript(
        """
        CREATE TABLE observed_insert_order (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            feature_id TEXT NOT NULL,
            instrument_id TEXT NOT NULL,
            event_time INTEGER NOT NULL,
            knowledge_time INTEGER NOT NULL
        );
        CREATE TRIGGER observe_feature_insert_order
        AFTER INSERT ON feature_values
        BEGIN
            INSERT INTO observed_insert_order(
                feature_id,
                instrument_id,
                event_time,
                knowledge_time
            ) VALUES (
                NEW.feature_id,
                NEW.instrument_id,
                NEW.event_time,
                NEW.knowledge_time
            );
        END;
        """
    )
    values = [
        _fv(300, 300, 3.0, fid="z_feature", inst="ETH"),
        _fv(200, 200, 2.0, fid="a_feature", inst="BTC"),
        _fv(100, 100, 1.0, fid="a_feature", inst="BTC"),
        _fv(150, 150, 1.5, fid="a_feature", inst="ADA"),
    ]

    assert store.add_many(values, version="v1") == 4
    observed = store._conn.execute(
        "SELECT feature_id, instrument_id, event_time, knowledge_time"
        " FROM observed_insert_order ORDER BY seq"
    ).fetchall()
    assert [tuple(row) for row in observed] == [
        ("a_feature", "ADA", 150, 150),
        ("a_feature", "BTC", 100, 100),
        ("a_feature", "BTC", 200, 200),
        ("z_feature", "ETH", 300, 300),
    ]


def test_value_store_locality_sort_preserves_equal_key_first_value(tmp_path):
    store = SqliteFeatureValueStore(
        os.path.join(str(tmp_path), "stable-order.db"),
        create=True,
    )
    first = _fv(100, 100, 1.0)
    same_identity_later = _fv(100, 100, 9.0)

    assert store.add_many(
        [same_identity_later, _fv(200, 200, 2.0), first],
        version="v1",
    ) == 2
    retained = store.latest_as_of("mom_10", "v1", "BTC", 100)
    assert retained is not None
    assert retained.value == 9.0


def test_value_store_row_count_counter_tracks_only_inserted_rows(tmp_path):
    path = os.path.join(str(tmp_path), "count.db")
    store = SqliteFeatureValueStore(path, create=True)
    values = [_fv(100, 100, 0.5), _fv(200, 200, 0.7)]

    assert store.add_many(values, version="v1") == 2
    assert store.row_count() == 2
    assert store.add_many(values, version="v1") == 0
    assert store.row_count() == 2
    assert store.add(_fv(300, 300, 0.9), version="v1") is True
    assert store.row_count() == 3
    store.close()

    reopened = SqliteFeatureValueStore(path, create=True)
    assert reopened.row_count() == 3


def test_value_store_row_count_bootstraps_existing_archive_exactly(tmp_path):
    path = os.path.join(str(tmp_path), "legacy-count.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE feature_values (
            feature_id TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            instrument_id TEXT NOT NULL,
            event_time INTEGER NOT NULL,
            knowledge_time INTEGER NOT NULL,
            value REAL,
            PRIMARY KEY (
                feature_id,
                feature_version,
                instrument_id,
                event_time,
                knowledge_time
            )
        );
        """
    )
    conn.executemany(
        "INSERT INTO feature_values VALUES (?,?,?,?,?,?)",
        [
            ("mom_10", "v1", "BTC", 100, 100, 0.5),
            ("mom_10", "v1", "BTC", 200, 200, 0.7),
        ],
    )
    conn.commit()
    conn.close()

    store = SqliteFeatureValueStore(path, create=True)
    assert store.row_count() == 2
    assert store.add(_fv(300, 300, 0.9), version="v1") is True
    assert store.row_count() == 3


def test_value_store_series_coverage_bootstraps_and_tracks_exactly(tmp_path):
    path = os.path.join(str(tmp_path), "legacy-series-coverage.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE feature_values (
            feature_id TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            instrument_id TEXT NOT NULL,
            event_time INTEGER NOT NULL,
            knowledge_time INTEGER NOT NULL,
            value REAL,
            PRIMARY KEY (
                feature_id,
                feature_version,
                instrument_id,
                event_time,
                knowledge_time
            )
        );
        """
    )
    conn.executemany(
        "INSERT INTO feature_values VALUES (?,?,?,?,?,?)",
        [
            ("mom_10", "v1", "BTC", 100, 100, 0.5),
            ("mom_10", "v1", "BTC", 200, 200, 0.7),
            ("mom_10", "v1", "ETH", 150, 150, 0.6),
            ("value", "v2", "BTC", 50, 50, 1.0),
        ],
    )
    conn.commit()
    conn.close()

    store = SqliteFeatureValueStore(path, create=True)
    rows = store._conn.execute(
        "SELECT feature_id,feature_version,instrument_id,row_count,"
        "first_event_time,last_event_time FROM feature_value_series_metadata "
        "ORDER BY feature_id,feature_version,instrument_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("mom_10", "v1", "BTC", 2, 100, 200),
        ("mom_10", "v1", "ETH", 1, 150, 150),
        ("value", "v2", "BTC", 1, 50, 50),
    ]

    assert store.add(_fv(300, 300, 0.9), version="v1") is True
    assert store.add(_fv(300, 300, 9.9), version="v1") is False
    updated = store._conn.execute(
        "SELECT row_count,first_event_time,last_event_time "
        "FROM feature_value_series_metadata "
        "WHERE feature_id='mom_10' AND feature_version='v1' "
        "AND instrument_id='BTC'"
    ).fetchone()
    assert tuple(updated) == (3, 100, 300)


def test_value_store_daily_row_count_does_not_scan_feature_archive(
    tmp_path,
    monkeypatch,
):
    path = os.path.join(str(tmp_path), "bounded-count.db")
    store = SqliteFeatureValueStore(path, create=True)
    store.add_many(
        [_fv(index, index, float(index)) for index in range(1, 101)],
        version="v1",
    )
    store.close()

    statements = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(value_store_module.sqlite3, "connect", traced_connect)
    reopened = SqliteFeatureValueStore(path, create=True)
    assert reopened.row_count() == 100
    normalized = [" ".join(statement.upper().split()) for statement in statements]
    assert not any(
        "COUNT(*) FROM FEATURE_VALUES" in statement for statement in normalized
    )
    assert not any(
        "GROUP BY FEATURE_ID,FEATURE_VERSION,INSTRUMENT_ID" in statement
        for statement in normalized
    )


def test_value_store_series_metadata_inconsistency_fails_closed(tmp_path):
    path = os.path.join(str(tmp_path), "inconsistent-series.db")
    store = SqliteFeatureValueStore(path, create=True)
    store.add(_fv(100, 100, 0.5), version="v1")
    store._conn.execute(
        "UPDATE feature_value_series_metadata SET row_count=row_count+1"
    )
    store._conn.commit()
    store.close()

    with pytest.raises(DataStoreError, match="series metadata is inconsistent"):
        SqliteFeatureValueStore(path, create=True)


def test_value_store_row_count_metadata_missing_fails_closed(tmp_path):
    path = os.path.join(str(tmp_path), "missing-count.db")
    store = SqliteFeatureValueStore(path, create=True)
    store.add(_fv(100, 100, 0.5), version="v1")
    store._conn.execute("DELETE FROM feature_value_store_metadata")
    store._conn.commit()

    with pytest.raises(DataStoreError, match="row-count metadata missing"):
        store.row_count()
    with pytest.raises(DataStoreError, match="row-count metadata missing"):
        store.add(_fv(200, 200, 0.7), version="v1")
    store.close()
    with pytest.raises(DataStoreError, match="row-count metadata missing"):
        SqliteFeatureValueStore(path, create=True)


def test_value_store_rejects_invalid_as_of_time():
    store = SqliteFeatureValueStore(":memory:", create=True)
    store.add(_fv(100, 100, 0.5), version="v1")
    with pytest.raises(ValueError):
        store.as_of("mom_10", "v1", "BTC", -1)
    with pytest.raises(ValueError):
        store.latest_as_of("mom_10", "v1", "BTC", True)
