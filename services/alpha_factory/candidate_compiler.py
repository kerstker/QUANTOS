"""Candidate compiler (727 §8.2) — from raw DSL expression to a CompiledCandidate.

The compiler does NOT judge quality. It guarantees exactly two things: the
candidate's FORM is legal (the DSL's own fail-closed machinery accepts it) and
its IDENTITY is stable (canonicalize -> serialize -> alpha_id, so equivalent
expressions share one hash and the dedup layer has something solid to compare).

Validation mirrors the three-layer pattern of
`services/factor_garden/definitions.py::_validate_expression` (structure ->
serialize/deserialize round-trip -> synthetic-panel evaluate). It is a PARALLEL
implementation, deliberately not a shared extraction: the factor-garden copy
raises `FactorDefinitionError` pinned by tests/factor_garden, this one raises
`CandidateCompileError`, and merging the two would touch a landed slice for no
behavioral gain. Keep the two in lock-step if the pattern ever changes.

Deterministic core: no I/O, no wall clock, no RNG.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from services.research.alpha_expression import (
    FieldPanel,
    alpha_id,
    canonicalize,
    deserialize,
    evaluate,
    serialize,
)

from .hypothesis import Hypothesis


class CandidateCompileError(ValueError):
    """A candidate expression failed the fail-closed DSL form checks."""


def _expression_fields(expr) -> tuple[str, ...]:
    """Field names referenced anywhere in the nested-tuple expression (sorted)."""
    fields: list[str] = []

    def walk(node) -> None:
        if not isinstance(node, tuple) or not node:
            return
        if node[0] == "field":
            if len(node) != 2 or not isinstance(node[1], str):
                raise CandidateCompileError(f"malformed field node: {node!r}")
            fields.append(node[1])
            return
        for child in node[1:]:
            walk(child)

    walk(expr)
    return tuple(sorted(set(fields)))


def _validate_expression(expression) -> tuple:
    """Fail-closed structural + DSL validation; returns the canonical expression."""
    if (
        not isinstance(expression, tuple)
        or not expression
        or not isinstance(expression[0], str)
    ):
        raise CandidateCompileError(
            "expression must be a non-empty nested-tuple DSL expression"
        )
    try:
        canonical = canonicalize(expression)
        canonical_str = serialize(canonical)
        # The DSL's own fail-closed parser must accept its own canonical form back.
        if serialize(canonicalize(deserialize(canonical_str))) != canonical_str:
            raise CandidateCompileError(
                "expression failed serialize/deserialize round-trip"
            )
    except CandidateCompileError:
        raise
    except Exception as exc:
        raise CandidateCompileError(f"expression is not valid DSL: {exc}") from exc
    # Evaluate-validate over a synthetic 3x2 panel: pins operator names, arity,
    # and window/exponent argument types through the DSL's own fail-closed evaluator.
    panel = FieldPanel(
        ("I0", "I1"),
        (1, 2, 3),
        {f: [[1.0, 1.0]] * 3 for f in _expression_fields(canonical)},
    )
    try:
        evaluate(canonical, panel)
    except Exception as exc:
        raise CandidateCompileError(
            f"expression failed DSL evaluation: {exc}"
        ) from exc
    return canonical


def _normalize_params(params: Optional[Mapping[str, str]]) -> dict[str, str]:
    if params is None:
        return {}
    if not isinstance(params, Mapping):
        raise CandidateCompileError("params must be a mapping of strings or None")
    out: dict[str, str] = {}
    for key, value in params.items():
        if not isinstance(key, str) or not key.strip():
            raise CandidateCompileError("params keys must be non-empty strings")
        if not isinstance(value, str):
            raise CandidateCompileError(f"params[{key!r}] must be a string")
        out[key] = value
    return out


@dataclass(frozen=True)
class CompiledCandidate:
    """One form-legal, identity-stable candidate.

    `expression` is the CANONICAL nested-tuple form (commutative children
    sorted), so `canonical_expression`/`expression_hash` are reproducible from
    it and equivalent candidates collapse onto the same hash upstream in the
    dedup layer. `hypothesis_id` stays "" when the candidate was not bound to a
    pre-registered hypothesis — honest absence, never fabricated.
    """

    expression: tuple
    canonical_expression: str
    expression_hash: str
    hypothesis_id: str
    params: Mapping[str, str]
    compile_warnings: tuple[str, ...]


def compile_candidate(
    expr,
    *,
    hypothesis_id: str = "",
    params: Optional[Mapping[str, str]] = None,
) -> CompiledCandidate:
    """Compile a raw nested-tuple DSL expression. Form only, never quality."""
    canonical = _validate_expression(expr)
    if not isinstance(hypothesis_id, str):
        raise CandidateCompileError("hypothesis_id must be a string")
    return CompiledCandidate(
        expression=canonical,
        canonical_expression=serialize(canonical),
        expression_hash=alpha_id(canonical),
        hypothesis_id=hypothesis_id,
        params=_normalize_params(params),
        compile_warnings=(),
    )


def compile_from_hypothesis(
    hypothesis: Hypothesis,
    expr,
    *,
    params: Optional[Mapping[str, str]] = None,
) -> CompiledCandidate:
    """Compile a candidate BOUND to its pre-registered hypothesis (727 §8.1).

    The bound candidate records `hypothesis.hypothesis_id`. A candidate whose
    expression references NONE of the hypothesis's declared `required_factors`
    earns a HYPOTHESIS_FACTOR_MISMATCH WARNING — not a hard rejection: the
    expression may legitimately reach the hypothesized mechanism through
    intermediate transforms or renamed/derived fields, and a hard reject would
    false-kill exactly the candidates a human would want to inspect. The
    warning travels with the candidate so the audit trail keeps the mismatch
    visible instead of silently blessed.
    """
    if not isinstance(hypothesis, Hypothesis):
        raise CandidateCompileError("hypothesis must be a Hypothesis")
    candidate = compile_candidate(
        expr, hypothesis_id=hypothesis.hypothesis_id, params=params
    )
    declared = set(hypothesis.required_factors)
    referenced = set(_expression_fields(candidate.expression))
    if declared and not (declared & referenced):
        candidate = CompiledCandidate(
            expression=candidate.expression,
            canonical_expression=candidate.canonical_expression,
            expression_hash=candidate.expression_hash,
            hypothesis_id=candidate.hypothesis_id,
            params=candidate.params,
            compile_warnings=("HYPOTHESIS_FACTOR_MISMATCH",),
        )
    return candidate


__all__ = [
    "CandidateCompileError",
    "CompiledCandidate",
    "compile_candidate",
    "compile_from_hypothesis",
]
