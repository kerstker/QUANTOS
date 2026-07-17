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
CREATE TABLE IF NOT EXISTS forward_realizations (
  realization_seq           INTEGER PRIMARY KEY AUTOINCREMENT,
  alpha_id                  TEXT    NOT NULL,
  decided_ts                INTEGER NOT NULL,
  as_of_ts                  INTEGER NOT NULL,
  decision_payload_sha256   TEXT    NOT NULL,
  realized_return           REAL    NOT NULL,
  realized_ic               REAL,
  source                    TEXT    NOT NULL,
  inputs_json               TEXT    NOT NULL,
  inputs_sha256             TEXT    NOT NULL,
  admission_id              TEXT    NOT NULL,
  admission_evidence_sha256 TEXT    NOT NULL,
  admission_decision_hash   TEXT    NOT NULL,
  committed_at_ms           INTEGER NOT NULL,
  prev_hash                 TEXT    NOT NULL,
  row_hash                  TEXT    NOT NULL UNIQUE,
  UNIQUE(alpha_id, decided_ts),
  UNIQUE(alpha_id, as_of_ts)
);
CREATE TABLE IF NOT EXISTS forward_local_events (
  event_seq    INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type   TEXT    NOT NULL,
  alpha_id     TEXT    NOT NULL,
  decided_ts   INTEGER NOT NULL,
  payload_json TEXT    NOT NULL,
  prev_hash    TEXT    NOT NULL,
  row_hash     TEXT    NOT NULL UNIQUE
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
        missing = {
            "forward_decisions",
            "forward_decision_bindings",
            "forward_realizations",
            "forward_local_events",
        } - names
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

    def commit_realization(
        self,
        alpha_id: str,
        decided_ts: int,
        *,
        as_of_ts: int,
        realized_return: float,
        realized_ic: Optional[float],
        source: str,
        inputs: Mapping[str, object],
        admission_ref: ForwardAdmissionRef,
        committed_at_ms: int,
    ) -> bool:
        normalized_inputs = {str(key): value for key, value in sorted(inputs.items())}
        if not normalized_inputs:
            raise ForwardLedgerError("refusing to commit a realization without retained inputs")
        if len(normalized_inputs) != len(inputs):
            raise ForwardLedgerError("realization input identities are ambiguous")
        realization = {
            "alpha_id": str(alpha_id),
            "decided_ts": int(decided_ts),
            "as_of_ts": int(as_of_ts),
            "realized_return": float(realized_return),
            "realized_ic": None if realized_ic is None else float(realized_ic),
            "source": str(source),
            "inputs_json": _canonical(normalized_inputs),
            "inputs_sha256": _sha256(normalized_inputs),
            "committed_at_ms": int(committed_at_ms),
        }

        try:
            with self._immediate_transaction():
                if not self._paper_periods_exists():
                    raise ForwardLedgerError(
                        "forward realization commit requires the paper_periods table"
                    )
                if not self._decision_chain_valid():
                    raise ForwardLedgerError("invalid forward decision binding chain")
                if not self._realization_chain_valid():
                    raise ForwardLedgerError("invalid forward realization chain")

                decision = self._conn.execute(
                    "SELECT d.recorded_at,d.weights_json,d.signals_json,d.source,d.realized,"
                    "b.decision_payload_sha256,b.admission_id,b.admission_evidence_sha256,"
                    "b.admission_decision_hash,b.admission_recorded_at_ms,b.price_basis,"
                    "b.instrument_scope_json FROM forward_decisions AS d "
                    "LEFT JOIN forward_decision_bindings AS b "
                    "ON b.alpha_id=d.alpha_id AND b.decided_ts=d.decided_ts "
                    "WHERE d.alpha_id=? AND d.decided_ts=?",
                    (realization["alpha_id"], realization["decided_ts"]),
                ).fetchone()
                if decision is None or decision["decision_payload_sha256"] is None:
                    raise ForwardLedgerError("attested forward decision not found for realization")
                if not self._admission_matches_binding(decision, admission_ref):
                    raise ForwardLedgerError(
                        "realization admission reference does not match the attested decision"
                    )
                admitted_scope = set(json.loads(decision["instrument_scope_json"]))
                if not set(normalized_inputs).issubset(admitted_scope):
                    raise ForwardLedgerError(
                        "realization inputs are outside the admitted instrument scope"
                    )

                existing = self._conn.execute(
                    "SELECT * FROM forward_realizations WHERE alpha_id=? "
                    "AND (decided_ts=? OR as_of_ts=?)",
                    (
                        realization["alpha_id"],
                        realization["decided_ts"],
                        realization["as_of_ts"],
                    ),
                ).fetchone()
                period = self._conn.execute(
                    "SELECT realized_return,realized_ic,source FROM paper_periods "
                    "WHERE alpha_id=? AND as_of_ts=?",
                    (realization["alpha_id"], realization["as_of_ts"]),
                ).fetchone()
                if existing is not None:
                    if self._same_retained_realization(
                        existing,
                        realization=realization,
                        decision_payload_sha256=str(decision["decision_payload_sha256"]),
                        admission_ref=admission_ref,
                    ) and self._realization_chain_valid():
                        return False
                    raise ForwardLedgerError(
                        "forward realization identity reused with different content: "
                        f"{realization['alpha_id']}@{realization['decided_ts']}"
                    )
                if period is not None:
                    raise ForwardLedgerError(
                        "paper period identity already exists without its atomic realization: "
                        f"{realization['alpha_id']}@{realization['as_of_ts']}"
                    )
                if int(decision["realized"]) != 0:
                    raise ForwardLedgerError("attested pending decision not found for realization")

                previous = self._conn.execute(
                    "SELECT row_hash FROM forward_realizations "
                    "ORDER BY realization_seq DESC LIMIT 1"
                ).fetchone()
                realization_prev_hash = (
                    _GENESIS_HASH if previous is None else str(previous["row_hash"])
                )
                realization_values = self._realization_values(
                    realization=realization,
                    decision_payload_sha256=str(decision["decision_payload_sha256"]),
                    admission_ref=admission_ref,
                    prev_hash=realization_prev_hash,
                )
                event_payload = {
                    "event_type": "FORWARD_L2_REALIZATION_COMMITTED",
                    "alpha_id": realization["alpha_id"],
                    "decided_ts": realization["decided_ts"],
                    "as_of_ts": realization["as_of_ts"],
                    "realization_row_hash": realization_values["row_hash"],
                    "admission_id": admission_ref.admission_id,
                    "source": realization["source"],
                }
                previous_event = self._conn.execute(
                    "SELECT row_hash FROM forward_local_events ORDER BY event_seq DESC LIMIT 1"
                ).fetchone()
                event_values = self._event_values(
                    event_payload,
                    prev_hash=(
                        _GENESIS_HASH if previous_event is None else str(previous_event["row_hash"])
                    ),
                )

                self._conn.execute(
                    "INSERT INTO forward_realizations("
                    "alpha_id,decided_ts,as_of_ts,decision_payload_sha256,realized_return,"
                    "realized_ic,source,inputs_json,inputs_sha256,admission_id,"
                    "admission_evidence_sha256,admission_decision_hash,committed_at_ms,"
                    "prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(
                        realization_values[name]
                        for name in self._realization_column_names()
                    ),
                )
                self._conn.execute(
                    "INSERT INTO paper_periods"
                    "(alpha_id,as_of_ts,realized_return,realized_ic,source) VALUES (?,?,?,?,?)",
                    (
                        realization["alpha_id"],
                        realization["as_of_ts"],
                        realization["realized_return"],
                        realization["realized_ic"],
                        realization["source"],
                    ),
                )
                updated = self._conn.execute(
                    "UPDATE forward_decisions SET realized=1 "
                    "WHERE alpha_id=? AND decided_ts=? AND realized=0",
                    (realization["alpha_id"], realization["decided_ts"]),
                )
                if updated.rowcount != 1:
                    raise ForwardLedgerError(
                        "attested pending decision changed during realization commit"
                    )
                self._conn.execute(
                    "INSERT INTO forward_local_events"
                    "(event_type,alpha_id,decided_ts,payload_json,prev_hash,row_hash) "
                    "VALUES (?,?,?,?,?,?)",
                    tuple(event_values[name] for name in self._event_column_names()),
                )
        except ForwardLedgerError:
            raise
        except sqlite3.DatabaseError as exc:
            raise ForwardLedgerError(
                f"forward realization commit failed: {type(exc).__name__}"
            ) from exc
        return True

    def _paper_periods_exists(self) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_periods'"
        ).fetchone()
        return row is not None

    @staticmethod
    def _admission_matches_binding(
        binding: sqlite3.Row,
        admission_ref: ForwardAdmissionRef,
    ) -> bool:
        return (
            binding["admission_id"] == admission_ref.admission_id
            and binding["admission_evidence_sha256"] == admission_ref.evidence_sha256
            and binding["admission_decision_hash"] == admission_ref.decision_hash
            and int(binding["admission_recorded_at_ms"]) == int(admission_ref.recorded_at_ms)
            and binding["price_basis"] == admission_ref.price_basis
            and binding["instrument_scope_json"]
            == _canonical(sorted(admission_ref.instrument_scope))
        )

    @staticmethod
    def _realization_column_names() -> tuple[str, ...]:
        return (
            "alpha_id",
            "decided_ts",
            "as_of_ts",
            "decision_payload_sha256",
            "realized_return",
            "realized_ic",
            "source",
            "inputs_json",
            "inputs_sha256",
            "admission_id",
            "admission_evidence_sha256",
            "admission_decision_hash",
            "committed_at_ms",
            "prev_hash",
            "row_hash",
        )

    @classmethod
    def _realization_values(
        cls,
        *,
        realization: Mapping[str, object],
        decision_payload_sha256: str,
        admission_ref: ForwardAdmissionRef,
        prev_hash: str,
    ) -> dict[str, object]:
        values: dict[str, object] = {
            "alpha_id": realization["alpha_id"],
            "decided_ts": realization["decided_ts"],
            "as_of_ts": realization["as_of_ts"],
            "decision_payload_sha256": decision_payload_sha256,
            "realized_return": realization["realized_return"],
            "realized_ic": realization["realized_ic"],
            "source": realization["source"],
            "inputs_json": realization["inputs_json"],
            "inputs_sha256": realization["inputs_sha256"],
            "admission_id": admission_ref.admission_id,
            "admission_evidence_sha256": admission_ref.evidence_sha256,
            "admission_decision_hash": admission_ref.decision_hash,
            "committed_at_ms": realization["committed_at_ms"],
            "prev_hash": prev_hash,
        }
        values["row_hash"] = _sha256(values)
        return values

    @staticmethod
    def _event_column_names() -> tuple[str, ...]:
        return (
            "event_type",
            "alpha_id",
            "decided_ts",
            "payload_json",
            "prev_hash",
            "row_hash",
        )

    @classmethod
    def _event_values(
        cls,
        payload: Mapping[str, object],
        *,
        prev_hash: str,
    ) -> dict[str, object]:
        values: dict[str, object] = {
            "event_type": payload["event_type"],
            "alpha_id": payload["alpha_id"],
            "decided_ts": payload["decided_ts"],
            "payload_json": _canonical(payload),
            "prev_hash": prev_hash,
        }
        values["row_hash"] = _sha256(values)
        return values

    def _same_retained_realization(
        self,
        row: sqlite3.Row,
        *,
        realization: Mapping[str, object],
        decision_payload_sha256: str,
        admission_ref: ForwardAdmissionRef,
    ) -> bool:
        expected = self._realization_values(
            realization=realization,
            decision_payload_sha256=decision_payload_sha256,
            admission_ref=admission_ref,
            prev_hash=str(row["prev_hash"]),
        )
        return all(row[name] == expected[name] for name in self._realization_column_names())

    @contextmanager
    def _immediate_transaction(self):
        if self._conn.in_transaction:
            raise ForwardLedgerError("forward ledger write requires no active transaction")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._conn.commit()
        except BaseException:
            try:
                if self._conn.in_transaction:
                    self._conn.rollback()
            except BaseException as rollback_error:
                raise ForwardLedgerError("forward ledger write transaction cleanup failed") from rollback_error
            raise

    @contextmanager
    def _read_snapshot(self):
        if self._conn.in_transaction:
            raise ForwardLedgerError("forward ledger read requires no active transaction")
        self._conn.execute("BEGIN")
        try:
            yield
            self._conn.commit()
        except BaseException:
            try:
                if self._conn.in_transaction:
                    self._conn.rollback()
            except BaseException as rollback_error:
                raise ForwardLedgerError("forward ledger read transaction cleanup failed") from rollback_error
            raise

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

    def attested_realizations(self, alpha_id: str | None = None) -> list[dict]:
        with self._read_snapshot():
            if not self._realization_chain_valid():
                raise ForwardLedgerError("invalid forward realization chain")
            if alpha_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM forward_realizations ORDER BY realization_seq"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM forward_realizations WHERE alpha_id=? "
                    "ORDER BY realization_seq",
                    (str(alpha_id),),
                ).fetchall()
            return [dict(row) for row in rows]

    def realization_chain_valid(self) -> bool:
        return self._realization_chain_valid()

    def _realization_chain_valid(self) -> bool:
        try:
            if not self._paper_periods_exists() or not self._decision_chain_valid():
                return False
            rows = self._conn.execute(
                "SELECT r.*,d.recorded_at,d.weights_json,d.signals_json,"
                "d.source AS decision_source,d.realized AS decision_realized,"
                "b.decision_payload_sha256 AS bound_decision_payload_sha256,"
                "b.admission_id AS bound_admission_id,"
                "b.admission_evidence_sha256 AS bound_admission_evidence_sha256,"
                "b.admission_decision_hash AS bound_admission_decision_hash,"
                "p.realized_return AS period_realized_return,"
                "p.realized_ic AS period_realized_ic,p.source AS period_source "
                "FROM forward_realizations AS r "
                "LEFT JOIN forward_decisions AS d "
                "ON d.alpha_id=r.alpha_id AND d.decided_ts=r.decided_ts "
                "LEFT JOIN forward_decision_bindings AS b "
                "ON b.alpha_id=r.alpha_id AND b.decided_ts=r.decided_ts "
                "LEFT JOIN paper_periods AS p "
                "ON p.alpha_id=r.alpha_id AND p.as_of_ts=r.as_of_ts "
                "ORDER BY r.realization_seq"
            ).fetchall()
            previous_hash = _GENESIS_HASH
            expected_events: dict[tuple[str, int], dict[str, object]] = {}
            for row in rows:
                if row["recorded_at"] is None or row["decision_realized"] != 1:
                    return False
                if row["prev_hash"] != previous_hash:
                    return False
                inputs = json.loads(row["inputs_json"])
                if not isinstance(inputs, dict) or row["inputs_json"] != _canonical(inputs):
                    return False
                if row["inputs_sha256"] != _sha256(inputs):
                    return False
                realization_values = {
                    name: row[name]
                    for name in self._realization_column_names()
                    if name != "row_hash"
                }
                if row["row_hash"] != _sha256(realization_values):
                    return False

                decision = {
                    "alpha_id": row["alpha_id"],
                    "decided_ts": int(row["decided_ts"]),
                    "recorded_at": int(row["recorded_at"]),
                    "weights": json.loads(row["weights_json"]),
                    "signals": (
                        None
                        if row["signals_json"] is None
                        else json.loads(row["signals_json"])
                    ),
                    "source": row["decision_source"],
                }
                decision_digest = _sha256(decision)
                if (
                    row["decision_payload_sha256"] != decision_digest
                    or row["bound_decision_payload_sha256"] != decision_digest
                    or row["admission_id"] != row["bound_admission_id"]
                    or row["admission_evidence_sha256"]
                    != row["bound_admission_evidence_sha256"]
                    or row["admission_decision_hash"]
                    != row["bound_admission_decision_hash"]
                ):
                    return False
                if (
                    row["period_realized_return"] is None
                    or float(row["period_realized_return"]) != float(row["realized_return"])
                    or (
                        None
                        if row["period_realized_ic"] is None
                        else float(row["period_realized_ic"])
                    )
                    != (
                        None if row["realized_ic"] is None else float(row["realized_ic"])
                    )
                    or row["period_source"] != row["source"]
                ):
                    return False
                key = (str(row["alpha_id"]), int(row["decided_ts"]))
                expected_events[key] = {
                    "event_type": "FORWARD_L2_REALIZATION_COMMITTED",
                    "alpha_id": row["alpha_id"],
                    "decided_ts": int(row["decided_ts"]),
                    "as_of_ts": int(row["as_of_ts"]),
                    "realization_row_hash": row["row_hash"],
                    "admission_id": row["admission_id"],
                    "source": row["source"],
                }
                previous_hash = str(row["row_hash"])

            events = self._conn.execute(
                "SELECT * FROM forward_local_events ORDER BY event_seq"
            ).fetchall()
            if len(events) != len(expected_events):
                return False
            previous_hash = _GENESIS_HASH
            seen: set[tuple[str, int]] = set()
            for row in events:
                if row["prev_hash"] != previous_hash:
                    return False
                event_values = {
                    name: row[name]
                    for name in self._event_column_names()
                    if name != "row_hash"
                }
                if row["row_hash"] != _sha256(event_values):
                    return False
                payload = json.loads(row["payload_json"])
                if not isinstance(payload, dict) or row["payload_json"] != _canonical(payload):
                    return False
                key = (str(row["alpha_id"]), int(row["decided_ts"]))
                if (
                    key in seen
                    or row["event_type"] != "FORWARD_L2_REALIZATION_COMMITTED"
                    or payload != expected_events.get(key)
                ):
                    return False
                seen.add(key)
                previous_hash = str(row["row_hash"])
            return seen == set(expected_events)
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            sqlite3.DatabaseError,
        ):
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
