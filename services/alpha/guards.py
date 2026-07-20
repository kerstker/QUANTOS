"""Lifecycle transition guards (Phase 6, step 10a).

Pure (ok, reasons) functions over already-computed evidence — one per forward
transition. The lifecycle manager runs the matching guard AFTER the structural
state-machine check. De-risking transitions (REJECTED/RETIRED) need no guard.
"""
from __future__ import annotations

import math
from typing import Optional

from quantos.contracts.alpha import Actor, AlphaEvaluation
from quantos.contracts.governance import ApprovalRecord
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.governance.approval import ApprovalRequest, ApprovalState, verify_multi_party_grant
from services.validation.catalog_evidence import verify_catalog_evidence
from services.validation.pbo_evidence import verify_batch_pbo_evidence

from .config import AlphaFactoryConfig

Reasons = tuple[str, ...]

# --- 多方批准 grant 的 subject 绑定(战线①,2026-07-06)------------------------
# grant 一次性绑定「哪个 alpha + 晋到哪个状态」:给别的 alpha / 别的目标状态签的批准花不到
# 这里。格式单一真源在此(guards 是证据门的家;promotion.py 复用,避免与 lifecycle 成环)。

PROMOTION_SUBJECT_PREFIX = "alpha-promotion"


def promotion_subject(alpha_id: str, dst: AlphaState) -> str:
    return f"{PROMOTION_SUBJECT_PREFIX}:{alpha_id}:{dst.value}"


def parse_promotion_subject(subject: str) -> tuple[str, AlphaState]:
    """解析失败 raise ValueError(fail-closed:格式不对的 subject 绝不猜)。"""
    parts = subject.split(":")
    if len(parts) < 3 or parts[0] != PROMOTION_SUBJECT_PREFIX:
        raise ValueError(f"not a promotion subject: {subject!r}")
    alpha_id = ":".join(parts[1:-1])
    if not alpha_id:
        raise ValueError(f"promotion subject missing alpha_id: {subject!r}")
    return alpha_id, AlphaState(parts[-1])   # 非法状态名 → ValueError


def grant_approver(grant: ApprovalRequest) -> Optional[str]:
    """grant 中 APPROVED 步的 actor_id(执行人必须是它——第三人执行自己的批准,别人不能代花)。"""
    for s in grant.steps:
        if s.state == ApprovalState.APPROVED:
            return s.actor_id
    return None


def guard_to_testing(evaluation, config, catalog_count) -> tuple[bool, Reasons]:
    return True, ()   # registration is enough to begin testing


def _pbo_provenance_reasons(
    evaluation: AlphaEvaluation,
    catalog_trial_set_hash: str,
) -> list[str]:
    evidence = evaluation.pbo_evidence
    if evidence is None:
        return ["PBO_EVIDENCE_UNBOUND"]
    reasons = []
    if (
        not verify_batch_pbo_evidence(evidence)
        or evaluation.pbo_evidence_hash != evidence.evidence_hash
        or not math.isclose(evaluation.pbo, evidence.pbo, rel_tol=0.0, abs_tol=1e-12)
    ):
        reasons.append("PBO_EVIDENCE_INVALID")
    if (
        evaluation.catalog_evidence is None
        or not set(evidence.candidate_definition_hashes)
        <= set(evaluation.catalog_evidence.trial_definition_hashes)
    ):
        reasons.append("PBO_EVIDENCE_TRIAL_SET_MISMATCH")
    if evaluation.catalog_trial_set_hash != catalog_trial_set_hash:
        reasons.append("TRIAL_SET_MISMATCH")
    return reasons


