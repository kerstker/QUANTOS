"""Append-only order-event authority for the L2 Testnet OMS."""
from __future__ import annotations

import math
import os
import re
import sqlite3
import threading
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

from live.execution.oms_state import canonical_for_venue_status
from quantos.contracts.hashing import stable_hash
from quantos.contracts.order import OrderState
from quantos.contracts.trading import OrderSide


_HEX64 = re.compile(r"[0-9a-f]{64}")
_GENESIS = ""


class OrderEventError(RuntimeError):
    """The order-event journal is unavailable, malformed, or unverifiable."""


class OrderEventConflict(OrderEventError):
    """A source identity or sequence was reused with conflicting content."""


class OrderEventSource(str, Enum):
    COMMAND = "COMMAND"
    REST_POLL = "REST_POLL"
    USER_STREAM = "USER_STREAM"
    RECOVERY = "RECOVERY"


def _text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError(f"{name} must be canonical text")
    if not allow_empty and not value:
        raise ValueError(f"{name} must be non-empty")
    return value


def _time(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _number(
    value: object, name: str, *, positive: bool = False, nonnegative: bool = False
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if positive and number <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and number < 0:
        raise ValueError(f"{name} must be non-negative")
    return number


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase sha256 digest")
    return value


@dataclass(frozen=True, slots=True)
class OrderEvent:
    source: OrderEventSource
    source_sequence: int
    venue_event_id: str
    client_order_id: str
    venue_order_id: str
    instrument_id: str
    side: OrderSide
    order_quantity: float
    strategy_id: str
    order_type: str
    limit_price: Optional[float]
    venue_status: str
    canonical_state: OrderState
    cumulative_filled_qty: float
    avg_fill_price: float
    observed_at_ms: int
    venue_payload_hash: str
    content_hash: str

    def body(self) -> dict[str, object]:
        return {
            "source": self.source.value,
            "source_sequence": self.source_sequence,
            "venue_event_id": self.venue_event_id,
            "client_order_id": self.client_order_id,
            "venue_order_id": self.venue_order_id,
            "instrument_id": self.instrument_id,
            "side": self.side.value,
            "order_quantity": self.order_quantity,
            "strategy_id": self.strategy_id,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "venue_status": self.venue_status,
            "canonical_state": self.canonical_state.value,
            "cumulative_filled_qty": self.cumulative_filled_qty,
            "avg_fill_price": self.avg_fill_price,
            "observed_at_ms": self.observed_at_ms,
            "venue_payload_hash": self.venue_payload_hash,
        }


def create_order_event(
    *,
    source: OrderEventSource,
    source_sequence: int,
    venue_event_id: str,
    client_order_id: str,
    venue_order_id: str,
    instrument_id: str,
    side: OrderSide,
    order_quantity: float,
    strategy_id: str,
    order_type: str,
    limit_price: Optional[float],
    venue_status: str,
    cumulative_filled_qty: float,
    avg_fill_price: float,
    observed_at_ms: int,
    venue_payload_hash: str,
) -> OrderEvent:
    if not isinstance(source, OrderEventSource):
        raise TypeError("source must be an OrderEventSource")
    source_sequence = _time(source_sequence, "source_sequence")
    venue_event_id = _text(venue_event_id, "venue_event_id")
    client_order_id = _text(client_order_id, "client_order_id")
    venue_order_id = _text(venue_order_id, "venue_order_id", allow_empty=True)
    instrument_id = _text(instrument_id, "instrument_id")
    if not isinstance(side, OrderSide):
        raise TypeError("side must be an OrderSide")
    order_quantity = _number(order_quantity, "order_quantity", positive=True)
    strategy_id = _text(strategy_id, "strategy_id")
    if order_type not in {"MARKET", "LIMIT"}:
        raise ValueError("order_type must be MARKET or LIMIT")
    if order_type == "LIMIT":
        limit_price = _number(limit_price, "limit_price", positive=True)
    elif limit_price is not None:
        raise ValueError("MARKET order cannot carry limit_price")
    venue_status = _text(venue_status, "venue_status")
    canonical = canonical_for_venue_status(venue_status)
    cumulative = _number(
        cumulative_filled_qty, "cumulative_filled_qty", nonnegative=True
    )
    average = _number(avg_fill_price, "avg_fill_price", nonnegative=True)
    if cumulative > order_quantity + 1e-12:
        raise ValueError("cumulative_filled_qty exceeds order_quantity")
    if cumulative > 0 and average <= 0:
        raise ValueError("positive cumulative fill requires avg_fill_price")
    if cumulative == 0 and average != 0:
        raise ValueError("zero cumulative fill requires zero avg_fill_price")
    observed_at_ms = _time(observed_at_ms, "observed_at_ms")
    venue_payload_hash = _digest(venue_payload_hash, "venue_payload_hash")
    provisional = OrderEvent(
        source=source,
        source_sequence=source_sequence,
        venue_event_id=venue_event_id,
        client_order_id=client_order_id,
        venue_order_id=venue_order_id,
        instrument_id=instrument_id,
        side=side,
        order_quantity=order_quantity,
        strategy_id=strategy_id,
        order_type=order_type,
        limit_price=limit_price,
        venue_status=venue_status,
        canonical_state=canonical,
        cumulative_filled_qty=cumulative,
        avg_fill_price=average,
        observed_at_ms=observed_at_ms,
        venue_payload_hash=venue_payload_hash,
        content_hash="pending",
    )
    return replace(provisional, content_hash=stable_hash(provisional.body()))


@dataclass(frozen=True, slots=True)
class OrderEventRecord:
    seq: int
    event: OrderEvent
    prev_hash: str
    event_hash: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_order_events (
  seq                    INTEGER PRIMARY KEY AUTOINCREMENT,
  source                 TEXT NOT NULL,
  source_sequence        INTEGER NOT NULL CHECK (source_sequence >= 0),
  venue_event_id         TEXT NOT NULL,
  client_order_id        TEXT NOT NULL,
  venue_order_id         TEXT NOT NULL,
  instrument_id          TEXT NOT NULL,
  side                   TEXT NOT NULL,
  order_quantity         REAL NOT NULL,
  strategy_id            TEXT NOT NULL,
  order_type             TEXT NOT NULL,
  limit_price            REAL,
  venue_status           TEXT NOT NULL,
  canonical_state        TEXT NOT NULL,
  cumulative_filled_qty  REAL NOT NULL,
  avg_fill_price          REAL NOT NULL,
  observed_at_ms         INTEGER NOT NULL CHECK (observed_at_ms >= 0),
  venue_payload_hash      TEXT NOT NULL,
  content_hash            TEXT NOT NULL,
  prev_hash               TEXT NOT NULL,
  event_hash              TEXT NOT NULL UNIQUE,
  UNIQUE(source, venue_event_id),
  UNIQUE(source, client_order_id, source_sequence)
);
CREATE INDEX IF NOT EXISTS live_order_events_client_idx
  ON live_order_events(client_order_id, seq);
CREATE TRIGGER IF NOT EXISTS live_order_events_no_update
BEFORE UPDATE ON live_order_events
BEGIN SELECT RAISE(ABORT, 'live_order_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS live_order_events_no_delete
BEFORE DELETE ON live_order_events
BEGIN SELECT RAISE(ABORT, 'live_order_events is append-only'); END;
"""


def _event_hash(seq: int, event: OrderEvent, previous: str) -> str:
    return stable_hash(
        {
            "seq": seq,
            "content_hash": event.content_hash,
            "prev_hash": previous,
        }
    )


class SqliteOrderEventStore:
    """Single-node event journal with global chain and source-level CAS."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if not isinstance(path, str) or not path:
            raise OrderEventError("order event store path must be non-empty")
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise OrderEventError("order event store is unavailable")
        if create and path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout=5000")
                if create:
                    try:
                        self._conn.execute("PRAGMA journal_mode=WAL")
                    except sqlite3.OperationalError as exc:
                        if "locked" not in str(exc).lower():
                            raise
                    self._conn.execute("PRAGMA synchronous=FULL")
                    self._conn.executescript(_SCHEMA)
                    self._conn.commit()
                self._verify_locked()
        except Exception:
            self._conn.close()
            raise

    @staticmethod
    def _decode(row: sqlite3.Row) -> OrderEventRecord:
        try:
            event = create_order_event(
                source=OrderEventSource(row["source"]),
                source_sequence=int(row["source_sequence"]),
                venue_event_id=row["venue_event_id"],
                client_order_id=row["client_order_id"],
                venue_order_id=row["venue_order_id"],
                instrument_id=row["instrument_id"],
                side=OrderSide(row["side"]),
                order_quantity=float(row["order_quantity"]),
                strategy_id=row["strategy_id"],
                order_type=row["order_type"],
                limit_price=(
                    None if row["limit_price"] is None else float(row["limit_price"])
                ),
                venue_status=row["venue_status"],
                cumulative_filled_qty=float(row["cumulative_filled_qty"]),
                avg_fill_price=float(row["avg_fill_price"]),
                observed_at_ms=int(row["observed_at_ms"]),
                venue_payload_hash=row["venue_payload_hash"],
            )
            if event.content_hash != row["content_hash"]:
                raise ValueError("content hash mismatch")
            if event.canonical_state.value != row["canonical_state"]:
                raise ValueError("canonical state mismatch")
            return OrderEventRecord(
                seq=int(row["seq"]),
                event=event,
                prev_hash=row["prev_hash"],
                event_hash=row["event_hash"],
            )
        except Exception as exc:
            raise OrderEventError("order event row is malformed") from exc

    def _rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(
                "SELECT * FROM live_order_events ORDER BY seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise OrderEventError("order event store is unreadable") from exc

    def _verify_locked(self) -> str:
        try:
            exists = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='live_order_events'"
            ).fetchone()
            if exists is None:
                raise OrderEventError("order event schema is missing")
            previous = _GENESIS
            source_state: dict[tuple[str, str], tuple[int, int]] = {}
            for expected_seq, row in enumerate(self._rows_locked(), start=1):
                if int(row["seq"]) != expected_seq:
                    raise OrderEventError("order event sequence is not contiguous")
                record = self._decode(row)
                if record.prev_hash != previous:
                    raise OrderEventError("order event chain link mismatch")
                expected_hash = _event_hash(record.seq, record.event, previous)
                if record.event_hash != expected_hash:
                    raise OrderEventError("order event hash mismatch")
                key = (record.event.source.value, record.event.client_order_id)
                prior = source_state.get(key)
                if prior is not None and (
                    record.event.source_sequence <= prior[0]
                    or record.event.observed_at_ms < prior[1]
                ):
                    raise OrderEventError("stored source sequence regressed")
                source_state[key] = (
                    record.event.source_sequence,
                    record.event.observed_at_ms,
                )
                previous = expected_hash
            return previous
        except sqlite3.DatabaseError as exc:
            raise OrderEventError("order event store is unreadable") from exc

    def append(self, event: OrderEvent) -> OrderEventRecord:
        if not isinstance(event, OrderEvent):
            raise TypeError("event must be an OrderEvent")
        try:
            restored = create_order_event(
                source=event.source,
                source_sequence=event.source_sequence,
                venue_event_id=event.venue_event_id,
                client_order_id=event.client_order_id,
                venue_order_id=event.venue_order_id,
                instrument_id=event.instrument_id,
                side=event.side,
                order_quantity=event.order_quantity,
                strategy_id=event.strategy_id,
                order_type=event.order_type,
                limit_price=event.limit_price,
                venue_status=event.venue_status,
                cumulative_filled_qty=event.cumulative_filled_qty,
                avg_fill_price=event.avg_fill_price,
                observed_at_ms=event.observed_at_ms,
                venue_payload_hash=event.venue_payload_hash,
            )
        except Exception as exc:
            raise OrderEventError("order event is malformed") from exc
        if restored != event:
            raise OrderEventError("order event failed canonical round-trip")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                previous = self._verify_locked()
                same_identity = self._conn.execute(
                    "SELECT * FROM live_order_events WHERE source=? AND venue_event_id=?",
                    (event.source.value, event.venue_event_id),
                ).fetchone()
                if same_identity is not None:
                    existing = self._decode(same_identity)
                    if existing.event.content_hash != event.content_hash:
                        raise OrderEventConflict(
                            "venue event identity reused with conflicting content"
                        )
                    self._conn.rollback()
                    return existing
                prior_sequence = self._conn.execute(
                    "SELECT source_sequence,observed_at_ms FROM live_order_events "
                    "WHERE source=? AND client_order_id=? ORDER BY source_sequence DESC LIMIT 1",
                    (event.source.value, event.client_order_id),
                ).fetchone()
                if prior_sequence is not None and (
                    event.source_sequence <= int(prior_sequence["source_sequence"])
                    or event.observed_at_ms < int(prior_sequence["observed_at_ms"])
                ):
                    raise OrderEventConflict("source sequence or time regressed")
                seq = int(
                    self._conn.execute(
                        "SELECT COALESCE(MAX(seq),0)+1 FROM live_order_events"
                    ).fetchone()[0]
                )
                event_hash = _event_hash(seq, event, previous)
                self._conn.execute(
                    "INSERT INTO live_order_events"
                    "(seq,source,source_sequence,venue_event_id,client_order_id,"
                    "venue_order_id,instrument_id,side,order_quantity,strategy_id,"
                    "order_type,limit_price,venue_status,canonical_state,"
                    "cumulative_filled_qty,avg_fill_price,observed_at_ms,"
                    "venue_payload_hash,content_hash,prev_hash,event_hash) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        seq,
                        event.source.value,
                        event.source_sequence,
                        event.venue_event_id,
                        event.client_order_id,
                        event.venue_order_id,
                        event.instrument_id,
                        event.side.value,
                        event.order_quantity,
                        event.strategy_id,
                        event.order_type,
                        event.limit_price,
                        event.venue_status,
                        event.canonical_state.value,
                        event.cumulative_filled_qty,
                        event.avg_fill_price,
                        event.observed_at_ms,
                        event.venue_payload_hash,
                        event.content_hash,
                        previous,
                        event_hash,
                    ),
                )
                self._conn.commit()
                return OrderEventRecord(seq, event, previous, event_hash)
            except sqlite3.IntegrityError as exc:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise OrderEventConflict("order event identity conflict") from exc
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def events(self, client_order_id: str | None = None) -> tuple[OrderEventRecord, ...]:
        with self._lock:
            self._verify_locked()
            if client_order_id is None:
                rows = self._rows_locked()
            else:
                client_order_id = _text(client_order_id, "client_order_id")
                rows = self._conn.execute(
                    "SELECT * FROM live_order_events WHERE client_order_id=? ORDER BY seq",
                    (client_order_id,),
                ).fetchall()
            return tuple(self._decode(row) for row in rows)

    def latest_for_order(self, client_order_id: str) -> OrderEventRecord | None:
        client_order_id = _text(client_order_id, "client_order_id")
        events = self.events(client_order_id)
        return events[-1] if events else None

    def event_hashes(self) -> tuple[str, ...]:
        return tuple(record.event_hash for record in self.events())

    def row_count(self) -> int:
        with self._lock:
            self._verify_locked()
            return int(
                self._conn.execute("SELECT COUNT(*) FROM live_order_events").fetchone()[0]
            )

    def chain_valid(self) -> bool:
        with self._lock:
            try:
                self._verify_locked()
                return True
            except OrderEventError:
                return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "OrderEvent",
    "OrderEventConflict",
    "OrderEventError",
    "OrderEventRecord",
    "OrderEventSource",
    "SqliteOrderEventStore",
    "create_order_event",
]
