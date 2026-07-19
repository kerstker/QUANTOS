"""Pass-M RED contract for the private sealed verifier registry.

The Pass-C component files declare schema metadata only.  This test freezes the
complete verifier inventory independently, then requires a private descriptor
module to resolve every structural and named obligation without prematurely
exposing the final runtime schema, migration, or verifier API.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
from collections import defaultdict
from types import ModuleType
from typing import Any

import pytest

from services.research.experiment_registry.family_schema_c4 import (
    C4_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_c5_c6 import (
    C5_SCHEMA_COMPONENT,
    C6_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_c7 import (
    C7_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_c8 import (
    C8_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_c9 import (
    C9_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_composition import (
    compose_family_schema_v3,
)
from services.research.experiment_registry.family_schema_spec import (
    FAMILY_AUTHORITY_GENESIS_TABLE_SPEC,
)


_REGISTRY_MODULE = (
    "services.research.experiment_registry.family_schema_verifier_registry"
)
_COMPONENTS = (
    C4_SCHEMA_COMPONENT,
    C5_SCHEMA_COMPONENT,
    C6_SCHEMA_COMPONENT,
    C7_SCHEMA_COMPONENT,
    C8_SCHEMA_COMPONENT,
    C9_SCHEMA_COMPONENT,
)
_GENESIS_COMPONENT_ID = "FAMILY_AUTHORITY_GENESIS_V1"
_FORBIDDEN_DEFAULT_OPERATIONS = {
    "ALLOW",
    "ASSUME_VALID",
    "DEFAULT_PASS",
    "NOOP",
    "PASS",
    "SCHEMA_ONLY",
    "SKIP",
}
_FORBIDDEN_API_PARAMETERS = {
    "callback",
    "dynamic_registry",
    "plugin",
    "provider_registry",
    "provider_resolver",
    "registry",
    "registry_override",
    "resolver",
    "semantic_verifiers",
    "skip_semantics",
    "verifier_callback",
    "verifier_registry",
    "verifiers",
}


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _rule_key(
    component_id: str,
    surface_kind: str,
    table_name: str,
    rule_id: str,
) -> str:
    return f"{component_id}:{surface_kind}:{table_name}:{rule_id}"


def _rule_bindings() -> tuple[tuple[str, Any], ...]:
    bindings: list[tuple[str, Any]] = []
    for rule in FAMILY_AUTHORITY_GENESIS_TABLE_SPEC.verification_rules:
        bindings.append(
            (
                _rule_key(
                    _GENESIS_COMPONENT_ID,
                    "TABLE",
                    FAMILY_AUTHORITY_GENESIS_TABLE_SPEC.table_name,
                    rule.rule_id,
                ),
                rule,
            )
        )
    for component in _COMPONENTS:
        for table in component.tables:
            for rule in table.verification_rules:
                bindings.append(
                    (
                        _rule_key(
                            component.component_id,
                            "TABLE",
                            table.table_name,
                            rule.rule_id,
                        ),
                        rule,
                    )
                )
        for extension in component.extensions:
            for rule in extension.verification_rules:
                bindings.append(
                    (
                        _rule_key(
                            component.component_id,
                            "EXTENSION",
                            extension.target_table,
                            rule.rule_id,
                        ),
                        rule,
                    )
                )
    return tuple(bindings)


def _continuity_bindings() -> tuple[tuple[str, Any], ...]:
    return tuple(
        (
            _rule_key(
                component.component_id,
                "CONTINUITY",
                extension.target_table,
                continuity.verifier_code,
            ),
            continuity,
        )
        for component in _COMPONENTS
        for extension in component.extensions
        for continuity in extension.external_continuity_rules
    )


def _graph_bindings() -> tuple[tuple[str, Any], ...]:
    return tuple(
        (
            _rule_key(
                component.component_id,
                "GRAPH",
                graph.derivation_id,
                graph.verifier_code,
            ),
            graph,
        )
        for component in _COMPONENTS
        for graph in component.artifact_graph_derivations
    )


def _provider_keys() -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            requirement.provider_contract_type,
            requirement.provider_capability,
        )
        for component in _COMPONENTS
        for requirement in component.required_external_provider_capabilities
    )


def _provider_registry_key(provider_key: tuple[str, str]) -> str:
    return "|".join(provider_key)


def _expected_named_bindings() -> tuple[tuple[str, str], ...]:
    coded_rules = tuple(
        (binding_key, rule.verifier_code)
        for binding_key, rule in _rule_bindings()
        if rule.verifier_code is not None
    )
    continuity = tuple(
        (binding_key, spec.verifier_code)
        for binding_key, spec in _continuity_bindings()
    )
    graphs = tuple(
        (binding_key, spec.verifier_code)
        for binding_key, spec in _graph_bindings()
    )
    return (*coded_rules, *continuity, *graphs)


def _registry_module() -> ModuleType:
    try:
        return importlib.import_module(_REGISTRY_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != _REGISTRY_MODULE:
            raise
        pytest.fail(
            "Pass-M RED: private family-schema verifier registry is not implemented",
            pytrace=False,
        )


def _frozen_descriptor(value: object, context: str) -> None:
    assert dataclasses.is_dataclass(value), f"{context} must be a frozen dataclass descriptor"
    params = getattr(type(value), "__dataclass_params__", None)
    assert params is not None and params.frozen, f"{context} must be immutable"
    assert hasattr(type(value), "__slots__"), f"{context} must use slots"


def _assert_no_callable_values(value: object, context: str) -> None:
    if dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            _assert_no_callable_values(getattr(value, field.name), f"{context}.{field.name}")
        return
    if isinstance(value, tuple):
        for index, item in enumerate(value):
            _assert_no_callable_values(item, f"{context}[{index}]")
        return
    if isinstance(value, dict):
        pytest.fail(f"{context} must not expose a mutable dictionary")
    assert not callable(value), f"{context} must contain descriptions, not executable callables"


def test_pass_m_verifier_inventory_freeze_is_exact() -> None:
    rules = _rule_bindings()
    structural = tuple(item for item in rules if item[1].verifier_code is None)
    coded = tuple(item for item in rules if item[1].verifier_code is not None)
    continuity = _continuity_bindings()
    graphs = _graph_bindings()
    providers = _provider_keys()
    named_bindings = _expected_named_bindings()
    unique_codes = {code for _, code in named_bindings}
    registry_keys = {
        *(("VERIFIER_CODE", code) for code in unique_codes),
        *(("EXTERNAL_PROVIDER", _provider_registry_key(key)) for key in providers),
    }

    assert len(rules) == 341
    assert len(structural) == 228
    assert len(coded) == 113
    assert len(continuity) == 9
    assert len(graphs) == 2
    assert len(named_bindings) == 124
    assert len(unique_codes) == 116
    assert len(providers) == len(set(providers)) == 6
    assert len(registry_keys) == 122
    assert all(rule.structural_source is not None for _, rule in structural)


def test_every_unnamed_rule_has_one_nonempty_fail_closed_structural_plan() -> None:
    module = _registry_module()
    expected_rule_keys = {
        binding_key
        for binding_key, rule in _rule_bindings()
        if rule.verifier_code is None
    }
    plans = module.FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS

    assert type(plans) is tuple
    assert len(plans) == 228
    assert {plan.rule_key for plan in plans} == expected_rule_keys
    assert len({plan.rule_key for plan in plans}) == len(plans)

    for plan in plans:
        _frozen_descriptor(plan, plan.rule_key)
        assert type(plan.read_tables) is tuple and plan.read_tables
        assert type(plan.read_columns) is tuple and plan.read_columns
        assert all(
            type(table_name) is str and table_name
            for table_name in plan.read_tables
        )
        assert all(
            type(column_name) is str
            and "." in column_name
            and column_name.split(".", 1)[0] in plan.read_tables
            for column_name in plan.read_columns
        )
        operation = _enum_value(plan.operation)
        assert operation and operation not in _FORBIDDEN_DEFAULT_OPERATIONS
        assert plan.fail_closed is True
        assert plan.default_pass is False


def test_structural_plan_constructor_is_independent_of_registry_entry_fields() -> None:
    module = _registry_module()
    plan = module.StructuralVerificationPlan(
        rule_key="component:TABLE:table:rule",
        read_tables=("table",),
        read_columns=("column",),
        operation=module.StructuralVerificationOperation.RECOMPUTE_ROW_HASH,
    )
    assert plan.rule_key == "component:TABLE:table:rule"
    assert plan.fail_closed is True


def test_registry_has_exact_116_codes_11_handlers_and_six_provider_trust_roots() -> None:
    module = _registry_module()
    unique_codes = {code for _, code in _expected_named_bindings()}
    expected_keys = {
        *(("VERIFIER_CODE", code) for code in unique_codes),
        *(
            ("STRUCTURAL_HANDLER", _enum_value(operation))
            for operation in module.StructuralVerificationOperation
        ),
        *(
            (
                "EXTERNAL_PROVIDER",
                _provider_registry_key(provider_key),
            )
            for provider_key in _provider_keys()
        ),
    }
    registry = module.FAMILY_SCHEMA_VERIFIER_REGISTRY

    assert type(registry) is tuple
    assert len(registry) == 133
    actual_keys = {
        (_enum_value(entry.key_kind), entry.key)
        for entry in registry
    }
    assert actual_keys == expected_keys
    assert len(actual_keys) == len(registry)
    for entry in registry:
        _frozen_descriptor(entry, f"registry[{entry.key!r}]")
        assert type(entry.implementation_requirement_id) is str
        assert entry.implementation_requirement_id
        if _enum_value(entry.key_kind) == "EXTERNAL_PROVIDER":
            assert entry.implementation_id is None
            assert _enum_value(entry.evidence_policy) == (
                "EXCLUDED_DEPLOYMENT_TRUST_ROOT"
            )
        else:
            assert type(entry.implementation_id) is str and entry.implementation_id
            assert _enum_value(entry.evidence_policy) == "REQUIRE_ACTUAL_SHA256"
        _assert_no_callable_values(entry, f"registry[{entry.key!r}]")


def test_named_bindings_are_exact_and_only_reviewed_groups_share_implementation() -> None:
    module = _registry_module()
    expected = set(_expected_named_bindings())
    bindings = module.FAMILY_SCHEMA_VERIFIER_BINDINGS

    assert type(bindings) is tuple
    assert len(bindings) == 124
    assert {(item.binding_key, item.verifier_code) for item in bindings} == expected
    assert len({item.binding_key for item in bindings}) == len(bindings)

    expected_aliases = {
        "c4.verify_subject_grant_required_v1": {
            _rule_key(
                "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1",
                "TABLE",
                table_name,
                f"{table_name}_grant_required",
            )
            for table_name in (
                "research_questions",
                "research_question_aliases",
                "strategy_lineage_roots",
                "strategy_lineage_nodes",
                "strategy_config_lineage_bindings",
                "selection_scopes",
                "selection_scope_aliases",
            )
        },
        "c8.verify_family_assessment_v1": {
            _rule_key(
                "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
                "TABLE",
                "data_snooping_assessments",
                "assessment_semantic_recomputation",
            ),
            _rule_key(
                "C9_ARTIFACT_PUBLICATION_RECEIPTS_V1",
                "TABLE",
                "artifact_publication_receipts",
                "publication_receipt_c8_semantic_recomputation",
            ),
        },
        "c9.verify_complete_acyclic_projection_v1": {
            _rule_key(
                "C9_ARTIFACT_PUBLICATION_RECEIPTS_V1",
                "TABLE",
                "artifact_publication_receipts",
                "publication_receipt_projection_totality",
            ),
            _rule_key(
                "C9_ARTIFACT_PUBLICATION_RECEIPTS_V1",
                "GRAPH",
                "c9.publication_projection.exact_byte_graph",
                "c9.verify_complete_acyclic_projection_v1",
            ),
        },
    }
    by_code: dict[str, list[Any]] = defaultdict(list)
    for binding in bindings:
        _frozen_descriptor(binding, binding.binding_key)
        by_code[binding.verifier_code].append(binding)

    actual_aliases = {
        code: {item.binding_key for item in items}
        for code, items in by_code.items()
        if len(items) > 1
    }
    assert actual_aliases == expected_aliases
    for code, items in by_code.items():
        implementation_ids = {item.implementation_id for item in items}
        assert len(implementation_ids) == 1, f"{code} aliases must share one implementation"


def test_every_named_rule_has_unique_nonempty_existing_owner_columns() -> None:
    composed_tables = {
        table.table_name: {column.name for column in table.columns}
        for table in compose_family_schema_v3().tables
    }
    named = tuple(
        (binding_key, rule)
        for binding_key, rule in _rule_bindings()
        if rule.verifier_code is not None
    )
    assert len(named) == 113
    for binding_key, rule in named:
        table_name = binding_key.split(":", 3)[2]
        assert rule.columns, binding_key
        assert len(set(rule.columns)) == len(rule.columns), binding_key
        assert set(rule.columns).issubset(composed_tables[table_name]), binding_key


def test_private_registry_does_not_expose_runtime_schema_or_verifier_surface() -> None:
    module = _registry_module()
    for forbidden in (
        "activate_family_schema_v3",
        "render_family_schema_v3",
        "verify_family_authority",
        "require_family_schema_v3",
    ):
        assert not hasattr(module, forbidden)


def test_registry_module_has_no_dynamic_discovery_or_public_override_api() -> None:
    module = _registry_module()
    source = inspect.getsource(module)
    tree = ast.parse(source)
    forbidden_modules = {"importlib", "pkg_resources"}
    forbidden_calls = {
        "entry_points",
        "getenv",
        "import_module",
        "iter_entry_points",
        "load",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not ({alias.name.split(".", 1)[0] for alias in node.names} & forbidden_modules)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or node.module.split(".", 1)[0] not in forbidden_modules
        elif isinstance(node, ast.Call):
            called_name = None
            if isinstance(node.func, ast.Name):
                called_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                called_name = node.func.attr
            assert called_name not in forbidden_calls
        elif isinstance(node, ast.Attribute):
            assert not (
                isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and node.attr == "environ"
            )

    public_names = {name for name in vars(module) if not name.startswith("_")}
    assert not {
        name
        for name in public_names
        if any(token in name.lower() for token in ("register", "override", "replace"))
        and any(token in name.lower() for token in ("registry", "verifier", "provider"))
    }
