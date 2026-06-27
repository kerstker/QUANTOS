"""Alpha similarity across modalities (Alpha Factory upgrade Phase 3).

Near-duplicate detection needs more than one lens. This provides the modalities the audit found
missing, each deterministic, symmetric, and bounded:
  * expression_similarity — shared-subtree Jaccard over two DSL trees (structural / AST similarity);
  * return_similarity     — |Pearson| of two PnL series (high => redundant even if sign-inverted);
  * holdings_overlap      — cosine of two position-weight vectors (sign-aware: opposite books < 0);
  * exposure_similarity   — cosine of two style/factor-exposure vectors.

These feed the Alpha Correlation Graph (Phase 3) and the population optimizer (Phase 4). Pure-Python
stdlib, deterministic (no RNG, no wall-clock).
"""
from __future__ import annotations

import math
import statistics
from typing import Mapping, Sequence

_EPS = 1e-12


# ---- expression / AST ----
def _ser(node) -> str:
    """Deterministic serialization of a nested-tuple DSL node (atoms are str/num)."""
    if not isinstance(node, tuple):
        return repr(node)
    return "(" + " ".join(_ser(c) for c in node) + ")"


def _subtrees(node) -> set:
    """The set of serialized subtrees rooted at every tuple node (the structural 'shingles')."""
    out: set = set()
    if isinstance(node, tuple):
        out.add(_ser(node))
        for c in node:
            out |= _subtrees(c)
    return out


def expression_similarity(a, b) -> float:
    """Jaccard overlap of the two trees' subtree sets: |A∩B| / |A∪B|. 1.0 iff identical structure,
    0.0 if no subtree (down to leaves) is shared. A shared inner sub-expression lifts the score."""
    sa, sb = _subtrees(a), _subtrees(b)
    if not sa and not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


# ---- returns ----
def _pearson(a: Sequence[float], b: Sequence[float]):
    n = min(len(a), len(b))
    if n < 2:
        return None
    xa, xb = list(a[:n]), list(b[:n])
    sda, sdb = statistics.pstdev(xa), statistics.pstdev(xb)
    if sda <= _EPS or sdb <= _EPS:
        return None
    ma, mb = statistics.fmean(xa), statistics.fmean(xb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(xa, xb)) / n
    return cov / (sda * sdb)


def return_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """|Pearson correlation| of two return series — redundancy is high whether the second alpha is the
    same bet or its mirror. 0.0 when undefined (too short / a constant series)."""
    r = _pearson(a, b)
    return abs(r) if r is not None else 0.0


# ---- holdings / exposures (cosine over a shared key space) ----
def _cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na <= _EPS or nb <= _EPS:
        return 0.0
    return dot / (na * nb)


def holdings_overlap(weights_a: Mapping[str, float], weights_b: Mapping[str, float]) -> float:
    """Cosine of two position-weight vectors over the union of instruments. Sign-aware: identical books
    -> 1, disjoint -> 0, opposite (long vs short the same names) -> negative."""
    return _cosine(weights_a, weights_b)


def exposure_similarity(exp_a: Mapping[str, float], exp_b: Mapping[str, float]) -> float:
    """Cosine of two style/factor-exposure vectors (e.g. {'mom': .., 'value': ..})."""
    return _cosine(exp_a, exp_b)
