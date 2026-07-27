"""F4 — dependency graph: build from definitions, propagation, dangling refs, immutability."""
import pytest

from services.factor_garden import (
    CANONICAL_FACTORS,
    CROWD_CONTRA_EXPRESSION,
    DependencyGraph,
    build_factor_definition,
)
from quantos.contracts.feature import FactorFamily, FeatureDefinition


def _defn(name, datasets):
    return build_factor_definition(
        name=name, family=FactorFamily.PRICE_TREND,
        expression=CROWD_CONTRA_EXPRESSION,
        economic_hypothesis="test factor",
        required_datasets=datasets, frequency="1d", universe="test",
        creator_actor="quantos-research", created_at_ms=0,
    )


def _graph():
    defns = (
        _defn("f_carry", ("crypto_funding", "crypto_funding_rates")),
        _defn("f_crowd", ("crypto_funding", "crypto_oi_metrics")),
    )
    links = {"a_combo": ("f_carry", "f_crowd"), "a_carry_only": ("f_carry",)}
    return DependencyGraph.from_definitions(defns, alpha_links=links)


def test_from_definitions_and_reads():
    g = _graph()
    assert g.factor_ids == ("f_carry", "f_crowd")
    assert g.datasets_for_factor("f_carry") == ("crypto_funding", "crypto_funding_rates")
    assert g.factors_for_dataset("crypto_funding") == ("f_carry", "f_crowd")
    assert g.factors_for_dataset("crypto_oi_metrics") == ("f_crowd",)
    assert g.factors_for_dataset("no_such_dataset") == ()
    assert g.datasets_for_factor("no_such_factor") == ()
    assert g.topo_factors() == ("f_carry", "f_crowd")


def test_dependents_of_dataset_propagates_to_alphas():
    g = _graph()
    factors, alphas = g.dependents_of_dataset("crypto_oi_metrics")
    assert factors == ("f_crowd",)
    assert alphas == ("a_combo",)                       # via f_crowd only
    factors, alphas = g.dependents_of_dataset("crypto_funding")
    assert factors == ("f_carry", "f_crowd")
    assert alphas == ("a_carry_only", "a_combo")


def test_validate_dangling_alpha_factor():
    g = DependencyGraph({"f1": ("d1",)}, alpha_factors={"a1": ("f1", "ghost_factor")})
    issues = g.validate()
    assert issues["alphas_to_missing_factors"] == (("a1", "ghost_factor"),)
    assert "factors_to_missing_datasets" not in issues   # no known_datasets declared -> skipped


def test_validate_dangling_dataset_only_when_declared():
    g = DependencyGraph({"f1": ("d1", "d2")}, known_datasets=("d1",))
    assert g.validate()["factors_to_missing_datasets"] == (("f1", "d2"),)
    ok = DependencyGraph({"f1": ("d1",)}, alpha_factors={"a": ("f1",)},
                         known_datasets=("d1",))
    assert ok.validate() == {}


def test_construction_validation_and_immutability():
    with pytest.raises(ValueError):
        DependencyGraph({"": ("d1",)})
    with pytest.raises(ValueError):
        DependencyGraph({"f1": ("d1",)}, alpha_factors={"a1": ("  ",)})
    with pytest.raises(TypeError):
        DependencyGraph.from_definitions(["not-a-definition"])
    g = _graph()
    with pytest.raises(TypeError):
        g._factor_datasets["evil"] = ("d",)              # MappingProxyType is immutable


def test_from_definitions_with_canonical_factors():
    g = DependencyGraph.from_definitions(CANONICAL_FACTORS)
    assert g.factors_for_dataset("crypto_oi_metrics") == ("crowd_contra", "top_contra")
    assert g.datasets_for_factor("carry_composite") == ("crypto_funding", "crypto_funding_rates")
    assert g.validate() == {}
