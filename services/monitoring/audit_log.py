"""Global tamper-evident audit log (Phase 13).

Append-only, hash-chained aggregator of audit events from ALL components into ONE
chain (prev_hash -> entry_hash). Components emit their own AuditEvents; ingest()
re-chains them into the global chain (preserving the source entry_hash in
metadata) so a unified, replayable, tamper-evident record exists. Mutating any
entry breaks chain_valid().
"""
from __future__ import annotations

from typing import Optional

from quantos.contracts.governance import AuditEvent


class GlobalAuditLog:
    def __init__(self):
        self.events: list[AuditEvent] = []

    def append(self, *, event_id: str, actor: str, action: str, timestamp: int,
               target: str = "", metadata: Optional[dict] = None) -> AuditEvent:
        prev = self.events[-1].entry_hash if self.events else ""
        ev = AuditEvent(event_id=event_id, actor=actor, action=action, target=target,
                        timestamp=timestamp, metadata=metadata or {}, prev_hash=prev)
        self.events.append(ev)
        return ev

    def ingest(self, source: AuditEvent) -> AuditEvent:
        """Re-chain a component-produced event into the global chain, keeping its
        original tamper-evident hash as provenance."""
        meta = dict(source.metadata)
        meta["source_hash"] = source.entry_hash
        return self.append(event_id=source.event_id, actor=source.actor, action=source.action,
                           timestamp=source.timestamp, target=source.target, metadata=meta)

    def chain_valid(self) -> bool:
        prev = ""
        for ev in self.events:
            if ev.prev_hash != prev:
                return False
            prev = ev.entry_hash
        return True
