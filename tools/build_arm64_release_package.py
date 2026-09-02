#!/usr/bin/env python3
"""Build and verify a reviewable native-ARM64 QUANTOS release package.

The builder is deliberately unprivileged.  It does not install a release,
touch systemd, mutate the state root, or stop services.  Its output binds one
clean immutable Git commit to the frontend payload, the reviewed L2 unit set,
and one externally staged TongDaXin SQLite/panel pair.  A privileged cutover
must consume this manifest and retain the transactional deployment stages in
``docs/runbooks/arm64-release-packaging.md``.
"""

from __future__ import annotations

import argparse
from collections import Counter
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from typing import Any, Mapping, Sequence

from quantos.contracts.l2_schedule_policy import (
    HARD_QUARANTINED_MODULES,
    MVP_DISABLED_MODULES,
    MVP_SAFE_MODULES,
)
from scripts.evidence_binding import (
    assert_release_evidence_manifest,
    resolve_canonical_source_anchor,
)
from tools.ashare_tdx_intake import _REQUIRED_SCHEMA_COLUMNS
from tools.render_systemd_l2_units import (
    SystemdRenderError,
    render_research_runner_unit,
    render_special_units,
    render_units,
)


SCHEMA = "quantos.arm64_release_package.v1"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
TDX_STATE_RELATIVE_ROOT = "data/stocks/pit/tdx-windows-20260830"
TDX_SQLITE_NAME = "ashare_tdx_pit.sqlite"
TDX_PANEL_NAME = "ashare_tdx_pit.adv20000000.panel.npz"
MIN_ADV_CNY = 20_000_000.0
FRONTEND_BUILD_CONTRACT = "quantos-build-contract.json"
FRONTEND_RUNTIME_CONTRACT = "quantos-runtime-contract.json"
FRONTEND_BUILD_SCHEMA = "quantos.frontend_build.v1"
FRONTEND_RUNTIME_SCHEMA = "quantos.frontend_runtime.v1"
SAME_ORIGIN_MARKER = "__QUANTOS_SAME_ORIGIN__"
TDX_V2_SNAPSHOT_TABLES = (
    "source_captures",
    "universe_snapshots",
    "security_master_snapshots",
    "fundamental_snapshots",
    "industry_snapshots",
    "current_business_snapshots",
    "opaque_source_snapshots",
)
TDX_PANEL_KEYS = (
    "db_sha256",
    "min_adv_cny",
    "metadata_json",
    "dates",
    "instruments",
    "opens",
    "highs",
    "lows",
    "closes",
    "volumes",
    "amounts",
    "base_eligible",
    "trailing_adv20",
    "bad_bars",
)
TDX_SOURCE_CAPTURE_COLUMNS = (
    (0, "capture_date", "INTEGER", 0, None, 1),
    (1, "manifest_sha256", "TEXT", 1, None, 0),
    (2, "source_root", "TEXT", 1, None, 0),
    (3, "source_report_json", "TEXT", 1, None, 0),
)
TDX_SOURCE_KIND = "TONGDAXIN_LOCAL_DAILY_AND_CURRENT_METADATA"
TDX_TECHNICAL_STATUS = "TECHNICAL_VALIDATION_ONLY"
DEPLOY_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "deploy" / "arm64" / "deploy.sh.in"
)
SYSTEMD_TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "deploy" / "systemd"
RELEASE_TOKEN = "__QUANTOS_RELEASE_SHA__"

# This is the last successfully deployed ARM64 worker profile plus the two
# admitted A-share jobs.  Conditional external seal/heartbeat workers are not
# silently enabled: they require separately reviewed endpoints and mounts.
DEFAULT_WORKER_MODULES = (
    "ashare_alpha_search_worker",
    "ashare_forward_observation",
    "binance_book_depth_forward_refresh",
    "binance_realtime_drift_alerts",
    "binance_realtime_features",
    "binance_realtime_market",
    "binance_rpi_depth",
    "binance_systemic_risk",
    "cftc_tff_forward_refresh",
    "cross_venue_liquidation_health",
    "defillama_stablecoin_forward_refresh",
    "defillama_tvl_forward_refresh",
    "domain_event_ops",
    "hyperliquid_public_forward_refresh",
    "l2_backup",
    "mexc_klines_fetch",
    "okx_borrow_forward_refresh",
    "raw_archive_audit",
    "wikimedia_attention_forward_refresh",
)
SPECIAL_UNITS = (
    "quantos-binance-realtime-stream.service",
    "quantos-bybit-realtime-stream.service",
    "quantos-l2-watchdog.service",
    "quantos-l2-watchdog.timer",
)
SUPPORT_UNITS = (
    "quantos-console-api.service",
    "quantos-console-frontend.service",
    "quantos-research-runner.service",
)
CONSOLE_UNITS = SUPPORT_UNITS[:2]
OLD_USER_TRANSIENT_UNITS = (
    "quantos-mvp-console-api.service",
    "quantos-mvp-research-runner.service",
    "quantos-mvp-frontend.service",
)
FORBIDDEN_RUNTIME_MODULES = tuple(
    sorted(HARD_QUARANTINED_MODULES | MVP_DISABLED_MODULES)
)


