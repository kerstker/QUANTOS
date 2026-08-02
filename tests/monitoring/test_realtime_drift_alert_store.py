import sqlite3

import pytest

from services.monitoring.realtime_drift_alert_store import (
    RealtimeDriftAlertStore,
    RealtimeDriftAlertStoreError,
)


def _event(event_id: str, event_type: str = "OPENED") -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "incident_key": "BINANCE_REALTIME_FEATURE_DRIFT",
        "event_type": event_type,
        "severity": "BLOCK",
        "observed_at_ms": 1_000,
        "status": "BLOCK",
    }


def test_alert_store_is_append_only_idempotent_and_chained(tmp_path):
    path = tmp_path / "alerts.db"
    store = RealtimeDriftAlertStore(str(path), create=True)
    try:
        assert store.append(_event("event-1")) is True
        assert store.append(_event("event-1")) is False
        assert store.append(_event("event-2", "DELIVERY_RESULT")) is True
        assert store.row_count() == 2
        assert store.chain_valid() is True
        assert store.latest_transition(
            "BINANCE_REALTIME_FEATURE_DRIFT"
        )["event_id"] == "event-1"
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute(
                "UPDATE realtime_drift_alert_events SET severity='PASS'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute("DELETE FROM realtime_drift_alert_events")
    finally:
        store.close()


def test_alert_store_rejects_conflicting_identity_and_tampering(tmp_path):
    path = tmp_path / "alerts.db"
    store = RealtimeDriftAlertStore(str(path), create=True)
    try:
        store.append(_event("event-1"))
        conflicting = _event("event-1")
        conflicting["status"] = "WARN"
        with pytest.raises(RealtimeDriftAlertStoreError):
            store.append(conflicting)
        store._conn.execute(
            "DROP TRIGGER realtime_drift_alert_events_no_update"
        )
        store._conn.execute(
            "UPDATE realtime_drift_alert_events SET payload_json='{}'"
        )
        store._conn.commit()
        assert store.chain_valid() is False
    finally:
        store.close()
