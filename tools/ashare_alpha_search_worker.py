"""Run the admitted A-share technical search from fixed L2 state paths.

The older ``tools.ashare_alpha_research`` driver is intentionally quarantined.
This worker uses only ``ashare_alpha_search_technical``: close-t signals,
open-(t+1) execution, non-overlapping holding periods, and a confirmation split
that is excluded from selection.  It never writes a paper/execution ledger.

On the first successful run it also freezes the selected candidates and the
latest source date for the independent forward-observation worker.  Later
searches may refresh the rolling diagnostic artifact, but never rewrite that
freeze.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from quantos.contracts.hashing import stable_hash
from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from quantos.runtime_paths import runtime_state_root
from tools.ashare_alpha_search_technical import (
    TECHNICAL_STATUS,
    _load_panel,
    _load_panel_cache,
    _write_panel_cache,
    run_search,
)
from tools.ashare_alpha_runtime_lock import DEFAULT_RUNTIME_LOCK, acquire_runtime_lock
from tools.cache_io import atomic_write_json


ROOT = runtime_state_root()
PIT_ROOT = ROOT / "data" / "stocks" / "pit" / "tdx-windows-20260830"
DEFAULT_DATABASE = PIT_ROOT / "ashare_tdx_pit.sqlite"
DEFAULT_PANEL_CACHE = PIT_ROOT / "ashare_tdx_pit.adv20000000.panel.npz"
DEFAULT_OUTPUT = ROOT / "data" / "cache" / "ashare_alpha_search.json"
DEFAULT_FREEZE = ROOT / "data" / "cache" / "ashare_forward_freeze.json"
MIN_ADV_CNY = 20_000_000.0
SOURCE_PREFIX_ALGORITHM = "SHA256_SQLITE_DAILY_PRICES_PK_PREFIX_V1"
_SOURCE_PREFIX_DOMAIN = b"quantos.ashare.daily_prices.pk-prefix.v1\0"
_SOURCE_PREFIX_ROW = struct.Struct(">qqdddddqq")
_CANDIDATE_KEYS = frozenset({"name", "family", "window", "skip", "hold_days"})
_SOURCE_PREFIX_KEYS = frozenset(
    {"schema_version", "algorithm", "cutoff", "rows", "sha256"}
)
_FREEZE_KEYS = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "evidence_status",
        "freeze_cutoff",
        "starts_after",
        "minimum_sessions",
        "candidate_specs",
        "candidate_set_hash",
        "source_database_sha256_at_freeze",
        "source_rows_at_freeze",
        "source_prefix",
        "selection_locked",
        "promotion_allowed",
        "paper_persistence_allowed",
        "execution_allowed",
        "search_artifact_sha256",
    }
)
_LAST_RELEASE_RUN_ATTESTATION: dict[str, Any] | None = None
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SAFETY = {
    "paper_only": True,
    "live_allowed": False,
    "order_submission_allowed": False,
    "credentials_accessed": False,
    "broker_connectivity": False,
}


class AShareSearchWorkerError(RuntimeError):
    """The fixed A-share source, cache, or freeze contract is invalid."""


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _published_json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_atomic_write_json(
    path: Path,
    value: Mapping[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, value, indent=2)
    # atomic_write_json fsyncs the file before rename.  Persist the renamed
    # directory entry as well so an immutable freeze never survives a reboot
    # while the content-addressed artifact it names disappears.
    _fsync_directory(path.parent)


def _source_signature(path: Path) -> tuple[int, int, int, int]:
    metadata = path.stat()
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
    )


def _database_latest_trade_date(database: str | Path) -> int:
    """Read the terminal source session without mutating or caching the database."""

    database_path = Path(database)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{database_path.resolve()}?mode=ro",
            uri=True,
        )
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
    except sqlite3.DatabaseError as exc:
        raise AShareSearchWorkerError(
            "A-share source terminal date cannot be verified"
        ) from exc
    finally:
        if connection is not None:
            connection.close()
    if row is None or isinstance(row[0], bool) or not isinstance(row[0], int):
        raise AShareSearchWorkerError("A-share source has no terminal trade date")
    return int(row[0])


def _source_session_finality(
    latest_date: int,
    *,
    observed_at_ms: int | None = None,
) -> dict[str, Any]:
    """Classify whether the terminal daily bar is past the Shanghai close.

    TongDaXin may expose today's still-changing daily record during the session.
    Such bytes must never become a frozen selection cutoff or an immutable
    forward checkpoint. Historical terminal dates are already final; a future
    terminal date is invalid rather than silently treated as complete.
    """

    if isinstance(latest_date, bool) or not isinstance(latest_date, int):
        raise AShareSearchWorkerError("A-share source terminal date is invalid")
    now_ms = int(time.time() * 1000) if observed_at_ms is None else observed_at_ms
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise AShareSearchWorkerError("A-share source observation time is invalid")
    try:
        observed = dt.datetime.fromtimestamp(now_ms / 1000.0, tz=_SHANGHAI)
    except (OverflowError, OSError, ValueError) as exc:
        raise AShareSearchWorkerError("A-share source observation time is invalid") from exc
    observed_date = int(observed.strftime("%Y%m%d"))
    if latest_date > observed_date:
        raise AShareSearchWorkerError("A-share source terminal date is in the future")
    close_at = dt.datetime.combine(
        observed.date(),
        dt.time(15, 0),
        tzinfo=_SHANGHAI,
    )
    close_at_ms = int(close_at.timestamp() * 1000)
    is_final = latest_date < observed_date or now_ms >= close_at_ms
    return {
        "latest_date": latest_date,
        "observed_at_ms": now_ms,
        "shanghai_date": observed_date,
        "session_close_ms": close_at_ms if latest_date == observed_date else None,
        "is_final": is_final,
    }


def _source_not_final_report(
    module: str,
    finality: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_kind": "ASHARE_RUNTIME_SOURCE_FINALITY_V1",
        "evidence_status": TECHNICAL_STATUS,
        "status": "SAFE_BLOCKED_SOURCE_NOT_FINAL",
        "module": module,
        "retry_allowed": True,
        "state_mutated": False,
        "source_finality": dict(finality),
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "execution_allowed": False,
        "safety": {**_SAFETY, "legacy_v1_used": False},
    }


def _source_not_final_attestation(
    module: str,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    release_commit = os.environ.get("QUANTOS_RELEASE_COMMIT", "").strip().lower()
    return {
        "schema_version": 1,
        "module": module,
        "release_commit": release_commit,
        "complete": bool(
            len(release_commit) == 40
            and all(character in "0123456789abcdef" for character in release_commit)
        ),
        "input_hashes": {
            "source_finality": stable_hash(dict(report["source_finality"]))
        },
        "output_hashes": {"safe_blocked_decision": stable_hash(dict(report))},
        "safety": dict(_SAFETY),
    }


def _is_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _source_prefix_bindings(
    database: str | Path,
    cutoffs: list[int] | tuple[int, ...] | set[int],
) -> dict[int, dict[str, Any]]:
    """Hash exact daily-price prefixes at one or more terminal dates.

    The row encoding is typed and length-delimited, and the SQL order is part of
    the versioned algorithm.  Computing several checkpoints in one scan keeps a
    daily forward cycle bounded even for the full A-share panel.
    """

    requested = sorted(set(cutoffs))
    if not requested or any(
        isinstance(value, bool) or not isinstance(value, int) for value in requested
    ):
        raise AShareSearchWorkerError("source prefix cutoffs are invalid")
    database_path = Path(database)
    signature_before = _source_signature(database_path)
    digests = {cutoff: hashlib.sha256(_SOURCE_PREFIX_DOMAIN) for cutoff in requested}
    row_counts = {cutoff: 0 for cutoff in requested}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{database_path.resolve()}?mode=ro",
            uri=True,
        )
        connection.execute("PRAGMA query_only=ON")
        columns = tuple(
            str(row[1])
            for row in connection.execute("PRAGMA table_info(daily_prices)")
        )
        required_columns = (
            "instrument",
            "trade_date",
            "knowledge_time",
            "open",
            "high",
            "low",
            "close",
            "amount",
            "volume",
            "reserved",
        )
        if columns != required_columns:
            raise AShareSearchWorkerError("A-share daily-price prefix schema drifted")
        rows = connection.execute(
            "SELECT instrument,trade_date,knowledge_time,open,high,low,close,"
            "amount,volume,reserved FROM daily_prices WHERE trade_date<=? "
            "ORDER BY instrument,trade_date",
            (requested[-1],),
        )
        for raw in rows:
            trade_date = int(raw[1])
            instrument = str(raw[0]).encode("utf-8")
            prices = tuple(float(value) for value in raw[3:8])
            if not instrument or len(instrument) > 0xFFFFFFFF or not all(
                math.isfinite(value) for value in prices
            ):
                raise AShareSearchWorkerError("A-share daily-price prefix row is invalid")
            encoded = (
                struct.pack(">I", len(instrument))
                + instrument
                + _SOURCE_PREFIX_ROW.pack(
                    trade_date, int(raw[2]), *prices, int(raw[8]), int(raw[9])
                )
            )
            for cutoff in requested:
                if trade_date <= cutoff:
                    digests[cutoff].update(encoded)
                    row_counts[cutoff] += 1
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError, struct.error) as exc:
        if isinstance(exc, AShareSearchWorkerError):
            raise
        raise AShareSearchWorkerError("A-share daily-price prefix cannot be verified") from exc
    finally:
        if connection is not None:
            connection.close()
    if signature_before != _source_signature(database_path):
        raise AShareSearchWorkerError("A-share PIT database changed during prefix verification")
    return {
        cutoff: {
            "schema_version": 1,
            "algorithm": SOURCE_PREFIX_ALGORITHM,
            "cutoff": cutoff,
            "rows": row_counts[cutoff],
            "sha256": digests[cutoff].hexdigest(),
        }
        for cutoff in requested
    }


def _validate_freeze_document(
    document: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Validate the complete immutable forward-freeze schema."""

    if set(document) != _FREEZE_KEYS:
        raise AShareSearchWorkerError("forward freeze fields are invalid")
    cutoff = document.get("freeze_cutoff")
    minimum_sessions = document.get("minimum_sessions")
    source_rows = document.get("source_rows_at_freeze")
    if (
        document.get("schema_version") != 1
        or document.get("artifact_kind") != "ASHARE_UNTOUCHED_FORWARD_FREEZE_V1"
        or document.get("evidence_status") != TECHNICAL_STATUS
        or isinstance(cutoff, bool)
        or not isinstance(cutoff, int)
        or document.get("starts_after") != cutoff
        or isinstance(minimum_sessions, bool)
        or not isinstance(minimum_sessions, int)
        or minimum_sessions <= 0
        or isinstance(source_rows, bool)
        or not isinstance(source_rows, int)
        or source_rows <= 0
        or not _is_sha256(document.get("candidate_set_hash"))
        or not _is_sha256(document.get("source_database_sha256_at_freeze"))
        or not _is_sha256(document.get("search_artifact_sha256"))
        or document.get("selection_locked") is not True
        or document.get("promotion_allowed") is not False
        or document.get("paper_persistence_allowed") is not False
        or document.get("execution_allowed") is not False
    ):
        raise AShareSearchWorkerError("forward freeze contract is invalid")

    raw_candidates = document.get("candidate_specs")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise AShareSearchWorkerError("forward freeze has no candidates")
    candidates: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw in raw_candidates:
        if not isinstance(raw, dict) or set(raw) != _CANDIDATE_KEYS:
            raise AShareSearchWorkerError("forward candidate fields are invalid")
        candidate = {
            "name": raw.get("name"),
            "family": raw.get("family"),
            "window": raw.get("window"),
            "skip": raw.get("skip"),
            "hold_days": raw.get("hold_days"),
        }
        if (
            not isinstance(candidate["name"], str)
            or not candidate["name"]
            or candidate["name"] in names
            or not isinstance(candidate["family"], str)
            or not candidate["family"]
            or isinstance(candidate["window"], bool)
            or not isinstance(candidate["window"], int)
            or candidate["window"] <= 0
            or isinstance(candidate["skip"], bool)
            or not isinstance(candidate["skip"], int)
            or candidate["skip"] < 0
            or candidate["skip"] >= candidate["window"]
            or isinstance(candidate["hold_days"], bool)
            or not isinstance(candidate["hold_days"], int)
            or candidate["hold_days"] <= 0
        ):
            raise AShareSearchWorkerError("forward candidate values are invalid")
        candidates.append(candidate)
        names.add(candidate["name"])
    if document.get("candidate_set_hash") != stable_hash(candidates):
        raise AShareSearchWorkerError("forward candidate set hash is invalid")

    source_prefix = document.get("source_prefix")
    if (
        not isinstance(source_prefix, dict)
        or set(source_prefix) != _SOURCE_PREFIX_KEYS
        or source_prefix.get("schema_version") != 1
        or source_prefix.get("algorithm") != SOURCE_PREFIX_ALGORITHM
        or source_prefix.get("cutoff") != cutoff
        or source_prefix.get("rows") != source_rows
        or not _is_sha256(source_prefix.get("sha256"))
    ):
        raise AShareSearchWorkerError("forward source-prefix binding is invalid")
    return tuple(candidates)


