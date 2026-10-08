"""Attested forward-paper ledger layered over the legacy paper database.

The repository still contains historical ``FORWARD_LIVE`` rows written before
data-admission binding existed.  Those rows remain readable for audit, but only
new rows carrying a :class:`ForwardAdmissionRef` participate in the forward
evidence clock.  This adapter deliberately shares the existing SQLite file so
the migration is additive and does not rewrite history.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import sqlite3
from collections.abc import Collection, Mapping
from typing import Optional

from services.research.paper_trading.forward_evidence import (
    FORWARD_L2_DECISION_SOURCE,
    FORWARD_L2_REALIZATION_SOURCE,
    ForwardAdmissionRef,
)
from services.research.ports.persistence import ForwardLedgerError


_GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_periods (
  alpha_id TEXT NOT NULL,
  as_of_ts INTEGER NOT NULL,
  realized_return REAL NOT NULL,
  realized_ic REAL,
  source TEXT NOT NULL,
  PRIMARY KEY (alpha_id, as_of_ts)
);
CREATE TABLE IF NOT EXISTS paper_track (
  alpha_id TEXT PRIMARY KEY,
  start_ts INTEGER, last_ts INTEGER, n_periods INTEGER,
  cum_return REAL, paper_sharpe REAL, paper_ic REAL,
  backtest_sharpe REAL, perf_gap REAL
);
CREATE TABLE IF NOT EXISTS forward_decisions (
  alpha_id TEXT NOT NULL,
  decided_ts INTEGER NOT NULL,
  recorded_at INTEGER NOT NULL,
  weights_json TEXT NOT NULL,
  signals_json TEXT,
  source TEXT NOT NULL,
  realized INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (alpha_id, decided_ts)
);
CREATE TABLE IF NOT EXISTS forward_decision_bindings (
  binding_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  alpha_id TEXT NOT NULL,
  decided_ts INTEGER NOT NULL,
  decision_payload_sha256 TEXT NOT NULL,
  admission_id TEXT NOT NULL,
  admission_evidence_sha256 TEXT NOT NULL,
  admission_decision_hash TEXT NOT NULL,
  admission_recorded_at_ms INTEGER NOT NULL,
  price_basis TEXT NOT NULL,
  instrument_scope_json TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  row_hash TEXT NOT NULL UNIQUE,
  UNIQUE(alpha_id, decided_ts)
);
CREATE TABLE IF NOT EXISTS forward_realizations (
  realization_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  alpha_id TEXT NOT NULL,
  decided_ts INTEGER NOT NULL,
  as_of_ts INTEGER NOT NULL,
  decision_payload_sha256 TEXT NOT NULL,
  realized_return REAL NOT NULL,
  realized_ic REAL,
  source TEXT NOT NULL,
  inputs_json TEXT NOT NULL,
  inputs_sha256 TEXT NOT NULL,
  admission_id TEXT NOT NULL,
  admission_evidence_sha256 TEXT NOT NULL,
  admission_decision_hash TEXT NOT NULL,
  committed_at_ms INTEGER NOT NULL,
  prev_hash TEXT NOT NULL,
  row_hash TEXT NOT NULL UNIQUE,
  UNIQUE(alpha_id, decided_ts),
  UNIQUE(alpha_id, as_of_ts)
);
CREATE TABLE IF NOT EXISTS forward_local_events (
  event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  alpha_id TEXT NOT NULL,
  decided_ts INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  row_hash TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS forward_decisions_identity_no_update
BEFORE UPDATE OF alpha_id,decided_ts,recorded_at,weights_json,signals_json,source
ON forward_decisions
BEGIN SELECT RAISE(ABORT, 'forward decision identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS forward_decisions_no_delete
BEFORE DELETE ON forward_decisions
BEGIN SELECT RAISE(ABORT, 'forward_decisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_periods_no_update
BEFORE UPDATE ON paper_periods
BEGIN SELECT RAISE(ABORT, 'paper_periods is append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_periods_no_delete
BEFORE DELETE ON paper_periods
BEGIN SELECT RAISE(ABORT, 'paper_periods is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_decision_bindings_no_update
BEFORE UPDATE ON forward_decision_bindings
BEGIN SELECT RAISE(ABORT, 'forward_decision_bindings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_decision_bindings_no_delete
BEFORE DELETE ON forward_decision_bindings
BEGIN SELECT RAISE(ABORT, 'forward_decision_bindings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_realizations_no_update
BEFORE UPDATE ON forward_realizations
BEGIN SELECT RAISE(ABORT, 'forward_realizations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_realizations_no_delete
BEFORE DELETE ON forward_realizations
BEGIN SELECT RAISE(ABORT, 'forward_realizations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_local_events_no_update
BEFORE UPDATE ON forward_local_events
BEGIN SELECT RAISE(ABORT, 'forward_local_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forward_local_events_no_delete
BEFORE DELETE ON forward_local_events
BEGIN SELECT RAISE(ABORT, 'forward_local_events is append-only'); END;
"""


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(v) for v in value)
    return True


