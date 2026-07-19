"""Append-only persistence for governed model-risk artifacts.

The registry is an offline evidence store. It has no wall clock, network access,
promotion authority, or execution integration.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pydantic import BaseModel, ValidationError

from quantos.contracts.hashing import stable_hash
from quantos.contracts.model_risk import (
    ChallengerReview,
    ModelCard,
    ModelRiskArtifact,
    OfflineEvaluation,
    PromptTemplateCard,
    RedTeamReport,
    RuntimeInvocation,
    ToolManifest,
)


class ModelRiskStoreError(RuntimeError):
    """The store is missing, unreadable, uninitialized, or corrupt."""


class ImmutableModelRiskArtifact(ModelRiskStoreError):
    """An existing artifact identity was presented with different content."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_risk_artifacts (
    artifact_id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL,
    recorded_at INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_risk_runtime_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_id TEXT NOT NULL UNIQUE,
    recorded_at INTEGER NOT NULL,
    model_ref TEXT NOT NULL,
    prompt_ref TEXT NOT NULL,
    tool_manifest_ref TEXT NOT NULL,
    action TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""

_ARTIFACT_TYPES: dict[str, type[ModelRiskArtifact]] = {
    cls.__name__: cls
    for cls in (
        ModelCard,
        PromptTemplateCard,
        ToolManifest,
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


class SqliteModelRiskStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self.path = str(Path(path))
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _require_schema(self) -> None:
        try:
            row = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                ("model_risk_artifacts",),
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise ModelRiskStoreError(f"model-risk store unreadable: {exc}") from exc
        if row is None:
            raise ModelRiskStoreError("model-risk store is uninitialized")
        try:
            columns = {
                item["name"]
                for item in self._conn.execute(
                    "PRAGMA table_info(model_risk_artifacts)"
                ).fetchall()
            }
        except sqlite3.DatabaseError as exc:
            raise ModelRiskStoreError(f"model-risk schema unreadable: {exc}") from exc
        required = {
            "artifact_id",
            "artifact_type",
            "recorded_at",
            "content_hash",
            "payload_json",
        }
        if columns != required:
            raise ModelRiskStoreError("model-risk store schema does not match the contract")

    def _require_runtime_schema(self) -> None:
        try:
            row = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                ("model_risk_runtime_events",),
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise ModelRiskStoreError(f"model-risk runtime store unreadable: {exc}") from exc
        if row is None:
            raise ModelRiskStoreError("model-risk runtime store is uninitialized")
        try:
            columns = {
                item["name"]
                for item in self._conn.execute(
                    "PRAGMA table_info(model_risk_runtime_events)"
                ).fetchall()
            }
        except sqlite3.DatabaseError as exc:
            raise ModelRiskStoreError(f"model-risk runtime schema unreadable: {exc}") from exc
        required = {
            "sequence",
            "invocation_id",
            "recorded_at",
            "model_ref",
            "prompt_ref",
            "tool_manifest_ref",
            "action",
            "previous_hash",
            "event_hash",
            "payload_json",
        }
        if columns != required:
            raise ModelRiskStoreError("model-risk runtime schema does not match the contract")

    def put(
        self,
        *,
        artifact_id: str,
        artifact_type: str,
        recorded_at: int,
        content_hash: str,
        payload_json: str,
    ) -> bool:
        self._require_schema()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT artifact_type, recorded_at, content_hash, payload_json "
                "FROM model_risk_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            candidate = (artifact_type, recorded_at, content_hash, payload_json)
            if existing is not None:
                stored = (
                    existing["artifact_type"],
                    existing["recorded_at"],
                    existing["content_hash"],
                    existing["payload_json"],
                )
                self._conn.execute("ROLLBACK")
                if stored == candidate:
                    return False
                raise ImmutableModelRiskArtifact(
                    f"artifact {artifact_id!r} already exists with different content"
                )
            self._conn.execute(
                "INSERT INTO model_risk_artifacts "
                "(artifact_id, artifact_type, recorded_at, content_hash, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (artifact_id, artifact_type, recorded_at, content_hash, payload_json),
            )
            self._conn.execute("COMMIT")
            return True
        except ImmutableModelRiskArtifact:
            raise
        except sqlite3.DatabaseError as exc:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
            raise ModelRiskStoreError(f"model-risk write failed: {exc}") from exc

    def get_row(self, artifact_id: str) -> sqlite3.Row:
        self._require_schema()
        try:
            row = self._conn.execute(
                "SELECT artifact_id, artifact_type, recorded_at, content_hash, payload_json "
                "FROM model_risk_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise ModelRiskStoreError(f"model-risk read failed: {exc}") from exc
        if row is None:
            raise ModelRiskStoreError(f"unknown model-risk artifact: {artifact_id!r}")
        return row

    def all_rows(self) -> list[sqlite3.Row]:
        self._require_schema()
        try:
            return self._conn.execute(
                "SELECT artifact_id, artifact_type, recorded_at, content_hash, payload_json "
                "FROM model_risk_artifacts ORDER BY artifact_id"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise ModelRiskStoreError(f"model-risk scan failed: {exc}") from exc

    def put_runtime_invocation(self, invocation: RuntimeInvocation) -> str:
        self._require_schema()
        self._require_runtime_schema()
        payload_json = _payload_json(invocation)
        payload = invocation.model_dump(mode="json")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT event_hash, payload_json FROM model_risk_runtime_events "
                "WHERE invocation_id=?",
                (invocation.invocation_id,),
            ).fetchone()
            if existing is not None:
                self._conn.execute("ROLLBACK")
                if existing["payload_json"] == payload_json:
                    return str(existing["event_hash"])
                raise ImmutableModelRiskArtifact(
                    f"runtime invocation {invocation.invocation_id!r} already exists "
                    "with different content"
                )
            previous = self._conn.execute(
                "SELECT event_hash FROM model_risk_runtime_events "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = "" if previous is None else str(previous["event_hash"])
            event_hash = stable_hash(
                {"previous_hash": previous_hash, "invocation": payload}
            )
            self._conn.execute(
                "INSERT INTO model_risk_runtime_events "
                "(invocation_id, recorded_at, model_ref, prompt_ref, "
                "tool_manifest_ref, action, previous_hash, event_hash, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    invocation.invocation_id,
                    invocation.recorded_at,
                    invocation.model_ref,
                    invocation.prompt_ref,
                    invocation.tool_manifest_ref,
                    invocation.action,
                    previous_hash,
                    event_hash,
                    payload_json,
                ),
            )
            self._conn.execute("COMMIT")
            return event_hash
        except ImmutableModelRiskArtifact:
            raise
        except sqlite3.DatabaseError as exc:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
            raise ModelRiskStoreError(f"model-risk runtime write failed: {exc}") from exc

    def runtime_rows(self) -> list[sqlite3.Row]:
        self._require_runtime_schema()
        try:
            return self._conn.execute(
                "SELECT sequence, invocation_id, recorded_at, model_ref, prompt_ref, "
                "tool_manifest_ref, action, previous_hash, event_hash, payload_json "
                "FROM model_risk_runtime_events ORDER BY sequence"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise ModelRiskStoreError(f"model-risk runtime scan failed: {exc}") from exc


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
            raise TypeError(f"unsupported model-risk artifact: {type(artifact).__name__}")
        return self._record(artifact)

    def record_model_card(self, artifact: ModelCard) -> bool:
        return self._record(artifact)

    def record_prompt_template(self, artifact: PromptTemplateCard) -> bool:
        return self._record(artifact)

    def record_tool_manifest(self, artifact: ToolManifest) -> bool:
        return self._record(artifact)

    def record_offline_evaluation(self, artifact: OfflineEvaluation) -> bool:
        return self._record(artifact)

    def record_red_team_report(self, artifact: RedTeamReport) -> bool:
        return self._record(artifact)

    def record_challenger_review(self, artifact: ChallengerReview) -> bool:
        return self._record(artifact)

    def load_artifact(self, artifact_id: str) -> ModelRiskArtifact:
        row = self._store.get_row(artifact_id)
        artifact_type = _ARTIFACT_TYPES.get(row["artifact_type"])
        if artifact_type is None:
            raise ModelRiskStoreError(
                f"unknown model-risk artifact type: {row['artifact_type']!r}"
            )
        try:
            payload = json.loads(row["payload_json"])
            artifact = artifact_type.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ModelRiskStoreError(f"artifact {artifact_id!r} is corrupt: {exc}") from exc
        if artifact.artifact_id != artifact_id:
            raise ModelRiskStoreError(f"artifact {artifact_id!r} has an identity mismatch")
        if artifact.recorded_at != row["recorded_at"]:
            raise ModelRiskStoreError(f"artifact {artifact_id!r} has a timestamp mismatch")
        if artifact.content_hash != row["content_hash"]:
            raise ModelRiskStoreError(f"artifact {artifact_id!r} has a content-hash mismatch")
        return artifact

    def verify_artifact(self, artifact_id: str) -> bool:
        try:
            row = self._store.get_row(artifact_id)
            payload = json.loads(row["payload_json"])
            if stable_hash(payload) != row["content_hash"]:
                return False
            self.load_artifact(artifact_id)
            return True
        except (ModelRiskStoreError, json.JSONDecodeError, TypeError, ValueError):
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

    def record_runtime_invocation(self, invocation: RuntimeInvocation) -> str:
        if not isinstance(invocation, RuntimeInvocation):
            raise TypeError("runtime invocation must satisfy the canonical contract")
        return self._store.put_runtime_invocation(invocation)

    def runtime_rows(self) -> list[sqlite3.Row]:
        return self._store.runtime_rows()

    def verify_runtime_history(self) -> bool:
        try:
            rows = self._store.runtime_rows()
            if not rows:
                return False
            previous_hash = ""
            for row in rows:
                payload = json.loads(row["payload_json"])
                invocation = RuntimeInvocation.model_validate(payload)
                if (
                    invocation.invocation_id != row["invocation_id"]
                    or invocation.recorded_at != row["recorded_at"]
                    or invocation.model_ref != row["model_ref"]
                    or invocation.prompt_ref != row["prompt_ref"]
                    or invocation.tool_manifest_ref != row["tool_manifest_ref"]
                    or invocation.action != row["action"]
                    or row["previous_hash"] != previous_hash
                ):
                    return False
                expected_hash = stable_hash(
                    {"previous_hash": previous_hash, "invocation": payload}
                )
                if row["event_hash"] != expected_hash:
                    return False
                previous_hash = expected_hash
            return True
        except (ModelRiskStoreError, ValidationError, json.JSONDecodeError, TypeError, ValueError):
            return False

    def runtime_history_hash(self) -> str:
        if not self.verify_runtime_history():
            return ""
        rows = self._store.runtime_rows()
        return str(rows[-1]["event_hash"])


__all__ = [
    "GovernedModelRegistry",
    "ImmutableModelRiskArtifact",
    "ModelRiskStoreError",
    "SqliteModelRiskStore",
]
