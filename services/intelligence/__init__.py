"""QUANTOS AI Agent Layer (blueprint §10 / Phase 11) — guardrailed research loop.

An autonomous research agent may PROPOSE alphas, EVALUATE them, and advance them
up to PAPER_TRADING, and may de-risk (RETIRE) — and NOTHING else. Approving an
alpha, resetting the kill-switch, submitting a live order, or touching credentials
are structurally impossible for a non-human actor (and live order / credentials
are forbidden for EVERYONE at L2). The agent hands PAPER_TRADING candidates to a
human review queue; it can REQUEST approval but never grant it.
"""
from .guardrails import (
    AgentAction, ForbiddenAgentAction, assert_action_allowed, AGENT_ALLOWED,
)
from .agent_loop import ResearchAgentLoop
from .runtime import GovernedModelRuntime, RuntimeGovernanceError

__all__ = [
    "AgentAction", "ForbiddenAgentAction", "assert_action_allowed", "AGENT_ALLOWED",
    "ResearchAgentLoop",
    "GovernedModelRuntime", "RuntimeGovernanceError",
]
