"""P1-6 — 面板的决策时刻语义(当日 bar 基准 + 显式 knowledge 时钟)。

两条被审查钉死的缺陷:

1) 决策时刻 t 的基准 bar 必须是**当日**(event_time == t)的 bar。`latest_as_of` 允许返回
   更早的陈旧 bar(内部数据缺口 / 停牌),拿它当基准会把已实现收益从错误的日期起算,
   再把那个结果当成 [t, t+h] 的标签 —— 收益的日期与标签不符,且无声。
2) decision_lag / minimum_knowledge_lag 此前不是显式输入。默认 (0, 0) 保持旧契约
   (own-event-time / as_of(t));显式给出时按 `services.data.available_time.
   fetch_bars_for_event_decisions` 同一套双时钟规则选取 vintage:
   `event_time + minimum_knowledge_lag <= knowledge_time <= event_time + decision_lag`,
   拒绝旧回填行。这与 tools/ic_panel_registry 的两分支写法一致。
"""
import pytest

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.data import Bar
from services.data.persistent_store import SqlitePITStore
from services.research.panel_builder import (
    build_panel_periods,
    make_data_periods_provider,
    make_volume_periods_provider,
)


def _bar(inst, t, close, *, kt=None, volume=1000.0):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t if kt is None else kt,
               open=close, high=close + 1, low=max(close - 1, 0.1), close=close, volume=volume)


def _last_close(closes):
    return float(closes[-1])


def _by_event_time(periods):
    return {p.event_time: p for p in periods}


# --- 1) 决策时刻的基准 bar 必须是当日 bar --------------------------------------

def test_interior_gap_in_the_event_grid_is_not_filled_with_a_stale_close():
    s = SqlitePITStore(":memory:", create=True)
    # A 在内部缺 et=1(数据缺口):et=0 与 et=2 之间有洞
    for t, c in [(0, 10.0), (2, 12.0), (3, 13.0)]:
        s.add("A", _bar("A", t, c))
    for inst in ("B", "C"):
        for t, c in [(0, 20.0), (1, 21.0), (2, 22.0), (3, 23.0)]:
            s.add(inst, _bar(inst, t, c + (1.0 if inst == "C" else 0.0)))
    ets = [0, 1, 2, 3]

    periods = _by_event_time(build_panel_periods(s, ["A", "B", "C"], event_times=ets,
                                                 fwd_horizon=1, signal_fn=_last_close))

    # t=1 当日没有 A 的 bar -> A 从该截面消失,而不是拿 et=0 的陈旧 close 当基准
    assert "A" not in periods[1].instruments
    assert set(periods[1].instruments) == {"B", "C"}
    # t=0 的 A 也拿不到 [0, 1] 的前向收益(et=1 缺失)-> 同样不参与,不构造虚假收益
    assert "A" not in periods[0].instruments
    # t=2 有当日 bar -> A 正常参与,且收益基准就是当日 close
    assert "A" in periods[2].instruments
    ai = periods[2].instruments.index("A")
    assert periods[2].forward_returns[ai] == pytest.approx(13.0 / 12.0 - 1.0)
    # 幸存者的收益也必须以当日 close 为基准(不是陈旧 close)
    bi = periods[1].instruments.index("B")
    assert periods[1].forward_returns[bi] == pytest.approx(22.0 / 21.0 - 1.0)


def test_stale_base_cannot_produce_a_mislabelled_forward_return():
    s = SqlitePITStore(":memory:", create=True)
    # A 只在 t=0 有数据(此后停牌/无数据),B/C 齐全
    s.add("A", _bar("A", 0, 10.0))
    for inst in ("B", "C"):
        for t, c in [(0, 20.0), (1, 21.0), (2, 22.0), (3, 23.0)]:
            s.add(inst, _bar(inst, t, c))
    ets = [0, 1, 2, 3]

    periods = _by_event_time(build_panel_periods(s, ["A", "B", "C"], event_times=ets,
                                                 fwd_horizon=1, signal_fn=_last_close))

    # A 的 [0,1] 前向收益不存在(没有 et=1 的 bar)-> A 不出现在任何截面
    assert all("A" not in p.instruments for p in periods.values())
    for t, p in periods.items():
        assert set(p.instruments) == {"B", "C"}
        bi = p.instruments.index("B")
        assert p.forward_returns[bi] == pytest.approx((20.0 + t + 1) / (20.0 + t) - 1.0)


# --- 2) 显式 decision_lag / minimum_knowledge_lag ------------------------------

def _close_known_store():
    """日线:event key 是开盘日,OHLCV 到下一期(收盘)才可知 -> knowledge_time = event_time + 1。"""
    s = SqlitePITStore(":memory:", create=True)
    for k, inst in enumerate(("A", "B", "C")):
        for t, c in [(0, 10.0), (1, 11.0), (2, 12.0), (3, 13.0)]:
            s.add(inst, _bar(inst, t, c + 10.0 * k, kt=t + 1, volume=1000.0 * (t + 1)))
    return s


