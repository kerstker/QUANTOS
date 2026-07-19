import json
import os
import sqlite3

import pytest

from quantos.contracts.events import EVENT_DATA_REFRESH_RECORDED
from services.data.refresh_history import (
    RefreshHistoryIntegrityError,
    RefreshHistoryStore,
)
from services.monitoring.domain_event_runtime import dispatch_refresh_event_outbox
from services.monitoring.event_bus import DurableEventBus


def _status(ran_at: int, *, outcome: str = "PASS") -> dict:
    failed = [] if outcome == "PASS" else ["BTC"]
    return {
        "run_id": f"refresh-{ran_at}",
        "ran_at": ran_at,
        "duration_ms": 25,
        "outcome": outcome,
        "failed_ticker_total": len(failed),
        "reports": [
            {
                "source": "prices",
                "outcome": outcome,
                "duration_ms": 20,
                "watermark_after": ran_at,
                "failed": failed,
                "failure_details": [],
                "failed_tickers": failed,
                "quality_failed": [],
            }
        ],
        "staleness_before": {
            "prices": {"freshest_days": 1, "n_stale": 0, "n_total": 2}
        },
        "staleness_after": {
            "prices": {"freshest_days": 1, "n_stale": 0, "n_total": 2}
        },
    }


def test_refresh_history_and_canonical_event_share_one_transaction(tmp_path):
    path = str(tmp_path / "refresh.db")
    store = RefreshHistoryStore(path)

    entry = store.append(_status(1_000), recorded_at_ms=1_100)
    pending = store.pending_event_outbox()

    assert len(pending) == 1
    message = pending[0]
    event = message["event"]
    assert event.event_type == EVENT_DATA_REFRESH_RECORDED
    assert event.event_version == "1.0.0"
    assert event.timestamp == 1_100_000
    assert event.domain == "data"
    assert event.payload == {
        "duration_ms": 25,
        "failed_instrument_count": 0,
        "failed_source_count": 0,
        "history_entry_hash": entry.entry_hash,
        "outcome": "PASS",
        "run_id": entry.run_id,
        "source_count": 1,
        "status_hash": entry.status_hash,
    }
    assert message["correlation_id"] == entry.run_id
    assert message["causation_id"] == ""
    assert store.chain_valid()
    store.close()

    reopened = RefreshHistoryStore(path, create=False)
    assert reopened.pending_event_outbox()[0]["event"] == event
    assert reopened.chain_valid()


def test_refresh_outbox_recovers_publish_before_dispatch_ack(tmp_path):
    store = RefreshHistoryStore(str(tmp_path / "refresh.db"))
    store.append(_status(2_000, outcome="FAILED"), recorded_at_ms=2_100)
    pending = store.pending_event_outbox()
    event = pending[0]["event"]
    bus = DurableEventBus(str(tmp_path / "events.db"))

    bus.publish_once(event, correlation_id=pending[0]["correlation_id"])
    assert store.pending_event_outbox()
    summary = dispatch_refresh_event_outbox(store, bus, at_ms=2_200)

    assert summary == {"dispatched": 1, "pending": 0}
    assert bus.count() == 1
    assert store.pending_event_outbox() == ()
    assert store.chain_valid()


def test_refresh_outbox_tamper_is_detected_and_blocks_extension(tmp_path):
    path = str(tmp_path / "refresh.db")
    store = RefreshHistoryStore(path)
    store.append(_status(3_000), recorded_at_ms=3_100)

    external = sqlite3.connect(path)
    external.execute("DROP TRIGGER refresh_event_outbox_no_update")
    external.execute(
        "UPDATE refresh_event_outbox SET event_json=? WHERE outbox_seq=1",
        (json.dumps({"event_id": "forged"}),),
    )
    external.commit()
    external.close()

    assert not store.chain_valid()
    with pytest.raises(RefreshHistoryIntegrityError):
        store.append(_status(4_000), recorded_at_ms=4_100)


def test_legacy_refresh_history_is_backfilled_only_when_outbox_table_is_absent(tmp_path):
    path = str(tmp_path / "refresh.db")
    store = RefreshHistoryStore(path)
    entry = store.append(_status(5_000), recorded_at_ms=5_100)
    store.close()

    legacy = sqlite3.connect(path)
    legacy.execute("DROP TABLE refresh_event_dispatch")
    legacy.execute("DROP TABLE refresh_event_outbox")
    legacy.commit()
    legacy.close()

    migrated = RefreshHistoryStore(path, create=True)
    pending = migrated.pending_event_outbox()
    assert len(pending) == 1
    assert pending[0]["event"].payload["history_entry_hash"] == entry.entry_hash
    assert migrated.chain_valid()


def test_read_write_open_of_legacy_schema_fails_without_creating_wal_sidecars(tmp_path):
    path = str(tmp_path / "refresh.db")
    store = RefreshHistoryStore(path)
    store.append(_status(6_000), recorded_at_ms=6_100)
    store.close()
    legacy = sqlite3.connect(path)
    legacy.execute("DROP TABLE refresh_event_dispatch")
    legacy.execute("DROP TABLE refresh_event_outbox")
    legacy.commit()
    legacy.close()
    for suffix in ("-wal", "-shm"):
        sidecar = path + suffix
        if os.path.exists(sidecar):
            os.remove(sidecar)
    before = open(path, "rb").read()

    with pytest.raises(RefreshHistoryIntegrityError):
        RefreshHistoryStore(path, create=False)

    assert open(path, "rb").read() == before
    assert not os.path.exists(path + "-wal")
    assert not os.path.exists(path + "-shm")
