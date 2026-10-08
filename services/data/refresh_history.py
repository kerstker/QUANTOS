"""Tamper-evident operational history and SLO evaluation for market-data refreshes.

The store is append-only at the SQLite layer. Each entry commits to the complete
refresh status plus the previous entry hash; normalized source rows support SLO
and watermark queries without weakening the canonical hash commitment.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Mapping, Sequence

from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from quantos.contracts.events import (
    EVENT_DATA_REFRESH_RECORDED,
    Event,
    EventOutboxMessage,
)

from .events import data_refresh_recorded_event


SCHEMA_VERSION = 1
SOURCE_OUTCOMES = {"PASS", "FAILED", "BUDGET_EXCEEDED", "CRASHED"}


class RefreshHistoryIntegrityError(RuntimeError):
    """Raised when an existing refresh-history chain cannot be trusted."""


_REQUIRED_REFRESH_TABLES = frozenset(
    {
        "refresh_history",
        "refresh_source_history",
        "refresh_event_outbox",
        "refresh_event_dispatch",
    }
)


def _require_existing_refresh_schema(db_path: str) -> None:
    if db_path == ":memory:":
        raise RefreshHistoryIntegrityError(
            "in-memory refresh history cannot be reopened"
        )
    try:
        uri = f"{Path(db_path).resolve().as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise RefreshHistoryIntegrityError(
            "refresh history is unavailable"
        ) from exc
    missing = sorted(_REQUIRED_REFRESH_TABLES - tables)
    if missing:
        raise RefreshHistoryIntegrityError(
            f"refresh history schema is incomplete; missing tables: {missing}"
        )


@dataclass(frozen=True)
class RefreshHistoryEntry:
    seq: int
    run_id: str
    ran_at_ms: int
    recorded_at_ms: int
    status_hash: str
    prev_hash: str
    entry_hash: str


def canonical_json(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_outcome(report: Mapping) -> str:
    explicit = str(report.get("outcome", "")).upper()
    if explicit in SOURCE_OUTCOMES:
        return explicit
    kinds = {
        str(detail.get("kind", "")).upper()
        for detail in report.get("failure_details", ())
        if isinstance(detail, Mapping)
    }
    if "BUDGET_EXCEEDED" in kinds:
        return "BUDGET_EXCEEDED"
    if "SOURCE_CRASHED" in kinds:
        return "CRASHED"
    if report.get("failed") or report.get("failed_tickers") or report.get("quality_failed"):
        return "FAILED"
    return "PASS"


def _instrument_coverage(outcome: str, failed_count: int, n_total: int) -> float | None:
    if outcome in {"BUDGET_EXCEEDED", "CRASHED"}:
        return 0.0
    if n_total <= 0:
        return None
    bounded_failures = min(max(0, int(failed_count)), int(n_total))
    return (int(n_total) - bounded_failures) / int(n_total)


def _entry_hash(*, run_id: str, ran_at_ms: int, recorded_at_ms: int,
                status_hash: str, prev_hash: str) -> str:
    return _sha256_text(canonical_json({
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "ran_at_ms": ran_at_ms,
        "recorded_at_ms": recorded_at_ms,
        "status_hash": status_hash,
        "prev_hash": prev_hash,
    }))


def _outbox_hash(
    *,
    history_seq: int,
    message: EventOutboxMessage,
    prev_record_hash: str,
) -> str:
    return _sha256_text(canonical_json({
        "causation_id": message.causation_id,
        "correlation_id": message.correlation_id,
        "event": message.event.model_dump(mode="json"),
        "history_seq": history_seq,
        "prev_record_hash": prev_record_hash,
    }))


def _dispatch_hash(
    *,
    event_id: str,
    dispatched_at: int,
    event_repro_hash: str,
    prev_record_hash: str,
) -> str:
    return _sha256_text(canonical_json({
        "dispatched_at": dispatched_at,
        "event_id": event_id,
        "event_repro_hash": event_repro_hash,
        "prev_record_hash": prev_record_hash,
    }))


class RefreshHistoryStore:
    """Thread-safe, append-only refresh ledger. All timestamps are caller-injected."""

    def __init__(self, db_path: str, *, create: bool = True):
        if create and db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        if not create:
            _require_existing_refresh_schema(db_path)
        self.db_path = db_path
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        try:
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA busy_timeout=5000")
            if db_path != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._lock = threading.Lock()
            if create:
                self._create_schema()
            else:
                with self._lock:
                    if not self._chain_valid_unlocked():
                        raise RefreshHistoryIntegrityError(
                            "refresh history or event outbox failed verification"
                        )
        except BaseException:
            self._connection.close()
            raise

    def _create_schema(self) -> None:
        with self._lock:
            tables = {
                str(row[0])
                for row in self._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            outbox_tables = {"refresh_event_outbox", "refresh_event_dispatch"}
            if tables & outbox_tables and not outbox_tables <= tables:
                raise RefreshHistoryIntegrityError(
                    "refresh event outbox schema is partial"
                )
            had_outbox = outbox_tables <= tables
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS refresh_history (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE,
                    ran_at_ms INTEGER NOT NULL,
                    recorded_at_ms INTEGER NOT NULL,
                    status_json TEXT NOT NULL,
                    status_hash TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS refresh_source_history (
                    history_seq INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    failed_count INTEGER NOT NULL,
                    watermark_after INTEGER,
                    freshest_days INTEGER,
                    n_stale INTEGER,
                    n_total INTEGER NOT NULL,
                    budget_scope TEXT,
                    PRIMARY KEY (history_seq, source),
                    FOREIGN KEY (history_seq) REFERENCES refresh_history(seq)
                );
                CREATE TABLE IF NOT EXISTS refresh_event_outbox (
                    outbox_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    history_seq INTEGER NOT NULL UNIQUE,
                    event_id TEXT NOT NULL UNIQUE,
                    event_json TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    causation_id TEXT NOT NULL,
                    event_repro_hash TEXT NOT NULL,
                    prev_record_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL UNIQUE,
                    FOREIGN KEY (history_seq) REFERENCES refresh_history(seq)
                );
                CREATE TABLE IF NOT EXISTS refresh_event_dispatch (
                    dispatch_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    dispatched_at INTEGER NOT NULL CHECK (dispatched_at >= 0),
                    event_repro_hash TEXT NOT NULL,
                    prev_record_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL UNIQUE
                );
                CREATE TRIGGER IF NOT EXISTS refresh_history_no_update
                BEFORE UPDATE ON refresh_history
                BEGIN SELECT RAISE(ABORT, 'refresh_history is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS refresh_history_no_delete
                BEFORE DELETE ON refresh_history
                BEGIN SELECT RAISE(ABORT, 'refresh_history is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS refresh_source_history_no_update
                BEFORE UPDATE ON refresh_source_history
                BEGIN SELECT RAISE(ABORT, 'refresh_source_history is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS refresh_source_history_no_delete
                BEFORE DELETE ON refresh_source_history
                BEGIN SELECT RAISE(ABORT, 'refresh_source_history is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS refresh_event_outbox_no_update
                BEFORE UPDATE ON refresh_event_outbox
                BEGIN SELECT RAISE(ABORT, 'refresh_event_outbox is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS refresh_event_outbox_no_delete
                BEFORE DELETE ON refresh_event_outbox
                BEGIN SELECT RAISE(ABORT, 'refresh_event_outbox is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS refresh_event_dispatch_no_update
                BEFORE UPDATE ON refresh_event_dispatch
                BEGIN SELECT RAISE(ABORT, 'refresh_event_dispatch is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS refresh_event_dispatch_no_delete
                BEFORE DELETE ON refresh_event_dispatch
                BEGIN SELECT RAISE(ABORT, 'refresh_event_dispatch is append-only'); END;
                """
            )
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                if not self._history_chain_valid_unlocked():
                    raise RefreshHistoryIntegrityError(
                        "legacy refresh history failed hash-chain verification"
                    )
                if not had_outbox:
                    previous = ""
                    for row in self._history_rows():
                        message = self._expected_message_for_history_row(row)
                        digest = _outbox_hash(
                            history_seq=int(row[0]),
                            message=message,
                            prev_record_hash=previous,
                        )
                        self._connection.execute(
                            "INSERT INTO refresh_event_outbox"
                            " (history_seq,event_id,event_json,correlation_id,"
                            " causation_id,event_repro_hash,prev_record_hash,record_hash)"
                            " VALUES (?,?,?,?,?,?,?,?)",
                            (
                                int(row[0]),
                                message.event.event_id,
                                canonical_json(message.event.model_dump(mode="json")),
                                message.correlation_id,
                                message.causation_id,
                                message.event.reproducibility_hash,
                                previous,
                                digest,
                            ),
                        )
                        previous = digest
                if not self._chain_valid_unlocked():
                    raise RefreshHistoryIntegrityError(
                        "refresh history or event outbox failed verification"
                    )
                self._connection.commit()
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    def close(self) -> None:
        self._connection.close()

    def _history_rows(self):
        return self._connection.execute(
            "SELECT seq, run_id, ran_at_ms, recorded_at_ms, status_json, status_hash,"
            " prev_hash, entry_hash FROM refresh_history ORDER BY seq"
        ).fetchall()

    def _history_chain_valid_unlocked(self) -> bool:
        prev_hash = ""
        for row in self._history_rows():
            seq, run_id, ran_at_ms, recorded_at_ms, status_json, stored_status_hash, \
                stored_prev_hash, stored_entry_hash = row
            if stored_prev_hash != prev_hash:
                return False
            if _sha256_text(status_json) != stored_status_hash:
                return False
            try:
                status = json.loads(status_json)
                if canonical_json(status) != status_json:
                    return False
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            stored_sources = self._connection.execute(
                "SELECT source, outcome, duration_ms, failed_count, watermark_after,"
                " freshest_days, n_stale, n_total, budget_scope"
                " FROM refresh_source_history WHERE history_seq=? ORDER BY source",
                (seq,),
            ).fetchall()
            expected_sources = sorted(self._normalized_source_rows(status), key=lambda item: item[0])
            if stored_sources != expected_sources:
                return False
            expected_entry_hash = _entry_hash(
                run_id=run_id,
                ran_at_ms=int(ran_at_ms),
                recorded_at_ms=int(recorded_at_ms),
                status_hash=stored_status_hash,
                prev_hash=stored_prev_hash,
            )
            if expected_entry_hash != stored_entry_hash:
                return False
            prev_hash = stored_entry_hash
        return True

    def _outbox_rows(self):
        return self._connection.execute(
            "SELECT outbox_seq,history_seq,event_id,event_json,correlation_id,"
            " causation_id,event_repro_hash,prev_record_hash,record_hash"
            " FROM refresh_event_outbox ORDER BY outbox_seq"
        ).fetchall()

    def _dispatch_rows(self):
        return self._connection.execute(
            "SELECT dispatch_seq,event_id,dispatched_at,event_repro_hash,"
            " prev_record_hash,record_hash FROM refresh_event_dispatch"
            " ORDER BY dispatch_seq"
        ).fetchall()

    @staticmethod
    def _message_from_outbox_row(row) -> EventOutboxMessage:
        try:
            event = Event.model_validate_json(row[3])
        except Exception as exc:
            raise RefreshHistoryIntegrityError(
                f"refresh outbox row {row[0]} cannot be reconstructed"
            ) from exc
        if event.reproducibility_hash != row[6]:
            raise RefreshHistoryIntegrityError(
                f"refresh outbox row {row[0]} payload hash mismatch"
            )
        return EventOutboxMessage(
            event=event,
            correlation_id=row[4],
            causation_id=row[5],
        )

    @classmethod
    def _expected_message_for_history_row(cls, row) -> EventOutboxMessage:
        status = json.loads(row[4])
        normalized = cls._normalized_source_rows(status)
        outcome = "PASS" if normalized and all(
            item[1] == "PASS" for item in normalized
        ) else "FAILED"
        event = data_refresh_recorded_event(
            run_id=str(row[1]),
            outcome=outcome,
            source_count=len(normalized),
            failed_source_count=sum(1 for item in normalized if item[1] != "PASS"),
            failed_instrument_count=sum(int(item[3]) for item in normalized),
            duration_ms=max(0, int(status.get("duration_ms", 0))),
            status_hash=str(row[5]),
            history_entry_hash=str(row[7]),
            at_ms=int(row[3]),
        )
        return EventOutboxMessage(event=event, correlation_id=str(row[1]))

    def _verify_event_outbox_unlocked(
        self,
    ) -> tuple[str, dict[str, EventOutboxMessage], str]:
        if not self._history_chain_valid_unlocked():
            raise RefreshHistoryIntegrityError(
                "refresh history failed hash-chain verification"
            )
        history_by_seq = {int(row[0]): row for row in self._history_rows()}
        outbox_rows = self._outbox_rows()
        if len(outbox_rows) != len(history_by_seq):
            raise RefreshHistoryIntegrityError(
                "refresh history and event outbox cardinality mismatch"
            )
        previous = ""
        messages: dict[str, EventOutboxMessage] = {}
        for row in outbox_rows:
            history_row = history_by_seq.get(int(row[1]))
            if history_row is None:
                raise RefreshHistoryIntegrityError(
                    f"refresh outbox row {row[0]} references missing history"
                )
            message = self._message_from_outbox_row(row)
            expected = self._expected_message_for_history_row(history_row)
            try:
                CANONICAL_EVENT_SCHEMAS.validate(message.event)
            except Exception as exc:
                raise RefreshHistoryIntegrityError(
                    f"refresh outbox row {row[0]} schema is invalid"
                ) from exc
            if (
                message != expected
                or row[2] != message.event.event_id
                or message.event.event_type != EVENT_DATA_REFRESH_RECORDED
            ):
                raise RefreshHistoryIntegrityError(
                    f"refresh outbox row {row[0]} does not match history"
                )
            if row[7] != previous:
                raise RefreshHistoryIntegrityError(
                    f"refresh outbox row {row[0]} previous hash mismatch"
                )
            digest = _outbox_hash(
                history_seq=int(row[1]),
                message=message,
                prev_record_hash=previous,
            )
            if row[8] != digest:
                raise RefreshHistoryIntegrityError(
                    f"refresh outbox row {row[0]} record hash mismatch"
                )
            if message.outbox_id in messages:
                raise RefreshHistoryIntegrityError(
                    f"duplicate refresh event id: {message.outbox_id}"
                )
            messages[message.outbox_id] = message
            previous = digest

        dispatch_previous = ""
        for row in self._dispatch_rows():
            message = messages.get(row[1])
            if message is None or message.event.reproducibility_hash != row[3]:
                raise RefreshHistoryIntegrityError(
                    f"refresh dispatch row {row[0]} reference mismatch"
                )
            if row[4] != dispatch_previous:
                raise RefreshHistoryIntegrityError(
                    f"refresh dispatch row {row[0]} previous hash mismatch"
                )
            digest = _dispatch_hash(
                event_id=row[1],
                dispatched_at=int(row[2]),
                event_repro_hash=row[3],
                prev_record_hash=dispatch_previous,
            )
            if row[5] != digest:
                raise RefreshHistoryIntegrityError(
                    f"refresh dispatch row {row[0]} record hash mismatch"
                )
            dispatch_previous = digest
        return previous, messages, dispatch_previous

    def _chain_valid_unlocked(self) -> bool:
        try:
            if not self._history_chain_valid_unlocked():
                return False
            self._verify_event_outbox_unlocked()
            return True
        except (RefreshHistoryIntegrityError, sqlite3.DatabaseError, ValueError):
            return False

    def chain_valid(self) -> bool:
        with self._lock:
            return self._chain_valid_unlocked()

    @property
    def head_hash(self) -> str:
        with self._lock:
            row = self._connection.execute(
                "SELECT entry_hash FROM refresh_history ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            return str(row[0]) if row else ""

    def append(self, status: Mapping, *, recorded_at_ms: int,
               run_id: str | None = None) -> RefreshHistoryEntry:
        status_document = canonical_json(dict(status))
        status_hash = _sha256_text(status_document)
        ran_at_ms = int(status["ran_at"])
        resolved_run_id = run_id or f"refresh-{ran_at_ms}-{status_hash[:16]}"

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if not self._chain_valid_unlocked():
                    raise RefreshHistoryIntegrityError(
                        "existing refresh history failed hash-chain verification"
                    )
                outbox_head, _, _ = self._verify_event_outbox_unlocked()
                row = self._connection.execute(
                    "SELECT entry_hash FROM refresh_history ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                prev_hash = str(row[0]) if row else ""
                entry_hash = _entry_hash(
                    run_id=resolved_run_id,
                    ran_at_ms=ran_at_ms,
                    recorded_at_ms=int(recorded_at_ms),
                    status_hash=status_hash,
                    prev_hash=prev_hash,
                )
                cursor = self._connection.execute(
                    "INSERT INTO refresh_history"
                    " (run_id, ran_at_ms, recorded_at_ms, status_json, status_hash,"
                    " prev_hash, entry_hash) VALUES (?,?,?,?,?,?,?)",
                    (resolved_run_id, ran_at_ms, int(recorded_at_ms), status_document,
                     status_hash, prev_hash, entry_hash),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("refresh history insert did not return a sequence")
                seq = int(cursor.lastrowid)
                self._append_source_rows(seq, status)
                history_row = (
                    seq,
                    resolved_run_id,
                    ran_at_ms,
                    int(recorded_at_ms),
                    status_document,
                    status_hash,
                    prev_hash,
                    entry_hash,
                )
                message = self._expected_message_for_history_row(history_row)
                digest = _outbox_hash(
                    history_seq=seq,
                    message=message,
                    prev_record_hash=outbox_head,
                )
                self._connection.execute(
                    "INSERT INTO refresh_event_outbox"
                    " (history_seq,event_id,event_json,correlation_id,causation_id,"
                    " event_repro_hash,prev_record_hash,record_hash)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        seq,
                        message.event.event_id,
                        canonical_json(message.event.model_dump(mode="json")),
                        message.correlation_id,
                        message.causation_id,
                        message.event.reproducibility_hash,
                        outbox_head,
                        digest,
                    ),
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        return RefreshHistoryEntry(
            seq=seq,
            run_id=resolved_run_id,
            ran_at_ms=ran_at_ms,
            recorded_at_ms=int(recorded_at_ms),
            status_hash=status_hash,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )

    def _append_source_rows(self, history_seq: int, status: Mapping) -> None:
        for row in self._normalized_source_rows(status):
            self._connection.execute(
                "INSERT INTO refresh_source_history"
                " (history_seq, source, outcome, duration_ms, failed_count, watermark_after,"
                " freshest_days, n_stale, n_total, budget_scope)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (history_seq, *row),
            )

    @staticmethod
    def _normalized_source_rows(status: Mapping) -> list[tuple]:
        staleness = status.get("staleness_after", {})
        seen = set()
        rows = []
        for raw_report in status.get("reports", ()):
            report = dict(raw_report)
            source = str(report.get("source", ""))
            if not source or source in seen:
                raise ValueError("refresh reports require unique non-empty source names")
            seen.add(source)
            stale = staleness.get(source, {}) if isinstance(staleness, Mapping) else {}
            failed_tickers = {
                str(ticker) for ticker in report.get("failed_tickers", ())
            } | {
                str(ticker) for ticker in report.get("quality_failed", ())
            }
            watermark = report.get("watermark_after")
            rows.append(
                (
                    source,
                    source_outcome(report),
                    max(0, int(report.get("duration_ms", 0))),
                    len(failed_tickers),
                    int(watermark) if watermark is not None else None,
                    int(stale["freshest_days"]) if stale.get("freshest_days") is not None else None,
                    int(stale["n_stale"]) if stale.get("n_stale") is not None else None,
                    int(stale.get("n_total", 0)),
                    str(report["budget_scope"]) if report.get("budget_scope") else None,
                )
            )
        return rows

    def count(self) -> int:
        with self._lock:
            return int(self._connection.execute(
                "SELECT COUNT(*) FROM refresh_history"
            ).fetchone()[0])

    def pending_event_outbox(self, *, limit: int = 1_000) -> tuple[dict, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            _, messages, _ = self._verify_event_outbox_unlocked()
            dispatched = {row[1] for row in self._dispatch_rows()}
            pending = []
            for event_id, message in messages.items():
                if event_id in dispatched:
                    continue
                pending.append({
                    "outbox_id": event_id,
                    "event": message.event,
                    "correlation_id": message.correlation_id,
                    "causation_id": message.causation_id,
                })
                if len(pending) == limit:
                    break
            return tuple(pending)

    def mark_event_dispatched(
        self,
        event_id: str,
        *,
        dispatched_at: int,
        event_repro_hash: str,
    ) -> bool:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id must be non-empty")
        if (
            isinstance(dispatched_at, bool)
            or not isinstance(dispatched_at, int)
            or dispatched_at < 0
        ):
            raise ValueError("dispatched_at must be a non-negative integer")
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                _, messages, dispatch_head = self._verify_event_outbox_unlocked()
                message = messages.get(event_id)
                if (
                    message is None
                    or message.event.reproducibility_hash != event_repro_hash
                ):
                    raise RefreshHistoryIntegrityError(
                        "refresh dispatch reference mismatch"
                    )
                existing = self._connection.execute(
                    "SELECT dispatch_seq,event_id,dispatched_at,event_repro_hash,"
                    " prev_record_hash,record_hash FROM refresh_event_dispatch"
                    " WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        int(existing[2]) == dispatched_at
                        and existing[3] == event_repro_hash
                    ):
                        self._connection.rollback()
                        return False
                    raise RefreshHistoryIntegrityError(
                        "refresh dispatch acknowledgement conflict"
                    )
                digest = _dispatch_hash(
                    event_id=event_id,
                    dispatched_at=dispatched_at,
                    event_repro_hash=event_repro_hash,
                    prev_record_hash=dispatch_head,
                )
                self._connection.execute(
                    "INSERT INTO refresh_event_dispatch"
                    " (event_id,dispatched_at,event_repro_hash,prev_record_hash,record_hash)"
                    " VALUES (?,?,?,?,?)",
                    (
                        event_id,
                        dispatched_at,
                        event_repro_hash,
                        dispatch_head,
                        digest,
                    ),
                )
                self._connection.commit()
                return True
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    def watermark_history(self, source: str, *, limit: int = 100) -> list[dict]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock:
            rows = self._connection.execute(
                "SELECT h.ran_at_ms, s.outcome, s.watermark_after, s.freshest_days,"
                " s.n_stale, s.n_total FROM refresh_source_history s"
                " JOIN refresh_history h ON h.seq=s.history_seq"
                " WHERE s.source=? ORDER BY h.seq DESC LIMIT ?",
                (source, int(limit)),
            ).fetchall()
        return [
            {
                "ran_at_ms": int(row[0]),
                "outcome": row[1],
                "watermark_after": int(row[2]) if row[2] is not None else None,
                "freshest_days": int(row[3]) if row[3] is not None else None,
                "n_stale": int(row[4]) if row[4] is not None else None,
                "n_total": int(row[5]),
            }
            for row in reversed(rows)
        ]

    def consecutive_failures(self, source: str, *,
                             minimum_instrument_coverage: float = 0.99) -> int:
        if not 0.0 <= minimum_instrument_coverage <= 1.0:
            raise ValueError("minimum_instrument_coverage must be in [0, 1]")
        with self._lock:
            rows = self._connection.execute(
                "SELECT outcome, failed_count, n_total FROM refresh_source_history WHERE source=?"
                " ORDER BY history_seq DESC",
                (source,),
            ).fetchall()
        count = 0
        for outcome, failed_count, n_total in rows:
            coverage = _instrument_coverage(outcome, failed_count, n_total)
            if coverage is not None and coverage >= minimum_instrument_coverage:
                break
            count += 1
        return count

    def evaluate_slo(
        self,
        *,
        expected_sources: Sequence[str],
        stale_limits: Mapping[str, int],
        evaluated_at_ms: int,
        window_runs: int = 30,
        minimum_runs: int = 30,
        minimum_success_rate: float = 0.99,
        minimum_instrument_coverage: float = 0.99,
        max_consecutive_failures: int = 3,
        max_global_duration_ms: int = 1_800_000,
    ) -> dict:
        if window_runs <= 0 or minimum_runs <= 0 or minimum_runs > window_runs:
            raise ValueError("SLO run-window bounds are invalid")
        if not 0.0 <= minimum_success_rate <= 1.0:
            raise ValueError("minimum_success_rate must be in [0, 1]")
        if not 0.0 <= minimum_instrument_coverage <= 1.0:
            raise ValueError("minimum_instrument_coverage must be in [0, 1]")
        if max_consecutive_failures <= 0 or max_global_duration_ms <= 0:
            raise ValueError("SLO thresholds must be positive")

        with self._lock:
            chain_valid = self._chain_valid_unlocked()
            latest_run = self._connection.execute(
                "SELECT seq, ran_at_ms, status_json FROM refresh_history"
                " ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            source_rows: dict[str, list[tuple]] = {}
            for source in expected_sources:
                source_rows[source] = self._connection.execute(
                    "SELECT outcome, duration_ms, failed_count, watermark_after,"
                    " freshest_days, n_stale, n_total, budget_scope"
                    " FROM refresh_source_history WHERE source=?"
                    " ORDER BY history_seq DESC LIMIT ?",
                    (source, int(window_runs)),
                ).fetchall()

        violations: list[dict[str, object]] = []
        if not chain_valid:
            violations.append({"code": "HISTORY_CHAIN_INVALID", "source": None})
        stats: dict[str, dict[str, object]] = {}
        enough_history = True
        for source in expected_sources:
            rows = source_rows[source]
            sample_count = len(rows)
            enough_history = enough_history and sample_count >= minimum_runs
            coverages = [
                _instrument_coverage(row[0], int(row[2]), int(row[6]))
                for row in rows
            ]
            meets_slo = [
                coverage is not None and coverage >= minimum_instrument_coverage
                for coverage in coverages
            ]
            pass_count = sum(meets_slo)
            success_rate = pass_count / sample_count if sample_count else None
            consecutive = 0
            for passed in meets_slo:
                if passed:
                    break
                consecutive += 1
            total_instruments = sum(max(0, int(row[6])) for row in rows)
            failed_instruments = sum(
                min(max(0, int(row[2])), max(0, int(row[6]))) for row in rows
            )
            instrument_coverage = (
                (total_instruments - failed_instruments) / total_instruments
                if total_instruments else None
            )
            latest = rows[0] if rows else None
            stats[source] = {
                "sample_count": sample_count,
                "success_rate": success_rate,
                "instrument_coverage": instrument_coverage,
                "consecutive_failures": consecutive,
                "latest_outcome": latest[0] if latest else None,
                "latest_meets_slo": meets_slo[0] if meets_slo else False,
                "latest_instrument_coverage": coverages[0] if coverages else None,
                "latest_duration_ms": int(latest[1]) if latest else None,
                "latest_failed_count": int(latest[2]) if latest else None,
                "latest_watermark_after": int(latest[3]) if latest and latest[3] is not None else None,
                "latest_freshest_days": int(latest[4]) if latest and latest[4] is not None else None,
                "latest_n_stale": int(latest[5]) if latest and latest[5] is not None else None,
                "latest_n_total": int(latest[6]) if latest else None,
            }
            if latest is None:
                violations.append({"code": "SOURCE_HISTORY_MISSING", "source": source})
                continue
            if latest[0] in {"BUDGET_EXCEEDED", "CRASHED"}:
                violations.append({
                    "code": "SOURCE_RUNTIME_FAILURE",
                    "source": source,
                    "observed": latest[0],
                    "budget_scope": latest[7],
                })
            if consecutive >= max_consecutive_failures:
                violations.append({
                    "code": "CONSECUTIVE_FAILURES",
                    "source": source,
                    "observed": consecutive,
                    "threshold": max_consecutive_failures,
                })
            if (sample_count >= minimum_runs and success_rate is not None
                    and success_rate < minimum_success_rate):
                violations.append({
                    "code": "SUCCESS_RATE_BELOW_SLO",
                    "source": source,
                    "observed": success_rate,
                    "threshold": minimum_success_rate,
                })
            stale_limit = stale_limits.get(source)
            if stale_limit is not None and (latest[4] is None or int(latest[4]) > stale_limit):
                violations.append({
                    "code": "SOURCE_STALE",
                    "source": source,
                    "observed": int(latest[4]) if latest[4] is not None else None,
                    "threshold": int(stale_limit),
                })

        latest_duration_ms: int | None = None
        latest_run_id: str | None = None
        if latest_run is not None:
            latest_status = json.loads(latest_run[2])
            latest_duration_ms = int(latest_status.get("duration_ms", 0))
            latest_run_id = str(
                latest_status.get("run_id") or f"refresh-seq-{latest_run[0]}"
            )
            if latest_duration_ms > max_global_duration_ms:
                violations.append({
                    "code": "GLOBAL_DURATION_EXCEEDED",
                    "source": None,
                    "observed": latest_duration_ms,
                    "threshold": max_global_duration_ms,
                })

        verdict = "BREACH" if violations else ("PASS" if enough_history else "INSUFFICIENT_HISTORY")
        return {
            "schema_version": SCHEMA_VERSION,
            "evaluated_at_ms": int(evaluated_at_ms),
            "verdict": verdict,
            "chain_valid": chain_valid,
            "latest_run_id": latest_run_id,
            "latest_duration_ms": latest_duration_ms,
            "window_runs": int(window_runs),
            "minimum_runs": int(minimum_runs),
            "minimum_success_rate": float(minimum_success_rate),
            "minimum_instrument_coverage": float(minimum_instrument_coverage),
            "max_consecutive_failures": int(max_consecutive_failures),
            "max_global_duration_ms": int(max_global_duration_ms),
            "sources": stats,
            "violations": violations,
        }
