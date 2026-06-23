"""Phase 6 — alpha lifecycle state machine: legal/illegal transitions and the
human-only APPROVED authorization (agents can never self-approve)."""
import pytest

from quantos.contracts.research import AlphaState
from quantos.contracts.alpha import (
    AGENT_REACHABLE,
    Actor,
    ActorKind,
    AlphaDefinition,
    AlphaTransition,
    InvalidAlphaTransition,
    UnauthorizedPromotion,
    assert_alpha_transition,
    can_alpha_transition,
    is_alpha_terminal,
)

HUMAN = Actor(actor_id="alice", kind=ActorKind.HUMAN)
AGENT = Actor(actor_id="research-agent", kind=ActorKind.AGENT)
S = AlphaState


def test_legal_transitions_allowed_for_human():
    legal = [
        (S.DRAFT, S.TESTING), (S.TESTING, S.VALIDATED), (S.VALIDATED, S.PAPER_TRADING),
        (S.PAPER_TRADING, S.APPROVED), (S.APPROVED, S.RETIRED),
        (S.DRAFT, S.REJECTED), (S.PAPER_TRADING, S.RETIRED),
    ]
    for src, dst in legal:
        assert can_alpha_transition(src, dst)
        assert_alpha_transition(src, dst, HUMAN)  # must not raise


def test_illegal_transitions_rejected():
    for src, dst in [(S.DRAFT, S.APPROVED), (S.VALIDATED, S.APPROVED),
                     (S.APPROVED, S.PAPER_TRADING), (S.TESTING, S.PAPER_TRADING)]:
        assert not can_alpha_transition(src, dst)
        with pytest.raises(InvalidAlphaTransition):
            assert_alpha_transition(src, dst, HUMAN)


def test_terminal_states_have_no_exit():
    for s in (S.RETIRED, S.REJECTED):
        assert is_alpha_terminal(s)
        with pytest.raises(InvalidAlphaTransition):
            assert_alpha_transition(s, S.TESTING, HUMAN)


def test_agent_cannot_reach_approved():
    assert S.APPROVED not in AGENT_REACHABLE
    # legal edge, but an agent actor is barred from driving it
    assert can_alpha_transition(S.PAPER_TRADING, S.APPROVED)
    with pytest.raises(UnauthorizedPromotion):
        assert_alpha_transition(S.PAPER_TRADING, S.APPROVED, AGENT)
    # a human may
    assert_alpha_transition(S.PAPER_TRADING, S.APPROVED, HUMAN)


def test_agent_may_advance_to_paper_and_de_risk_to_retired():
    assert_alpha_transition(S.VALIDATED, S.PAPER_TRADING, AGENT)
    assert_alpha_transition(S.APPROVED, S.RETIRED, AGENT)        # de-risking is allowed
    assert_alpha_transition(S.PAPER_TRADING, S.REJECTED, AGENT)


def test_alpha_definition_hash_is_deterministic():
    d1 = AlphaDefinition(alpha_id="a1", name="A", factor_ids=("f1", "f2"), universe="CN")
    d2 = AlphaDefinition(alpha_id="a1", name="A", factor_ids=("f1", "f2"), universe="CN")
    assert d1.definition_hash == d2.definition_hash and len(d1.definition_hash) == 64


def test_alpha_transition_hash_chain():
    t1 = AlphaTransition(transition_id="t1", alpha_id="a1", src_state=S.DRAFT,
                         dst_state=S.TESTING, actor=HUMAN, timestamp=1)
    t2 = AlphaTransition(transition_id="t2", alpha_id="a1", src_state=S.TESTING,
                         dst_state=S.VALIDATED, actor=HUMAN, timestamp=2, prev_hash=t1.entry_hash)
    assert t1.entry_hash != t2.entry_hash
    assert t2.prev_hash == t1.entry_hash
