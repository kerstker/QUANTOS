"""Frozen exact-v2 schema catalog inherited by family schema v3.

The Pass-M composer uses this data-only catalog to close references to legacy
evaluation/search authority without importing the SQLite store or opening a
database.  A separate contract test compares every entry with a fresh exact-v2
store.
"""

from __future__ import annotations

from dataclasses import dataclass

from .family_schema_spec import Collation, SortDirection, SqlType


@dataclass(frozen=True, slots=True)
class InheritedColumnSpec:
    name: str
    sql_type: SqlType
    not_null: bool
    primary_key_ordinal: int = 0


@dataclass(frozen=True, slots=True)
class InheritedUniqueKeySpec:
    columns: tuple[str, ...]
    directions: tuple[SortDirection, ...]
    collations: tuple[Collation, ...]


@dataclass(frozen=True, slots=True)
class InheritedTableSpec:
    table_name: str
    columns: tuple[InheritedColumnSpec, ...]
    unique_keys: tuple[InheritedUniqueKeySpec, ...]

    @property
    def unique_parent_keys(self) -> tuple[tuple[str, ...], ...]:
        return tuple(key.columns for key in self.unique_keys)


@dataclass(frozen=True, slots=True)
class InheritedObjectSpec:
    object_type: str
    name: str
    table_name: str


@dataclass(frozen=True, slots=True)
class InheritedSqliteSchemaEntrySpec:
    object_type: str
    name: str
    table_name: str
    sql: str | None


@dataclass(frozen=True, slots=True)
class InheritedIndexSpec:
    table_name: str
    name: str
    unique: bool
    origin: str
    partial: bool
    columns: tuple[str, ...]
    descending: tuple[bool, ...]
    collations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExactV2OriginPrimaryKeyOrderSpec:
    table_name: str
    columns: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.table_name) is not str or not self.table_name:
            raise ValueError("origin primary-key table must be non-empty text")
        if (
            type(self.columns) is not tuple
            or not self.columns
            or any(type(column) is not str or not column for column in self.columns)
            or len(set(self.columns)) != len(self.columns)
        ):
            raise ValueError("origin primary-key columns must be unique text")


@dataclass(frozen=True, slots=True)
class ExactV2OriginTypeTagSpec:
    sqlite_storage_class: str
    tag: bytes
    payload_encoding: str

    def __post_init__(self) -> None:
        if type(self.sqlite_storage_class) is not str or not self.sqlite_storage_class:
            raise ValueError("origin SQLite storage class must be non-empty text")
        if type(self.tag) is not bytes or len(self.tag) != 1:
            raise ValueError("origin type tag must be exactly one byte")
        if type(self.payload_encoding) is not str or not self.payload_encoding:
            raise ValueError("origin payload encoding must be non-empty text")


