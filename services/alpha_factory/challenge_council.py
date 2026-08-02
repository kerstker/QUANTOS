"""Challenge council (727 §8.4) — the Deterministic Judge's fixed convening rules.

`convene` is the 727 §8.4 Deterministic Judge for the challenge stage: it
reads ONLY machine evidence (structured objections) and applies a FIXED policy.
No language-model vote is an input — an EXTERNAL_STRUCTURED objection (the only
shape an LLM's output may take inside services/) can never directly FAIL a
candidate; it can only escalate to REQUIRES_HUMAN_REVIEW. Final lifecycle
state transitions remain with the Governance Plane downstream; this verdict is
the judge's machine-readable recommendation into it.

Fixed policy (each rule documented, evaluated in order):
  1. any DETERMINISTIC objection with severity BLOCK     -> FAIL
     (hard machine-evidence defect; no negotiation)
  2. any EXTERNAL_STRUCTURED objection with BLOCK        -> REQUIRES_HUMAN_REVIEW
     (an LLM's objection escalates to a human; it NEVER fails directly — §8.4)
  3. any INFO/INSUFFICIENT_EVIDENCE objection            -> REQUIRES_HUMAN_REVIEW
     (missing evidence does not default to PASS — §9.2)
  4. any WARN objection                                  -> REQUIRES_HUMAN_REVIEW
  5. empty objections                                    -> REQUIRES_HUMAN_REVIEW
     (a council that never convened is not a PASS)
  6. otherwise                                           -> PASS
     (only reachable with non-INSUFFICIENT INFO objections present)

Deterministic core: no I/O, no wall clock (`now_ms` caller-supplied), no RNG.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from quantos.contracts.hashing import stable_hash

from .challenger import (
    ChallengeInput,
    ChallengeObjection,
    statistical_challenger,
)

VERDICTS = frozenset({"PASS", "FAIL", "REQUIRES_HUMAN_REVIEW"})


class CouncilError(ValueError):
    """The council was convened with invalid inputs (fail-closed)."""


@dataclass(frozen=True)
class CouncilVerdict:
    """The judge's immutable verdict. `decision_hash` covers the verdict plus
    the objection evidence hashes (IN ORDER) — evaluated_at_ms is provenance
    of the convening act and deliberately NOT in the hash."""

    verdict: str
    objections: tuple[ChallengeObjection, ...]
    blocking_count: int
    external_count: int
    decision_hash: str
    evaluated_at_ms: int

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise CouncilError(f"verdict must be one of {sorted(VERDICTS)}")
        for objection in self.objections:
            if not isinstance(objection, ChallengeObjection):
                raise CouncilError("objections must be ChallengeObjection")
        if (
            isinstance(self.evaluated_at_ms, bool)
            or not isinstance(self.evaluated_at_ms, int)
            or self.evaluated_at_ms < 0
        ):
            raise CouncilError("evaluated_at_ms must be a non-negative integer")


def _decision_hash(verdict: str, objections: Sequence[ChallengeObjection]) -> str:
    return stable_hash(
        {
            "objection_hashes": [o.evidence_hash for o in objections],
            "verdict": verdict,
        }
    )


def convene(
    objections: Sequence[ChallengeObjection], *, now_ms: int
) -> CouncilVerdict:
    """Apply the fixed convening policy (see module docstring for the rules)."""
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise CouncilError("now_ms must be a non-negative integer")
    try:
        panel = tuple(objections)
    except TypeError as exc:
        raise CouncilError("objections must be an iterable") from exc
    for objection in panel:
        if not isinstance(objection, ChallengeObjection):
            raise CouncilError("objections must be ChallengeObjection")

    blocking_count = sum(1 for o in panel if o.severity == "BLOCK")
    external_count = sum(1 for o in panel if o.origin == "EXTERNAL_STRUCTURED")

    # Rule 1: hard machine-evidence defect.
    if any(o.origin == "DETERMINISTIC" and o.severity == "BLOCK" for o in panel):
        verdict = "FAIL"
    # Rule 2: an LLM's objection escalates to a human — never a direct FAIL.
    elif any(
        o.origin == "EXTERNAL_STRUCTURED" and o.severity == "BLOCK" for o in panel
    ):
        verdict = "REQUIRES_HUMAN_REVIEW"
    # Rule 3: missing evidence does not default to PASS.
    elif any(o.code == "INSUFFICIENT_EVIDENCE" for o in panel):
        verdict = "REQUIRES_HUMAN_REVIEW"
    # Rule 4: warnings need human judgement.
    elif any(o.severity == "WARN" for o in panel):
        verdict = "REQUIRES_HUMAN_REVIEW"
    # Rule 5: a council that never convened is not a PASS.
    elif not panel:
        verdict = "REQUIRES_HUMAN_REVIEW"
    # Rule 6: only reachable with non-INSUFFICIENT INFO objections present.
    else:
        verdict = "PASS"

    return CouncilVerdict(
        verdict=verdict,
        objections=panel,
        blocking_count=blocking_count,
        external_count=external_count,
        decision_hash=_decision_hash(verdict, panel),
        evaluated_at_ms=now_ms,
    )


def judge_candidate(
    inp: ChallengeInput,
    *,
    external_objections: Sequence[ChallengeObjection] = (),
    now_ms: int,
) -> CouncilVerdict:
    """727 §8.4 Deterministic Judge, end to end: the deterministic Statistical
    Challenger attacks the machine evidence, any EXTERNAL_STRUCTURED
    objections (the only LLM-shaped input) are admitted alongside, and the
    fixed convening policy renders the verdict."""
    if not isinstance(inp, ChallengeInput):
        raise CouncilError("inp must be a ChallengeInput")
    try:
        external = tuple(external_objections)
    except TypeError as exc:
        raise CouncilError("external_objections must be an iterable") from exc
    for objection in external:
        if not isinstance(objection, ChallengeObjection):
            raise CouncilError("external_objections must be ChallengeObjection")
        if objection.origin != "EXTERNAL_STRUCTURED":
            raise CouncilError(
                "external_objections must be built via external_objection "
                "(origin EXTERNAL_STRUCTURED)"
            )
    objections = statistical_challenger(inp) + external
    return convene(objections, now_ms=now_ms)


__all__ = [
    "CouncilError",
    "CouncilVerdict",
    "VERDICTS",
    "convene",
    "judge_candidate",
]
