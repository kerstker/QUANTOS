"""Challenger (727 §8.4) — structured objections against a candidate.

Two origins, one shape:

- DETERMINISTIC: `statistical_challenger` attacks the MACHINE EVIDENCE with
  fixed rules (no LLM needed for v1 — DSR/PBO/leakage/perturbation are already
  computed by the evaluator/gauntlet; this module turns them into objections).
- EXTERNAL_STRUCTURED: `external_objection` is the ONLY entry point for an
  LLM- or human-authored objection. A tools/ LLM client translates model
  output into this structure; services/ never knows a model exists (727 §8.4:
  services/ holds no LLM client, symmetric with "network only in live/exchange
  and tools/").

An objection is EVIDENCE, not a verdict. Final state transitions belong to the
Deterministic Judge (`challenge_council.convene`) + Governance Plane — never
to a language-model vote.

Deterministic core: no I/O, no wall clock, no RNG.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional

from quantos.contracts.hashing import stable_hash


class ObjectionError(ValueError):
    """An objection or its challenge input fails fail-closed validation."""


SEVERITIES = frozenset({"INFO", "WARN", "BLOCK"})
ORIGINS = frozenset({"DETERMINISTIC", "EXTERNAL_STRUCTURED"})

#: creator_model value for deterministically-generated objections.
DETERMINISTIC_CHALLENGER_MODULE = "services.alpha_factory.challenger"

#: Minimum rationale length for EXTERNAL objections. An LLM objection that
#: cannot state its attack in a sentence is not an objection. Heuristic,
#: UNCALIBRATED.
MIN_EXTERNAL_RATIONALE_CHARS = 20

#: Known machine-evidence keys (all OPTIONAL — None = evidence honestly absent,
#: never fabricated). trial_count is carried for audit/future rules; v1 has no
#: attack line on it.
KNOWN_EVIDENCE_KEYS = frozenset(
    {
        "dsr",
        "pbo",
        "leakage_ok",
        "regime_profitable_frac",
        "perturbation_verdict",
        "cost_stress_verdict",
        "concentration_verdict",
        "trial_count",
    }
)


def _text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ObjectionError(f"{field} must be a string")
    if value != value.strip():
        raise ObjectionError(f"{field} must not have surrounding whitespace")
    if not value and not allow_empty:
        raise ObjectionError(f"{field} must not be empty")
    return value


def _optional_number(value: object, field: str) -> Optional[float]:
    """None stays None (evidence absent); NaN/Inf/non-number is fail-closed —
    an unverifiable metric must never be silently treated as missing."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ObjectionError(f"machine_evidence[{field}] must be a number or None")
    number = float(value)
    if not math.isfinite(number):
        raise ObjectionError(f"machine_evidence[{field}] must be finite")
    return number


@dataclass(frozen=True)
class ChallengeObjection:
    """One structured objection. Immutable; identity is its evidence_hash."""

    role: str
    code: str
    severity: str
    rationale: str
    evidence_hash: str
    origin: str
    creator_model: str
    prompt_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _text(self.role, "role"))
        object.__setattr__(self, "code", _text(self.code, "code"))
        if self.severity not in SEVERITIES:
            raise ObjectionError(f"severity must be one of {sorted(SEVERITIES)}")
        object.__setattr__(self, "rationale", _text(self.rationale, "rationale"))
        object.__setattr__(
            self, "evidence_hash", _text(self.evidence_hash, "evidence_hash")
        )
        if self.origin not in ORIGINS:
            raise ObjectionError(f"origin must be one of {sorted(ORIGINS)}")
        object.__setattr__(
            self, "creator_model", _text(self.creator_model, "creator_model")
        )
        # prompt_hash must be PROVIDED (a string); "" honestly means the
        # external source did not supply one.
        object.__setattr__(
            self, "prompt_hash", _text(self.prompt_hash, "prompt_hash", allow_empty=True)
        )


