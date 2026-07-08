"""Rank #27 — append-only incident ledger: durable, opened-once/resolved, fail-closed."""
import os

import pytest

from services.data.persistent_store import DataStoreError
from services.monitoring.incident_ledger import IncidentLedger


def test_open_and_resolve_persist_across_reopen(tmp_path):
    path = os.path.join(str(tmp_path), "inc.db")
    led = IncidentLedger(path, create=True)
    led.open(incident_id="inc-1", severity="CRITICAL", kind="kill_switch_trip",
             opened_by="ops", opened_ts=100, description="anomaly halt")
    assert led.open_count() == 1
    led.resolve("inc-1", resolved_by="alice", resolved_ts=200, resolution="multi-party reset")
    reopened = IncidentLedger(path)
    row = reopened.get("inc-1")
    assert row["status"] == "RESOLVED" and row["resolved_by"] == "alice" and row["resolution"] == "multi-party reset"
    assert reopened.open_count() == 0


def test_duplicate_open_raises(tmp_path):
    led = IncidentLedger(os.path.join(str(tmp_path), "d.db"), create=True)
    led.open(incident_id="inc-1", severity="WARN", kind="x", opened_by="ops", opened_ts=1)
    with pytest.raises(ValueError):
        led.open(incident_id="inc-1", severity="WARN", kind="x", opened_by="ops", opened_ts=2)


def test_resolve_unknown_or_already_resolved_raises(tmp_path):
    led = IncidentLedger(os.path.join(str(tmp_path), "r.db"), create=True)
    with pytest.raises(ValueError):
        led.resolve("nope", resolved_by="a", resolved_ts=1)
    led.open(incident_id="inc-1", severity="INFO", kind="x", opened_by="ops", opened_ts=1)
    led.resolve("inc-1", resolved_by="a", resolved_ts=2)
    with pytest.raises(ValueError):
        led.resolve("inc-1", resolved_by="a", resolved_ts=3)          # already resolved


def test_invalid_severity_rejected(tmp_path):
    led = IncidentLedger(os.path.join(str(tmp_path), "s.db"), create=True)
    with pytest.raises(ValueError):
        led.open(incident_id="i", severity="APOCALYPSE", kind="x", opened_by="ops", opened_ts=1)


def test_list_filters_by_status(tmp_path):
    led = IncidentLedger(os.path.join(str(tmp_path), "l.db"), create=True)
    led.open(incident_id="a", severity="WARN", kind="x", opened_by="o", opened_ts=1)
    led.open(incident_id="b", severity="WARN", kind="x", opened_by="o", opened_ts=2)
    led.resolve("a", resolved_by="o", resolved_ts=3)
    assert [r["incident_id"] for r in led.list(status="OPEN")] == ["b"]
    assert len(led.list()) == 2


def test_fail_closed_on_uninitialized(tmp_path):
    led = IncidentLedger(os.path.join(str(tmp_path), "e.db"), create=False)   # no schema created
    with pytest.raises(DataStoreError):
        led.open_count()
