"""Safely stage legacy owner-store schema upgrades for domain-event outboxes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time

from services.data.refresh_history import RefreshHistoryStore
from services.execution.dry_run_store import DryRunStore
from services.research.experiment_registry.store import SqliteExperimentStore
from tools.cache_io import atomic_write_json
from tools.domain_event_outbox_drain import (
    DATA_REFRESH_HISTORY_DB,
    DRY_RUN_DB,
    EXPERIMENT_REGISTRY_DB,
)


DOMAIN_EVENT_OWNER_MIGRATION_SNAPSHOT = os.path.join(
    "data", "cache", "domain_event_owner_migration.json"
)
DOMAIN_EVENT_OWNER_MIGRATION_BACKUP_DIR = os.path.join(
    "data", "backup", "domain_event_owner_migration"
)


class OwnerStoreMigrationError(RuntimeError):
    """Base class for fail-closed owner-store migration errors."""


class PartialMigrationError(OwnerStoreMigrationError):
    """Only part of a target outbox schema already exists."""


class ActiveSqliteSidecarError(OwnerStoreMigrationError):
    """A WAL/SHM sidecar makes atomic main-file replacement unsafe."""


class StagingFileExistsError(OwnerStoreMigrationError):
    """A prior staging artifact must be investigated before retrying."""


class ProtectedDataChangedError(OwnerStoreMigrationError):
    """A migration changed pre-existing domain tables."""


@dataclass(frozen=True)
class OwnerStoreSpec:
    name: str
    path: str
    protected_tables: tuple[str, ...]
    target_tables: tuple[str, ...]
    migrate: Callable[[str], None]
    verify: Callable[[str], Mapping[str, object]]

    def __post_init__(self) -> None:
        if not self.name or not self.path:
            raise ValueError("owner-store name and path must be non-empty")
        if not self.protected_tables or not self.target_tables:
            raise ValueError("owner-store table sets must be non-empty")
        if set(self.protected_tables) & set(self.target_tables):
            raise ValueError("protected and target tables must be disjoint")


@dataclass(frozen=True)
class _StoreSnapshot:
    file_sha256: str
    tables: frozenset[str]
    protected: Mapping[str, Mapping[str, object]]
    protected_digest: str


def _close(store: object | None) -> None:
    if store is None:
        return
    close = getattr(store, "close", None)
    if callable(close):
        close()


def _migrate_dry_run(path: str) -> None:
    store = None
    try:
        store = DryRunStore(path, create=True)
    finally:
        _close(store)


def _verify_dry_run(path: str) -> Mapping[str, object]:
    store = None
    try:
        store = DryRunStore(path, create=False)
        if not store.outbox_chain_valid():
            raise OwnerStoreMigrationError("dry-run outbox verification failed")
        return {
            "domain_rows": len(store.all_runs()),
            "pending_events": len(store.pending_outbox()),
        }
    finally:
        _close(store)


def _migrate_experiment(path: str) -> None:
    store = None
    try:
        store = SqliteExperimentStore(path, create=True)
    finally:
        _close(store)


def _verify_experiment(path: str) -> Mapping[str, object]:
    store = None
    try:
        store = SqliteExperimentStore(path, create=False)
        if not store.event_outbox_chain_valid():
            raise OwnerStoreMigrationError("experiment outbox verification failed")
        return {
            "domain_rows": (
                store.trial_count()
                + len(store.all_alphas())
                + sum(
                    len(store.list_evaluations(alpha["alpha_id"]))
                    for alpha in store.all_alphas()
                )
            ),
            "pending_events": len(store.pending_event_outbox()),
        }
    finally:
        _close(store)


def _migrate_refresh(path: str) -> None:
    store = None
    try:
        store = RefreshHistoryStore(path, create=True)
    finally:
        _close(store)


def _verify_refresh(path: str) -> Mapping[str, object]:
    store = None
    try:
        store = RefreshHistoryStore(path, create=False)
        if not store.chain_valid():
            raise OwnerStoreMigrationError("refresh outbox verification failed")
        return {
            "domain_rows": store.count(),
            "pending_events": len(store.pending_event_outbox()),
        }
    finally:
        _close(store)


def default_owner_store_specs(
    *,
    dry_run_path: str = DRY_RUN_DB,
    experiment_path: str = EXPERIMENT_REGISTRY_DB,
    refresh_path: str = DATA_REFRESH_HISTORY_DB,
) -> tuple[OwnerStoreSpec, ...]:
    return (
        OwnerStoreSpec(
            name="dry_run",
            path=dry_run_path,
            protected_tables=("dry_run",),
            target_tables=("dry_run_outbox", "dry_run_outbox_dispatch"),
            migrate=_migrate_dry_run,
            verify=_verify_dry_run,
        ),
        OwnerStoreSpec(
            name="experiment_registry",
            path=experiment_path,
            protected_tables=(
                "trials",
                "alpha_state",
                "evaluations",
                "bandit_state",
            ),
            target_tables=(
                "experiment_event_outbox",
                "experiment_event_dispatch",
            ),
            migrate=_migrate_experiment,
            verify=_verify_experiment,
        ),
        OwnerStoreSpec(
            name="data_refresh",
            path=refresh_path,
            protected_tables=("refresh_history", "refresh_source_history"),
            target_tables=("refresh_event_outbox", "refresh_event_dispatch"),
            migrate=_migrate_refresh,
            verify=_verify_refresh,
        ),
    )


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float)):
        return value
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    raise TypeError(f"unsupported SQLite value type: {type(value).__name__}")


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_snapshot(connection: sqlite3.Connection, table: str) -> dict:
    info = connection.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()
    if not info:
        raise OwnerStoreMigrationError(f"protected table is unavailable: {table}")
    columns = [str(row[1]) for row in info]
    primary_key = [
        str(row[1])
        for row in sorted(info, key=lambda item: int(item[5]) or 10_000)
        if int(row[5]) > 0
    ]
    order_columns = primary_key or columns
    rows = connection.execute(
        f"SELECT * FROM {_quoted(table)} ORDER BY "
        + ",".join(_quoted(column) for column in order_columns)
    ).fetchall()
    canonical = {
        "columns": columns,
        "rows": [[_canonical_value(value) for value in row] for row in rows],
        "schema": str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()[0]
        ),
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "row_count": len(rows),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "schema_sha256": hashlib.sha256(
            canonical["schema"].encode("utf-8")
        ).hexdigest(),
    }


def _snapshot(path: str, protected_tables: Sequence[str]) -> _StoreSnapshot:
    uri = f"{Path(path).resolve().as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        integrity = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()
        ]
        if integrity != ["ok"]:
            raise OwnerStoreMigrationError("SQLite integrity check failed")
        tables = frozenset(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        )
        missing = sorted(set(protected_tables) - tables)
        if missing:
            raise OwnerStoreMigrationError(
                f"protected owner tables are missing: {missing}"
            )
        protected = {
            table: _table_snapshot(connection, table)
            for table in protected_tables
        }
    finally:
        connection.close()
    encoded = json.dumps(
        protected,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _StoreSnapshot(
        file_sha256=_sha256_file(path),
        tables=tables,
        protected=protected,
        protected_digest=hashlib.sha256(encoded).hexdigest(),
    )


def _sqlite_backup(source_path: str, destination_path: str) -> None:
    if os.path.exists(destination_path):
        raise StagingFileExistsError(
            f"migration artifact already exists: {destination_path}"
        )
    parent = os.path.dirname(os.path.abspath(destination_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    source_uri = f"{Path(source_path).resolve().as_uri()}?mode=ro&immutable=1"
    source = sqlite3.connect(source_uri, uri=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()


def _assert_no_sidecars(path: str) -> None:
    present = [suffix for suffix in ("-wal", "-shm") if os.path.exists(path + suffix)]
    if present:
        raise ActiveSqliteSidecarError(
            f"SQLite sidecars must be absent before replacement: {present}"
        )


def _summary(snapshot: _StoreSnapshot) -> dict:
    return {
        "file_sha256": snapshot.file_sha256,
        "protected_digest": snapshot.protected_digest,
        "row_counts": {
            table: int(detail["row_count"])
            for table, detail in snapshot.protected.items()
        },
    }


def _migrate_one(
    spec: OwnerStoreSpec,
    *,
    apply: bool,
    backup_dir: str,
) -> dict:
    if not os.path.isfile(spec.path):
        return {"status": "UNAVAILABLE", "path": spec.path}
    _assert_no_sidecars(spec.path)
    before = _snapshot(spec.path, spec.protected_tables)
    target_present = set(spec.target_tables) & set(before.tables)
    if target_present and target_present != set(spec.target_tables):
        raise PartialMigrationError("target outbox schema is partial")
    if set(spec.target_tables) <= set(before.tables):
        verification = dict(spec.verify(spec.path))
        return {
            "status": "CURRENT",
            "path": spec.path,
            "before": _summary(before),
            "verification": verification,
        }
    if not apply:
        return {
            "status": "MIGRATION_REQUIRED",
            "path": spec.path,
            "before": _summary(before),
            "added_tables": list(spec.target_tables),
        }

    stage_path = spec.path + ".domain-event-migration.stage"
    _assert_no_sidecars(stage_path)
    if os.path.exists(stage_path):
        raise StagingFileExistsError("migration stage already exists")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(
        backup_dir,
        f"{spec.name}-{before.file_sha256}.db",
    )
    _sqlite_backup(spec.path, backup_path)
    backup_snapshot = _snapshot(backup_path, spec.protected_tables)
    if backup_snapshot.protected != before.protected:
        raise ProtectedDataChangedError("backup does not match source domain tables")
    _sqlite_backup(spec.path, stage_path)
    spec.migrate(stage_path)
    _assert_no_sidecars(stage_path)
    staged = _snapshot(stage_path, spec.protected_tables)
    if staged.protected != before.protected:
        raise ProtectedDataChangedError("staged migration changed domain tables")
    if not set(spec.target_tables) <= set(staged.tables):
        raise OwnerStoreMigrationError("staged migration did not create target tables")
    verification = dict(spec.verify(stage_path))
    _assert_no_sidecars(stage_path)

    _assert_no_sidecars(spec.path)
    if _sha256_file(spec.path) != before.file_sha256:
        raise OwnerStoreMigrationError("source changed during staged migration")
    os.replace(stage_path, spec.path)
    try:
        final = _snapshot(spec.path, spec.protected_tables)
        if final.protected != before.protected:
            raise ProtectedDataChangedError("final migration changed domain tables")
        final_verification = dict(spec.verify(spec.path))
    except Exception:
        restore_path = spec.path + ".domain-event-migration.restore"
        _sqlite_backup(backup_path, restore_path)
        os.replace(restore_path, spec.path)
        raise
    return {
        "status": "MIGRATED",
        "path": spec.path,
        "backup_path": backup_path,
        "before": _summary(before),
        "after": _summary(final),
        "added_tables": list(spec.target_tables),
        "verification": final_verification or verification,
    }


def migrate_domain_event_owner_stores(
    *,
    apply: bool,
    specs: Sequence[OwnerStoreSpec] | None = None,
    backup_dir: str = DOMAIN_EVENT_OWNER_MIGRATION_BACKUP_DIR,
    output_path: str = DOMAIN_EVENT_OWNER_MIGRATION_SNAPSHOT,
    now_ms: int | None = None,
) -> dict:
    if not isinstance(apply, bool):
        raise TypeError("apply must be boolean")
    resolved_now_ms = int(time.time() * 1_000) if now_ms is None else now_ms
    if isinstance(resolved_now_ms, bool) or not isinstance(resolved_now_ms, int):
        raise TypeError("now_ms must be an integer")
    if resolved_now_ms < 0:
        raise ValueError("now_ms must be non-negative")
    resolved_specs = default_owner_store_specs() if specs is None else tuple(specs)
    if any(not isinstance(spec, OwnerStoreSpec) for spec in resolved_specs):
        raise TypeError("specs must contain OwnerStoreSpec values")
    names = [spec.name for spec in resolved_specs]
    if len(names) != len(set(names)):
        raise ValueError("owner-store names must be unique")

    results = {}
    for spec in resolved_specs:
        before_sha = _sha256_file(spec.path) if os.path.isfile(spec.path) else None
        try:
            results[spec.name] = _migrate_one(
                spec,
                apply=apply,
                backup_dir=backup_dir,
            )
        except Exception as exc:
            source_preserved = (
                before_sha is not None
                and os.path.isfile(spec.path)
                and _sha256_file(spec.path) == before_sha
            )
            results[spec.name] = {
                "status": "BREACH",
                "error_type": type(exc).__name__,
                "path": spec.path,
                "source_preserved": source_preserved,
            }

    statuses = [result["status"] for result in results.values()]
    if "BREACH" in statuses:
        verdict = "BREACH"
    elif not apply and "MIGRATION_REQUIRED" in statuses:
        verdict = "PLAN_REQUIRED"
    elif "UNAVAILABLE" in statuses:
        verdict = "PARTIAL"
    else:
        verdict = "PASS"
    document = {
        "schema_version": 1,
        "mode": "STAGED_SQLITE_OWNER_MIGRATION",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": resolved_now_ms,
        "apply": apply,
        "verdict": verdict,
        "sources": results,
        "safety": {
            "staged_before_replace": True,
            "protected_domain_tables_immutable": True,
            "order_submission": False,
            "broker_connectivity": False,
            "credential_access": False,
        },
    }
    atomic_write_json(output_path, document, indent=2)
    return document


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan or apply staged domain-event owner-store migrations."
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    document = migrate_domain_event_owner_stores(apply=args.apply)
    print(
        "domain event owner migration: "
        f"{document['verdict']}; apply={document['apply']}"
    )
    if document["verdict"] == "PASS":
        return 0
    if document["verdict"] == "BREACH":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DOMAIN_EVENT_OWNER_MIGRATION_BACKUP_DIR",
    "DOMAIN_EVENT_OWNER_MIGRATION_SNAPSHOT",
    "OwnerStoreSpec",
    "default_owner_store_specs",
    "migrate_domain_event_owner_stores",
]
