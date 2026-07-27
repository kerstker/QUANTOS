"""F2 — canonical factor convergence: the moved expressions/helpers stay bit-identical,
CANONICAL_FACTORS pass the F1 garden invariants, and register_canonical_factors is
idempotent / fail-closed on conflict."""
import pytest

from quantos.contracts.feature import FactorFamily
from services.factor_garden import (
    CANONICAL_FACTOR_VERSION,
    CANONICAL_FACTORS,
    CARRY_COMPOSITE_EXPRESSION,
    CARRY_QUANTILE_LIVE,
    CARRY_QUANTILE_OFFLINE,
    CROWD_CONTRA_EXPRESSION,
    FUNDING_FIELD,
    TOP_CONTRA_EXPRESSION,
    TREND_COMPOSITE_EXPRESSION,
    FactorDefinitionError,
    FactorGardenRegistry,
    legs,
    register_canonical_factors,
)
from services.factor_garden.definitions import _validate_expression
from services.research import alpha_expression as ax


def test_canonical_factors_non_empty_and_garden_conformant():
    assert CANONICAL_FACTORS
    for defn in CANONICAL_FACTORS:
        assert defn.economic_hypothesis.strip()          # 727 §8.1: no factor without a story
        assert defn.required_datasets                    # §6.1 data dependencies
        assert defn.factor_family_id in {f.value for f in FactorFamily}
        assert defn.expression_hash                      # DSL identity carried
        assert defn.creator_actor == "quantos-research"
        assert defn.created_at_ms == 0                   # constants — no wall clock


def test_expression_hash_identity_preserved_through_the_move():
    """Each canonical definition's expression_hash equals alpha_id of the moved tuple."""
    moved = {
        "carry_composite": CARRY_COMPOSITE_EXPRESSION,
        "crowd_contra": CROWD_CONTRA_EXPRESSION,
        "top_contra": TOP_CONTRA_EXPRESSION,
    }
    assert {d.name for d in CANONICAL_FACTORS} == set(moved)
    for defn in CANONICAL_FACTORS:
        canonical_expr = _validate_expression(moved[defn.name])
        assert defn.expression_hash == ax.alpha_id(canonical_expr)
        assert defn.formula == ax.serialize(canonical_expr)


def test_dedup_proof_carry_single_object_across_both_tools_modules():
    """The CARRY/COMPOSITE verbatim duplicate is killed: both tools modules now resolve
    to the SAME canonical object (identity, not just equality) with one alpha_id."""
    from tools.crypto_carry_strategy import COMPOSITE, FUND
    from tools.crypto_oi_research import CARRY

    assert CARRY is CARRY_COMPOSITE_EXPRESSION
    assert COMPOSITE is CARRY_COMPOSITE_EXPRESSION
    assert ax.serialize(CARRY) == ax.serialize(COMPOSITE)
    assert ax.alpha_id(CARRY) == ax.alpha_id(COMPOSITE)
    assert FUND is FUNDING_FIELD == ("field", "funding")


def test_dedup_proof_crowd_top_and_trend_resolve_to_canonical():
    from tools.crypto_oi_verify import CROWD_CONTRA, TOP_CONTRA
    from tools.crypto_daily_composite import COMPOSITE as TREND_A
    from tools.dry_run_trend_multi import _COMPOSITE as TREND_B

    assert CROWD_CONTRA is CROWD_CONTRA_EXPRESSION
    assert TOP_CONTRA is TOP_CONTRA_EXPRESSION
    assert TREND_A is TREND_COMPOSITE_EXPRESSION
    assert TREND_B is TREND_COMPOSITE_EXPRESSION
    # the trend composite keeps its PRODUCTION pair-tuple form (not the tuple DSL)
    assert TREND_COMPOSITE_EXPRESSION == (
        ("dist_from_high", 7), ("dist_from_high", 14),
        ("dist_from_high", 30), ("momentum", 7),
    )


