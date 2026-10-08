"""Golden and tamper tests for the nine Pass-M SQLite semantic functions."""

from __future__ import annotations

import dataclasses
import hashlib
import math

import pytest

from quantos.contracts.canonical import canonical_json_bytes, canonical_sha256, float64_hex
from services.research.experiment_registry import family_schema_spec as fs
from services.research.experiment_registry.family_schema_composition import (
    compose_family_schema_v3,
)
from services.research.experiment_registry.family_schema_semantic_functions import (
    FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS,
    PartialSemanticImplementationEvidenceCatalog,
    QuantosAnyGridDisagreementMaxPV1,
    QuantosCanonicalOrderedValueSha256V1,
    _semantic_implementation_evidence_sha256,
    build_canonical_semantic_callable_bindings,
    build_partial_semantic_implementation_evidence,
    quantos_canonical_json_array_contains_v1,
    quantos_canonical_json_array_length_v1,
    quantos_canonical_json_array_value_v1,
    quantos_canonical_json_field_v1,
    quantos_canonical_json_sha256_v1,
    quantos_mean_net_return_differential_v1,
    quantos_prefix_stream_extension_v1,
)


def _text(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _ordered_hash(*values: object) -> str | None:
    aggregate = QuantosCanonicalOrderedValueSha256V1()
    for value in values:
        aggregate.step(value)
    return aggregate.finalize()


def _latest_policy(*rows: tuple[object, ...]) -> str | None:
    aggregate = QuantosAnyGridDisagreementMaxPV1()
    for row in rows:
        aggregate.step(*row)
    return aggregate.finalize()


def _grid_row(
    block_length: object,
    verdict: object,
    *,
    status: object = "RESOLVED",
    replication_count: object = 10_000,
    record_digit: str = "1",
) -> tuple[object, ...]:
    return (
        block_length,
        replication_count,
        "a" * 64,
        "b" * 64,
        status,
        verdict,
        record_digit * 64,
    )


def test_canonical_json_field_is_exact_typed_and_normalizes_negative_zero() -> None:
    document = _text(
        {
            "flag": True,
            "integer": 7,
            "nested": {"real": -0.0},
            "text": "café",
        }
    )

    assert quantos_canonical_json_field_v1(document, "$.integer", "INTEGER") == 7
    assert quantos_canonical_json_field_v1(document, "$.text", "TEXT") == "café"
    real = quantos_canonical_json_field_v1(document, "$.nested.real", "REAL")
    assert real == 0.0
    assert math.copysign(1.0, real) == 1.0

    assert quantos_canonical_json_field_v1(document, "$.flag", "INTEGER") is None
    assert quantos_canonical_json_field_v1(document, "$.integer", "REAL") is None
    assert quantos_canonical_json_field_v1(document, "$.missing", "INTEGER") is None
    assert quantos_canonical_json_field_v1(document, "integer", "INTEGER") is None
    assert quantos_canonical_json_field_v1(document, "$.integer", "integer") is None


@pytest.mark.parametrize(
    ("tampered", "path", "type_tag"),
    (
        ('{"a":1,"a":2}\n', "$.a", "INTEGER"),
        ('{"b":2,"a":1}\n', "$.a", "INTEGER"),
        ('{"real":-0.0}\n', "$.real", "REAL"),
        ('{"text":"cafe\u0301"}\n', "$.text", "TEXT"),
        ('{"integer":1}', "$.integer", "INTEGER"),
    ),
)
def test_canonical_json_field_rejects_duplicate_keys_order_raw_float_nfc_and_lf(
    tampered: str,
    path: str,
    type_tag: str,
) -> None:
    assert quantos_canonical_json_field_v1(tampered, path, type_tag) is None


def test_canonical_array_functions_enforce_projection_type_and_domain_uniqueness() -> None:
    integers = _text([1, 2, 3])
    assert (
        quantos_canonical_json_array_value_v1(
            integers,
            "$",
            "INTEGER",
            "ELEMENT_AT_ORDINAL",
            1,
        )
        == 2
    )
    assert (
        quantos_canonical_json_array_value_v1(
            integers,
            "$",
            "INTEGER",
            "CANONICAL_ARRAY",
            None,
        )
        == integers
    )
    assert quantos_canonical_json_array_length_v1(integers, "$", "INTEGER") == 3
    assert quantos_canonical_json_array_contains_v1(integers, "$", "INTEGER", 2) == 1
    assert quantos_canonical_json_array_contains_v1(integers, "$", "INTEGER", 4) == 0

    reals = _text([-0.0, 1.5])
    zero = quantos_canonical_json_array_value_v1(
        reals,
        "$",
        "REAL",
        "ELEMENT_AT_ORDINAL",
        0,
    )
    assert zero == 0.0
    assert math.copysign(1.0, zero) == 1.0
    assert quantos_canonical_json_array_contains_v1(reals, "$", "REAL", -0.0) == 1

    duplicate_domain = _text([1, 1])
    assert (
        quantos_canonical_json_array_value_v1(
            duplicate_domain,
            "$",
            "INTEGER",
            "CANONICAL_ARRAY",
            None,
        )
        is None
    )
    # Duplicate elements are forbidden only for a canonical domain projection.
    assert quantos_canonical_json_array_length_v1(duplicate_domain, "$", "INTEGER") == 2


@pytest.mark.parametrize(
    ("document", "path", "tag", "projection", "ordinal"),
    (
        ("[1,true]\n", "$", "INTEGER", "ELEMENT_AT_ORDINAL", 0),
        ("[0.5]\n", "$", "REAL", "ELEMENT_AT_ORDINAL", 0),
        ("[1]\n", "$", "INTEGER", "ELEMENT_AT_ORDINAL", True),
        ("[1]\n", "$", "INTEGER", "ELEMENT_AT_ORDINAL", -1),
        ("[1]\n", "$", "INTEGER", "ELEMENT_AT_ORDINAL", 1),
        ("[1]\n", "$", "INTEGER", "CANONICAL_ARRAY", 0),
        ("[1]\n", "$", "INTEGER", "UNKNOWN", None),
        ("[1]\n", "$", "BOOLEAN", "CANONICAL_ARRAY", None),
        ("[1]\n", "$[0]", "INTEGER", "ELEMENT_AT_ORDINAL", 0),
    ),
)
def test_canonical_array_value_returns_none_for_every_malformed_abi_input(
    document: str,
    path: str,
    tag: str,
    projection: str,
    ordinal: object,
) -> None:
    assert (
        quantos_canonical_json_array_value_v1(
            document,
            path,
            tag,
            projection,
            ordinal,
        )
        is None
    )


def test_payload_hash_removes_exact_owned_paths_and_hashes_canonical_bytes() -> None:
    document = _text({"keep": "x", "owned": {"drop": 1, "keep": 2}})
    excluded = _text([["owned", "drop"]])

    assert quantos_canonical_json_sha256_v1(document, excluded) == (
        "dbe720efbaf49092c13af9670adc3994f2705f3bc9dc79968be43403867eed9a"
    )
    assert quantos_canonical_json_sha256_v1(document, _text([])) == hashlib.sha256(
        document.encode("utf-8")
    ).hexdigest()

    assert quantos_canonical_json_sha256_v1(document, _text([["owned", "missing"]])) is None
    assert quantos_canonical_json_sha256_v1(document, _text([["owned"], ["owned", "drop"]])) is None
    assert quantos_canonical_json_sha256_v1(document, _text([["Owned"]])) is None
    assert quantos_canonical_json_sha256_v1(document, _text(["owned"])) is None
    assert quantos_canonical_json_sha256_v1('{"a":1,"a":2}\n', _text([])) is None


def test_ordered_value_hash_is_a_golden_canonical_sequence_and_preserves_input_order() -> None:
    assert _ordered_hash("a", "b") == (
        "eb394fd4559b1d9c383f4359667a508a615b82a74e1b160fce539f86ae0842e8"
    )
    assert _ordered_hash("b", "a") == (
        "ad3a12409aeab00857792400db0f1b78d46e5740c0284745141339ff4ca77066"
    )
    assert _ordered_hash("a", "b") == canonical_sha256(("a", "b"))

    negative_zero = _ordered_hash(0, 1, -0.0, "text")
    positive_zero = _ordered_hash(0, 1, 0.0, "text")
    assert negative_zero == positive_zero
    assert _ordered_hash() is None
    assert _ordered_hash(True) is None
    assert _ordered_hash(float("inf")) is None
    assert _ordered_hash("cafe\u0301") is None


def test_mean_net_return_differential_uses_aligned_canonical_float64_vectors() -> None:
    candidate = _text({"net_returns": [0.1, 0.2, -0.0]})
    benchmark = _text({"net_returns": [0.0, 0.1, 0.0]})
    expected = ((0.1 - 0.0) + (0.2 - 0.1) + (0.0 - 0.0)) / 3

    assert quantos_mean_net_return_differential_v1(candidate, benchmark) == expected
    zero = quantos_mean_net_return_differential_v1(
        _text({"net_returns": [-0.0]}),
        _text({"net_returns": [0.0]}),
    )
    assert zero == 0.0
    assert math.copysign(1.0, zero) == 1.0

    assert quantos_mean_net_return_differential_v1(
        _text({"net_returns": [0.1]}),
        _text({"net_returns": [0.1, 0.2]}),
    ) is None
    assert quantos_mean_net_return_differential_v1(
        _text({"net_returns": [True]}),
        _text({"net_returns": [0.0]}),
    ) is None
    assert quantos_mean_net_return_differential_v1(
        '{"net_returns":[0.1]}\n',
        _text({"net_returns": [0.0]}),
    ) is None
    assert quantos_mean_net_return_differential_v1(
        _text({"other": [0.1]}),
        _text({"net_returns": [0.0]}),
    ) is None


_GENESIS = "a" * 64
_PREFIX_ONE = "deb2eafa01b915517df78f40f140be8ea141aef73afe8b348c3fe3dc2c7aff16"
_PREFIX_TWO = "19d00115a65a893bbb2ee074f5e956a3f2dd897f346bd9b56b961f5140a66630"
_SIGNED_SEED_MAX_PREFIX_ONE = (
    "7761af92a66de5b41fb79f55ad3a8dce2c15458fef34de1635b8cca73e2649ed"
)
_PREFIX_ALGORITHMS = (
    "QUANTOS_STATIONARY_BOOTSTRAP_V1",
    "SPLITMIX64_V1",
    "QUANTOS_CANONICAL_JSON_V1",
    "QUANTOS_BLOCK_LENGTH_GRID_V1",
)


def test_prefix_stream_extension_matches_splitmix64_stationary_bootstrap_golden() -> None:
    assert quantos_prefix_stream_extension_v1(
        _PREFIX_ONE,
        1,
        20,
        _GENESIS,
        0,
        0,
        3,
        1,
        _GENESIS,
        7,
        *_PREFIX_ALGORITHMS,
    ) == 1
    assert quantos_prefix_stream_extension_v1(
        _PREFIX_TWO,
        2,
        20,
        _PREFIX_ONE,
        1,
        20,
        3,
        2,
        _GENESIS,
        7,
        *_PREFIX_ALGORITHMS,
    ) == 1

    assert quantos_prefix_stream_extension_v1(
        "f" * 64,
        2,
        20,
        _PREFIX_ONE,
        1,
        20,
        3,
        2,
        _GENESIS,
        7,
        *_PREFIX_ALGORITHMS,
    ) == 0
    assert quantos_prefix_stream_extension_v1(
        _PREFIX_TWO,
        2,
        20,
        "f" * 64,
        1,
        20,
        3,
        2,
        _GENESIS,
        7,
        *_PREFIX_ALGORITHMS,
    ) == 0


def test_prefix_stream_extension_returns_none_for_malformed_or_unknown_contract_inputs() -> None:
    base = (
        _PREFIX_TWO,
        2,
        20,
        _PREFIX_ONE,
        1,
        20,
        3,
        2,
        _GENESIS,
        7,
        *_PREFIX_ALGORITHMS,
    )
    malformed = (
        (*base[:9], True, *base[10:]),
        (*base[:10], "OTHER_BOOTSTRAP_V1", *base[11:]),
        (*base[:4], 2, *base[5:]),
        (*base[:5], 19, *base[6:]),
        (*base[:7], 0, *base[8:]),
        ("F" * 64, *base[1:]),
    )
    for arguments in malformed:
        assert quantos_prefix_stream_extension_v1(*arguments) is None


def test_prefix_seed_boundary_is_honest_about_the_sqlite_integer_abi() -> None:
    # SplitMix64 has a uint64 state, but this UDF ABI receives a persisted
    # SQLite INTEGER.  The current v3 persistence contract therefore cannot
    # transport seeds above signed int64 without a separate schema fix.
    assert quantos_prefix_stream_extension_v1(
        _SIGNED_SEED_MAX_PREFIX_ONE,
        1,
        20,
        _GENESIS,
        0,
        0,
        3,
        1,
        _GENESIS,
        (1 << 63) - 1,
        *_PREFIX_ALGORITHMS,
    ) == 1
    assert quantos_prefix_stream_extension_v1(
        _SIGNED_SEED_MAX_PREFIX_ONE,
        1,
        20,
        _GENESIS,
        0,
        0,
        3,
        1,
        _GENESIS,
        1 << 63,
        *_PREFIX_ALGORITHMS,
    ) is None


def test_latest_per_domain_policy_returns_exact_canonical_conclusion() -> None:
    result = _latest_policy(
        _grid_row(2, "SUPPORTS_SUPERIORITY", record_digit="1"),
        _grid_row(4, "DOES_NOT_SUPPORT_SUPERIORITY", record_digit="2"),
    )
    assert result == (
        '{"diagnostic_verdict":"DOES_NOT_SUPPORT_SUPERIORITY",'
        '"ordered_latest_grid_assessment_hashes_hash":'
        '"9f515cdfec65715c559e23d7ffb5956b48f9d2a47ee09addb8ee88c2b66c4f55"}\n'
    )

    all_support = _latest_policy(
        _grid_row(2, "SUPPORTS_SUPERIORITY", record_digit="1"),
        _grid_row(4, "SUPPORTS_SUPERIORITY", record_digit="2"),
    )
    assert all_support is not None
    assert '"diagnostic_verdict":"SUPPORTS_SUPERIORITY"' in all_support

    disagreement = _latest_policy(
        _grid_row(2, "SUPPORTS_SUPERIORITY", record_digit="1"),
        _grid_row(4, "METHOD_DISAGREEMENT", record_digit="2"),
    )
    assert disagreement is not None
    assert '"diagnostic_verdict":"METHOD_DISAGREEMENT"' in disagreement

    unresolved = _latest_policy(
        _grid_row(
            2,
            "MONTE_CARLO_UNRESOLVED",
            status="AMBIGUOUS",
            record_digit="1",
        ),
        _grid_row(4, "METHOD_DISAGREEMENT", record_digit="2"),
    )
    assert unresolved is not None
    assert '"diagnostic_verdict":"MONTE_CARLO_UNRESOLVED"' in unresolved


@pytest.mark.parametrize(
    "rows",
    (
        (),
        (_grid_row(True, "SUPPORTS_SUPERIORITY"),),
        (_grid_row(2, "SUPPORTS_SUPERIORITY", replication_count=9_999),),
        (_grid_row(2, "NOT_APPLICABLE", status="INVALID"),),
        (_grid_row(2, "PENDING_MORE_REPLICATIONS", status="AMBIGUOUS"),),
        (
            _grid_row(4, "SUPPORTS_SUPERIORITY", record_digit="1"),
            _grid_row(2, "SUPPORTS_SUPERIORITY", record_digit="2"),
        ),
        (
            _grid_row(2, "SUPPORTS_SUPERIORITY", record_digit="1"),
            _grid_row(4, "SUPPORTS_SUPERIORITY", record_digit="1"),
        ),
    ),
)
def test_latest_per_domain_policy_fails_closed_on_invalid_rows_or_order(
    rows: tuple[tuple[object, ...], ...],
) -> None:
    assert _latest_policy(*rows) is None


def test_partial_evidence_catalog_binds_only_the_nine_real_semantic_callables() -> None:
    first = build_partial_semantic_implementation_evidence()
    second = build_partial_semantic_implementation_evidence()

    assert first == second
    assert type(first) is PartialSemanticImplementationEvidenceCatalog
    assert not isinstance(first, fs.ImplementationEvidenceCatalog)
    assert first.sealed is False
    assert first.complete is False
    assert len(first.requirements.entries) == 9
    assert len(first.bindings) == 9
    assert tuple(item.operation for item in FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS) == tuple(
        fs.RendererSemanticOperation
    )
    assert all(
        binding.hash_method
        is fs.ImplementationEvidenceHashMethod.PYTHON_CALLABLE_AND_CANONICAL_BINDING_SHA256_V1
        for binding in first.bindings
    )
    assert len({binding.implementation_sha256 for binding in first.bindings}) == 9

    composition = compose_family_schema_v3()
    full_internal = tuple(
        requirement
        for requirement in composition.implementation_evidence_requirements.entries
        if requirement.evidence_policy is fs.ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256
    )
    assert len(full_internal) > len(first.bindings)


def test_semantic_evidence_hash_changes_with_callable_or_canonical_abi_binding() -> None:
    composition = compose_family_schema_v3()
    requirement = next(
        item
        for item in composition.implementation_evidence_requirements.entries
        if item.semantic_operation is fs.RendererSemanticOperation.CANONICAL_JSON_FIELD_VALUE
    )
    abi = next(
        item
        for item in composition.renderer_grammar.semantic_function_abis
        if item.operation is requirement.semantic_operation
    )
    implementation = FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS[0].implementation
    baseline = _semantic_implementation_evidence_sha256(requirement, abi, implementation)

    def changed_implementation(document: object, path: object, type_tag: object) -> None:
        del document, path, type_tag
        return None

    changed_callable = _semantic_implementation_evidence_sha256(
        requirement,
        abi,
        changed_implementation,
    )
    changed_abi = _semantic_implementation_evidence_sha256(
        requirement,
        dataclasses.replace(
            abi,
            result_type=fs.RendererAbiValueKind.TEXT,
        ),
        implementation,
    )

    assert baseline != changed_callable
    assert baseline != changed_abi
    assert baseline == build_partial_semantic_implementation_evidence().bindings[0].implementation_sha256


def test_typed_registry_converts_directly_to_canonical_binder_rows_without_import_cycle() -> None:
    # The semantic module was imported before the renderer at module collection.
    # This delayed conversion proves the reverse import is not needed at module
    # initialization and produces the binder's exact native row type.
    from services.research.experiment_registry import family_schema as canonical_binder

    composition = compose_family_schema_v3()
    rows = build_canonical_semantic_callable_bindings(composition)
    partial = build_partial_semantic_implementation_evidence(composition)
    requirements = partial.requirements.entries
    abis = composition.renderer_grammar.semantic_function_abis

    assert len(rows) == 9
    assert all(type(row) is canonical_binder._ImplementationCallableBinding for row in rows)
    for row, requirement, abi, evidence in zip(
        rows,
        requirements,
        abis,
        partial.bindings,
        strict=True,
    ):
        key = requirement.canonical_binding_keys[0]
        direct_hash = canonical_binder._implementation_evidence_sha256(
            requirement,
            ((key, abi),),
            canonical_binder._implementation_descriptor(row.implementation),
        )
        assert row.target_kind is requirement.target_kind
        assert row.target_id == requirement.target_id
        assert row.implementation_id == requirement.implementation_id
        assert direct_hash == evidence.implementation_sha256


def test_real_type_tag_rejects_noncanonical_float_hex_spelling() -> None:
    canonical = _text({"value": 1.5})
    assert quantos_canonical_json_field_v1(canonical, "$.value", "REAL") == 1.5
    assert quantos_canonical_json_field_v1('{"value":"0X1.8P+0"}\n', "$.value", "REAL") is None
    assert float64_hex(-0.0) == "0x0.0p+0"
