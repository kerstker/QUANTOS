"""Deterministic genetic operators on GP expression trees (no RNG, no clock).

Separated from gp_tree.py so the grammar/eval core stays operator-free. Same contract as
evolutionary_search.mutate/crossover: enumerate structural sites, return a SORTED, de-duped
list of CANONICAL neighbour trees, never including the (canonicalized) input itself, and
DROP (never emit) any candidate that violates the depth/node bound (fail-closed). Growth
comes only from crossover (subtree splice); mutate offers point edits + a SHRINK that
replaces an internal node by one of its children — so the per-call neighbour set is finite
and reproducible.
"""
from __future__ import annotations

from typing import Sequence

from .gp_tree import (canonicalize, serialize, validate_tree, tree_depth, tree_size,
                      LEAF_OPS, UNARY, BINARY, CONST_VOCAB)


def enumerate_nodes(tree) -> list:
    """Stable pre-order (index, subtree) pairs (reproducible site positions)."""
    nodes: list = []

    def walk(t):
        nodes.append(t)
        tag = t[0]
        if tag in UNARY:
            walk(t[1])
        elif tag in BINARY:
            walk(t[1])
            walk(t[2])

    walk(tree)
    return list(enumerate(nodes))


def _replace_at(tree, index: int, new_subtree):
    """Functional splice at the given pre-order index (matches enumerate_nodes order)."""
    counter = [0]

    def rec(t):
        i = counter[0]
        counter[0] += 1
        if i == index:
            return new_subtree
        tag = t[0]
        if tag in UNARY:
            return (tag, rec(t[1]))
        if tag in BINARY:
            left = rec(t[1])
            right = rec(t[2])
            return (tag, left, right)
        return t

    return rec(tree)


def _accept(candidate, base, out: set, *, max_depth: int, max_nodes: int) -> None:
    try:
        ct = canonicalize(candidate)
        validate_tree(ct, max_depth=max_depth, max_nodes=max_nodes)
    except (ValueError, IndexError, TypeError):
        return                                    # fail-closed: drop any malformed/oversized candidate
    if ct != base:
        out.add(ct)


def point_and_subtree_mutate(tree, vocab, *, max_depth: int, max_nodes: int) -> list:
    base = canonicalize(tree)
    windows = tuple(vocab.get("windows", ("3", "5", "10")))
    out: set = set()
    for idx, sub in enumerate_nodes(tree):
        tag = sub[0]
        cands = []
        if tag == "leaf":
            _, op, w = sub
            cands += [("leaf", op2, w) for op2 in LEAF_OPS if op2 != op]
            cands += [("leaf", op, w2) for w2 in windows if w2 != w]
        elif tag == "const":
            cands += [("const", k2) for k2 in CONST_VOCAB if k2 != sub[1]]
        elif tag in UNARY:
            cands += [(c2, sub[1]) for c2 in UNARY if c2 != tag]
            cands.append(sub[1])                  # SHRINK: node -> its child
        elif tag in BINARY:
            cands += [(c2, sub[1], sub[2]) for c2 in BINARY if c2 != tag]
            cands.append(sub[1])                  # SHRINK: node -> left child
            cands.append(sub[2])                  # SHRINK: node -> right child
        for c in cands:
            _accept(_replace_at(tree, idx, c), base, out, max_depth=max_depth, max_nodes=max_nodes)
    return sorted(out, key=serialize)


def crossover(tree1, tree2, vocab, *, max_depth: int, max_nodes: int) -> list:
    base = canonicalize(tree1)
    out: set = set()
    sites2 = enumerate_nodes(tree2)
    for idxA, _subA in enumerate_nodes(tree1):
        for _idxB, subB in sites2:
            _accept(_replace_at(tree1, idxA, subB), base, out,
                    max_depth=max_depth, max_nodes=max_nodes)
    return sorted(out, key=serialize)


def parsimony_penalty(tree, lam: float, mu: float) -> float:
    """Bloat pressure for the SEARCH-selection reward only (never the honest evaluation)."""
    return lam * tree_size(tree) + mu * tree_depth(tree)
