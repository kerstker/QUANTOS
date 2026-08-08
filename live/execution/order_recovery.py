"""Unknown-order recovery (727 §13.4) and its durable safety boundary.

An order submission that ends in a timeout, a 5xx, or a lost connection is not
the same thing as a rejected order.  The venue may already have accepted it.
This module makes that ambiguity durable and explicit:

``ORDER_SUBMISSION_UNKNOWN`` -> query by client/exchange id -> FOUND or
CONFIRMED_ABSENT -> an explicit retry authorization.  A query error stays
blocked and can arm the supplied kill switch; there is no automatic resubmit.

The store is deliberately in ``live/``.  It contains no networking and no
credentials; the venue adapter and the operator-owned kill-switch are injected
at the boundary.  The event table is append-only and hash chained so a crash
or a partial manual repair cannot silently turn an unknown order into a safe
retry.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Optional

from live.contracts import VenueOrderResult
from quantos.contracts.alpha import Actor, ActorKind


class OrderRecoveryError(RuntimeError):
    """The recovery store is missing, corrupt, or a transition is unsafe."""


class OrderRecoveryConflict(OrderRecoveryError):
    """An idempotency key or recovery transition was reused with new data."""


class RecoveryState(str, Enum):
    ORDER_SUBMISSION_UNKNOWN = "ORDER_SUBMISSION_UNKNOWN"
    FOUND = "FOUND"
    CONFIRMED_ABSENT = "CONFIRMED_ABSENT"
    INDETERMINATE = "INDETERMINATE"
    KILL_SWITCHED = "KILL_SWITCHED"
    RESOLVED = "RESOLVED"
    RETRY_AUTHORIZED = "RETRY_AUTHORIZED"


_BLOCKING_STATES = frozenset({
    RecoveryState.ORDER_SUBMISSION_UNKNOWN,
    RecoveryState.FOUND,
    RecoveryState.INDETERMINATE,
    RecoveryState.KILL_SWITCHED,
})
_ALLOWED_TRANSITIONS = {
    RecoveryState.ORDER_SUBMISSION_UNKNOWN: frozenset({
        RecoveryState.FOUND,
        RecoveryState.CONFIRMED_ABSENT,
        RecoveryState.INDETERMINATE,
        RecoveryState.KILL_SWITCHED,
    }),
    RecoveryState.FOUND: frozenset({
        RecoveryState.FOUND,
        RecoveryState.RESOLVED,
        RecoveryState.KILL_SWITCHED,
    }),
    RecoveryState.CONFIRMED_ABSENT: frozenset({
        RecoveryState.RETRY_AUTHORIZED,
        RecoveryState.KILL_SWITCHED,
    }),
    RecoveryState.INDETERMINATE: frozenset({
        RecoveryState.FOUND,
        RecoveryState.CONFIRMED_ABSENT,
        RecoveryState.KILL_SWITCHED,
    }),
    RecoveryState.KILL_SWITCHED: frozenset(),
    RecoveryState.RESOLVED: frozenset(),
    RecoveryState.RETRY_AUTHORIZED: frozenset(),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_recovery_events (
  seq                INTEGER PRIMARY KEY AUTOINCREMENT,
  client_order_id    TEXT NOT NULL,
  deployment_id      TEXT NOT NULL,
  instrument_id      TEXT NOT NULL,
  side               TEXT NOT NULL,
  quantity           REAL NOT NULL,
  exchange_order_id  TEXT NOT NULL,
  state              TEXT NOT NULL,
  reason             TEXT NOT NULL,
  observed_status    TEXT NOT NULL,
  recorded_at_ms     INTEGER NOT NULL CHECK (recorded_at_ms >= 0),
  payload_sha256     TEXT NOT NULL,
  prev_hash          TEXT NOT NULL,
  record_hash        TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS order_recovery_events_client_idx
  ON order_recovery_events(client_order_id, seq);
CREATE TRIGGER IF NOT EXISTS order_recovery_events_no_update
BEFORE UPDATE ON order_recovery_events
BEGIN SELECT RAISE(ABORT, 'order_recovery_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_recovery_events_no_delete
BEFORE DELETE ON order_recovery_events
BEGIN SELECT RAISE(ABORT, 'order_recovery_events is append-only'); END;
"""
_REQUIRED_TABLES = frozenset({"order_recovery_events"})
_GENESIS = ""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise OrderRecoveryError("order recovery payload is not canonical JSON") from exc


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record_hash(row: dict, *, prev_hash: str) -> str:
    content = {
        "seq": int(row["seq"]),
        "client_order_id": row["client_order_id"],
        "deployment_id": row["deployment_id"],
        "instrument_id": row["instrument_id"],
        "side": row["side"],
        "quantity": float(row["quantity"]),
        "exchange_order_id": row["exchange_order_id"],
        "state": row["state"],
        "reason": row["reason"],
        "observed_status": row["observed_status"],
        "recorded_at_ms": int(row["recorded_at_ms"]),
        "payload_sha256": row["payload_sha256"],
        "prev_hash": prev_hash,
    }
    return _sha256(_canonical_json(content))


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_time(value: object, *, name: str = "now_ms") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _require_quantity(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("quantity must be a positive finite number")
    quantity = float(value)
    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("quantity must be a positive finite number")
    return quantity


@dataclass(frozen=True)
class OrderRecoveryRecord:
    seq: int
    client_order_id: str
    deployment_id: str
    instrument_id: str
    side: str
    quantity: float
    exchange_order_id: str
    state: RecoveryState
    reason: str
    observed_status: str
    recorded_at_ms: int
    payload_sha256: str
    prev_hash: str
    record_hash: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "OrderRecoveryRecord":
        try:
            state = RecoveryState(row["state"])
        except ValueError as exc:
            raise OrderRecoveryError(
                f"unknown order recovery state in store: {row['state']!r}"
            ) from exc
        return cls(
            seq=int(row["seq"]),
            client_order_id=str(row["client_order_id"]),
            deployment_id=str(row["deployment_id"]),
            instrument_id=str(row["instrument_id"]),
            side=str(row["side"]),
            quantity=float(row["quantity"]),
            exchange_order_id=str(row["exchange_order_id"]),
            state=state,
            reason=str(row["reason"]),
            observed_status=str(row["observed_status"]),
            recorded_at_ms=int(row["recorded_at_ms"]),
            payload_sha256=str(row["payload_sha256"]),
            prev_hash=str(row["prev_hash"]),
            record_hash=str(row["record_hash"]),
        )


class SqliteOrderRecoveryStore:
    """Durable, append-only order-ambiguity state with a tamper-evident chain."""

    def __init__(self, db_path: str, *, create: bool = False) -> None:
        if not isinstance(db_path, str) or not db_path:
            raise OrderRecoveryError("order recovery database path must be non-empty")
        if create and db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        if not create and db_path == ":memory:":
            raise OrderRecoveryError("in-memory recovery store cannot be reopened")
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout=5000")
                if create:
                    self._conn.execute("PRAGMA synchronous=FULL")
                    self._conn.executescript(_SCHEMA)
                    self._conn.commit()
                self._verify_locked()
        except Exception:
            self._conn.close()
            raise

    def _rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(
                "SELECT * FROM order_recovery_events ORDER BY seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise OrderRecoveryError("order recovery store is unreadable") from exc

    def _verify_locked(self) -> None:
        previous = _GENESIS
        rows = self._rows_locked()
        for expected_seq, row in enumerate(rows, start=1):
            if int(row["seq"]) != expected_seq:
                raise OrderRecoveryError("order recovery sequence is not contiguous")
            if row["prev_hash"] != previous:
                raise OrderRecoveryError(
                    f"order recovery seq={row['seq']} previous hash mismatch"
                )
            expected = _record_hash(dict(row), prev_hash=previous)
            if row["record_hash"] != expected:
                raise OrderRecoveryError(
                    f"order recovery seq={row['seq']} record hash mismatch"
                )
            previous = expected

    def chain_valid(self) -> bool:
        with self._lock:
            try:
                self._verify_locked()
                return True
            except (OrderRecoveryError, sqlite3.DatabaseError, TypeError, ValueError):
                return False

    def _latest_locked(self, client_order_id: str) -> Optional[OrderRecoveryRecord]:
        row = self._conn.execute(
            "SELECT * FROM order_recovery_events WHERE client_order_id=? "
            "ORDER BY seq DESC LIMIT 1", (client_order_id,)
        ).fetchone()
        return None if row is None else OrderRecoveryRecord.from_row(row)

    def latest(self, client_order_id: str) -> Optional[OrderRecoveryRecord]:
        client_order_id = _require_text(client_order_id, name="client_order_id")
        with self._lock:
            self._verify_locked()
            return self._latest_locked(client_order_id)

    def latest_records(self) -> tuple[OrderRecoveryRecord, ...]:
        with self._lock:
            self._verify_locked()
            rows = self._conn.execute(
                "SELECT e.* FROM order_recovery_events e "
                "JOIN (SELECT client_order_id, MAX(seq) AS seq "
                "      FROM order_recovery_events GROUP BY client_order_id) latest "
                "ON latest.client_order_id=e.client_order_id AND latest.seq=e.seq "
                "ORDER BY e.seq"
            ).fetchall()
            return tuple(OrderRecoveryRecord.from_row(row) for row in rows)

    def pending(self, *, deployment_id: Optional[str] = None) -> tuple[OrderRecoveryRecord, ...]:
        records = self.latest_records()
        if deployment_id is not None:
            deployment_id = _require_text(deployment_id, name="deployment_id")
        return tuple(
            record for record in records
            if record.state in _BLOCKING_STATES
            and (deployment_id is None or record.deployment_id == deployment_id)
        )

    def deployment_blocked(self, deployment_id: str) -> bool:
        deployment_id = _require_text(deployment_id, name="deployment_id")
        return bool(self.pending(deployment_id=deployment_id))

    def retry_permitted(self, *, deployment_id: str, client_order_id: str) -> bool:
        """Return true only after an explicit ``authorize_retry`` transition.

        A confirmed absence alone is not enough to silently re-submit.  The
        caller must first perform the explicit transition and there must be no
        other unresolved order for the deployment.
        """
        deployment_id = _require_text(deployment_id, name="deployment_id")
        client_order_id = _require_text(client_order_id, name="client_order_id")
        record = self.latest(client_order_id)
        return bool(
            record is not None
            and record.deployment_id == deployment_id
            and record.state is RecoveryState.RETRY_AUTHORIZED
            and not self.deployment_blocked(deployment_id)
        )

    def client_submission_permitted(self, *, deployment_id: str,
                                    client_order_id: str) -> bool:
        """Gate one deterministic client id before it reaches a venue.

        A never-seen id is allowed.  An id that was confirmed absent is *not*
        automatically allowed: ``authorize_retry`` is the explicit, durable
        operator action required by 727 §13.4.  Resolved ids are permanently
        spent so a replay cannot create a second venue order.
        """
        deployment_id = _require_text(deployment_id, name="deployment_id")
        client_order_id = _require_text(client_order_id, name="client_order_id")
        record = self.latest(client_order_id)
        if record is None:
            return not self.deployment_blocked(deployment_id)
        if record.deployment_id != deployment_id:
            return False
        return self.retry_permitted(
            deployment_id=deployment_id, client_order_id=client_order_id
        )

    def _append_locked(
        self,
        *,
        previous: Optional[OrderRecoveryRecord],
        state: RecoveryState,
        reason: str,
        observed_status: str,
        exchange_order_id: str,
        recorded_at_ms: int,
    ) -> OrderRecoveryRecord:
        if previous is None:
            raise OrderRecoveryError("cannot append a transition without an order")
        if state not in _ALLOWED_TRANSITIONS[previous.state]:
            if state is previous.state and (
                previous.reason == reason
                and previous.observed_status == observed_status
                and previous.exchange_order_id == exchange_order_id
            ):
                return previous
            if state is previous.state is RecoveryState.FOUND:
                # A located order can legitimately move NEW -> FILLED while
                # recovery is running.  Keep the deployment blocked and append
                # the newer observed venue status; only mark_resolved() may
                # close the recovery record.
                pass
            else:
                raise OrderRecoveryConflict(
                    f"illegal recovery transition {previous.state.value}->{state.value} "
                    f"for {previous.client_order_id}"
                )
        payload = {
            "client_order_id": previous.client_order_id,
            "deployment_id": previous.deployment_id,
            "instrument_id": previous.instrument_id,
            "side": previous.side,
            "quantity": previous.quantity,
            "exchange_order_id": exchange_order_id,
            "state": state.value,
            "reason": reason,
            "observed_status": observed_status,
            "recorded_at_ms": recorded_at_ms,
        }
        payload_sha256 = _sha256(_canonical_json(payload))
        previous_hash = previous.record_hash
        seq = int(self._conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM order_recovery_events"
        ).fetchone()[0])
        row = {
            "seq": seq,
            "client_order_id": previous.client_order_id,
            "deployment_id": previous.deployment_id,
            "instrument_id": previous.instrument_id,
            "side": previous.side,
            "quantity": previous.quantity,
            "exchange_order_id": exchange_order_id,
            "state": state.value,
            "reason": reason,
            "observed_status": observed_status,
            "recorded_at_ms": recorded_at_ms,
            "payload_sha256": payload_sha256,
        }
        record_hash = _record_hash(row, prev_hash=previous_hash)
        self._conn.execute(
            "INSERT INTO order_recovery_events"
            " (seq,client_order_id,deployment_id,instrument_id,side,quantity,"
            "  exchange_order_id,state,reason,observed_status,recorded_at_ms,"
            "  payload_sha256,prev_hash,record_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                seq, previous.client_order_id, previous.deployment_id,
                previous.instrument_id, previous.side, previous.quantity,
                exchange_order_id, state.value, reason, observed_status,
                recorded_at_ms, payload_sha256, previous_hash, record_hash,
            ),
        )
        return OrderRecoveryRecord(
            seq=seq,
            client_order_id=previous.client_order_id,
            deployment_id=previous.deployment_id,
            instrument_id=previous.instrument_id,
            side=previous.side,
            quantity=previous.quantity,
            exchange_order_id=exchange_order_id,
            state=state,
            reason=reason,
            observed_status=observed_status,
            recorded_at_ms=recorded_at_ms,
            payload_sha256=payload_sha256,
            prev_hash=previous_hash,
            record_hash=record_hash,
        )

    def register_unknown(
        self,
        *,
        client_order_id: str,
        deployment_id: str,
        instrument_id: str,
        side: str,
        quantity: float,
        now_ms: int,
        exchange_order_id: str = "",
        reason: str = "submission response could not be confirmed",
    ) -> OrderRecoveryRecord:
        """Durably journal an ambiguous submission before any later retry."""
        client_order_id = _require_text(client_order_id, name="client_order_id")
        deployment_id = _require_text(deployment_id, name="deployment_id")
        instrument_id = _require_text(instrument_id, name="instrument_id")
        side = _require_text(side, name="side")
        quantity = _require_quantity(quantity)
        now_ms = _require_time(now_ms)
        exchange_order_id = str(exchange_order_id or "")
        reason = _require_text(reason, name="reason")
        with self._lock:
            self._verify_locked()
            previous = self._latest_locked(client_order_id)
            if previous is not None:
                immutable = (
                    previous.deployment_id, previous.instrument_id,
                    previous.side, previous.quantity,
                )
                if immutable != (deployment_id, instrument_id, side, quantity):
                    raise OrderRecoveryConflict(
                        f"client_order_id reused with a different immutable payload: "
                        f"{client_order_id}"
                    )
                if previous.state is RecoveryState.ORDER_SUBMISSION_UNKNOWN:
                    return previous
                raise OrderRecoveryConflict(
                    f"client_order_id already has recovery state {previous.state.value}: "
                    f"{client_order_id}"
                )
            payload = {
                "client_order_id": client_order_id,
                "deployment_id": deployment_id,
                "instrument_id": instrument_id,
                "side": side,
                "quantity": quantity,
                "exchange_order_id": exchange_order_id,
                "state": RecoveryState.ORDER_SUBMISSION_UNKNOWN.value,
                "reason": reason,
                "observed_status": "",
                "recorded_at_ms": now_ms,
            }
            payload_sha256 = _sha256(_canonical_json(payload))
            seq = int(self._conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM order_recovery_events"
            ).fetchone()[0])
            row = {
                "seq": seq,
                **payload,
                "payload_sha256": payload_sha256,
            }
            record_hash = _record_hash(row, prev_hash=_GENESIS if seq == 1 else
                                       self._conn.execute(
                                           "SELECT record_hash FROM order_recovery_events "
                                           "ORDER BY seq DESC LIMIT 1"
                                       ).fetchone()[0])
            prev_hash = _GENESIS if seq == 1 else self._conn.execute(
                "SELECT record_hash FROM order_recovery_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO order_recovery_events"
                " (seq,client_order_id,deployment_id,instrument_id,side,quantity,"
                "  exchange_order_id,state,reason,observed_status,recorded_at_ms,"
                "  payload_sha256,prev_hash,record_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    seq, client_order_id, deployment_id, instrument_id, side, quantity,
                    exchange_order_id, RecoveryState.ORDER_SUBMISSION_UNKNOWN.value,
                    reason, "", now_ms, payload_sha256, prev_hash, record_hash,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM order_recovery_events WHERE seq=?", (seq,)
            ).fetchone()
            return OrderRecoveryRecord.from_row(row)

    def transition(
        self,
        client_order_id: str,
        state: RecoveryState,
        *,
        now_ms: int,
        reason: str,
        observed_status: str = "",
        exchange_order_id: Optional[str] = None,
    ) -> OrderRecoveryRecord:
        client_order_id = _require_text(client_order_id, name="client_order_id")
        if not isinstance(state, RecoveryState):
            raise TypeError("state must be a RecoveryState")
        now_ms = _require_time(now_ms)
        reason = _require_text(reason, name="reason")
        observed_status = str(observed_status or "")
        with self._lock:
            self._verify_locked()
            previous = self._latest_locked(client_order_id)
            if previous is None:
                raise OrderRecoveryError(f"unknown recovery order: {client_order_id}")
            exchange = (previous.exchange_order_id if exchange_order_id is None
                        else str(exchange_order_id or ""))
            record = self._append_locked(
                previous=previous, state=state, reason=reason,
                observed_status=observed_status,
                exchange_order_id=exchange, recorded_at_ms=now_ms,
            )
            if record is previous:
                return previous
            self._conn.commit()
            return record

    def mark_found(self, client_order_id: str, result: VenueOrderResult, *, now_ms: int,
                   reason: str = "venue order located") -> OrderRecoveryRecord:
        if not isinstance(result, VenueOrderResult):
            raise TypeError("result must be a VenueOrderResult")
        return self.transition(
            client_order_id, RecoveryState.FOUND, now_ms=now_ms, reason=reason,
            observed_status=result.status, exchange_order_id=result.venue_order_id,
        )

    def mark_absent(self, client_order_id: str, *, now_ms: int,
                    reason: str = "venue explicitly confirmed order absent") -> OrderRecoveryRecord:
        return self.transition(client_order_id, RecoveryState.CONFIRMED_ABSENT,
                               now_ms=now_ms, reason=reason)

    def mark_indeterminate(self, client_order_id: str, *, now_ms: int,
                           reason: str = "venue query failed; existence is unknown") -> OrderRecoveryRecord:
        return self.transition(client_order_id, RecoveryState.INDETERMINATE,
                               now_ms=now_ms, reason=reason)

    def mark_kill_switched(self, client_order_id: str, *, now_ms: int,
                           reason: str = "unknown order query could not be confirmed") -> OrderRecoveryRecord:
        return self.transition(client_order_id, RecoveryState.KILL_SWITCHED,
                               now_ms=now_ms, reason=reason)

    def mark_resolved(self, client_order_id: str, *, now_ms: int,
                      observed_status: str) -> OrderRecoveryRecord:
        return self.transition(client_order_id, RecoveryState.RESOLVED,
                               now_ms=now_ms, reason="venue order was taken over and reconciled",
                               observed_status=observed_status)

    def authorize_retry(self, client_order_id: str, *, now_ms: int,
                        reason: str = "explicit operator authorization after confirmed absence") -> OrderRecoveryRecord:
        return self.transition(client_order_id, RecoveryState.RETRY_AUTHORIZED,
                               now_ms=now_ms, reason=reason)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _arm_kill_switch(kill_switch: object, *, now_ms: int, reason: str,
                     actor: Optional[Actor],
                     kill_switch_arm: Optional[Callable[[str, int], object]] = None
                     ) -> Optional[str]:
    if kill_switch_arm is not None:
        try:
            kill_switch_arm(reason, now_ms)
        except Exception as exc:
            return type(exc).__name__
        return None
    if kill_switch is None:
        return "kill_switch_not_supplied"
    arm = getattr(kill_switch, "arm", None)
    if not callable(arm):
        return "kill_switch_has_no_arm_method"
    actor = actor or Actor(actor_id="order-recovery", kind=ActorKind.SYSTEM)
    try:
        arm(actor, reason, at=now_ms)
    except Exception as exc:
        # A failed attempt to record the halt is itself a fail-closed blocker;
        # the caller must not infer that trading is safe from this return value.
        return type(exc).__name__
    return None


