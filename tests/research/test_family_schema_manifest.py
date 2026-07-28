"""Pass-M fail-closed implementation binding and exact-v3 manifest sealing."""

from __future__ import annotations

import dataclasses
import datetime
import functools
import hashlib
import importlib
import math
import sqlite3
import subprocess
import sys
from importlib.machinery import ModuleSpec
from types import ModuleType

import pytest
from pydantic_core import PydanticUndefined

from quantos.contracts import canonical as canonical_module
from services.research.experiment_registry import family_schema_spec as fs
from services.research.experiment_registry.family_schema_composition import (
    EXACT_V3_VERIFIER_COMPOSITION_BINDING_KEY,
    auxiliary_semantic_recipe_binding_key,
    compose_family_schema_v3,
    external_claim_profile_binding_key,
)


def _module():
    from services.research.experiment_registry import family_schema

    return family_schema


def _reviewed_test_implementation(value: object) -> object:
    return value


def _changed_test_implementation(value: object) -> tuple[str, object]:
    return ("changed", value)


class _ReviewedTestAggregate:
    def step(self, *_values: object) -> None:
        pass

    def finalize(self) -> None:
        return None


def _helper_version_one(value: object) -> tuple[str, object]:
    return ("one", value)


def _helper_version_two(value: object) -> tuple[str, object]:
    return ("two", value)


_HASHED_HELPER = _helper_version_one
_NESTED_HASHED_HELPER = _helper_version_one
_MODULE_MEMBER_HELPER = _helper_version_one
_HASHED_CONSTANT = "CONSTANT_ONE"
_DATE_DEPENDENCY = datetime.date(2020, 1, 1)
_PARTIAL_DEPENDENCY = functools.partial(int, base=10)
_PYDANTIC_UNDEFINED_DEPENDENCY = PydanticUndefined


class _OpaqueRuntimeDependency:
    __slots__ = ()


_OPAQUE_RUNTIME_DEPENDENCY = _OpaqueRuntimeDependency()


@dataclasses.dataclass(frozen=True)
class _DataclassDependency:
    value: int

    VERSION = "DATACLASS_ONE"


_DATACLASS_DEPENDENCY = _DataclassDependency(7)
_ALIASED_PARTIAL = functools.partial(int, base=10)
_PARTIAL_ALIAS_PAIR = (_ALIASED_PARTIAL, _ALIASED_PARTIAL)
_SINGLE_CYCLE: list[object] = []
_SINGLE_CYCLE.append(_SINGLE_CYCLE)
_DOUBLE_CYCLE_LEFT: list[object] = []
_DOUBLE_CYCLE_RIGHT: list[object] = []
_DOUBLE_CYCLE_LEFT.append(_DOUBLE_CYCLE_RIGHT)
_DOUBLE_CYCLE_RIGHT.append(_DOUBLE_CYCLE_LEFT)
_CYCLIC_DEPENDENCY = _SINGLE_CYCLE


def _uses_hashed_helper(value: object) -> object:
    return _HASHED_HELPER(value)


def _uses_hashed_constant(value: object) -> tuple[str, object]:
    return (_HASHED_CONSTANT, value)


def _uses_date_dependency(value: object) -> tuple[int, object]:
    return (_DATE_DEPENDENCY.year, value)


def _uses_partial_dependency(value: str) -> int:
    return _PARTIAL_DEPENDENCY(value)


def _uses_native_singleton_dependency(value: object) -> tuple[object, object]:
    return (_PYDANTIC_UNDEFINED_DEPENDENCY, value)


def _uses_dataclass_class_attribute(value: object) -> tuple[str, object]:
    return (_DATACLASS_DEPENDENCY.VERSION, value)


def _uses_partial_alias_topology(value: object) -> tuple[bool, object]:
    return (_PARTIAL_ALIAS_PAIR[0] is _PARTIAL_ALIAS_PAIR[1], value)


def _uses_cyclic_dependency(value: object) -> tuple[int, object]:
    return (len(_CYCLIC_DEPENDENCY), value)


def _uses_nested_hashed_helper(value: object) -> object:
    def nested() -> object:
        return _NESTED_HASHED_HELPER(value)

    return nested()


def _uses_comprehension_hashed_helper(value: object) -> object:
    return [_NESTED_HASHED_HELPER(item) for item in (value,)][0]


class _MutableGlobalDependency:
    VERSION = "GLOBAL_CLASS_ONE"


_FUNCTION_ATTRIBUTE_HELPER = _helper_version_one
_FUNCTION_ATTRIBUTE_HELPER.VERSION = "FUNCTION_ATTRIBUTE_ONE"
_GETATTR_DEPENDENCY = _MutableGlobalDependency
_DYNAMIC_ATTRIBUTE_NAME = "VERSION"


def _uses_global_class_attribute(value: object) -> tuple[str, object]:
    return (_MutableGlobalDependency.VERSION, value)


def _uses_function_attribute(value: object) -> tuple[str, object]:
    return (_FUNCTION_ATTRIBUTE_HELPER.VERSION, value)


def _uses_constant_getattr(value: object) -> tuple[str, object]:
    return (getattr(_GETATTR_DEPENDENCY, "VERSION"), value)


def _uses_dynamic_getattr(value: object) -> tuple[str, object]:
    return (getattr(_GETATTR_DEPENDENCY, _DYNAMIC_ATTRIBUTE_NAME), value)


def _module_member_uses_indirect_helper(value: object) -> object:
    return _MODULE_MEMBER_HELPER(value)


def _uses_module_attribute(value: float) -> int:
    return math.floor(value)