@dataclass(frozen=True, slots=True)
class ExactV2OriginProjectionSpec:
    projection_id: str
    projection_version: int
    domain_separator: bytes
    digest_algorithm: str
    schema_field_order: tuple[str, ...]
    schema_entry_order: tuple[tuple[str, str], ...]
    table_order: tuple[str, ...]
    primary_key_order: tuple[ExactV2OriginPrimaryKeyOrderSpec, ...]
    sequence_order: tuple[str, ...]
    chain_head_order: tuple[str, ...]
    type_tags: tuple[ExactV2OriginTypeTagSpec, ...]
    integer_encoding: str
    real_encoding: str
    text_encoding: str
    blob_encoding: str
    frame_encoding: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.projection_id, "origin projection ID"),
            (self.digest_algorithm, "origin digest algorithm"),
            (self.integer_encoding, "origin INTEGER encoding"),
            (self.real_encoding, "origin REAL encoding"),
            (self.text_encoding, "origin TEXT encoding"),
            (self.blob_encoding, "origin BLOB encoding"),
            (self.frame_encoding, "origin frame encoding"),
        ):
            if type(value) is not str or not value:
                raise ValueError(f"{label} must be non-empty text")
        if type(self.projection_version) is not int or self.projection_version < 1:
            raise ValueError("origin projection version must be positive")
        if type(self.domain_separator) is not bytes or not self.domain_separator:
            raise ValueError("origin domain separator must be non-empty bytes")
        for values, label in (
            (self.schema_field_order, "origin schema fields"),
            (self.schema_entry_order, "origin schema entries"),
            (self.table_order, "origin tables"),
            (self.primary_key_order, "origin primary keys"),
            (self.sequence_order, "origin sequences"),
            (self.chain_head_order, "origin chain heads"),
            (self.type_tags, "origin type tags"),
        ):
            if type(values) is not tuple or not values or len(set(values)) != len(values):
                raise ValueError(f"{label} must be a non-empty unique tuple")
        if any(type(value) is not str or not value for value in self.schema_field_order):
            raise ValueError("origin schema fields must be non-empty text")
        if any(
            type(value) is not tuple
            or len(value) != 2
            or any(type(item) is not str or not item for item in value)
            for value in self.schema_entry_order
        ):
            raise ValueError("origin schema entry order must contain text pairs")
        for values, label in (
            (self.table_order, "origin table"),
            (self.sequence_order, "origin sequence"),
            (self.chain_head_order, "origin chain head"),
        ):
            if any(type(value) is not str or not value for value in values):
                raise ValueError(f"{label} names must be non-empty text")
        if any(
            type(value) is not ExactV2OriginPrimaryKeyOrderSpec
            for value in self.primary_key_order
        ):
            raise TypeError("origin primary-key order requires exact typed rows")
        if any(type(value) is not ExactV2OriginTypeTagSpec for value in self.type_tags):
            raise TypeError("origin type tags require exact typed rows")


@dataclass(frozen=True, slots=True)
class ExactV2InheritedSchemaCatalog:
    catalog_id: str
    schema_version: int
    tables: tuple[InheritedTableSpec, ...]
    objects: tuple[InheritedObjectSpec, ...]
    sqlite_schema_entries: tuple[InheritedSqliteSchemaEntrySpec, ...]
    indexes: tuple[InheritedIndexSpec, ...]
    owned_sqlite_sequence_tables: tuple[str, ...]
    origin_projection: ExactV2OriginProjectionSpec


def _c(
    name: str,
    sql_type: SqlType,
    *,
    not_null: bool = True,
    primary_key_ordinal: int = 0,
) -> InheritedColumnSpec:
    return InheritedColumnSpec(
        name=name,
        sql_type=sql_type,
        not_null=not_null,
        primary_key_ordinal=primary_key_ordinal,
    )


def _t(
    table_name: str,
    columns: tuple[InheritedColumnSpec, ...],
    unique_parent_keys: tuple[tuple[str, ...], ...],
) -> InheritedTableSpec:
    return InheritedTableSpec(
        table_name=table_name,
        columns=columns,
        unique_keys=tuple(
            InheritedUniqueKeySpec(
                columns=key,
                directions=(SortDirection.ASC,) * len(key),
                collations=(Collation.BINARY,) * len(key),
            )
            for key in unique_parent_keys
        ),
    )


