"""services/research/exit_policies.py 的单元测试 —— 全合成。

覆盖:每个策略的触发/不触发/边界/空头对称/数据不足 fail-safe;无前视性质
(追加未来 bar 决策不变);权重流后处理器(基线恒等、锁定与信号复位、只减不加);
实验室增量度量与预注册选择规则。不碰真实 DB/网络。"""
from __future__ import annotations

import statistics

import pytest

from services.research import exit_policies as ep

_DAY = 86_400_000
_T0 = 1_700_000_000_000


def _bars(closes, *, start=_T0, step=_DAY, spread=2.0):
    """由收盘价造合成日线:high=close+spread/2, low=close-spread/2(合法 OHLC)。"""
    out = []
    for i, c in enumerate(closes):
        out.append(ep.ExitBar(start + i * step, c, c + spread / 2, c - spread / 2, c))
    return tuple(out)


def _ms(closes, *, as_of=None, extra=(), spread=2.0):
    bars = _bars(closes, spread=spread) + tuple(extra)
    return ep.MarketState("X", as_of if as_of is not None else _T0 + (len(closes) - 1) * _DAY, bars)


def _ps(qty=1.0, entry=100.0, held=1, hi=None, lo=None, fav=0.0, stopped=False):
    return ep.PositionState("X", qty, entry, held,
                            hi if hi is not None else entry,
                            lo if lo is not None else entry, fav, stopped)


_ALPHA = ep.AlphaState(0.5, 0.5)


# ---------------------------------------------------------------------------
# MarketState 访问器 + 无前视性质
# ---------------------------------------------------------------------------

def test_accessors_filter_future_bars():
    closes = [100.0, 101.0, 102.0]
    as_of = _T0 + 2 * _DAY
    future = _bars([0.01, 9999.0], start=as_of + _DAY)
    a = _ms(closes, as_of=as_of)
    b = _ms(closes, as_of=as_of, extra=future)
    assert a.trailing_bars() == b.trailing_bars()
    assert a.current_close() == b.current_close() == 102.0
    assert a.atr(2) == b.atr(2)
    assert a.realized_daily_vol(2) == b.realized_daily_vol(2)


def test_atr_formula_and_insufficient_data():
    # spread=10 → 每根 high-low=10;收盘平 → TR 恒 10 → ATR=10(需 window+1 根)
    ms = _ms([100.0] * 15, spread=10.0)
    assert ms.atr(14) == pytest.approx(10.0)
    assert _ms([100.0] * 14, spread=10.0).atr(14) is None
    assert _ms([100.0] * 3).current_close() == 100.0
    assert ep.MarketState("X", _T0, ()).current_close() is None


def test_realized_vol_independent_computation():
    closes = [100.0 * (1.01 if i % 2 else 1.0) for i in range(31)]
    ms = _ms(closes)
    rets = [closes[i + 1] / closes[i] - 1.0 for i in range(30)]
    assert ms.realized_daily_vol(30) == pytest.approx(statistics.pstdev(rets))
    assert _ms(closes[:30]).realized_daily_vol(30) is None   # 差一根 → None


@pytest.mark.parametrize("policy", sorted(ep.POLICY_REGISTRY))
def test_no_lookahead_property_all_policies(policy):
    """追加 as_of 之后的极端未来 bar,任何策略的决策必须逐字段不变。"""
    closes = [100.0 + (i % 5) for i in range(60)]
    as_of = _T0 + 59 * _DAY
    future = _bars([0.01, 1e6, 50.0], start=as_of + _DAY)
    reg = ep.POLICY_REGISTRY[policy]
    ps = _ps(qty=1.0, entry=95.0, held=3, hi=110.0, lo=90.0, fav=0.15)
    d1 = reg.fn(ps, _ms(closes, as_of=as_of), _ALPHA, reg.params)
    d2 = reg.fn(ps, _ms(closes, as_of=as_of, extra=future), _ALPHA, reg.params)
    assert d1 == d2
    assert d1.policy_version == reg.version


# ---------------------------------------------------------------------------
# 逐策略:触发 / 不触发 / 边界 / 空头对称 / fail-safe
# ---------------------------------------------------------------------------

def test_baseline_always_hold():
    for ps in (_ps(), _ps(qty=0.0, entry=None), _ps(qty=-1.0)):
        d = ep.baseline_signal_only(ps, _ms([100.0]), _ALPHA)
        assert (d.action, d.target_fraction, d.reason_code) == (ep.HOLD, 1.0, "SIGNAL_ONLY_BASELINE")


