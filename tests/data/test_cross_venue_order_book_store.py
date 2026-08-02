import sqlite3

import pytest

from services.data.cross_venue_order_book_store import (
    CrossVenueOrderBookStore,
    CrossVenueOrderBookStoreError,
)


def _bundle(snapshot_id: str = "snapshot-1") -> dict:
    return {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "observed_at_ms": 1_000,
        "books": [
            {
                "instrument_id": "DOGEUSDT",
                "venue": "binance_usdm",
                "bids": [["0.1", 100]],
                "asks": [["0.11", 100]],
            }
        ],
    }


def test_append_is_idempotent_and_chain_is_valid(tmp_path):
    path = tmp_path / "books.db"
    store = CrossVenueOrderBookStore(str(path), create=True)
    try:
        assert store.append_bundle(_bundle()) is True
        assert store.append_bundle(_bundle()) is False
        assert store.row_count() == 1
        assert store.latest()["snapshot_id"] == "snapshot-1"
        assert store.chain_valid() is True
    finally:
        store.close()


def test_snapshot_identity_conflict_fails_closed(tmp_path):
    path = tmp_path / "books.db"
    store = CrossVenueOrderBookStore(str(path), create=True)
    try:
        store.append_bundle(_bundle())
        changed = _bundle()
        changed["books"][0]["asks"] = [["0.12", 100]]
        with pytest.raises(
            CrossVenueOrderBookStoreError,
            match="reused with different content",
        ):
            store.append_bundle(changed)
    finally:
        store.close()


def test_tamper_is_detected(tmp_path):
    path = tmp_path / "books.db"
    store = CrossVenueOrderBookStore(str(path), create=True)
    store.append_bundle(_bundle())
    store.close()

    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER cross_venue_order_book_bundles_no_update")
    conn.execute(
        "UPDATE cross_venue_order_book_bundles SET payload_json='{}' WHERE seq=1"
    )
    conn.commit()
    conn.close()

    reopened = CrossVenueOrderBookStore(str(path))
    try:
        assert reopened.chain_valid() is False
    finally:
        reopened.close()