def objection_evidence_hash(
    *, role: str, code: str, severity: str, rationale: str, origin: str
) -> str:
    """Content hash over the objection's identity fields (audit/dedup)."""
    return stable_hash(
        {
            "code": code,
            "origin": origin,
            "rationale": rationale,
            "role": role,
            "severity": severity,
        }
    )


@dataclass(frozen=True)
class ChallengeInput:
    """The machine-evidence bundle a challenger may read. Every key is
    OPTIONAL: None = the evidence does not exist (honest), and an all-None
    input produces INSUFFICIENT_EVIDENCE — never a silent pass. Validation is
    fail-closed AT CONSTRUCTION: an unverifiable metric (NaN/Inf/wrong type)
    must never exist as a value object."""

    machine_evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.machine_evidence, Mapping):
            raise ObjectionError("machine_evidence must be a mapping")
        unknown = sorted(set(self.machine_evidence) - KNOWN_EVIDENCE_KEYS)
        if unknown:
            raise ObjectionError(f"unknown machine_evidence keys: {unknown}")
        evidence = dict(self.machine_evidence)
        for key in ("dsr", "pbo", "regime_profitable_frac"):
            evidence[key] = _optional_number(evidence.get(key), key)
        leakage_ok = evidence.get("leakage_ok")
        if leakage_ok is not None and not isinstance(leakage_ok, bool):
            raise ObjectionError(
                "machine_evidence['leakage_ok'] must be a bool or None"
            )
        for key in (
            "perturbation_verdict",
            "cost_stress_verdict",
            "concentration_verdict",
        ):
            value = evidence.get(key)
            if value is not None and value not in ("PASS", "WARN", "FAIL"):
                raise ObjectionError(
                    f"machine_evidence[{key}] must be PASS/WARN/FAIL or None"
                )
        trial_count = evidence.get("trial_count")
        if trial_count is not None and (
            isinstance(trial_count, bool)
            or not isinstance(trial_count, int)
            or trial_count < 0
        ):
            raise ObjectionError(
                "machine_evidence['trial_count'] must be a non-negative int or None"
            )
        object.__setattr__(self, "machine_evidence", evidence)


def _deterministic(code: str, severity: str, rationale: str) -> ChallengeObjection:
    return ChallengeObjection(
        role="STATISTICAL_CHALLENGER",
        code=code,
        severity=severity,
        rationale=rationale,
        evidence_hash=objection_evidence_hash(
            role="STATISTICAL_CHALLENGER",
            code=code,
            severity=severity,
            rationale=rationale,
            origin="DETERMINISTIC",
        ),
        origin="DETERMINISTIC",
        creator_model=DETERMINISTIC_CHALLENGER_MODULE,
        prompt_hash="",
    )