def test_fixed_stop_trigger_no_trigger_boundary_and_short():
    p = ep.FixedStopParams(pct=0.10)
    ms = _ms([89.0])
    assert ep.fixed_stop(_ps(entry=100.0), ms, _ALPHA, p).action == ep.EXIT      # -11%
    assert ep.fixed_stop(_ps(entry=100.0), _ms([91.0]), _ALPHA, p).action == ep.HOLD
    b = ep.fixed_stop(_ps(entry=100.0), _ms([90.0]), _ALPHA, p)                  # 恰 -10%
    assert b.action == ep.EXIT and b.reason_code == "FIXED_STOP_HIT"
    s = ep.fixed_stop(_ps(qty=-1.0, entry=100.0), _ms([110.0]), _ALPHA, p)       # 空 -10%
    assert s.action == ep.EXIT
    assert ep.fixed_stop(_ps(qty=-1.0, entry=100.0), _ms([109.0]), _ALPHA, p).action == ep.HOLD
    f = ep.fixed_stop(_ps(qty=0.0, entry=None), ms, _ALPHA, p)
    assert f.action == ep.HOLD and f.reason_code == "FLAT_OR_NO_ENTRY"
    n = ep.fixed_stop(_ps(), ep.MarketState("X", _T0, ()), _ALPHA, p)
    assert n.action == ep.HOLD and n.reason_code == "NO_MARKET_DATA"


def test_atr_stop_levels_and_insufficient_data():
    p = ep.AtrStopParams(mult=3.0, window=14)
    # 步进 5(spread=10 → 每根 TR 恒 10 → ATR=10):多 level=100-3×10=70 / 空 level=130
    hold = [100.0] * 10 + [95.0, 90.0, 85.0, 80.0, 75.0]
    hit = [100.0] * 9 + [95.0, 90.0, 85.0, 80.0, 75.0, 70.0]
    assert ep.atr_stop(_ps(entry=100.0), _ms(hold, spread=10.0), _ALPHA, p).action == ep.HOLD
    got = ep.atr_stop(_ps(entry=100.0), _ms(hit, spread=10.0), _ALPHA, p)
    assert got.action == ep.EXIT and got.reason_code == "ATR_STOP_HIT"
    up_hit = [100.0] * 9 + [105.0, 110.0, 115.0, 120.0, 125.0, 130.0]
    up_hold = [100.0] * 10 + [105.0, 110.0, 115.0, 120.0, 125.0]
    assert ep.atr_stop(_ps(qty=-1.0, entry=100.0), _ms(up_hit, spread=10.0), _ALPHA, p).action == ep.EXIT
    assert ep.atr_stop(_ps(qty=-1.0, entry=100.0), _ms(up_hold, spread=10.0), _ALPHA, p).action == ep.HOLD
    ins = ep.atr_stop(_ps(entry=100.0), _ms([100.0] * 5, spread=10.0), _ALPHA, p)
    assert ins.action == ep.HOLD and ins.reason_code == "ATR_INSUFFICIENT_DATA"


def test_chandelier_uses_watermark_not_entry():
    p = ep.ChandelierParams(mult=3.0, window=14)
    down_hold = [100.0] * 11 + [95.0, 94.0, 93.0, 91.0]
    down_hit = [100.0] * 11 + [95.0, 94.0, 93.0, 90.0]
    ps = _ps(entry=100.0, hi=120.0)           # level = 120 - 3×10 = 90(非 entry-30)
    assert ep.chandelier(ps, _ms(down_hold, spread=10.0), _ALPHA, p).action == ep.HOLD
    assert ep.chandelier(ps, _ms(down_hit, spread=10.0), _ALPHA, p).action == ep.EXIT
    up_hit = [100.0] * 11 + [105.0, 106.0, 108.0, 110.0]
    up_hold = [100.0] * 11 + [105.0, 106.0, 108.0, 109.0]
    ps_s = _ps(qty=-1.0, entry=100.0, lo=80.0)  # level = 80 + 30 = 110
    assert ep.chandelier(ps_s, _ms(up_hit, spread=10.0), _ALPHA, p).action == ep.EXIT
    assert ep.chandelier(ps_s, _ms(up_hold, spread=10.0), _ALPHA, p).action == ep.HOLD


