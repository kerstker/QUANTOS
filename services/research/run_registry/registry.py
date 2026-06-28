"""RunRegistry — validated facade over ResearchRunStore (fail-closed boundary).

Rejects any record whose status is not a VALID_STATUS or whose kind is outside the research-only
ALLOWED_KINDS whitelist — so a non-research (e.g. order/trade) kind can never enter the registry,
even via a buggy caller. Read methods pass through."""
from __future__ import annotations

from .contract import ALLOWED_KINDS, VALID_STATUS, ResearchRun
from .store import ResearchRunStore


class RunRegistry:
    def __init__(self, store: ResearchRunStore):
        self._s = store

    def _validate(self, r: ResearchRun) -> None:
        if r.status not in VALID_STATUS:
            raise ValueError(f"invalid status {r.status!r}")
        if r.kind not in ALLOWED_KINDS:
            raise ValueError(f"non-whitelisted research kind {r.kind!r}")

    def upsert(self, r: ResearchRun) -> None:
        self._validate(r)
        self._s.upsert(r)

    def get(self, run_id: str):
        return self._s.get(run_id)

    def list(self, kind=None, limit: int = 200):
        return self._s.list(kind, limit)

    def set_status(self, run_id: str, status: str, error=None) -> None:
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status {status!r}")
        self._s.set_status(run_id, status, error)

    def append_log(self, run_id: str, line: str) -> None:
        self._s.append_log(run_id, line)
