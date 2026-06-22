"""Survivorship-bias-free, point-in-time universe membership (Phase 2).

Membership is stored as intervals [start_date, end_date] (ISO YYYY-MM-DD;
end_date None = still a member). `members_as_of(date)` returns the universe as
it was known on that date — including instruments that were later delisted, and
correctly handling delisted-then-readded names (multiple intervals). This is
what makes backtests survivorship-bias-free: you never see today's index, only
the index as of the historical date.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_iso(date: str) -> str:
    # Membership comparisons are lexicographic, which is only correct for
    # zero-padded ISO YYYY-MM-DD. Reject anything else fail-closed.
    if not isinstance(date, str) or not _ISO_DATE.match(date):
        raise ValueError(f"date must be ISO YYYY-MM-DD, got {date!r}")
    return date


class PITUniverse:
    def __init__(self) -> None:
        # instrument_id -> list of (start_date, end_date|None)
        self._intervals: dict[str, list[tuple[str, Optional[str]]]] = defaultdict(list)

    def add_membership(self, instrument_id: str, start_date: str, end_date: Optional[str] = None) -> None:
        _check_iso(start_date)
        if end_date is not None:
            _check_iso(end_date)
            if end_date < start_date:
                raise ValueError("end_date precedes start_date")
        self._intervals[instrument_id].append((start_date, end_date))

    def is_member_as_of(self, instrument_id: str, date: str) -> bool:
        _check_iso(date)
        for start, end in self._intervals.get(instrument_id, []):
            if start <= date and (end is None or date <= end):
                return True
        return False

    def members_as_of(self, date: str) -> set[str]:
        _check_iso(date)
        return {iid for iid in self._intervals if self.is_member_as_of(iid, date)}