class AttestedForwardLedger:
    """Append-only attested ledger; legacy rows are never promoted."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._require_schema()

    def _require_schema(self) -> None:
        names = {
            row["name"]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {
            "forward_decisions",
            "forward_decision_bindings",
            "forward_realizations",
            "forward_local_events",
            "paper_periods",
        }
        missing = sorted(required - names)
        if missing:
            raise ForwardLedgerError(f"attested forward ledger missing table: {missing[0]}")

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _write(self):
        if self._conn.in_transaction:
            raise ForwardLedgerError("attested ledger write requires no active transaction")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._conn.commit()
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def decisions(self, alpha_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self._conn.execute(
                "SELECT decided_ts,recorded_at,weights_json,signals_json,source,realized "
                "FROM forward_decisions WHERE alpha_id=? ORDER BY decided_ts",
                (str(alpha_id),),
            )
        ]

    def attested_decisions(self, alpha_id: str) -> list[dict]:
        """Return only decisions bound to a verified admission authority.

        Legacy rows remain queryable through ``decisions`` for audit display,
        but they must not hold the cadence of a newly admitted paper clock.
        """
        if not self.decision_chain_valid():
            raise ForwardLedgerError("invalid forward decision binding chain")
        return [
            dict(row)
            for row in self._conn.execute(
                "SELECT d.decided_ts,d.recorded_at,d.weights_json,d.signals_json,"
                "d.source,d.realized FROM forward_decisions d INNER JOIN "
                "forward_decision_bindings b ON b.alpha_id=d.alpha_id "
                "AND b.decided_ts=d.decided_ts WHERE d.alpha_id=? "
                "ORDER BY d.decided_ts",
                (str(alpha_id),),
            )
        ]

    def pending(self, alpha_id: str) -> list[dict]:
        return [row for row in self.decisions(alpha_id) if not row["realized"]]

    def attested_pending(self, alpha_id: str) -> list[dict]:
        if not self.decision_chain_valid():
            raise ForwardLedgerError("invalid forward decision binding chain")
        return [
            dict(row)
            for row in self._conn.execute(
                "SELECT d.decided_ts,d.recorded_at,d.weights_json,d.signals_json,d.source,d.realized "
                "FROM forward_decisions d INNER JOIN forward_decision_bindings b "
                "ON b.alpha_id=d.alpha_id AND b.decided_ts=d.decided_ts "
                "WHERE d.alpha_id=? AND d.realized=0 ORDER BY d.decided_ts",
                (str(alpha_id),),
            )
        ]

    def legacy_decisions(self) -> list[dict]:
        return [
            dict(row)
            for row in self._conn.execute(
                "SELECT d.alpha_id,d.decided_ts,d.recorded_at,d.weights_json,d.signals_json,"
                "d.source,d.realized FROM forward_decisions d LEFT JOIN "
                "forward_decision_bindings b ON b.alpha_id=d.alpha_id "
                "AND b.decided_ts=d.decided_ts WHERE b.alpha_id IS NULL "
                "ORDER BY d.alpha_id,d.decided_ts"
            )
        ]

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
            raise ForwardLedgerError("refusing to record an empty-weights decision")
        normalized_weights = {str(k): float(v) for k, v in sorted(weights.items())}
        normalized_signals = (
            None
            if signals is None
            else {str(k): float(v) for k, v in sorted(signals.items())}
        )
        if not _finite(normalized_weights) or not _finite(normalized_signals):
            raise ForwardLedgerError("decision contains non-finite evidence")
        if not set(normalized_weights).issubset(set(admission_ref.instrument_scope)):
            raise ForwardLedgerError("decision weights exceed admitted instrument scope")
        decision = {
            "alpha_id": str(alpha_id),
            "decided_ts": int(decided_ts),
            "recorded_at": int(recorded_at),
            "weights": normalized_weights,
            "signals": normalized_signals,
            "source": str(source),
        }
        digest = _sha256(decision)
        weights_json = _canonical(normalized_weights)
        signals_json = None if normalized_signals is None else _canonical(normalized_signals)
        with self._write():
            if not self.decision_chain_valid():
                raise ForwardLedgerError("invalid forward decision binding chain")
            existing = self._conn.execute(
                "SELECT recorded_at,weights_json,signals_json,source FROM forward_decisions "
                "WHERE alpha_id=? AND decided_ts=?",
                (decision["alpha_id"], decision["decided_ts"]),
            ).fetchone()
            binding = self._conn.execute(
                "SELECT * FROM forward_decision_bindings WHERE alpha_id=? AND decided_ts=?",
                (decision["alpha_id"], decision["decided_ts"]),
            ).fetchone()
            if existing is not None or binding is not None:
                if (
                    existing is not None
                    and binding is not None
                    and int(existing["recorded_at"]) == decision["recorded_at"]
                    and existing["weights_json"] == weights_json
                    and existing["signals_json"] == signals_json
                    and existing["source"] == decision["source"]
                    and binding["decision_payload_sha256"] == digest
                    and self.decision_chain_valid()
                ):
                    return False
                raise ForwardLedgerError("forward decision identity reused with different content")
            previous = self._conn.execute(
                "SELECT row_hash FROM forward_decision_bindings ORDER BY binding_seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = _GENESIS if previous is None else str(previous["row_hash"])
            binding_values = {
                "alpha_id": decision["alpha_id"],
                "decided_ts": decision["decided_ts"],
                "decision_payload_sha256": digest,
                "admission_id": admission_ref.admission_id,
                "admission_evidence_sha256": admission_ref.evidence_sha256,
                "admission_decision_hash": admission_ref.decision_hash,
                "admission_recorded_at_ms": int(admission_ref.recorded_at_ms),
                "price_basis": admission_ref.price_basis,
                "instrument_scope_json": _canonical(sorted(admission_ref.instrument_scope)),
                "prev_hash": prev_hash,
            }
            binding_values["row_hash"] = _sha256(binding_values)
            self._conn.execute(
                "INSERT INTO forward_decisions(alpha_id,decided_ts,recorded_at,weights_json,"
                "signals_json,source,realized) VALUES (?,?,?,?,?,?,0)",
                (
                    decision["alpha_id"], decision["decided_ts"], decision["recorded_at"],
                    weights_json, signals_json, decision["source"],
                ),
            )
            self._conn.execute(
                "INSERT INTO forward_decision_bindings(alpha_id,decided_ts,decision_payload_sha256,"
                "admission_id,admission_evidence_sha256,admission_decision_hash,"
                "admission_recorded_at_ms,price_basis,instrument_scope_json,prev_hash,row_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                tuple(binding_values[name] for name in (
                    "alpha_id", "decided_ts", "decision_payload_sha256", "admission_id",
                    "admission_evidence_sha256", "admission_decision_hash",
                    "admission_recorded_at_ms", "price_basis", "instrument_scope_json",
                    "prev_hash", "row_hash",
                )),
            )
        return True

    def commit_realization(
        self,
        alpha_id: str,
        decided_ts: int,
        *,
        as_of_ts: int,
        realized_return: float,
        realized_ic: Optional[float],
        source: str = FORWARD_L2_REALIZATION_SOURCE,
        inputs: Mapping[str, object],
        admission_ref: ForwardAdmissionRef,
        committed_at_ms: int,
    ) -> bool:
        normalized_inputs = {str(k): v for k, v in sorted(inputs.items())}
        if not normalized_inputs or not _finite(normalized_inputs):
            raise ForwardLedgerError("invalid realization evidence")
        ret = float(realized_return)
        ic = None if realized_ic is None else float(realized_ic)
        if not math.isfinite(ret) or (ic is not None and not math.isfinite(ic)):
            raise ForwardLedgerError("invalid realization evidence")
        inputs_json = _canonical(normalized_inputs)
        with self._write():
            if not self.decision_chain_valid():
                raise ForwardLedgerError("invalid forward decision binding chain")
            row = self._conn.execute(
                "SELECT d.*,b.decision_payload_sha256,b.admission_id,b.price_basis,"
                "b.instrument_scope_json FROM forward_decisions d INNER JOIN "
                "forward_decision_bindings b ON b.alpha_id=d.alpha_id AND b.decided_ts=d.decided_ts "
                "WHERE d.alpha_id=? AND d.decided_ts=?",
                (str(alpha_id), int(decided_ts)),
            ).fetchone()
            if row is None or int(row["realized"]) != 0:
                raise ForwardLedgerError("attested pending decision not found")
            scope = set(json.loads(row["instrument_scope_json"]))
            if (
                row["price_basis"] != admission_ref.price_basis
                or row["price_basis"] != "PERP_MARK_PRICE"
                or not set(normalized_inputs).issubset(scope)
                or not set(normalized_inputs).issubset(set(admission_ref.instrument_scope))
            ):
                raise ForwardLedgerError("realization inputs exceed admitted scope")
            existing = self._conn.execute(
                "SELECT row_hash FROM forward_realizations WHERE alpha_id=? "
                "AND (decided_ts=? OR as_of_ts=?)",
                (str(alpha_id), int(decided_ts), int(as_of_ts)),
            ).fetchone()
            if existing is not None:
                raise ForwardLedgerError("forward realization identity already exists")
            previous = self._conn.execute(
                "SELECT row_hash FROM forward_realizations ORDER BY realization_seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = _GENESIS if previous is None else str(previous["row_hash"])
            values = {
                "alpha_id": str(alpha_id),
                "decided_ts": int(decided_ts),
                "as_of_ts": int(as_of_ts),
                "decision_payload_sha256": row["decision_payload_sha256"],
                "realized_return": ret,
                "realized_ic": ic,
                "source": str(source),
                "inputs_json": inputs_json,
                "inputs_sha256": _sha256(normalized_inputs),
                "admission_id": admission_ref.admission_id,
                "admission_evidence_sha256": admission_ref.evidence_sha256,
                "admission_decision_hash": admission_ref.decision_hash,
                "committed_at_ms": int(committed_at_ms),
                "prev_hash": prev_hash,
            }
            values["row_hash"] = _sha256(values)
            self._conn.execute(
                "INSERT INTO forward_realizations(alpha_id,decided_ts,as_of_ts,"
                "decision_payload_sha256,realized_return,realized_ic,source,inputs_json,"
                "inputs_sha256,admission_id,admission_evidence_sha256,admission_decision_hash,"
                "committed_at_ms,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(values[name] for name in (
                    "alpha_id", "decided_ts", "as_of_ts", "decision_payload_sha256",
                    "realized_return", "realized_ic", "source", "inputs_json", "inputs_sha256",
                    "admission_id", "admission_evidence_sha256", "admission_decision_hash",
                    "committed_at_ms", "prev_hash", "row_hash",
                )),
            )
            self._conn.execute(
                "INSERT INTO paper_periods(alpha_id,as_of_ts,realized_return,realized_ic,source) "
                "VALUES (?,?,?,?,?)",
                (str(alpha_id), int(as_of_ts), ret, ic, str(source)),
            )
            updated = self._conn.execute(
                "UPDATE forward_decisions SET realized=1 WHERE alpha_id=? AND decided_ts=? AND realized=0",
                (str(alpha_id), int(decided_ts)),
            )
            if updated.rowcount != 1:
                raise ForwardLedgerError("attested decision changed during realization")
            event = {
                "event_type": "FORWARD_L2_REALIZATION_COMMITTED",
                "alpha_id": str(alpha_id),
                "decided_ts": int(decided_ts),
                "as_of_ts": int(as_of_ts),
                "realization_row_hash": values["row_hash"],
                "admission_id": admission_ref.admission_id,
                "source": str(source),
            }
            previous_event = self._conn.execute(
                "SELECT row_hash FROM forward_local_events ORDER BY event_seq DESC LIMIT 1"
            ).fetchone()
            event_values = {
                "event_type": event["event_type"],
                "alpha_id": event["alpha_id"],
                "decided_ts": event["decided_ts"],
                "payload_json": _canonical(event),
                "prev_hash": _GENESIS if previous_event is None else str(previous_event["row_hash"]),
            }
            event_values["row_hash"] = _sha256(event_values)
            self._conn.execute(
                "INSERT INTO forward_local_events(event_type,alpha_id,decided_ts,payload_json,prev_hash,row_hash) "
                "VALUES (?,?,?,?,?,?)",
                tuple(event_values[name] for name in (
                    "event_type", "alpha_id", "decided_ts", "payload_json", "prev_hash", "row_hash",
                )),
            )
        return True

    def attested_realizations(self, alpha_id: str | None = None) -> list[dict]:
        if not self.realization_chain_valid():
            raise ForwardLedgerError("invalid forward realization chain")
        if alpha_id is None:
            rows = self._conn.execute(
                "SELECT * FROM forward_realizations ORDER BY realization_seq"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM forward_realizations WHERE alpha_id=? ORDER BY realization_seq",
                (str(alpha_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def decision_chain_valid(self) -> bool:
        try:
            rows = self._conn.execute(
                "SELECT b.*,d.recorded_at,d.weights_json,d.signals_json,d.source "
                "FROM forward_decision_bindings b LEFT JOIN forward_decisions d "
                "ON d.alpha_id=b.alpha_id AND d.decided_ts=b.decided_ts ORDER BY b.binding_seq"
            ).fetchall()
            previous = _GENESIS
            for row in rows:
                if row["recorded_at"] is None or row["prev_hash"] != previous:
                    return False
                scope = json.loads(row["instrument_scope_json"])
                if scope != sorted(set(scope)):
                    return False
                decision = {
                    "alpha_id": row["alpha_id"],
                    "decided_ts": int(row["decided_ts"]),
                    "recorded_at": int(row["recorded_at"]),
                    "weights": json.loads(row["weights_json"]),
                    "signals": None if row["signals_json"] is None else json.loads(row["signals_json"]),
                    "source": row["source"],
                }
                binding = {
                    name: row[name]
                    for name in (
                        "alpha_id", "decided_ts", "decision_payload_sha256", "admission_id",
                        "admission_evidence_sha256", "admission_decision_hash",
                        "admission_recorded_at_ms", "price_basis", "instrument_scope_json", "prev_hash",
                    )
                }
                row_hash = binding.pop("row_hash", None)
                if row["decision_payload_sha256"] != _sha256(decision):
                    return False
                expected = dict(binding)
                expected["row_hash"] = row["row_hash"]
                if _sha256(binding) != row["row_hash"]:
                    return False
                previous = str(row["row_hash"])
            return True
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError):
            return False

    def realization_chain_valid(self) -> bool:
        try:
            rows = self._conn.execute(
                "SELECT r.*,d.realized,d.recorded_at FROM forward_realizations r "
                "LEFT JOIN forward_decisions d ON d.alpha_id=r.alpha_id AND d.decided_ts=r.decided_ts "
                "ORDER BY r.realization_seq"
            ).fetchall()
            previous = _GENESIS
            for row in rows:
                if row["realized"] != 1 or row["recorded_at"] is None or row["prev_hash"] != previous:
                    return False
                values = {
                    name: row[name]
                    for name in (
                        "alpha_id", "decided_ts", "as_of_ts", "decision_payload_sha256",
                        "realized_return", "realized_ic", "source", "inputs_json", "inputs_sha256",
                        "admission_id", "admission_evidence_sha256", "admission_decision_hash",
                        "committed_at_ms", "prev_hash",
                    )
                }
                if _sha256(values) != row["row_hash"]:
                    return False
                previous = str(row["row_hash"])
            return True
        except (KeyError, TypeError, ValueError, sqlite3.DatabaseError):
            return False

    def mark_realized(self, alpha_id: str, decided_ts: int) -> None:
        bound = self._conn.execute(
            "SELECT 1 FROM forward_decision_bindings WHERE alpha_id=? AND decided_ts=?",
            (str(alpha_id), int(decided_ts)),
        ).fetchone()
        if bound is not None:
            raise ForwardLedgerError("attested decision requires commit_realization")
        cursor = self._conn.execute(
            "UPDATE forward_decisions SET realized=1 WHERE alpha_id=? AND decided_ts=? AND realized=0",
            (str(alpha_id), int(decided_ts)),
        )
        if cursor.rowcount != 1:
            self._conn.rollback()
            raise ForwardLedgerError(f"pending forward decision not found: {alpha_id}@{int(decided_ts)}")
        self._conn.commit()


__all__ = ["AttestedForwardLedger"]
