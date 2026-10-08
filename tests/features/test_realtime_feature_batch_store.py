import sqlite3

import pytest

from services.features.realtime_feature_batch_store import (
    RealtimeFeatureBatchStore,
    RealtimeFeatureBatchStoreError,
    trusted_cursor_chain_checkpoint,
    trusted_source_chain_checkpoint,
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
        first_authority = store.authority_hash()
        assert len(first_authority) == 64
        assert store.append(_batch("batch-2")) is True
        assert store.authority_hash() != first_authority
        authority, latest = store.latest_with_authority()
        assert authority == store.authority_hash()
        assert latest is not None and latest["batch_id"] == "batch-2"
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
        with pytest.raises(
            RealtimeFeatureBatchStoreError,
            match="batch journal authority is invalid",
        ):
            reopened.authority_hash()
    finally:
        reopened.close()


def test_existing_batch_store_reopens_as_read_only_consumer(tmp_path):
    path = str(tmp_path / "batches.db")
    writer = RealtimeFeatureBatchStore(path, create=True)
    try:
        assert writer.append(_batch()) is True
        reader = RealtimeFeatureBatchStore(path, create=False)
        try:
            assert reader.latest()["batch_id"] == "batch-1"
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.append(_batch("batch-2"))
        finally:
            reader.close()
    finally:
        writer.close()


def test_latest_authority_and_checkpoint_share_one_read_snapshot(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "batches.db"
    reader = RealtimeFeatureBatchStore(str(path), create=True)
    reader.append(_batch())
    writer = RealtimeFeatureBatchStore(str(path), create=True)
    original_authority_hash = reader.authority_hash

    def interleave_append():
        authority = original_authority_hash()
        assert writer.append(_batch("batch-2")) is True
        return authority

    monkeypatch.setattr(reader, "authority_hash", interleave_append)
    try:
        authority, latest = reader.latest_with_authority()
        assert latest is not None and latest["batch_id"] == "batch-1"
        assert authority != writer.authority_hash()
    finally:
        writer.close()
        reader.close()


def test_trusted_source_checkpoint_is_loaded_from_verified_batch_authority(tmp_path):
    path = tmp_path / "batches.db"
    store = RealtimeFeatureBatchStore(str(path), create=True)
    payload = _batch()
    payload["source"] = {
        "chain_checkpoint": {
            "schema_version": 1,
            "verified_seq": 123,
            "verified_row_hash": "b" * 64,
            "full_verified_at_ms": 900,
        }
    }
    store.append(payload)
    expected_authority = store.authority_hash()
    store.close()

    checkpoint = trusted_source_chain_checkpoint(
        str(path),
        now_ms=1_000,
        full_chain_audit_interval_ms=500,
    )

    assert checkpoint == {
        "checkpoint_seq": 123,
        "checkpoint_row_hash": "b" * 64,
        "full_verified_at_ms": 900,
        "verification_mode": "INCREMENTAL_FROM_TRUSTED_BATCH",
        "batch_authority": expected_authority,
    }


def test_expired_source_checkpoint_requires_full_periodic_audit(tmp_path):
    path = tmp_path / "batches.db"
    store = RealtimeFeatureBatchStore(str(path), create=True)
    payload = _batch()
    payload["source"] = {
        "chain_checkpoint": {
            "schema_version": 1,
            "verified_seq": 123,
            "verified_row_hash": "b" * 64,
            "full_verified_at_ms": 400,
        }
    }
    store.append(payload)
    store.close()

    checkpoint = trusted_source_chain_checkpoint(
        str(path),
        now_ms=1_000,
        full_chain_audit_interval_ms=500,
    )

    assert checkpoint["checkpoint_seq"] == 0
    assert checkpoint["checkpoint_row_hash"] == ""
    assert checkpoint["full_verified_at_ms"] == 400
    assert checkpoint["verification_mode"] == "FULL_PERIODIC_AUDIT"


def test_trusted_cursor_checkpoint_is_loaded_from_verified_batch_authority(tmp_path):
    path = tmp_path / "batches.db"
    store = RealtimeFeatureBatchStore(str(path), create=True)
    payload = _batch()
    payload["source"] = {
        "cursor_chain_checkpoint": {
            "schema_version": 1,
            "verified_seq": 77,
            "verified_row_hash": "c" * 64,
            "full_verified_at_ms": 900,
        }
    }
    store.append(payload)
    expected_authority = store.authority_hash()
    store.close()

    checkpoint = trusted_cursor_chain_checkpoint(
        str(path),
        now_ms=1_000,
        full_chain_audit_interval_ms=500,
    )

    assert checkpoint == {
        "checkpoint_seq": 77,
        "checkpoint_row_hash": "c" * 64,
        "full_verified_at_ms": 900,
        "verification_mode": "INCREMENTAL_FROM_TRUSTED_BATCH",
        "batch_authority": expected_authority,
    }


def test_expired_cursor_checkpoint_requires_full_periodic_audit(tmp_path):
    path = tmp_path / "batches.db"
    store = RealtimeFeatureBatchStore(str(path), create=True)
    payload = _batch()
    payload["source"] = {
        "cursor_chain_checkpoint": {
            "schema_version": 1,
            "verified_seq": 77,
            "verified_row_hash": "c" * 64,
            "full_verified_at_ms": 400,
        }
    }
    store.append(payload)
    store.close()

    checkpoint = trusted_cursor_chain_checkpoint(
        str(path),
        now_ms=1_000,
        full_chain_audit_interval_ms=500,
    )

    assert checkpoint["checkpoint_seq"] == 0
    assert checkpoint["checkpoint_row_hash"] == ""
    assert checkpoint["full_verified_at_ms"] == 400
    assert checkpoint["verification_mode"] == "FULL_PERIODIC_AUDIT"
