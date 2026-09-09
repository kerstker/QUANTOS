from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import sqlite3
import threading
import time
import sys

import pytest

from services.governance.l2_restore_history import L2RestoreHistoryStore
import tools.l2_backup as l2_backup_module
from tools.l2_backup import (
    BackupSource,
    DEFAULT_SOURCES,
    L2BackupError,
    SNAPSHOT_POLICY_SQLITE_WAL_PINNED,
    SNAPSHOT_POLICY_STANDARD,
    SOURCE_CONTRACT_VERSION,
    classify_destination,
    resolve_backup_passphrase,
    restore_l2_batch,
    run_recorded_l2_backup,
    run_l2_backup,
    run_l2_backup_preflight,
)
from tools.live_backup import decrypt_bytes, decrypt_file, encrypt_bytes, encrypt_file


PASSPHRASE = "institutional-backup-test-passphrase"


def test_destination_classification_preserves_windows_unc_and_volume_semantics():
    assert classify_destination(
        PureWindowsPath("C:/QUANTOS"), PureWindowsPath("//backup-host/quantos")
    ) == "OFF_HOST_UNC"
    assert classify_destination(
        PureWindowsPath("C:/QUANTOS"), PureWindowsPath("D:/quantos-backup")
    ) == "SEPARATE_VOLUME"


def test_posix_double_slash_is_not_misclassified_as_windows_unc(monkeypatch):
    monkeypatch.setattr(l2_backup_module, "_filesystem_device_id", lambda _path: 10)

    assert classify_destination(
        PurePosixPath("/srv/quantos"), PurePosixPath("//tmp/backup")
    ) == "LOCAL_ONLY"


def test_destination_classification_uses_posix_filesystem_devices(monkeypatch):
    devices = {"/srv/quantos": 10, "/mnt/backup/quantos": 20}
    monkeypatch.setattr(
        l2_backup_module,
        "_filesystem_device_id",
        lambda path: devices[os.fspath(path)],
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_posix_destination_is_durable",
        lambda _repo, _destination, **_kwargs: True,
    )

    assert classify_destination(
        PurePosixPath("/srv/quantos"), PurePosixPath("/mnt/backup/quantos")
    ) == "SEPARATE_FILESYSTEM"


def test_destination_classification_rejects_non_durable_posix_filesystem(
    monkeypatch,
):
    monkeypatch.setattr(
        l2_backup_module,
        "_filesystem_device_id",
        lambda path: 10 if os.fspath(path) == "/srv/quantos" else 20,
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_posix_destination_is_durable",
        lambda _repo, _destination, **_kwargs: False,
    )

    assert classify_destination(
        PurePosixPath("/srv/quantos"), PurePosixPath("/dev/shm/quantos-backup")
    ) == "LOCAL_ONLY"


def test_mount_provenance_selects_deepest_mount():
    mountinfo = (
        "24 1 8:1 / / rw - ext4 /dev/root rw\n"
        "25 24 8:2 / /mnt/backup rw - xfs /dev/nvme1n1p1 rw\n"
    )
    assert l2_backup_module._mount_provenance_from_lines(
        PurePosixPath("/mnt/backup/quantos"),
        (8, 2),
        mountinfo.splitlines(),
    ) == (
        "/dev/nvme1n1p1",
        "xfs",
    )


def test_mount_provenance_requires_matching_major_minor():
    mountinfo = (
        "24 1 8:1 / / rw - ext4 /dev/root rw\n"
        "25 24 8:3 / /mnt/backup rw - xfs /dev/wrong rw\n"
    )

    assert l2_backup_module._mount_provenance_from_lines(
        PurePosixPath("/mnt/backup/quantos"),
        (8, 2),
        mountinfo.splitlines(),
    ) is None


def test_mount_provenance_decodes_only_defined_mountinfo_escapes():
    mountinfo = (
        r"25 24 8:2 / /mnt/back\040up rw - xfs /dev/disk\134by-id\134backup rw"
        "\n"
    )

    assert l2_backup_module._mount_provenance_from_lines(
        PurePosixPath("/mnt/back up/quantos"),
        (8, 2),
        mountinfo.splitlines(),
    ) == (r"/dev/disk\by-id\backup", "xfs")
    with pytest.raises(ValueError, match="unsupported mountinfo escape"):
        l2_backup_module._decode_mountinfo_path(r"/mnt/not-defined\141")


@pytest.mark.parametrize(
    ("encoded", "decoded"),
    (
        (r"\040", " "),
        (r"\011", "\t"),
        (r"\012", "\n"),
        (r"\134", "\\"),
    ),
)
def test_mountinfo_decoder_accepts_exactly_defined_escapes(encoded, decoded):
    assert l2_backup_module._decode_mountinfo_path(f"left{encoded}right") == (
        f"left{decoded}right"
    )


def test_mount_provenance_rejects_malformed_table_fail_closed():
    assert l2_backup_module._mount_provenance_from_lines(
        PurePosixPath("/mnt/backup/quantos"),
        (8, 2),
        ("malformed",),
    ) is None


def test_linux_durability_uses_one_mountinfo_snapshot_and_device_identity(
    monkeypatch,
):
    mountinfo = (
        "24 1 8:1 / /srv/quantos rw - ext4 /dev/nvme0n1p1 rw",
        "25 24 8:2 / /mnt/backup rw - xfs /dev/nvme1n1p1 rw",
    )
    calls = 0

    def read_mountinfo():
        nonlocal calls
        calls += 1
        return mountinfo

    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        l2_backup_module,
        "_resolved_posix_path",
        lambda path: PurePosixPath(os.fspath(path)),
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_filesystem_device_numbers",
        lambda device_id: {10: (8, 1), 20: (8, 2)}[device_id],
    )
    monkeypatch.setattr(
        l2_backup_module, "_read_linux_mountinfo_lines", read_mountinfo
    )

    assert l2_backup_module._posix_destination_is_durable(
        PurePosixPath("/srv/quantos"),
        PurePosixPath("/mnt/backup"),
        repo_device_id=10,
        destination_device_id=20,
    )
    assert calls == 1


@pytest.mark.parametrize(
    ("filesystem_type", "source"),
    (("nfs4", "storage.example:/quantos"), ("cifs", "//nas/quantos")),
)
def test_linux_durability_accepts_allowlisted_network_filesystem(
    monkeypatch, filesystem_type, source
):
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        l2_backup_module,
        "_resolved_posix_path",
        lambda path: PurePosixPath(os.fspath(path)),
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_filesystem_device_numbers",
        lambda device_id: {10: (8, 1), 20: (0, 43)}[device_id],
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_read_linux_mountinfo_lines",
        lambda: (
            "24 1 8:1 / /srv/quantos rw - ext4 /dev/root rw",
            f"25 24 0:43 / /mnt/backup rw - {filesystem_type} {source} rw",
        ),
    )

    assert l2_backup_module._posix_destination_is_durable(
        PurePosixPath("/srv/quantos"),
        PurePosixPath("/mnt/backup"),
        repo_device_id=10,
        destination_device_id=20,
    )


@pytest.mark.parametrize("filesystem_type", ("tmpfs", "ramfs", "proc", "overlay"))
def test_posix_durability_rejects_volatile_or_pseudo_filesystems(
    monkeypatch, filesystem_type
):
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        l2_backup_module,
        "_resolved_posix_path",
        lambda path: PurePosixPath(os.fspath(path)),
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_filesystem_device_numbers",
        lambda device_id: {10: (8, 1), 20: (0, 42)}[device_id],
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_read_linux_mountinfo_lines",
        lambda: (
            "24 1 8:1 / /srv/quantos rw - ext4 /dev/root rw",
            f"25 24 0:42 / /mnt/backup rw - {filesystem_type} synthetic rw",
        ),
    )

    assert not l2_backup_module._posix_destination_is_durable(
        PurePosixPath("/srv/quantos"),
        PurePosixPath("/mnt/backup"),
        repo_device_id=10,
        destination_device_id=20,
    )