_TABLES = (
    _t(
        "alpha_state",
        (
            _c("alpha_id", SqlType.TEXT, not_null=False, primary_key_ordinal=1),
            _c("definition_json", SqlType.TEXT),
            _c("state", SqlType.TEXT),
            _c("returns_json", SqlType.TEXT),
            _c("sharpe", SqlType.REAL, not_null=False),
            _c("pvalue", SqlType.REAL, not_null=False),
        ),
        (("alpha_id",),),
    ),
    _t(
        "bandit_state",
        (
            _c("id", SqlType.INTEGER, not_null=False, primary_key_ordinal=1),
            _c("snapshot_json", SqlType.TEXT),
        ),
        (("id",),),
    ),
    _t(
        "evaluation_audit_dispatch",
        (
            _c("dispatch_seq", SqlType.INTEGER, not_null=False, primary_key_ordinal=1),
            _c("event_id", SqlType.TEXT),
            _c("dispatched_at", SqlType.INTEGER),
            _c("audit_entry_hash", SqlType.TEXT),
            _c("prev_record_hash", SqlType.TEXT),
            _c("record_hash", SqlType.TEXT),
        ),
        (("dispatch_seq",), ("event_id",), ("record_hash",)),
    ),
    _t(
        "evaluation_audit_outbox",
        (
            _c("audit_seq", SqlType.INTEGER, not_null=False, primary_key_ordinal=1),
            _c("run_id", SqlType.TEXT),
            _c("event_id", SqlType.TEXT),
            _c("actor", SqlType.TEXT),
            _c("action", SqlType.TEXT),
            _c("timestamp", SqlType.INTEGER),
            _c("target", SqlType.TEXT),
            _c("metadata_json", SqlType.TEXT),
            _c("evaluation_content_hash", SqlType.TEXT),
            _c("prev_record_hash", SqlType.TEXT),
            _c("record_hash", SqlType.TEXT),
        ),
        (("audit_seq",), ("event_id",), ("record_hash",), ("run_id",)),
    ),
    _t(
        "evaluations",
        (
            _c("run_id", SqlType.TEXT, not_null=False, primary_key_ordinal=1),
            _c("alpha_id", SqlType.TEXT),
            _c("dataset_version", SqlType.TEXT),
            _c("recorded_ts", SqlType.INTEGER),
            _c("verdict", SqlType.TEXT),
            _c("content_hash", SqlType.TEXT),
            _c("evaluation_json", SqlType.TEXT),
        ),
        (("run_id",),),
    ),
    _t(
        "experiment_event_dispatch",
        (
            _c("dispatch_seq", SqlType.INTEGER, not_null=False, primary_key_ordinal=1),
            _c("outbox_id", SqlType.TEXT),
            _c("dispatched_at", SqlType.INTEGER),
            _c("event_repro_hash", SqlType.TEXT),
            _c("prev_record_hash", SqlType.TEXT),
            _c("record_hash", SqlType.TEXT),
        ),
        (("dispatch_seq",), ("outbox_id",)),
    ),
    _t(
        "experiment_event_outbox",
        (
            _c("outbox_seq", SqlType.INTEGER, not_null=False, primary_key_ordinal=1),
            _c("outbox_id", SqlType.TEXT),
            _c("event_json", SqlType.TEXT),
            _c("correlation_id", SqlType.TEXT),
            _c("causation_id", SqlType.TEXT),
            _c("repro_hash", SqlType.TEXT),
            _c("prev_record_hash", SqlType.TEXT),
            _c("record_hash", SqlType.TEXT),
        ),
        (("outbox_id",), ("outbox_seq",)),
    ),
    _t(
        "search_attempt_events",
        (
            _c("event_seq", SqlType.INTEGER, not_null=False, primary_key_ordinal=1),
            _c("search_id", SqlType.TEXT),
            _c("search_kind", SqlType.TEXT),
            _c("source", SqlType.TEXT),
            _c("dataset_version", SqlType.TEXT),
            _c("status", SqlType.TEXT),
            _c("recorded_at", SqlType.INTEGER),
            _c("prev_event_hash", SqlType.TEXT),
            _c("event_hash", SqlType.TEXT),
        ),
        (("event_hash",), ("event_seq",)),
    ),
    _t(
        "search_attempt_heartbeats",
        (
            _c("heartbeat_seq", SqlType.INTEGER, not_null=False, primary_key_ordinal=1),
            _c("search_id", SqlType.TEXT),
            _c("recorded_at", SqlType.INTEGER),
            _c("prev_heartbeat_hash", SqlType.TEXT),
            _c("heartbeat_hash", SqlType.TEXT),
        ),
        (
            ("heartbeat_hash",),
            ("heartbeat_seq",),
            ("search_id", "recorded_at"),
        ),
    ),
    _t(
        "search_attempt_recoveries",
        (
            _c("recovery_seq", SqlType.INTEGER, not_null=False, primary_key_ordinal=1),
            _c("search_id", SqlType.TEXT),
            _c("reason", SqlType.TEXT),
            _c("timeout_ms", SqlType.INTEGER),
            _c("last_activity_at", SqlType.INTEGER),
            _c("recovered_at", SqlType.INTEGER),
            _c("terminal_event_hash", SqlType.TEXT),
            _c("prev_recovery_hash", SqlType.TEXT),
            _c("recovery_hash", SqlType.TEXT),
        ),
        (
            ("recovery_hash",),
            ("recovery_seq",),
            ("search_id",),
            ("terminal_event_hash",),
        ),
    ),
    _t(
        "search_candidates",
        (
            _c("candidate_seq", SqlType.INTEGER, not_null=False, primary_key_ordinal=1),
            _c("search_id", SqlType.TEXT),
            _c("config_hash", SqlType.TEXT),
            _c("status", SqlType.TEXT),
            _c("registered_at", SqlType.INTEGER),
            _c("prev_candidate_hash", SqlType.TEXT),
            _c("candidate_hash", SqlType.TEXT),
        ),
        (
            ("candidate_hash",),
            ("candidate_seq",),
            ("search_id", "config_hash"),
        ),
    ),
    _t(
        "search_evaluations",
        (
            _c("link_seq", SqlType.INTEGER, not_null=False, primary_key_ordinal=1),
            _c("search_id", SqlType.TEXT),
            _c("config_hash", SqlType.TEXT),
            _c("run_id", SqlType.TEXT),
            _c("recorded_at", SqlType.INTEGER),
            _c("evaluation_content_hash", SqlType.TEXT),
            _c("prev_link_hash", SqlType.TEXT),
            _c("link_hash", SqlType.TEXT),
        ),
        (("link_hash",), ("link_seq",), ("search_id", "run_id")),
    ),
    _t(
        "trials",
        (
            _c("config_hash", SqlType.TEXT, not_null=False, primary_key_ordinal=1),
            _c("first_seen_ts", SqlType.INTEGER),
        ),
        (("config_hash",),),
    ),
)


