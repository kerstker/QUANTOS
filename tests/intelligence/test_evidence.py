"""断点1 (方案A) provider hook: the search loops collect a provider-supplied leakage_ok and forward it as
evidence to the agent loop. Backward-compatible (no hook -> {} -> evaluator stays fail-closed None). The real
data periods provider exposes a working leakage_ok (it has the PIT bars + the op), and a genuine trailing-window
op is detected leak-free; the whole chain reaches the evaluator's gate without weakening containment."""
from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.data import Bar
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.evaluator import AlphaEvaluator
from services.data.persistent_store import SqlitePITStore
from services.intelligence.agent_loop import ResearchAgentLoop
from services.intelligence.evidence import provider_leakage_evidence
from services.intelligence.guided_search import GuidedSearchLoop
from services.intelligence.search_space import HypothesisSpace
from services.research.panel_builder import make_data_periods_provider


def _defn(aid="a", op="momentum", w=3):
    return AlphaDefinition(alpha_id=aid, name=aid, factor_ids=("close",),
                           params={"operator": op, "window": str(w)})


def test_provider_without_hook_yields_empty_evidence():
    def bare(defn):
        return []
    assert provider_leakage_evidence(bare, [(_defn(), [])]) == {}     # backward-compatible


def test_provider_with_hook_builds_evidence_per_alpha():
    def prov(defn):
        return []
    prov.leakage_ok = lambda defn: True
    ev = provider_leakage_evidence(prov, [(_defn("x"), []), (_defn("y"), [])])
    assert ev == {"x": {"leakage_ok": True}, "y": {"leakage_ok": True}}


def test_hook_error_is_omitted_not_raised():
    def prov(defn):
        return []

    def boom(defn):
        raise RuntimeError("nope")
    prov.leakage_ok = boom
    assert provider_leakage_evidence(prov, [(_defn("x"), [])]) == {}  # omitted -> downstream fail-closed None


def _store():
    st = SqlitePITStore(":memory:", create=True)
    insts = ["A", "B", "C"]
    for k, inst in enumerate(insts):
        for i in range(14):
            px = 100.0 + i + k * (1 if i % 2 else -1)
            st.add(inst, Bar(instrument_id=inst, event_time=i + 1, knowledge_time=i + 1,
                             open=px, high=px + 1, low=px - 1, close=px, volume=100))
    return st, insts, list(range(1, 15))


def test_data_provider_exposes_working_leakage_ok():
    st, insts, ets = _store()
    prov = make_data_periods_provider(st, insts, event_times=ets, fwd_horizon=1)
    assert hasattr(prov, "leakage_ok")
    assert prov.leakage_ok(_defn(op="momentum", w=3)) is True        # trailing op is PIT-safe -> a REAL True


def _vol_store():
    st = SqlitePITStore(":memory:", create=True)
    insts = ["A", "B", "C"]
    for k, inst in enumerate(insts):
        for i in range(14):
            px = 100.0 + i + k
            vol = 100.0 + 10 * i + 5 * k + (3 if i % 2 else 0)       # varying volume so vol ops are finite
            st.add(inst, Bar(instrument_id=inst, event_time=i + 1, knowledge_time=i + 1,
                             open=px, high=px + 1, low=px - 1, close=px, volume=vol))
    return st, insts, list(range(1, 15))


def test_volume_provider_exposes_working_leakage_ok():
    from services.research.panel_builder import make_volume_periods_provider
    st, insts, ets = _vol_store()
    prov = make_volume_periods_provider(st, insts, event_times=ets, fwd_horizon=1)
    assert hasattr(prov, "leakage_ok")
    d = AlphaDefinition(alpha_id="v", name="v", factor_ids=("close",),
                        params={"operator": "vol_momentum", "window": "3"})
    assert prov.leakage_ok(d) is True                                # trailing volume op -> real True


def test_combined_provider_delegates_price_and_is_none_for_fundamental():
    from services.research.fundamental_factors import make_combined_periods_provider
    st, insts, ets = _store()
    prov = make_combined_periods_provider(st, None, insts, event_times=ets, fwd_horizon=1)
    assert hasattr(prov, "leakage_ok")
    price_d = _defn("p", op="momentum", w=3)
    fund_d = AlphaDefinition(alpha_id="f", name="f", factor_ids=("close",),
                             params={"operator": "momentum", "window": "3", "metric": "ep"})
    assert prov.leakage_ok(price_d) is True                          # delegated to the price detector
    assert prov.leakage_ok(fund_d) is None                           # fundamental: honest NOT-ASSESSED


def test_gp_tree_provider_exposes_real_leakage_ok():
    from services.research.gp_tree import make_tree_provider, tree_definition
    st, insts, ets = _store()
    prov = make_tree_provider(st, insts, event_times=ets, fwd_horizon=1)
    assert hasattr(prov, "leakage_ok")
    d = tree_definition(("leaf", "momentum", "3"))               # a GP expression tree
    assert d.params.get("family") == "gp_tree"
    assert prov.leakage_ok(d) is True                            # tree eval is trailing-closes -> VERIFIED safe


def test_evolution_provider_dispatches_leakage_by_family():
    from services.intelligence.evolutionary_search import make_evolution_provider
    from services.research.gp_tree import tree_definition
    st, insts, ets = _store()
    prov = make_evolution_provider(st, None, insts, event_times=ets, fwd_horizon=1)
    assert hasattr(prov, "leakage_ok")
    assert prov.leakage_ok(tree_definition(("leaf", "momentum", "3"))) is True   # gp_tree -> real True
    assert prov.leakage_ok(_defn("p", op="momentum", w=3)) is True               # price -> real True
    fund_d = AlphaDefinition(alpha_id="f", name="f", factor_ids=("close",),
                             params={"operator": "momentum", "window": "3", "metric": "ep"})
    assert prov.leakage_ok(fund_d) is None                                       # fundamental -> honest None
    inter_d = AlphaDefinition(alpha_id="i", name="i", factor_ids=("close",),
                              params={"family": "interaction", "combine": "product"})
    assert prov.leakage_ok(inter_d) is None                                      # interaction -> honest None


class _SpyEvaluator(AlphaEvaluator):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls = []

    def _evaluate_legacy_diagnostic(self, alpha_id, periods, **kw):
        self.calls.append(dict(kw))
        return super()._evaluate_legacy_diagnostic(alpha_id, periods, **kw)


def test_guided_loop_forwards_provider_leakage_to_the_gate():
    st, insts, ets = _store()
    prov = make_data_periods_provider(st, insts, event_times=ets, fwd_horizon=1)
    space = HypothesisSpace(factors=("close",), operators=("momentum",), windows=(3,))
    catalog = AlphaCatalog()
    spy = _SpyEvaluator(catalog, AlphaFactoryConfig())
    loop = GuidedSearchLoop(space, inner_loop=ResearchAgentLoop(catalog=catalog, evaluator=spy))
    loop.search(rounds=1, batch_size=1, periods_provider=prov, diagnostic_only=True)
    assert spy.calls                                                 # the loop evaluated at least one candidate
    assert all(c.get("leakage_ok") is True for c in spy.calls)       # provider's real verdict reached the gate