def _uses_quantos_canonical_module(value: object) -> bytes:
    return canonical_module.canonical_json_bytes(value)


class _MutableSourceLoader:
    source = "def transform(value):\n    return value\n"

    def get_source(self, _fullname: str) -> str:
        return self.source


_MUTABLE_SOURCE_LOADER = _MutableSourceLoader()
_HASHED_MODULE = ModuleType("quantos_test_material_module")
_HASHED_MODULE.__spec__ = ModuleSpec(
    _HASHED_MODULE.__name__,
    _MUTABLE_SOURCE_LOADER,
    origin="quantos_test_material_module.py",
)
_HASHED_MODULE.transform = _module_member_uses_indirect_helper
_HASHED_MODULE.Dependency = _MutableGlobalDependency
_UNCLOSED_MODULE = ModuleType("quantos_test_unclosed_module")
_UNCLOSED_MODULE.transform = _helper_version_one


def _uses_hashed_module(value: object) -> object:
    return _HASHED_MODULE.transform(value)


def _uses_module_nested_attribute(value: object) -> tuple[str, object]:
    return (_HASHED_MODULE.Dependency.VERSION, value)


def _uses_unclosed_module(value: object) -> object:
    return _UNCLOSED_MODULE.transform(value)


class _MutableAggregateBase:
    BASE_VERSION = "BASE_ONE"


class _ReviewedMutableAggregate(_MutableAggregateBase):
    CLASS_VERSION = "CLASS_ONE"

    @staticmethod
    def normalize(value: object) -> object:
        return value

    @classmethod
    def versioned(cls, value: object) -> tuple[str, object]:
        return (cls.CLASS_VERSION, value)

    def step(self, *_values: object) -> None:
        self.value = self.versioned(self.normalize(_values))

    def finalize(self) -> tuple[str, object]:
        return (self.BASE_VERSION, self.value)


def _complete_callable_bindings(
    function=_reviewed_test_implementation,
    aggregate=_ReviewedTestAggregate,
):
    module = _module()
    composition = compose_family_schema_v3()
    requirements = composition.implementation_evidence_requirements
    aggregate_operations = {
        abi.operation
        for abi in composition.renderer_grammar.semantic_function_abis
        if abi.kind is fs.RendererSemanticFunctionKind.AGGREGATE
    }
    return tuple(
        module._ImplementationCallableBinding(
            target_kind=requirement.target_kind,
            target_id=requirement.target_id,
            implementation_id=requirement.implementation_id,
            implementation=(aggregate if requirement.semantic_operation in aggregate_operations else function),
        )
        for requirement in requirements.entries
        if requirement.evidence_policy is fs.ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256
    )


def test_binding_requires_exact_internal_callable_coverage_and_hashes_real_source() -> None:
    module = _module()
    composition = compose_family_schema_v3()
    bindings = _complete_callable_bindings()

    first = module._bind_implementation_evidence_for_test(bindings, composition)
    second = module._bind_implementation_evidence_for_test(bindings, composition)

    assert first == second
    assert first.requirements is composition.implementation_evidence_requirements
    assert len(first.bindings) == 136
    assert len({item.implementation_sha256 for item in first.bindings}) == 136
    assert all(
        item.hash_method is fs.ImplementationEvidenceHashMethod.PYTHON_CALLABLE_AND_CANONICAL_BINDING_SHA256_V1
        for item in first.bindings
    )

    with pytest.raises(module.FamilySchemaRenderError, match="exactly cover"):
        module._bind_implementation_evidence_for_test(bindings[:-1], composition)
    with pytest.raises(module.FamilySchemaRenderError, match="exactly cover"):
        module._bind_implementation_evidence_for_test(
            (bindings[1], bindings[0], *bindings[2:]),
            composition,
        )
    with pytest.raises(TypeError, match="_ImplementationCallableBinding"):
        module._bind_implementation_evidence_for_test(first, composition)


def test_callable_or_bound_spec_change_changes_actual_evidence_hash() -> None:
    module = _module()
    composition = compose_family_schema_v3()
    baseline_bindings = _complete_callable_bindings()
    changed_bindings = (
        dataclasses.replace(
            baseline_bindings[0],
            implementation=_changed_test_implementation,
        ),
        *baseline_bindings[1:],
    )

    baseline = module._bind_implementation_evidence_for_test(
        baseline_bindings,
        composition,
    )
    changed = module._bind_implementation_evidence_for_test(
        changed_bindings,
        composition,
    )

    assert baseline.bindings[0].implementation_sha256 != (changed.bindings[0].implementation_sha256)
    assert baseline.bindings[1:] == changed.bindings[1:]

    requirement = composition.implementation_evidence_requirements.entries[0]
    binding_key = requirement.canonical_binding_keys[0]
    binding_map = module._canonical_implementation_binding_map(composition)
    descriptor = module._implementation_descriptor(_reviewed_test_implementation)
    canonical_hash = module._implementation_evidence_sha256(
        requirement,
        ((binding_key, binding_map[binding_key]),),
        descriptor,
    )
    wrong_abi_hash = module._implementation_evidence_sha256(
        requirement,
        ((binding_key, composition.renderer_grammar.semantic_function_abis[1]),),
        descriptor,
    )
    assert canonical_hash != wrong_abi_hash


def _requirement_hash(
    module,
    requirement,
    binding_map,
) -> str:
    return module._implementation_evidence_sha256(
        requirement,
        tuple((key, binding_map[key]) for key in requirement.canonical_binding_keys),
        module._implementation_descriptor(_reviewed_test_implementation),
    )


