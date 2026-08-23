"""Append-only SQLite evidence store for automatic Alpha-family discovery.

The registry is intentionally separate from ``AlphaCatalog``.  The catalog is
the multiple-testing/lifecycle authority; this store records family coverage,
evaluation evidence and paper shadow runs, including blocked families.  No
record here can authorize live trading or create an APPROVED alpha.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Optional

from quantos.contracts.hashing import stable_hash


class AlphaFamilyRegistryError(RuntimeError):
    """Raised when the family evidence store is missing, corrupt or conflicted."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS alpha_family_events (
    event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('CANDIDATE_REGISTERED','EVALUATED','PAPER_RUN')),
    candidate_id TEXT NOT NULL,
    recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0),
    payload_json TEXT NOT NULL,
    prev_record_hash TEXT NOT NULL,
    record_hash TEXT UNIQUE NOT NULL
);
CREATE INDEX IF NOT EXISTS alpha_family_events_by_candidate
ON alpha_family_events(candidate_id, event_seq);
CREATE TRIGGER IF NOT EXISTS alpha_family_events_no_update
BEFORE UPDATE ON alpha_family_events
BEGIN SELECT RAISE(ABORT, 'alpha_family_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS alpha_family_events_no_delete
BEFORE DELETE ON alpha_family_events
BEGIN SELECT RAISE(ABORT, 'alpha_family_events is append-only'); END;
"""


def _payload_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _record_hash(*, event_id: str, event_type: str, candidate_id: str, recorded_at: int,
                 payload_json: str, prev_record_hash: str) -> str:
    return stable_hash({
        "candidate_id": candidate_id,
        "event_id": event_id,
        "event_type": event_type,
        "payload_json": payload_json,
        "prev_record_hash": prev_record_hash,
        "recorded_at": int(recorded_at),
    })


class AlphaFamilyRegistry:
    """Small fail-closed append-only store with an auditable hash chain."""

    def __init__(
        self,
        path: str,
        *,
        create: bool = False,
        read_only: bool = False,
    ) -> None:
        if not isinstance(path, str) or not path:
            raise AlphaFamilyRegistryError("family registry path must be non-empty")
        if create and read_only:
            raise AlphaFamilyRegistryError("create and read_only are mutually exclusive")
        if read_only and path == ":memory:":
            raise AlphaFamilyRegistryError("read-only registry requires a file authority")
        if read_only and not os.path.isfile(path):
            raise AlphaFamilyRegistryError("family registry does not exist")
        self.path = path
        connection = None
        try:
            target = (
                Path(path).resolve().as_uri() + "?mode=ro"
                if read_only
                else path
            )
            connection = sqlite3.connect(target, uri=read_only)
            connection.row_factory = sqlite3.Row
            self._conn = connection
            if create:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
            if read_only:
                self._conn.execute("PRAGMA query_only=ON")
            self._require_schema()
        except AlphaFamilyRegistryError:
            if connection is not None:
                connection.close()
            raise
        except (OSError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
            if connection is not None:
                connection.close()
            raise AlphaFamilyRegistryError("family registry unavailable") from exc

    def _require_schema(self) -> None:
        try:
            tables = {
                row["name"]
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        except sqlite3.DatabaseError as exc:
            raise AlphaFamilyRegistryError(f"family registry unreadable: {exc}") from exc
        if "alpha_family_events" not in tables:
            raise AlphaFamilyRegistryError("family registry missing alpha_family_events")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AlphaFamilyRegistry":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _head(self) -> str:
        row = self._conn.execute(
            "SELECT record_hash FROM alpha_family_events ORDER BY event_seq DESC LIMIT 1"
        ).fetchone()
        return str(row["record_hash"]) if row is not None else ""

    def _events(self, *, event_type: Optional[str] = None, candidate_id: Optional[str] = None) -> list[dict]:
        clauses, args = [], []
        if event_type is not None:
            clauses.append("event_type=?"); args.append(event_type)
        if candidate_id is not None:
            clauses.append("candidate_id=?"); args.append(candidate_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            "SELECT event_seq,event_id,event_type,candidate_id,recorded_at,payload_json,"
            f"prev_record_hash,record_hash FROM alpha_family_events{where} ORDER BY event_seq",
            args,
        ).fetchall()
        out = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError) as exc:
                raise AlphaFamilyRegistryError("family registry payload is not valid JSON") from exc
            if not isinstance(payload, dict):
                raise AlphaFamilyRegistryError("family registry payload must be an object")
            item = dict(row)
            item["payload"] = payload
            del item["payload_json"]
            out.append(item)
        return out

    def _append(self, *, event_id: str, event_type: str, candidate_id: str,
                recorded_at: int, payload: Mapping[str, Any]) -> bool:
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id must be non-empty")
        if event_type not in {"CANDIDATE_REGISTERED", "EVALUATED", "PAPER_RUN"}:
            raise ValueError(f"unknown event_type: {event_type}")
        if isinstance(recorded_at, bool) or int(recorded_at) < 0:
            raise ValueError("recorded_at must be a non-negative integer")
        body = _payload_json(payload)
        try:
            # The chain head and insert are one writer-serialized operation.
            # A deferred transaction permits two processes to read the same
            # head and append different event ids, creating a valid-row fork.
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT event_type,candidate_id,recorded_at,payload_json,record_hash "
                "FROM alpha_family_events WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["event_type"],
                    existing["candidate_id"],
                    existing["payload_json"],
                ) == (event_type, candidate_id, body):
                    self._conn.rollback()
                    return False
                raise AlphaFamilyRegistryError(
                    f"event id content conflict: {event_id}"
                )
            prev = self._head()
            digest = _record_hash(
                event_id=event_id,
                event_type=event_type,
                candidate_id=candidate_id,
                recorded_at=int(recorded_at),
                payload_json=body,
                prev_record_hash=prev,
            )
            self._conn.execute(
                "INSERT INTO alpha_family_events"
                "(event_id,event_type,candidate_id,recorded_at,payload_json,prev_record_hash,record_hash)"
                " VALUES (?,?,?,?,?,?,?)",
                (event_id, event_type, candidate_id, int(recorded_at), body, prev, digest),
            )
            self._conn.commit()
        except AlphaFamilyRegistryError:
            self._conn.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            self._conn.rollback()
            raise AlphaFamilyRegistryError(f"family registry append conflict: {event_id}") from exc
        return True

    def register_candidate(self, candidate: Mapping[str, Any], *, recorded_at: int) -> bool:
        """Append a candidate definition; same research identity is idempotent.

        Human-readable metadata (display name, complementarity note, notes and
        the derived config hash) may be corrected without rewriting the
        append-only evidence row.  The immutable research identity remains the
        candidate id, family, expression, required fields, frequency and paper
        eligibility.  Any other change is still a hard conflict.
        """
        body = dict(candidate)
        candidate_id = str(body.get("candidate_id", ""))
        if not candidate_id:
            raise ValueError("candidate_id is required")
        body["candidate_id"] = candidate_id
        event_id = f"candidate:{candidate_id}"
        existing = self._events(event_type="CANDIDATE_REGISTERED", candidate_id=candidate_id)
        if existing:
            presentation_keys = {"name_zh", "complementarity", "notes", "config_hash"}
            old_identity = {k: v for k, v in existing[-1]["payload"].items() if k not in presentation_keys}
            new_identity = {k: v for k, v in body.items() if k not in presentation_keys}
            if old_identity != new_identity:
                raise AlphaFamilyRegistryError(f"candidate definition conflict: {candidate_id}")
            return False
        return self._append(event_id=event_id, event_type="CANDIDATE_REGISTERED",
                            candidate_id=candidate_id, recorded_at=recorded_at, payload=body)

    def record_evaluation(self, candidate_id: str, evaluation: Mapping[str, Any], *,
                          dataset_version: str, recorded_at: int) -> bool:
        if not self._events(event_type="CANDIDATE_REGISTERED", candidate_id=candidate_id):
            raise AlphaFamilyRegistryError(f"cannot evaluate unregistered candidate: {candidate_id}")
        payload = {"candidate_id": candidate_id, "dataset_version": str(dataset_version),
                   "evaluation": dict(evaluation)}
        # Every scan is a new immutable observation.  Include the caller's
        # timestamp in the event identity so changed metrics (or paper
        # admission state) cannot collide with a prior evaluation artifact.
        eid = f"evaluation:{candidate_id}:{stable_hash(payload)[:20]}:{int(recorded_at)}"
        return self._append(event_id=eid, event_type="EVALUATED", candidate_id=candidate_id,
                            recorded_at=recorded_at, payload=payload)

    def record_paper_run(self, candidate_id: str, *, run_id: str, status: str,
                         metrics: Mapping[str, Any], dataset_version: str,
                         recorded_at: int) -> bool:
        if not self._events(event_type="CANDIDATE_REGISTERED", candidate_id=candidate_id):
            raise AlphaFamilyRegistryError(f"cannot record paper run for unregistered candidate: {candidate_id}")
        payload = {"candidate_id": candidate_id, "run_id": str(run_id), "status": str(status),
                   "dataset_version": str(dataset_version), "metrics": dict(metrics)}
        eid = f"paper:{candidate_id}:{run_id}:{stable_hash(payload)[:16]}"
        return self._append(event_id=eid, event_type="PAPER_RUN", candidate_id=candidate_id,
                            recorded_at=recorded_at, payload=payload)

    def candidates(self) -> list[dict]:
        out = []
        for body in self.candidate_definitions():
            evaluations = self.evaluations(body["candidate_id"])
            papers = self.paper_runs(body["candidate_id"])
            body["evaluations"] = evaluations
            body["paper_runs"] = papers
            out.append(body)
        return out

    def candidate_definitions(self) -> list[dict]:
        """Return immutable registrations without attaching growing histories."""
        rows = self._events(event_type="CANDIDATE_REGISTERED")
        out = []
        for row in rows:
            body = dict(row["payload"])
            body.update(
                {
                    "event_seq": row["event_seq"],
                    "recorded_at": row["recorded_at"],
                    "record_hash": row["record_hash"],
                }
            )
            out.append(body)
        return out

    def evaluations(self, candidate_id: str) -> list[dict]:
        return [row["payload"] | {"event_seq": row["event_seq"],
                                  "recorded_at": row["recorded_at"],
                                  "record_hash": row["record_hash"]}
                for row in self._events(event_type="EVALUATED", candidate_id=candidate_id)]

    def paper_runs(self, candidate_id: str) -> list[dict]:
        return [row["payload"] | {"event_seq": row["event_seq"], "record_hash": row["record_hash"]}
                for row in self._events(event_type="PAPER_RUN", candidate_id=candidate_id)]

    def verify_chain(self) -> bool:
        try:
            self.authority_hash()
            return True
        except AlphaFamilyRegistryError:
            return False

    def authority_hash(self) -> str:
        """Return the verified append-only family authority frontier."""
        try:
            rows = self._conn.execute(
                "SELECT event_seq,event_id,event_type,candidate_id,recorded_at,payload_json,"
                "prev_record_hash,record_hash FROM alpha_family_events ORDER BY event_seq"
            ).fetchall()
            prev = ""
            event_count = 0
            for row in rows:
                if row["prev_record_hash"] != prev:
                    raise AlphaFamilyRegistryError(
                        "family registry authority is invalid"
                    )
                expected = _record_hash(event_id=row["event_id"], event_type=row["event_type"],
                                        candidate_id=row["candidate_id"], recorded_at=int(row["recorded_at"]),
                                        payload_json=row["payload_json"], prev_record_hash=prev)
                if expected != row["record_hash"]:
                    raise AlphaFamilyRegistryError(
                        "family registry authority is invalid"
                    )
                prev = row["record_hash"]
                event_count += 1
            return stable_hash(
                {
                    "schema_version": 1,
                    "event_count": event_count,
                    "chain_head": prev,
                }
            )
        except AlphaFamilyRegistryError:
            raise
        except (sqlite3.DatabaseError, KeyError, TypeError, ValueError) as exc:
            raise AlphaFamilyRegistryError(
                "family registry authority is invalid"
            ) from exc

    def discovery_snapshot(
        self,
    ) -> tuple[str, list[dict], dict[str, list[dict]]]:
        """Freeze candidate identities and feedback under one read snapshot."""
        try:
            self._conn.execute("BEGIN")
            authority = self.authority_hash()
            definitions = self.candidate_definitions()
            paper_runs = {
                str(row["candidate_id"]): self.paper_runs(
                    str(row["candidate_id"])
                )
                for row in definitions
            }
            self._conn.commit()
            return authority, definitions, paper_runs
        except AlphaFamilyRegistryError:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise AlphaFamilyRegistryError(
                "family registry discovery snapshot is unavailable"
            ) from exc
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def workflow_evidence_snapshot(
        self,
    ) -> tuple[str, list[dict], dict[str, list[dict]]]:
        """Freeze identities and durable evaluation histories in one read view."""
        try:
            self._conn.execute("BEGIN")
            authority = self.authority_hash()
            definitions = self.candidate_definitions()
            evaluations = {
                str(row["candidate_id"]): self.evaluations(
                    str(row["candidate_id"])
                )
                for row in definitions
            }
            self._conn.commit()
            return authority, definitions, evaluations
        except AlphaFamilyRegistryError:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise AlphaFamilyRegistryError(
                "family registry workflow snapshot is unavailable"
            ) from exc
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def event_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM alpha_family_events").fetchone()["n"])


__all__ = ["AlphaFamilyRegistry", "AlphaFamilyRegistryError"]
