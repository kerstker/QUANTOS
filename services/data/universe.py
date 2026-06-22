"""Survivorship-bias-free, point-in-time universe membership (Phase 2).

Membership is stored as intervals [start_date, end_date] (ISO YYYY-MM-DD;
end_date None = still a member). `members_as_of(date)` returns the universe as
it was known on that date — including instruments that were later delisted, and
correctly handling delisted-then-readded names (multiple intervals). This is
what makes backtests survivorship-bias-free: you never see today's index, only
the index as of the historical date.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional


class PITUniverse:
    def __init__(self) -> None:
        # instrument_id -> list of (start_date, end_date|None)
        self._intervals: dict[str, list[tuple[str, Optional[str]]]] = defaultdict(list)

    def add_membership(self, instrument_id: str, start_date: str, end_date: Optional[str] = None) -> None:
        if end_date is not None and end_date < start_date:
            raise ValueError("end_date precedes start_date")
        self._intervals[instrument_id].append((start_date, end_date))

    def is_member_as_of(self, instrument_id: str, date: str) -> bool:
        for start, end in self._intervals.get(instrument_id, []):
            if start <= date and (end is None or date <= end):
                return True
        return False

    def members_as_of(self, date: str) -> set[str]:
        return {iid for iid in self._intervals if self.is_member_as_of(iid, date)}
