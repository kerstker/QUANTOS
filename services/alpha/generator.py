"""Alpha generator (Phase 6, step 12) — PROPOSAL-ONLY and inert.

Deterministically enumerates candidate AlphaDefinitions from factor combinations.
It is structurally incapable of doing anything dangerous: it imports ONLY the
alpha contract, produces frozen candidate specs (which carry no state — state
lives in the catalog/lifecycle), and never evaluates, registers, scores, or
promotes anything. An import-boundary test pins this isolation.
"""
from __future__ import annotations

from itertools import combinations
from typing import Optional, Sequence

from quantos.contracts.alpha import AlphaDefinition


def propose(
    factor_ids: Sequence[str],
    *,
    universe: str = "DEFAULT",
    max_factors: int = 2,
    params: Optional[dict[str, str]] = None,
) -> list[AlphaDefinition]:
    """All factor combinations of size 1..max_factors, as DRAFT-ready candidates.
    Deterministic: factors de-duplicated and sorted; combinations in order."""
    if max_factors < 1:
        raise ValueError("max_factors must be >= 1")
    params = dict(params or {})
    fids = sorted(set(factor_ids))
    out: list[AlphaDefinition] = []
    for r in range(1, max_factors + 1):
        for combo in combinations(fids, r):
            out.append(AlphaDefinition(
                alpha_id="alpha_" + "_".join(combo),
                name=f"Alpha({', '.join(combo)})",
                factor_ids=tuple(combo),
                universe=universe,
                params=params,
            ))
    return out