def _o(object_type: str, name: str, table_name: str) -> InheritedObjectSpec:
    return InheritedObjectSpec(
        object_type=object_type,
        name=name,
        table_name=table_name,
    )


_OBJECTS = (
    _o("index", "search_attempt_events_by_search", "search_attempt_events"),
    _o("index", "search_attempt_heartbeats_by_search", "search_attempt_heartbeats"),
    _o("index", "search_candidates_by_search", "search_candidates"),
    _o("index", "search_evaluations_by_search", "search_evaluations"),
    *(_o("table", table.table_name, table.table_name) for table in _TABLES),
    _o("trigger", "evaluation_audit_dispatch_no_delete", "evaluation_audit_dispatch"),
    _o("trigger", "evaluation_audit_dispatch_no_update", "evaluation_audit_dispatch"),
    _o("trigger", "evaluation_audit_outbox_no_delete", "evaluation_audit_outbox"),
    _o("trigger", "evaluation_audit_outbox_no_update", "evaluation_audit_outbox"),
    _o("trigger", "evaluations_no_delete", "evaluations"),
    _o("trigger", "evaluations_no_update", "evaluations"),
    _o("trigger", "experiment_event_dispatch_no_delete", "experiment_event_dispatch"),
    _o("trigger", "experiment_event_dispatch_no_update", "experiment_event_dispatch"),
    _o("trigger", "experiment_event_outbox_no_delete", "experiment_event_outbox"),
    _o("trigger", "experiment_event_outbox_no_update", "experiment_event_outbox"),
    _o("trigger", "search_attempt_events_no_delete", "search_attempt_events"),
    _o("trigger", "search_attempt_events_no_update", "search_attempt_events"),
    _o("trigger", "search_attempt_heartbeats_no_delete", "search_attempt_heartbeats"),
    _o("trigger", "search_attempt_heartbeats_no_update", "search_attempt_heartbeats"),
    _o("trigger", "search_attempt_recoveries_no_delete", "search_attempt_recoveries"),
    _o("trigger", "search_attempt_recoveries_no_update", "search_attempt_recoveries"),
    _o("trigger", "search_candidates_no_delete", "search_candidates"),
    _o("trigger", "search_candidates_no_update", "search_candidates"),
    _o("trigger", "search_evaluations_no_delete", "search_evaluations"),
    _o("trigger", "search_evaluations_no_update", "search_evaluations"),
    _o("trigger", "trials_no_delete", "trials"),
    _o("trigger", "trials_no_update", "trials"),
)


def _table_sql(table_name: str, lines: tuple[str, ...]) -> str:
    return f"CREATE TABLE {table_name} (\n" + "\n".join(
        f"    {line}" for line in lines
    ) + "\n)"


