from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from services.data.market_resilience_store import (
    MarketResilienceStore,
    MarketResilienceStoreError,
)


_NOW = 1_800_000_000_000


def _snapshot(book_update_id: int = 1) -> dict:
    body = {
        "schema_version": 1,
        "venue": "bybit_linear",
        "instrument_id": "BTCUSDT",
        "segment_id": "2" * 64,
        "source_event_time_ms": _NOW,
        "received_at_ms": _NOW + 5,
        "processed_at_ms": _NOW + 10,
        "book_update_id": book_update_id,
        "book_cross_sequence": 2,
        "best_bid": "100",
        "best_ask": "101",
        "source_event_ids": ["3" * 64],
        "raw_authority_head": {"head_seq": 8, "head_hash": "4" * 64},
        "cancel_rate_inferred": False,
    }
    snapshot_id = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {"snapshot_id": snapshot_id, **body}


def test_store_is_append_only_idempotent_and_hash_chained(tmp_path):
    path = tmp_path / "resilience.db"
    store = MarketResilienceStore(str(path), create=True)
    first_snapshot = _snapshot()
    first = store.append_snapshots((first_snapshot,))
    assert first == {
        "inserted": 1,
        "duplicates": 0,
        "head_seq": 1,
        "head_hash": first["head_hash"],
    }
    duplicate = store.append_snapshots((first_snapshot,))
    assert duplicate["inserted"] == 0
    assert duplicate["duplicates"] == 1
    second_snapshot = _snapshot(2)
    second = store.append_snapshots((second_snapshot,))
    assert second["head_seq"] == 2
    assert store.verify_chain()["valid"] is True
    rows = store.recent_snapshots(limit=2)
    assert [row["snapshot_id"] for row in rows] == [
        first_snapshot["snapshot_id"],
        second_snapshot["snapshot_id"],
    ]
    assert rows[0]["raw_authority_head"]["head_seq"] == 8
    store.close()

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE market_resilience_snapshots SET venue='tampered' WHERE seq=1"
            )


def test_store_rejects_conflicting_identity_and_outcome_fields(tmp_path):
    store = MarketResilienceStore(str(tmp_path / "resilience.db"), create=True)
    first_snapshot = _snapshot()
    store.append_snapshots((first_snapshot,))
    conflict = _snapshot()
    conflict["best_bid"] = "99"
    with pytest.raises(MarketResilienceStoreError, match="does not match"):
        store.append_snapshots((conflict,))

    prohibited = _snapshot(6)
    prohibited["future_return"] = "0.01"
    with pytest.raises(MarketResilienceStoreError, match="outcome field"):
        store.append_snapshots((prohibited,))
    nested = _snapshot(7)
    nested["research"] = {"label": 1}
    with pytest.raises(MarketResilienceStoreError, match="outcome field"):
        store.append_snapshots((nested,))
    store.close()


def test_store_detects_payload_tampering(tmp_path):
    path = tmp_path / "resilience.db"
    store = MarketResilienceStore(str(path), create=True)
    store.append_snapshots((_snapshot(),))
    store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER market_resilience_snapshots_no_update")
        connection.execute(
            "UPDATE market_resilience_snapshots SET payload_json='{}' WHERE seq=1"
        )
        connection.commit()
    reopened = MarketResilienceStore(str(path), create=False)
    assert reopened.verify_chain()["valid"] is False
    with pytest.raises(MarketResilienceStoreError, match="payload"):
        reopened.recent_snapshots()
    reopened.close()
