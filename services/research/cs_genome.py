"""Deterministic genome operators over cross-sectional alpha-expression trees (mass-production core).

`alpha_expression` evaluates nested-tuple cs-alphas; this module lets a search MACHINE-GENERATE them:
deterministic `mutate` / `crossover` that always return a VALID, cross-sectional (cs-op at the root),
depth-bounded expression. Determinism is by content hashing (hashlib only) — NO RNG, NO wall-clock — so
the same (tree, step) always yields the same child (reproducible search; honours the offline-core rule).
Every product is `valid()` and PIT-safe by construction (it only composes the engine's PIT-safe operators).

Used by the contained evolutionary/MCTS search to discover funding/price/volume cross-sectional alphas
beyond a hand-written grid, with the usual honesty machinery (DSR deflation, no-peek OOS, de-redundancy).

2026-07-09 (top-tier gap audit rank #2): imports _CS2 (cs_residualize, the DSL's only cross-sectional
NEUTRALIZATION primitive) so mutate/crossover can propose a candidate residualized against another
signal/field — e.g. "momentum, market-beta-neutralized". Before this, cs_residualize existed and was
tested but was UNREACHABLE by every automated search path (in-gate ResearchAgentLoop and out-of-gate
alpha_factory alike): every machine-discovered candidate was unconditionally a raw, un-neutralized
exposure, and neutralization was possible only if a human hand-wrote the expression. The growth path
(_mutate_node's kind==3) can now wrap a subtree in cs_residualize against a fresh atom (the same
atom-generation machinery _E2's binary growth already uses) as the neutralization target.
"""
from __future__ import annotations

import hashlib

from .alpha_expression import _TS_WINDOW, _CS, _CS2, _EW1, _EW2, _TS_PAIR, serialize, canonicalize, alpha_id  # noqa: F401

_TS = tuple(_TS_WINDOW)          # windowed unary ops (op, child, window)
_CSO = tuple(_CS)                # cross-sectional unary ops (op, child)
_CS2O = tuple(_CS2)              # cross-sectional binary/neutralization ops (op, child, target): cs_residualize
_E1 = tuple(_EW1)                # elementwise unary (op, child)
_E2 = tuple(_EW2)                # elementwise binary (op, a, b)
_TSP = tuple(_TS_PAIR)           # windowed pairwise ops (op, a, b, window): ts_corr/ts_cov/ts_beta
_MAX_DEPTH = 6
_CS_ROOTS = _CSO + _CS2O         # either kind is already a valid cross-sectional root (no extra cs_rank wrap needed)


def _hidx(seed: str, mod: int) -> int:
    """Deterministic index in [0, mod) from a string seed (no RNG)."""
    if mod <= 0:
        return 0
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % mod


def _depth(t) -> int:
    if not isinstance(t, tuple) or t[0] in ("field", "const"):
        return 1
    return 1 + max((_depth(a) for a in t[1:] if isinstance(a, tuple)), default=0)


def _nodes(t, path=()):           # (path, subtree) for every tuple node
    yield path, t
    for i, a in enumerate(t[1:], start=1):
        if isinstance(a, tuple):
            yield from _nodes(a, path + (i,))


def _replace(t, path, new):
    if not path:
        return new
    i = path[0]
    return t[:i] + (_replace(t[i], path[1:], new),) + t[i + 1:]


def valid(t, fields) -> bool:
    """Structurally valid, depth-bounded, cross-sectional (root is a cs op, unary or the
    cs_residualize neutralization binary), references only `fields`."""
    if _depth(t) > _MAX_DEPTH or not isinstance(t, tuple) or t[0] not in _CS_ROOTS:
        return False
    return _node_ok(t, fields)


def _node_ok(t, fields) -> bool:
    if not isinstance(t, tuple) or not t:
        return False
    h = t[0]
    if h == "field":
        return len(t) == 2 and t[1] in fields
    if h == "const":
        return len(t) == 2 and isinstance(t[1], (int, float))
    if h in _TS:
        return len(t) == 3 and isinstance(t[2], int) and t[2] >= 1 and _node_ok(t[1], fields)
    if h in _CSO or h in _E1:
        return len(t) == 2 and _node_ok(t[1], fields)
    if h in _E2 or h in _CS2O:
        return len(t) == 3 and _node_ok(t[1], fields) and _node_ok(t[2], fields)
    if h in _TSP:                # ts_corr/ts_cov/ts_beta share (op, a, b, window)
        return len(t) == 4 and isinstance(t[3], int) and t[3] >= 1 and _node_ok(t[1], fields) and _node_ok(t[2], fields)
    if h == "signed_power":
        return len(t) == 3 and isinstance(t[2], (int, float)) and _node_ok(t[1], fields)
    return False


