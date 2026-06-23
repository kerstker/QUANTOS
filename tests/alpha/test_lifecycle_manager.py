"""Phase 6 step 10 — lifecycle manager: only-PASS advances, agent can never
self-approve, human-token + paper-consistency gate APPROVED, audit chain."""
import pytest

from quantos.contracts.alpha import (
    Actor, ActorKind, AlphaDefinition, UnauthorizedPromotion,
)
from quantos.contracts.governance import ApprovalRecord
from quantos.contracts.research import AlphaState, ValidationVerdict
from quantos.contracts.alpha import AlphaEvaluation
from services.alpha.catalog import AlphaCatalog
from services.alpha.lifecycle import AlphaLifecycleManager, GuardRejected

HUMAN = Actor(actor_id="alice", kind=ActorKind.HUMAN)
AGENT = Actor(actor_id="research-agent", kind=ActorKind.AGENT)
S = AlphaState
GOOD_PAPER = {"backtest_sharpe": 1.1, "paper_sharpe": 1.0}


def _eval(alpha_id, trial_count, verdict=ValidationVerdict.PASS, dsr=0.8, pbo=0.1, mir=0.5):
    return AlphaEvaluation(alpha_id=alpha_id, verdict=verdict, deflated_sharpe=dsr, pbo=pbo,
                           catalog_trial_count=trial_count, marginal_ir=mir)


def _mgr():
    cat = AlphaCatalog()
    cat.register(AlphaDefinition(alpha_id="a1", name="A", factor_ids=("f1",)))
    return AlphaLifecycleManager(cat), cat


def _to_paper(mgr, cat, t0=1):
    cc = cat.global_trial_count()
    mgr.advance("a1", S.TESTING, AGENT, timestamp=t0)
    mgr.advance("a1", S.VALIDATED, AGENT, timestamp=t0 + 1, evaluation=_eval("a1", cc))
    mgr.advance("a1", S.PAPER_TRADING, AGENT, timestamp=t0 + 2, evaluation=_eval("a1", cc))


# --- only PASS advances ------------------------------------------------------

def test_only_pass_advances_to_validated():
    mgr, cat = _mgr()
    mgr.advance("a1", S.TESTING, AGENT, timestamp=1)
    for bad in (ValidationVerdict.WARN, ValidationVerdict.FAIL, ValidationVerdict.REQUIRES_HUMAN_REVIEW):
        with pytest.raises(GuardRejected):
            mgr.advance("a1", S.VALIDATED, AGENT, timestamp=2, evaluation=_eval("a1", 1, verdict=bad))
    mgr.advance("a1", S.VALIDATED, AGENT, timestamp=3, evaluation=_eval("a1", 1))
    assert cat.state("a1") == S.VALIDATED


def test_understated_trial_count_is_rejected():
    mgr, cat = _mgr()
    for k in range(50):
        cat.register_trial(f"c{k}")           # catalog grew to 51 after registration
    mgr.advance("a1", S.TESTING, AGENT, timestamp=1)
    with pytest.raises(GuardRejected):         # eval computed at trial_count=1
        mgr.advance("a1", S.VALIDATED, AGENT, timestamp=2, evaluation=_eval("a1", 1))


# --- APPROVED is human-only --------------------------------------------------

def test_agent_cannot_self_approve_even_with_perfect_evidence():
    mgr, cat = _mgr()
    _to_paper(mgr, cat)
    approval = ApprovalRecord(approval_id="ap1", subject_id="a1", approver="alice",
                              approved=True, timestamp=9)
    with pytest.raises(UnauthorizedPromotion):
        mgr.advance("a1", S.APPROVED, AGENT, timestamp=10, approval=approval, paper_evidence=GOOD_PAPER)


def test_approved_requires_human_token_and_paper_consistency():
    mgr, cat = _mgr()
    _to_paper(mgr, cat)
    # no token
    with pytest.raises(GuardRejected):
        mgr.advance("a1", S.APPROVED, HUMAN, timestamp=10, paper_evidence=GOOD_PAPER)
    # token not granted
    with pytest.raises(GuardRejected):
        mgr.advance("a1", S.APPROVED, HUMAN, timestamp=11, paper_evidence=GOOD_PAPER,
                    approval=ApprovalRecord(approval_id="x", subject_id="a1", approver="alice",
                                            approved=False, timestamp=11))
    # subject mismatch
    with pytest.raises(GuardRejected):
        mgr.advance("a1", S.APPROVED, HUMAN, timestamp=12, paper_evidence=GOOD_PAPER,
                    approval=ApprovalRecord(approval_id="y", subject_id="other", approver="alice",
                                            approved=True, timestamp=12))
    # valid token + consistent paper -> success
    ok = ApprovalRecord(approval_id="z", subject_id="a1", approver="alice", approved=True, timestamp=13)
    tr = mgr.advance("a1", S.APPROVED, HUMAN, timestamp=13, approval=ok, paper_evidence=GOOD_PAPER)
    assert cat.state("a1") == S.APPROVED and tr.approval_id == "z"


def test_paper_inconsistency_blocks_approval():
    mgr, cat = _mgr()
    _to_paper(mgr, cat)
    ok = ApprovalRecord(approval_id="z", subject_id="a1", approver="alice", approved=True, timestamp=13)
    bad_paper = {"backtest_sharpe": 2.0, "paper_sharpe": 0.1}   # collapsed in paper
    with pytest.raises(GuardRejected):
        mgr.advance("a1", S.APPROVED, HUMAN, timestamp=13, approval=ok, paper_evidence=bad_paper)


# --- audit chain -------------------------------------------------------------

def test_every_transition_is_audited_and_chained():
    mgr, cat = _mgr()
    _to_paper(mgr, cat)
    assert len(mgr.audit) == 3 and len(mgr.transitions) == 3
    assert mgr.audit_chain_valid()
    # chain linkage
    assert mgr.audit[1].prev_hash == mgr.audit[0].entry_hash
    assert mgr.transitions[2].prev_hash == mgr.transitions[1].entry_hash


def test_agent_may_de_risk_to_retired():
    mgr, cat = _mgr()
    _to_paper(mgr, cat)
    tr = mgr.advance("a1", S.RETIRED, AGENT, timestamp=20)   # de-risking allowed
    assert cat.state("a1") == S.RETIRED and tr.dst_state == S.RETIRED


def test_human_cannot_spend_another_humans_token():
    mgr, cat = _mgr()
    _to_paper(mgr, cat)
    alice_token = ApprovalRecord(approval_id="ap", subject_id="a1", approver="alice",
                                 approved=True, timestamp=9)
    bob = Actor(actor_id="bob", kind=ActorKind.HUMAN)        # different human
    with pytest.raises(GuardRejected):                       # APPROVER_IDENTITY_MISMATCH
        mgr.advance("a1", S.APPROVED, bob, timestamp=10, approval=alice_token, paper_evidence=GOOD_PAPER)


def test_catalog_growth_after_paper_blocks_approval():
    mgr, cat = _mgr()
    _to_paper(mgr, cat)                                      # eval captured at trial_count=1
    for k in range(5):
        cat.register_trial(f"c{k}")                          # catalog grows -> DSR now understated
    ok = ApprovalRecord(approval_id="z", subject_id="a1", approver="alice", approved=True, timestamp=13)
    with pytest.raises(GuardRejected):                       # TRIAL_COUNT_UNDERSTATED_AT_APPROVAL
        mgr.advance("a1", S.APPROVED, HUMAN, timestamp=13, approval=ok, paper_evidence=GOOD_PAPER)
