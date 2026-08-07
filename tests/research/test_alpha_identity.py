from dataclasses import replace
import math

import pytest

from services.research.alpha_identity import (
    MODEL_REGISTRY,
    AlphaDefinition,
    alpha_id,
    canonical_alpha_json,
)


def definition(**overrides):
    value = dict(
        definition=("ts_mean", ("field", "close"), 5),
        feature_contract={"features": ["close"], "strict": True},
        data_schema={"dataset": "bars", "version": 1},
        universe_policy={"name": "top_liquid", "min_count": 10},
        horizon="1h",
        cost_model={"bps": 10.0},
        validation_policy={"method": "purged_walk_forward", "folds": 5},
        model="ridge",
        model_version="1",
    )
    value.update(overrides)
    return AlphaDefinition(**value)


def test_mapping_order_is_canonical_and_identity_is_full_sha256():
    a = definition(feature_contract={"strict": True, "features": ["close"]})
    b = definition(feature_contract={"features": ["close"], "strict": True})
    assert canonical_alpha_json(a) == canonical_alpha_json(b)
    assert alpha_id(a) == alpha_id(b)
    assert len(alpha_id(a)) == 64 and alpha_id(a) == alpha_id(a).lower()
    assert canonical_alpha_json(a).startswith('{')
    assert ': ' not in canonical_alpha_json(a)


@pytest.mark.parametrize("field", ["horizon", "feature_contract", "data_schema", "universe_policy",
                                    "cost_model", "validation_policy", "model", "model_version"])
def test_each_identity_component_changes_id(field):
    a = definition()
    changed = {"horizon": "4h", "feature_contract": {"features": ["volume"]},
               "data_schema": {"dataset": "bars", "version": 2},
               "universe_policy": {"name": "all"}, "cost_model": {"bps": 11.0},
               "validation_policy": {"method": "holdout"}, "model": "gbm",
               "model_version": "1.0"}[field]
    assert alpha_id(replace(a, **{field: changed})) != alpha_id(a)


@pytest.mark.parametrize("bad", ["15m", "1d", 1, 4, True, False, None])
def test_horizon_accepts_only_registered_hourly_labels(bad):
    with pytest.raises((ValueError, TypeError)):
        definition(horizon=bad)


def test_replace_can_change_hourly_horizon_and_identity():
    a = definition(horizon="1h")
    b = replace(a, horizon="4h")
    assert b.horizon == "4h"
    assert alpha_id(b) != alpha_id(a)


def test_model_registry_rejects_downstream_mutation():
    with pytest.raises(TypeError):
        MODEL_REGISTRY["new_model"] = frozenset({"1"})
    with pytest.raises(AttributeError):
        MODEL_REGISTRY["ridge"].add("2")
    assert alpha_id(definition(definition=("signed_power", ("field", "close"), 2.0)))
    with pytest.raises(ValueError):
        definition(definition=("not_registered", ("field", "close")))
    with pytest.raises(ValueError):
        definition(model="future_model")


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True, False])
def test_numeric_positions_reject_non_finite_and_bool(bad):
    with pytest.raises((ValueError, TypeError)):
        definition(horizon=bad)


@pytest.mark.parametrize("bad", [lambda: 1, b"x", {"x"}, object(), {1: "bad"}])
def test_recursive_json_values_fail_closed(bad):
    with pytest.raises((ValueError, TypeError)):
        definition(feature_contract={"nested": [bad]})


def test_frozen_inputs_cannot_mutate_identity():
    source = {"features": ["close"]}
    a = definition(feature_contract=source)
    before = alpha_id(a)
    source["features"].append("volume")
    assert alpha_id(a) == before
    with pytest.raises((AttributeError, TypeError)):
        a.horizon = 2


def test_expression_field_name_is_not_operator_and_deep_tree_is_checked():
    assert alpha_id(definition(definition=("field", "ts_mean")))
    with pytest.raises(ValueError):
        definition(definition=("add", ("field", "close"), ("bad", ("field", "volume"))))