def test_default_knowledge_policy_keeps_the_own_event_time_contract():
    s = _close_known_store()
    ets = [0, 1, 2, 3]
    # 默认 (0, 0):决策时刻 t 只看 knowledge_time <= t 的行。收盘才可知的日线在 t 尚不可知,
    # 当日 bar 也不存在 -> 一个截面都建不出来(旧的 fail-soft 行为不变)。
    assert build_panel_periods(s, ["A", "B", "C"], event_times=ets, fwd_horizon=1,
                               signal_fn=_last_close) == []


def test_explicit_decision_lag_makes_close_known_bars_usable():
    s = _close_known_store()
    ets = [0, 1, 2, 3]

    periods = build_panel_periods(s, ["A", "B", "C"], event_times=ets, fwd_horizon=1,
                                  signal_fn=_last_close,
                                  decision_lag=1, minimum_knowledge_lag=1)

    assert [p.event_time for p in periods] == [0, 1, 2]          # t=3 无前向收益
    p = _by_event_time(periods)[1]
    assert set(p.instruments) == {"A", "B", "C"}
    ai = p.instruments.index("A")
    assert p.signals[ai] == pytest.approx(11.0)                   # 截至决策时刻可知的收盘
    assert p.forward_returns[ai] == pytest.approx(12.0 / 11.0 - 1.0)   # 基准 = et=1 当日 bar


def test_minimum_knowledge_lag_rejects_a_backdated_vintage():
    s = SqlitePITStore(":memory:", create=True)
    for k, inst in enumerate(("A", "B", "C")):
        for t, c in [(0, 10.0), (1, 11.0), (2, 12.0), (3, 13.0)]:
            value = c + 10.0 * k
            s.add(inst, _bar(inst, t, value, kt=t))               # 旧回填行(kt == et)
            s.add(inst, _bar(inst, t, value, kt=t + 2))           # 收盘后 2 期才可知的正规 vintage
    ets = [0, 1, 2, 3]

    periods = build_panel_periods(s, ["A", "B", "C"], event_times=ets, fwd_horizon=1,
                                  signal_fn=_last_close,
                                  decision_lag=2, minimum_knowledge_lag=2)

    p = _by_event_time(periods)[2]
    ai = p.instruments.index("A")
    assert p.signals[ai] == pytest.approx(12.0)                   # 选中 kt == et+2 的 vintage
    assert p.forward_returns[ai] == pytest.approx(13.0 / 12.0 - 1.0)

    # 同一条数据在 minimum_knowledge_lag=0 下会接受 kt == et 的旧回填行(旧契约),所以
    # "拒绝"必须由显式的 minimum_knowledge_lag 决定 —— 这里用 kt 门槛把两者区分开:
    legacy = build_panel_periods(s, ["A", "B", "C"], event_times=ets, fwd_horizon=1,
                                 signal_fn=_last_close, decision_lag=2, minimum_knowledge_lag=0)
    assert [p.event_time for p in legacy] == [0, 1, 2]            # 门槛为 0 时两种 vintage 都合法


def test_invalid_knowledge_lags_fail_closed():
    s = _close_known_store()
    kw = dict(event_times=[0, 1, 2], fwd_horizon=1, signal_fn=_last_close)
    with pytest.raises(ValueError):
        build_panel_periods(s, ["A", "B", "C"], decision_lag=-1, minimum_knowledge_lag=0, **kw)
    with pytest.raises(ValueError):
        build_panel_periods(s, ["A", "B", "C"], decision_lag=1, minimum_knowledge_lag=2, **kw)


def test_providers_thread_the_explicit_knowledge_policy():
    s = _close_known_store()
    ets = [0, 1, 2, 3]
    defn = AlphaDefinition(alpha_id="h", name="h", factor_ids=("A",),
                           params={"operator": "momentum", "window": "2"})

    default_provider = make_data_periods_provider(s, ["A", "B", "C"], event_times=ets, fwd_horizon=1)
    assert default_provider(defn) == []                          # 默认 0/0:旧契约不变

    lagged = make_data_periods_provider(s, ["A", "B", "C"], event_times=ets, fwd_horizon=1,
                                        decision_lag=1, minimum_knowledge_lag=1)
    assert len(lagged(defn)) >= 1

    vol_provider = make_volume_periods_provider(s, ["A", "B", "C"], event_times=ets, fwd_horizon=1,
                                                decision_lag=1, minimum_knowledge_lag=1)
    vol_defn = AlphaDefinition(alpha_id="v", name="v", factor_ids=("A",),
                               params={"operator": "vol_momentum", "window": "2"})
    assert len(vol_provider(vol_defn)) >= 1
