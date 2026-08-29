"""Pipeline integration — guided search runs end-to-end on REAL persisted-data
PanelPeriods (store -> panel_builder -> GuidedSearchLoop), still contained
(no APPROVED) and deterministic."""
import pytest
from quantos.contracts.data import Bar
from quantos.contracts.research import AlphaState
from services.data.persistent_store import SqlitePITStore
from services.research.panel_builder import make_data_periods_provider
from services.intelligence.search_space import HypothesisSpace
from services.intelligence.guided_search import GuidedSearchLoop


def _bar(inst, t, close):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t,
               open=close, high=close + 1, low=max(close - 1, 0.1), close=close, volume=1000)


def _store():
    s = SqlitePITStore(":memory:", create=True)
    # deterministic multi-instrument series over 24 periods
    for k in range(6):
        base = 10.0 + k
        for t in range(24):
            close = base * (1.0 + 0.01 * ((t * (k + 1)) % 7 - 3))
            s.add(f"i{k}", _bar(f"i{k}", t, close))
    return s


def _space():
    return HypothesisSpace(factors=("i0",), operators=("momentum", "zscore"), windows=(3, 5))


def _provider(store):
    return make_data_periods_provider(store, [f"i{k}" for k in range(6)],
                                      event_times=list(range(24)), fwd_horizon=1)


def test_guided_search_runs_on_persisted_data_and_is_contained():
    store = _store()
    loop = GuidedSearchLoop(_space())
    out = loop.search(rounds=4, batch_size=2, periods_provider=_provider(store),
                      diagnostic_only=True)
    assert out["report"]                                 # produced evaluations on real data
    cat = loop.inner_loop.catalog
    assert all(cat.state(a) != AlphaState.APPROVED for a in cat.all_ids())   # contained


def test_integration_is_deterministic():
    a = GuidedSearchLoop(_space()).search(rounds=4, batch_size=2,
                                          periods_provider=_provider(_store()), diagnostic_only=True)
    b = GuidedSearchLoop(_space()).search(rounds=4, batch_size=2,
                                          periods_provider=_provider(_store()), diagnostic_only=True)
    assert a["arm_means"] == b["arm_means"]
