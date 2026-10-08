"""UCB1 multi-armed bandit over hypothesis-space arms — the learning component.

Each arm (a factor / operator / window bucket) tracks (pulls, reward_sum). A
hypothesis is scored by the SUM of its three arms' UCB1 values; propose() returns
the top unproposed hypotheses by that score (deterministic tie-break by the
hypothesis tuple). update() feeds an evaluation reward back into the touched arms,
so later proposals concentrate on buckets that have scored well. No RNG.

Regret tracking (self-assessment, NOT a decision input): update() also records a
per-round "simple regret" = best_known_mean_before_this_round - reward, where
best_known_mean is the max over ALL arm means as of just before this round's stats
are folded in (global best-known, not just the arms this hypothesis touched — the
question answered is "relative to everything already known, how much did THIS
pick leave on the table"). Before any arm has ever been pulled, best_known_mean is
undefined and the round is recorded as None (never faked as 0.0). This is a purely
observational side-channel: it never feeds back into propose()/_ucb()/_score()."""
from __future__ import annotations

import math
from typing import List, Optional


class UCBSearch:
    def __init__(self, space, *, c: float = math.sqrt(2)) -> None:
        self.space = space
        self.c = c
        self.pulls: dict = {}
        self.reward_sum: dict = {}
        self.n_evaluated: int = 0
        self.proposed: set = set()
        self.regret_history: List[Optional[float]] = []

    def _ucb(self, arm) -> float:
        n = self.pulls.get(arm, 0)
        if n == 0:
            return math.inf                              # force first exploration of every arm
        mean = self.reward_sum[arm] / n
        return mean + self.c * math.sqrt(math.log(max(self.n_evaluated, 1)) / n)

    def _score(self, hypothesis) -> float:
        # MEAN of arm UCBs (not sum): for a homogeneous space (every hypothesis has the
        # same arm count) this is sum/k → identical ranking (zero regression). For a
        # CombinedHypothesisSpace mixing 3-arm price and 2-arm fundamental hypotheses,
        # averaging removes the bias toward higher-arm-count families. An unpulled arm
        # is +inf, so the mean stays +inf and forces first exploration of every arm.
        arms = self.space.arms_of(hypothesis)
        return sum(self._ucb(a) for a in arms) / len(arms) if arms else 0.0

    def propose(self, batch_size: int) -> list:
        pool = [h for h in self.space.hypotheses() if h not in self.proposed]
        pool.sort(key=lambda h: (-self._score(h), h))    # score desc, then tuple asc (deterministic)
        chosen = pool[:max(0, batch_size)]
        self.proposed.update(chosen)
        return chosen

    def update(self, hypothesis, reward: float) -> None:
        # regret is computed against the state as of BEFORE this round's stats are
        # folded in, and against the GLOBAL best-known arm mean (not just the arms
        # this hypothesis touches) — pure self-assessment, does not affect the
        # pulls/reward_sum/n_evaluated update below in any way.
        means_before = self.arm_means()
        best_known_mean = max(means_before.values()) if means_before else None
        regret = None if best_known_mean is None else best_known_mean - reward
        self.regret_history.append(regret)

        for arm in self.space.arms_of(hypothesis):
            self.pulls[arm] = self.pulls.get(arm, 0) + 1
            self.reward_sum[arm] = self.reward_sum.get(arm, 0.0) + reward
        self.n_evaluated += 1

    def arm_means(self) -> dict:
        return {a: self.reward_sum[a] / self.pulls[a] for a in self.pulls}

    def cumulative_regret(self) -> float:
        """Sum of all recorded per-round regrets, skipping None (undefined) rounds —
        None is never treated as 0.0."""
        return sum(r for r in self.regret_history if r is not None)

    # --- persistence port (pure in-memory; serialized by the experiment registry) -

    SNAPSHOT_VERSION = 1

    def snapshot(self) -> dict:
        # NOTE: arm stats (pulls/reward_sum) are keyed by (dim,value) tuples and survive
        # a space change as long as arm shapes are unchanged. The `proposed` dedup set is
        # keyed by the FULL hypothesis tuple — if the hypothesis encoding changes (e.g.
        # adding a family tag for CombinedHypothesisSpace), restored `proposed` entries
        # won't match new hypotheses, so a previously-tried hypothesis may be re-proposed
        # (harmless: contained, and multiple-testing honesty lives in the catalog, not here).
        # `regret_history` is a PURELY ADDITIVE field (list of float|None, JSON-safe as-is)
        # -> no version bump: restore() already treats a missing key as "no history yet"
        # (honest — history predating this field genuinely doesn't exist, never backfilled).
        return {
            "version": self.SNAPSHOT_VERSION,
            "pulls": [[list(arm), n] for arm, n in sorted(self.pulls.items())],
            "reward_sum": [[list(arm), r] for arm, r in sorted(self.reward_sum.items())],
            "n_evaluated": self.n_evaluated,
            "proposed": [list(h) for h in sorted(self.proposed)],
            "regret_history": list(self.regret_history),
        }

    def restore(self, snap: dict) -> None:
        # fail-closed on an incompatible future schema; a missing version = legacy v1.
        version = int(snap.get("version", 1))
        if version != self.SNAPSHOT_VERSION:
            raise ValueError(f"unsupported bandit snapshot version {version} "
                             f"(expected {self.SNAPSHOT_VERSION})")
        self.pulls = {tuple(arm): n for arm, n in snap["pulls"]}
        self.reward_sum = {tuple(arm): r for arm, r in snap["reward_sum"]}
        self.n_evaluated = int(snap["n_evaluated"])
        self.proposed = {tuple(h) for h in snap["proposed"]}
        # older (pre-regret-tracking) snapshots have no "regret_history" key at all --
        # that history genuinely doesn't exist, so restore it as empty (not fabricated).
        self.regret_history = list(snap.get("regret_history", []))