def _catalog_provenance_reasons(
    evaluation: AlphaEvaluation,
    catalog_trial_set_hash: str,
    *,
    catalog_evidence_matches: bool,
) -> list[str]:
    evidence = evaluation.catalog_evidence
    if evidence is None:
        return ["CATALOG_EVIDENCE_UNBOUND"]
    reasons = []
    if (
        not verify_catalog_evidence(evidence)
        or evaluation.catalog_evidence_hash != evidence.evidence_hash
        or not math.isclose(
            evaluation.deflated_sharpe,
            evidence.deflated_sharpe,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        or evaluation.catalog_trial_count != evidence.trial_count
        or evaluation.catalog_trial_set_hash
        != evidence.catalog_trial_set_hash
    ):
        reasons.append("CATALOG_EVIDENCE_INVALID")
    if evaluation.catalog_trial_set_hash != catalog_trial_set_hash:
        reasons.append("TRIAL_SET_MISMATCH")
    if not catalog_evidence_matches:
        reasons.append("CATALOG_SNAPSHOT_MISMATCH")
    return reasons


def guard_to_validated(evaluation: Optional[AlphaEvaluation], config: AlphaFactoryConfig,
                       catalog_count: int, catalog_trial_set_hash: str,
                       bh_survived: bool = True,
                       catalog_evidence_matches: bool = False) -> tuple[bool, Reasons]:
    if evaluation is None:
        return False, ("NO_EVALUATION",)
    reasons = []
    if evaluation.verdict != ValidationVerdict.PASS:        # PASS only (WARN/FAIL/REVIEW do not advance)
        reasons.append(f"VERDICT_{evaluation.verdict.value}")
    if evaluation.deflated_sharpe < config.min_deflated_sharpe:
        reasons.append("DEFLATED_SHARPE_BELOW_MIN")
    if evaluation.pbo > config.max_pbo:
        reasons.append("PBO_TOO_HIGH")
    reasons.extend(
        _catalog_provenance_reasons(
            evaluation,
            catalog_trial_set_hash,
            catalog_evidence_matches=catalog_evidence_matches,
        )
    )
    reasons.extend(_pbo_provenance_reasons(evaluation, catalog_trial_set_hash))
    # the DSR was computed against the catalog count at eval time; if the catalog
    # has since grown, the correction is understated -> reject (critique test #3)
    if evaluation.catalog_trial_count < catalog_count:
        reasons.append("TRIAL_COUNT_UNDERSTATED")
    # an edge must clear BOTH DSR and the catalog-wide BH-FDR (review P0): if the
    # alpha has an IC p-value it must survive the catalog FDR
    if evaluation.ic_pvalue is not None and not bh_survived:
        reasons.append("FDR_NOT_SURVIVED")
    return (not reasons, tuple(reasons))


def guard_to_paper(evaluation: Optional[AlphaEvaluation], config: AlphaFactoryConfig,
                   catalog_count: int, catalog_trial_set_hash: str,
                   catalog_evidence_matches: bool = False) -> tuple[bool, Reasons]:
    if evaluation is None:
        return False, ("NO_EVALUATION",)
    reasons = []
    if evaluation.verdict != ValidationVerdict.PASS:
        reasons.append(f"VERDICT_{evaluation.verdict.value}")
    if evaluation.marginal_ir is not None and evaluation.marginal_ir <= 0.0:
        reasons.append("NO_MARGINAL_EDGE")
    reasons.extend(
        _catalog_provenance_reasons(
            evaluation,
            catalog_trial_set_hash,
            catalog_evidence_matches=catalog_evidence_matches,
        )
    )
    reasons.extend(_pbo_provenance_reasons(evaluation, catalog_trial_set_hash))
    return (not reasons, tuple(reasons))


def guard_to_approved(alpha_id: str, approval: Optional[ApprovalRecord], actor: Actor,
                      paper_evidence: Optional[dict], config: AlphaFactoryConfig,
                      eval_trial_count: Optional[int] = None,
                      catalog_count: Optional[int] = None,
                      eval_trial_set_hash: Optional[str] = None,
                      catalog_trial_set_hash: Optional[str] = None,
                      eval_catalog_evidence_hash: Optional[str] = None,
                      catalog_evidence_hash: Optional[str] = None) -> tuple[bool, Reasons]:
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
    if eval_trial_set_hash is None or catalog_trial_set_hash is None:
        reasons.append("NO_EVALUATION_TRIAL_SET_HASH")
    elif eval_trial_set_hash != catalog_trial_set_hash:
        reasons.append("TRIAL_SET_MISMATCH_AT_APPROVAL")
    if eval_catalog_evidence_hash is None or catalog_evidence_hash is None:
        reasons.append("NO_CATALOG_EVIDENCE_HASH")
    elif eval_catalog_evidence_hash != catalog_evidence_hash:
        reasons.append("CATALOG_SNAPSHOT_MISMATCH_AT_APPROVAL")
    if paper_evidence is None:
        reasons.append("NO_PAPER_EVIDENCE")
    else:
        bt, pp = paper_evidence.get("backtest_sharpe"), paper_evidence.get("paper_sharpe")
        if bt is None or pp is None or bt <= 0:
            reasons.append("PAPER_EVIDENCE_INCOMPLETE")
        elif (bt - pp) / bt > config.max_perf_gap:
            reasons.append("PAPER_INCONSISTENT_WITH_BACKTEST")
    return (not reasons, tuple(reasons))


def guard_to_approved_with_grant(alpha_id: str, grant: Optional[ApprovalRequest], actor: Actor,
                                 paper_evidence: Optional[dict], config: AlphaFactoryConfig,
                                 eval_trial_count: Optional[int] = None,
                                 catalog_count: Optional[int] = None,
                                 eval_trial_set_hash: Optional[str] = None,
                                 catalog_trial_set_hash: Optional[str] = None,
                                 eval_catalog_evidence_hash: Optional[str] = None,
                                 catalog_evidence_hash: Optional[str] = None) -> tuple[bool, Reasons]:
    """多方批准(三个不同真人 + 哈希链,approval.py)版的 APPROVED 证据门(战线①)。

    多方 grant 替代的只是单人 ApprovalRecord 那一项——其余证据门与 guard_to_approved 同强度,
    一寸不松;且有一处刻意更严(评审 #1):持久化世界里每次执行都是新进程、manager 的进程内
    eval 计数恒空,所以 eval_trial_count 必须来自该 alpha 的 evaluation artifact,取不到就
    fail-closed 拒绝(NO_EVALUATION_TRIAL_COUNT),绝不像旧路径那样在 None 时静默跳过。"""
    reasons = []
    if not actor.is_human:
        reasons.append("APPROVER_NOT_HUMAN")
    if grant is None:
        reasons.append("NO_MULTI_PARTY_GRANT")
    else:
        # fail-closed 校验器:CONSUMED ∧ subject 绑定本 alpha+APPROVED ∧ ≥3 不同真人 ∧ 链自洽
        if not verify_multi_party_grant(grant, promotion_subject(alpha_id, AlphaState.APPROVED)):
            reasons.append("MULTI_PARTY_GRANT_INVALID")
        approver = grant_approver(grant)
        if approver is None or approver != actor.actor_id:   # 执行人必须是 APPROVED 步那个人
            reasons.append("APPROVER_IDENTITY_MISMATCH")
    if eval_trial_count is None or catalog_count is None:
        reasons.append("NO_EVALUATION_TRIAL_COUNT")
    elif eval_trial_count < catalog_count:
        reasons.append("TRIAL_COUNT_UNDERSTATED_AT_APPROVAL")
    if eval_trial_set_hash is None or catalog_trial_set_hash is None:
        reasons.append("NO_EVALUATION_TRIAL_SET_HASH")
    elif eval_trial_set_hash != catalog_trial_set_hash:
        reasons.append("TRIAL_SET_MISMATCH_AT_APPROVAL")
    if eval_catalog_evidence_hash is None or catalog_evidence_hash is None:
        reasons.append("NO_CATALOG_EVIDENCE_HASH")
    elif eval_catalog_evidence_hash != catalog_evidence_hash:
        reasons.append("CATALOG_SNAPSHOT_MISMATCH_AT_APPROVAL")
    if paper_evidence is None:
        reasons.append("NO_PAPER_EVIDENCE")
    else:
        bt, pp = paper_evidence.get("backtest_sharpe"), paper_evidence.get("paper_sharpe")
        if bt is None or pp is None or bt <= 0:
            reasons.append("PAPER_EVIDENCE_INCOMPLETE")
        elif (bt - pp) / bt > config.max_perf_gap:
            reasons.append("PAPER_INCONSISTENT_WITH_BACKTEST")
    return (not reasons, tuple(reasons))
