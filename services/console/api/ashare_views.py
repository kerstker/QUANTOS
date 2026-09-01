"""Read-only HTTP route for the A-share micro-cap alpha research stack.

Serves the persisted research artifacts under ``data/stocks/snapshots/``
(alpha_snapshot.json + paper_track_snapshot.json + klines_snapshot.json) as one
compact, read-only console snapshot. Fail-closed: when the artifacts are absent
(CI / fresh checkout) the route returns the honest ``SYNTHETIC_DETERMINISTIC``
marker with the same top-level shape — never fabricated figures.

The basket block is champion-consistent: stats come from the train-selected
turnover-cap champion (``turnover_cap_sensitivity.train_champion_cap``) and the
equity curve is compounded from the paper-track weekly net returns, so
basket / dry_run / paper_track / variants all describe the SAME cap30 champion.
holdings_latest is the uncapped baseline basket's final-week weights (the only
persisted holdings surface) — labelled in honest_limitations.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import stat
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from fastapi import APIRouter

from quantos.contracts.hashing import stable_hash
from quantos.runtime_paths import runtime_state_root

router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SNAPSHOT_NAMES = (
    "alpha_snapshot.json",
    "paper_track_snapshot.json",
    "klines_snapshot.json",
)

REAL_PROVENANCE = "REAL_ASHARE_ALPHA_SNAPSHOT"
_INSTITUTIONAL_SEARCH_PATH = _REPO_ROOT / "evidence" / "ashare_alpha_search_tdx_20260830.json"
_STATE_CACHE = runtime_state_root(cwd=_REPO_ROOT) / "data" / "cache"
_RUNTIME_SEARCH_PATH = _STATE_CACHE / "ashare_alpha_search.json"
_FORWARD_OBSERVATION_PATH = _STATE_CACHE / "ashare_forward_observation.json"
_FORWARD_FREEZE_PATH = _STATE_CACHE / "ashare_forward_freeze.json"
_FORWARD_LEDGER_PATH = _STATE_CACHE / "ashare_forward_observation.db"
_CANONICAL_TDX_SQLITE = (
    runtime_state_root(cwd=_REPO_ROOT)
    / "data"
    / "stocks"
    / "pit"
    / "tdx-windows-20260830"
    / "ashare_tdx_pit.sqlite"
)
_CANONICAL_SHA256_CACHE: dict[str, tuple[tuple[int, int, int, int], str]] = {}
_CANONICAL_SHA256_CACHE_LOCK = threading.Lock()
_FORWARD_AUTHORITY_CACHE_LOCK = threading.Lock()
_FORWARD_AUTHORITY_CACHE: tuple[tuple[Any, ...], bool] | None = None


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    """Return the exact identity fields used to invalidate the digest cache."""

    return (value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _full_file_sha256(
    path: Path,
    expected_identity: tuple[int, int, int, int],
) -> str | None:
    """Hash one stable regular file, rejecting replacement during the read."""

    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                return None
            if _file_identity(opened) != expected_identity:
                return None
            digest = hashlib.sha256()
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
            if _file_identity(os.fstat(handle.fileno())) != expected_identity:
                return None
        if _file_identity(path.stat()) != expected_identity:
            return None
    except OSError:
        return None
    return digest.hexdigest()


def _canonical_tdx_sha256(path: Path | None = None) -> str | None:
    """Return the canonical TDX database digest with stat-identity caching.

    The database is hundreds of MiB, so ordinary console polling must not hash
    it on every request.  The first request (and only a changed inode, size,
    mtime or ctime thereafter) performs the full read.  The stability checks in
    ``_full_file_sha256`` prevent an atomic cutover from being cached under the
    identity observed before that cutover.
    """

    candidate = Path(path or _CANONICAL_TDX_SQLITE)
    cache_key = os.path.abspath(os.fspath(candidate))
    with _CANONICAL_SHA256_CACHE_LOCK:
        try:
            current_stat = candidate.stat()
        except OSError:
            _CANONICAL_SHA256_CACHE.pop(cache_key, None)
            return None
        if not stat.S_ISREG(current_stat.st_mode):
            _CANONICAL_SHA256_CACHE.pop(cache_key, None)
            return None
        identity = _file_identity(current_stat)
        cached = _CANONICAL_SHA256_CACHE.get(cache_key)
        if cached is not None and cached[0] == identity:
            return cached[1]
        digest = _full_file_sha256(candidate, identity)
        if digest is None:
            _CANONICAL_SHA256_CACHE.pop(cache_key, None)
            return None
        _CANONICAL_SHA256_CACHE[cache_key] = (identity, digest)
        return digest


def _runtime_source_binding(document: dict) -> str:
    """Classify an artifact's exact binding to the currently published DB."""

    source = document.get("source")
    claimed = source.get("sha256") if isinstance(source, dict) else None
    if not (
        isinstance(claimed, str)
        and len(claimed) == 64
        and all(character in "0123456789abcdef" for character in claimed)
    ):
        return "INVALID_CLAIM"
    canonical = _canonical_tdx_sha256()
    if canonical is None:
        return "CANONICAL_UNAVAILABLE"
    return "MATCH" if claimed == canonical else "MISMATCH"