def test_time_stop_boundary_and_flat():
    p = ep.TimeStopParams(max_periods=8)
    assert ep.time_stop(_ps(held=7), _ms([100.0]), _ALPHA, p).action == ep.HOLD
    hit = ep.time_stop(_ps(held=8), _ms([100.0]), _ALPHA, p)
    assert hit.action == ep.EXIT and hit.reason_code == "TIME_STOP_HIT"
    assert ep.time_stop(_ps(qty=0.0, entry=None), _ms([100.0]), _ALPHA, p).reason_code == "FLAT_OR_NO_ENTRY"


def test_scale_out_levels_one_way_and_flat():
    p = ep.ScaleOutParams(levels=((0.10, 0.5), (0.20, 0.25)))
    assert ep.scale_out(_ps(fav=0.099), _ms([100.0]), _ALPHA, p).action == ep.HOLD
    d1 = ep.scale_out(_ps(fav=0.10), _ms([100.0]), _ALPHA, p)
    assert (d1.action, d1.target_fraction, d1.reason_code) == (ep.REDUCE, 0.5, "SCALE_OUT_LEVEL_0")
    d2 = ep.scale_out(_ps(fav=0.25), _ms([100.0]), _ALPHA, p)
    assert (d2.action, d2.target_fraction, d2.reason_code) == (ep.REDUCE, 0.25, "SCALE_OUT_LEVEL_1")
    assert ep.scale_out(_ps(qty=0.0, entry=None, fav=0.5), _ms([100.0]), _ALPHA, p).action == ep.HOLD


def test_vol_adaptive_stop_horizon_scaling():
    closes = [100.0 * (1.01 if i % 2 else 1.0) for i in range(31)]
    sigma = statistics.pstdev([closes[i + 1] / closes[i] - 1.0 for i in range(30)])
    p = ep.VolAdaptiveStopParams(base_mult=2.0, vol_lookback=30)
    ms = _ms(closes)                          # 现价 100;σ 由同一序列独立复算
    dist = 2.0 * sigma                        # held=1 → horizon=1
    # 边界 = 浮亏恰 2σ:entry = 100/(1-2σ);±0.1% 偏移避开浮点尘埃
    assert ep.vol_adaptive_stop(_ps(entry=100.0 / (1 - dist) * 1.001, held=1),
                                ms, _ALPHA, p).action == ep.EXIT
    assert ep.vol_adaptive_stop(_ps(entry=100.0 / (1 - dist) * 0.999, held=1),
                                ms, _ALPHA, p).action == ep.HOLD
    # held=9 → horizon=3,距离×3:同一浮亏不再触发(确认 √t 缩放生效)
    assert ep.vol_adaptive_stop(_ps(entry=100.0 / (1 - dist) * 1.001, held=9),
                                ms, _ALPHA, p).action == ep.HOLD
    ins = ep.vol_adaptive_stop(_ps(entry=100.0, held=1), _ms(closes[:10]), _ALPHA, p)
    assert ins.action == ep.HOLD and ins.reason_code == "VOL_INSUFFICIENT_DATA"


def test_risk_budget_reduce_ladder_and_no_exit_action():
    p = ep.RiskBudgetReduceParams()
    ms = lambda px: _ms([px])
    assert ep.risk_budget_reduce(_ps(hi=100.0), ms(96.0), _ALPHA, p).action == ep.HOLD       # dd 4%
    d = ep.risk_budget_reduce(_ps(hi=100.0), ms(95.0), _ALPHA, p)                            # 恰 5%
    assert (d.action, d.target_fraction, d.reason_code) == (ep.REDUCE, 0.5, "RISK_BUDGET_DE_RISK")
    assert ep.risk_budget_reduce(_ps(hi=100.0), ms(90.0), _ALPHA, p).target_fraction == 0.25
    flat = ep.risk_budget_reduce(_ps(hi=100.0), ms(80.0), _ALPHA, p)                         # dd 20%
    assert (flat.action, flat.target_fraction, flat.reason_code) == (ep.REDUCE, 0.0, "RISK_BUDGET_FLAT")
    assert flat.action != ep.EXIT                       # 关键:无闩锁语义,绝不是 EXIT
    s = ep.risk_budget_reduce(_ps(qty=-1.0, lo=100.0), _ms([105.3]), _ALPHA, p)            # 空 dd≈5%
    assert (s.action, s.target_fraction) == (ep.REDUCE, 0.5)


