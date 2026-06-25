"""UCB1 multi-armed bandit over hypothesis-space arms — the learning component.

Each arm (a factor / operator / window bucket) tracks (pulls, reward_sum). A
hypothesis is scored by the SUM of its three arms' UCB1 values; propose() returns
the top unproposed hypotheses by that score (deterministic tie-break by the
hypothesis tuple). update() feeds an evaluation reward back into the touched arms,
so later proposals concentrate on buckets that have scored well. No RNG."""
from __future__ import annotations

import math


class UCBSearch:
    def __init__(self, space, *, c: float = math.sqrt(2)) -> None:
        self.space = space
        self.c = c
        self.pulls: dict = {}
        self.reward_sum: dict = {}
        self.n_evaluated: int = 0
        self.proposed: set = set()

    def _ucb(self, arm) -> float:
        n = self.pulls.get(arm, 0)
        if n == 0:
            return math.inf                              # force first exploration of every arm
        mean = self.reward_sum[arm] / n
        return mean + self.c * math.sqrt(math.log(max(self.n_evaluated, 1)) / n)

    def _score(self, hypothesis) -> float:
        return sum(self._ucb(a) for a in self.space.arms_of(hypothesis))

    def propose(self, batch_size: int) -> list:
        pool = [h for h in self.space.hypotheses() if h not in self.proposed]
        pool.sort(key=lambda h: (-self._score(h), h))    # score desc, then tuple asc (deterministic)
        chosen = pool[:max(0, batch_size)]
        self.proposed.update(chosen)
        return chosen

    def update(self, hypothesis, reward: float) -> None:
        for arm in self.space.arms_of(hypothesis):
            self.pulls[arm] = self.pulls.get(arm, 0) + 1
            self.reward_sum[arm] = self.reward_sum.get(arm, 0.0) + reward
        self.n_evaluated += 1

    def arm_means(self) -> dict:
        return {a: self.reward_sum[a] / self.pulls[a] for a in self.pulls}

    # --- persistence port (pure in-memory; serialized by the experiment registry) -

    def snapshot(self) -> dict:
        return {
            "pulls": [[list(arm), n] for arm, n in sorted(self.pulls.items())],
            "reward_sum": [[list(arm), r] for arm, r in sorted(self.reward_sum.items())],
            "n_evaluated": self.n_evaluated,
            "proposed": [list(h) for h in sorted(self.proposed)],
        }

    def restore(self, snap: dict) -> None:
        self.pulls = {tuple(arm): n for arm, n in snap["pulls"]}
        self.reward_sum = {tuple(arm): r for arm, r in snap["reward_sum"]}
        self.n_evaluated = int(snap["n_evaluated"])
        self.proposed = {tuple(h) for h in snap["proposed"]}
