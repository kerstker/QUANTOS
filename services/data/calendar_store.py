"""Persisted trading calendar (durable sqlite3) + bidirectional data reconciliation.

Sessions are YYYYMMDD ints (same encoding as market_csv event_time, directly
comparable to Bar.event_time). Reconciliation audits data completeness BOTH ways:
missing (a trading day with no bar) and ghost (a bar on a non-trading day —
holiday / bad data, the direction a one-way "missing sessions" check omits).
Complements universe_data_gaps (universe-membership dimension). Offline, no
network; deterministic; fail-closed.
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .persistent_store import DataStoreError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    calendar_id TEXT NOT NULL,
    session     INTEGER NOT NULL,
    PRIMARY KEY (calendar_id, session)
);
"""


def _validate_date_format(date_format: str) -> None:
    """Reject ambiguous formats that would silently mis-order sessions (same guard as
    market_csv): require %Y/%m/%d, forbid 2-digit %y."""
    if "%y" in date_format:
        raise ValueError(f"date_format 禁用两位年 %y(世纪边界翻转);用 %Y。得到 {date_format!r}")
    for d in ("%Y", "%m", "%d"):
        if d not in date_format:
            raise ValueError(f"date_format 必须含 {d}(需明确 年/月/日)。得到 {date_format!r}")


@dataclass(frozen=True)
class CalendarIngestionReport:
    rows_read: int
    added: int
    duplicates: int
    rejected: tuple


class SqliteCalendarStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise DataStoreError(f"calendar store unreadable: {exc}") from exc
        if not rows:
            raise DataStoreError("calendar store uninitialized; missing 'sessions' table")

    def add_session(self, calendar_id: str, session: int) -> bool:
        self._require_schema()
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO sessions(calendar_id, session) VALUES (?,?)",
            (calendar_id, int(session)))
        self._conn.commit()
        return cur.rowcount == 1

    def add_sessions(self, calendar_id: str, sessions) -> int:
        return sum(1 for s in sessions if self.add_session(calendar_id, s))

    def sessions(self, calendar_id: str) -> list:
        self._require_schema()
        return [r["session"] for r in self._conn.execute(
            "SELECT session FROM sessions WHERE calendar_id=? ORDER BY session", (calendar_id,))]

    def sessions_in(self, calendar_id: str, start: int, end: int) -> list:
        self._require_schema()
        return [r["session"] for r in self._conn.execute(
            "SELECT session FROM sessions WHERE calendar_id=? AND session>=? AND session<=? ORDER BY session",
            (calendar_id, int(start), int(end)))]

    def is_session(self, calendar_id: str, session: int) -> bool:
        self._require_schema()
        return self._conn.execute(
            "SELECT 1 FROM sessions WHERE calendar_id=? AND session=?",
            (calendar_id, int(session))).fetchone() is not None


def ingest_calendar_csv(path: str, store: SqliteCalendarStore, calendar_id: str, *,
                        date_col: str = "date", date_format: str = "%Y-%m-%d") -> CalendarIngestionReport:
    _validate_date_format(date_format)
    rows_read, added, duplicates, rejected = 0, 0, 0, []
    with open(path, newline="", encoding="utf-8") as fh:
        for i, raw in enumerate(csv.DictReader(fh)):
            rows_read += 1
            try:
                dt = datetime.strptime(raw[date_col], date_format)
                session = dt.year * 10000 + dt.month * 100 + dt.day
            except Exception as exc:
                rejected.append((i, type(exc).__name__, str(exc)))
                continue
            if store.add_session(calendar_id, session):
                added += 1
            else:
                duplicates += 1
    return CalendarIngestionReport(rows_read=rows_read, added=added, duplicates=duplicates, rejected=tuple(rejected))


def reconcile_calendar(store, calendar_store: SqliteCalendarStore, instruments, calendar_id: str, *,
                       start: int, end: int) -> dict:
    """Audit data coverage vs the calendar over [start,end] (raw event_time window — a
    completeness audit, not a PIT read). Per instrument: missing trading days, ghost
    bars on non-trading days, and coverage fraction."""
    sessions = set(calendar_store.sessions_in(calendar_id, start, end))
    missing, ghost, coverage = {}, {}, {}
    for inst in sorted(instruments):
        ets = {b.event_time for b in store.fetch_bars(inst, start, end)}
        missing[inst] = len(sessions - ets)
        ghost[inst] = len(ets - sessions)
        coverage[inst] = (len(sessions & ets) / len(sessions)) if sessions else 0.0
    return {"total_sessions": len(sessions), "missing": missing, "ghost": ghost, "coverage": coverage}


def align_calendars(calendar_store: SqliteCalendarStore, cal_a: str, cal_b: str) -> dict:
    """Cross-market alignment: sessions common to both, and exclusive to each."""
    a, b = set(calendar_store.sessions(cal_a)), set(calendar_store.sessions(cal_b))
    return {"common": sorted(a & b), "only_a": sorted(a - b), "only_b": sorted(b - a)}
