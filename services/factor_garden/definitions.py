"""Factor definition construction (727 §6.1) — pure, no IO, no wall clock, no RNG.

The canonical expression form is the nested-tuple DSL of
``services.research.alpha_expression``: ``formula`` is its injective S-expression
(``serialize``) and ``expression_hash`` is ``alpha_id`` (equivalent expressions share
one identity via ``canonicalize``). The factor_engine string-AST compiler is NOT used
(dead/narrow — see docs/engineering/specs/2026-07-27-factor-garden-design.md).

Garden invariants (727 §6.1/§8.1), enforced fail-closed here AND again at the registry
boundary (defense in depth): a factor must carry a non-empty economic hypothesis (a
factor without an economic story is exactly what §8.1 forbids), a non-empty
required_datasets list, a real FactorFamily, and an expression the DSL itself accepts.

factor_version note: the widened contract deliberately does NOT duplicate the version
field — versions are carried by ``FeatureRegistryItem.version`` (the registry layer),
so ``build_factor_definition`` only validates ``factor_version`` non-empty; the caller
hands the same string to ``FactorGardenRegistry.register``.
"""
from __future__ import annotations

from typing import Optional, Sequence

from quantos.contracts.feature import (
    FactorFamily,
    FeatureDefinition,
    FeatureDType,
)
from quantos.contracts.hashing import stable_hash
from services.research.alpha_expression import (
    FieldPanel,
    alpha_id,
    canonicalize,
    deserialize,
    evaluate,
    serialize,
)


class FactorDefinitionError(ValueError):
    """A factor definition violates the 727 §6.1/§8.1 garden invariants."""


def _expression_fields(expr) -> tuple[str, ...]:
    """Field names referenced anywhere in the nested-tuple expression (sorted, deduped)."""
    fields: list[str] = []

    def walk(node) -> None:
        if not isinstance(node, tuple) or not node:
            return
        if node[0] == "field":
            if len(node) != 2 or not isinstance(node[1], str):
                raise FactorDefinitionError(f"malformed field node: {node!r}")
            fields.append(node[1])
            return
        for child in node[1:]:
            walk(child)

    walk(expr)
    return tuple(sorted(set(fields)))


def _validate_expression(expression) -> tuple:
    """Fail-closed structural + DSL validation; returns the canonical expression."""
    if not isinstance(expression, tuple) or not expression \
            or not isinstance(expression[0], str):
        raise FactorDefinitionError(
            "expression must be a non-empty nested-tuple DSL expression"
        )
    try:
        canonical = canonicalize(expression)
        canonical_str = serialize(canonical)
        # The DSL's own fail-closed parser must accept its own canonical form back.
        if serialize(canonicalize(deserialize(canonical_str))) != canonical_str:
            raise FactorDefinitionError("expression failed serialize/deserialize round-trip")
    except FactorDefinitionError:
        raise
    except Exception as exc:
        raise FactorDefinitionError(f"expression is not valid DSL: {exc}") from exc
    # Evaluate-validate over a synthetic 3x2 panel: pins operator names, arity, and
    # window/exponent argument types through the DSL's own fail-closed evaluator.
    panel = FieldPanel(
        ("I0", "I1"),
        (1, 2, 3),
        {f: [[1.0, 1.0]] * 3 for f in _expression_fields(canonical)},
    )
    try:
        evaluate(canonical, panel)
    except Exception as exc:
        raise FactorDefinitionError(f"expression failed DSL evaluation: {exc}") from exc
    return canonical


def _require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FactorDefinitionError(f"{field} must be a non-empty string")
    return value.strip()


def build_factor_definition(
    *,
    name: str,
    family: FactorFamily,
    expression: tuple,
    economic_hypothesis: str,
    required_datasets: Sequence[str],
    frequency: str,
    universe: str,
    minimum_lag: Optional[int] = None,
    normalization: str = "",
    neutralization: str = "",
    winsorization: str = "",
    missing_policy: str = "",
    expected_turnover: Optional[float] = None,
    known_failure_regimes: Sequence[str] = (),
    creator_actor: str,
    creator_model: str = "",
    prompt_hash: str = "",
    created_at_ms: int,
    factor_version: str = "1.0.0",
) -> FeatureDefinition:
    """Build a garden-conformant ``FeatureDefinition`` (727 §6.1); fail-closed."""
    name = _require_text(name, "name")
    if not isinstance(family, FactorFamily):
        raise FactorDefinitionError(f"family must be a FactorFamily, got {family!r}")
    hypothesis = _require_text(economic_hypothesis, "economic_hypothesis")
    _require_text(creator_actor, "creator_actor")
    datasets = tuple(required_datasets)
    if not datasets or any(not isinstance(d, str) or not d.strip() for d in datasets):
        raise FactorDefinitionError("required_datasets must be a non-empty list of dataset ids")
    if not isinstance(factor_version, str) or not factor_version.strip():
        raise FactorDefinitionError("factor_version must be a non-empty string")
    if minimum_lag is not None and (
        isinstance(minimum_lag, bool) or not isinstance(minimum_lag, int) or minimum_lag < 0
    ):
        raise FactorDefinitionError("minimum_lag must be a non-negative integer or None")
    if isinstance(created_at_ms, bool) or not isinstance(created_at_ms, int) \
            or created_at_ms < 0:
        raise FactorDefinitionError("created_at_ms must be a caller-supplied non-negative int ms")
    canonical = _validate_expression(expression)
    return FeatureDefinition(
        feature_id=name,                     # stable human identity; version carries evolution
        name=name,
        dtype=FeatureDType.FLOAT,            # contract convention (FeatureDType enum)
        formula=serialize(canonical),
        inputs=datasets,                     # upstream data ids (contract semantics)
        factor_family_id=family.value,
        expression_hash=alpha_id(canonical),
        economic_hypothesis=hypothesis,
        required_datasets=datasets,
        minimum_lag=minimum_lag,
        frequency=frequency,
        universe=universe,
        normalization=normalization,
        neutralization=neutralization,
        winsorization=winsorization,
        missing_policy=missing_policy,
        expected_turnover=expected_turnover,
        known_failure_regimes=tuple(known_failure_regimes),
        creator_actor=creator_actor.strip(),
        creator_model=creator_model,
        prompt_hash=prompt_hash,
        created_at_ms=created_at_ms,
    )


def factor_content_hash(defn: FeatureDefinition, *, factor_version: str = "") -> str:
    """stable_hash over the canonical identity fields — for change detection/audit.

    Only identity-bearing fields participate: name, family, expression_hash,
    factor_version (registry-carried, passed by the caller), required_datasets.
    Provenance/observability fields (created_at_ms, prompt_hash, status, …) must NOT
    change the content hash.
    """
    if not isinstance(defn, FeatureDefinition):
        raise TypeError("defn must be a FeatureDefinition")
    return stable_hash({
        "name": defn.name,
        "factor_family_id": defn.factor_family_id or "",
        "expression_hash": defn.expression_hash,
        "factor_version": factor_version,
        "required_datasets": list(defn.required_datasets),
    })
