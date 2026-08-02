"""Falsifiable Hypothesis value object (727 §8.1) — the pre-registered claim.

727 §8.1 forbids candidates whose only justification is "Sharpe 可能更高": every
alpha search must start from an explicit, falsifiable economic hypothesis. This
module is the pure domain half of that rule: a frozen `Hypothesis` carrying every
§8.1 field, a content-derived identity, and a fail-closed builder.

No I/O, no wall clock, no RNG, no uuid — `created_at_ms` is caller-supplied and
`hypothesis_id` is derived from the hypothesis CONTENT ("hyp_" + sha256[:16]),
so a hypothesis is immutable by construction: changing any content field yields
a NEW hypothesis_id, which is exactly the pre-registration semantics §8.1 wants
(a hypothesis that was edited after the trials ran is not the hypothesis the
trials tested).

Identity-hash scope (mirrors the factor-garden content-hash decision):
`created_at_ms` and `prompt_hash` are PROVENANCE of the act of writing the
hypothesis down, not part of the claim itself, so they do not enter the identity
hash. `creator_model` DOES — the same claim written by a different proposer is a
different pre-registration lineage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from quantos.contracts.hashing import stable_hash


class HypothesisError(ValueError):
    """A hypothesis failed a fail-closed §8.1 construction rule."""


_MAX_TEXT = 1024


def _text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise HypothesisError(f"{field} must be a string")
    if len(value) > _MAX_TEXT:
        raise HypothesisError(f"{field} exceeds {_MAX_TEXT} characters")
    if value != value.strip():
        raise HypothesisError(f"{field} must not have surrounding whitespace")
    if not value and not allow_empty:
        raise HypothesisError(f"{field} must not be empty")
    return value


def _time(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HypothesisError(f"{field} must be a non-negative integer")
    return value


def _tuple_of_text(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise HypothesisError(f"{field} must be a sequence of strings")
    return tuple(_text(item, f"{field}[]") for item in value)


@dataclass(frozen=True)
class Hypothesis:
    """One immutable falsifiable hypothesis (727 §8.1).

    `hypothesis_id` is content-derived by `create_hypothesis`; construct through
    the builder, never by hand — the builder is the fail-closed gate.
    """

    hypothesis_id: str
    economic_mechanism: str
    expected_direction: str
    expected_horizon: str
    required_factors: tuple[str, ...]
    market_scope: str
    regime_assumptions: tuple[str, ...]
    expected_cost_sensitivity: str
    capacity_assumptions: str
    falsification_criteria: tuple[str, ...]
    known_risks: tuple[str, ...]
    creator_model: str
    prompt_hash: str
    created_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "hypothesis_id", _text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(
            self,
            "economic_mechanism",
            _text(self.economic_mechanism, "economic_mechanism"),
        )
        object.__setattr__(
            self,
            "expected_direction",
            _text(self.expected_direction, "expected_direction"),
        )
        object.__setattr__(
            self,
            "expected_horizon",
            _text(self.expected_horizon, "expected_horizon"),
        )
        object.__setattr__(
            self, "market_scope", _text(self.market_scope, "market_scope")
        )
        object.__setattr__(
            self,
            "expected_cost_sensitivity",
            _text(self.expected_cost_sensitivity, "expected_cost_sensitivity"),
        )
        object.__setattr__(
            self,
            "capacity_assumptions",
            _text(self.capacity_assumptions, "capacity_assumptions"),
        )
        object.__setattr__(
            self, "creator_model", _text(self.creator_model, "creator_model")
        )
        object.__setattr__(
            self,
            "prompt_hash",
            _text(self.prompt_hash, "prompt_hash", allow_empty=True),
        )
        object.__setattr__(
            self, "created_at_ms", _time(self.created_at_ms, "created_at_ms")
        )
        object.__setattr__(
            self,
            "required_factors",
            _tuple_of_text(self.required_factors, "required_factors"),
        )
        object.__setattr__(
            self,
            "regime_assumptions",
            _tuple_of_text(self.regime_assumptions, "regime_assumptions"),
        )
        object.__setattr__(
            self,
            "falsification_criteria",
            _tuple_of_text(self.falsification_criteria, "falsification_criteria"),
        )
        object.__setattr__(
            self, "known_risks", _tuple_of_text(self.known_risks, "known_risks")
        )

    def canonical_payload(self) -> dict[str, Any]:
        """Deterministic dict for hashing and durable storage."""
        return {
            "capacity_assumptions": self.capacity_assumptions,
            "created_at_ms": self.created_at_ms,
            "creator_model": self.creator_model,
            "economic_mechanism": self.economic_mechanism,
            "expected_cost_sensitivity": self.expected_cost_sensitivity,
            "expected_direction": self.expected_direction,
            "expected_horizon": self.expected_horizon,
            "falsification_criteria": list(self.falsification_criteria),
            "hypothesis_id": self.hypothesis_id,
            "known_risks": list(self.known_risks),
            "market_scope": self.market_scope,
            "prompt_hash": self.prompt_hash,
            "regime_assumptions": list(self.regime_assumptions),
            "required_factors": list(self.required_factors),
        }

    def identity_fields(self) -> dict[str, Any]:
        """The CONTENT of the claim — what the identity hash covers.

        `created_at_ms` / `prompt_hash` are deliberately excluded (provenance of
        the writing act, not the claim); everything else, including
        `creator_model`, is in.
        """
        payload = self.canonical_payload()
        payload.pop("hypothesis_id")
        payload.pop("created_at_ms")
        payload.pop("prompt_hash")
        return payload


def hypothesis_content_hash(hypothesis: Hypothesis) -> str:
    """Stable content hash over the identity fields (audit / change detection)."""
    return stable_hash(hypothesis.identity_fields())


def create_hypothesis(
    *,
    economic_mechanism: str,
    expected_direction: str,
    expected_horizon: str,
    required_factors: Sequence[str],
    market_scope: str,
    regime_assumptions: Sequence[str] = (),
    expected_cost_sensitivity: str,
    capacity_assumptions: str,
    falsification_criteria: Sequence[str],
    known_risks: Sequence[str] = (),
    creator_model: str,
    prompt_hash: str = "",
    created_at_ms: int,
) -> Hypothesis:
    """Build a Hypothesis fail-closed (727 §8.1).

    The content-derived id is computed AFTER full validation, and the economic
    rules (`economic_rules.check_hypothesis_rules`) are enforced here so a
    hypothesis that is only "Sharpe 可能更高" can never exist as a value object.
    `creator_model` is provenance: "human" is allowed for human-authored
    hypotheses; an LLM-produced hypothesis may only ever ENTER the system as
    structured data via tools/ — no LLM client lives in services/.
    """
    candidate = Hypothesis(
        hypothesis_id="hyp_pending",  # replaced below once content validates
        economic_mechanism=economic_mechanism,
        expected_direction=expected_direction,
        expected_horizon=expected_horizon,
        required_factors=tuple(required_factors),
        market_scope=market_scope,
        regime_assumptions=tuple(regime_assumptions),
        expected_cost_sensitivity=expected_cost_sensitivity,
        capacity_assumptions=capacity_assumptions,
        falsification_criteria=tuple(falsification_criteria),
        known_risks=tuple(known_risks),
        creator_model=creator_model,
        prompt_hash=prompt_hash,
        created_at_ms=created_at_ms,
    )
    # Deferred import: economic_rules depends on this module's Hypothesis type
    # for checking; importing here keeps the dependency one-directional.
    from .economic_rules import check_hypothesis_rules

    violations = check_hypothesis_rules(candidate)
    if violations:
        raise HypothesisError(
            "hypothesis violates 727 §8.1 economic rules: " + ", ".join(violations)
        )
    hypothesis_id = "hyp_" + hypothesis_content_hash(candidate)[:16]
    return Hypothesis(
        hypothesis_id=hypothesis_id,
        economic_mechanism=candidate.economic_mechanism,
        expected_direction=candidate.expected_direction,
        expected_horizon=candidate.expected_horizon,
        required_factors=candidate.required_factors,
        market_scope=candidate.market_scope,
        regime_assumptions=candidate.regime_assumptions,
        expected_cost_sensitivity=candidate.expected_cost_sensitivity,
        capacity_assumptions=candidate.capacity_assumptions,
        falsification_criteria=candidate.falsification_criteria,
        known_risks=candidate.known_risks,
        creator_model=candidate.creator_model,
        prompt_hash=candidate.prompt_hash,
        created_at_ms=candidate.created_at_ms,
    )
