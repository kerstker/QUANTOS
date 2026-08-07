from __future__ import annotations

import random

import pytest

from services.research.alpha_identity import AlphaDefinition, alpha_id
from services.research.alpha_family import AlphaCandidate, family_id
from services.research.alpha_garden import (
    CandidateBatch,
    GenerationBudget,
    GeneratorEntry,
    GeneratorRegistry,
    adapt_gp_output,
    make_identity_adapter,
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

    return GeneratorEntry(name=name, version="1", family=family_id(values[0]) if isinstance(values[0], AlphaDefinition) else "0" * 64, generate=generate, max_share=max_share)


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
    assert batch.generator_counts["a@1"] <= 3
    assert batch.generator_counts["b@1"] <= 10
    assert any(item.reason == "family_cap" for item in batch.rejections)


def test_zero_budget_does_not_invoke_generators():
    called = False
    def generate(context, rng):
        nonlocal called
        called = True
        raise AssertionError("must not run")
    registry = GeneratorRegistry((GeneratorEntry("x", "1", "0" * 64, generate, 1.0),))
    batch = generate_batch(registry, GenerationBudget(0, 0, 2), {})
    assert batch.candidates == ()
    assert not called


def test_invalid_generated_definition_is_rejected_and_audited():
    registry = GeneratorRegistry((entry("bad", "bad", [("not", "an", "alpha")]),))
    batch = generate_batch(registry, GenerationBudget(2, 2, 1), {})
    assert batch.candidates == ()
    assert batch.rejections[0].reason == "adapter_error"


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
    registry = GeneratorRegistry((GeneratorEntry("smoke", "1", family_id(AlphaDefinition(definition=("signed_power", ("field", "close"), 1), feature_contract={"fields": ["close"]}, data_schema={"name": "hourly"}, universe_policy={"name": "test"}, horizon="1h", cost_model={"bps": 5}, validation_policy={"name": "purged"}, model="ridge", model_version="1")), generate, 1.0),))
    budget = GenerationBudget(10000, 10000, 123)
    batch = generate_batch(registry, budget, {})
    assert len(batch.candidates) <= 10000
    assert len({c.alpha_id for c in batch.candidates}) == len(batch.candidates)
    assert batch.generator_counts["smoke@1"] == len(batch.candidates)


def test_iterator_error_after_yield_is_audited_without_escape():
    def generate(context, rng):
        yield definition(4)
        raise RuntimeError("broken body")
    e = entry("raises", "ignored", [definition(4)])
    e = GeneratorEntry(e.name, e.version, family_id(definition(4)), generate)
    batch = generate_batch(GeneratorRegistry((e,)), GenerationBudget(4, 4, 1), {})
    assert len(batch.candidates) == 1
    assert batch.audit["generator_error"] == 1
    assert all(r.generator == "raises@1" for r in batch.rejections)


def test_infinite_duplicate_generator_hits_attempt_cap():
    def generate(context, rng):
        while True:
            yield definition(4)
    e = GeneratorEntry("infinite", "1", family_id(definition(4)), generate)
    batch = generate_batch(GeneratorRegistry((e,)), GenerationBudget(3, 3, 1), {})
    assert len(batch.rejections) == 3
    assert batch.audit["generator_attempt_cap"] == 1


def test_family_mismatch_is_rejected():
    e = GeneratorEntry("wrong", "1", family_id(definition(4, model="xgb")), lambda c, r: (definition(4),))
    batch = generate_batch(GeneratorRegistry((e,)), GenerationBudget(2, 2, 1), {})
    assert batch.candidates == ()
    assert batch.rejections[0].reason == "family_mismatch"
    assert batch.rejections[0].family_id == family_id(definition(4))


def test_adapter_is_invoked_and_adapter_errors_are_audited():
    calls = []
    def adapter(raw):
        calls.append(raw)
        if raw == "bad":
            raise ValueError("no identity")
        return raw
    e = GeneratorEntry("adapt", "1", family_id(definition(4)), lambda c, r: (definition(4), "bad"), adapter=adapter)
    batch = generate_batch(GeneratorRegistry((e,)), GenerationBudget(2, 2, 1), {})
    assert len(calls) == 2
    assert batch.audit["adapter_error"] == 1


def test_same_name_versions_have_independent_entry_counts():
    d4, d5 = definition(4), definition(5)
    entries = (GeneratorEntry("same", "1", family_id(d4), lambda c, r: (d4,)),
               GeneratorEntry("same", "2", family_id(d5), lambda c, r: (d5,)))
    batch = generate_batch(GeneratorRegistry(entries), GenerationBudget(4, 4, 1), {})
    assert batch.generator_counts["same@1"] == 1
    assert batch.generator_counts["same@2"] == 1
    assert {r.generator for r in batch.rejections} == set()


def test_named_adapter_fails_closed_without_complete_identity():
    with pytest.raises(TypeError):
        adapt_gp_output({"expression": ("field", "close")})
    adapter = make_identity_adapter({"feature_contract": {"fields": ["close"]}, "data_schema": {"name": "hourly"}, "universe_policy": {"name": "test"}, "horizon": "1h", "cost_model": {"bps": 5}, "validation_policy": {"name": "purged"}, "model": "ridge", "model_version": "1"})
    assert isinstance(adapter({"expression": ("field", "close")}), AlphaDefinition)
