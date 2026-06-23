"""Phase 11 — research agent loop: advances to PAPER_TRADING + human handoff,
and the agent can NEVER approve or reset the kill-switch."""
import pytest

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition, AlphaEvaluation
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.intelligence.agent_loop import ResearchAgentLoop
from services.intelligence.guardrails import ForbiddenAgentAction


class _StubEval:
    def __init__(self, catalog, verdict):
        self.catalog, self.verdict = catalog, verdict

    def evaluate(self, alpha_id, periods, **k):
        self.catalog.set_returns(alpha_id, (0.01, 0.0, 0.02, -0.01))
        return AlphaEvaluation(alpha_id=alpha_id, verdict=self.verdict, deflated_sharpe=0.8,
                               pbo=0.1, catalog_trial_count=self.catalog.global_trial_count(), marginal_ir=0.5)


def _loop(verdict):
    cat = AlphaCatalog()
    return ResearchAgentLoop(catalog=cat, evaluator=_StubEval(cat, verdict))


def _cands(n=2):
    return [(AlphaDefinition(alpha_id=f"a{i}", name=f"A{i}", factor_ids=(f"f{i}",)), object()) for i in range(n)]


def test_loop_advances_pass_to_paper_and_queues_for_human():
    loop = _loop(ValidationVerdict.PASS)
    out = loop.run(_cands(2))
    assert all(loop.catalog.state(f"a{i}") == AlphaState.PAPER_TRADING for i in range(2))
    assert set(out["human_review_queue"]) == {"a0", "a1"}
    assert all(loop.catalog.state(a) != AlphaState.APPROVED for a in loop.catalog.all_ids())  # never approved


def test_loop_rejects_fail():
    loop = _loop(ValidationVerdict.FAIL)
    loop.run(_cands(1))
    assert loop.catalog.state("a0") == AlphaState.REJECTED


def test_agent_cannot_approve_or_reset_via_loop():
    loop = _loop(ValidationVerdict.PASS)
    loop.run(_cands(1))
    with pytest.raises(ForbiddenAgentAction):
        loop.try_approve("a0")
    with pytest.raises(ForbiddenAgentAction):
        loop.try_reset_kill_switch(None)


def test_agent_can_only_request_human_approval():
    loop = _loop(ValidationVerdict.PASS)
    assert loop.request_human_approval("a0") == "APPROVAL_REQUESTED:a0"


def test_loop_must_run_as_non_human():
    with pytest.raises(ValueError):
        ResearchAgentLoop(actor=Actor(actor_id="alice", kind=ActorKind.HUMAN))
