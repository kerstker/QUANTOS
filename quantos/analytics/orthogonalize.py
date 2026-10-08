"""Alpha orthogonalization (Phase 6, step 6c) — pay only for INCREMENTAL edge.

Residualize a candidate's return series against the already-APPROVED set via
modified Gram-Schmidt, then measure the marginal information ratio of the
residual. A duplicate of an approved alpha has residual ~= 0 (explained_R2 ~= 1,
marginal_IR ~= 0) so the ranker won't reward it; a genuinely orthogonal alpha
keeps its edge.

MIN_OVERLAP-gated (critique P1-3): all series are aligned on a single shared
window (the common overlap); if that is below min_overlap the result is
fail-closed (ok=False, marginal_ir=None) so the caller routes to review rather
than fabricating a marginal IR on mismatched samples.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

_EPS = 1e-10


@dataclass(frozen=True)
class OrthogonalizationResult:
    ok: bool
    overlap: int
    residual: tuple[float, ...] = ()
    explained_r2: Optional[float] = None
    marginal_ir: Optional[float] = None


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _sub(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [x - y for x, y in zip(a, b)]


def _scale(a: Sequence[float], s: float) -> list[float]:
    return [x * s for x in a]


def orthogonalize(
    candidate_returns: tuple[float, ...],
    approved_set: dict[str, tuple[float, ...]],
    min_overlap: int = 60,
    periods_per_year: int = 252,
) -> OrthogonalizationResult:
    # 2026-07-09: annualization base for marginal_ir below. Default 252 (daily) is UNCHANGED for
    # every existing caller. AlphaEvaluator passes its own periods_per_year here (services/alpha/
    # evaluator.py) so marginal_ir stays consistent with the SAME net_sharpe already correctly
    # annualized at that evaluator's cadence — this was a real, verified sibling bug to the
    # panel_to_periods/AlphaEvaluator.periods_per_year fix (a hardcoded sqrt(252) here silently
    # over-annualized marginal_ir by sqrt(252/52)~=2.20x for the first weekly-cadence evaluator).
    annualize = math.sqrt(periods_per_year)
    # common shared window across candidate + every basis vector
    lengths = [len(candidate_returns)] + [len(v) for v in approved_set.values()]
    overlap = min(lengths) if lengths else 0

    # empty approved set -> the candidate is fully marginal (against itself only)
    if not approved_set:
        cand = list(candidate_returns)
        if len(cand) < 2:
            return OrthogonalizationResult(ok=False, overlap=len(cand))
        sd = statistics.pstdev(cand)
        ir = statistics.fmean(cand) / sd * annualize if sd > _EPS else None
        return OrthogonalizationResult(ok=True, overlap=len(cand), residual=tuple(cand),
                                       explained_r2=0.0, marginal_ir=ir)

    if overlap < min_overlap:
        return OrthogonalizationResult(ok=False, overlap=overlap)

    cand = list(candidate_returns[-overlap:])
    basis = [list(v[-overlap:]) for _, v in sorted(approved_set.items())]

    # modified Gram-Schmidt: build an orthonormal set Q from the basis
    q_set: list[list[float]] = []
    for vec in basis:
        v = list(vec)
        for q in q_set:
            v = _sub(v, _scale(q, _dot(v, q)))
        norm = math.sqrt(_dot(v, v))
        if norm > _EPS:                      # drop rank-deficient (duplicate) basis vectors
            q_set.append(_scale(v, 1.0 / norm))

    # residual of the candidate after removing its projection onto span(Q)
    residual = list(cand)
    for q in q_set:
        residual = _sub(residual, _scale(q, _dot(residual, q)))

    var_cand = statistics.pvariance(cand) if len(cand) > 1 else 0.0
    var_res = statistics.pvariance(residual) if len(residual) > 1 else 0.0
    explained_r2 = 1.0 - var_res / var_cand if var_cand > _EPS else 1.0

    sd_res = statistics.pstdev(residual)
    marginal_ir = statistics.fmean(residual) / sd_res * annualize if sd_res > _EPS else 0.0

    return OrthogonalizationResult(
        ok=True, overlap=overlap, residual=tuple(residual),
        explained_r2=explained_r2, marginal_ir=marginal_ir,
    )
