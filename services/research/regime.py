"""Regime-conditional factor evaluation.

A factor's edge often lives in ONE market state (e.g. only in calm markets, or only in
high-volatility stress) and averages to mediocrity across all states. This labels each
period into a volatility regime and reports a factor's performance WITHIN each regime, so a
state-dependent edge is not hidden by the average.

PIT / no-lookahead: a period's regime label uses the trailing realized volatility (knowable
at that period) ranked against the EXPANDING distribution of vols seen up to that period —
never a full-sample threshold. So appending future bars never changes a past label.

Pure-Python stdlib, deterministic, fail-closed (periods whose vol cannot be computed are
unlabelled; regimes with <2 observations report None rather than a fabricated statistic).
"""
from __future__ import annotations

import statistics
from typing import Optional, Sequence

from .labeling import _trailing_vol


def label_regimes_by_vol(market_closes: Sequence[float], *, vol_window: int = 20,
                         n_regimes: int = 2) -> list:
    """Label each period 0..n_regimes-1 by where its trailing volatility falls in the
    EXPANDING distribution of trailing vols seen so far (0 = calmest bucket). Returns a list
    aligned to `market_closes`; None where the trailing vol is not yet computable. PIT-safe."""
    if n_regimes < 2:
        raise ValueError("n_regimes must be >= 2")
    n = len(market_closes)
    labels: list = [None] * n
    seen: list = []
    for i in range(n):
        v = _trailing_vol(market_closes, i, vol_window)
        if v is None:
            continue
        seen.append(v)
        # fraction of past vols this one STRICTLY exceeds (ties -> low bucket): a typical /
        # equal-to-history vol sits in the calm bucket, only a genuinely elevated vol climbs.
        # Regimes are RELATIVE — a persistent high-vol state eventually becomes the new normal.
        pct = sum(1 for x in seen if x < v) / len(seen)
        labels[i] = min(n_regimes - 1, int(pct * n_regimes))
    return labels


def regime_conditional_stats(returns: Sequence[float], regime_labels: Sequence) -> dict:
    """Per-regime performance of a factor's per-period returns (aligned to regime_labels).
    Returns {regime: {n, mean, sharpe, positive_frac}}; a regime with <2 points reports
    None metrics (fail-closed). sharpe here is the raw per-period mean/stdev (not annualized),
    used only to compare regimes."""
    buckets: dict = {}
    for r, lab in zip(returns, regime_labels):
        if lab is None:
            continue
        buckets.setdefault(lab, []).append(float(r))
    out = {}
    for lab in sorted(buckets):
        rs = buckets[lab]
        if len(rs) >= 2:
            sd = statistics.pstdev(rs)
            mean = statistics.fmean(rs)
            sharpe = (mean / sd) if sd > 0 else (float("inf") if mean > 0 else 0.0)
            out[lab] = {"n": len(rs), "mean": round(mean, 6),
                        "sharpe": round(sharpe, 4) if sd > 0 else sharpe,
                        "positive_frac": round(sum(1 for x in rs if x > 0) / len(rs), 4)}
        else:
            out[lab] = {"n": len(rs), "mean": None, "sharpe": None, "positive_frac": None}
    return out


def regime_profitable_frac(returns: Sequence[float], regime_labels: Sequence) -> Optional[float]:
    """Fraction of (assessable) regimes in which the factor is profitable (sharpe > 0) — the
    state-robustness signal that can feed the evaluator's `regime_profitable_frac` evidence.
    None when no regime has enough points to assess (fail-closed)."""
    stats = regime_conditional_stats(returns, regime_labels)
    assessable = [s for s in stats.values() if s["sharpe"] is not None]
    if not assessable:
        return None
    return round(sum(1 for s in assessable if s["sharpe"] > 0) / len(assessable), 4)