_FORWARD_ZERO_HASH = "0" * 64
_FORWARD_PREFIX_ALGORITHM = "SHA256_SQLITE_DAILY_PRICES_PK_PREFIX_V1"
_FORWARD_CANDIDATE_FIELDS = frozenset(
    {"name", "family", "window", "skip", "hold_days"}
)
_FORWARD_FREEZE_FIELDS = frozenset(
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
_FORWARD_AUTHORITY_FIELDS = (
    "schema_version",
    "freeze_sha256",
    "candidate_set_hash",
    "freeze_cutoff",
    "source_prefix_sha256",
    "source_prefix_rows",
    "source_database_sha256_at_freeze",
    "search_artifact_sha256",
    "recorded_at_ms",
)
_FORWARD_CHECKPOINT_FIELDS = (
    "sequence",
    "through_date",
    "prefix_algorithm",
    "prefix_sha256",
    "prefix_rows",
    "source_database_sha256",
    "observation_status",
    "decision_deadline_ms",
    "recorded_at_ms",
)
_FORWARD_LEGACY_CHECKPOINT_FIELDS = (
    "sequence",
    "through_date",
    "prefix_algorithm",
    "prefix_sha256",
    "prefix_rows",
    "source_database_sha256",
    "recorded_at_ms",
)
_FORWARD_LEGACY_AUTHORITY_FIELDS = (
    "legacy_table_name",
    "legacy_schema_version",
    "legacy_row_count",
    "legacy_head_hash",
    "legacy_last_through_date",
    "legacy_last_prefix_algorithm",
    "legacy_last_prefix_sha256",
    "legacy_last_prefix_rows",
    "migration_status",
    "recorded_at_ms",
)
_FORWARD_DECISION_FIELDS = (
    "sequence",
    "candidate_id",
    "signal_date",
    "hold_days",
    "factor_spec_json",
    "selected_json",
    "source_sha256",
    "recorded_at_ms",
)
_FORWARD_OUTCOME_FIELDS = (
    "sequence",
    "candidate_id",
    "signal_date",
    "entry_date",
    "exit_date",
    "gross_return",
    "net_return",
    "turnover",
    "end_weights_json",
    "unpriced_legs",
    "source_sha256",
    "recorded_at_ms",
)
_FORWARD_TABLE_COLUMNS = {
    "ledger_authority": (
        "singleton",
        *_FORWARD_AUTHORITY_FIELDS,
        "previous_hash",
        "record_hash",
    ),
    "source_checkpoints": (
        *_FORWARD_CHECKPOINT_FIELDS,
        "previous_hash",
        "record_hash",
    ),
    "checkpoint_legacy_authority": (
        "singleton",
        *_FORWARD_LEGACY_AUTHORITY_FIELDS,
        "previous_hash",
        "record_hash",
    ),
    "decisions": (*_FORWARD_DECISION_FIELDS, "previous_hash", "record_hash"),
    "outcomes": (*_FORWARD_OUTCOME_FIELDS, "previous_hash", "record_hash"),
}


class _ForwardAuthorityError(RuntimeError):
    """The published forward report is not backed by its immutable ledger."""


def _is_sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _forward_canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _forward_record_hash(payload: Mapping[str, Any], previous_hash: str) -> str:
    encoded = (previous_hash + "\0" + _forward_canonical(dict(payload))).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _read_forward_json(path: Path, *, immutable: bool = False) -> tuple[dict, bytes]:
    if immutable and path.is_symlink():
        raise _ForwardAuthorityError("immutable forward authority is a symlink")
    try:
        encoded = path.read_bytes()
        value = json.loads(encoded.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise _ForwardAuthorityError("forward authority JSON is unreadable") from exc
    if not isinstance(value, dict):
        raise _ForwardAuthorityError("forward authority JSON is not an object")
    return value, encoded


def _validate_forward_freeze(freeze: Mapping[str, Any]) -> tuple[dict, ...]:
    if set(freeze) != _FORWARD_FREEZE_FIELDS:
        raise _ForwardAuthorityError("forward freeze fields are invalid")
    cutoff = freeze.get("freeze_cutoff")
    minimum = freeze.get("minimum_sessions")
    rows = freeze.get("source_rows_at_freeze")
    if (
        freeze.get("schema_version") != 1
        or freeze.get("artifact_kind") != "ASHARE_UNTOUCHED_FORWARD_FREEZE_V1"
        or freeze.get("evidence_status") != "TECHNICAL_VALIDATION_ONLY"
        or isinstance(cutoff, bool)
        or not isinstance(cutoff, int)
        or freeze.get("starts_after") != cutoff
        or isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or minimum <= 0
        or isinstance(rows, bool)
        or not isinstance(rows, int)
        or rows <= 0
        or not _is_sha256(freeze.get("candidate_set_hash"))
        or not _is_sha256(freeze.get("source_database_sha256_at_freeze"))
        or not _is_sha256(freeze.get("search_artifact_sha256"))
        or freeze.get("selection_locked") is not True
        or freeze.get("promotion_allowed") is not False
        or freeze.get("paper_persistence_allowed") is not False
        or freeze.get("execution_allowed") is not False
    ):
        raise _ForwardAuthorityError("forward freeze contract is invalid")
    raw_candidates = freeze.get("candidate_specs")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise _ForwardAuthorityError("forward freeze candidates are missing")
    candidates: list[dict] = []
    names: set[str] = set()
    for raw in raw_candidates:
        if not isinstance(raw, dict) or set(raw) != _FORWARD_CANDIDATE_FIELDS:
            raise _ForwardAuthorityError("forward candidate fields are invalid")
        candidate = {name: raw[name] for name in ("name", "family", "window", "skip", "hold_days")}
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
            raise _ForwardAuthorityError("forward candidate values are invalid")
        candidates.append(candidate)
        names.add(candidate["name"])
    if freeze.get("candidate_set_hash") != stable_hash(candidates):
        raise _ForwardAuthorityError("forward candidate set hash is invalid")
    prefix = freeze.get("source_prefix")
    if (
        not isinstance(prefix, dict)
        or set(prefix) != {"schema_version", "algorithm", "cutoff", "rows", "sha256"}
        or prefix.get("schema_version") != 1
        or prefix.get("algorithm") != _FORWARD_PREFIX_ALGORITHM
        or prefix.get("cutoff") != cutoff
        or prefix.get("rows") != rows
        or not _is_sha256(prefix.get("sha256"))
    ):
        raise _ForwardAuthorityError("forward freeze source prefix is invalid")
    return tuple(candidates)


def _validate_frozen_search_artifact(
    freeze: Mapping[str, Any],
    freeze_path: Path,
) -> Path:
    digest = freeze["search_artifact_sha256"]
    artifact = freeze_path.parent / "ashare_alpha_search_artifacts" / f"{digest}.json"
    search, encoded = _read_forward_json(artifact, immutable=True)
    if hashlib.sha256(encoded).hexdigest() != digest:
        raise _ForwardAuthorityError("frozen search artifact digest mismatch")
    source = search.get("source")
    observation = search.get("untouched_forward_observation")
    if (
        search.get("artifact_kind") != "ASHARE_ALPHA_SEARCH_INSTITUTIONAL_SURFACES_V2"
        or search.get("evidence_status") != "TECHNICAL_VALIDATION_ONLY"
        or search.get("promotion_allowed") is not False
        or search.get("paper_persistence_allowed") is not False
        or not isinstance(source, dict)
        or not isinstance(observation, dict)
        or source.get("sha256") != freeze["source_database_sha256_at_freeze"]
        or source.get("last_date") != freeze["freeze_cutoff"]
        or observation.get("freeze_cutoff") != freeze["freeze_cutoff"]
        or observation.get("starts_after") != freeze["starts_after"]
        or observation.get("minimum_sessions") != freeze["minimum_sessions"]
        or observation.get("candidate_specs") != freeze["candidate_specs"]
        or observation.get("candidate_set_hash") != freeze["candidate_set_hash"]
        or observation.get("selection_locked") is not True
        or observation.get("promotion_allowed") is not False
    ):
        raise _ForwardAuthorityError("frozen search artifact does not match freeze")
    return artifact


def _forward_table_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))


def _forward_verify_chain(
    connection: sqlite3.Connection,
    table: str,
    fields: Sequence[str],
) -> tuple[int, str, list[sqlite3.Row]]:
    previous = _FORWARD_ZERO_HASH
    rows = connection.execute(f"SELECT * FROM {table} ORDER BY sequence").fetchall()
    for sequence, row in enumerate(rows, start=1):
        if int(row["sequence"]) != sequence or row["previous_hash"] != previous:
            raise _ForwardAuthorityError(f"{table} chain ordering is invalid")
        payload = {name: row[name] for name in fields}
        expected = _forward_record_hash(payload, previous)
        if row["record_hash"] != expected:
            raise _ForwardAuthorityError(f"{table} chain hash is invalid")
        previous = expected
    return len(rows), previous, rows


