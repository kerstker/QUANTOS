"""Domain-neutral effective-trial accounting primitive."""

from __future__ import annotations


def effective_trials(n_trials: int, mean_abs_corr: float) -> float:
    """Return the equicorrelation effective trial count.

    ``corr=0`` preserves every trial and ``corr=1`` collapses a comparable
    cluster to one independent trial.  Callers remain responsible for ensuring
    that missing or incomparable histories are not included in the discounted
    cluster.
    """

    if n_trials <= 1:
        return float(n_trials)
    rho = max(0.0, min(1.0, mean_abs_corr))
    return n_trials / (1.0 + (n_trials - 1) * rho)


__all__ = ["effective_trials"]
