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
    adapt_alpha_factory_output, adapt_mcts_output, adapt_ml_template_output,
    make_alpha_factory_adapter, make_evolutionary_adapter, make_gp_adapter,
    make_mcts_adapter, make_ml_template_adapter,
    make_identity_adapter,
    adapt_existing_generator,
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
    if not isinstance(family, str) or len(family) != 64:
        raise ValueError("tests must pass a canonical family id")

    def generate(context, rng):
        return iter(values)

    return GeneratorEntry(name=name, version="1", family=family,
                          generate=generate, max_share=max_share)


def test_same_seed_has_same_order_and_audit():
    registry = GeneratorRegistry((entry("b", family_id(definition(2)), [definition(2), definition(3)]), entry("a", family_id(definition(4)), [definition(4)])))
    budget = GenerationBudget(max_candidates=10, max_family_candidates=10, seed=17)
    left = generate_batch(registry, budget, {})
    right = generate_batch(registry, budget, {})
    assert [c.alpha_id for c in left.candidates] == [c.alpha_id for c in right.candidates]
    assert left.audit == right.audit


def test_total_family_and_entry_caps_are_enforced():
    values = [definition(i) for i in range(1, 20)]
    other_values = [definition(i, model="xgb") for i in range(1, 20)]
    registry = GeneratorRegistry((entry("a", family_id(values[0]), values, max_share=0.3),
                                  entry("b", family_id(other_values[0]), other_values)))
    batch = generate_batch(registry, GenerationBudget(10, 3, 1), {})
    assert len(batch.candidates) <= 10
    assert max(batch.family_counts.values()) <= 3
    assert batch.generator_counts["a@1"] <= 3
    assert batch.generator_counts["b@1"] <= 10
    assert sum(batch.family_counts.values()) == 6


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
    registry = GeneratorRegistry((entry("bad", "0" * 64, [("not", "an", "alpha")]),))
    batch = generate_batch(registry, GenerationBudget(2, 2, 1), {})
    assert batch.candidates == ()
    assert batch.rejections[0].reason == "adapter_error"


def test_duplicate_alpha_ids_keep_first_in_deterministic_order():
    registry = GeneratorRegistry((entry("a", family_id(definition(4)), [definition(4)]), entry("b", family_id(definition(4)), [definition(4)])))
    batch = generate_batch(registry, GenerationBudget(4, 4, 1), {})
    assert len(batch.candidates) == 1
    assert batch.rejections[0].reason == "duplicate_alpha_id"


@pytest.mark.slow
def test_10000_candidate_smoke_is_bounded_and_deterministic():
    def make(op, i):
        return AlphaDefinition(
            definition=(op, ("field", "close"), i + 1),
            feature_contract={"fields": ["close"]}, data_schema={"name": "hourly"},
            universe_policy={"name": "test"}, horizon="1h", cost_model={"bps": 5},
            validation_policy={"name": "purged"}, model="ridge", model_version="1",
        )

    family_a = family_id(make("ts_mean", 0))
    family_b = family_id(make("ts_std", 0))
    def generate_a(context, rng):
        for i in range(6000):
            yield make("ts_mean", i + rng.randrange(1))
    def generate_b(context, rng):
        for i in range(6000):
            yield make("ts_std", i + rng.randrange(1))

    registry = GeneratorRegistry((
        GeneratorEntry("family-a", "1", family_a, generate_a),
        GeneratorEntry("family-b", "1", family_b, generate_b),
    ))
    budget = GenerationBudget(10000, 5000, 123)
    left = generate_batch(registry, budget, {"panel": "fixed"})
    right = generate_batch(registry, budget, {"panel": "fixed"})
    assert [c.alpha_id for c in left.candidates] == [c.alpha_id for c in right.candidates]
    assert left.audit == right.audit
    assert left.rejections == right.rejections
    assert len(left.candidates) <= budget.max_candidates
    assert len({c.alpha_id for c in left.candidates}) == len(left.candidates)
    assert all(count <= budget.max_family_candidates for count in left.family_counts.values())
    assert not left.audit.get("generator_attempt_cap", 0)
    assert left.generator_counts["family-a@1"] == 5000
    assert left.generator_counts["family-b@1"] == 5000


def test_iterator_error_after_yield_is_audited_without_escape():
    def generate(context, rng):
        yield definition(4)
        raise RuntimeError("broken body")
    e = entry("raises", family_id(definition(4)), [definition(4)])
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


def test_name_and_version_cannot_ambiguously_contain_separator():
    with pytest.raises(ValueError):
        GeneratorEntry("a@b", "1", "0" * 64, lambda c, r: ())
    with pytest.raises(ValueError):
        GeneratorEntry("a", "1@2", "0" * 64, lambda c, r: ())


def test_mapping_and_scalar_outputs_are_singletons_not_iterated():
    payload = {
        "definition": ("ts_mean", ("field", "close"), 4),
        "feature_contract": {"fields": ["close"]},
        "data_schema": {"name": "hourly"}, "universe_policy": {"name": "test"},
        "horizon": "1h", "cost_model": {"bps": 5},
        "validation_policy": {"name": "purged"}, "model": "ridge", "model_version": "1",
    }
    # A custom adapter proves that a non-iterable scalar reaches the adapter once.
    calls = []
    wrapped = adapt_existing_generator(lambda c, r: payload, lambda value: (calls.append(value), definition())[1])
    result = wrapped({}, random.Random(1))
    assert isinstance(result, AlphaDefinition)
    assert calls == [payload]


