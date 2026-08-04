"""Append-only evidence ledger for autonomous realtime forward shadow-paper.

The ledger freezes decisions before outcomes and appends realizations later.
It is physically separate from official paper/live stores and carries no
promotion, broker, credential, or order-submission authority.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping
from typing import Any

from quantos.contracts.hashing import stable_hash


class RealtimeAlphaShadowStoreError(RuntimeError):
    """The autonomous forward-shadow ledger is invalid or conflicted."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS realtime_alpha_shadow_events (
  event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE NOT NULL,
  event_type TEXT NOT NULL CHECK(event_type IN ('ADMITTED','DECISION','REALIZATION')),
  candidate_id TEXT NOT NULL,
  identity_time_ms INTEGER NOT NULL CHECK(identity_time_ms >= 0),
  recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms >= 0),
  payload_json TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  row_hash TEXT UNIQUE NOT NULL
);
CREATE INDEX IF NOT EXISTS realtime_alpha_shadow_by_candidate
ON realtime_alpha_shadow_events(candidate_id,event_seq);
CREATE TRIGGER IF NOT EXISTS realtime_alpha_shadow_no_update
BEFORE UPDATE ON realtime_alpha_shadow_events
BEGIN SELECT RAISE(ABORT, 'realtime_alpha_shadow_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS realtime_alpha_shadow_no_delete
BEFORE DELETE ON realtime_alpha_shadow_events
BEGIN SELECT RAISE(ABORT, 'realtime_alpha_shadow_events is append-only'); END;
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
        raise RealtimeAlphaShadowStoreError("shadow payload is not canonical JSON") from exc


def _row_hash(
    *,
    event_id: str,
    event_type: str,
    candidate_id: str,
    identity_time_ms: int,
    recorded_at_ms: int,
    payload_json: str,
    prev_hash: str,
) -> str:
    return stable_hash(
        {
            "event_id": event_id,
            "event_type": event_type,
            "candidate_id": candidate_id,
            "identity_time_ms": identity_time_ms,
            "recorded_at_ms": recorded_at_ms,
            "payload_json": payload_json,
            "prev_hash": prev_hash,
        }
    )


class RealtimeAlphaShadowStore:
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
        if "realtime_alpha_shadow_events" not in names:
            raise RealtimeAlphaShadowStoreError("shadow ledger schema is missing")

    def __enter__(self) -> "RealtimeAlphaShadowStore":
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
            args.append(str(event_type))
        if candidate_id is not None:
            clauses.append("candidate_id=?")
            args.append(str(candidate_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            "SELECT event_seq,event_id,event_type,candidate_id,identity_time_ms,"
            "recorded_at_ms,payload_json,prev_hash,row_hash "
            f"FROM realtime_alpha_shadow_events{where} ORDER BY event_seq",
            args,
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RealtimeAlphaShadowStoreError("shadow payload JSON is invalid") from exc
            if not isinstance(payload, dict):
                raise RealtimeAlphaShadowStoreError("shadow payload must be an object")
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
        if event_type not in {"ADMITTED", "DECISION", "REALIZATION"}:
            raise ValueError("unknown shadow event type")
        if not candidate_id or identity_time_ms < 0 or recorded_at_ms < identity_time_ms:
            raise ValueError("shadow event identity is invalid")
        body = _canonical(payload)
        existing = self._conn.execute(
            "SELECT event_type,candidate_id,identity_time_ms,payload_json "
            "FROM realtime_alpha_shadow_events WHERE event_id=?",
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
            raise RealtimeAlphaShadowStoreError(
                f"shadow event identity reused with different content: {event_id}"
            )
        previous = self._conn.execute(
            "SELECT row_hash FROM realtime_alpha_shadow_events ORDER BY event_seq DESC LIMIT 1"
        ).fetchone()
        prev_hash = "" if previous is None else str(previous["row_hash"])
        digest = _row_hash(
            event_id=event_id,
            event_type=event_type,
            candidate_id=candidate_id,
            identity_time_ms=int(identity_time_ms),
            recorded_at_ms=int(recorded_at_ms),
            payload_json=body,
            prev_hash=prev_hash,
        )
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO realtime_alpha_shadow_events"
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
            raise RealtimeAlphaShadowStoreError("shadow append conflict") from exc
        return True

    def admission(self, candidate_id: str) -> dict[str, Any] | None:
        rows = self._events(event_type="ADMITTED", candidate_id=candidate_id)
        if len(rows) > 1:
            raise RealtimeAlphaShadowStoreError("candidate has multiple shadow admissions")
        return rows[0] if rows else None

    def admitted_candidate_ids(self) -> tuple[str, ...]:
        """Return the immutable set of tracks that must survive worker restarts."""
        rows = self._events(event_type="ADMITTED")
        candidate_ids = [str(row["candidate_id"]) for row in rows]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise RealtimeAlphaShadowStoreError("candidate has multiple shadow admissions")
        return tuple(sorted(candidate_ids))

    def admit(
        self,
        candidate_id: str,
        identity: Mapping[str, Any],
        *,
        admitted_at_ms: int,
    ) -> bool:
        existing = self.admission(candidate_id)
        normalized = dict(identity)
        if existing is not None:
            if existing["payload"].get("identity") != normalized:
                raise RealtimeAlphaShadowStoreError("shadow admission identity conflict")
            return False
        payload = {
            "identity": normalized,
            "admitted_at_ms": int(admitted_at_ms),
            "paper_only": True,
            "promotion_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        }
        return self._append(
            event_id=f"admission:{candidate_id}",
            event_type="ADMITTED",
            candidate_id=str(candidate_id),
            identity_time_ms=int(admitted_at_ms),
            recorded_at_ms=int(admitted_at_ms),
            payload=payload,
        )

    def record_decision(
        self,
        candidate_id: str,
        decision: Mapping[str, Any],
        *,
        recorded_at_ms: int,
    ) -> bool:
        if self.admission(candidate_id) is None:
            raise RealtimeAlphaShadowStoreError("cannot decide before shadow admission")
        if (
            decision.get("candidate_id") != candidate_id
            or decision.get("paper_only") is not True
            or decision.get("promotion_eligible") is not False
            or decision.get("live_allowed") is not False
            or decision.get("order_submission_allowed") is not False
        ):
            raise RealtimeAlphaShadowStoreError("shadow decision safety boundary is invalid")
        decision_time = int(decision["decision_time_ms"])
        return self._append(
            event_id=f"decision:{candidate_id}:{decision_time}",
            event_type="DECISION",
            candidate_id=candidate_id,
            identity_time_ms=decision_time,
            recorded_at_ms=int(recorded_at_ms),
            payload=decision,
        )

    def record_realization(
        self,
        candidate_id: str,
        realization: Mapping[str, Any],
        *,
        recorded_at_ms: int,
    ) -> bool:
        decision_time = int(realization["decision_time_ms"])
        decisions = {
            int(row["identity_time_ms"]): row
            for row in self._events(event_type="DECISION", candidate_id=candidate_id)
        }
        if decision_time not in decisions:
            raise RealtimeAlphaShadowStoreError("cannot realize an unknown decision")
        if (
            realization.get("candidate_id") != candidate_id
            or realization.get("paper_only") is not True
            or realization.get("promotion_eligible") is not False
            or realization.get("live_allowed") is not False
            or realization.get("order_submission_allowed") is not False
        ):
            raise RealtimeAlphaShadowStoreError("shadow realization safety boundary is invalid")
        return self._append(
            event_id=f"realization:{candidate_id}:{decision_time}",
            event_type="REALIZATION",
            candidate_id=candidate_id,
            identity_time_ms=decision_time,
            recorded_at_ms=int(recorded_at_ms),
            payload=realization,
        )

    def decisions(self, candidate_id: str) -> list[dict[str, Any]]:
        return self._events(event_type="DECISION", candidate_id=candidate_id)

    def realizations(self, candidate_id: str) -> list[dict[str, Any]]:
        return self._events(event_type="REALIZATION", candidate_id=candidate_id)

    def pending(self, candidate_id: str) -> list[dict[str, Any]]:
        realized = {
            int(row["identity_time_ms"])
            for row in self.realizations(candidate_id)
        }
        return [
            row for row in self.decisions(candidate_id)
            if int(row["identity_time_ms"]) not in realized
        ]

    def latest_weights(self, candidate_id: str) -> dict[str, float]:
        rows = self.decisions(candidate_id)
        if not rows:
            return {}
        return {
            str(key): float(value)
            for key, value in rows[-1]["payload"].get("weights", {}).items()
        }

    def summary(self, candidate_id: str) -> dict[str, Any]:
        decisions = self.decisions(candidate_id)
        realizations = self.realizations(candidate_id)
        returns = [float(row["payload"]["net_return"]) for row in realizations]
        equity = 1.0
        curve: list[float] = []
        for value in returns:
            if not math.isfinite(value):
                raise RealtimeAlphaShadowStoreError("shadow return is non-finite")
            equity *= 1.0 + value
            curve.append(equity)
        peak = 1.0
        max_drawdown = 0.0
        history: list[dict[str, Any]] = []
        for index, value in enumerate(curve):
            peak = max(peak, value)
            drawdown = (peak - value) / peak
            max_drawdown = max(max_drawdown, drawdown)
            realization = realizations[index]
            history.append(
                {
                    "as_of_ms": int(realization["payload"]["as_of_ms"]),
                    "period_return": returns[index],
                    "cum_return": value - 1.0,
                    "drawdown": drawdown,
                }
            )
        return {
            "candidate_id": candidate_id,
            "admitted": self.admission(candidate_id) is not None,
            "n_decisions": len(decisions),
            "n_pending": len(self.pending(candidate_id)),
            "n_realizations": len(realizations),
            "cum_return": equity - 1.0 if returns else None,
            "max_drawdown": max_drawdown if returns else None,
            # Bound console artifacts while retaining the append-only store as
            # the complete source of truth.
            "performance_history": history[-256:],
            "last_decision_time_ms": (
                int(decisions[-1]["identity_time_ms"]) if decisions else None
            ),
            "last_realization_time_ms": (
                int(realizations[-1]["payload"]["as_of_ms"])
                if realizations
                else None
            ),
        }

    def chain_valid(self) -> bool:
        previous = ""
        try:
            rows = self._conn.execute(
                "SELECT event_id,event_type,candidate_id,identity_time_ms,recorded_at_ms,"
                "payload_json,prev_hash,row_hash FROM realtime_alpha_shadow_events "
                "ORDER BY event_seq"
            )
            for row in rows:
                if str(row["prev_hash"]) != previous:
                    return False
                expected = _row_hash(
                    event_id=str(row["event_id"]),
                    event_type=str(row["event_type"]),
                    candidate_id=str(row["candidate_id"]),
                    identity_time_ms=int(row["identity_time_ms"]),
                    recorded_at_ms=int(row["recorded_at_ms"]),
                    payload_json=str(row["payload_json"]),
                    prev_hash=previous,
                )
                if str(row["row_hash"]) != expected:
                    return False
                previous = expected
            return True
        except (sqlite3.DatabaseError, TypeError, ValueError):
            return False

    def event_count(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM realtime_alpha_shadow_events"
            ).fetchone()[0]
        )


__all__ = ["RealtimeAlphaShadowStore", "RealtimeAlphaShadowStoreError"]
