"""Encrypted, restore-verified backup for QUANTOS L2 authority state.

The scheduled path is deliberately fail closed: it requires a strong external
passphrase and a destination on another volume/filesystem or a UNC path. Broker
keys and the ``key/`` tree are outside the source allowlist.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from typing import Iterable

from services.governance import l2_backup_boundary as _backup_boundary
from services.governance.l2_restore_history import (
    L2RestoreHistoryStore,
    L2RestoreHistoryStoreError,
    RestoreAttempt,
    RestoreOutcome,
    RestoreProof,
)
from services.governance.external_evidence import (
    ContinuityAssessment,
    ContinuityObservation,
    evaluate_continuity,
)
from tools.live_backup import (
    BackupDecryptionError,
    BackupEncryptionError,
    BackupEncryptionUnavailable,
    BackupPassphraseTooWeak,
    _check_passphrase_strength,
    encrypt_bytes,
    encrypt_file,
)
from tools.restore_drill import (
    SHA256SUM_BINARY as RESTORE_SHA256SUM_BINARY,
    TEE_BINARY as RESTORE_TEE_BINARY,
    RestoreDrillError,
    run_restore_drill,
    measure_restore_stage,
)


PASSPHRASE_ENV = _backup_boundary.PASSPHRASE_ENV
PASSPHRASE_FILE_ENV = _backup_boundary.PASSPHRASE_FILE_ENV
CREDENTIALS_DIRECTORY_ENV = _backup_boundary.CREDENTIALS_DIRECTORY_ENV
SYSTEMD_PASSPHRASE_CREDENTIAL = _backup_boundary.SYSTEMD_PASSPHRASE_CREDENTIAL
DESTINATION_ENV = "QUANTOS_BACKUP_DEST"
DEFAULT_RTO_MS = 300_000
DEFAULT_MAX_RPO_MS = 86_400_000
MANIFEST_SCHEMA_VERSION = 2
SOURCE_CONTRACT_VERSION = 16
# Larger authenticated chunks reduce Python scheduling and framing overhead for
# the multi-gigabyte public-data authorities retained by the DGX backup.  The
# QBK2 format records this value per file, so older 8 MiB batches remain fully
# restorable.
L2_ENCRYPTION_CHUNK_SIZE = 64 * 1024 * 1024
# Compress only the authorities large enough to dominate the restore RTO.  The
# encrypted filename and manifest record the codec, while small artifacts and
# all existing v2 batches retain their identity encoding.
L2_ZSTD_MIN_SIZE = 256 * 1024 * 1024
STORAGE_ENCODING_IDENTITY = "IDENTITY"
STORAGE_ENCODING_ZSTD_LEVEL_1 = "ZSTD_LEVEL_1"
_STORAGE_ENCODINGS = frozenset(
    {STORAGE_ENCODING_IDENTITY, STORAGE_ENCODING_ZSTD_LEVEL_1}
)
ZSTD_BINARY = "/usr/bin/zstd"
# SQLite's Python backup API copies every remaining page in one step when
# ``pages`` is left at its default.  A multi-gigabyte DELETE-journal authority
# then retains a read lock long enough for scheduled writers to exhaust their
# busy timeout.  Bound each step and yield after successful partial steps so
# production authority writes get a deterministic lock-acquisition window.
SQLITE_BACKUP_PAGES = 256
SQLITE_BACKUP_BUSY_SLEEP_SECONDS = 0.05
SQLITE_BACKUP_WRITER_YIELD_SECONDS = 0.002
# This bounds repeated callbacks without new progress, not total snapshot time
# or the independent restore RTO. A slow but advancing copy remains valid.
SQLITE_BACKUP_NO_PROGRESS_SECONDS = 60.0
SNAPSHOT_POLICY_STANDARD = "STANDARD"
SNAPSHOT_POLICY_SQLITE_WAL_PINNED = "SQLITE_WAL_PINNED"
_SNAPSHOT_POLICIES = frozenset(
    {SNAPSHOT_POLICY_STANDARD, SNAPSHOT_POLICY_SQLITE_WAL_PINNED}
)
_SQLITE_HEADER = b"SQLite format 3\x00"
_ALLOWED_SOURCE_ROOTS = (
    PurePosixPath("data/cache"),
    PurePosixPath("data/stocks"),
    PurePosixPath("evidence"),
)
_SNAPSHOT_METHODS = frozenset(
    {
        "SQLITE_ONLINE_BACKUP",
        "SQLITE_WAL_PINNED_BACKUP",
        "STABLE_FILE_READ",
        "EMPTY_DB_PLACEHOLDER",
    }
)
_EXTERNAL_DESTINATION_CLASSES = _backup_boundary.EXTERNAL_DESTINATION_CLASSES
_MAX_PASSPHRASE_FILE_BYTES = _backup_boundary._MAX_PASSPHRASE_FILE_BYTES
_PERSISTENT_LINUX_FILESYSTEMS = _backup_boundary._PERSISTENT_LINUX_FILESYSTEMS
_NON_DURABLE_LINUX_SOURCES = _backup_boundary._NON_DURABLE_LINUX_SOURCES
L2BackupError = _backup_boundary.L2BackupError


@dataclass(frozen=True)
class BackupSource:
    relative_path: str
    role: str
    required: bool = False
    snapshot_policy: str = SNAPSHOT_POLICY_STANDARD


DEFAULT_SOURCES = (
    BackupSource("data/cache/ai_factory_checkpoint.zip", "ai_factory_verified_checkpoint", False),
    BackupSource("data/cache/domain_events.db", "domain_event_authority", True),
    BackupSource("data/cache/domain_event_projection.db", "domain_event_projection", True),
    BackupSource("data/cache/dry_run.db", "dry_run_ledger", True),
    BackupSource("data/cache/experiment_registry.db", "experiment_registry", True),
    BackupSource("data/cache/live_refresh_history.db", "data_refresh_history", True),
    BackupSource("data/cache/data_quality_admission.db", "data_quality_admission", True),
    BackupSource("data/cache/paper_track.db", "paper_track", True),
    BackupSource(
        "data/cache/realtime_alpha_execution_paper.db",
        "realtime_engineering_execution_paper",
        True,
    ),
    BackupSource(
        "data/cache/alpha_production_workflow.db",
        "alpha_production_workflow",
        True,
    ),
    BackupSource("data/cache/research_runs.db", "research_run_registry", True),
    BackupSource("data/cache/governance_audit.db", "governance_audit", True),
    BackupSource(
        "data/cache/l2_restore_history.db",
        "l2_restore_history_authority",
        True,
    ),
    BackupSource("data/cache/alpha_maintenance.db", "alpha_maintenance", False),
    BackupSource(
        "data/cache/mft_testnet_soak.db",
        "mft_testnet_soak_evidence_authority",
        False,
    ),
    BackupSource(
        "data/cache/paper_candidate_registry.db",
        "factory_candidate_admission",
        False,
    ),
    BackupSource("data/cache/live_kill_switch.db", "kill_switch_authority", False),
    BackupSource("data/cache/deployment_state.db", "deployment_state", False),
    BackupSource(
        "data/cache/artifact_admission.db",
        "artifact_admission_authority",
        False,
    ),
    BackupSource("data/cache/approvals.db", "approval_authority", False),
    BackupSource(
        "data/cache/identity_authorization.db",
        "identity_authorization_authority",
        False,
    ),
    BackupSource(
        "data/cache/vendor_delivery_history.db",
        "vendor_delivery_history_authority",
        False,
    ),
    BackupSource(
        "data/cache/defillama_stablecoin_vintages.db",
        "defillama_stablecoin_forward_vintages",
        False,
    ),
    BackupSource(
        "data/cache/defillama_tvl_vintages.db",
        "defillama_tvl_forward_vintages",
        False,
    ),
    BackupSource(
        "data/cache/binance_book_depth_archive.db",
        "binance_book_depth_archive_authority",
        False,
    ),
    BackupSource(
        "data/cache/bybit_linear_events.db",
        "bybit_public_linear_event_authority",
        False,
        snapshot_policy=SNAPSHOT_POLICY_SQLITE_WAL_PINNED,
    ),
    BackupSource(
        "data/cache/bybit_market_resilience.db",
        "outcome_free_market_resilience_authority",
        False,
        snapshot_policy=SNAPSHOT_POLICY_SQLITE_WAL_PINNED,
    ),
    BackupSource(
        "data/cache/okx_borrow_vintages.db",
        "okx_public_borrow_forward_vintages",
        False,
    ),
    BackupSource(
        "data/cache/cross_venue_liquidation_health.json",
        "cross_venue_liquidation_health",
        False,
    ),
    BackupSource(
        "data/stocks/research.db",
        "ashare_research_database",
        True,
    ),
    BackupSource(
        "data/stocks/pit/tdx-windows-20260830/ashare_tdx_pit.sqlite",
        "ashare_tdx_daily_price_authority",
        True,
    ),
    BackupSource(
        "data/stocks/pit/tdx-windows-20260830/ashare_tdx_pit.adv20000000.panel.npz",
        "ashare_tdx_panel_cache",
        False,
    ),
    BackupSource(
        "data/stocks/snapshots/alpha_snapshot.json",
        "ashare_alpha_research_snapshot",
        True,
    ),
    BackupSource(
        "data/stocks/snapshots/klines_snapshot.json",
        "ashare_klines_snapshot",
        True,
    ),
    BackupSource(
        "data/stocks/snapshots/paper_track_snapshot.json",
        "ashare_paper_track_snapshot",
        True,
    ),
    BackupSource(
        "data/stocks/stocks.db",
        "ashare_market_data_authority",
        True,
    ),
    BackupSource(
        "evidence/cross_venue_liquidation_preregistration_764e5b5.json",
        "cross_venue_liquidation_preregistration",
        False,
    ),
    BackupSource(
        "evidence/borrow_pressure_preregistration_5eed0f1.json",
        "borrow_pressure_preregistration",
        False,
    ),
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path, *, block_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(document: object) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_json_atomic(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_relative_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise L2BackupError(f"invalid backup source path: {value!r}")
    if not any(relative == root or root in relative.parents for root in _ALLOWED_SOURCE_ROOTS):
        raise L2BackupError(f"backup source is outside allowlisted backup roots: {value!r}")
    return relative


def _is_sqlite_path(path: Path | PurePosixPath) -> bool:
    return path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}


def _source_contract(sources: Iterable[BackupSource]) -> dict:
    entries: list[dict] = []
    seen_paths: set[PurePosixPath] = set()
    seen_roles: set[str] = set()
    for source in sources:
        if not isinstance(source, BackupSource):
            raise L2BackupError("backup source contract contains an invalid source")
        relative = _safe_relative_path(source.relative_path)
        if relative in seen_paths:
            raise L2BackupError(f"duplicate backup source: {relative.as_posix()}")
        if (
            not isinstance(source.role, str)
            or not source.role
            or source.role != source.role.strip()
            or not source.role.replace("_", "").isalnum()
        ):
            raise L2BackupError(f"invalid backup source role for {relative.as_posix()}")
        if source.role in seen_roles:
            raise L2BackupError(f"duplicate backup source role: {source.role}")
        if type(source.required) is not bool:
            raise L2BackupError(f"backup source requiredness must be boolean: {relative.as_posix()}")
        if source.snapshot_policy not in _SNAPSHOT_POLICIES:
            raise L2BackupError(f"invalid backup snapshot policy for {relative.as_posix()}")
        if (
            source.snapshot_policy == SNAPSHOT_POLICY_SQLITE_WAL_PINNED
            and not _is_sqlite_path(relative)
        ):
            raise L2BackupError(
                f"WAL-pinned backup policy requires a SQLite source: {relative.as_posix()}"
            )
        seen_paths.add(relative)
        seen_roles.add(source.role)
        entries.append(
            {
                "relative_path": relative.as_posix(),
                "required": source.required,
                "role": source.role,
                "snapshot_policy": source.snapshot_policy,
            }
        )
    if not entries:
        raise L2BackupError("backup source contract is empty")
    entries.sort(key=lambda item: item["relative_path"])
    return {"version": SOURCE_CONTRACT_VERSION, "sources": entries}


def _source_contract_sha256(contract: dict) -> str:
    return _sha256(_canonical_json(contract))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_posix_platform() -> bool:
    return _backup_boundary._is_posix_platform()


def _filesystem_device_id(path: os.PathLike[str] | str) -> int:
    return _backup_boundary._filesystem_device_id(path)


def _decode_mountinfo_path(value: str) -> str:
    return _backup_boundary._decode_mountinfo_path(value)


def _filesystem_device_numbers(device_id: int) -> tuple[int, int] | None:
    return _backup_boundary._filesystem_device_numbers(device_id)


def _mount_provenance_from_lines(
    path: PurePosixPath,
    device_numbers: tuple[int, int],
    lines: Iterable[str],
) -> tuple[str, str] | None:
    return _backup_boundary._mount_provenance_from_lines(
        path, device_numbers, lines
    )


def _resolved_posix_path(path: os.PathLike[str] | str) -> PurePosixPath:
    return _backup_boundary._resolved_posix_path(path)


def _read_linux_mountinfo_lines() -> tuple[str, ...] | None:
    return _backup_boundary._read_linux_mountinfo_lines()


def _posix_destination_is_durable(
    repo_root: Path,
    destination_root: Path,
    *,
    repo_device_id: int | None = None,
    destination_device_id: int | None = None,
) -> bool:
    return _backup_boundary._posix_destination_is_durable(
        repo_root,
        destination_root,
        repo_device_id=repo_device_id,
        destination_device_id=destination_device_id,
        filesystem_device_id=_filesystem_device_id,
        is_posix_platform=_is_posix_platform,
        resolved_posix_path=_resolved_posix_path,
        filesystem_device_numbers=_filesystem_device_numbers,
        read_linux_mountinfo_lines=_read_linux_mountinfo_lines,
        mount_provenance_from_lines=_mount_provenance_from_lines,
    )


def classify_destination(repo_root: Path, destination_root: Path) -> str:
    return _backup_boundary.classify_destination(
        repo_root,
        destination_root,
        filesystem_device_id=_filesystem_device_id,
        posix_destination_is_durable=_posix_destination_is_durable,
    )


def _assert_destination_boundary(
    *, repo_root: Path, destination_root: Path, expected_class: str
) -> None:
    """Re-prove the destination immediately around every publication step."""

    try:
        metadata = destination_root.lstat()
        resolved = destination_root.resolve(strict=True)
    except OSError:
        raise L2BackupError("backup destination boundary is unavailable") from None
    if not stat.S_ISDIR(metadata.st_mode) or destination_root.is_symlink():
        raise L2BackupError("backup destination boundary is invalid")
    if _is_within(resolved, repo_root):
        raise L2BackupError("backup destination boundary changed")
    current_class = classify_destination(repo_root, resolved)
    if current_class != expected_class:
        raise L2BackupError("backup destination boundary changed")


def _validate_passphrase_file_mode(
    mode: int, *, systemd_managed: bool = False
) -> None:
    _backup_boundary._validate_passphrase_file_mode(
        mode, systemd_managed=systemd_managed
    )


def _is_systemd_credential_path(path: Path) -> bool:
    return _backup_boundary._is_systemd_credential_path(path)


def _read_passphrase_file(path_value: str | os.PathLike[str]) -> str:
    return _backup_boundary._read_passphrase_file(
        path_value,
        is_posix_platform=_is_posix_platform,
        passphrase_validator=_check_passphrase_strength,
    )


def resolve_backup_passphrase(
    *,
    environment_passphrase: str | None,
    passphrase_file: str | os.PathLike[str] | None,
) -> str:
    return _backup_boundary.resolve_backup_passphrase(
        environment_passphrase=environment_passphrase,
        passphrase_file=passphrase_file,
        passphrase_validator=_check_passphrase_strength,
        passphrase_file_reader=_read_passphrase_file,
    )


def _default_passphrase_file() -> str | None:
    return _backup_boundary._default_passphrase_file()


def _validate_destination(
    repo_root: Path,
    destination_root: Path,
    *,
    allow_local_destination: bool,
) -> tuple[Path, str]:
    if not destination_root.is_absolute():
        raise L2BackupError("backup destination must be an absolute path")
    resolved_repo = repo_root.resolve()
    try:
        metadata = destination_root.lstat()
        resolved_destination = destination_root.resolve(strict=True)
    except OSError:
        raise L2BackupError(
            "backup destination must be a pre-existing directory"
        ) from None
    if not stat.S_ISDIR(metadata.st_mode) or destination_root.is_symlink():
        raise L2BackupError("backup destination must be a non-symlink directory")
    if _is_within(resolved_destination, resolved_repo):
        raise L2BackupError("backup destination must be outside the QUANTOS workspace")
    destination_class = classify_destination(resolved_repo, resolved_destination)
    if destination_class == "LOCAL_ONLY" and not allow_local_destination:
        raise L2BackupError(
            "scheduled L2 backup requires a separate volume or UNC destination "
            "(a separate filesystem is accepted on POSIX); "
            "local-only output is allowed only with the explicit development opt-out"
        )
    return resolved_destination, destination_class


def _stable_read(path: Path) -> tuple[bytes, int]:
    before = path.stat(follow_symlinks=False)
    data = path.read_bytes()
    after = path.stat(follow_symlinks=False)
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or len(data) != after.st_size:
        raise L2BackupError(f"source changed while it was being copied: {path.name}")
    return data, after.st_mtime_ns


def _stable_copy(path: Path, destination: Path) -> int:
    before = path.stat(follow_symlinks=False)
    shutil.copyfile(path, destination)
    after = path.stat(follow_symlinks=False)
    copied_size = destination.stat(follow_symlinks=False).st_size
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or copied_size != after.st_size
    ):
        raise L2BackupError(f"source changed while it was being copied: {path.name}")
    return after.st_mtime_ns


def _sqlite_integrity(path: Path) -> None:
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=30)) as connection:
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        raise L2BackupError(f"SQLite integrity check failed for {path.name}") from exc
    if rows != [("ok",)]:
        raise L2BackupError(f"SQLite integrity check failed for {path.name}")


def _online_sqlite_backup(
    source_connection,
    snapshot_connection,
    *,
    snapshot_policy: str = SNAPSHOT_POLICY_STANDARD,
) -> None:
    """Copy a live authority using its contract-bound concurrency policy."""

    if snapshot_policy == SNAPSHOT_POLICY_SQLITE_WAL_PINNED:
        # A single SQLite backup step fixes the read snapshot. WAL writers can
        # continue appending while the copy runs, so a high-churn authority
        # cannot indefinitely invalidate already-copied pages.
        source_connection.backup(
            snapshot_connection,
            pages=-1,
            sleep=SQLITE_BACKUP_BUSY_SLEEP_SECONDS,
        )
        return
    if snapshot_policy != SNAPSHOT_POLICY_STANDARD:
        raise L2BackupError("unsupported SQLite snapshot policy")

    best_remaining: int | None = None
    last_progress = time.monotonic()

    def yield_to_writers(status: int, remaining: int, _total: int) -> None:
        nonlocal best_remaining, last_progress
        if status == sqlite3.SQLITE_DONE:
            return
        now = time.monotonic()
        # Busy/locked callbacks may contain stale page counters. Restarts must
        # exceed the previous high-water mark before resetting the clock.
        if status == sqlite3.SQLITE_OK and (
            best_remaining is None or remaining < best_remaining
        ):
            best_remaining = remaining
            last_progress = now
        elif now - last_progress >= SQLITE_BACKUP_NO_PROGRESS_SECONDS:
            raise L2BackupError("SQLITE_SNAPSHOT_NO_PROGRESS")
        if remaining > 0:
            time.sleep(SQLITE_BACKUP_WRITER_YIELD_SECONDS)

    source_connection.backup(
        snapshot_connection,
        pages=SQLITE_BACKUP_PAGES,
        progress=yield_to_writers,
        sleep=SQLITE_BACKUP_BUSY_SLEEP_SECONDS,
    )


def _snapshot_source_file(
    source: Path,
    scratch: Path,
    *,
    snapshot_policy: str = SNAPSHOT_POLICY_STANDARD,
) -> tuple[Path, str, int]:
    if snapshot_policy not in _SNAPSHOT_POLICIES:
        raise L2BackupError("unsupported backup snapshot policy")
    if (
        snapshot_policy == SNAPSHOT_POLICY_SQLITE_WAL_PINNED
        and not _is_sqlite_path(source)
    ):
        raise L2BackupError("WAL-pinned backup policy requires a SQLite source")
    identity = hashlib.sha256(os.fspath(source.resolve()).encode("utf-8")).hexdigest()[:12]
    snapshot = scratch / f"{identity}-{source.name}"
    if not _is_sqlite_path(source):
        mtime_ns = _stable_copy(source, snapshot)
        return snapshot, "STABLE_FILE_READ", mtime_ns

    size = source.stat(follow_symlinks=False).st_size
    if size == 0:
        mtime_ns = _stable_copy(source, snapshot)
        return snapshot, "EMPTY_DB_PLACEHOLDER", mtime_ns
    with source.open("rb") as handle:
        if handle.read(len(_SQLITE_HEADER)) != _SQLITE_HEADER:
            raise L2BackupError(f"source is not a valid SQLite database: {source.name}")

    source_uri = source.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(source_uri, uri=True, timeout=30)) as source_connection:
            source_connection.execute("PRAGMA query_only=ON")
            if snapshot_policy == SNAPSHOT_POLICY_SQLITE_WAL_PINNED:
                journal_mode = source_connection.execute("PRAGMA journal_mode").fetchone()
                if not journal_mode or str(journal_mode[0]).lower() != "wal":
                    raise L2BackupError(
                        f"WAL-pinned backup source is not in WAL mode: {source.name}"
                    )
            with closing(sqlite3.connect(snapshot)) as snapshot_connection:
                _online_sqlite_backup(
                    source_connection,
                    snapshot_connection,
                    snapshot_policy=snapshot_policy,
                )
    except sqlite3.Error as exc:
        raise L2BackupError(f"failed to snapshot SQLite source: {source.name}") from exc
    # The encrypted round-trip compares this snapshot's manifest digest and
    # performs a full SQLite integrity check on the restored bytes. Repeating
    # the same multi-GB integrity scan here adds no independent evidence.
    method = (
        "SQLITE_WAL_PINNED_BACKUP"
        if snapshot_policy == SNAPSHOT_POLICY_SQLITE_WAL_PINNED
        else "SQLITE_ONLINE_BACKUP"
    )
    return snapshot, method, source.stat().st_mtime_ns


def _snapshot_source(source: Path, scratch: Path) -> tuple[bytes, str, int]:
    """Compatibility wrapper for callers that explicitly need an in-memory snapshot."""

    snapshot, method, mtime_ns = _snapshot_source_file(source, scratch)
    return snapshot.read_bytes(), method, mtime_ns


def _discover_sources(
    repo_root: Path,
    sources: Iterable[BackupSource],
) -> tuple[list[tuple[BackupSource, PurePosixPath, Path]], list[str]]:
    discovered: list[tuple[BackupSource, PurePosixPath, Path]] = []
    missing_optional: list[str] = []
    for source in sources:
        relative = _safe_relative_path(source.relative_path)
        path = repo_root.joinpath(*relative.parts)
        if not path.exists():
            if source.required:
                raise L2BackupError(f"required backup source is missing: {relative.as_posix()}")
            missing_optional.append(relative.as_posix())
            continue
        if path.is_symlink() or not path.is_file():
            raise L2BackupError(f"backup source must be a regular non-symlink file: {relative.as_posix()}")
        discovered.append((source, relative, path))
    if not discovered:
        raise L2BackupError("backup source set is empty")
    return discovered, missing_optional


def _artifact_name(index: int, relative: PurePosixPath) -> str:
    digest = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()[:12]
    safe_name = relative.name.replace(".enc", "_enc")
    return f"{index:03d}-{digest}-{safe_name}"


def _require_zstd() -> None:
    required_binaries = (
        ZSTD_BINARY,
        RESTORE_TEE_BINARY,
        RESTORE_SHA256SUM_BINARY,
    )
    if any(
        not os.path.isfile(binary) or not os.access(binary, os.X_OK)
        for binary in required_binaries
    ):
        raise L2BackupError("ZSTD_CODEC_UNAVAILABLE")
    try:
        subprocess.run(
            [ZSTD_BINARY, "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise L2BackupError("ZSTD_CODEC_UNAVAILABLE") from exc


def _compress_zstd(source: Path, output: Path) -> None:
    _require_zstd()
    try:
        subprocess.run(
            [
                ZSTD_BINARY,
                "-1",
                "-T0",
                "-f",
                "--quiet",
                os.fspath(source),
                "-o",
                os.fspath(output),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        output.unlink(missing_ok=True)
        raise L2BackupError("ZSTD_COMPRESSION_FAILED") from exc


def _load_manifest(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise L2BackupError("restored backup manifest is invalid") from exc
    if not isinstance(document, dict):
        raise L2BackupError("restored backup manifest must be an object")
    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION or document.get("mode") != "L2_AUTHORITY_STATE":
        raise L2BackupError("restored backup manifest has an unsupported contract")
    return document


def _verify_manifest_source_contract(manifest: dict, expected_contract: dict) -> str:
    contract = manifest.get("source_contract")
    recorded_digest = manifest.get("source_contract_sha256")
    if not isinstance(contract, dict) or not isinstance(recorded_digest, str):
        raise L2BackupError("restored backup manifest has no source contract")
    actual_digest = _source_contract_sha256(contract)
    if recorded_digest != actual_digest:
        raise L2BackupError("restored backup source contract digest mismatch")
    expected_digest = _source_contract_sha256(expected_contract)
    if contract != expected_contract or recorded_digest != expected_digest:
        raise L2BackupError("restored backup source contract does not match expected authority sources")
    return recorded_digest


def restore_l2_batch(
    *,
    batch_dir: str | Path,
    restore_dir: str | Path,
    passphrase: str,
    rto_ms: int,
    max_rpo_ms: int,
    now_ms: int | None = None,
    sources: Iterable[BackupSource] = DEFAULT_SOURCES,
    diagnostics: dict | None = None,
) -> dict:
    """Restore into an isolated tree and verify the encrypted manifest and SQLite files."""
    started = time.perf_counter()
    diagnostics = {} if diagnostics is None else diagnostics
    diagnostics.update(scope="RESTORE_PERFORMANCE_DIAGNOSTIC_ONLY", stages=[], completed=False)
    source_specs = tuple(sources)
    expected_contract = _source_contract(source_specs)
    if Path(restore_dir).is_symlink():
        raise L2BackupError("restore target must be non-symlink")
    target = Path(restore_dir).resolve(strict=False)
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise L2BackupError("restore target must be empty or absent")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".quantos-l2-restore-", dir=target.parent))
    flat = stage_root / "flat"
    tree = stage_root / "tree"
    tree.mkdir()
    try:
        with measure_restore_stage(diagnostics, "ARTIFACT_RESTORE"):
            drill = run_restore_drill(
                batch_dir=os.fspath(batch_dir),
                restore_dir=os.fspath(flat),
                passphrase=passphrase,
                rto_ms=rto_ms,
                max_rpo_ms=max_rpo_ms,
                now_ms=now_ms,
                diagnostics=diagnostics,
            )
        drill_hashes = drill.get("sha256")
        if not isinstance(drill_hashes, dict):
            raise L2BackupError("restore drill did not return artifact digests")
        manifest_path = flat / "manifest.json"
        with measure_restore_stage(diagnostics, "MANIFEST_CONTRACT"):
            manifest = _load_manifest(manifest_path)
            source_contract_digest = _verify_manifest_source_contract(manifest, expected_contract)
        entries = manifest.get("entries")
        if not isinstance(entries, list) or not entries:
            raise L2BackupError("restored backup manifest has no entries")
        expected_sources = {item["relative_path"]: item for item in expected_contract["sources"]}
        required_paths = {path for path, item in expected_sources.items() if item["required"]}
        optional_paths = set(expected_sources) - required_paths
        declared_missing = manifest.get("missing_optional_sources")
        if (
            not isinstance(declared_missing, list)
            or any(not isinstance(path, str) for path in declared_missing)
            or len(declared_missing) != len(set(declared_missing))
            or declared_missing != sorted(declared_missing)
        ):
            raise L2BackupError("restored backup manifest has an invalid missing optional source set")
        missing_optional_paths = set(declared_missing)
        if not missing_optional_paths <= optional_paths:
            raise L2BackupError("restored backup manifest marks a non-optional source as missing")
        actual_flat = {path.name for path in flat.iterdir() if path.is_file()}
        expected_flat = {"manifest.json"}
        expected_batch = {"manifest.json.enc"}
        sqlite_verified = True
        restore_history_verified = False
        present_paths: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise L2BackupError("restored backup manifest entry is invalid")
            artifact = entry.get("artifact")
            relative_value = entry.get("relative_path")
            if not isinstance(artifact, str) or not artifact or not isinstance(relative_value, str):
                raise L2BackupError("restored backup manifest entry is incomplete")
            if artifact in {".", ".."} or Path(artifact).name != artifact or artifact in expected_flat:
                raise L2BackupError("restored backup manifest artifact name is unsafe or duplicated")
            expected_flat.add(artifact)
            relative = _safe_relative_path(relative_value)
            relative_path = relative.as_posix()
            expected_source = expected_sources.get(relative_path)
            if expected_source is None:
                raise L2BackupError("restored backup source entry is outside the expected source contract")
            if relative_path in present_paths:
                raise L2BackupError("restored backup source entry is duplicated")
            if entry.get("role") != expected_source["role"] or entry.get("required") is not expected_source["required"]:
                raise L2BackupError("restored backup source entry does not match path, role, or requiredness")
            snapshot_method = entry.get("snapshot_method")
            if snapshot_method not in _SNAPSHOT_METHODS:
                raise L2BackupError("restored backup source entry has an unsupported snapshot method")
            expected_policy = expected_source["snapshot_policy"]
            if (
                expected_policy == SNAPSHOT_POLICY_SQLITE_WAL_PINNED
                and snapshot_method != "SQLITE_WAL_PINNED_BACKUP"
            ) or (
                expected_policy == SNAPSHOT_POLICY_STANDARD
                and snapshot_method == "SQLITE_WAL_PINNED_BACKUP"
            ):
                raise L2BackupError(
                    "restored backup source entry violates its snapshot policy"
                )
            storage_encoding = entry.get(
                "storage_encoding",
                STORAGE_ENCODING_IDENTITY,
            )
            if storage_encoding not in _STORAGE_ENCODINGS:
                raise L2BackupError(
                    "restored backup source entry has an unsupported storage encoding"
                )
            encrypted_suffix = (
                ".zst.enc"
                if storage_encoding == STORAGE_ENCODING_ZSTD_LEVEL_1
                else ".enc"
            )
            expected_batch.add(f"{artifact}{encrypted_suffix}")
            present_paths.add(relative_path)
            source = flat / artifact
            if not source.is_file():
                raise L2BackupError(f"restored artifact is missing: {artifact}")
            source_size = source.stat(follow_symlinks=False).st_size
            # The generic restore drill already hashed every restored artifact
            # once. Reuse that digest instead of rereading each multi-GB file a
            # third time; the manifest value remains the independent expected
            # digest recorded before encryption.
            if entry.get("size") != source_size or entry.get("sha256") != drill_hashes.get(artifact):
                raise L2BackupError(f"restored artifact digest mismatch: {artifact}")
            output = tree.joinpath(*relative.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, output)
            if snapshot_method in {
                "SQLITE_ONLINE_BACKUP",
                "SQLITE_WAL_PINNED_BACKUP",
            }:
                with measure_restore_stage(diagnostics, "SQLITE_INTEGRITY", artifact):
                    _sqlite_integrity(output)
            if expected_source["role"] == "l2_restore_history_authority":
                history_store = None
                try:
                    with measure_restore_stage(diagnostics, "HISTORY_SEMANTICS", artifact):
                        history_store = L2RestoreHistoryStore(os.fspath(output), create=False)
                        if not history_store.chain_valid():
                            raise L2RestoreHistoryStoreError("restore history authority chain is invalid")
                        history_store.read_snapshot()
                        restore_history_verified = True
                except (L2RestoreHistoryStoreError, ValueError, TypeError) as exc:
                    raise L2BackupError("restore history semantic verification failed") from exc
                finally:
                    if history_store is not None:
                        history_store.close()
        if (
            not required_paths <= present_paths
            or present_paths & missing_optional_paths
            or present_paths | missing_optional_paths != set(expected_sources)
        ):
            raise L2BackupError("restored backup source entries do not match the expected source contract")
        if actual_flat != expected_flat:
            raise L2BackupError("backup batch has missing or undeclared encrypted artifacts")
        actual_batch = {
            path.name
            for path in Path(batch_dir).iterdir()
            if path.is_file() and not path.is_symlink()
        }
        if actual_batch != expected_batch:
            raise L2BackupError("backup batch storage encoding does not match its manifest")
        shutil.copy2(manifest_path, tree / ".quantos_backup_manifest.json")
        # Readiness requires the entire verification above, including SQLite
        # integrity and history semantics, not merely decrypted bytes.
        elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        if elapsed_ms > rto_ms:
            raise L2BackupError(
                f"restore drill exceeded RTO: {elapsed_ms}ms > {rto_ms}ms"
            )
        if target.exists():
            target.rmdir()
        os.replace(tree, target)
        diagnostics["completed"] = True
        return {
            **drill,
            "elapsed_ms": elapsed_ms,
            "manifest_verified": True,
            "source_contract_verified": True,
            "source_contract_sha256": source_contract_digest,
            "sqlite_integrity_verified": sqlite_verified,
            "restore_history_verified": restore_history_verified,
            "restored_tree": os.fspath(target),
        }
    except RestoreDrillError as exc:
        raise L2BackupError(str(exc)) from exc
    finally:
        diagnostics["total_ms"] = max(0, round((time.perf_counter() - started) * 1000))
        shutil.rmtree(stage_root, ignore_errors=True)


def _prune_old_snapshots(
    backup_root: Path,
    *,
    keep_count: int = 30,
    keep_days: int = 90,
    now_ms: int | None = None,
) -> dict:
    """Remove old backup snapshots, keeping the most recent keep_count and all
    snapshots younger than keep_days. Returns pruning statistics."""
    if not backup_root.is_dir():
        return {
            "pruned_count": 0,
            "retained_count": 0,
            "reason": "backup_root_not_directory",
        }

    observed_at_ms = int(time.time() * 1000) if now_ms is None else now_ms
    cutoff_ms = observed_at_ms - (keep_days * 86_400_000)

    snapshots = []
    for entry in backup_root.iterdir():
        if not entry.is_dir():
            continue
        # Snapshot naming: YYYYMMDDTHHMMSSZ
        if len(entry.name) == 16 and entry.name.endswith("Z"):
            try:
                # Parse the timestamp from the directory name
                timestamp = datetime.strptime(entry.name, "%Y%m%dT%H%M%SZ")
                timestamp_ms = int(timestamp.replace(tzinfo=timezone.utc).timestamp() * 1000)
                snapshots.append((timestamp_ms, entry))
            except (ValueError, OSError):
                continue

    if not snapshots:
        return {
            "pruned_count": 0,
            "retained_count": 0,
            "reason": "no_snapshots_found",
        }

    snapshots.sort(reverse=True)

    to_prune = []
    for idx, (timestamp_ms, entry) in enumerate(snapshots):
        # Keep if: within keep_count most recent OR younger than cutoff
        if idx >= keep_count and timestamp_ms < cutoff_ms:
            to_prune.append(entry)

    pruned_count = 0
    for entry in to_prune:
        try:
            shutil.rmtree(entry)
            pruned_count += 1
        except OSError:
            pass

    return {
        "pruned_count": pruned_count,
        "retained_count": len(snapshots) - pruned_count,
        "total_found": len(snapshots),
        "keep_count": keep_count,
        "keep_days": keep_days,
        "cutoff_ms": cutoff_ms,
    }


def run_l2_backup(
    *,
    repo_root: str | Path,
    destination_root: str | Path,
    passphrase: str,
    sources: Iterable[BackupSource] = DEFAULT_SOURCES,
    evidence_path: str | Path | None = None,
    status_path: str | Path | None = None,
    allow_local_destination: bool = False,
    rto_ms: int = DEFAULT_RTO_MS,
    max_rpo_ms: int = DEFAULT_MAX_RPO_MS,
    now_ms: int | None = None,
    _publish_compatibility: bool = True,
    restore_diagnostics: dict | None = None,
) -> dict:
    restore_diagnostics = {} if restore_diagnostics is None else restore_diagnostics
    _check_passphrase_strength(passphrase)
    root = Path(repo_root).resolve()
    destination, destination_class = _validate_destination(
        root,
        Path(destination_root),
        allow_local_destination=allow_local_destination,
    )
    source_specs = tuple(sources)
    source_contract = _source_contract(source_specs)
    source_contract_digest = _source_contract_sha256(source_contract)
    discovered, missing_optional = _discover_sources(root, source_specs)
    compression_required = any(
        path.stat(follow_symlinks=False).st_size >= L2_ZSTD_MIN_SIZE
        for _, _, path in discovered
    )
    if compression_required:
        _require_zstd()
    missing_optional = sorted(missing_optional)
    observed_at_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if observed_at_ms < 0:
        raise L2BackupError("now_ms must be non-negative")
    stamp = datetime.fromtimestamp(observed_at_ms / 1000, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_batch = destination / stamp
    if final_batch.exists():
        raise L2BackupError(f"backup batch already exists: {final_batch}")

    _assert_destination_boundary(
        repo_root=root,
        destination_root=destination,
        expected_class=destination_class,
    )
    stage = destination / f".{stamp}.partial-{os.getpid()}-{secrets.token_hex(4)}"
    stage.mkdir()
    entries: list[dict] = []
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="quantos-l2-backup-") as scratch_name:
            scratch = Path(scratch_name)
            for index, (source_spec, relative, source_path) in enumerate(discovered, start=1):
                snapshot, snapshot_method, source_mtime_ns = _snapshot_source_file(
                    source_path,
                    scratch,
                    snapshot_policy=source_spec.snapshot_policy,
                )
                artifact = _artifact_name(index, relative)
                storage_encoding = STORAGE_ENCODING_IDENTITY
                encryption_source = snapshot
                encrypted_name = f"{artifact}.enc"
                compressed = scratch / f"{artifact}.zst"
                if snapshot.stat(follow_symlinks=False).st_size >= L2_ZSTD_MIN_SIZE:
                    _compress_zstd(snapshot, compressed)
                    storage_encoding = STORAGE_ENCODING_ZSTD_LEVEL_1
                    encryption_source = compressed
                    encrypted_name = f"{artifact}.zst.enc"
                try:
                    encrypt_file(
                        os.fspath(encryption_source),
                        os.fspath(stage / encrypted_name),
                        passphrase,
                        chunk_size=L2_ENCRYPTION_CHUNK_SIZE,
                    )
                finally:
                    compressed.unlink(missing_ok=True)
                entries.append(
                    {
                        "artifact": artifact,
                        "relative_path": relative.as_posix(),
                        "required": source_spec.required,
                        "role": source_spec.role,
                        "sha256": _sha256_file(snapshot),
                        "size": snapshot.stat(follow_symlinks=False).st_size,
                        "snapshot_method": snapshot_method,
                        "source_mtime_ns": source_mtime_ns,
                        "storage_encoding": storage_encoding,
                    }
                )

        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "mode": "L2_AUTHORITY_STATE",
            "observed_at_ms": observed_at_ms,
            "source_contract": source_contract,
            "source_contract_sha256": source_contract_digest,
            "entries": entries,
            "missing_optional_sources": missing_optional,
        }
        (stage / "manifest.json.enc").write_bytes(encrypt_bytes(_canonical_json(manifest), passphrase))
        with tempfile.TemporaryDirectory(prefix="quantos-l2-verify-") as verify_parent:
            verification = restore_l2_batch(
                batch_dir=stage,
                restore_dir=Path(verify_parent) / "restored",
                passphrase=passphrase,
                rto_ms=rto_ms,
                max_rpo_ms=max_rpo_ms,
                now_ms=observed_at_ms,
                sources=source_specs,
                diagnostics=restore_diagnostics,
            )
        _assert_destination_boundary(
            repo_root=root,
            destination_root=destination,
            expected_class=destination_class,
        )
        if stage.parent.resolve(strict=True) != destination:
            raise L2BackupError("backup staging boundary changed")
        os.replace(stage, final_batch)
        _assert_destination_boundary(
            repo_root=root,
            destination_root=destination,
            expected_class=destination_class,
        )

        # Prune old snapshots after successful backup
        prune_result = _prune_old_snapshots(destination, now_ms=observed_at_ms)

        elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        result = {
            "schema_version": 1,
            "verdict": "PASS",
            "mode": "L2_AUTHORITY_STATE",
            "observed_at_ms": observed_at_ms,
            "batch_dir": os.fspath(final_batch),
            "destination_class": destination_class,
            "external_destination_verified": destination_class in _EXTERNAL_DESTINATION_CLASSES,
            "encrypted": True,
            "file_count": len(entries),
            # Compatibility count only; the v2 contract and digest are authoritative.
            "required_source_count": sum(1 for entry in entries if entry["required"]),
            "source_contract": source_contract,
            "source_contract_sha256": source_contract_digest,
            "source_contract_verified": verification["source_contract_verified"],
            "restore_history_verified": verification["restore_history_verified"],
            "backed_up_sources": sorted(
                (
                    {
                        "relative_path": entry["relative_path"],
                        "required": entry["required"],
                        "role": entry["role"],
                    }
                    for entry in entries
                ),
                key=lambda item: item["relative_path"],
            ),
            "missing_optional_sources": missing_optional,
            "manifest_verified": verification["manifest_verified"],
            "restore_verified": verification["integrity_verified"],
            "sqlite_integrity_verified": verification["sqlite_integrity_verified"],
            "restore_elapsed_ms": verification["elapsed_ms"],
            "restore_diagnostics": restore_diagnostics,
            "rto_ms": rto_ms,
            "rto_met": verification["rto_met"],
            "source_age_ms": verification["source_age_ms"],
            "max_rpo_ms": max_rpo_ms,
            "rpo_met": verification["rpo_met"],
            "backup_elapsed_ms": elapsed_ms,
            "prune_result": prune_result,
        }
        if _publish_compatibility:
            resolved_evidence = (
                Path(evidence_path) if evidence_path is not None else root / "evidence" / "l2_backup_latest.json"
            )
            resolved_status = (
                Path(status_path) if status_path is not None else root / "data" / "cache" / "l2_backup_status.json"
            )
            _write_json_atomic(resolved_evidence, result)
            _write_json_atomic(
                resolved_status,
                {
                    "schema_version": 1,
                    "verdict": "PASS",
                    "last_attempt_at_ms": observed_at_ms,
                    "last_success_at_ms": observed_at_ms,
                    "batch_dir": os.fspath(final_batch),
                    "destination_class": destination_class,
                    "file_count": len(entries),
                },
            )
        return result
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def run_l2_backup_preflight(
    *,
    repo_root: str | Path,
    destination_root: str | Path,
    passphrase: str,
    sources: Iterable[BackupSource] = DEFAULT_SOURCES,
    history_path: str | Path | None = None,
    allow_local_destination: bool = False,
) -> dict:
    """Validate backup readiness without copying, encrypting, or restoring data.

    Release deployment uses this bounded check to prove the credential,
    destination boundary, source contract, required source presence, and
    append-only restore-history chain.  The scheduled ``l2_backup`` worker
    remains the authority for the expensive backup and isolated restore drill.
    """

    _check_passphrase_strength(passphrase)
    root = Path(repo_root).resolve()
    destination, destination_class = _validate_destination(
        root,
        Path(destination_root),
        allow_local_destination=allow_local_destination,
    )
    source_specs = tuple(sources)
    source_contract = _source_contract(source_specs)
    discovered, missing_optional = _discover_sources(root, source_specs)
    compression_required = any(
        path.stat(follow_symlinks=False).st_size >= L2_ZSTD_MIN_SIZE
        for _, _, path in discovered
    )
    if compression_required:
        _require_zstd()
    resolved_history = (
        Path(history_path)
        if history_path is not None
        else root / "data" / "cache" / "l2_restore_history.db"
    )
    store = None
    try:
        store = L2RestoreHistoryStore(os.fspath(resolved_history), create=False)
        snapshot = store.read_snapshot()
    except (L2RestoreHistoryStoreError, ValueError, TypeError) as exc:
        raise L2BackupError("RESTORE_HISTORY_UNAVAILABLE") from exc
    finally:
        if store is not None:
            store.close()
    return {
        "schema_version": 1,
        "verdict": "PASS",
        "mode": "L2_BACKUP_READINESS_PREFLIGHT",
        "destination_class": destination_class,
        "external_destination_verified": (
            destination_class in _EXTERNAL_DESTINATION_CLASSES
        ),
        "credential_validated": True,
        "source_contract_sha256": _source_contract_sha256(source_contract),
        "source_count": len(discovered),
        "required_source_count": sum(
            1 for source, _relative, _path in discovered if source.required
        ),
        "missing_optional_sources": sorted(missing_optional),
        "restore_history_chain_verified": True,
        "restore_history_attempt_count": len(snapshot.attempts),
        "restore_history_open_attempt_count": len(snapshot.open_attempts),
    }


def _batch_sha256(batch_dir: Path) -> str:
    members = []
    for path in sorted(batch_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            raise L2BackupError("backup batch contains a non-file member")
        members.append(
            {
                "name_sha256": _sha256(path.name.encode("utf-8")),
                "sha256": _sha256_file(path),
                "size": path.stat(follow_symlinks=False).st_size,
            }
        )
    return _sha256(_canonical_json(members))


def _destination_ref_sha256(destination_root: str | Path | None) -> str:
    marker = "UNAVAILABLE" if destination_root is None else os.fspath(destination_root)
    return _sha256(marker.encode("utf-8"))


def _unverified_proof(source_contract_sha256: str) -> RestoreProof:
    return RestoreProof(
        source_contract_sha256=source_contract_sha256,
        destination_class="UNVERIFIED",
        external_destination_verified=False,
        encrypted=False,
        manifest_verified=False,
        source_contract_verified=False,
        restore_verified=False,
        sqlite_integrity_verified=False,
        restore_elapsed_ms=None,
        source_age_ms=None,
        rto_met=False,
        rpo_met=False,
        batch_sha256="",
    )


def _history_projection(
    *,
    snapshot,
    observed_at_ms: int,
    source_contract_sha256: str,
    outcome: RestoreOutcome | None,
    reason_code: str,
) -> dict:
    last_success_at_ms = (
        next(
            (
                record.outcome.completed_at_ms
                for record in reversed(snapshot.outcomes)
                if record.outcome.verdict == "PASS"
            ),
            None,
        )
        if snapshot is not None
        else None
    )
    head = snapshot.authority_head if snapshot is not None else None
    destination_class = "UNVERIFIED" if outcome is None else outcome.proof.destination_class
    last_attempt_at_ms = (
        snapshot.attempts[-1].attempt.started_at_ms if snapshot is not None and snapshot.attempts else observed_at_ms
    )
    return {
        "schema_version": 2,
        "verdict": "PASS" if outcome is not None and outcome.verdict == "PASS" else "BREACH",
        "mode": "L2_AUTHORITY_STATE",
        "observed_at_ms": observed_at_ms,
        "last_attempt_at_ms": last_attempt_at_ms,
        "last_attempt_verdict": "FAIL" if outcome is None else outcome.verdict,
        "last_attempt_stage": "AUTHORITY" if outcome is None else outcome.stage,
        "last_reason_code": reason_code,
        "last_success_at_ms": last_success_at_ms,
        "destination_class": destination_class,
        "source_contract_sha256": source_contract_sha256,
        "history_authoritative": snapshot is not None,
        "history_event_seq": None if head is None else head.event_seq,
        "history_event_hash": "" if head is None else head.event_hash,
    }


def _failure_evidence(
    *,
    observed_at_ms: int,
    source_contract_sha256: str,
    stage: str,
    reason_code: str,
    snapshot,
) -> dict:
    head = snapshot.authority_head if snapshot is not None else None
    return {
        "schema_version": 2,
        "verdict": "FAIL",
        "mode": "L2_AUTHORITY_STATE",
        "observed_at_ms": observed_at_ms,
        "stage": stage,
        "reason_code": reason_code,
        "source_contract_sha256": source_contract_sha256,
        "history_authoritative": snapshot is not None,
        "history_event_seq": None if head is None else head.event_seq,
        "history_event_hash": "" if head is None else head.event_hash,
    }


def _recorded_failure_classification(exc: Exception) -> tuple[str, str]:
    """Return a stable redacted reason and stage for the authority ledger.

    Arbitrary exception text can contain passphrases or destination paths, so
    only reviewed exact/prefix matches become durable reason codes. Unknown
    failures retain the generic code rather than leaking their text.
    """

    message = str(exc)
    if isinstance(exc, L2BackupError):
        exact = {
            "SQLITE_SNAPSHOT_NO_PROGRESS": (
                "SQLITE_SNAPSHOT_NO_PROGRESS",
                "SNAPSHOT",
            ),
            "BACKUP_CONFIGURATION_UNAVAILABLE": (
                "BACKUP_CONFIGURATION_UNAVAILABLE",
                "PREFLIGHT",
            ),
            "EXTERNAL_DESTINATION_REQUIRED": (
                "EXTERNAL_DESTINATION_REQUIRED",
                "PREFLIGHT",
            ),
            "ZSTD_CODEC_UNAVAILABLE": (
                "ZSTD_CODEC_UNAVAILABLE",
                "PREFLIGHT",
            ),
            "ZSTD_COMPRESSION_FAILED": (
                "ZSTD_COMPRESSION_FAILED",
                "ENCRYPTION",
            ),
        }
        if message in exact:
            return exact[message]
        prefixes = (
            (
                "restore drill exceeded RTO:",
                "RESTORE_RTO_EXCEEDED",
                "RESTORE_VERIFICATION",
            ),
            (
                "backup batch exceeds RPO:",
                "RESTORE_RPO_EXCEEDED",
                "RESTORE_VERIFICATION",
            ),
            (
                "SQLite integrity check failed",
                "SQLITE_INTEGRITY_FAILED",
                "RESTORE_VERIFICATION",
            ),
            (
                "restore history semantic verification failed",
                "RESTORE_HISTORY_INVALID",
                "RESTORE_VERIFICATION",
            ),
            (
                "restored backup manifest",
                "RESTORE_MANIFEST_INVALID",
                "RESTORE_VERIFICATION",
            ),
            (
                "restored backup source",
                "RESTORE_SOURCE_CONTRACT_INVALID",
                "RESTORE_VERIFICATION",
            ),
            (
                "restored artifact",
                "RESTORE_ARTIFACT_INVALID",
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
            ("source changed while", "SOURCE_CHANGED_DURING_SNAPSHOT", "SNAPSHOT"),
            ("failed to snapshot SQLite source:", "SQLITE_SNAPSHOT_FAILED", "SNAPSHOT"),
            (
                "source is not a valid SQLite database:",
                "SQLITE_SOURCE_INVALID",
                "SNAPSHOT",
            ),
        )
        for prefix, reason_code, stage in prefixes:
            if message.startswith(prefix):
                return reason_code, stage
    if isinstance(exc, BackupEncryptionError):
        return "BACKUP_ENCRYPTION_FAILED", "ENCRYPTION"
    if isinstance(exc, BackupDecryptionError):
        return "RESTORE_DECRYPTION_FAILED", "RESTORE_VERIFICATION"
    if isinstance(exc, (BackupEncryptionUnavailable, BackupPassphraseTooWeak)):
        return "BACKUP_ENCRYPTION_UNAVAILABLE", "PREFLIGHT"
    return "BACKUP_ATTEMPT_FAILED", "SNAPSHOT"


def _write_recorded_projections(
    *,
    evidence_path: Path,
    status_path: Path,
    evidence: dict,
    status: dict,
) -> None:
    _write_json_atomic(evidence_path, evidence)
    _write_json_atomic(status_path, status)


def run_recorded_l2_backup(
    *,
    repo_root: str | Path,
    destination_root: str | Path | None,
    passphrase: str | None,
    sources: Iterable[BackupSource] = DEFAULT_SOURCES,
    evidence_path: str | Path | None = None,
    status_path: str | Path | None = None,
    history_path: str | Path | None = None,
    allow_local_destination: bool = False,
    rto_ms: int = DEFAULT_RTO_MS,
    max_rpo_ms: int = DEFAULT_MAX_RPO_MS,
    now_ms: int | None = None,
    attempt_id: str | None = None,
) -> dict:
    """Run one backup attempt with append-only restore history as authority."""
    root = Path(repo_root).resolve()
    observed_at_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if isinstance(observed_at_ms, bool) or not isinstance(observed_at_ms, int) or observed_at_ms < 0:
        raise L2BackupError("BACKUP_TIME_INVALID")
    source_specs = tuple(sources)
    try:
        source_contract_sha256 = _source_contract_sha256(_source_contract(source_specs))
    except Exception:
        source_contract_sha256 = _sha256(b"UNVERIFIED_L2_SOURCE_CONTRACT")
    resolved_evidence = (
        Path(evidence_path) if evidence_path is not None else root / "evidence" / "l2_backup_latest.json"
    )
    resolved_status = (
        Path(status_path) if status_path is not None else root / "data" / "cache" / "l2_backup_status.json"
    )
    resolved_history = (
        Path(history_path) if history_path is not None else root / "data" / "cache" / "l2_restore_history.db"
    )
    identifier = attempt_id or (f"l2-backup-{observed_at_ms}-{secrets.token_hex(8)}")
    attempt = RestoreAttempt(
        attempt_id=identifier,
        started_at_ms=observed_at_ms,
        operation_kind="BACKUP_AND_ISOLATED_RESTORE",
        mode="L2_AUTHORITY_STATE",
        source_contract_sha256=source_contract_sha256,
        destination_ref_sha256=_destination_ref_sha256(destination_root),
        rto_ms=rto_ms,
        max_rpo_ms=max_rpo_ms,
    )
    history_store = None
    try:
        history_store = L2RestoreHistoryStore(
            os.fspath(resolved_history),
            create=not resolved_history.is_file(),
        )
        started, _ = history_store.begin_attempt(attempt, expected_head=history_store.authority_head())
    except (L2RestoreHistoryStoreError, ValueError, TypeError):
        reason_code = "RESTORE_HISTORY_UNAVAILABLE"
        status = _history_projection(
            snapshot=None,
            observed_at_ms=observed_at_ms,
            source_contract_sha256=source_contract_sha256,
            outcome=None,
            reason_code=reason_code,
        )
        evidence = _failure_evidence(
            observed_at_ms=observed_at_ms,
            source_contract_sha256=source_contract_sha256,
            stage="AUTHORITY",
            reason_code=reason_code,
            snapshot=None,
        )
        try:
            _write_recorded_projections(
                evidence_path=resolved_evidence,
                status_path=resolved_status,
                evidence=evidence,
                status=status,
            )
        except OSError:
            pass
        if history_store is not None:
            history_store.close()
        raise L2BackupError(reason_code) from None

    restore_diagnostics: dict = {}
    try:
        if destination_root is None or passphrase is None:
            raise L2BackupError("BACKUP_CONFIGURATION_UNAVAILABLE")
        result = run_l2_backup(
            repo_root=root,
            destination_root=destination_root,
            passphrase=passphrase,
            sources=source_specs,
            evidence_path=resolved_evidence,
            status_path=resolved_status,
            allow_local_destination=allow_local_destination,
            rto_ms=rto_ms,
            max_rpo_ms=max_rpo_ms,
            now_ms=observed_at_ms,
            _publish_compatibility=False,
            restore_diagnostics=restore_diagnostics,
        )
        if result.get("external_destination_verified") is not True:
            raise L2BackupError("EXTERNAL_DESTINATION_REQUIRED")
        proof = RestoreProof(
            source_contract_sha256=source_contract_sha256,
            destination_class=result["destination_class"],
            external_destination_verified=True,
            encrypted=result["encrypted"],
            manifest_verified=result["manifest_verified"],
            source_contract_verified=result["source_contract_verified"],
            restore_verified=result["restore_verified"],
            sqlite_integrity_verified=result["sqlite_integrity_verified"],
            restore_elapsed_ms=result["restore_elapsed_ms"],
            source_age_ms=result["source_age_ms"],
            rto_met=result["rto_met"],
            rpo_met=result["rpo_met"],
            batch_sha256=_batch_sha256(Path(result["batch_dir"])),
        )
        completed_at_ms = observed_at_ms if now_ms is not None else max(observed_at_ms, int(time.time() * 1000))
        outcome = RestoreOutcome(
            attempt_id=attempt.attempt_id,
            completed_at_ms=completed_at_ms,
            verdict="PASS",
            stage="COMPLETE",
            reason_code="RESTORE_VERIFIED",
            proof=proof,
        )
        history_store.complete_attempt(
            outcome,
            expected_start=started,
            expected_head=history_store.authority_head(),
        )
        snapshot = history_store.read_snapshot()
        compatible_evidence = {key: value for key, value in result.items() if key != "batch_dir"}
        assert snapshot.authority_head is not None
        compatible_evidence.update(
            history_authoritative=True,
            history_event_seq=snapshot.authority_head.event_seq,
            history_event_hash=snapshot.authority_head.event_hash,
        )
        status = _history_projection(
            snapshot=snapshot,
            observed_at_ms=completed_at_ms,
            source_contract_sha256=source_contract_sha256,
            outcome=outcome,
            reason_code=outcome.reason_code,
        )
        _write_recorded_projections(
            evidence_path=resolved_evidence,
            status_path=resolved_status,
            evidence=compatible_evidence,
            status=status,
        )
        return result
    except Exception as exc:
        reason_code, stage = _recorded_failure_classification(exc)
        completed_at_ms = observed_at_ms if now_ms is not None else max(observed_at_ms, int(time.time() * 1000))
        outcome = RestoreOutcome(
            attempt_id=attempt.attempt_id,
            completed_at_ms=completed_at_ms,
            verdict="FAIL",
            stage=stage,
            reason_code=reason_code,
            proof=_unverified_proof(source_contract_sha256),
        )
        try:
            history_store.complete_attempt(
                outcome,
                expected_start=started,
                expected_head=history_store.authority_head(),
            )
            snapshot = history_store.read_snapshot()
            status = _history_projection(
                snapshot=snapshot,
                observed_at_ms=completed_at_ms,
                source_contract_sha256=source_contract_sha256,
                outcome=outcome,
                reason_code=reason_code,
            )
            evidence = _failure_evidence(
                observed_at_ms=completed_at_ms,
                source_contract_sha256=source_contract_sha256,
                stage=stage,
                reason_code=reason_code,
                snapshot=snapshot,
            )
            if restore_diagnostics:
                evidence["restore_diagnostics"] = restore_diagnostics
            _write_recorded_projections(
                evidence_path=resolved_evidence,
                status_path=resolved_status,
                evidence=evidence,
                status=status,
            )
        except (L2RestoreHistoryStoreError, OSError, ValueError, TypeError):
            pass
        raise L2BackupError(reason_code) from None
    finally:
        history_store.close()


def evaluate_restore_continuity(
    observations: tuple[ContinuityObservation, ...],
    *,
    now_ms: int,
    max_gap_ms: int,
    minimum_history_span_ms: int,
    minimum_passes: int,
    clean_lookback_ms: int,
) -> ContinuityAssessment:
    """Evaluate retained restore receipts without mutating the history store."""

    return evaluate_continuity(
        observations,
        now_ms=now_ms,
        max_gap_ms=max_gap_ms,
        minimum_history_span_ms=minimum_history_span_ms,
        minimum_passes=minimum_passes,
        clean_lookback_ms=clean_lookback_ms,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the encrypted QUANTOS L2 state backup")
    parser.add_argument("--dest", default=os.environ.get(DESTINATION_ENV))
    parser.add_argument("--repo-root", default=os.getcwd())
    parser.add_argument("--rto-ms", type=int, default=DEFAULT_RTO_MS)
    parser.add_argument("--max-rpo-ms", type=int, default=DEFAULT_MAX_RPO_MS)
    parser.add_argument("--allow-local-destination", action="store_true")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help=(
            "Validate credential, external destination, source presence, and "
            "restore-history integrity without creating a backup"
        ),
    )
    parser.add_argument(
        "--passphrase-file",
        default=_default_passphrase_file(),
        help=(
            "Read the backup passphrase from a protected file; mutually exclusive "
            f"with {PASSPHRASE_ENV}"
        ),
    )
    args = parser.parse_args()
    try:
        passphrase = resolve_backup_passphrase(
            environment_passphrase=os.environ.get(PASSPHRASE_ENV),
            passphrase_file=args.passphrase_file,
        )
        if args.preflight_only:
            result = run_l2_backup_preflight(
                repo_root=args.repo_root,
                destination_root=args.dest,
                passphrase=passphrase,
                allow_local_destination=args.allow_local_destination,
            )
        else:
            result = run_recorded_l2_backup(
                repo_root=args.repo_root,
                destination_root=args.dest,
                passphrase=passphrase,
                allow_local_destination=args.allow_local_destination,
                rto_ms=args.rto_ms,
                max_rpo_ms=args.max_rpo_ms,
            )
    except L2BackupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "batch_dir"},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
