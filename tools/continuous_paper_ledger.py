"""Append-only ledger for the quote-asset paper replay.

The ordinary forward paper-track stores one return per period.  The Binance
microstructure replay also needs to prove that it actually rebalanced every
week, including the exact decimal quantity targets produced by the current
exchange filters.  This small tool-side store is deliberately separate from
the live ledger: it has no broker/network imports, no order endpoint, and it
only records deterministic research evidence.

Rows are idempotent by ``(strategy_id, decision_day)`` and form a hash chain.
Replaying the same historical window therefore does not duplicate a decision,
while a changed decision at an existing timestamp fails closed instead of
silently replacing paper history.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Mapping
from typing import Any


class ContinuousPaperLedgerError(RuntimeError):
    """The append-only continuous paper ledger is unavailable or inconsistent."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS rebalance_ledger (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id TEXT NOT NULL,
  decision_day INTEGER NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL,
  UNIQUE(strategy_id, decision_day)
);
CREATE TRIGGER IF NOT EXISTS rebalance_ledger_no_update
BEFORE UPDATE ON rebalance_ledger
BEGIN SELECT RAISE(ABORT, 'rebalance_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS rebalance_ledger_no_delete
BEFORE DELETE ON rebalance_ledger
BEGIN SELECT RAISE(ABORT, 'rebalance_ledger is append-only'); END;
"""


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ContinuousPaperLedger:
    """Durable, append-only rebalance evidence for one quote-asset replay."""

    def __init__(self, path: str, *, create: bool = True) -> None:
        if path != ":memory:" and create:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        if not create and path != ":memory:" and not os.path.isfile(path):
            raise ContinuousPaperLedgerError(f"ledger unavailable: {path}")
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._verify()

    def _verify(self) -> None:
        try:
            names = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        except sqlite3.DatabaseError as exc:
            raise ContinuousPaperLedgerError(f"ledger unreadable: {exc}") from exc
        if "rebalance_ledger" not in names:
            raise ContinuousPaperLedgerError("ledger schema missing rebalance_ledger")

    def _last_hash(self) -> str:
        row = self._conn.execute(
            "SELECT record_hash FROM rebalance_ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return "" if row is None else str(row["record_hash"])

    def append(
        self,
        strategy_id: str,
        decision_day: int,
        payload: Mapping[str, Any],
        *,
        status: str,
    ) -> bool:
        """Append one decision, returning ``False`` for an identical replay."""
        strategy = str(strategy_id).strip()
        state = str(status).strip()
        if not strategy or not state:
            raise ValueError("strategy_id and status must be non-empty")
        if isinstance(decision_day, bool) or not isinstance(decision_day, int) or decision_day < 0:
            raise ValueError("decision_day must be a non-negative integer")
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        payload_json = _canonical(dict(payload))
        payload_hash = _sha256(payload_json)
        existing = self._conn.execute(
            "SELECT status,payload_json,payload_hash FROM rebalance_ledger "
            "WHERE strategy_id=? AND decision_day=?",
            (strategy, decision_day),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["status"]) == state
                and str(existing["payload_hash"]) == payload_hash
                and str(existing["payload_json"]) == payload_json
            ):
                return False
            raise ContinuousPaperLedgerError(
                f"reused rebalance identity with different content: {strategy}@{decision_day}"
            )
        previous = self._last_hash()
        record_hash = _sha256(_canonical({
            "strategy_id": strategy,
            "decision_day": decision_day,
            "status": state,
            "payload_hash": payload_hash,
            "prev_record_hash": previous,
        }))
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO rebalance_ledger "
                    "(strategy_id,decision_day,status,payload_json,payload_hash,prev_record_hash,record_hash) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (strategy, decision_day, state, payload_json, payload_hash, previous, record_hash),
                )
        except sqlite3.IntegrityError as exc:
            raise ContinuousPaperLedgerError(f"rebalance append conflict: {exc}") from exc
        except sqlite3.DatabaseError as exc:
            raise ContinuousPaperLedgerError(f"rebalance append failed: {exc}") from exc
        return True

    def append_many(
        self,
        strategy_id: str,
        rows: list[Mapping[str, Any]],
        *,
        status: str,
    ) -> tuple[int, int]:
        """Append rows in decision-date order; return ``(inserted, replayed)``."""
        inserted = replayed = 0
        for row in sorted(rows, key=lambda item: int(item["day"])):
            payload = dict(row)
            day = int(payload.pop("day"))
            if self.append(strategy_id, day, payload, status=status):
                inserted += 1
            else:
                replayed += 1
        return inserted, replayed

    def rows(self, strategy_id: str | None = None) -> list[dict[str, Any]]:
        if strategy_id is None:
            query, args = (
                "SELECT * FROM rebalance_ledger ORDER BY seq",
                (),
            )
        else:
            query, args = (
                "SELECT * FROM rebalance_ledger WHERE strategy_id=? ORDER BY seq",
                (str(strategy_id),),
            )
        return [dict(row) for row in self._conn.execute(query, args).fetchall()]

    def close(self) -> None:
        self._conn.close()


__all__ = ["ContinuousPaperLedger", "ContinuousPaperLedgerError"]
