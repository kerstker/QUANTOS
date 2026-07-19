"""Map an AlphaEvaluation to a scalar reward in [0,1] for the search bandit.

Monotonic in the robust quality signals (deflated Sharpe, net Sharpe) with a
verdict bonus, so the bandit learns from a continuous signal even when verdicts
are REQUIRES_HUMAN_REVIEW (the common case before evidence is supplied). A None
evaluation (an errored candidate) scores 0.

`generalization_gap` is a SEARCH-REWARD-ONLY signal (it never touches the recorded
AlphaEvaluation): it measures a candidate's in-sample-vs-out-of-sample Sharpe gap on
a held-out split of its own return stream, so the search can be biased AWAY from
factors that look great in-sample but collapse out-of-sample (the overfit signature).
This bakes a cheap generalisation pressure INTO the search objective, rather than
relying only on the post-hoc CPCV/no-peek-OOS gate to catch selection overfit later."""
from __future__ import annotations

from quantos.contracts.research import ValidationVerdict
# generalization_gap lives with domain-neutral validation metrics so both
# search engines — intelligence (here) and research/alpha_factory — share one impl without a circular import.
# Re-exported here for the existing call sites (evolutionary_search + tests import it from reward).
from services.validation.performance import generalization_gap  # noqa: F401

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