def _unsafe_dataclass_replace(value, **changes):
    clone = object.__new__(type(value))
    for field in dataclasses.fields(value):
        object.__setattr__(
            clone,
            field.name,
            changes.get(field.name, getattr(value, field.name)),
        )
    return clone


def test_recipe_profile_and_exact_structural_source_bytes_change_evidence_hash() -> None:
    module = _module()
    composition = compose_family_schema_v3()
    binding_map = module._canonical_implementation_binding_map(composition)

    component = next(item for item in composition.components if item.auxiliary_semantic_recipes)
    recipe = component.auxiliary_semantic_recipes[0]
    recipe_key = auxiliary_semantic_recipe_binding_key(
        component.component_id,
        recipe.recipe_id,
    )
    recipe_requirement = next(
        item
        for item in composition.implementation_evidence_requirements.entries
        if recipe_key in item.canonical_binding_keys
        and item.evidence_policy is fs.ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256
    )
    recipe_baseline = _requirement_hash(
        module,
        recipe_requirement,
        binding_map,
    )
    recipe_tampered_map = {
        **binding_map,
        recipe_key: dataclasses.replace(
            recipe,
            grant_required=not recipe.grant_required,
        ),
    }
    assert (
        _requirement_hash(
            module,
            recipe_requirement,
            recipe_tampered_map,
        )
        != recipe_baseline
    )

    profile = composition.external_authority_claim_profiles[0]
    profile_key = external_claim_profile_binding_key(
        profile.provider_contract_type,
        profile.provider_capability,
    )
    profile_requirement = next(
        item
        for item in composition.implementation_evidence_requirements.entries
        if item.target_kind is fs.ImplementationTargetKind.NAMED_VERIFIER and profile_key in item.canonical_binding_keys
    )
    profile_baseline = _requirement_hash(
        module,
        profile_requirement,
        binding_map,
    )
    profile_tampered_map = {
        **binding_map,
        profile_key: dataclasses.replace(
            profile,
            required_binding_columns=(
                "tampered_binding",
                *profile.required_binding_columns[1:],
            ),
        ),
    }
    assert (
        _requirement_hash(
            module,
            profile_requirement,
            profile_tampered_map,
        )
        != profile_baseline
    )

    structural_requirement = next(
        item
        for item in composition.implementation_evidence_requirements.entries
        if item.target_kind is fs.ImplementationTargetKind.STRUCTURAL_HANDLER
    )
    structural_baseline = _requirement_hash(
        module,
        structural_requirement,
        binding_map,
    )

    def composition_with_table(table, tampered_table):
        return _unsafe_dataclass_replace(
            composition,
            tables=tuple(tampered_table if item is table else item for item in composition.tables),
        )

    row_hash_table = composition.tables[0]
    chain_table = next(item for item in composition.tables if item.chain is not None)
    check_table = next(item for item in composition.tables if item.checks)
    index_table = next(item for item in composition.tables if item.indexes)
    trigger_table = next(item for item in composition.tables if item.triggers)
    foreign_key_table = next(item for item in composition.tables if item.foreign_keys)
    multi_primary_key_table = next(item for item in composition.tables if len(item.primary_key_columns) > 1)
    tampered_compositions = (
        composition_with_table(
            row_hash_table,
            _unsafe_dataclass_replace(
                row_hash_table,
                row_hash=_unsafe_dataclass_replace(
                    row_hash_table.row_hash,
                    version_literal="TAMPERED_ROW_HASH",
                ),
            ),
        ),
        composition_with_table(
            chain_table,
            _unsafe_dataclass_replace(
                chain_table,
                chain=_unsafe_dataclass_replace(
                    chain_table.chain,
                    ordinal_column="tampered_chain_ordinal",
                ),
            ),
        ),
        composition_with_table(
            check_table,
            _unsafe_dataclass_replace(
                check_table,
                checks=(
                    _unsafe_dataclass_replace(
                        check_table.checks[0],
                        name="tampered_check",
                    ),
                    *check_table.checks[1:],
                ),
            ),
        ),
        composition_with_table(
            index_table,
            _unsafe_dataclass_replace(
                index_table,
                indexes=(
                    _unsafe_dataclass_replace(
                        index_table.indexes[0],
                        name="tampered_index",
                    ),
                    *index_table.indexes[1:],
                ),
            ),
        ),
        composition_with_table(
            trigger_table,
            _unsafe_dataclass_replace(
                trigger_table,
                triggers=(
                    _unsafe_dataclass_replace(
                        trigger_table.triggers[0],
                        error_code="TAMPERED_TRIGGER",
                    ),
                    *trigger_table.triggers[1:],
                ),
            ),
        ),
        composition_with_table(
            foreign_key_table,
            _unsafe_dataclass_replace(
                foreign_key_table,
                foreign_keys=(
                    _unsafe_dataclass_replace(
                        foreign_key_table.foreign_keys[0],
                        referenced_table="tampered_reference",
                    ),
                    *foreign_key_table.foreign_keys[1:],
                ),
            ),
        ),
        composition_with_table(
            multi_primary_key_table,
            _unsafe_dataclass_replace(
                multi_primary_key_table,
                primary_key_columns=tuple(reversed(multi_primary_key_table.primary_key_columns)),
            ),
        ),
    )
    for tampered_composition in tampered_compositions:
        tampered_map = {
            **binding_map,
            EXACT_V3_VERIFIER_COMPOSITION_BINDING_KEY: tampered_composition,
        }
        assert (
            _requirement_hash(
                module,
                structural_requirement,
                tampered_map,
            )
            != structural_baseline
        )