_TABLE_SQL = {
    "alpha_state": _table_sql(
        "alpha_state",
        (
            "alpha_id TEXT PRIMARY KEY,",
            "definition_json TEXT NOT NULL,",
            "state TEXT NOT NULL,",
            "returns_json TEXT NOT NULL,",
            "sharpe REAL,",
            "pvalue REAL",
        ),
    ),
    "bandit_state": _table_sql(
        "bandit_state",
        (
            "id INTEGER PRIMARY KEY CHECK (id = 1),",
            "snapshot_json TEXT NOT NULL",
        ),
    ),
    "evaluation_audit_dispatch": _table_sql(
        "evaluation_audit_dispatch",
        (
            "dispatch_seq INTEGER PRIMARY KEY AUTOINCREMENT,",
            "event_id TEXT UNIQUE NOT NULL,",
            "dispatched_at INTEGER NOT NULL CHECK(dispatched_at >= 0),",
            "audit_entry_hash TEXT NOT NULL,",
            "prev_record_hash TEXT NOT NULL,",
            "record_hash TEXT UNIQUE NOT NULL",
        ),
    ),
    "evaluation_audit_outbox": _table_sql(
        "evaluation_audit_outbox",
        (
            "audit_seq INTEGER PRIMARY KEY AUTOINCREMENT,",
            "run_id TEXT UNIQUE NOT NULL,",
            "event_id TEXT UNIQUE NOT NULL,",
            "actor TEXT NOT NULL,",
            "action TEXT NOT NULL,",
            "timestamp INTEGER NOT NULL CHECK(timestamp >= 0),",
            "target TEXT NOT NULL,",
            "metadata_json TEXT NOT NULL,",
            "evaluation_content_hash TEXT NOT NULL,",
            "prev_record_hash TEXT NOT NULL,",
            "record_hash TEXT UNIQUE NOT NULL",
        ),
    ),
    "evaluations": _table_sql(
        "evaluations",
        (
            "run_id TEXT PRIMARY KEY,",
            "alpha_id TEXT NOT NULL,",
            "dataset_version TEXT NOT NULL,",
            "recorded_ts INTEGER NOT NULL,",
            "verdict TEXT NOT NULL,",
            "content_hash TEXT NOT NULL,",
            "evaluation_json TEXT NOT NULL",
        ),
    ),
    "experiment_event_dispatch": _table_sql(
        "experiment_event_dispatch",
        (
            "dispatch_seq INTEGER PRIMARY KEY AUTOINCREMENT,",
            "outbox_id TEXT UNIQUE NOT NULL,",
            "dispatched_at INTEGER NOT NULL,",
            "event_repro_hash TEXT NOT NULL,",
            "prev_record_hash TEXT NOT NULL,",
            "record_hash TEXT NOT NULL",
        ),
    ),
    "experiment_event_outbox": _table_sql(
        "experiment_event_outbox",
        (
            "outbox_seq INTEGER PRIMARY KEY AUTOINCREMENT,",
            "outbox_id TEXT UNIQUE NOT NULL,",
            "event_json TEXT NOT NULL,",
            "correlation_id TEXT NOT NULL,",
            "causation_id TEXT NOT NULL,",
            "repro_hash TEXT NOT NULL,",
            "prev_record_hash TEXT NOT NULL,",
            "record_hash TEXT NOT NULL",
        ),
    ),
    "search_attempt_events": _table_sql(
        "search_attempt_events",
        (
            "event_seq INTEGER PRIMARY KEY AUTOINCREMENT,",
            "search_id TEXT NOT NULL,",
            "search_kind TEXT NOT NULL,",
            "source TEXT NOT NULL,",
            "dataset_version TEXT NOT NULL,",
            "status TEXT NOT NULL CHECK(status IN ('STARTED', 'FAILED', 'COMPLETED')),",
            "recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0),",
            "prev_event_hash TEXT NOT NULL,",
            "event_hash TEXT UNIQUE NOT NULL",
        ),
    ),
    "search_attempt_heartbeats": _table_sql(
        "search_attempt_heartbeats",
        (
            "heartbeat_seq INTEGER PRIMARY KEY AUTOINCREMENT,",
            "search_id TEXT NOT NULL,",
            "recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0),",
            "prev_heartbeat_hash TEXT NOT NULL,",
            "heartbeat_hash TEXT UNIQUE NOT NULL,",
            "UNIQUE(search_id, recorded_at)",
        ),
    ),
    "search_attempt_recoveries": _table_sql(
        "search_attempt_recoveries",
        (
            "recovery_seq INTEGER PRIMARY KEY AUTOINCREMENT,",
            "search_id TEXT UNIQUE NOT NULL,",
            "reason TEXT NOT NULL CHECK(reason = 'STALE_STARTED_TIMEOUT'),",
            "timeout_ms INTEGER NOT NULL CHECK(timeout_ms > 0),",
            "last_activity_at INTEGER NOT NULL CHECK(last_activity_at >= 0),",
            "recovered_at INTEGER NOT NULL CHECK(recovered_at >= 0),",
            "terminal_event_hash TEXT UNIQUE NOT NULL,",
            "prev_recovery_hash TEXT NOT NULL,",
            "recovery_hash TEXT UNIQUE NOT NULL",
        ),
    ),
    "search_candidates": _table_sql(
        "search_candidates",
        (
            "candidate_seq INTEGER PRIMARY KEY AUTOINCREMENT,",
            "search_id TEXT NOT NULL,",
            "config_hash TEXT NOT NULL,",
            "status TEXT NOT NULL CHECK(status = 'REGISTERED'),",
            "registered_at INTEGER NOT NULL CHECK(registered_at >= 0),",
            "prev_candidate_hash TEXT NOT NULL,",
            "candidate_hash TEXT UNIQUE NOT NULL,",
            "UNIQUE(search_id, config_hash)",
        ),
    ),
    "search_evaluations": _table_sql(
        "search_evaluations",
        (
            "link_seq INTEGER PRIMARY KEY AUTOINCREMENT,",
            "search_id TEXT NOT NULL,",
            "config_hash TEXT NOT NULL,",
            "run_id TEXT NOT NULL,",
            "recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0),",
            "evaluation_content_hash TEXT NOT NULL,",
            "prev_link_hash TEXT NOT NULL,",
            "link_hash TEXT UNIQUE NOT NULL,",
            "UNIQUE(search_id, run_id)",
        ),
    ),
    "trials": _table_sql(
        "trials",
        (
            "config_hash TEXT PRIMARY KEY,",
            "first_seen_ts INTEGER NOT NULL",
        ),
    ),
}


