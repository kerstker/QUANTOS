"""Append-only SQLite Hypothesis Store (727 §8.1) — the pre-registration ledger.

A hypothesis is an immutable pre-registered CLAIM: it must exist BEFORE the
trials that test it, and it must never be edited afterwards (a changed
hypothesis is a NEW hypothesis_id by construction — the id is content-derived).
The store therefore has no update path at all: no-update / no-delete triggers
make the table append-only, and a record-level hash chain (each record binds the
previous record's hash) makes any retroactive tamper detectable.

Open semantics mirror the other ledger stores (maintenance_store /
experiment_registry): `create=False` against a missing path is an error, WAL +
busy_timeout on create, and the schema + append-only guards are verified on
open. Deterministic core: `registered_at_ms` is caller-supplied, no wall clock.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading

from quantos.contracts.hashing import stable_hash

from .hypothesis import Hypothesis, hypothesis_content_hash


class HypothesisStoreError(RuntimeError):
    """The hypothesis store is missing, conflicting, or unverifiable."""


class HypothesisConflictError(HypothesisStoreError):
    """Same hypothesis_id with DIFFERENT content. With content-derived ids this
    should be impossible — if it ever fires it is a hash collision or a
    hand-forged id, and it must be loud, never silently resolved."""


class HypothesisNotFoundError(HypothesisStoreError):
    """No hypothesis is registered under the requested id."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses (
  record_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  hypothesis_id TEXT UNIQUE NOT NULL,
  payload_json TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  registered_at_ms INTEGER NOT NULL,
  prev_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS hypotheses_no_update
