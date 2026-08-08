"""Append-only, hash-chained MFT parity report authority."""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from typing import Any

from quantos.contracts.hashing import stable_hash


class MftParityStoreError(RuntimeError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS mft_parity_reports (
  seq               INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id         TEXT NOT NULL UNIQUE,
  canonical_run_id  TEXT NOT NULL,
  input_hash        TEXT NOT NULL,
  status            TEXT NOT NULL,
  report_json       TEXT NOT NULL,
  report_hash       TEXT NOT NULL,
  recorded_at_ms    INTEGER NOT NULL CHECK(recorded_at_ms >= 0),
  prev_hash         TEXT NOT NULL,
  row_hash          TEXT NOT NULL UNIQUE,
  UNIQUE(canonical_run_id, input_hash)
);
CREATE TRIGGER IF NOT EXISTS mft_parity_reports_no_update
BEFORE UPDATE ON mft_parity_reports
BEGIN SELECT RAISE(ABORT, 'mft_parity_reports is append-only'); END;
CREATE TRIGGER IF NOT EXISTS mft_parity_reports_no_delete
BEFORE DELETE ON mft_parity_reports
BEGIN SELECT RAISE(ABORT, 'mft_parity_reports is append-only'); END;
"""


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MftParityStoreError("parity report is not canonical JSON") from exc


class MftParityStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("path must be non-empty")
        if create and path != ":memory:":
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
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
                "SELECT name FROM sqlite_master WHERE type='table' AND name='mft_parity_reports'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise MftParityStoreError("parity store is unreadable") from exc
        if row is None:
            raise MftParityStoreError("parity store schema is missing")

    @staticmethod
    def _identity(report: Mapping[str, Any]) -> tuple[str, str, str, str]:
        if not isinstance(report, Mapping):
            raise MftParityStoreError("report must be a mapping")
        if report.get("schema") != "mft-parity.v1":
            raise MftParityStoreError("report schema is invalid")
        values = tuple(str(report.get(name, "")) for name in (
            "report_id", "canonical_run_id", "input_hash", "status"
        ))
        if any(not value for value in values):
            raise MftParityStoreError("report identity is incomplete")
        manifest = report.get("input_manifest")
        if not isinstance(manifest, Mapping) or stable_hash(manifest) != values[2]:
            raise MftParityStoreError("report input_hash does not bind input_manifest")
        gate = report.get("engineering_gate")
        policy_hash = gate.get("policy_hash") if isinstance(gate, Mapping) else None
        expected_report_id = stable_hash(
            {
                "kind": "mft-parity.v1",
                "canonical_run_id": values[1],
                "input_hash": values[2],
                "policy_hash": policy_hash,
            }
        )
        if values[0] != expected_report_id:
            raise MftParityStoreError("report_id does not bind canonical run and input")
        return values

    def append(self, report: Mapping[str, Any], *, recorded_at_ms: int) -> bool:
        if isinstance(recorded_at_ms, bool) or not isinstance(recorded_at_ms, int) or recorded_at_ms < 0:
            raise ValueError("recorded_at_ms must be a non-negative integer")
        report_id, canonical_run_id, input_hash, status = self._identity(report)
        report_json = _canonical(dict(report))
        report_hash = stable_hash(dict(report))
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT canonical_run_id,input_hash,report_hash FROM mft_parity_reports "
                "WHERE report_id=?",
                (report_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["canonical_run_id"] == canonical_run_id
                    and existing["input_hash"] == input_hash
                    and existing["report_hash"] == report_hash
                ):
                    self._conn.rollback()
                    return False
                raise MftParityStoreError("report_id reuse with different content")
            prior = self._conn.execute(
                "SELECT row_hash FROM mft_parity_reports ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = "" if prior is None else str(prior["row_hash"])
            row_hash = stable_hash(
                {
                    "report_id": report_id,
                    "canonical_run_id": canonical_run_id,
                    "input_hash": input_hash,
                    "status": status,
                    "report_hash": report_hash,
                    "recorded_at_ms": recorded_at_ms,
                    "prev_hash": prev_hash,
                }
            )
            self._conn.execute(
                "INSERT INTO mft_parity_reports"
                "(report_id,canonical_run_id,input_hash,status,report_json,report_hash,"
                " recorded_at_ms,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    report_id,
                    canonical_run_id,
                    input_hash,
                    status,
                    report_json,
                    report_hash,
                    recorded_at_ms,
                    prev_hash,
                    row_hash,
                ),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError as exc:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise MftParityStoreError("canonical run/input identity conflict") from exc
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    @staticmethod
    def _report(row: sqlite3.Row) -> dict[str, Any]:
        try:
            value = json.loads(row["report_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MftParityStoreError("stored report JSON is invalid") from exc
        if not isinstance(value, dict) or stable_hash(value) != row["report_hash"]:
            raise MftParityStoreError("stored report hash mismatch")
        return value

    def rows(self, *, canonical_run_id: str | None = None) -> list[dict[str, Any]]:
        if canonical_run_id is None:
            rows = self._conn.execute("SELECT * FROM mft_parity_reports ORDER BY seq")
        else:
            rows = self._conn.execute(
                "SELECT * FROM mft_parity_reports WHERE canonical_run_id=? ORDER BY seq",
                (canonical_run_id,),
            )
        return [self._report(row) for row in rows]

    def latest(self, canonical_run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM mft_parity_reports WHERE canonical_run_id=? ORDER BY seq DESC LIMIT 1",
            (str(canonical_run_id),),
        ).fetchone()
        return None if row is None else self._report(row)

    def chain_valid(self) -> bool:
        previous = ""
        try:
            for row in self._conn.execute("SELECT * FROM mft_parity_reports ORDER BY seq"):
                report = self._report(row)
                if row["prev_hash"] != previous:
                    return False
                expected = stable_hash(
                    {
                        "report_id": row["report_id"],
                        "canonical_run_id": row["canonical_run_id"],
                        "input_hash": row["input_hash"],
                        "status": row["status"],
                        "report_hash": stable_hash(report),
                        "recorded_at_ms": int(row["recorded_at_ms"]),
                        "prev_hash": row["prev_hash"],
                    }
                )
                if row["row_hash"] != expected:
                    return False
                previous = expected
            return True
        except (MftParityStoreError, sqlite3.DatabaseError, TypeError, ValueError):
            return False

    def close(self) -> None:
        self._conn.close()


__all__ = ["MftParityStore", "MftParityStoreError"]
