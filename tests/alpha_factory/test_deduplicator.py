"""Candidate deduplicator (727 §8.3 layer 3): EXACT / NEAR / KEEP, first-seen order."""

from __future__ import annotations

import pytest

from services.alpha.similarity import expression_similarity
from services.alpha_factory.candidate_compiler import compile_candidate
from services.alpha_factory.candidate_deduplicator import (
    CandidateDeduplicator,
    DeduplicatorError,
)
from services.research import alpha_expression as ax

_BIG = ("neg", ("ts_mean", ("ts_delta", ("field", "close"), 5), 10))


def _compiled(expr):
    return compile_candidate(expr)


def test_exact_duplicate_by_hash() -> None:
    dedup = CandidateDeduplicator()
    candidate = _compiled(("cs_rank", ("field", "close")))
    decision = dedup.check(candidate, seen_hashes={candidate.expression_hash})
    assert decision.verdict == "EXACT_DUPLICATE"
    assert decision.matched_expression_hash == candidate.expression_hash


def test_near_duplicate_by_similarity() -> None:
    # share the whole child subtree, differ only at the cs root -> high Jaccard
    seen = ("cs_rank", _BIG)
    candidate = _compiled(("cs_scale", _BIG))
    similarity = expression_similarity(
        candidate.expression, ax.canonicalize(seen)
    )
    dedup = CandidateDeduplicator(near_threshold=similarity)  # boundary is inclusive
    decision = dedup.check(
        candidate, seen_hashes=set(), seen_expressions=[seen]
    )
    assert decision.verdict == "NEAR_DUPLICATE"
    assert decision.matched_expression_hash == ax.alpha_id(ax.canonicalize(seen))
    assert "near_threshold" in decision.reason


def test_below_threshold_keeps() -> None:
    seen = ("cs_rank", _BIG)
    candidate = _compiled(("cs_scale", _BIG))
    similarity = expression_similarity(candidate.expression, ax.canonicalize(seen))
    dedup = CandidateDeduplicator(near_threshold=similarity + 0.01)
    decision = dedup.check(candidate, seen_hashes=set(), seen_expressions=[seen])
    assert decision.verdict == "KEEP"
    assert decision.matched_expression_hash == ""


def test_distinct_candidate_keeps() -> None:
    dedup = CandidateDeduplicator()
    candidate = _compiled(("cs_rank", ("field", "funding")))
    decision = dedup.check(
        candidate,
        seen_hashes=set(),
        seen_expressions=[("cs_rank", ("field", "close"))],
    )
    assert decision.verdict == "KEEP"


def test_filter_batch_first_seen_order() -> None:
    dedup = CandidateDeduplicator()
    first = _compiled(("cs_rank", ("field", "close")))
    duplicate = _compiled(("cs_rank", ("field", "close")))
    other = _compiled(("cs_rank", ("field", "funding")))

    kept, rejected = dedup.filter_batch([first, duplicate, other])
    assert kept == [first, other]  # first-seen wins, input order preserved
    assert len(rejected) == 1
    rejected_candidate, decision = rejected[0]
    assert rejected_candidate is duplicate
    assert decision.verdict == "EXACT_DUPLICATE"


def test_threshold_validation_is_fail_closed() -> None:
    for bad in (0.0, 1.5, True, float("nan")):
        with pytest.raises(DeduplicatorError):
            CandidateDeduplicator(near_threshold=bad)
    assert CandidateDeduplicator(near_threshold=1.0).near_threshold == 1.0
    with pytest.raises(DeduplicatorError):
        CandidateDeduplicator().check("not-a-candidate", seen_hashes=set())