BEFORE UPDATE ON hypotheses
BEGIN SELECT RAISE(ABORT, 'hypotheses is append-only'); END;
CREATE TRIGGER IF NOT EXISTS hypotheses_no_delete
BEFORE DELETE ON hypotheses
BEGIN SELECT RAISE(ABORT, 'hypotheses is append-only'); END;
"""


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _record_hash(
    *,
    hypothesis_id: str,
    payload_json: str,
    content_hash: str,
    registered_at_ms: int,
    prev_hash: str,
) -> str:
    return stable_hash(
        {
            "content_hash": content_hash,
            "hypothesis_id": hypothesis_id,
            "payload": json.loads(payload_json),
            "prev_hash": prev_hash,
            "registered_at_ms": registered_at_ms,
        }
    )


def _decode(payload_json: str) -> Hypothesis:
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError) as exc:
        raise HypothesisStoreError("hypothesis payload is corrupt") from exc
    if not isinstance(payload, dict):
        raise HypothesisStoreError("hypothesis payload is not an object")
    try:
        return Hypothesis(**payload)
    except (TypeError, ValueError) as exc:
        raise HypothesisStoreError(f"hypothesis payload is invalid: {exc}") from exc


class SqliteHypothesisStore:
    """Append-only durable store for Hypothesis pre-registrations."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise HypothesisStoreError(f"hypothesis store unavailable: {path}")
        if create and path != ":memory:":
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.Lock()
        with self._lock:
            if create:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
            self._verify_locked()

    def _verify_locked(self) -> None:
        """Fail-closed open: the table and both append-only triggers must exist."""
        try:
            tables = {
                str(row[0])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            triggers = {
                str(row[0])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
        except sqlite3.DatabaseError as exc:
            raise HypothesisStoreError(
                f"hypothesis store unreadable: {exc}"
            ) from exc
        if "hypotheses" not in tables:
            raise HypothesisStoreError("hypotheses table is missing")
        for trigger in ("hypotheses_no_update", "hypotheses_no_delete"):
            if trigger not in triggers:
                raise HypothesisStoreError(
                    f"append-only guard is missing: {trigger}"
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- writes ------------------------------------------------------------

    def register(self, hypothesis: Hypothesis, *, registered_at_ms: int) -> Hypothesis:
        """Append one immutable pre-registration; idempotent on identical content.

        Same hypothesis_id + identical content_hash -> return the EXISTING record
        (a re-run of the same driver is a no-op). Same id + different content ->
        HypothesisConflictError (loud: with content-derived ids this means a hash
        collision or a forged id, never something to paper over).
        """
        if not isinstance(hypothesis, Hypothesis):
            raise HypothesisStoreError("register expects a Hypothesis")
        if (
            isinstance(registered_at_ms, bool)
            or not isinstance(registered_at_ms, int)
            or registered_at_ms < 0
        ):
            raise HypothesisStoreError(
                "registered_at_ms must be a non-negative integer"
            )
        content_hash = hypothesis_content_hash(hypothesis)
        payload_json = _canonical(hypothesis.canonical_payload())
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                existing = self._conn.execute(
                    "SELECT content_hash,payload_json FROM hypotheses"
                    " WHERE hypothesis_id = ?",
                    (hypothesis.hypothesis_id,),
                ).fetchone()
                if existing is not None:
                    self._conn.commit()
                    if str(existing["content_hash"]) != content_hash:
                        raise HypothesisConflictError(
                            "hypothesis_id is already bound to different content: "
                            f"{hypothesis.hypothesis_id}"
                        )
                    return _decode(str(existing["payload_json"]))
                head = self._conn.execute(
                    "SELECT record_hash FROM hypotheses"
                    " ORDER BY record_seq DESC LIMIT 1"
                ).fetchone()
                prev = "" if head is None else str(head["record_hash"])
                record_hash = _record_hash(
                    hypothesis_id=hypothesis.hypothesis_id,
                    payload_json=payload_json,
                    content_hash=content_hash,
                    registered_at_ms=registered_at_ms,
                    prev_hash=prev,
                )
                self._conn.execute(
                    "INSERT INTO hypotheses("
                    " hypothesis_id,payload_json,content_hash,registered_at_ms,"
                    " prev_hash,record_hash) VALUES (?,?,?,?,?,?)",
                    (
                        hypothesis.hypothesis_id,
                        payload_json,
                        content_hash,
                        registered_at_ms,
                        prev,
                        record_hash,
                    ),
                )
                self._conn.commit()
                return hypothesis
            except HypothesisStoreError:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
            except sqlite3.DatabaseError as exc:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise HypothesisStoreError(
                    f"hypothesis could not be registered: {exc}"
                ) from exc

    # --- reads -------------------------------------------------------------

    def has(self, hypothesis_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM hypotheses WHERE hypothesis_id = ?",
                (hypothesis_id,),
            ).fetchone()
            return row is not None

    def get(self, hypothesis_id: str) -> Hypothesis:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM hypotheses WHERE hypothesis_id = ?",
                (hypothesis_id,),
            ).fetchone()
            if row is None:
                raise HypothesisNotFoundError(
                    f"hypothesis is not registered: {hypothesis_id}"
                )
            return _decode(str(row["payload_json"]))

    def all(self) -> list[Hypothesis]:
        """Every registered hypothesis in registration order (deterministic)."""
        with self._lock:
            return [
                _decode(str(row["payload_json"]))
                for row in self._conn.execute(
                    "SELECT payload_json FROM hypotheses ORDER BY record_seq"
                )
            ]

    def count(self) -> int:
        with self._lock:
            return int(
                self._conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
            )

    def chain_valid(self) -> bool:
        """Recompute the whole record chain. Fail-closed: any corruption,
        reordering, payload drift, or content-hash drift returns False."""
        try:
            with self._lock:
                previous = ""
                for row in self._conn.execute(
                    "SELECT hypothesis_id,payload_json,content_hash,"
                    " registered_at_ms,prev_hash,record_hash"
                    " FROM hypotheses ORDER BY record_seq"
                ):
                    payload_json = str(row["payload_json"])
                    hypothesis = _decode(payload_json)
                    if hypothesis.hypothesis_id != str(row["hypothesis_id"]):
                        return False
                    if hypothesis_content_hash(hypothesis) != str(row["content_hash"]):
                        return False
                    if str(row["prev_hash"]) != previous:
                        return False
                    expected = _record_hash(
                        hypothesis_id=str(row["hypothesis_id"]),
                        payload_json=payload_json,
                        content_hash=str(row["content_hash"]),
                        registered_at_ms=int(row["registered_at_ms"]),
                        prev_hash=previous,
                    )
                    if str(row["record_hash"]) != expected:
                        return False
                    previous = expected
                return True
        except Exception:
            return False


__all__ = [
    "HypothesisConflictError",
    "HypothesisNotFoundError",
    "HypothesisStoreError",
    "SqliteHypothesisStore",
]
