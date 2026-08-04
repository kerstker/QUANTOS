"""Append-only authority for isolated realtime engineering execution-paper.

This ledger has no relationship to the official Execution-Paper authority.  It
records an admission baseline and only successful, fully reconciled simulated
executions that occur after that baseline.  Non-executed decisions are resolved
in a second append-only hash chain as either explicit latency misses or
legitimate no-action decisions; they are never silently dropped, backfilled, or
misreported as fills.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping
from typing import Any

from quantos.contracts.hashing import stable_hash


class RealtimeEngineeringPaperStoreError(RuntimeError):
    """The engineering execution-paper ledger is invalid or conflicted."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS realtime_engineering_paper_events (
  event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE NOT NULL,
  event_type TEXT NOT NULL CHECK(event_type IN ('ADMITTED','EXECUTION')),
  candidate_id TEXT NOT NULL,
  identity_time_ms INTEGER NOT NULL CHECK(identity_time_ms >= 0),
  recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms >= 0),
  payload_json TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  row_hash TEXT UNIQUE NOT NULL
);
CREATE INDEX IF NOT EXISTS realtime_engineering_paper_by_candidate
ON realtime_engineering_paper_events(candidate_id,event_seq);
CREATE TRIGGER IF NOT EXISTS realtime_engineering_paper_no_update
BEFORE UPDATE ON realtime_engineering_paper_events
BEGIN SELECT RAISE(ABORT, 'realtime_engineering_paper_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS realtime_engineering_paper_no_delete
BEFORE DELETE ON realtime_engineering_paper_events
BEGIN SELECT RAISE(ABORT, 'realtime_engineering_paper_events is append-only'); END;
"""

_MISSED_SCHEMA = """
CREATE TABLE IF NOT EXISTS realtime_engineering_paper_missed (
  event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE NOT NULL,
  candidate_id TEXT NOT NULL,
  identity_time_ms INTEGER NOT NULL CHECK(identity_time_ms >= 0),
  recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms >= identity_time_ms),
  payload_json TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  row_hash TEXT UNIQUE NOT NULL
);
CREATE INDEX IF NOT EXISTS realtime_engineering_paper_missed_by_candidate
ON realtime_engineering_paper_missed(candidate_id,event_seq);
CREATE TRIGGER IF NOT EXISTS realtime_engineering_paper_missed_no_update
BEFORE UPDATE ON realtime_engineering_paper_missed
BEGIN SELECT RAISE(ABORT, 'realtime_engineering_paper_missed is append-only'); END;
CREATE TRIGGER IF NOT EXISTS realtime_engineering_paper_missed_no_delete
BEFORE DELETE ON realtime_engineering_paper_missed
BEGIN SELECT RAISE(ABORT, 'realtime_engineering_paper_missed is append-only'); END;
"""


def _canonical(payload: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(payload),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RealtimeEngineeringPaperStoreError(
            "engineering paper payload is not canonical JSON"
        ) from exc


def _row_hash(row: Mapping[str, Any]) -> str:
    return stable_hash(dict(row))


class RealtimeEngineeringPaperStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        names = {
            str(row["name"])
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "realtime_engineering_paper_events" not in names:
            raise RealtimeEngineeringPaperStoreError(
                "engineering paper ledger schema is missing"
            )
        self._conn.executescript(_MISSED_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> "RealtimeEngineeringPaperStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def _events(
        self,
        *,
        event_type: str | None = None,
        candidate_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[str] = []
        if event_type is not None:
            clauses.append("event_type=?")
            args.append(event_type)
        if candidate_id is not None:
            clauses.append("candidate_id=?")
            args.append(candidate_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            "SELECT event_seq,event_id,event_type,candidate_id,identity_time_ms,"
            "recorded_at_ms,payload_json,prev_hash,row_hash "
            f"FROM realtime_engineering_paper_events{where} ORDER BY event_seq",
            args,
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RealtimeEngineeringPaperStoreError(
                    "engineering paper payload JSON is invalid"
                ) from exc
            if not isinstance(payload, dict):
                raise RealtimeEngineeringPaperStoreError(
                    "engineering paper payload must be an object"
                )
            item = dict(row)
            item.pop("payload_json")
            item["payload"] = payload
            result.append(item)
        return result

    def _append(
        self,
        *,
        event_id: str,
        event_type: str,
        candidate_id: str,
        identity_time_ms: int,
        recorded_at_ms: int,
        payload: Mapping[str, Any],
    ) -> bool:
        if event_type not in {"ADMITTED", "EXECUTION"}:
            raise ValueError("unknown engineering paper event type")
        if not candidate_id or identity_time_ms < 0 or recorded_at_ms < identity_time_ms:
            raise ValueError("engineering paper event identity is invalid")
        body = _canonical(payload)
        existing = self._conn.execute(
            "SELECT event_type,candidate_id,identity_time_ms,payload_json "
            "FROM realtime_engineering_paper_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["event_type"]),
                str(existing["candidate_id"]),
                int(existing["identity_time_ms"]),
                str(existing["payload_json"]),
            ) == (event_type, candidate_id, identity_time_ms, body):
                return False
            raise RealtimeEngineeringPaperStoreError(
                f"engineering event identity reused with different content: {event_id}"
            )
        previous = self._conn.execute(
            "SELECT row_hash FROM realtime_engineering_paper_events "
            "ORDER BY event_seq DESC LIMIT 1"
        ).fetchone()
        prev_hash = "" if previous is None else str(previous["row_hash"])
        identity = {
            "event_id": event_id,
            "event_type": event_type,
            "candidate_id": candidate_id,
            "identity_time_ms": int(identity_time_ms),
            "recorded_at_ms": int(recorded_at_ms),
            "payload_json": body,
            "prev_hash": prev_hash,
        }
        digest = _row_hash(identity)
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO realtime_engineering_paper_events"
                    "(event_id,event_type,candidate_id,identity_time_ms,recorded_at_ms,"
                    "payload_json,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        event_type,
                        candidate_id,
                        int(identity_time_ms),
                        int(recorded_at_ms),
                        body,
                        prev_hash,
                        digest,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RealtimeEngineeringPaperStoreError(
                "engineering paper append conflict"
            ) from exc
        return True

    def admission(self, candidate_id: str) -> dict[str, Any] | None:
        rows = self._events(event_type="ADMITTED", candidate_id=candidate_id)
        if len(rows) > 1:
            raise RealtimeEngineeringPaperStoreError(
                "candidate has multiple engineering admissions"
            )
        return rows[0] if rows else None

    def admit(
        self,
        candidate_id: str,
        identity: Mapping[str, Any],
        *,
        baseline_decision_time_ms: int,
        admitted_at_ms: int,
    ) -> bool:
        payload = {
            "identity": dict(identity),
            "baseline_decision_time_ms": int(baseline_decision_time_ms),
            "paper_only": True,
            "official_paper": False,
            "promotion_eligible": False,
            "forward_evidence_eligible": False,
            "deployment_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        }
        existing = self.admission(candidate_id)
        if existing is not None:
            if existing["payload"] != payload:
                raise RealtimeEngineeringPaperStoreError(
                    "engineering admission identity conflict"
                )
            return False
        return self._append(
            event_id=f"admission:{candidate_id}",
            event_type="ADMITTED",
            candidate_id=candidate_id,
            identity_time_ms=int(admitted_at_ms),
            recorded_at_ms=int(admitted_at_ms),
            payload=payload,
        )

    def admitted_candidate_ids(self) -> tuple[str, ...]:
        rows = self._events(event_type="ADMITTED")
        ids = [str(row["candidate_id"]) for row in rows]
        if len(ids) != len(set(ids)):
            raise RealtimeEngineeringPaperStoreError(
                "candidate has multiple engineering admissions"
            )
        return tuple(sorted(ids))

    def executions(self, candidate_id: str) -> list[dict[str, Any]]:
        return self._events(event_type="EXECUTION", candidate_id=candidate_id)

    def _non_execution_resolutions(
        self, candidate_id: str
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT event_seq,event_id,candidate_id,identity_time_ms,recorded_at_ms,"
            "payload_json,prev_hash,row_hash FROM realtime_engineering_paper_missed "
            "WHERE candidate_id=? ORDER BY event_seq",
            (candidate_id,),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RealtimeEngineeringPaperStoreError(
                    "missed-decision payload JSON is invalid"
                ) from exc
            if not isinstance(payload, dict):
                raise RealtimeEngineeringPaperStoreError(
                    "missed-decision payload must be an object"
                )
            resolution = payload.get("resolution")
            if resolution not in {"MISSED_EXECUTION_WINDOW", "NO_ACTION"}:
                raise RealtimeEngineeringPaperStoreError(
                    "non-execution resolution type is invalid"
                )
            item = dict(row)
            item.pop("payload_json")
            item["event_type"] = str(resolution)
            item["payload"] = payload
            result.append(item)
        return result

    def missed_decisions(self, candidate_id: str) -> list[dict[str, Any]]:
        return [
            row
            for row in self._non_execution_resolutions(candidate_id)
            if row["event_type"] == "MISSED_EXECUTION_WINDOW"
        ]

    def no_action_decisions(self, candidate_id: str) -> list[dict[str, Any]]:
        return [
            row
            for row in self._non_execution_resolutions(candidate_id)
            if row["event_type"] == "NO_ACTION"
        ]

    def resolved_decision_times(self, candidate_id: str) -> set[int]:
        return {
            int(row["identity_time_ms"])
            for row in self.executions(candidate_id)
            + self._non_execution_resolutions(candidate_id)
        }

    def _record_non_execution_resolution(
        self,
        candidate_id: str,
        payload: Mapping[str, Any],
        *,
        resolution: str,
        decision_time_ms: int,
        recorded_at_ms: int,
    ) -> bool:
        if resolution not in {"MISSED_EXECUTION_WINDOW", "NO_ACTION"}:
            raise ValueError("unknown non-execution resolution")
        body = _canonical(payload)
        prefix = "missed" if resolution == "MISSED_EXECUTION_WINDOW" else "no-action"
        event_id = f"{prefix}:{candidate_id}:{int(decision_time_ms)}"
        existing = self._conn.execute(
            "SELECT candidate_id,identity_time_ms,payload_json "
            "FROM realtime_engineering_paper_missed WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["candidate_id"]),
                int(existing["identity_time_ms"]),
                str(existing["payload_json"]),
            ) == (candidate_id, int(decision_time_ms), body):
                return False
            raise RealtimeEngineeringPaperStoreError(
                f"non-execution identity reused with different content: {event_id}"
            )
        previous = self._conn.execute(
            "SELECT row_hash FROM realtime_engineering_paper_missed "
            "ORDER BY event_seq DESC LIMIT 1"
        ).fetchone()
        prev_hash = "" if previous is None else str(previous["row_hash"])
        identity = {
            "event_id": event_id,
            "event_type": resolution,
            "candidate_id": candidate_id,
            "identity_time_ms": int(decision_time_ms),
            "recorded_at_ms": int(recorded_at_ms),
            "payload_json": body,
            "prev_hash": prev_hash,
        }
        digest = _row_hash(identity)
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO realtime_engineering_paper_missed"
                    "(event_id,candidate_id,identity_time_ms,recorded_at_ms,"
                    "payload_json,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?)",
                    (
                        event_id,
                        candidate_id,
                        int(decision_time_ms),
                        int(recorded_at_ms),
                        body,
                        prev_hash,
                        digest,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RealtimeEngineeringPaperStoreError(
                "engineering non-execution append conflict"
            ) from exc
        return True

    def record_missed_decision(
        self,
        candidate_id: str,
        payload: Mapping[str, Any],
        *,
        decision_time_ms: int,
        recorded_at_ms: int,
    ) -> bool:
        if self.admission(candidate_id) is None:
            raise RealtimeEngineeringPaperStoreError(
                "cannot resolve a decision before engineering admission"
            )
        if (
            payload.get("candidate_id") != candidate_id
            or payload.get("resolution") != "MISSED_EXECUTION_WINDOW"
            or any(
                payload.get(field) is not False
                for field in (
                    "promotion_eligible",
                    "forward_evidence_eligible",
                    "deployment_eligible",
                    "live_allowed",
                    "order_submission_allowed",
                )
            )
        ):
            raise RealtimeEngineeringPaperStoreError(
                "missed-decision safety boundary is invalid"
            )
        return self._record_non_execution_resolution(
            candidate_id,
            payload,
            resolution="MISSED_EXECUTION_WINDOW",
            decision_time_ms=decision_time_ms,
            recorded_at_ms=recorded_at_ms,
        )

    def record_no_action(
        self,
        candidate_id: str,
        payload: Mapping[str, Any],
        *,
        decision_time_ms: int,
        recorded_at_ms: int,
    ) -> bool:
        if self.admission(candidate_id) is None:
            raise RealtimeEngineeringPaperStoreError(
                "cannot resolve a decision before engineering admission"
            )
        if (
            payload.get("candidate_id") != candidate_id
            or payload.get("resolution") != "NO_ACTION"
            or payload.get("reason_code") != "NO_ACTIONABLE_INTENT"
            or payload.get("paper_only") is not True
            or any(
                payload.get(field) is not False
                for field in (
                    "promotion_eligible",
                    "forward_evidence_eligible",
                    "deployment_eligible",
                    "live_allowed",
                    "order_submission_allowed",
                )
            )
        ):
            raise RealtimeEngineeringPaperStoreError(
                "no-action safety boundary is invalid"
            )
        return self._record_non_execution_resolution(
            candidate_id,
            payload,
            resolution="NO_ACTION",
            decision_time_ms=decision_time_ms,
            recorded_at_ms=recorded_at_ms,
        )

    def get_by_decision(
        self, candidate_id: str, decision_time_ms: int
    ) -> dict[str, Any] | None:
        rows = [
            row
            for row in self.executions(candidate_id)
            if int(row["identity_time_ms"]) == int(decision_time_ms)
        ]
        if len(rows) > 1:
            raise RealtimeEngineeringPaperStoreError(
                "candidate decision has multiple executions"
            )
        return rows[0] if rows else None

    def record_execution(
        self,
        candidate_id: str,
        preview: Mapping[str, Any],
        *,
        recorded_at_ms: int,
    ) -> bool:
        if self.admission(candidate_id) is None:
            raise RealtimeEngineeringPaperStoreError(
                "cannot execute before engineering admission"
            )
        try:
            decision_time = int(preview["decision_ts"])
            residual = float(preview["reconciliation_residual"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RealtimeEngineeringPaperStoreError(
                "engineering execution identity is invalid"
            ) from exc
        tca = preview.get("execution_tca")
        if (
            preview.get("alpha_id") != candidate_id
            or preview.get("execution_class")
            != "AUTONOMOUS_REALTIME_ENGINEERING_EXECUTION_PAPER"
            or preview.get("paper_execution") != "PAPER_FILLED"
            or preview.get("official_paper_execution") != "PAPER_BLOCKED"
            or any(
                preview.get(field) is not False
                for field in (
                    "promotion_eligible",
                    "forward_evidence_eligible",
                    "deployment_eligible",
                    "live_allowed",
                    "order_submission_allowed",
                    "broker_connectivity",
                    "broker_order_submission",
                    "credentials_accessed",
                )
            )
            or not math.isfinite(residual)
            or abs(residual) > 1e-6
            or not isinstance(tca, Mapping)
            or tca.get("route_tca_available") is not True
        ):
            raise RealtimeEngineeringPaperStoreError(
                "engineering execution safety/reconciliation boundary is invalid"
            )
        return self._append(
            event_id=f"execution:{candidate_id}:{decision_time}",
            event_type="EXECUTION",
            candidate_id=candidate_id,
            identity_time_ms=decision_time,
            recorded_at_ms=int(recorded_at_ms),
            payload=preview,
        )

    def latest_positions(self, candidate_id: str) -> dict[str, int]:
        rows = self.executions(candidate_id)
        if not rows:
            return {}
        raw = rows[-1]["payload"].get("paper_positions")
        if not isinstance(raw, Mapping):
            raise RealtimeEngineeringPaperStoreError(
                "engineering positions are missing"
            )
        return {str(key): int(value) for key, value in raw.items()}

    def summary(self, candidate_id: str) -> dict[str, Any]:
        admission = self.admission(candidate_id)
        rows = self.executions(candidate_id)
        missed = self.missed_decisions(candidate_id)
        no_actions = self.no_action_decisions(candidate_id)
        cumulative_net_pnl = 0.0
        cumulative_gross_pnl = 0.0
        cumulative_turnover = 0.0
        accounting_missing_count = 0
        execution_history: list[dict[str, Any]] = []
        last_equity: float | None = None
        last_net_pnl: float | None = None
        last_gross_pnl: float | None = None
        for row in rows:
            payload = row["payload"]
            pnl = payload.get("pnl")
            tca = payload.get("execution_tca")
            if not isinstance(pnl, Mapping) or not isinstance(tca, Mapping):
                accounting_missing_count += 1
                continue
            values: dict[str, float | None] = {}
            for name, value in (
                ("net_pnl", pnl.get("net_pnl")),
                ("gross_pnl", pnl.get("gross_pnl")),
                ("equity", pnl.get("equity")),
                ("turnover", pnl.get("turnover")),
                ("execution_cost", tca.get("execution_cost")),
            ):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    values[name] = None
                    continue
                numeric = float(value)
                values[name] = numeric if math.isfinite(numeric) else None
            if any(
                values[name] is None
                for name in ("net_pnl", "equity", "turnover", "execution_cost")
            ):
                accounting_missing_count += 1
                continue
            net_pnl = float(values["net_pnl"])
            gross_pnl = values["gross_pnl"]
            equity = float(values["equity"])
            turnover = float(values["turnover"])
            execution_cost = float(values["execution_cost"])
            cumulative_net_pnl += net_pnl
            cumulative_turnover += turnover
            if gross_pnl is not None:
                cumulative_gross_pnl += float(gross_pnl)
            last_net_pnl = net_pnl
            last_gross_pnl = float(gross_pnl) if gross_pnl is not None else None
            last_equity = equity
            execution_history.append(
                {
                    "as_of_ms": int(row["identity_time_ms"]),
                    "net_pnl": net_pnl,
                    "gross_pnl": (
                        float(gross_pnl) if gross_pnl is not None else None
                    ),
                    "cumulative_net_pnl": cumulative_net_pnl,
                    "equity": equity,
                    "turnover": turnover,
                    "execution_cost": execution_cost,
                    "reconciliation_residual": float(
                        payload.get("reconciliation_residual", 0.0)
                    ),
                }
            )
        accounting_complete = bool(rows) and accounting_missing_count == 0
        return {
            "candidate_id": candidate_id,
            "admitted": admission is not None,
            "baseline_decision_time_ms": (
                admission["payload"].get("baseline_decision_time_ms")
                if admission
                else None
            ),
            "n_executions": len(rows),
            "n_missed_execution_windows": len(missed),
            "n_no_actions": len(no_actions),
            "n_orders": sum(len(row["payload"].get("orders", [])) for row in rows),
            "n_fill_slices": sum(
                int(row["payload"].get("n_paper_fill_slices", 0)) for row in rows
            ),
            "execution_cost": sum(
                float(row["payload"].get("execution_tca", {}).get("execution_cost", 0.0))
                for row in rows
            ),
            "execution_pnl_available": accounting_complete,
            "execution_accounting_missing_count": accounting_missing_count,
            "cumulative_net_pnl": (
                cumulative_net_pnl if accounting_complete else None
            ),
            "cumulative_gross_pnl": (
                cumulative_gross_pnl
                if accounting_complete
                and all(
                    isinstance(item.get("gross_pnl"), (int, float))
                    for item in execution_history
                )
                else None
            ),
            "last_net_pnl": last_net_pnl if accounting_complete else None,
            "last_gross_pnl": last_gross_pnl if accounting_complete else None,
            "last_equity": last_equity if accounting_complete else None,
            "cumulative_turnover": (
                cumulative_turnover if accounting_complete else None
            ),
            "execution_history": execution_history,
            "last_decision_time_ms": (
                int(rows[-1]["identity_time_ms"]) if rows else None
            ),
            "last_missed_decision_time_ms": (
                int(missed[-1]["identity_time_ms"]) if missed else None
            ),
            "last_no_action_decision_time_ms": (
                int(no_actions[-1]["identity_time_ms"]) if no_actions else None
            ),
            "positions_lots": self.latest_positions(candidate_id),
        }

    def event_count(self) -> int:
        primary = int(
            self._conn.execute(
                "SELECT COUNT(*) FROM realtime_engineering_paper_events"
            ).fetchone()[0]
        )
        missed = int(
            self._conn.execute(
                "SELECT COUNT(*) FROM realtime_engineering_paper_missed"
            ).fetchone()[0]
        )
        return primary + missed

    def _missed_chain_valid(self) -> bool:
        previous = ""
        rows = self._conn.execute(
            "SELECT event_id,candidate_id,identity_time_ms,recorded_at_ms,"
            "payload_json,prev_hash,row_hash FROM realtime_engineering_paper_missed "
            "ORDER BY event_seq"
        )
        for row in rows:
            if str(row["prev_hash"]) != previous:
                return False
            try:
                payload = json.loads(str(row["payload_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            resolution = payload.get("resolution") if isinstance(payload, dict) else None
            if resolution not in {"MISSED_EXECUTION_WINDOW", "NO_ACTION"}:
                return False
            identity = {
                "event_id": str(row["event_id"]),
                "event_type": str(resolution),
                "candidate_id": str(row["candidate_id"]),
                "identity_time_ms": int(row["identity_time_ms"]),
                "recorded_at_ms": int(row["recorded_at_ms"]),
                "payload_json": str(row["payload_json"]),
                "prev_hash": previous,
            }
            expected = _row_hash(identity)
            if str(row["row_hash"]) != expected:
                return False
            previous = expected
        return True

    def chain_valid(self) -> bool:
        previous = ""
        try:
            rows = self._conn.execute(
                "SELECT event_id,event_type,candidate_id,identity_time_ms,recorded_at_ms,"
                "payload_json,prev_hash,row_hash FROM realtime_engineering_paper_events "
                "ORDER BY event_seq"
            )
            for row in rows:
                if str(row["prev_hash"]) != previous:
                    return False
                identity = {
                    "event_id": str(row["event_id"]),
                    "event_type": str(row["event_type"]),
                    "candidate_id": str(row["candidate_id"]),
                    "identity_time_ms": int(row["identity_time_ms"]),
                    "recorded_at_ms": int(row["recorded_at_ms"]),
                    "payload_json": str(row["payload_json"]),
                    "prev_hash": previous,
                }
                expected = _row_hash(identity)
                if str(row["row_hash"]) != expected:
                    return False
                previous = expected
            return self._missed_chain_valid()
        except (sqlite3.DatabaseError, TypeError, ValueError):
            return False


__all__ = [
    "RealtimeEngineeringPaperStore",
    "RealtimeEngineeringPaperStoreError",
]
