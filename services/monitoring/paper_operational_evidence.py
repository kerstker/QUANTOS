"""Append-only evidence and policy for unattended realtime paper operation.

This module is deliberately local and paper-only.  It records observations and
isolated drill results; it cannot authorize paper admission, broker access or
live orders.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


class PaperOperationalEvidenceError(RuntimeError):
    """Operational evidence is missing, corrupt, or conflicting."""


@dataclass(frozen=True)
class PaperOperationalPolicy:
    policy_version: str
    cadence_ms: int
    max_gap_ms: int
    max_latest_age_ms: int
    minimum_success_ratio: float
    smoke_minimum_span_ms: int
    smoke_minimum_successes: int
    production_minimum_span_ms: int
    production_minimum_successes: int
    restart_recovery_max_ms: int
    required_drills: tuple[str, ...]


PRODUCTION_PAPER_POLICY_V1 = PaperOperationalPolicy(
    policy_version="paper_operational_evidence.v1",
    cadence_ms=5 * 60_000,
    max_gap_ms=12 * 60_000,
    max_latest_age_ms=12 * 60_000,
    minimum_success_ratio=0.99,
    smoke_minimum_span_ms=30 * 60_000,
    smoke_minimum_successes=6,
    production_minimum_span_ms=24 * 60 * 60_000,
    production_minimum_successes=274,
    restart_recovery_max_ms=15 * 60_000,
    required_drills=(
        "EXACT_RETRY_IDEMPOTENT",
        "CONFLICTING_DUPLICATE_REJECTED",
        "HASH_CHAIN_TAMPER_DETECTED",
        "STALE_CYCLE_REJECTED",
        "UNSAFE_BROKER_BOUNDARY_REJECTED",
        "INCOMPLETE_CANDIDATE_SET_REJECTED",
    ),
)


_GENESIS = ""
_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_operational_events (
  seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type       TEXT NOT NULL,
  event_id         TEXT NOT NULL,
  occurred_at_ms   INTEGER NOT NULL,
  payload_json     TEXT NOT NULL,
  payload_sha256   TEXT NOT NULL,
  prev_hash        TEXT NOT NULL,
  row_hash         TEXT NOT NULL,
  UNIQUE(event_type, event_id)
);
CREATE TRIGGER IF NOT EXISTS paper_operational_events_no_update
BEFORE UPDATE ON paper_operational_events
BEGIN SELECT RAISE(ABORT, 'paper_operational_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_operational_events_no_delete
BEFORE DELETE ON paper_operational_events
BEGIN SELECT RAISE(ABORT, 'paper_operational_events is append-only'); END;
"""


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PaperOperationalEvidenceError("event payload is not canonical JSON") from exc


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class PaperOperationalEvidenceStore:
    """Durable hash chain for cycle observations and isolated drills."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._require_schema()

    def _require_schema(self) -> None:
        try:
            names = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        except sqlite3.DatabaseError as exc:
            raise PaperOperationalEvidenceError("operational store is unreadable") from exc
        if "paper_operational_events" not in names:
            raise PaperOperationalEvidenceError("operational store schema is missing")

    def close(self) -> None:
        self._conn.close()

    def append(
        self,
        *,
        event_type: str,
        event_id: str,
        occurred_at_ms: int,
        payload: Mapping[str, Any],
    ) -> bool:
        if not isinstance(event_type, str) or not event_type.strip():
            raise PaperOperationalEvidenceError("event type is invalid")
        if not isinstance(event_id, str) or not event_id.strip():
            raise PaperOperationalEvidenceError("event identity is invalid")
        if (
            isinstance(occurred_at_ms, bool)
            or not isinstance(occurred_at_ms, int)
            or occurred_at_ms < 0
        ):
            raise PaperOperationalEvidenceError("event timestamp is invalid")
        if not isinstance(payload, Mapping):
            raise PaperOperationalEvidenceError("event payload must be a mapping")
        stored = dict(payload)
        payload_json = _canonical(stored)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT payload_sha256,occurred_at_ms FROM paper_operational_events "
                "WHERE event_type=? AND event_id=?",
                (event_type, event_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["payload_sha256"] == payload_sha256
                    and int(existing["occurred_at_ms"]) == occurred_at_ms
                ):
                    self._conn.rollback()
                    return False
                raise PaperOperationalEvidenceError(
                    "operational event identity reused with different content"
                )
            previous = self._conn.execute(
                "SELECT row_hash FROM paper_operational_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = _GENESIS if previous is None else str(previous["row_hash"])
            row_values = {
                "event_type": event_type,
                "event_id": event_id,
                "occurred_at_ms": occurred_at_ms,
                "payload_sha256": payload_sha256,
                "prev_hash": prev_hash,
            }
            row_hash = _hash(row_values)
            self._conn.execute(
                "INSERT INTO paper_operational_events"
                "(event_type,event_id,occurred_at_ms,payload_json,payload_sha256,prev_hash,row_hash)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    event_type,
                    event_id,
                    occurred_at_ms,
                    payload_json,
                    payload_sha256,
                    prev_hash,
                    row_hash,
                ),
            )
            self._conn.commit()
            return True
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    @staticmethod
    def _payload(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PaperOperationalEvidenceError("operational event JSON is invalid") from exc
        if not isinstance(payload, dict) or _hash(payload) != row["payload_sha256"]:
            raise PaperOperationalEvidenceError("operational event payload hash mismatch")
        return payload

    def events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        if event_type is None:
            rows = self._conn.execute(
                "SELECT * FROM paper_operational_events ORDER BY seq"
            )
        else:
            rows = self._conn.execute(
                "SELECT * FROM paper_operational_events WHERE event_type=? ORDER BY seq",
                (str(event_type),),
            )
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "event_type": str(row["event_type"]),
                    "event_id": str(row["event_id"]),
                    "occurred_at_ms": int(row["occurred_at_ms"]),
                    "payload": self._payload(row),
                    "row_hash": str(row["row_hash"]),
                }
            )
        return result

    def chain_valid(self) -> bool:
        previous = _GENESIS
        try:
            for row in self._conn.execute(
                "SELECT * FROM paper_operational_events ORDER BY seq"
            ):
                self._payload(row)
                if row["prev_hash"] != previous:
                    return False
                values = {
                    "event_type": row["event_type"],
                    "event_id": row["event_id"],
                    "occurred_at_ms": int(row["occurred_at_ms"]),
                    "payload_sha256": row["payload_sha256"],
                    "prev_hash": row["prev_hash"],
                }
                if row["row_hash"] != _hash(values):
                    return False
                previous = str(row["row_hash"])
            return True
        except (PaperOperationalEvidenceError, sqlite3.DatabaseError, TypeError, ValueError):
            return False


def assess_shadow_artifact(
    report: Mapping[str, Any],
    *,
    expected_candidates: Sequence[str],
    upstream_exit_code: int,
) -> tuple[bool, tuple[str, ...]]:
    """Assess the engineering shadow artifact without widening its authority."""
    reasons: list[str] = []
    verdict = report.get("verdict")
    safety = report.get("safety")
    alphas = report.get("alphas")
    errors = report.get("errors")
    expected = tuple(str(item) for item in expected_candidates)
    nonpromotable_exit = bool(
        upstream_exit_code == 1
        and isinstance(verdict, Mapping)
        and verdict.get("backtest_reference_present_for_all_execution_candidates") is False
        and errors == {}
    )
    if upstream_exit_code != 0 and not nonpromotable_exit:
        reasons.append("UPSTREAM_OUTCOME_NOT_OPERATIONALLY_ACCEPTABLE")
    if not isinstance(verdict, Mapping) or verdict.get("paper_mark_ready") is not True:
        reasons.append("PAPER_MARK_NOT_READY")
    if not isinstance(safety, Mapping):
        reasons.append("SAFETY_BOUNDARY_MISSING")
    else:
        required_safety = {
            "paper_only": True,
            "broker_connectivity": False,
            "broker_order_submission": False,
        }
        if any(safety.get(key) is not value for key, value in required_safety.items()):
            reasons.append("PAPER_SAFETY_BOUNDARY_INVALID")
    if not isinstance(alphas, Mapping) or set(alphas) != set(expected):
        reasons.append("CANDIDATE_COVERAGE_INCOMPLETE")
    elif any(
        not isinstance(row, Mapping) or row.get("status") != "PAPER_MARKED"
        for row in alphas.values()
    ):
        reasons.append("CANDIDATE_MARK_STATUS_INVALID")
    return not reasons, tuple(reasons)


def assess_current_shadow_cycle(
    report: Mapping[str, Any],
    *,
    expected_candidates: Sequence[str],
    upstream_exit_code: int,
    artifact_refreshed: bool,
) -> tuple[bool, tuple[str, ...]]:
    """Bind semantic acceptance to a newly written current-cycle artifact."""
    accepted, reasons = assess_shadow_artifact(
        report,
        expected_candidates=expected_candidates,
        upstream_exit_code=upstream_exit_code,
    )
    if not artifact_refreshed:
        return False, ("CURRENT_ARTIFACT_NOT_REFRESHED", *reasons)
    return accepted, reasons


def _finite_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _valid_release_commit(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 40
        and all(char in "0123456789abcdef" for char in value)
    )


def evaluate_operational_window(
    events: Iterable[Mapping[str, Any]],
    *,
    now_ms: int,
    chain_valid: bool,
    policy: PaperOperationalPolicy = PRODUCTION_PAPER_POLICY_V1,
) -> dict[str, Any]:
    """Evaluate smoke and 24-hour production maturity from retained evidence."""
    cycles: list[dict[str, Any]] = []
    drills: dict[str, bool] = {}
    for event in events:
        event_type = event.get("event_type")
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if event_type == "CYCLE":
            cycles.append(dict(payload))
        elif event_type == "DRILL":
            name = payload.get("name")
            if (
                isinstance(name, str)
                and payload.get("isolated") is True
                and _valid_release_commit(payload.get("collector_release_commit"))
            ):
                drills[name] = payload.get("passed") is True
    cycles.sort(key=lambda row: int(row.get("started_at_ms", -1)))
    accepted = [row for row in cycles if row.get("accepted") is True]
    accepted_count = len(accepted)
    cycle_count = len(cycles)
    success_ratio = _finite_ratio(accepted_count, cycle_count)
    starts = [int(row["started_at_ms"]) for row in cycles if isinstance(row.get("started_at_ms"), int)]
    span_ms = max(starts) - min(starts) if len(starts) >= 2 else 0
    gaps = [right - left for left, right in zip(starts, starts[1:])]
    max_gap_ms = max(gaps) if gaps else None
    latest_age_ms = now_ms - max(starts) if starts else None
    all_drills_pass = all(drills.get(name) is True for name in policy.required_drills)

    restart_recoveries: list[dict[str, Any]] = []
    for left, right in zip(accepted, accepted[1:]):
        left_release = left.get("release_commit")
        right_release = right.get("release_commit")
        if left_release and right_release and left_release != right_release:
            recovery_ms = int(right["started_at_ms"]) - int(left["finished_at_ms"])
            restart_recoveries.append(
                {
                    "from_release": left_release,
                    "to_release": right_release,
                    "recovery_ms": recovery_ms,
                    "within_policy": 0 <= recovery_ms <= policy.restart_recovery_max_ms,
                }
            )
    restart_ready = any(item["within_policy"] for item in restart_recoveries)
    freshness_ready = latest_age_ms is not None and 0 <= latest_age_ms <= policy.max_latest_age_ms
    cadence_ready = max_gap_ms is not None and max_gap_ms <= policy.max_gap_ms
    ratio_ready = success_ratio >= policy.minimum_success_ratio
    common_ready = bool(chain_valid and freshness_ready and cadence_ready and ratio_ready and all_drills_pass)
    smoke_ready = bool(
        common_ready
        and restart_ready
        and span_ms >= policy.smoke_minimum_span_ms
        and accepted_count >= policy.smoke_minimum_successes
    )
    production_ready = bool(
        smoke_ready
        and span_ms >= policy.production_minimum_span_ms
        and accepted_count >= policy.production_minimum_successes
    )
    blockers: list[str] = []
    if not chain_valid:
        blockers.append("OPERATIONAL_EVIDENCE_CHAIN_INVALID")
    if not freshness_ready:
        blockers.append("LATEST_CYCLE_STALE_OR_MISSING")
    if not cadence_ready:
        blockers.append("CONTINUOUS_CADENCE_NOT_PROVEN")
    if not ratio_ready:
        blockers.append("CYCLE_SUCCESS_RATIO_BELOW_POLICY")
    if not all_drills_pass:
        blockers.append("FAULT_MATRIX_INCOMPLETE")
    if not restart_ready:
        blockers.append("PLANNED_RESTART_RECOVERY_NOT_PROVEN")
    if span_ms < policy.production_minimum_span_ms:
        blockers.append("PRODUCTION_WINDOW_SPAN_MATURING")
    if accepted_count < policy.production_minimum_successes:
        blockers.append("PRODUCTION_WINDOW_SUCCESS_COUNT_MATURING")
    return {
        "policy_version": policy.policy_version,
        "cycle_count": cycle_count,
        "accepted_cycle_count": accepted_count,
        "success_ratio": success_ratio,
        "history_span_ms": span_ms,
        "max_gap_ms": max_gap_ms,
        "latest_age_ms": latest_age_ms,
        "required_drills": list(policy.required_drills),
        "drills": {name: drills.get(name, False) for name in policy.required_drills},
        "planned_restart_recoveries": restart_recoveries,
        "engineering_smoke_ready": smoke_ready,
        "production_window_ready": production_ready,
        "status": "PASS" if production_ready else ("SMOKE_PASS" if smoke_ready else "MATURING"),
        "blockers": blockers,
    }


__all__ = [
    "PRODUCTION_PAPER_POLICY_V1",
    "PaperOperationalEvidenceError",
    "PaperOperationalEvidenceStore",
    "PaperOperationalPolicy",
    "assess_current_shadow_cycle",
    "assess_shadow_artifact",
    "evaluate_operational_window",
]
