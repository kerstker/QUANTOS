"""SQLite adapter for the append-only forward-decision ledger."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import sqlite3
from typing import Mapping, Optional

from services.research.ports.persistence import ForwardLedgerError
from services.research.paper_trading.forward_evidence import (
    FORWARD_L2_DECISION_SOURCE,
    ForwardAdmissionRef,
)


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
CREATE TABLE IF NOT EXISTS forward_decision_bindings (
  binding_seq               INTEGER PRIMARY KEY AUTOINCREMENT,
  alpha_id                  TEXT    NOT NULL,
  decided_ts                INTEGER NOT NULL,
  decision_payload_sha256   TEXT    NOT NULL,
  admission_id              TEXT    NOT NULL,
  admission_evidence_sha256 TEXT    NOT NULL,
  admission_decision_hash   TEXT    NOT NULL,
  admission_recorded_at_ms  INTEGER NOT NULL,
  price_basis               TEXT    NOT NULL,
  instrument_scope_json     TEXT    NOT NULL,
  prev_hash                 TEXT    NOT NULL,
  row_hash                  TEXT    NOT NULL UNIQUE,
  UNIQUE(alpha_id, decided_ts)
);
CREATE TRIGGER IF NOT EXISTS forward_decision_bindings_no_update
BEFORE UPDATE ON forward_decision_bindings
BEGIN SELECT RAISE(ABORT, 'forward_decision_bindings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_decision_bindings_no_delete
BEFORE DELETE ON forward_decision_bindings
BEGIN SELECT RAISE(ABORT, 'forward_decision_bindings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_decisions_identity_no_update
BEFORE UPDATE OF alpha_id,decided_ts,recorded_at,weights_json,signals_json,source
ON forward_decisions
BEGIN SELECT RAISE(ABORT, 'forward decision identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS forward_decisions_no_delete
BEFORE DELETE ON forward_decisions
BEGIN SELECT RAISE(ABORT, 'forward_decisions is append-only'); END;
"""

