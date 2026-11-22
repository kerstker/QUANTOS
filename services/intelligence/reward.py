"""Map an AlphaEvaluation to a scalar reward in [0,1] for the search bandit.

Monotonic in the robust quality signals (deflated Sharpe, net Sharpe) with a
verdict bonus, so the bandit learns from a continuous signal even when verdicts
are REQUIRES_HUMAN_REVIEW (the common case before evidence is supplied). A None
or non-finite evaluation (an errored or corrupted candidate) scores 0.0.

`generalization_gap` is a SEARCH-REWARD-ONLY signal (it never touches the recorded
AlphaEvaluation): it measures a candidate's in-sample-vs-out-of-sample Sharpe gap on
a held-out split of its own return stream, so the search can be biased AWAY from
factors that look great in-sample but collapse out-of-sample (the overfit signature).
"""
from __future__ import annotations

import math

try:
    from quantos.contracts.research import ValidationVerdict
except Exception:  # pragma: no cover - lightweight fallback when quantos package is absent
    from enum import Enum

    class ValidationVerdict(str, Enum):  # type: ignore[no-redef]
        PASS = "PASS"
        WARN = "WARN"
        FAIL = "FAIL"
        REQUIRES_HUMAN_REVIEW = "REQUIRES_HUMAN_REVIEW"

try:
    from services.validation.performance import generalization_gap  # noqa: F401
except Exception:  # pragma: no cover
    generalization_gap = None  # type: ignore[assignment]

_VERDICT_BONUS = {ValidationVerdict.PASS: 1.0, ValidationVerdict.WARN: 0.5}


def _finite_real(x: object) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    val = float(x)
    if not math.isfinite(val):
        return None
    return val


def _clamp01(x: float) -> float:
    """Clamp a finite real value into [0.0, 1.0]; fail-closed to 0.0 on NaN/Inf/bool."""
    val = _finite_real(x)
    if val is None:
        return 0.0
    if val <= 0.0:
        return 0.0
    if val >= 1.0:
        return 1.0
    return val


def evaluation_reward(ev) -> float:
    """Compute a deterministic bounded reward in [0.0, 1.0].

    Fail-closed: if `ev` is None or either `deflated_sharpe` or `net_sharpe` is
    non-finite / boolean / missing, returns 0.0 rather than letting NaN poison
    UCB/MCTS search statistics.
    """
    if ev is None:
        return 0.0
    dsr = _finite_real(getattr(ev, "deflated_sharpe", None))
    net_sharpe = _finite_real(getattr(ev, "net_sharpe", None))
    if dsr is None or net_sharpe is None:
        return 0.0
    verdict = getattr(ev, "verdict", None)
    bonus = _VERDICT_BONUS.get(verdict, 0.0)
    if bonus == 0.0 and isinstance(verdict, str):
        if verdict == "PASS":
            bonus = 1.0
        elif verdict == "WARN":
            bonus = 0.5
    return _clamp01(
        0.5 * _clamp01(dsr)
        + 0.35 * _clamp01((net_sharpe + 1.0) / 2.0)
        + 0.15 * bonus
    )
