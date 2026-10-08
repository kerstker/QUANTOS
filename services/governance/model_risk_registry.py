"""Durable append-only authority for governed model-risk artifacts.

The registry is offline evidence infrastructure.  It has no wall clock,
network, provider credential, promotion authority or execution capability.
Artifacts and runtime invocations have independent contiguous hash chains,
while a transactional metadata row pins both retained heads so tail truncation
cannot be mistaken for a valid shorter local history.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from pydantic import BaseModel, ValidationError

from quantos.contracts.hashing import stable_hash
from quantos.contracts.model_risk import (
    ChallengerReview,
    ExternalProviderBinding,
    ModelCard,
    ModelRiskArtifact,
    OfflineEvaluation,
    PromptTemplateCard,
    RedTeamReport,
    RuntimeInvocation,
    ToolManifest,
)


MODEL_RISK_STORE_KIND = "QUANTOS_MODEL_RISK_AUTHORITY"
MODEL_RISK_STORE_SCHEMA_VERSION = 2


class ModelRiskStoreError(RuntimeError):
    """The store is missing, unreadable, uninitialized, or corrupt."""


class ImmutableModelRiskArtifact(ModelRiskStoreError):
    """An immutable identity was presented with different content."""


_SCHEMA = f"""
CREATE TABLE model_risk_meta (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    store_kind TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    artifact_count INTEGER NOT NULL,
    artifact_head_hash TEXT NOT NULL,
    runtime_count INTEGER NOT NULL,
    runtime_head_hash TEXT NOT NULL
) STRICT;
CREATE TABLE model_risk_artifacts (
    sequence INTEGER PRIMARY KEY,
    artifact_id TEXT UNIQUE NOT NULL,
    artifact_type TEXT NOT NULL,
    recorded_at INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    record_hash TEXT UNIQUE NOT NULL,
    payload_json TEXT NOT NULL
) STRICT;
CREATE TABLE model_risk_runtime_events (
    sequence INTEGER PRIMARY KEY,
    invocation_id TEXT UNIQUE NOT NULL,
    recorded_at INTEGER NOT NULL,
    model_ref TEXT NOT NULL,
    prompt_ref TEXT NOT NULL,
    tool_manifest_ref TEXT NOT NULL,
    action TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT UNIQUE NOT NULL,
    payload_json TEXT NOT NULL
) STRICT;
INSERT INTO model_risk_meta
  (singleton,store_kind,schema_version,artifact_count,artifact_head_hash,
   runtime_count,runtime_head_hash)
VALUES
  (1,'{MODEL_RISK_STORE_KIND}',{MODEL_RISK_STORE_SCHEMA_VERSION},0,'',0,'');