def _validate_frozen_search_artifact(
    freeze: Mapping[str, Any],
    freeze_path: str | Path,
) -> Path:
    digest = str(freeze.get("search_artifact_sha256") or "")
    if not _is_sha256(digest):
        raise AShareSearchWorkerError("forward freeze search artifact digest is invalid")
    artifact = (
        Path(freeze_path).parent
        / "ashare_alpha_search_artifacts"
        / f"{digest}.json"
    )
    if not artifact.is_file() or artifact.is_symlink():
        raise AShareSearchWorkerError("frozen search artifact is missing or unsafe")
    if _sha256(artifact) != digest:
        raise AShareSearchWorkerError("frozen search artifact digest mismatch")
    report = _read_object(artifact)
    source = report.get("source")
    observation = report.get("untouched_forward_observation")
    expected_candidates = [dict(row) for row in freeze["candidate_specs"]]
    if not isinstance(source, Mapping) or not isinstance(observation, Mapping):
        raise AShareSearchWorkerError("frozen search artifact lacks source/forward evidence")
    if (
        source.get("sha256") != freeze["source_database_sha256_at_freeze"]
        or int(source.get("last_date", -1)) != int(freeze["freeze_cutoff"])
        or int(observation.get("freeze_cutoff", -1)) != int(freeze["freeze_cutoff"])
        or int(observation.get("starts_after", -1)) != int(freeze["starts_after"])
        or int(observation.get("minimum_sessions", -1))
        != int(freeze["minimum_sessions"])
        or observation.get("candidate_specs") != expected_candidates
        or observation.get("candidate_set_hash") != freeze["candidate_set_hash"]
        or observation.get("selection_locked") is not True
        or observation.get("promotion_allowed") is not False
    ):
        raise AShareSearchWorkerError(
            "frozen search artifact does not match the immutable authority"
        )
    return artifact


