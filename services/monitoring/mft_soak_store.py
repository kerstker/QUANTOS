"""Append-only, hash-chained authority for Task 39 soak receipts."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from quantos.contracts.hashing import stable_hash
from services.monitoring.mft_soak import (
    CYCLE_KIND,
    START_KIND,
    MftSoakError,
    MftSoakPolicy,
    evaluate_cycle,
    validate_start_receipt,
)


class MftSoakStoreError(RuntimeError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS mft_soak_events (
  seq             INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id        TEXT NOT NULL UNIQUE,
  event_kind      TEXT NOT NULL,
  run_id          TEXT NOT NULL,
  cycle_index     INTEGER,
  policy_hash     TEXT NOT NULL,
  payload_json    TEXT NOT NULL,
  payload_hash    TEXT NOT NULL,
  recorded_at_ms  INTEGER NOT NULL CHECK(recorded_at_ms >= 0),
  prev_hash       TEXT NOT NULL,
  event_hash      TEXT NOT NULL UNIQUE,
  UNIQUE(run_id, event_kind, cycle_index)
);
CREATE INDEX IF NOT EXISTS idx_mft_soak_events_run
ON mft_soak_events(run_id, seq);
CREATE TRIGGER IF NOT EXISTS mft_soak_events_no_update
BEFORE UPDATE ON mft_soak_events
BEGIN SELECT RAISE(ABORT, 'mft_soak_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS mft_soak_events_no_delete
BEFORE DELETE ON mft_soak_events
BEGIN SELECT RAISE(ABORT, 'mft_soak_events is append-only'); END;
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
        raise MftSoakStoreError("soak receipt is not canonical JSON") from exc


class MftSoakStore:
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
                "SELECT name FROM sqlite_master WHERE type='table' AND name='mft_soak_events'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise MftSoakStoreError("soak store is unreadable") from exc
        if row is None:
            raise MftSoakStoreError("soak store schema is missing")

    @staticmethod
    def _payload(row: sqlite3.Row) -> dict[str, Any]:
        try:
            value = json.loads(row["payload_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MftSoakStoreError("stored soak receipt JSON is invalid") from exc
        if not isinstance(value, dict) or stable_hash(value) != row["payload_hash"]:
            raise MftSoakStoreError("stored soak receipt hash mismatch")
        return value

    def _append(
        self,
        *,
        event_id: str,
        event_kind: str,
        run_id: str,
        cycle_index: int | None,
        policy_hash: str,
        payload: Mapping[str, Any],
        recorded_at_ms: int,
    ) -> bool:
        if isinstance(recorded_at_ms, bool) or not isinstance(recorded_at_ms, int) or recorded_at_ms < 0:
            raise ValueError("recorded_at_ms must be a non-negative integer")
        payload_body = dict(payload)
        payload_json = _canonical(payload_body)
        payload_hash = stable_hash(payload_body)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT payload_hash FROM mft_soak_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] == payload_hash:
                    self._conn.rollback()
                    return False
                raise MftSoakStoreError("soak event identity reuse with different content")
            if cycle_index is not None:
                collision = self._conn.execute(
                    "SELECT event_id FROM mft_soak_events "
                    "WHERE run_id=? AND event_kind=? AND cycle_index=?",
                    (run_id, event_kind, cycle_index),
                ).fetchone()
                if collision is not None:
                    raise MftSoakStoreError("cycle index reuse with different identity")
            prior = self._conn.execute(
                "SELECT event_hash FROM mft_soak_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = "" if prior is None else str(prior["event_hash"])
            event_hash = stable_hash(
                {
                    "event_id": event_id,
                    "event_kind": event_kind,
                    "run_id": run_id,
                    "cycle_index": cycle_index,
                    "policy_hash": policy_hash,
                    "payload_hash": payload_hash,
                    "recorded_at_ms": recorded_at_ms,
                    "prev_hash": prev_hash,
                }
            )
            self._conn.execute(
                "INSERT INTO mft_soak_events"
                "(event_id,event_kind,run_id,cycle_index,policy_hash,payload_json,"
                " payload_hash,recorded_at_ms,prev_hash,event_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    event_kind,
                    run_id,
                    cycle_index,
                    policy_hash,
                    payload_json,
                    payload_hash,
                    recorded_at_ms,
                    prev_hash,
                    event_hash,
                ),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError as exc:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise MftSoakStoreError("soak event identity conflict") from exc
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def append_start(
        self,
        receipt: Mapping[str, Any],
        *,
        policy: MftSoakPolicy,
        recorded_at_ms: int,
    ) -> bool:
        try:
            body = validate_start_receipt(receipt, policy)
        except MftSoakError as exc:
            raise MftSoakStoreError(str(exc)) from None
        if recorded_at_ms < body["started_at_ms"]:
            raise MftSoakStoreError("start receipt cannot be recorded before it started")
        return self._append(
            event_id=f"START:{body['run_id']}",
            event_kind=START_KIND,
            run_id=body["run_id"],
            cycle_index=None,
            policy_hash=policy.policy_hash,
            payload=body,
            recorded_at_ms=recorded_at_ms,
        )

    def append_cycle(
        self,
        receipt: Mapping[str, Any],
        *,
        policy: MftSoakPolicy,
        evidence_root: str | os.PathLike[str],
        recorded_at_ms: int,
    ) -> bool:
        if not isinstance(receipt, Mapping):
            raise MftSoakStoreError("cycle receipt must be an object")
        run_id = receipt.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise MftSoakStoreError("cycle run_id is invalid")
        start = self.start_receipt(run_id)
        if start is None:
            raise MftSoakStoreError("SOAK_START_RECEIPT_MISSING")
        try:
            evaluation = evaluate_cycle(receipt, policy, start)
        except MftSoakError as exc:
            raise MftSoakStoreError(str(exc)) from None
        self._verify_cycle_artifacts(receipt, evidence_root=evidence_root)
        if recorded_at_ms < evaluation.completed_at_ms:
            raise MftSoakStoreError("cycle cannot be recorded before completion")
        return self._append(
            event_id=f"CYCLE:{run_id}:{evaluation.cycle_id}",
            event_kind=CYCLE_KIND,
            run_id=run_id,
            cycle_index=evaluation.cycle_index,
            policy_hash=policy.policy_hash,
            payload=dict(receipt),
            recorded_at_ms=recorded_at_ms,
        )

    @staticmethod
    def _verify_cycle_artifacts(
        receipt: Mapping[str, Any],
        *,
        evidence_root: str | os.PathLike[str],
    ) -> None:
        root_input = Path(evidence_root)
        if root_input.is_symlink():
            raise MftSoakStoreError("cycle evidence root is unsafe")
        try:
            root = root_input.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise MftSoakStoreError("cycle evidence root is unavailable") from exc
        if not root.is_dir():
            raise MftSoakStoreError("cycle evidence root is unavailable")
        evidence = receipt.get("evidence")
        if not isinstance(evidence, Mapping):
            raise MftSoakStoreError("cycle evidence is invalid")
        for kind, item in evidence.items():
            if not isinstance(item, Mapping):
                raise MftSoakStoreError("cycle evidence entry is invalid")
            artifact = item.get("artifact")
            expected = item.get("artifact_hash")
            if not isinstance(artifact, str) or not isinstance(expected, str):
                raise MftSoakStoreError("cycle evidence identity is invalid")
            relative = PurePosixPath(artifact)
            if (
                "\\" in artifact
                or relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise MftSoakStoreError("cycle evidence artifact path is unsafe")
            unresolved = root.joinpath(*relative.parts)
            current = root
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise MftSoakStoreError("cycle evidence artifact path is unsafe")
            try:
                candidate = unresolved.resolve(strict=True)
                candidate.relative_to(root)
            except (OSError, RuntimeError, ValueError) as exc:
                raise MftSoakStoreError("cycle evidence artifact path is unsafe or missing") from exc
            if not candidate.is_file():
                raise MftSoakStoreError("cycle evidence artifact is not a regular file")
            digest = hashlib.sha256()
            try:
                with candidate.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError as exc:
                raise MftSoakStoreError("cycle evidence artifact is unreadable") from exc
            if digest.hexdigest() != expected:
                raise MftSoakStoreError(f"cycle evidence artifact digest mismatch: {kind}")

    def start_receipt(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM mft_soak_events WHERE run_id=? AND event_kind=? "
            "ORDER BY seq LIMIT 1",
            (str(run_id), START_KIND),
        ).fetchone()
        return None if row is None else self._payload(row)

    def cycles(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM mft_soak_events WHERE run_id=? AND event_kind=? "
            "ORDER BY cycle_index,seq",
            (str(run_id), CYCLE_KIND),
        )
        return [self._payload(row) for row in rows]

    def latest_run_id(self) -> str | None:
        row = self._conn.execute(
            "SELECT run_id FROM mft_soak_events WHERE event_kind=? ORDER BY seq DESC LIMIT 1",
            (START_KIND,),
        ).fetchone()
        return None if row is None else str(row["run_id"])

    def chain_valid(self) -> bool:
        previous = ""
        try:
            for row in self._conn.execute("SELECT * FROM mft_soak_events ORDER BY seq"):
                payload = self._payload(row)
                if row["prev_hash"] != previous:
                    return False
                expected = stable_hash(
                    {
                        "event_id": row["event_id"],
                        "event_kind": row["event_kind"],
                        "run_id": row["run_id"],
                        "cycle_index": row["cycle_index"],
                        "policy_hash": row["policy_hash"],
                        "payload_hash": stable_hash(payload),
                        "recorded_at_ms": int(row["recorded_at_ms"]),
                        "prev_hash": row["prev_hash"],
                    }
                )
                if row["event_hash"] != expected:
                    return False
                previous = expected
            return True
        except (MftSoakStoreError, sqlite3.DatabaseError, TypeError, ValueError):
            return False

    def close(self) -> None:
        self._conn.close()


__all__ = ["MftSoakStore", "MftSoakStoreError"]
