"""Append-only local state for the L2 paper-order operator seam.

This store is deliberately narrower than the research forward ledger: it
records successful paper rebalance snapshots so the next *new* attested
decision can start from the previous paper positions.  It never represents a
broker account, live authorization, or forward-evidence realization.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from services.execution.canonical_store_admission import (
    CanonicalStoreAdmissionError,
    require_authoritative_binding,
)


class PaperExecutionStoreError(RuntimeError):
    """The paper execution state is missing, corrupt, or conflicting."""


_GENESIS = ""
_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_preview_runs (
  seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  alpha_id         TEXT NOT NULL,
  decision_ts      INTEGER NOT NULL,
  run_id           TEXT NOT NULL,
  payload_json     TEXT NOT NULL,
  payload_sha256   TEXT NOT NULL,
  prev_hash        TEXT NOT NULL,
  row_hash         TEXT NOT NULL,
  UNIQUE(alpha_id, decision_ts)
);
CREATE TRIGGER IF NOT EXISTS paper_preview_runs_no_update
BEFORE UPDATE ON paper_preview_runs
BEGIN SELECT RAISE(ABORT, 'paper_preview_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_preview_runs_no_delete
BEFORE DELETE ON paper_preview_runs
BEGIN SELECT RAISE(ABORT, 'paper_preview_runs is append-only'); END;
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
        raise PaperExecutionStoreError("paper preview payload is not canonical JSON") from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class PaperExecutionStore:
    """Durable, append-only paper position snapshots with a hash chain."""

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
            names = {
                row["name"]
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        except sqlite3.DatabaseError as exc:
            raise PaperExecutionStoreError("paper execution store is unreadable") from exc
        if "paper_preview_runs" not in names:
            raise PaperExecutionStoreError("paper execution store schema is missing")

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _stored_payload(preview: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(preview, Mapping):
            raise PaperExecutionStoreError("paper preview must be a mapping")
        payload = dict(preview)
        # Wall-clock publication metadata is intentionally not part of the
        # immutable decision identity; a retry of the same decision is exact.
        payload.pop("generated_at", None)
        payload.pop("replayed_from_store", None)
        return payload

    def _row_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PaperExecutionStoreError("paper preview row has invalid JSON") from exc
        if not isinstance(payload, dict) or _sha256(payload) != row["payload_sha256"]:
            raise PaperExecutionStoreError("paper preview payload hash mismatch")
        return payload

    @staticmethod
    def _require_authoritative_write(
        payload: Mapping[str, Any],
        authority_record: Mapping[str, Any] | None,
    ) -> None:
        embedded = payload.get("authoritative_backtest_record")
        if not isinstance(embedded, Mapping) or embedded != authority_record:
            raise PaperExecutionStoreError(
                "canonical paper persistence blocked: exact authority record is missing"
            )
        try:
            require_authoritative_binding(
                authority_record,
                run_id=str(payload.get("run_id", "")),
                manifest_hash=payload.get("authority_manifest_hash"),
                result_hash=payload.get("authority_result_hash"),
            )
        except CanonicalStoreAdmissionError as exc:
            raise PaperExecutionStoreError(str(exc)) from exc
        if (
            payload.get("paper_execution") != "PAPER_FILLED"
            or payload.get("execution_semantics") != "LINEAR_PERPETUAL_EVENT_V2"
        ):
            raise PaperExecutionStoreError(
                "canonical paper persistence blocked: V2 filled execution semantics "
                "are not bound"
            )

    def append_preview(
        self,
        preview: Mapping[str, Any],
        *,
        authority_record: Mapping[str, Any] | None = None,
    ) -> bool:
        """Append one successful preview; exact retries return ``False``."""
        payload = self._stored_payload(preview)
        self._require_authoritative_write(payload, authority_record)
        alpha_id = str(payload.get("alpha_id", ""))
        run_id = str(payload.get("run_id", ""))
        try:
            decision_ts = int(payload["decision_ts"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PaperExecutionStoreError("paper preview decision timestamp is invalid") from exc
        if not alpha_id or not run_id or decision_ts < 0:
            raise PaperExecutionStoreError("paper preview identity is invalid")
        payload_json = _canonical(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT payload_sha256 FROM paper_preview_runs "
                "WHERE alpha_id=? AND decision_ts=?",
                (alpha_id, decision_ts),
            ).fetchone()
            if existing is not None:
                if existing["payload_sha256"] == payload_sha256:
                    self._conn.rollback()
                    return False
                raise PaperExecutionStoreError(
                    "paper preview decision identity reused with different content"
                )
            previous = self._conn.execute(
                "SELECT row_hash FROM paper_preview_runs ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = _GENESIS if previous is None else str(previous["row_hash"])
            row_values = {
                "alpha_id": alpha_id,
                "decision_ts": decision_ts,
                "run_id": run_id,
                "payload_sha256": payload_sha256,
                "prev_hash": prev_hash,
            }
            row_hash = _sha256(row_values)
            self._conn.execute(
                "INSERT INTO paper_preview_runs"
                "(alpha_id,decision_ts,run_id,payload_json,payload_sha256,prev_hash,row_hash)"
                " VALUES (?,?,?,?,?,?,?)",
                (alpha_id, decision_ts, run_id, payload_json, payload_sha256, prev_hash, row_hash),
            )
            self._conn.commit()
            return True
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def get_by_decision(self, alpha_id: str, decision_ts: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM paper_preview_runs WHERE alpha_id=? AND decision_ts=?",
            (str(alpha_id), int(decision_ts)),
        ).fetchone()
        return None if row is None else self._row_payload(row)

    def latest(self, alpha_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM paper_preview_runs WHERE alpha_id=? ORDER BY seq DESC LIMIT 1",
            (str(alpha_id),),
        ).fetchone()
        return None if row is None else self._row_payload(row)

    def latest_positions(self, alpha_id: str) -> dict[str, int]:
        payload = self.latest(alpha_id)
        if not payload:
            return {}
        raw = payload.get("paper_positions")
        if not isinstance(raw, Mapping):
            raise PaperExecutionStoreError("paper preview positions are missing")
        positions: dict[str, int] = {}
        for instrument, quantity in raw.items():
            if isinstance(quantity, bool):
                raise PaperExecutionStoreError("paper position quantity is invalid")
            try:
                positions[str(instrument)] = int(quantity)
            except (TypeError, ValueError) as exc:
                raise PaperExecutionStoreError("paper position quantity is invalid") from exc
        return positions

    def rows(self) -> list[dict[str, Any]]:
        return [
            self._row_payload(row)
            for row in self._conn.execute(
                "SELECT * FROM paper_preview_runs ORDER BY seq"
            )
        ]

    def chain_valid(self) -> bool:
        previous = _GENESIS
        try:
            for row in self._conn.execute(
                "SELECT * FROM paper_preview_runs ORDER BY seq"
            ):
                payload = self._row_payload(row)
                if row["prev_hash"] != previous:
                    return False
                values = {
                    "alpha_id": row["alpha_id"],
                    "decision_ts": int(row["decision_ts"]),
                    "run_id": row["run_id"],
                    "payload_sha256": row["payload_sha256"],
                    "prev_hash": row["prev_hash"],
                }
                if row["row_hash"] != _sha256(values):
                    return False
                previous = row["row_hash"]
            return True
        except (PaperExecutionStoreError, sqlite3.DatabaseError, TypeError, ValueError):
            return False


__all__ = ["PaperExecutionStore", "PaperExecutionStoreError"]
