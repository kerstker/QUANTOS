"""特征交互复合因子:价格×价格 / 价格×基本面、横截面 z 组合、退化跳过、门控、桥接、确定性。"""
import math
import pytest
from quantos.contracts.data import Bar
from quantos.contracts.feature import FeatureValue
from services.data.persistent_store import SqlitePITStore
from services.data.fundamental_store import SqliteFundamentalStore
from services.data.universe import PITUniverse
from services.research.interaction_factors import (
    build_interaction_periods, interaction_definition, make_interaction_provider)

DATES = [20240102, 20240103, 20240104, 20240105, 20240106, 20240107]


def _bar(inst, t, close):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t,
               open=close, high=close + 1, low=max(close - 1, 0.1), close=close, volume=1000)


def _price(insts):
    s = SqlitePITStore(":memory:", create=True)
    for k, inst in enumerate(insts):
        for j, t in enumerate(DATES):
            # non-linear, per-instrument-distinct shapes so BOTH momentum and (time-series)
            # zscore vary cross-sectionally (a purely linear path makes the zscore leg
            # cross-sectionally constant → every period degenerate)
            close = 10.0 + 2 * k + (j + 1) + 1.5 * (k + 1) * (j % 3)
            s.add(inst, _bar(inst, t, close))
    return s


def _fund(insts, vals):
    s = SqliteFundamentalStore(":memory:", create=True)
    for inst in insts:
        s.add(FeatureValue(feature_id="eps", instrument_id=inst, event_time=0, knowledge_time=0, value=vals[inst]))
    return s


_MOM = ("price", "momentum", 2)
_ZS = ("price", "zscore", 3)


def test_price_x_price_sum():
    insts = ["i0", "i1", "i2", "i3"]
    periods = build_interaction_periods(_price(insts), None, insts, _MOM, _ZS, "sum",
                                        event_times=DATES, fwd_horizon=1)
    assert periods
    for p in periods:
        assert len(p.signals) == len(p.instruments) >= 2
        assert all(math.isfinite(s) for s in p.signals)


def test_price_x_fundamental_product():
    # 旗舰:动量 × 价值(E/P)
    insts = ["i0", "i1", "i2", "i3"]
    fund = _fund(insts, {"i0": 1.0, "i1": 2.0, "i2": 3.0, "i3": 4.0})
    periods = build_interaction_periods(_price(insts), fund, insts, _MOM,
                                        ("fund", "eps", "ratio_to_price"), "product",
                                        event_times=DATES, fwd_horizon=1)
    assert periods
    assert all(math.isfinite(s) for p in periods for s in p.signals)


def test_sum_combine_is_cross_sectionally_zero():
    insts = ["i0", "i1", "i2", "i3"]
    periods = build_interaction_periods(_price(insts), None, insts, _MOM, _ZS, "sum",
                                        event_times=DATES, fwd_horizon=1)
    assert periods
    for p in periods:
        assert abs(sum(p.signals)) < 1e-9          # 两腿 z 各和为 0 → sum 组合横截面和为 0


def test_degenerate_leg_period_skipped():
    # 一腿(基本面 raw)所有标的同值 → 横截面 pstdev=0 → 所有期跳过
    insts = ["i0", "i1", "i2", "i3"]
    fund = _fund(insts, {i: 5.0 for i in insts})
    periods = build_interaction_periods(_price(insts), fund, insts, _MOM,
                                        ("fund", "eps", "raw"), "sum",
                                        event_times=DATES, fwd_horizon=1)
    assert periods == []


def test_universe_gating():
    insts = ["i0", "i1", "i2", "i3"]
    u = PITUniverse()
    for i in ("i0", "i1", "i2"):
        u.add_membership(i, "2024-01-02")           # i3 非成员
    periods = build_interaction_periods(_price(insts), None, insts, _MOM, _ZS, "sum",
                                        event_times=DATES, fwd_horizon=1, universe=u)
    assert periods
    for p in periods:
        assert "i3" not in p.instruments


def test_bad_combine_and_leg_kind_raise():
    insts = ["i0", "i1", "i2", "i3"]
    with pytest.raises(ValueError):
        build_interaction_periods(_price(insts), None, insts, _MOM, _ZS, "bogus",
                                  event_times=DATES, fwd_horizon=1)
    with pytest.raises(ValueError):
        build_interaction_periods(_price(insts), None, insts, ("bogus", "x", "1"), _ZS, "sum",
                                  event_times=DATES, fwd_horizon=1)


def test_deterministic():
    insts = ["i0", "i1", "i2", "i3"]
    def run():
        return build_interaction_periods(_price(insts), None, insts, _MOM, _ZS, "sum",
                                         event_times=DATES, fwd_horizon=1)
    a, b = run(), run()
    assert [p.signals for p in a] == [p.signals for p in b]
    assert [p.instruments for p in a] == [p.instruments for p in b]


def test_zscore_relative_tolerance():
    from services.research.interaction_factors import _zscore_xs
    # 小量级但真实价差 → 不应被误杀(审查 P2)
    assert _zscore_xs([0.001, 0.002, 0.003, 0.004]) is not None
    # 常量带 FP 噪声 → 判退化
    assert _zscore_xs([1.2247, 1.2247, 1.2247, 1.2247 + 1e-15]) is None
    assert _zscore_xs([0.0, 0.0, 0.0]) is None


def test_leg_id_rejects_colon():
    # 含 ':' 的腿分量会破坏 id 编码单射性 → 失败关闭(审查 P1)
    with pytest.raises(ValueError):
        interaction_definition(("fund", "a:b", "raw"), _ZS, "sum")


def test_definition_provider_roundtrip():
    insts = ["i0", "i1", "i2", "i3"]
    price = _price(insts)
    defn = interaction_definition(_MOM, _ZS, "sum")
    assert defn.params["family"] == "interaction" and defn.factor_ids == ("price:momentum:2", "price:zscore:3")
    provider = make_interaction_provider(price, None, insts, event_times=DATES, fwd_horizon=1)
    via_provider = provider(defn)
    direct = build_interaction_periods(price, None, insts, _MOM, _ZS, "sum",
                                       event_times=DATES, fwd_horizon=1)
    assert [p.signals for p in via_provider] == [p.signals for p in direct]
