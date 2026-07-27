"""Economic plausibility rules for hypotheses (727 §8.1) — reusable checker.

`create_hypothesis` enforces these rules at construction (a bad hypothesis can
never exist as a value object). This module exists SEPARATELY so the search
controller (P5-2) can re-check hypotheses it did NOT construct — e.g. ones
loaded back from the hypothesis store or handed in as structured data from a
tools/ driver — through exactly the same rule set.

Each rule has a stable machine-readable code. `check_hypothesis_rules` returns
the tuple of VIOLATED codes; an empty tuple means the hypothesis passes.

Calibration honesty: HYP_VAGUE_FALSIFICATION is a HEURISTIC (UNCALIBRATED) — a
criterion is flagged when it is shorter than 10 characters or contains neither
a digit nor a comparator hint. It can false-positive on a genuinely measurable
criterion written tersely; that direction is safe (construction refuses, a human
rewords). It must never be cited as proof that a criterion IS measurable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # one-directional dependency: hypothesis.py imports this module
    from .hypothesis import Hypothesis

#: Minimum length for an economic mechanism narrative. "Sharpe 可能更高" style
#: one-liners cannot clear it; a real mechanism (who is on the other side, why
#: does the premium exist, what keeps it from being arbitraged away) cannot be
#: stated in fewer characters. Documented heuristic, UNCALIBRATED.
MIN_MECHANISM_CHARS = 20

#: A falsification criterion shorter than this cannot name a measurable
#: condition (metric + threshold + window). Heuristic, UNCALIBRATED.
MIN_CRITERION_CHARS = 10

#: Comparator / magnitude hints that mark a criterion as measurable. A digit
#: (threshold, horizon, percentile) or a comparison operator. Heuristic,
#: UNCALIBRATED — see module docstring.
_COMPARATOR_HINTS = ("<", ">", "=", "%")


def _is_vague_criterion(criterion: str) -> bool:
    if len(criterion) < MIN_CRITERION_CHARS:
        return True
    has_digit = any(ch.isdigit() for ch in criterion)
    has_comparator = any(hint in criterion for hint in _COMPARATOR_HINTS)
    return not (has_digit or has_comparator)


def check_hypothesis_rules(hypothesis: "Hypothesis") -> tuple[str, ...]:
    """Return the violated rule codes (empty tuple = pass). Deterministic."""
    violations: list[str] = []
    if len(hypothesis.economic_mechanism) < MIN_MECHANISM_CHARS:
        violations.append("HYP_MECHANISM_TOO_SHORT")
    if not hypothesis.falsification_criteria:
        violations.append("HYP_NO_FALSIFICATION")
    if not hypothesis.expected_direction:
        violations.append("HYP_NO_DIRECTION")
    if not hypothesis.required_factors:
        violations.append("HYP_NO_FACTORS")
    if any(_is_vague_criterion(c) for c in hypothesis.falsification_criteria):
        violations.append("HYP_VAGUE_FALSIFICATION")
    if not hypothesis.creator_model:
        violations.append("HYP_NO_PROVENANCE")
    return tuple(violations)


__all__ = [
    "MIN_CRITERION_CHARS",
    "MIN_MECHANISM_CHARS",
    "check_hypothesis_rules",
]
