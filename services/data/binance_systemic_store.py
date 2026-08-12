"""Append-only storage for low-frequency Binance systemic-risk observations."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from collections.abc import Iterable
from typing import Any


class BinanceSystemicStoreError(RuntimeError):
    """The systemic-risk store is missing, corrupt, or conflicting."""


_GENESIS = ""
_SCHEMA = """
CREATE TABLE IF NOT EXISTS binance_systemic_observations (
  seq               INTEGER PRIMARY KEY AUTOINCREMENT,
  dataset           TEXT NOT NULL,
  instrument_id     TEXT NOT NULL,
  event_time_ms     INTEGER NOT NULL,
  knowledge_time_ms INTEGER NOT NULL,
  payload_json      TEXT NOT NULL,
  payload_sha256    TEXT NOT NULL,
  prev_hash         TEXT NOT NULL,
  row_hash          TEXT NOT NULL,
  UNIQUE(dataset, instrument_id, event_time_ms)
);
CREATE INDEX IF NOT EXISTS idx_binance_systemic_lookup
ON binance_systemic_observations(dataset, instrument_id, event_time_ms);
CREATE TRIGGER IF NOT EXISTS binance_systemic_observations_no_update
BEFORE UPDATE ON binance_systemic_observations
BEGIN SELECT RAISE(ABORT, 'binance_systemic_observations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS binance_systemic_observations_no_delete
BEFORE DELETE ON binance_systemic_observations
BEGIN SELECT RAISE(ABORT, 'binance_systemic_observations is append-only'); END;
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
        raise BinanceSystemicStoreError("observation is not canonical JSON") from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _event_content(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Return immutable event content without receipt-time provenance."""
    return {
        str(key): value
        for key, value in observation.items()
        if key not in {"knowledge_time_ms", "forward_eligible_from_ms"}
    }


class BinanceSystemicStore:
    """Durable, idempotent, tamper-evident public observations."""

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
            tables = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        except sqlite3.DatabaseError as exc:
            raise BinanceSystemicStoreError("systemic store is unreadable") from exc
        if "binance_systemic_observations" not in tables:
            raise BinanceSystemicStoreError("systemic store schema is missing")

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _identity(observation: Mapping[str, Any]) -> tuple[str, str, int, int]:
        if not isinstance(observation, Mapping):
            raise BinanceSystemicStoreError("observation must be a mapping")
        dataset = str(observation.get("dataset", "")).strip()
        instrument_id = str(observation.get("instrument_id", "")).upper().strip()
        try:
            event_time_ms = int(observation["event_time_ms"])
            knowledge_time_ms = int(observation["knowledge_time_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BinanceSystemicStoreError("observation timestamps are invalid") from exc
        if (
            not dataset
            or not instrument_id
            or event_time_ms < 0
            or knowledge_time_ms < 0
            or event_time_ms > knowledge_time_ms
        ):
            raise BinanceSystemicStoreError("observation identity is invalid")
        return dataset, instrument_id, event_time_ms, knowledge_time_ms

    def _decode(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BinanceSystemicStoreError("observation row has invalid JSON") from exc
        if not isinstance(payload, dict) or _sha256(payload) != row["payload_sha256"]:
            raise BinanceSystemicStoreError("observation payload hash mismatch")
        return payload

    def append(self, observation: Mapping[str, Any]) -> bool:
        return self.append_many((observation,)) == 1

    def append_many(self, observations: Iterable[Mapping[str, Any]]) -> int:
        """Atomically append a deterministic batch of public observations.

        Existing identical events (including a later receipt time for the same
        immutable event content) are idempotent.  Any malformed or conflicting
        member rolls the entire batch back, so a cross-sectional public archive
        can never become partially authoritative.
        """
        prepared: list[tuple[dict[str, Any], str, str, int, int, str, str]] = []
        for observation in observations:
            payload = dict(observation)
            dataset, instrument_id, event_time_ms, knowledge_time_ms = self._identity(
                payload
            )
            payload_json = _canonical(payload)
            payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            prepared.append(
                (
                    payload,
                    dataset,
                    instrument_id,
                    event_time_ms,
                    knowledge_time_ms,
                    payload_json,
                    payload_sha256,
                )
            )
        inserted = 0
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            previous = self._conn.execute(
                "SELECT row_hash FROM binance_systemic_observations "
                "ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = _GENESIS if previous is None else str(previous["row_hash"])
            for (
                payload,
                dataset,
                instrument_id,
                event_time_ms,
                knowledge_time_ms,
                payload_json,
                payload_sha256,
            ) in prepared:
                existing = self._conn.execute(
                    "SELECT * FROM binance_systemic_observations "
                    "WHERE dataset=? AND instrument_id=? AND event_time_ms=?",
                    (dataset, instrument_id, event_time_ms),
                ).fetchone()
                if existing is not None:
                    if existing["payload_sha256"] == payload_sha256:
                        continue
                    existing_payload = self._decode(existing)
                    if _canonical(_event_content(existing_payload)) == _canonical(
                        _event_content(payload)
                    ):
                        # A later fetch of the same immutable exchange event has
                        # a later receipt time. Preserve the earliest knowledge
                        # time and treat the replay as idempotent.
                        continue
                    raise BinanceSystemicStoreError(
                        "observation identity reused with different content: "
                        f"{dataset}/{instrument_id}/{event_time_ms}"
                    )
                row_hash = _sha256(
                    {
                        "dataset": dataset,
                        "instrument_id": instrument_id,
                        "event_time_ms": event_time_ms,
                        "knowledge_time_ms": knowledge_time_ms,
                        "payload_sha256": payload_sha256,
                        "prev_hash": prev_hash,
                    }
                )
                self._conn.execute(
                    "INSERT INTO binance_systemic_observations"
                    "(dataset,instrument_id,event_time_ms,knowledge_time_ms,"
                    "payload_json,payload_sha256,prev_hash,row_hash)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        dataset,
                        instrument_id,
                        event_time_ms,
                        knowledge_time_ms,
                        payload_json,
                        payload_sha256,
                        prev_hash,
                        row_hash,
                    ),
                )
                prev_hash = row_hash
                inserted += 1
            self._conn.commit()
            return inserted
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def latest(
        self,
        dataset: str,
        instrument_id: str,
        *,
        as_of_ms: int | None = None,
    ) -> dict[str, Any] | None:
        if as_of_ms is None:
            suffix = ""
            args: tuple[object, ...] = (
                str(dataset),
                str(instrument_id).upper(),
            )
        else:
            if (
                isinstance(as_of_ms, bool)
                or not isinstance(as_of_ms, int)
                or as_of_ms < 0
            ):
                raise ValueError("as_of_ms must be a non-negative integer")
            suffix = " AND knowledge_time_ms<=?"
            args = (str(dataset), str(instrument_id).upper(), as_of_ms)
        row = self._conn.execute(
            "SELECT * FROM binance_systemic_observations "
            "WHERE dataset=? AND instrument_id=?"
            + suffix
            + " ORDER BY event_time_ms DESC, seq DESC LIMIT 1",
            args,
        ).fetchone()
        return None if row is None else self._decode(row)

    def latest_event_time(self, dataset: str, instrument_id: str) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(event_time_ms) FROM binance_systemic_observations "
            "WHERE dataset=? AND instrument_id=?",
            (str(dataset), str(instrument_id).upper()),
        ).fetchone()
        value = row[0] if row is not None else None
        return None if value is None else int(value)

    def range_observations(
        self,
        dataset: str,
        *,
        start_event_time_ms: int,
        end_event_time_ms: int,
        instruments: Iterable[str] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Read one bounded event-time range in deterministic panel order."""
        if (
            isinstance(start_event_time_ms, bool)
            or not isinstance(start_event_time_ms, int)
            or isinstance(end_event_time_ms, bool)
            or not isinstance(end_event_time_ms, int)
            or start_event_time_ms < 0
            or end_event_time_ms <= start_event_time_ms
        ):
            raise ValueError("event-time range is invalid")
        selected = (
            tuple(sorted({str(value).upper().strip() for value in instruments}))
            if instruments is not None
            else ()
        )
        if instruments is not None and (not selected or any(not value for value in selected)):
            raise ValueError("instrument filter is invalid")
        placeholders = ",".join("?" for _ in selected)
        instrument_clause = (
            f" AND instrument_id IN ({placeholders})" if selected else ""
        )
        try:
            rows = self._conn.execute(
                "SELECT * FROM binance_systemic_observations "
                "WHERE dataset=? AND event_time_ms>=? AND event_time_ms<?"
                + instrument_clause
                + " ORDER BY event_time_ms, instrument_id, seq",
                (
                    str(dataset),
                    start_event_time_ms,
                    end_event_time_ms,
                    *selected,
                ),
            ).fetchall()
            return tuple(self._decode(row) for row in rows)
        except sqlite3.DatabaseError as exc:
            raise BinanceSystemicStoreError(
                "systemic range query failed"
            ) from exc

    def row_count(self, dataset: str | None = None) -> int:
        if dataset is None:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM binance_systemic_observations"
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM binance_systemic_observations WHERE dataset=?",
                (str(dataset),),
            ).fetchone()
        return int(row[0])

    def chain_valid(self) -> bool:
        previous = _GENESIS
        try:
            for row in self._conn.execute(
                "SELECT * FROM binance_systemic_observations ORDER BY seq"
            ):
                self._decode(row)
                if row["prev_hash"] != previous:
                    return False
                expected = _sha256(
                    {
                        "dataset": row["dataset"],
                        "instrument_id": row["instrument_id"],
                        "event_time_ms": int(row["event_time_ms"]),
                        "knowledge_time_ms": int(row["knowledge_time_ms"]),
                        "payload_sha256": row["payload_sha256"],
                        "prev_hash": row["prev_hash"],
                    }
                )
                if row["row_hash"] != expected:
                    return False
                previous = str(row["row_hash"])
            return True
        except (
            BinanceSystemicStoreError,
            sqlite3.DatabaseError,
            TypeError,
            ValueError,
        ):
            return False


__all__ = ["BinanceSystemicStore", "BinanceSystemicStoreError"]
