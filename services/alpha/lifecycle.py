"""Alpha lifecycle manager (Phase 6, step 10b) — the single transition chokepoint.

Every state change goes through advance():
  1. structural lock: assert_alpha_transition (legality + non-human cannot drive
     to APPROVED -> UnauthorizedPromotion);
  2. evidence guard for the target state;
  3. commit: update catalog state, append a hash-chained AuditEvent AND an
     immutable AlphaTransition.

The manager NEVER constructs an ApprovalRecord — it only verifies one supplied by
the caller. This, plus the actor-kind check, makes self-approval impossible:
an agent cannot drive ->APPROVED no matter what token it forges.
"""
from __future__ import annotations

from typing import Optional

from quantos.contracts.alpha import (
    Actor, AlphaTransition, assert_alpha_transition,
)
from quantos.contracts.governance import ApprovalRecord, AuditEvent
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import AlphaState

from .config import AlphaFactoryConfig
from . import guards


class GuardRejected(ValueError):
    def __init__(self, reasons):
        super().__init__("; ".join(reasons) or "guard rejected")
        self.reasons = tuple(reasons)


class AlphaLifecycleManager:
    def __init__(self, catalog, config: Optional[AlphaFactoryConfig] = None):
        self.catalog = catalog
        self.config = config or AlphaFactoryConfig()
        self.transitions: list[AlphaTransition] = []
        self.audit: list[AuditEvent] = []

    def advance(
        self,
        alpha_id: str,
        dst: AlphaState,
        actor: Actor,
        *,
        timestamp: int,
        evaluation=None,
        approval: Optional[ApprovalRecord] = None,
        paper_evidence: Optional[dict] = None,
        dataset_version: str = "",
    ) -> AlphaTransition:
        src = self.catalog.state(alpha_id)

        # 1) structural lock — raises InvalidAlphaTransition / UnauthorizedPromotion
        assert_alpha_transition(src, dst, actor)

        # 2) evidence guard
        ok, reasons = self._run_guard(alpha_id, dst, actor, evaluation, approval, paper_evidence)
        if not ok:
            raise GuardRejected(reasons)

        # 3) commit + tamper-evident audit
        self.catalog._set_state(alpha_id, dst)
        evidence_hash = stable_hash({
            "alpha_id": alpha_id, "src": src.value, "dst": dst.value,
            "eval": getattr(evaluation, "reproducibility_hash", ""),
            "approval": approval.approval_id if approval else "",
        })
        prev_audit = self.audit[-1].entry_hash if self.audit else ""
        self.audit.append(AuditEvent(
            event_id=f"{alpha_id}:{src.value}->{dst.value}:{timestamp}",
            actor=actor.actor_id, action=f"ALPHA_TRANSITION_{dst.value}", target=alpha_id,
            timestamp=timestamp,
            metadata={"src": src.value, "dst": dst.value, "actor_kind": actor.kind.value},
            prev_hash=prev_audit,
        ))
        prev_tr = self.transitions[-1].entry_hash if self.transitions else ""
        tr = AlphaTransition(
            transition_id=f"t{len(self.transitions) + 1}", alpha_id=alpha_id,
            src_state=src, dst_state=dst, actor=actor, evidence_hash=evidence_hash,
            approval_id=approval.approval_id if approval else "",
            dataset_version=dataset_version, timestamp=timestamp, prev_hash=prev_tr,
        )
        self.transitions.append(tr)
        return tr

    def _run_guard(self, alpha_id, dst, actor, evaluation, approval, paper_evidence):
        cc = self.catalog.global_trial_count()
        if dst == AlphaState.TESTING:
            return guards.guard_to_testing(evaluation, self.config, cc)
        if dst == AlphaState.VALIDATED:
            return guards.guard_to_validated(evaluation, self.config, cc)
        if dst == AlphaState.PAPER_TRADING:
            return guards.guard_to_paper(evaluation, self.config, cc)
        if dst == AlphaState.APPROVED:
            return guards.guard_to_approved(alpha_id, approval, actor, paper_evidence, self.config)
        if dst in (AlphaState.REJECTED, AlphaState.RETIRED):
            return True, ()      # rejection / de-risking is always permitted
        return False, ("UNKNOWN_TRANSITION",)

    def audit_chain_valid(self) -> bool:
        """Recompute the chain; any tamper breaks prev_hash linkage."""
        prev = ""
        for ev in self.audit:
            if ev.prev_hash != prev:
                return False
            prev = ev.entry_hash
        return True
