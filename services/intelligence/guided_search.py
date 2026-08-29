"""Guided research search: a UCB1 bandit proposes alpha hypotheses, the UNCHANGED
guardrailed ResearchAgentLoop evaluates + advances them (so the agent still can
never reach APPROVED), and the evaluation reward is fed back so later rounds
concentrate on the best feature buckets. Pure-Python, deterministic; adds NO new
authority — proposing is the existing PROPOSE agent action."""
from __future__ import annotations

from typing import Callable, Optional

from services.research.ports.persistence import TrialPersistenceError
from services.validation.selection_authority import (
    LEGACY_INVALIDATION_CODE,
    require_diagnostic_selection_seam,
)

from .agent_loop import ResearchAgentLoop
from .evidence import provider_leakage_evidence
from .guardrails import AgentAction, assert_action_allowed
from .reward import evaluation_reward
from .search_space import HypothesisSpace
from .search_strategy import UCBSearch


class GuidedSearchLoop:
    def __init__(self, space: HypothesisSpace, *, strategy=None, inner_loop=None) -> None:
        self.space = space
        self.strategy = strategy or UCBSearch(space)
        self.inner_loop = inner_loop or ResearchAgentLoop()

    def search(self, rounds: int, batch_size: int, periods_provider: Callable,
               *, timestamp: int = 0, diagnostic_only: bool = False) -> dict:
        require_diagnostic_selection_seam(
            "GuidedSearchLoop.search", diagnostic_only=diagnostic_only
        )
        history, review_queue, report = [], [], {}
        for _ in range(rounds):
            assert_action_allowed(self.inner_loop.actor, AgentAction.PROPOSE)
            hyps = self.strategy.propose(batch_size)
            if not hyps:
                break                                    # grid exhausted -> stop (fail-soft)
            defns = {h: self.space.to_definition(*h) for h in hyps}
            # isolate the periods_provider per hypothesis: one provider error must not
            # abort the whole multi-round search — that hypothesis is recorded with
            # reward 0 (its arms learn the failure) and the search continues (review).
            candidates, failed = [], []
            for h in hyps:
                try:
                    candidates.append((defns[h], periods_provider(defns[h])))
                except TrialPersistenceError:
                    raise
                except Exception as exc:
                    failed.append(h)
                    self.strategy.update(h, 0.0)
                    history.append({"hypothesis": h, "reward": 0.0,
                                    "state": None, "error": type(exc).__name__})
            if not candidates:
                continue
            # 断点1 (方案A): if the provider can self-assess leakage, feed it as evidence so the
            # evaluator's leakage gate participates with a real verdict (else {} -> fail-closed None).
            evidence = provider_leakage_evidence(periods_provider, candidates)
            out = self.inner_loop.run(
                candidates,
                timestamp=timestamp,
                evidence=evidence,
                diagnostic_only=True,
            )
            report.update(out["report"])
            review_queue.extend(out["human_review_queue"])
            for h in hyps:
                if h in failed:
                    continue                             # already scored 0 above
                entry = out["report"].get(defns[h].alpha_id, {})
                reward = evaluation_reward(entry.get("evaluation"))
                self.strategy.update(h, reward)
                history.append({"hypothesis": h, "reward": reward, "state": entry.get("state")})
        return {"history": history, "arm_means": self.strategy.arm_means(),
                "human_review_queue": review_queue, "report": report,
                "diagnostic_only": True, "selection_authority": False,
                "invalidation_code": LEGACY_INVALIDATION_CODE}
