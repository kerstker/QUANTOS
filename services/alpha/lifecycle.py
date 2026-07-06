"""Alpha lifecycle manager (Phase 6, step 10b) — the single transition chokepoint.

Every state change goes through advance():
  1. structural lock: assert_alpha_transition (legality + non-human cannot drive
     to APPROVED -> UnauthorizedPromotion);
  2. evidence guard for the target state;
  3. commit: update catalog state, append a hash-chained AuditEvent AND an
     immutable AlphaTransition.

The manager NEVER constructs an ApprovalRecord — it only verifies one supplied by
the caller. Combined with the actor-kind check, a non-human ACTOR cannot drive
->APPROVED (assert_alpha_transition raises UnauthorizedPromotion before any guard).

THREAT MODEL (be precise — see CLAUDE.md): this is a RUNTIME control over the
actor passed in. In a single process, trusted code that constructs a HUMAN Actor
or calls catalog._set_state directly can bypass the chokepoint — that is a
build-time concern governed by code review, the governance scanner, and (Phase 14)
deployment isolation, NOT by this runtime check. The autonomous agent path
(services/intelligence) only ever holds a non-human actor and cannot self-approve.
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
        self._eval_trial_count: dict[str, int] = {}   # last-seen eval trial count per alpha

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
        grant=None,                      # 多方批准聚合(services.governance.approval.ApprovalRequest)
        eval_trial_count: Optional[int] = None,   # grant 路径:来自 evaluation artifact 的 eval 期 trial count
    ) -> AlphaTransition:
        # 战线①(2026-07-06)加法入口:dst==APPROVED 且传入 grant 时走多方批准 guard
        # (guards.guard_to_approved_with_grant,镜像 KillSwitch.reset_with_grant /
        # evaluate_deployment(grant=) 先例);grant is None → 旧 ApprovalRecord 路径一字不动。
        src = self.catalog.state(alpha_id)

        # 1) structural lock — raises InvalidAlphaTransition / UnauthorizedPromotion
        assert_alpha_transition(src, dst, actor)

        # 2) evidence guard
        ok, reasons = self._run_guard(alpha_id, dst, actor, evaluation, approval, paper_evidence,
                                      grant=grant, eval_trial_count=eval_trial_count)
        if not ok:
            raise GuardRejected(reasons)

        # 3) commit + tamper-evident audit
        if evaluation is not None:
            self._eval_trial_count[alpha_id] = evaluation.catalog_trial_count
        self.catalog._set_state(alpha_id, dst)
        # grant 可追溯性(评审 #2):grant 路径下把 grant.request_id 记进 evidence_hash 与
        # AlphaTransition.approval_id,使 APPROVED 转换在转换链上与其三方批准连着;旧路径不变。
        approval_ref = approval.approval_id if approval else (
            getattr(grant, "request_id", "") if grant is not None else "")
        evidence_hash = stable_hash({
            "alpha_id": alpha_id, "src": src.value, "dst": dst.value,
            "eval": getattr(evaluation, "reproducibility_hash", ""),
            "approval": approval_ref,
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
            approval_id=approval_ref,
            dataset_version=dataset_version, timestamp=timestamp, prev_hash=prev_tr,
        )
        self.transitions.append(tr)
        return tr

    def _run_guard(self, alpha_id, dst, actor, evaluation, approval, paper_evidence,
                   grant=None, eval_trial_count=None):
        cc = self.catalog.global_trial_count()
        if dst == AlphaState.TESTING:
            return guards.guard_to_testing(evaluation, self.config, cc)
        if dst == AlphaState.VALIDATED:
            # Rank #12: promotion requires surviving the arbitrary-dependence BHY FDR (stricter than
            # plain BH — correct when the catalog's edges are correlated), not just BH.
            fdr_ok = alpha_id in self.catalog.bhy_fdr_survivors(self.config.fdr_q)
            return guards.guard_to_validated(evaluation, self.config, cc, bh_survived=fdr_ok)
        if dst == AlphaState.PAPER_TRADING:
            return guards.guard_to_paper(evaluation, self.config, cc)
        if dst == AlphaState.APPROVED:
            if grant is not None:
                # 多方批准路径:eval trial count 优先取调用方从 evaluation artifact 显式喂入的值
                # (评审 #1——新进程里 self._eval_trial_count 恒空,靠它就结构性失效);两者都
                # 没有 → guard 内 fail-closed 拒绝(NO_EVALUATION_TRIAL_COUNT)。
                etc = eval_trial_count if eval_trial_count is not None \
                    else self._eval_trial_count.get(alpha_id)
                return guards.guard_to_approved_with_grant(
                    alpha_id, grant, actor, paper_evidence, self.config,
                    eval_trial_count=etc, catalog_count=cc)
            return guards.guard_to_approved(
                alpha_id, approval, actor, paper_evidence, self.config,
                eval_trial_count=self._eval_trial_count.get(alpha_id), catalog_count=cc)
        if dst in (AlphaState.REJECTED, AlphaState.RETIRED):
            return True, ()      # rejection / de-risking is always permitted
        if dst in (AlphaState.SHADOW, AlphaState.DEGRADED, AlphaState.PAUSED):
            # dry-run observation (SHADOW) and de-risking (DEGRADED / PAUSED) grant NO live authority —
            # the only live gate is APPROVED (human-only, handled above) — so these are always permitted
            return True, ()
        return False, ("UNKNOWN_TRANSITION",)

    def audit_chain_valid(self) -> bool:
        """Recompute the chain; any tamper breaks prev_hash linkage."""
        prev = ""
        for ev in self.audit:
            if ev.prev_hash != prev:
                return False
            prev = ev.entry_hash
        return True