def test_identity_adapter_deep_snapshots_nested_metadata():
    metadata = {"fields": ["close"], "nested": {"x": [1]}}
    adapter = make_identity_adapter({
        "feature_contract": metadata,
        "data_schema": {"name": "hourly"}, "universe_policy": {"name": "test"},
        "horizon": "1h", "cost_model": {"bps": 5},
        "validation_policy": {"name": "purged"}, "model": "ridge", "model_version": "1",
    })
    metadata["fields"].append("open")
    metadata["nested"]["x"].append(2)
    result = adapter({"expression": ("field", "close")})
    assert result.feature_contract["fields"] == ("close",)
    assert result.feature_contract["nested"]["x"] == (1,)


def _identity():
    return {"feature_contract": {"fields": ["close"]}, "data_schema": {"name": "hourly"},
            "universe_policy": {"name": "test"}, "horizon": "1h", "cost_model": {"bps": 5},
            "validation_policy": {"name": "purged"}, "model": "ridge", "model_version": "1"}


def test_default_mapping_and_text_scalars_are_bounded():
    payload = {"definition": ("ts_mean", ("field", "close"), 4), **_identity()}
    family = family_id(definition())
    mapping_batch = generate_batch(GeneratorRegistry((GeneratorEntry("mapping", "1", family, lambda c, r: payload),)),
                                   GenerationBudget(1, 1, 1), {})
    assert len(mapping_batch.candidates) == 1
    text_batch = generate_batch(GeneratorRegistry((GeneratorEntry("text", "1", family, lambda c, r: "bad"),)),
                               GenerationBudget(1, 1, 1), {})
    assert text_batch.audit["adapter_error"] == 1
    assert len(text_batch.rejections) == 1


def test_global_and_entry_caps_stop_invocation_and_next():
    calls = []
    d = definition(4); family = family_id(d)
    def first(c, r):
        calls.append("first")
        return (d,)
    def second(c, r):
        calls.append("second")
        return (definition(5),)
    batch = generate_batch(GeneratorRegistry((GeneratorEntry("a", "1", family, first, 1.0),
                                               GeneratorEntry("b", "1", family, second, 1.0))),
                           GenerationBudget(1, 10, 1), {})
    assert calls == ["first"] and len(batch.candidates) == 1


def test_finite_exact_budget_has_no_attempt_cap_and_zero_dimensions_audit_all():
    d = definition(4); family = family_id(d); called = []
    def finite(c, r):
        called.append(1)
        return (definition(4), definition(5))
    entry_value = GeneratorEntry("finite", "1", family, finite)
    exact = generate_batch(GeneratorRegistry((entry_value,)), GenerationBudget(2, 2, 1), {})
    assert "generator_attempt_cap" not in exact.audit
    for budget in (GenerationBudget(0, 2, 1), GenerationBudget(2, 0, 1)):
        called.clear(); result = generate_batch(GeneratorRegistry((entry_value,)), budget, {})
        assert not called and result.generator_counts == {"finite@1": 0}


def test_registry_validation_and_unregistered_expression_rejection():
    d = definition(); family = family_id(d)
    with pytest.raises(ValueError): GeneratorRegistry((GeneratorEntry("x", "1", family, lambda c, r: ()), GeneratorEntry("x", "1", family, lambda c, r: ())))
    with pytest.raises(ValueError): GeneratorEntry("x", "1", family, lambda c, r: (), 1.1)
    with pytest.raises(ValueError): GeneratorEntry("x", "1", family, lambda c, r: (), -0.1)
    bad = GeneratorEntry("bad", "1", family, lambda c, r: {**_identity(), "definition": ("unregistered",)})
    assert generate_batch(GeneratorRegistry((bad,)), GenerationBudget(1, 1, 1), {}).audit["adapter_error"] == 1


def test_real_legacy_shapes_require_explicit_extractor_and_identity():
    identity = _identity()
    cases = [
        (adapt_alpha_factory_output, {"alpha_id": "x", "expr": "text", "score": 1.0, "leakage_ok": True}, lambda v: ("field", "close")),
        (adapt_gp_output, {"tree": ("field", "close")}, lambda v: v["tree"]),
        (adapt_mcts_output, (("field", "close"), 0.4), lambda v: v[0]),
        (adapt_ml_template_output, {"metrics": {"sharpe": 0.2}, "template": ("field", "close")}, lambda v: v["template"]),
    ]
    for adapter, value, extractor in cases:
        result = adapter(value, identity=identity, expression_extractor=extractor)
        assert isinstance(result, AlphaDefinition)
    for builder in (make_alpha_factory_adapter, make_gp_adapter, make_mcts_adapter,
                    make_evolutionary_adapter, make_ml_template_adapter):
        result = builder(identity, lambda value: ("field", "close"))({"legacy": True})
        assert isinstance(result, AlphaDefinition)
    with pytest.raises(TypeError): adapt_alpha_factory_output({"expr": "(field,close)"})


def test_family_cap_and_zero_share_prevent_generator_pull():
    d = definition(4); family = family_id(d); calls = []
    def first(c, r):
        calls.append("first"); return (d, definition(5))
    def second(c, r):
        calls.append("second"); return (definition(6),)
    def zero(c, r):
        calls.append("zero"); raise AssertionError("zero-share entry was invoked")
    result = generate_batch(GeneratorRegistry((
        GeneratorEntry("a", "1", family, first),
        GeneratorEntry("b", "1", family, second),
        GeneratorEntry("z", "1", family, zero, max_share=0),
    )), GenerationBudget(4, 1, 1), {})
    assert calls == ["first"]
    assert result.family_counts[family] == 1
    assert result.generator_counts["z@1"] == 0
