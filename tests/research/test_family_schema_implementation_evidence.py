"""Pass-M implementation-evidence requirements and fail-closed binding."""

from __future__ import annotations

import dataclasses

import pytest

from services.research.experiment_registry.family_schema_composition import (
    EXACT_V3_VERIFIER_COMPOSITION_BINDING_KEY,
    FamilySchemaCompositionError,
    auxiliary_semantic_recipe_binding_key,
    compose_family_schema_v3,
    external_claim_profile_binding_key,
)
from services.research.experiment_registry.family_schema_spec import (
    AuxiliarySemanticRecipeKind,
    ImplementationEvidenceBinding,
    ImplementationEvidenceCatalog,
    ImplementationEvidenceHashMethod,
    ImplementationEvidencePolicy,
    ImplementationTargetKind,
    StructuralVerificationOperation,
)


def _fake_actual_evidence():
    requirements = compose_family_schema_v3().implementation_evidence_requirements
    return tuple(
        ImplementationEvidenceBinding(
            target_kind=item.target_kind,
            target_id=item.target_id,
            implementation_id=item.implementation_id,
            implementation_sha256=f"{ordinal + 1:064x}",
            hash_method=item.hash_method,
            hash_method_version=item.hash_method_version,
        )
        for ordinal, item in enumerate(requirements.entries)
        if item.evidence_policy is ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256
    )


def test_composition_declares_exact_internal_and_external_evidence_boundary() -> None:
    composition = compose_family_schema_v3()
    catalog = composition.implementation_evidence_requirements
    by_kind = {
        kind: tuple(item for item in catalog.entries if item.target_kind is kind)
        for kind in ImplementationTargetKind
    }

    assert len(by_kind[ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION]) == 9
    assert len(by_kind[ImplementationTargetKind.NAMED_VERIFIER]) == 116
    assert len(by_kind[ImplementationTargetKind.STRUCTURAL_HANDLER]) == 11
    assert len(by_kind[ImplementationTargetKind.EXTERNAL_PROVIDER_TRUST_ROOT]) == 6
    assert len(catalog.entries) == 142
    assert len({item.target_id for item in catalog.entries}) == 142

    for item in (
        *by_kind[ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION],
        *by_kind[ImplementationTargetKind.NAMED_VERIFIER],
        *by_kind[ImplementationTargetKind.STRUCTURAL_HANDLER],
    ):
        assert item.evidence_policy is ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256
        assert item.implementation_id
        assert item.hash_method is (
            ImplementationEvidenceHashMethod.PYTHON_CALLABLE_AND_CANONICAL_BINDING_SHA256_V1
        )
        assert item.requires_callable_closure is True
        assert item.requires_canonical_bound_spec is True

    for item in by_kind[ImplementationTargetKind.EXTERNAL_PROVIDER_TRUST_ROOT]:
        assert item.evidence_policy is (
            ImplementationEvidencePolicy.EXCLUDED_DEPLOYMENT_TRUST_ROOT
        )
        assert item.implementation_id is None
        assert item.hash_method is ImplementationEvidenceHashMethod.NONE
        assert item.requires_callable_closure is False
        assert item.requires_canonical_bound_spec is True


def test_semantic_requirements_bind_operation_sqlite_name_and_contract_exactly() -> None:
    composition = compose_family_schema_v3()
    expected = {
        (abi.operation, abi.sqlite_name, abi.semantic_contract_id)
        for abi in composition.renderer_grammar.semantic_function_abis
    }
    actual = {
        (item.semantic_operation, item.sqlite_name, item.semantic_contract_id)
        for item in composition.implementation_evidence_requirements.entries
        if item.target_kind is ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION
    }
    assert actual == expected


def test_named_and_structural_requirements_bind_all_canonical_plan_bytes() -> None:
    entries = compose_family_schema_v3().implementation_evidence_requirements.entries
    named = tuple(
        item for item in entries if item.target_kind is ImplementationTargetKind.NAMED_VERIFIER
    )
    structural = tuple(
        item for item in entries if item.target_kind is ImplementationTargetKind.STRUCTURAL_HANDLER
    )
    assert all(item.verifier_code and item.canonical_binding_keys for item in named)
    assert all(item.structural_operation and item.canonical_binding_keys for item in structural)
    assert any(len(item.canonical_binding_keys) > 1 for item in named)
    assert any(len(item.canonical_binding_keys) > 1 for item in structural)


