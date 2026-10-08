"""基本面价值因子面板:PIT 对齐、ratio_to_price/raw、universe 门控、provider、确定性。"""
import pytest
from types import SimpleNamespace
from quantos.contracts.data import Bar
from quantos.contracts.feature import FeatureValue
from services.data.persistent_store import SqlitePITStore
from services.data.fundamental_store import SqliteFundamentalStore
from services.data.universe import PITUniverse
from services.research.fundamental_factors import build_fundamental_periods, make_fundamental_periods_provider

DATES = [20240102, 20240103, 20240104, 20240105, 20240106]


def _bar(inst, t, close):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t,
               open=close, high=close + 1, low=max(close - 1, 0.1), close=close, volume=1000)


def _price(insts):
    s = SqlitePITStore(":memory:", create=True)
    for k, inst in enumerate(insts):
        for j, t in enumerate(DATES):
            s.add(inst, _bar(inst, t, 10.0 + k + j))      # 上行价
    return s


def _fund(insts, kt):
    s = SqliteFundamentalStore(":memory:", create=True)
    for k, inst in enumerate(insts):
        s.add(FeatureValue(feature_id="eps", instrument_id=inst,
                           event_time=20231231, knowledge_time=kt, value=2.0 + k))
    return s


def test_ratio_to_price_pit_and_value():
    insts = ["i0", "i1", "i2"]
    periods = build_fundamental_periods(_fund(insts, 20240101), _price(insts), insts, "eps",
                                        event_times=DATES, fwd_horizon=1, mode="ratio_to_price")
    assert len(periods) == 4                              # t = 0102..0105 都可知
    p0 = periods[0]                                       # t=0102
    i = p0.instruments.index("i0")
    assert p0.signals[i] == pytest.approx(2.0 / 10.0)     # eps/close = 2.0/10.0
    assert all(s > 0 for s in p0.signals)


def test_pit_fundamental_not_yet_announced_dropped():
    insts = ["i0", "i1", "i2"]
    periods = build_fundamental_periods(_fund(insts, 20240104), _price(insts), insts, "eps",
                                        event_times=DATES, fwd_horizon=1, mode="ratio_to_price")
    assert len(periods) == 2                              # 仅 t=0104,0105 基本面可知


def test_raw_mode():
    insts = ["i0", "i1"]
    periods = build_fundamental_periods(_fund(insts, 20240101), _price(insts), insts, "eps",
                                        event_times=DATES, fwd_horizon=1, mode="raw")
    p0 = periods[0]
    i = p0.instruments.index("i1")
    assert p0.signals[i] == pytest.approx(3.0)            # i1 raw eps = 2.0+1


def test_universe_gating():
    insts = ["i0", "i1", "i2"]
    u = PITUniverse()
    u.add_membership("i0", "2024-01-02")
    u.add_membership("i1", "2024-01-02")                  # i2 非成员
    periods = build_fundamental_periods(_fund(insts, 20240101), _price(insts), insts, "eps",
                                        event_times=DATES, fwd_horizon=1, mode="raw", universe=u)
    assert periods
    for p in periods:
        assert "i2" not in p.instruments
        assert set(p.instruments) <= {"i0", "i1"}


def test_provider_and_bad_mode():
    insts = ["i0", "i1"]
    provider = make_fundamental_periods_provider(_fund(insts, 20240101), _price(insts), insts,
                                                 event_times=DATES, fwd_horizon=1)
    assert provider(SimpleNamespace(params={"metric": "eps"}))   # 默认 ratio_to_price
    with pytest.raises(ValueError):
        build_fundamental_periods(_fund(insts, 20240101), _price(insts), insts, "eps",
                                  event_times=DATES, fwd_horizon=1, mode="bogus")


def test_non_finite_signal_skipped():
    # 程序化注入 inf 基本面(绕过 ingest)→ builder 必须跳过,产出无非有限信号(审查 P1)
    import math
    insts = ["i0", "i1", "i2"]
    fund = _fund(insts, 20240101)
    # 更晚公告日 → 不同 PK、成为 i2 最新可知值(且 DATES 均 >= 20240102)
    fund.add(FeatureValue(feature_id="eps", instrument_id="i2", event_time=20231231,
                          knowledge_time=20240102, value=float("inf")))
    periods = build_fundamental_periods(fund, _price(insts), insts, "eps",
                                        event_times=DATES, fwd_horizon=1, mode="ratio_to_price")
    assert periods
    for p in periods:
        assert "i2" not in p.instruments                 # inf 信号被跳过
        assert all(math.isfinite(s) for s in p.signals)


def test_deterministic():
    insts = ["i0", "i1", "i2"]
    def run():
        return build_fundamental_periods(_fund(insts, 20240101), _price(insts), insts, "eps",
                                         event_times=DATES, fwd_horizon=1, mode="ratio_to_price")
    a, b = run(), run()
    assert [p.signals for p in a] == [p.signals for p in b]
    assert [p.instruments for p in a] == [p.instruments for p in b]
