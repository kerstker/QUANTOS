"""AI agent guardrails (Phase 11) — the explicit, machine-checked capability policy.

Defines exactly what a non-human (AGENT/SYSTEM) actor MAY do. Everything an
autonomous researcher legitimately needs is allowed; everything that approves,
clears the kill-switch, trades live, or touches secrets is not. APPROVE and
RESET_KILL_SWITCH are human-only; LIVE_TRADE and ACCESS_CREDENTIALS are
forbidden for EVERYONE at L2 (live remains structurally impossible).
"""
from __future__ import annotations

from enum import Enum

from quantos.contracts.alpha import Actor


class AgentAction(str, Enum):
    PROPOSE = "PROPOSE"
    EVALUATE = "EVALUATE"
    ADVANCE_TESTING = "ADVANCE_TESTING"
    ADVANCE_VALIDATED = "ADVANCE_VALIDATED"
    ADVANCE_PAPER = "ADVANCE_PAPER"
    REQUEST_HUMAN_APPROVAL = "REQUEST_HUMAN_APPROVAL"
    RETIRE = "RETIRE"
    REJECT = "REJECT"
    APPROVE = "APPROVE"                    # human-only
    RESET_KILL_SWITCH = "RESET_KILL_SWITCH"  # human-only
    LIVE_TRADE = "LIVE_TRADE"      # forbidden for everyone at L2 (live impossible)
    ACCESS_CREDENTIALS = "ACCESS_CREDENTIALS"  # forbidden for everyone at L2


AGENT_ALLOWED = frozenset({
    AgentAction.PROPOSE, AgentAction.EVALUATE, AgentAction.ADVANCE_TESTING,
    AgentAction.ADVANCE_VALIDATED, AgentAction.ADVANCE_PAPER,
    AgentAction.REQUEST_HUMAN_APPROVAL, AgentAction.RETIRE, AgentAction.REJECT,
})
HUMAN_ONLY = frozenset({AgentAction.APPROVE, AgentAction.RESET_KILL_SWITCH})
FORBIDDEN_ALWAYS = frozenset({AgentAction.LIVE_TRADE, AgentAction.ACCESS_CREDENTIALS})


class ForbiddenAgentAction(PermissionError):
    def __init__(self, actor: Actor, action: AgentAction, why: str = ""):
        super().__init__(f"actor {actor.actor_id} ({actor.kind.value}) may not {action.value}: {why}")
        self.actor, self.action = actor, action


def assert_action_allowed(actor: Actor, action: AgentAction) -> None:
    if actor is None:
        raise PermissionError("an actor is required to perform any action")
    if action in FORBIDDEN_ALWAYS:
        raise ForbiddenAgentAction(actor, action, "forbidden for ALL actors at L2 (live impossible)")
    if actor.is_human:
        return                              # humans may do anything except the always-forbidden
    if action in AGENT_ALLOWED:
        return
    raise ForbiddenAgentAction(actor, action, "human-only")
