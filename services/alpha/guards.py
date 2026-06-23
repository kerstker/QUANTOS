"""Lifecycle transition guards (Phase 6, step 10a).

Pure (ok, reasons) functions over already-computed evidence — one per forward
transition. The lifecycle manager runs the matching guard AFTER the structural
state-machine check. De-risking transitions (REJECTED/RETIRED) need no guard.
"""
from __future__ import annotations

from typing import Optional

from quantos.contracts.alpha import Actor, AlphaEvaluation
from quantos.contracts.governance import ApprovalRecord
from quantos.contracts.research import ValidationVerdict

from .config import AlphaFactoryConfig

Reasons = tuple[str, ...]


def guard_to_testing(evaluation, config, catalog_count) -> tuple[bool, Reasons]:
    return True, ()   # registration is enough to begin testing


def guard_to_validated(evaluation: Optional[AlphaEvaluation], config: AlphaFactoryConfig,
                       catalog_count: int) -> tuple[bool, Reasons]:
    if evaluation is None:
        return False, ("NO_EVALUATION",)
    reasons = []
    if evaluation.verdict != ValidationVerdict.PASS:        # PASS only (WARN/FAIL/REVIEW do not advance)
        reasons.append(f"VERDICT_{evaluation.verdict.value}")
    if evaluation.deflated_sharpe < config.min_deflated_sharpe:
        reasons.append("DEFLATED_SHARPE_BELOW_MIN")
    if evaluation.pbo > config.max_pbo:
        reasons.append("PBO_TOO_HIGH")
    # the DSR was computed against the catalog count at eval time; if the catalog
    # has since grown, the correction is understated -> reject (critique test #3)
    if evaluation.catalog_trial_count < catalog_count:
        reasons.append("TRIAL_COUNT_UNDERSTATED")
    return (not reasons, tuple(reasons))


def guard_to_paper(evaluation: Optional[AlphaEvaluation], config: AlphaFactoryConfig,
                   catalog_count: int) -> tuple[bool, Reasons]:
    if evaluation is None:
        return False, ("NO_EVALUATION",)
    reasons = []
    if evaluation.verdict != ValidationVerdict.PASS:
        reasons.append(f"VERDICT_{evaluation.verdict.value}")
    if evaluation.marginal_ir is not None and evaluation.marginal_ir <= 0.0:
        reasons.append("NO_MARGINAL_EDGE")
    return (not reasons, tuple(reasons))


def guard_to_approved(alpha_id: str, approval: Optional[ApprovalRecord], actor: Actor,
                      paper_evidence: Optional[dict], config: AlphaFactoryConfig,
                      eval_trial_count: Optional[int] = None,
                      catalog_count: Optional[int] = None) -> tuple[bool, Reasons]:
    """APPROVED is human-only AND evidence-gated. The actor.kind is the verifiable
    human check the ApprovalRecord contract alone cannot provide (critique P0-3);
    the approver-identity check stops one human from spending another's token."""
    reasons = []
    if not actor.is_human:
        reasons.append("APPROVER_NOT_HUMAN")
    if approval is None:
        reasons.append("NO_HUMAN_APPROVAL_TOKEN")
    else:
        if not approval.approved:
            reasons.append("APPROVAL_NOT_GRANTED")
        if approval.subject_id != alpha_id:
            reasons.append("APPROVAL_SUBJECT_MISMATCH")
        if approval.approver != actor.actor_id:          # the executor must own the token (review P0)
            reasons.append("APPROVER_IDENTITY_MISMATCH")
    # the catalog may have grown since this alpha was evaluated -> its DSR is now
    # understated; refuse approval on a stale trial count (review P1)
    if eval_trial_count is not None and catalog_count is not None and eval_trial_count < catalog_count:
        reasons.append("TRIAL_COUNT_UNDERSTATED_AT_APPROVAL")
    if paper_evidence is None:
        reasons.append("NO_PAPER_EVIDENCE")
    else:
        bt, pp = paper_evidence.get("backtest_sharpe"), paper_evidence.get("paper_sharpe")
        if bt is None or pp is None or bt <= 0:
            reasons.append("PAPER_EVIDENCE_INCOMPLETE")
        elif (bt - pp) / bt > config.max_perf_gap:
            reasons.append("PAPER_INCONSISTENT_WITH_BACKTEST")
    return (not reasons, tuple(reasons))
