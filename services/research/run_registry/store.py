"""Durable SQLite persistence for research runs.

Runner invocations and legacy indexed mirrors share the read model but not the write contract. A
runner invocation has a globally unique invocation id plus two hashes: ``request_hash`` makes retries
idempotent, while ``identity_hash`` fences every immutable field during terminal publication. Legacy
mirrors remain refreshable through the compatibility ``upsert``/``set_status`` methods and can never
overwrite a runner-owned row.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Optional

from .contract import (
    RUN_OWNER_LEGACY_MIRROR,
    RUN_OWNER_RUNNER_INVOCATION,
    RUNNER_RESTART_INTERRUPTED,
    InvocationConflictError,
    ResearchRun,
    RunIdentityError,
    make_request_hash,
    make_run_id,
    make_run_identity_hash,
    normalize_invocation_id,
)

_COLS = (
    "run_id",
    "kind",
    "title",
    "status",
    "created_ts",
    "finished_ts",
    "verdict",
    "provenance",
    "error",
    "payload_json",
    "owner",
    "invocation_id",
    "request_hash",
    "identity_hash",
)


class ResearchRunStore:
    """Thread-safe per connection; SQLite constraints coordinate independent processes."""

    def __init__(self, db_path: str, create: bool = True):
        if create and db_path != ":memory:":
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._c = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        if create:
            with self._lock:
                self._initialize_schema()

    def _initialize_schema(self) -> None:
        """Create or atomically migrate the pre-invocation schema.

        Rows written by the first invocation implementation stored ``invocation_id`` only inside the
        JSON payload. They are promoted to runner ownership here. Duplicate historical invocation ids
        fail migration instead of silently choosing one record.
        """
        self._c.execute("BEGIN IMMEDIATE")
        try:
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, kind TEXT, title TEXT,"
                " status TEXT, created_ts INTEGER, finished_ts INTEGER, verdict TEXT,"
                " provenance TEXT, error TEXT, payload_json TEXT,"
                f" owner TEXT NOT NULL DEFAULT '{RUN_OWNER_LEGACY_MIRROR}',"
                " invocation_id TEXT, request_hash TEXT, identity_hash TEXT)"
            )
            columns = {
                row[1] for row in self._c.execute("PRAGMA table_info(runs)").fetchall()
            }
            additions = {
                "owner": f"TEXT NOT NULL DEFAULT '{RUN_OWNER_LEGACY_MIRROR}'",
                "invocation_id": "TEXT",
                "request_hash": "TEXT",
                "identity_hash": "TEXT",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    self._c.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")

            rows = self._c.execute(f"SELECT {','.join(_COLS)} FROM runs").fetchall()
            for row in rows:
                payload = json.loads(row[9])
                flat_invocation = row[11]
                payload_invocation = payload.get("invocation_id")
                if (
                    flat_invocation is not None
                    and payload_invocation is not None
                    and flat_invocation != payload_invocation
                ):
                    raise RunIdentityError(
                        f"run {row[0]} has conflicting invocation identities"
                    )
                invocation_id = flat_invocation or payload_invocation
                if invocation_id is None:
                    if row[10] == RUN_OWNER_RUNNER_INVOCATION:
                        raise RunIdentityError(
                            f"runner-owned run {row[0]} has no invocation_id"
                        )
                    self._c.execute(
                        "UPDATE runs SET owner=?,invocation_id=NULL,request_hash=NULL,"
                        "identity_hash=NULL WHERE run_id=?",
                        (RUN_OWNER_LEGACY_MIRROR, row[0]),
                    )
                    continue

                invocation_id = normalize_invocation_id(invocation_id)
                request_hash = make_request_hash(row[1], payload["params"], payload.get("operator"))
                identity_hash = make_run_identity_hash(
                    run_id=row[0],
                    kind=row[1],
                    params=payload["params"],
                    created_ts=row[4],
                    operator=payload.get("operator"),
                    code_version=payload.get("code_version"),
                    invocation_id=invocation_id,
                    owner=RUN_OWNER_RUNNER_INVOCATION,
                )
                if row[12] not in (None, request_hash) or row[13] not in (None, identity_hash):
                    raise RunIdentityError(f"run {row[0]} has invalid persisted identity hashes")
                payload["invocation_id"] = invocation_id
                self._c.execute(
                    "UPDATE runs SET owner=?,invocation_id=?,request_hash=?,identity_hash=?,"
                    "payload_json=? WHERE run_id=?",
                    (
                        RUN_OWNER_RUNNER_INVOCATION,
                        invocation_id,
                        request_hash,
                        identity_hash,
                        json.dumps(payload),
                        row[0],
                    ),
                )

            self._c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_runs_runner_invocation "
                "ON runs(invocation_id) "
                f"WHERE owner='{RUN_OWNER_RUNNER_INVOCATION}'"
            )
            self._c.execute("CREATE INDEX IF NOT EXISTS ix_runs_created ON runs(created_ts)")
            self._c.commit()
        except sqlite3.IntegrityError as exc:
            self._c.rollback()
            raise RunIdentityError("duplicate runner invocation in existing run registry") from exc
        except Exception:
            self._c.rollback()
            raise

    @staticmethod
    def _payload(r: ResearchRun) -> str:
        return json.dumps(
            {
                "params": r.params,
                "metrics": r.metrics,
                "series": r.series,
                "caveats": r.caveats,
                "log": r.log,
                "code_version": r.code_version,
                "operator": r.operator,
                "invocation_id": r.invocation_id,
            }
        )

    @staticmethod
    def _values(r: ResearchRun) -> tuple:
        return (
            r.run_id,
            r.kind,
            r.title,
            r.status,
            r.created_ts,
            r.finished_ts,
            r.verdict,
            r.provenance,
            r.error,
            ResearchRunStore._payload(r),
            r.owner,
            r.invocation_id,
            r.request_hash,
            r.identity_hash,
        )

    @staticmethod
    def _validate_mirror(r: ResearchRun) -> None:
        if (
            r.owner != RUN_OWNER_LEGACY_MIRROR
            or r.invocation_id is not None
            or r.request_hash is not None
            or r.identity_hash is not None
        ):
            raise RunIdentityError("legacy mirror writes cannot carry runner invocation identity")

    @staticmethod
    def _validate_new_runner(r: ResearchRun) -> None:
        if r.owner != RUN_OWNER_RUNNER_INVOCATION or r.invocation_id is None:
            raise RunIdentityError("runner insertion requires runner ownership and invocation_id")
        invocation_id = normalize_invocation_id(r.invocation_id)
        if invocation_id != r.invocation_id:
            raise RunIdentityError("invocation_id must be normalized before persistence")
        expected_run_id = make_run_id(
            r.kind, r.params, r.created_ts, invocation_id=invocation_id
        )
        expected_request_hash = make_request_hash(r.kind, r.params, r.operator)
        expected_identity_hash = make_run_identity_hash(
            run_id=r.run_id,
            kind=r.kind,
            params=r.params,
            created_ts=r.created_ts,
            operator=r.operator,
            code_version=r.code_version,
            invocation_id=invocation_id,
            owner=r.owner,
        )
        if r.run_id != expected_run_id:
            raise RunIdentityError("runner run_id is not bound to invocation_id")
        if r.request_hash != expected_request_hash or r.identity_hash != expected_identity_hash:
            raise RunIdentityError("runner request or identity hash does not match immutable fields")

    @staticmethod
    def _same_request(existing: ResearchRun, proposed: ResearchRun) -> bool:
        return (
            existing.invocation_id == proposed.invocation_id
            and existing.request_hash == proposed.request_hash
            and existing.kind == proposed.kind
            and existing.params == proposed.params
            and existing.operator == proposed.operator
        )

    def upsert(self, r: ResearchRun) -> None:
        """Refresh a legacy indexed mirror; runner-owned rows are never eligible."""
        self._validate_mirror(r)
        values = self._values(r)
        with self._lock:
            cursor = self._c.execute(
                f"INSERT INTO runs ({','.join(_COLS)}) VALUES ({','.join('?' for _ in _COLS)}) "
                "ON CONFLICT(run_id) DO UPDATE SET kind=excluded.kind,title=excluded.title,"
                "status=excluded.status,created_ts=excluded.created_ts,"
                "finished_ts=excluded.finished_ts,verdict=excluded.verdict,"
                "provenance=excluded.provenance,error=excluded.error,"
                "payload_json=excluded.payload_json "
                f"WHERE runs.owner='{RUN_OWNER_LEGACY_MIRROR}' "
                "AND runs.invocation_id IS NULL AND runs.request_hash IS NULL "
                "AND runs.identity_hash IS NULL",
                values,
            )
            self._c.commit()
            if cursor.rowcount != 1:
                raise RunIdentityError("legacy mirror cannot overwrite a runner-owned run")

    def insert_or_get(self, r: ResearchRun) -> tuple[ResearchRun, bool]:
        """Atomically insert one invocation or return its identical prior request."""
        self._validate_new_runner(r)
        with self._lock:
            try:
                self._c.execute(
                    f"INSERT INTO runs ({','.join(_COLS)}) "
                    f"VALUES ({','.join('?' for _ in _COLS)})",
                    self._values(r),
                )
                self._c.commit()
                return r, True
            except sqlite3.IntegrityError as exc:
                self._c.rollback()
                row = self._c.execute(
                    f"SELECT {','.join(_COLS)} FROM runs WHERE invocation_id=? "
                    f"AND owner='{RUN_OWNER_RUNNER_INVOCATION}'",
                    (r.invocation_id,),
                ).fetchone()
                if row is not None:
                    existing = self._row(row)
                    if self._same_request(existing, r):
                        return existing, False
                    raise InvocationConflictError(
                        "invocation_id is already bound to a different kind, params, or operator"
                    ) from exc
                if self._c.execute(
                    "SELECT 1 FROM runs WHERE run_id=?", (r.run_id,)
                ).fetchone():
                    raise InvocationConflictError(
                        "runner run_id collides with a different persisted invocation"
                    ) from exc
                raise

    def insert(self, r: ResearchRun) -> None:
        """Strict runner insert retained for callers that do not want idempotent replay."""
        _existing, inserted = self.insert_or_get(r)
        if not inserted:
            raise ValueError(f"research run already exists: {r.run_id}")

    @staticmethod
    def _immutable_identity(r: ResearchRun) -> tuple:
        return (
            r.run_id,
            r.kind,
            r.params,
            r.created_ts,
            r.operator,
            r.code_version,
            r.invocation_id,
            r.owner,
            r.request_hash,
            r.identity_hash,
        )

    def replace_if_status(self, r: ResearchRun, *, expected_status: str) -> bool:
        """Publish mutable result fields only, fenced by the persisted runner identity."""
        self._validate_new_runner(r)
        with self._lock:
            self._c.execute("BEGIN IMMEDIATE")
            try:
                row = self._c.execute(
                    f"SELECT {','.join(_COLS)} FROM runs WHERE run_id=?", (r.run_id,)
                ).fetchone()
                if row is None:
                    self._c.commit()
                    return False
                existing = self._row(row)
                if self._immutable_identity(existing) != self._immutable_identity(r):
                    raise RunIdentityError(
                        "terminal result does not match persisted immutable run identity"
                    )
                if existing.status != expected_status:
                    self._c.commit()
                    return False

                payload = json.loads(row[9])
                payload.update(
                    {
                        "metrics": r.metrics,
                        "series": r.series,
                        "caveats": r.caveats,
                        "log": r.log,
                    }
                )
                cursor = self._c.execute(
                    "UPDATE runs SET title=?,status=?,finished_ts=?,verdict=?,provenance=?,"
                    "error=?,payload_json=? WHERE run_id=? AND status=? AND owner=? "
                    "AND invocation_id=? AND request_hash=? AND identity_hash=?",
                    (
                        r.title,
                        r.status,
                        r.finished_ts,
                        r.verdict,
                        r.provenance,
                        r.error,
                        json.dumps(payload),
                        r.run_id,
                        expected_status,
                        r.owner,
                        r.invocation_id,
                        r.request_hash,
                        r.identity_hash,
                    ),
                )
                self._c.commit()
                return cursor.rowcount == 1
            except Exception:
                self._c.rollback()
                raise

    def _row(self, row) -> ResearchRun:
        payload = json.loads(row[9])
        result = ResearchRun(
            run_id=row[0],
            kind=row[1],
            title=row[2],
            status=row[3],
            created_ts=row[4],
            finished_ts=row[5],
            verdict=row[6],
            provenance=row[7],
            error=row[8],
            params=payload["params"],
            metrics=payload["metrics"],
            series=payload["series"],
            caveats=payload["caveats"],
            log=payload.get("log", []),
            code_version=payload.get("code_version"),
            operator=payload.get("operator"),
            invocation_id=row[11],
            owner=row[10],
            request_hash=row[12],
            identity_hash=row[13],
        )
        if result.owner == RUN_OWNER_RUNNER_INVOCATION:
            if payload.get("invocation_id") not in (None, result.invocation_id):
                raise RunIdentityError("payload invocation_id conflicts with the persisted identity")
            if result.invocation_id is None:
                raise RunIdentityError("runner-owned row has no invocation_id")
            expected_request_hash = make_request_hash(
                result.kind, result.params, result.operator
            )
            expected_identity_hash = make_run_identity_hash(
                run_id=result.run_id,
                kind=result.kind,
                params=result.params,
                created_ts=result.created_ts,
                operator=result.operator,
                code_version=result.code_version,
                invocation_id=result.invocation_id,
                owner=result.owner,
            )
            if (
                result.request_hash != expected_request_hash
                or result.identity_hash != expected_identity_hash
            ):
                raise RunIdentityError("persisted runner identity hash validation failed")
        return result

    def append_log(self, run_id: str, line: str) -> None:
        with self._lock:
            row = self._c.execute(
                "SELECT payload_json FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not row:
                return
            payload = json.loads(row[0])
            payload.setdefault("log", []).append(line)
            self._c.execute(
                "UPDATE runs SET payload_json=? WHERE run_id=?",
                (json.dumps(payload), run_id),
            )
            self._c.commit()

    def get(self, run_id: str) -> Optional[ResearchRun]:
        with self._lock:
            row = self._c.execute(
                f"SELECT {','.join(_COLS)} FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._row(row) if row else None

    def get_by_invocation(self, invocation_id: str) -> Optional[ResearchRun]:
        normalized = normalize_invocation_id(invocation_id)
        with self._lock:
            row = self._c.execute(
                f"SELECT {','.join(_COLS)} FROM runs WHERE invocation_id=? "
                f"AND owner='{RUN_OWNER_RUNNER_INVOCATION}'",
                (normalized,),
            ).fetchone()
        return self._row(row) if row else None

    def list(self, kind: Optional[str] = None, limit: int = 200) -> list[ResearchRun]:
        query = f"SELECT {','.join(_COLS)} FROM runs"
        args: tuple = ()
        if kind:
            query += " WHERE kind=?"
            args = (kind,)
        query += " ORDER BY created_ts DESC,run_id DESC LIMIT ?"
        with self._lock:
            rows = self._c.execute(query, args + (limit,)).fetchall()
        return [self._row(row) for row in rows]

    def set_status(
        self, run_id: str, status: str, error: Optional[str] = None
    ) -> None:
        """Legacy mirror compatibility only; runner rows require CAS transitions."""
        with self._lock:
            cursor = self._c.execute(
                "UPDATE runs SET status=?,error=? WHERE run_id=? AND owner=? "
                "AND invocation_id IS NULL AND request_hash IS NULL AND identity_hash IS NULL",
                (status, error, run_id, RUN_OWNER_LEGACY_MIRROR),
            )
            self._c.commit()
            if cursor.rowcount == 0 and self._c.execute(
                "SELECT 1 FROM runs WHERE run_id=?", (run_id,)
            ).fetchone():
                raise RunIdentityError("set_status cannot mutate a runner-owned run")

    def compare_and_set_status(
        self,
        run_id: str,
        *,
        expected_statuses: tuple[str, ...],
        status: str,
        error: Optional[str] = None,
    ) -> bool:
        if not expected_statuses:
            raise ValueError("expected_statuses must not be empty")
        placeholders = ",".join("?" for _ in expected_statuses)
        with self._lock:
            cursor = self._c.execute(
                f"UPDATE runs SET status=?,error=? WHERE run_id=? "
                f"AND owner='{RUN_OWNER_RUNNER_INVOCATION}' "
                "AND invocation_id IS NOT NULL AND identity_hash IS NOT NULL "
                f"AND status IN ({placeholders})",
                (status, error, run_id, *expected_statuses),
            )
            self._c.commit()
            return cursor.rowcount == 1

    def reconcile_interrupted_runner_runs(
        self,
        *,
        finished_ts: int,
    ) -> tuple[tuple[str, str], ...]:
        """Atomically terminalize invocations abandoned by a prior runner process.

        The executor is process-local, so a persisted ``queued`` or ``running`` invocation cannot
        still have a future after this service has restarted.  Replaying it implicitly would violate
        invocation provenance; leaving it non-terminal makes the console poll forever.  Fence the
        whole reconciliation to runner-owned identities and commit status, timestamp, error, and log
        together in one write transaction.  Legacy mirrors belong to their indexers and are excluded.

        Returns ``(run_id, previous_status)`` pairs in deterministic order for the adapter to audit.
        """
        if isinstance(finished_ts, bool) or not isinstance(finished_ts, int) or finished_ts < 0:
            raise ValueError("finished_ts must be a non-negative integer")

        with self._lock:
            self._c.execute("BEGIN IMMEDIATE")
            try:
                rows = self._c.execute(
                    f"SELECT {','.join(_COLS)} FROM runs WHERE owner=? "
                    "AND status IN ('queued','running') ORDER BY created_ts,run_id",
                    (RUN_OWNER_RUNNER_INVOCATION,),
                ).fetchall()
                prepared: list[tuple[ResearchRun, str, str]] = []
                for row in rows:
                    run = self._row(row)
                    payload = json.loads(row[9])
                    log = payload.get("log", [])
                    if not isinstance(log, list) or any(
                        not isinstance(line, str) for line in log
                    ):
                        raise RunIdentityError(
                            f"runner-owned run {run.run_id} has an invalid durable log"
                        )
                    payload["log"] = [
                        *log,
                        f"[error] {RUNNER_RESTART_INTERRUPTED}",
                    ]
                    prepared.append((run, run.status, json.dumps(payload)))

                for run, previous_status, payload_json in prepared:
                    cursor = self._c.execute(
                        "UPDATE runs SET status='error',finished_ts=?,error=?,payload_json=? "
                        "WHERE run_id=? AND owner=? AND status=? AND invocation_id=? "
                        "AND request_hash=? AND identity_hash=?",
                        (
                            finished_ts,
                            RUNNER_RESTART_INTERRUPTED,
                            payload_json,
                            run.run_id,
                            RUN_OWNER_RUNNER_INVOCATION,
                            previous_status,
                            run.invocation_id,
                            run.request_hash,
                            run.identity_hash,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RunIdentityError(
                            f"runner restart reconciliation lost identity fence for {run.run_id}"
                        )
                self._c.commit()
                return tuple(
                    (run.run_id, previous_status)
                    for run, previous_status, _payload_json in prepared
                )
            except Exception:
                self._c.rollback()
                raise