def test_local_helper_source_and_global_constant_are_material_hash_dependencies() -> None:
    module = _module()
    composition = compose_family_schema_v3()
    global _HASHED_CONSTANT, _HASHED_HELPER

    helper_baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_hashed_helper),
        composition,
    )
    try:
        _HASHED_HELPER = _helper_version_two
        helper_changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_hashed_helper),
            composition,
        )
    finally:
        _HASHED_HELPER = _helper_version_one
    assert helper_baseline.bindings[0].implementation_sha256 != (helper_changed.bindings[0].implementation_sha256)

    constant_baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_hashed_constant),
        composition,
    )
    try:
        _HASHED_CONSTANT = "CONSTANT_TWO"
        constant_changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_hashed_constant),
            composition,
        )
    finally:
        _HASHED_CONSTANT = "CONSTANT_ONE"
    assert constant_baseline.bindings[0].implementation_sha256 != (constant_changed.bindings[0].implementation_sha256)


@pytest.mark.parametrize(
    "implementation",
    (_uses_nested_hashed_helper, _uses_comprehension_hashed_helper),
)
def test_nested_code_global_helpers_are_material_hash_dependencies(
    implementation,
) -> None:
    module = _module()
    global _NESTED_HASHED_HELPER

    baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(implementation),
    )
    try:
        _NESTED_HASHED_HELPER = _helper_version_two
        changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(implementation),
        )
    finally:
        _NESTED_HASHED_HELPER = _helper_version_one

    assert baseline.bindings[0].implementation_sha256 != (changed.bindings[0].implementation_sha256)


def test_global_class_runtime_attributes_are_material_hash_dependencies() -> None:
    module = _module()
    original = _MutableGlobalDependency.VERSION
    baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_global_class_attribute),
    )
    try:
        _MutableGlobalDependency.VERSION = "GLOBAL_CLASS_TWO"
        changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_global_class_attribute),
        )
    finally:
        _MutableGlobalDependency.VERSION = original

    assert baseline.bindings[0].implementation_sha256 != (changed.bindings[0].implementation_sha256)


def test_module_member_function_indirect_globals_are_material_dependencies() -> None:
    module = _module()
    global _MODULE_MEMBER_HELPER
    baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_hashed_module),
    )
    try:
        _MODULE_MEMBER_HELPER = _helper_version_two
        changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_hashed_module),
        )
    finally:
        _MODULE_MEMBER_HELPER = _helper_version_one

    assert baseline.bindings[0].implementation_sha256 != (changed.bindings[0].implementation_sha256)


def test_module_nested_attributes_are_material_hash_dependencies() -> None:
    module = _module()
    original = _MutableGlobalDependency.VERSION
    baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_module_nested_attribute),
    )
    try:
        _MutableGlobalDependency.VERSION = "GLOBAL_CLASS_TWO"
        changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_module_nested_attribute),
        )
    finally:
        _MutableGlobalDependency.VERSION = original

    assert baseline.bindings[0].implementation_sha256 != (changed.bindings[0].implementation_sha256)


def test_function_runtime_attributes_are_material_hash_dependencies() -> None:
    module = _module()
    original = _FUNCTION_ATTRIBUTE_HELPER.VERSION
    baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_function_attribute),
    )
    try:
        _FUNCTION_ATTRIBUTE_HELPER.VERSION = "FUNCTION_ATTRIBUTE_TWO"
        changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_function_attribute),
        )
    finally:
        _FUNCTION_ATTRIBUTE_HELPER.VERSION = original

    assert baseline.bindings[0].implementation_sha256 != (changed.bindings[0].implementation_sha256)


def test_constant_getattr_target_is_a_material_hash_dependency() -> None:
    module = _module()
    original = _MutableGlobalDependency.VERSION
    baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_constant_getattr),
    )
    try:
        _MutableGlobalDependency.VERSION = "GLOBAL_CLASS_TWO"
        changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_constant_getattr),
        )
    finally:
        _MutableGlobalDependency.VERSION = original

    assert baseline.bindings[0].implementation_sha256 != (changed.bindings[0].implementation_sha256)


def test_dynamic_getattr_dependency_fails_closed() -> None:
    module = _module()

    with pytest.raises(
        module.FamilySchemaRenderError,
        match="dynamic getattr",
    ):
        module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_dynamic_getattr),
        )


def test_date_runtime_value_is_a_material_hash_dependency() -> None:
    module = _module()
    global _DATE_DEPENDENCY
    baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_date_dependency),
    )
    try:
        _DATE_DEPENDENCY = datetime.date(2021, 1, 1)
        changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_date_dependency),
        )
    finally:
        _DATE_DEPENDENCY = datetime.date(2020, 1, 1)

    assert baseline.bindings[0].implementation_sha256 != (changed.bindings[0].implementation_sha256)


def test_partial_runtime_configuration_is_a_material_hash_dependency() -> None:
    module = _module()
    global _PARTIAL_DEPENDENCY
    baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_partial_dependency),
    )
    try:
        _PARTIAL_DEPENDENCY = functools.partial(int, base=16)
        changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_partial_dependency),
        )
    finally:
        _PARTIAL_DEPENDENCY = functools.partial(int, base=10)

    assert baseline.bindings[0].implementation_sha256 != (changed.bindings[0].implementation_sha256)