def test_verifier_requirements_bind_full_composition_and_all_29_recipes() -> None:
    composition = compose_family_schema_v3()
    internal_verifiers = tuple(
        item
        for item in composition.implementation_evidence_requirements.entries
        if item.target_kind
        in {
            ImplementationTargetKind.NAMED_VERIFIER,
            ImplementationTargetKind.STRUCTURAL_HANDLER,
        }
    )
    assert len(internal_verifiers) == 127
    assert all(
        EXACT_V3_VERIFIER_COMPOSITION_BINDING_KEY
        in item.canonical_binding_keys
        for item in internal_verifiers
    )

    recipe_owners = tuple(
        (component.component_id, recipe)
        for component in composition.components
        for recipe in component.auxiliary_semantic_recipes
    )
    recipe_keys = {
        auxiliary_semantic_recipe_binding_key(component_id, recipe.recipe_id): recipe
        for component_id, recipe in recipe_owners
    }
    assert len(recipe_keys) == 29
    requirement_keys = {
        key
        for item in internal_verifiers
        for key in item.canonical_binding_keys
    }
    assert set(recipe_keys) <= requirement_keys
    assert all(
        sum(key in item.canonical_binding_keys for item in internal_verifiers) >= 1
        for key in recipe_keys
    )

    structural_replay = next(
        item
        for item in internal_verifiers
        if item.target_kind is ImplementationTargetKind.STRUCTURAL_HANDLER
        and item.structural_operation
        is StructuralVerificationOperation.REPLAY_INSERT_GUARD
    )
    structural_recipe_keys = {
        key
        for key, recipe in recipe_keys.items()
        if recipe.recipe_kind
        is AuxiliarySemanticRecipeKind.CANONICAL_CONTENT_HASH
        and not recipe.required_verifier_codes
    }
    assert len(structural_recipe_keys) == 4
    assert structural_recipe_keys <= set(structural_replay.canonical_binding_keys)


def test_claim_profiles_bind_named_verifiers_and_external_trust_roots() -> None:
    composition = compose_family_schema_v3()
    entries = composition.implementation_evidence_requirements.entries
    profiles = composition.external_authority_claim_profiles
    assert len(profiles) == 2

    for profile in profiles:
        key = external_claim_profile_binding_key(
            profile.provider_contract_type,
            profile.provider_capability,
        )
        named = tuple(
            item
            for item in entries
            if item.target_kind is ImplementationTargetKind.NAMED_VERIFIER
            and key in item.canonical_binding_keys
        )
        external = tuple(
            item
            for item in entries
            if item.target_kind
            is ImplementationTargetKind.EXTERNAL_PROVIDER_TRUST_ROOT
            and item.provider_contract_type == profile.provider_contract_type
            and item.provider_capability == profile.provider_capability
        )
        assert named
        assert len(external) == 1
        assert key in external[0].canonical_binding_keys


def test_actual_evidence_binding_is_exact_and_external_provider_evidence_is_forbidden() -> None:
    requirements = compose_family_schema_v3().implementation_evidence_requirements
    evidence = _fake_actual_evidence()
    bound = ImplementationEvidenceCatalog(requirements=requirements, bindings=evidence)
    assert len(bound.bindings) == 136

    with pytest.raises(ValueError, match="exactly cover"):
        ImplementationEvidenceCatalog(requirements=requirements, bindings=evidence[:-1])
    with pytest.raises(ValueError, match="unique"):
        ImplementationEvidenceCatalog(
            requirements=requirements,
            bindings=(*evidence, evidence[0]),
        )
    with pytest.raises(ValueError, match="requirement"):
        ImplementationEvidenceCatalog(
            requirements=requirements,
            bindings=(
                dataclasses.replace(
                    evidence[0],
                    implementation_id="FAMILY_SCHEMA_SQLITE_SEMANTIC::wrong",
                ),
                *evidence[1:],
            ),
        )
    external = next(
        item
        for item in requirements.entries
        if item.target_kind is ImplementationTargetKind.EXTERNAL_PROVIDER_TRUST_ROOT
    )
    with pytest.raises(ValueError, match="external provider"):
        ImplementationEvidenceCatalog(
            requirements=requirements,
            bindings=(
                *evidence,
                ImplementationEvidenceBinding(
                    target_kind=external.target_kind,
                    target_id=external.target_id,
                    implementation_id="forbidden.external.provider.implementation",
                    implementation_sha256="f" * 64,
                    hash_method=(
                        ImplementationEvidenceHashMethod.PYTHON_CALLABLE_AND_CANONICAL_BINDING_SHA256_V1
                    ),
                    hash_method_version="QUANTOS_IMPLEMENTATION_EVIDENCE_HASH_V1",
                ),
            ),
        )


def test_composition_rejects_requirement_catalog_tampering() -> None:
    composition = compose_family_schema_v3()
    catalog = composition.implementation_evidence_requirements
    with pytest.raises(FamilySchemaCompositionError, match="implementation evidence"):
        dataclasses.replace(
            composition,
            implementation_evidence_requirements=dataclasses.replace(
                catalog,
                entries=catalog.entries[:-1],
            ),
        )
    with pytest.raises(FamilySchemaCompositionError, match="claim profiles"):
        dataclasses.replace(
            composition,
            external_authority_claim_profiles=(
                composition.external_authority_claim_profiles[:-1]
            ),
        )
    with pytest.raises(FamilySchemaCompositionError, match="semantic recipes"):
        dataclasses.replace(
            composition,
            auxiliary_semantic_recipes=composition.auxiliary_semantic_recipes[:-1],
        )
