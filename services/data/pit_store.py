"""Point-in-time bitemporal store — the data-moat core (Phase 2, closes Gap 1).

Stores BitemporalRecords keyed by a string (e.g. instrument_id) and answers
as-of queries that return ONLY records knowable at the query time
(`knowledge_time <= as_of`). Lookahead is therefore impossible to read out, not
merely discouraged. Deterministic ordering for reproducibility (CT-3).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from quantos.contracts.temporal import BitemporalRecord


class PITStore:
    def __init__(self) -> None:
        self._by_key: dict[str, list[BitemporalRecord]] = defaultdict(list)

    def add(self, key: str, record: BitemporalRecord) -> None:
        if not isinstance(record, BitemporalRecord):
            raise TypeError("PITStore only accepts BitemporalRecord instances")
        self._by_key[key].append(record)

    def add_many(self, key: str, records) -> None:
        for r in records:
            self.add(key, r)

    def keys(self) -> list[str]:
        return sorted(self._by_key)

    def as_of(self, key: str, as_of_time: int, event_time_max: Optional[int] = None) -> list[BitemporalRecord]:
        """Records for `key` knowable at `as_of_time`, optionally restricted to
        event_time <= event_time_max, sorted by (event_time, knowledge_time)."""
        out = [
            r for r in self._by_key.get(key, [])
            if r.knowledge_time <= as_of_time
            and (event_time_max is None or r.event_time <= event_time_max)
        ]
        return sorted(out, key=lambda r: (r.event_time, r.knowledge_time))

    def latest_as_of(self, key: str, as_of_time: int, event_time_max: Optional[int] = None) -> Optional[BitemporalRecord]:
        """The most recent (by event_time, then knowledge_time) record knowable
        at `as_of_time`. None if nothing is knowable yet."""
        rs = self.as_of(key, as_of_time, event_time_max)
        return rs[-1] if rs else None
