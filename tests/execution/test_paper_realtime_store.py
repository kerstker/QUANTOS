from __future__ import annotations

import json

import pytest

from services.execution.paper_realtime_store import (
    PaperRealtimeStore,
    PaperRealtimeStoreError,
)


def _snapshot(alpha: str = "crowd_contra", observed_at: int = 1000, equity: float = 101.0):
    return {
        "alpha_id": alpha,
        "observed_at": observed_at,
        "provider": "BINANCE_PUBLIC_MARK_PRICE",
        "equity": equity,
        "marks": {"BTC": 50.0},
        "paper_only": True,
        "generated_at": 123,
    }


def test_store_appends_idempotently_and_verifies_chain(tmp_path):
    path = tmp_path / "paper-realtime.db"
    store = PaperRealtimeStore(str(path), create=True)
    try:
        first = _snapshot()
        assert store.append_snapshot(first) is True
        assert store.append_snapshot(dict(first, generated_at=999)) is False
        assert store.latest("crowd_contra")["equity"] == 101.0
        assert store.rows() == [{k: v for k, v in first.items() if k != "generated_at"}]
        assert store.recent_rows() == store.rows()
        assert store.chain_valid() is True
    finally:
        store.close()


def test_store_recent_rows_are_bounded_and_chronological(tmp_path):
    path = tmp_path / "paper-realtime.db"
    store = PaperRealtimeStore(str(path), create=True)
    try:
        for observed_at in (1000, 2000, 3000):
            store.append_snapshot(_snapshot(observed_at=observed_at, equity=float(observed_at)))
        assert [row["observed_at"] for row in store.recent_rows(2)] == [2000, 3000]
        with pytest.raises(ValueError, match="positive integer"):
            store.recent_rows(0)
    finally:
        store.close()


def test_store_rejects_identity_conflict_and_mutation(tmp_path):
    path = tmp_path / "paper-realtime.db"
    store = PaperRealtimeStore(str(path), create=True)
    try:
        store.append_snapshot(_snapshot())
        with pytest.raises(PaperRealtimeStoreError, match="identity reused"):
            store.append_snapshot(_snapshot(equity=102.0))
        with pytest.raises(Exception, match="append-only"):
            store._conn.execute(
                "UPDATE paper_mark_snapshots SET payload_json=? WHERE alpha_id=?",
                (json.dumps({"tampered": True}), "crowd_contra"),
            )
        with pytest.raises(Exception, match="append-only"):
            store._conn.execute(
                "DELETE FROM paper_mark_snapshots WHERE alpha_id=?", ("crowd_contra",)
            )
        assert store.chain_valid() is True
    finally:
        store.close()


def test_store_missing_schema_fails_closed(tmp_path):
    import sqlite3

    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    with pytest.raises(PaperRealtimeStoreError, match="schema"):
        PaperRealtimeStore(str(path), create=False)
