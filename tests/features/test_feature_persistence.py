"""Rank #15 — the feature layer gains persistence (was in-memory only, lost on restart) +
a PIT feature-value materialization store (was recomputed from raw bars every run).

- SqliteFeatureRegistryStore mirrors the run_registry sqlite pattern; each version carries a
  leakage_passed flag + code_version so a cached feature is auditable.
- SqliteFeatureValueStore clones the fundamental_store bitemporal schema (+ feature_version):
  knowledge_time>=event_time blocks boundary lookahead, as_of() filters knowledge_time<=t.
Both fail-closed on an uninitialized/corrupt store."""
import os

import pytest

from quantos.contracts.feature import (
    FeatureDefinition, FeatureRegistryItem, FeatureStatus, FeatureValue)
from services.data.persistent_store import DataStoreError
from services.features.registry_store import SqliteFeatureRegistryStore
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


def test_value_store_rejects_invalid_as_of_time():
    store = SqliteFeatureValueStore(":memory:", create=True)
    store.add(_fv(100, 100, 0.5), version="v1")
    with pytest.raises(ValueError):
        store.as_of("mom_10", "v1", "BTC", -1)
    with pytest.raises(ValueError):
        store.latest_as_of("mom_10", "v1", "BTC", True)