def test_posix_durability_rejects_unknown_filesystem_type(monkeypatch):
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        l2_backup_module,
        "_resolved_posix_path",
        lambda path: PurePosixPath(os.fspath(path)),
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_filesystem_device_numbers",
        lambda device_id: {10: (8, 1), 20: (8, 2)}[device_id],
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_read_linux_mountinfo_lines",
        lambda: (
            "24 1 8:1 / /srv/quantos rw - ext4 /dev/root rw",
            "25 24 8:2 / /mnt/backup rw - future-pseudofs remote rw",
        ),
    )

    assert not l2_backup_module._posix_destination_is_durable(
        PurePosixPath("/srv/quantos"),
        PurePosixPath("/mnt/backup"),
        repo_device_id=10,
        destination_device_id=20,
    )


@pytest.mark.parametrize("source", ("none", "overlay", "tmpfs", "proc"))
def test_posix_durability_rejects_synthetic_source(monkeypatch, source):
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        l2_backup_module,
        "_resolved_posix_path",
        lambda path: PurePosixPath(os.fspath(path)),
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_filesystem_device_numbers",
        lambda device_id: {10: (8, 1), 20: (8, 2)}[device_id],
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_read_linux_mountinfo_lines",
        lambda: (
            "24 1 8:1 / /srv/quantos rw - ext4 /dev/root rw",
            f"25 24 8:2 / /mnt/backup rw - ext4 {source} rw",
        ),
    )

    assert not l2_backup_module._posix_destination_is_durable(
        PurePosixPath("/srv/quantos"),
        PurePosixPath("/mnt/backup"),
        repo_device_id=10,
        destination_device_id=20,
    )


def test_posix_durability_requires_distinct_mount_source(monkeypatch):
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        l2_backup_module,
        "_resolved_posix_path",
        lambda path: PurePosixPath(os.fspath(path)),
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_filesystem_device_numbers",
        lambda device_id: {10: (8, 1), 20: (8, 2)}[device_id],
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_read_linux_mountinfo_lines",
        lambda: (
            "24 1 8:1 / /srv/quantos rw - ext4 /dev/nvme0n1p2 rw",
            "25 24 8:2 / /mnt/backup rw - ext4 /dev/nvme0n1p2 rw",
        ),
    )

    assert not l2_backup_module._posix_destination_is_durable(
        PurePosixPath("/srv/quantos"),
        PurePosixPath("/mnt/backup"),
        repo_device_id=10,
        destination_device_id=20,
    )


def test_non_linux_posix_preserves_device_boundary_semantics(monkeypatch):
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        l2_backup_module,
        "_filesystem_device_id",
        lambda path: 10 if os.fspath(path) == "/srv/quantos" else 20,
    )

    assert classify_destination(
        PurePosixPath("/srv/quantos"), PurePosixPath("/mnt/backup")
    ) == "SEPARATE_FILESYSTEM"


def test_linux_mountinfo_failure_is_local_only(monkeypatch):
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        l2_backup_module,
        "_filesystem_device_id",
        lambda path: 10 if os.fspath(path) == "/srv/quantos" else 20,
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_resolved_posix_path",
        lambda path: PurePosixPath(os.fspath(path)),
    )
    monkeypatch.setattr(
        l2_backup_module,
        "_filesystem_device_numbers",
        lambda device_id: {10: (8, 1), 20: (8, 2)}[device_id],
    )
    monkeypatch.setattr(l2_backup_module, "_read_linux_mountinfo_lines", lambda: None)

    assert classify_destination(
        PurePosixPath("/srv/quantos"), PurePosixPath("/mnt/backup")
    ) == "LOCAL_ONLY"


def test_destination_classification_keeps_same_posix_filesystem_local(monkeypatch):
    monkeypatch.setattr(l2_backup_module, "_filesystem_device_id", lambda _path: 10)

    assert classify_destination(
        PurePosixPath("/srv/quantos"), PurePosixPath("/srv/quantos-backup")
    ) == "LOCAL_ONLY"


def test_filesystem_device_id_uses_nearest_existing_parent(tmp_path):
    missing = tmp_path / "not-created" / "nested" / "backup"

    assert l2_backup_module._filesystem_device_id(missing) == tmp_path.stat().st_dev


def test_backup_destination_must_preexist_and_rejects_symlink(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(L2BackupError, match="pre-existing directory"):
        l2_backup_module._validate_destination(
            repo,
            tmp_path / "missing",
            allow_local_destination=True,
        )

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - locked-down Windows host
        pytest.skip(f"symlink creation unavailable: {exc.__class__.__name__}")
    with pytest.raises(L2BackupError, match="non-symlink"):
        l2_backup_module._validate_destination(
            repo,
            link,
            allow_local_destination=True,
        )


def test_backup_rechecks_destination_before_publish(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    state = repo / "data" / "cache" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("state", encoding="utf-8")
    destination = tmp_path / "backup"
    destination.mkdir()
    calls = 0
    real_classify = l2_backup_module.classify_destination

    def drifting_classification(repo_root, destination_root):
        nonlocal calls
        calls += 1
        if calls >= 3:
            return "SEPARATE_FILESYSTEM"
        return real_classify(repo_root, destination_root)

    monkeypatch.setattr(
        l2_backup_module,
        "classify_destination",
        drifting_classification,
    )
    with pytest.raises(L2BackupError, match="boundary changed"):
        run_l2_backup(
            repo_root=repo,
            destination_root=destination,
            passphrase=PASSPHRASE,
            sources=(BackupSource("data/cache/state.json", "state", required=True),),
            allow_local_destination=True,
            now_ms=1_784_044_800_000,
        )
    assert not any(path.name.startswith("2026") for path in destination.iterdir())


def test_passphrase_file_is_loaded_without_terminal_newline(tmp_path, monkeypatch):
    secret = tmp_path / "backup.passphrase"
    secret.write_text(PASSPHRASE + "\n", encoding="utf-8")
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: False)

    assert resolve_backup_passphrase(
        environment_passphrase=None,
        passphrase_file=secret,
    ) == PASSPHRASE


def test_passphrase_sources_are_strictly_mutually_exclusive(tmp_path):
    secret = tmp_path / "backup.passphrase"
    secret.write_text(PASSPHRASE, encoding="utf-8")

    with pytest.raises(L2BackupError, match="exactly one") as raised:
        resolve_backup_passphrase(
            environment_passphrase=PASSPHRASE,
            passphrase_file=secret,
        )
    assert PASSPHRASE not in str(raised.value)
    assert str(secret) not in str(raised.value)


def test_passphrase_file_must_be_regular_and_not_a_symlink(tmp_path, monkeypatch):
    secret = tmp_path / "backup.passphrase"
    secret.write_text(PASSPHRASE, encoding="utf-8")
    link = tmp_path / "backup.passphrase.link"
    try:
        link.symlink_to(secret)
    except OSError as exc:  # pragma: no cover - unusual locked-down Windows host
        pytest.skip(f"symlink creation unavailable: {exc.__class__.__name__}")
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: False)

    with pytest.raises(L2BackupError, match="regular non-symlink") as raised:
        resolve_backup_passphrase(
            environment_passphrase=None,
            passphrase_file=link,
        )
    assert str(link) not in str(raised.value)


def test_posix_passphrase_file_rejects_group_or_other_permissions():
    with pytest.raises(L2BackupError, match="owner-only"):
        l2_backup_module._validate_passphrase_file_mode(0o100640)


def test_systemd_credential_accepts_read_only_service_group_copy():
    l2_backup_module._validate_passphrase_file_mode(
        0o100440, systemd_managed=True
    )


@pytest.mark.parametrize("mode", (0o100460, 0o100441, 0o100640, 0o100444))
def test_systemd_credential_rejects_write_execute_or_other_access(mode):
    with pytest.raises(L2BackupError, match="systemd.*unsafe"):
        l2_backup_module._validate_passphrase_file_mode(
            mode, systemd_managed=True
        )


def test_group_read_exception_is_limited_to_exact_systemd_credential(
    tmp_path, monkeypatch
):
    credentials = tmp_path / "credentials"
    credential = credentials / "l2-backup-passphrase"
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))

    assert l2_backup_module._is_systemd_credential_path(credential) is True
    assert (
        l2_backup_module._is_systemd_credential_path(
            credentials / "different-name"
        )
        is False
    )
    assert (
        l2_backup_module._is_systemd_credential_path(
            tmp_path / "l2-backup-passphrase"
        )
        is False
    )


