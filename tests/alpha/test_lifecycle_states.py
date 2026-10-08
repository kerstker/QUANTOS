"""Phase 6 — the lifecycle gains dry-run MONITORING states SHADOW / DEGRADED / PAUSED. These are
research/dry-run only and grant NO live authority; the sacred invariant is unchanged: APPROVED stays
human-only and NO new path lets a non-human actor reach it. live/small_live/production are deliberately
NOT added (that is the L3 boundary, human-only, not built here)."""
import pytest

from quantos.contracts.alpha import (Actor, ActorKind, AlphaDefinition, UnauthorizedPromotion,
                                      AGENT_REACHABLE, can_alpha_transition)
from quantos.contracts.governance import ApprovalRecord
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.lifecycle import AlphaLifecycleManager
from tests.alpha.fixtures import bound_alpha_evaluation, seed_catalog_evidence

HUMAN = Actor(actor_id="alice", kind=ActorKind.HUMAN)
AGENT = Actor(actor_id="research-agent", kind=ActorKind.AGENT)
S = AlphaState
GOOD_PAPER = {"backtest_sharpe": 1.1, "paper_sharpe": 1.0}


def _eval(catalog, aid, tc):
    return bound_alpha_evaluation(catalog, aid, catalog_trial_count=tc)


def _to_paper(mgr, cat):
    cc = cat.global_trial_count()
    mgr.advance("a1", S.TESTING, AGENT, timestamp=1)
    mgr.advance("a1", S.VALIDATED, AGENT, timestamp=2, evaluation=_eval(cat, "a1", cc))
    mgr.advance("a1", S.PAPER_TRADING, AGENT, timestamp=3, evaluation=_eval(cat, "a1", cc))


def _mgr():
    cat = AlphaCatalog()
    cat.register(AlphaDefinition(alpha_id="a1", name="A", factor_ids=("f1",)))
    seed_catalog_evidence(cat, "a1")
    return AlphaLifecycleManager(cat), cat


def test_new_states_exist_and_are_agent_reachable_but_approved_is_not():
    for s in (S.SHADOW, S.DEGRADED, S.PAUSED):
        assert s in AlphaState and s in AGENT_REACHABLE
    assert S.APPROVED not in AGENT_REACHABLE          # the sacred invariant


def test_agent_can_move_into_monitoring_states():
    mgr, cat = _mgr()
    _to_paper(mgr, cat)
    mgr.advance("a1", S.SHADOW, AGENT, timestamp=4)            # dry-run deeper observation
    assert cat.state("a1") == S.SHADOW
    mgr.advance("a1", S.DEGRADED, AGENT, timestamp=5)          # flag decay
    assert cat.state("a1") == S.DEGRADED
    mgr.advance("a1", S.PAUSED, AGENT, timestamp=6)            # reversible halt
    assert cat.state("a1") == S.PAUSED
    mgr.advance("a1", S.SHADOW, AGENT, timestamp=7)            # resume to observation
    assert cat.state("a1") == S.SHADOW


def test_agent_cannot_reach_approved_via_any_new_path():
    # CONTAINMENT: shadow/paused are agent-reachable, but ->APPROVED from them is STILL human-only.
    for via in (S.SHADOW, S.PAUSED):
        mgr, cat = _mgr()
        _to_paper(mgr, cat)
        mgr.advance("a1", via, AGENT, timestamp=4)
        approval = ApprovalRecord(approval_id="ap", subject_id="a1", approver="alice",
                                  approved=True, timestamp=9)
        with pytest.raises(UnauthorizedPromotion):
            mgr.advance("a1", S.APPROVED, AGENT, timestamp=10, approval=approval,
                        paper_evidence=GOOD_PAPER)


def test_human_can_approve_from_shadow_with_token():
    mgr, cat = _mgr()
    _to_paper(mgr, cat)
    mgr.advance("a1", S.SHADOW, AGENT, timestamp=4)
    ok = ApprovalRecord(approval_id="z", subject_id="a1", approver="alice", approved=True, timestamp=13)
    mgr.advance("a1", S.APPROVED, HUMAN, timestamp=13, approval=ok, paper_evidence=GOOD_PAPER)
    assert cat.state("a1") == S.APPROVED


def test_approved_can_be_de_risked_to_degraded_or_paused():
    mgr, cat = _mgr()
    _to_paper(mgr, cat)
    mgr.advance("a1", S.SHADOW, AGENT, timestamp=4)
    ok = ApprovalRecord(approval_id="z", subject_id="a1", approver="alice", approved=True, timestamp=13)
    mgr.advance("a1", S.APPROVED, HUMAN, timestamp=13, approval=ok, paper_evidence=GOOD_PAPER)
    mgr.advance("a1", S.DEGRADED, AGENT, timestamp=14)         # monitor flags live decay
    assert cat.state("a1") == S.DEGRADED


def test_transition_legality_table():
    assert can_alpha_transition(S.PAPER_TRADING, S.SHADOW)
    assert can_alpha_transition(S.SHADOW, S.APPROVED)
    assert can_alpha_transition(S.APPROVED, S.DEGRADED)
    assert can_alpha_transition(S.DEGRADED, S.SHADOW)
    assert can_alpha_transition(S.PAUSED, S.SHADOW)
    assert not can_alpha_transition(S.DEGRADED, S.APPROVED)    # recover must re-observe, not jump to live
    assert not can_alpha_transition(S.RETIRED, S.SHADOW)       # terminal stays terminal
    assert not can_alpha_transition(S.DRAFT, S.SHADOW)


def test_monitoring_transitions_are_audited_and_chained():
    mgr, cat = _mgr()
    _to_paper(mgr, cat)
    mgr.advance("a1", S.SHADOW, AGENT, timestamp=4)
    mgr.advance("a1", S.PAUSED, AGENT, timestamp=5)
    assert mgr.audit_chain_valid()
    assert mgr.transitions[-1].dst_state == S.PAUSED