def _expected_forward_authority(freeze: Mapping[str, Any], freeze_sha256: str, recorded_at_ms: int) -> dict:
    prefix = freeze["source_prefix"]
    return {
        "schema_version": 1,
        "freeze_sha256": freeze_sha256,
        "candidate_set_hash": freeze["candidate_set_hash"],
        "freeze_cutoff": freeze["freeze_cutoff"],
        "source_prefix_sha256": prefix["sha256"],
        "source_prefix_rows": prefix["rows"],
        "source_database_sha256_at_freeze": freeze["source_database_sha256_at_freeze"],
        "search_artifact_sha256": freeze["search_artifact_sha256"],
        "recorded_at_ms": recorded_at_ms,
    }


def _validate_legacy_forward_authority(
    connection: sqlite3.Connection,
    report_ledger: Mapping[str, Any],
) -> None:
    legacy_columns = _forward_table_columns(connection, "source_checkpoints_legacy_v1")
    authorities = connection.execute(
        "SELECT * FROM checkpoint_legacy_authority ORDER BY singleton"
    ).fetchall()
    report_migration = report_ledger.get("legacy_checkpoint_migration")
    if not legacy_columns:
        if authorities or report_migration is not None:
            raise _ForwardAuthorityError("orphaned legacy checkpoint authority")
        return
    expected_columns = (*_FORWARD_LEGACY_CHECKPOINT_FIELDS, "previous_hash", "record_hash")
    if legacy_columns != expected_columns:
        raise _ForwardAuthorityError("legacy checkpoint schema is invalid")
    count, head, rows = _forward_verify_chain(
        connection,
        "source_checkpoints_legacy_v1",
        _FORWARD_LEGACY_CHECKPOINT_FIELDS,
    )
    if count == 0:
        if authorities or report_migration is not None:
            raise _ForwardAuthorityError("empty legacy checkpoint authority is invalid")
        return
    if len(authorities) != 1 or int(authorities[0]["singleton"]) != 1:
        raise _ForwardAuthorityError("legacy checkpoint authority count is invalid")
    authority = authorities[0]
    last = rows[-1]
    payload = {
        "legacy_table_name": "source_checkpoints_legacy_v1",
        "legacy_schema_version": 1,
        "legacy_row_count": count,
        "legacy_head_hash": head,
        "legacy_last_through_date": int(last["through_date"]),
        "legacy_last_prefix_algorithm": str(last["prefix_algorithm"]),
        "legacy_last_prefix_sha256": str(last["prefix_sha256"]),
        "legacy_last_prefix_rows": int(last["prefix_rows"]),
        "migration_status": "LEGACY_UNATTESTED_DECISION_TIME_RESTARTED",
        "recorded_at_ms": int(authority["recorded_at_ms"]),
    }
    if (
        {name: authority[name] for name in _FORWARD_LEGACY_AUTHORITY_FIELDS} != payload
        or authority["previous_hash"] != _FORWARD_ZERO_HASH
        or authority["record_hash"] != _forward_record_hash(payload, _FORWARD_ZERO_HASH)
        or report_migration
        != {
            "status": payload["migration_status"],
            "row_count": count,
            "head_hash": head,
            "last_through_date": payload["legacy_last_through_date"],
        }
    ):
        raise _ForwardAuthorityError("legacy checkpoint authority is invalid")


def _forward_source_dates(cutoff: int, latest: int) -> list[int]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{_CANONICAL_TDX_SQLITE.resolve()}?mode=ro",
            uri=True,
        )
        connection.execute("PRAGMA query_only=ON")
        terminal = connection.execute(
            "SELECT MAX(trade_date) FROM daily_prices"
        ).fetchone()
        dates = [
            int(row[0])
            for row in connection.execute(
                "SELECT DISTINCT trade_date FROM daily_prices "
                "WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
                (cutoff, latest),
            )
        ]
    except sqlite3.DatabaseError as exc:
        raise _ForwardAuthorityError("canonical forward source dates are unavailable") from exc
    finally:
        if connection is not None:
            connection.close()
    if (
        terminal is None
        or terminal[0] is None
        or int(terminal[0]) != latest
        or not dates
        or dates[0] != cutoff
        or dates[-1] != latest
    ):
        raise _ForwardAuthorityError("forward report is not current with canonical dates")
    return dates


def _forward_metric(returns: Sequence[float], periods_per_year: float) -> dict:
    """Recompute the exact worker metric without trusting its JSON projection."""

    import numpy as np
    from scipy import stats

    clean = np.asarray([value for value in returns if math.isfinite(value)], dtype=float)
    if clean.size == 0:
        return {
            "n_periods": 0,
            "total_return": None,
            "annualized_return": None,
            "annualized_sharpe": None,
            "max_drawdown": None,
            "mean_return": None,
            "t_stat": None,
            "one_sided_p": None,
        }
    equity = np.cumprod(1.0 + clean)
    total = float(equity[-1] - 1.0)
    annualized = (
        float((1.0 + total) ** (periods_per_year / clean.size) - 1.0)
        if total > -1.0
        else -1.0
    )
    sample_std = float(np.std(clean, ddof=1)) if clean.size >= 2 else 0.0
    sharpe = (
        float(np.mean(clean) / sample_std * math.sqrt(periods_per_year))
        if sample_std > 0
        else None
    )
    curve = np.concatenate(([1.0], equity))
    drawdown = float(np.max(1.0 - curve / np.maximum.accumulate(curve)))
    t_stat = (
        float(np.mean(clean) / (sample_std / math.sqrt(clean.size)))
        if sample_std > 0
        else None
    )
    return {
        "n_periods": int(clean.size),
        "total_return": total,
        "annualized_return": annualized,
        "annualized_sharpe": sharpe,
        "max_drawdown": drawdown,
        "mean_return": float(np.mean(clean)),
        "t_stat": t_stat,
        "one_sided_p": float(stats.t.sf(t_stat, clean.size - 1)) if t_stat is not None else None,
    }


