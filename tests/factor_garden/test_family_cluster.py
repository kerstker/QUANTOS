"""F5 — family clustering: declared-family-first, union-find within family, deterministic
cluster ids, honest singleton semantics, fail-closed rulings."""
import pytest

from quantos.contracts.feature import FactorFamily, FeatureDefinition
from services.factor_garden import (
    CROWD_CONTRA_EXPRESSION,
    FactorClusterError,
    assign_factor_clusters,
    build_factor_definition,
    cluster_summary,
    family_of,
)


def _defn(name, family=FactorFamily.POSITIONING_CROWDING, family_id=...):
    kw = dict(
        name=name, family=family, expression=CROWD_CONTRA_EXPRESSION,
        economic_hypothesis="test factor", required_datasets=("d",),
        frequency="1d", universe="test",
        creator_actor="quantos-research", created_at_ms=0,
    )
    defn = build_factor_definition(**kw)
    if family_id is not ...:                            # hand-override the declared family
        defn = FeatureDefinition(**{**defn.model_dump(), "factor_family_id": family_id})
    return defn


def test_declared_family_grouping_and_family_of():
    defns = [_defn("a"), _defn("b", FactorFamily.FUNDING_CARRY)]
    out = {a.factor_id: a for a in assign_factor_clusters(defns)}
    assert out["a"].family is FactorFamily.POSITIONING_CROWDING
    assert out["b"].family is FactorFamily.FUNDING_CARRY
    assert family_of(defns, "b") is FactorFamily.FUNDING_CARRY
    with pytest.raises(FactorClusterError):
        family_of(defns, "ghost")


def test_missing_or_invalid_family_fails_closed():
    # 契约层(F1 field_validator)已拒未知字符串家族;模块层可达的非法形态只有 None
    with pytest.raises(FactorClusterError, match="never guessed"):
        assign_factor_clusters([_defn("a", family_id=None)])
    with pytest.raises(FactorClusterError):
        family_of([_defn("a", family_id=None)], "a")


def test_transitive_closure_merges_one_cluster():
    defns = [_defn("a"), _defn("b"), _defn("c")]
    out = assign_factor_clusters(defns, correlation={("a", "b"): 0.8, ("b", "c"): 0.9})
    cids = {a.factor_id: a.cluster_id for a in out}
    assert cids["a"] == cids["b"] == cids["c"] == "POSITIONING_CROWDING#a"
    assert all(a.method == "CORRELATION" for a in out)


def test_below_cut_stays_singleton():
    defns = [_defn("a"), _defn("b")]
    out = assign_factor_clusters(defns, correlation={("a", "b"): 0.49})
    assert {a.cluster_id for a in out} == {
        "POSITIONING_CROWDING#a", "POSITIONING_CROWDING#b"}
    assert all(a.method == "SINGLETON" for a in out)


def test_cross_family_never_merges_even_at_high_rho():
    defns = [_defn("a"), _defn("b", FactorFamily.FUNDING_CARRY)]
    out = assign_factor_clusters(defns, correlation={("a", "b"): 0.99})
    by_id = {a.factor_id: a for a in out}
    assert by_id["a"].cluster_id == "POSITIONING_CROWDING#a"
    assert by_id["b"].cluster_id == "FUNDING_CARRY#b"


def test_no_correlation_input_all_singletons():
    defns = [_defn("a"), _defn("b")]
    out = assign_factor_clusters(defns, correlation=None)
    assert all(a.method == "SINGLETON" for a in out)
    assert len({a.cluster_id for a in out}) == 2      # 无相关证据 = 不声称冗余


def test_declared_method_when_factor_has_no_correlation_evidence():
    defns = [_defn("a"), _defn("b"), _defn("c")]
    out = assign_factor_clusters(defns, correlation={("a", "b"): 0.9})
    by_id = {a.factor_id: a for a in out}
    assert by_id["c"].method == "DECLARED"            # 有相关图但 c 无任何边
    assert by_id["c"].cluster_id == "POSITIONING_CROWDING#c"


def test_cluster_id_deterministic_and_insertion_order_independent():
    defns1 = [_defn("a"), _defn("b"), _defn("c")]
    defns2 = [_defn("c"), _defn("a"), _defn("b")]
    corr1 = {("a", "b"): 0.8, ("b", "c"): 0.9}
    corr2 = {("c", "b"): 0.9, ("b", "a"): 0.8}        # 键序无关
    out1 = assign_factor_clusters(defns1, correlation=corr1)
    out2 = assign_factor_clusters(defns2, correlation=corr2)
    assert out1 == out2
    again = assign_factor_clusters(defns1, correlation=corr1)
    assert out1 == again


def test_negative_high_abs_rho_merges():
    defns = [_defn("long_crowd"), _defn("fade_crowd")]
    out = assign_factor_clusters(defns,
                                 correlation={("long_crowd", "fade_crowd"): -0.95})
    assert len({a.cluster_id for a in out}) == 1      # 拥挤反向变体同族(§7.3)


def test_correlation_validation_fail_closed():
    defns = [_defn("a"), _defn("b")]
    with pytest.raises(FactorClusterError):
        assign_factor_clusters(defns, correlation={("a", "ghost"): 0.5})
    with pytest.raises(FactorClusterError):
        assign_factor_clusters(defns, correlation={("a", "b"): 1.5})
    with pytest.raises(FactorClusterError):
        assign_factor_clusters(defns, correlation={("a", "b"): float("nan")})
    with pytest.raises(FactorClusterError):
        assign_factor_clusters(defns, rho_cut=1.5)
    with pytest.raises(FactorClusterError):
        assign_factor_clusters([_defn("a"), _defn("a")])    # duplicate ids


def test_cluster_summary_counts():
    defns = [_defn("a"), _defn("b"), _defn("c"), _defn("d", FactorFamily.FUNDING_CARRY)]
    out = assign_factor_clusters(defns, correlation={("a", "b"): 0.9})
    s = cluster_summary(out)
    assert s["n_factors"] == 4 and s["n_clusters"] == 3
    assert s["by_family"]["POSITIONING_CROWDING"] == {
        "factors": 3, "clusters": 2,
        "cluster_ids": ("POSITIONING_CROWDING#a", "POSITIONING_CROWDING#c"),
    }
    assert s["by_family"]["FUNDING_CARRY"]["clusters"] == 1
    assert s["by_cluster"]["POSITIONING_CROWDING#a"] == ("a", "b")