def test_quantile_divergence_recorded_not_unified():
    from tools.crypto_carry_strategy import QUANTILE as offline_q
    from tools.crypto_oi_research import QUANTILE as live_q

    assert offline_q == CARRY_QUANTILE_OFFLINE == 0.2
    assert live_q == CARRY_QUANTILE_LIVE == 0.3


def test_legs_helper_semantics_on_synthetic_rows():
    # < 4 non-None signals -> empty book
    assert legs([1.0, None, 2.0, None], 0.5) == {}
    # 8 valid, q=0.25 -> k = round(2.0) = 2 longs / 2 shorts, index-keyed, dollar-neutral
    w = legs([0.1, 0.9, None, 0.5, 0.3, 0.7, 0.2, None, 0.4, 0.6], 0.25)
    assert w == {5: 0.25, 1: 0.25, 0: -0.25, 6: -0.25}
    assert sum(w.values()) == pytest.approx(0.0)
    # longs written before shorts: a name in both tails nets (only reachable when 2k > n)
    w2 = legs([0.1, 0.2, 0.3, 0.4], 0.6)   # k = round(2.4) = 2 -> no overlap
    assert w2 == {3: 0.25, 2: 0.25, 0: -0.25, 1: -0.25}
    # k floor: tiny q still takes exactly one name per side
    w3 = legs([0.4, 0.1, 0.3, 0.2, 0.5], 0.01)
    assert w3 == {4: 0.5, 1: -0.5}


def test_legs_tools_aliases_are_the_canonical_helper():
    from tools.crypto_carry_strategy import _legs as carry_legs
    from tools.crypto_oi_research import _legs as oi_legs

    assert carry_legs is legs
    assert oi_legs is legs


def test_register_canonical_factors_fresh_store_registers_all(tmp_path):
    with FactorGardenRegistry(str(tmp_path / "g.db"), create=True) as reg:
        out = register_canonical_factors(reg, recorded_at_ms=100)
        assert out == {d.name: "registered" for d in CANONICAL_FACTORS}
        for defn in CANONICAL_FACTORS:
            item = reg.get(defn.name, CANONICAL_FACTOR_VERSION)
            assert item.definition == defn
            assert item.owner == "quantos-research"


def test_register_canonical_factors_idempotent(tmp_path):
    path = str(tmp_path / "g.db")
    with FactorGardenRegistry(path, create=True) as reg:
        register_canonical_factors(reg, recorded_at_ms=100)
    with FactorGardenRegistry(path) as reg:          # reopen, create=False
        out = register_canonical_factors(reg, recorded_at_ms=200)
        assert out == {d.name: "already_registered" for d in CANONICAL_FACTORS}
        for defn in CANONICAL_FACTORS:
            assert reg.versions(defn.name) == [CANONICAL_FACTOR_VERSION]


def test_register_canonical_factors_conflict_raises(tmp_path):
    from quantos.contracts.feature import FeatureDefinition, FeatureRegistryItem, FeatureStatus

    victim = CANONICAL_FACTORS[0]
    tampered = FeatureDefinition(
        **{**victim.model_dump(), "economic_hypothesis": "a DIFFERENT story"}
    )
    with FactorGardenRegistry(str(tmp_path / "g.db"), create=True) as reg:
        reg._store.register(                          # plant a conflicting same-id+version row
            FeatureRegistryItem(definition=tampered, version=CANONICAL_FACTOR_VERSION,
                                owner="someone-else", status=FeatureStatus.DRAFT),
            recorded_at_ms=1,
        )
        with pytest.raises(FactorDefinitionError, match="conflicts"):
            register_canonical_factors(reg, recorded_at_ms=100)


def test_register_canonical_factors_validates_inputs(tmp_path):
    with FactorGardenRegistry(str(tmp_path / "g.db"), create=True) as reg:
        with pytest.raises(FactorDefinitionError):
            register_canonical_factors(reg, recorded_at_ms=-1)
        with pytest.raises(FactorDefinitionError):
            register_canonical_factors(reg, recorded_at_ms=True)
        with pytest.raises(TypeError):
            register_canonical_factors(object(), recorded_at_ms=1)
