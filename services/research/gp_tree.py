"""Genetic-programming expression-tree alpha factors (machine-discovered STRUCTURE).

Generalizes the flat (operator, window) atom and the 2-leg interaction to arbitrary-depth
SCALAR expression trees:
  - LEAVES are the existing SIGNAL_OPS atoms `op(closes, window) -> Optional[float]` — the
    ONLY readers of the price series (the single PIT chokepoint), plus tiny int constants.
  - INTERNAL nodes are pure scalar combinators (neg, abs, add, sub, mul, protected-div,
    min, max). They never see the series, so no node can introduce lookahead.

A tree compiles to one `signal_fn(closes) -> Optional[float]` and is fed UNCHANGED into
`build_panel_periods`; the existing backtest / evaluator / catalog / Deflated-Sharpe /
lifecycle path runs byte-for-byte. A tree emits ONE scalar per instrument, so — unlike the
2-leg interaction — it needs NO per-period cross-sectional step; the panel backtest ranks
the cross-section downstream exactly as for any single-signal factor. We deliberately admit
NO cross-sectional / panel-level op into the per-instrument tree (that would smuggle
across-name lookahead).

Pure-Python stdlib, deterministic, fail-closed (any degenerate sub-result -> None -> the
instrument is dropped, never a fabricated value). Canonicalization is STRUCTURAL only
(sort commutative children; preserve sub/pdiv order) — no algebraic rewrites, because a
wrong rewrite could fold two DISTINCT hypotheses into one trial and under-count the
multiple-testing burden (over-counting is the safe direction). Each distinct canonical tree
serializes to a distinct `params['tree']` -> distinct definition_hash -> exactly ONE trial.
"""
from __future__ import annotations

import hashlib
import math
from typing import Callable, Optional, Sequence

from quantos.contracts.alpha import AlphaDefinition

from .panel_builder import build_panel_periods
from .signals import SIGNAL_OPS

LEAF_OPS = tuple(sorted(SIGNAL_OPS))
UNARY = ("abs1", "neg")
BINARY = ("add", "max2", "min2", "mul", "pdiv", "sub")
COMMUTATIVE = frozenset({"add", "mul", "min2", "max2"})
CONST_VOCAB = ("-1", "1", "2")
EPS = 1e-12
MAX_DEPTH_DEFAULT = 4
MAX_NODES_DEFAULT = 12

_UNARY_FN = {"neg": lambda a: -a, "abs1": abs}
_BINARY_FN = {"add": lambda a, b: a + b, "sub": lambda a, b: a - b,
              "mul": lambda a, b: a * b, "min2": min, "max2": max}


# ---------- structure helpers -------------------------------------------------
def tree_depth(tree) -> int:
    tag = tree[0]
    if tag in ("leaf", "const"):
        return 1
    if tag in UNARY:
        return 1 + tree_depth(tree[1])
    if tag in BINARY:
        return 1 + max(tree_depth(tree[1]), tree_depth(tree[2]))
    return 1


def tree_size(tree) -> int:
    tag = tree[0]
    if tag in ("leaf", "const"):
        return 1
    if tag in UNARY:
        return 1 + tree_size(tree[1])
    if tag in BINARY:
        return 1 + tree_size(tree[1]) + tree_size(tree[2])
    return 1


def validate_tree(tree, *, max_depth: int, max_nodes: int) -> None:
    """Fail-closed grammar check. Raises ValueError on unknown op/combinator/const, bad
    arity, non-int/<1 window, or depth/size past the bound. Called at the grammar boundary
    (tree_definition) and after every mutate/crossover candidate."""
    # structural/arity check FIRST so a malformed node raises ValueError (caught fail-closed
    # by the genome operators), never an IndexError from tree_depth/size indexing a missing child.
    _validate_node(tree)
    if tree_depth(tree) > max_depth:
        raise ValueError(f"tree depth {tree_depth(tree)} > max_depth {max_depth}")
    if tree_size(tree) > max_nodes:
        raise ValueError(f"tree size {tree_size(tree)} > max_nodes {max_nodes}")