# ---------------------------------------------------------------------------
# 权重流后处理器(接缝)
# ---------------------------------------------------------------------------

def test_runner_baseline_is_identity():
    periods = [_T0 + i * _DAY for i in range(6)]
    weights = {t: {"A": 0.5, "B": -0.25} for t in periods}
    res = ep.apply_exit_policy(periods=periods, weights_by_period=weights,
                               bars_by_instrument={"A": _bars([100] * 6), "B": _bars([50] * 6)},
                               policy="baseline_signal_only")
    assert res.weights == {t: dict(w) for t, w in weights.items()}
    assert all(e.action == ep.HOLD for e in res.events)
    assert all(e.policy_version == ep.V_BASELINE for e in res.events)


def test_runner_exit_lockout_and_signal_reset_rearm():
    periods = [_T0 + i * _DAY for i in range(5)]
    px = [100.0, 89.0, 95.0, 88.0, 96.0]              # t1 触发 fixed_stop(-11%)
    sig = [0.5, 0.5, 0.5, 0.0, 0.5]                   # t3 信号归零 → 复位;t4 重新进场
    weights = {t: ({"A": w} if w else {}) for t, w in zip(periods, sig)}
    res = ep.apply_exit_policy(periods=periods, weights_by_period=weights,
                               bars_by_instrument={"A": _bars(px)}, policy="fixed_stop",
                               params=ep.FixedStopParams(0.10))
    assert res.weights[periods[0]] == {"A": 0.5}
    assert periods[1] not in res.weights              # 止损 → 空仓
    assert periods[2] not in res.weights              # 锁定中:信号仍 0.5 但保持空仓
    lockout = [e for e in res.events if e.reason_code == "STOP_LOCKOUT"]
    assert len(lockout) == 1 and lockout[0].t == periods[2]
    assert periods[3] not in res.weights              # 信号归零(基线本就空仓)
    assert res.weights[periods[4]] == {"A": 0.5}      # 复位后重新武装进场
    exits = [e for e in res.events if e.reason_code == "FIXED_STOP_HIT"]
    assert len(exits) == 1 and exits[0].t == periods[1]


def test_runner_never_increases_exposure_all_policies():
    periods = [_T0 + i * _DAY for i in range(8)]
    px = [100.0, 103.0, 97.0, 88.0, 95.0, 120.0, 90.0, 99.0]
    weights = {t: {"A": 0.6, "B": -0.4} for t in periods}
    bars = {"A": _bars(px), "B": _bars([c / 2 for c in px])}
    for name in ep.POLICY_REGISTRY:
        res = ep.apply_exit_policy(periods=periods, weights_by_period=weights,
                                   bars_by_instrument=bars, policy=name)
        for t, w in res.weights.items():
            for inst, x in w.items():
                base = weights[t][inst]
                assert abs(x) <= abs(base) + 1e-12 and (x == 0 or (x > 0) == (base > 0)), name


def test_runner_appended_future_bars_change_nothing():
    periods = [_T0 + i * _DAY for i in range(6)]
    px = [100.0, 101.0, 99.0, 98.0, 102.0, 103.0]
    weights = {t: {"A": 0.5} for t in periods}
    base = ep.apply_exit_policy(periods=periods, weights_by_period=weights,
                                bars_by_instrument={"A": _bars(px)}, policy="risk_budget_reduce")
    poisoned = _bars(px) + _bars([0.01, 1e6, 42.0], start=periods[-1] + _DAY)
    fut = ep.apply_exit_policy(periods=periods, weights_by_period=weights,
                               bars_by_instrument={"A": poisoned}, policy="risk_budget_reduce")
    assert base.weights == fut.weights and base.events == fut.events


def test_runner_risk_budget_recovers_without_signal_reset():
    periods = [_T0 + i * _DAY for i in range(3)]
    px = [100.0, 79.0, 97.0]                          # dd 21% → ×0;修复 → 乘数回升
    weights = {t: {"A": 0.5} for t in periods}
    res = ep.apply_exit_policy(periods=periods, weights_by_period=weights,
                               bars_by_instrument={"A": _bars(px)}, policy="risk_budget_reduce")
    assert res.weights[periods[0]] == {"A": 0.5}
    assert periods[1] not in res.weights              # RISK_BUDGET_FLAT(非 EXIT,无锁定)
    assert res.weights[periods[2]] == {"A": 0.5}      # 信号未复位,乘数随修复自动回 1.0
    assert not any(e.reason_code == "STOP_LOCKOUT" for e in res.events)


