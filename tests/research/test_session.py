"""#2 remainder — reproducible research session: persisted data -> guided search ->
registry persistence, in one deterministic call; contained + cross-session."""
import itertools

import pytest
from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.data import Bar
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.data.persistent_store import SqlitePITStore
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.ports.persistence import ExperimentStoreError
from services.intelligence.search_space import HypothesisSpace
from services.intelligence.research_session import (
    run_research_session as _run_research_session,
)
from services.research import session as session_core


_SEARCH_SEQUENCE = itertools.count()


def run_research_session(*args, **kwargs):
    """Every test states durable versus intentionally process-local execution."""
    if kwargs.get("registry") is None:
        kwargs["ephemeral"] = True
    else:
        kwargs.setdefault("search_id", f"test-session-{next(_SEARCH_SEQUENCE)}")
        kwargs.setdefault("search_kind", "TEST_RESEARCH_SESSION")
        kwargs.setdefault("search_source", "tests.research.test_session")
    return _run_research_session(*args, **kwargs)


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
    out = run_research_session(
        store,
        registry=reg,
        search_id="session-main",
        **_kw(),
    )
    assert out["dataset_version"]                         # derived from data content
    assert out["recorded_run_ids"]                       # evaluations persisted
    for rid in out["recorded_run_ids"]:
        assert reg.get_evaluation(rid) is not None        # retrievable artifact
    assert {row["run_id"] for row in reg.search_evaluations("session-main")} == set(
        out["recorded_run_ids"]
    )
    cat = AlphaCatalog()
    reg.load_into(cat)
    for link in reg.search_evaluations("session-main"):
        evaluation_row = reg.get_evaluation(link["run_id"])
        assert (
            link["config_hash"]
            == cat.get(evaluation_row["alpha_id"]).definition_hash
        )
    assert all(cat.state(a) != AlphaState.APPROVED for a in cat.all_ids())   # contained
    assert [row["status"] for row in reg.search_attempt_events("session-main")] == [
        "STARTED",
        "COMPLETED",
    ]
    candidates = reg.search_candidates("session-main")
    assert candidates
    assert {row["config_hash"] for row in candidates} <= set(
        reg._store.all_trial_hashes()
    )
    provenance = [
        provenance_row["record"]
        for candidate_row in candidates
        for provenance_row in reg.trial_provenance_for_config(
            candidate_row["config_hash"]
        )
    ]
    assert len(provenance) == len(candidates)
    assert {record["actor_id"] for record in provenance} == {
        "tests.research.test_session"
    }
    assert all(record["status"] == "RECORDED" for record in provenance)


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


def test_session_re_evaluates_same_candidates_on_a_new_dataset_version():
    store, reg = _store(), _reg()
    first = run_research_session(
        store,
        registry=reg,
        search_id="dataset-v1-search",
        dataset_version="dataset-v1",
        **_kw(),
    )
    canonical_rows = reg._store._conn.execute(
        "SELECT alpha_id FROM alpha_state ORDER BY alpha_id LIMIT 2"
    ).fetchall()
    assert len(canonical_rows) == 2
    for row, state in zip(canonical_rows, ("PAPER_TRADING", "REJECTED")):
        reg._store._conn.execute(
            "UPDATE alpha_state SET state=? WHERE alpha_id=?",
            (state, row["alpha_id"]),
        )
    reg._store._conn.commit()
    before = {
        row["alpha_id"]: tuple(row)
        for row in reg._store._conn.execute(
            "SELECT alpha_id,definition_json,state,returns_json,sharpe,pvalue "
            "FROM alpha_state ORDER BY alpha_id"
        )
    }
    second = run_research_session(
        store,
        registry=reg,
        search_id="dataset-v2-search",
        dataset_version="dataset-v2",
        **_kw(),
    )

    assert first["recorded_run_ids"]
    assert second["recorded_run_ids"], second["report"]
    assert set(first["recorded_run_ids"]).isdisjoint(second["recorded_run_ids"])
    assert [row["status"] for row in reg.search_attempt_events("dataset-v2-search")] == [
        "STARTED",
        "COMPLETED",
    ]
    assert reg.search_candidates("dataset-v2-search")
    after = {
        row["alpha_id"]: tuple(row)
        for row in reg._store._conn.execute(
            "SELECT alpha_id,definition_json,state,returns_json,sharpe,pvalue "
            "FROM alpha_state ORDER BY alpha_id"
        )
    }
    for alpha_id, canonical in before.items():
        assert after[alpha_id] == canonical


