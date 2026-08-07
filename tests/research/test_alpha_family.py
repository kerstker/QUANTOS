from __future__ import annotations

import pytest

from services.research.alpha_identity import AlphaDefinition, alpha_id
from services.research.alpha_family import (
    AlphaCandidate,
    deduplicate_candidates,
    family_id,
)


def definition(expr, *, field="close", horizon="1h"):
    return AlphaDefinition(
        definition=expr,
        feature_contract={"fields": [field]},
        data_schema={"version": 1},
        universe_policy={"name": "all"},
        horizon=horizon,
        cost_model={"bps": 10},
        validation_policy={"method": "walk_forward"},
        model="ridge",
        model_version="1",
    )


def test_parameter_variants_share_family_but_direction_and_field_do_not():
    a = definition(("ts_mean", ("field", "close"), 5))
    b = definition(("ts_mean", ("field", "close"), 20))
    neg = definition(("signed_power", ("field", "close"), -1))
    other = definition(("ts_mean", ("field", "volume"), 20), field="volume")
    assert family_id(a) == family_id(b)
    assert family_id(a) != family_id(neg)
    assert family_id(a) != family_id(other)


def test_exact_duplicates_are_collapsed_with_stable_alpha_id_tie_break():
    d = definition(("ts_mean", ("field", "close"), 5))
    candidates = [AlphaCandidate(d, return_fingerprint=(1.0, 2.0)), AlphaCandidate(d, return_fingerprint=(1.0, 2.0))]
    result = deduplicate_candidates(candidates, correlation_threshold=0.9)
    assert len(result.representatives) == 1
    assert result.representatives[0].alpha_id == alpha_id(d)
    assert len(result.exact_duplicates) == 1


def test_behavioral_duplicate_is_audit_only_and_does_not_merge_family():
    a = definition(("ts_mean", ("field", "close"), 5))
    b = definition(("ts_mean", ("field", "close"), 20))
    result = deduplicate_candidates(
        [AlphaCandidate(a, return_fingerprint=(1.0, 2.0, 3.0)), AlphaCandidate(b, return_fingerprint=(2.0, 4.0, 6.0))],
        correlation_threshold=0.99,
    )
    assert len(result.representatives) == 2
    assert len(result.behavioral_duplicates) == 1
    assert result.behavioral_duplicates[0].kind == "behavioral"
    assert result.behavioral_duplicates[0].family_id_a == result.behavioral_duplicates[0].family_id_b


def test_invalid_fingerprints_fail_closed_and_threshold_is_validated():
    d = definition(("ts_mean", ("field", "close"), 5))
    result = deduplicate_candidates([AlphaCandidate(d, return_fingerprint=(1.0, float("nan")))], correlation_threshold=0.9)
    assert not result.behavioral_duplicates
    with pytest.raises(ValueError):
        deduplicate_candidates([], correlation_threshold=1.0)
    with pytest.raises(ValueError):
        deduplicate_candidates([], correlation_threshold=-0.1)


def test_zero_and_one_candidate_are_legal():
    assert deduplicate_candidates([], correlation_threshold=0.9).representatives == ()
    d = definition(("field", "close"))
    assert len(deduplicate_candidates([AlphaCandidate(d)], correlation_threshold=0.9).representatives) == 1
