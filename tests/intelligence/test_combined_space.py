"""统一因子搜索:复合假设空间(价格+基本面)+ UCB 均值评分。"""
import math
import pytest
from services.intelligence.search_space import HypothesisSpace, CombinedHypothesisSpace
from services.intelligence.search_strategy import UCBSearch


def _price():
    return HypothesisSpace(factors=("i0",), operators=("momentum", "zscore"), windows=(3, 5))


def test_hypotheses_union():
    cs = CombinedHypothesisSpace(_price(), metrics=("eps", "bps"), modes=("ratio_to_price",))
    hyps = cs.hypotheses()
    assert len([h for h in hyps if h[0] == "P"]) == 1 * 2 * 2     # factors*ops*windows
    assert len([h for h in hyps if h[0] == "F"]) == 2 * 1         # metrics*modes
    assert ("F", "eps", "ratio_to_price") in hyps
    assert ("P", "i0", "momentum", "3") in hyps


def test_arms_dispatch():
    cs = CombinedHypothesisSpace(_price(), metrics=("eps",), modes=("ratio_to_price",))
    assert cs.arms_of(("P", "i0", "momentum", "3")) == (("factor", "i0"), ("operator", "momentum"), ("window", "3"))
    assert cs.arms_of(("F", "eps", "ratio_to_price")) == (("metric", "eps"), ("mode", "ratio_to_price"))


def test_to_definition_both_families():
    cs = CombinedHypothesisSpace(_price(), metrics=("eps",), modes=("ratio_to_price",))
    p = cs.to_definition("P", "i0", "momentum", "3")
    assert p.alpha_id == "h_momentum_i0_3" and p.factor_ids == ("i0",)
    assert p.params == {"operator": "momentum", "window": "3"}
    f = cs.to_definition("F", "eps", "ratio_to_price")
    assert f.alpha_id == "hf_eps_ratio_to_price" and f.factor_ids == ("eps",)
    assert f.params["metric"] == "eps" and f.params["mode"] == "ratio_to_price" and f.params["family"] == "fundamental"


def test_empty_dims_raise():
    with pytest.raises(ValueError):
        CombinedHypothesisSpace(_price(), metrics=(), modes=("ratio_to_price",))


def test_bandit_over_combined_roundtrip():
    cs = CombinedHypothesisSpace(_price(), metrics=("eps",), modes=("ratio_to_price",))
    s = UCBSearch(cs)
    chosen = s.propose(3)
    assert chosen
    for h in chosen:
        s.update(h, 0.5)
    snap = s.snapshot()
    s2 = UCBSearch(cs)
    s2.restore(snap)
    assert s2.proposed == s.proposed and s2.arm_means() == s.arm_means()


def test_score_is_mean_and_unpulled_is_inf():
    cs = CombinedHypothesisSpace(_price(), metrics=("eps",), modes=("ratio_to_price",))
    s = UCBSearch(cs)
    assert all(math.isinf(s._score(h)) for h in cs.hypotheses())   # 全未拉 → inf 优先
    # 拉满某价格假设的臂后,其分数 = 该假设三臂 UCB 均值(有限)
    h = ("P", "i0", "momentum", "3")
    s.update(h, 1.0)
    assert math.isfinite(s._score(h))


def test_mean_score_preserves_homogeneous_ranking():
    # 纯价格(同构 3 臂)空间:均值与求和排序一致 → 现有行为零回归
    sp = _price()
    s = UCBSearch(sp)
    s.propose(2)                                       # 拉两个,产生有限分数差异
    for h in sp.hypotheses()[:2]:
        s.update(h, 0.3 if h == sp.hypotheses()[0] else 0.9)
    by_mean = sorted(sp.hypotheses(), key=lambda h: (-s._score(h), h))
    by_sum = sorted(sp.hypotheses(), key=lambda h: (-sum(s._ucb(a) for a in sp.arms_of(h)), h))
    assert by_mean == by_sum                           # 同构下均值不改排序
