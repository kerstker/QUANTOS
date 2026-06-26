"""Frequent-subtree avoidance: structural-diversity statistics over GP expression trees.

Return-based de-redundancy ([[factor_set]]) catches factors whose PnL is redundant. This
catches structural homogenisation: a search that keeps re-finding the same FORMULA SHAPE. We
count how often each canonical sub-expression recurs across discovered trees; a candidate
whose sub-expressions are already common is "structurally common", and an opt-in search-reward
penalty biases the search toward novel structures (mechanism from arXiv 2505.11122).

Pure-Python, deterministic. Operates on the canonical serialization of every subtree, so it
composes with the existing GP grammar without touching it.
"""
from __future__ import annotations

from typing import Mapping

from .gp_tree import serialize, UNARY, BINARY


def subtree_keys(tree) -> list:
    """Canonical serialization of EVERY subtree (the whole tree + each descendant), so a shape
    that recurs anywhere is counted. Leaves/consts included."""
    out = []

    def walk(t):
        out.append(serialize(t))
        tag = t[0]
        if tag in UNARY:
            walk(t[1])
        elif tag in BINARY:
            walk(t[1])
            walk(t[2])

    walk(tree)
    return out


def update_corpus(corpus: dict, tree) -> None:
    """Record one tree's DISTINCT subtree shapes (each counted once per tree, so a tree using
    the same leaf twice does not double-count its own structure)."""
    for k in set(subtree_keys(tree)):
        corpus[k] = corpus.get(k, 0) + 1


def structural_commonness(tree, corpus: Mapping[str, int]) -> float:
    """Mean over the tree's subtrees of saturation 1 - 1/(1+count): 0 for an all-novel tree,
    -> 1 as its sub-shapes recur. In [0,1); needs no corpus total (self-normalising)."""
    keys = subtree_keys(tree)
    if not keys:
        return 0.0
    return sum(1.0 - 1.0 / (1.0 + corpus.get(k, 0)) for k in keys) / len(keys)
