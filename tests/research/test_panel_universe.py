"""无幸存者偏差面板构建 + 日历缺口 + session 接入。"""
import itertools

import pytest
from quantos.contracts.data import Bar
from quantos.contracts.research import AlphaState
from services.data.persistent_store import SqlitePITStore
from services.data.universe import PITUniverse
from services.research.panel_builder import _iso, build_panel_periods, universe_data_gaps
from services.alpha.catalog import AlphaCatalog
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.intelligence.search_space import HypothesisSpace
from services.intelligence.research_session import (
    run_research_session as _run_research_session,
)


_SEARCH_SEQUENCE = itertools.count()


def run_research_session(*args, **kwargs):
    kwargs["diagnostic_only"] = True
    if kwargs.get("registry") is None:
        kwargs["ephemeral"] = True
    else:
        kwargs.setdefault("search_id", f"test-panel-universe-{next(_SEARCH_SEQUENCE)}")
        kwargs.setdefault("search_kind", "TEST_PANEL_UNIVERSE_SESSION")
        kwargs.setdefault("search_source", "tests.research.test_panel_universe")
    return _run_research_session(*args, **kwargs)


DATES = [20240102, 20240103, 20240104, 20240105, 20240106]


def _bar(inst, t, close):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t,
               open=close, high=close + 1, low=max(close - 1, 0.1), close=close, volume=1000)


def _full_store(insts):
    s = SqlitePITStore(":memory:", create=True)
    for k, inst in enumerate(insts):
        for j, t in enumerate(DATES):
            s.add(inst, _bar(inst, t, 10.0 + k + 0.5 * j))
    return s


# count, sumformat=len-based signal so inclusion is gated ONLY by universe + fwd return
def _count_signal(closes):
    return float(len(closes)) if closes else None


def test_iso_conversion():
    assert _iso(20240315) == "2024-03-15"
    assert _iso(20240102) == "2024-01-02"
    with pytest.raises(ValueError):
        _iso(2024)            # 非 YYYYMMDD
    with pytest.raises(ValueError):
        _iso(20240230)        # 不存在的日历日(2 月 30 日,审查 P2)


def _appearances(periods):
    c = {}
    for p in periods:
        for inst in p.instruments:
            c[inst] = c.get(inst, 0) + 1
    return c


def test_survivorship_free_gating():
    insts = ["i0", "i1", "i2", "i3"]
    store = _full_store(insts)                 # 所有标的每个交易日都有数据
    u = PITUniverse()
    u.add_membership("i0", "2024-01-02")
    u.add_membership("i1", "2024-01-02")
    u.add_membership("i3", "2024-01-02", "2024-01-03")   # 仅前两个决策日是成员,之后退出
    u.add_membership("i2", "2024-01-06")                 # 在所有决策日(0102-0105)之后才入会

    gated = build_panel_periods(store, insts, event_times=DATES, fwd_horizon=1,
                                signal_fn=_count_signal, universe=u)
    ungated = build_panel_periods(store, insts, event_times=DATES, fwd_horizon=1,
                                  signal_fn=_count_signal)
    ga, ua = _appearances(gated), _appearances(ungated)

    assert ga.get("i2", 0) == 0                # 决策时点从非成员 → 永不纳入(防幸存者偏差)
    assert ga.get("i3", 0) >= 1                # 成员期内纳入(虽随后退出/退市)
    assert ua.get("i2", 0) >= 1                # 不带 universe 时 i2 照常出现(零回归基线)
    assert ga["i0"] == ga["i1"] >= 1           # 全程成员者全程在场


def test_universe_none_is_byte_identical():
    insts = ["i0", "i1", "i2"]
    store = _full_store(insts)
    a = build_panel_periods(store, insts, event_times=DATES, fwd_horizon=1, signal_fn=_count_signal)
    b = build_panel_periods(store, insts, event_times=DATES, fwd_horizon=1, signal_fn=_count_signal, universe=None)
    assert [p.instruments for p in a] == [p.instruments for p in b]
    assert [p.forward_returns for p in a] == [p.forward_returns for p in b]


def test_universe_data_gaps():
    s = SqlitePITStore(":memory:", create=True)
    for inst in ("i0", "i1"):
        for t in DATES:
            s.add(inst, _bar(inst, t, 10.0))
    # i9 是成员但缺 20240104 当日 bar
    for t in DATES:
        if t != 20240104:
            s.add("i9", _bar("i9", t, 12.0))
    u = PITUniverse()
    for inst in ("i0", "i1", "i9"):
        u.add_membership(inst, "2024-01-02")
    rep = universe_data_gaps(s, u, DATES)
    assert rep["total_gaps"] == 1
    assert rep["by_date"].get("2024-01-04") == 1


def test_session_with_universe_runs_contained_deterministic():
    insts = [f"i{k}" for k in range(4)]
    u = PITUniverse()
    for inst in insts:
        u.add_membership(inst, "2024-01-02")
    kw = dict(instruments=insts, event_times=DATES, fwd_horizon=1,
              space=HypothesisSpace(factors=("i0",), operators=("momentum", "zscore"), windows=(2, 3)),
              rounds=3, batch_size=2, timestamp=20240601, universe=u)
    a = run_research_session(_full_store(insts),
                             registry=ExperimentRegistry(SqliteExperimentStore(":memory:", create=True)), **kw)
    assert "data_gaps" in a and a["data_gaps"]["total_gaps"] == 0     # 全员有数据 → 无缺口
    cat = AlphaCatalog()
    assert all(cat.state(x) != AlphaState.APPROVED for x in cat.all_ids())   # contained (空目录也成立)
    b = run_research_session(_full_store(insts), registry=None, **kw)
    assert a["arm_means"] == b["arm_means"]                            # 确定性
