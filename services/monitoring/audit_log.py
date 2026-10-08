"""Global tamper-evident audit log (Phase 13).

Append-only, hash-chained aggregator of audit events from ALL components into ONE
chain (prev_hash -> entry_hash). chain_valid() detects entry mutation and
reordering of an INTERNAL chain. A bare hash chain cannot, by itself, detect
TRUNCATION or WHOLESALE REPLACEMENT — so callers periodically seal() a commitment
(length + head hash) out of band and verify_against() it; that catches truncation,
deletion, replacement, and full erasure (review P0). The events view is read-only
(no external reassignment) and event_ids are deduplicated (no replay forks).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from quantos.contracts.governance import AuditEvent


@dataclass(frozen=True)
class AuditCommitment:
    """An out-of-band anchor: the chain length and head hash at seal time."""
    length: int
    head_hash: str


class GlobalAuditLog:
    def __init__(self):
        self._events: list[AuditEvent] = []
        self._seen_ids: set[str] = set()

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)            # read-only view; cannot be reassigned/mutated externally

    def append(self, *, event_id: str, actor: str, action: str, timestamp: int,
               target: str = "", metadata: Optional[dict] = None) -> AuditEvent:
        if event_id in self._seen_ids:
            raise ValueError(f"duplicate audit event_id (replay): {event_id}")
        prev = self._events[-1].entry_hash if self._events else ""
        ev = AuditEvent(event_id=event_id, actor=actor, action=action, target=target,
                        timestamp=timestamp, metadata=metadata or {}, prev_hash=prev)
        self._events.append(ev)
        self._seen_ids.add(event_id)
        return ev

    def ingest(self, source: AuditEvent) -> AuditEvent:
        meta = dict(source.metadata)
        meta["source_hash"] = source.entry_hash
        return self.append(event_id=source.event_id, actor=source.actor, action=source.action,
                           timestamp=source.timestamp, target=source.target, metadata=meta)

    def chain_valid(self) -> bool:
        prev = ""
        for ev in self._events:
            if ev.prev_hash != prev:
                return False
            prev = ev.entry_hash
        return True

    @property
    def head_hash(self) -> str:
        return self._events[-1].entry_hash if self._events else ""

    def seal(self) -> AuditCommitment:
        """Out-of-band commitment to the current chain. Hold this externally and
        verify_against() it later to detect truncation / replacement / erasure."""
        return AuditCommitment(length=len(self._events), head_hash=self.head_hash)

    def verify_against(self, commitment: AuditCommitment) -> bool:
        """Internal chain valid AND length + head match the sealed commitment.
        Catches truncation (length), deletion, wholesale replacement (head), and
        full erasure (length 0 != sealed length)."""
        return (self.chain_valid()
                and len(self._events) == commitment.length
                and self.head_hash == commitment.head_hash)
