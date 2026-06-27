"""Lineage construction (Alpha Factory upgrade Phase 3).

When the evolutionary / cs_genome search breeds a child alpha from parents, this builds the child
AlphaAsset with its genealogy written in: the parent ids, the mutation operator that produced it, and
the performance delta (child metric minus the BEST parent's metric — the improvement the breeding
actually bought). Calling this from the search loop is what turns ephemeral genome parent->child links
into durable, queryable registry lineage. Pure-Python stdlib, deterministic.
"""
from __future__ import annotations

from typing import Optional, Sequence

from quantos.contracts.alpha import AlphaEvaluation
from quantos.contracts.alpha_asset import AlphaAsset, AlphaLineage


def make_child_asset(*, alpha_id: str, expression: str, parents: Sequence[AlphaAsset],
                     mutation_op: str, evaluation: Optional[AlphaEvaluation] = None,
                     metric: str = "net_sharpe", **meta) -> AlphaAsset:
    """Construct the child asset. `perf_delta` = child[metric] - max(parent[metric]) when both the child
    and at least one parent have an evaluation, else None (fail-safe). `generator` defaults to the first
    parent's generator unless given in `meta`. Any other AlphaAsset field may be overridden via `meta`."""
    parent_ids = tuple(p.alpha_id for p in parents)

    perf_delta = None
    if evaluation is not None and parents:
        child_m = getattr(evaluation, metric, None)
        parent_ms = [getattr(p.evaluation, metric, None) for p in parents if p.evaluation is not None]
        parent_ms = [m for m in parent_ms if m is not None]
        if child_m is not None and parent_ms:
            perf_delta = child_m - max(parent_ms)

    lineage = AlphaLineage(parent_ids=parent_ids, mutation_op=mutation_op, perf_delta=perf_delta)
    if "generator" not in meta and parents:
        meta["generator"] = parents[0].generator
    return AlphaAsset(alpha_id=alpha_id, expression=expression, evaluation=evaluation,
                      lineage=lineage, **meta)
