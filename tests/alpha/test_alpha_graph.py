"""The Alpha Correlation Graph is an EXPLICIT nodes/edges object (not just a pairwise dict): it finds
redundancy clusters (connected components above a similarity threshold), near-duplicate pairs, and a
representative per cluster — the de-duplication surface the optimizer and zoo build on. Deterministic."""
from quantos.contracts.alpha import AlphaCluster
from services.alpha.alpha_graph import AlphaGraph, build_graph, correlation_graph, expression_graph
from services.alpha import similarity as sim


def test_correlation_graph_edges_and_neighbors():
    # a and b are perfectly correlated; c is unrelated
    base = [0.01, -0.02, 0.03, -0.01, 0.02, -0.015]
    rbi = {"a": base, "b": [2 * x for x in base], "c": [0.01, 0.01, -0.05, 0.02, -0.03, 0.04]}
    g = correlation_graph(rbi)
    assert set(g.nodes) == {"a", "b", "c"}
    assert "b" in g.neighbors("a", threshold=0.9)
    assert "c" not in g.neighbors("a", threshold=0.9)
    assert g.weight("a", "b") == g.weight("b", "a")            # symmetric


def test_clusters_group_redundant_alphas():
    base = [0.01, -0.02, 0.03, -0.01, 0.02, -0.015]
    rbi = {"a": base, "b": [2 * x for x in base], "c": [0.01, 0.01, -0.05, 0.02, -0.03, 0.04]}
    clusters = g_clusters = correlation_graph(rbi).clusters(threshold=0.9)
    sets = sorted((tuple(sorted(c)) for c in clusters))
    assert ("a", "b") in sets and ("c",) in sets


def test_near_duplicate_pairs_sorted():
    base = [0.01, -0.02, 0.03, -0.01, 0.02, -0.015]
    rbi = {"a": base, "b": [2 * x for x in base], "c": [-x for x in base]}
    pairs = correlation_graph(rbi).near_duplicate_pairs(threshold=0.95)
    # all three are |corr|=1 with each other -> 3 pairs, deterministically ordered
    assert [(p[0], p[1]) for p in pairs] == [("a", "b"), ("a", "c"), ("b", "c")]


def test_representatives_pick_best_by_key():
    base = [0.01, -0.02, 0.03, -0.01, 0.02, -0.015]
    rbi = {"a": base, "b": [2 * x for x in base], "c": [0.01, 0.01, -0.05, 0.02, -0.03, 0.04]}
    g = correlation_graph(rbi)
    score = {"a": 0.5, "b": 0.9, "c": 0.1}
    clusters = g.clusters_as_contracts(threshold=0.9, key=lambda aid: score[aid])
    assert all(isinstance(c, AlphaCluster) for c in clusters)
    ab = next(c for c in clusters if set(c.member_ids) == {"a", "b"})
    assert ab.representative_id == "b"                          # higher score wins
    assert ab.intra_corr is not None


def test_expression_graph_clusters_structural_dupes():
    exprs = {
        "x": ("cs_rank", ("ts_mean", ("field", "close"), 5)),
        "y": ("cs_rank", ("ts_mean", ("field", "close"), 5)),   # identical structure to x
        "z": ("cs_zscore", ("field", "volume")),
    }
    clusters = expression_graph(exprs).clusters(threshold=0.99)
    sets = sorted(tuple(sorted(c)) for c in clusters)
    assert ("x", "y") in sets and ("z",) in sets


def test_build_graph_generic_and_empty():
    g = build_graph({}, sim.return_similarity)
    assert g.nodes == () and g.clusters(0.5) == []
    one = build_graph({"a": [0.1, 0.2, 0.3]}, sim.return_similarity)
    assert one.clusters(0.5) == [{"a"}]                         # singleton is its own cluster


def test_no_rng_or_wallclock_import():
    import inspect
    import services.alpha.alpha_graph as ag
    src = inspect.getsource(ag)
    assert "import random" not in src and "import time" not in src and "Random" not in src