def _validate_forward_report_alignment(
    document: Mapping[str, Any],
    freeze: Mapping[str, Any],
    freeze_sha256: str,
    authority: sqlite3.Row,
    checkpoints: Sequence[sqlite3.Row],
    checkpoint_head: str,
    decisions: Sequence[sqlite3.Row],
    decision_head: str,
    outcomes: Sequence[sqlite3.Row],
    outcome_head: str,
    pending_count: int,
    source_dates: Sequence[int],
) -> None:
    ledger = document.get("ledger")
    cycle = document.get("cycle")
    if not isinstance(ledger, dict) or not isinstance(cycle, dict):
        raise _ForwardAuthorityError("forward report lacks ledger accounting")
    expected_ledger = {
        "path": str(_FORWARD_LEDGER_PATH),
        "append_only": True,
        "chain_valid": True,
        "freeze_sha256": freeze_sha256,
        "authority_hash": authority["record_hash"],
        "source_checkpoint_count": len(checkpoints),
        "source_checkpoint_head": checkpoint_head,
        "checkpoint_schema_version": 2,
        "decision_head": decision_head,
        "outcome_head": outcome_head,
    }
    if any(ledger.get(name) != value for name, value in expected_ledger.items()):
        raise _ForwardAuthorityError("forward report ledger heads do not match")
    if (
        cycle.get("decision_count") != len(decisions)
        or cycle.get("outcome_count") != len(outcomes)
        or cycle.get("pending_count") != pending_count
        or document.get("freeze_cutoff") != freeze["freeze_cutoff"]
        or document.get("minimum_sessions") != freeze["minimum_sessions"]
    ):
        raise _ForwardAuthorityError("forward report counts do not match ledger")
    cutoff = int(freeze["freeze_cutoff"])
    post_freeze_dates = list(source_dates[1:])
    checkpoints_by_date = {int(row["through_date"]): row for row in checkpoints}
    allowed_statuses = {
        "FREEZE_ANCHOR",
        "TIMELY_PRE_NEXT_OPEN",
        "MISSED_NEXT_OPEN_DEADLINE",
        "SOURCE_NOT_FINAL_AT_OBSERVATION",
    }
    if any(
        int(row["through_date"]) not in source_dates
        or row["observation_status"] not in allowed_statuses
        or (
            row["observation_status"] == "TIMELY_PRE_NEXT_OPEN"
            and (
                int(row["decision_deadline_ms"]) <= 0
                or int(row["recorded_at_ms"]) >= int(row["decision_deadline_ms"])
            )
        )
        or (
            row["observation_status"] == "MISSED_NEXT_OPEN_DEADLINE"
            and int(row["recorded_at_ms"]) < int(row["decision_deadline_ms"])
        )
        for row in checkpoints
    ):
        raise _ForwardAuthorityError("forward checkpoints are not canonical source sessions")
    timely_dates = [
        date
        for date in post_freeze_dates
        if date in checkpoints_by_date
        and checkpoints_by_date[date]["observation_status"] == "TIMELY_PRE_NEXT_OPEN"
    ]
    observed_sessions = len(timely_dates)
    calendar_sessions = len(post_freeze_dates)
    missed_sessions = calendar_sessions - observed_sessions
    checkpoint_gaps = [
        {
            "session_date": int(row["through_date"]),
            "status": str(row["observation_status"]),
            "decision_deadline_ms": int(row["decision_deadline_ms"]),
            "recorded_at_ms": int(row["recorded_at_ms"]),
        }
        for row in checkpoints
        if int(row["through_date"]) > cutoff
        and row["observation_status"] != "TIMELY_PRE_NEXT_OPEN"
    ]
    missing_gaps = [
        {
            "session_date": date,
            "status": "NO_PRE_OUTCOME_CHECKPOINT",
            "decision_deadline_ms": None,
            "recorded_at_ms": None,
        }
        for date in post_freeze_dates
        if date not in checkpoints_by_date
    ]
    if (
        document.get("latest_source_date") != source_dates[-1]
        or document.get("sessions_observed") != observed_sessions
        or document.get("calendar_sessions_since_freeze") != calendar_sessions
        or document.get("missed_observation_sessions") != missed_sessions
        or document.get("observation_gaps_detected") is not (missed_sessions > 0)
        or document.get("observation_gaps") != checkpoint_gaps + missing_gaps
    ):
        raise _ForwardAuthorityError("forward observation-session accounting is invalid")
    candidates = document.get("candidates")
    expected_specs = {row["name"]: row for row in freeze["candidate_specs"]}
    if not isinstance(candidates, dict) or set(candidates) != set(expected_specs):
        raise _ForwardAuthorityError("forward candidate report does not match freeze")
    for name, spec in expected_specs.items():
        candidate = candidates.get(name)
        if not isinstance(candidate, dict) or candidate.get("candidate") != spec:
            raise _ForwardAuthorityError("forward candidate identity is invalid")
        periods = [
            {
                "signal_date": int(row["signal_date"]),
                "entry_date": int(row["entry_date"]),
                "exit_date": int(row["exit_date"]),
                "net_return": float(row["net_return"]),
                "turnover": float(row["turnover"]),
                "unpriced_legs": int(row["unpriced_legs"]),
            }
            for row in outcomes
            if row["candidate_id"] == name
        ]
        returns = [float(row["net_return"]) for row in outcomes if row["candidate_id"] == name]
        expected_candidate = {
            "candidate": spec,
            "realized": _forward_metric(returns, 252.0 / int(spec["hold_days"])),
            "average_turnover": (
                sum(float(row["turnover"]) for row in outcomes if row["candidate_id"] == name)
                / len(returns)
                if returns
                else None
            ),
            "unpriced_legs": sum(
                int(row["unpriced_legs"]) for row in outcomes if row["candidate_id"] == name
            ),
            "periods": periods,
        }
        if candidate != expected_candidate:
            raise _ForwardAuthorityError("forward candidate metrics do not match ledger")

    decisions_by_key = {
        (str(row["candidate_id"]), int(row["signal_date"])) for row in decisions
    }
    due_expected = 0
    due_recorded = 0
    due_keys: set[tuple[str, int]] = set()
    required_gaps: list[dict[str, Any]] = []
    for spec in freeze["candidate_specs"]:
        for ordinal, date in enumerate(post_freeze_dates):
            if ordinal % int(spec["hold_days"]) != 0:
                continue
            due_expected += 1
            due_keys.add((str(spec["name"]), date))
            checkpoint = checkpoints_by_date.get(date)
            status = (
                str(checkpoint["observation_status"])
                if checkpoint is not None
                else "NO_PRE_OUTCOME_CHECKPOINT"
            )
            recorded = (str(spec["name"]), date) in decisions_by_key
            if status == "TIMELY_PRE_NEXT_OPEN" and recorded:
                due_recorded += 1
            else:
                required_gaps.append(
                    {"candidate_id": spec["name"], "session_date": date, "status": status}
                )
    due_complete = due_recorded == due_expected and not required_gaps
    research = document.get("research_contract")
    expected_minimum_met = (
        observed_sessions >= int(freeze["minimum_sessions"])
        and due_complete
        and missed_sessions == 0
    )
    if (
        not isinstance(research, dict)
        or not decisions_by_key <= due_keys
        or research.get("due_decisions_expected") != due_expected
        or research.get("due_decisions_recorded") != due_recorded
        or research.get("due_decision_coverage_complete") is not due_complete
        or research.get("required_decision_gaps") != required_gaps
        or document.get("minimum_sessions_met") is not expected_minimum_met
    ):
        raise _ForwardAuthorityError("forward due-decision or minimum-session gate is invalid")


