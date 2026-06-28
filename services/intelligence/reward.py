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

import math
from typing import Optional, Sequence

from quantos.contracts.research import ValidationVerdict
from services.research.backtest_engine.metrics import sharpe

_VERDICT_BONUS = {ValidationVerdict.PASS: 1.0, ValidationVerdict.WARN: 0.5}


def _clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def generalization_gap(returns: Sequence[float], *, embargo: int = 1,
                       min_len: int = 40, gap_scale: float = 2.0) -> Optional[float]:
    """Normalised in-sample-minus-out-of-sample Sharpe gap of a return stream, in [0,1].

    Split `returns` into a train half (first) and a test half (second, after an `embargo`
    gap to avoid boundary overlap). The gap = max(0, IS_Sharpe - OOS_Sharpe): only the
    OVERFIT direction (strong in-sample, weak out-of-sample) is penalised; a factor that
    holds up or strengthens out-of-sample scores ~0. Returns None (-> NO penalty, the
    conservative default) when the stream is too short to split honestly or when either
    half is degenerate (non-finite Sharpe). Pure, deterministic, stdlib."""
    n = len(returns)
    if n < min_len:
        return None
    mid = n // 2
    train = list(returns[:mid])
    test = list(returns[mid + embargo:])
    if len(train) < min_len // 2 or len(test) < min_len // 2:
        return None
    is_sr, oos_sr = sharpe(train), sharpe(test)
    if not (math.isfinite(is_sr) and math.isfinite(oos_sr)):
        return None
    # Single mid-split: cheap (one extra Sharpe), scales with actual overfit severity, but a back-loaded
    # factor (stronger in its 2nd half) escapes — so this is a COMPLEMENT to, not a replacement for, the
    # post-hoc CPCV / no-peek-OOS gate, which stays the authority on selection overfit.
    return _clamp01(max(0.0, is_sr - oos_sr) / gap_scale)


def evaluation_reward(ev) -> float:
    if ev is None:
        return 0.0
    bonus = _VERDICT_BONUS.get(ev.verdict, 0.0)
    return _clamp01(
        0.5 * _clamp01(ev.deflated_sharpe)
        + 0.35 * _clamp01((ev.net_sharpe + 1.0) / 2.0)
        + 0.15 * bonus
    )
