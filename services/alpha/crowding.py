"""Alpha crowding (Phase 6, step 7a) — exposure to KNOWN public factors.

crowding_score = fraction of the candidate's return variance explained by an
injected public-factor panel (R^2 via the same Gram-Schmidt projection used for
orthogonalization). High R^2 => the "alpha" is mostly a repackaged public factor
(momentum/value/size/low-vol/...) and the catalog is paying consensus rent.

Fail-closed (critique P1-4): when the panel is ABSENT or the overlap is
insufficient, crowding_score is None — the caller must route the alpha to
REQUIRES_HUMAN_REVIEW, never silently treat it as uncrowded (0). In Phase 6 the
real anti-crowding teeth are correlation + clustering + orthogonalization vs the
approved set; crowding_score is live only once a real reference panel is sourced.
"""
from __future__ import annotations

from typing import Optional

from .correlation import max_abs_corr_to_set
from .orthogonalize import orthogonalize


def crowding_score(
    candidate_returns: tuple[float, ...],
    public_panel: dict[str, tuple[float, ...]],
    min_overlap: int = 60,
) -> Optional[float]:
    if not public_panel:
        return None                       # no panel -> undefined, NOT 0
    res = orthogonalize(candidate_returns, public_panel, min_overlap=min_overlap)
    if not res.ok or res.explained_r2 is None:
        return None
    return min(max(res.explained_r2, 0.0), 1.0)


def max_factor_loading(
    candidate_returns: tuple[float, ...],
    public_panel: dict[str, tuple[float, ...]],
    min_overlap: int = 60,
) -> Optional[float]:
    """Largest |correlation| to any single public factor."""
    if not public_panel:
        return None
    return max_abs_corr_to_set("candidate", candidate_returns, public_panel, min_overlap=min_overlap)


def is_crowded(score: Optional[float], crowd_max: float) -> bool:
    """A None score is NOT 'not crowded' — it is unknown and handled upstream."""
    return score is not None and score > crowd_max