def _ensure_cs(t):
    """Guarantee the output is cross-sectional: wrap in cs_rank if the root is not already a cs op
    (a unary _CSO op or the cs_residualize neutralization binary)."""
    return t if (isinstance(t, tuple) and t[0] in _CS_ROOTS) else ("cs_rank", t)


def _atom(fields, windows, seed):
    """A fresh leaf-ish subtree: a field, or a windowed-ts of a field."""
    f = ("field", fields[_hidx(seed + "f", len(fields))])
    if _hidx(seed + "a", 2) == 0:
        return f
    return (_TS[_hidx(seed + "t", len(_TS))], f, windows[_hidx(seed + "w", len(windows))])


def _mutate_node(sub, fields, windows, seed, grow_ok):
    kind = _hidx(seed + "k", 5)
    if kind == 0 and isinstance(sub, tuple) and sub[0] in _TS:               # change window
        return (sub[0], sub[1], windows[_hidx(seed + "nw", len(windows))])
    if kind == 0 and isinstance(sub, tuple) and sub[0] == "ts_corr":
        return (sub[0], sub[1], sub[2], windows[_hidx(seed + "nw", len(windows))])
    if kind == 1 and isinstance(sub, tuple):                                 # swap op (same arity)
        if sub[0] in _TS:
            return (_TS[_hidx(seed + "o", len(_TS))],) + sub[1:]
        if sub[0] in _CSO:
            return (_CSO[_hidx(seed + "o", len(_CSO))],) + sub[1:]
        if sub[0] in _E1:
            return (_E1[_hidx(seed + "o", len(_E1))],) + sub[1:]
        if sub[0] in _E2:
            return (_E2[_hidx(seed + "o", len(_E2))],) + sub[1:]
        if sub[0] in _CS2O:
            return (_CS2O[_hidx(seed + "o", len(_CS2O))],) + sub[1:]
    if kind == 2 and isinstance(sub, tuple) and sub[0] == "field":           # swap field
        return ("field", fields[_hidx(seed + "sf", len(fields))])
    if kind == 3 and grow_ok:                                                # wrap (grow depth by 1)
        w = _hidx(seed + "g", 5)
        if w == 0:
            return (_TS[_hidx(seed + "gt", len(_TS))], sub, windows[_hidx(seed + "gw", len(windows))])
        if w == 1:
            return (_CSO[_hidx(seed + "gc", len(_CSO))], sub)
        if w == 2:
            return (_E1[_hidx(seed + "ge", len(_E1))], sub)
        if w == 3:
            return (_E2[_hidx(seed + "gb", len(_E2))], sub, _atom(fields, windows, seed + "gba"))  # binary interaction
        # neutralize: residualize sub against a fresh atom (a raw field or windowed-ts of one) —
        # the search's only way to propose a risk-neutralized candidate (2026-07-09, gap audit).
        return (_CS2O[_hidx(seed + "gr", len(_CS2O))], sub, _atom(fields, windows, seed + "gra"))
    return _atom(fields, windows, seed + "r")                                # replace with fresh atom


def mutate(tree, fields, windows, step=0):
    """Deterministically mutate `tree` into a valid cross-sectional alpha (same inputs -> same output)."""
    nodes = list(_nodes(tree))
    seed = serialize(tree) + "|" + str(step)
    path, sub = nodes[_hidx(seed + "p", len(nodes))]
    child = _mutate_node(sub, fields, windows, seed, grow_ok=_depth(tree) < _MAX_DEPTH)
    out = _ensure_cs(_replace(tree, path, child))
    return out if valid(out, fields) and _depth(out) <= _MAX_DEPTH else tree


def crossover(a, b, fields, windows, step=0):
    """Splice a subtree of `b` into `a` deterministically; return a valid cross-sectional alpha."""
    a_nodes, b_nodes = list(_nodes(a)), list(_nodes(b))
    seed = serialize(a) + "#" + serialize(b) + "|" + str(step)
    path = a_nodes[_hidx(seed + "pa", len(a_nodes))][0]
    donor = b_nodes[_hidx(seed + "pb", len(b_nodes))][1]
    out = _ensure_cs(_replace(a, path, donor))
    return out if valid(out, fields) and _depth(out) <= _MAX_DEPTH else a


def seed_alphas(fields, windows):
    """A deterministic, valid, diverse seed population: each field through each cs op, raw and ts-wrapped."""
    out, seen = [], set()
    for f in fields:
        fe = ("field", f)
        for cs in _CSO:
            for cand in (
                (cs, fe),
                (cs, ("neg", fe)),
                (cs, ("ts_delta", fe, windows[min(1, len(windows) - 1)])),
                (cs, ("ts_mean", fe, windows[min(2, len(windows) - 1)])),
                (cs, ("decay_linear", fe, windows[min(2, len(windows) - 1)])),
            ):
                aid = alpha_id(cand)
                if aid not in seen and valid(cand, fields):
                    seen.add(aid)
                    out.append(cand)
    return out
