"""Static Type + Dimension checker for the cross-sectional alpha DSL (Phase 1).

`evaluate()` is permissive: a malformed tree surfaces as an IndexError / KeyError deep inside the
interpreter. This module validates a tree UP FRONT — known operator, correct arity, scalar-slot
types (window int>=1, exponent number, field name str, const number), and matrix-slot positions
(a binary operand must be a sub-expression, not a bare scalar) — and returns every problem with a
path to the offending node. A search/generator can reject early; a console can show WHY.

Type/Dimension note: every panel matrix is (T x N) and every operator is shape-preserving, so the
DIMENSION check reduces to operand-POSITION validation (matrix-vs-scalar), which this performs. The
operator SIGNATURE table is derived from the engine registries (_TS_WINDOW/_CS/_CS2/_EW1/_EW2/
_TS_PAIR), so it cannot drift from what evaluate() actually dispatches. Pure stdlib, deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass

from .alpha_expression import _CS, _CS2, _EW1, _EW2, _TS_PAIR, _TS_WINDOW

# operand-position kinds
_E = "expr"        # a sub-expression (matrix)
_W = "window"      # an int >= 1
_N = "number"      # an int/float scalar

# head -> tuple of operand kinds, built from the engine registries (stays in sync automatically)
_SIG: dict[str, tuple] = {}
for _h in _TS_WINDOW:
    _SIG[_h] = (_E, _W)
for _h in (*_CS, *_EW1):
    _SIG[_h] = (_E,)
for _h in (*_CS2, *_EW2):
    _SIG[_h] = (_E, _E)
for _h in _TS_PAIR:
    _SIG[_h] = (_E, _E, _W)
_SIG["signed_power"] = (_E, _N)


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    errors: tuple


def _is_expr(x) -> bool:
    return isinstance(x, tuple) and len(x) >= 1 and isinstance(x[0], str)


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _check(expr, path: str, errs: list, fields) -> None:
    if not _is_expr(expr):
        errs.append(f"{path}: not an expression node: {expr!r}")
        return
    head, args = expr[0], expr[1:]

    if head == "field":
        if len(args) != 1:
            errs.append(f"{path}: 'field' takes 1 operand (name), got {len(args)}")
        elif not isinstance(args[0], str):
            errs.append(f"{path}: field name must be a str, got {args[0]!r}")
        elif fields is not None and args[0] not in fields:
            errs.append(f"{path}: unknown field {args[0]!r} (not in panel: {sorted(fields)})")
        return
    if head == "const":
        if len(args) != 1:
            errs.append(f"{path}: 'const' takes 1 operand (value), got {len(args)}")
        elif not _is_number(args[0]):
            errs.append(f"{path}: const value must be a number, got {args[0]!r}")
        return

    sig = _SIG.get(head)
    if sig is None:
        errs.append(f"{path}: unknown operator '{head}'")
        return
    if len(args) != len(sig):
        errs.append(f"{path}: '{head}' takes {len(sig)} operand(s), got {len(args)}")

    for i, kind in enumerate(sig):
        if i >= len(args):
            continue
        v = args[i]
        loc = f"{path}.{i + 1}"
        if kind == _E:
            if not _is_expr(v):
                errs.append(f"{loc}: '{head}' operand {i + 1} must be an expression, got {v!r}")
            else:
                _check(v, loc, errs, fields)
        elif kind == _W:
            if isinstance(v, bool) or not isinstance(v, int):
                errs.append(f"{loc}: '{head}' window must be an int, got {v!r}")
            elif v < 1:
                errs.append(f"{loc}: '{head}' window must be >= 1, got {v}")
        elif kind == _N:
            if not _is_number(v):
                errs.append(f"{loc}: '{head}' exponent must be a number, got {v!r}")


def check_expression(expr, *, fields=None) -> CheckResult:
    """Validate a DSL expression tree. Returns ok + a tuple of human-readable errors (each prefixed
    with a dotted path to the offending node, e.g. 'root.1.2').

    `fields` (optional set/iterable of field names knowable from the FieldPanel) enables an existence
    check: a ('field', name) whose name is not in `fields` is rejected up front instead of crashing
    evaluate() with a KeyError. Without `fields` the field check is structural only (name must be a
    str) — the checker cannot know which fields exist without a panel."""
    errs: list = []
    _check(expr, "root", errs, set(fields) if fields is not None else None)
    return CheckResult(ok=not errs, errors=tuple(errs))


def validate_expression(expr, *, fields=None) -> None:
    """Fail-fast variant: raise ValueError listing every problem, or return None if the tree is valid."""
    r = check_expression(expr, fields=fields)
    if not r.ok:
        raise ValueError("invalid alpha expression: " + "; ".join(r.errors))