CREATE TRIGGER model_risk_meta_no_delete
BEFORE DELETE ON model_risk_meta
BEGIN SELECT RAISE(ABORT, 'model risk metadata cannot be deleted'); END;
CREATE TRIGGER model_risk_artifacts_no_update
BEFORE UPDATE ON model_risk_artifacts
BEGIN SELECT RAISE(ABORT, 'model risk artifacts are append-only'); END;
CREATE TRIGGER model_risk_artifacts_no_delete
BEFORE DELETE ON model_risk_artifacts
BEGIN SELECT RAISE(ABORT, 'model risk artifacts are append-only'); END;
CREATE TRIGGER model_risk_runtime_no_update
BEFORE UPDATE ON model_risk_runtime_events
BEGIN SELECT RAISE(ABORT, 'model risk runtime history is append-only'); END;
CREATE TRIGGER model_risk_runtime_no_delete
BEFORE DELETE ON model_risk_runtime_events
BEGIN SELECT RAISE(ABORT, 'model risk runtime history is append-only'); END;
"""


_ARTIFACT_TYPES: dict[str, type[ModelRiskArtifact]] = {
    cls.__name__: cls
    for cls in (
        ModelCard,
        PromptTemplateCard,
        ToolManifest,
        ExternalProviderBinding,
        OfflineEvaluation,
        RedTeamReport,
        ChallengerReview,
    )
}


def _payload_json(artifact: BaseModel) -> str:
    return json.dumps(
        artifact.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _artifact_record_hash(
    *,
    sequence: int,
    previous_hash: str,
    payload: dict,
) -> str:
    return stable_hash(
        {
            "artifact": payload,
            "previous_hash": previous_hash,
            "sequence": sequence,
        }
    )


def _runtime_event_hash(previous_hash: str, payload: dict) -> str:
    return stable_hash({"previous_hash": previous_hash, "invocation": payload})


class SqliteModelRiskStore:
    """Strict SQLite authority with transactional chain-head pinning."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("path must be non-empty")
        self.path = str(Path(path))
        if os.path.islink(self.path):
            raise ModelRiskStoreError("model-risk authority path must not be a symlink")
        if not create and not os.path.isfile(self.path):
            raise ModelRiskStoreError("model-risk store is unavailable")
        try:
            self._conn = sqlite3.connect(self.path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            if create:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
                tables = self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if not tables:
                    self._conn.executescript(_SCHEMA)
                    self._conn.commit()
            self._verify_store()
        except (sqlite3.DatabaseError, ModelRiskStoreError):
            if hasattr(self, "_conn"):
                self._conn.close()
            raise

    def close(self) -> None:
        self._conn.close()

    def _artifact_chain_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT sequence,artifact_id,artifact_type,recorded_at,content_hash,"
            "previous_hash,record_hash,payload_json "
            "FROM model_risk_artifacts ORDER BY sequence"
        ).fetchall()

    def _runtime_chain_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT sequence,invocation_id,recorded_at,model_ref,prompt_ref,"
            "tool_manifest_ref,action,previous_hash,event_hash,payload_json "
            "FROM model_risk_runtime_events ORDER BY sequence"
        ).fetchall()

    @staticmethod
    def _load_artifact_row(row: sqlite3.Row) -> ModelRiskArtifact:
        artifact_type = _ARTIFACT_TYPES.get(row["artifact_type"])
        if artifact_type is None:
            raise ModelRiskStoreError(
                f"unknown model-risk artifact type: {row['artifact_type']!r}"
            )
        try:
            payload = json.loads(row["payload_json"])
            artifact = artifact_type.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ModelRiskStoreError(
                f"artifact {row['artifact_id']!r} is corrupt"
            ) from exc
        if (
            artifact.artifact_id != row["artifact_id"]
            or artifact.recorded_at != row["recorded_at"]
            or artifact.content_hash != row["content_hash"]
            or stable_hash(payload) != row["content_hash"]
            or row["payload_json"] != _payload_json(artifact)
        ):
            raise ModelRiskStoreError(
                f"artifact {row['artifact_id']!r} contract mismatch"
            )
        return artifact

    @staticmethod
    def _load_runtime_row(row: sqlite3.Row) -> tuple[RuntimeInvocation, dict]:
        try:
            payload = json.loads(row["payload_json"])
            invocation = RuntimeInvocation.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ModelRiskStoreError(
                f"runtime invocation {row['invocation_id']!r} is corrupt"
            ) from exc
        if (
            invocation.invocation_id != row["invocation_id"]
            or invocation.recorded_at != row["recorded_at"]
            or invocation.model_ref != row["model_ref"]
            or invocation.prompt_ref != row["prompt_ref"]
            or invocation.tool_manifest_ref != row["tool_manifest_ref"]
            or invocation.action != row["action"]
            or row["payload_json"] != _payload_json(invocation)
        ):
            raise ModelRiskStoreError(
                f"runtime invocation {row['invocation_id']!r} contract mismatch"
            )
        return invocation, payload

    def _verify_store(self) -> None:
        try:
            if (
                self._conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
                != "wal"
                or self._conn.execute("PRAGMA synchronous").fetchone()[0] != 2
                or self._conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1
            ):
                raise ModelRiskStoreError("model-risk durability pragmas are invalid")
            if self._conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ModelRiskStoreError("model-risk SQLite integrity failed")
            tables = {
                row[0]
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if tables != {
                "model_risk_meta",
                "model_risk_artifacts",
                "model_risk_runtime_events",
            }:
                raise ModelRiskStoreError("model-risk store schema does not match")
            expected_columns = {
                "model_risk_meta": (
                    ("singleton", "INTEGER", 0, 1),
                    ("store_kind", "TEXT", 1, 0),
                    ("schema_version", "INTEGER", 1, 0),
                    ("artifact_count", "INTEGER", 1, 0),
                    ("artifact_head_hash", "TEXT", 1, 0),
                    ("runtime_count", "INTEGER", 1, 0),
                    ("runtime_head_hash", "TEXT", 1, 0),
                ),
                "model_risk_artifacts": (
                    ("sequence", "INTEGER", 0, 1),
                    ("artifact_id", "TEXT", 1, 0),
                    ("artifact_type", "TEXT", 1, 0),
                    ("recorded_at", "INTEGER", 1, 0),
                    ("content_hash", "TEXT", 1, 0),
                    ("previous_hash", "TEXT", 1, 0),
                    ("record_hash", "TEXT", 1, 0),
                    ("payload_json", "TEXT", 1, 0),
                ),
                "model_risk_runtime_events": (
                    ("sequence", "INTEGER", 0, 1),
                    ("invocation_id", "TEXT", 1, 0),
                    ("recorded_at", "INTEGER", 1, 0),
                    ("model_ref", "TEXT", 1, 0),
                    ("prompt_ref", "TEXT", 1, 0),
                    ("tool_manifest_ref", "TEXT", 1, 0),
                    ("action", "TEXT", 1, 0),
                    ("previous_hash", "TEXT", 1, 0),
                    ("event_hash", "TEXT", 1, 0),
                    ("payload_json", "TEXT", 1, 0),
                ),
            }
            for table in tables:
                actual = tuple(
                    (row["name"], row["type"], row["notnull"], row["pk"])
                    for row in self._conn.execute(f"PRAGMA table_info({table})")
                )
                strict = self._conn.execute(
                    "SELECT strict FROM pragma_table_list WHERE name=?",
                    (table,),
                ).fetchone()
                if (
                    actual != expected_columns[table]
                    or strict is None
                    or strict[0] != 1
                ):
                    raise ModelRiskStoreError(
                        "model-risk table contract is invalid"
                    )
            triggers = {
                row[0]
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            if triggers != {
                "model_risk_meta_no_delete",
                "model_risk_artifacts_no_update",
                "model_risk_artifacts_no_delete",
                "model_risk_runtime_no_update",
                "model_risk_runtime_no_delete",
            }:
                raise ModelRiskStoreError("model-risk trigger contract is invalid")
            trigger_sql = {
                row["name"]: " ".join(row["sql"].split())
                for row in self._conn.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
                )
            }
            expected_trigger_fragments = {
                "model_risk_meta_no_delete": (
                    "BEFORE DELETE ON model_risk_meta",
                    "RAISE(ABORT, 'model risk metadata cannot be deleted')",
                ),
                "model_risk_artifacts_no_update": (
                    "BEFORE UPDATE ON model_risk_artifacts",
                    "RAISE(ABORT, 'model risk artifacts are append-only')",
                ),
                "model_risk_artifacts_no_delete": (
                    "BEFORE DELETE ON model_risk_artifacts",
                    "RAISE(ABORT, 'model risk artifacts are append-only')",
                ),
                "model_risk_runtime_no_update": (
                    "BEFORE UPDATE ON model_risk_runtime_events",
                    "RAISE(ABORT, 'model risk runtime history is append-only')",
                ),
                "model_risk_runtime_no_delete": (
                    "BEFORE DELETE ON model_risk_runtime_events",
                    "RAISE(ABORT, 'model risk runtime history is append-only')",
                ),
            }
            if any(
                any(fragment not in trigger_sql[name] for fragment in fragments)
                for name, fragments in expected_trigger_fragments.items()
            ):
                raise ModelRiskStoreError(
                    "model-risk trigger implementation is invalid"
                )
            meta = self._conn.execute(
                "SELECT store_kind,schema_version,artifact_count,"
                "artifact_head_hash,runtime_count,runtime_head_hash "
                "FROM model_risk_meta WHERE singleton=1"
            ).fetchone()
            if (
                meta is None
                or meta["store_kind"] != MODEL_RISK_STORE_KIND
                or meta["schema_version"] != MODEL_RISK_STORE_SCHEMA_VERSION
            ):
                raise ModelRiskStoreError("model-risk metadata is invalid")

            previous = ""
            artifacts = self._artifact_chain_rows()
            for expected_sequence, row in enumerate(artifacts, start=1):
                if row["sequence"] != expected_sequence:
                    raise ModelRiskStoreError(
                        "model-risk artifact sequence is not contiguous"
                    )
                artifact = self._load_artifact_row(row)
                expected_hash = _artifact_record_hash(
                    sequence=expected_sequence,
                    previous_hash=previous,
                    payload=artifact.model_dump(mode="json"),
                )
                if (
                    row["previous_hash"] != previous
                    or row["record_hash"] != expected_hash
                ):
                    raise ModelRiskStoreError(
                        "model-risk artifact chain is invalid"
                    )
                previous = expected_hash
            if (
                meta["artifact_count"] != len(artifacts)
                or meta["artifact_head_hash"] != previous
            ):
                raise ModelRiskStoreError("model-risk artifact head mismatch")

            previous = ""
            runtime = self._runtime_chain_rows()
            for expected_sequence, row in enumerate(runtime, start=1):
                if row["sequence"] != expected_sequence:
                    raise ModelRiskStoreError(
                        "model-risk runtime sequence is not contiguous"
                    )
                _invocation, payload = self._load_runtime_row(row)
                expected_hash = _runtime_event_hash(previous, payload)
                if (
                    row["previous_hash"] != previous
                    or row["event_hash"] != expected_hash
                ):
                    raise ModelRiskStoreError(
                        "model-risk runtime chain is invalid"
                    )
                previous = expected_hash
            if (
                meta["runtime_count"] != len(runtime)
                or meta["runtime_head_hash"] != previous
            ):
                raise ModelRiskStoreError("model-risk runtime head mismatch")
        except sqlite3.DatabaseError as exc:
            raise ModelRiskStoreError("model-risk store is unreadable") from exc

    def _require_schema(self) -> None:
        self._verify_store()

    def _require_runtime_schema(self) -> None:
        self._verify_store()

    def put(
        self,
        *,
        artifact_id: str,
        artifact_type: str,
        recorded_at: int,
        content_hash: str,
        payload_json: str,
    ) -> bool:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._verify_store()
            existing = self._conn.execute(
                "SELECT artifact_type,recorded_at,content_hash,payload_json "
                "FROM model_risk_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            candidate = (artifact_type, recorded_at, content_hash, payload_json)
            if existing is not None:
                stored = tuple(existing)
                self._conn.rollback()
                if stored == candidate:
                    return False
                raise ImmutableModelRiskArtifact(
                    f"artifact {artifact_id!r} already exists with different content"
                )
            payload = json.loads(payload_json)
            meta = self._conn.execute(
                "SELECT artifact_count,artifact_head_hash FROM model_risk_meta "
                "WHERE singleton=1"
            ).fetchone()
            sequence = int(meta["artifact_count"]) + 1
            previous = str(meta["artifact_head_hash"])
            record_hash = _artifact_record_hash(
                sequence=sequence,
                previous_hash=previous,
                payload=payload,
            )
            self._conn.execute(
                "INSERT INTO model_risk_artifacts "
                "(sequence,artifact_id,artifact_type,recorded_at,content_hash,"
                "previous_hash,record_hash,payload_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    sequence,
                    artifact_id,
                    artifact_type,
                    recorded_at,
                    content_hash,
                    previous,
                    record_hash,
                    payload_json,
                ),
            )
            self._conn.execute(
                "UPDATE model_risk_meta SET artifact_count=?,artifact_head_hash=? "
                "WHERE singleton=1",
                (sequence, record_hash),
            )
            self._verify_store()
            self._conn.commit()
            return True
        except ImmutableModelRiskArtifact:
            raise
        except (json.JSONDecodeError, sqlite3.DatabaseError, ModelRiskStoreError) as exc:
            if self._conn.in_transaction:
                self._conn.rollback()
            if isinstance(exc, ModelRiskStoreError):
                raise
            raise ModelRiskStoreError("model-risk write failed") from exc

    def get_row(self, artifact_id: str) -> sqlite3.Row:
        self._verify_store()
        row = self._conn.execute(
            "SELECT artifact_id,artifact_type,recorded_at,content_hash,payload_json "
            "FROM model_risk_artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            raise ModelRiskStoreError(
                f"unknown model-risk artifact: {artifact_id!r}"
            )
        return row

    def all_rows(self) -> list[sqlite3.Row]:
        self._verify_store()
        return self._conn.execute(
            "SELECT artifact_id,artifact_type,recorded_at,content_hash,payload_json "
            "FROM model_risk_artifacts ORDER BY artifact_id"
        ).fetchall()

    def put_runtime_invocation(self, invocation: RuntimeInvocation) -> str:
        if not isinstance(invocation, RuntimeInvocation):
            raise TypeError("invocation must be a RuntimeInvocation")
        payload_json = _payload_json(invocation)
        payload = invocation.model_dump(mode="json")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._verify_store()
            existing = self._conn.execute(
                "SELECT event_hash,payload_json FROM model_risk_runtime_events "
                "WHERE invocation_id=?",
                (invocation.invocation_id,),
            ).fetchone()
            if existing is not None:
                self._conn.rollback()
                if existing["payload_json"] == payload_json:
                    return str(existing["event_hash"])
                raise ImmutableModelRiskArtifact(
                    f"runtime invocation {invocation.invocation_id!r} already "
                    "exists with different content"
                )
            meta = self._conn.execute(
                "SELECT runtime_count,runtime_head_hash FROM model_risk_meta "
                "WHERE singleton=1"
            ).fetchone()
            sequence = int(meta["runtime_count"]) + 1
            previous = str(meta["runtime_head_hash"])
            event_hash = _runtime_event_hash(previous, payload)
            self._conn.execute(
                "INSERT INTO model_risk_runtime_events "
                "(sequence,invocation_id,recorded_at,model_ref,prompt_ref,"
                "tool_manifest_ref,action,previous_hash,event_hash,payload_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    sequence,
                    invocation.invocation_id,
                    invocation.recorded_at,
                    invocation.model_ref,
                    invocation.prompt_ref,
                    invocation.tool_manifest_ref,
                    invocation.action,
                    previous,
                    event_hash,
                    payload_json,
                ),
            )
            self._conn.execute(
                "UPDATE model_risk_meta SET runtime_count=?,runtime_head_hash=? "
                "WHERE singleton=1",
                (sequence, event_hash),
            )
            self._verify_store()
            self._conn.commit()
            return event_hash
        except ImmutableModelRiskArtifact:
            raise
        except (sqlite3.DatabaseError, ModelRiskStoreError) as exc:
            if self._conn.in_transaction:
                self._conn.rollback()
            if isinstance(exc, ModelRiskStoreError):
                raise
            raise ModelRiskStoreError("model-risk runtime write failed") from exc

    def runtime_rows(self) -> list[sqlite3.Row]:
        self._verify_store()
        return self._runtime_chain_rows()

    def artifact_history_hash(self) -> str:
        self._verify_store()
        row = self._conn.execute(
            "SELECT artifact_head_hash FROM model_risk_meta WHERE singleton=1"
        ).fetchone()
        return str(row["artifact_head_hash"])