def _validate_node(t) -> None:
    if not isinstance(t, tuple) or not t:
        raise ValueError(f"node must be a non-empty tuple: {t!r}")
    tag = t[0]
    if tag == "leaf":
        if len(t) != 3:
            raise ValueError(f"leaf arity: {t!r}")
        _, op, w = t
        if op not in SIGNAL_OPS:
            raise ValueError(f"unknown leaf op: {op!r}")
        try:
            iw = int(w)
        except (TypeError, ValueError):
            raise ValueError(f"leaf window not an int: {w!r}")
        if iw < 1:
            raise ValueError(f"leaf window must be >= 1: {w!r}")
    elif tag == "const":
        if len(t) != 2 or t[1] not in CONST_VOCAB:
            raise ValueError(f"bad const node: {t!r}")
    elif tag in UNARY:
        if len(t) != 2:
            raise ValueError(f"unary arity: {t!r}")
        _validate_node(t[1])
    elif tag in BINARY:
        if len(t) != 3:
            raise ValueError(f"binary arity: {t!r}")
        _validate_node(t[1])
        _validate_node(t[2])
    else:
        raise ValueError(f"unknown node tag: {tag!r}")


# ---------- evaluation (post-order, fail-closed) ------------------------------
def eval_tree(tree, closes: Sequence[float]) -> Optional[float]:
    """closes is the ONLY data input (the instrument's PIT history); combinators never see
    the series, so there is no lookahead path. Any None/non-finite sub-result -> None."""
    tag = tree[0]
    if tag == "leaf":
        fn = SIGNAL_OPS.get(tree[1])
        if fn is None:
            return None
        try:
            iw = int(tree[2])
        except (TypeError, ValueError):
            return None
        v = fn(closes, iw)
        return v if (v is not None and math.isfinite(v)) else None
    if tag == "const":
        try:
            v = float(tree[1])
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None
    if tag in UNARY:
        a = eval_tree(tree[1], closes)
        if a is None:
            return None
        v = _UNARY_FN[tag](a)
        return v if math.isfinite(v) else None
    if tag in BINARY:
        a = eval_tree(tree[1], closes)
        if a is None:
            return None
        b = eval_tree(tree[2], closes)
        if b is None:
            return None
        if tag == "pdiv":
            if abs(b) <= EPS:          # protected division: drop, never a sentinel
                return None
            v = a / b
        else:
            v = _BINARY_FN[tag](a, b)
        return v if math.isfinite(v) else None
    return None


def tree_signal_fn(tree) -> Callable[[Sequence[float]], Optional[float]]:
    return lambda closes: eval_tree(tree, closes)


# ---------- canonical form (STRUCTURAL only) ----------------------------------
def _ser(t) -> str:
    tag = t[0]
    if tag == "leaf":
        return f"(leaf {t[1]} {t[2]})"
    if tag == "const":
        return f"(const {t[1]})"
    if tag in UNARY:
        return f"({tag} {_ser(t[1])})"
    if tag in BINARY:
        return f"({tag} {_ser(t[1])} {_ser(t[2])})"
    raise ValueError(f"cannot serialize node: {t!r}")


def serialize(tree) -> str:
    """Injective S-expression over the fixed token alphabet [a-z0-9_()- space]; NEVER ':'
    (the reserved interaction-leg delimiter). Same tree -> same string; structurally
    distinct canonical trees -> distinct strings."""
    s = _ser(tree)
    if ":" in s:
        raise ValueError(f"serialized tree must not contain ':' : {s!r}")
    return s


