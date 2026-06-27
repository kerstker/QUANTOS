"""The rich 'Alpha Asset' (~50 fields) turns a bare alpha into a managed research asset: provenance
(owner/generator/version/dataset), intent (hypothesis/economic_rationale/tags), dependencies, the
embedded scientific evidence (the frozen AlphaEvaluation + Phase-2 CPCV distribution), portfolio
context (cluster/exposures/capacity/marginal), lineage (parents/mutation/perf-delta), and lifecycle
(status/rejection_reasons/event refs). It COMPOSES the existing AlphaDefinition/AlphaEvaluation rather
than duplicating them (adapt, don't rewrite). Frozen + hashable for audit."""
import pytest
from pydantic import ValidationError

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.alpha_asset import AlphaAsset, AlphaLineage
from quantos.contracts.research import AlphaState, ValidationVerdict


def _evaluation(aid="a1", verdict=ValidationVerdict.PASS, dsr=0.8):
    return AlphaEvaluation(alpha_id=aid, verdict=verdict, deflated_sharpe=dsr, net_sharpe=1.2,
                           icir=0.5, turnover=0.3, marginal_ir=0.2, reproducibility_hash="repro1")


def test_minimal_asset_has_sane_defaults():
    a = AlphaAsset(alpha_id="a1")
    assert a.version == 1
    assert a.status == AlphaState.DRAFT
    assert a.lineage.parent_ids == () and a.lineage.mutation_op == ""
    assert a.evaluation is None and a.tags == () and a.exposures == {}


def test_asset_is_frozen():
    a = AlphaAsset(alpha_id="a1")
    with pytest.raises(ValidationError):
        a.version = 2


def test_rich_field_coverage():
    # the asset must carry the institutional metadata the audit said was missing (~50 effective fields:
    # its own management layer + the embedded evaluation)
    fields = set(AlphaAsset.model_fields)
    required = {"alpha_id", "version", "owner", "generator", "created_ts", "dataset_version",
                "expression", "hypothesis", "economic_rationale", "tags", "universe",
                "data_dependencies", "operator_dependencies", "evaluation", "cpcv",
                "regime_stability", "cluster_id", "exposures", "capacity_notional", "post_cost_ir",
                "marginal_contribution", "lineage", "status", "rejection_reasons", "lifecycle_event_ids"}
    assert required <= fields
    assert len(fields) + len(AlphaEvaluation.model_fields) >= 50    # ~50-field schema


def test_from_definition_composes_existing_entities():
    d = AlphaDefinition(alpha_id="a1", name="carry", factor_ids=("funding",), universe="perp")
    ev = _evaluation("a1")
    a = AlphaAsset.from_definition(d, evaluation=ev, generator="cs_genome", owner="research-agent",
                                   hypothesis="perp funding carry", expression="(cs_rank (field funding))",
                                   data_dependencies=("funding",), tags=("carry", "crypto"))
    assert a.alpha_id == "a1" and a.name == "carry" and a.universe == "perp"
    assert a.evaluation is ev and a.generator == "cs_genome"
    assert a.verdict == ValidationVerdict.PASS and a.deflated_sharpe == 0.8 and a.net_sharpe == 1.2
    assert "carry" in a.tags


def test_lineage_records_parentage():
    lin = AlphaLineage(parent_ids=("p1", "p2"), mutation_op="crossover", perf_delta=0.05)
    a = AlphaAsset(alpha_id="child", version=2, lineage=lin, generator="evolutionary")
    assert a.lineage.parent_ids == ("p1", "p2") and a.lineage.mutation_op == "crossover"
    assert a.lineage.perf_delta == 0.05


def test_asset_hash_is_deterministic_and_version_sensitive():
    d = AlphaDefinition(alpha_id="a1", name="x")
    a1 = AlphaAsset.from_definition(d, evaluation=_evaluation("a1"), expression="(field close)")
    a2 = AlphaAsset.from_definition(d, evaluation=_evaluation("a1"), expression="(field close)")
    assert a1.asset_hash == a2.asset_hash                       # deterministic
    a3 = a1.model_copy(update={"version": 2})
    assert a3.asset_hash != a1.asset_hash                       # identity (version) changes the hash
    a4 = a1.model_copy(update={"expression": "(field volume)"})
    assert a4.asset_hash != a1.asset_hash                       # expression changes the hash


def test_derived_props_fail_safe_without_evaluation():
    a = AlphaAsset(alpha_id="a1")
    assert a.verdict is None and a.deflated_sharpe is None and a.net_sharpe is None
    assert a.is_promotable is False