def resolve_unknown_orders(
    store: SqliteOrderRecoveryStore,
    venue: object,
    *,
    now_ms: int,
    deployment_id: Optional[str] = None,
    kill_switch: object = None,
    kill_switch_actor: Optional[Actor] = None,
    kill_switch_arm: Optional[Callable[[str, int], object]] = None,
    oms_authority: object = None,
) -> dict:
    """Resolve durable unknowns without ever submitting a replacement order.

    ``None`` from a venue query is only accepted as a confirmed absence when
    every available query path completed without error.  If the query is
    unavailable or errors, the order remains blocking and the supplied kill
    switch is armed.  The return value is diagnostic; callers must still gate
    order submission on ``store.deployment_blocked``/``retry_permitted``.
    """
    if not isinstance(store, SqliteOrderRecoveryStore):
        raise TypeError("store must be a SqliteOrderRecoveryStore")
    if oms_authority is not None:
        from live.execution.oms import OmsAuthority

        if not isinstance(oms_authority, OmsAuthority):
            raise TypeError("oms_authority must be an OmsAuthority")
    now_ms = _require_time(now_ms)
    if deployment_id is not None:
        deployment_id = _require_text(deployment_id, name="deployment_id")
    get_by_exchange = getattr(venue, "get_order_by_exchange_id", None)
    outcomes: list[dict] = []
    for record in store.pending(deployment_id=deployment_id):
        results: list[VenueOrderResult] = []
        errors: list[str] = []
        queried = 0
        get_order = getattr(venue, "get_order", None)
        if callable(get_order):
            queried += 1
            try:
                result = get_order(record.instrument_id, record.client_order_id)
                if result is not None:
                    results.append(result)
            except Exception as exc:
                errors.append(type(exc).__name__)
        else:
            errors.append("get_order_unavailable")
        if not results and record.exchange_order_id and callable(get_by_exchange):
            queried += 1
            try:
                result = get_by_exchange(record.instrument_id, record.exchange_order_id)
                if result is not None:
                    results.append(result)
            except Exception as exc:
                errors.append(type(exc).__name__)
        if results:
            found = store.mark_found(record.client_order_id, results[0], now_ms=now_ms)
            final = found
            if oms_authority is not None:
                from live.execution.order_event_store import OrderEventSource
                from quantos.contracts.hashing import stable_hash

                snapshot = {
                    "client_order_id": results[0].client_order_id,
                    "venue_order_id": results[0].venue_order_id,
                    "instrument_id": results[0].instrument_id,
                    "status": results[0].status,
                    "filled_qty": results[0].filled_qty,
                    "avg_price": results[0].avg_price,
                }
                snapshot_hash = stable_hash(snapshot)
                try:
                    oms_authority.ingest_venue_snapshot(
                        client_order_id=record.client_order_id,
                        source=OrderEventSource.RECOVERY,
                        source_sequence=now_ms,
                        venue_event_id=f"recovery-{snapshot_hash}",
                        venue_order_id=results[0].venue_order_id,
                        venue_status=results[0].status,
                        cumulative_filled_qty=results[0].filled_qty,
                        avg_fill_price=results[0].avg_price,
                        observed_at_ms=now_ms,
                        venue_payload_hash=snapshot_hash,
                    )
                except Exception as exc:
                    outcomes.append({
                        "client_order_id": record.client_order_id,
                        "deployment_id": record.deployment_id,
                        "outcome": "OMS_PROJECTION_BLOCKED",
                        "state": found.state.value,
                        "error_code": f"OMS_{type(exc).__name__.upper()}",
                    })
                    continue
            if results[0].status in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                final = store.mark_resolved(
                    record.client_order_id, now_ms=now_ms,
                    observed_status=results[0].status,
                )
            outcomes.append({
                "client_order_id": record.client_order_id,
                "deployment_id": record.deployment_id,
                "outcome": "RESOLVED" if final.state is RecoveryState.RESOLVED else "FOUND",
                "status": results[0].status,
                "state": final.state.value,
            })
            continue
        if errors:
            reason = "venue query indeterminate (" + ",".join(sorted(set(errors))) + ")"
            try:
                store.mark_indeterminate(record.client_order_id, now_ms=now_ms, reason=reason)
            except OrderRecoveryConflict:
                # A concurrent resolver may already have advanced the same
                # record.  Re-read rather than weakening the block.
                pass
            kill_error = _arm_kill_switch(
                kill_switch, now_ms=now_ms,
                reason=f"ORDER_SUBMISSION_UNKNOWN:{record.client_order_id}",
                actor=kill_switch_actor,
                kill_switch_arm=kill_switch_arm,
            )
            if kill_error is None:
                try:
                    store.mark_kill_switched(record.client_order_id, now_ms=now_ms)
                except OrderRecoveryConflict:
                    pass
                state = RecoveryState.KILL_SWITCHED.value
            else:
                state = RecoveryState.INDETERMINATE.value
            outcomes.append({
                "client_order_id": record.client_order_id,
                "deployment_id": record.deployment_id,
                "outcome": "KILL_SWITCHED" if kill_error is None else "INDETERMINATE",
                "state": state,
                "query_errors": sorted(set(errors)),
                "kill_switch_error": kill_error,
            })
            continue
        if queried == 0:
            # Defensive branch: a malformed venue object must never be read as
            # a confirmed absence.
            reason = "venue exposes no order-query capability"
            store.mark_indeterminate(record.client_order_id, now_ms=now_ms, reason=reason)
            kill_error = _arm_kill_switch(
                kill_switch, now_ms=now_ms, reason=f"ORDER_SUBMISSION_UNKNOWN:{record.client_order_id}",
                actor=kill_switch_actor,
                kill_switch_arm=kill_switch_arm,
            )
            if kill_error is None:
                store.mark_kill_switched(record.client_order_id, now_ms=now_ms)
            outcomes.append({
                "client_order_id": record.client_order_id,
                "deployment_id": record.deployment_id,
                "outcome": "KILL_SWITCHED" if kill_error is None else "INDETERMINATE",
                "state": (RecoveryState.KILL_SWITCHED.value if kill_error is None
                          else RecoveryState.INDETERMINATE.value),
                "query_errors": ["get_order_unavailable"],
                "kill_switch_error": kill_error,
            })
            continue
        absent = store.mark_absent(record.client_order_id, now_ms=now_ms)
        outcomes.append({
            "client_order_id": record.client_order_id,
            "deployment_id": record.deployment_id,
            "outcome": "CONFIRMED_ABSENT",
            "state": absent.state.value,
            "retry_authorized": False,
        })
    return {
        "outcomes": outcomes,
        "blocked_deployments": sorted({
            record.deployment_id for record in store.pending(deployment_id=deployment_id)
        }),
        "retryable_orders": sorted(
            record.client_order_id for record in store.latest_records()
            if record.state is RecoveryState.RETRY_AUTHORIZED
            and (deployment_id is None or record.deployment_id == deployment_id)
        ),
    }


__all__ = [
    "OrderRecoveryConflict",
    "OrderRecoveryError",
    "OrderRecoveryRecord",
    "RecoveryState",
    "SqliteOrderRecoveryStore",
    "resolve_unknown_orders",
]
