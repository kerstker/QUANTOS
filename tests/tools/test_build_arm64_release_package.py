from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess

import numpy as np
import pytest

from quantos.contracts.l2_schedule_policy import (
    HARD_QUARANTINED_MODULES,
    MVP_DISABLED_MODULES,
    MVP_SAFE_MODULES,
)
from tools.build_arm64_release_package import (
    CONSOLE_UNITS,
    DEFAULT_WORKER_MODULES,
    FRONTEND_BUILD_CONTRACT,
    FRONTEND_BUILD_SCHEMA,
    FRONTEND_RUNTIME_CONTRACT,
    FRONTEND_RUNTIME_SCHEMA,
    MIN_ADV_CNY,
    RELEASE_TOKEN,
    ReleasePackageError,
    SAME_ORIGIN_MARKER,
    SPECIAL_UNITS,
    SUPPORT_UNITS,
    SYSTEMD_TEMPLATE_ROOT,
    TDX_V2_SNAPSHOT_TABLES,
    _assert_rendered_console_unit,
    _render_profile_units,
    _rendered_research_runner_unit_text,
    _write_sha256sums,
    _verify_packaged_systemd_contract,
    inspect_tdx_panel,
    inspect_tdx_sqlite,
    render_deploy_script,
    sha256_file,
    tree_manifest,
    verify_package,
)
from tools.ashare_alpha_search_technical import _load_panel
from tools.ashare_tdx_intake import _REQUIRED_SCHEMA_COLUMNS


_CAPTURE_DATE = 20260830
_CAPTURE_MANIFEST = "a" * 64
_CAPTURE_ROOT = "/fixture/tdx-windows-20260830"


def _capture_report() -> dict[str, object]:
    verification = {
        "status": "PASS",
        "manifest_sha256": _CAPTURE_MANIFEST,
        "files": 3,
        "bytes": 4096,
    }
    return {
        "schema_version": 2,
        "evidence_status": "TECHNICAL_VALIDATION_ONLY",
        "source_kind": "TONGDAXIN_LOCAL_DAILY_AND_CURRENT_METADATA",
        "capture_date": _CAPTURE_DATE,
        "source_root": _CAPTURE_ROOT,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "sha256_verification": verification,
        "pre_publish_sha256_verification": dict(verification),
        "rows_total": 3,
        "rows": 3,
        "instruments": 2,
        "first_date": 20260827,
        "last_date": 20260828,
    }


