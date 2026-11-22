"""UCB1 multi-armed bandit over hypothesis-space arms — the learning component.

Each arm (a factor / operator / window bucket) tracks (pulls, reward_sum). A
hypothesis is scored by the MEAN of its arms' UCB1 values; propose() returns
the top unproposed hypotheses by that score (deterministic tie-break by the
hypothesis tuple). update() feeds a finite evaluation reward back into the
touched arms, so later proposals concentrate on buckets that have scored well.
No RNG. Fail-closed on non-finite exploration constants, corrupted rewards, or
invalid restored snapshots.
"""
from __future__ import annotations

import math
from typing import List, Optional


def _validate_finite_real(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite real number, got {value!r}")
    num = float(value)
    if not math.isfinite(num):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return num


class UCBSearch:
    def __init__(self, space, *, c: float = math.sqrt(2)) -> None:
        c_val = _validate_finite_real(c, name="c")
        if c_val < 0.0:
            raise ValueError(f"c must be >= 0, got {c!r}")
        self.space = space
        self.c = c_val
        self.pulls: dict = {}
        self.reward_sum: dict = {}
        self.n_evaluated: int = 0
        self.proposed: set = set()
        self.regret_history: List[Optional[float]] = []

    def _ucb(self, arm) -> float:
        n = self.pulls.get(arm, 0)
        if n <= 0:
            return math.inf                              # force first exploration of every arm
        mean = self.reward_sum[arm] / n
        return mean + self.c * math.sqrt(math.log(max(self.n_evaluated, 1)) / n)

    def _score(self, hypothesis) -> float:
        arms = self.space.arms_of(hypothesis)
        return sum(self._ucb(a) for a in arms) / len(arms) if arms else 0.0

    def propose(self, batch_size: int) -> list:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise ValueError(f"batch_size must be an integer, got {batch_size!r}")
        if batch_size <= 0:
            return []
        pool = [h for h in self.space.hypotheses() if h not in self.proposed]
        pool.sort(key=lambda h: (-self._score(h), h))    # score desc, then tuple asc (deterministic)
        chosen = pool[:batch_size]
        self.proposed.update(chosen)
        return chosen

    def update(self, hypothesis, reward: float) -> None:
        r = _validate_finite_real(reward, name="reward")
        means_before = self.arm_means()
        best_known_mean = max(means_before.values()) if means_before else None
        regret = None if best_known_mean is None else best_known_mean - r
        self.regret_history.append(regret)
        for arm in self.space.arms_of(hypothesis):
            self.pulls[arm] = self.pulls.get(arm, 0) + 1
            self.reward_sum[arm] = self.reward_sum.get(arm, 0.0) + r
        self.n_evaluated += 1

    def arm_means(self) -> dict:
        return {a: self.reward_sum[a] / self.pulls[a] for a in self.pulls if self.pulls[a] > 0}

    def cumulative_regret(self) -> float:
        """Sum of all recorded per-round regrets, skipping None (undefined) rounds —
        None is never treated as 0.0."""
        return sum(r for r in self.regret_history if r is not None)

    # --- persistence port (pure in-memory; serialized by the experiment registry) -
    SNAPSHOT_VERSION = 1

    def snapshot(self) -> dict:
        return {
            "version": self.SNAPSHOT_VERSION,
            "pulls": [[list(arm), n] for arm, n in sorted(self.pulls.items())],
            "reward_sum": [[list(arm), r] for arm, r in sorted(self.reward_sum.items())],
            "n_evaluated": self.n_evaluated,
            "proposed": [list(h) for h in sorted(self.proposed)],
            "regret_history": list(self.regret_history),
        }

    def restore(self, snap: dict) -> None:
        if not isinstance(snap, dict):
            raise ValueError("bandit snapshot must be a dict")
        raw_version = snap.get("version", 1)
        if isinstance(raw_version, bool) or not isinstance(raw_version, int):
            raise ValueError(f"invalid bandit snapshot version {raw_version!r}")
        if raw_version != self.SNAPSHOT_VERSION:
            raise ValueError(f"unsupported bandit snapshot version {raw_version} "
                             f"(expected {self.SNAPSHOT_VERSION})")
        raw_n_eval = snap["n_evaluated"]
        if isinstance(raw_n_eval, bool) or not isinstance(raw_n_eval, int) or raw_n_eval < 0:
            raise ValueError(f"invalid n_evaluated in snapshot: {raw_n_eval!r}")

        restored_pulls: dict = {}
        for arm, n in snap["pulls"]:
            if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
                raise ValueError(f"invalid pull count for arm {arm!r}: {n!r}")
            restored_pulls[tuple(arm)] = n

        restored_rewards: dict = {}
        for arm, r in snap["reward_sum"]:
            restored_rewards[tuple(arm)] = _validate_finite_real(r, name=f"reward_sum[{arm!r}]")

        if set(restored_pulls.keys()) != set(restored_rewards.keys()):
            raise ValueError("bandit snapshot pulls and reward_sum keys mismatch")

        restored_regret: List[Optional[float]] = []
        for item in snap.get("regret_history", []):
            if item is None:
                restored_regret.append(None)
            else:
                restored_regret.append(_validate_finite_real(item, name="regret_history item"))

        self.pulls = restored_pulls
        self.reward_sum = restored_rewards
        self.n_evaluated = raw_n_eval
        self.proposed = {tuple(h) for h in snap["proposed"]}
        self.regret_history = restored_regret
