"""F1 — Factor Garden definition builder + FactorFamily contract tests (727 §6.1/§6.4)."""
import pytest
from pydantic import ValidationError

from quantos.contracts.feature import (
    FactorFamily,
    FeatureDefinition,
    FeatureDType,
)
from services.factor_garden import (
    FactorDefinitionError,
    build_factor_definition,
    factor_content_hash,
)
from services.research.alpha_expression import alpha_id, serialize

CROWD_EXPR = ("cs_rank", ("neg", ("field", "global_ls")))
TREND_EXPR = ("cs_rank", ("ts_mean", ("field", "close"), 20))


def _build(**over):
    kw = dict(
        name="crowd_contra",
        family=FactorFamily.POSITIONING_CROWDING,
        expression=CROWD_EXPR,
        economic_hypothesis="反着做拥挤的散户多空仓位(拥挤多头未来跑输)",
        required_datasets=("oi_metrics",),
        frequency="1d",
        universe="crypto_oi_majors",
        minimum_lag=1,
        creator_actor="researcher:test",
        created_at_ms=1_000,
    )
    kw.update(over)
    return build_factor_definition(**kw)


# --- FactorFamily (727 §6.4) --------------------------------------------------

def test_factor_family_has_exactly_the_thirteen_6_4_values():
    assert {f.value for f in FactorFamily} == {
        "PRICE_TREND", "PRICE_MEAN_REVERSION", "VOLATILITY", "VOLUME", "LIQUIDITY",
        "ORDER_FLOW", "POSITIONING_CROWDING", "OPEN_INTEREST", "FUNDING_CARRY",
        "BASIS", "CROSS_VENUE", "REGIME", "DEFENSIVE_OVERLAY",
    }


# --- build: identity + canonical dedup ----------------------------------------

def test_expression_hash_is_stable_for_same_expression():
    assert _build().expression_hash == _build().expression_hash
    assert _build().expression_hash == alpha_id(CROWD_EXPR)


def test_equivalent_commutative_expressions_share_identity():
    a = _build(expression=("add", ("field", "funding"), ("field", "basis")))
    b = _build(expression=("add", ("field", "basis"), ("field", "funding")))
    assert a.expression_hash == b.expression_hash


def test_build_lands_all_6_1_fields_on_the_contract():
    d = _build(
        normalization="cs_rank", neutralization="", winsorization="mad_3",
        missing_policy="drop_period", expected_turnover=0.15,
        known_failure_regimes=("2021-05_early_oi_sparse",),
        creator_model="gpt-test", prompt_hash="ph_1",
    )
    assert d.feature_id == "crowd_contra" == d.name      # stable human identity
    assert d.dtype is FeatureDType.FLOAT                 # contract convention
    assert d.formula == serialize(CROWD_EXPR)
    assert d.inputs == ("oi_metrics",)                   # upstream data ids
    assert d.factor_family_id == "POSITIONING_CROWDING"
    assert d.required_datasets == ("oi_metrics",)
    assert d.minimum_lag == 1
    assert d.frequency == "1d" and d.universe == "crypto_oi_majors"
    assert d.normalization == "cs_rank" and d.winsorization == "mad_3"
    assert d.missing_policy == "drop_period" and d.expected_turnover == 0.15
    assert d.known_failure_regimes == ("2021-05_early_oi_sparse",)
    assert d.creator_actor == "researcher:test" and d.creator_model == "gpt-test"
    assert d.prompt_hash == "ph_1" and d.created_at_ms == 1_000


# --- fail-closed garden invariants (727 §6.1/§8.1) -----------------------------

def test_empty_economic_hypothesis_raises():
    with pytest.raises(FactorDefinitionError):
        _build(economic_hypothesis="   ")


def test_empty_required_datasets_raises():
    with pytest.raises(FactorDefinitionError):
        _build(required_datasets=())


def test_non_factorfamily_raises():
    with pytest.raises(FactorDefinitionError):
        _build(family="POSITIONING_CROWDING")   # bare string is not the enum


def test_non_tuple_expression_raises():
    with pytest.raises(FactorDefinitionError):
        _build(expression="cs_rank(neg(global_ls))")   # string-AST form is rejected


def test_unknown_operator_raises():
    with pytest.raises(FactorDefinitionError):
        _build(expression=("not_an_op", ("field", "close")))


def test_wall_clock_shape_created_at_ms_raises():
    with pytest.raises(FactorDefinitionError):
        _build(created_at_ms=-1)


# --- backward compatibility (pre-F1 constructor shape) -------------------------

def test_bare_feature_definition_still_constructs():
    d = FeatureDefinition(
        feature_id="mom_20", name="20d momentum", dtype=FeatureDType.FLOAT,
        formula="ts_mean(close,20)", inputs=("close",),
    )
    assert d.factor_family_id is None
    assert d.expression_hash == "" and d.economic_hypothesis == ""
    assert d.required_datasets == () and d.minimum_lag is None
    assert d.created_at_ms == 0 and d.known_failure_regimes == ()


def test_unknown_family_value_on_contract_raises():
    with pytest.raises(ValidationError):
        FeatureDefinition(feature_id="f", name="f", factor_family_id="NOT_A_FAMILY")


# --- content hash ---------------------------------------------------------------

def test_content_hash_changes_with_expression():
    assert factor_content_hash(_build()) != factor_content_hash(_build(expression=TREND_EXPR))


def test_content_hash_ignores_created_at_ms():
    assert factor_content_hash(_build(created_at_ms=1)) == \
        factor_content_hash(_build(created_at_ms=999_999))