def _validate_forward_ledger(
    document: Mapping[str, Any],
    freeze: Mapping[str, Any],
    freeze_sha256: str,
) -> None:
    if _FORWARD_LEDGER_PATH.is_symlink():
        raise _ForwardAuthorityError("forward ledger is a symlink")
    connection = sqlite3.connect(
        f"file:{_FORWARD_LEDGER_PATH.resolve()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        if [tuple(row) for row in connection.execute("PRAGMA quick_check")] != [("ok",)]:
            raise _ForwardAuthorityError("forward ledger quick_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise _ForwardAuthorityError("forward ledger foreign keys are invalid")
        for table, columns in _FORWARD_TABLE_COLUMNS.items():
            if _forward_table_columns(connection, table) != columns:
                raise _ForwardAuthorityError(f"forward ledger {table} schema is invalid")
        trigger_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        required_triggers = {
            f"{table}_{operation}"
            for table in (
                "decisions",
                "outcomes",
                "ledger_authority",
                "source_checkpoints",
                "checkpoint_legacy_authority",
            )
            for operation in ("no_update", "no_delete")
        }
        if not required_triggers <= trigger_names:
            raise _ForwardAuthorityError("forward append-only triggers are missing")
        authorities = connection.execute(
            "SELECT * FROM ledger_authority ORDER BY singleton"
        ).fetchall()
        if len(authorities) != 1 or int(authorities[0]["singleton"]) != 1:
            raise _ForwardAuthorityError("forward ledger authority count is invalid")
        authority = authorities[0]
        authority_payload = {
            name: authority[name] for name in _FORWARD_AUTHORITY_FIELDS
        }
        expected = _expected_forward_authority(
            freeze,
            freeze_sha256,
            int(authority["recorded_at_ms"]),
        )
        if (
            authority_payload != expected
            or authority["previous_hash"] != _FORWARD_ZERO_HASH
            or authority["record_hash"]
            != _forward_record_hash(authority_payload, _FORWARD_ZERO_HASH)
        ):
            raise _ForwardAuthorityError("forward ledger authority is invalid")
        checkpoint_count, checkpoint_head, checkpoints = _forward_verify_chain(
            connection,
            "source_checkpoints",
            _FORWARD_CHECKPOINT_FIELDS,
        )
        _, decision_head, decisions = _forward_verify_chain(
            connection,
            "decisions",
            _FORWARD_DECISION_FIELDS,
        )
        _, outcome_head, outcomes = _forward_verify_chain(
            connection,
            "outcomes",
            _FORWARD_OUTCOME_FIELDS,
        )
        if checkpoint_count == 0:
            raise _ForwardAuthorityError("forward ledger lacks freeze checkpoint")
        first = checkpoints[0]
        prefix = freeze["source_prefix"]
        if (
            int(first["through_date"]) != int(freeze["freeze_cutoff"])
            or first["prefix_algorithm"] != prefix["algorithm"]
            or first["prefix_sha256"] != prefix["sha256"]
            or int(first["prefix_rows"]) != int(prefix["rows"])
            or first["observation_status"] != "FREEZE_ANCHOR"
            or int(first["decision_deadline_ms"]) != 0
        ):
            raise _ForwardAuthorityError("forward freeze checkpoint is invalid")
        checkpoint_by_date = {int(row["through_date"]): row for row in checkpoints}
        if list(checkpoint_by_date) != sorted(checkpoint_by_date):
            raise _ForwardAuthorityError("forward checkpoint dates are not increasing")
        latest_source_date = document.get("latest_source_date")
        if isinstance(latest_source_date, bool) or not isinstance(latest_source_date, int):
            raise _ForwardAuthorityError("forward latest source date is invalid")
        source_dates = _forward_source_dates(
            int(freeze["freeze_cutoff"]),
            latest_source_date,
        )
        specs = {row["name"]: row for row in freeze["candidate_specs"]}
        for row in decisions:
            spec = specs.get(str(row["candidate_id"]))
            checkpoint = checkpoint_by_date.get(int(row["signal_date"]))
            if spec is None or checkpoint is None:
                raise _ForwardAuthorityError("forward decision authority is missing")
            try:
                selected = json.loads(str(row["selected_json"]))
            except (ValueError, TypeError) as exc:
                raise _ForwardAuthorityError("forward selected weights are invalid") from exc
            if (
                int(row["signal_date"]) <= int(freeze["freeze_cutoff"])
                or int(row["hold_days"]) != int(spec["hold_days"])
                or row["factor_spec_json"] != _forward_canonical(spec)
                or not isinstance(selected, dict)
                or row["selected_json"] != _forward_canonical(selected)
                or any(
                    not isinstance(name, str)
                    or isinstance(weight, bool)
                    or not isinstance(weight, (int, float))
                    or not math.isfinite(float(weight))
                    for name, weight in selected.items()
                )
                or checkpoint["observation_status"] != "TIMELY_PRE_NEXT_OPEN"
                or row["source_sha256"] != checkpoint["source_database_sha256"]
                or int(row["recorded_at_ms"]) > int(checkpoint["decision_deadline_ms"])
            ):
                raise _ForwardAuthorityError("forward decision contract is invalid")
        pending_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM decisions d LEFT JOIN outcomes o "
                "ON o.candidate_id=d.candidate_id AND o.signal_date=d.signal_date "
                "WHERE o.sequence IS NULL"
            ).fetchone()[0]
        )
        report_ledger = document.get("ledger")
        if not isinstance(report_ledger, dict):
            raise _ForwardAuthorityError("forward report ledger is invalid")
        _validate_legacy_forward_authority(connection, report_ledger)
        _validate_forward_report_alignment(
            document,
            freeze,
            freeze_sha256,
            authority,
            checkpoints,
            checkpoint_head,
            decisions,
            decision_head,
            outcomes,
            outcome_head,
            pending_count,
            source_dates,
        )
    except sqlite3.DatabaseError as exc:
        raise _ForwardAuthorityError("forward ledger cannot be verified") from exc
    finally:
        connection.close()


def _optional_forward_identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        return _file_identity(path.stat())
    except OSError:
        return None


