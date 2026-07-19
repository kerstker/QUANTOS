"""MCTS/UCT alpha search — smarter navigation of the formula-tree space than blind GP.

Instead of generational mutate/crossover, MCTS grows a search tree: each node is a candidate
alpha, UCT balances exploiting high-reward branches vs exploring new refinements. Same
containment (every eval through the UNCHANGED ResearchAgentLoop -> agent never APPROVES) and
same multiple-testing honesty (each distinct genome = one catalog trial). Deterministic
(no RNG: argmax-UCT with id tie-break, refinements enumerated in a fixed order).
"""
from quantos.contracts.data import Bar
from quantos.contracts.research import AlphaState
from services.data.persistent_store import SqlitePITStore
from services.intelligence.agent_loop import ResearchAgentLoop
from services.intelligence.evolutionary_search import make_evolution_provider
from services.intelligence.mcts_search import MCTSAlphaSearch
from services.research.ports.persistence import TrialPersistenceError

DATES = list(range(24))
INSTS = [f"i{k}" for k in range(6)]


def _provider():
    s = SqlitePITStore(":memory:", create=True)
    for k, inst in enumerate(INSTS):
        base = 10.0 + k
        for t in DATES:
            px = base * (1.0 + 0.01 * ((t * (k + 1)) % 7 - 3))
            s.add(inst, Bar(instrument_id=inst, event_time=t, knowledge_time=t,
                            open=px, high=px + 1, low=max(px - 1, 0.1), close=px, volume=1000))
    return make_evolution_provider(s, None, INSTS, event_times=DATES, fwd_horizon=1)


def test_mcts_deterministic_and_contained():
    seed = [("P", "momentum", "5"), ("P", "zscore", "3")]
    def run():
        loop = ResearchAgentLoop()
        out = MCTSAlphaSearch(_provider(), inner_loop=loop).run(seed, iterations=12, timestamp=10)
        return out, loop
    a, la = run()
    b, _lb = run()
    assert a["best"] == b["best"] and a["n_evaluated"] == b["n_evaluated"]   # deterministic
    assert a["n_evaluated"] > len(seed)                                      # expanded beyond seeds
    for aid in la.catalog.all_ids():
        assert la.catalog.state(aid) != AlphaState.APPROVED                  # containment


def test_mcts_trial_count_grows_one_per_distinct_genome():
    loop = ResearchAgentLoop()
    MCTSAlphaSearch(_provider(), inner_loop=loop).run([("P", "momentum", "5")],
                                                      iterations=8, timestamp=10)
    assert loop.catalog.global_trial_count() >= 2          # each distinct alpha = one trial


def test_mcts_navigates_tree_genomes():
    out = MCTSAlphaSearch(_provider(), inner_loop=ResearchAgentLoop()).run(
        [("T", ("leaf", "momentum", "5"))], iterations=6, timestamp=10)
    assert out["best"] and all(isinstance(r, tuple) for r, _ in out["best"])


def test_mcts_fail_closed_on_bad_args():
    import pytest
    with pytest.raises(ValueError):
        MCTSAlphaSearch(_provider()).run([], iterations=5)            # empty seed
    with pytest.raises(ValueError):
        MCTSAlphaSearch(_provider()).run([("P", "momentum", "5")], iterations=0)


def test_mcts_trial_persistence_error_is_batch_fatal():
    import pytest

    def unavailable(_definition):
        raise TrialPersistenceError("ledger unavailable")

    with pytest.raises(TrialPersistenceError, match="ledger unavailable"):
        MCTSAlphaSearch(unavailable).run(
            [("P", "momentum", "5")],
            iterations=1,
        )
