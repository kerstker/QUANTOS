"""Constrained search space (727 §8.2) — the DSL sub-space a search may propose in.

727 §8.2: AI may only generate candidates inside a restricted, explainable,
auditable DSL. This module is the machine half of that rule: a frozen
`SearchSpaceConstraints` declaring which fields/ops/depths/windows are in
scope, a pure `check_expression` returning stable violation codes, and a
deliberately SMALL deterministic `enumerate_space` for grid-style families.

Default-deny mapping (§8.2「禁止或默认拒绝」):
- "超出数据可用时间的窗口" -> SPACE_WINDOW_OUT_OF_RANGE (max_window is a
  REQUIRED constraint field: the caller must state the data-availability
  bound; there is no invented default).
- "无经济解释的深层随机表达式 / 无法解释的高阶嵌套" -> max_depth +
  SPACE_TOO_DEEP, and require_economic_root (the root must be a cross-sectional
  op — the only root forms with a portfolio meaning) + SPACE_ROOT_NOT_ECONOMIC.
- "依赖未来信息" is structurally impossible in this DSL (every operator is
  PIT-safe by construction, see cs_genome) — no code needed, documented here.

cs_genome is NOT modified: op-name sets come from alpha_expression's public
constants; `_depth` is a 4-line parallel implementation (importing the private
cs_genome._depth would couple this slice to a private it does not own).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional, Sequence

from services.research.alpha_expression import (
    _CS,
    _CS2,
    _EW1,
    _EW2,
    _TS_PAIR,
    _TS_WINDOW,
    alpha_id,
    canonicalize,
)

#: The op universe cs_genome can compose (its _TS/_CSO/_CS2O/_E1/_E2/_TSP sets).
#: `signed_power` is deliberately excluded: the DSL evaluator supports it but
#: cs_genome never generates it, so the default space stays exactly the
#: machine-searchable universe. Callers may add it explicitly.
DEFAULT_ALLOWED_OPS: tuple[str, ...] = tuple(
    sorted(set(_TS_WINDOW) | set(_CS) | set(_CS2) | set(_EW1) | set(_EW2) | set(_TS_PAIR))
)

_KNOWN_OPS = frozenset(DEFAULT_ALLOWED_OPS) | {"signed_power", "field", "const"}
_CS_ROOT_OPS = frozenset(_CS) | frozenset(_CS2)


class SearchSpaceError(ValueError):
    """A constraint declaration is itself invalid (fail-closed construction)."""


def _depth(tree) -> int:
    """Expression depth. Parallel implementation of cs_genome._depth (4 lines);
    kept local so this slice does not import a private it does not own."""
    if not isinstance(tree, tuple) or tree[0] in ("field", "const"):
        return 1
    return 1 + max((_depth(a) for a in tree[1:] if isinstance(a, tuple)), default=0)


def _windows(tree) -> Iterator[int]:
    """Every window argument in the tree (ts-window ops and windowed pairs)."""
    if not isinstance(tree, tuple) or not tree:
        return
    if tree[0] in _TS_WINDOW and len(tree) == 3 and isinstance(tree[2], int):
        yield tree[2]
    elif tree[0] in _TS_PAIR and len(tree) == 4 and isinstance(tree[3], int):
        yield tree[3]
    for child in tree[1:]:
        if isinstance(child, tuple):
            yield from _windows(child)


def _fields(tree) -> Iterator[str]:
    if not isinstance(tree, tuple) or not tree:
        return
    if tree[0] == "field" and len(tree) == 2 and isinstance(tree[1], str):
        yield tree[1]
        return
    for child in tree[1:]:
        if isinstance(child, tuple):
            yield from _fields(child)


def _ops(tree) -> Iterator[str]:
    if not isinstance(tree, tuple) or not tree:
        return
    head = tree[0]
    if head not in ("field", "const") and isinstance(head, str):
        yield head
    for child in tree[1:]:
        if isinstance(child, tuple):
            yield from _ops(child)


@dataclass(frozen=True)
class SearchSpaceConstraints:
    """The restricted DSL sub-space (727 §8.2). Construction is fail-closed.

    `max_window` has NO default on purpose: it is the data-availability bound
    (§8.2「超出数据可用时间的窗口」默认拒绝) and only the caller knows how much
    data the search actually has. `allowed_ops` defaults to the cs_genome
    universe; declaring an unknown op is rejected at construction.
    """

    allowed_fields: tuple[str, ...]
    max_window: int
    allowed_ops: tuple[str, ...] = DEFAULT_ALLOWED_OPS
    max_depth: int = 6
    min_window: int = 2
    require_economic_root: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.allowed_fields, str) or not isinstance(
            self.allowed_fields, Sequence
        ):
            raise SearchSpaceError("allowed_fields must be a sequence of strings")
        fields = tuple(self.allowed_fields)
        if not fields or any(
            not isinstance(f, str) or not f.strip() for f in fields
        ):
            raise SearchSpaceError("allowed_fields must be non-empty strings")
        object.__setattr__(self, "allowed_fields", fields)

        if isinstance(self.allowed_ops, str) or not isinstance(
            self.allowed_ops, Sequence
        ):
            raise SearchSpaceError("allowed_ops must be a sequence of strings")
        ops = tuple(self.allowed_ops)
        unknown = sorted(set(ops) - _KNOWN_OPS)
        if unknown:
            raise SearchSpaceError(f"allowed_ops contains unknown DSL ops: {unknown}")
        object.__setattr__(self, "allowed_ops", ops)

        for name in ("max_window", "max_depth", "min_window"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise SearchSpaceError(f"{name} must be a positive integer")
        if self.min_window > self.max_window:
            raise SearchSpaceError("min_window must not exceed max_window")
        if not isinstance(self.require_economic_root, bool):
            raise SearchSpaceError("require_economic_root must be a bool")


def check_expression(expr, constraints: SearchSpaceConstraints) -> tuple[str, ...]:
    """Return the violated space codes (empty tuple = in-space). Deterministic.

    Codes are deduplicated in a fixed check order (root, depth, then a
    pre-order walk for op/field/window) so the same expression always yields
    the same tuple — the codes land in TrialRecord failure_reasons.
    """
    if not isinstance(constraints, SearchSpaceConstraints):
        raise SearchSpaceError("constraints must be a SearchSpaceConstraints")
    violations: list[str] = []

    def add(code: str) -> None:
        if code not in violations:
            violations.append(code)

    if not isinstance(expr, tuple) or not expr or not isinstance(expr[0], str):
        return ("SPACE_OP_NOT_ALLOWED",)
    if constraints.require_economic_root and expr[0] not in _CS_ROOT_OPS:
        add("SPACE_ROOT_NOT_ECONOMIC")
    if _depth(expr) > constraints.max_depth:
        add("SPACE_TOO_DEEP")
    for op in _ops(expr):
        if op not in constraints.allowed_ops:
            add("SPACE_OP_NOT_ALLOWED")
    for field in _fields(expr):
        if field not in constraints.allowed_fields:
            add("SPACE_FIELD_NOT_ALLOWED")
    for window in _windows(expr):
        if window > constraints.max_window or window < constraints.min_window:
            add("SPACE_WINDOW_OUT_OF_RANGE")
    return tuple(violations)


def _window_ladder(min_window: int, max_window: int) -> tuple[int, ...]:
    """Coarse geometric window grid inside [min_window, max_window]:
    min_window, doubling, and always max_window (e.g. 2..60 -> 2,4,8,16,32,60).
    Deliberately coarse — fine window sweeps are a driver's pre-registered
    family, not this enumerator's job."""
    ladder = [min_window]
    while ladder[-1] * 2 <= max_window:
        ladder.append(ladder[-1] * 2)
    if ladder[-1] != max_window:
        ladder.append(max_window)
    return tuple(ladder)


