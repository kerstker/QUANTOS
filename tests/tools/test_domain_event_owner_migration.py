import json
import os
import sqlite3

from services.data.refresh_history import RefreshHistoryStore
from services.execution.dry_run_store import DryRunStore
from services.research.experiment_registry.store import SqliteExperimentStore
from tools.domain_event_owner_migration import (
    default_owner_store_specs,
    migrate_domain_event_owner_stores,
)


def _legacy_dry_run(path):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE dry_run ("
        "run_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, timestamp INTEGER NOT NULL,"
        "n_intents INTEGER, n_approved INTEGER, n_rejected INTEGER,"
        "rejected_reasons TEXT, pnl TEXT, audit_root TEXT, detail TEXT)"
    )
    connection.execute(
        "INSERT INTO dry_run VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("run-1", "carry", 100, 1, 1, 0, "[]", "{}", "audit", "{}"),
    )
    connection.commit()
    connection.close()


def _legacy_experiment(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE trials (
            config_hash TEXT PRIMARY KEY,
            first_seen_ts INTEGER NOT NULL
        );
        CREATE TABLE alpha_state (
            alpha_id TEXT PRIMARY KEY,
            definition_json TEXT NOT NULL,
            state TEXT NOT NULL,
            returns_json TEXT NOT NULL,
            sharpe REAL,
            pvalue REAL
        );
        CREATE TABLE evaluations (
            run_id TEXT PRIMARY KEY,
            alpha_id TEXT NOT NULL,
            dataset_version TEXT NOT NULL,
            recorded_ts INTEGER NOT NULL,
            verdict TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            evaluation_json TEXT NOT NULL
        );
        CREATE TABLE bandit_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            snapshot_json TEXT NOT NULL
        );
        """
    )
    connection.execute("INSERT INTO trials VALUES ('cfg-1', 100)")
    connection.execute(
        "INSERT INTO alpha_state VALUES ('alpha-1', '{}', 'PAPER', '[]', 1.0, 0.1)"
    )
    connection.execute(
        "INSERT INTO evaluations VALUES "
        "('eval-1','alpha-1','dataset-1',100,'PASS','hash-1','{}')"
    )
    connection.commit()
    connection.close()


def _status(ran_at):
    return {
        "run_id": f"refresh-{ran_at}",
        "ran_at": ran_at,
        "duration_ms": 25,
        "outcome": "PASS",
        "failed_ticker_total": 0,
        "reports": [
            {
                "source": "prices",
                "outcome": "PASS",
                "duration_ms": 20,
                "watermark_after": ran_at,
                "failed": [],
                "failure_details": [],
                "failed_tickers": [],
                "quality_failed": [],
            }
        ],
        "staleness_before": {
            "prices": {"freshest_days": 1, "n_stale": 0, "n_total": 2}
        },
        "staleness_after": {
            "prices": {"freshest_days": 1, "n_stale": 0, "n_total": 2}
        },
    }


def _legacy_refresh(path):
    store = RefreshHistoryStore(str(path), create=True)
    store.append(_status(100), recorded_at_ms=110)
    store.close()
    connection = sqlite3.connect(path)
    connection.execute("DROP TABLE refresh_event_dispatch")
    connection.execute("DROP TABLE refresh_event_outbox")
    connection.commit()
    connection.close()
    for suffix in ("-wal", "-shm"):
        sidecar = str(path) + suffix
        if os.path.exists(sidecar):
            os.remove(sidecar)


def _table_rows(path, table):
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True
    )
    rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
    connection.close()
    return rows


def _specs(dry_path, experiment_path, refresh_path):
    return default_owner_store_specs(
        dry_run_path=str(dry_path),
        experiment_path=str(experiment_path),
        refresh_path=str(refresh_path),
    )


def test_plan_mode_reports_required_migration_without_writing(tmp_path):
    dry_path = tmp_path / "dry.db"
    _legacy_dry_run(dry_path)
    before = dry_path.read_bytes()
    backup_dir = tmp_path / "backups"

    document = migrate_domain_event_owner_stores(
        apply=False,
        specs=_specs(dry_path, tmp_path / "missing-exp.db", tmp_path / "missing-ref.db"),
        backup_dir=str(backup_dir),
        output_path=str(tmp_path / "migration.json"),
    )

    assert document["verdict"] == "PLAN_REQUIRED"
    assert document["sources"]["dry_run"]["status"] == "MIGRATION_REQUIRED"
    assert dry_path.read_bytes() == before
    assert not backup_dir.exists()


def test_all_three_legacy_stores_migrate_without_changing_domain_rows(tmp_path):
    dry_path = tmp_path / "dry.db"
    experiment_path = tmp_path / "experiment.db"
    refresh_path = tmp_path / "refresh.db"
    _legacy_dry_run(dry_path)
    _legacy_experiment(experiment_path)
    _legacy_refresh(refresh_path)
    protected_before = {
        "dry": _table_rows(dry_path, "dry_run"),
        "trials": _table_rows(experiment_path, "trials"),
        "alphas": _table_rows(experiment_path, "alpha_state"),
        "evaluations": _table_rows(experiment_path, "evaluations"),
        "refresh": _table_rows(refresh_path, "refresh_history"),
        "refresh_sources": _table_rows(refresh_path, "refresh_source_history"),
    }

    document = migrate_domain_event_owner_stores(
        apply=True,
        specs=_specs(dry_path, experiment_path, refresh_path),
        backup_dir=str(tmp_path / "backups"),
        output_path=str(tmp_path / "migration.json"),
    )

    assert document["verdict"] == "PASS"
    assert {item["status"] for item in document["sources"].values()} == {"MIGRATED"}
    assert _table_rows(dry_path, "dry_run") == protected_before["dry"]
    assert _table_rows(experiment_path, "trials") == protected_before["trials"]
    assert _table_rows(experiment_path, "alpha_state") == protected_before["alphas"]
    assert _table_rows(experiment_path, "evaluations") == protected_before["evaluations"]
    assert _table_rows(refresh_path, "refresh_history") == protected_before["refresh"]
    assert _table_rows(refresh_path, "refresh_source_history") == protected_before["refresh_sources"]
    dry_store = DryRunStore(str(dry_path), create=False)
    assert dry_store.outbox_chain_valid()
    dry_store.close()
    experiment_store = SqliteExperimentStore(str(experiment_path), create=False)
    assert experiment_store.event_outbox_chain_valid()
    experiment_store.close()
    refresh_store = RefreshHistoryStore(str(refresh_path), create=False)
    assert refresh_store.chain_valid()
    assert len(refresh_store.pending_event_outbox()) == 1
    refresh_store.close()
    assert len(list((tmp_path / "backups").glob("*.db"))) == 3


def test_bad_refresh_history_does_not_block_independent_dry_run_migration(tmp_path):
    dry_path = tmp_path / "dry.db"
    refresh_path = tmp_path / "refresh.db"
    _legacy_dry_run(dry_path)
    _legacy_refresh(refresh_path)
    connection = sqlite3.connect(refresh_path)
    connection.execute("DROP TRIGGER refresh_history_no_update")
    connection.execute("UPDATE refresh_history SET status_hash='tampered'")
    connection.commit()
    connection.close()
    for suffix in ("-wal", "-shm"):
        sidecar = str(refresh_path) + suffix
        if os.path.exists(sidecar):
            os.remove(sidecar)
    refresh_before = refresh_path.read_bytes()

    document = migrate_domain_event_owner_stores(
        apply=True,
        specs=_specs(dry_path, tmp_path / "missing-exp.db", refresh_path),
        backup_dir=str(tmp_path / "backups"),
        output_path=str(tmp_path / "migration.json"),
    )

    assert document["verdict"] == "BREACH"
    assert document["sources"]["dry_run"]["status"] == "MIGRATED"
    assert document["sources"]["data_refresh"] == {
        "status": "BREACH",
        "error_type": "RefreshHistoryIntegrityError",
        "path": str(refresh_path),
        "source_preserved": True,
    }
    assert refresh_path.read_bytes() == refresh_before
    assert "tampered" not in json.dumps(document)


def test_partial_target_schema_is_rejected_without_mutation(tmp_path):
    dry_path = tmp_path / "dry.db"
    _legacy_dry_run(dry_path)
    connection = sqlite3.connect(dry_path)
    connection.execute(
        "CREATE TABLE dry_run_outbox(outbox_seq INTEGER PRIMARY KEY)"
    )
    connection.commit()
    connection.close()
    before = dry_path.read_bytes()

    document = migrate_domain_event_owner_stores(
        apply=True,
        specs=_specs(dry_path, tmp_path / "missing-exp.db", tmp_path / "missing-ref.db"),
        backup_dir=str(tmp_path / "backups"),
        output_path=str(tmp_path / "migration.json"),
    )

    assert document["verdict"] == "BREACH"
    assert document["sources"]["dry_run"]["error_type"] == "PartialMigrationError"
    assert dry_path.read_bytes() == before


def test_active_wal_sidecar_blocks_atomic_replacement(tmp_path):
    dry_path = tmp_path / "dry.db"
    _legacy_dry_run(dry_path)
    before = dry_path.read_bytes()
    (tmp_path / "dry.db-wal").write_bytes(b"active-writer")

    document = migrate_domain_event_owner_stores(
        apply=True,
        specs=_specs(dry_path, tmp_path / "missing-exp.db", tmp_path / "missing-ref.db"),
        backup_dir=str(tmp_path / "backups"),
        output_path=str(tmp_path / "migration.json"),
    )

    assert document["sources"]["dry_run"]["error_type"] == "ActiveSqliteSidecarError"
    assert dry_path.read_bytes() == before


def test_current_store_is_idempotent_and_does_not_create_backup(tmp_path):
    dry_path = tmp_path / "dry.db"
    store = DryRunStore(str(dry_path), create=True)
    store.close()
    before = dry_path.read_bytes()
    backup_dir = tmp_path / "backups"

    document = migrate_domain_event_owner_stores(
        apply=True,
        specs=_specs(dry_path, tmp_path / "missing-exp.db", tmp_path / "missing-ref.db"),
        backup_dir=str(backup_dir),
        output_path=str(tmp_path / "migration.json"),
    )

    assert document["verdict"] == "PARTIAL"
    assert document["sources"]["dry_run"]["status"] == "CURRENT"
    assert dry_path.read_bytes() == before
    assert not backup_dir.exists()