def test_trial_recorder_failure_is_batch_fatal_and_retained(monkeypatch):
    store, reg = _store(), _reg()

    def fail_candidates(*_args, **_kwargs):
        raise ExperimentStoreError("candidate persistence unavailable")

    monkeypatch.setattr(reg, "register_search_candidates", fail_candidates)
    with pytest.raises(ExperimentStoreError, match="candidate persistence"):
        run_research_session(
            store,
            registry=reg,
            search_id="recorder-failed-search",
            dataset_version="dataset-v1",
            **_kw(),
        )

    assert [
        row["status"]
        for row in reg.search_attempt_events("recorder-failed-search")
    ] == ["STARTED", "FAILED"]


def test_trial_recording_provider_records_before_call_and_preserves_evidence_hook():
    order = []
    catalog = AlphaCatalog(
        trial_recorder=lambda config_hash: order.append(("record", config_hash))
    )
    definition = AlphaDefinition(
        alpha_id="candidate",
        name="Candidate",
        factor_ids=("close",),
    )

    class FailingProvider:
        def __call__(self, received):
            order.append(("provider", received.definition_hash))
            raise RuntimeError("provider failed")

        @staticmethod
        def leakage_ok(_received):
            return True

    provider = session_core._TrialRecordingProvider(FailingProvider(), catalog)
    assert provider.leakage_ok(definition) is True
    with pytest.raises(RuntimeError, match="provider failed"):
        provider(definition)

    assert order == [
        ("record", definition.definition_hash),
        ("provider", definition.definition_hash),
    ]
    assert catalog.global_trial_count() == 1


def test_guided_session_persists_provider_failure_before_isolating_it(
    monkeypatch,
):
    store, registry = _store(), _reg()
    original_factory = session_core.make_data_periods_provider
    failed_hashes = []

    def probing_factory(*args, **kwargs):
        delegate = original_factory(*args, **kwargs)

        class Probe:
            def __call__(self, definition):
                rows = registry.search_candidates("provider-failure-search")
                assert definition.definition_hash in {
                    row["config_hash"] for row in rows
                }
                if not failed_hashes:
                    failed_hashes.append(definition.definition_hash)
                    raise RuntimeError("provider failed after durable registration")
                return delegate(definition)

            def __getattr__(self, name):
                return getattr(delegate, name)

        return Probe()

    monkeypatch.setattr(session_core, "make_data_periods_provider", probing_factory)
    params = {**_kw(), "rounds": 1, "batch_size": 2}
    result = run_research_session(
        store,
        registry=registry,
        search_id="provider-failure-search",
        **params,
    )

    registered = {
        row["config_hash"]
        for row in registry.search_candidates("provider-failure-search")
    }
    assert set(failed_hashes) <= registered
    assert len(registered) > len(result["report"])
    assert [
        row["status"]
        for row in registry.search_attempt_events("provider-failure-search")
    ] == ["STARTED", "COMPLETED"]


def test_session_works_without_registry():
    out = run_research_session(_store(), registry=None, **_kw())
    assert out["dataset_version"] and out["recorded_run_ids"] == []   # ran, nothing persisted


def test_intelligence_composition_rejects_runtime_override():
    with pytest.raises(TypeError, match="fixed by the intelligence composition boundary"):
        run_research_session(
            _store(), registry=None, search_runtime=object(), **_kw()
        )


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
