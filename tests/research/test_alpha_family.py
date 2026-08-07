from __future__ import annotations

import itertools
import math

import pytest

from services.research.alpha_identity import AlphaDefinition, alpha_id
from services.research.alpha_family import AlphaCandidate, deduplicate_candidates, family_id


def definition(expr, *, field="close", horizon="1h"):
    return AlphaDefinition(
        definition=expr, feature_contract={"fields": [field]}, data_schema={"version": 1},
        universe_policy={"name": "all"}, horizon=horizon, cost_model={"bps": 10},
        validation_policy={"method": "walk_forward"}, model="ridge", model_version="1",
    )


def test_parameter_variants_share_family_but_direction_and_field_do_not():
    a = definition(("ts_mean", ("field", "close"), 5))
    b = definition(("ts_mean", ("field", "close"), 20))
    neg = definition(("signed_power", ("field", "close"), -1))
    other = definition(("ts_mean", ("field", "volume"), 20), field="volume")
    assert family_id(a) == family_id(b)
    assert family_id(a) != family_id(neg)
    assert family_id(a) != family_id(other)


def test_family_uses_only_real_window_grammar_and_preserves_unary_children():
    close = definition(("cs_rank", ("field", "close")))
    volume = definition(("cs_rank", ("field", "volume")), field="volume")
    assert family_id(close) != family_id(volume)
    assert family_id(definition(("neg", ("field", "close")))) != family_id(close)
    assert family_id(definition(("ts_mean", ("field", "close"), 5))) == family_id(definition(("ts_mean", ("field", "close"), 20)))
    with pytest.raises(ValueError):
        definition(("ts_corr", ("field", "close"), 5))


def test_constant_direction_is_structural_but_magnitude_is_tunable():
    assert family_id(definition(("const", 2))) == family_id(definition(("const", 20.0)))
    assert family_id(definition(("const", 2))) != family_id(definition(("const", -2)))
    assert family_id(definition(("const", 2))) != family_id(definition(("const", 0)))


def test_exact_duplicates_are_collapsed_with_stable_alpha_id_tie_break():
    d = definition(("ts_mean", ("field", "close"), 5))
    candidates = [AlphaCandidate(d, return_fingerprint=(1.0, 2.0)), AlphaCandidate(d, return_fingerprint=(1.0, 2.0))]
    result = deduplicate_candidates(candidates, correlation_threshold=0.9)
    assert len(result.representatives) == 1
    assert result.representatives[0].alpha_id == alpha_id(d)
    assert len(result.exact_duplicates) == 1


def test_exact_representative_and_relationships_are_permutation_invariant():
    d = definition(("ts_mean", ("field", "close"), 5))
    candidates = [AlphaCandidate(d, return_fingerprint=fp) for fp in ((1.0, 2.0), (3.0, 1.0), (0.0, 4.0))]
    results = [deduplicate_candidates(order, correlation_threshold=0.9) for order in itertools.permutations(candidates)]
    assert all(result == results[0] for result in results)


def test_behavioral_duplicate_is_signed_and_same_domain_only():
    a = definition(("ts_mean", ("field", "close"), 5))
    b = definition(("ts_mean", ("field", "close"), 20))
    positive = deduplicate_candidates([AlphaCandidate(a, return_fingerprint=(1.0, 2.0, 3.0)), AlphaCandidate(b, return_fingerprint=(2.0, 4.0, 6.0))], correlation_threshold=0.99)
    assert len(positive.representatives) == 2 and len(positive.behavioral_duplicates) == 1
    anti = deduplicate_candidates([AlphaCandidate(a, return_fingerprint=(1.0, 2.0, 3.0)), AlphaCandidate(b, return_fingerprint=(3.0, 2.0, 1.0))], correlation_threshold=0.99)
    assert not anti.behavioral_duplicates
    mixed = deduplicate_candidates([
        AlphaCandidate(a, return_fingerprint=(1.0, 2.0, 3.0)),
        AlphaCandidate(b, signal_fingerprint=(2.0, 4.0, 6.0)),
    ], correlation_threshold=0.99)
    assert not mixed.behavioral_duplicates


def test_extreme_fingerprints_have_finite_behavioral_correlation():
    a = definition(("ts_mean", ("field", "close"), 5))
    b = definition(("ts_mean", ("field", "close"), 20))
    result = deduplicate_candidates([
        AlphaCandidate(a, return_fingerprint=(1e308, 1e308, -1e308, -1e308)),
        AlphaCandidate(b, return_fingerprint=(2e307, 2e307, -2e307, -2e307)),
    ], correlation_threshold=0.99)
    assert len(result.behavioral_duplicates) == 1
    correlation = result.behavioral_duplicates[0].correlation
    assert correlation is not None and math.isfinite(correlation)
    assert correlation == pytest.approx(1.0)


def test_extreme_anti_correlated_fingerprints_do_not_emit_false_relation():
    a = definition(("ts_mean", ("field", "close"), 5))
    b = definition(("ts_mean", ("field", "close"), 20))
    result = deduplicate_candidates([
        AlphaCandidate(a, return_fingerprint=(1e308, 1e308, -1e308, -1e308)),
        AlphaCandidate(b, return_fingerprint=(-2e307, -2e307, 2e307, 2e307)),
    ], correlation_threshold=0.99)
    assert not result.behavioral_duplicates


def test_fingerprints_are_deeply_immutable_numeric_and_fail_closed():
    d = definition(("ts_mean", ("field", "close"), 5))
    for bad in ([[1.0, 2.0], 3], (True, 1.0), (1.0, math.nan), (1.0, math.inf), ("1", 2.0)):
        with pytest.raises((TypeError, ValueError)):
            AlphaCandidate(d, return_fingerprint=bad)
    candidate = AlphaCandidate(d, return_fingerprint=[1, 2.0])
    assert candidate.return_fingerprint == (1, 2.0) and isinstance(candidate.return_fingerprint, tuple)


def test_invalid_threshold_is_strict_and_one_is_allowed():
    with pytest.raises((TypeError, ValueError)):
        deduplicate_candidates([], correlation_threshold=True)
    with pytest.raises(ValueError):
        deduplicate_candidates([], correlation_threshold=math.nan)
    with pytest.raises(ValueError):
        deduplicate_candidates([], correlation_threshold=-0.1)
    deduplicate_candidates([], correlation_threshold=1.0)


def test_zero_and_one_candidate_are_legal():
    assert deduplicate_candidates([], correlation_threshold=0.9).representatives == ()
    d = definition(("field", "close"))
    assert len(deduplicate_candidates([AlphaCandidate(d)], correlation_threshold=0.9).representatives) == 1
