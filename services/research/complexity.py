"""Operator-weighted structural complexity for the cross-sectional alpha DSL (Phase 1).

The gp_tree search has a size/depth parsimony reward, but the engine had no general operator-weighted
complexity. This scores a tree by summing a per-operator cost (cheap elementwise < windowed/cross-
sectional < regression/pairwise), so heavier machinery (a cross-sectional regression, a two-window
correlation) counts for more than a bare add. It is Alpha-asset metadata and a parsimony / dedup
tie-break: prefer the cheaper, shallower of two otherwise-equal alphas. Pure stdlib, deterministic.
"""
from __future__ import annotations

from .alpha_expression import _CS, _CS2, _EW1, _EW2, _TS_PAIR, _TS_WINDOW


def _op_cost(head: str) -> float:
    if head in _EW1:
        return 0.5                       # neg/abs/sign/log/sqrt — cheap, pointwise
    if head in _EW2:
        return 1.0                       # add/sub/mul/div
    if head == "signed_power":
        return 1.5
    if head in _TS_WINDOW:
        return 1.5                       # windowed time-series reduction
    if head in _CS:
        return 1.5                       # cross-sectional transform
    if head in _CS2:
        return 3.0                       # cross-sectional regression (residualize)
    if head in _TS_PAIR:
        return 3.0                       # two-series windowed corr/cov/beta
    return 2.0                           # unknown -> conservative


def complexity(expr) -> float:
    """Operator-weighted structural cost: leaves are cheap (field 1.0, const 0.5), each operator adds
    its class cost, summed over the whole tree."""
    if not isinstance(expr, tuple) or not expr:
        return 0.0
    head = expr[0]
    if head == "field":
        return 1.0
    if head == "const":
        return 0.5
    total = _op_cost(head)
    for a in expr[1:]:
        if isinstance(a, tuple):
            total += complexity(a)
    return total


def node_count(expr) -> int:
    """Number of expression nodes (operators + leaves)."""
    if not isinstance(expr, tuple) or not expr:
        return 0
    return 1 + sum(node_count(a) for a in expr[1:] if isinstance(a, tuple))


def depth(expr) -> int:
    """Longest root-to-leaf path (a leaf has depth 1)."""
    if not isinstance(expr, tuple) or not expr or expr[0] in ("field", "const"):
        return 1
    return 1 + max((depth(a) for a in expr[1:] if isinstance(a, tuple)), default=0)
