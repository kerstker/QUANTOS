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
from quantos.contracts.alpha import InvalidAlphaTransition
from quantos.contracts.governance import ApprovalRecord, AuditEvent
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import AlphaState

from services.lifecycle import (
    DeploymentState,
    HealthState,
    HealthVerdict,
    InvalidHealthTransition,
    can_health_transition,
    is_deployed,
)

from .config import AlphaFactoryConfig
from . import guards


# 727 C3: how a health-machine verdict maps onto the legacy mixed field when the
# two machines overlap. DEGRADED and PAUSED existed before; FAILED (execution or
# risk broke) is what the legacy PAUSED actually meant coming from the monitor;
# WATCH never existed — it keeps the more severe legacy representation (PAUSED),
# the fail-safe direction. HEALTHY has no entry: recovery is §11.3 human-gated
# and deliberately NOT wired in C3.
_HEALTH_TO_LEGACY = {
    HealthState.WATCH: AlphaState.PAUSED,
    HealthState.DEGRADED: AlphaState.DEGRADED,
    HealthState.FAILED: AlphaState.PAUSED,
}


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
            # Promotion requires BOTH the existing arbitrary-dependence BHY
            # lens and 727 §7.3's family/within-family FDR.  Family accounting
            # is additive: it cannot gift a smaller trial count or bypass BHY.
            bhy_ok = alpha_id in self.catalog.bhy_fdr_survivors(
                self.config.fdr_q
            )
            family_ok = alpha_id in self.catalog.family_fdr_survivors(
                self.config.fdr_q,
                rho_cut=self.config.rho_cut,
                min_overlap=self.config.min_overlap,
            )
            return guards.guard_to_validated(
                evaluation,
                self.config,
                cc,
                bh_survived=bhy_ok,
                family_fdr_survived=family_ok,
            )
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

    def advance_health(
        self,
        alpha_id: str,
        verdict: HealthVerdict,
        actor: Actor,
        *,
        timestamp: int,
        dataset_version: str = "",
    ) -> Optional[AlphaTransition]:
        """727 C3 health-machine entry — the orchestrated way to record that an
        alpha's BEHAVIOUR changed, without touching its research verdict or its
        deployment position (the two things the legacy DEGRADED/PAUSED writes
        used to overwrite).

        Flow: health-edge legality -> legacy write through the existing audited
        advance() (so every unswitched reader and the transition chain see
        exactly what they saw before C3) -> restore the orthogonal dimensions the
        legacy projection clobbered -> append a health-machine audit event.

        De-risk coupling (§3.4 invariant 3): a FAILED verdict while deployed
        drives the deployment dimension to PAUSED — always permitted, §11.2.
        If the legacy edge needed for the overlap write does not exist (e.g.
        legacy PAUSED -> DEGRADED, a recovery the mixed field cannot express),
        the legacy state is LEFT at the more severe value and the health machine
        still records the improvement — that expressiveness gap is the reason
        the three machines exist. Returns the legacy AlphaTransition, or None
        for an idempotent re-observation / a reason-only update / a skipped
        legacy write.
        """
        if not isinstance(verdict, HealthVerdict):
            raise TypeError("verdict must be a HealthVerdict")
        if verdict.state is HealthState.HEALTHY:
            raise ValueError(
                "HEALTHY recovery is not wired in C3: §11.3 recovery is human-gated"
            )
        current = self.catalog.triple(alpha_id)
        cur_health = current.health
        if cur_health == verdict:
            return None                     # idempotent re-observation
        legacy_write = "skipped_not_needed"
        tr = None
        if cur_health.state is verdict.state:
            # Same state, sharper reason: a correction, not a transition. The
            # health table has no self-loops; record it as an audited update.
            self.catalog._set_health(alpha_id, verdict)
            legacy_write = "skipped_same_state"
        else:
            if not can_health_transition(cur_health.state, verdict.state):
                raise InvalidHealthTransition(cur_health.state, verdict.state)
            deployment = current.deployment
            if verdict.forces_de_risk and is_deployed(deployment):
                deployment = DeploymentState.PAUSED
            legacy_dst = _HEALTH_TO_LEGACY[verdict.state]
            if self.catalog.state(alpha_id) is not legacy_dst:
                try:
                    tr = self.advance(
                        alpha_id, legacy_dst, actor,
                        timestamp=timestamp, dataset_version=dataset_version,
                    )
                    legacy_write = legacy_dst.value
                except InvalidAlphaTransition:
                    # The mixed field cannot express this recovery edge; keep the
                    # more severe legacy value and let the health machine tell
                    # the truth. This gap is why the machines were split.
                    legacy_write = "skipped_illegal_legacy_edge"
            self.catalog._set_deployment(alpha_id, deployment)
            self.catalog._set_health(alpha_id, verdict)
        prev_audit = self.audit[-1].entry_hash if self.audit else ""
        self.audit.append(AuditEvent(
            event_id=(
                f"{alpha_id}:health:{cur_health.state.value}"
                f"->{verdict.state.value}:{timestamp}"
            ),
            actor=actor.actor_id,
            action=f"ALPHA_HEALTH_{verdict.state.value}",
            target=alpha_id,
            timestamp=timestamp,
            metadata={
                "machine": "health",
                "src": cur_health.state.value,
                "dst": verdict.state.value,
                "reason": verdict.reason.value,
                "actor_kind": actor.kind.value,
                "deployment": self.catalog.deployment_state(alpha_id).value,
                "legacy_write": legacy_write,
            },
            prev_hash=prev_audit,
        ))
        return tr

    def audit_chain_valid(self) -> bool:
        """Recompute the chain; any tamper breaks prev_hash linkage."""
        prev = ""
        for ev in self.audit:
            if ev.prev_hash != prev:
                return False
            prev = ev.entry_hash
        return True
