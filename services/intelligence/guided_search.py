"""Guided research search: a UCB1 bandit proposes alpha hypotheses, the UNCHANGED
guardrailed ResearchAgentLoop evaluates + advances them (so the agent still can
never reach APPROVED), and the evaluation reward is fed back so later rounds
concentrate on the best feature buckets. Pure-Python, deterministic; adds NO new
authority — proposing is the existing PROPOSE agent action."""
from __future__ import annotations

from typing import Callable, Optional

from .agent_loop import ResearchAgentLoop
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
               *, timestamp: int = 0) -> dict:
        history, review_queue, report = [], [], {}
        for _ in range(rounds):
            assert_action_allowed(self.inner_loop.actor, AgentAction.PROPOSE)
            hyps = self.strategy.propose(batch_size)
            if not hyps:
                break                                    # grid exhausted -> stop (fail-soft)
            defns = {h: self.space.to_definition(*h) for h in hyps}
            candidates = [(defns[h], periods_provider(defns[h])) for h in hyps]
            out = self.inner_loop.run(candidates, timestamp=timestamp)
            report.update(out["report"])
            review_queue.extend(out["human_review_queue"])
            for h in hyps:
                entry = out["report"].get(defns[h].alpha_id, {})
                reward = evaluation_reward(entry.get("evaluation"))
                self.strategy.update(h, reward)
                history.append({"hypothesis": h, "reward": reward, "state": entry.get("state")})
        return {"history": history, "arm_means": self.strategy.arm_means(),
                "human_review_queue": review_queue, "report": report}