def _ix(
    table_name: str,
    name: str,
    *,
    unique: bool,
    origin: str,
    columns: tuple[str, ...],
) -> InheritedIndexSpec:
    return InheritedIndexSpec(
        table_name=table_name,
        name=name,
        unique=unique,
        origin=origin,
        partial=False,
        columns=columns,
        descending=(False,) * len(columns),
        collations=("BINARY",) * len(columns),
    )


_INDEXES = (
    _ix("alpha_state", "sqlite_autoindex_alpha_state_1", unique=True, origin="pk", columns=("alpha_id",)),
    _ix("evaluation_audit_dispatch", "sqlite_autoindex_evaluation_audit_dispatch_1", unique=True, origin="u", columns=("event_id",)),
    _ix("evaluation_audit_dispatch", "sqlite_autoindex_evaluation_audit_dispatch_2", unique=True, origin="u", columns=("record_hash",)),
    _ix("evaluation_audit_outbox", "sqlite_autoindex_evaluation_audit_outbox_1", unique=True, origin="u", columns=("run_id",)),
    _ix("evaluation_audit_outbox", "sqlite_autoindex_evaluation_audit_outbox_2", unique=True, origin="u", columns=("event_id",)),
    _ix("evaluation_audit_outbox", "sqlite_autoindex_evaluation_audit_outbox_3", unique=True, origin="u", columns=("record_hash",)),
    _ix("evaluations", "sqlite_autoindex_evaluations_1", unique=True, origin="pk", columns=("run_id",)),
    _ix("experiment_event_dispatch", "sqlite_autoindex_experiment_event_dispatch_1", unique=True, origin="u", columns=("outbox_id",)),
    _ix("experiment_event_outbox", "sqlite_autoindex_experiment_event_outbox_1", unique=True, origin="u", columns=("outbox_id",)),
    _ix("search_attempt_events", "search_attempt_events_by_search", unique=False, origin="c", columns=("search_id", "event_seq")),
    _ix("search_attempt_events", "sqlite_autoindex_search_attempt_events_1", unique=True, origin="u", columns=("event_hash",)),
    _ix("search_attempt_heartbeats", "search_attempt_heartbeats_by_search", unique=False, origin="c", columns=("search_id", "heartbeat_seq")),
    _ix("search_attempt_heartbeats", "sqlite_autoindex_search_attempt_heartbeats_1", unique=True, origin="u", columns=("heartbeat_hash",)),
    _ix("search_attempt_heartbeats", "sqlite_autoindex_search_attempt_heartbeats_2", unique=True, origin="u", columns=("search_id", "recorded_at")),
    _ix("search_attempt_recoveries", "sqlite_autoindex_search_attempt_recoveries_1", unique=True, origin="u", columns=("search_id",)),
    _ix("search_attempt_recoveries", "sqlite_autoindex_search_attempt_recoveries_2", unique=True, origin="u", columns=("terminal_event_hash",)),
    _ix("search_attempt_recoveries", "sqlite_autoindex_search_attempt_recoveries_3", unique=True, origin="u", columns=("recovery_hash",)),
    _ix("search_candidates", "search_candidates_by_search", unique=False, origin="c", columns=("search_id", "candidate_seq")),
    _ix("search_candidates", "sqlite_autoindex_search_candidates_1", unique=True, origin="u", columns=("candidate_hash",)),
    _ix("search_candidates", "sqlite_autoindex_search_candidates_2", unique=True, origin="u", columns=("search_id", "config_hash")),
    _ix("search_evaluations", "search_evaluations_by_search", unique=False, origin="c", columns=("search_id", "link_seq")),
    _ix("search_evaluations", "sqlite_autoindex_search_evaluations_1", unique=True, origin="u", columns=("link_hash",)),
    _ix("search_evaluations", "sqlite_autoindex_search_evaluations_2", unique=True, origin="u", columns=("search_id", "run_id")),
    _ix("trials", "sqlite_autoindex_trials_1", unique=True, origin="pk", columns=("config_hash",)),
)


