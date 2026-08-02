import sqlite3

import pytest

from services.features.realtime_feature_batch_store import (
    RealtimeFeatureBatchStore,
    RealtimeFeatureBatchStoreError,
)


def _batch(batch_id: str = "batch-1") -> dict:
    return {
        "schema_version": 1,
        "batch_id": batch_id,
        "observed_at_ms": 100,
        "source": {"snapshot_digest": "a" * 64},
    }


def test_batch_journal_is_idempotent_and_hash_chained(tmp_path):
    path = tmp_path / "batches.db"
    store = RealtimeFeatureBatchStore(str(path), create=True)
    try:
        assert store.append(_batch()) is True
        assert store.append(_batch()) is False
        assert store.row_count() == 1
        assert store.latest()["batch_id"] == "batch-1"
        assert store.chain_valid() is True
        changed = _batch()
        changed["source"] = {"snapshot_digest": "b" * 64}
        with pytest.raises(
            RealtimeFeatureBatchStoreError,
            match="reused with different content",
        ):
            store.append(changed)
    finally:
        store.close()


def test_batch_journal_detects_tamper(tmp_path):
    path = tmp_path / "batches.db"
    store = RealtimeFeatureBatchStore(str(path), create=True)
    store.append(_batch())
    store.close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER realtime_feature_batches_no_update")
    conn.execute("UPDATE realtime_feature_batches SET payload_json='{}'")
    conn.commit()
    conn.close()
    reopened = RealtimeFeatureBatchStore(str(path))
    try:
        assert reopened.chain_valid() is False
    finally:
        reopened.close()
