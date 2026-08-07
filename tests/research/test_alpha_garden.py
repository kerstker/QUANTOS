from __future__ import annotations

import random

import pytest

from services.research.alpha_identity import AlphaDefinition, alpha_id
from services.research.alpha_family import AlphaCandidate
from services.research.alpha_garden import (
    CandidateBatch,
    GenerationBudget,
    GeneratorEntry,
    GeneratorRegistry,
    generate_batch,
)


def definition(window: int = 4, *, model: str = "ridge") -> AlphaDefinition:
    return AlphaDefinition(
        definition=("ts_mean", ("field", "close"), window),
        feature_contract={"fields": ["close"]},
        data_schema={"name": "hourly"},
        universe_policy={"name": "test"},
        horizon="1h",
        cost_model={"bps": 5},
        validation_policy={"name": "purged"},
        model=model,
        model_version="1",
    )


def entry(name, family, values, *, max_share=1.0):
    def generate(context, rng):
        return iter(values)

    return GeneratorEntry(name=name, version="1", family=family, generate=generate, max_share=max_share)


def test_same_seed_has_same_order_and_audit():
    registry = GeneratorRegistry((entry("b", "b", [definition(2), definition(3)]), entry("a", "a", [definition(4)])))
    budget = GenerationBudget(max_candidates=10, max_family_candidates=10, seed=17)
    left = generate_batch(registry, budget, {})
    right = generate_batch(registry, budget, {})
    assert [c.alpha_id for c in left.candidates] == [c.alpha_id for c in right.candidates]
    assert left.audit == right.audit


def test_total_family_and_entry_caps_are_enforced():
    values = [definition(i) for i in range(1, 20)]
    registry = GeneratorRegistry((entry("a", "same", values, max_share=0.3), entry("b", "other", values)))
    batch = generate_batch(registry, GenerationBudget(10, 3, 1), {})
    assert len(batch.candidates) <= 10
    assert max(batch.family_counts.values()) <= 3
    assert batch.generator_counts["a"] <= 3
    assert batch.generator_counts["b"] <= 10
    assert any(item.reason == "family_cap" for item in batch.rejections)


def test_zero_budget_does_not_invoke_generators():
    called = False
    def generate(context, rng):
        nonlocal called
        called = True
        raise AssertionError("must not run")
    registry = GeneratorRegistry((GeneratorEntry("x", "1", "x", generate, 1.0),))
    batch = generate_batch(registry, GenerationBudget(0, 0, 2), {})
    assert batch.candidates == ()
    assert not called


def test_invalid_generated_definition_is_rejected_and_audited():
    registry = GeneratorRegistry((entry("bad", "bad", [("not", "an", "alpha")]),))
    batch = generate_batch(registry, GenerationBudget(2, 2, 1), {})
    assert batch.candidates == ()
    assert batch.rejections[0].reason == "invalid_alpha_definition"


def test_duplicate_alpha_ids_keep_first_in_deterministic_order():
    registry = GeneratorRegistry((entry("a", "a", [definition(4)]), entry("b", "b", [definition(4)])))
    batch = generate_batch(registry, GenerationBudget(4, 4, 1), {})
    assert len(batch.candidates) == 1
    assert batch.rejections[0].reason == "duplicate_alpha_id"


@pytest.mark.slow
def test_10000_candidate_smoke_is_bounded_and_deterministic():
    def generate(context, rng):
        for i in range(10000):
            yield AlphaDefinition(
                definition=("signed_power", ("field", "close"), i + 1),
                feature_contract={"fields": ["close"]},
                data_schema={"name": "hourly"},
                universe_policy={"name": "test"},
                horizon="1h",
                cost_model={"bps": 5},
                validation_policy={"name": "purged"},
                model="ridge",
                model_version="1",
            )
    registry = GeneratorRegistry((GeneratorEntry("smoke", "1", "smoke", generate, 1.0),))
    budget = GenerationBudget(10000, 10000, 123)
    batch = generate_batch(registry, budget, {})
    assert len(batch.candidates) <= 10000
    assert len({c.alpha_id for c in batch.candidates}) == len(batch.candidates)
    assert batch.generator_counts["smoke"] == len(batch.candidates)