_EXPLICIT_INDEX_SQL = {
    "search_attempt_events_by_search": "CREATE INDEX search_attempt_events_by_search\nON search_attempt_events(search_id, event_seq)",
    "search_attempt_heartbeats_by_search": "CREATE INDEX search_attempt_heartbeats_by_search\nON search_attempt_heartbeats(search_id, heartbeat_seq)",
    "search_candidates_by_search": "CREATE INDEX search_candidates_by_search\nON search_candidates(search_id, candidate_seq)",
    "search_evaluations_by_search": "CREATE INDEX search_evaluations_by_search\nON search_evaluations(search_id, link_seq)",
}


_TRIGGER_ACTIONS = {
    item.name: ("DELETE" if item.name.endswith("_no_delete") else "UPDATE")
    for item in _OBJECTS
    if item.object_type == "trigger"
}


def _trigger_sql(name: str, table_name: str) -> str:
    action = _TRIGGER_ACTIONS[name]
    return (
        f"CREATE TRIGGER {name}\n"
        f"BEFORE {action} ON {table_name}\n"
        f"BEGIN SELECT RAISE(ABORT, '{table_name} is append-only'); END"
    )


_SQLITE_SCHEMA_ENTRIES = tuple(
    InheritedSqliteSchemaEntrySpec(*row)
    for row in sorted(
        (
            *(
                ("index", item.name, item.table_name, _EXPLICIT_INDEX_SQL.get(item.name))
                for item in _INDEXES
            ),
            *(
                ("table", table.table_name, table.table_name, _TABLE_SQL[table.table_name])
                for table in _TABLES
            ),
            (
                "table",
                "sqlite_sequence",
                "sqlite_sequence",
                "CREATE TABLE sqlite_sequence(name,seq)",
            ),
            *(
                ("trigger", item.name, item.table_name, _trigger_sql(item.name, item.table_name))
                for item in _OBJECTS
                if item.object_type == "trigger"
            ),
        ),
        key=lambda item: (item[0], item[1]),
    )
)