class GovernedModelRegistry:
    def __init__(self, store: SqliteModelRiskStore) -> None:
        self._store = store

    def _record(self, artifact: ModelRiskArtifact) -> bool:
        payload = _payload_json(artifact)
        return self._store.put(
            artifact_id=artifact.artifact_id,
            artifact_type=type(artifact).__name__,
            recorded_at=artifact.recorded_at,
            content_hash=artifact.content_hash,
            payload_json=payload,
        )

    def record_artifact(self, artifact: ModelRiskArtifact) -> bool:
        if type(artifact).__name__ not in _ARTIFACT_TYPES:
            raise TypeError(
                f"unsupported model-risk artifact: {type(artifact).__name__}"
            )
        return self._record(artifact)

    def record_model_card(self, artifact: ModelCard) -> bool:
        return self._record(artifact)

    def record_prompt_template(self, artifact: PromptTemplateCard) -> bool:
        return self._record(artifact)

    def record_tool_manifest(self, artifact: ToolManifest) -> bool:
        return self._record(artifact)

    def record_external_provider_binding(
        self, artifact: ExternalProviderBinding
    ) -> bool:
        return self._record(artifact)

    def record_offline_evaluation(self, artifact: OfflineEvaluation) -> bool:
        return self._record(artifact)

    def record_red_team_report(self, artifact: RedTeamReport) -> bool:
        return self._record(artifact)

    def record_challenger_review(self, artifact: ChallengerReview) -> bool:
        return self._record(artifact)

    def load_artifact(self, artifact_id: str) -> ModelRiskArtifact:
        row = self._store.get_row(artifact_id)
        return self._store._load_artifact_row(row)

    def verify_artifact(self, artifact_id: str) -> bool:
        try:
            self.load_artifact(artifact_id)
            return True
        except (ModelRiskStoreError, TypeError, ValueError):
            return False

    def verify_all(self) -> bool:
        try:
            rows = self._store.all_rows()
            return bool(rows) and all(
                self.verify_artifact(row["artifact_id"]) for row in rows
            )
        except ModelRiskStoreError:
            return False

    def artifact_ids(self) -> tuple[str, ...]:
        return tuple(row["artifact_id"] for row in self._store.all_rows())

    def artifact_history_hash(self) -> str:
        try:
            return self._store.artifact_history_hash()
        except ModelRiskStoreError:
            return ""

    def record_runtime_invocation(self, invocation: RuntimeInvocation) -> str:
        if not isinstance(invocation, RuntimeInvocation):
            raise TypeError(
                "runtime invocation must satisfy the canonical contract"
            )
        return self._store.put_runtime_invocation(invocation)

    def runtime_rows(self) -> list[sqlite3.Row]:
        return self._store.runtime_rows()

    def verify_runtime_history(self) -> bool:
        try:
            rows = self._store.runtime_rows()
            return bool(rows)
        except ModelRiskStoreError:
            return False

    def runtime_history_hash(self) -> str:
        if not self.verify_runtime_history():
            return ""
        rows = self._store.runtime_rows()
        return str(rows[-1]["event_hash"])


__all__ = [
    "MODEL_RISK_STORE_KIND",
    "MODEL_RISK_STORE_SCHEMA_VERSION",
    "GovernedModelRegistry",
    "ImmutableModelRiskArtifact",
    "ModelRiskStoreError",
    "SqliteModelRiskStore",
]
