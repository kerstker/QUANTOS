"""Adversarial external-authority rules for exact-v3 Pass C slice C6.

C6 adds no business table.  It freezes provider-qualified columns plus typed
verification obligations inside extensions owned by C4/C7/C8/C9.  Merely
listing a verifier name next to an extension is insufficient for Pass M.
"""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
import importlib
from itertools import product
from typing import Any, Mapping, Sequence

import pytest

import quantos.contracts.research as research_contracts


MODULE = "services.research.experiment_registry.family_schema_c5_c6"
SPEC_MODULE = "services.research.experiment_registry.family_schema_spec"


SURFACE_COLUMNS = {
    "data_snooping_declarations": (
        "observed_pit_authority_id",
        "observed_pit_handle_id",
        "observed_pit_content_hash",
        "knowledge_time_policy_id",
        "knowledge_time_policy_version",
        "observed_pit_authority_head",
        "code_manifest_policy_id",
        "code_manifest_policy_version",
        "code_manifest_policy_hash",
        "code_manifest_evidence_hash",
        "code_manifest_authority_id",
        "code_manifest_authority_head",
    ),
    "global_oos_exposures": (
        "selection_scope_id",
        "selection_decision_id",
        "observed_pit_authority_id",
        "observed_pit_handle_id",
        "observed_pit_content_hash",
        "observed_pit_authority_head",
        "dataset_id",
        "dataset_version",
        "universe_id",
        "universe_hash",
        "ordered_period_calendar_hash",
        "knowledge_time_policy_id",
        "knowledge_time_policy_version",
        "label_horizon",
        "purge_periods",
        "embargo_periods",
        "cost_contract_hash",
        "benchmark_contract_hash",
        "exposure_fingerprint",
        "exposure_version",
    ),
    "data_snooping_oos_releases": (
        "authority_instance_id",
        "attempt_id",
        "started_attempt_event_id",
        "family_id",
        "oos_exposure_id",
        "exposure_fingerprint",
        "exposure_authority_id",
        "exposure_authority_head",
        "start_execution_attestation_hash",
        "execution_attestation_authority_id",
        "execution_attestation_authority_head",
        "immutable_artifact_lease_id",
        "immutable_artifact_lease_hash",
        "immutable_artifact_lease_authority_id",
        "immutable_artifact_lease_authority_head",
    ),
    "data_snooping_family_closures": (
        "observed_pit_authority_id",
        "observed_pit_handle_id",
        "observed_pit_content_hash",
        "knowledge_time_policy_id",
        "knowledge_time_policy_version",
        "observed_pit_authority_head",
        "oos_exposure_id",
        "exposure_fingerprint",
        "exposure_authority_id",
        "exposure_authority_head",
        "code_manifest_policy_id",
        "code_manifest_policy_version",
        "code_manifest_policy_hash",
        "code_manifest_evidence_hash",
        "code_manifest_authority_id",
        "code_manifest_authority_head",
        "start_execution_attestation_hash",
        "terminal_execution_attestation_hash",
        "execution_attestation_authority_id",
        "start_execution_attestation_authority_head",
        "terminal_execution_attestation_authority_head",
        "immutable_artifact_lease_id",
        "immutable_artifact_lease_hash",
        "immutable_artifact_lease_authority_id",
        "immutable_artifact_lease_authority_head",
    ),
    "artifact_publication_receipts": (
        "closure_manifest_hash",
        "observed_pit_authority_id",
        "observed_pit_handle_id",
        "observed_pit_content_hash",
        "knowledge_time_policy_id",
        "knowledge_time_policy_version",
        "observed_pit_authority_head",
        "oos_exposure_id",
        "exposure_fingerprint",
        "exposure_authority_id",
        "exposure_authority_head",
        "code_manifest_policy_id",
        "code_manifest_policy_version",
        "code_manifest_policy_hash",
        "code_manifest_evidence_hash",
        "code_manifest_authority_id",
        "code_manifest_authority_head",
        "start_execution_attestation_hash",
        "terminal_execution_attestation_hash",
        "execution_attestation_authority_id",
        "start_execution_attestation_authority_head",
        "terminal_execution_attestation_authority_head",
        "immutable_artifact_lease_id",
        "immutable_artifact_lease_hash",
        "immutable_artifact_lease_authority_id",
        "immutable_artifact_lease_authority_head",
    ),
}

CORE_FIELDS = {
    "data_snooping_declarations": (),
    "global_oos_exposures": (
        "selection_scope_id",
        "selection_decision_id",
        "exposure_fingerprint",
    ),
    "data_snooping_oos_releases": (
        "authority_instance_id",
        "attempt_id",
        "started_attempt_event_id",
        "family_id",
        "oos_exposure_id",
    ),
    "data_snooping_family_closures": (),
    "artifact_publication_receipts": ("closure_manifest_hash",),
}


