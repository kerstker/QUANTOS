"""Challenge council (727 §8.4): the Deterministic Judge's fixed convening rules."""

from __future__ import annotations

import pytest

from services.alpha_factory.challenge_council import (
    CouncilError,
    convene,
    judge_candidate,
)
from services.alpha_factory.challenger import (
    ChallengeInput,
    external_objection,
    statistical_challenger,
)


def _det(code="OVERFIT_DSR_NONPOSITIVE", severity="BLOCK"):
    evidence = {"dsr": -0.1} if code == "OVERFIT_DSR_NONPOSITIVE" else {"pbo": 0.9}
    return statistical_challenger(ChallengeInput(machine_evidence=evidence))[0]


def _ext(severity="BLOCK", code="ADVERSARIAL_REGIME_BREAK"):
    return external_objection(
        role="ADVERSARIAL_REVIEWER",
        code=code,
        severity=severity,
        rationale="the edge dies when funding flips sign for two weeks",
        creator_model="external-llm-via-tools",
    )


def test_rule1_deterministic_block_fails() -> None:
    verdict = convene([_det(), _ext("INFO", "NOTE")], now_ms=100)
    assert verdict.verdict == "FAIL"
    assert verdict.blocking_count == 1
    assert verdict.external_count == 1


def test_rule2_external_block_never_fails_directly() -> None:
    """§8.4 pinned: an LLM-authored BLOCK objection escalates to a human —
    it must NEVER produce a FAIL verdict by itself."""
    verdict = convene([_ext("BLOCK")], now_ms=100)
    assert verdict.verdict == "REQUIRES_HUMAN_REVIEW"
    assert verdict.verdict != "FAIL"
    # deterministic evidence wins over external when both block
    both = convene([_det(), _ext("BLOCK")], now_ms=100)
    assert both.verdict == "FAIL"
    assert both.blocking_count == 2


def test_rule3_insufficient_evidence_is_not_a_pass() -> None:
    objections = statistical_challenger(ChallengeInput(machine_evidence={}))
    verdict = convene(objections, now_ms=100)
    assert objections[0].code == "INSUFFICIENT_EVIDENCE"
    assert verdict.verdict == "REQUIRES_HUMAN_REVIEW"


def test_rule4_warn_goes_to_human() -> None:
    objections = statistical_challenger(
        ChallengeInput(machine_evidence={"cost_stress_verdict": "FAIL"})
    )
    verdict = convene(objections, now_ms=100)
    assert verdict.verdict == "REQUIRES_HUMAN_REVIEW"


def test_rule5_empty_council_is_not_a_pass() -> None:
    verdict = convene([], now_ms=100)
    assert verdict.verdict == "REQUIRES_HUMAN_REVIEW"
    assert verdict.objections == ()


def test_rule6_pass_shape_and_deterministic_hash() -> None:
    """PASS is reachable only via non-INSUFFICIENT INFO objections. decision_hash
    is content-derived: same panel + verdict -> same hash, any time."""
    info = _ext("INFO", "CONTEXT_NOTE")
    first = convene([info], now_ms=100)
    second = convene([info], now_ms=999)
    assert first.verdict == "PASS"
    assert first.decision_hash == second.decision_hash  # time not in the hash
    changed = convene([info, _ext("INFO", "OTHER_NOTE")], now_ms=100)
    assert changed.decision_hash != first.decision_hash


def test_judge_candidate_end_to_end() -> None:
    # hard machine defect + an external objection: deterministic BLOCK wins
    verdict = judge_candidate(
        ChallengeInput(machine_evidence={"dsr": -0.2, "leakage_ok": True}),
        external_objections=[_ext("BLOCK")],
        now_ms=42,
    )
    assert verdict.verdict == "FAIL"
    assert verdict.external_count == 1
    assert verdict.evaluated_at_ms == 42

    # clean machine evidence + external BLOCK -> human review, not FAIL
    review = judge_candidate(
        ChallengeInput(
            machine_evidence={
                "dsr": 0.4,
                "pbo": 0.1,
                "leakage_ok": True,
                "perturbation_verdict": "PASS",
                "regime_profitable_frac": 0.8,
                "cost_stress_verdict": "PASS",
                "concentration_verdict": "PASS",
            }
        ),
        external_objections=[_ext("BLOCK")],
        now_ms=42,
    )
    assert review.verdict == "REQUIRES_HUMAN_REVIEW"


def test_council_input_validation() -> None:
    with pytest.raises(CouncilError):
        convene(["not-an-objection"], now_ms=100)
    with pytest.raises(CouncilError):
        convene([], now_ms=-1)
    with pytest.raises(CouncilError):
        judge_candidate(
            ChallengeInput(machine_evidence={}),
            external_objections=[_det()],  # deterministic objection smuggled as external
            now_ms=1,
        )
