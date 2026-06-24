import pytest
from services.intelligence.search_space import HypothesisSpace
from services.intelligence.search_strategy import UCBSearch


def _space():
    return HypothesisSpace(factors=("a", "b"), operators=("op",), windows=(10, 20))


def test_proposes_distinct_and_marks_proposed():
    s = UCBSearch(_space())
    first = s.propose(2)
    assert len(first) == 2 and len(set(first)) == 2
    second = s.propose(2)
    assert set(first).isdisjoint(second)                 # never re-proposes
    assert s.propose(99) == []                           # grid (4) exhausted


def test_update_raises_arm_mean_and_strategy_favors_good_arm():
    s = UCBSearch(_space())
    for hyp in s.space.hypotheses():
        s.update(hyp, 1.0 if hyp[2] == 20 else 0.0)
    assert s.arm_means()[("window", "20")] > s.arm_means()[("window", "10")]


def test_is_deterministic():
    a = UCBSearch(_space()).propose(4)
    b = UCBSearch(_space()).propose(4)
    assert a == b