def enumerate_space(
    constraints: SearchSpaceConstraints, *, max_candidates: int
) -> Iterator[tuple]:
    """Deterministically enumerate the two shallow candidate shapes in-space:

      depth-2: (ts_op (field f) window)
      depth-3: (cs_op (ts_op (field f) window))   <- the only cs-rooted shape

    Only these two layers are enumerated (deeper nesting is the evolutionary
    engine's job, not a grid's), which bounds the family at
    |fields| x |ts_ops| x |windows| x (1 + |cs_ops|) BEFORE the max_candidates
    hard truncation. Every yielded expression passes check_expression for the
    given constraints (with require_economic_root=True the depth-2 ts-rooted
    shape is filtered out) and is alpha_id-deduplicated. Order is fully
    deterministic: sorted fields x sorted ops x ascending windows.
    """
    if not isinstance(constraints, SearchSpaceConstraints):
        raise SearchSpaceError("constraints must be a SearchSpaceConstraints")
    if (
        isinstance(max_candidates, bool)
        or not isinstance(max_candidates, int)
        or max_candidates < 1
    ):
        raise SearchSpaceError("max_candidates must be a positive integer")

    fields = sorted(set(constraints.allowed_fields))
    ts_ops = sorted(set(constraints.allowed_ops) & set(_TS_WINDOW))
    cs_ops = sorted(set(constraints.allowed_ops) & _CS_ROOT_OPS)
    windows = _window_ladder(constraints.min_window, constraints.max_window)

    seen: set[str] = set()
    emitted = 0
    for field in fields:
        leaf = ("field", field)
        for ts_op in ts_ops:
            for window in windows:
                ts_expr = (ts_op, leaf, window)
                for candidate in (ts_expr, *[(cs_op, ts_expr) for cs_op in cs_ops]):
                    if emitted >= max_candidates:
                        return
                    if check_expression(candidate, constraints):
                        continue
                    identity = alpha_id(canonicalize(candidate))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    emitted += 1
                    yield candidate


__all__ = [
    "DEFAULT_ALLOWED_OPS",
    "SearchSpaceConstraints",
    "SearchSpaceError",
    "check_expression",
    "enumerate_space",
]
