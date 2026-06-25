"""#2 remainder — reproducible research session: persisted data -> guided search ->
registry persistence, in one deterministic call; contained + cross-session."""
import pytest
from quantos.contracts.data import Bar
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.data.persistent_store import SqlitePITStore
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.intelligence.search_space import HypothesisSpace
from services.research.session import run_research_session


def _bar(inst, t, close):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t,
               open=close, high=close + 1, low=max(close - 1, 0.1), close=close, volume=1000)


def _store():
    s = SqlitePITStore(":memory:", create=True)
    for k in range(6):
        base = 10.0 + k
        for t in range(24):
            s.add(f"i{k}", _bar(f"i{k}", t, base * (1.0 + 0.01 * ((t * (k + 1)) % 7 - 3))))
    return s


def _reg():
    return ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))


def _kw():
    return dict(instruments=[f"i{k}" for k in range(6)], event_times=list(range(24)),
                fwd_horizon=1, space=HypothesisSpace(factors=("i0",), operators=("momentum", "zscore"),
                                                     windows=(3, 5)),
                rounds=4, batch_size=2, timestamp=10)


def test_session_runs_persists_and_is_contained():
    store, reg = _store(), _reg()
    out = run_research_session(store, registry=reg, **_kw())
    assert out["dataset_version"]                         # derived from data content
    assert out["recorded_run_ids"]                       # evaluations persisted
    for rid in out["recorded_run_ids"]:
        assert reg.get_evaluation(rid) is not None        # retrievable artifact
    cat = AlphaCatalog()
    reg.load_into(cat)
    assert all(cat.state(a) != AlphaState.APPROVED for a in cat.all_ids())   # contained


def test_session_is_deterministic():
    a = run_research_session(_store(), registry=_reg(), **_kw())
    b = run_research_session(_store(), registry=_reg(), **_kw())
    assert a["dataset_version"] == b["dataset_version"]
    assert a["arm_means"] == b["arm_means"]
    assert a["recorded_run_ids"] == b["recorded_run_ids"]


def test_session_persists_trial_count_across_sessions():
    store, reg = _store(), _reg()
    run_research_session(store, registry=reg, **_kw())
    cat = AlphaCatalog()
    reg.load_into(cat)
    assert cat.global_trial_count() >= 1                  # trials survived the session


def test_session_works_without_registry():
    out = run_research_session(_store(), registry=None, **_kw())
    assert out["dataset_version"] and out["recorded_run_ids"] == []   # ran, nothing persisted


def test_bandit_learning_persists_across_sessions():
    # the guided-search bandit's learned arm statistics survive into the next session
    store, reg = _store(), _reg()
    run_research_session(store, registry=reg, **_kw())
    from services.intelligence.search_strategy import UCBSearch
    s = UCBSearch(_kw()["space"])
    reg.load_bandit_into(s)
    assert s.n_evaluated >= 1 and s.arm_means()       # prior session's learning restored
    assert s.proposed                                 # and what was already tried is remembered
