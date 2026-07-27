"""Challenger (727 §8.4): structured objections, deterministic attack lines."""

from __future__ import annotations

import pytest

from services.alpha_factory.challenger import (
    ChallengeInput,
    ObjectionError,
    external_objection,
    statistical_challenger,
)


def _input(**evidence):
    return ChallengeInput(machine_evidence=evidence)


def test_objection_validation_is_fail_closed() -> None:
    with pytest.raises(ObjectionError):
        external_objection(
            role="ADVERSARIAL_REVIEWER",
            code="X",
            severity="CRITICAL",  # not a legal severity
            rationale="a long enough rationale for the rule",
            creator_model="gpt-test",
        )
    with pytest.raises(ObjectionError):
        external_objection(
            role="ADVERSARIAL_REVIEWER",
            code="X",
            severity="BLOCK",
            rationale="",  # empty rationale
            creator_model="gpt-test",
        )
    with pytest.raises(ObjectionError):
        external_objection(
            role="ADVERSARIAL_REVIEWER",
            code="X",
            severity="BLOCK",
            rationale="too short",
            creator_model="gpt-test",
        )
    with pytest.raises(ObjectionError):
        external_objection(
            role="ADVERSARIAL_REVIEWER",
            code="X",
            severity="BLOCK",
            rationale="a long enough rationale for the rule",
            creator_model="",  # provenance required
        )


def test_external_objection_marks_origin_and_provenance() -> None:
    objection = external_objection(
        role="ADVERSARIAL_REVIEWER",
        code="ADVERSARIAL_REGIME_BREAK",
        severity="BLOCK",
        rationale="the edge dies when funding flips sign for two weeks",
        creator_model="external-llm-via-tools",
        prompt_hash="f" * 64,
    )
    assert objection.origin == "EXTERNAL_STRUCTURED"
    assert objection.creator_model == "external-llm-via-tools"
    assert objection.prompt_hash == "f" * 64
    assert len(objection.evidence_hash) == 64
    # prompt_hash may honestly be absent ("")
    honest = external_objection(
        role="HUMAN_REVIEWER",
        code="HUMAN_CONCERN",
        severity="WARN",
        rationale="a human concern stated in a full sentence",
        creator_model="human",
    )
    assert honest.prompt_hash == ""


def test_statistical_challenger_block_lines() -> None:
    objections = statistical_challenger(
        _input(dsr=-0.1, pbo=0.6, leakage_ok=False, perturbation_verdict="FAIL")
    )
    by_code = {o.code: o for o in objections}
    assert set(by_code) == {
        "OVERFIT_DSR_NONPOSITIVE",
        "OVERFIT_PBO_HIGH",
        "LEAKAGE_DETECTED",
        "PARAM_SPIKE",
    }
    for objection in objections:
        assert objection.severity == "BLOCK"
        assert objection.origin == "DETERMINISTIC"
        assert objection.creator_model == "services.alpha_factory.challenger"


def test_statistical_challenger_warn_lines() -> None:
    objections = statistical_challenger(
        _input(
            dsr=0.5,
            pbo=0.1,
            regime_profitable_frac=0.3,
            cost_stress_verdict="FAIL",
            concentration_verdict="FAIL",
        )
    )
    by_code = {o.code: o for o in objections}
    assert set(by_code) == {"REGIME_FRAGILE", "COST_FRAGILE", "CONCENTRATION_RISK"}
    assert all(o.severity == "WARN" for o in objections)


def test_all_none_evidence_is_insufficient_never_a_pass() -> None:
    objections = statistical_challenger(_input())
    assert len(objections) == 1
    assert objections[0].code == "INSUFFICIENT_EVIDENCE"
    assert objections[0].severity == "INFO"
    # all-None explicitly supplied behaves identically
    explicit = statistical_challenger(_input(dsr=None, pbo=None, leakage_ok=None))
    assert explicit[0].code == "INSUFFICIENT_EVIDENCE"


def test_challenge_input_fail_closed_on_bad_values() -> None:
    with pytest.raises(ObjectionError, match="unknown machine_evidence"):
        _input(sharpe=1.0)  # unknown key must not silently pass
    with pytest.raises(ObjectionError):
        _input(dsr=float("nan"))
    with pytest.raises(ObjectionError):
        _input(leakage_ok="yes")
    with pytest.raises(ObjectionError):
        _input(perturbation_verdict="MAYBE")


def test_clean_evidence_still_yields_insufficient_marker() -> None:
    """Evidence present and clean: no attack line fires, and the single
    INSUFFICIENT marker keeps 'no objection' distinct from 'judged good'."""
    objections = statistical_challenger(
        _input(
            dsr=0.5,
            pbo=0.1,
            leakage_ok=True,
            perturbation_verdict="PASS",
            regime_profitable_frac=0.8,
            cost_stress_verdict="PASS",
            concentration_verdict="PASS",
        )
    )
    assert [o.code for o in objections] == ["INSUFFICIENT_EVIDENCE"]
