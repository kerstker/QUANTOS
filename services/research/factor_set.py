"""Synergistic factor-set selection (de-redundancy via incremental IR).

The optimal factor SET is not the greedy top-N by single-factor IR: the second-best
factor is often a near-copy of the best and adds no information. This selects a set by
FORWARD selection on INCREMENTAL IR — each candidate is scored by the information ratio of
its return series AFTER projecting out the span of the already-selected set (reusing the
existing Gram-Schmidt `orthogonalize`). A redundant clone has ~0 residual IR and is
rejected; only factors carrying NEW, low-correlation information are kept.

Pure-Python, deterministic (ties broken by id), fail-closed (a factor whose incremental IR
cannot be honestly computed — too-thin overlap — is simply not selected, never fabricated).
Operates on per-factor net return series (e.g. AlphaCatalog.returns), so it composes with the
contained search WITHOUT changing it.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

from services.alpha.orthogonalize import orthogonalize


def incremental_ir(candidate_returns: Sequence[float],
                   basis: Mapping[str, tuple], *, min_overlap: int = 20) -> Optional[float]:
    """EFFECTIVE incremental IR vs span(basis): the residual information ratio weighted by
    the NOVEL-variance fraction (1 - explained_r2). A near-duplicate of a basis factor has a
    tiny, near-constant residual whose raw IR is misleadingly huge; weighting by novel variance
    collapses it to ~0, which is the economically correct redundancy signal. An empty basis
    yields the standalone IR (novelty = 1). None when it cannot be honestly assessed (too-thin
    overlap / degenerate), so an un-scored factor is never treated as informative."""
    res = orthogonalize(tuple(candidate_returns), dict(basis), min_overlap=min_overlap)
    if not res.ok or res.marginal_ir is None:
        return None
    novelty = 1.0 if res.explained_r2 is None else max(0.0, min(1.0, 1.0 - res.explained_r2))
    return res.marginal_ir * novelty


def novelty_vs_basis(candidate_returns: Sequence[float],
                     basis: Mapping[str, tuple], *, min_overlap: int = 20) -> Optional[float]:
    """Fraction of the candidate's variance NOT already spanned by the basis (1 - explained_r2),
    clamped to [0,1]: 1 = fully novel/orthogonal, 0 = a redundant clone. An empty basis -> 1.0.
    None when it cannot be honestly assessed (too-thin overlap). This is the per-candidate
    redundancy signal an incremental-discovery reward multiplies in."""
    res = orthogonalize(tuple(candidate_returns), dict(basis), min_overlap=min_overlap)
    if not res.ok:
        return None
    if res.explained_r2 is None:
        return 1.0
    return max(0.0, min(1.0, 1.0 - res.explained_r2))


def select_synergistic_set(returns_by_id: Mapping[str, Sequence[float]], *, max_k: int,
                           min_incremental_ir: float = 0.0, min_overlap: int = 20) -> dict:
    """Greedy forward selection by incremental IR vs the running selected set.

    Start empty; each step pick the not-yet-selected factor with the highest incremental IR
    (standalone IR on the first pick). Stop at `max_k`, or when the best incremental IR falls
    below `min_incremental_ir` (the rest are redundant / uninformative). Deterministic: on an
    IR tie the lexicographically smaller id wins. Returns the ordered selection (with each
    factor's incremental IR at the moment it was added) and the ids dropped as redundant."""
    if max_k < 1:
        raise ValueError("max_k must be >= 1")
    remaining = {aid: tuple(r) for aid, r in returns_by_id.items()}
    basis: dict[str, tuple] = {}
    selected = []
    while remaining and len(selected) < max_k:
        best_id, best_ir = None, None
        for aid in sorted(remaining):
            ir = incremental_ir(remaining[aid], basis, min_overlap=min_overlap)
            if ir is None:
                continue
            if best_ir is None or ir > best_ir:
                best_id, best_ir = aid, ir
        if best_id is None or best_ir < min_incremental_ir:
            break                                  # nothing left adds enough new information
        selected.append({"id": best_id, "incremental_ir": round(best_ir, 4)})
        basis[best_id] = remaining.pop(best_id)
    return {
        "method": "FORWARD_SELECTION_INCREMENTAL_IR",
        "n_candidates": len(returns_by_id),
        "max_k": max_k,
        "min_incremental_ir": min_incremental_ir,
        "min_overlap": min_overlap,
        "selected": selected,
        "selected_ids": [s["id"] for s in selected],
        "dropped_redundant": sorted(remaining),
    }