def test_runner_missing_bars_passthrough_and_unknown_policy():
    periods = [_T0 + i * _DAY for i in range(3)]
    weights = {t: {"A": 0.5} for t in periods}
    res = ep.apply_exit_policy(periods=periods, weights_by_period=weights,
                               bars_by_instrument={}, policy="fixed_stop")
    assert res.weights == weights                     # 无数据 → fail-safe 直通
    assert all(e.reason_code == "NO_MARKET_DATA" for e in res.events)
    with pytest.raises(KeyError):
        ep.apply_exit_policy(periods=periods, weights_by_period=weights,
                             bars_by_instrument={}, policy="nonexistent")


# ---------------------------------------------------------------------------
# 增量度量 + 预注册选择规则
# ---------------------------------------------------------------------------

def test_period_dollar_pnl_and_profit_factor():
    pnls = ep.period_dollar_pnl([1_010_000.0, 990_000.0, 1_030_000.0], 1_000_000.0)
    assert pnls == [10_000.0, -20_000.0, 40_000.0]
    assert ep.profit_factor(pnls) == pytest.approx(2.5)
    assert ep.profit_factor([1.0, 2.0]) is None       # 无亏损期 → None,不报 inf
    assert ep.profit_factor([-1.0, 2.0]) == pytest.approx(2.0)


def test_right_tail_share_definition():
    pnls = [100.0] + [-10.0] * 9 + [50.0]             # n=11 → top ceil(11/10)=2;总=60
    assert ep.right_tail_share(pnls) == pytest.approx(150.0 / 60.0)   # >1:右尾之外净亏
    assert ep.right_tail_share([-5.0] * 10) is None   # 总 PnL ≤0 → None
    assert ep.right_tail_share([]) is None


def test_delta_concentration_thresholds():
    concentrated = ep.delta_concentration([100.0, -20.0, -10.0])   # k=1 覆盖总增量 70
    assert concentrated["periods_for_delta"] == 1
    assert concentrated["concentration_ok"] is False
    assert "CONCENTRATED_DELTA" in concentrated["selection_warning"]
    narrow = ep.delta_concentration([30.0] * 6 + [-80.0])          # k=4 → 硬失败
    assert narrow["periods_for_delta"] == 4 and narrow["concentration_ok"] is False
    soft = ep.delta_concentration([20.0] * 7 + [-40.0])            # k=6 → 软告警
    assert soft["concentration_ok"] is True and "NARROW_DELTA" in soft["selection_warning"]
    flat = ep.delta_concentration([-5.0, -5.0])                    # 负增量无法"集中"
    assert flat["periods_for_delta"] is None and flat["concentration_ok"] is True
    assert flat["selection_warning"] is None


def test_evaluate_selection_rule_matrix():
    ok = ep.evaluate_selection(sharpe_delta=0.1, mdd_delta=-0.05, profit_factor_value=1.5,
                               baseline_profit_factor=1.2, delta_pnl=1000.0, periods_for_delta=7)
    assert ok["worth_further_study"] is True and ok["failed"] == []
    base = dict(sharpe_delta=0.1, mdd_delta=0.0, profit_factor_value=1.5,
                baseline_profit_factor=1.2, delta_pnl=1000.0, periods_for_delta=7)
    assert ep.evaluate_selection(**{**base, "sharpe_delta": 0.0})["worth_further_study"] is False
    assert ep.evaluate_selection(**{**base, "mdd_delta": 0.021})["failed"] == ["mdd_delta<=0.02"]
    assert ep.evaluate_selection(**{**base, "periods_for_delta": 3})["concentration_ok"] is False
    neg = ep.evaluate_selection(**{**base, "delta_pnl": -1.0, "periods_for_delta": None})
    assert neg["concentration_ok"] is True and neg["worth_further_study"] is True
    assert ep.evaluate_selection(**{**base, "profit_factor_value": 1.1})["pf_ok"] is False
    assert ep.evaluate_selection(**{**base, "profit_factor_value": None})["pf_ok"] is False
    both_none = ep.evaluate_selection(**{**base, "profit_factor_value": None,
                                         "baseline_profit_factor": None})
    assert both_none["pf_ok"] is True
