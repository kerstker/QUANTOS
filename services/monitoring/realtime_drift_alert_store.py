"""Append-only incident and delivery journal for realtime drift alerts."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any


class RealtimeDriftAlertStoreError(RuntimeError):
    """The realtime drift alert journal is invalid or unavailable."""


_GENESIS = ""
_SCHEMA = """
CREATE TABLE IF NOT EXISTS realtime_drift_alert_events (
  seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id         TEXT NOT NULL UNIQUE,
  incident_key     TEXT NOT NULL,
  event_type       TEXT NOT NULL,
  severity         TEXT NOT NULL,
  observed_at_ms   INTEGER NOT NULL,
  payload_json     TEXT NOT NULL,
  payload_sha256   TEXT NOT NULL,
  prev_hash        TEXT NOT NULL,
  row_hash         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_realtime_drift_incident
ON realtime_drift_alert_events(incident_key, seq);
CREATE TRIGGER IF NOT EXISTS realtime_drift_alert_events_no_update
BEFORE UPDATE ON realtime_drift_alert_events
BEGIN SELECT RAISE(ABORT, 'realtime_drift_alert_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS realtime_drift_alert_events_no_delete
BEFORE DELETE ON realtime_drift_alert_events
BEGIN SELECT RAISE(ABORT, 'realtime_drift_alert_events is append-only'); END;
"""


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RealtimeDriftAlertStoreError(
            "alert event is not canonical JSON"
        ) from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class RealtimeDriftAlertStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._require_schema()

    def _require_schema(self) -> None:
        try:
            row = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='realtime_drift_alert_events'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise RealtimeDriftAlertStoreError(
                "alert store is unreadable"
            ) from exc
        if row is None:
            raise RealtimeDriftAlertStoreError(
                "alert store schema is missing"
            )

    def close(self) -> None:
        self._conn.close()

    def append(self, payload: Mapping[str, Any]) -> bool:
        if not isinstance(payload, Mapping):
            raise RealtimeDriftAlertStoreError("alert event must be a mapping")
        event = dict(payload)
        fields = {
            name: str(event.get(name, "")).strip()
            for name in ("event_id", "incident_key", "event_type", "severity")
        }
        try:
            observed_at_ms = int(event["observed_at_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RealtimeDriftAlertStoreError(
                "alert event timestamp is invalid"
            ) from exc
        if any(not value for value in fields.values()) or observed_at_ms < 0:
            raise RealtimeDriftAlertStoreError(
                "alert event identity is invalid"
            )
        payload_json = _canonical(event)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT payload_sha256 FROM realtime_drift_alert_events "
                "WHERE event_id=?",
                (fields["event_id"],),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) == payload_sha256:
                    self._conn.rollback()
                    return False
                raise RealtimeDriftAlertStoreError(
                    "event_id reused with different content"
                )
            previous = self._conn.execute(
                "SELECT row_hash FROM realtime_drift_alert_events "
                "ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = _GENESIS if previous is None else str(previous["row_hash"])
            row_hash = _sha256(
                {
                    **fields,
                    "observed_at_ms": observed_at_ms,
                    "payload_sha256": payload_sha256,
                    "prev_hash": prev_hash,
                }
            )
            self._conn.execute(
                "INSERT INTO realtime_drift_alert_events"
                "(event_id,incident_key,event_type,severity,observed_at_ms,"
                "payload_json,payload_sha256,prev_hash,row_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    fields["event_id"],
                    fields["incident_key"],
                    fields["event_type"],
                    fields["severity"],
                    observed_at_ms,
                    payload_json,
                    payload_sha256,
                    prev_hash,
                    row_hash,
                ),
            )
            self._conn.commit()
            return True
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def row_count(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM realtime_drift_alert_events"
            ).fetchone()[0]
        )

    def latest_transition(self, incident_key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM realtime_drift_alert_events "
            "WHERE incident_key=? AND event_type<>'DELIVERY_RESULT' "
            "ORDER BY seq DESC LIMIT 1",
            (incident_key,),
        ).fetchone()
        return None if row is None else self._decode(row)

    def latest_event(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM realtime_drift_alert_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return None if row is None else self._decode(row)

    def _decode(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RealtimeDriftAlertStoreError(
                "alert event JSON is invalid"
            ) from exc
        if (
            not isinstance(payload, dict)
            or _sha256(payload) != str(row["payload_sha256"])
        ):
            raise RealtimeDriftAlertStoreError(
                "alert event payload hash mismatch"
            )
        return payload

    def chain_valid(self) -> bool:
        previous = _GENESIS
        try:
            for row in self._conn.execute(
                "SELECT * FROM realtime_drift_alert_events ORDER BY seq"
            ):
                self._decode(row)
                if str(row["prev_hash"]) != previous:
                    return False
                expected = _sha256(
                    {
                        "event_id": str(row["event_id"]),
                        "incident_key": str(row["incident_key"]),
                        "event_type": str(row["event_type"]),
                        "severity": str(row["severity"]),
                        "observed_at_ms": int(row["observed_at_ms"]),
                        "payload_sha256": str(row["payload_sha256"]),
                        "prev_hash": str(row["prev_hash"]),
                    }
                )
                if str(row["row_hash"]) != expected:
                    return False
                previous = str(row["row_hash"])
            return True
        except (
            RealtimeDriftAlertStoreError,
            sqlite3.DatabaseError,
            TypeError,
            ValueError,
        ):
            return False


__all__ = [
    "RealtimeDriftAlertStore",
    "RealtimeDriftAlertStoreError",
]