def _read_object(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AShareSearchWorkerError(f"unreadable JSON authority: {path}") from exc
    if not isinstance(value, dict):
        raise AShareSearchWorkerError(f"JSON authority is not an object: {path}")
    return value


def _ensure_bound_panel_cache(
    database: Path,
    panel_cache: Path,
    *,
    min_adv_cny: float,
) -> str:
    """Verify the cache, rebuilding it atomically after an admitted DB refresh."""

    source_before = _source_signature(database)
    try:
        cached = _load_panel_cache(database, panel_cache, min_adv_cny=min_adv_cny)
    except RuntimeError as exc:
        if "database hash mismatch" not in str(exc):
            raise
        cached = None
    if source_before != _source_signature(database):
        raise AShareSearchWorkerError("A-share PIT database changed during cache verification")
    if cached is not None:
        return "BOUND_CACHE_HIT"

    source_before = _source_signature(database)
    panel, metadata = _load_panel(database, min_adv_cny=min_adv_cny)
    if source_before != _source_signature(database):
        raise AShareSearchWorkerError("A-share PIT database changed during panel rebuild")
    incoming = panel_cache.with_name(
        f".{panel_cache.stem}.incoming-{os.getpid()}.npz"
    )
    try:
        _write_panel_cache(
            database,
            incoming,
            panel,
            metadata,
            min_adv_cny=min_adv_cny,
        )
        if _load_panel_cache(database, incoming, min_adv_cny=min_adv_cny) is None:
            raise AShareSearchWorkerError("rebuilt panel cache is unavailable")
        if source_before != _source_signature(database):
            raise AShareSearchWorkerError("A-share PIT database changed before cache publish")
        os.replace(incoming, panel_cache)
    finally:
        incoming.unlink(missing_ok=True)
    return "BOUND_CACHE_REBUILT"


def _freeze_candidates(
    report: Mapping[str, Any],
    *,
    database: Path,
    search_artifact_sha256: str,
) -> tuple[dict[str, Any], bool]:
    observation = report.get("untouched_forward_observation")
    source = report.get("source")
    if not isinstance(observation, Mapping) or not isinstance(source, Mapping):
        raise AShareSearchWorkerError("search report lacks its forward/source contract")
    raw_candidates = observation.get("candidate_specs")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise AShareSearchWorkerError("search report selected no forward candidates")

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_candidates:
        if not isinstance(raw, Mapping):
            raise AShareSearchWorkerError("forward candidate is malformed")
        candidate = {
            "name": str(raw.get("name") or ""),
            "family": str(raw.get("family") or ""),
            "window": int(raw.get("window", 0)),
            "skip": int(raw.get("skip", 0)),
            "hold_days": int(raw.get("hold_days", 0)),
        }
        if (
            not candidate["name"]
            or not candidate["family"]
            or candidate["window"] <= 0
            or candidate["hold_days"] <= 0
            or candidate["skip"] < 0
            or candidate["skip"] >= candidate["window"]
        ):
            raise AShareSearchWorkerError("forward candidate values are invalid")
        if candidate["name"] not in seen:
            candidates.append(candidate)
            seen.add(candidate["name"])

    freeze_cutoff = int(observation.get("freeze_cutoff", 0))
    if freeze_cutoff != int(source.get("last_date", -1)):
        raise AShareSearchWorkerError("forward cutoff is not the source terminal date")
    source_signature = _source_signature(database)
    database_sha256 = _sha256(database)
    if database_sha256 != source.get("sha256"):
        raise AShareSearchWorkerError("search report is not bound to the current database")
    if DEFAULT_FREEZE.exists():
        existing = _read_object(DEFAULT_FREEZE)
        _validate_freeze_document(existing)
        _validate_frozen_search_artifact(existing, DEFAULT_FREEZE)
        existing_cutoff = int(existing["freeze_cutoff"])
        existing_prefix = _source_prefix_bindings(database, [existing_cutoff])[
            existing_cutoff
        ]
        if source_signature != _source_signature(database):
            raise AShareSearchWorkerError(
                "A-share PIT database changed while checking the forward freeze"
            )
        if existing_prefix != existing["source_prefix"]:
            raise AShareSearchWorkerError(
                "current database revised the immutable forward prefix"
            )
        # A refreshed search may produce a new diagnostic champion, but it may
        # never silently move an already-running forward clock.
        return existing, False

    source_prefix = _source_prefix_bindings(database, [freeze_cutoff])[freeze_cutoff]
    if source_signature != _source_signature(database):
        raise AShareSearchWorkerError(
            "A-share PIT database changed while creating the forward freeze"
        )
    source_rows = int(source.get("rows", 0))
    if source_prefix["rows"] != source_rows:
        raise AShareSearchWorkerError("search source rows do not match the frozen prefix")
    document = {
        "schema_version": 1,
        "artifact_kind": "ASHARE_UNTOUCHED_FORWARD_FREEZE_V1",
        "evidence_status": TECHNICAL_STATUS,
        "freeze_cutoff": freeze_cutoff,
        "starts_after": freeze_cutoff,
        "minimum_sessions": int(observation.get("minimum_sessions", 60)),
        "candidate_specs": candidates,
        "candidate_set_hash": stable_hash(candidates),
        "source_database_sha256_at_freeze": database_sha256,
        "source_rows_at_freeze": source_rows,
        "source_prefix": source_prefix,
        "selection_locked": True,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "execution_allowed": False,
        "search_artifact_sha256": search_artifact_sha256,
    }
    _validate_freeze_document(document)

    return document, True


def _forward_observation_projection(freeze: Mapping[str, Any]) -> dict[str, Any]:
    """Expose the effective immutable authority, not the latest rolling winner."""

    return {
        "status": "ARMED_AWAITING_NEW_SESSIONS",
        "freeze_cutoff": int(freeze["freeze_cutoff"]),
        "starts_after": int(freeze["starts_after"]),
        "minimum_sessions": int(freeze["minimum_sessions"]),
        "candidate_specs": [dict(row) for row in freeze["candidate_specs"]],
        "candidate_set_hash": str(freeze["candidate_set_hash"]),
        "selection_locked": True,
        "promotion_allowed": False,
        "refresh_dependency": "NEW_POST_CUTOFF_DAILY_BARS_WITH_IMMUTABLE_APPEND_AUDIT",
    }


def _publish_immutable_search_artifact(
    artifact_root: Path,
    report: Mapping[str, Any],
    expected_sha256: str,
) -> Path:
    artifact_dir = artifact_root / "ashare_alpha_search_artifacts"
    artifact_path = artifact_dir / f"{expected_sha256}.json"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _fsync_directory(artifact_root)
    if artifact_path.exists():
        if _sha256(artifact_path) != expected_sha256:
            raise AShareSearchWorkerError(
                "content-addressed search artifact is corrupt"
            )
        return artifact_path
    _durable_atomic_write_json(artifact_path, report)
    if _sha256(artifact_path) != expected_sha256:
        raise AShareSearchWorkerError("immutable search artifact hash drifted")
    return artifact_path


def _build_attestation(
    report: Mapping[str, Any],
    freeze: Mapping[str, Any],
    *,
    database_sha256: str,
    panel_cache: Path,
    output: Path,
    freeze_path: Path,
) -> dict[str, Any]:
    release_commit = os.environ.get("QUANTOS_RELEASE_COMMIT", "").strip().lower()
    complete = bool(
        len(release_commit) == 40
        and all(character in "0123456789abcdef" for character in release_commit)
        and report.get("evidence_status") == TECHNICAL_STATUS
        and report.get("promotion_allowed") is False
        and report.get("paper_persistence_allowed") is False
        and freeze.get("selection_locked") is True
        and freeze.get("promotion_allowed") is False
    )
    return {
        "schema_version": 1,
        "module": "ashare_alpha_search_worker",
        "release_commit": release_commit,
        "complete": complete,
        "input_hashes": {
            "ashare_pit_database": database_sha256,
            "ashare_bound_panel": _sha256(panel_cache),
        },
        "output_hashes": {
            "ashare_search_report": _sha256(output),
            "ashare_forward_freeze": _sha256(freeze_path),
        },
        "safety": dict(_SAFETY),
    }


def release_run_attestation() -> dict[str, Any] | None:
    return dict(_LAST_RELEASE_RUN_ATTESTATION) if _LAST_RELEASE_RUN_ATTESTATION else None


def _busy_report() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_kind": "ASHARE_RUNTIME_SINGLE_FLIGHT_V1",
        "evidence_status": TECHNICAL_STATUS,
        "status": "SAFE_BLOCKED_RUNTIME_BUSY",
        "module": "ashare_alpha_search_worker",
        "retry_allowed": True,
        "state_mutated": False,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "execution_allowed": False,
        "safety": {**_SAFETY, "legacy_v1_used": False},
    }