def _forward_authority_cache_key(document: Mapping[str, Any]) -> tuple[Any, ...]:
    try:
        document_hash = hashlib.sha256(_forward_canonical(document).encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        document_hash = "INVALID_DOCUMENT"
    try:
        freeze_hint = json.loads(_FORWARD_FREEZE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        freeze_hint = None
    digest = freeze_hint.get("search_artifact_sha256") if isinstance(freeze_hint, dict) else None
    artifact = (
        _FORWARD_FREEZE_PATH.parent
        / "ashare_alpha_search_artifacts"
        / f"{digest}.json"
        if _is_sha256(digest)
        else _FORWARD_FREEZE_PATH.parent / "ashare_alpha_search_artifacts" / "INVALID"
    )
    ledger_sidecars = tuple(
        Path(f"{_FORWARD_LEDGER_PATH}{suffix}")
        for suffix in ("-journal", "-wal", "-shm")
    )
    paths = (
        _FORWARD_OBSERVATION_PATH,
        _FORWARD_FREEZE_PATH,
        _FORWARD_LEDGER_PATH,
        *ledger_sidecars,
        _CANONICAL_TDX_SQLITE,
        artifact,
    )
    return (document_hash, *((str(path), _optional_forward_identity(path)) for path in paths))


def _validate_forward_authority_uncached(document: Mapping[str, Any]) -> None:
    report, _ = _read_forward_json(_FORWARD_OBSERVATION_PATH, immutable=True)
    if report != document or _runtime_source_binding(report) != "MATCH":
        raise _ForwardAuthorityError("forward report source binding is invalid")
    freeze, freeze_bytes = _read_forward_json(_FORWARD_FREEZE_PATH, immutable=True)
    _validate_forward_freeze(freeze)
    _validate_frozen_search_artifact(freeze, _FORWARD_FREEZE_PATH)
    freeze_sha256 = hashlib.sha256(freeze_bytes).hexdigest()
    _validate_forward_ledger(report, freeze, freeze_sha256)


def _forward_authority_is_valid(document: Mapping[str, Any]) -> bool:
    """Verify and stat-cache the complete immutable forward authority chain."""

    global _FORWARD_AUTHORITY_CACHE
    with _FORWARD_AUTHORITY_CACHE_LOCK:
        before = _forward_authority_cache_key(document)
        if _FORWARD_AUTHORITY_CACHE is not None and _FORWARD_AUTHORITY_CACHE[0] == before:
            return _FORWARD_AUTHORITY_CACHE[1]
        try:
            _validate_forward_authority_uncached(document)
            valid = True
        except Exception:
            valid = False
        after = _forward_authority_cache_key(document)
        if before != after:
            return False
        _FORWARD_AUTHORITY_CACHE = (before, valid)
        return valid


def _snapshot_paths(root: Path) -> tuple[Path, Path, Path]:
    directory = root / "data" / "stocks" / "snapshots"
    return tuple(directory / name for name in _SNAPSHOT_NAMES)


def _load_bundle(root: Path) -> tuple[dict, dict, dict] | None:
    loaded = tuple(_load(str(path)) for path in _snapshot_paths(Path(root)))
    if all(value is not None for value in loaded):
        return loaded  # type: ignore[return-value]
    return None


def _ashare_snapshot_bundle() -> tuple[dict, dict, dict] | None:
    state_bundle = _load_bundle(runtime_state_root(cwd=_REPO_ROOT))
    return state_bundle if state_bundle is not None else _load_bundle(_REPO_ROOT)


def _load(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _compound(weekly: List[dict], key: str) -> List[float]:
    """Cum-product a weekly return series into an equity curve (starts at 1.0)."""
    equity = [1.0]
    for row in weekly:
        try:
            equity.append(equity[-1] * (1.0 + float(row[key])))
        except (KeyError, TypeError, ValueError):
            return []
    return [round(v, 6) for v in equity]


def _synthetic_payload(reason: str) -> Dict:
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "provenance": "SYNTHETIC_DETERMINISTIC",
        "data_provenance": "SYNTHETIC_DETERMINISTIC",
        "snapshot_present": False,
        "generated_at": "UNKNOWN",
        "data_freshness": {"stocks": 0, "bars": 0, "last_date": 0,
                           "sources": {"wind": 0, "ifind": 0}},
        "universe": {"file_count": 0, "used": 0},
        "factors": [],
        "combo": {"champion": "", "weights": {}, "h1_test_ic": 0.0, "h1_test_t": 0.0,
                  "h5_test_ic": 0.0, "h5_test_t": 0.0, "hit_rate": 0.0},
        "basket": {"n": 0, "aum_cny": 0.0, "cost_bps": 0.0,
                   "test": {"total_return": 0.0, "sharpe": 0.0, "max_dd": 0.0, "avg_turnover": 0.0},
                   "bench_test": {"total_return": 0.0, "sharpe": 0.0, "max_dd": 0.0},
                   "equity_test": [], "bench_equity_test": []},
        "variants": [],
        "dry_run": {"run_id": "", "total_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
                    "n_approved": 0, "n_rejected": 0, "residual": 0.0},
        "paper_track": {"alpha_id": "", "n_weeks": 0, "cum_return": 0.0, "sharpe": 0.0,
                        "weekly_ic_mean": 0.0, "weekly_ic_t": 0.0, "kind": "BACKFILL"},
        "cost_grid": [],
        "turnover_caps": [],
        "holdings_latest": [],
        "honest_limitations": [reason],
    }


def _data_freshness(klines: Optional[dict], alpha: dict) -> Dict:
    klines = klines or {}
    per_stock = klines.get("per_stock") or {}
    last_date = 0
    for row in per_stock.values():
        if isinstance(row, dict):
            try:
                last_date = max(last_date, int(row.get("date_max") or 0))
            except (TypeError, ValueError):
                continue
    if not last_date:
        rng = (alpha.get("panel") or {}).get("range") or [0, 0]
        last_date = int(rng[-1]) if rng else 0
    return {
        "stocks": int(klines.get("stocks_with_data") or (alpha.get("universe") or {}).get("used") or 0),
        "bars": int(klines.get("total_rows") or 0),
        "last_date": last_date,
        "sources": {"wind": int(klines.get("wind_stocks") or 0),
                    "ifind": int(klines.get("ifind_stocks") or 0)},
    }


def _factors_block(alpha: dict) -> List[Dict]:
    kept = set((alpha.get("hygiene") or {}).get("kept") or [])
    rows = []
    for name, block in (alpha.get("factor_ic") or {}).items():
        h1 = (block or {}).get("h1") or {}
        train, test = h1.get("train") or {}, h1.get("test") or {}
        rows.append({
            "name": str(name),
            "train_ic": float(train.get("mean_ic") or 0.0),
            "train_t": float(train.get("t_stat") or 0.0),
            "test_ic": float(test.get("mean_ic") or 0.0),
            "test_t": float(test.get("t_stat") or 0.0),
            "kept": name in kept,
        })
    return rows


def _combo_block(alpha: dict) -> Dict:
    combo = alpha.get("combo") or {}
    ic = combo.get("ic") or {}
    h1t, h5t = (ic.get("h1") or {}).get("test") or {}, (ic.get("h5") or {}).get("test") or {}
    return {
        "champion": str(combo.get("champion") or ""),
        "weights": {str(k): float(v) for k, v in (combo.get("weights") or {}).items()},
        "h1_test_ic": float(h1t.get("mean_ic") or 0.0),
        "h1_test_t": float(h1t.get("t_stat") or 0.0),
        "h5_test_ic": float(h5t.get("mean_ic") or 0.0),
        "h5_test_t": float(h5t.get("t_stat") or 0.0),
        "hit_rate": float(h1t.get("hit_rate") or 0.0),
    }


def _variants_block(alpha: dict) -> List[Dict]:
    tv = alpha.get("turnover_variants") or {}
    champion = tv.get("champion")
    rows = []
    for name, cand in (tv.get("candidates") or {}).items():
        train = (cand or {}).get("train_15bps") or {}
        test15 = ((cand or {}).get("test_cost_grid") or {}).get("15") or {}
        rows.append({
            "name": str(name),
            "train_sharpe": float(train.get("ann_sharpe") or 0.0),
            "test_return": float(test15.get("total_return") or 0.0),
            "test_sharpe": float(test15.get("ann_sharpe") or 0.0),
            "test_mdd": float(test15.get("max_drawdown") or 0.0),
            "turnover": float((cand or {}).get("test_avg_turnover") or 0.0),
            "is_champion": name == champion,
        })
    return rows


def _basket_block(alpha: dict, paper: Optional[dict]) -> Dict:
    basket = alpha.get("basket") or {}
    bench = ((basket.get("test") or {}).get("benchmark")) or {}
    out = {
        "n": int(basket.get("n") or 0),
        "aum_cny": float(basket.get("aum_cny") or 0.0),
        "cost_bps": float(basket.get("cost_bps") or 0.0),
        "bench_test": {
            "total_return": float(bench.get("total_return") or 0.0),
            "sharpe": float(bench.get("ann_sharpe") or 0.0),
            "max_dd": float(bench.get("max_drawdown") or 0.0),
        },
    }
    # Champion-consistent stats: the train-selected turnover cap's test row.
    tcs = alpha.get("turnover_cap_sensitivity") or {}
    cap = tcs.get("train_champion_cap")
    cap_row = (tcs.get("grid") or {}).get(str(cap)) or {}
    test15 = cap_row.get("test_15bps") or {}
    weekly = (paper or {}).get("weekly") or []
    equity = _compound(weekly, "paper_ret") if weekly else []
    bench_equity = _compound(weekly, "bench_ret") if weekly else []
    if test15 and equity:
        out["test"] = {
            "total_return": float(test15.get("total_return") or 0.0),
            "sharpe": float(test15.get("ann_sharpe") or 0.0),
            "max_dd": float(test15.get("max_drawdown") or 0.0),
            "avg_turnover": float(cap_row.get("test_avg_turnover") or 0.0),
        }
        out["equity_test"] = equity
        out["bench_equity_test"] = bench_equity
    else:
        # fallback: the uncapped baseline basket (equity arrays are only persisted for it)
        strat = ((basket.get("test") or {}).get("strategy")) or {}
        out["test"] = {
            "total_return": float(strat.get("total_return") or 0.0),
            "sharpe": float(strat.get("ann_sharpe") or 0.0),
            "max_dd": float(strat.get("max_drawdown") or 0.0),
            "avg_turnover": float((basket.get("test") or {}).get("avg_turnover") or 0.0),
        }
        out["equity_test"] = list((basket.get("test") or {}).get("equity") or [])
        out["bench_equity_test"] = list((basket.get("test") or {}).get("bench_equity") or [])
    return out


def _ashare_alpha_payload() -> Dict:
    bundle = _ashare_snapshot_bundle()
    if bundle is None:
        return _synthetic_payload(
            "未找到完整的 data/stocks/snapshots 三件套 —— 先运行 "
            "tools/ashare_alpha_research.py(+ tools/ashare_paper_track.py)生成研究快照"
        )
    alpha, paper, klines = bundle

    dry = alpha.get("dry_run_champion_variant") or alpha.get("dry_run") or {}
    paper_summary = (paper or {}).get("summary") or {}
    paper_prov = (paper or {}).get("provenance") or {}
    weekly = (paper or {}).get("weekly") or []

    # cost grid: champion variant's own cost sweep (fall back to the baseline grid)
    tv = alpha.get("turnover_variants") or {}
    champion = tv.get("champion")
    champ_grid = (((tv.get("candidates") or {}).get(champion) or {}).get("test_cost_grid")) or {}
    grid_src = champ_grid or (alpha.get("basket") or {}).get("cost_grid_test") or {}
    cost_grid = [
        {"cost_bps": float(k), "total_return": float(v.get("total_return") or 0.0),
         "sharpe": float(v.get("ann_sharpe") or 0.0)}
        for k, v in sorted(grid_src.items(), key=lambda kv: float(kv[0]))
        if isinstance(v, dict)
    ]

    caps = []
    for k, v in ((alpha.get("turnover_cap_sensitivity") or {}).get("grid") or {}).items():
        try:
            cap_value = float(k)
        except (TypeError, ValueError):
            continue   # baseline_inf = uncapped; it appears in `variants` as "baseline"
        test15 = (v or {}).get("test_15bps") or {}
        caps.append({
            "cap": cap_value,
            "train_sharpe": float((v or {}).get("train_sharpe_15bps") or 0.0),
            "test_return": float(test15.get("total_return") or 0.0),
            "test_sharpe": float(test15.get("ann_sharpe") or 0.0),
        })
    caps.sort(key=lambda r: r["cap"])

    weights_last = ((alpha.get("basket") or {}).get("test") or {}).get("weights_last") or {}
    holdings = [
        {"code": str(code), "weight": float(w)}
        for code, w in sorted(weights_last.items(), key=lambda kv: -float(kv[1]))
    ]

    limitations = [str(x) for x in (alpha.get("honest_limitations") or [])]
    limitations.append(
        "basket 块为冠军一致口径:test 统计取 train 选出的 cap30 换手上限行,"
        "equity_test 由 paper-track 周度净收益复利而成(与回测引擎不分叉);"
        "holdings_latest 是 uncapped baseline 篮子的最后一周权重(cap30 冠军末周持仓未单独落快照)")
    limitations.append(
        "已注册进 experiment_registry(DRAFT,verdict=REQUIRES_HUMAN_REVIEW):"
        "证据未过 evaluator 门禁,非人 actor 永不晋级;初筛诊断,不驱动任何自动晋级/下单")
    if paper is None:
        limitations.append("paper_track_snapshot.json 缺失:paper_track 块为零值占位")

    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "provenance": REAL_PROVENANCE,
        "data_provenance": REAL_PROVENANCE,
        "snapshot_present": True,
        "generated_at": str((klines or {}).get("fetched_at") or "UNKNOWN"),
        "data_freshness": _data_freshness(klines, alpha),
        "universe": {
            "file_count": int((alpha.get("universe") or {}).get("file_count") or 0),
            "used": int((alpha.get("universe") or {}).get("used") or 0),
        },
        "factors": _factors_block(alpha),
        "combo": _combo_block(alpha),
        "basket": _basket_block(alpha, paper),
        "variants": _variants_block(alpha),
        "dry_run": {
            "run_id": str(dry.get("run_id") or ""),
            "total_return": float(dry.get("total_return") or 0.0),
            "sharpe": float(dry.get("sharpe") or 0.0),
            "max_drawdown": float(dry.get("max_drawdown") or 0.0),
            "n_approved": int(dry.get("n_approved") or 0),
            "n_rejected": int(dry.get("n_rejected") or 0),
            "residual": float(dry.get("residual") or 0.0),
        },
        "paper_track": {
            "alpha_id": str((paper or {}).get("alpha_id") or ""),
            "n_weeks": int(paper_summary.get("n_weeks") or 0),
            "cum_return": float(paper_summary.get("paper_cum_return") or 0.0),
            "sharpe": float(paper_summary.get("paper_sharpe_weekly_annualized") or 0.0),
            "weekly_ic_mean": float(paper_summary.get("paper_ic_mean") or 0.0),
            "weekly_ic_t": float(paper_summary.get("paper_ic_t_stat") or 0.0),
            "kind": str(paper_prov.get("source_field") or "BACKFILL"),
        },
        "cost_grid": cost_grid,
        "turnover_caps": caps,
        "holdings_latest": holdings,
        "honest_limitations": limitations,
    }


@router.get("/api/v1/console/ashare-alpha")
def ashare_alpha() -> Dict:
    return _ashare_alpha_payload()


def _surface_summary(name: str, value: dict) -> dict:
    by_cost = value.get("confirmation_cost_sensitivity") or {}
    primary = ((by_cost.get("30") or {}).get("primary_phase") or {}).get("excess") or {}
    robustness = (by_cost.get("30") or {}).get("phase_robustness") or {}
    return {
        "surface": name,
        "candidate": value.get("candidate_selected_without_confirmation"),
        "stable_positive_before_confirmation": bool(
            value.get("stable_positive_before_confirmation")
        ),
        "confirmation_excess_sharpe": primary.get("annualized_sharpe"),
        "confirmation_excess_return": primary.get("annualized_return"),
        "confirmation_median_excess_sharpe": robustness.get("median_excess_sharpe"),
        "bonferroni_adjusted_p": value.get("bonferroni_adjusted_p"),
        "candidate_usefulness": value.get("candidate_usefulness", "NOT_VALIDATED"),
    }


def _load_institutional_search() -> tuple[dict | None, str, str]:
    """Prefer the current worker artifact over release-bundled evidence.

    Once the scheduled worker has published a state artifact, silently falling
    back to an older release snapshot would make the frontend look healthy
    while displaying stale hashes.  An unreadable runtime artifact is therefore
    reported as unavailable instead of being hidden by the repository copy.
    """

    if _RUNTIME_SEARCH_PATH.exists():
        document = _load(str(_RUNTIME_SEARCH_PATH))
        valid = bool(
            isinstance(document, dict)
            and document.get("artifact_kind")
            == "ASHARE_ALPHA_SEARCH_INSTITUTIONAL_SURFACES_V2"
            and document.get("evidence_status") == "TECHNICAL_VALIDATION_ONLY"
            and document.get("promotion_allowed") is False
            and document.get("paper_persistence_allowed") is False
        )
        if not valid:
            return None, "UNAVAILABLE", "RUNTIME_ARTIFACT_INVALID"
        binding = _runtime_source_binding(document)
        if binding == "CANONICAL_UNAVAILABLE":
            return None, "UNAVAILABLE", "RUNTIME_CANONICAL_SOURCE_UNAVAILABLE"
        if binding != "MATCH":
            return None, "UNAVAILABLE", "RUNTIME_SOURCE_BINDING_INVALID"
        return document, "REAL_ASHARE_TECHNICAL_RUNTIME", "RUNTIME_STATE"
    return _load(str(_INSTITUTIONAL_SEARCH_PATH)), "REPOSITORY_EVIDENCE", "RELEASE_EVIDENCE"


def _ashare_institutional_search_payload() -> Dict:
    document, provenance, artifact_scope = _load_institutional_search()
    if document is None:
        return {
            "source": "BACKEND_COMPUTED_SNAPSHOT",
            "read_only": True,
            "api_version": "0.1.0",
            "data_provenance": provenance,
            "snapshot_present": False,
            "authoritative": False,
            "diagnostic_only": True,
            "evidence_status": "UNAVAILABLE",
            "availability_mode": "SEARCH_EVIDENCE_NOT_PUBLISHED",
            "candidate_usefulness": "UNAVAILABLE",
            "artifact_scope": artifact_scope,
            "source_summary": {},
            "surface_coverage": {},
            "surface_results": [],
            "untouched_forward_observation": {},
            "limitations": ["Run the admitted read-only A-share institutional surface search."],
        }
    source = document.get("source") or {}
    surface_results = [
        _surface_summary(str(name), value)
        for name, value in sorted((document.get("surface_confirmations") or {}).items())
        if isinstance(value, dict)
    ]
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": provenance,
        "snapshot_present": True,
        "authoritative": False,
        "diagnostic_only": True,
        "evidence_status": document.get("evidence_status", "TECHNICAL_VALIDATION_ONLY"),
        "availability_mode": "READ_ONLY_TECHNICAL_RESEARCH",
        "generated_at": document.get("generated_at"),
        "candidate_usefulness": document.get("candidate_usefulness", "NOT_VALIDATED"),
        "artifact_scope": artifact_scope,
        "source_summary": {
            "rows": source.get("rows"),
            "instruments": source.get("instruments"),
            "first_date": source.get("first_date"),
            "last_date": source.get("last_date"),
            "sqlite_quick_check": source.get("sqlite_quick_check"),
            "future_known_rows": source.get("future_known_rows"),
            "bad_bar_count": source.get("bad_bar_count"),
            "historical_universe_semantics": (
                (source.get("intake_contract") or {}).get("historical_universe_semantics")
            ),
        },
        "trial_accounting": document.get("trial_accounting") or {},
        "surface_coverage": document.get("requested_surface_coverage") or {},
        "surface_results": surface_results,
        "untouched_forward_observation": document.get("untouched_forward_observation") or {},
        "limitations": document.get("limitations") or [],
    }