def statistical_challenger(inp: ChallengeInput) -> tuple[ChallengeObjection, ...]:
    """The deterministic Statistical Challenger (727 §8.4): attack overfitting,
    multiple testing, parameter fragility, and leakage with FIXED rules over
    machine evidence only. All-None evidence yields a single
    INFO/INSUFFICIENT_EVIDENCE objection — absence of evidence is itself an
    objection, never a silent pass.
    """
    if not isinstance(inp, ChallengeInput):
        raise ObjectionError("inp must be a ChallengeInput")
    # ChallengeInput validates every value at construction (fail-closed), so
    # the reads below are already type-safe and finite.
    evidence = inp.machine_evidence
    dsr = evidence.get("dsr")
    pbo = evidence.get("pbo")
    regime_frac = evidence.get("regime_profitable_frac")
    leakage_ok = evidence.get("leakage_ok")

    objections: list[ChallengeObjection] = []
    if dsr is not None and dsr <= 0.0:
        objections.append(
            _deterministic(
                "OVERFIT_DSR_NONPOSITIVE",
                "BLOCK",
                f"deflated Sharpe ratio {dsr:.6f} <= 0: the edge does not "
                "survive multiple-testing deflation",
            )
        )
    if pbo is not None and pbo >= 0.5:
        objections.append(
            _deterministic(
                "OVERFIT_PBO_HIGH",
                "BLOCK",
                f"probability of backtest overfitting {pbo:.6f} >= 0.5",
            )
        )
    if leakage_ok is False:
        objections.append(
            _deterministic(
                "LEAKAGE_DETECTED",
                "BLOCK",
                "the leakage detector flagged this candidate's expression",
            )
        )
    if evidence.get("perturbation_verdict") == "FAIL":
        objections.append(
            _deterministic(
                "PARAM_SPIKE",
                "BLOCK",
                "parameter perturbation FAIL: the deployment point is an "
                "isolated optimum or below its neighbourhood median",
            )
        )
    if regime_frac is not None and regime_frac < 0.5:
        # UNCALIBRATED engineering threshold: profitable in fewer than half of
        # the regime buckets is a fragility WARNING, not a hard fail.
        objections.append(
            _deterministic(
                "REGIME_FRAGILE",
                "WARN",
                f"profitable in only {regime_frac:.4f} of regime buckets (< 0.5)",
            )
        )
    if evidence.get("cost_stress_verdict") == "FAIL":
        objections.append(
            _deterministic(
                "COST_FRAGILE",
                "WARN",
                "cost-stress FAIL: the edge does not tolerate higher cost "
                "assumptions (RELATIVE mode may still stand)",
            )
        )
    if evidence.get("concentration_verdict") == "FAIL":
        objections.append(
            _deterministic(
                "CONCENTRATION_RISK",
                "WARN",
                "concentration FAIL: removing the best asset/month/period "
                "kills the edge",
            )
        )
    if not objections:
        objections.append(
            _deterministic(
                "INSUFFICIENT_EVIDENCE",
                "INFO",
                "no machine evidence triggered an attack line; absence of "
                "evidence is not evidence of an edge",
            )
        )
    return tuple(objections)


def external_objection(
    *,
    role: str,
    code: str,
    severity: str,
    rationale: str,
    creator_model: str,
    prompt_hash: str = "",
) -> ChallengeObjection:
    """The ONLY entry point for LLM- or human-authored objections.

    A tools/ LLM client translates model output into this structure; services/
    never knows a model exists. Validation is fail-closed: legal severity, a
    meaningful rationale (>= MIN_EXTERNAL_RATIONALE_CHARS), and provenance
    (creator_model non-empty; prompt_hash must be provided as a string, ""
    honestly meaning not supplied). An EXTERNAL objection can never directly
    FAIL a candidate — challenge_council.convene caps it at
    REQUIRES_HUMAN_REVIEW (727 §8.4).
    """
    rationale = _text(rationale, "rationale")
    if len(rationale) < MIN_EXTERNAL_RATIONALE_CHARS:
        raise ObjectionError(
            f"rationale must be at least {MIN_EXTERNAL_RATIONALE_CHARS} characters"
        )
    return ChallengeObjection(
        role=_text(role, "role"),
        code=_text(code, "code"),
        severity=severity,
        rationale=rationale,
        evidence_hash=objection_evidence_hash(
            role=role,
            code=code,
            severity=severity,
            rationale=rationale,
            origin="EXTERNAL_STRUCTURED",
        ),
        origin="EXTERNAL_STRUCTURED",
        creator_model=_text(creator_model, "creator_model"),
        prompt_hash=prompt_hash,
    )


__all__ = [
    "ChallengeInput",
    "ChallengeObjection",
    "DETERMINISTIC_CHALLENGER_MODULE",
    "KNOWN_EVIDENCE_KEYS",
    "MIN_EXTERNAL_RATIONALE_CHARS",
    "ObjectionError",
    "external_objection",
    "objection_evidence_hash",
    "statistical_challenger",
]
