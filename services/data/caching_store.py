"""In-memory read-through cache of a PIT bar store (performance, not new behavior).

Panel construction calls `store.as_of(inst, t)` for every (instrument, period) — O(P) such calls per
instrument, each returning the GROWING prefix of bars knowable by t. Against SqlitePITStore every call
is a fresh SQL query + json.loads + Bar validation of each returned row, so a P-period build re-parses
~O(P^2) rows from disk — the dominant cost on long-history backtests (it is why a 5.5-year run crawled).

CachingPITStore loads every bar ONCE (parsed) and answers as_of / latest_as_of / fetch_bars from memory
with BIT-IDENTICAL results and ordering — only the cost model changes, never the data. Read-only view;
the underlying store is untouched. Wrap AFTER all ingestion (the cache is a snapshot at construction).

Identical-output vs SqlitePITStore is pinned by tests/data/test_caching_store.py.
"""
from __future__ import annotations

from typing import Optional

# event_time bounds wide enough to load every bar regardless of timestamp unit (s / ms / day-int).
_MIN, _MAX = -(10 ** 18), 10 ** 18


class CachingPITStore:
    """Read-only in-memory view over any PIT store exposing keys/fetch_bars/as_of/latest_as_of."""

    def __init__(self, source) -> None:
        # fetch_bars returns bars ordered by (event_time, knowledge_time) — exactly the order
        # _query/as_of produce — so filtering this list by knowledge_time preserves as_of ordering.
        self._by_key = {k: tuple(source.fetch_bars(k, _MIN, _MAX)) for k in source.keys()}

    def keys(self) -> list:
        return sorted(self._by_key)

    def as_of(self, key: str, as_of_time: int, event_time_max: Optional[int] = None) -> list:
        bars = self._by_key.get(key, ())
        if event_time_max is None:
            return [b for b in bars if b.knowledge_time <= as_of_time]
        return [b for b in bars if b.knowledge_time <= as_of_time and b.event_time <= event_time_max]

    def latest_as_of(self, key: str, as_of_time: int, event_time_max: Optional[int] = None):
        rows = self.as_of(key, as_of_time, event_time_max)
        return rows[-1] if rows else None

    def fetch_bars(self, instrument_id: str, start: int, end: int) -> list:
        return [b for b in self._by_key.get(instrument_id, ()) if start <= b.event_time <= end]
