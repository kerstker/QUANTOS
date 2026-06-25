"""进化搜索:基因型映射、变异/交叉、provider 派发、run、容器化、多重检验、错误隔离。"""
import pytest
from quantos.contracts.data import Bar
from quantos.contracts.feature import FeatureValue
from quantos.contracts.research import AlphaState
from services.data.persistent_store import SqlitePITStore
from services.data.fundamental_store import SqliteFundamentalStore
from services.intelligence.agent_loop import ResearchAgentLoop
from services.intelligence.evolutionary_search import (
    genome_to_definition, mutate, crossover, make_evolution_provider, EvolutionarySearchLoop)

DATES = list(range(24))
INSTS = [f"i{k}" for k in range(6)]


def _bar(inst, t, close):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t,
               open=close, high=close + 1, low=max(close - 1, 0.1), close=close, volume=1000)


def _price(insts=INSTS):
    s = SqlitePITStore(":memory:", create=True)
    for k, inst in enumerate(insts):
        base = 10.0 + k
        for t in DATES:
            s.add(inst, _bar(inst, t, base * (1.0 + 0.01 * ((t * (k + 1)) % 7 - 3))))
    return s


def _fund(insts=INSTS):
    fs = SqliteFundamentalStore(":memory:", create=True)
    for k, inst in enumerate(insts):
        fs.add(FeatureValue(feature_id="eps", instrument_id=inst, event_time=0, knowledge_time=0, value=1.0 + k))
    return fs


def _provider(price=None, fund=None):
    return make_evolution_provider(price or _price(), fund if fund is not None else _fund(),
                                   INSTS, event_times=DATES, fwd_horizon=1)


def test_genome_to_definition():
    p = genome_to_definition(("P", "momentum", "5"))
    assert p.alpha_id == "h_momentum_5" and p.factor_ids == ("close",)
    assert p.params == {"operator": "momentum", "window": "5"}
    f = genome_to_definition(("F", "eps", "raw"))
    assert f.alpha_id == "hf_eps_raw" and f.params["family"] == "fundamental"
    x = genome_to_definition(("X", ("price", "momentum", "5"), ("fund", "eps", "raw"), "sum"))
    assert x.params["family"] == "interaction"
    with pytest.raises(ValueError):
        genome_to_definition(("Z", "x"))


def test_mutate_price_deterministic():
    out = mutate(("P", "momentum", "5"))
    assert ("P", "zscore", "5") in out and ("P", "momentum", "3") in out
    assert ("P", "momentum", "5") not in out          # 不含自身
    assert out == sorted(set(out))                    # 确定性、去重


def test_crossover_atomic_to_interaction():
    out = crossover(("P", "momentum", "5"), ("F", "eps", "raw"))
    assert ("X", ("price", "momentum", "5"), ("fund", "eps", "raw"), "sum") in out
    assert ("X", ("price", "momentum", "5"), ("fund", "eps", "raw"), "product") in out
    # 非原子亲本不交叉
    assert crossover(("X", ("price", "momentum", "5"), ("fund", "eps", "raw"), "sum"),
                     ("P", "momentum", "5")) == []


def test_provider_dispatch():
    prov = _provider()
    for g in [("P", "momentum", "5"), ("F", "eps", "ratio_to_price"),
              ("X", ("price", "momentum", "5"), ("fund", "eps", "ratio_to_price"), "product")]:
        assert isinstance(prov(genome_to_definition(g)), list)


def test_run_evolves_and_is_deterministic():
    seed = [("P", "momentum", "5"), ("P", "zscore", "3"), ("F", "eps", "ratio_to_price")]
    def run():
        return EvolutionarySearchLoop(_provider(), inner_loop=ResearchAgentLoop()).run(
            seed, generations=2, population_size=4, elite_k=2, timestamp=10)
    a = run()
    assert a["best"] and a["n_evaluated"] > len(seed)     # 后代(变异/交叉)被创造并评估
    b = run()
    assert a["best"] == b["best"] and a["n_evaluated"] == b["n_evaluated"]   # 确定性


def test_containment_no_approved():
    loop = ResearchAgentLoop()
    EvolutionarySearchLoop(_provider(), inner_loop=loop).run(
        [("P", "momentum", "5"), ("F", "eps", "ratio_to_price")],
        generations=2, population_size=4, elite_k=2, timestamp=10)
    assert not loop.actor.is_human
    for aid in loop.catalog.all_ids():
        assert loop.catalog.state(aid) != AlphaState.APPROVED   # agent 永不可达 APPROVED


def test_multiple_testing_trial_count_grows():
    loop = ResearchAgentLoop()
    EvolutionarySearchLoop(_provider(), inner_loop=loop).run(
        [("P", "momentum", "5"), ("P", "zscore", "3")],
        generations=2, population_size=4, elite_k=2, timestamp=10)
    assert loop.catalog.global_trial_count() >= 2          # 每个不同基因型计一次试验


def test_provider_error_isolated():
    # 基本面基因型但无 fund_store → provider 抛 → reward 0,其余正常
    prov = make_evolution_provider(_price(), None, INSTS, event_times=DATES, fwd_horizon=1)
    out = EvolutionarySearchLoop(prov, inner_loop=ResearchAgentLoop()).run(
        [("P", "momentum", "5"), ("F", "eps", "raw")],
        generations=0, population_size=4, elite_k=2, timestamp=10)
    rewards = dict(out["best"])
    assert rewards[("F", "eps", "raw")] == 0.0
    assert ("P", "momentum", "5") in rewards
    assert any(h["genome"] == ("F", "eps", "raw") and h.get("error") for h in out["history"])
