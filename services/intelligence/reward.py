"""Map an AlphaEvaluation to a scalar reward in [0,1] for the search bandit.

Monotonic in the robust quality signals (deflated Sharpe, net Sharpe) with a
verdict bonus, so the bandit learns from a continuous signal even when verdicts
are REQUIRES_HUMAN_REVIEW (the common case before evidence is supplied). A None
evaluation (an errored candidate) scores 0."""
from __future__ import annotations

from quantos.contracts.research import ValidationVerdict

_VERDICT_BONUS = {ValidationVerdict.PASS: 1.0, ValidationVerdict.WARN: 0.5}


def _clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def evaluation_reward(ev) -> float:
    if ev is None:
        return 0.0
    bonus = _VERDICT_BONUS.get(ev.verdict, 0.0)
    return _clamp01(
        0.5 * _clamp01(ev.deflated_sharpe)
        + 0.35 * _clamp01((ev.net_sharpe + 1.0) / 2.0)
        + 0.15 * bonus
    )