def _bindings(*pairs: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return pairs


EXTERNAL_RULES = {
    "data_snooping_declarations": {
        "VERIFY_OBSERVED_PIT_DECLARATION_BINDING": (
            "OBSERVED_PIT",
            "ObservedPitDatasetHandle",
            "observed_pit_dataset_handle.verify",
            _bindings(
                ("observed_pit_authority_id", "authority_id"),
                ("observed_pit_handle_id", "handle_id"),
                ("observed_pit_content_hash", "content_hash"),
                ("knowledge_time_policy_id", "knowledge_time_policy_id"),
                ("knowledge_time_policy_version", "knowledge_time_policy_version"),
                ("observed_pit_authority_head", "authority_head"),
            ),
            False,
        ),
        "VERIFY_CODE_MANIFEST_DECLARATION_BINDING": (
            "CODE_MANIFEST",
            "CodeManifestEvidence",
            "code_manifest_evidence.verify",
            _bindings(
                ("code_manifest_policy_id", "policy_ref.policy_id"),
                ("code_manifest_policy_version", "policy_ref.policy_version"),
                ("code_manifest_policy_hash", "policy_ref.policy_hash"),
                ("code_manifest_evidence_hash", "evidence_hash"),
                ("code_manifest_authority_id", "authority_id"),
                ("code_manifest_authority_head", "authority_head"),
            ),
            False,
        ),
    },
    "global_oos_exposures": {
        "VERIFY_OBSERVED_PIT_EXPOSURE_BINDING": (
            "OBSERVED_PIT",
            "ObservedPitDatasetHandle",
            "observed_pit_dataset_handle.verify",
            _bindings(
                ("observed_pit_authority_id", "authority_id"),
                ("observed_pit_handle_id", "handle_id"),
                ("observed_pit_content_hash", "content_hash"),
                ("observed_pit_authority_head", "authority_head"),
                ("dataset_id", "dataset_id"),
                ("dataset_version", "dataset_version"),
                ("universe_id", "universe_id"),
                ("universe_hash", "universe_hash"),
                ("ordered_period_calendar_hash", "ordered_period_calendar_hash"),
                ("knowledge_time_policy_id", "knowledge_time_policy_id"),
                ("knowledge_time_policy_version", "knowledge_time_policy_version"),
                ("label_horizon", "label_horizon"),
                ("purge_periods", "purge_periods"),
                ("embargo_periods", "embargo_periods"),
                ("cost_contract_hash", "cost_contract_hash"),
                ("benchmark_contract_hash", "benchmark_contract_hash"),
            ),
            False,
        ),
    },
    "data_snooping_oos_releases": {
        "VERIFY_OOS_RELEASE_EXPOSURE_BINDING": (
            "OOS_EXPOSURE",
            "C4GlobalOosExposureAuthorityRow",
            "c4.global_oos_exposure.verify",
            _bindings(
                ("oos_exposure_id", "oos_exposure_id"),
                ("exposure_fingerprint", "exposure_fingerprint"),
                ("exposure_authority_id", "authority_instance_id"),
                ("exposure_authority_head", "record_hash"),
            ),
            False,
        ),
        "VERIFY_START_EXECUTION_ATTESTATION": (
            "EXECUTION_ATTESTATION",
            "ExecutionCodeAttestation",
            "execution_code_attestation.verify",
            _bindings(
                ("start_execution_attestation_hash", "attestation_hash"),
                ("execution_attestation_authority_id", "authority_id"),
                ("execution_attestation_authority_head", "authority_head"),
            ),
            False,
        ),
        "VERIFY_IMMUTABLE_ARTIFACT_LEASE": (
            "IMMUTABLE_LEASE",
            "ImmutableArtifactLease",
            "immutable_artifact_lease.verify",
            _bindings(
                ("immutable_artifact_lease_id", "lease_id"),
                ("immutable_artifact_lease_hash", "lease_hash"),
                ("immutable_artifact_lease_authority_id", "authority_id"),
                ("immutable_artifact_lease_authority_head", "authority_head"),
            ),
            False,
        ),
    },
}

_OBSERVED_REFERENCE = EXTERNAL_RULES["data_snooping_declarations"]["VERIFY_OBSERVED_PIT_DECLARATION_BINDING"]
_CODE_REFERENCE = EXTERNAL_RULES["data_snooping_declarations"]["VERIFY_CODE_MANIFEST_DECLARATION_BINDING"]
_OOS_REFERENCE = EXTERNAL_RULES["data_snooping_oos_releases"]["VERIFY_OOS_RELEASE_EXPOSURE_BINDING"]
_START_REFERENCE = EXTERNAL_RULES["data_snooping_oos_releases"]["VERIFY_START_EXECUTION_ATTESTATION"]
_LEASE_REFERENCE = EXTERNAL_RULES["data_snooping_oos_releases"]["VERIFY_IMMUTABLE_ARTIFACT_LEASE"]
_CLOSURE_START_REFERENCE = (
    "EXECUTION_ATTESTATION",
    "ExecutionCodeAttestation",
    "execution_code_attestation.verify",
    _bindings(
        ("start_execution_attestation_hash", "attestation_hash"),
        ("execution_attestation_authority_id", "authority_id"),
        ("start_execution_attestation_authority_head", "authority_head"),
    ),
    True,
)
_TERMINAL_REFERENCE = (
    "EXECUTION_ATTESTATION",
    "ExecutionCodeAttestation",
    "execution_code_attestation.verify",
    _bindings(
        ("terminal_execution_attestation_hash", "attestation_hash"),
        ("execution_attestation_authority_id", "authority_id"),
        ("terminal_execution_attestation_authority_head", "authority_head"),
    ),
    True,
)

EXTERNAL_RULES.update(
    {
        "data_snooping_family_closures": {
            "VERIFY_CLOSURE_OBSERVED_PIT_BINDING": _OBSERVED_REFERENCE,
            "VERIFY_CLOSURE_OOS_EXPOSURE_BINDING": (*_OOS_REFERENCE[:-1], True),
            "VERIFY_CLOSURE_CODE_MANIFEST_BINDING": _CODE_REFERENCE,
            "VERIFY_CLOSURE_START_EXECUTION_ATTESTATION": (
                _CLOSURE_START_REFERENCE
            ),
            "VERIFY_CLOSURE_TERMINAL_EXECUTION_ATTESTATION": _TERMINAL_REFERENCE,
            "VERIFY_CLOSURE_IMMUTABLE_ARTIFACT_LEASE": (
                *_LEASE_REFERENCE[:-1],
                True,
            ),
        },
        "artifact_publication_receipts": {
            "VERIFY_PUBLICATION_OBSERVED_PIT_BINDING": _OBSERVED_REFERENCE,
            "VERIFY_PUBLICATION_OOS_EXPOSURE_BINDING": (
                *_OOS_REFERENCE[:-1],
                True,
            ),
            "VERIFY_PUBLICATION_CODE_MANIFEST_BINDING": _CODE_REFERENCE,
            "VERIFY_PUBLICATION_START_EXECUTION_ATTESTATION": (
                _CLOSURE_START_REFERENCE
            ),
            "VERIFY_PUBLICATION_TERMINAL_EXECUTION_ATTESTATION": (_TERMINAL_REFERENCE),
            "VERIFY_PUBLICATION_IMMUTABLE_ARTIFACT_LEASE": (
                *_LEASE_REFERENCE[:-1],
                True,
            ),
        },
    }
)


def _module():
    return importlib.import_module(MODULE)


def _spec_module():
    return importlib.import_module(SPEC_MODULE)


def _payload(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise AssertionError(f"not an inspectable schema value: {type(value).__name__}")


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _surface_by_table() -> dict[str, Any]:
    return {surface.table_name: surface for surface in _module().C6_EMBEDDED_REFERENCE_SPECS}


def test_c6_surface_columns_are_exact_provider_qualified_and_row_hashed() -> None:
    surfaces = _surface_by_table()
    assert set(surfaces) == set(SURFACE_COLUMNS)
    for table_name, expected in SURFACE_COLUMNS.items():
        surface = surfaces[table_name]
        names = tuple(column.name for column in surface.columns)
        assert names == expected
        assert tuple(surface.core_owned_fields) == CORE_FIELDS[table_name]
        assert tuple(surface.row_hash_fields) == expected

        added = tuple(name for name in expected if name not in CORE_FIELDS[table_name])
        extension = surface.extension
        assert extension.column_insertion.anchor_column == "genesis_record_hash"
        assert _enum_value(extension.column_insertion.position) == "AFTER"
        assert tuple(column.name for column in extension.column_insertion.columns) == added
        assert tuple(extension.row_hash_field_insertion.fields) == added

    forbidden = {"authority_id", "authority_head"}
    for expected in SURFACE_COLUMNS.values():
        assert forbidden.isdisjoint(expected)


def test_c6_numeric_columns_and_closed_checks_are_not_text_or_implicit() -> None:
    exposure = _surface_by_table()["global_oos_exposures"]
    columns = {column.name: column for column in exposure.columns}
    for name in ("purge_periods", "embargo_periods", "exposure_version"):
        assert _enum_value(columns[name].sql_type) == "INTEGER"

    checks = {check.name: check for check in exposure.extension.checks}
    assert set(checks) == {
        "global_oos_exposures_nonnegative_purge_periods",
        "global_oos_exposures_nonnegative_embargo_periods",
        "global_oos_exposures_positive_exposure_version",
    }
    assert _column_names(checks["global_oos_exposures_nonnegative_purge_periods"].expression) == {"purge_periods"}
    assert _column_names(checks["global_oos_exposures_nonnegative_embargo_periods"].expression) == {"embargo_periods"}
    assert _column_names(checks["global_oos_exposures_positive_exposure_version"].expression) == {"exposure_version"}


@pytest.mark.parametrize(
    "table_name",
    ("data_snooping_family_closures", "artifact_publication_receipts"),
)
def test_c6_nullable_provider_groups_are_closed_all_or_none(
    table_name: str,
) -> None:
    extension = _surface_by_table()[table_name].extension
    checks = {check.name: check for check in extension.checks}
    expected_groups = {
        f"{table_name}_oos_reference_all_or_none": (
            "oos_exposure_id",
            "exposure_fingerprint",
            "exposure_authority_id",
            "exposure_authority_head",
        ),
        f"{table_name}_start_execution_reference_all_or_none": (
            "start_execution_attestation_hash",
            "execution_attestation_authority_id",
            "start_execution_attestation_authority_head",
        ),
        f"{table_name}_terminal_execution_reference_all_or_none": (
            "terminal_execution_attestation_hash",
            "terminal_execution_attestation_authority_head",
        ),
        f"{table_name}_lease_reference_all_or_none": (
            "immutable_artifact_lease_id",
            "immutable_artifact_lease_hash",
            "immutable_artifact_lease_authority_id",
            "immutable_artifact_lease_authority_head",
        ),
    }
    expected_check_names = {
        *expected_groups,
        f"{table_name}_terminal_execution_requires_start",
        f"{table_name}_external_reference_branch",
    }
    if table_name == "artifact_publication_receipts":
        expected_check_names.add(
            "artifact_publication_receipts_attempt_reference_branch"
        )
    assert set(checks) == expected_check_names
    for check_name, columns in expected_groups.items():
        assert _all_or_none_shape(checks[check_name].expression) == columns

    dependency = checks[f"{table_name}_terminal_execution_requires_start"].expression
    assert _enum_value(dependency.operator) == "OR"
    assert tuple((_enum_value(item.operator), item.value.column) for item in dependency.operands) == (
        ("IS_NULL", "terminal_execution_attestation_hash"),
        ("IS_NOT_NULL", "start_execution_attestation_hash"),
    )
    if table_name == "artifact_publication_receipts":
        assert _column_names(
            checks["artifact_publication_receipts_attempt_reference_branch"].expression
        ) == {
            "closure_status",
            "attempt_status",
            "oos_exposure_id",
            "start_execution_attestation_hash",
            "terminal_execution_attestation_hash",
            "immutable_artifact_lease_id",
        }


def _column_names(value: Any) -> set[str]:
    result: set[str] = set()
    payload = _payload(value)
    if "column" in payload and "source" in payload:
        result.add(str(payload["column"]))
    for child in payload.values():
        if is_dataclass(child) or isinstance(child, Mapping):
            result.update(_column_names(child))
        elif isinstance(child, Sequence) and not isinstance(child, (str, bytes)):
            for item in child:
                if is_dataclass(item) or isinstance(item, Mapping):
                    result.update(_column_names(item))
    return result


def _all_or_none_shape(expression: Any) -> tuple[str, ...]:
    assert _enum_value(expression.operator) == "OR"
    assert len(expression.operands) == 2
    null_branch, nonnull_branch = expression.operands
    assert _enum_value(null_branch.operator) == "AND"
    assert _enum_value(nonnull_branch.operator) == "AND"
    assert all(_enum_value(item.operator) == "IS_NULL" for item in null_branch.operands)
    assert all(_enum_value(item.operator) == "IS_NOT_NULL" for item in nonnull_branch.operands)
    null_columns = tuple(item.value.column for item in null_branch.operands)
    nonnull_columns = tuple(item.value.column for item in nonnull_branch.operands)
    assert nonnull_columns == null_columns
    return null_columns


def test_c6_external_verifiers_are_typed_and_embedded_in_extensions() -> None:
    surfaces = _surface_by_table()
    for table_name, expected_rules in EXTERNAL_RULES.items():
        extension_rules = {rule.verifier_code: rule for rule in surfaces[table_name].extension.verification_rules}
        assert set(expected_rules).issubset(extension_rules)
        for rule_id, expected in expected_rules.items():
            kind, provider_type, capability, bindings, nullable = expected
            rule = extension_rules[rule_id]
            assert _enum_value(rule.kind) == "EXTERNAL_AUTHORITY_REFERENCE"
            assert _enum_value(rule.external_reference_kind) == kind
            assert rule.provider_contract_type == provider_type
            assert rule.provider_capability == capability
            expected_component = (
                "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1"
                if provider_type == "C4GlobalOosExposureAuthorityRow"
                else None
            )
            assert getattr(rule, "provider_component_id", None) == expected_component
            assert (
                tuple((binding.local_column, binding.provider_field_path) for binding in rule.external_bindings)
                == bindings
            )
            assert tuple(rule.columns) == tuple(local for local, _ in bindings)
            assert rule.nullable_reference is nullable
            assert rule.reference_presence_column == (bindings[0][0] if nullable else None)


def test_c6_verifier_names_are_not_detached_parallel_metadata() -> None:
    for surface in _surface_by_table().values():
        embedded_ids = {
            rule.verifier_code or rule.rule_id
            for rule in surface.extension.verification_rules
        }
        assert embedded_ids == set(surface.verifier_ids)
        assert embedded_ids, surface.table_name


def test_external_bindings_resolve_real_provider_contract_fields() -> None:
    c4 = importlib.import_module("services.research.experiment_registry.family_schema_c4")
    exposure_row_fields = {
        column.name
        for table in c4.C4_AUTHORITY_TABLE_SPECS
        if table.table_name == "global_oos_exposures"
        for column in table.columns
    }
    exposure_row_fields.update(column.name for column in _surface_by_table()["global_oos_exposures"].columns)
    for surface in _surface_by_table().values():
        for rule in surface.extension.verification_rules:
            if _enum_value(rule.kind) != "EXTERNAL_AUTHORITY_REFERENCE":
                continue
            if rule.provider_contract_type == "C4GlobalOosExposureAuthorityRow":
                assert {binding.provider_field_path for binding in rule.external_bindings} <= exposure_row_fields
                continue

            provider = getattr(research_contracts, rule.provider_contract_type, None)
            assert provider is not None, rule.provider_contract_type
            for binding in rule.external_bindings:
                current = provider
                for segment in binding.provider_field_path.split("."):
                    assert segment in current.model_fields, (
                        rule.verifier_code,
                        binding.provider_field_path,
                    )
                    current = current.model_fields[segment].annotation


def test_external_continuity_paths_resolve_real_provider_contract_fields() -> None:
    c4 = importlib.import_module("services.research.experiment_registry.family_schema_c4")
    exposure_row_fields = {
        column.name
        for table in c4.C4_AUTHORITY_TABLE_SPECS
        if table.table_name == "global_oos_exposures"
        for column in table.columns
    }
    exposure_row_fields.update(column.name for column in _surface_by_table()["global_oos_exposures"].columns)
    for surface in _surface_by_table().values():
        external_rules = {
            rule.verifier_code: rule
            for rule in surface.extension.verification_rules
            if _enum_value(rule.kind) == "EXTERNAL_AUTHORITY_REFERENCE"
        }
        for continuity in surface.extension.external_continuity_rules:
            for equality in continuity.provider_field_equalities:
                for verifier_code, provider_path in (
                    (
                        equality.left_verifier_code,
                        equality.left_provider_field_path,
                    ),
                    (
                        equality.right_verifier_code,
                        equality.right_provider_field_path,
                    ),
                ):
                    rule = external_rules[verifier_code]
                    if rule.provider_contract_type == "C4GlobalOosExposureAuthorityRow":
                        assert provider_path in exposure_row_fields
                        continue
                    provider = getattr(research_contracts, rule.provider_contract_type)
                    current = provider
                    for segment in provider_path.split("."):
                        assert segment in current.model_fields, (
                            continuity.verifier_code,
                            provider_path,
                        )
                        current = current.model_fields[segment].annotation


def test_external_verification_rule_requires_provider_and_exact_bindings() -> None:
    spec = _spec_module()
    kind = spec.VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE
    with pytest.raises(ValueError):
        spec.VerificationRuleSpec(
            "missing_provider",
            kind,
            columns=("authority_id",),
            verifier_code="MISSING_PROVIDER",
        )

    binding = spec.ExternalFieldBindingSpec("authority_id", "authority_id")
    with pytest.raises(ValueError):
        spec.VerificationRuleSpec(
            "mismatched_binding",
            kind,
            columns=("authority_head",),
            provider_contract_type="ObservedPitDatasetHandle",
            provider_capability="observed_pit_dataset_handle.verify",
            external_reference_kind=spec.ExternalAuthorityReferenceKind.OBSERVED_PIT,
            external_bindings=(binding,),
            verifier_code="MISMATCHED_BINDING",
        )

    with pytest.raises(ValueError):
        spec.ExternalFieldBindingSpec("authority_id", "provider..field")


def test_c6_only_claims_coverage_for_contracts_that_exist_now() -> None:
    component = _module().C6_SCHEMA_COMPONENT
    existing_contracts = {
        item for item in ("OosExposureRef", "OosReleaseReceipt") if getattr(research_contracts, item, None) is not None
    }
    assert {item.contract_type for item in component.coverage} <= existing_contracts
    assert "DataSnoopingDeclaration" not in {item.contract_type for item in component.coverage}
    assert "DataSnoopingFamilyClosure" not in {item.contract_type for item in component.coverage}
    assert "ArtifactPublicationReceipt" not in {item.contract_type for item in component.coverage}

    exposure_surface = _surface_by_table()["global_oos_exposures"]
    added_columns = {column.name for column in exposure_surface.extension.column_insertion.columns}
    exposure_coverage = {item.field_name for item in component.coverage if item.contract_type == "OosExposureRef"}
    assert exposure_coverage == added_columns

    c4 = importlib.import_module("services.research.experiment_registry.family_schema_c4")
    delegated = next(
        surface
        for surface in c4.C4_SCHEMA_COMPONENT.required_contract_surfaces
        if surface.contract_type == "OosExposureRef"
    )
    assert set(delegated.field_names) == exposure_coverage
    assert delegated.provider_component_id == component.component_id
    assert delegated.required_capability == "c6.global_oos_exposure.embedded_refs"

    release_surface = _surface_by_table()["data_snooping_oos_releases"]
    release_added_columns = {column.name for column in release_surface.extension.column_insertion.columns}
    release_coverage = {item.field_name for item in component.coverage if item.contract_type == "OosReleaseReceipt"}
    assert release_coverage == release_added_columns

    c7 = importlib.import_module("services.research.experiment_registry.family_schema_c7")
    release_delegation = next(
        surface
        for surface in c7.C7_SCHEMA_COMPONENT.required_contract_surfaces
        if surface.contract_type == "OosReleaseReceipt"
    )
    assert set(release_delegation.field_names) == release_coverage
    assert release_delegation.provider_component_id == component.component_id
    assert release_delegation.required_capability == "c6.oos_release.embedded_refs"

    current_dependencies = {
        dependency.component_id: dependency
        for dependency in component.dependencies
        if dependency.component_id in {c4.C4_SCHEMA_COMPONENT.component_id, c7.C7_SCHEMA_COMPONENT.component_id}
    }
    for owner in (c4.C4_SCHEMA_COMPONENT, c7.C7_SCHEMA_COMPONENT):
        dependency = current_dependencies[owner.component_id]
        assert set(dependency.required_capabilities) <= set(owner.capabilities)


def test_external_authority_continuity_ast_is_closed_and_extension_owned() -> None:
    spec = _spec_module()
    continuity_type = getattr(spec, "ExternalAuthorityContinuitySpec", None)
    equality_type = getattr(spec, "ExternalProviderFieldEqualitySpec", None)

    assert continuity_type is not None, "shared AST must define typed external-authority continuity"
    assert equality_type is not None, "provider field equalities must be typed, not string expressions"
    assert is_dataclass(continuity_type)
    assert is_dataclass(equality_type)
    assert {field.name for field in fields(continuity_type)} == {
        "verifier_code",
        "required_external_verifier_codes",
        "provider_field_equalities",
        "reference_presence_column",
        "semantic_profile",
    }
    assert {field.name for field in fields(equality_type)} == {
        "left_verifier_code",
        "left_provider_field_path",
        "right_verifier_code",
        "right_provider_field_path",
    }

    extension_fields = {field.name for field in fields(spec.AuthorityTableExtensionSpec)}
    assert "external_continuity_rules" in extension_fields

    equality = equality_type(
        left_verifier_code="VERIFY_START_EXECUTION_ATTESTATION",
        left_provider_field_path="lease_id",
        right_verifier_code="VERIFY_IMMUTABLE_ARTIFACT_LEASE",
        right_provider_field_path="lease_id",
    )
    with pytest.raises(ValueError):
        continuity_type(
            verifier_code="VERIFY_UNBOUND_CONTINUITY",
            required_external_verifier_codes=("VERIFY_START_EXECUTION_ATTESTATION",),
            provider_field_equalities=(equality,),
            reference_presence_column=None,
        )


def _continuity_payload(rule: Any) -> tuple[Any, ...]:
    return (
        rule.verifier_code,
        tuple(rule.required_external_verifier_codes),
        tuple(
            (
                equality.left_verifier_code,
                equality.left_provider_field_path,
                equality.right_verifier_code,
                equality.right_provider_field_path,
            )
            for equality in rule.provider_field_equalities
        ),
        rule.reference_presence_column,
    )


def _provider_equalities(
    left_verifier: str,
    right_verifier: str,
    field_pairs: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str, str, str], ...]:
    return tuple((left_verifier, left_field, right_verifier, right_field) for left_field, right_field in field_pairs)


_START_LEASE_FIELDS = (
    ("lease_id", "lease_id"),
    ("code_manifest_hash", "code_manifest_hash"),
    ("immutable_artifact_id", "artifact_id"),
    ("immutable_artifact_digest", "artifact_digest"),
    ("process_image_hash", "process_image_hash"),
)
_CODE_START_FIELDS = (
    ("manifest_hash", "code_manifest_hash"),
    ("source_commit", "source_commit"),
    ("runtime_identity", "runtime_identity"),
    ("dependency_lock_hash", "dependency_lock_hash"),
)
_START_TERMINAL_FIELDS = (
    ("code_manifest_hash", "code_manifest_hash"),
    ("source_commit", "source_commit"),
    ("runtime_identity", "runtime_identity"),
    ("dependency_lock_hash", "dependency_lock_hash"),
    ("immutable_artifact_id", "immutable_artifact_id"),
    ("immutable_artifact_digest", "immutable_artifact_digest"),
    ("entrypoint", "entrypoint"),
    ("process_image_hash", "process_image_hash"),
    ("lease_id", "lease_id"),
)
_OBSERVED_PIT_EXPOSURE_FIELDS = (
    ("authority_id", "observed_pit_authority_id"),
    ("handle_id", "observed_pit_handle_id"),
    ("content_hash", "observed_pit_content_hash"),
    ("knowledge_time_policy_id", "knowledge_time_policy_id"),
    ("knowledge_time_policy_version", "knowledge_time_policy_version"),
    ("authority_head", "observed_pit_authority_head"),
)


def _continuity_expectations(prefix: str) -> tuple[tuple[Any, ...], ...]:
    observed = f"VERIFY_{prefix}_OBSERVED_PIT_BINDING"
    exposure = f"VERIFY_{prefix}_OOS_EXPOSURE_BINDING"
    code = f"VERIFY_{prefix}_CODE_MANIFEST_BINDING"
    start = f"VERIFY_{prefix}_START_EXECUTION_ATTESTATION"
    terminal = f"VERIFY_{prefix}_TERMINAL_EXECUTION_ATTESTATION"
    lease = f"VERIFY_{prefix}_IMMUTABLE_ARTIFACT_LEASE"
    return (
        (
            f"VERIFY_{prefix}_OBSERVED_PIT_EXPOSURE_CONTINUITY",
            (observed, exposure),
            _provider_equalities(
                observed,
                exposure,
                _OBSERVED_PIT_EXPOSURE_FIELDS,
            ),
            "oos_exposure_id",
        ),
        (
            f"VERIFY_{prefix}_CODE_START_CONTINUITY",
            (code, start),
            _provider_equalities(code, start, _CODE_START_FIELDS),
            "start_execution_attestation_hash",
        ),
        (
            f"VERIFY_{prefix}_START_TERMINAL_CONTINUITY",
            (start, terminal),
            _provider_equalities(start, terminal, _START_TERMINAL_FIELDS),
            "terminal_execution_attestation_hash",
        ),
        (
            f"VERIFY_{prefix}_START_LEASE_CONTINUITY",
            (start, lease),
            _provider_equalities(start, lease, _START_LEASE_FIELDS),
            "start_execution_attestation_hash",
        ),
    )


def test_c6_extensions_freeze_exact_cross_provider_continuity() -> None:
    surfaces = _surface_by_table()
    release_start = "VERIFY_START_EXECUTION_ATTESTATION"
    release_lease = "VERIFY_IMMUTABLE_ARTIFACT_LEASE"
    expected = {
        "data_snooping_oos_releases": (
            (
                "VERIFY_OOS_RELEASE_START_LEASE_CONTINUITY",
                (release_start, release_lease),
                _provider_equalities(
                    release_start,
                    release_lease,
                    _START_LEASE_FIELDS,
                ),
                None,
            ),
        ),
        "data_snooping_family_closures": _continuity_expectations("CLOSURE"),
        "artifact_publication_receipts": _continuity_expectations("PUBLICATION"),
    }

    for table_name, expected_rules in expected.items():
        extension = surfaces[table_name].extension
        rules = getattr(extension, "external_continuity_rules", None)
        assert rules is not None, f"{table_name} must embed typed continuity rules"
        assert tuple(_continuity_payload(rule) for rule in rules) == expected_rules


def test_c6_execution_continuity_profiles_freeze_strict_prefix_and_time_semantics() -> None:
    module = _module()
    embedded_rules = {
        rule.verifier_code: rule
        for surface in module.C6_EMBEDDED_REFERENCE_SPECS
        for rule in surface.extension.external_continuity_rules
        if rule.semantic_profile is not None
    }
    assert not hasattr(module, "C6_EXECUTION_CONTINUITY_SEMANTIC_PROFILES")
    assert set(embedded_rules) == {
        "VERIFY_CLOSURE_START_TERMINAL_CONTINUITY",
        "VERIFY_CLOSURE_START_LEASE_CONTINUITY",
        "VERIFY_PUBLICATION_START_TERMINAL_CONTINUITY",
        "VERIFY_PUBLICATION_START_LEASE_CONTINUITY",
    }
    for prefix in ("CLOSURE", "PUBLICATION"):
        start_code = f"VERIFY_{prefix}_START_EXECUTION_ATTESTATION"
        terminal_code = f"VERIFY_{prefix}_TERMINAL_EXECUTION_ATTESTATION"
        lease_code = f"VERIFY_{prefix}_IMMUTABLE_ARTIFACT_LEASE"

        terminal_rule = embedded_rules[f"VERIFY_{prefix}_START_TERMINAL_CONTINUITY"]
        terminal = terminal_rule.semantic_profile
        assert terminal is not None
        assert _enum_value(terminal.semantic_kind) == (
            "START_TERMINAL_STRICT_PROVIDER_PREFIX"
        )
        assert terminal.required_subject_context_fields == (
            "family_id",
            "attempt_id",
            "evaluation_batch_id",
        )
        assert terminal.required_subject_roles == (
            ("left", "START"),
            ("right", "TERMINAL"),
        )
        assert terminal_rule.required_external_verifier_codes == (
            start_code,
            terminal_code,
        )
        assert tuple(
            (
                provider.alias,
                provider.verifier_code,
                _enum_value(provider.source_kind),
                provider.required_fields,
            )
            for provider in terminal.provider_inputs
        ) == (
            (
                "start",
                start_code,
                "OUTER_REQUIRED",
                ("attestation_hash", "issued_at_us"),
            ),
            (
                "terminal",
                terminal_code,
                "OUTER_REQUIRED",
                ("attestation_hash", "issued_at_us"),
            ),
        )
        assert terminal.require_distinct_evidence_hashes is True
        assert terminal.require_same_provider_authority is True
        assert terminal.require_same_provider_genesis is True
        assert terminal.require_right_projection_strict_extension is True
        assert terminal.require_strict_issued_chronology is True
        assert terminal.strict_time_order_paths == (
            "start.issued_at_us",
            "terminal.issued_at_us",
        )
        assert terminal.chronology_branches == ()

        lease_rule = embedded_rules[f"VERIFY_{prefix}_START_LEASE_CONTINUITY"]
        lease = lease_rule.semantic_profile
        assert lease is not None
        assert _enum_value(lease.semantic_kind) == (
            "START_LEASE_SUBJECT_TIME_WINDOW"
        )
        assert lease.required_subject_context_fields == (
            "family_id",
            "attempt_id",
            "evaluation_batch_id",
        )
        assert lease_rule.required_external_verifier_codes == (start_code, lease_code)
        assert tuple(
            (
                provider.alias,
                provider.verifier_code,
                _enum_value(provider.source_kind),
                provider.required_fields,
            )
            for provider in lease.provider_inputs
        ) == (
            (
                "start",
                start_code,
                "OUTER_REQUIRED",
                ("attestation_hash", "issued_at_us"),
            ),
            (
                "lease",
                lease_code,
                "OUTER_REQUIRED",
                ("lease_hash", "acquired_at_us", "expires_at_us"),
            ),
            (
                "terminal",
                terminal_code,
                "BRANCH_ADDITIONAL",
                ("attestation_hash", "issued_at_us"),
            ),
        )
        assert lease.strict_time_order_paths == ()
        assert lease.require_full_lease_time_window is True

        complete, incomplete = lease.chronology_branches
        closure_alias = "closure" if prefix == "CLOSURE" else "related_closure"
        subject_aliases = (
            ("closure",)
            if prefix == "CLOSURE"
            else ("publication", "related_closure")
        )
        assert _enum_value(complete.branch_kind) == "COMPLETE"
        assert complete.selector.closure_status_path == f"{closure_alias}.closure_status"
        assert complete.selector.attempt_status_path == "attempt.status"
        assert complete.required_additional_external_verifier_codes == (terminal_code,)
        assert tuple(row.alias for row in complete.row_inputs) == (
            *subject_aliases,
            "attempt",
            "evaluation_batch",
        )
        assert _enum_value(incomplete.branch_kind) == "INCOMPLETE"
        assert incomplete.selector.closure_status_path == f"{closure_alias}.closure_status"
        assert incomplete.selector.attempt_status_path == "attempt.status"
        assert incomplete.required_additional_external_verifier_codes == ()
        assert tuple(row.alias for row in incomplete.row_inputs) == (
            *subject_aliases,
            "attempt",
        )

        complete_rows = {row.alias: row for row in complete.row_inputs}
        assert complete_rows["attempt"].required_verification_rule_ids == (
            "oos_attempt_row_hash",
            "oos_attempt_identity_stability",
            "oos_attempt_terminal_batch_branch",
        )
        assert complete_rows["evaluation_batch"].required_verification_rule_ids == (
            "evaluation_batch_row_hash",
            "evaluation_batch_started_attempt_identity",
            "evaluation_batch_attempt_terminal_binding",
        )
        batch_bindings = {
            binding.related_column: binding.source_path
            for binding in complete_rows["evaluation_batch"].bindings
        }
        assert batch_bindings["start_execution_attestation_hash"] == (
            "start.attestation_hash"
        )
        assert batch_bindings["terminal_execution_attestation_hash"] == (
            "terminal.attestation_hash"
        )
        assert batch_bindings["immutable_artifact_lease_hash"] == "lease.lease_hash"

        if prefix == "CLOSURE":
            assert complete_rows["closure"].required_verification_rule_ids == (
                "family_closure_row_hash",
                "family_closure_attempt_batch_branch",
            )
        else:
            assert complete_rows["publication"].required_verification_rule_ids == (
                "publication_receipt_closure_exact",
                "publication_receipt_closure_external_summaries_exact",
                "verify_publication_external_reference_totality",
            )
            assert complete_rows["related_closure"].required_verification_rule_ids == (
                "family_closure_row_hash",
                "family_closure_attempt_batch_branch",
            )

        assert tuple(
            (step.left_path, _enum_value(step.operator), step.right_path)
            for step in complete.comparisons
        ) == (
            ("lease.acquired_at_us", "LE", "start.issued_at_us"),
            ("start.issued_at_us", "LT", "terminal.issued_at_us"),
            (
                "terminal.issued_at_us",
                "LE",
                "evaluation_batch.committed_at_us",
            ),
            (
                "evaluation_batch.committed_at_us",
                "LE",
                f"{closure_alias}.closed_at_us",
            ),
            (f"{closure_alias}.closed_at_us", "LE", "lease.expires_at_us"),
        )
        assert tuple(
            (step.left_path, _enum_value(step.operator), step.right_path)
            for step in incomplete.comparisons
        ) == (
            ("lease.acquired_at_us", "LE", "start.issued_at_us"),
            ("start.issued_at_us", "LE", f"{closure_alias}.closed_at_us"),
            (f"{closure_alias}.closed_at_us", "LE", "lease.expires_at_us"),
        )


_BRANCH_PRESENCE_COLUMNS = (
    "oos_exposure_id",
    "start_execution_attestation_hash",
    "terminal_execution_attestation_hash",
    "immutable_artifact_lease_id",
)
_ALLOWED_REFERENCE_BRANCHES = {
    (False, False, False, False),
    (True, True, False, True),
    (True, True, True, True),
}


def _evaluate_presence_predicate(expression: Any, row: Mapping[str, object | None]) -> bool:
    spec = _spec_module()
    if type(expression) is spec.ComparisonExpression:
        left = row[expression.left.column]
        right = expression.right.value
        operator = _enum_value(expression.operator)
        if operator == "EQ":
            return left == right
        raise AssertionError(f"unknown comparison operator: {operator}")
    if type(expression) is spec.NullExpression:
        value = row[expression.value.column]
        operator = _enum_value(expression.operator)
        if operator == "IS_NULL":
            return value is None
        if operator == "IS_NOT_NULL":
            return value is not None
        raise AssertionError(f"unknown null operator: {operator}")
    if type(expression) is spec.LogicalExpression:
        values = tuple(_evaluate_presence_predicate(item, row) for item in expression.operands)
        operator = _enum_value(expression.operator)
        if operator == "AND":
            return all(values)
        if operator == "OR":
            return any(values)
        raise AssertionError(f"unknown logical operator: {operator}")
    if type(expression) is spec.NotExpression:
        return not _evaluate_presence_predicate(expression.operand, row)
    raise AssertionError(
        f"external-reference branch must use only closed null/logical predicate nodes; got {type(expression).__name__}"
    )


@pytest.mark.parametrize(
    ("table_name", "verifier_code"),
    (
        ("data_snooping_family_closures", "VERIFY_CLOSURE_EXTERNAL_REFERENCE_BRANCH"),
        ("artifact_publication_receipts", "VERIFY_PUBLICATION_EXTERNAL_REFERENCE_TOTALITY"),
    ),
)
def test_nullable_external_reference_branch_has_one_closed_check_and_truth_table(
    table_name: str,
    verifier_code: str,
) -> None:
    extension = _surface_by_table()[table_name].extension
    rule = next(rule for rule in extension.verification_rules if rule.verifier_code == verifier_code)
    predicate = getattr(rule, "predicate", None)
    assert predicate is not None, "STATE_BRANCH verifier must carry its closed predicate"

    checks = {check.name: check for check in extension.checks}
    check_name = (
        "artifact_publication_receipts_attempt_reference_branch"
        if table_name == "artifact_publication_receipts"
        else f"{table_name}_external_reference_branch"
    )
    check = checks[check_name]
    assert check.expression == predicate

    if table_name == "data_snooping_family_closures":
        status_cases = (((None, None), _ALLOWED_REFERENCE_BRANCHES),)
    else:
        status_cases = (
            (("COMPLETE", "COMPLETED"), {(True, True, True, True)}),
            (("NOT_ASSESSABLE", "NOT_STARTED"), {(False, False, False, False)}),
            (("NOT_ASSESSABLE", "INCOMPLETE"), {(True, True, False, True)}),
            (("COMPLETE", "NOT_STARTED"), set()),
            (("NOT_ASSESSABLE", "COMPLETED"), set()),
        )
    for (closure_status, attempt_status), allowed in status_cases:
        for presence in product((False, True), repeat=len(_BRANCH_PRESENCE_COLUMNS)):
            row = {
                column: (f"present:{column}" if is_present else None)
                for column, is_present in zip(
                    _BRANCH_PRESENCE_COLUMNS,
                    presence,
                    strict=True,
                )
            }
            row.update(
                closure_status=closure_status,
                attempt_status=attempt_status,
            )
            assert _evaluate_presence_predicate(predicate, row) is (presence in allowed), (
                table_name,
                closure_status,
                attempt_status,
                dict(zip(_BRANCH_PRESENCE_COLUMNS, presence, strict=True)),
            )


def test_publication_closure_external_summaries_are_enforced_by_one_real_trigger() -> None:
    extension = _surface_by_table()["artifact_publication_receipts"].extension
    trigger = next(
        item
        for item in extension.triggers
        if item.error_code == "PUBLICATION_CLOSURE_EXTERNAL_SUMMARIES_EXACT"
    )
    relation = trigger.violation_when
    assert type(relation).__name__ == "RelationExistsExpression"
    assert relation.table == "data_snooping_family_closures"
    assert relation.negated is True
    actual_bindings = tuple(
        (binding.value.column, binding.related_column)
        for binding in relation.bindings
    )
    assert actual_bindings == _module()._C6_PUBLICATION_CLOSURE_EXACT_BINDINGS
    assert {
        "authority_instance_id",
        "family_id",
        "closure_manifest_hash",
        "closure_status",
        "start_execution_attestation_authority_head",
        "terminal_execution_attestation_authority_head",
        "code_manifest_evidence_hash",
    }.issubset(local for local, _ in actual_bindings)

    structural_rule = next(
        rule
        for rule in extension.verification_rules
        if rule.rule_id
        == "publication_receipt_closure_external_summaries_exact"
    )
    assert structural_rule.verifier_code is None
    assert structural_rule.structural_source.source_id == trigger.name
    assert _enum_value(structural_rule.structural_source.kind) == "TRIGGER_POLICY"
    assert set(structural_rule.columns) == {local for local, _ in actual_bindings}
    assert "VERIFY_PUBLICATION_BYTE_HASH_PROVENANCE" not in {
        rule.verifier_code for rule in extension.verification_rules
    }


def test_c6_declares_pass_m_provider_capability_requirements_without_fake_services() -> None:
    module = _module()
    requirements = getattr(module, "C6_REQUIRED_EXTERNAL_PROVIDER_CAPABILITIES", None)
    assert requirements is not None, "C6 must publish typed Pass-M provider requirements"
    assert type(requirements) is tuple
    assert all(is_dataclass(requirement) for requirement in requirements)

    actual = tuple(
        (
            requirement.provider_contract_type,
            requirement.provider_capability,
            _enum_value(requirement.resolution_stage),
        )
        for requirement in requirements
    )
    assert actual == (
        (
            "ObservedPitDatasetHandle",
            "observed_pit_dataset_handle.verify",
            "C8_PASS_M",
        ),
        ("CodeManifestEvidence", "code_manifest_evidence.verify", "C8_PASS_M"),
        (
            "ExecutionCodeAttestation",
            "execution_code_attestation.verify",
            "C8_PASS_M",
        ),
        ("ImmutableArtifactLease", "immutable_artifact_lease.verify", "C8_PASS_M"),
    )
    assert len(set(actual)) == len(actual)

    component_requirements = getattr(
        module.C6_SCHEMA_COMPONENT,
        "required_external_provider_capabilities",
        None,
    )
    assert component_requirements == requirements
    assert all(
        requirement.provider_contract_type != "C4GlobalOosExposureAuthorityRow" for requirement in requirements
    ), "the C4 row is resolved by the component dependency, not a fake external service"

    c4 = importlib.import_module("services.research.experiment_registry.family_schema_c4")
    dependency = next(
        dependency
        for dependency in module.C6_SCHEMA_COMPONENT.dependencies
        if dependency.component_id == c4.C4_SCHEMA_COMPONENT.component_id
    )
    assert "c4.global_oos_exposure.verify" in c4.C4_SCHEMA_COMPONENT.capabilities
    assert "c4.global_oos_exposure.verify" in dependency.required_capabilities

    external_rules = {
        (rule.provider_contract_type, rule.provider_capability)
        for extension in module.C6_SCHEMA_COMPONENT.extensions
        for rule in extension.verification_rules
        if _enum_value(rule.kind) == "EXTERNAL_AUTHORITY_REFERENCE"
        and getattr(rule, "provider_component_id", None) is None
    }
    assert external_rules == {
        (requirement.provider_contract_type, requirement.provider_capability) for requirement in requirements
    }
