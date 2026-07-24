from __future__ import annotations

import json
import pytest

from services.execution.paper_execution_store import (
    PaperExecutionStore,
    PaperExecutionStoreError,
)


def _preview(alpha="crowd_contra", decision_ts=1000, quantity=2):
    return {
        "alpha_id": alpha,
        "decision_ts": decision_ts,
        "run_id": f"run-{alpha}-{decision_ts}",
        "paper_execution": "PAPER_FILLED",
        "paper_positions": {"BTC": quantity},
        "orders": [{"instrument": "BTC", "quantity": quantity}],
        "generated_at": 123,
    }


def test_store_appends_idempotently_and_rehydrates_positions(tmp_path):
    path = tmp_path / "paper.db"
    store = PaperExecutionStore(str(path), create=True)
    try:
        first = _preview()
        assert store.append_preview(first) is True
        assert store.append_preview(dict(first, generated_at=999)) is False
        assert store.latest_positions("crowd_contra") == {"BTC": 2}
        assert store.get_by_decision("crowd_contra", 1000)["run_id"] == "run-crowd_contra-1000"
        assert store.chain_valid() is True
    finally:
        store.close()


def test_store_rejects_identity_conflict_and_update_delete(tmp_path):
    path = tmp_path / "paper.db"
    store = PaperExecutionStore(str(path), create=True)
    try:
        store.append_preview(_preview())
        with pytest.raises(PaperExecutionStoreError, match="identity reused"):
            store.append_preview(dict(_preview(), paper_positions={"BTC": 3}))
        with pytest.raises(Exception, match="append-only"):
            store._conn.execute(
                "UPDATE paper_preview_runs SET payload_json=? WHERE alpha_id=?",
                (json.dumps({"tampered": True}), "crowd_contra"),
            )
        with pytest.raises(Exception, match="append-only"):
            store._conn.execute(
                "DELETE FROM paper_preview_runs WHERE alpha_id=?", ("crowd_contra",)
            )
        assert store.chain_valid() is True
    finally:
        store.close()


def test_store_missing_schema_fails_closed(tmp_path):
    path = tmp_path / "empty.db"
    import sqlite3

    sqlite3.connect(path).close()
    with pytest.raises(PaperExecutionStoreError, match="schema"):
        PaperExecutionStore(str(path), create=False)
