"""The Alpha Zoo is the query surface the audit found missing: search/filter the registry's latest
assets by name/expression substring, universe, generator, status, cluster, tag, and metric floors
(DSR / net Sharpe / promotable). Thin, deterministic, read-only over the registry."""
from quantos.contracts.alpha import AlphaEvaluation
from quantos.contracts.alpha_asset import AlphaAsset
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.registry import AlphaAssetRegistry
from services.alpha.zoo import AlphaZoo


def _ev(aid, dsr, ns, verdict=ValidationVerdict.PASS):
    return AlphaEvaluation(alpha_id=aid, verdict=verdict, deflated_sharpe=dsr, net_sharpe=ns)


def _reg():
    r = AlphaAssetRegistry()
    r.add(AlphaAsset(alpha_id="carry", name="funding carry", expression="(cs_rank (field funding))",
                     universe="perp", generator="cs_genome", status=AlphaState.VALIDATED,
                     tags=("carry", "crypto"), cluster_id="cl_0", evaluation=_ev("carry", 0.82, 2.0)), ts=1)
    r.add(AlphaAsset(alpha_id="mom", name="price momentum", expression="(cs_rank (ts_delta (field close) 5))",
                     universe="spot", generator="gp", status=AlphaState.DRAFT,
                     tags=("price",), cluster_id="cl_1", evaluation=_ev("mom", 0.40, 0.6,
                                                                        ValidationVerdict.WARN)), ts=2)
    r.add(AlphaAsset(alpha_id="vol", name="volume thrust", expression="(cs_zscore (field volume))",
                     universe="perp", generator="cs_genome", status=AlphaState.VALIDATED,
                     tags=("volume",), cluster_id="cl_0", evaluation=_ev("vol", 0.65, 1.1)), ts=3)
    return r


def test_filter_by_generator_universe_status():
    z = AlphaZoo(_reg())
    assert {a.alpha_id for a in z.search(generator="cs_genome")} == {"carry", "vol"}
    assert {a.alpha_id for a in z.search(universe="perp")} == {"carry", "vol"}
    assert {a.alpha_id for a in z.search(status=AlphaState.DRAFT)} == {"mom"}


def test_substring_search_name_and_expression():
    z = AlphaZoo(_reg())
    assert {a.alpha_id for a in z.search(name_contains="carry")} == {"carry"}
    assert {a.alpha_id for a in z.search(expression_contains="field funding")} == {"carry"}
    assert {a.alpha_id for a in z.search(name_contains="o")} == {"mom", "vol"}   # momentum, volume


def test_tag_and_cluster_filters():
    z = AlphaZoo(_reg())
    assert {a.alpha_id for a in z.search(tag="crypto")} == {"carry"}
    assert {a.alpha_id for a in z.by_cluster("cl_0")} == {"carry", "vol"}


def test_metric_floors_and_promotable():
    z = AlphaZoo(_reg())
    assert {a.alpha_id for a in z.search(min_dsr=0.6)} == {"carry", "vol"}
    assert {a.alpha_id for a in z.search(min_net_sharpe=1.5)} == {"carry"}
    assert {a.alpha_id for a in z.search(promotable_only=True)} == {"carry", "vol"}  # PASS verdicts


def test_combined_filters_are_anded_and_sorted():
    z = AlphaZoo(_reg())
    res = z.search(generator="cs_genome", universe="perp", min_dsr=0.7)
    assert [a.alpha_id for a in res] == ["carry"]               # only carry meets all three
    # results are deterministically ordered by alpha_id
    assert [a.alpha_id for a in z.search(universe="perp")] == ["carry", "vol"]


def test_top_by_metric():
    z = AlphaZoo(_reg())
    top = z.top_by("deflated_sharpe", n=2)
    assert [a.alpha_id for a in top] == ["carry", "vol"]        # 0.82, 0.65 descending


def test_empty_and_no_match():
    assert AlphaZoo(AlphaAssetRegistry()).search() == ()
    assert AlphaZoo(_reg()).search(generator="nope") == ()