_OWNED_SQLITE_SEQUENCE_TABLES = (
    "evaluation_audit_dispatch",
    "evaluation_audit_outbox",
    "experiment_event_dispatch",
    "experiment_event_outbox",
    "search_attempt_events",
    "search_attempt_heartbeats",
    "search_attempt_recoveries",
    "search_candidates",
    "search_evaluations",
)


_ORIGIN_CHAIN_HEAD_ORDER = (
    "evaluation_audit",
    "evaluation_audit_dispatch",
    "experiment_event_dispatch",
    "experiment_event_outbox",
    "search_candidates",
    "search_evaluations",
    "search_events",
    "search_heartbeats",
    "search_recoveries",
)


_ORIGIN_PROJECTION = ExactV2OriginProjectionSpec(
    projection_id="QUANTOS_EXACT_V2_LOGICAL_ORIGIN_V1",
    projection_version=1,
    domain_separator=b"QUANTOS_EXACT_V2_LOGICAL_ORIGIN_V1",
    digest_algorithm="SHA256",
    schema_field_order=("type", "name", "tbl_name", "sql"),
    schema_entry_order=tuple(
        (entry.object_type, entry.name) for entry in _SQLITE_SCHEMA_ENTRIES
    ),
    table_order=tuple(table.table_name for table in _TABLES),
    primary_key_order=tuple(
        ExactV2OriginPrimaryKeyOrderSpec(
            table_name=table.table_name,
            columns=tuple(
                column.name
                for column in sorted(
                    table.columns,
                    key=lambda item: item.primary_key_ordinal or len(table.columns) + 1,
                )
                if column.primary_key_ordinal
            ),
        )
        for table in _TABLES
    ),
    sequence_order=_OWNED_SQLITE_SEQUENCE_TABLES,
    chain_head_order=_ORIGIN_CHAIN_HEAD_ORDER,
    type_tags=(
        ExactV2OriginTypeTagSpec("NULL", b"N", "EMPTY"),
        ExactV2OriginTypeTagSpec("INTEGER", b"I", "SIGNED_INT64_BIG_ENDIAN"),
        ExactV2OriginTypeTagSpec("REAL", b"R", "IEEE754_BINARY64_BIG_ENDIAN"),
        ExactV2OriginTypeTagSpec("TEXT", b"T", "UTF8"),
        ExactV2OriginTypeTagSpec("BLOB", b"B", "RAW_BYTES"),
    ),
    integer_encoding="SIGNED_INT64_BIG_ENDIAN",
    real_encoding="IEEE754_BINARY64_BIG_ENDIAN",
    text_encoding="UTF8",
    blob_encoding="RAW_BYTES",
    frame_encoding="LABEL_ASCII_U32_BIG_ENDIAN_VALUE_U64_BIG_ENDIAN",
)


EXACT_V2_INHERITED_SCHEMA_CATALOG = ExactV2InheritedSchemaCatalog(
    catalog_id="EXACT_V2_FAMILY_SCHEMA_INHERITED_CATALOG_V1",
    schema_version=2,
    tables=_TABLES,
    objects=_OBJECTS,
    sqlite_schema_entries=_SQLITE_SCHEMA_ENTRIES,
    indexes=_INDEXES,
    owned_sqlite_sequence_tables=_OWNED_SQLITE_SEQUENCE_TABLES,
    origin_projection=_ORIGIN_PROJECTION,
)


__all__ = [
    "EXACT_V2_INHERITED_SCHEMA_CATALOG",
    "ExactV2InheritedSchemaCatalog",
    "ExactV2OriginPrimaryKeyOrderSpec",
    "ExactV2OriginProjectionSpec",
    "ExactV2OriginTypeTagSpec",
    "InheritedColumnSpec",
    "InheritedIndexSpec",
    "InheritedObjectSpec",
    "InheritedSqliteSchemaEntrySpec",
    "InheritedTableSpec",
    "InheritedUniqueKeySpec",
]
