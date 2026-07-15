"""SQLite adapter for the append-only forward-decision ledger."""

from __future__ import annotations

import json
import sqlite3
from typing import Mapping, Optional

from services.research.ports.persistence import ForwardLedgerError


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forward_decisions (
  alpha_id     TEXT    NOT NULL,
  decided_ts   INTEGER NOT NULL,
  recorded_at  INTEGER NOT NULL,
  weights_json TEXT    NOT NULL,
  signals_json TEXT,
  source       TEXT    NOT NULL,
  realized     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (alpha_id, decided_ts)
);
"""


class ForwardLedger:
    """Append-only forward decisions co-located with paper-track tables."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._require_schema()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise ForwardLedgerError(f"forward ledger unreadable: {exc}") from exc
        if "forward_decisions" not in {row["name"] for row in rows}:
            raise ForwardLedgerError("forward ledger missing table: forward_decisions")

    def record_decision(
        self,
        alpha_id: str,
        decided_ts: int,
        *,
        recorded_at: int,
        weights: Mapping[str, float],
        signals: Optional[Mapping[str, float]] = None,
        source: str = "FORWARD_LIVE",
    ) -> bool:
        if not weights:
            raise ForwardLedgerError("refusing to record an empty-weights decision (fail-closed)")
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO forward_decisions"
            "(alpha_id, decided_ts, recorded_at, weights_json, signals_json, "
            "source, realized) VALUES (?,?,?,?,?,?,0)",
            (
                alpha_id,
                int(decided_ts),
                int(recorded_at),
                json.dumps({key: float(value) for key, value in sorted(weights.items())}),
                (
                    None
                    if signals is None
                    else json.dumps({key: float(value) for key, value in sorted(signals.items())})
                ),
                str(source),
            ),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def decisions(self, alpha_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self._conn.execute(
                "SELECT decided_ts, recorded_at, weights_json, signals_json, "
                "source, realized FROM forward_decisions WHERE alpha_id=? "
                "ORDER BY decided_ts",
                (alpha_id,),
            )
        ]

    def pending(self, alpha_id: str) -> list[dict]:
        return [decision for decision in self.decisions(alpha_id) if not decision["realized"]]

    def mark_realized(self, alpha_id: str, decided_ts: int) -> None:
        self._conn.execute(
            "UPDATE forward_decisions SET realized=1 WHERE alpha_id=? AND decided_ts=?",
            (alpha_id, int(decided_ts)),
        )
        self._conn.commit()