def test_native_module_singleton_dependency_is_closed_and_cross_process_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    descriptor = module._runtime_dependency_value(
        _PYDANTIC_UNDEFINED_DEPENDENCY,
        set(),
    )

    assert descriptor["native_module_singleton"]["export_name"] == "PydanticUndefined"
    assert descriptor["native_module_singleton"]["reduction_name"] == "PydanticUndefined"
    expected_sha256 = hashlib.sha256(canonical_module.canonical_json_bytes(descriptor)).hexdigest()
    script = "\n".join(
        (
            "import hashlib",
            "from pydantic_core import PydanticUndefined",
            "from quantos.contracts.canonical import canonical_json_bytes",
            "from services.research.experiment_registry import family_schema",
            "descriptor = family_schema._runtime_dependency_value(PydanticUndefined, set())",
            "print(hashlib.sha256(canonical_json_bytes(descriptor)).hexdigest())",
        )
    )
    observed = tuple(
        subprocess.check_output(
            [sys.executable, "-c", script],
            text=True,
        ).strip()
        for _ in range(2)
    )
    assert observed == (expected_sha256, expected_sha256)

    owner = importlib.import_module(type(_PYDANTIC_UNDEFINED_DEPENDENCY).__module__)
    monkeypatch.setattr(owner, "PydanticUndefined", object())
    with pytest.raises(
        module.FamilySchemaRenderError,
        match="native module singleton",
    ):
        module._runtime_dependency_value(
            _PYDANTIC_UNDEFINED_DEPENDENCY,
            set(),
        )


def test_native_type_member_dependency_is_exactly_owner_bound() -> None:
    module = _module()

    descriptor = module._runtime_dependency_value(object.__str__, set())

    assert descriptor["descriptor_kind"] == "NATIVE_TYPE_MEMBER"
    assert descriptor["identity"] == "builtins.object.__str__"
    assert descriptor["python_runtime"] == module._python_runtime_descriptor()


def test_unknown_opaque_runtime_dependency_remains_fail_closed() -> None:
    module = _module()

    with pytest.raises(
        module.FamilySchemaRenderError,
        match="unsupported opaque runtime dependency",
    ):
        module._runtime_dependency_value(_OPAQUE_RUNTIME_DEPENDENCY, set())


def test_dataclass_runtime_type_attributes_are_material_dependencies() -> None:
    module = _module()
    original = _DataclassDependency.VERSION
    baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_dataclass_class_attribute),
    )
    try:
        _DataclassDependency.VERSION = "DATACLASS_TWO"
        changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_dataclass_class_attribute),
        )
    finally:
        _DataclassDependency.VERSION = original

    assert baseline.bindings[0].implementation_sha256 != (changed.bindings[0].implementation_sha256)


def test_runtime_dependency_alias_topology_is_material() -> None:
    module = _module()
    global _PARTIAL_ALIAS_PAIR
    baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_partial_alias_topology),
    )
    try:
        _PARTIAL_ALIAS_PAIR = (
            functools.partial(int, base=10),
            functools.partial(int, base=10),
        )
        changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_partial_alias_topology),
        )
    finally:
        _PARTIAL_ALIAS_PAIR = (_ALIASED_PARTIAL, _ALIASED_PARTIAL)

    assert baseline.bindings[0].implementation_sha256 != (changed.bindings[0].implementation_sha256)


def test_runtime_dependency_cycles_are_closed_and_topology_bound() -> None:
    module = _module()
    global _CYCLIC_DEPENDENCY
    baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_cyclic_dependency),
    )
    try:
        _CYCLIC_DEPENDENCY = _DOUBLE_CYCLE_LEFT
        changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_cyclic_dependency),
        )
    finally:
        _CYCLIC_DEPENDENCY = _SINGLE_CYCLE

    assert baseline.bindings[0].implementation_sha256 != (changed.bindings[0].implementation_sha256)


def test_aggregate_class_bases_attributes_and_descriptors_are_material() -> None:
    module = _module()
    bindings = _complete_callable_bindings(aggregate=_ReviewedMutableAggregate)
    aggregate_index = next(index for index, binding in enumerate(bindings) if type(binding.implementation) is type)
    baseline = module._bind_implementation_evidence_for_test(bindings)
    original_base_version = _MutableAggregateBase.BASE_VERSION
    original_class_version = _ReviewedMutableAggregate.CLASS_VERSION
    original_staticmethod = vars(_ReviewedMutableAggregate)["normalize"]
    original_classmethod = vars(_ReviewedMutableAggregate)["versioned"]
    try:
        _MutableAggregateBase.BASE_VERSION = "BASE_TWO"
        base_changed = module._bind_implementation_evidence_for_test(bindings)
        _MutableAggregateBase.BASE_VERSION = original_base_version

        _ReviewedMutableAggregate.CLASS_VERSION = "CLASS_TWO"
        class_changed = module._bind_implementation_evidence_for_test(bindings)
        _ReviewedMutableAggregate.CLASS_VERSION = original_class_version

        _ReviewedMutableAggregate.normalize = staticmethod(_helper_version_two)
        static_changed = module._bind_implementation_evidence_for_test(bindings)
        _ReviewedMutableAggregate.normalize = original_staticmethod

        _ReviewedMutableAggregate.versioned = classmethod(_helper_version_two)
        classmethod_changed = module._bind_implementation_evidence_for_test(bindings)
    finally:
        _MutableAggregateBase.BASE_VERSION = original_base_version
        _ReviewedMutableAggregate.CLASS_VERSION = original_class_version
        _ReviewedMutableAggregate.normalize = original_staticmethod
        _ReviewedMutableAggregate.versioned = original_classmethod

    baseline_hash = baseline.bindings[aggregate_index].implementation_sha256
    for changed in (
        base_changed,
        class_changed,
        static_changed,
        classmethod_changed,
    ):
        assert changed.bindings[aggregate_index].implementation_sha256 != baseline_hash


