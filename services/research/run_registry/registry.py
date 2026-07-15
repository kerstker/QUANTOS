"""RunRegistry — validated facade over ResearchRunStore (fail-closed boundary).

Rejects any record whose status is not a VALID_STATUS or whose kind is outside the research-only
ALLOWED_KINDS whitelist — so a non-research (e.g. order/trade) kind can never enter the registry,
even via a buggy caller. Read methods pass through."""

from __future__ import annotations

from services.research.ports.persistence import RunStorePort

from .contract import (
    ALLOWED_KINDS,
    RUN_OWNER_LEGACY_MIRROR,
    RUN_OWNER_RUNNER_INVOCATION,
    VALID_RUN_OWNERS,
    VALID_STATUS,
    ResearchRun,
)


class RunRegistry:
    def __init__(self, store: RunStorePort):
        self._s = store

    def _validate(self, r: ResearchRun) -> None:
        if r.status not in VALID_STATUS:
            raise ValueError(f"invalid status {r.status!r}")
        if r.kind not in ALLOWED_KINDS:
            raise ValueError(f"non-whitelisted research kind {r.kind!r}")
        if r.owner not in VALID_RUN_OWNERS:
            raise ValueError(f"invalid run owner {r.owner!r}")
        if r.owner == RUN_OWNER_RUNNER_INVOCATION:
            if not r.invocation_id or not r.request_hash or not r.identity_hash:
                raise ValueError("runner-owned runs require complete invocation identity")
        elif (
            r.owner != RUN_OWNER_LEGACY_MIRROR
            or r.invocation_id is not None
            or r.request_hash is not None
            or r.identity_hash is not None
        ):
            raise ValueError("legacy mirrors cannot carry runner invocation identity")

    def upsert(self, r: ResearchRun) -> None:
        self._validate(r)
        existing = self._s.get(r.run_id)
        if existing is not None and existing.owner != RUN_OWNER_LEGACY_MIRROR:
            raise ValueError("legacy mirror cannot overwrite a runner-owned run")
        self._s.upsert(r)

    def insert(self, r: ResearchRun) -> None:
        self._validate(r)
        self._s.insert(r)

    def insert_or_get(self, r: ResearchRun) -> tuple[ResearchRun, bool]:
        self._validate(r)
        return self._s.insert_or_get(r)

    def replace_if_status(self, r: ResearchRun, *, expected_status: str) -> bool:
        self._validate(r)
        if expected_status not in VALID_STATUS:
            raise ValueError(f"invalid expected status {expected_status!r}")
        return self._s.replace_if_status(r, expected_status=expected_status)

    def get(self, run_id: str):
        return self._s.get(run_id)

    def get_by_invocation(self, invocation_id: str):
        return self._s.get_by_invocation(invocation_id)

    def list(self, kind=None, limit: int = 200):
        return self._s.list(kind, limit)

    def set_status(self, run_id: str, status: str, error=None) -> None:
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status {status!r}")
        existing = self._s.get(run_id)
        if existing is not None and existing.owner != RUN_OWNER_LEGACY_MIRROR:
            raise ValueError("set_status cannot mutate a runner-owned run")
        self._s.set_status(run_id, status, error)

    def compare_and_set_status(
        self,
        run_id: str,
        *,
        expected_statuses: tuple[str, ...],
        status: str,
        error=None,
    ) -> bool:
        if status not in VALID_STATUS or any(
            value not in VALID_STATUS for value in expected_statuses
        ):
            raise ValueError("invalid run status transition")
        return self._s.compare_and_set_status(
            run_id,
            expected_statuses=expected_statuses,
            status=status,
            error=error,
        )

    def append_log(self, run_id: str, line: str) -> None:
        self._s.append_log(run_id, line)
