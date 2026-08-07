"""Durable, fenced checkpoints for resumable alpha-candidate batches.

The store is deliberately a narrow persistence boundary.  It records canonical
identifiers and commitments only; research objects and executable callables
never cross the SQLite boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Mapping


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_BATCH_STATES = {
    "QUEUED",
    "RUNNING",
    "VALIDATED",
    "BLOCKED",
    "SHADOW",
    "PAPER",
    "PAUSED",
    "RETIRED",
}
_CANDIDATE_STATES = {"QUEUED", "VALIDATED", "BLOCKED"}
_TRANSITIONS = {
    "QUEUED": {"RUNNING"},
    "RUNNING": {"VALIDATED", "BLOCKED"},
    "VALIDATED": {"SHADOW", "PAUSED", "RETIRED"},
    "BLOCKED": {"RETIRED"},
    "SHADOW": {"PAPER", "PAUSED", "RETIRED"},
    "PAPER": {"PAUSED", "RETIRED"},
    "PAUSED": {"RETIRED"},
    "RETIRED": set(),
}


class AlphaWorkflowError(RuntimeError):
    pass


class AlphaWorkflowConflict(AlphaWorkflowError):
    pass


class AlphaWorkflowBusy(AlphaWorkflowConflict):
    pass


class AlphaWorkflowFenced(AlphaWorkflowConflict):
    pass


class AlphaWorkflowStateError(AlphaWorkflowError):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowCandidate:
    ordinal: int
    candidate_id: str
    state: str
    trial_id: str | None
    artifact_commitment: str | None


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    batch_id: str
    state: str
    candidate_ids: tuple[str, ...]
    panel_digest: str
    registry_digest: str
    policy_digest: str
    budget_digest: str
    event_at: int
    generated_count: int
    checkpoint_watermark: int
    receipt_digest: str | None
    owner: str | None
    lease_until: int | None
    fencing_token: int
    version: int
    candidates: tuple[WorkflowCandidate, ...]


@dataclass(frozen=True, slots=True)
class WorkflowClaim:
    batch_id: str
    worker_id: str
    lease_until: int
    fencing_token: int
    checkpoint_watermark: int


@dataclass(frozen=True, slots=True)
class WorkflowEvent:
    batch_id: str
    event_index: int
    state: str
    at: int
    worker_id: str | None
    fencing_token: int
    detail: Mapping[str, Any]
    global_seq: int
    prev_hash: str
    event_hash: str


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_hex(name: str, value: str) -> None:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase sha256")


def _require_int(name: str, value: int) -> None:
    if type(value) is not int:
        raise ValueError(f"{name} must be an int")


class WorkflowStore:
    """Single-node SQLite authority for candidate-batch leases and checkpoints."""

    def __init__(self, path: str) -> None:
        if type(path) is not str or not path:
            raise ValueError("path is required")
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS alpha_workflow_batches (
                    batch_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    candidate_ids_json TEXT NOT NULL,
                    panel_digest TEXT NOT NULL,
                    registry_digest TEXT NOT NULL,
                    policy_digest TEXT NOT NULL,
                    budget_digest TEXT NOT NULL,
                    event_at INTEGER NOT NULL,
                    generated_count INTEGER NOT NULL,
                    checkpoint_watermark INTEGER NOT NULL,
                    receipt_digest TEXT,
                    owner TEXT,
                    lease_until INTEGER,
                    fencing_token INTEGER NOT NULL,
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alpha_workflow_candidates (
                    batch_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    candidate_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    trial_id TEXT,
                    artifact_commitment TEXT,
                    PRIMARY KEY (batch_id, ordinal),
                    UNIQUE (batch_id, candidate_id),
                    FOREIGN KEY (batch_id) REFERENCES alpha_workflow_batches(batch_id)
                );
                CREATE TABLE IF NOT EXISTS alpha_workflow_events (
                    global_seq INTEGER PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    event_index INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    at INTEGER NOT NULL,
                    worker_id TEXT,
                    fencing_token INTEGER NOT NULL,
                    detail_json TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    UNIQUE (batch_id, event_index),
                    FOREIGN KEY (batch_id) REFERENCES alpha_workflow_batches(batch_id)
                );
                CREATE TABLE IF NOT EXISTS alpha_workflow_event_head (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    event_count INTEGER NOT NULL,
                    head_hash TEXT NOT NULL
                );
                INSERT OR IGNORE INTO alpha_workflow_event_head(id, event_count, head_hash)
                VALUES (1, 0, '');
                CREATE TRIGGER IF NOT EXISTS alpha_workflow_events_no_update
                BEFORE UPDATE ON alpha_workflow_events BEGIN
                    SELECT RAISE(ABORT, 'alpha workflow events are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS alpha_workflow_events_no_delete
                BEFORE DELETE ON alpha_workflow_events BEGIN
                    SELECT RAISE(ABORT, 'alpha workflow events are append-only');
                END;
                """
            )

    def close(self) -> None:
        with self._lock:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
                self._connection = None

    def __enter__(self) -> WorkflowStore:
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.close()
        return False

    def _begin(self, *, write: bool) -> None:
        self._connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        self._verify_locked()

    def _rollback(self) -> None:
        try:
            self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def _event_payload(
        self,
        *,
        batch_id: str,
        event_index: int,
        state: str,
        at: int,
        worker_id: str | None,
        fencing_token: int,
        detail_json: str,
        global_seq: int,
        prev_hash: str,
    ) -> dict[str, Any]:
        return {
            "batch_id": batch_id,
            "event_index": event_index,
            "state": state,
            "at": at,
            "worker_id": worker_id,
            "fencing_token": fencing_token,
            "detail": json.loads(detail_json),
            "global_seq": global_seq,
            "prev_hash": prev_hash,
        }

    def _verify_locked(self) -> None:
        rows = self._connection.execute(
            """SELECT global_seq,batch_id,event_index,state,at,worker_id,
                      fencing_token,detail_json,prev_hash,event_hash
               FROM alpha_workflow_events ORDER BY global_seq"""
        ).fetchall()
        previous = ""
        batch_positions: dict[str, int] = {}
        batch_states: dict[str, str] = {}
        batch_events: dict[str, list[dict[str, Any]]] = {}
        for position, row in enumerate(rows, 1):
            (
                global_seq,
                batch_id,
                event_index,
                state,
                at,
                worker_id,
                fencing_token,
                detail_json,
                prev_hash,
                event_hash,
            ) = row
            expected_index = batch_positions.get(batch_id, -1) + 1
            if global_seq != position or event_index != expected_index or prev_hash != previous:
                raise AlphaWorkflowError("workflow event chain corruption")
            try:
                payload = self._event_payload(
                    batch_id=batch_id,
                    event_index=event_index,
                    state=state,
                    at=at,
                    worker_id=worker_id,
                    fencing_token=fencing_token,
                    detail_json=detail_json,
                    global_seq=global_seq,
                    prev_hash=prev_hash,
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise AlphaWorkflowError("workflow event payload corruption") from exc
            if event_hash != _hash(payload):
                raise AlphaWorkflowError("workflow event hash corruption")
            previous = event_hash
            batch_positions[batch_id] = event_index
            batch_states[batch_id] = state
            batch_events.setdefault(batch_id, []).append(payload)
        head = self._connection.execute(
            "SELECT event_count,head_hash FROM alpha_workflow_event_head WHERE id=1"
        ).fetchone()
        if head != (len(rows), previous):
            raise AlphaWorkflowError("workflow event head corruption")
        batch_rows = self._connection.execute(
            """SELECT batch_id,state,candidate_ids_json,panel_digest,registry_digest,
                      policy_digest,budget_digest,event_at,generated_count,
                      checkpoint_watermark,receipt_digest,owner,lease_until,
                      fencing_token
               FROM alpha_workflow_batches"""
        ).fetchall()
        for row in batch_rows:
            (
                batch_id,
                state,
                candidate_ids_json,
                panel_digest,
                registry_digest,
                policy_digest,
                budget_digest,
                event_at,
                generated_count,
                checkpoint_watermark,
                receipt_digest,
                owner,
                lease_until,
                fencing_token,
            ) = row
            if batch_states.get(batch_id) != state:
                raise AlphaWorkflowError("workflow state projection corruption")
            events = batch_events.get(batch_id, [])
            if not events:
                raise AlphaWorkflowError("workflow registration event missing")
            registration = events[0]
            expected_registration = {
                "kind": "REGISTERED",
                "candidate_ids": json.loads(candidate_ids_json),
                "panel_digest": panel_digest,
                "registry_digest": registry_digest,
                "policy_digest": policy_digest,
                "budget_digest": budget_digest,
                "event_at": event_at,
                "generated_count": generated_count,
            }
            if registration["at"] != event_at or registration["detail"] != expected_registration:
                raise AlphaWorkflowError("workflow registration projection corruption")
            candidate_rows = self._connection.execute(
                """SELECT ordinal,candidate_id,state,trial_id,artifact_commitment
                   FROM alpha_workflow_candidates WHERE batch_id=? ORDER BY ordinal""",
                (batch_id,),
            ).fetchall()
            checkpoints = [event for event in events if event["detail"].get("kind") == "CHECKPOINT"]
            if checkpoint_watermark != len(checkpoints):
                raise AlphaWorkflowError("workflow checkpoint history corruption")
            for ordinal, candidate_id, candidate_state, trial_id, artifact_commitment in candidate_rows:
                if ordinal < len(checkpoints):
                    expected_checkpoint = {
                        "kind": "CHECKPOINT",
                        "ordinal": ordinal,
                        "candidate_id": candidate_id,
                        "candidate_state": candidate_state,
                        "trial_id": trial_id,
                        "artifact_commitment": artifact_commitment,
                        "watermark": ordinal + 1,
                    }
                    if checkpoints[ordinal]["detail"] != expected_checkpoint:
                        raise AlphaWorkflowError("workflow candidate projection corruption")
                elif (candidate_state, trial_id, artifact_commitment) != ("QUEUED", None, None):
                    raise AlphaWorkflowError("workflow unfinished candidate projection corruption")
            completions = [event for event in events if event["detail"].get("kind") == "COMPLETED"]
            if receipt_digest is None:
                if completions:
                    raise AlphaWorkflowError("workflow completion projection corruption")
            elif len(completions) != 1 or completions[0]["detail"].get("receipt_digest") != receipt_digest:
                raise AlphaWorkflowError("workflow receipt projection corruption")
            claims = [event for event in events if event["detail"].get("kind") == "CLAIMED"]
            if state == "RUNNING":
                if not claims:
                    raise AlphaWorkflowError("workflow claim projection corruption")
                latest_claim = claims[-1]
                if (
                    latest_claim["worker_id"] != owner
                    or latest_claim["fencing_token"] != fencing_token
                    or latest_claim["detail"].get("lease_until") != lease_until
                ):
                    raise AlphaWorkflowError("workflow lease projection corruption")

    def _append_event_locked(
        self,
        batch_id: str,
        state: str,
        at: int,
        worker_id: str | None,
        fencing_token: int,
        detail: Mapping[str, Any],
    ) -> None:
        if state not in _BATCH_STATES:
            raise AlphaWorkflowStateError(f"unknown workflow state {state}")
        detail_json = _canonical(dict(detail))
        event_index = self._connection.execute(
            "SELECT count(*) FROM alpha_workflow_events WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
        global_seq, previous = self._connection.execute(
            "SELECT event_count,head_hash FROM alpha_workflow_event_head WHERE id=1"
        ).fetchone()
        global_seq += 1
        payload = self._event_payload(
            batch_id=batch_id,
            event_index=event_index,
            state=state,
            at=at,
            worker_id=worker_id,
            fencing_token=fencing_token,
            detail_json=detail_json,
            global_seq=global_seq,
            prev_hash=previous,
        )
        event_hash = _hash(payload)
        self._connection.execute(
            """INSERT INTO alpha_workflow_events
               (global_seq,batch_id,event_index,state,at,worker_id,fencing_token,
                detail_json,prev_hash,event_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                global_seq,
                batch_id,
                event_index,
                state,
                at,
                worker_id,
                fencing_token,
                detail_json,
                previous,
                event_hash,
            ),
        )
        self._connection.execute(
            "UPDATE alpha_workflow_event_head SET event_count=?,head_hash=? WHERE id=1",
            (global_seq, event_hash),
        )

    def _snapshot_locked(self, batch_id: str) -> WorkflowSnapshot:
        row = self._connection.execute(
            """SELECT batch_id,state,candidate_ids_json,panel_digest,registry_digest,
                      policy_digest,budget_digest,event_at,generated_count,
                      checkpoint_watermark,receipt_digest,owner,lease_until,
                      fencing_token,version
               FROM alpha_workflow_batches WHERE batch_id=?""",
            (batch_id,),
        ).fetchone()
        if row is None:
            raise AlphaWorkflowConflict("unknown workflow batch")
        candidate_rows = self._connection.execute(
            """SELECT ordinal,candidate_id,state,trial_id,artifact_commitment
               FROM alpha_workflow_candidates WHERE batch_id=? ORDER BY ordinal""",
            (batch_id,),
        ).fetchall()
        candidates = tuple(WorkflowCandidate(*candidate) for candidate in candidate_rows)
        candidate_ids = tuple(json.loads(row[2]))
        if candidate_ids != tuple(candidate.candidate_id for candidate in candidates):
            raise AlphaWorkflowError("workflow candidate projection corruption")
        if row[9] != sum(candidate.state != "QUEUED" for candidate in candidates):
            raise AlphaWorkflowError("workflow checkpoint projection corruption")
        return WorkflowSnapshot(
            batch_id=row[0],
            state=row[1],
            candidate_ids=candidate_ids,
            panel_digest=row[3],
            registry_digest=row[4],
            policy_digest=row[5],
            budget_digest=row[6],
            event_at=row[7],
            generated_count=row[8],
            checkpoint_watermark=row[9],
            receipt_digest=row[10],
            owner=row[11],
            lease_until=row[12],
            fencing_token=row[13],
            version=row[14],
            candidates=candidates,
        )

    def register_batch(
        self,
        batch_id: str,
        *,
        candidate_ids: tuple[str, ...] | list[str],
        panel_digest: str,
        registry_digest: str,
        policy_digest: str,
        budget_digest: str,
        event_at: int,
        generated_count: int,
    ) -> WorkflowSnapshot:
        for name, value in (
            ("batch_id", batch_id),
            ("panel_digest", panel_digest),
            ("registry_digest", registry_digest),
            ("policy_digest", policy_digest),
            ("budget_digest", budget_digest),
        ):
            _require_hex(name, value)
        _require_int("event_at", event_at)
        _require_int("generated_count", generated_count)
        candidate_tuple = tuple(candidate_ids)
        if len(set(candidate_tuple)) != len(candidate_tuple):
            raise ValueError("candidate_ids must be unique")
        for candidate_id in candidate_tuple:
            _require_hex("candidate_id", candidate_id)
        if generated_count < len(candidate_tuple):
            raise ValueError("generated_count cannot be below representative count")
        identity = (
            _canonical(candidate_tuple),
            panel_digest,
            registry_digest,
            policy_digest,
            budget_digest,
            event_at,
            generated_count,
        )
        with self._lock:
            try:
                self._begin(write=True)
                existing = self._connection.execute(
                    """SELECT candidate_ids_json,panel_digest,registry_digest,policy_digest,
                              budget_digest,event_at,generated_count
                       FROM alpha_workflow_batches WHERE batch_id=?""",
                    (batch_id,),
                ).fetchone()
                if existing is not None:
                    if existing != identity:
                        raise AlphaWorkflowConflict("batch identity conflicts with persisted workflow")
                    snapshot = self._snapshot_locked(batch_id)
                    self._connection.execute("COMMIT")
                    return snapshot
                self._connection.execute(
                    """INSERT INTO alpha_workflow_batches
                       VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,0,0)""",
                    (
                        batch_id,
                        "QUEUED",
                        *identity,
                        0,
                    ),
                )
                self._connection.executemany(
                    """INSERT INTO alpha_workflow_candidates
                       (batch_id,ordinal,candidate_id,state,trial_id,artifact_commitment)
                       VALUES (?,?,?,'QUEUED',NULL,NULL)""",
                    (
                        (batch_id, ordinal, candidate_id)
                        for ordinal, candidate_id in enumerate(candidate_tuple)
                    ),
                )
                self._append_event_locked(
                    batch_id,
                    "QUEUED",
                    event_at,
                    None,
                    0,
                    {
                        "kind": "REGISTERED",
                        "candidate_ids": list(candidate_tuple),
                        "panel_digest": panel_digest,
                        "registry_digest": registry_digest,
                        "policy_digest": policy_digest,
                        "budget_digest": budget_digest,
                        "event_at": event_at,
                        "generated_count": generated_count,
                    },
                )
                snapshot = self._snapshot_locked(batch_id)
                self._connection.execute("COMMIT")
                return snapshot
            except (AlphaWorkflowError, ValueError, TypeError):
                self._rollback()
                raise
            except sqlite3.Error as exc:
                self._rollback()
                raise AlphaWorkflowError("workflow database failure") from exc

    def snapshot(self, batch_id: str) -> WorkflowSnapshot:
        _require_hex("batch_id", batch_id)
        with self._lock:
            try:
                self._begin(write=False)
                snapshot = self._snapshot_locked(batch_id)
                self._connection.execute("COMMIT")
                return snapshot
            except AlphaWorkflowError:
                self._rollback()
                raise
            except (sqlite3.Error, ValueError, TypeError) as exc:
                self._rollback()
                raise AlphaWorkflowError("invalid workflow snapshot") from exc

    def claim(self, batch_id: str, worker_id: str, lease_until: int, *, now: int) -> WorkflowClaim:
        _require_hex("batch_id", batch_id)
        if type(worker_id) is not str or not worker_id:
            raise ValueError("worker_id is required")
        _require_int("lease_until", lease_until)
        _require_int("now", now)
        if lease_until <= now:
            raise ValueError("lease_until must be after now")
        with self._lock:
            try:
                self._begin(write=True)
                snapshot = self._snapshot_locked(batch_id)
                if snapshot.state not in {"QUEUED", "RUNNING"}:
                    raise AlphaWorkflowStateError(f"cannot claim batch in {snapshot.state}")
                if snapshot.state == "RUNNING" and snapshot.lease_until is not None and snapshot.lease_until > now:
                    if snapshot.owner != worker_id:
                        raise AlphaWorkflowBusy(f"batch is leased by {snapshot.owner}")
                    claim = WorkflowClaim(
                        batch_id,
                        worker_id,
                        snapshot.lease_until,
                        snapshot.fencing_token,
                        snapshot.checkpoint_watermark,
                    )
                    self._connection.execute("COMMIT")
                    return claim
                token = snapshot.fencing_token + 1
                self._connection.execute(
                    """UPDATE alpha_workflow_batches
                       SET state='RUNNING',owner=?,lease_until=?,fencing_token=?,version=version+1
                       WHERE batch_id=? AND version=?""",
                    (worker_id, lease_until, token, batch_id, snapshot.version),
                )
                if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise AlphaWorkflowConflict("workflow claim version conflict")
                self._append_event_locked(
                    batch_id,
                    "RUNNING",
                    now,
                    worker_id,
                    token,
                    {"kind": "CLAIMED", "lease_until": lease_until},
                )
                self._connection.execute("COMMIT")
                return WorkflowClaim(
                    batch_id,
                    worker_id,
                    lease_until,
                    token,
                    snapshot.checkpoint_watermark,
                )
            except AlphaWorkflowError:
                self._rollback()
                raise
            except sqlite3.Error as exc:
                self._rollback()
                raise AlphaWorkflowError("workflow claim failed") from exc

    def _require_claim_locked(
        self,
        snapshot: WorkflowSnapshot,
        worker_id: str,
        fencing_token: int,
        now: int,
    ) -> None:
        if snapshot.state != "RUNNING":
            raise AlphaWorkflowStateError(f"batch is {snapshot.state}, not RUNNING")
        if (
            snapshot.owner != worker_id
            or snapshot.fencing_token != fencing_token
            or snapshot.lease_until is None
            or snapshot.lease_until <= now
        ):
            raise AlphaWorkflowFenced("workflow lease is stale or not owned by caller")

    def checkpoint(
        self,
        batch_id: str,
        worker_id: str,
        fencing_token: int,
        candidate_id: str,
        state: str,
        *,
        trial_id: str,
        artifact_commitment: str,
        now: int,
    ) -> WorkflowSnapshot:
        for name, value in (
            ("batch_id", batch_id),
            ("candidate_id", candidate_id),
            ("trial_id", trial_id),
            ("artifact_commitment", artifact_commitment),
        ):
            _require_hex(name, value)
        if state not in {"VALIDATED", "BLOCKED"}:
            raise AlphaWorkflowStateError("candidate checkpoint must be VALIDATED or BLOCKED")
        _require_int("fencing_token", fencing_token)
        _require_int("now", now)
        with self._lock:
            try:
                self._begin(write=True)
                snapshot = self._snapshot_locked(batch_id)
                self._require_claim_locked(snapshot, worker_id, fencing_token, now)
                if snapshot.checkpoint_watermark >= len(snapshot.candidates):
                    raise AlphaWorkflowConflict("all candidates are already checkpointed")
                expected = snapshot.candidates[snapshot.checkpoint_watermark]
                if expected.candidate_id != candidate_id:
                    raise AlphaWorkflowConflict("checkpoint must match next candidate")
                self._connection.execute(
                    """UPDATE alpha_workflow_candidates
                       SET state=?,trial_id=?,artifact_commitment=?
                       WHERE batch_id=? AND ordinal=? AND state='QUEUED'""",
                    (state, trial_id, artifact_commitment, batch_id, expected.ordinal),
                )
                if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise AlphaWorkflowConflict("candidate checkpoint conflict")
                self._connection.execute(
                    """UPDATE alpha_workflow_batches
                       SET checkpoint_watermark=checkpoint_watermark+1,version=version+1
                       WHERE batch_id=? AND version=?""",
                    (batch_id, snapshot.version),
                )
                if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise AlphaWorkflowConflict("workflow checkpoint version conflict")
                self._append_event_locked(
                    batch_id,
                    "RUNNING",
                    now,
                    worker_id,
                    fencing_token,
                    {
                        "kind": "CHECKPOINT",
                        "ordinal": expected.ordinal,
                        "candidate_id": candidate_id,
                        "candidate_state": state,
                        "trial_id": trial_id,
                        "artifact_commitment": artifact_commitment,
                        "watermark": expected.ordinal + 1,
                    },
                )
                result = self._snapshot_locked(batch_id)
                self._connection.execute("COMMIT")
                return result
            except AlphaWorkflowError:
                self._rollback()
                raise
            except sqlite3.Error as exc:
                self._rollback()
                raise AlphaWorkflowError("workflow checkpoint failed") from exc

    def complete(
        self,
        batch_id: str,
        worker_id: str,
        fencing_token: int,
        state: str,
        receipt_digest: str,
        *,
        now: int,
    ) -> WorkflowSnapshot:
        _require_hex("batch_id", batch_id)
        _require_hex("receipt_digest", receipt_digest)
        if state not in {"VALIDATED", "BLOCKED"}:
            raise AlphaWorkflowStateError("workflow completion must be VALIDATED or BLOCKED")
        _require_int("fencing_token", fencing_token)
        _require_int("now", now)
        with self._lock:
            try:
                self._begin(write=True)
                snapshot = self._snapshot_locked(batch_id)
                self._require_claim_locked(snapshot, worker_id, fencing_token, now)
                if snapshot.checkpoint_watermark != len(snapshot.candidates):
                    raise AlphaWorkflowConflict("cannot complete before every candidate checkpoint")
                expected = (
                    "VALIDATED"
                    if any(candidate.state == "VALIDATED" for candidate in snapshot.candidates)
                    else "BLOCKED"
                )
                if state != expected:
                    raise AlphaWorkflowStateError(f"completion state must be {expected}")
                self._connection.execute(
                    """UPDATE alpha_workflow_batches
                       SET state=?,receipt_digest=?,owner=NULL,lease_until=NULL,version=version+1
                       WHERE batch_id=? AND version=?""",
                    (state, receipt_digest, batch_id, snapshot.version),
                )
                if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise AlphaWorkflowConflict("workflow completion version conflict")
                self._append_event_locked(
                    batch_id,
                    state,
                    now,
                    worker_id,
                    fencing_token,
                    {"kind": "COMPLETED", "receipt_digest": receipt_digest},
                )
                result = self._snapshot_locked(batch_id)
                self._connection.execute("COMMIT")
                return result
            except AlphaWorkflowError:
                self._rollback()
                raise
            except sqlite3.Error as exc:
                self._rollback()
                raise AlphaWorkflowError("workflow completion failed") from exc

    def transition(self, batch_id: str, state: str, *, at: int) -> WorkflowSnapshot:
        _require_hex("batch_id", batch_id)
        _require_int("at", at)
        if state not in _BATCH_STATES:
            raise AlphaWorkflowStateError(f"unknown workflow state {state}")
        with self._lock:
            try:
                self._begin(write=True)
                snapshot = self._snapshot_locked(batch_id)
                if state not in _TRANSITIONS[snapshot.state]:
                    raise AlphaWorkflowStateError(f"invalid workflow transition {snapshot.state} -> {state}")
                self._connection.execute(
                    """UPDATE alpha_workflow_batches SET state=?,version=version+1
                       WHERE batch_id=? AND version=?""",
                    (state, batch_id, snapshot.version),
                )
                if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise AlphaWorkflowConflict("workflow transition version conflict")
                self._append_event_locked(
                    batch_id,
                    state,
                    at,
                    None,
                    snapshot.fencing_token,
                    {"kind": "TRANSITION"},
                )
                result = self._snapshot_locked(batch_id)
                self._connection.execute("COMMIT")
                return result
            except AlphaWorkflowError:
                self._rollback()
                raise
            except sqlite3.Error as exc:
                self._rollback()
                raise AlphaWorkflowError("workflow transition failed") from exc

    def events(self, batch_id: str) -> tuple[WorkflowEvent, ...]:
        _require_hex("batch_id", batch_id)
        with self._lock:
            try:
                self._begin(write=False)
                rows = self._connection.execute(
                    """SELECT batch_id,event_index,state,at,worker_id,fencing_token,
                              detail_json,global_seq,prev_hash,event_hash
                       FROM alpha_workflow_events WHERE batch_id=? ORDER BY event_index""",
                    (batch_id,),
                ).fetchall()
                result = tuple(
                    WorkflowEvent(
                        batch_id=row[0],
                        event_index=row[1],
                        state=row[2],
                        at=row[3],
                        worker_id=row[4],
                        fencing_token=row[5],
                        detail=json.loads(row[6]),
                        global_seq=row[7],
                        prev_hash=row[8],
                        event_hash=row[9],
                    )
                    for row in rows
                )
                self._connection.execute("COMMIT")
                return result
            except AlphaWorkflowError:
                self._rollback()
                raise
            except (sqlite3.Error, json.JSONDecodeError) as exc:
                self._rollback()
                raise AlphaWorkflowError("workflow event read failed") from exc


__all__ = [
    "AlphaWorkflowError",
    "AlphaWorkflowConflict",
    "AlphaWorkflowBusy",
    "AlphaWorkflowFenced",
    "AlphaWorkflowStateError",
    "WorkflowCandidate",
    "WorkflowSnapshot",
    "WorkflowClaim",
    "WorkflowEvent",
    "WorkflowStore",
]
