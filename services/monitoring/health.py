"""System health + anomaly snapshot (Phase 13).

Aggregates read-only system state into a frozen MonitoringSnapshot and flags
anomalies. The integrity invariant is the audit chain: `healthy` is False if the
chain is broken. A tripped kill-switch is surfaced as an operational anomaly
(the system halted on purpose) but is not itself "unhealthy". Pure, deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MonitoringSnapshot:
    timestamp: int
    audit_chain_valid: bool
    kill_switch_active: bool
    alpha_state_counts: dict
    rejected_decisions: int
    anomalies: tuple[str, ...]

    @property
    def healthy(self) -> bool:
        # integrity is the health invariant; a halted kill-switch is intentional
        return self.audit_chain_valid


def build_snapshot(*, timestamp: int, audit_log, kill_switch=None, catalog=None,
                   rejected_decisions: int = 0) -> MonitoringSnapshot:
    anomalies = []
    chain_ok = audit_log.chain_valid()
    if not chain_ok:
        anomalies.append("AUDIT_CHAIN_BROKEN")            # critical: integrity violated
    ks_active = bool(kill_switch.is_active()) if kill_switch is not None else False
    if ks_active:
        anomalies.append("KILL_SWITCH_ACTIVE")            # operational: trading halted
    counts: dict = {}
    if catalog is not None:
        for aid in catalog.all_ids():
            s = catalog.state(aid).value
            counts[s] = counts.get(s, 0) + 1
    return MonitoringSnapshot(
        timestamp=timestamp, audit_chain_valid=chain_ok, kill_switch_active=ks_active,
        alpha_state_counts=counts, rejected_decisions=rejected_decisions,
        anomalies=tuple(anomalies),
    )