def test_module_source_and_native_binary_dependencies_are_materially_bound() -> None:
    module = _module()
    composition = compose_family_schema_v3()

    native_bound = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_module_attribute),
        composition,
    )
    assert len(native_bound.bindings[0].implementation_sha256) == 64
    math_artifact = module._module_artifact_descriptor(math)
    assert math_artifact["artifact_kind"] in {
        "BUILT_IN",
        "FROZEN",
        "NATIVE_OR_BYTECODE_BINARY",
    }
    if math_artifact["artifact_kind"] == "NATIVE_OR_BYTECODE_BINARY":
        assert len(math_artifact["binary_sha256"]) == 64
    else:
        assert math_artifact["python_runtime"]
    assert "origin" not in math_artifact

    hashlib_artifact = module._module_artifact_descriptor(hashlib)
    implementation_imports = {item["module_name"]: item for item in hashlib_artifact["imported_modules"]}
    assert "_hashlib" in implementation_imports
    hashlib_backend = implementation_imports["_hashlib"]
    if hashlib_backend["artifact_kind"] == "NATIVE_OR_BYTECODE_BINARY":
        assert len(hashlib_backend["binary_sha256"]) == 64
    else:
        assert hashlib_backend["python_runtime"]

    source_baseline = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_hashed_module),
        composition,
    )
    original_source = _MUTABLE_SOURCE_LOADER.source
    try:
        _MUTABLE_SOURCE_LOADER.source = "def transform(value):\n    return ('changed', value)\n"
        source_changed = module._bind_implementation_evidence_for_test(
            _complete_callable_bindings(_uses_hashed_module),
            composition,
        )
    finally:
        _MUTABLE_SOURCE_LOADER.source = original_source
    assert source_baseline.bindings[0].implementation_sha256 != (source_changed.bindings[0].implementation_sha256)


def test_sqlite_lazy_dump_import_cannot_change_module_descriptor_or_seal() -> None:
    module = _module()
    prior_submodule = sys.modules.pop("sqlite3.dump", None)
    prior_attribute = getattr(sqlite3, "dump", None)
    if hasattr(sqlite3, "dump"):
        delattr(sqlite3, "dump")
    try:
        descriptor_before = module._module_artifact_descriptor(sqlite3)
        seal_before = module.seal_family_schema_v3()
        connection = sqlite3.connect(":memory:", cached_statements=0)
        try:
            connection.execute('CREATE TABLE "probe" ("id" INTEGER) STRICT')
            tuple(connection.iterdump())
        finally:
            connection.close()
        assert "sqlite3.dump" in sys.modules
        descriptor_after = module._module_artifact_descriptor(sqlite3)
        seal_after = module.seal_family_schema_v3()
    finally:
        sys.modules.pop("sqlite3.dump", None)
        if prior_submodule is not None:
            sys.modules["sqlite3.dump"] = prior_submodule
        if prior_attribute is not None:
            setattr(sqlite3, "dump", prior_attribute)
        elif hasattr(sqlite3, "dump"):
            delattr(sqlite3, "dump")

    assert descriptor_after == descriptor_before
    assert seal_after.manifest_sha256 == seal_before.manifest_sha256


def test_unexecuted_conditional_import_cannot_become_a_late_dependency() -> None:
    module = _module()
    inactive_name = "quantos_test_inactive_branch_dependency"
    loader = _MutableSourceLoader()
    loader.source = (
        "if False:\n"
        f"    import {inactive_name} as branch_dependency\n"
        "else:\n"
        "    import math as branch_dependency\n"
    )
    conditional = ModuleType("quantos_test_conditional_import_module")
    conditional.__spec__ = ModuleSpec(
        conditional.__name__,
        loader,
        origin="quantos_test_conditional_import_module.py",
    )
    conditional.branch_dependency = math
    inactive = ModuleType(inactive_name)
    inactive.__spec__ = ModuleSpec(
        inactive_name,
        _MutableSourceLoader(),
        origin=f"{inactive_name}.py",
    )
    sys.modules.pop(inactive_name, None)
    try:
        before = module._module_artifact_descriptor(conditional)
        sys.modules[inactive_name] = inactive
        after = module._module_artifact_descriptor(conditional)
    finally:
        sys.modules.pop(inactive_name, None)

    assert after == before
    imported = {item["module_name"] for item in after["imported_modules"]}
    assert "math" in imported
    assert inactive_name not in imported


def test_descriptor_snapshot_memo_is_equivalent_and_does_not_cross_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    baseline = module._module_artifact_descriptor(_HASHED_MODULE)
    uncached = module._uncached_module_artifact_descriptor
    calls = 0

    def counted(module_value, dependency_depth):
        nonlocal calls
        calls += 1
        return uncached(module_value, dependency_depth)

    monkeypatch.setattr(module, "_uncached_module_artifact_descriptor", counted)
    with module._descriptor_snapshot():
        first = module._module_artifact_descriptor(_HASHED_MODULE)
        second = module._module_artifact_descriptor(_HASHED_MODULE)

    assert first is second
    assert first == baseline
    assert calls == 1
    assert module._ACTIVE_DESCRIPTOR_SNAPSHOT.get() is None

    original_source = _MUTABLE_SOURCE_LOADER.source
    try:
        _MUTABLE_SOURCE_LOADER.source = "def transform(value):\n    return ('new snapshot', value)\n"
        with module._descriptor_snapshot():
            changed = module._module_artifact_descriptor(_HASHED_MODULE)
    finally:
        _MUTABLE_SOURCE_LOADER.source = original_source

    assert changed != first
    assert calls == 2


