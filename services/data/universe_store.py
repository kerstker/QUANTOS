"""Persisted point-in-time universe membership (durable sqlite3) + BYO-CSV ingest.

Parallels SqlitePITStore: survives process restarts, fail-closed on an
uninitialized/corrupt store. Reuses the in-memory PITUniverse for all membership
logic (interval-based, survivorship-bias-free). Offline; no network/broker.
"""
from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass

from .persistent_store import DataStoreError
from .universe import PITUniverse

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso_ok(d) -> bool:
    return isinstance(d, str) and bool(_ISO.match(d))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS membership (
    instrument_id TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    PRIMARY KEY (instrument_id, start_date)
);
"""


@dataclass(frozen=True)
class UniverseIngestionReport:
    rows_read: int
    added: int
    rejected: tuple


class SqliteUniverseStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='membership'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise DataStoreError(f"universe store unreadable: {exc}") from exc
        if not rows:
            raise DataStoreError("universe store uninitialized; missing 'membership' table")

    def add_membership(self, instrument_id: str, start_date: str, end_date=None) -> bool:
        self._require_schema()
        if not _iso_ok(start_date):
            raise ValueError(f"start_date must be ISO YYYY-MM-DD, got {start_date!r}")
        if end_date is not None:
            if not _iso_ok(end_date):
                raise ValueError(f"end_date must be ISO YYYY-MM-DD, got {end_date!r}")
            if end_date < start_date:
                raise ValueError("end_date precedes start_date")
        # fail-closed on a corrected end_date for an existing (id,start): silently
        # IGNORE-ing it would keep a stale interval and could re-introduce the very
        # survivorship error this store exists to prevent (review P1). Identical
        # re-adds are idempotent no-ops; a genuine conflict raises.
        existing = self._conn.execute(
            "SELECT end_date FROM membership WHERE instrument_id=? AND start_date=?",
            (instrument_id, start_date)).fetchone()
        if existing is not None:
            if existing["end_date"] != end_date:
                raise ValueError(
                    f"conflicting end_date for ({instrument_id},{start_date}): "
                    f"stored {existing['end_date']!r} != {end_date!r}")
            return False
        self._conn.execute(
            "INSERT INTO membership(instrument_id, start_date, end_date) VALUES (?,?,?)",
            (instrument_id, start_date, end_date))
        self._conn.commit()
        return True

    def save(self, universe: PITUniverse) -> int:
        """Persist every interval of an in-memory universe (PK-deduped). Returns new rows."""
        self._require_schema()
        return sum(1 for iid, start, end in universe.memberships() if self.add_membership(iid, start, end))

    def load_into(self, universe: PITUniverse) -> PITUniverse:
        self._require_schema()
        for r in self._conn.execute(
                "SELECT instrument_id, start_date, end_date FROM membership ORDER BY instrument_id, start_date"):
            universe.add_membership(r["instrument_id"], r["start_date"], r["end_date"])
        return universe

    def members_as_of(self, date: str) -> set:
        u = PITUniverse()
        self.load_into(u)
        return u.members_as_of(date)


def ingest_universe_csv(path: str, store: SqliteUniverseStore) -> UniverseIngestionReport:
    """Read a local membership CSV (columns instrument_id,start_date,end_date; end blank=open).
    Bad rows (non-ISO dates, end<start, missing column) are recorded, not crashed."""
    rows_read, added, rejected = 0, 0, []
    with open(path, newline="", encoding="utf-8") as fh:
        for i, raw in enumerate(csv.DictReader(fh)):
            rows_read += 1
            try:
                iid = (raw["instrument_id"] or "").strip()
                start = (raw["start_date"] or "").strip()
                end = (raw.get("end_date") or "").strip() or None
                if not iid:
                    raise ValueError("empty instrument_id")
                ok = store.add_membership(iid, start, end)
            except Exception as exc:
                rejected.append((i, type(exc).__name__, str(exc)))
                continue
            if ok:
                added += 1
    return UniverseIngestionReport(rows_read=rows_read, added=added, rejected=tuple(rejected))