def _write_sqlite(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE daily_prices("
            "instrument TEXT NOT NULL,trade_date INTEGER NOT NULL,"
            "knowledge_time INTEGER NOT NULL,open REAL NOT NULL,high REAL NOT NULL,"
            "low REAL NOT NULL,close REAL NOT NULL,amount REAL NOT NULL,"
            "volume INTEGER NOT NULL,reserved INTEGER NOT NULL,"
            "PRIMARY KEY(instrument,trade_date)) WITHOUT ROWID"
        )
        connection.execute(
            "CREATE TABLE source_captures("
            "capture_date INTEGER PRIMARY KEY,"
            "manifest_sha256 TEXT NOT NULL UNIQUE,"
            "source_root TEXT NOT NULL,"
            "source_report_json TEXT NOT NULL)"
        )
        for table, columns in _REQUIRED_SCHEMA_COLUMNS.items():
            if table in {"daily_prices", "source_captures"}:
                continue
            definitions = ",".join(f'"{column}" TEXT' for column in sorted(columns))
            connection.execute(f'CREATE TABLE "{table}"({definitions})')
        connection.execute("PRAGMA user_version=2")
        connection.executemany(
            "INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                ("000001.SZ", 20260827, 20260827, 10, 11, 9, 10.5, 1000, 100, 0),
                ("000001.SZ", 20260828, 20260828, 10.5, 12, 10, 11, 1200, 110, 0),
                ("600000.SH", 20260828, 20260828, 8, 9, 7, 8.5, 900, 90, 0),
            ),
        )
        connection.execute(
            "INSERT INTO source_captures VALUES (?,?,?,?)",
            (
                _CAPTURE_DATE,
                _CAPTURE_MANIFEST,
                _CAPTURE_ROOT,
                json.dumps(_capture_report(), sort_keys=True),
            ),
        )
        connection.executemany(
            "INSERT INTO universe_snapshots("
            "capture_date,instrument,market,code,authority_mode) VALUES (?,?,?,?,?)",
            (
                (_CAPTURE_DATE, "000001.SZ", "sz", "000001", "CURRENT"),
                (_CAPTURE_DATE, "600000.SH", "sh", "600000", "CURRENT"),
            ),
        )
        connection.executemany(
            "INSERT INTO security_master_snapshots("
            "capture_date,instrument,market,code,name,is_st,authority_mode) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                (_CAPTURE_DATE, "000001.SZ", "sz", "000001", "A", "0", "CURRENT"),
                (_CAPTURE_DATE, "600000.SH", "sh", "600000", "B", "0", "CURRENT"),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _rewrite_panel(path: Path, mutate) -> None:
    with np.load(path, allow_pickle=False) as source:
        fields = {key: source[key].copy() for key in source.files}
    mutate(fields)
    temporary = path.with_name(f".{path.name}.rewrite.npz")
    np.savez_compressed(temporary, **fields)
    temporary.replace(path)


def _write_panel(path: Path, database: Path, *, digest: str | None = None) -> None:
    rebuilt, metadata = _load_panel(
        database,
        min_adv_cny=MIN_ADV_CNY,
        panel_cache=path,
    )
    del rebuilt, metadata
    if digest is not None:
        _rewrite_panel(
            path,
            lambda fields: fields.__setitem__("db_sha256", np.array(digest)),
        )


def _refresh_test_package_integrity(package: Path, manifest: dict[str, object]) -> None:
    rows, _digest = tree_manifest(package)
    manifest["package_files"] = {
        name: digest
        for name, digest in rows.items()
        if name not in {"PACKAGE_MANIFEST.json", "SHA256SUMS"}
    }
    (package / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_sha256sums(package)


def _write_complete_verifier_package(
    package: Path,
    *,
    release: str = "a" * 40,
) -> dict[str, object]:
    frontend_source_sha256 = "f" * 64
    package.mkdir()
    payload = package / "payload.txt"
    payload.write_text("immutable", encoding="utf-8")
    deploy = package / "deploy.sh"
    deploy.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    deploy.chmod(0o755)

    frontend = package / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    (frontend / FRONTEND_BUILD_CONTRACT).write_text(
        json.dumps(
            {
                "schema": FRONTEND_BUILD_SCHEMA,
                "release_commit": release,
                "frontend_source_sha256": frontend_source_sha256,
                "api_url": SAME_ORIGIN_MARKER,
                "runner_url": SAME_ORIGIN_MARKER,
                "mock_mode": "false",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (frontend / FRONTEND_RUNTIME_CONTRACT).write_text(
        json.dumps(
            {
                "schema": FRONTEND_RUNTIME_SCHEMA,
                "release_commit": release,
                "api_url": SAME_ORIGIN_MARKER,
                "runner_url": SAME_ORIGIN_MARKER,
                "mock_mode": False,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    frontend_rows, frontend_digest = tree_manifest(frontend)
    unit_rows, unit_digest = _render_profile_units(
        package / "units",
        release_commit=release,
        backup_mount="/mnt/quantos-backup",
    )
    manifest: dict[str, object] = {
        "schema": "quantos.arm64_release_package.v1",
        "release": {"commit": release},
        "frontend": {
            "tree_sha256": frontend_digest,
            "file_count": len(frontend_rows),
            "runtime_contract": FRONTEND_RUNTIME_CONTRACT,
            "source_sha256": frontend_source_sha256,
        },
        "systemd": {
            "worker_modules": list(DEFAULT_WORKER_MODULES),
            "special_units": list(SPECIAL_UNITS),
            "support_units": list(SUPPORT_UNITS),
            "unit_tree_sha256": unit_digest,
            "unit_files": unit_rows,
            "backup_mount": "/mnt/quantos-backup",
            "attention_release_commit": release,
        },
        "installer": {
            "path": "deploy.sh",
            "sha256": sha256_file(deploy),
            "transaction": "QUIESCED_ROLLBACKABLE_ARM64_CUTOVER",
        },
    }
    _refresh_test_package_integrity(package, manifest)
    return manifest


def test_release_profile_adds_only_admitted_ashare_workers() -> None:
    requested = set(DEFAULT_WORKER_MODULES)
    assert {"ashare_alpha_search_worker", "ashare_forward_observation"} <= requested
    assert requested <= MVP_SAFE_MODULES
    assert not requested & HARD_QUARANTINED_MODULES
    assert not requested & MVP_DISABLED_MODULES


def test_tree_manifest_is_deterministic_and_rejects_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "b.txt").write_text("b", encoding="utf-8")
    (root / "a.txt").write_text("a", encoding="utf-8")
    first = tree_manifest(root)
    second = tree_manifest(root)
    assert first == second
    assert list(first[0]) == ["a.txt", "b.txt"]
    (root / "link").symlink_to(root / "a.txt")
    with pytest.raises(ReleasePackageError, match="symlink"):
        tree_manifest(root)


def test_tdx_contract_binds_panel_to_read_only_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "ashare_tdx_pit.sqlite"
    panel = tmp_path / "ashare_tdx_pit.adv20000000.panel.npz"
    _write_sqlite(database)
    database_contract = inspect_tdx_sqlite(database)
    _write_panel(panel, database)
    panel_contract = inspect_tdx_panel(
        panel,
        database_path=database,
        database_sha256=database_contract["sha256"],
    )
    assert database_contract == {
        "path": str(database.resolve()),
        "sha256": sha256_file(database),
        "bytes": database.stat().st_size,
        "rows": 3,
        "instruments": 2,
        "dates": 2,
        "first_date": 20260827,
        "last_date": 20260828,
        "future_known_rows": 0,
        "quick_check": "ok",
            "schema_version": 2,
            "snapshot_rows": {
                table: (
                    1
                    if table == "source_captures"
                    else 2
                    if table in {"universe_snapshots", "security_master_snapshots"}
                    else 0
                )
                for table in TDX_V2_SNAPSHOT_TABLES
            },
        "capture_first": 20260830,
        "capture_last": 20260830,
    }
    assert panel_contract["database_sha256"] == database_contract["sha256"]
    assert panel_contract["min_adv_cny"] == MIN_ADV_CNY

    wrong = tmp_path / "wrong.npz"
    _write_panel(wrong, database, digest="f" * 64)
    with pytest.raises(ReleasePackageError, match="not bound"):
        inspect_tdx_panel(
            wrong,
            database_path=database,
            database_sha256=database_contract["sha256"],
        )


def test_tdx_sqlite_rejects_fake_single_column_capture_table(tmp_path: Path) -> None:
    database = tmp_path / "ashare_tdx_pit.sqlite"
    _write_sqlite(database)
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE source_captures RENAME TO fake_captures")
        connection.execute("CREATE TABLE source_captures(capture_date INTEGER)")
        connection.execute("INSERT INTO source_captures VALUES (?)", (_CAPTURE_DATE,))
        connection.execute("DROP TABLE fake_captures")

    with pytest.raises(ReleasePackageError, match="lacks required columns"):
        inspect_tdx_sqlite(database)


def test_tdx_sqlite_requires_at_least_one_capture_report(tmp_path: Path) -> None:
    database = tmp_path / "ashare_tdx_pit.sqlite"
    _write_sqlite(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM source_captures")

    with pytest.raises(ReleasePackageError, match="no provenance rows"):
        inspect_tdx_sqlite(database)


def test_tdx_sqlite_rejects_snapshot_table_schema_drift(tmp_path: Path) -> None:
    database = tmp_path / "ashare_tdx_pit.sqlite"
    _write_sqlite(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE universe_snapshots")
        connection.execute(
            "CREATE TABLE universe_snapshots("
            "capture_date INTEGER,instrument TEXT,market TEXT,code TEXT)"
        )

    with pytest.raises(ReleasePackageError, match="lacks required columns"):
        inspect_tdx_sqlite(database)


def test_tdx_sqlite_accepts_incremental_latest_source_row_count(tmp_path: Path) -> None:
    database = tmp_path / "ashare_tdx_pit.sqlite"
    _write_sqlite(database)
    report = _capture_report()
    report["rows"] = 2
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE source_captures SET source_report_json=?",
            (json.dumps(report, sort_keys=True),),
        )

    assert inspect_tdx_sqlite(database)["rows"] == 3


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda report: report["sha256_verification"].__setitem__(
                "status", "NOT_RERUN"
            ),
            "sha256 verification is not PASS",
        ),
        (
            lambda report: report.__setitem__("paper_persistence_allowed", 0),
            "permits paper persistence",
        ),
        (
            lambda report: report["sha256_verification"].__setitem__("files", 0),
            "invalid sha256_verification.files",
        ),
        (
            lambda report: report.__setitem__("capture_date", 20260829),
            "invalid capture_date",
        ),
        (
            lambda report: report.__setitem__("source_root", "/different/source"),
            "invalid source_root",
        ),
        (
            lambda report: report["sha256_verification"].__setitem__(
                "manifest_sha256", "b" * 64
            ),
            "manifest identity drifted",
        ),
        (
            lambda report: report["pre_publish_sha256_verification"].__setitem__(
                "status", "NOT_RERUN"
            ),
            "pre-publish verification is not PASS",
        ),
        (
            lambda report: report["pre_publish_sha256_verification"].__setitem__(
                "bytes", 4097
            ),
            "pre-publish identity drifted",
        ),
        (
            lambda report: report.__setitem__("rows_total", 4),
            "rows_total does not match daily_prices",
        ),
    ),
    ids=(
        "not-rerun",
        "non-boolean-paper-flag",
        "empty-manifest",
        "capture-date-identity",
        "source-root-identity",
        "manifest-identity",
        "pre-publish-not-rerun",
        "pre-publish-identity",
        "latest-stats",
    ),
)
def test_tdx_sqlite_rejects_untrusted_capture_report(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    database = tmp_path / "ashare_tdx_pit.sqlite"
    _write_sqlite(database)
    report = _capture_report()
    mutation(report)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE source_captures SET source_report_json=?",
            (json.dumps(report, sort_keys=True),),
        )

    with pytest.raises(ReleasePackageError, match=message):
        inspect_tdx_sqlite(database)


@pytest.mark.parametrize(
    ("raw_report", "message"),
    (
        ("[]", "must be a JSON object"),
        (
            json.dumps(_capture_report(), sort_keys=True).replace(
                '"schema_version": 2',
                '"schema_version": 2, "schema_version": 2',
                1,
            ),
            "duplicate key: schema_version",
        ),
    ),
    ids=("list", "duplicate-key"),
)
def test_tdx_sqlite_requires_strict_capture_report_object(
    tmp_path: Path,
    raw_report: str,
    message: str,
) -> None:
    database = tmp_path / "ashare_tdx_pit.sqlite"
    _write_sqlite(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE source_captures SET source_report_json=?",
            (raw_report,),
        )

    with pytest.raises(ReleasePackageError, match=message):
        inspect_tdx_sqlite(database)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda fields: fields.pop("opens"), "field contract drifted"),
        (
            lambda fields: fields["closes"].__setitem__(
                (0, 0), fields["closes"][0, 0] + 1.0
            ),
            "value mismatch: closes",
        ),
        (
            lambda fields: fields.__setitem__(
                "dates", fields["dates"].astype(np.int32)
            ),
            "dtype mismatch: dates",
        ),
        (
            lambda fields: fields.__setitem__("opens", fields["opens"][:-1]),
            "shape mismatch: opens",
        ),
        (
            lambda fields: fields.__setitem__(
                "metadata_json",
                np.array(
                    json.dumps(
                        {
                            **json.loads(str(fields["metadata_json"].item())),
                            "rows": 9,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                ),
            ),
            "value mismatch: metadata",
        ),
    ),
    ids=("missing", "tampered", "dtype", "shape", "metadata"),
)
def test_tdx_panel_rejects_any_drift_from_sqlite_rebuild(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    database = tmp_path / "ashare_tdx_pit.sqlite"
    panel = tmp_path / "ashare_tdx_pit.adv20000000.panel.npz"
    _write_sqlite(database)
    database_contract = inspect_tdx_sqlite(database)
    _write_panel(panel, database)
    _rewrite_panel(panel, mutation)

    with pytest.raises(ReleasePackageError, match=message):
        inspect_tdx_panel(
            panel,
            database_path=database,
            database_sha256=database_contract["sha256"],
        )


def test_renderer_emits_complete_runner_and_ashare_profile(tmp_path: Path) -> None:
    destination = tmp_path / "units"
    release = "a" * 40
    rows, digest = _render_profile_units(
        destination,
        release_commit=release,
        backup_mount="/mnt/quantos-backup",
    )
    assert len(digest) == 64
    assert "workers/quantos-l2@ashare_alpha_search_worker.timer" in rows
    assert "workers/quantos-l2@ashare_forward_observation.timer" in rows
    assert {f"support/{name}" for name in SUPPORT_UNITS} <= set(rows)
    runner = (destination / "support/quantos-research-runner.service").read_text(
        encoding="utf-8"
    )
    assert "--host 127.0.0.1 --port 8420" in runner
    assert "QUANTOS_REQUIRE_EXISTING_RUNNER_TOKEN=true" in runner
    assert "ALLOW_ORDER_SUBMISSION=false" in runner
    assert "SystemCallArchitectures=native" in runner.splitlines()
    assert "__QUANTOS_RELEASE_SHA__" not in runner
    assert runner == _rendered_research_runner_unit_text(release_commit=release)

    # The packaged support units are byte-for-byte renders of reviewed source,
    # and their exact hashes participate in the complete unit-tree digest.
    for name in CONSOLE_UNITS:
        template = (SYSTEMD_TEMPLATE_ROOT / name).read_text(encoding="utf-8")
        assert RELEASE_TOKEN in template
        assert re.search(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", template) is None
        expected = template.replace(RELEASE_TOKEN, release).rstrip()
        expected += "\n\n[Install]\nWantedBy=multi-user.target\n"
        path = destination / "support" / name
        assert path.read_text(encoding="utf-8") == expected
        assert rows[f"support/{name}"] == sha256_file(path)
    assert tree_manifest(destination) == (rows, digest)


def test_rendered_console_units_have_exact_release_paths_and_hardening(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "units"
    release = "b" * 40
    _render_profile_units(
        destination,
        release_commit=release,
        backup_mount="/mnt/quantos-backup",
    )
    common = {
        "Environment=LIVE_TRADING_ENABLED=false",
        "Environment=REAL_BROKER_ENABLED=false",
        "Environment=ALLOW_ORDER_SUBMISSION=false",
        "Environment=ALLOW_AI_DIRECT_TRADING=false",
        "Environment=REQUIRE_HUMAN_APPROVAL=true",
        "Environment=QUANTOS_L2_DRY_RUN_ONLY=true",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "PrivateTmp=true",
        "PrivateDevices=true",
        "ProtectKernelTunables=true",
        "ProtectKernelModules=true",
        "ProtectKernelLogs=true",
        "ProtectControlGroups=true",
        "ProtectClock=true",
        "ProtectHostname=true",
        "RestrictSUIDSGID=true",
        "LockPersonality=true",
        "RestrictRealtime=true",
        "RestrictNamespaces=true",
        "SystemCallArchitectures=native",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "IPAddressDeny=any",
    }
    for name in CONSOLE_UNITS:
        content = (destination / "support" / name).read_text(encoding="utf-8")
        lines = content.splitlines()
        assert not common - set(lines)
        assert "EnvironmentFile=" not in content
        assert "LoadCredential=" not in content
        assert RELEASE_TOKEN not in content
        assert set(
            re.findall(
                r"/opt/quantos/(?:releases|venvs|frontend)/([0-9a-f]{40})",
                content,
            )
        ) == {release}
        assert len([line for line in lines if line.startswith("ExecStart=")]) == 1
    api = (destination / "support/quantos-console-api.service").read_text(
        encoding="utf-8"
    )
    frontend = (
        destination / "support/quantos-console-frontend.service"
    ).read_text(encoding="utf-8")
    assert (
        f"ExecStart=/opt/quantos/venvs/{release}/bin/python -P -m uvicorn "
        f"--app-dir /opt/quantos/releases/{release} services.console.main:app "
        "--host 127.0.0.1 --port 8400"
    ) in api
    assert f"Environment=PYTHONPATH=/opt/quantos/releases/{release}" in api
    assert (
        f"ExecStart=/opt/quantos/venvs/{release}/bin/python -P "
        f"/opt/quantos/releases/{release}/tools/local_frontend_server.py "
        "--host 192.168.5.68 --port 3100 --allow-network 192.168.5.0/24"
    ) in frontend
    assert f"Environment=QUANTOS_FRONTEND_DIST=/opt/quantos/frontend/{release}" in frontend
    assert f"WorkingDirectory=/opt/quantos/frontend/{release}" in frontend


def test_console_unit_contract_rejects_mixed_mutable_or_weakened_text(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "units"
    release = "c" * 40
    _render_profile_units(
        destination,
        release_commit=release,
        backup_mount="/mnt/quantos-backup",
    )
    name = "quantos-console-api.service"
    healthy = (destination / "support" / name).read_text(encoding="utf-8")
    with pytest.raises(ReleasePackageError, match="mixed release bindings"):
        _assert_rendered_console_unit(
            name,
            healthy.replace(release, "d" * 40, 1),
            release_commit=release,
        )
    with pytest.raises(ReleasePackageError, match="mutable environment input"):
        _assert_rendered_console_unit(
            name,
            healthy + "EnvironmentFile=/etc/quantos/mutable.env\n",
            release_commit=release,
        )
    with pytest.raises(ReleasePackageError, match="exact contract drifted"):
        _assert_rendered_console_unit(
            name,
            healthy.replace("NoNewPrivileges=true\n", "", 1),
            release_commit=release,
        )
    for extra in (
        "Environment=LIVE_TRADING_ENABLED=true\n",
        "User=root\n",
        "ExecStartPost=/bin/true\n",
    ):
        with pytest.raises(ReleasePackageError, match="exact contract drifted"):
            _assert_rendered_console_unit(
                name,
                healthy + extra,
                release_commit=release,
            )
    moved_user = healthy.replace("User=quantos\n", "", 1).replace(
        "\n[Service]\n",
        "\nUser=quantos\n\n[Service]\n",
        1,
    )
    with pytest.raises(ReleasePackageError, match="exact contract drifted"):
        _assert_rendered_console_unit(
            name,
            moved_user,
            release_commit=release,
        )


def test_package_systemd_contract_rejects_rehashed_console_text_drift(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    release = "e" * 40
    rows, digest = _render_profile_units(
        package / "units",
        release_commit=release,
        backup_mount="/mnt/quantos-backup",
    )
    manifest = {
        "release": {"commit": release},
        "systemd": {
            "worker_modules": list(DEFAULT_WORKER_MODULES),
            "special_units": [
                "quantos-binance-realtime-stream.service",
                "quantos-bybit-realtime-stream.service",
                "quantos-l2-watchdog.service",
                "quantos-l2-watchdog.timer",
            ],
            "support_units": list(SUPPORT_UNITS),
            "unit_tree_sha256": digest,
            "unit_files": rows,
            "backup_mount": "/mnt/quantos-backup",
            "attention_release_commit": release,
        },
    }
    _verify_packaged_systemd_contract(package, manifest)

    api = package / "units/support/quantos-console-api.service"
    api.write_text(
        api.read_text(encoding="utf-8").replace("NoNewPrivileges=true\n", "", 1),
        encoding="utf-8",
    )
    rehashed_rows, rehashed_digest = tree_manifest(package / "units")
    manifest["systemd"]["unit_files"] = rehashed_rows
    manifest["systemd"]["unit_tree_sha256"] = rehashed_digest
    with pytest.raises(ReleasePackageError, match="rendered systemd unit tree drifted"):
        _verify_packaged_systemd_contract(package, manifest)


def test_package_verifier_binds_exact_payload_and_sha256sums(tmp_path: Path) -> None:
    package = tmp_path / "package"
    manifest = _write_complete_verifier_package(package)
    assert verify_package(package, verify_external_data=False) == manifest
    (package / "payload.txt").write_text("drift", encoding="utf-8")
    with pytest.raises(ReleasePackageError, match="payload hash"):
        verify_package(package, verify_external_data=False)


def test_v1_verifier_rejects_deleted_systemd_or_frontend_contract(
    tmp_path: Path,
) -> None:
    for missing, message in (
        ("systemd", "systemd or release contract is invalid"),
        ("frontend", "packaged frontend release contract is absent"),
    ):
        package = tmp_path / missing
        manifest = _write_complete_verifier_package(package)
        del manifest[missing]
        _refresh_test_package_integrity(package, manifest)
        with pytest.raises(ReleasePackageError, match=message):
            verify_package(package, verify_external_data=False)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("release_commit", "b" * 40),
        ("frontend_source_sha256", "e" * 64),
    ),
)
def test_v1_verifier_rejects_rehashed_stale_frontend_identity(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    package = tmp_path / "package"
    manifest = _write_complete_verifier_package(package)
    contract_path = package / "frontend" / FRONTEND_BUILD_CONTRACT
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract[field] = value
    contract_path.write_text(
        json.dumps(contract, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    frontend_rows, frontend_digest = tree_manifest(package / "frontend")
    frontend = manifest["frontend"]
    assert isinstance(frontend, dict)
    frontend["tree_sha256"] = frontend_digest
    frontend["file_count"] = len(frontend_rows)
    _refresh_test_package_integrity(package, manifest)

    with pytest.raises(ReleasePackageError, match="frontend build"):
        verify_package(package, verify_external_data=False)


def test_v1_verifier_rejects_rehashed_research_runner_text_drift(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    manifest = _write_complete_verifier_package(package)
    runner = package / "units/support/quantos-research-runner.service"
    runner.write_text(
        runner.read_text(encoding="utf-8") + "# rehashed runner tamper\n",
        encoding="utf-8",
    )
    unit_rows, unit_digest = tree_manifest(package / "units")
    systemd = manifest["systemd"]
    assert isinstance(systemd, dict)
    systemd["unit_files"] = unit_rows
    systemd["unit_tree_sha256"] = unit_digest
    _refresh_test_package_integrity(package, manifest)

    with pytest.raises(
        ReleasePackageError,
        match="rendered systemd unit tree drifted",
    ):
        verify_package(package, verify_external_data=False)


@pytest.mark.parametrize(
    "relative",
    (
        "workers/quantos-l2@cross_venue_liquidation_health.service",
        "special/quantos-binance-realtime-stream.service",
    ),
    ids=("worker", "special"),
)
def test_v1_verifier_rejects_rehashed_worker_or_special_text_drift(
    tmp_path: Path,
    relative: str,
) -> None:
    package = tmp_path / "package"
    manifest = _write_complete_verifier_package(package)
    unit = package / "units" / relative
    unit.write_text(
        unit.read_text(encoding="utf-8") + "# rehashed unit tamper\n",
        encoding="utf-8",
    )
    unit_rows, unit_digest = tree_manifest(package / "units")
    systemd = manifest["systemd"]
    assert isinstance(systemd, dict)
    systemd["unit_files"] = unit_rows
    systemd["unit_tree_sha256"] = unit_digest
    _refresh_test_package_integrity(package, manifest)

    with pytest.raises(ReleasePackageError, match="rendered systemd unit tree drifted"):
        verify_package(package, verify_external_data=False)


def test_deploy_renderer_is_native_transactional_and_shell_valid(
    tmp_path: Path,
) -> None:
    script = render_deploy_script(
        tmp_path / "deploy.sh",
        package_root=tmp_path / "package with spaces",
        release_commit="a" * 40,
        source_commit="b" * 40,
        evidence_manifest_sha256="c" * 64,
        bundle_name="quantos-aaaaaaaa.bundle",
        bundle_sha256="d" * 64,
        frontend_tree_sha256="e" * 64,
        backup_mount="/mnt/quantos-backup",
        baseline={
            "commit": "f" * 40,
            "root": "/opt/quantos/releases/" + "f" * 40,
            "base_venv": "/opt/quantos/venvs/native-arm64",
        },
        sqlite_contract={
            "path": "/srv/intake/ashare_tdx_pit.sqlite",
            "sha256": "1" * 64,
            "rows": 6_267_665,
            "instruments": 5_549,
            "dates": 1_231,
            "first_date": 20210802,
            "last_date": 20260828,
        },
        panel_contract={
            "path": "/srv/intake/ashare_tdx_pit.adv20000000.panel.npz",
            "sha256": "2" * 64,
        },
    )
    mode = script.stat().st_mode
    assert stat.S_ISREG(mode)
    assert mode & 0o111
    subprocess.run(
        ["bash", "-n", str(script)],
        check=True,
        capture_output=True,
        text=True,
    )
    content = script.read_text(encoding="utf-8")
    assert "__DEPLOY_" not in content
    assert "[[ $(uname -m) == aarch64 ]]" in content
    assert "x86_64" not in content
    assert "docker" not in content
    assert "ALLOW_ORDER_SUBMISSION=false" in content
    assert "REAL_BROKER_ENABLED=false" in content
    assert "--submit" not in content
    assert "recover_preexisting_worker_failures" not in content
    assert "backup_readiness_preflight" not in content
    assert "core_refresh:wikimedia" not in content
    assert 'systemctl enable "${RESIDENT_UNITS[@]}"' in content
    assert 'systemctl enable "${WORKER_TIMERS[@]}"' in content
    assert 'systemctl start "${EARLY_WORKER_TIMERS[@]}"' in content
    assert "systemctl start quantos-l2@ashare_forward_observation.timer" in content
    assert "RENAME_EXCHANGE = 2" in content
    assert 'exchange_directories "$TDX_STATE_DIR" "$TDX_PAIR_STAGE"' in content
    assert content.index("PAIR_REPLACED=1") > content.index(
        'exchange_directories "$TDX_STATE_DIR" "$TDX_PAIR_STAGE"'
    )
    assert "TDX_EXISTING_FORWARD_PREFIX_COMPATIBILITY" not in content
    assert "ASHARE_FORWARD_PREFIX_COMPATIBILITY_PASS" in content
    assert "TRUSTED_PACKAGE_PARENT=/var/lib/quantos-deploy/packages" in content
    assert "PACKAGE_DIR=$TRUSTED_PACKAGE_DIR" in content
    assert "result\" == success" in content
    assert "quantos-research-runner.service" in content
    assert "QUANTOS_REQUIRE_EXISTING_RUNNER_TOKEN" not in content
    assert "research_runner_token.txt" in content
    assert 'chown quantos:quantos "$RUNNER_TOKEN"' in content
    assert 'chmod 0600 "$RUNNER_TOKEN"' in content
    assert '[[ -f "$RUNNER_TOKEN" && ! -L "$RUNNER_TOKEN" ]]' in content
    assert '[[ $(stat -c \'%s\' "$RUNNER_TOKEN") -ge 32 ]]' in content


def test_deploy_search_forward_watchdog_order_and_lock_race_are_pinned(
    tmp_path: Path,
) -> None:
    script = render_deploy_script(
        tmp_path / "deploy.sh",
        package_root=tmp_path / "package",
        release_commit="a" * 40,
        source_commit="b" * 40,
        evidence_manifest_sha256="c" * 64,
        bundle_name="quantos.bundle",
        bundle_sha256="d" * 64,
        frontend_tree_sha256="e" * 64,
        backup_mount="/mnt/quantos-backup",
        baseline={
            "commit": "f" * 40,
            "root": "/opt/quantos/releases/" + "f" * 40,
            "base_venv": "/opt/quantos/venvs/native-arm64",
        },
        sqlite_contract={
            "path": "/srv/intake/db.sqlite",
            "sha256": "1" * 64,
            "rows": 1,
            "instruments": 1,
            "dates": 1,
            "first_date": 20260828,
            "last_date": 20260828,
        },
        panel_contract={"path": "/srv/intake/panel.npz", "sha256": "2" * 64},
    )
    content = script.read_text(encoding="utf-8")
    assert 'lslocks --pid "$main_pid"' in content
    assert 'main_pid=$(systemctl show "$unit" -p MainPID --value' in content
    assert '$1 == pid && $2 == "FLOCK" && $3 == path' in content
    assert "a globally busy lock are both insufficient" in content
    assert 'for command in flock git lslocks runuser' in content
    completion_body = content[
        content.index("wait_for_unit_completion() {") : content.index(
            "wait_for_new_unit_invocation() {"
        )
    ]
    invocation_body = content[
        content.index("wait_for_new_unit_invocation() {") : content.index(
            "start_unit_and_wait() {"
        )
    ]
    start_body = content[
        content.index("start_unit_and_wait() {") : content.index(
            "start_search_and_wait_for_lock() {"
        )
    ]
    search_body = content[
        content.index("start_search_and_wait_for_lock() {") : content.index(
            "snapshot_runtime() {"
        )
    ]
    assert 'invocation=$(systemctl show "$unit" -p InvocationID --value' in completion_body
    assert completion_body.count('"$invocation" != "$expected_invocation"') == 2
    assert completion_body.index("InvocationID --value") < completion_body.index(
        'result=$(systemctl show "$unit" -p Result --value)'
    )
    assert '[[ "$invocation" =~ ^[0-9a-f]{32}$' in invocation_body
    assert '"$invocation" != "$previous_invocation"' in invocation_body
    assert "NEW_INVOCATION_ID=$invocation" in invocation_body
    assert "waiting=new_invocation" in invocation_body
    assert "unit did not begin a new invocation" in invocation_body
    assert start_body.index("previous_invocation=$(systemctl show") < start_body.index(
        'systemctl start --no-block "$unit"'
    )
    assert start_body.index('systemctl start --no-block "$unit"') < start_body.index(
        'wait_for_new_unit_invocation "$unit" "$previous_invocation"'
    )
    assert start_body.index("wait_for_new_unit_invocation") < start_body.index(
        "wait_for_unit_completion"
    )
    assert search_body.index("previous_invocation=$(systemctl show") < search_body.index(
        'systemctl start --no-block "$unit"'
    )
    assert search_body.index('systemctl start --no-block "$unit"') < search_body.index(
        'wait_for_new_unit_invocation "$unit" "$previous_invocation" 120'
    )
    assert "ASHARE_SEARCH_INVOCATION_ID=$NEW_INVOCATION_ID" in search_body
    assert '"$invocation" != "$ASHARE_SEARCH_INVOCATION_ID"' in search_body
    transaction_commit = content.index("\nSUCCESS=1\n")
    post_commit_trap = content.index(
        "\ntrap recover_post_commit_schedulers_on_exit EXIT\n",
        transaction_commit,
    )
    initial_search = content.index("\nstart_search_and_wait_for_lock\n", transaction_commit)
    initial_forward = content.index(
        "\nstart_unit_and_wait quantos-l2@ashare_forward_observation.service",
        initial_search,
    )
    timers_activate = content.index(
        "\nstage_start parallel_worker_timers_activate_post_commit",
        initial_forward,
    )
    search_wait = content.index(
        "\nwait_for_unit_completion quantos-l2@ashare_alpha_search_worker.service",
        timers_activate,
    )
    final_forward = content.index(
        "\nstart_unit_and_wait quantos-l2@ashare_forward_observation.service",
        search_wait,
    )
    deferred_timers = content.index(
        "\nstage_start deferred_observation_timers_activate",
        final_forward,
    )
    final_watchdog = content.index(
        "\nstart_unit_and_wait quantos-l2-watchdog.service watchdog_post_search_probe",
        deferred_timers,
    )
    final_pass = content.index("printf 'DGX_ARM64_RELEASE_DEPLOY_PASS", final_watchdog)
    final_trap_clear = content.rindex("\ntrap - EXIT\n", final_watchdog, final_pass)
    assert transaction_commit < post_commit_trap < initial_search
    assert initial_search < initial_forward < timers_activate
    assert timers_activate < search_wait < final_forward < deferred_timers
    assert deferred_timers < final_watchdog < final_trap_clear < final_pass
    assert "ashare_search_completion 7200 '0'" in content
    assert (
        "ashare_search_completion 7200 '0' \"$ASHARE_SEARCH_INVOCATION_ID\""
        in content
    )
    assert "alpha_search=complete forward=initialized" in content
    assert "next_report=$((SECONDS + 16))" in content
    assert "quantos-mvp-console-api.service" in content
    assert "quantos-mvp-research-runner.service" in content
    assert "quantos-mvp-frontend.service" in content
    assert "PREVIOUS_ENABLED_TIMERS=()" in content
    snapshot_body = content[
        content.index("snapshot_runtime() {") : content.index("snapshot_alpha_runtime() {")
    ]
    assert snapshot_body.count("PREVIOUS_ENABLED_TIMERS=()") == 1
    assert (
        content.count(
            '"$SQLITE_INCOMING" "$PANEL_INCOMING" \\\n'
            '  "$TDX_SQLITE_SHA256" "$TDX_PANEL_SHA256"'
        )
        == 1
    )
    assert (
        content.count(
            '"$TDX_SQLITE" "$TDX_PANEL" "$TDX_SQLITE_SHA256" '
            '"$TDX_PANEL_SHA256"'
        )
        == 1
    )


@pytest.mark.parametrize("active_failure", [False, True])
def test_post_commit_failure_recovers_all_timers_and_preserves_original_rc(
    tmp_path: Path,
    active_failure: bool,
) -> None:
    template = (Path(__file__).parents[2] / "deploy/arm64/deploy.sh.in").read_text(
        encoding="utf-8"
    )
    function_text = template[
        template.index("recover_post_commit_schedulers_on_exit() {") : template.index(
            "\nrollback() {"
        )
    ]
    log = tmp_path / "systemctl.log"
    script = tmp_path / "exercise-post-commit-recovery.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        + function_text
        + "\nWORKER_TIMERS=(alpha.timer forward.timer)\n"
        + "CURRENT_STAGE=forced_failure\n"
        + f"SYSTEMCTL_LOG={str(log)!r}\n"
        + f"ACTIVE_FAILURE={1 if active_failure else 0}\n"
        + "systemctl() {\n"
        + "  printf '%s\\n' \"$*\" >>\"$SYSTEMCTL_LOG\"\n"
        + "  if [[ \"$1\" == is-active && \"$ACTIVE_FAILURE\" == 1 "
        + "&& \"${3:-}\" == forward.timer ]]; then\n"
        + "    return 1\n"
        + "  fi\n"
        + "  return 0\n"
        + "}\n"
        + "trap recover_post_commit_schedulers_on_exit EXIT\n"
        + "exit 23\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 23
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls[0] == "start alpha.timer forward.timer quantos-l2-watchdog.timer"
    assert calls[1:] == [
        "is-active --quiet alpha.timer",
        "is-active --quiet forward.timer",
        "is-active --quiet quantos-l2-watchdog.timer",
    ]
    if active_failure:
        assert "POST_COMMIT_TIMER_RECOVERY_UNIT_FAILURE unit=forward.timer" in result.stderr
        assert "POST_COMMIT_TIMER_RECOVERY_FAILURE original_rc=23" in result.stderr
    else:
        assert "POST_COMMIT_TIMER_RECOVERY_PASS original_rc=23" in result.stderr


def test_cleanup_does_not_disable_errexit_needed_by_post_commit_recovery(
    tmp_path: Path,
) -> None:
    template = (Path(__file__).parents[2] / "deploy/arm64/deploy.sh.in").read_text(
        encoding="utf-8"
    )
    function_text = template[
        template.index("cleanup_tmp() {") : template.index(
            "\nrecover_post_commit_schedulers_on_exit() {"
        )
    ]
    script = tmp_path / "exercise-cleanup-options.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        + function_text
        + "\nCLONE_TMP=''\n"
        + "TRUSTED_PACKAGE_TMP=''\n"
        + "TDX_PAIR_STAGE=''\n"
        + "TDX_STATE_PARENT=/unused\n"
        + "RELEASE_COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        + "PAIR_REPLACED=0\n"
        + "cleanup_tmp\n"
        + "[[ $- == *e* ]] || exit 91\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_first_install_missing_runner_is_skipped_by_quiesce_and_rollback(
    tmp_path: Path,
) -> None:
    template = (Path(__file__).parents[2] / "deploy/arm64/deploy.sh.in").read_text(
        encoding="utf-8"
    )
    resident_functions = template[
        template.index("unit_loaded() {") : template.index(
            "\nacquire_alpha_deploy_lock() {"
        )
    ]
    rollback = template[
        template.index("\nrollback() {") : template.index(
            "\non_exit() {", template.index("\nrollback() {")
        )
    ]
    assert (
        'stop_loaded_units_no_block "${RESIDENT_UNITS[@]}"' in rollback
    )
    assert 'systemctl stop --no-block "${RESIDENT_UNITS[@]}"' not in rollback

    log = tmp_path / "systemctl.log"
    script = tmp_path / "exercise-first-install-resident-stop.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        + "RESIDENT_UNITS=(quantos-console-api.service "
        + "quantos-research-runner.service quantos-console-frontend.service)\n"
        + "WORKER_TIMERS=()\n"
        + "CURRENT_STAGE=bootstrap\n"
        + "STAGE_STARTED_SECONDS=0\n"
        + "DEPLOY_STARTED_SECONDS=0\n"
        + "SWITCH_STARTED=1\n"
        + "PAIR_REPLACED=0\n"
        + "ROLLBACK_ROOT=/rollback-test\n"
        + "current_sqlite_hash=''\n"
        + "current_panel_hash=''\n"
        + "TDX_SQLITE=/missing/tdx.sqlite\n"
        + "TDX_PANEL=/missing/tdx.panel.npz\n"
        + f"SYSTEMCTL_LOG={str(log)!r}\n"
        + "stage_start() { CURRENT_STAGE=$1; }\n"
        + "stage_pass() { :; }\n"
        + "die() { return 1; }\n"
        + "systemctl() {\n"
        + "  printf '%s\\n' \"$*\" >>\"$SYSTEMCTL_LOG\"\n"
        + "  if [[ \"$1\" == show ]]; then\n"
        + "    if [[ \"$4\" == LoadState ]]; then\n"
        + "      if [[ \"$2\" == quantos-research-runner.service ]]; then\n"
        + "        printf 'not-found\\n'\n"
        + "      else\n"
        + "        printf 'loaded\\n'\n"
        + "      fi\n"
        + "    elif [[ \"$4\" == ActiveState ]]; then\n"
        + "      printf 'inactive\\n'\n"
        + "    fi\n"
        + "    return 0\n"
        + "  fi\n"
        + "  if [[ \"$1\" == stop "
        + "&& \"${3:-}\" == quantos-research-runner.service ]]; then\n"
        + "    return 5\n"
        + "  fi\n"
        + "  return 0\n"
        + "}\n"
        + resident_functions
        + rollback
        + "\nstop_known_timers() { :; }\n"
        + "wait_for_units_inactive() { :; }\n"
        + "wait_for_workers_to_drain() { :; }\n"
        + "restore_alpha_runtime() { :; }\n"
        + "restore_units() { :; }\n"
        + "release_alpha_deploy_lock() { :; }\n"
        + "cleanup_tmp() { :; }\n"
        + "\nstop_residents_and_verify resident_reader_writer_quiesce 1\n"
        + "rollback 23\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 23, result.stderr
    assert "DEPLOY_ROLLBACK_PASS rollback=/rollback-test data_pair_restored=0" in result.stderr
    assert "DEPLOY_ROLLBACK_FAILURE" not in result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert "stop --no-block quantos-research-runner.service" not in calls
    assert calls.count("stop --no-block quantos-console-api.service") == 2
    assert calls.count("stop --no-block quantos-console-frontend.service") == 2


def test_deploy_rollback_restores_only_the_four_alpha_runtime_artifacts(
    tmp_path: Path,
) -> None:
    script = render_deploy_script(
        tmp_path / "deploy.sh",
        package_root=tmp_path / "package",
        release_commit="a" * 40,
        source_commit="b" * 40,
        evidence_manifest_sha256="c" * 64,
        bundle_name="quantos.bundle",
        bundle_sha256="d" * 64,
        frontend_tree_sha256="e" * 64,
        backup_mount="/mnt/quantos-backup",
        baseline={
            "commit": "f" * 40,
            "root": "/opt/quantos/releases/" + "f" * 40,
            "base_venv": "/opt/quantos/venvs/native-arm64",
        },
        sqlite_contract={
            "path": "/srv/intake/db.sqlite",
            "sha256": "1" * 64,
            "rows": 1,
            "instruments": 1,
            "dates": 1,
            "first_date": 20260828,
            "last_date": 20260828,
        },
        panel_contract={"path": "/srv/intake/panel.npz", "sha256": "2" * 64},
    )
    content = script.read_text(encoding="utf-8")
    artifact_block = content[
        content.index("ALPHA_RUNTIME_ARTIFACTS=(") : content.index(
            ")\n\ndie()", content.index("ALPHA_RUNTIME_ARTIFACTS=(")
        )
    ]
    assert artifact_block.splitlines()[1:] == [
        "  ashare_alpha_search.json",
        "  ashare_forward_freeze.json",
        "  ashare_forward_observation.db",
        "  ashare_forward_observation.json",
    ]
    rollback_start = content.index("\nrollback() {")
    rollback = content[rollback_start : content.index("\non_exit() {", rollback_start)]
    assert rollback.index(
        'stop_loaded_units_no_block "${RESIDENT_UNITS[@]}"'
    ) < rollback.index("wait_for_workers_to_drain rollback_worker_drain")
    assert rollback.index("wait_for_workers_to_drain rollback_worker_drain") < rollback.index(
        "restore_alpha_runtime"
    )
    assert rollback.index("restore_alpha_runtime") < rollback.index("restore_units")
    assert "DEPLOY_ROLLBACK_FAILURE" in rollback
    assert "rollback_rc=1" in rollback
    assert "current_sqlite_hash" in rollback
    assert "current_panel_hash" in rollback
    assert "if (( drained == 1 ))" in rollback
    assert "PREVIOUS_ENABLED_RESIDENTS" in content
    restore_units = content[content.index("restore_units() {") : content.index("cleanup_tmp() {")]
    assert 'systemctl disable "$unit"' in restore_units
    assert 'systemctl enable "${PREVIOUS_ENABLED_RESIDENTS[@]}"' in restore_units
    cutover = content[content.index("stage_start scheduler_quiesce_and_worker_drain") :]
    assert cutover.index("snapshot_alpha_runtime") < cutover.index("stage_start tdx_pair_publish")


def test_arm64_release_packaging_surface_is_supply_chain_bound() -> None:
    from scripts.generate_supply_chain_evidence import _MANIFESTS

    assert {
        "tools/build_arm64_release_package.py",
        "deploy/arm64/deploy.sh.in",
        "docs/runbooks/arm64-release-packaging.md",
        "tests/tools/test_build_arm64_release_package.py",
    } <= set(_MANIFESTS)


@pytest.mark.skipif(os.geteuid() == 0, reason="requires an unprivileged shell")
def test_rendered_deploy_script_sandbox_refuses_unprivileged_execution(
    tmp_path: Path,
) -> None:
    script = render_deploy_script(
        tmp_path / "deploy.sh",
        package_root=tmp_path / "nonexistent-package",
        release_commit="a" * 40,
        source_commit="b" * 40,
        evidence_manifest_sha256="c" * 64,
        bundle_name="quantos.bundle",
        bundle_sha256="d" * 64,
        frontend_tree_sha256="e" * 64,
        backup_mount="/mnt/quantos-backup",
        baseline={
            "commit": "f" * 40,
            "root": "/opt/quantos/releases/" + "f" * 40,
            "base_venv": "/opt/quantos/venvs/native-arm64",
        },
        sqlite_contract={
            "path": "/srv/intake/db.sqlite",
            "sha256": "1" * 64,
            "rows": 1,
            "instruments": 1,
            "dates": 1,
            "first_date": 20260828,
            "last_date": 20260828,
        },
        panel_contract={"path": "/srv/intake/panel.npz", "sha256": "2" * 64},
    )
    completed = subprocess.run(
        ["bash", str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode != 0
    assert "DEPLOY_BLOCKED: run with sudo" in completed.stderr
    assert not (tmp_path / "nonexistent-package").exists()


def test_tdx_future_known_rows_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "future.sqlite"
    _write_sqlite(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE daily_prices SET knowledge_time=20260829 WHERE trade_date=20260828"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ReleasePackageError, match="future-known"):
        inspect_tdx_sqlite(database)
