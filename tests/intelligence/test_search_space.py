import pytest
from services.intelligence.search_space import HypothesisSpace


def _space():
    return HypothesisSpace(factors=("mom", "rev"), operators=("zscore", "rank"), windows=(20, 60))


def test_grid_is_full_product_sorted():
    hs = _space()
    assert len(hs.hypotheses()) == 2 * 2 * 2
    assert hs.hypotheses() == sorted(hs.hypotheses())


def test_to_definition_encodes_params_and_distinct_hashes():
    hs = _space()
    d1 = hs.to_definition("mom", "zscore", 20)
    d2 = hs.to_definition("mom", "zscore", 60)
    assert d1.alpha_id == "h_zscore_mom_20"
    assert d1.factor_ids == ("mom",) and d1.params == {"operator": "zscore", "window": "20"}
    assert d1.definition_hash != d2.definition_hash      # distinct trials


def test_arms_of_returns_one_arm_per_kind():
    hs = _space()
    arms = hs.arms_of(("mom", "zscore", 20))
    assert arms == (("factor", "mom"), ("operator", "zscore"), ("window", "20"))
    assert set(arms) <= set(hs.arms())


def test_empty_dimension_fails_closed():
    with pytest.raises(ValueError):
        HypothesisSpace(factors=(), operators=("zscore",), windows=(20,))