def test_passphrase_file_rejects_empty_or_weak_secret_without_leaking_path(
    tmp_path, monkeypatch
):
    secret = tmp_path / "do-not-expose-this-path.passphrase"
    secret.write_text("short\n", encoding="utf-8")
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: False)

    with pytest.raises(L2BackupError, match="strong passphrase") as raised:
        resolve_backup_passphrase(
            environment_passphrase=None,
            passphrase_file=secret,
        )
    assert str(secret) not in str(raised.value)
    assert "short" not in str(raised.value)


def test_main_accepts_passphrase_file_argument(tmp_path, monkeypatch, capsys):
    secret = tmp_path / "backup.passphrase"
    secret.write_text(PASSPHRASE + "\n", encoding="utf-8")
    captured = {}
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: False)
    monkeypatch.delenv(l2_backup_module.PASSPHRASE_ENV, raising=False)
    monkeypatch.delenv(l2_backup_module.PASSPHRASE_FILE_ENV, raising=False)
    monkeypatch.setattr(
        l2_backup_module,
        "run_recorded_l2_backup",
        lambda **kwargs: captured.update(kwargs) or {"verdict": "PASS"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "l2_backup.py",
            "--dest",
            str(tmp_path / "backup"),
            "--passphrase-file",
            str(secret),
        ],
    )

    assert l2_backup_module.main() == 0

    assert captured["passphrase"] == PASSPHRASE
    assert json.loads(capsys.readouterr().out)["verdict"] == "PASS"