_GENESIS_HASH = "0" * 64


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


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
        names = {row["name"] for row in rows}
        missing = {"forward_decisions", "forward_decision_bindings"} - names
        if missing:
            raise ForwardLedgerError(f"forward ledger missing table: {sorted(missing)[0]}")

    def record_decision(
        self,
        alpha_id: str,
        decided_ts: int,
        *,
        recorded_at: int,
        weights: Mapping[str, float],
        admission_ref: ForwardAdmissionRef,
        signals: Optional[Mapping[str, float]] = None,
        source: str = FORWARD_L2_DECISION_SOURCE,
    ) -> bool:
        if not weights:
            raise ForwardLedgerError("refusing to record an empty-weights decision (fail-closed)")
        normalized_weights = {key: float(value) for key, value in sorted(weights.items())}
        if not set(normalized_weights).issubset(set(admission_ref.instrument_scope)):
            raise ForwardLedgerError("decision weights are outside the admitted instrument scope")
        normalized_signals = (
            None if signals is None else {key: float(value) for key, value in sorted(signals.items())}
        )
        decision = {
            "alpha_id": str(alpha_id),
            "decided_ts": int(decided_ts),
            "recorded_at": int(recorded_at),
            "weights": normalized_weights,
            "signals": normalized_signals,
            "source": str(source),
        }
        decision_payload_sha256 = _sha256(decision)
        weights_json = _canonical(normalized_weights)
        signals_json = None if normalized_signals is None else _canonical(normalized_signals)

        with self._immediate_transaction():
            if not self._decision_chain_valid():
                raise ForwardLedgerError("invalid forward decision binding chain")
            existing = self._conn.execute(
                "SELECT recorded_at,weights_json,signals_json,source FROM forward_decisions "
                "WHERE alpha_id=? AND decided_ts=?",
                (decision["alpha_id"], decision["decided_ts"]),
            ).fetchone()
            binding = self._conn.execute(
                "SELECT decision_payload_sha256,admission_id,admission_evidence_sha256,"
                "admission_decision_hash,admission_recorded_at_ms,price_basis,"
                "instrument_scope_json,prev_hash,row_hash FROM forward_decision_bindings "
                "WHERE alpha_id=? AND decided_ts=?",
                (decision["alpha_id"], decision["decided_ts"]),
            ).fetchone()
            if existing is not None or binding is not None:
                if self._same_retained_decision(
                    existing,
                    binding,
                    decision=decision,
                    weights_json=weights_json,
                    signals_json=signals_json,
                    decision_payload_sha256=decision_payload_sha256,
                    admission_ref=admission_ref,
                ) and self.decision_chain_valid():
                    return False
                raise ForwardLedgerError(
                    "forward decision identity reused with different content: "
                    f"{decision['alpha_id']}@{decision['decided_ts']}"
                )

            previous = self._conn.execute(
                "SELECT row_hash FROM forward_decision_bindings ORDER BY binding_seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = _GENESIS_HASH if previous is None else str(previous["row_hash"])
            binding_values = self._binding_values(
                decision=decision,
                decision_payload_sha256=decision_payload_sha256,
                admission_ref=admission_ref,
                prev_hash=prev_hash,
            )
            self._conn.execute(
                "INSERT INTO forward_decisions"
                "(alpha_id,decided_ts,recorded_at,weights_json,signals_json,source,realized) "
                "VALUES (?,?,?,?,?,?,0)",
                (
                    decision["alpha_id"],
                    decision["decided_ts"],
                    decision["recorded_at"],
                    weights_json,
                    signals_json,
                    decision["source"],
                ),
            )
            self._conn.execute(
                "INSERT INTO forward_decision_bindings("
                "alpha_id,decided_ts,decision_payload_sha256,admission_id,"
                "admission_evidence_sha256,admission_decision_hash,admission_recorded_at_ms,"
                "price_basis,instrument_scope_json,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                tuple(binding_values[name] for name in self._binding_column_names()),
            )
        return True

    @contextmanager
    def _immediate_transaction(self):
        if self._conn.in_transaction:
            raise ForwardLedgerError("forward ledger write requires no active transaction")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    @contextmanager
    def _read_snapshot(self):
        if self._conn.in_transaction:
            raise ForwardLedgerError("forward ledger read requires no active transaction")
        self._conn.execute("BEGIN")
        try:
            yield
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    @staticmethod
    def _binding_column_names() -> tuple[str, ...]:
        return (
            "alpha_id",
            "decided_ts",
            "decision_payload_sha256",
            "admission_id",
            "admission_evidence_sha256",
            "admission_decision_hash",
            "admission_recorded_at_ms",
            "price_basis",
            "instrument_scope_json",
            "prev_hash",
            "row_hash",
        )

    @staticmethod
    def _binding_values(
        *,
        decision: Mapping[str, object],
        decision_payload_sha256: str,
        admission_ref: ForwardAdmissionRef,
        prev_hash: str,
    ) -> dict[str, object]:
        values: dict[str, object] = {
            "alpha_id": decision["alpha_id"],
            "decided_ts": decision["decided_ts"],
            "decision_payload_sha256": decision_payload_sha256,
            "admission_id": admission_ref.admission_id,
            "admission_evidence_sha256": admission_ref.evidence_sha256,
            "admission_decision_hash": admission_ref.decision_hash,
            "admission_recorded_at_ms": int(admission_ref.recorded_at_ms),
            "price_basis": admission_ref.price_basis,
            "instrument_scope_json": _canonical(sorted(admission_ref.instrument_scope)),
            "prev_hash": prev_hash,
        }
        values["row_hash"] = _sha256(values)
        return values

    def _same_retained_decision(
        self,
        decision_row: sqlite3.Row | None,
        binding_row: sqlite3.Row | None,
        *,
        decision: Mapping[str, object],
        weights_json: str,
        signals_json: str | None,
        decision_payload_sha256: str,
        admission_ref: ForwardAdmissionRef,
    ) -> bool:
        if decision_row is None or binding_row is None:
            return False
        if (
            int(decision_row["recorded_at"]) != decision["recorded_at"]
            or decision_row["weights_json"] != weights_json
            or decision_row["signals_json"] != signals_json
            or decision_row["source"] != decision["source"]
        ):
            return False
        expected = self._binding_values(
            decision=decision,
            decision_payload_sha256=decision_payload_sha256,
            admission_ref=admission_ref,
            prev_hash=str(binding_row["prev_hash"]),
        )
        return all(binding_row[name] == expected[name] for name in self._binding_column_names()[2:])

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

    def attested_pending(self, alpha_id: str) -> list[dict]:
        with self._read_snapshot():
            if not self._decision_chain_valid():
                raise ForwardLedgerError("invalid forward decision binding chain")
            return [
                dict(row)
                for row in self._conn.execute(
                    "SELECT d.decided_ts,d.recorded_at,d.weights_json,d.signals_json,d.source,d.realized "
                    "FROM forward_decisions AS d INNER JOIN forward_decision_bindings AS b "
                    "ON b.alpha_id=d.alpha_id AND b.decided_ts=d.decided_ts "
                    "WHERE d.alpha_id=? AND d.realized=0 ORDER BY d.decided_ts",
                    (alpha_id,),
                )
            ]

    def legacy_decisions(self) -> list[dict]:
        return [
            dict(row)
            for row in self._conn.execute(
                "SELECT d.alpha_id,d.decided_ts,d.recorded_at,d.weights_json,d.signals_json,"
                "d.source,d.realized FROM forward_decisions AS d "
                "LEFT JOIN forward_decision_bindings AS b "
                "ON b.alpha_id=d.alpha_id AND b.decided_ts=d.decided_ts "
                "WHERE b.alpha_id IS NULL ORDER BY d.alpha_id,d.decided_ts"
            )
        ]

    def decision_chain_valid(self) -> bool:
        return self._decision_chain_valid()

    def _decision_chain_valid(self) -> bool:
        try:
            counts = self._conn.execute(
                "SELECT COUNT(*) AS bindings, COUNT(d.alpha_id) AS matched "
                "FROM forward_decision_bindings AS b LEFT JOIN forward_decisions AS d "
                "ON d.alpha_id=b.alpha_id AND d.decided_ts=b.decided_ts"
            ).fetchone()
            if counts["bindings"] != counts["matched"]:
                return False
            rows = self._conn.execute(
                "SELECT b.binding_seq,b.alpha_id,b.decided_ts,b.decision_payload_sha256,"
                "b.admission_id,b.admission_evidence_sha256,b.admission_decision_hash,"
                "b.admission_recorded_at_ms,b.price_basis,b.instrument_scope_json,b.prev_hash,"
                "b.row_hash,d.recorded_at,d.weights_json,d.signals_json,d.source "
                "FROM forward_decision_bindings AS b LEFT JOIN forward_decisions AS d "
                "ON d.alpha_id=b.alpha_id AND d.decided_ts=b.decided_ts "
                "ORDER BY b.binding_seq"
            ).fetchall()
            previous_hash = _GENESIS_HASH
            for row in rows:
                if row["recorded_at"] is None:
                    return False
                if row["prev_hash"] != previous_hash:
                    return False
                scope = json.loads(row["instrument_scope_json"])
                if not isinstance(scope, list) or row["instrument_scope_json"] != _canonical(sorted(scope)):
                    return False
                decision = {
                    "alpha_id": row["alpha_id"],
                    "decided_ts": int(row["decided_ts"]),
                    "recorded_at": int(row["recorded_at"]),
                    "weights": json.loads(row["weights_json"]),
                    "signals": None if row["signals_json"] is None else json.loads(row["signals_json"]),
                    "source": row["source"],
                }
                if row["decision_payload_sha256"] != _sha256(decision):
                    return False
                binding = {
                    name: row[name]
                    for name in self._binding_column_names()
                    if name != "row_hash"
                }
                if row["row_hash"] != _sha256(binding):
                    return False
                previous_hash = str(row["row_hash"])
            return True
        except (TypeError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError):
            return False

    def mark_realized(self, alpha_id: str, decided_ts: int) -> None:
        cursor = self._conn.execute(
            "UPDATE forward_decisions SET realized=1 "
            "WHERE alpha_id=? AND decided_ts=? AND realized=0",
            (alpha_id, int(decided_ts)),
        )
        if cursor.rowcount != 1:
            self._conn.rollback()
            raise ForwardLedgerError(
                f"pending forward decision not found: {alpha_id}@{int(decided_ts)}"
            )
        self._conn.commit()
