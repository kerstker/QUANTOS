"""Frozen validation is separate from subsequent forward performance."""

import copy

import pytest


def reference():
    return dict(
        review_id="1" * 64,
        content_hash="2" * 64,
        project_cut_sha256="3" * 64,
        invocation_id="4" * 64,
        native_trial_id="5" * 64,
        config_hash="6" * 64,
        statistical_policy_sha256="7" * 64,
        statistical_report_sha256="8" * 64,
        reviewed_at_ms=100,
        registered_at_ms=100,
        attempt_count=3,
        unique_definition_count=3,
    )


def policy():
    from quantos.contracts.gold_frozen_validation_policy import declare

    return declare(
        reference=reference(),
        candidate_id="channel",
        strategy_definition_sha256="9" * 64,
        declared_at_ms=200,
        series_start_ms=300,
    )


@pytest.mark.parametrize("field", ["reviewed_at_ms", "registered_at_ms"])
def test_original_review_must_exist_before_forward_selection(field):
    from quantos.contracts.gold_frozen_validation_policy import declare

    original = reference()
    original[field] = 200
    with pytest.raises(ValueError, match="FROZEN_VALIDATION"):
        declare(
            reference=original,
            candidate_id="channel",
            strategy_definition_sha256="9" * 64,
            declared_at_ms=200,
            series_start_ms=300,
        )


@pytest.mark.parametrize("damage", ["bool_count", "missing_hash", "extra", "future", "population"])
def test_changed_or_unbounded_frozen_reference_rejected(damage):
    from quantos.contracts.gold_frozen_validation_policy import validate

    p = policy()
    if damage == "bool_count":
        p["reference"]["attempt_count"] = True
    elif damage == "missing_hash":
        del p["reference"]["content_hash"]
    elif damage == "extra":
        p["authority"] = True
    elif damage == "future":
        p["declared_at_ms"] = 300
    else:
        p["reference"]["attempt_count"] = 100001
    with pytest.raises(ValueError, match="FROZEN_VALIDATION"):
        validate(p)


def test_new_model_policy_cannot_inherit_forward_dsr_if_original_unavailable():
    from tests.validation.test_gold_model_risk_inputs import context
    from services.validation.gold_model_risk_inputs import from_verified_statistics

    cut, subject, stats, native = context()
    stats["report"]["metrics"].update(dsr="0.99", pbo="0.01")
    # Unit fixture identities are synthetic. The production original port is
    # tested separately with actual registered backtest and registry rows.
    p = policy()
    subject_copy = copy.deepcopy(subject)
    value = from_verified_statistics(
        project_cut=cut,
        subject=subject_copy,
        verified_statistics=stats,
        checked_at_ms=1000,
        candidate_id="channel",
        native_result_hash=native,
        frozen_validation_policy=p,
    )
    assert value.projection["facts"]["validation_confidence"]["value"] is None
    assert value.projection["facts"]["overfit_flag"]["value"] is None
    assert value.projection["schema"] == "QUANTOS_GOLD_MODEL_RISK_INPUTS_V5"
    assert value.engine_metadata is None


@pytest.mark.parametrize(
    ("current_pbo", "original_pbo", "overfit"), [("0.01", "0.7", True), ("0.8", "0.01", True), ("0.01", "0.01", False)]
)
def test_frozen_confidence_age_and_known_overfit_are_separate_from_forward(current_pbo, original_pbo, overfit):
    from decimal import Decimal
    from tests.validation.test_gold_model_risk_inputs import context
    from services.validation.gold_model_risk_inputs import from_verified_statistics
    from quantos.contracts.gold_frozen_validation_policy import REVIEW_SCHEMA, digest, policy_sha256

    cut, subject, stats, native = context()
    stats["report"]["metrics"].update(dsr="0.99", pbo=current_pbo)
    p = policy()
    review = dict(
        schema=REVIEW_SCHEMA,
        status="AVAILABLE",
        policy_sha256=policy_sha256(p),
        reference=p["reference"],
        **subject,
        candidate_id="channel",
        native_result_hash=native,
        project_cut_sha256=cut["cut_sha256"],
        snapshot_as_of_ms=1000,
        validation_as_of_ms=50,
        validation_confidence="0.2",
        pbo=original_pbo,
        explicit_overfit=False,
        frozen_attempt_count=3,
        frozen_unique_definition_count=3,
        selection_history_status="COMPLETE_RETAINED_PREFIX",
        statistical_reasons=[],
        limitations=[],
        economic_authority=False,
        submission_enabled=False,
        independent_oos_verified=False,
        promotion_approved=False,
    )
    review["review_sha256"] = digest(review)
    model = from_verified_statistics(
        project_cut=cut,
        subject=subject,
        verified_statistics=stats,
        checked_at_ms=1000,
        candidate_id="channel",
        native_result_hash=native,
        frozen_validation_policy=p,
        frozen_validation_review=review,
    ).projection
    assert model["facts"]["validation_confidence"]["value"] == "0.2"
    assert Decimal(model["facts"]["age_days"]["value"]) > 0
    assert model["facts"]["overfit_flag"]["value"] is overfit
    assert model["facts"]["paper_confidence"]["value"] is None
    assert model["facts"]["leakage_flag"]["value"] is None