def test_shared_descriptor_graph_keeps_one_full_material_value_and_exact_refs() -> None:
    module = _module()
    shared = {"binary_sha256": "a" * 64, "source": "reviewed"}

    encoded = module._deduplicated_descriptor_graph({"left": shared, "right": shared})

    assert encoded == {
        "left": {
            "shared_descriptor_node_id": "SHARED_DESCRIPTOR_NODE_0001",
            "value_kind": "MAPPING",
            "value": shared,
        },
        "right": {"shared_descriptor_node_ref": "SHARED_DESCRIPTOR_NODE_0001"},
    }
    baseline_sha256 = hashlib.sha256(canonical_module.canonical_json_bytes(encoded)).hexdigest()
    shared["source"] = "tampered"
    changed_sha256 = hashlib.sha256(
        canonical_module.canonical_json_bytes(module._deduplicated_descriptor_graph({"left": shared, "right": shared}))
    ).hexdigest()
    assert changed_sha256 != baseline_sha256


def test_real_quantos_canonical_module_dependency_can_be_source_closed() -> None:
    module = _module()

    evidence = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(_uses_quantos_canonical_module)
    )

    assert len(evidence.bindings) == 136


def test_unsealed_manifest_body_covers_every_required_exact_v3_input() -> None:
    module = _module()
    composition = compose_family_schema_v3()
    rendered = module.render_family_schema_v3(composition)
    evidence = module._bind_implementation_evidence_for_test(
        _complete_callable_bindings(),
        composition,
    )

    body = module._family_schema_manifest_body(
        composition,
        rendered,
        evidence,
    )

    assert "state" not in body
    assert body["manifest_version"] == "QUANTOS_FAMILY_SCHEMA_MANIFEST_V3"
    assert body["composition"] == module._canonical_manifest_value(composition)
    assert body["component_order"] == list(composition.component_order)

    renderer = body["renderer"]
    assert renderer["version"] == composition.renderer_grammar.renderer_version
    assert renderer["grammar"] == module._canonical_manifest_value(composition.renderer_grammar)
    assert renderer["sqlite_runtime"] == module._canonical_manifest_value(composition.renderer_grammar.sqlite_runtime)
    assert len(renderer["semantic_function_abis"]) == 9
    assert renderer["null_lowering"]
    assert renderer["related_lookup_lowering"]
    assert renderer["state_machine_lowering"]

    statement_catalog = body["statement_catalog"]
    assert len(statement_catalog) == 401
    for item, payload in zip(rendered, statement_catalog, strict=True):
        assert payload["ordinal"] == item.ordinal
        assert payload["sqlite_schema_sql"] == item.sqlite_schema_sql
        assert payload["statement_utf8_hex"] == item.statement_bytes.hex()
        assert payload["statement_sha256"] == item.statement_sha256

    implementation_evidence = body["implementation_evidence"]
    assert len(implementation_evidence["requirements"]) == 142
    assert len(implementation_evidence["bindings"]) == 136
    assert len(body["external_provider_trust_roots"]) == 6
    assert body["external_authority_claim_profiles"] == [
        module._canonical_manifest_value(item) for item in composition.external_authority_claim_profiles
    ]
    assert body["auxiliary_semantic_recipes"] == [
        module._canonical_manifest_value(item) for item in composition.auxiliary_semantic_recipes
    ]

    verifier_mappings = body["verifier_mappings"]
    assert len(verifier_mappings["structural_plans"]) == 228
    assert len(verifier_mappings["named_bindings"]) == 124
    assert len(verifier_mappings["registry"]) == 133
    assert body["traceability"] == {
        "resolved_contract_surfaces": [
            module._canonical_manifest_value(item) for item in composition.resolved_contract_surfaces
        ],
        "coverage": [module._canonical_manifest_value(item) for item in composition.coverage],
        "contract_derivations": [module._canonical_manifest_value(item) for item in composition.contract_derivations],
        "artifact_graph_derivations": [
            module._canonical_manifest_value(item) for item in composition.artifact_graph_derivations
        ],
    }
    assert body["composition_sha256"] == module._canonical_sha256(body["composition"])
    assert body["statement_catalog_sha256"] == module._canonical_sha256(statement_catalog)
    assert body["implementation_evidence_sha256"] == module._canonical_sha256(implementation_evidence)
    assert body["verifier_mapping_sha256"] == module._canonical_sha256(verifier_mappings)


def test_module_name_without_source_runtime_or_binary_identity_fails_closed() -> None:
    module = _module()

    with pytest.raises(
        module.FamilySchemaRenderError,
        match="module implementation artifact has no import specification",
    ):
        module._bind_implementation_evidence_for_test(_complete_callable_bindings(_uses_unclosed_module))


