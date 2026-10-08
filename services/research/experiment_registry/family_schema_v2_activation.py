"""Fail-closed, read-only verification of an exact family-schema v2 source.

The verifier never opens a database, starts or ends a transaction, performs
DDL/DML, changes ``user_version``, or hashes database-file bytes.  Its origin
fingerprint is a logical digest over the frozen catalog, catalog/primary-key
ordered rows, verified chain heads, and the owned AUTOINCREMENT sequences.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
from dataclasses import dataclass

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.research import AlphaState

from services.research.experiment_registry.evaluation_artifact import (
    validate_evaluation_artifact_write,
)
from services.research.experiment_registry.family_schema_v2_catalog import (
    EXACT_V2_INHERITED_SCHEMA_CATALOG,
    ExactV2OriginProjectionSpec,
    InheritedTableSpec,
)
from services.research.experiment_registry.family_schema_v2_semantics import (
    VerifiedV2SemanticState,
    verify_v2_semantics,
)
from services.research.ports.persistence import (
    EvaluationArtifactWrite,
    ExperimentStoreError,
)


_ORIGIN_CHAIN_HEAD_NAMES = frozenset(
    {
        "evaluation_audit",
        "evaluation_audit_dispatch",
        "experiment_event_dispatch",
        "experiment_event_outbox",
        "search_candidates",
        "search_evaluations",
        "search_events",
        "search_heartbeats",
        "search_recoveries",
    }
)


class ExactV2ActivationSourceError(ExperimentStoreError):
    """The supplied connection is not one closed, coherent exact-v2 source."""


@dataclass(frozen=True, slots=True)
class ExactV2ActivationSourceEvidence:
    catalog_id: str
    schema_version: int
    origin_fingerprint: str
    row_counts: tuple[tuple[str, int], ...]
    chain_heads: tuple[tuple[str, str], ...]
    owned_sequences: tuple[tuple[str, int | None], ...]


@dataclass(frozen=True, slots=True)
class ExactV2SearchWriterSourceEvidence:
    """Exact catalog and chain evidence required by ordinary v2 writers."""

    catalog_id: str
    schema_version: int
    row_counts: tuple[tuple[str, int], ...]
    chain_heads: tuple[tuple[str, str], ...]


def _validate_origin_projection(
    projection: ExactV2OriginProjectionSpec,
) -> ExactV2OriginProjectionSpec:
    catalog = EXACT_V2_INHERITED_SCHEMA_CATALOG
    if type(projection) is not ExactV2OriginProjectionSpec:
        raise ExactV2ActivationSourceError(
            "exact-v2 origin projection requires its exact typed specification"
        )
    if (
        projection.projection_id != "QUANTOS_EXACT_V2_LOGICAL_ORIGIN_V1"
        or projection.projection_version != 1
        or projection.digest_algorithm != "SHA256"
        or projection.schema_field_order != ("type", "name", "tbl_name", "sql")
        or projection.integer_encoding != "SIGNED_INT64_BIG_ENDIAN"
        or projection.real_encoding != "IEEE754_BINARY64_BIG_ENDIAN"
        or projection.text_encoding != "UTF8"
        or projection.blob_encoding != "RAW_BYTES"
        or projection.frame_encoding
        != "LABEL_ASCII_U32_BIG_ENDIAN_VALUE_U64_BIG_ENDIAN"
    ):
        raise ExactV2ActivationSourceError(
            "unknown exact-v2 origin projection ABI"
        )
    expected_schema_keys = {
        (entry.object_type, entry.name) for entry in catalog.sqlite_schema_entries
    }
    expected_tables = {table.table_name for table in catalog.tables}
    expected_primary_keys = {
        table.table_name: tuple(
            column.name
            for column in sorted(
                table.columns,
                key=lambda item: item.primary_key_ordinal or len(table.columns) + 1,
            )
            if column.primary_key_ordinal
        )
        for table in catalog.tables
    }
    actual_primary_keys = {
        item.table_name: item.columns for item in projection.primary_key_order
    }
    type_tags = {
        item.sqlite_storage_class: (item.tag, item.payload_encoding)
        for item in projection.type_tags
    }
    if (
        set(projection.schema_entry_order) != expected_schema_keys
        or set(projection.table_order) != expected_tables
        or len(projection.primary_key_order) != len(expected_primary_keys)
        or actual_primary_keys != expected_primary_keys
        or set(projection.sequence_order)
        != set(catalog.owned_sqlite_sequence_tables)
        or set(projection.chain_head_order) != _ORIGIN_CHAIN_HEAD_NAMES
        or type_tags
        != {
            "NULL": (b"N", "EMPTY"),
            "INTEGER": (b"I", "SIGNED_INT64_BIG_ENDIAN"),
            "REAL": (b"R", "IEEE754_BINARY64_BIG_ENDIAN"),
            "TEXT": (b"T", "UTF8"),
            "BLOB": (b"B", "RAW_BYTES"),
        }
    ):
        raise ExactV2ActivationSourceError(
            "exact-v2 origin projection does not close the frozen catalog"
        )
    return projection


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _scalar(connection: sqlite3.Connection, statement: str) -> object:
    row = connection.execute(statement).fetchone()
    if row is None or len(row) != 1:
        raise ExactV2ActivationSourceError(
            f"exact-v2 scalar read returned an invalid shape: {statement}"
        )
    return row[0]


def _verify_exact_namespace(connection: sqlite3.Connection) -> None:
    catalog = EXACT_V2_INHERITED_SCHEMA_CATALOG
    databases = tuple(
        str(row[1]) for row in connection.execute("PRAGMA database_list")
    )
    if databases not in (("main",), ("main", "temp")):
        raise ExactV2ActivationSourceError(
            f"exact-v2 source has unexpected database namespaces: {databases}"
        )
    if tuple(
        connection.execute(
            "SELECT type,name,tbl_name,sql FROM temp.sqlite_schema "
            "ORDER BY type,name"
        )
    ):
        raise ExactV2ActivationSourceError(
            "exact-v2 source has unexpected temp-schema objects"
        )
    if int(_scalar(connection, "PRAGMA user_version")) != catalog.schema_version:
        raise ExactV2ActivationSourceError("exact-v2 user_version mismatch")

    expected_schema = tuple(
        (item.object_type, item.name, item.table_name, item.sql)
        for item in catalog.sqlite_schema_entries
    )
    actual_schema = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM main.sqlite_schema "
            "ORDER BY type,name"
        )
    )
    if actual_schema != expected_schema:
        raise ExactV2ActivationSourceError(
            "exact-v2 sqlite_schema namespace mismatch"
        )

    for table in catalog.tables:
        table_name = _quote(table.table_name)
        actual_columns = tuple(
            (
                int(row[0]),
                str(row[1]),
                str(row[2]),
                bool(row[3]),
                row[4],
                int(row[5]),
                int(row[6]),
            )
            for row in connection.execute(f"PRAGMA table_xinfo({table_name})")
        )
        expected_columns = tuple(
            (
                ordinal,
                column.name,
                column.sql_type.value,
                column.not_null,
                None,
                column.primary_key_ordinal,
                0,
            )
            for ordinal, column in enumerate(table.columns)
        )
        if actual_columns != expected_columns:
            raise ExactV2ActivationSourceError(
                f"exact-v2 table_xinfo mismatch: {table.table_name}"
            )

        actual_indexes = []
        for index_row in connection.execute(f"PRAGMA index_list({table_name})"):
            index_name = str(index_row[1])
            key_rows = tuple(
                row
                for row in connection.execute(
                    f"PRAGMA index_xinfo({_quote(index_name)})"
                )
                if bool(row[5])
            )
            actual_indexes.append(
                (
                    table.table_name,
                    index_name,
                    bool(index_row[2]),
                    str(index_row[3]),
                    bool(index_row[4]),
                    tuple(row[2] for row in key_rows),
                    tuple(bool(row[3]) for row in key_rows),
                    tuple(str(row[4] or "BINARY").upper() for row in key_rows),
                )
            )
        expected_indexes = tuple(
            (
                item.table_name,
                item.name,
                item.unique,
                item.origin,
                item.partial,
                item.columns,
                item.descending,
                item.collations,
            )
            for item in catalog.indexes
            if item.table_name == table.table_name
        )
        if tuple(sorted(actual_indexes)) != tuple(sorted(expected_indexes)):
            raise ExactV2ActivationSourceError(
                f"exact-v2 index metadata mismatch: {table.table_name}"
            )
        if tuple(connection.execute(f"PRAGMA foreign_key_list({table_name})")):
            raise ExactV2ActivationSourceError(
                f"exact-v2 unexpected foreign key: {table.table_name}"
            )


def _read_typed_table_rows(
    connection: sqlite3.Connection,
    table: InheritedTableSpec,
    projection: ExactV2OriginProjectionSpec,
) -> tuple[tuple[object, ...], ...]:
    columns = ",".join(_quote(column.name) for column in table.columns)
    storage = ",".join(
        f"typeof({_quote(column.name)})" for column in table.columns
    )
    primary_key = next(
        (
            item.columns
            for item in projection.primary_key_order
            if item.table_name == table.table_name
        ),
        (),
    )
    if not primary_key:
        raise ExactV2ActivationSourceError(
            f"exact-v2 table lacks a catalog primary key: {table.table_name}"
        )
    order = ",".join(_quote(column) for column in primary_key)
    width = len(table.columns)
    rows: list[tuple[object, ...]] = []
    for raw in connection.execute(
        f"SELECT {columns},{storage} FROM {_quote(table.table_name)} "
        f"ORDER BY {order}"
    ):
        values = tuple(raw[:width])
        storage_classes = tuple(str(value) for value in raw[width:])
        for column, value, actual_storage in zip(
            table.columns,
            values,
            storage_classes,
            strict=True,
        ):
            if value is None:
                if column.not_null or column.primary_key_ordinal:
                    raise ExactV2ActivationSourceError(
                        f"exact-v2 NULL in required column: "
                        f"{table.table_name}.{column.name}"
                    )
                if actual_storage != "null":
                    raise ExactV2ActivationSourceError(
                        f"exact-v2 NULL storage mismatch: "
                        f"{table.table_name}.{column.name}"
                    )
                continue
            expected_storage = next(
                (
                    item.sqlite_storage_class.lower()
                    for item in projection.type_tags
                    if item.sqlite_storage_class == column.sql_type.value
                ),
                None,
            )
            if expected_storage is None or actual_storage != expected_storage:
                raise ExactV2ActivationSourceError(
                    f"exact-v2 storage class mismatch: "
                    f"{table.table_name}.{column.name}"
                )
        rows.append(values)
    return tuple(rows)


def _require_nonempty_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExactV2ActivationSourceError(f"{label} must be non-empty TEXT")
    return value


def _require_nonnegative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExactV2ActivationSourceError(
            f"{label} must be a non-negative INTEGER"
        )
    return value


def _require_finite_number(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ExactV2ActivationSourceError(f"{label} must be finite")
    return float(value)


def _validate_trials(rows: tuple[tuple[object, ...], ...]) -> None:
    for config_hash, first_seen_ts in rows:
        _require_nonempty_text(config_hash, "trials.config_hash")
        _require_nonnegative_integer(first_seen_ts, "trials.first_seen_ts")


def _validate_alpha_state(
    rows: tuple[tuple[object, ...], ...],
    trial_hashes: frozenset[object],
) -> None:
    allowed_states = frozenset(state.value for state in AlphaState)
    for (
        alpha_id,
        definition_json,
        state,
        returns_json,
        sharpe,
        pvalue,
    ) in rows:
        alpha_id = _require_nonempty_text(alpha_id, "alpha_state.alpha_id")
        definition_json = _require_nonempty_text(
            definition_json,
            "alpha_state.definition_json",
        )
        try:
            definition = AlphaDefinition.model_validate_json(definition_json)
        except Exception as exc:
            raise ExactV2ActivationSourceError(
                f"alpha_state definition is invalid: {alpha_id}"
            ) from exc
        if definition.alpha_id != alpha_id:
            raise ExactV2ActivationSourceError(
                f"alpha_state definition identity mismatch: {alpha_id}"
            )
        if definition.definition_hash not in trial_hashes:
            raise ExactV2ActivationSourceError(
                f"alpha_state definition lacks trial provenance: {alpha_id}"
            )
        if state not in allowed_states:
            raise ExactV2ActivationSourceError(
                f"alpha_state lifecycle value is invalid: {alpha_id}"
            )
        try:
            returns = json.loads(
                _require_nonempty_text(returns_json, "alpha_state.returns_json")
            )
        except (TypeError, json.JSONDecodeError) as exc:
            raise ExactV2ActivationSourceError(
                f"alpha_state returns are invalid JSON: {alpha_id}"
            ) from exc
        if not isinstance(returns, list):
            raise ExactV2ActivationSourceError(
                f"alpha_state returns are not a list: {alpha_id}"
            )
        for index, value in enumerate(returns):
            _require_finite_number(
                value,
                f"alpha_state returns[{index}] for {alpha_id}",
            )
        if sharpe is not None:
            _require_finite_number(sharpe, f"alpha_state.sharpe for {alpha_id}")
        if pvalue is not None:
            normalized = _require_finite_number(
                pvalue,
                f"alpha_state.pvalue for {alpha_id}",
            )
            if not 0.0 <= normalized <= 1.0:
                raise ExactV2ActivationSourceError(
                    f"alpha_state.pvalue is outside [0,1]: {alpha_id}"
                )


def _validate_evaluations(rows: tuple[tuple[object, ...], ...]) -> None:
    for row in rows:
        validate_evaluation_artifact_write(
            EvaluationArtifactWrite(
                run_id=row[0],
                alpha_id=row[1],
                dataset_version=row[2],
                recorded_ts=row[3],
                verdict=row[4],
                content_hash=row[5],
                evaluation_json=row[6],
                audit_intent=None,
            )
        )


def _json_tuple_key(value: object, label: str) -> str:
    if not isinstance(value, list) or not value:
        raise ExactV2ActivationSourceError(f"{label} must be a non-empty list")
    for item in value:
        if isinstance(item, (dict, list)):
            raise ExactV2ActivationSourceError(
                f"{label} contains an unhashable tuple element"
            )
        if isinstance(item, float) and not math.isfinite(item):
            raise ExactV2ActivationSourceError(
                f"{label} contains a non-finite tuple element"
            )
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _validate_bandit_snapshot(snapshot: object) -> None:
    if not isinstance(snapshot, dict):
        raise ExactV2ActivationSourceError(
            "bandit_state learned snapshot must be an object"
        )
    allowed_keys = {
        "version",
        "pulls",
        "reward_sum",
        "n_evaluated",
        "proposed",
        "regret_history",
    }
    required_keys = {"pulls", "reward_sum", "n_evaluated", "proposed"}
    if not required_keys.issubset(snapshot) or not set(snapshot).issubset(
        allowed_keys
    ):
        raise ExactV2ActivationSourceError(
            "bandit_state learned snapshot shape is invalid"
        )
    version = snapshot.get("version", 1)
    if version != 1 or isinstance(version, bool):
        raise ExactV2ActivationSourceError(
            "bandit_state learned snapshot version is invalid"
        )
    n_evaluated = _require_nonnegative_integer(
        snapshot["n_evaluated"],
        "bandit_state.snapshot.n_evaluated",
    )

    arm_maps: list[dict[str, float]] = []
    for field in ("pulls", "reward_sum"):
        entries = snapshot[field]
        if not isinstance(entries, list):
            raise ExactV2ActivationSourceError(
                f"bandit_state.snapshot.{field} must be a list"
            )
        values: dict[str, float] = {}
        for entry in entries:
            if not isinstance(entry, list) or len(entry) != 2:
                raise ExactV2ActivationSourceError(
                    f"bandit_state.snapshot.{field} entry is invalid"
                )
            key = _json_tuple_key(
                entry[0],
                f"bandit_state.snapshot.{field}.arm",
            )
            if key in values:
                raise ExactV2ActivationSourceError(
                    f"bandit_state.snapshot.{field} has a duplicate arm"
                )
            if field == "pulls":
                count = _require_nonnegative_integer(
                    entry[1],
                    "bandit_state.snapshot.pulls.count",
                )
                if count == 0 or count > n_evaluated:
                    raise ExactV2ActivationSourceError(
                        "bandit_state pull count is inconsistent"
                    )
                values[key] = float(count)
            else:
                values[key] = _require_finite_number(
                    entry[1],
                    "bandit_state.snapshot.reward_sum.value",
                )
        arm_maps.append(values)
    if arm_maps[0].keys() != arm_maps[1].keys():
        raise ExactV2ActivationSourceError(
            "bandit_state pulls and rewards cover different arms"
        )

    proposed = snapshot["proposed"]
    if not isinstance(proposed, list):
        raise ExactV2ActivationSourceError(
            "bandit_state.snapshot.proposed must be a list"
        )
    proposed_keys = [
        _json_tuple_key(item, "bandit_state.snapshot.proposed item")
        for item in proposed
    ]
    if len(set(proposed_keys)) != len(proposed_keys):
        raise ExactV2ActivationSourceError(
            "bandit_state.snapshot.proposed has duplicates"
        )

    if "regret_history" in snapshot:
        regret_history = snapshot["regret_history"]
        if not isinstance(regret_history, list) or len(regret_history) != n_evaluated:
            raise ExactV2ActivationSourceError(
                "bandit_state regret history length is inconsistent"
            )
        for value in regret_history:
            if value is not None:
                _require_finite_number(
                    value,
                    "bandit_state.snapshot.regret_history value",
                )


def _validate_bandit(rows: tuple[tuple[object, ...], ...]) -> None:
    if len(rows) > 1:
        raise ExactV2ActivationSourceError("bandit_state has more than one row")
    if not rows:
        return
    row_id, snapshot_json = rows[0]
    if row_id != 1:
        raise ExactV2ActivationSourceError("bandit_state id must be 1")
    try:
        payload = json.loads(
            _require_nonempty_text(snapshot_json, "bandit_state.snapshot_json")
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise ExactV2ActivationSourceError(
            "bandit_state snapshot is invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ExactV2ActivationSourceError(
            "bandit_state snapshot must be a JSON object"
        )
    snapshot: object = payload
    if "schema_version" in payload:
        if payload["schema_version"] != 2 or isinstance(
            payload["schema_version"], bool
        ):
            raise ExactV2ActivationSourceError(
                "bandit_state schema_version must be 2"
            )
        if not isinstance(payload.get("dataset_version", ""), str):
            raise ExactV2ActivationSourceError(
                "bandit_state dataset_version must be text"
            )
        if "snapshot" not in payload:
            raise ExactV2ActivationSourceError(
                "bandit_state v2 payload lacks snapshot"
            )
        if set(payload) != {"schema_version", "dataset_version", "snapshot"}:
            raise ExactV2ActivationSourceError(
                "bandit_state v2 envelope shape is invalid"
            )
        snapshot = payload["snapshot"]
    _validate_bandit_snapshot(snapshot)


def _validate_search_domain(
    tables: dict[str, tuple[tuple[object, ...], ...]],
) -> None:
    trials = {row[0]: row[1] for row in tables["trials"]}
    for row in tables["search_attempt_events"]:
        for value, label in zip(
            row[1:5],
            ("search_id", "search_kind", "source", "dataset_version"),
            strict=True,
        ):
            _require_nonempty_text(value, f"search_attempt_events.{label}")
        if row[5] not in {"STARTED", "FAILED", "COMPLETED"}:
            raise ExactV2ActivationSourceError(
                "search_attempt_events.status is invalid"
            )
        _require_nonnegative_integer(row[6], "search_attempt_events.recorded_at")
    for row in tables["search_candidates"]:
        _require_nonempty_text(row[1], "search_candidates.search_id")
        config_hash = _require_nonempty_text(
            row[2], "search_candidates.config_hash"
        )
        if row[3] != "REGISTERED":
            raise ExactV2ActivationSourceError(
                "search_candidates.status is invalid"
            )
        registered_at = _require_nonnegative_integer(
            row[4], "search_candidates.registered_at"
        )
        if config_hash not in trials or trials[config_hash] > registered_at:
            raise ExactV2ActivationSourceError(
                "search candidate trial provenance is invalid"
            )
    for table_name, time_columns in (
        ("search_attempt_heartbeats", (2,)),
        ("search_attempt_recoveries", (3, 4, 5)),
        ("search_evaluations", (4,)),
        ("evaluation_audit_outbox", (5,)),
        ("evaluation_audit_dispatch", (2,)),
        ("experiment_event_dispatch", (2,)),
    ):
        for row in tables[table_name]:
            for index in time_columns:
                _require_nonnegative_integer(
                    row[index],
                    f"{table_name}.{index}",
                )

    evaluations = {row[0]: row for row in tables["evaluations"]}
    for row in tables["search_evaluations"]:
        evaluation = evaluations.get(row[3])
        if evaluation is None or row[4] != evaluation[3]:
            raise ExactV2ActivationSourceError(
                "search evaluation timestamp reference is invalid"
            )

    audit_by_event = {row[2]: row for row in tables["evaluation_audit_outbox"]}
    for event_id, audit in audit_by_event.items():
        evaluation = evaluations.get(audit[1])
        if evaluation is None:
            raise ExactV2ActivationSourceError(
                "evaluation audit run reference is invalid"
            )
        try:
            metadata = json.loads(audit[7])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ExactV2ActivationSourceError(
                "evaluation audit metadata is invalid JSON"
            ) from exc
        expected = {
            "run_id": evaluation[0],
            "content_hash": evaluation[5],
            "verdict": evaluation[4],
        }
        if (
            event_id != f"exp-{evaluation[0]}"
            or audit[3] != "experiment-registry"
            or audit[4] != "EXPERIMENT_RECORDED"
            or audit[5] != evaluation[3]
            or audit[6] != evaluation[1]
            or metadata != expected
        ):
            raise ExactV2ActivationSourceError(
                "evaluation audit intent is not the exact v2 derivation"
            )
    for row in tables["evaluation_audit_dispatch"]:
        _require_nonempty_text(
            row[3],
            "evaluation_audit_dispatch.audit_entry_hash",
        )


def _read_and_verify_sequences(
    connection: sqlite3.Connection,
    tables: dict[str, tuple[tuple[object, ...], ...]],
    projection: ExactV2OriginProjectionSpec,
) -> tuple[tuple[str, int | None], ...]:
    catalog = EXACT_V2_INHERITED_SCHEMA_CATALOG
    raw_rows = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT name,seq,typeof(name),typeof(seq) FROM sqlite_sequence "
            "ORDER BY name"
        )
    )
    actual: dict[str, int] = {}
    owned = frozenset(catalog.owned_sqlite_sequence_tables)
    for name, sequence, name_type, sequence_type in raw_rows:
        if (
            name_type != "text"
            or sequence_type != "integer"
            or not isinstance(name, str)
            or name not in owned
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
            or name in actual
        ):
            raise ExactV2ActivationSourceError(
                "sqlite_sequence contains unknown or invalid state"
            )
        actual[name] = sequence

    evidence: list[tuple[str, int | None]] = []
    by_name = {table.table_name: table for table in catalog.tables}
    for table_name in projection.sequence_order:
        table = by_name[table_name]
        primary_key = next(
            column.name for column in table.columns if column.primary_key_ordinal == 1
        )
        maximum = max((int(row[0]) for row in tables[table_name]), default=None)
        sequence = actual.get(table_name)
        if sequence != maximum:
            raise ExactV2ActivationSourceError(
                f"sqlite_sequence does not match {table_name}.{primary_key}"
            )
        evidence.append((table_name, sequence))
    return tuple(evidence)


def _chain_heads(
    semantic: VerifiedV2SemanticState,
    projection: ExactV2OriginProjectionSpec,
) -> tuple[tuple[str, str], ...]:
    available = {
        "evaluation_audit": semantic.evaluation_audit.audit_head,
        "evaluation_audit_dispatch": semantic.evaluation_audit.dispatch_head,
        "experiment_event_dispatch": semantic.event_outbox.dispatch_head,
        "experiment_event_outbox": semantic.event_outbox.outbox_head,
        "search_candidates": semantic.search.candidate_head,
        "search_evaluations": semantic.search.evaluation_link_head,
        "search_events": semantic.search.event_head,
        "search_heartbeats": semantic.search.heartbeat_head,
        "search_recoveries": semantic.search.recovery_head,
    }
    return tuple((name, available[name]) for name in projection.chain_head_order)


def _typed_bytes(
    value: object,
    projection: ExactV2OriginProjectionSpec | None = None,
) -> bytes:
    projection = _validate_origin_projection(
        EXACT_V2_INHERITED_SCHEMA_CATALOG.origin_projection
        if projection is None
        else projection
    )
    tags = {
        item.sqlite_storage_class: item.tag for item in projection.type_tags
    }
    if value is None:
        return tags["NULL"]
    if isinstance(value, bool):
        raise ExactV2ActivationSourceError("BOOLEAN is not a SQLite v2 value type")
    if isinstance(value, int):
        try:
            return tags["INTEGER"] + struct.pack(">q", value)
        except struct.error as exc:
            raise ExactV2ActivationSourceError(
                "INTEGER lies outside SQLite's signed 64-bit range"
            ) from exc
    if isinstance(value, float):
        return tags["REAL"] + struct.pack(">d", value)
    if isinstance(value, str):
        return tags["TEXT"] + value.encode("utf-8")
    if isinstance(value, bytes):
        return tags["BLOB"] + value
    raise ExactV2ActivationSourceError(
        f"unsupported logical origin value type: {type(value).__name__}"
    )


def _feed(
    digest: object,
    label: str,
    value: object,
    projection: ExactV2OriginProjectionSpec,
) -> None:
    label_bytes = label.encode("ascii")
    value_bytes = _typed_bytes(value, projection)
    digest.update(struct.pack(">I", len(label_bytes)))
    digest.update(label_bytes)
    digest.update(struct.pack(">Q", len(value_bytes)))
    digest.update(value_bytes)


def _origin_fingerprint(
    *,
    tables: dict[str, tuple[tuple[object, ...], ...]],
    row_counts: tuple[tuple[str, int], ...],
    chain_heads: tuple[tuple[str, str], ...],
    sequences: tuple[tuple[str, int | None], ...],
    projection: ExactV2OriginProjectionSpec | None = None,
) -> str:
    catalog = EXACT_V2_INHERITED_SCHEMA_CATALOG
    projection = _validate_origin_projection(
        catalog.origin_projection if projection is None else projection
    )
    digest = hashlib.sha256()
    digest.update(projection.domain_separator)
    _feed(digest, "projection_id", projection.projection_id, projection)
    _feed(digest, "projection_version", projection.projection_version, projection)
    _feed(digest, "catalog_id", catalog.catalog_id, projection)
    _feed(digest, "schema_version", catalog.schema_version, projection)
    schema_by_key = {
        (entry.object_type, entry.name): entry
        for entry in catalog.sqlite_schema_entries
    }
    for key in projection.schema_entry_order:
        entry = schema_by_key[key]
        values = {
            "type": entry.object_type,
            "name": entry.name,
            "tbl_name": entry.table_name,
            "sql": entry.sql,
        }
        for field_name in projection.schema_field_order:
            _feed(
                digest,
                f"schema.{field_name}",
                values[field_name],
                projection,
            )
    tables_by_name = {table.table_name: table for table in catalog.tables}
    for table_name in projection.table_order:
        table = tables_by_name[table_name]
        _feed(digest, "table", table.table_name, projection)
        _feed(
            digest,
            "row_count",
            len(tables[table.table_name]),
            projection,
        )
        for row in tables[table.table_name]:
            _feed(digest, "row", table.table_name, projection)
            for column, value in zip(table.columns, row, strict=True):
                _feed(digest, "column", column.name, projection)
                _feed(digest, "value", value, projection)
    for table_name, count in row_counts:
        _feed(digest, "count.table", table_name, projection)
        _feed(digest, "count.value", count, projection)
    for chain_name, head in chain_heads:
        _feed(digest, "chain.name", chain_name, projection)
        _feed(digest, "chain.head", head, projection)
    for table_name, sequence in sequences:
        _feed(digest, "sequence.table", table_name, projection)
        _feed(digest, "sequence.value", sequence, projection)
    return digest.hexdigest()


def _verify_exact_v2_activation_source(
    connection: sqlite3.Connection,
    projection: ExactV2OriginProjectionSpec | None = None,
) -> ExactV2ActivationSourceEvidence:
    catalog = EXACT_V2_INHERITED_SCHEMA_CATALOG
    projection = _validate_origin_projection(
        catalog.origin_projection if projection is None else projection
    )
    data_version = int(_scalar(connection, "PRAGMA data_version"))
    schema_version = int(_scalar(connection, "PRAGMA schema_version"))
    _verify_exact_namespace(connection)

    integrity = tuple(
        tuple(row) for row in connection.execute("PRAGMA integrity_check")
    )
    if integrity != (("ok",),):
        raise ExactV2ActivationSourceError("exact-v2 integrity_check failed")
    if tuple(connection.execute("PRAGMA foreign_key_check")):
        raise ExactV2ActivationSourceError("exact-v2 foreign_key_check failed")

    tables_by_name = {table.table_name: table for table in catalog.tables}
    tables = {
        table_name: _read_typed_table_rows(
            connection,
            tables_by_name[table_name],
            projection,
        )
        for table_name in projection.table_order
    }
    _validate_trials(tables["trials"])
    _validate_alpha_state(
        tables["alpha_state"],
        frozenset(row[0] for row in tables["trials"]),
    )
    _validate_evaluations(tables["evaluations"])
    _validate_bandit(tables["bandit_state"])
    _validate_search_domain(tables)
    semantic = verify_v2_semantics(connection)
    sequences = _read_and_verify_sequences(connection, tables, projection)

    row_counts = tuple(
        (table_name, len(tables[table_name]))
        for table_name in projection.table_order
    )
    chain_heads = _chain_heads(semantic, projection)
    if (
        int(_scalar(connection, "PRAGMA data_version")) != data_version
        or int(_scalar(connection, "PRAGMA schema_version")) != schema_version
    ):
        raise ExactV2ActivationSourceError(
            "exact-v2 source changed during read-only verification"
        )
    return ExactV2ActivationSourceEvidence(
        catalog_id=catalog.catalog_id,
        schema_version=catalog.schema_version,
        origin_fingerprint=_origin_fingerprint(
            tables=tables,
            row_counts=row_counts,
            chain_heads=chain_heads,
            sequences=sequences,
            projection=projection,
        ),
        row_counts=row_counts,
        chain_heads=chain_heads,
        owned_sequences=sequences,
    )


def verify_exact_v2_activation_source(
    connection: sqlite3.Connection,
) -> ExactV2ActivationSourceEvidence:
    """Verify and fingerprint one already-open exact-v2 SQLite source.

    Any missing, extra, malformed, internally inconsistent, or concurrently
    changing source fails closed.  The supplied connection remains caller-owned.
    """
    if type(connection) is not sqlite3.Connection:
        raise TypeError("connection must be an exact sqlite3.Connection")
    try:
        return _verify_exact_v2_activation_source(connection)
    except ExactV2ActivationSourceError:
        raise
    except Exception as exc:
        raise ExactV2ActivationSourceError(
            "exact-v2 activation source verification failed"
        ) from exc


def verify_exact_v2_search_writer_source(
    connection: sqlite3.Connection,
) -> ExactV2SearchWriterSourceEvidence:
    """Verify the frozen v2 catalog and all append-only chains for a writer.

    Unlike activation verification, this deliberately does not impose the
    one-time migration's stronger domain-admission and logical-origin rules on
    ordinary historical writes.  It owns no transaction or connection state.
    """

    if type(connection) is not sqlite3.Connection:
        raise TypeError("connection must be an exact sqlite3.Connection")
    initial_transaction = connection.in_transaction
    initial_total_changes = connection.total_changes
    try:
        _verify_exact_namespace(connection)
        integrity = tuple(
            tuple(row) for row in connection.execute("PRAGMA integrity_check")
        )
        if integrity != (("ok",),):
            raise ExactV2ActivationSourceError(
                "exact-v2 search-writer integrity_check failed"
            )
        if tuple(connection.execute("PRAGMA foreign_key_check")):
            raise ExactV2ActivationSourceError(
                "exact-v2 search-writer foreign_key_check failed"
            )
        semantic = verify_v2_semantics(connection)
        catalog = EXACT_V2_INHERITED_SCHEMA_CATALOG
        row_counts = tuple(
            (
                table.table_name,
                int(
                    _scalar(
                        connection,
                        f"SELECT COUNT(*) FROM {_quote(table.table_name)}",
                    )
                ),
            )
            for table in catalog.tables
        )
        if (
            connection.in_transaction is not initial_transaction
            or connection.total_changes != initial_total_changes
            or int(_scalar(connection, "PRAGMA user_version")) != 2
        ):
            raise ExactV2ActivationSourceError(
                "exact-v2 search-writer verifier changed caller-owned state"
            )
        return ExactV2SearchWriterSourceEvidence(
            catalog_id=catalog.catalog_id,
            schema_version=2,
            row_counts=row_counts,
            chain_heads=_chain_heads(semantic, catalog.origin_projection),
        )
    except ExactV2ActivationSourceError:
        raise
    except Exception as exc:
        raise ExactV2ActivationSourceError(
            "exact-v2 search-writer verification failed"
        ) from exc


__all__ = [
    "ExactV2ActivationSourceError",
    "ExactV2ActivationSourceEvidence",
    "ExactV2SearchWriterSourceEvidence",
    "verify_exact_v2_activation_source",
    "verify_exact_v2_search_writer_source",
]
