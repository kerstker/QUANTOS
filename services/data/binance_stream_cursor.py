"""Append-only sequence cursor and gap-recovery authority for Binance streams."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from services.data.binance_realtime_store import MAX_FUTURE_SKEW_MS


class StreamCursorStoreError(RuntimeError):
    """The stream cursor journal is missing, corrupt, or used illegally."""


class StreamCursorState(str, Enum):
    CONTIGUOUS = "CONTIGUOUS"
    GAP_DETECTED = "GAP_DETECTED"
    RECOVERING = "RECOVERING"
    RECOVERED = "RECOVERED"
    BLOCKED = "BLOCKED"


_PUBLISHABLE = frozenset({StreamCursorState.CONTIGUOUS, StreamCursorState.RECOVERED})
_GENESIS = ""
_SCHEMA = """
CREATE TABLE IF NOT EXISTS binance_stream_cursor_events (
  seq             INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id        TEXT NOT NULL UNIQUE,
  channel         TEXT NOT NULL,
  instrument_id   TEXT NOT NULL,
  state           TEXT NOT NULL,
  event_time_ms   INTEGER NOT NULL,
  observed_at_ms  INTEGER NOT NULL,
  payload_json    TEXT NOT NULL,
  payload_sha256  TEXT NOT NULL,
  prev_hash       TEXT NOT NULL,
  row_hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_binance_stream_cursor_latest
ON binance_stream_cursor_events(channel, instrument_id, seq);
CREATE TRIGGER IF NOT EXISTS binance_stream_cursor_events_no_update
BEFORE UPDATE ON binance_stream_cursor_events
BEGIN SELECT RAISE(ABORT, 'binance_stream_cursor_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS binance_stream_cursor_events_no_delete
BEFORE DELETE ON binance_stream_cursor_events
BEGIN SELECT RAISE(ABORT, 'binance_stream_cursor_events is append-only'); END;
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
        raise StreamCursorStoreError("cursor event is not canonical JSON") from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise StreamCursorStoreError(f"{name} must be non-empty canonical text")
    return value


def _instrument(value: object) -> str:
    instrument_id = _text(value, name="instrument_id").upper()
    if not instrument_id.endswith("USDT"):
        raise StreamCursorStoreError("instrument_id must be a USDT symbol")
    return instrument_id


def _integer(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StreamCursorStoreError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class StreamCursorRecord:
    seq: int
    event_id: str
    channel: str
    instrument_id: str
    event_type: str
    state: StreamCursorState
    cursor_update_id: int
    observed_first_update_id: int | None
    observed_final_update_id: int | None
    observed_previous_final_update_id: int | None
    event_time_ms: int
    observed_at_ms: int
    pending_through_update_id: int | None
    reason_code: str | None
    prev_hash: str
    row_hash: str

    @property
    def publishable(self) -> bool:
        return self.state in _PUBLISHABLE


class StreamCursorStore:
    """Single-writer sequence authority; rows are immutable and hash chained."""

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
            row = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='binance_stream_cursor_events'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StreamCursorStoreError("cursor store is unreadable") from exc
        if row is None:
            raise StreamCursorStoreError("cursor store schema is missing")

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _payload(
        *,
        channel: str,
        instrument_id: str,
        event_type: str,
        state: StreamCursorState,
        cursor_update_id: int,
        observed_first_update_id: int | None,
        observed_final_update_id: int | None,
        observed_previous_final_update_id: int | None,
        event_time_ms: int,
        observed_at_ms: int,
        pending_through_update_id: int | None,
        reason_code: str | None,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "channel": channel,
            "instrument_id": instrument_id,
            "event_type": event_type,
            "state": state.value,
            "cursor_update_id": cursor_update_id,
            "observed_first_update_id": observed_first_update_id,
            "observed_final_update_id": observed_final_update_id,
            "observed_previous_final_update_id": observed_previous_final_update_id,
            "event_time_ms": event_time_ms,
            "observed_at_ms": observed_at_ms,
            "pending_through_update_id": pending_through_update_id,
            "reason_code": reason_code,
        }

    @staticmethod
    def _decode(row: sqlite3.Row) -> StreamCursorRecord:
        try:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
            if hashlib.sha256(
                str(row["payload_json"]).encode("utf-8")
            ).hexdigest() != str(row["payload_sha256"]):
                raise ValueError("payload hash mismatch")
            expected_event_id = _sha256(
                {"kind": "BINANCE_STREAM_CURSOR_EVENT_V1", **payload}
            )
            if str(row["event_id"]) != expected_event_id:
                raise ValueError("event identity mismatch")
            if (
                str(row["channel"]) != str(payload["channel"])
                or str(row["instrument_id"]) != str(payload["instrument_id"])
                or str(row["state"]) != str(payload["state"])
                or int(row["event_time_ms"]) != int(payload["event_time_ms"])
                or int(row["observed_at_ms"]) != int(payload["observed_at_ms"])
            ):
                raise ValueError("cursor index columns do not match payload")
            return StreamCursorRecord(
                seq=int(row["seq"]),
                event_id=str(row["event_id"]),
                channel=str(payload["channel"]),
                instrument_id=str(payload["instrument_id"]),
                event_type=str(payload["event_type"]),
                state=StreamCursorState(str(payload["state"])),
                cursor_update_id=int(payload["cursor_update_id"]),
                observed_first_update_id=(
                    None
                    if payload["observed_first_update_id"] is None
                    else int(payload["observed_first_update_id"])
                ),
                observed_final_update_id=(
                    None
                    if payload["observed_final_update_id"] is None
                    else int(payload["observed_final_update_id"])
                ),
                observed_previous_final_update_id=(
                    None
                    if payload["observed_previous_final_update_id"] is None
                    else int(payload["observed_previous_final_update_id"])
                ),
                event_time_ms=int(payload["event_time_ms"]),
                observed_at_ms=int(payload["observed_at_ms"]),
                pending_through_update_id=(
                    None
                    if payload["pending_through_update_id"] is None
                    else int(payload["pending_through_update_id"])
                ),
                reason_code=(
                    None if payload["reason_code"] is None else str(payload["reason_code"])
                ),
                prev_hash=str(row["prev_hash"]),
                row_hash=str(row["row_hash"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StreamCursorStoreError("cursor event row is malformed") from exc

    def _append(self, payload: dict[str, object]) -> StreamCursorRecord:
        payload_json = _canonical(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        event_id = _sha256({"kind": "BINANCE_STREAM_CURSOR_EVENT_V1", **payload})
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT * FROM binance_stream_cursor_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) != payload_sha256:
                    raise StreamCursorStoreError(
                        "cursor event identity reused with different content"
                    )
                self._conn.rollback()
                return self._decode(existing)
            previous = self._conn.execute(
                "SELECT row_hash FROM binance_stream_cursor_events "
                "ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = _GENESIS if previous is None else str(previous["row_hash"])
            row_hash = _sha256(
                {
                    "event_id": event_id,
                    "payload_sha256": payload_sha256,
                    "prev_hash": prev_hash,
                }
            )
            cursor = self._conn.execute(
                "INSERT INTO binance_stream_cursor_events"
                "(event_id,channel,instrument_id,state,event_time_ms,observed_at_ms,"
                "payload_json,payload_sha256,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    payload["channel"],
                    payload["instrument_id"],
                    payload["state"],
                    payload["event_time_ms"],
                    payload["observed_at_ms"],
                    payload_json,
                    payload_sha256,
                    prev_hash,
                    row_hash,
                ),
            )
            seq = int(cursor.lastrowid)
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM binance_stream_cursor_events WHERE seq=?", (seq,)
            ).fetchone()
            if row is None:
                raise StreamCursorStoreError("cursor event disappeared after commit")
            return self._decode(row)
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def latest(self, channel: str, instrument_id: str) -> StreamCursorRecord | None:
        channel = _text(channel, name="channel")
        instrument_id = _instrument(instrument_id)
        row = self._conn.execute(
            "SELECT * FROM binance_stream_cursor_events "
            "WHERE channel=? AND instrument_id=? ORDER BY seq DESC LIMIT 1",
            (channel, instrument_id),
        ).fetchone()
        return None if row is None else self._decode(row)

    def observe(
        self,
        *,
        channel: str,
        instrument_id: str,
        first_update_id: int,
        final_update_id: int,
        previous_final_update_id: int,
        event_time_ms: int,
        observed_at_ms: int,
    ) -> StreamCursorRecord:
        channel = _text(channel, name="channel")
        instrument_id = _instrument(instrument_id)
        first_update_id = _integer(first_update_id, name="first_update_id")
        final_update_id = _integer(final_update_id, name="final_update_id")
        previous_final_update_id = _integer(
            previous_final_update_id, name="previous_final_update_id"
        )
        event_time_ms = _integer(event_time_ms, name="event_time_ms")
        observed_at_ms = _integer(observed_at_ms, name="observed_at_ms")
        if final_update_id < first_update_id:
            raise StreamCursorStoreError("update range is invalid")
        if event_time_ms > observed_at_ms + MAX_FUTURE_SKEW_MS:
            raise StreamCursorStoreError("event time exceeds bounded future skew")

        previous = self.latest(channel, instrument_id)
        if previous is None:
            state = StreamCursorState.CONTIGUOUS
            cursor_update_id = final_update_id
            pending = None
            reason = "INITIAL_SNAPSHOT_BASELINE"
        elif final_update_id <= previous.cursor_update_id:
            return previous
        elif event_time_ms < previous.event_time_ms:
            state = StreamCursorState.BLOCKED
            cursor_update_id = previous.cursor_update_id
            pending = max(previous.pending_through_update_id or 0, final_update_id)
            reason = "EVENT_TIME_REVERSED"
        elif previous.state in {
            StreamCursorState.GAP_DETECTED,
            StreamCursorState.RECOVERING,
            StreamCursorState.BLOCKED,
        }:
            state = previous.state
            cursor_update_id = previous.cursor_update_id
            pending = max(previous.pending_through_update_id or 0, final_update_id)
            reason = previous.reason_code
        elif (
            previous.state is StreamCursorState.RECOVERED
            and first_update_id <= previous.cursor_update_id <= final_update_id
        ):
            # Binance's documented snapshot bootstrap discards buffered frames
            # through REST `lastUpdateId`, then accepts the first event whose
            # [U, u] range covers that id.  Its `pu` still refers to the last
            # pre-snapshot stream event, so requiring `pu == lastUpdateId` here
            # would manufacture a gap after every valid REST recovery.
            state = StreamCursorState.CONTIGUOUS
            cursor_update_id = final_update_id
            pending = None
            reason = None
        elif (
            previous_final_update_id == previous.cursor_update_id
        ):
            state = StreamCursorState.CONTIGUOUS
            cursor_update_id = final_update_id
            pending = None
            reason = None
        else:
            state = StreamCursorState.GAP_DETECTED
            cursor_update_id = previous.cursor_update_id
            pending = final_update_id
            reason = "SEQUENCE_GAP"
        return self._append(
            self._payload(
                channel=channel,
                instrument_id=instrument_id,
                event_type="OBSERVATION",
                state=state,
                cursor_update_id=cursor_update_id,
                observed_first_update_id=first_update_id,
                observed_final_update_id=final_update_id,
                observed_previous_final_update_id=previous_final_update_id,
                event_time_ms=event_time_ms,
                observed_at_ms=observed_at_ms,
                pending_through_update_id=pending,
                reason_code=reason,
            )
        )

    def begin_recovery(
        self,
        channel: str,
        instrument_id: str,
        *,
        observed_at_ms: int,
        reason_code: str = "REST_SNAPSHOT_REQUIRED",
    ) -> StreamCursorRecord:
        observed_at_ms = _integer(observed_at_ms, name="observed_at_ms")
        reason_code = _text(reason_code, name="reason_code")
        previous = self.latest(channel, instrument_id)
        if previous is None or previous.state not in {
            StreamCursorState.GAP_DETECTED,
            StreamCursorState.BLOCKED,
            StreamCursorState.RECOVERING,
        }:
            raise StreamCursorStoreError("recovery requires a blocked cursor")
        if previous.state is StreamCursorState.RECOVERING:
            return previous
        return self._append(
            self._payload(
                channel=previous.channel,
                instrument_id=previous.instrument_id,
                event_type="RECOVERY_STARTED",
                state=StreamCursorState.RECOVERING,
                cursor_update_id=previous.cursor_update_id,
                observed_first_update_id=None,
                observed_final_update_id=None,
                observed_previous_final_update_id=None,
                event_time_ms=previous.event_time_ms,
                observed_at_ms=observed_at_ms,
                pending_through_update_id=previous.pending_through_update_id,
                reason_code=reason_code,
            )
        )

    def complete_recovery(
        self,
        channel: str,
        instrument_id: str,
        *,
        snapshot_update_id: int,
        event_time_ms: int,
        observed_at_ms: int,
    ) -> StreamCursorRecord:
        snapshot_update_id = _integer(snapshot_update_id, name="snapshot_update_id")
        event_time_ms = _integer(event_time_ms, name="event_time_ms")
        observed_at_ms = _integer(observed_at_ms, name="observed_at_ms")
        previous = self.latest(channel, instrument_id)
        if previous is None or previous.state is not StreamCursorState.RECOVERING:
            raise StreamCursorStoreError("recovery completion requires RECOVERING state")
        if (
            event_time_ms < previous.event_time_ms
            or event_time_ms > observed_at_ms + MAX_FUTURE_SKEW_MS
        ):
            raise StreamCursorStoreError("recovery timestamps are invalid")
        required = previous.pending_through_update_id or previous.cursor_update_id
        complete = snapshot_update_id >= required
        return self._append(
            self._payload(
                channel=previous.channel,
                instrument_id=previous.instrument_id,
                event_type="RECOVERY_COMPLETED" if complete else "RECOVERY_BLOCKED",
                state=(StreamCursorState.RECOVERED if complete else StreamCursorState.BLOCKED),
                # A successful REST snapshot becomes the recovery frontier.
                # Buffered stream events at or behind it are stale and are
                # ignored by observe(); the first newer event must either span
                # this id via [U, u] or connect through `pu`.
                cursor_update_id=(
                    snapshot_update_id if complete else previous.cursor_update_id
                ),
                observed_first_update_id=None,
                observed_final_update_id=snapshot_update_id,
                observed_previous_final_update_id=None,
                event_time_ms=event_time_ms,
                observed_at_ms=observed_at_ms,
                pending_through_update_id=(None if complete else required),
                reason_code=(None if complete else "REST_COVERAGE_INCOMPLETE"),
            )
        )

    def fail_recovery(
        self,
        channel: str,
        instrument_id: str,
        *,
        observed_at_ms: int,
        error_code: str,
    ) -> StreamCursorRecord:
        observed_at_ms = _integer(observed_at_ms, name="observed_at_ms")
        error_code = _text(error_code, name="error_code")
        previous = self.latest(channel, instrument_id)
        if previous is None or previous.state is not StreamCursorState.RECOVERING:
            raise StreamCursorStoreError("recovery failure requires RECOVERING state")
        return self._append(
            self._payload(
                channel=previous.channel,
                instrument_id=previous.instrument_id,
                event_type="RECOVERY_FAILED",
                state=StreamCursorState.BLOCKED,
                cursor_update_id=previous.cursor_update_id,
                observed_first_update_id=None,
                observed_final_update_id=None,
                observed_previous_final_update_id=None,
                event_time_ms=previous.event_time_ms,
                observed_at_ms=observed_at_ms,
                pending_through_update_id=previous.pending_through_update_id,
                reason_code=error_code,
            )
        )

    def event_count(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM binance_stream_cursor_events"
            ).fetchone()[0]
        )

    @staticmethod
    def _validate_checkpoint(
        checkpoint_seq: int,
        checkpoint_row_hash: str,
    ) -> None:
        if (
            isinstance(checkpoint_seq, bool)
            or not isinstance(checkpoint_seq, int)
            or checkpoint_seq < 0
        ):
            raise StreamCursorStoreError("cursor checkpoint sequence is invalid")
        if checkpoint_seq == 0:
            if checkpoint_row_hash != _GENESIS:
                raise StreamCursorStoreError("cursor genesis checkpoint is invalid")
        elif (
            not isinstance(checkpoint_row_hash, str)
            or len(checkpoint_row_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in checkpoint_row_hash
            )
        ):
            raise StreamCursorStoreError("cursor checkpoint hash is invalid")

    def verify_chain_from(
        self,
        *,
        checkpoint_seq: int = 0,
        checkpoint_row_hash: str = _GENESIS,
    ) -> dict[str, object]:
        """Verify the cursor-chain suffix after an exact trusted anchor."""
        previous = checkpoint_row_hash
        verified = 0
        last_seq = checkpoint_seq
        try:
            self._validate_checkpoint(checkpoint_seq, checkpoint_row_hash)
            if checkpoint_seq > 0:
                anchor = self._conn.execute(
                    "SELECT row_hash FROM binance_stream_cursor_events WHERE seq=?",
                    (checkpoint_seq,),
                ).fetchone()
                if (
                    anchor is None
                    or str(anchor["row_hash"]) != checkpoint_row_hash
                ):
                    raise StreamCursorStoreError(
                        "cursor checkpoint anchor mismatch"
                    )
            for row in self._conn.execute(
                "SELECT * FROM binance_stream_cursor_events "
                "WHERE seq>? ORDER BY seq",
                (checkpoint_seq,),
            ):
                self._decode(row)
                if str(row["prev_hash"]) != previous:
                    raise StreamCursorStoreError("cursor chain link mismatch")
                expected = _sha256(
                    {
                        "event_id": str(row["event_id"]),
                        "payload_sha256": str(row["payload_sha256"]),
                        "prev_hash": str(row["prev_hash"]),
                    }
                )
                if str(row["row_hash"]) != expected:
                    raise StreamCursorStoreError("cursor row hash mismatch")
                previous = str(row["row_hash"])
                last_seq = int(row["seq"])
                verified += 1
            return {
                "valid": True,
                "anchor_seq": checkpoint_seq,
                "verified_suffix_events": verified,
                "verified_seq": last_seq,
                "verified_row_hash": previous,
            }
        except (StreamCursorStoreError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
            return {
                "valid": False,
                "anchor_seq": checkpoint_seq,
                "verified_suffix_events": verified,
                "verified_seq": last_seq,
                "verified_row_hash": previous,
                "error": str(exc),
            }

    def verify_chain(self) -> dict[str, object]:
        verification = self.verify_chain_from()
        result = {
            "valid": verification["valid"],
            "verified_events": verification["verified_suffix_events"],
            "head": verification["verified_row_hash"],
        }
        if "error" in verification:
            result["error"] = verification["error"]
        return result

    def trading_watermark(
        self,
        required: Iterable[tuple[str, str]],
        *,
        chain_verification: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        normalized = tuple(
            sorted(
                {
                    (_text(channel, name="channel"), _instrument(instrument_id))
                    for channel, instrument_id in required
                }
            )
        )
        if not normalized:
            raise StreamCursorStoreError("required cursors must not be empty")
        blockers: list[str] = []
        watermarks: list[int] = []
        heads: dict[str, dict[str, object]] = {}
        for channel, instrument_id in normalized:
            record = self.latest(channel, instrument_id)
            key = f"{channel}:{instrument_id}"
            if record is None:
                blockers.append(f"{key}:MISSING")
                continue
            heads[key] = {
                "state": record.state.value,
                "cursor_update_id": record.cursor_update_id,
                "event_time_ms": record.event_time_ms,
                "observed_at_ms": record.observed_at_ms,
                "row_hash": record.row_hash,
                "reason_code": record.reason_code,
            }
            if not record.publishable:
                blockers.append(f"{key}:{record.state.value}")
            else:
                watermarks.append(min(record.event_time_ms, record.observed_at_ms))
        chain = (
            self.verify_chain()
            if chain_verification is None
            else dict(chain_verification)
        )
        if chain.get("valid") is not True:
            blockers.append("CURSOR_CHAIN_INVALID")
        elif chain_verification is not None:
            frontier = self._conn.execute(
                "SELECT seq,row_hash FROM binance_stream_cursor_events "
                "ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            try:
                verified_seq = int(chain["verified_seq"])
                verified_row_hash = str(chain["verified_row_hash"])
            except (KeyError, TypeError, ValueError):
                blockers.append("CURSOR_CHAIN_VERIFICATION_MALFORMED")
            else:
                if (
                    frontier is None
                    or verified_seq != int(frontier["seq"])
                    or verified_row_hash != str(frontier["row_hash"])
                ):
                    blockers.append("CURSOR_CHAIN_UNVERIFIED_SUFFIX")
        return {
            "schema_version": 1,
            "publishable": not blockers,
            "watermark_ms": min(watermarks) if not blockers and watermarks else None,
            "required_count": len(normalized),
            "blockers": blockers,
            "heads": heads,
            "chain": chain,
        }


__all__ = [
    "StreamCursorState",
    "StreamCursorRecord",
    "StreamCursorStore",
    "StreamCursorStoreError",
]
