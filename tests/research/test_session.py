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


def test_session_exposes_real_factor_returns():
    # the session must expose each evaluated factor's REAL per-period net return series so the console
    # snapshot can run the real risk/PnL engines on real data (Phase 1B), not synthetic sin/cos.
    out = run_research_session(_store(), registry=_reg(), **_kw())
    fr = out["factor_returns"]
    assert isinstance(fr, dict) and fr, "factor_returns must be a non-empty {alpha_id: [returns]} map"
    for aid, series in fr.items():
        assert isinstance(series, list) and len(series) >= 2
        assert all(isinstance(x, float) for x in series)


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


def _fund_store():
    from services.data.fundamental_store import SqliteFundamentalStore
    from quantos.contracts.feature import FeatureValue
    fs = SqliteFundamentalStore(":memory:", create=True)
    for k in range(6):
        fs.add(FeatureValue(feature_id="eps", instrument_id=f"i{k}",
                            event_time=0, knowledge_time=0, value=1.0 + k))
    return fs


def test_session_unified_price_and_fundamental_search():
    # one bandit searches BOTH price factors (h_*) and fundamental value factors (hf_*)
    store, reg = _store(), _reg()
    out = run_research_session(store, registry=reg, fund_store=_fund_store(), metrics=("eps",), **_kw())
    ids = set(out["report"].keys())
    assert any(a.startswith("h_") for a in ids)        # price hypotheses evaluated
    assert any(a.startswith("hf_") for a in ids)       # fundamental value factor evaluated
    cat = AlphaCatalog()
    reg.load_into(cat)
    assert all(cat.state(a) != AlphaState.APPROVED for a in cat.all_ids())   # contained
    b = run_research_session(_store(), registry=None, fund_store=_fund_store(), metrics=("eps",), **_kw())
    assert out["arm_means"] == b["arm_means"]          # deterministic


def test_session_evolutionary_mode():
    # evolutionary mode: machine-evolve alpha (price/value atoms + crossover interactions),
    # contained, recorded to registry, multiple-testing accounted, deterministic
    store, reg = _store(), _reg()
    out = run_research_session(store, registry=reg, fund_store=_fund_store(), metrics=("eps",),
                               evolutionary=True, evo_generations=2, evo_population=4, evo_elite=2, **_kw())
    assert out["mode"] == "evolutionary"
    assert out["best"] and out["best"] == sorted(out["best"], key=lambda gr: (-gr[1], gr[0]))  # 降序
    ids = set(out["report"].keys())
    assert any(a.startswith("h_") for a in ids)                       # 价格原子
    assert any(a.startswith("hx_") or a.startswith("hf_") for a in ids)  # 交互 或 价值因子
    assert out["recorded_run_ids"]                                    # 评估记入 registry
    cat = AlphaCatalog()
    reg.load_into(cat)
    assert cat.global_trial_count() >= 1                              # 多重检验:每基因型计一次试验
    assert all(cat.state(a) != AlphaState.APPROVED for a in cat.all_ids())   # contained
    b = run_research_session(_store(), registry=None, fund_store=_fund_store(), metrics=("eps",),
                             evolutionary=True, evo_generations=2, evo_population=4, evo_elite=2, **_kw())
    assert out["best"] == b["best"]                                   # 确定性


def test_bandit_learning_persists_across_sessions():
    # the guided-search bandit's learned arm statistics survive into the next session
    store, reg = _store(), _reg()
    run_research_session(store, registry=reg, **_kw())
    from services.intelligence.search_strategy import UCBSearch
    s = UCBSearch(_kw()["space"])
    reg.load_bandit_into(s)
    assert s.n_evaluated >= 1 and s.arm_means()       # prior session's learning restored
    assert s.proposed                                 # and what was already tried is remembered