def test_systemd_credential_directory_supplies_default_passphrase_file(
    tmp_path, monkeypatch, capsys
):
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    secret = credentials / "l2-backup-passphrase"
    secret.write_text(PASSPHRASE, encoding="utf-8")
    captured = {}
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: False)
    monkeypatch.delenv(l2_backup_module.PASSPHRASE_ENV, raising=False)
    monkeypatch.delenv(l2_backup_module.PASSPHRASE_FILE_ENV, raising=False)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))
    monkeypatch.setattr(
        l2_backup_module,
        "run_recorded_l2_backup",
        lambda **kwargs: captured.update(kwargs) or {"verdict": "PASS"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["l2_backup.py", "--dest", str(tmp_path / "backup")],
    )

    assert l2_backup_module.main() == 0

    assert captured["passphrase"] == PASSPHRASE
    assert json.loads(capsys.readouterr().out)["verdict"] == "PASS"


def test_main_returns_numeric_failure_without_ambiguous_system_exit(
    tmp_path, monkeypatch, capsys
):
    secret = tmp_path / "backup.passphrase"
    secret.write_text(PASSPHRASE + "\n", encoding="utf-8")
    monkeypatch.setattr(l2_backup_module, "_is_posix_platform", lambda: False)
    monkeypatch.delenv(l2_backup_module.PASSPHRASE_ENV, raising=False)
    monkeypatch.setattr(
        l2_backup_module,
        "run_recorded_l2_backup",
        lambda **_kwargs: (_ for _ in ()).throw(
            L2BackupError("BACKUP_CONFIGURATION_UNAVAILABLE")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "l2_backup.py",
            "--dest",
            str(tmp_path / "backup"),
            "--passphrase-file",
            str(secret),
        ],
    )

    assert l2_backup_module.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "BACKUP_CONFIGURATION_UNAVAILABLE\n"


def test_default_backup_sources_require_data_quality_admission_authority():
    source = next(item for item in DEFAULT_SOURCES if item.relative_path == "data/cache/data_quality_admission.db")

    assert source.role == "data_quality_admission"
    assert source.required is True


def test_default_backup_sources_require_restore_history_authority():
    source = next(item for item in DEFAULT_SOURCES if item.relative_path == "data/cache/l2_restore_history.db")

    assert source.role == "l2_restore_history_authority"
    assert source.required is True


def test_current_backup_contract_requires_complete_ashare_research_state():
    ashare = {
        source.relative_path: (source.role, source.required)
        for source in DEFAULT_SOURCES
        if source.relative_path.startswith("data/stocks/")
    }

    assert SOURCE_CONTRACT_VERSION == 16
    assert ashare == {
        "data/stocks/pit/tdx-windows-20260830/ashare_tdx_pit.adv20000000.panel.npz": (
            "ashare_tdx_panel_cache",
            False,
        ),
        "data/stocks/pit/tdx-windows-20260830/ashare_tdx_pit.sqlite": (
            "ashare_tdx_daily_price_authority",
            True,
        ),
        "data/stocks/research.db": ("ashare_research_database", True),
        "data/stocks/snapshots/alpha_snapshot.json": (
            "ashare_alpha_research_snapshot",
            True,
        ),
        "data/stocks/snapshots/klines_snapshot.json": (
            "ashare_klines_snapshot",
            True,
        ),
        "data/stocks/snapshots/paper_track_snapshot.json": (
            "ashare_paper_track_snapshot",
            True,
        ),
        "data/stocks/stocks.db": ("ashare_market_data_authority", True),
    }
    contract = l2_backup_module._source_contract(DEFAULT_SOURCES)
    assert contract["version"] == 16
    assert all(
        source["snapshot_policy"] == SNAPSHOT_POLICY_STANDARD
        for source in contract["sources"]
        if source["relative_path"].startswith("data/stocks/")
    )


def test_default_backup_sources_require_alpha_production_workflow():
    source = next(
        item
        for item in DEFAULT_SOURCES
        if item.relative_path == "data/cache/alpha_production_workflow.db"
    )

    assert source.role == "alpha_production_workflow"
    assert source.required is True


def test_default_backup_sources_require_realtime_execution_paper_ledger():
    source = next(
        item
        for item in DEFAULT_SOURCES
        if item.relative_path == "data/cache/realtime_alpha_execution_paper.db"
    )

    assert source.role == "realtime_engineering_execution_paper"
    assert source.required is True


def test_fixed_artifact_admission_authority_is_an_optional_backup_source():
    source = next(item for item in DEFAULT_SOURCES if item.relative_path == "data/cache/artifact_admission.db")

    assert source.role == "artifact_admission_authority"
    assert source.required is False


def test_mft_testnet_soak_evidence_is_an_optional_backup_source():
    source = next(
        item
        for item in DEFAULT_SOURCES
        if item.relative_path == "data/cache/mft_testnet_soak.db"
    )

    assert source.role == "mft_testnet_soak_evidence_authority"
    assert source.required is False


def test_defillama_forward_vintages_are_an_optional_backup_source():
    source = next(
        item
        for item in DEFAULT_SOURCES
        if item.relative_path == "data/cache/defillama_tvl_vintages.db"
    )

    assert source.role == "defillama_tvl_forward_vintages"
    assert source.required is False


def test_defillama_stablecoin_forward_vintages_are_an_optional_backup_source():
    source = next(
        item
        for item in DEFAULT_SOURCES
        if item.relative_path == "data/cache/defillama_stablecoin_vintages.db"
    )

    assert source.role == "defillama_stablecoin_forward_vintages"
    assert source.required is False


def test_binance_book_depth_archive_is_an_optional_backup_source():
    source = next(
        item
        for item in DEFAULT_SOURCES
        if item.relative_path == "data/cache/binance_book_depth_archive.db"
    )

    assert source.role == "binance_book_depth_archive_authority"
    assert source.required is False


@pytest.mark.parametrize(
    ("relative_path", "role"),
    [
        (
            "data/cache/bybit_linear_events.db",
            "bybit_public_linear_event_authority",
        ),
        (
            "data/cache/bybit_market_resilience.db",
            "outcome_free_market_resilience_authority",
        ),
    ],
)
def test_bybit_raw_and_outcome_free_resilience_are_optional_backup_sources(
    relative_path,
    role,
):
    source = next(
        item for item in DEFAULT_SOURCES if item.relative_path == relative_path
    )

    assert source.role == role
    assert source.required is False
    assert source.snapshot_policy == SNAPSHOT_POLICY_SQLITE_WAL_PINNED


def test_okx_public_borrow_forward_vintages_are_an_optional_backup_source():
    source = next(
        item
        for item in DEFAULT_SOURCES
        if item.relative_path == "data/cache/okx_borrow_vintages.db"
    )

    assert source.role == "okx_public_borrow_forward_vintages"
    assert source.required is False


def test_liquidation_forward_contract_and_health_are_optional_backup_sources():
    expected = {
        "data/cache/cross_venue_liquidation_health.json": (
            "cross_venue_liquidation_health",
            False,
        ),
        "evidence/cross_venue_liquidation_preregistration_764e5b5.json": (
            "cross_venue_liquidation_preregistration",
            False,
        ),
    }

    assert {
        source.relative_path: (source.role, source.required)
        for source in DEFAULT_SOURCES
        if source.relative_path in expected
    } == expected


def test_borrow_pressure_preregistration_is_an_optional_backup_source():
    source = next(
        item
        for item in DEFAULT_SOURCES
        if item.relative_path
        == "evidence/borrow_pressure_preregistration_5eed0f1.json"
    )

    assert source.role == "borrow_pressure_preregistration"
    assert source.required is False


@pytest.mark.parametrize(
    ("relative_path", "role"),
    [
        ("data/cache/approvals.db", "approval_authority"),
        (
            "data/cache/identity_authorization.db",
            "identity_authorization_authority",
        ),
    ],
)
def test_governance_authorities_are_optional_backup_sources(relative_path, role):
    source = next(item for item in DEFAULT_SOURCES if item.relative_path == relative_path)

    assert source.role == role
    assert source.required is False


def _seed_sqlite(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE authority (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO authority(value) VALUES ('committed-state')")
    connection.commit()
    return connection


def test_online_sqlite_backup_is_paginated_and_yields_to_authority_writers(monkeypatch):
    calls: dict[str, object] = {}
    pauses: list[float] = []

    class SourceConnection:
        def backup(self, target, *, pages, progress, sleep):
            calls.update(target=target, pages=pages, sleep=sleep)
            progress(0, 10, 20)
            progress(101, 0, 20)

    target = object()
    monkeypatch.setattr(l2_backup_module.time, "sleep", pauses.append)

    l2_backup_module._online_sqlite_backup(SourceConnection(), target)

    assert calls == {
        "target": target,
        "pages": l2_backup_module.SQLITE_BACKUP_PAGES,
        "sleep": l2_backup_module.SQLITE_BACKUP_BUSY_SLEEP_SECONDS,
    }
    assert 0 < l2_backup_module.SQLITE_BACKUP_PAGES <= 1_024
    assert pauses == [l2_backup_module.SQLITE_BACKUP_WRITER_YIELD_SECONDS]


@pytest.mark.parametrize("status", [sqlite3.SQLITE_OK, sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED])
def test_standard_snapshot_rejects_sustained_non_progress(monkeypatch, status):
    clock = [0.0]

    class SourceConnection:
        def backup(self, target, *, pages, progress, sleep):
            for second in range(0, 121):
                clock[0] = float(second)
                progress(status, 500, 1000)

    monkeypatch.setattr(l2_backup_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(l2_backup_module.time, "sleep", lambda _: None)
    with pytest.raises(L2BackupError, match="^SQLITE_SNAPSHOT_NO_PROGRESS$"):
        l2_backup_module._online_sqlite_backup(SourceConnection(), object())


def test_standard_snapshot_progress_can_exceed_stall_window(monkeypatch):
    clock = [0.0]

    class SourceConnection:
        def backup(self, target, *, pages, progress, sleep):
            # A transient restart must not fail; sustained new progress resets
            # the stall clock even when total elapsed time exceeds its window.
            for second, remaining in [(1, 900), (30, 950), (50, 800),
                                      (100, 700), (150, 600), (200, 0)]:
                clock[0] = float(second)
                progress(sqlite3.SQLITE_DONE if remaining == 0 else sqlite3.SQLITE_OK,
                         remaining, 1000)

    monkeypatch.setattr(l2_backup_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(l2_backup_module.time, "sleep", lambda _: None)
    l2_backup_module._online_sqlite_backup(SourceConnection(), object())


def test_snapshot_stall_failure_has_redacted_stage():
    assert l2_backup_module._recorded_failure_classification(
        L2BackupError("SQLITE_SNAPSHOT_NO_PROGRESS")
    ) == ("SQLITE_SNAPSHOT_NO_PROGRESS", "SNAPSHOT")


def test_repeated_snapshot_restarts_do_not_refresh_progress_clock(monkeypatch):
    clock = [0.0]

    class SourceConnection:
        def backup(self, target, *, pages, progress, sleep):
            for second in range(121):
                clock[0] = float(second)
                progress(sqlite3.SQLITE_OK, 500 if second % 2 == 0 else 750, 1000)

    monkeypatch.setattr(l2_backup_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(l2_backup_module.time, "sleep", lambda _: None)
    with pytest.raises(L2BackupError, match="^SQLITE_SNAPSHOT_NO_PROGRESS$"):
        l2_backup_module._online_sqlite_backup(SourceConnection(), object())


def test_wal_pinned_sqlite_backup_uses_one_fixed_step_without_yielding(monkeypatch):
    calls: dict[str, object] = {}

    class SourceConnection:
        def backup(self, target, *, pages, sleep):
            calls.update(target=target, pages=pages, sleep=sleep)

    target = object()
    monkeypatch.setattr(
        l2_backup_module.time,
        "sleep",
        lambda _seconds: pytest.fail("WAL-pinned backup must not yield between pages"),
    )

    l2_backup_module._online_sqlite_backup(
        SourceConnection(),
        target,
        snapshot_policy=SNAPSHOT_POLICY_SQLITE_WAL_PINNED,
    )

    assert calls == {
        "target": target,
        "pages": -1,
        "sleep": l2_backup_module.SQLITE_BACKUP_BUSY_SLEEP_SECONDS,
    }


def test_wal_pinned_snapshot_fails_closed_for_non_wal_database(tmp_path):
    source = tmp_path / "authority.db"
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("CREATE TABLE authority (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(L2BackupError, match="not in WAL mode"):
        l2_backup_module._snapshot_source_file(
            source,
            scratch,
            snapshot_policy=SNAPSHOT_POLICY_SQLITE_WAL_PINNED,
        )


def test_sqlite_extension_uses_online_sqlite_snapshot(tmp_path):
    source = tmp_path / "ashare_tdx_pit.sqlite"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE prices (trade_date INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO prices VALUES (20260828)")
    connection.commit()
    connection.close()
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    snapshot, method, _mtime_ns = l2_backup_module._snapshot_source_file(
        source,
        scratch,
    )

    assert method == "SQLITE_ONLINE_BACKUP"
    with sqlite3.connect(snapshot) as restored:
        assert restored.execute("SELECT trade_date FROM prices").fetchone() == (
            20260828,
        )


def test_online_sqlite_backup_allows_delete_journal_writer_to_commit(tmp_path, monkeypatch):
    source = tmp_path / "authority.db"
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("CREATE TABLE authority (id INTEGER PRIMARY KEY, payload BLOB NOT NULL)")
    connection.executemany(
        "INSERT INTO authority(payload) VALUES (zeroblob(4096))",
        [()] * 2_048,
    )
    connection.commit()
    connection.close()

    first_yield = threading.Event()
    errors: list[BaseException] = []
    real_sleep = time.sleep

    def writer_window(seconds: float) -> None:
        if not first_yield.is_set():
            first_yield.set()
            real_sleep(0.1)
        else:
            real_sleep(min(seconds, 0.001))

    monkeypatch.setattr(l2_backup_module, "SQLITE_BACKUP_PAGES", 32)
    monkeypatch.setattr(l2_backup_module.time, "sleep", writer_window)

    def take_snapshot() -> None:
        try:
            l2_backup_module._snapshot_source_file(source, tmp_path / "scratch")
        except BaseException as exc:  # surfaced in the asserting thread
            errors.append(exc)

    (tmp_path / "scratch").mkdir()
    backup = threading.Thread(target=take_snapshot)
    backup.start()
    assert first_yield.wait(timeout=5)

    writer = sqlite3.connect(source, timeout=1)
    writer.execute("INSERT INTO authority(payload) VALUES (zeroblob(4096))")
    writer.commit()
    writer.close()

    backup.join(timeout=10)
    assert not backup.is_alive()
    assert errors == []


def test_l2_backup_uses_consistent_sqlite_snapshot_and_verified_restore(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    database = repo / "data" / "cache" / "authority.db"
    status = repo / "data" / "cache" / "l2_backup_status.json"
    evidence = repo / "evidence" / "l2_backup_latest.json"
    (tmp_path / "backup").mkdir()
    connection = _seed_sqlite(database)
    state = repo / "data" / "cache" / "state.json"
    state.write_text(json.dumps({"marker": "sensitive-state-marker"}), encoding="utf-8")

    sources = (
        BackupSource("data/cache/authority.db", "event_authority", required=True),
        BackupSource("data/cache/state.json", "operational_state", required=True),
    )
    monkeypatch.setattr(l2_backup_module, "L2_ENCRYPTION_CHUNK_SIZE", 16)
    result = run_l2_backup(
        repo_root=repo,
        destination_root=tmp_path / "backup",
        passphrase=PASSPHRASE,
        sources=sources,
        evidence_path=evidence,
        status_path=status,
        allow_local_destination=True,
        now_ms=1_784_044_800_000,
    )
    connection.close()

    assert result["verdict"] == "PASS"
    assert result["mode"] == "L2_AUTHORITY_STATE"
    assert result["encrypted"] is True
    assert result["file_count"] == 2
    assert result["manifest_verified"] is True
    assert result["restore_verified"] is True
    assert result["sqlite_integrity_verified"] is True
    assert result["destination_class"] == "LOCAL_ONLY"
    assert result["source_contract"]["version"] == SOURCE_CONTRACT_VERSION
    assert result["source_contract"]["sources"] == [
        {
            "relative_path": "data/cache/authority.db",
            "required": True,
            "role": "event_authority",
            "snapshot_policy": SNAPSHOT_POLICY_STANDARD,
        },
        {
            "relative_path": "data/cache/state.json",
            "required": True,
            "role": "operational_state",
            "snapshot_policy": SNAPSHOT_POLICY_STANDARD,
        },
    ]
    assert len(result["source_contract_sha256"]) == 64
    assert result["source_contract_verified"] is True
    assert result["backed_up_sources"] == [
        {
            "relative_path": source["relative_path"],
            "required": source["required"],
            "role": source["role"],
        }
        for source in result["source_contract"]["sources"]
    ]
    assert json.loads(evidence.read_text(encoding="utf-8")) == result
    assert json.loads(status.read_text(encoding="utf-8"))["last_success_at_ms"] == result["observed_at_ms"]

    batch = Path(result["batch_dir"])
    assert batch.is_dir()
    assert {path.suffix for path in batch.iterdir()} == {".enc"}
    assert all(b"sensitive-state-marker" not in path.read_bytes() for path in batch.iterdir())
    manifest = _read_manifest(batch)
    for entry in manifest["entries"]:
        assert (batch / f"{entry['artifact']}.enc").read_bytes().startswith(b"QBK2")

    restored = tmp_path / "restored"
    verification = restore_l2_batch(
        batch_dir=batch,
        restore_dir=restored,
        passphrase=PASSPHRASE,
        rto_ms=30_000,
        max_rpo_ms=86_400_000,
        now_ms=result["observed_at_ms"],
        sources=sources,
    )
    assert verification["manifest_verified"] is True
    assert verification["source_contract_verified"] is True
    assert verification["source_contract_sha256"] == result["source_contract_sha256"]
    restored_db = restored / "data" / "cache" / "authority.db"
    with sqlite3.connect(restored_db) as restored_connection:
        assert restored_connection.execute("SELECT value FROM authority").fetchone() == ("committed-state",)
    assert json.loads((restored / "data" / "cache" / "state.json").read_text()) == {"marker": "sensitive-state-marker"}


def test_l2_backup_compresses_large_artifact_and_restores_original(tmp_path, monkeypatch):
    import tools.restore_drill as restore_drill_module

    repo = tmp_path / "repo"
    state = repo / "data" / "cache" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_bytes(b"large-authority-state")
    destination = tmp_path / "backup"
    destination.mkdir()
    sources = (BackupSource("data/cache/state.json", "authority", required=True),)

    def fake_compress(source, output):
        shutil.copyfile(source, output)

    def fake_decompress(source_path, output_path):
        shutil.copyfile(source_path, output_path)
        return restore_drill_module._sha256_file(output_path)

    monkeypatch.setattr(l2_backup_module, "L2_ZSTD_MIN_SIZE", 1)
    monkeypatch.setattr(l2_backup_module, "_require_zstd", lambda: None)
    monkeypatch.setattr(l2_backup_module, "_compress_zstd", fake_compress)
    monkeypatch.setattr(restore_drill_module, "_decompress_zstd", fake_decompress)

    result = run_l2_backup(
        repo_root=repo,
        destination_root=destination,
        passphrase=PASSPHRASE,
        sources=sources,
        allow_local_destination=True,
        now_ms=1_784_044_800_000,
    )

    batch = Path(result["batch_dir"])
    manifest = _read_manifest(batch)
    entry = manifest["entries"][0]
    assert entry["storage_encoding"] == l2_backup_module.STORAGE_ENCODING_ZSTD_LEVEL_1
    assert (batch / f"{entry['artifact']}.zst.enc").is_file()
    restored = tmp_path / "restored"
    restore_l2_batch(
        batch_dir=batch,
        restore_dir=restored,
        passphrase=PASSPHRASE,
        rto_ms=30_000,
        max_rpo_ms=86_400_000,
        now_ms=result["observed_at_ms"],
        sources=sources,
    )
    assert (restored / "data" / "cache" / "state.json").read_bytes() == state.read_bytes()


def test_l2_backup_missing_required_source_fails_without_publishing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    destination = tmp_path / "backup"
    destination.mkdir()

    with pytest.raises(L2BackupError, match="required backup source is missing"):
        run_l2_backup(
            repo_root=repo,
            destination_root=destination,
            passphrase=PASSPHRASE,
            sources=(BackupSource("data/cache/missing.db", "authority", required=True),),
            evidence_path=repo / "evidence.json",
            status_path=repo / "status.json",
            allow_local_destination=True,
        )

    assert not any(destination.iterdir())
    assert not (repo / "evidence.json").exists()
    assert not (repo / "status.json").exists()


def test_l2_backup_rejects_sources_outside_allowlisted_roots(tmp_path):
    repo = tmp_path / "repo"
    secret = repo / "key" / "broker_secret.json"
    secret.parent.mkdir(parents=True)
    secret.write_text("must-not-be-backed-up", encoding="utf-8")
    (tmp_path / "backup").mkdir()

    with pytest.raises(L2BackupError, match="allowlisted backup roots"):
        run_l2_backup(
            repo_root=repo,
            destination_root=tmp_path / "backup",
            passphrase=PASSPHRASE,
            sources=(BackupSource("key/broker_secret.json", "forbidden", required=True),),
            evidence_path=repo / "evidence.json",
            status_path=repo / "status.json",
            allow_local_destination=True,
        )


def test_l2_backup_requires_external_destination_without_explicit_local_optout(tmp_path):
    repo = tmp_path / "repo"
    state = repo / "data" / "cache" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("state", encoding="utf-8")
    (tmp_path / "backup").mkdir()

    with pytest.raises(L2BackupError, match="separate volume or UNC"):
        run_l2_backup(
            repo_root=repo,
            destination_root=tmp_path / "backup",
            passphrase=PASSPHRASE,
            sources=(BackupSource("data/cache/state.json", "state", required=True),),
            evidence_path=repo / "evidence.json",
            status_path=repo / "status.json",
        )


def test_l2_backup_rejects_corrupt_nonempty_sqlite_source(tmp_path):
    repo = tmp_path / "repo"
    database = repo / "data" / "cache" / "authority.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not-a-sqlite-database")
    (tmp_path / "backup").mkdir()

    with pytest.raises(L2BackupError, match="not a valid SQLite database"):
        run_l2_backup(
            repo_root=repo,
            destination_root=tmp_path / "backup",
            passphrase=PASSPHRASE,
            sources=(BackupSource("data/cache/authority.db", "authority", required=True),),
            evidence_path=repo / "evidence.json",
            status_path=repo / "status.json",
            allow_local_destination=True,
        )


def _write_manifest(batch: Path, document: dict) -> None:
    encoded = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    (batch / "manifest.json.enc").write_bytes(encrypt_bytes(encoded, PASSPHRASE))


def _read_manifest(batch: Path) -> dict:
    encoded = decrypt_bytes((batch / "manifest.json.enc").read_bytes(), PASSPHRASE)
    return json.loads(encoded)


def _contract_batch(tmp_path) -> tuple[Path, tuple[BackupSource, ...], int]:
    repo = tmp_path / "repo"
    first = repo / "data" / "cache" / "first.json"
    second = repo / "data" / "cache" / "second.json"
    first.parent.mkdir(parents=True)
    first.write_text('{"first":1}', encoding="utf-8")
    second.write_text('{"second":2}', encoding="utf-8")
    sources = (
        BackupSource("data/cache/first.json", "first_authority", required=True),
        BackupSource("data/cache/second.json", "second_authority", required=False),
    )
    now_ms = 1_784_044_800_000
    (tmp_path / "backup").mkdir(exist_ok=True)
    result = run_l2_backup(
        repo_root=repo,
        destination_root=tmp_path / "backup",
        passphrase=PASSPHRASE,
        sources=sources,
        evidence_path=repo / "evidence.json",
        status_path=repo / "status.json",
        allow_local_destination=True,
        now_ms=now_ms,
    )
    return Path(result["batch_dir"]), sources, now_ms


def _restore_contract_batch(tmp_path, batch: Path, sources: tuple[BackupSource, ...], now_ms: int) -> None:
    restore_l2_batch(
        batch_dir=batch,
        restore_dir=tmp_path / "restored",
        passphrase=PASSPHRASE,
        rto_ms=30_000,
        max_rpo_ms=86_400_000,
        now_ms=now_ms,
        sources=sources,
    )


@pytest.mark.parametrize("validation_ms", [20, 40])
def test_restore_rto_includes_post_decryption_validation(tmp_path, monkeypatch, validation_ms):
    batch, sources, now_ms = _contract_batch(tmp_path)
    clock = [0.0]
    original = l2_backup_module._verify_manifest_source_contract

    def slow_validation(*args):
        result = original(*args)
        clock[0] += validation_ms / 1000
        return result

    monkeypatch.setattr(l2_backup_module.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(l2_backup_module, "_verify_manifest_source_contract", slow_validation)
    target = tmp_path / "restored"
    kwargs = dict(
        batch_dir=batch, restore_dir=target, passphrase=PASSPHRASE,
        rto_ms=30, max_rpo_ms=86_400_000, now_ms=now_ms, sources=sources,
    )
    if validation_ms > 30:
        with pytest.raises(L2BackupError, match="restore drill exceeded RTO"):
            restore_l2_batch(**kwargs)
        assert not target.exists()
        assert not list(tmp_path.glob(".quantos-l2-restore-*"))
    else:
        result = restore_l2_batch(**kwargs)
        assert result["elapsed_ms"] == validation_ms
        assert result["rto_met"] is True
        assert (target / "data/cache/first.json").is_file()


def test_restore_rejects_tampered_source_contract_digest(tmp_path):
    batch, sources, now_ms = _contract_batch(tmp_path)
    manifest = _read_manifest(batch)
    manifest["source_contract_sha256"] = "0" * 64
    _write_manifest(batch, manifest)

    with pytest.raises(L2BackupError, match="source contract digest"):
        _restore_contract_batch(tmp_path, batch, sources, now_ms)


def test_restore_rejects_storage_encoding_that_disagrees_with_batch(tmp_path):
    batch, sources, now_ms = _contract_batch(tmp_path)
    manifest = _read_manifest(batch)
    manifest["entries"][0]["storage_encoding"] = (
        l2_backup_module.STORAGE_ENCODING_ZSTD_LEVEL_1
    )
    _write_manifest(batch, manifest)

    with pytest.raises(L2BackupError, match="storage encoding"):
        _restore_contract_batch(tmp_path, batch, sources, now_ms)


def test_restore_rejects_missing_contracted_source_entry(tmp_path):
    batch, sources, now_ms = _contract_batch(tmp_path)
    manifest = _read_manifest(batch)
    removed = manifest["entries"].pop()
    (batch / f"{removed['artifact']}.enc").unlink()
    _write_manifest(batch, manifest)

    with pytest.raises(L2BackupError, match="source entries do not match"):
        _restore_contract_batch(tmp_path, batch, sources, now_ms)


def test_restore_rejects_extra_source_entry(tmp_path):
    batch, sources, now_ms = _contract_batch(tmp_path)
    manifest = _read_manifest(batch)
    extra = dict(manifest["entries"][0])
    extra.update(
        artifact="extra-artifact",
        relative_path="data/cache/extra.json",
        role="extra_authority",
    )
    manifest["entries"].append(extra)
    source_artifact = batch / f"{manifest['entries'][0]['artifact']}.enc"
    (batch / "extra-artifact.enc").write_bytes(source_artifact.read_bytes())
    _write_manifest(batch, manifest)

    with pytest.raises(L2BackupError, match="source entry is outside"):
        _restore_contract_batch(tmp_path, batch, sources, now_ms)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("role", "wrong_authority", "source entry does not match"),
        ("relative_path", "data/cache/wrong.json", "source entry is outside"),
        ("required", False, "source entry does not match"),
    ],
)
def test_restore_rejects_path_role_or_requiredness_mismatch(tmp_path, field, value, error):
    batch, sources, now_ms = _contract_batch(tmp_path)
    manifest = _read_manifest(batch)
    manifest["entries"][0][field] = value
    _write_manifest(batch, manifest)

    with pytest.raises(L2BackupError, match=error):
        _restore_contract_batch(tmp_path, batch, sources, now_ms)


def _recorded_sources() -> tuple[BackupSource, ...]:
    return (
        BackupSource(
            "data/cache/l2_restore_history.db",
            "l2_restore_history_authority",
            required=True,
        ),
        BackupSource("data/cache/state.json", "operational_state", required=True),
    )


def _seed_recorded_state(repo: Path) -> None:
    state = repo / "data" / "cache" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"state":"committed"}', encoding="utf-8")


def test_backup_preflight_is_bounded_and_read_only(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _seed_recorded_state(repo)
    history_path = repo / "data" / "cache" / "l2_restore_history.db"
    store = L2RestoreHistoryStore(str(history_path), create=True)
    store.close()
    destination = tmp_path / "external-backup"
    destination.mkdir()
    monkeypatch.setattr(
        l2_backup_module,
        "classify_destination",
        lambda *_args: "SEPARATE_VOLUME",
    )
    state_path = repo / "data" / "cache" / "state.json"
    before = {
        "state": hashlib.sha256(state_path.read_bytes()).hexdigest(),
        "history": hashlib.sha256(history_path.read_bytes()).hexdigest(),
    }

    result = run_l2_backup_preflight(
        repo_root=repo,
        destination_root=destination,
        passphrase=PASSPHRASE,
        sources=_recorded_sources(),
    )

    assert result == {
        "schema_version": 1,
        "verdict": "PASS",
        "mode": "L2_BACKUP_READINESS_PREFLIGHT",
        "destination_class": "SEPARATE_VOLUME",
        "external_destination_verified": True,
        "credential_validated": True,
        "source_contract_sha256": l2_backup_module._source_contract_sha256(
            l2_backup_module._source_contract(_recorded_sources())
        ),
        "source_count": 2,
        "required_source_count": 2,
        "missing_optional_sources": [],
        "restore_history_chain_verified": True,
        "restore_history_attempt_count": 0,
        "restore_history_open_attempt_count": 0,
    }
    assert list(destination.iterdir()) == []
    assert hashlib.sha256(state_path.read_bytes()).hexdigest() == before["state"]
    assert hashlib.sha256(history_path.read_bytes()).hexdigest() == before["history"]


def test_backup_preflight_fails_closed_for_missing_required_source(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    history_path = repo / "data" / "cache" / "l2_restore_history.db"
    store = L2RestoreHistoryStore(str(history_path), create=True)
    store.close()
    destination = tmp_path / "external-backup"
    destination.mkdir()
    monkeypatch.setattr(
        l2_backup_module,
        "classify_destination",
        lambda *_args: "SEPARATE_VOLUME",
    )

    with pytest.raises(L2BackupError, match="required backup source is missing"):
        run_l2_backup_preflight(
            repo_root=repo,
            destination_root=destination,
            passphrase=PASSPHRASE,
            sources=_recorded_sources(),
        )


def test_backup_preflight_fails_closed_for_invalid_restore_history(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    _seed_recorded_state(repo)
    history_path = repo / "data" / "cache" / "l2_restore_history.db"
    with sqlite3.connect(history_path) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    destination = tmp_path / "external-backup"
    destination.mkdir()
    monkeypatch.setattr(
        l2_backup_module,
        "classify_destination",
        lambda *_args: "SEPARATE_VOLUME",
    )

    with pytest.raises(L2BackupError, match="RESTORE_HISTORY_UNAVAILABLE"):
        run_l2_backup_preflight(
            repo_root=repo,
            destination_root=destination,
            passphrase=PASSPHRASE,
            sources=_recorded_sources(),
        )


def test_recorded_backup_commits_pass_before_sanitized_compatibility_projection(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _seed_recorded_state(repo)
    (tmp_path / "external-backup").mkdir()
    monkeypatch.setattr(
        l2_backup_module,
        "classify_destination",
        lambda *_args: "SEPARATE_VOLUME",
    )

    result = run_recorded_l2_backup(
        repo_root=repo,
        destination_root=tmp_path / "external-backup",
        passphrase=PASSPHRASE,
        sources=_recorded_sources(),
        now_ms=1_784_044_800_000,
    )

    history_path = repo / "data" / "cache" / "l2_restore_history.db"
    store = L2RestoreHistoryStore(str(history_path), create=False)
    snapshot = store.read_snapshot()
    store.close()
    assert len(snapshot.attempts) == len(snapshot.outcomes) == 1
    assert snapshot.open_attempts == ()
    assert snapshot.outcomes[0].outcome.verdict == "PASS"
    assert snapshot.outcomes[0].outcome.reason_code == "RESTORE_VERIFIED"

    evidence = json.loads((repo / "evidence" / "l2_backup_latest.json").read_text(encoding="utf-8"))
    status = json.loads((repo / "data" / "cache" / "l2_backup_status.json").read_text(encoding="utf-8"))
    assert evidence["verdict"] == "PASS"
    assert "batch_dir" not in evidence
    assert result["restore_history_verified"] is True
    assert status["schema_version"] == 2
    assert status["verdict"] == "PASS"
    assert status["destination_class"] == "SEPARATE_VOLUME"
    assert status["last_attempt_verdict"] == "PASS"
    assert status["history_event_hash"] == snapshot.authority_head.event_hash
    assert "batch_dir" not in status
    assert result["batch_dir"]


def test_recorded_backup_appends_redacted_failure_and_overwrites_old_pass_projection(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _seed_recorded_state(repo)
    evidence_path = repo / "evidence" / "l2_backup_latest.json"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text('{"schema_version":1,"verdict":"PASS"}', encoding="utf-8")
    secret_path = tmp_path / "SECRET-DESTINATION-PATH"
    secret_passphrase = "SECRET"

    def fail_core(**_kwargs):
        raise L2BackupError(f"failure exposed {secret_path} and {secret_passphrase}")

    monkeypatch.setattr(l2_backup_module, "run_l2_backup", fail_core)
    with pytest.raises(L2BackupError, match="BACKUP_ATTEMPT_FAILED") as raised:
        run_recorded_l2_backup(
            repo_root=repo,
            destination_root=secret_path,
            passphrase=secret_passphrase,
            sources=_recorded_sources(),
            now_ms=1_784_044_800_000,
        )
    assert str(secret_path) not in str(raised.value)
    assert secret_passphrase not in str(raised.value)

    history_path = repo / "data" / "cache" / "l2_restore_history.db"
    store = L2RestoreHistoryStore(str(history_path), create=False)
    snapshot = store.read_snapshot()
    store.close()
    assert snapshot.open_attempts == ()
    assert snapshot.outcomes[0].outcome.verdict == "FAIL"
    assert snapshot.outcomes[0].outcome.reason_code == "BACKUP_ATTEMPT_FAILED"
    status_path = repo / "data" / "cache" / "l2_backup_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert status["schema_version"] == 2
    assert status["verdict"] == "BREACH"
    assert evidence["verdict"] == "FAIL"
    serialized = history_path.read_bytes() + status_path.read_bytes() + evidence_path.read_bytes()
    assert str(secret_path).encode() not in serialized
    assert secret_passphrase.encode() not in serialized


@pytest.mark.parametrize(
    ("detail", "reason_code", "stage"),
    [
        (
            "restore drill exceeded RTO: 900001ms > 300000ms",
            "RESTORE_RTO_EXCEEDED",
            "RESTORE_VERIFICATION",
        ),
        (
            "backup batch exceeds RPO: 900001ms > 300000ms",
            "RESTORE_RPO_EXCEEDED",
            "RESTORE_VERIFICATION",
        ),
        (
            "zstd restore codec is unavailable",
            "RESTORE_CODEC_UNAVAILABLE",
            "RESTORE_VERIFICATION",
        ),
        (
            "zstd restore codec failed",
            "RESTORE_CODEC_FAILED",
            "RESTORE_VERIFICATION",
        ),
        ("SQLITE_SNAPSHOT_NO_PROGRESS", "SQLITE_SNAPSHOT_NO_PROGRESS", "SNAPSHOT"),
    ],
)
def test_recorded_backup_retains_redacted_restore_gate_reason(
    tmp_path,
    monkeypatch,
    detail,
    reason_code,
    stage,
):
    repo = tmp_path / "repo"
    _seed_recorded_state(repo)

    def fail_core(**_kwargs):
        raise L2BackupError(detail)

    monkeypatch.setattr(l2_backup_module, "run_l2_backup", fail_core)
    with pytest.raises(L2BackupError, match=reason_code):
        run_recorded_l2_backup(
            repo_root=repo,
            destination_root=tmp_path / "external-backup",
            passphrase=PASSPHRASE,
            sources=_recorded_sources(),
            now_ms=1_784_044_800_000,
        )

    store = L2RestoreHistoryStore(
        str(repo / "data" / "cache" / "l2_restore_history.db"),
        create=False,
    )
    snapshot = store.read_snapshot()
    store.close()
    outcome = snapshot.outcomes[0].outcome
    assert outcome.stage == stage
    assert outcome.reason_code == reason_code


def test_recorded_backup_fails_closed_when_history_authority_is_unavailable(tmp_path):
    repo = tmp_path / "repo"
    history_path = repo / "data" / "cache" / "l2_restore_history.db"
    history_path.parent.mkdir(parents=True)
    with sqlite3.connect(history_path) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    before = hashlib.sha256(history_path.read_bytes()).hexdigest()

    with pytest.raises(L2BackupError, match="RESTORE_HISTORY_UNAVAILABLE"):
        run_recorded_l2_backup(
            repo_root=repo,
            destination_root=None,
            passphrase=None,
            sources=_recorded_sources(),
            now_ms=1_784_044_800_000,
        )

    status = json.loads((repo / "data" / "cache" / "l2_backup_status.json").read_text(encoding="utf-8"))
    assert status["schema_version"] == 2
    assert status["verdict"] == "BREACH"
    assert status["last_reason_code"] == "RESTORE_HISTORY_UNAVAILABLE"
    assert hashlib.sha256(history_path.read_bytes()).hexdigest() == before


def test_recorded_backup_hard_interrupt_leaves_authoritative_open_attempt(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _seed_recorded_state(repo)

    class SimulatedHardInterrupt(BaseException):
        pass

    def interrupt_core(**_kwargs):
        raise SimulatedHardInterrupt()

    monkeypatch.setattr(l2_backup_module, "run_l2_backup", interrupt_core)
    with pytest.raises(SimulatedHardInterrupt):
        run_recorded_l2_backup(
            repo_root=repo,
            destination_root=tmp_path / "backup",
            passphrase=PASSPHRASE,
            sources=_recorded_sources(),
            now_ms=1_784_044_800_000,
        )

    store = L2RestoreHistoryStore(str(repo / "data" / "cache" / "l2_restore_history.db"), create=False)
    snapshot = store.read_snapshot()
    store.close()
    assert len(snapshot.open_attempts) == 1
    assert snapshot.outcomes == ()


def test_restore_rejects_semantically_tampered_restore_history_database(tmp_path):
    repo = tmp_path / "repo"
    history_path = repo / "data" / "cache" / "l2_restore_history.db"
    store = L2RestoreHistoryStore(str(history_path), create=True)
    store.close()
    sources = (
        BackupSource(
            "data/cache/l2_restore_history.db",
            "l2_restore_history_authority",
            required=True,
        ),
    )
    (tmp_path / "backup").mkdir()
    result = run_l2_backup(
        repo_root=repo,
        destination_root=tmp_path / "backup",
        passphrase=PASSPHRASE,
        sources=sources,
        evidence_path=repo / "evidence.json",
        status_path=repo / "status.json",
        allow_local_destination=True,
        now_ms=1_784_044_800_000,
    )
    batch = Path(result["batch_dir"])
    manifest = _read_manifest(batch)
    entry = next(item for item in manifest["entries"] if item["role"] == "l2_restore_history_authority")
    artifact_path = batch / f"{entry['artifact']}.enc"
    tampered_path = tmp_path / "tampered-history.db"
    decrypt_file(str(artifact_path), str(tampered_path), PASSPHRASE)
    with sqlite3.connect(tampered_path) as connection:
        connection.execute("DROP TRIGGER l2_restore_history_events_no_update")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    tampered = tampered_path.read_bytes()
    encrypt_file(str(tampered_path), str(artifact_path), PASSPHRASE, chunk_size=32)
    entry["sha256"] = hashlib.sha256(tampered).hexdigest()
    entry["size"] = len(tampered)
    _write_manifest(batch, manifest)

    with pytest.raises(L2BackupError, match="restore history semantic verification"):
        restore_l2_batch(
            batch_dir=batch,
            restore_dir=tmp_path / "restored-tampered",
            passphrase=PASSPHRASE,
            rto_ms=30_000,
            max_rpo_ms=86_400_000,
            now_ms=result["observed_at_ms"],
            sources=sources,
        )


def test_l2_backup_uses_reviewed_large_authenticated_chunks():
    assert l2_backup_module.L2_ENCRYPTION_CHUNK_SIZE == 64 * 1024 * 1024
    assert l2_backup_module.L2_ENCRYPTION_CHUNK_SIZE <= 64 * 1024 * 1024


def test_ai_factory_checkpoint_is_an_optional_encrypted_backup_source():
    entries=[s for s in DEFAULT_SOURCES if s.relative_path=='data/cache/ai_factory_checkpoint.zip']
    assert len(entries)==1
    assert entries[0].role=='ai_factory_verified_checkpoint'
    assert entries[0].required is False


@pytest.mark.parametrize("validation_ms", [20, 40])
def test_restore_profile_survives_validation_timeout(tmp_path, monkeypatch, validation_ms):
    batch, sources, now_ms = _contract_batch(tmp_path)
    clock = [0.0]
    original = l2_backup_module._verify_manifest_source_contract

    def slow_validation(*args):
        result = original(*args)
        clock[0] += validation_ms / 1000
        return result

    monkeypatch.setattr(l2_backup_module.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(l2_backup_module, "_verify_manifest_source_contract", slow_validation)
    diagnostics = {}
    kwargs = dict(batch_dir=batch, restore_dir=tmp_path / "profile-restore",
                  passphrase=PASSPHRASE, rto_ms=30, max_rpo_ms=86_400_000,
                  now_ms=now_ms, sources=sources, diagnostics=diagnostics)
    if validation_ms > 30:
        with pytest.raises(L2BackupError, match="exceeded RTO"):
            restore_l2_batch(**kwargs)
    else:
        restore_l2_batch(**kwargs)
    assert diagnostics["scope"] == "RESTORE_PERFORMANCE_DIAGNOSTIC_ONLY"
    assert diagnostics["total_ms"] == validation_ms
    assert diagnostics["completed"] is (validation_ms <= 30)
    contract_stage = next(s for s in diagnostics["stages"] if s["stage"] == "MANIFEST_CONTRACT")
    assert contract_stage["elapsed_ms"] == validation_ms
    assert contract_stage["completed"] is True
    assert str(tmp_path) not in json.dumps(diagnostics)
    assert PASSPHRASE not in json.dumps(diagnostics)


@pytest.mark.parametrize("dangling", [False, True])
def test_restore_rejects_target_symlink_before_writing(tmp_path, dangling):
    batch, sources, now_ms = _contract_batch(tmp_path)
    actual = tmp_path / "untouched"
    if not dangling:
        actual.mkdir()
    link = tmp_path / "restore-link"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(L2BackupError, match="non-symlink"):
        restore_l2_batch(batch_dir=batch, restore_dir=link, passphrase=PASSPHRASE,
                         rto_ms=30_000, max_rpo_ms=86_400_000,
                         now_ms=now_ms, sources=sources)
    assert link.is_symlink()
    assert actual.exists() is (not dangling)
    assert not actual.exists() or list(actual.iterdir()) == []


def test_recorded_restore_timeout_retains_profile_and_failed_history(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _seed_recorded_state(repo)
    destination = tmp_path / "backup"
    destination.mkdir()
    clock = [0.0]
    original = l2_backup_module._sqlite_integrity

    def slow_sqlite(path):
        original(path)
        clock[0] += 0.040

    monkeypatch.setattr(l2_backup_module.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(l2_backup_module, "_sqlite_integrity", slow_sqlite)
    with pytest.raises(L2BackupError, match="RESTORE_RTO_EXCEEDED"):
        run_recorded_l2_backup(repo_root=repo, destination_root=destination,
                               passphrase=PASSPHRASE, sources=_recorded_sources(),
                               allow_local_destination=True, rto_ms=30,
                               now_ms=1_784_044_800_000)
    evidence = json.loads((repo / "evidence/l2_backup_latest.json").read_text())
    assert evidence["verdict"] == "FAIL"
    profile = evidence["restore_diagnostics"]
    assert profile["completed"] is False
    assert profile["total_ms"] > 30
    assert any(s["stage"] == "SQLITE_INTEGRITY" and s["elapsed_ms"] == 40
               for s in profile["stages"])
    assert any(s["stage"] == "HISTORY_SEMANTICS" and s["completed"]
               for s in profile["stages"])
    assert str(tmp_path) not in json.dumps(profile)
    assert PASSPHRASE not in json.dumps(profile)
    store = L2RestoreHistoryStore(str(repo / "data/cache/l2_restore_history.db"), create=False)
    try:
        snapshot = store.read_snapshot()
        assert snapshot.outcomes[-1].outcome.verdict == "FAIL"
        assert snapshot.outcomes[-1].outcome.reason_code == "RESTORE_RTO_EXCEEDED"
        assert snapshot.open_attempts == ()
    finally:
        store.close()
    assert not list(destination.glob(".quantos-l2-*"))


def test_restore_corrupt_ciphertext_retains_failed_artifact_profile(tmp_path):
    batch, sources, now_ms = _contract_batch(tmp_path)
    artifact = next(p for p in batch.glob("*.enc") if p.name != "manifest.json.enc")
    damaged = bytearray(artifact.read_bytes())
    damaged[-1] ^= 1
    artifact.write_bytes(damaged)
    profile = {}
    with pytest.raises((L2BackupError, l2_backup_module.BackupDecryptionError)):
        restore_l2_batch(batch_dir=batch, restore_dir=tmp_path / "failed-restore",
                         passphrase=PASSPHRASE, rto_ms=30_000, max_rpo_ms=86_400_000,
                         now_ms=now_ms, sources=sources, diagnostics=profile)
    assert profile["completed"] is False
    assert any(s["stage"] == "DECRYPT_DECOMPRESS_HASH" and not s["completed"]
               for s in profile["stages"])
    assert not (tmp_path / "failed-restore").exists()
    assert not list(tmp_path.glob(".quantos-l2-restore-*"))