def canonicalize(tree):
    """Order-stable normal form: recurse, then sort the two children of a COMMUTATIVE
    binary node by their serialization. sub/pdiv preserve operand order. Idempotent.
    NO algebraic simplification (a missed equivalence merely over-counts trials — safe)."""
    tag = tree[0]
    if tag == "leaf":
        # type-stable window AT THE CHOKEPOINT: int/str windows ('5' vs 5) collapse to one
        # canonical tuple, so the catalog trial count cannot be under-counted by a type variant
        # (trial-honesty no longer depends on every caller pre-stringifying the window).
        return ("leaf", tree[1], str(int(tree[2])))
    if tag == "const":
        return tree
    if tag in UNARY:
        return (tag, canonicalize(tree[1]))
    if tag in BINARY:
        left = canonicalize(tree[1])
        right = canonicalize(tree[2])
        if tag in COMMUTATIVE and _ser(left) > _ser(right):
            left, right = right, left
        return (tag, left, right)
    return tree


def parse(s: str):
    """Inverse of serialize: parse(serialize(canonicalize(t))) == canonicalize(t)."""
    toks = s.replace("(", " ( ").replace(")", " ) ").split()
    pos = 0

    def _p():
        nonlocal pos
        if pos >= len(toks) or toks[pos] != "(":
            raise ValueError("parse: expected '('")
        pos += 1
        tag = toks[pos]
        pos += 1
        if tag == "leaf":
            node = ("leaf", toks[pos], toks[pos + 1])
            pos += 2
        elif tag == "const":
            node = ("const", toks[pos])
            pos += 1
        elif tag in UNARY:
            node = (tag, _p())
        elif tag in BINARY:
            left = _p()
            right = _p()
            node = (tag, left, right)
        else:
            raise ValueError(f"parse: unknown tag {tag!r}")
        if pos >= len(toks) or toks[pos] != ")":
            raise ValueError("parse: expected ')'")
        pos += 1
        return node

    tree = _p()
    if pos != len(toks):
        raise ValueError("parse: trailing tokens")
    return tree


# ---------- AlphaDefinition codec (mirrors interaction_definition) ------------
def _short_hash(s: str, n: int = 16) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def tree_definition(tree, *, max_depth: int = MAX_DEPTH_DEFAULT,
                    max_nodes: int = MAX_NODES_DEFAULT) -> AlphaDefinition:
    """Encode a tree as an AlphaDefinition. alpha_id = 'hgp_' + bounded ':'-free hash of the
    canonical form; definition_hash keys on params['tree'] (the canonical string), so a
    distinct canonical tree is exactly ONE trial in the catalog."""
    t = canonicalize(tree)
    validate_tree(t, max_depth=max_depth, max_nodes=max_nodes)
    c = serialize(t)
    aid = "hgp_" + _short_hash(c, 16)
    return AlphaDefinition(alpha_id=aid, name=f"GP({c})", factor_ids=("close",),
                           params={"family": "gp_tree", "tree": c})


def tree_from_params(params: dict):
    return parse(params["tree"])


def make_tree_provider(price_store, instruments, *, event_times, fwd_horizon,
                       universe=None) -> Callable:
    """Tree AlphaDefinition -> PanelPeriods, reusing build_panel_periods VERBATIM (the tree
    is just its signal_fn). No new PIT code; the leaf atoms are the single price chokepoint."""
    def provider(defn):
        tree = tree_from_params(defn.params)
        return build_panel_periods(price_store, instruments, event_times=event_times,
                                   fwd_horizon=fwd_horizon, signal_fn=tree_signal_fn(tree),
                                   universe=universe)

    def _leakage_ok(defn):
        # 断点1 (方案A): the GP tree's signal is a trailing-closes function (eval_tree), so the REAL
        # detect_leakage applies — we VERIFY PIT-safety per discovered expression rather than asserting it.
        from services.research.leakage_evidence import leakage_ok_for_signal
        ets = list(event_times)
        lk_ets = ets[-80:] if len(ets) > 80 else ets
        if len(lk_ets) < 4:
            return None
        bars = [b for inst in sorted(instruments)
                for b in price_store.as_of(inst, lk_ets[-1]) if b.event_time >= lk_ets[0]]
        times = lk_ets[len(lk_ets) // 2: len(lk_ets) // 2 + 3]
        return leakage_ok_for_signal(tree_signal_fn(tree_from_params(defn.params)), bars, times)

    provider.leakage_ok = _leakage_ok
    return provider
