from __future__ import annotations

import os
from pathlib import Path
import sqlite3

import pytest

from quantos.contracts.killswitch import KillSwitchState
from services.console.risk_console import DefaultRiskConsolePort
from services.monitoring.audit_store import DurableAuditLog


def _identity(path: Path) -> tuple[bytes, int, int]:
    stat = path.stat()
    return path.read_bytes(), stat.st_mtime_ns, stat.st_size


@pytest.mark.parametrize("contents", ("empty_schema", "corrupt"))
def test_monitoring_snapshot_fails_closed_without_modifying_invalid_audit_db(
    tmp_path: Path,
    contents: str,
) -> None:
    path = tmp_path / "governance_audit.db"
    if contents == "empty_schema":
        connection = sqlite3.connect(path)
        connection.close()
    else:
        path.write_bytes(b"not-a-sqlite-database")
    fixed_ns = 1_700_000_000_000_000_000
    os.utime(path, ns=(fixed_ns, fixed_ns))
    before = _identity(path)

    payload = DefaultRiskConsolePort().monitoring_snapshot(
        audit_db=str(path),
        kill_switch_state=KillSwitchState(),
    )

    assert payload == {
        "provenance": "MONITORING_ERROR",
        "audit_chain_valid": False,
        "healthy": False,
        "kill_switch_active": True,
        "anomalies": ["MONITORING_ERROR"],
        "audit_events": 0,
    }
    assert _identity(path) == before
    if contents == "empty_schema":
        connection = sqlite3.connect(path)
        try:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        finally:
            connection.close()
        assert tables == []


def test_monitoring_snapshot_reads_valid_audit_db_without_modifying_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "governance_audit.db"
    audit = DurableAuditLog(str(path))
    try:
        audit.append(
            event_id="event-1",
            actor="human:ops",
            action="note",
            timestamp=1,
        )
    finally:
        audit.close()
    fixed_ns = 1_700_000_000_000_000_000
    os.utime(path, ns=(fixed_ns, fixed_ns))
    before = _identity(path)

    payload = DefaultRiskConsolePort().monitoring_snapshot(
        audit_db=str(path),
        kill_switch_state=KillSwitchState(),
    )

    assert payload == {
        "provenance": "REAL_MONITORING_SNAPSHOT",
        "audit_chain_valid": True,
        "kill_switch_active": False,
        "healthy": True,
        "anomalies": [],
        "audit_events": 1,
    }
    assert _identity(path) == before


def test_monitoring_snapshot_does_not_recreate_authority_removed_after_exists_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "governance_audit.db"
    path.write_bytes(b"present-before-open")
    real_exists = os.path.exists

    def remove_after_positive_check(candidate) -> bool:
        present = real_exists(candidate)
        if present and Path(candidate) == path:
            path.unlink()
        return present

    monkeypatch.setattr("services.console.risk_console.os.path.exists", remove_after_positive_check)

    payload = DefaultRiskConsolePort().monitoring_snapshot(
        audit_db=str(path),
        kill_switch_state=KillSwitchState(),
    )

    assert payload["provenance"] == "MONITORING_ERROR"
    assert payload["kill_switch_active"] is True
    assert not path.exists()