class ReleasePackageError(RuntimeError):
    """A package input or output violates the immutable release contract."""


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
) -> str:
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleasePackageError(f"command failed to start: {argv[0]}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        suffix = detail[-1] if detail else f"exit {completed.returncode}"
        raise ReleasePackageError(f"command failed: {argv[0]}: {suffix}")
    return completed.stdout.strip()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular_file(path: Path, *, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleasePackageError(f"{label} is unavailable: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ReleasePackageError(f"{label} must be a regular non-symlink file: {path}")
    return path


def _strict_source_report(raw: object, *, capture_date: int) -> dict[str, Any]:
    """Decode a capture report without duplicate keys or non-JSON constants."""

    if not isinstance(raw, str):
        raise ReleasePackageError(
            f"TDX source capture {capture_date} report must be JSON text"
        )

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReleasePackageError(
                    f"TDX source capture {capture_date} report has duplicate key: {key}"
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ReleasePackageError(
            f"TDX source capture {capture_date} report has invalid constant: {value}"
        )

    try:
        report = json.loads(
            raw,
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except ReleasePackageError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleasePackageError(
            f"TDX source capture {capture_date} report is invalid JSON"
        ) from exc
    if not isinstance(report, dict):
        raise ReleasePackageError(
            f"TDX source capture {capture_date} report must be a JSON object"
        )
    return report


def _positive_contract_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReleasePackageError(f"TDX source capture has invalid {label}")
    return value


def _validate_source_capture(
    *,
    capture_date: object,
    manifest_sha256: object,
    source_root: object,
    source_report_json: object,
) -> dict[str, Any]:
    if (
        isinstance(capture_date, bool)
        or not isinstance(capture_date, int)
        or capture_date <= 0
    ):
        raise ReleasePackageError("TDX source capture has invalid capture_date column")
    if (
        not isinstance(manifest_sha256, str)
        or SHA64.fullmatch(manifest_sha256) is None
    ):
        raise ReleasePackageError("TDX source capture has invalid manifest_sha256 column")
    if not isinstance(source_root, str) or not source_root:
        raise ReleasePackageError("TDX source capture has invalid source_root column")

    report = _strict_source_report(source_report_json, capture_date=capture_date)
    exact_values: tuple[tuple[str, object], ...] = (
        ("schema_version", 2),
        ("evidence_status", TDX_TECHNICAL_STATUS),
        ("source_kind", TDX_SOURCE_KIND),
        ("capture_date", capture_date),
        ("source_root", source_root),
    )
    for key, expected in exact_values:
        value = report.get(key)
        invalid_integer = key in {"schema_version", "capture_date"} and (
            isinstance(value, bool) or not isinstance(value, int)
        )
        if value != expected or invalid_integer:
            raise ReleasePackageError(
                f"TDX source capture {capture_date} report has invalid {key}"
            )
    if report.get("promotion_allowed") is not False:
        raise ReleasePackageError(
            f"TDX source capture {capture_date} report permits promotion"
        )
    if report.get("paper_persistence_allowed") is not False:
        raise ReleasePackageError(
            f"TDX source capture {capture_date} report permits paper persistence"
        )
    if "manifest_sha256" in report and report["manifest_sha256"] != manifest_sha256:
        raise ReleasePackageError(
            f"TDX source capture {capture_date} top-level manifest identity drifted"
        )

    verification = report.get("sha256_verification")
    if not isinstance(verification, dict):
        raise ReleasePackageError(
            f"TDX source capture {capture_date} lacks sha256 verification"
        )
    if verification.get("status") != "PASS":
        raise ReleasePackageError(
            f"TDX source capture {capture_date} sha256 verification is not PASS"
        )
    if verification.get("manifest_sha256") != manifest_sha256:
        raise ReleasePackageError(
            f"TDX source capture {capture_date} manifest identity drifted"
        )
    files = _positive_contract_int(
        verification.get("files"),
        label="sha256_verification.files",
    )
    bytes_ = _positive_contract_int(
        verification.get("bytes"),
        label="sha256_verification.bytes",
    )

    pre_publish = report.get("pre_publish_sha256_verification")
    if pre_publish is not None:
        if not isinstance(pre_publish, dict) or pre_publish.get("status") != "PASS":
            raise ReleasePackageError(
                f"TDX source capture {capture_date} pre-publish verification is not PASS"
            )
        pre_publish_files = _positive_contract_int(
            pre_publish.get("files"),
            label="pre_publish_sha256_verification.files",
        )
        pre_publish_bytes = _positive_contract_int(
            pre_publish.get("bytes"),
            label="pre_publish_sha256_verification.bytes",
        )
        expected_identity = (manifest_sha256, files, bytes_)
        observed_identity = (
            pre_publish.get("manifest_sha256"),
            pre_publish_files,
            pre_publish_bytes,
        )
        if observed_identity != expected_identity:
            raise ReleasePackageError(
                f"TDX source capture {capture_date} pre-publish identity drifted"
            )
    return report


def tree_manifest(root: str | Path) -> tuple[dict[str, str], str]:
    """Return exact regular-file hashes and a canonical tree digest."""

    directory = Path(root)
    if not directory.is_dir() or directory.is_symlink():
        raise ReleasePackageError(f"tree root must be a real directory: {directory}")
    rows: dict[str, str] = {}
    for candidate in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
        relative = candidate.relative_to(directory).as_posix()
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ReleasePackageError(f"tree contains a symlink: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ReleasePackageError(f"tree contains a non-regular file: {relative}")
        rows[relative] = sha256_file(candidate)
    if not rows:
        raise ReleasePackageError(f"tree contains no files: {directory}")
    encoded = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return rows, hashlib.sha256(encoded).hexdigest()


def inspect_tdx_sqlite(path: str | Path) -> dict[str, Any]:
    database = _regular_file(Path(path), label="TDX SQLite")
    uri = f"file:{database.resolve()}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check != ("ok",):
                raise ReleasePackageError("TDX SQLite quick_check failed")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "daily_prices" not in tables:
                raise ReleasePackageError("TDX SQLite lacks daily_prices")
            schema_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            missing_v2 = sorted(set(_REQUIRED_SCHEMA_COLUMNS) - tables)
            if schema_version != 2 or missing_v2:
                raise ReleasePackageError(
                    "TDX SQLite is not the reviewed schema v2 snapshot store: "
                    f"version={schema_version} missing={missing_v2}"
                )
            for table, required_columns in _REQUIRED_SCHEMA_COLUMNS.items():
                escaped = table.replace('"', '""')
                actual_columns = {
                    str(row[1])
                    for row in connection.execute(
                        f'PRAGMA table_info("{escaped}")'
                    )
                }
                missing_columns = sorted(required_columns - actual_columns)
                if missing_columns:
                    raise ReleasePackageError(
                        "TDX schema v2 table "
                        f"{table} lacks required columns: {missing_columns}"
                    )
            source_capture_columns = tuple(
                (
                    int(row[0]),
                    str(row[1]),
                    str(row[2]).upper(),
                    int(row[3]),
                    row[4],
                    int(row[5]),
                )
                for row in connection.execute("PRAGMA table_info(source_captures)")
            )
            if source_capture_columns != TDX_SOURCE_CAPTURE_COLUMNS:
                raise ReleasePackageError(
                    "TDX source_captures schema drifted from the exact four-column contract"
                )
            manifest_unique = False
            for index in connection.execute("PRAGMA index_list(source_captures)"):
                if not int(index[2]):
                    continue
                index_name = str(index[1]).replace('"', '""')
                indexed_columns = tuple(
                    str(row[2])
                    for row in connection.execute(
                        f'PRAGMA index_info("{index_name}")'
                    )
                )
                if indexed_columns == ("manifest_sha256",):
                    manifest_unique = True
            if not manifest_unique:
                raise ReleasePackageError(
                    "TDX source_captures manifest_sha256 is not uniquely constrained"
                )
            observed = connection.execute(
                "SELECT count(*),count(DISTINCT instrument),"
                "count(DISTINCT trade_date),min(trade_date),max(trade_date),"
                "sum(CASE WHEN knowledge_time>trade_date THEN 1 ELSE 0 END) "
                "FROM daily_prices"
            ).fetchone()
            snapshot_rows = {
                table: int(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                )
                for table in TDX_V2_SNAPSHOT_TABLES
            }
            capture_range = connection.execute(
                "SELECT min(capture_date),max(capture_date) FROM source_captures"
            ).fetchone()
            capture_rows = connection.execute(
                "SELECT capture_date,manifest_sha256,source_root,source_report_json "
                "FROM source_captures ORDER BY capture_date"
            ).fetchall()
            if not capture_rows:
                raise ReleasePackageError("TDX source_captures has no provenance rows")
            capture_reports = [
                _validate_source_capture(
                    capture_date=row[0],
                    manifest_sha256=row[1],
                    source_root=row[2],
                    source_report_json=row[3],
                )
                for row in capture_rows
            ]
            latest_capture_date = int(capture_rows[-1][0])
            latest_universe_rows = int(
                connection.execute(
                    "SELECT count(*) FROM universe_snapshots WHERE capture_date=?",
                    (latest_capture_date,),
                ).fetchone()[0]
            )
            latest_security_rows = int(
                connection.execute(
                    "SELECT count(*) FROM security_master_snapshots WHERE capture_date=?",
                    (latest_capture_date,),
                ).fetchone()[0]
            )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ReleasePackageError("TDX SQLite cannot be inspected read-only") from exc
    assert observed is not None
    rows, instruments, dates, first_date, last_date, future_known = observed
    if not rows or not instruments or not dates or first_date is None or last_date is None:
        raise ReleasePackageError("TDX SQLite has no usable daily-price history")
    if int(future_known or 0) != 0:
        raise ReleasePackageError("TDX SQLite contains future-known price rows")
    latest_report = capture_reports[-1]
    latest_rows_total = latest_report.get("rows_total")
    if (
        isinstance(latest_rows_total, bool)
        or not isinstance(latest_rows_total, int)
        or latest_rows_total != int(rows)
    ):
        raise ReleasePackageError(
            "latest TDX source capture rows_total does not match daily_prices"
        )
    latest_source_rows = latest_report.get("rows")
    if (
        isinstance(latest_source_rows, bool)
        or not isinstance(latest_source_rows, int)
        or latest_source_rows < 1
        or latest_source_rows > int(rows)
    ):
        raise ReleasePackageError(
            "latest TDX source capture rows is not a valid source-scan count"
        )
    latest_source_instruments = latest_report.get("instruments")
    if (
        isinstance(latest_source_instruments, bool)
        or not isinstance(latest_source_instruments, int)
        or latest_source_instruments < 1
        or latest_source_instruments != latest_universe_rows
        or latest_security_rows != latest_universe_rows
    ):
        raise ReleasePackageError(
            "latest TDX source capture does not match its current universe snapshots"
        )
    latest_source_first = latest_report.get("first_date")
    latest_source_last = latest_report.get("last_date")
    if (
        isinstance(latest_source_first, bool)
        or not isinstance(latest_source_first, int)
        or isinstance(latest_source_last, bool)
        or not isinstance(latest_source_last, int)
        or latest_source_first > latest_source_last
        or latest_source_last > latest_capture_date
    ):
        raise ReleasePackageError(
            "latest TDX source capture has an invalid source-scan date range"
        )
    return {
        "path": str(database.resolve()),
        "sha256": sha256_file(database),
        "bytes": database.stat().st_size,
        "rows": int(rows),
        "instruments": int(instruments),
        "dates": int(dates),
        "first_date": int(first_date),
        "last_date": int(last_date),
        "future_known_rows": 0,
        "quick_check": "ok",
        "schema_version": schema_version,
        "snapshot_rows": snapshot_rows,
        "capture_first": int(capture_range[0]) if capture_range[0] is not None else None,
        "capture_last": int(capture_range[1]) if capture_range[1] is not None else None,
    }


def inspect_tdx_panel(
    path: str | Path,
    *,
    database_path: str | Path,
    database_sha256: str,
) -> dict[str, Any]:
    """Prove a panel is the complete production rebuild of ``database_path``.

    A cache's self-reported database digest is not evidence that its matrices
    came from that database.  Rebuild the canonical cache through the exact
    research loader and compare every scalar, metadata document, and array.
    NPZ members are decompressed one at a time so verification does not retain
    a second full panel in memory.
    """

    panel = _regular_file(Path(path), label="TDX panel")
    database = _regular_file(Path(database_path), label="TDX SQLite")
    if SHA64.fullmatch(database_sha256) is None:
        raise ReleasePackageError("TDX database digest is invalid")

    required = set(TDX_PANEL_KEYS)
    try:
        import numpy as np

        with np.load(panel, allow_pickle=False) as payload:
            keys = tuple(payload.files)
            key_set = set(keys)
            if len(keys) != len(key_set):
                raise ReleasePackageError("TDX panel contains duplicate fields")
            if key_set != required:
                raise ReleasePackageError(
                    "TDX panel field contract drifted: "
                    f"missing={sorted(required - key_set)} "
                    f"unexpected={sorted(key_set - required)}"
                )
            database_field = payload["db_sha256"]
            minimum_adv_field = payload["min_adv_cny"]
            metadata_field = payload["metadata_json"]
            if database_field.shape != () or database_field.dtype.kind != "U":
                raise ReleasePackageError("TDX panel db_sha256 scalar dtype drifted")
            if minimum_adv_field.shape != () or minimum_adv_field.dtype != np.dtype(
                np.float64
            ):
                raise ReleasePackageError("TDX panel min_adv_cny scalar dtype drifted")
            if metadata_field.shape != () or metadata_field.dtype.kind != "U":
                raise ReleasePackageError("TDX panel metadata_json scalar dtype drifted")
            bound_database = str(database_field.item())
            minimum_adv = float(minimum_adv_field.item())
            try:
                submitted_metadata = json.loads(str(metadata_field.item()))
            except (TypeError, ValueError) as exc:
                raise ReleasePackageError("TDX panel metadata_json is invalid") from exc
            if not isinstance(submitted_metadata, dict):
                raise ReleasePackageError("TDX panel metadata_json must be an object")
    except ReleasePackageError:
        raise
    except Exception as exc:
        raise ReleasePackageError("TDX panel cannot be inspected safely") from exc

    if bound_database != database_sha256:
        raise ReleasePackageError("TDX panel is not bound to the supplied database")
    if minimum_adv != MIN_ADV_CNY:
        raise ReleasePackageError("TDX panel minimum-ADV contract drifted")

    # `_load_panel(..., panel_cache=...)` also exercises the exact cache writer,
    # giving this verifier a disk-backed canonical reference.  Drop the returned
    # MarketPanel before comparison; the submitted and reference NPZ members are
    # then loaded and released one pair at a time.
    try:
        from tools.ashare_alpha_search_technical import _load_panel

        with tempfile.TemporaryDirectory(prefix="quantos-tdx-panel-verify-") as root:
            rebuilt_path = Path(root) / TDX_PANEL_NAME
            rebuilt_panel, rebuilt_metadata = _load_panel(
                database,
                min_adv_cny=MIN_ADV_CNY,
                panel_cache=rebuilt_path,
            )
            del rebuilt_panel, rebuilt_metadata
            gc.collect()

            first_date: int | None = None
            last_date: int | None = None
            date_count = 0
            instrument_count = 0
            with (
                np.load(panel, allow_pickle=False) as submitted,
                np.load(rebuilt_path, allow_pickle=False) as rebuilt,
            ):
                rebuilt_keys = tuple(rebuilt.files)
                if set(rebuilt_keys) != required or len(rebuilt_keys) != len(required):
                    raise ReleasePackageError(
                        "canonical TDX panel writer emitted an unexpected field contract"
                    )
                for key in TDX_PANEL_KEYS:
                    actual = submitted[key]
                    expected = rebuilt[key]
                    if actual.dtype != expected.dtype:
                        raise ReleasePackageError(
                            f"TDX panel field dtype mismatch: {key}: "
                            f"actual={actual.dtype} expected={expected.dtype}"
                        )
                    if actual.shape != expected.shape:
                        raise ReleasePackageError(
                            f"TDX panel field shape mismatch: {key}: "
                            f"actual={actual.shape} expected={expected.shape}"
                        )
                    if np.issubdtype(actual.dtype, np.inexact):
                        equivalent = np.array_equal(actual, expected, equal_nan=True)
                    else:
                        equivalent = np.array_equal(actual, expected)
                    if not equivalent:
                        label = "metadata" if key == "metadata_json" else key
                        raise ReleasePackageError(
                            f"TDX panel field value mismatch: {label}"
                        )
                    if key == "dates":
                        date_count = int(actual.size)
                        if date_count:
                            first_date = int(actual[0])
                            last_date = int(actual[-1])
                    elif key == "instruments":
                        instrument_count = int(actual.size)
                    del actual, expected
    except ReleasePackageError:
        raise
    except Exception as exc:
        raise ReleasePackageError(
            "TDX panel cannot be reconstructed from the supplied database"
        ) from exc

    if not date_count or not instrument_count or first_date is None or last_date is None:
        raise ReleasePackageError("TDX panel has no usable observations")
    return {
        "path": str(panel.resolve()),
        "sha256": sha256_file(panel),
        "bytes": panel.stat().st_size,
        "database_sha256": bound_database,
        "min_adv_cny": minimum_adv,
        "dates": date_count,
        "instruments": instrument_count,
        "first_date": first_date,
        "last_date": last_date,
    }


def _assert_clean_commit(repo: Path, release_commit: str | None) -> str:
    head = _run(("git", "rev-parse", "HEAD"), cwd=repo)
    if SHA40.fullmatch(head) is None:
        raise ReleasePackageError("repository HEAD is not a full lowercase commit")
    if release_commit is not None and release_commit != head:
        raise ReleasePackageError("requested release commit is not repository HEAD")
    dirty = _run(
        (
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ),
        cwd=repo,
    )
    if dirty:
        raise ReleasePackageError("release packaging requires a completely clean worktree")
    return head


def _frontend_source_sha256(repo: Path) -> str:
    """Hash the exact Git-tracked frontend inputs from their working-tree bytes."""

    try:
        result = subprocess.run(
            ("git", "ls-files", "-z", "--", "apps/research-console"),
            cwd=repo,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleasePackageError("frontend tracked source set cannot be listed") from exc
    try:
        relatives = sorted(
            item.decode("utf-8")
            for item in result.stdout.split(b"\0")
            if item
        )
    except UnicodeDecodeError as exc:
        raise ReleasePackageError("frontend tracked path is not UTF-8") from exc
    if not relatives:
        raise ReleasePackageError("frontend tracked source set is empty")
    rows: list[list[str]] = []
    for relative in relatives:
        source = _regular_file(repo / relative, label="frontend tracked source")
        try:
            source.relative_to(repo)
        except ValueError as exc:
            raise ReleasePackageError("frontend tracked source escapes repository") from exc
        rows.append([relative, sha256_file(source)])
    encoded = json.dumps(
        rows,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _copy_regular_tree(source: Path, destination: Path) -> None:
    source_rows, _ = tree_manifest(source)
    destination.mkdir(parents=True, mode=0o700)
    for relative in source_rows:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)


def _assert_rendered_console_unit(
    name: str,
    content: str,
    *,
    release_commit: str,
) -> None:
    """Reject mutable, mixed-release, or weakened console unit renderings."""

    if name not in CONSOLE_UNITS:
        raise ReleasePackageError(f"unsupported console unit: {name}")
    if RELEASE_TOKEN in content:
        raise ReleasePackageError(f"console unit has an unresolved release token: {name}")
    release_values = re.findall(
        r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])",
        content,
    )
    if not release_values or set(release_values) != {release_commit}:
        raise ReleasePackageError(f"console unit has mixed release bindings: {name}")
    path_bindings = re.findall(
        r"/opt/quantos/(?:releases|venvs|frontend)/([0-9a-f]{40})",
        content,
    )
    if not path_bindings or set(path_bindings) != {release_commit}:
        raise ReleasePackageError(f"console unit has mixed immutable paths: {name}")
    if "EnvironmentFile=" in content or "LoadCredential=" in content:
        raise ReleasePackageError(f"console unit allows mutable environment input: {name}")

    common_service = (
        "Type=simple",
        "User=quantos",
        "Group=quantos",
        "UMask=0077",
        "Environment=LIVE_TRADING_ENABLED=false",
        "Environment=REAL_BROKER_ENABLED=false",
        "Environment=ALLOW_ORDER_SUBMISSION=false",
        "Environment=ALLOW_AI_DIRECT_TRADING=false",
        "Environment=REQUIRE_HUMAN_APPROVAL=true",
        "Environment=QUANTOS_L2_DRY_RUN_ONLY=true",
        f"Environment=QUANTOS_RELEASE_ROOT=/opt/quantos/releases/{release_commit}",
        f"Environment=QUANTOS_RELEASE_COMMIT={release_commit}",
        f"Environment=QUANTOS_BUILD_SHA={release_commit}",
        "Environment=PYTHONDONTWRITEBYTECODE=1",
        "Environment=HOME=/tmp",
        "Restart=on-failure",
        "RestartSec=5s",
        "KillMode=control-group",
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
        "IPAddressAllow=localhost",
    )
    if name == "quantos-console-api.service":
        unit_directives = (
            "Description=QUANTOS read-only monitoring API (L2 dry-run only)",
            f"Documentation=file:///opt/quantos/releases/{release_commit}/"
            "docs/runbooks/local-research-stack.md",
            "After=network.target",
        )
        service_directives = (
            "Environment=QUANTOS_STATE_ROOT=/srv/quantos/state",
            f"Environment=PYTHONPATH=/opt/quantos/releases/{release_commit}",
            "WorkingDirectory=/srv/quantos/state",
            f"ExecStart=/opt/quantos/venvs/{release_commit}/bin/python -P -m uvicorn "
            f"--app-dir /opt/quantos/releases/{release_commit} "
            "services.console.main:app --host 127.0.0.1 --port 8400",
            "TimeoutStartSec=90s",
            "TimeoutStopSec=30s",
            f"ReadOnlyPaths=/opt/quantos/releases/{release_commit}",
            "ReadOnlyPaths=/srv/quantos/state",
        )
    else:
        unit_directives = (
            "Description=QUANTOS read-only monitoring frontend (L2 dry-run only)",
            f"Documentation=file:///opt/quantos/releases/{release_commit}/"
            "docs/runbooks/local-research-stack.md",
            "BindsTo=quantos-console-api.service",
            "After=quantos-console-api.service",
        )
        service_directives = (
            f"Environment=QUANTOS_FRONTEND_DIST=/opt/quantos/frontend/{release_commit}",
            f"WorkingDirectory=/opt/quantos/frontend/{release_commit}",
            f"ExecStart=/opt/quantos/venvs/{release_commit}/bin/python -P "
            f"/opt/quantos/releases/{release_commit}/tools/local_frontend_server.py "
            "--host 192.168.5.68 --port 3100 --allow-network 192.168.5.0/24",
            "TimeoutStartSec=30s",
            "TimeoutStopSec=20s",
            f"ReadOnlyPaths=/opt/quantos/releases/{release_commit}",
            f"ReadOnlyPaths=/opt/quantos/frontend/{release_commit}",
            "InaccessiblePaths=/srv/quantos/state",
            "IPAddressAllow=192.168.5.0/24",
        )
    lines = content.splitlines()
    sections: list[str] = []
    observed: Counter[tuple[str, str]] = Counter()
    section: str | None = None
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            if line not in {"[Unit]", "[Service]", "[Install]"}:
                raise ReleasePackageError(f"console unit has an unknown section: {name}")
            section = line[1:-1]
            sections.append(section)
            continue
        if line != line.strip() or section is None:
            raise ReleasePackageError(f"console unit directive placement drifted: {name}")
        observed[(section, line)] += 1
    if sections != ["Unit", "Service", "Install"]:
        raise ReleasePackageError(f"console unit section contract drifted: {name}")
    expected: Counter[tuple[str, str]] = Counter(
        [
            *(("Unit", line) for line in unit_directives),
            *(("Service", line) for line in (*common_service, *service_directives)),
            ("Install", "WantedBy=multi-user.target"),
        ]
    )
    if observed != expected:
        missing = [f"[{section}] {line}" for section, line in sorted((expected - observed).elements())]
        unexpected = [
            f"[{section}] {line}"
            for section, line in sorted((observed - expected).elements())
        ]
        raise ReleasePackageError(
            f"console unit exact contract drifted: {name}: "
            f"missing={missing!r} unexpected={unexpected!r}"
        )


def _render_console_units(
    *,
    output_root: Path,
    release_commit: str,
) -> tuple[Path, ...]:
    """Render the two checked-in console templates into an existing support tree."""

    if SHA40.fullmatch(release_commit) is None:
        raise ReleasePackageError("release commit must be 40 lowercase hex characters")
    if not output_root.is_dir() or output_root.is_symlink():
        raise ReleasePackageError("console support output must be a real directory")
    written: list[Path] = []
    for name in CONSOLE_UNITS:
        rendered = _rendered_console_unit_text(name, release_commit=release_commit)
        target = output_root / name
        target.write_text(rendered, encoding="utf-8", newline="\n")
        written.append(target)
    return tuple(written)


def _rendered_console_unit_text(name: str, *, release_commit: str) -> str:
    if name not in CONSOLE_UNITS:
        raise ReleasePackageError(f"unsupported console unit: {name}")
    if SHA40.fullmatch(release_commit) is None:
        raise ReleasePackageError("release commit must be 40 lowercase hex characters")
    template = _regular_file(
        SYSTEMD_TEMPLATE_ROOT / name,
        label=f"console unit template {name}",
    ).read_text(encoding="utf-8")
    if template.count(RELEASE_TOKEN) < 2:
        raise ReleasePackageError(f"console unit template lacks release bindings: {name}")
    if re.search(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", template):
        raise ReleasePackageError(f"console unit template embeds a release commit: {name}")
    if "\n[Install]\n" in template:
        raise ReleasePackageError(f"console unit template is directly installable: {name}")
    rendered = template.replace(RELEASE_TOKEN, release_commit).rstrip()
    rendered += "\n\n[Install]\nWantedBy=multi-user.target\n"
    _assert_rendered_console_unit(
        name,
        rendered,
        release_commit=release_commit,
    )
    return rendered


def _rendered_research_runner_unit_text(*, release_commit: str) -> str:
    """Rebuild the exact vetted runner unit for package verification."""

    if SHA40.fullmatch(release_commit) is None:
        raise ReleasePackageError("release commit must be 40 lowercase hex characters")
    name = "quantos-research-runner.service"
    template = _regular_file(
        SYSTEMD_TEMPLATE_ROOT / name,
        label="research runner unit template",
    ).read_text(encoding="utf-8")
    if template.count(RELEASE_TOKEN) < 2:
        raise ReleasePackageError("research runner unit template lacks release bindings")
    if re.search(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", template):
        raise ReleasePackageError("research runner unit template embeds a release commit")
    if "\n[Install]\n" in template:
        raise ReleasePackageError("research runner unit template is directly installable")
    rendered = template.replace(RELEASE_TOKEN, release_commit).rstrip()
    rendered += "\n\n[Install]\nWantedBy=multi-user.target\n"
    if RELEASE_TOKEN in rendered:
        raise ReleasePackageError("research runner unit release binding is incomplete")
    release_values = re.findall(
        r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])",
        rendered,
    )
    path_bindings = re.findall(
        r"/opt/quantos/(?:releases|venvs)/([0-9a-f]{40})",
        rendered,
    )
    if (
        not release_values
        or set(release_values) != {release_commit}
        or not path_bindings
        or set(path_bindings) != {release_commit}
    ):
        raise ReleasePackageError("research runner unit has mixed release bindings")
    return rendered


def _render_profile_units(
    destination: Path,
    *,
    release_commit: str,
    backup_mount: str,
    attention_release_commit: str | None = None,
    worker_modules: Sequence[str] = DEFAULT_WORKER_MODULES,
) -> tuple[dict[str, str], str]:
    worker_root = destination / "workers"
    special_root = destination / "special"
    support_root = destination / "support"
    render_units(
        output_root=worker_root,
        release_commit=release_commit,
        modules=worker_modules,
        backup_mount=backup_mount,
    )
    render_special_units(
        output_root=special_root,
        release_commit=release_commit,
        attention_release_commit=attention_release_commit,
    )
    render_research_runner_unit(
        output_root=support_root,
        release_commit=release_commit,
    )
    _render_console_units(
        output_root=support_root,
        release_commit=release_commit,
    )
    return tree_manifest(destination)


def _canonical_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _shell_array(values: Sequence[str]) -> str:
    """Render one safely quoted Bash array body, one value per line."""

    return "\n".join(f"  {shlex.quote(value)}" for value in values)


def _current_release_contract(current_release_root: str | Path) -> dict[str, str]:
    """Bind the reusable native venv and exact rollback baseline.

    The release package intentionally does not build dependencies while root is
    cutting over services.  The already deployed release is therefore part of
    the package contract, and its venv symlink target must be an absolute,
    executable native environment.
    """

    current = Path(current_release_root).resolve()
    if not current.is_dir() or current.is_symlink():
        raise ReleasePackageError("current release root must be a real directory")
    commit = _run(("git", "rev-parse", "HEAD"), cwd=current)
    if SHA40.fullmatch(commit) is None:
        raise ReleasePackageError("current release identity is invalid")
    if current.name != commit:
        raise ReleasePackageError("current release path is not commit-addressed")
    if _run(
        (
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ),
        cwd=current,
    ):
        raise ReleasePackageError("current release checkout is dirty")
    venv_link = Path("/opt/quantos/venvs") / commit
    try:
        link_metadata = venv_link.lstat()
    except OSError as exc:
        raise ReleasePackageError("current release venv link is unavailable") from exc
    if not stat.S_ISLNK(link_metadata.st_mode):
        raise ReleasePackageError("current release venv must be a symlink")
    try:
        base_venv = venv_link.resolve(strict=True)
    except OSError as exc:
        raise ReleasePackageError("current release venv target is unavailable") from exc
    if not base_venv.is_dir() or base_venv.is_symlink():
        raise ReleasePackageError("current release venv target is unsafe")
    python = base_venv / "bin" / "python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ReleasePackageError("current venv Python is not executable")
    return {
        "commit": commit,
        "root": str(current),
        "venv_link": str(venv_link),
        "base_venv": str(base_venv),
    }


def render_deploy_script(
    destination: str | Path,
    *,
    package_root: str | Path,
    release_commit: str,
    source_commit: str,
    evidence_manifest_sha256: str,
    bundle_name: str,
    bundle_sha256: str,
    frontend_tree_sha256: str,
    backup_mount: str,
    baseline: Mapping[str, str],
    sqlite_contract: Mapping[str, Any],
    panel_contract: Mapping[str, Any],
) -> Path:
    """Render the reviewed, target-specific privileged cutover transaction."""

    template = _regular_file(DEPLOY_TEMPLATE, label="ARM64 deploy template").read_text(
        encoding="utf-8"
    )
    replacements = {
        "__DEPLOY_PACKAGE_DIR__": shlex.quote(str(Path(package_root).resolve())),
        "__DEPLOY_RELEASE_COMMIT__": shlex.quote(release_commit),
        "__DEPLOY_SOURCE_COMMIT__": shlex.quote(source_commit),
        "__DEPLOY_EVIDENCE_SHA256__": shlex.quote(evidence_manifest_sha256),
        "__DEPLOY_BUNDLE_NAME__": shlex.quote(bundle_name),
        "__DEPLOY_BUNDLE_SHA256__": shlex.quote(bundle_sha256),
        "__DEPLOY_FRONTEND_TREE_SHA256__": shlex.quote(frontend_tree_sha256),
        "__DEPLOY_BACKUP_MOUNT__": shlex.quote(backup_mount),
        "__DEPLOY_BASELINE_COMMIT__": shlex.quote(str(baseline["commit"])),
        "__DEPLOY_BASELINE_ROOT__": shlex.quote(str(baseline["root"])),
        "__DEPLOY_BASE_VENV__": shlex.quote(str(baseline["base_venv"])),
        "__DEPLOY_TDX_SQLITE_PATH__": shlex.quote(str(sqlite_contract["path"])),
        "__DEPLOY_TDX_SQLITE_SHA256__": shlex.quote(str(sqlite_contract["sha256"])),
        "__DEPLOY_TDX_SQLITE_ROWS__": str(int(sqlite_contract["rows"])),
        "__DEPLOY_TDX_INSTRUMENTS__": str(int(sqlite_contract["instruments"])),
        "__DEPLOY_TDX_DATES__": str(int(sqlite_contract["dates"])),
        "__DEPLOY_TDX_FIRST_DATE__": str(int(sqlite_contract["first_date"])),
        "__DEPLOY_TDX_LAST_DATE__": str(int(sqlite_contract["last_date"])),
        "__DEPLOY_TDX_PANEL_PATH__": shlex.quote(str(panel_contract["path"])),
        "__DEPLOY_TDX_PANEL_SHA256__": shlex.quote(str(panel_contract["sha256"])),
        "__DEPLOY_WORKERS__": _shell_array(DEFAULT_WORKER_MODULES),
        "__DEPLOY_FORBIDDEN_MODULES__": _shell_array(FORBIDDEN_RUNTIME_MODULES),
        "__DEPLOY_OLD_USER_UNITS__": _shell_array(OLD_USER_TRANSIENT_UNITS),
    }
    for token, value in replacements.items():
        if token not in template:
            raise ReleasePackageError(f"deploy template lacks token: {token}")
        template = template.replace(token, value)
    if re.search(r"__DEPLOY_[A-Z0-9_]+__", template):
        raise ReleasePackageError("deploy template contains an unresolved token")
    target = Path(destination)
    target.write_text(template, encoding="utf-8", newline="\n")
    target.chmod(0o755)
    return target


def _package_hash_rows(package: Path) -> dict[str, str]:
    rows, _ = tree_manifest(package)
    return {
        name: digest
        for name, digest in rows.items()
        if name != "SHA256SUMS"
    }


def _write_sha256sums(package: Path) -> None:
    rows = _package_hash_rows(package)
    content = "".join(f"{digest}  {name}\n" for name, digest in sorted(rows.items()))
    (package / "SHA256SUMS").write_text(content, encoding="utf-8", newline="\n")


def _strict_json(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReleasePackageError(f"duplicate JSON key in {path}: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleasePackageError(f"invalid package manifest: {path}") from exc
    if not isinstance(value, dict):
        raise ReleasePackageError("package manifest must be an object")
    return value


def _validate_frontend_build_contract(
    frontend: Path,
    *,
    release_commit: str,
    frontend_source_sha256: str,
) -> dict[str, Any]:
    contract = _strict_json(
        _regular_file(
            frontend / FRONTEND_BUILD_CONTRACT,
            label="frontend build contract",
        )
    )
    expected = {
        "schema": FRONTEND_BUILD_SCHEMA,
        "release_commit": release_commit,
        "frontend_source_sha256": frontend_source_sha256,
        "api_url": SAME_ORIGIN_MARKER,
        "runner_url": SAME_ORIGIN_MARKER,
        "mock_mode": "false",
    }
    if contract != expected:
        raise ReleasePackageError(
            "frontend build is not same-origin, real-backend, and mock-disabled"
        )
    return contract


def _frontend_runtime_contract(release_commit: str) -> dict[str, Any]:
    return {
        "schema": FRONTEND_RUNTIME_SCHEMA,
        "release_commit": release_commit,
        "api_url": SAME_ORIGIN_MARKER,
        "runner_url": SAME_ORIGIN_MARKER,
        "mock_mode": False,
    }


def _verify_packaged_frontend_contract(
    package: Path,
    manifest: Mapping[str, Any],
) -> None:
    release = manifest.get("release")
    frontend_contract = manifest.get("frontend")
    if not isinstance(release, Mapping) or not isinstance(frontend_contract, Mapping):
        raise ReleasePackageError("packaged frontend release contract is absent")
    commit = str(release.get("commit") or "")
    if SHA40.fullmatch(commit) is None:
        raise ReleasePackageError("packaged frontend release commit is invalid")
    frontend = package / "frontend"
    source_sha256 = frontend_contract.get("source_sha256")
    if not isinstance(source_sha256, str) or SHA64.fullmatch(source_sha256) is None:
        raise ReleasePackageError("packaged frontend source identity is invalid")
    _validate_frontend_build_contract(
        frontend,
        release_commit=commit,
        frontend_source_sha256=source_sha256,
    )
    runtime = _strict_json(
        _regular_file(
            frontend / FRONTEND_RUNTIME_CONTRACT,
            label="frontend runtime contract",
        )
    )
    if runtime != _frontend_runtime_contract(commit):
        raise ReleasePackageError("packaged frontend runtime identity drifted")
    rows, digest = tree_manifest(frontend)
    if (
        frontend_contract.get("tree_sha256") != digest
        or frontend_contract.get("file_count") != len(rows)
        or frontend_contract.get("runtime_contract") != FRONTEND_RUNTIME_CONTRACT
    ):
        raise ReleasePackageError("packaged frontend tree contract drifted")


def _verify_packaged_systemd_contract(
    package: Path,
    manifest: Mapping[str, Any],
) -> None:
    """Re-render and byte-compare the complete immutable systemd unit tree."""

    systemd = manifest.get("systemd")
    release = manifest.get("release")
    if not isinstance(systemd, dict) or not isinstance(release, dict):
        raise ReleasePackageError("systemd or release contract is invalid")
    release_commit = release.get("commit")
    if not isinstance(release_commit, str) or SHA40.fullmatch(release_commit) is None:
        raise ReleasePackageError("systemd release commit is invalid")
    required_systemd_keys = {
        "worker_modules",
        "special_units",
        "support_units",
        "unit_tree_sha256",
        "unit_files",
        "backup_mount",
        "attention_release_commit",
    }
    if set(systemd) != required_systemd_keys:
        raise ReleasePackageError("systemd construction contract drifted")
    rows, digest = tree_manifest(package / "units")
    if systemd.get("unit_files") != rows or systemd.get("unit_tree_sha256") != digest:
        raise ReleasePackageError("systemd unit tree manifest drifted")
    if systemd.get("worker_modules") != list(DEFAULT_WORKER_MODULES):
        raise ReleasePackageError("systemd worker profile drifted")
    if systemd.get("special_units") != list(SPECIAL_UNITS):
        raise ReleasePackageError("systemd special-unit profile drifted")
    if systemd.get("support_units") != list(SUPPORT_UNITS):
        raise ReleasePackageError("systemd support-unit profile drifted")
    backup_mount = systemd.get("backup_mount")
    attention_release_commit = systemd.get("attention_release_commit")
    if not isinstance(backup_mount, str) or not backup_mount:
        raise ReleasePackageError("systemd backup mount contract is invalid")
    if attention_release_commit != release_commit:
        raise ReleasePackageError("systemd attention release contract is invalid")
    expected_names = {
        *(f"workers/quantos-l2@{module}.service" for module in DEFAULT_WORKER_MODULES),
        *(f"workers/quantos-l2@{module}.timer" for module in DEFAULT_WORKER_MODULES),
        *(f"special/{name}" for name in SPECIAL_UNITS),
        *(f"support/{name}" for name in SUPPORT_UNITS),
    }
    if set(rows) != expected_names:
        raise ReleasePackageError("systemd unit profile is incomplete")
    with tempfile.TemporaryDirectory(prefix="quantos-systemd-verify-") as temporary:
        expected_root = Path(temporary) / "units"
        try:
            expected_rows, expected_digest = _render_profile_units(
                expected_root,
                release_commit=release_commit,
                backup_mount=backup_mount,
                attention_release_commit=attention_release_commit,
                worker_modules=tuple(systemd["worker_modules"]),
            )
        except (OSError, UnicodeError, SystemdRenderError) as exc:
            raise ReleasePackageError("systemd construction parameters are invalid") from exc
        if expected_rows != rows or expected_digest != digest:
            raise ReleasePackageError("rendered systemd unit tree drifted")
        for relative in sorted(expected_rows):
            try:
                actual_text = (package / "units" / relative).read_text(encoding="utf-8")
                expected_text = (expected_root / relative).read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ReleasePackageError(
                    f"rendered systemd unit is unreadable: {relative}"
                ) from exc
            if actual_text != expected_text:
                raise ReleasePackageError(f"rendered systemd unit text drifted: {relative}")


def verify_package(
    package_root: str | Path,
    *,
    verify_external_data: bool = True,
    source_root: str | Path | None = None,
) -> dict[str, Any]:
    package = Path(package_root)
    if not package.is_dir() or package.is_symlink():
        raise ReleasePackageError("package root must be a real directory")
    manifest_path = _regular_file(package / "PACKAGE_MANIFEST.json", label="manifest")
    manifest = _strict_json(manifest_path)
    if manifest.get("schema") != SCHEMA:
        raise ReleasePackageError("package schema is unsupported")
    expected = manifest.get("package_files")
    if not isinstance(expected, dict) or not expected:
        raise ReleasePackageError("package file manifest is missing")
    actual, _ = tree_manifest(package)
    actual.pop("SHA256SUMS", None)
    # PACKAGE_MANIFEST cannot hash itself.  It is bound by SHA256SUMS, while its
    # package_files field binds every payload below it.
    actual_payload = {k: v for k, v in actual.items() if k != "PACKAGE_MANIFEST.json"}
    if actual_payload != expected:
        raise ReleasePackageError("package payload hash set drifted")
    _verify_packaged_systemd_contract(package, manifest)
    _verify_packaged_frontend_contract(package, manifest)
    if source_root is not None:
        source = Path(source_root).resolve()
        release = manifest.get("release")
        frontend = manifest.get("frontend")
        if not isinstance(release, Mapping) or not isinstance(frontend, Mapping):
            raise ReleasePackageError("packaged frontend release contract is absent")
        commit = release.get("commit")
        if _run(("git", "rev-parse", "HEAD"), cwd=source) != commit:
            raise ReleasePackageError("frontend verifier checkout does not match release")
        if frontend.get("source_sha256") != _frontend_source_sha256(source):
            raise ReleasePackageError("packaged frontend was not built from the release source")
    installer = manifest.get("installer")
    if (
        not isinstance(installer, dict)
        or installer.get("path") != "deploy.sh"
        or installer.get("sha256") != actual_payload.get("deploy.sh")
        or installer.get("transaction") != "QUIESCED_ROLLBACKABLE_ARM64_CUTOVER"
    ):
        raise ReleasePackageError("deploy installer contract is absent or drifted")
    sums_path = _regular_file(package / "SHA256SUMS", label="SHA256SUMS")
    parsed: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or SHA64.fullmatch(digest) is None or name in parsed:
            raise ReleasePackageError("SHA256SUMS is malformed")
        parsed[name] = digest
    if parsed != actual:
        raise ReleasePackageError("SHA256SUMS does not bind the exact package")
    deploy = _regular_file(package / "deploy.sh", label="deploy.sh")
    if not deploy.stat().st_mode & 0o111:
        raise ReleasePackageError("deploy.sh is not executable")
    if verify_external_data:
        tdx = manifest.get("tdx")
        if not isinstance(tdx, dict):
            raise ReleasePackageError("TDX contract is absent")
        for key in ("sqlite", "panel"):
            row = tdx.get(key)
            if not isinstance(row, dict):
                raise ReleasePackageError(f"TDX {key} contract is absent")
            path = _regular_file(Path(str(row.get("path") or "")), label=f"TDX {key}")
            if sha256_file(path) != row.get("sha256"):
                raise ReleasePackageError(f"TDX {key} hash drifted")
    return manifest


def build_package(
    *,
    repo_root: str | Path,
    frontend_dist: str | Path,
    tdx_sqlite: str | Path,
    tdx_panel: str | Path,
    output_root: str | Path,
    backup_mount: str,
    release_commit: str | None = None,
    current_release_root: str | Path | None = None,
) -> Path:
    repo = Path(repo_root).resolve()
    if not repo.is_dir() or repo.is_symlink():
        raise ReleasePackageError("repository root must be a real directory")
    commit = _assert_clean_commit(repo, release_commit)
    frontend_source_sha256 = _frontend_source_sha256(repo)
    source_commit = resolve_canonical_source_anchor(repo)
    _document, evidence_digest = assert_release_evidence_manifest(
        repo,
        expected_release_commit=commit,
    )
    sqlite_contract = inspect_tdx_sqlite(tdx_sqlite)
    panel_contract = inspect_tdx_panel(
        tdx_panel,
        database_path=tdx_sqlite,
        database_sha256=sqlite_contract["sha256"],
    )
    if (
        panel_contract["first_date"] != sqlite_contract["first_date"]
        or panel_contract["last_date"] != sqlite_contract["last_date"]
    ):
        raise ReleasePackageError("TDX panel date range does not match its database")
    frontend_source = Path(frontend_dist).resolve()
    frontend_source_rows, _frontend_source_digest = tree_manifest(frontend_source)
    if "index.html" not in frontend_source_rows:
        raise ReleasePackageError("frontend distribution lacks index.html")
    _validate_frontend_build_contract(
        frontend_source,
        release_commit=commit,
        frontend_source_sha256=frontend_source_sha256,
    )
    if current_release_root is None:
        raise ReleasePackageError(
            "current release root is required to bind the native venv and rollback baseline"
        )
    current = Path(current_release_root).resolve()
    baseline_contract = _current_release_contract(current)
    _regular_file(current / "requirements.txt", label="current requirements")
    _regular_file(repo / "requirements.txt", label="new requirements")
    if (current / "requirements.txt").read_bytes() != (repo / "requirements.txt").read_bytes():
        raise ReleasePackageError(
            "Python dependency lock changed; build and validate a new native ARM64 venv first"
        )

    output = Path(output_root).resolve()
    if output.exists() or output.is_symlink():
        raise ReleasePackageError("output path must not already exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)
    )
    try:
        assert temporary is not None
        bundle_name = f"quantos-{commit[:8]}.bundle"
        bundle = temporary / bundle_name
        _run(("git", "bundle", "create", str(bundle), "HEAD"), cwd=repo, timeout=300)
        _run(("git", "bundle", "verify", str(bundle)), cwd=repo, timeout=300)
        packaged_frontend = temporary / "frontend"
        _copy_regular_tree(frontend_source, packaged_frontend)
        _canonical_json(
            packaged_frontend / FRONTEND_RUNTIME_CONTRACT,
            _frontend_runtime_contract(commit),
        )
        frontend_rows, frontend_digest = tree_manifest(packaged_frontend)
        units_rows, units_digest = _render_profile_units(
            temporary / "units",
            release_commit=commit,
            backup_mount=backup_mount,
        )
        rendered_names = tuple(sorted(units_rows))
        expected_names = tuple(
            sorted(
                [
                    *(f"workers/quantos-l2@{module}.service" for module in DEFAULT_WORKER_MODULES),
                    *(f"workers/quantos-l2@{module}.timer" for module in DEFAULT_WORKER_MODULES),
                    *(f"special/{name}" for name in SPECIAL_UNITS),
                    *(f"support/{name}" for name in SUPPORT_UNITS),
                ]
            )
        )
        if rendered_names != expected_names:
            raise ReleasePackageError("rendered systemd profile is incomplete")
        render_deploy_script(
            temporary / "deploy.sh",
            package_root=output,
            release_commit=commit,
            source_commit=source_commit,
            evidence_manifest_sha256=evidence_digest,
            bundle_name=bundle_name,
            bundle_sha256=sha256_file(bundle),
            frontend_tree_sha256=frontend_digest,
            backup_mount=backup_mount,
            baseline=baseline_contract,
            sqlite_contract=sqlite_contract,
            panel_contract=panel_contract,
        )
        package_payload = _package_hash_rows(temporary)
        manifest: dict[str, Any] = {
            "schema": SCHEMA,
            "architecture": "linux/arm64",
            "readiness": "L2_DRY_RUN_ONLY",
            "release": {
                "commit": commit,
                "source_commit": source_commit,
                "evidence_manifest_sha256": evidence_digest,
                "bundle": bundle_name,
                "bundle_sha256": sha256_file(bundle),
                "baseline": baseline_contract,
            },
            "frontend": {
                "tree_sha256": frontend_digest,
                "file_count": len(frontend_rows),
                "runtime_contract": FRONTEND_RUNTIME_CONTRACT,
                "source_sha256": frontend_source_sha256,
            },
            "installer": {
                "path": "deploy.sh",
                "sha256": package_payload["deploy.sh"],
                "transaction": "QUIESCED_ROLLBACKABLE_ARM64_CUTOVER",
                "synchronous_backup": False,
                "synchronous_wikimedia_refresh": False,
            },
            "tdx": {
                "state_relative_root": TDX_STATE_RELATIVE_ROOT,
                "sqlite": sqlite_contract,
                "panel": panel_contract,
                "publish_mode": "QUIESCED_PAIR_REPLACE_WITH_ROLLBACK",
            },
            "systemd": {
                "worker_modules": list(DEFAULT_WORKER_MODULES),
                "special_units": list(SPECIAL_UNITS),
                "support_units": list(SUPPORT_UNITS),
                "unit_tree_sha256": units_digest,
                "unit_files": units_rows,
                "backup_mount": backup_mount,
                "attention_release_commit": commit,
            },
            "cleanup_after_official_readiness": {
                "user_transient_units": list(OLD_USER_TRANSIENT_UNITS),
                "action": "STOP_ONLY_AFTER_OFFICIAL_RUNNER_AND_FRONTEND_PASS",
            },
            "safety": {
                "live_trading_enabled": False,
                "real_broker_enabled": False,
                "order_submission_allowed": False,
                "ai_direct_trading_allowed": False,
                "human_approval_required": True,
                "invalidated_perp_ledger_v1_enabled": False,
                "forbidden_runtime_modules": list(FORBIDDEN_RUNTIME_MODULES),
            },
            "required_cutover_stages": [
                "native_arm64_and_package_preflight",
                "release_and_evidence_install",
                "scheduler_quiesce",
                "oneshot_worker_drain_without_forced_stop",
                "tdx_pair_stage_verify_replace",
                "systemd_unit_snapshot_and_cutover",
                "resident_stream_semantic_readiness",
                "console_frontend_and_research_runner_readiness",
                "ashare_search_lock_and_forward_history_seed",
                "watchdog_initial_probe",
                "post_cutover_health",
                "rollback_sensitive_transaction_commit",
                "ashare_search_completion_and_forward_observation",
                "watchdog_final_probe",
                "old_user_transient_stop",
            ],
            "package_files": package_payload,
        }
        _canonical_json(temporary / "PACKAGE_MANIFEST.json", manifest)
        _write_sha256sums(temporary)
        verify_package(
            temporary,
            verify_external_data=True,
            source_root=repo,
        )
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build or verify a native ARM64 QUANTOS release package"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    build.add_argument("--frontend-dist", required=True)
    build.add_argument("--tdx-sqlite", required=True)
    build.add_argument("--tdx-panel", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--backup-mount", required=True)
    build.add_argument("--release-commit")
    build.add_argument("--current-release-root", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("package")
    verify.add_argument("--skip-external-data", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            output = build_package(
                repo_root=args.repo_root,
                frontend_dist=args.frontend_dist,
                tdx_sqlite=args.tdx_sqlite,
                tdx_panel=args.tdx_panel,
                output_root=args.output,
                backup_mount=args.backup_mount,
                release_commit=args.release_commit,
                current_release_root=args.current_release_root,
            )
            print(f"ARM64_RELEASE_PACKAGE_PASS path={output}")
        else:
            manifest = verify_package(
                args.package,
                verify_external_data=not args.skip_external_data,
                source_root=Path(__file__).resolve().parents[1],
            )
            print(
                "ARM64_RELEASE_PACKAGE_VERIFY_PASS "
                f"release={manifest['release']['commit']}"
            )
    except (OSError, UnicodeError, ValueError, ReleasePackageError) as exc:
        raise SystemExit(f"ARM64_RELEASE_PACKAGE_BLOCKED: {exc}") from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