def _busy_attestation(report: Mapping[str, Any]) -> dict[str, Any]:
    release_commit = os.environ.get("QUANTOS_RELEASE_COMMIT", "").strip().lower()
    return {
        "schema_version": 1,
        "module": "ashare_alpha_search_worker",
        "release_commit": release_commit,
        "complete": bool(
            len(release_commit) == 40
            and all(character in "0123456789abcdef" for character in release_commit)
        ),
        "input_hashes": {
            "single_flight_contract": stable_hash(
                {"lock": "data/cache/ashare_alpha_runtime.lock", "blocking": False}
            )
        },
        "output_hashes": {"safe_blocked_decision": stable_hash(dict(report))},
        "safety": dict(_SAFETY),
    }


def _run_cycle_once(
    *,
    database: str | Path = DEFAULT_DATABASE,
    panel_cache: str | Path = DEFAULT_PANEL_CACHE,
    output: str | Path = DEFAULT_OUTPUT,
    min_adv_cny: float = MIN_ADV_CNY,
    source_observed_at_ms: int | None = None,
) -> dict[str, Any]:
    global _LAST_RELEASE_RUN_ATTESTATION

    database_path = Path(database)
    panel_path = Path(panel_cache)
    output_path = Path(output)
    if not database_path.is_file():
        raise AShareSearchWorkerError(f"A-share PIT database is missing: {database_path}")
    finality = _source_session_finality(
        _database_latest_trade_date(database_path),
        observed_at_ms=source_observed_at_ms,
    )
    if not finality["is_final"]:
        blocked = _source_not_final_report("ashare_alpha_search_worker", finality)
        _LAST_RELEASE_RUN_ATTESTATION = _source_not_final_attestation(
            "ashare_alpha_search_worker", blocked
        )
        return blocked
    cache_status = _ensure_bound_panel_cache(
        database_path,
        panel_path,
        min_adv_cny=min_adv_cny,
    )
    report = run_search(
        database_path,
        basket_size=20,
        selection_cost_bps=30.0,
        min_adv_cny=min_adv_cny,
        panel_cache=panel_path,
    )
    report_source = report.get("source")
    if not isinstance(report_source, Mapping):
        raise AShareSearchWorkerError("search report lacks its source contract")
    report_latest_date = report_source.get("last_date")
    if isinstance(report_latest_date, bool) or not isinstance(report_latest_date, int):
        raise AShareSearchWorkerError("search report terminal date is invalid")
    # Recheck the report's actual source after the expensive panel/search work.
    # This closes an atomic data-generation cutover between the initial read and
    # publication without ever creating the immutable freeze from an intraday bar.
    finality = _source_session_finality(
        report_latest_date,
        observed_at_ms=source_observed_at_ms,
    )
    if not finality["is_final"]:
        blocked = _source_not_final_report("ashare_alpha_search_worker", finality)
        _LAST_RELEASE_RUN_ATTESTATION = _source_not_final_attestation(
            "ashare_alpha_search_worker", blocked
        )
        return blocked
    freeze_expected = not DEFAULT_FREEZE.exists()
    report["scheduled_worker"] = {
        "module": "ashare_alpha_search_worker",
        "cache_status": cache_status,
        "fixed_state_paths": True,
        "legacy_v1_used": False,
        "forward_freeze_created": freeze_expected,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # First resolve the effective immutable authority.  A later rolling search
    # may discover another diagnostic winner, but the public forward section
    # must continue to show the candidates/cutoff that are actually frozen.
    freeze, created = _freeze_candidates(
        report,
        database=database_path,
        search_artifact_sha256="0" * 64,
    )
    if created is not freeze_expected:
        raise AShareSearchWorkerError("forward freeze creation state changed unexpectedly")
    report["untouched_forward_observation"] = _forward_observation_projection(freeze)
    search_artifact_sha256 = _published_json_digest(report)
    if created:
        freeze["search_artifact_sha256"] = search_artifact_sha256
        _validate_freeze_document(freeze)

    # Publish the exact content-addressed artifact before the freeze.  If power
    # is lost before the freeze write, the orphan is harmless.  If the canonical
    # mirror write fails afterwards, the freeze still references durable bytes
    # and the next run can repair the disposable mirror.
    _publish_immutable_search_artifact(
        DEFAULT_FREEZE.parent,
        report,
        search_artifact_sha256,
    )
    if created:
        _validate_frozen_search_artifact(freeze, DEFAULT_FREEZE)
        DEFAULT_FREEZE.parent.mkdir(parents=True, exist_ok=True)
        _durable_atomic_write_json(DEFAULT_FREEZE, freeze)
    _durable_atomic_write_json(output_path, report)
    if _sha256(output_path) != search_artifact_sha256:
        raise AShareSearchWorkerError("published search artifact hash drifted")
    database_sha256 = str((report.get("source") or {}).get("sha256") or "")
    if len(database_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in database_sha256
    ):
        raise AShareSearchWorkerError("search report source digest is invalid")
    _LAST_RELEASE_RUN_ATTESTATION = _build_attestation(
        report,
        freeze,
        database_sha256=database_sha256,
        panel_cache=panel_path,
        output=output_path,
        freeze_path=DEFAULT_FREEZE,
    )
    return report


def run_cycle(
    *,
    database: str | Path = DEFAULT_DATABASE,
    panel_cache: str | Path = DEFAULT_PANEL_CACHE,
    output: str | Path = DEFAULT_OUTPUT,
    min_adv_cny: float = MIN_ADV_CNY,
    lock_path: str | Path = DEFAULT_RUNTIME_LOCK,
    source_observed_at_ms: int | None = None,
) -> dict[str, Any]:
    global _LAST_RELEASE_RUN_ATTESTATION

    with acquire_runtime_lock(lock_path) as acquired:
        if not acquired:
            report = _busy_report()
            _LAST_RELEASE_RUN_ATTESTATION = _busy_attestation(report)
            return report
        return _run_cycle_once(
            database=database,
            panel_cache=panel_cache,
            output=output,
            min_adv_cny=min_adv_cny,
            source_observed_at_ms=source_observed_at_ms,
        )


def main() -> int:
    try:
        report = run_cycle()
    except Exception as exc:
        print(f"A-share technical search failed: {type(exc).__name__}: {exc}")
        return SCHEDULE_EXIT_FAILURE
    if str(report.get("status", "")).startswith("SAFE_BLOCKED_"):
        reason = str(report["status"]).removeprefix("SAFE_BLOCKED_")
        print(f"ASHARE_TECHNICAL_SEARCH_SAFE_BLOCKED reason={reason}")
        return SCHEDULE_EXIT_SAFE_BLOCKED
    print(
        "ASHARE_TECHNICAL_SEARCH_PASS "
        f"candidate={report['champion_selected_without_confirmation']['name']} "
        f"trials={report['trial_accounting']['candidate_specs']} "
        f"usefulness={report['candidate_usefulness']} "
        f"status={report['evidence_status']}"
    )
    return (
        SCHEDULE_EXIT_SUCCESS
        if report.get("evidence_status") == TECHNICAL_STATUS
        else SCHEDULE_EXIT_SAFE_BLOCKED
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_DATABASE",
    "DEFAULT_FREEZE",
    "DEFAULT_OUTPUT",
    "DEFAULT_PANEL_CACHE",
    "release_run_attestation",
    "run_cycle",
]