@router.get("/api/v1/console/ashare-alpha-search")
def ashare_alpha_search() -> Dict:
    return _ashare_institutional_search_payload()


def _unavailable_forward_payload(
    *,
    availability_mode: str,
    status: str,
    selection_locked: bool = True,
) -> Dict:
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "UNAVAILABLE",
        "snapshot_present": False,
        "authoritative": False,
        "diagnostic_only": True,
        "evidence_status": "UNAVAILABLE",
        "availability_mode": availability_mode,
        "status": status,
        "sessions_observed": 0,
        "minimum_sessions": 60,
        "minimum_sessions_met": False,
        "selection_locked": selection_locked,
        "chain_valid": False,
        "cycle": {},
        "candidates": {},
        "observation": {},
        "safety": {
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
        },
    }


def _ashare_forward_observation_payload() -> Dict:
    document = _load(str(_FORWARD_OBSERVATION_PATH))
    if document is None:
        return _unavailable_forward_payload(
            availability_mode="FORWARD_OBSERVATION_NOT_STARTED",
            status="NO_SNAPSHOT",
        )
    valid = bool(
        document.get("artifact_kind") == "ASHARE_UNTOUCHED_FORWARD_OBSERVATION_V1"
        and document.get("evidence_status") == "TECHNICAL_VALIDATION_ONLY"
        and document.get("selection_locked") is True
        and document.get("promotion_allowed") is False
        and document.get("paper_persistence_allowed") is False
        and document.get("execution_allowed") is False
        and document.get("chain_valid") is True
    )
    if not valid:
        return _unavailable_forward_payload(
            availability_mode="FORWARD_OBSERVATION_INVALID",
            status="INVALID_ARTIFACT",
            selection_locked=False,
        )
    binding = _runtime_source_binding(document)
    if binding == "CANONICAL_UNAVAILABLE":
        return _unavailable_forward_payload(
            availability_mode="FORWARD_OBSERVATION_CANONICAL_SOURCE_UNAVAILABLE",
            status="SOURCE_UNAVAILABLE",
            selection_locked=False,
        )
    if binding != "MATCH":
        return _unavailable_forward_payload(
            availability_mode="FORWARD_OBSERVATION_SOURCE_BINDING_INVALID",
            status="INVALID_SOURCE_BINDING",
            selection_locked=False,
        )
    if not _forward_authority_is_valid(document):
        return _unavailable_forward_payload(
            availability_mode="FORWARD_OBSERVATION_AUTHORITY_INVALID",
            status="INVALID_AUTHORITY",
            selection_locked=False,
        )
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_ASHARE_FORWARD_OBSERVATION",
        "snapshot_present": True,
        "authoritative": False,
        "diagnostic_only": True,
        "evidence_status": document["evidence_status"],
        "availability_mode": "READ_ONLY_UNTOUCHED_FORWARD_OBSERVATION",
        "status": document.get("status", "UNKNOWN"),
        "sessions_observed": int(document.get("sessions_observed", 0)),
        "minimum_sessions": int(document.get("minimum_sessions", 60)),
        "minimum_sessions_met": bool(document.get("minimum_sessions_met", False)),
        "selection_locked": True,
        "chain_valid": True,
        "cycle": document.get("cycle") or {},
        "candidates": document.get("candidates") or {},
        "observation": document,
        "safety": document.get("safety") or {},
    }


@router.get("/api/v1/console/ashare-forward-observation")
def ashare_forward_observation() -> Dict:
    return _ashare_forward_observation_payload()