def test_public_manifest_seal_is_zero_argument_and_closure_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    bindings = _complete_callable_bindings()

    assert not hasattr(module, "_seal_family_schema_v3_from_registry")
    assert not hasattr(module, "_public_sealer")
    assert not hasattr(module, "_build_public_sealer")
    assert not hasattr(module, "_family_schema_manifest_payload")

    with pytest.raises(TypeError, match="seal_family_schema_v3"):
        module.FamilySchemaManifest()
    sealed = module.seal_family_schema_v3()
    assert sealed.state == "SEALED"
    assert len(sealed.implementation_evidence.bindings) == 136
    assert len(sealed.rendered_objects) == 401
    with pytest.raises(TypeError):
        module.seal_family_schema_v3(bindings)

    assert len(module._CANONICAL_IMPLEMENTATION_REGISTRY) == 136
    assert (
        sum(
            binding.target_kind is fs.ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION
            for binding in module._CANONICAL_IMPLEMENTATION_REGISTRY
        )
        == 9
    )
    assert (
        sum(
            binding.target_kind is fs.ImplementationTargetKind.NAMED_VERIFIER
            for binding in module._CANONICAL_IMPLEMENTATION_REGISTRY
        )
        == 116
    )
    assert (
        sum(
            binding.target_kind is fs.ImplementationTargetKind.STRUCTURAL_HANDLER
            for binding in module._CANONICAL_IMPLEMENTATION_REGISTRY
        )
        == 11
    )
    monkeypatch.setattr(
        module,
        "_CANONICAL_IMPLEMENTATION_REGISTRY",
        module._CANONICAL_IMPLEMENTATION_REGISTRY[:-1],
    )
    resealed = module.seal_family_schema_v3()
    assert resealed.manifest_sha256 == sealed.manifest_sha256

    forged = object.__new__(module.FamilySchemaManifest)
    object.__setattr__(forged, "_seal_token", object())
    with pytest.raises(module.FamilySchemaUnsealedError, match="UNSEALED"):
        _ = forged.state
    with pytest.raises(AttributeError):
        object.__setattr__(forged, "_sealed_state", "SEALED")


def test_verifier_mapping_runtime_tamper_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    from services.research.experiment_registry import (
        family_schema_verifier_registry as verifier_registry,
    )

    monkeypatch.setattr(
        verifier_registry,
        "FAMILY_SCHEMA_VERIFIER_BINDINGS",
        verifier_registry.FAMILY_SCHEMA_VERIFIER_BINDINGS[:-1],
    )

    with pytest.raises(
        module.FamilySchemaUnsealedError,
        match="UNSEALED.*verifier mapping",
    ):
        module._verifier_mapping_payload()


def test_verifier_mapping_snapshot_cannot_be_advanced_with_live_registry_tamper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    from services.research.experiment_registry import (
        family_schema_verifier_registry as verifier_registry,
    )

    monkeypatch.setattr(
        verifier_registry,
        "FAMILY_SCHEMA_VERIFIER_BINDINGS",
        verifier_registry.FAMILY_SCHEMA_VERIFIER_BINDINGS[:-1],
    )
    synchronized_tamper = module._read_verifier_mapping_payload()
    monkeypatch.setattr(
        module,
        "_CANONICAL_VERIFIER_MAPPING_PAYLOAD",
        synchronized_tamper,
        raising=False,
    )

    with pytest.raises(
        module.FamilySchemaUnsealedError,
        match="UNSEALED.*verifier mapping",
    ):
        module._verifier_mapping_payload()
    assert type(module._CANONICAL_VERIFIER_MAPPING_BYTES) is bytes


def test_verifier_mapping_reader_cannot_be_rebound_to_hide_live_tamper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    from services.research.experiment_registry import (
        family_schema_verifier_registry as verifier_registry,
    )

    reviewed_payload = module._verifier_mapping_payload()
    monkeypatch.setattr(
        verifier_registry,
        "FAMILY_SCHEMA_VERIFIER_BINDINGS",
        verifier_registry.FAMILY_SCHEMA_VERIFIER_BINDINGS[:-1],
    )
    monkeypatch.setattr(
        module,
        "_read_verifier_mapping_payload",
        lambda: reviewed_payload,
    )

    with pytest.raises(
        module.FamilySchemaUnsealedError,
        match="UNSEALED.*verifier mapping",
    ):
        module._verifier_mapping_payload()


def test_partial_semantic_function_registry_cannot_be_promoted_to_a_seal() -> None:
    module = _module()
    composition = compose_family_schema_v3()
    semantic_only = tuple(
        binding
        for binding in _complete_callable_bindings()
        if binding.target_kind is fs.ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION
    )

    assert len(semantic_only) == 9
    with pytest.raises(module.FamilySchemaUnsealedError, match="UNSEALED.*127"):
        module._bind_canonical_production_implementation_registry(
            semantic_only,
            composition,
        )


def test_aggregate_abi_rejects_a_scalar_function_implementation() -> None:
    module = _module()
    composition = compose_family_schema_v3()
    bindings = _complete_callable_bindings()
    aggregate_index = next(
        index
        for index, binding in enumerate(bindings)
        if binding.target_kind is fs.ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION
        and type(binding.implementation) is type
    )
    tampered = (
        *bindings[:aggregate_index],
        dataclasses.replace(
            bindings[aggregate_index],
            implementation=_reviewed_test_implementation,
        ),
        *bindings[aggregate_index + 1 :],
    )

    with pytest.raises(module.FamilySchemaRenderError, match="aggregate semantic"):
        module._bind_implementation_evidence_for_test(tampered, composition)


def test_callable_binding_rejects_builtin_or_external_trust_root_targets() -> None:
    module = _module()
    requirements = compose_family_schema_v3().implementation_evidence_requirements
    internal = requirements.entries[0]
    external = next(
        item
        for item in requirements.entries
        if item.target_kind is fs.ImplementationTargetKind.EXTERNAL_PROVIDER_TRUST_ROOT
    )

    with pytest.raises(TypeError, match="Python function"):
        module._ImplementationCallableBinding(
            target_kind=internal.target_kind,
            target_id=internal.target_id,
            implementation_id=internal.implementation_id,
            implementation=len,
        )
    with pytest.raises(ValueError, match="external provider"):
        module._ImplementationCallableBinding(
            target_kind=external.target_kind,
            target_id=external.target_id,
            implementation_id="forbidden.external.implementation",
            implementation=_reviewed_test_implementation,
        )
