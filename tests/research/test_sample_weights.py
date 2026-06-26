"""Uniqueness sample weighting (tests-first).

Overlapping labels are not independent: if two samples' label windows cover the same period,
that period is "shared" and each sample is worth less. Lopez de Prado weights each sample by
its average uniqueness = mean over its window of 1/concurrency(t). Pure, deterministic.
"""
from services.research.sample_weights import uniqueness_weights


def test_non_overlapping_are_fully_unique():
    assert uniqueness_weights([(0, 2), (3, 5), (6, 8)]) == [1.0, 1.0, 1.0]


def test_identical_spans_share_equally():
    assert uniqueness_weights([(0, 4), (0, 4), (0, 4)]) == [round(1 / 3, 6)] * 3


def test_partial_overlap_is_between():
    w = uniqueness_weights([(0, 3), (2, 5)])             # overlap only at t=2,3
    assert all(0.5 < x < 1.0 for x in w)


def test_fail_closed_empty_and_degenerate():
    assert uniqueness_weights([]) == []
    assert uniqueness_weights([(5, 3)]) == [0.0]          # end < start -> 0
