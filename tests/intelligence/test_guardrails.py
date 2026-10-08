"""Phase 11 — AI guardrails: the explicit capability policy holds."""
import pytest

from quantos.contracts.alpha import Actor, ActorKind
from services.intelligence.guardrails import (
    AGENT_ALLOWED, AgentAction, ForbiddenAgentAction, assert_action_allowed,
)

HUMAN = Actor(actor_id="alice", kind=ActorKind.HUMAN)
AGENT = Actor(actor_id="bot", kind=ActorKind.AGENT)
SYSTEM = Actor(actor_id="sys", kind=ActorKind.SYSTEM)


def test_agent_allowed_actions_pass():
    for a in AGENT_ALLOWED:
        assert_action_allowed(AGENT, a)            # no raise
        assert_action_allowed(SYSTEM, a)


def test_agent_and_system_cannot_approve_or_reset():
    for actor in (AGENT, SYSTEM):
        for a in (AgentAction.APPROVE, AgentAction.RESET_KILL_SWITCH):
            with pytest.raises(ForbiddenAgentAction):
                assert_action_allowed(actor, a)


def test_human_may_approve_and_reset():
    assert_action_allowed(HUMAN, AgentAction.APPROVE)
    assert_action_allowed(HUMAN, AgentAction.RESET_KILL_SWITCH)


def test_live_order_and_credentials_forbidden_for_everyone():
    for actor in (HUMAN, AGENT, SYSTEM):
        for a in (AgentAction.LIVE_TRADE, AgentAction.ACCESS_CREDENTIALS):
            with pytest.raises(ForbiddenAgentAction):
                assert_action_allowed(actor, a)
