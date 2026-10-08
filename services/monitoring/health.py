"""System health + anomaly snapshot (Phase 13) — FAIL-CLOSED.

Aggregates read-only system state into a frozen MonitoringSnapshot. The integrity
invariant is the audit chain (verified against a sealed commitment when supplied —
review). build_snapshot is wrapped so that ANY error from an input (a raising
audit_log/kill_switch/catalog) yields an UNHEALTHY snapshot (MONITORING_ERROR),
never a crash or a healthy-looking one — monitoring must never fail OPEN.
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
                   rejected_decisions: int = 0, audit_commitment=None) -> MonitoringSnapshot:
    try:
        anomalies = []
        if audit_commitment is not None:
            chain_ok = audit_log.verify_against(audit_commitment)
            if not chain_ok:
                anomalies.append("AUDIT_CHAIN_TAMPERED")     # truncation/replacement/erasure
        else:
            chain_ok = audit_log.chain_valid()
            if not chain_ok:
                anomalies.append("AUDIT_CHAIN_BROKEN")
        ks_active = bool(kill_switch.is_active()) if kill_switch is not None else False
        if ks_active:
            anomalies.append("KILL_SWITCH_ACTIVE")           # operational: halted on purpose
        valid_rd = isinstance(rejected_decisions, int) and rejected_decisions >= 0
        if not valid_rd:
            anomalies.append("REJECTED_DECISIONS_INVALID")
        counts: dict = {}
        if catalog is not None:
            for aid in catalog.all_ids():
                s = catalog.state(aid).value
                counts[s] = counts.get(s, 0) + 1
        return MonitoringSnapshot(
            timestamp=timestamp, audit_chain_valid=chain_ok, kill_switch_active=ks_active,
            alpha_state_counts=counts, rejected_decisions=rejected_decisions if valid_rd else 0,
            anomalies=tuple(anomalies),
        )
    except Exception:
        # fail-closed: a broken input must read as UNHEALTHY, never crash or look clean
        return MonitoringSnapshot(timestamp=timestamp, audit_chain_valid=False,
                                  kill_switch_active=True, alpha_state_counts={},
                                  rejected_decisions=0, anomalies=("MONITORING_ERROR",))
