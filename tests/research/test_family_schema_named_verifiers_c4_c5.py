"""Executable Pass-M coverage for genesis, C4, and C5 named verifiers.

The fixtures are SQLite-shaped immutable snapshots.  External governance
claims are supplied only through the typed C4 provider seam; no test verifier
is allowed to manufacture a trusted grant or actor claim locally.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import replace
from types import FunctionType

import pytest

from quantos.contracts import canonical
from services.research.experiment_registry.family_schema_c4 import (
    C4_IDENTITY_ISSUANCE,
    C4_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_c5_c6 import (
    C5_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_external_verification_c4 import (
    ActorProvenanceClaims,
    ActorProvenanceEvidence,
    ActorProvenanceVerificationRequest,
    C4ExternalProviderBundle,
    MultiPartyGrantClaims,
    MultiPartyGrantEvidence,
    MultiPartyGrantVerificationRequest,
)
from services.research.experiment_registry.family_schema_named_verifiers_c4_c5 import (
    GENESIS_C4_C5_NAMED_VERIFIER_IMPLEMENTATIONS,
    GenesisC4C5NamedVerifierImplementation,
    verify_genesis_c4_c5_named,
)
from services.research.experiment_registry.family_schema_spec import (
    FAMILY_AUTHORITY_GENESIS_TABLE_SPEC,
)
from services.research.experiment_registry.family_schema_verification_view import (
    FrozenFamilySchemaVerificationView,
)
from services.research.experiment_registry.family_schema_verifier_registry import (
    FAMILY_SCHEMA_VERIFIER_BINDINGS,
)


_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64
_E = "e" * 64
_F = "f" * 64
_ZERO = "0" * 64


def _view(
    *tables: tuple[str, tuple[Mapping[str, object], ...]],
) -> FrozenFamilySchemaVerificationView:
    return FrozenFamilySchemaVerificationView.from_rows(tables)


def _declared_named_bindings() -> tuple[tuple[str, str], ...]:
    bindings: list[tuple[str, str]] = []
    components = (
        (
            "FAMILY_AUTHORITY_GENESIS_V1",
            (FAMILY_AUTHORITY_GENESIS_TABLE_SPEC,),
            (),
        ),
        (C4_SCHEMA_COMPONENT.component_id, C4_SCHEMA_COMPONENT.tables, C4_SCHEMA_COMPONENT.extensions),
        (C5_SCHEMA_COMPONENT.component_id, C5_SCHEMA_COMPONENT.tables, C5_SCHEMA_COMPONENT.extensions),
    )
    for component_id, tables, extensions in components:
        for surface, items in (("TABLE", tables), ("EXTENSION", extensions)):
            for item in items:
                owner = item.table_name if surface == "TABLE" else item.target_table
                bindings.extend(
                    (
                        f"{component_id}:{surface}:{owner}:{rule.rule_id}",
                        rule.verifier_code,
                    )
                    for rule in item.verification_rules
                    if rule.verifier_code is not None
                )
    return tuple(bindings)


def _identity_payload(recipe, owner: Mapping[str, object], predecessor: str) -> dict[str, object]:
    return {
        recipe.payload_schema_version_field: recipe.payload_schema_version,
        recipe.domain_separator_field: recipe.domain_separator,
        recipe.predecessor_field: predecessor,
        recipe.ordered_fields_field: [
            {
                recipe.ordered_field_name_field: field,
                recipe.ordered_field_value_field: owner[field],
            }
            for field in recipe.source_fields
        ],
    }


def _identity_owner(rule_id: str) -> tuple[dict[str, object], FrozenFamilySchemaVerificationView]:
    recipes = tuple(item for item in C4_IDENTITY_ISSUANCE if item.verification_rule_id == rule_id)
    assert recipes
    owner: dict[str, object] = {
        "authority_instance_id": "authority-1",
        "question_hash": _A,
        "source_manifest_hash": _B,
        "strategy_lineage_root_id": _C,
        "parent_strategy_lineage_node_id": None,
        "node_manifest_hash": _D,
        "event_ordinal": 1,
        "deployment_mandate_hash": _E,
        "selection_scope_id": _F,
        "research_question_id": _A,
        "decision_ordinal": 1,
        "decision_class": "PUBLISHABLE",
        "selection_decision_id": _B,
        "family_id": _C,
        "exposure_ordinal": 1,
        "exposure_fingerprint": _D,
        "authority_event_id": _E,
        "prev_record_hash": _ZERO,
    }
    predecessor = _ZERO
    for recipe in recipes:
        owner[recipe.field_name] = canonical.canonical_sha256(
            _identity_payload(recipe, owner, predecessor)
        )
    view = _view(
        (
            "research_authority_events",
            (
                {
                    "event_id": _E,
                    "prev_record_hash": predecessor,
                },
            ),
        ),
    )
    return owner, view


def _genesis_row() -> dict[str, object]:
    row: dict[str, object] = {
        "singleton_key": 1,
        "authority_instance_id": "authority-1",
        "v2_origin_fingerprint": _A,
        "sealed_v3_manifest_hash": _B,
        "activation_code_manifest_evidence_hash": _C,
        "migration_id": "FAMILY_SCHEMA_V2_TO_V3_V1",
        "activated_at_us": 1_000_000,
        "record_type": "FAMILY_AUTHORITY_GENESIS_V1",
    }
    row["record_hash"] = canonical.canonical_sha256(row)
    return row


class _GrantProvider:
    def __init__(self, evidence: MultiPartyGrantEvidence | None, *, raises: bool = False) -> None:
        self.evidence = evidence
        self.raises = raises
        self.requests: list[MultiPartyGrantVerificationRequest] = []

    def verify(
        self,
        request: MultiPartyGrantVerificationRequest,
        /,
    ) -> MultiPartyGrantEvidence | None:
        self.requests.append(request)
        if self.raises:
            raise RuntimeError("external grant authority unavailable")
        return self.evidence


class _ActorProvider:
    def __init__(self, evidence: ActorProvenanceEvidence | None, *, raises: bool = False) -> None:
        self.evidence = evidence
        self.raises = raises
        self.requests: list[ActorProvenanceVerificationRequest] = []

    def verify(
        self,
        request: ActorProvenanceVerificationRequest,
        /,
    ) -> ActorProvenanceEvidence | None:
        self.requests.append(request)
        if self.raises:
            raise RuntimeError("external actor authority unavailable")
        return self.evidence


def _governed_event() -> dict[str, object]:
    return {
        "authority_instance_id": "authority-1",
        "genesis_record_hash": _ZERO,
            "event_id": _E,
        "action": "QUESTION_ISSUED",
        "subject_record_type": "RESEARCH_QUESTION_V1",
        "subject_id": _A,
        "subject_record_hash": _B,
        "grant_id": "grant-1",
        "grant_record_hash": _C,
        "grant_policy_id": "MULTI_PARTY_GRANT_POLICY",
        "grant_policy_version": "1",
        "grant_policy_hash": _D,
        "grant_authority_id": "grant-authority-1",
        "grant_authority_head": _E,
        "audit_actor_id": "human-approver-3",
        "audit_actor_kind": "HUMAN",
        "audit_actor_evidence_hash": _F,
        "audit_actor_policy_id": "ACTOR_PROVENANCE_POLICY",
        "audit_actor_policy_version": "1",
        "audit_actor_policy_hash": _A,
        "audit_actor_authority_id": "identity-authority-1",
        "audit_actor_authority_head": _B,
        "event_at_us": 1_000_000,
    }


def _grant_evidence(event: Mapping[str, object]) -> MultiPartyGrantEvidence:
    return MultiPartyGrantEvidence(
        grant_id=event["grant_id"],
        grant_record_hash=event["grant_record_hash"],
        policy_id=event["grant_policy_id"],
        policy_version=event["grant_policy_version"],
        policy_hash=event["grant_policy_hash"],
        authority_id=event["grant_authority_id"],
        authority_head=event["grant_authority_head"],
        action=event["action"],
        subject_record_type=event["subject_record_type"],
        subject_id=event["subject_id"],
        subject_record_hash=event["subject_record_hash"],
        consumed_by_actor_id=event["audit_actor_id"],
        consumed_at_us=event["event_at_us"],
        claims=MultiPartyGrantClaims(
            state="CONSUMED",
            verified_human_actor_count=3,
            distinct_human_actors=True,
            complete_hash_chain=True,
            grant_not_reused=True,
            subject_action_bound=True,
            consumption_time_bound=True,
            policy_verified=True,
            authority_head_verified=True,
        ),
    )


def _actor_evidence(event: Mapping[str, object]) -> ActorProvenanceEvidence:
    return ActorProvenanceEvidence(
        actor_id=event["audit_actor_id"],
        actor_kind=event["audit_actor_kind"],
        evidence_hash=event["audit_actor_evidence_hash"],
        policy_id=event["audit_actor_policy_id"],
        policy_version=event["audit_actor_policy_version"],
        policy_hash=event["audit_actor_policy_hash"],
        authority_id=event["audit_actor_authority_id"],
        authority_head=event["audit_actor_authority_head"],
        action=event["action"],
        subject_record_type=event["subject_record_type"],
        subject_id=event["subject_id"],
        subject_record_hash=event["subject_record_hash"],
        event_at_us=event["event_at_us"],
        claims=ActorProvenanceClaims(
            provenance_verified=True,
            actor_kind_verified=True,
            action_context_bound=True,
            subject_context_bound=True,
            event_time_bound=True,
            evidence_hash_verified=True,
            policy_verified=True,
            authority_head_verified=True,
        ),
    )


def _provider_bundle(event: Mapping[str, object]) -> tuple[C4ExternalProviderBundle, _GrantProvider, _ActorProvider]:
    grant = _GrantProvider(_grant_evidence(event))
    actor = _ActorProvider(_actor_evidence(event))
    return C4ExternalProviderBundle(grant, actor), grant, actor


def _proposal_identity(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "family_id": row["family_id"],
        "search_id": row["search_id"],
        "emission_ordinal": row["emission_ordinal"],
        "proposal_id": row["proposal_id"],
        "proposal_branch": row["proposal_branch"],
        "raw_envelope_hash": row["raw_envelope_hash"],
    }


def _registration(
    ordinal: int,
    *,
    branch: str = "CLEAN",
    raw_hash: str | None = _A,
    quarantine_id: str | None = None,
) -> dict[str, object]:
    key: dict[str, object] = {
        "family_id": "family-1",
        "search_id": "search-1",
        "emission_ordinal": ordinal,
    }
    if branch == "SECRET_QUARANTINED":
        key["status"] = branch
        raw_hash = None
    else:
        key["raw_envelope_hash"] = raw_hash
    row: dict[str, object] = {
        "authority_instance_id": "authority-1",
        **key,
        "proposal_id": canonical.canonical_sha256(key),
        "proposal_branch": branch,
        "raw_envelope_hash": raw_hash,
        "media_type": "application/json",
        "raw_byte_length": None if branch == "SECRET_QUARANTINED" else 64,
        "length_bucket": "LE_1_KIB",
        "dlp_policy_id": "DLP_POLICY_V1",
        "dlp_policy_version": "1",
        "scanner_implementation": "QUANTOS_DLP_SCANNER",
        "scanner_version": "1",
        "dlp_result_code": branch if branch == "SECRET_QUARANTINED" else "CLEAN",
        "restricted_clean_blob_receipt_hash": None if branch == "SECRET_QUARANTINED" else _B,
        "quarantine_record_id": quarantine_id,
        "registered_at_us": 11 + ordinal,
    }
    row["identity_payload_json"] = canonical.canonical_json_bytes(_proposal_identity(row)).decode("utf-8")
    row["record_hash"] = canonical.canonical_sha256(
        {"registration": ordinal, "proposal_id": row["proposal_id"]}
    )
    return row


def _canonical_alpha_proposal(ordinal: int, alpha_id: str) -> dict[str, object]:
    return {
        "alpha_id": alpha_id,
        "factor_ids": [f"factor-{ordinal}"],
        "name": f"Alpha {ordinal}",
        "params": {"lookback": str(ordinal * 10)},
        "universe": "crypto-liquid",
    }


def _proposal_result(
    registration: Mapping[str, object],
    *,
    parse_status: str,
    alpha_id: str | None = None,
) -> dict[str, object]:
    accepted = parse_status == "ACCEPTED"
    ordinal = int(registration["emission_ordinal"])
    canonical_proposal = (
        _canonical_alpha_proposal(ordinal, alpha_id or f"alpha-{ordinal}")
        if accepted
        else None
    )
    config = (
        {
            "factor_ids": canonical_proposal["factor_ids"],
            "universe": canonical_proposal["universe"],
            "params": canonical_proposal["params"],
        }
        if canonical_proposal is not None
        else None
    )
    return {
        "authority_instance_id": "authority-1",
        "family_id": registration["family_id"],
        "search_id": registration["search_id"],
        "emission_ordinal": registration["emission_ordinal"],
        "proposal_id": registration["proposal_id"],
        "registration_record_hash": registration["record_hash"],
        "parse_status": parse_status,
        "canonical_proposal_json": (
            canonical.canonical_json_bytes(canonical_proposal).decode("utf-8")
            if canonical_proposal is not None
            else None
        ),
        "canonical_proposal_hash": (
            canonical.canonical_sha256(canonical_proposal)
            if canonical_proposal is not None
            else None
        ),
        "config_hash": canonical.canonical_sha256(config) if config is not None else None,
        "alpha_id": canonical_proposal["alpha_id"] if canonical_proposal is not None else None,
        "parsed_at_us": 15 + ordinal,
    }


def _empty_candidate_chain_head(*, authority_id: str, family_id: str, search_id: str) -> str:
    return canonical.canonical_sha256(
        {
            "domain_separator": "QUANTOS_EMPTY_SEARCH_CANDIDATE_CHAIN_V1",
            "authority_instance_id": authority_id,
            "family_id": family_id,
            "search_id": search_id,
        }
    )


def _seal_fixture() -> tuple[dict[str, object], FrozenFamilySchemaVerificationView]:
    registrations = (
        _registration(1, raw_hash=_A),
        _registration(2, raw_hash=_D),
        _registration(3, branch="SECRET_QUARANTINED", quarantine_id="qtn_" + "f" * 32),
    )
    proposals = (
        _proposal_result(registrations[0], parse_status="ACCEPTED", alpha_id="alpha-1"),
        _proposal_result(registrations[1], parse_status="INVALID"),
        _proposal_result(registrations[2], parse_status="SECRET_QUARANTINED"),
    )
    accepted = sorted(
        {row["config_hash"] for row in proposals if row["parse_status"] == "ACCEPTED"}
    )
    candidate = {
        "candidate_seq": 1,
        "search_id": "search-1",
        "config_hash": proposals[0]["config_hash"],
        "registered_at": 20,
        "candidate_hash": _F,
    }
    events = (
        {
            "event_seq": 1,
            "search_id": "search-1",
            "status": "STARTED",
            "recorded_at": 10,
            "event_hash": _A,
        },
        {
            "event_seq": 2,
            "search_id": "search-1",
            "status": "COMPLETED",
            "recorded_at": 30,
            "event_hash": _B,
        },
    )
    seal = {
        "authority_instance_id": "authority-1",
        "family_id": "family-1",
        "search_id": "search-1",
        "actual_raw_proposal_count": len(registrations),
        "ordered_proposal_ids_hash": canonical.canonical_sha256([row["proposal_id"] for row in registrations]),
        "ordered_non_quarantined_raw_hashes_hash": canonical.canonical_sha256(
            [row["raw_envelope_hash"] for row in registrations if row["proposal_branch"] == "CLEAN"]
        ),
        "ordered_canonical_proposal_hashes_hash": canonical.canonical_sha256(
            [row["canonical_proposal_hash"] for row in proposals if row["parse_status"] == "ACCEPTED"]
        ),
        "ordered_quarantine_record_ids_hash": canonical.canonical_sha256(
            [row["quarantine_record_id"] for row in registrations if row["proposal_branch"] == "SECRET_QUARANTINED"]
        ),
        "accepted_config_set_hash": canonical.canonical_sha256(accepted),
        "candidate_count": 1,
        "candidate_chain_head": _F,
    }
    return seal, _view(
        ("data_snooping_proposal_registrations", registrations),
        ("data_snooping_proposals", proposals),
        ("search_candidates", (candidate,)),
        ("search_attempt_events", events),
    )


def test_registry_exactly_covers_genesis_c4_c5_declarations_and_descriptors() -> None:
    declared = _declared_named_bindings()
    component_prefixes = (
        "FAMILY_AUTHORITY_GENESIS_V1:",
        f"{C4_SCHEMA_COMPONENT.component_id}:",
        f"{C5_SCHEMA_COMPONENT.component_id}:",
    )
    descriptors = tuple(
        (item.binding_key, item.verifier_code)
        for item in FAMILY_SCHEMA_VERIFIER_BINDINGS
        if item.binding_key.startswith(component_prefixes)
    )
    implementations = GENESIS_C4_C5_NAMED_VERIFIER_IMPLEMENTATIONS

    assert len(declared) == len(descriptors) == 37
    assert set(declared) == set(descriptors)
    assert len(implementations) == len({code for _, code in declared}) == 31
    assert all(type(item) is GenesisC4C5NamedVerifierImplementation for item in implementations)
    assert {item.verifier_code for item in implementations} == {code for _, code in declared}
    by_code = {item.verifier_code: item for item in implementations}
    for code in by_code:
        assert by_code[code].binding_keys == tuple(key for key, candidate in declared if candidate == code)


def test_registry_callables_have_exact_three_parameter_abi_and_material_helper() -> None:
    for item in GENESIS_C4_C5_NAMED_VERIFIER_IMPLEMENTATIONS:
        implementation = item.implementation
        assert type(implementation) is FunctionType
        parameters = tuple(inspect.signature(implementation).parameters.values())
        assert tuple(parameter.name for parameter in parameters) == (
            "owner_row",
            "view",
            "providers",
        )
        assert all(parameter.default is inspect.Parameter.empty for parameter in parameters)
        helper_names = tuple(
            name
            for name in implementation.__code__.co_names
            if name.startswith("_verify_") and type(implementation.__globals__.get(name)) is FunctionType
        )
        assert "_invoke" in implementation.__code__.co_names
        assert len(helper_names) == 1

    blocked = tuple(item for item in GENESIS_C4_C5_NAMED_VERIFIER_IMPLEMENTATIONS if not item.production_ready)
    assert blocked == ()


def test_genesis_named_verifiers_recompute_hash_and_require_exact_singleton_read() -> None:
    row = _genesis_row()
    view = _view(("family_authority_genesis", (row,)))
    for code in (
        "family_schema.verify_genesis_row_hash_v1",
        "family_schema.verify_genesis_read_v1",
    ):
        assert verify_genesis_c4_c5_named(code, row, view, None)

    for field, value in (
        ("record_hash", _D),
        ("singleton_key", 2),
        ("migration_id", "FAMILY_SCHEMA_V2_TO_V3_BYPASS"),
        ("record_type", "WRONG"),
        ("activated_at_us", 0),
    ):
        tampered = {**row, field: value}
        assert not verify_genesis_c4_c5_named(
            "family_schema.verify_genesis_read_v1",
            tampered,
            view,
            None,
        )
    assert not verify_genesis_c4_c5_named(
        "family_schema.verify_genesis_read_v1",
        row,
        _view(("family_authority_genesis", (row, row))),
        None,
    )


@pytest.mark.parametrize(
    "rule_id",
    sorted({item.verification_rule_id for item in C4_IDENTITY_ISSUANCE}),
)
def test_c4_identity_verifiers_recompute_exact_recipe_and_predecessor(rule_id: str) -> None:
    owner, view = _identity_owner(rule_id)
    code = f"c4.verify_{rule_id}_v1"
    assert verify_genesis_c4_c5_named(code, owner, view, None)

    recipes = tuple(item for item in C4_IDENTITY_ISSUANCE if item.verification_rule_id == rule_id)
    for recipe in recipes:
        assert not verify_genesis_c4_c5_named(
            code,
            {**owner, recipe.field_name: _ZERO},
            view,
            None,
        )
    if recipes[0].table_name != "research_authority_events":
        wrong_view = _view(
            (
                "research_authority_events",
                ({"event_id": _E, "prev_record_hash": _F},),
            ),
        )
        assert not verify_genesis_c4_c5_named(code, owner, wrong_view, None)


@pytest.mark.parametrize(
    "code",
    (
        "c4.verify_research_question_aliases_canonical_alias_hash_v1",
        "c4.verify_selection_scope_aliases_canonical_alias_hash_v1",
    ),
)
def test_c4_alias_hash_verifiers_use_canonical_single_field_object(code: str) -> None:
    owner = {"alias": "research-alias", "alias_hash": canonical.canonical_sha256({"alias": "research-alias"})}
    assert verify_genesis_c4_c5_named(code, owner, _view(), None)
    assert not verify_genesis_c4_c5_named(code, {**owner, "alias_hash": _A}, _view(), None)
    assert not verify_genesis_c4_c5_named(code, {**owner, "alias": "e\u0301"}, _view(), None)


def test_c4_decision_class_slot_policy_closes_publishable_and_diagnostic_branches() -> None:
    code = "c4.verify_selection_decisions_decision_class_slot_policy_v1"
    slot = {"selection_scope_id": _A, "selection_decision_id": _B}
    publishable = {
        **slot,
        "decision_ordinal": 1,
        "decision_class": "PUBLISHABLE",
        "prev_record_hash": _ZERO,
    }
    diagnostic = {
        **publishable,
        "selection_decision_id": _C,
        "decision_ordinal": 2,
        "decision_class": "DIAGNOSTIC_ONLY",
    }
    view = _view(("selection_scope_publishable_decisions", (slot,)))
    assert verify_genesis_c4_c5_named(code, publishable, view, None)
    assert verify_genesis_c4_c5_named(code, diagnostic, view, None)
    assert not verify_genesis_c4_c5_named(code, {**diagnostic, "decision_class": "PUBLISHABLE"}, view, None)
    assert not verify_genesis_c4_c5_named(code, publishable, _view(("selection_scope_publishable_decisions", ())), None)


def test_c4_external_grant_and_actor_verifiers_call_typed_trust_roots() -> None:
    event = _governed_event()
    providers, grant, actor = _provider_bundle(event)
    assert verify_genesis_c4_c5_named(
        "c4.verify_research_authority_events_grant_policy_v1",
        event,
        _view(),
        providers,
    )
    assert verify_genesis_c4_c5_named(
        "c4.verify_research_authority_events_audit_actor_provenance_v1",
        event,
        _view(),
        providers,
    )
    assert len(grant.requests) == len(actor.requests) == 1
    assert grant.requests[0].grant_id == event["grant_id"]
    assert actor.requests[0].actor_id == event["audit_actor_id"]

    assert not verify_genesis_c4_c5_named(
        "c4.verify_research_authority_events_grant_policy_v1",
        event,
        _view(),
        None,
    )
    assert not verify_genesis_c4_c5_named(
        "c4.verify_research_authority_events_audit_actor_provenance_v1",
        event,
        _view(),
        C4ExternalProviderBundle(None, None),
    )


def test_c4_external_verifiers_reject_provider_error_wrong_binding_and_false_claim() -> None:
    event = _governed_event()
    grant_evidence = _grant_evidence(event)
    actor_evidence = _actor_evidence(event)
    bad_claim = replace(
        grant_evidence,
        claims=replace(grant_evidence.claims, complete_hash_chain=False),
    )
    cases = (
        C4ExternalProviderBundle(_GrantProvider(None, raises=True), _ActorProvider(actor_evidence)),
        C4ExternalProviderBundle(_GrantProvider(bad_claim), _ActorProvider(actor_evidence)),
        C4ExternalProviderBundle(
            _GrantProvider(replace(grant_evidence, subject_id=_F)),
            _ActorProvider(actor_evidence),
        ),
    )
    for providers in cases:
        assert not verify_genesis_c4_c5_named(
            "c4.verify_research_authority_events_grant_policy_v1",
            event,
            _view(),
            providers,
        )
    false_actor = replace(
        actor_evidence,
        claims=replace(actor_evidence.claims, evidence_hash_verified=False),
    )
    assert not verify_genesis_c4_c5_named(
        "c4.verify_research_authority_events_audit_actor_provenance_v1",
        event,
        _view(),
        C4ExternalProviderBundle(_GrantProvider(grant_evidence), _ActorProvider(false_actor)),
    )
    assert not verify_genesis_c4_c5_named(
        "c4.verify_research_authority_events_audit_actor_provenance_v1",
        event,
        _view(),
        C4ExternalProviderBundle(
            _GrantProvider(grant_evidence),
            _ActorProvider(None, raises=True),
        ),
    )


def test_c4_non_governance_actions_require_all_grant_fields_absent_without_provider_call() -> None:
    event = {
        **_governed_event(),
        "action": "RESEARCH_PROGRAM_ISSUED",
        "subject_record_type": "RESEARCH_PROGRAM_V1",
        "subject_id": _C,
        **{field: None for field in (
            "grant_id",
            "grant_record_hash",
            "grant_policy_id",
            "grant_policy_version",
            "grant_policy_hash",
            "grant_authority_id",
            "grant_authority_head",
        )},
    }
    assert verify_genesis_c4_c5_named(
        "c4.verify_research_authority_events_grant_policy_v1",
        event,
        _view(),
        None,
    )
    assert not verify_genesis_c4_c5_named(
        "c4.verify_research_authority_events_grant_policy_v1",
        {**event, "grant_id": "forged-partial-grant"},
        _view(),
        None,
    )


def test_c4_subject_grant_adapter_requires_exact_event_and_verified_grant() -> None:
    event = _governed_event()
    providers, grant, _ = _provider_bundle(event)
    owner = {
        "authority_instance_id": event["authority_instance_id"],
        "genesis_record_hash": event["genesis_record_hash"],
        "authority_event_id": event["event_id"],
        "record_type": event["subject_record_type"],
        "research_question_id": event["subject_id"],
        "record_hash": event["subject_record_hash"],
    }
    code = "c4.verify_subject_grant_required_v1"
    view = _view(("research_authority_events", (event,)))
    assert verify_genesis_c4_c5_named(code, owner, view, providers)
    assert len(grant.requests) == 1
    assert not verify_genesis_c4_c5_named(code, {**owner, "record_hash": _F}, view, providers)
    assert not verify_genesis_c4_c5_named(
        code,
        owner,
        _view(("research_authority_events", ({**event, "subject_id": _F},))),
        providers,
    )


def test_c5_proposal_branch_public_id_and_identity_payload_are_exact() -> None:
    clean_registration = _registration(1)
    quarantine_registration = _registration(
        2,
        branch="SECRET_QUARANTINED",
        quarantine_id="qtn_" + "a" * 32,
    )
    clean = _proposal_result(clean_registration, parse_status="INVALID")
    quarantine = _proposal_result(
        quarantine_registration,
        parse_status="SECRET_QUARANTINED",
    )
    codes = (
        "c5.verify_proposal_branch_totality_composite_v1",
        "c5.verify_proposal_public_id_v1",
        "c5.verify_proposal_identity_payload_v1",
    )
    for row, registration in (
        (clean, clean_registration),
        (quarantine, quarantine_registration),
    ):
        view = _view(("data_snooping_proposal_registrations", (registration,)))
        for code in codes:
            assert verify_genesis_c4_c5_named(code, row, view, None)
    assert verify_genesis_c4_c5_named(
        "c5.verify_quarantine_public_id_safe_formula_v1",
        quarantine,
        _view(("data_snooping_proposal_registrations", (quarantine_registration,))),
        None,
    )
    assert verify_genesis_c4_c5_named(
        "c5.verify_quarantine_public_id_safe_formula_v1",
        clean,
        _view(("data_snooping_proposal_registrations", (clean_registration,))),
        None,
    )
    assert not verify_genesis_c4_c5_named(
        "c5.verify_proposal_branch_totality_composite_v1",
        _proposal_result(clean_registration, parse_status="SECRET_QUARANTINED"),
        _view(("data_snooping_proposal_registrations", (clean_registration,))),
        None,
    )
    assert not verify_genesis_c4_c5_named(
        "c5.verify_proposal_branch_totality_composite_v1",
        _proposal_result(quarantine_registration, parse_status="INVALID"),
        _view(("data_snooping_proposal_registrations", (quarantine_registration,))),
        None,
    )
    assert not verify_genesis_c4_c5_named(
        "c5.verify_proposal_branch_totality_composite_v1",
        {
            **_proposal_result(clean_registration, parse_status="INVALID"),
            "parsed_at_us": int(clean_registration["registered_at_us"]) - 1,
        },
        _view(("data_snooping_proposal_registrations", (clean_registration,))),
        None,
    )

    for code, tampered_registration in (
        (
            "c5.verify_proposal_branch_totality_composite_v1",
            {**clean_registration, "raw_byte_length": None},
        ),
        (
            "c5.verify_proposal_public_id_v1",
            {**clean_registration, "proposal_id": _F},
        ),
        (
            "c5.verify_proposal_identity_payload_v1",
            {**clean_registration, "identity_payload_json": "{}"},
        ),
        (
            "c5.verify_quarantine_public_id_safe_formula_v1",
            {**quarantine_registration, "proposal_id": _F},
        ),
    ):
        owner = clean if code != "c5.verify_quarantine_public_id_safe_formula_v1" else quarantine
        assert not verify_genesis_c4_c5_named(
            code,
            owner,
            _view(("data_snooping_proposal_registrations", (tampered_registration,))),
            None,
        )


def test_c5_canonical_identity_verifier_recomputes_exact_terminal_bytes() -> None:
    code = "c5.verify_proposal_canonical_identity_from_canonical_bytes_v1"
    registration = _registration(1)
    accepted = _proposal_result(registration, parse_status="ACCEPTED", alpha_id="alpha-1")
    invalid = _proposal_result(registration, parse_status="INVALID")
    assert verify_genesis_c4_c5_named(code, accepted, _view(), None)
    assert verify_genesis_c4_c5_named(code, invalid, _view(), None)
    for field, replacement in (
        ("canonical_proposal_hash", _F),
        ("config_hash", _F),
        ("alpha_id", "alpha-forged"),
        ("canonical_proposal_json", "{}\n"),
    ):
        assert not verify_genesis_c4_c5_named(
            code,
            {**accepted, field: replacement},
            _view(),
            None,
        )
    assert not verify_genesis_c4_c5_named(
        code,
        {**invalid, "canonical_proposal_hash": _F},
        _view(),
        None,
    )


def test_c5_family_search_registration_requires_one_well_formed_attempt() -> None:
    code = "c5.verify_family_search_registration_totality_v1"
    owner = {"search_id": "search-1"}
    events = (
        {"event_seq": 1, "search_id": "search-1", "status": "STARTED", "recorded_at": 10},
        {"event_seq": 2, "search_id": "search-1", "status": "COMPLETED", "recorded_at": 20},
    )
    assert verify_genesis_c4_c5_named(code, owner, _view(("search_attempt_events", events)), None)
    assert not verify_genesis_c4_c5_named(code, owner, _view(("search_attempt_events", (events[1],))), None)
    assert not verify_genesis_c4_c5_named(
        code,
        owner,
        _view(("search_attempt_events", (*events, {**events[0], "event_seq": 3}))),
        None,
    )


def test_all_c5_emission_seal_verifiers_recompute_rows_and_terminal_provenance() -> None:
    seal, view = _seal_fixture()
    codes_by_field = {
        "c5.verify_emission_seal_actual_proposal_count_v1": "actual_raw_proposal_count",
        "c5.verify_emission_seal_ordered_proposal_ids_v1": "ordered_proposal_ids_hash",
        "c5.verify_emission_seal_ordered_non_quarantined_raw_hashes_v1": (
            "ordered_non_quarantined_raw_hashes_hash"
        ),
        "c5.verify_emission_seal_ordered_canonical_proposal_hashes_v1": (
            "ordered_canonical_proposal_hashes_hash"
        ),
        "c5.verify_emission_seal_ordered_quarantine_record_ids_v1": (
            "ordered_quarantine_record_ids_hash"
        ),
        "c5.verify_emission_seal_accepted_config_set_v1": "accepted_config_set_hash",
        "c5.verify_emission_seal_candidate_chain_head_v1": "candidate_chain_head",
    }
    for code, field in codes_by_field.items():
        assert verify_genesis_c4_c5_named(code, seal, view, None), code
        wrong = 99 if field == "actual_raw_proposal_count" else _A
        assert not verify_genesis_c4_c5_named(code, {**seal, field: wrong}, view, None), code
    assert not verify_genesis_c4_c5_named(
        "c5.verify_emission_seal_candidate_chain_head_v1",
        {**seal, "candidate_count": 0},
        view,
        None,
    )
    assert verify_genesis_c4_c5_named(
        "c5.verify_emission_seal_terminal_result_totality_v1",
        seal,
        view,
        None,
    )
    terminal_code = "c5.verify_emission_seal_terminal_search_from_event_chain_v1"
    assert verify_genesis_c4_c5_named(terminal_code, seal, view, None)
    assert not verify_genesis_c4_c5_named(
        terminal_code,
        seal,
        _view(
            (
                "data_snooping_proposal_registrations",
                view.rows("data_snooping_proposal_registrations"),
            ),
            ("data_snooping_proposals", view.rows("data_snooping_proposals")),
            ("search_candidates", view.rows("search_candidates")),
            (
                "search_attempt_events",
                ({"event_seq": 1, "search_id": "search-1", "status": "STARTED", "recorded_at": 10},),
            ),
        ),
        None,
    )


@pytest.mark.parametrize(
    ("code", "source_table", "proposal_field", "replacement"),
    (
        (
            "c5.verify_emission_seal_ordered_proposal_ids_v1",
            "data_snooping_proposal_registrations",
            "proposal_id",
            _F,
        ),
        (
            "c5.verify_emission_seal_ordered_non_quarantined_raw_hashes_v1",
            "data_snooping_proposal_registrations",
            "raw_envelope_hash",
            _F,
        ),
        (
            "c5.verify_emission_seal_ordered_canonical_proposal_hashes_v1",
            "data_snooping_proposals",
            "canonical_proposal_hash",
            _F,
        ),
        (
            "c5.verify_emission_seal_ordered_quarantine_record_ids_v1",
            "data_snooping_proposal_registrations",
            "quarantine_record_id",
            "qtn_" + "0" * 32,
        ),
        (
            "c5.verify_emission_seal_accepted_config_set_v1",
            "data_snooping_proposals",
            "config_hash",
            _F,
        ),
    ),
)
def test_c5_emission_seals_reject_referenced_proposal_tamper(
    code: str,
    source_table: str,
    proposal_field: str,
    replacement: object,
) -> None:
    seal, view = _seal_fixture()
    registrations = [
        dict(row) for row in view.rows("data_snooping_proposal_registrations")
    ]
    proposals = [dict(row) for row in view.rows("data_snooping_proposals")]
    index = 2 if proposal_field == "quarantine_record_id" else 0
    source = registrations if source_table == "data_snooping_proposal_registrations" else proposals
    source[index][proposal_field] = replacement
    tampered = _view(
        ("data_snooping_proposal_registrations", tuple(registrations)),
        ("data_snooping_proposals", tuple(proposals)),
        ("search_candidates", view.rows("search_candidates")),
        ("search_attempt_events", view.rows("search_attempt_events")),
    )
    assert not verify_genesis_c4_c5_named(code, seal, tampered, None)


def test_c5_explicit_terminal_result_totality_rejects_missing_or_duplicate_result() -> None:
    seal, view = _seal_fixture()
    registrations = view.rows("data_snooping_proposal_registrations")
    proposals = view.rows("data_snooping_proposals")
    candidates = view.rows("search_candidates")
    events = view.rows("search_attempt_events")
    malformed_views = (
        _view(
            ("data_snooping_proposal_registrations", registrations),
            ("data_snooping_proposals", proposals[:-1]),
            ("search_candidates", candidates),
            ("search_attempt_events", events),
        ),
        _view(
            ("data_snooping_proposal_registrations", registrations),
            ("data_snooping_proposals", (*proposals, proposals[0])),
            ("search_candidates", candidates),
            ("search_attempt_events", events),
        ),
    )
    for malformed in malformed_views:
        assert not verify_genesis_c4_c5_named(
            "c5.verify_emission_seal_terminal_result_totality_v1",
            seal,
            malformed,
            None,
        )

    wrong_binding = {**dict(proposals[0]), "registration_record_hash": _F}
    assert not verify_genesis_c4_c5_named(
        "c5.verify_proposal_branch_totality_composite_v1",
        wrong_binding,
        _view(("data_snooping_proposal_registrations", registrations)),
        None,
    )


def test_c5_zero_proposal_zero_candidate_seal_uses_domain_separated_empty_head() -> None:
    authority_id = "authority-1"
    family_id = "family-1"
    search_id = "search-empty"
    seal = {
        "authority_instance_id": authority_id,
        "family_id": family_id,
        "search_id": search_id,
        "actual_raw_proposal_count": 0,
        "ordered_proposal_ids_hash": canonical.canonical_sha256([]),
        "ordered_non_quarantined_raw_hashes_hash": canonical.canonical_sha256([]),
        "ordered_canonical_proposal_hashes_hash": canonical.canonical_sha256([]),
        "ordered_quarantine_record_ids_hash": canonical.canonical_sha256([]),
        "accepted_config_set_hash": canonical.canonical_sha256([]),
        "candidate_count": 0,
        "candidate_chain_head": _empty_candidate_chain_head(
            authority_id=authority_id,
            family_id=family_id,
            search_id=search_id,
        ),
    }
    events = (
        {"event_seq": 1, "search_id": search_id, "status": "STARTED", "recorded_at": 10},
        {"event_seq": 2, "search_id": search_id, "status": "COMPLETED", "recorded_at": 20},
    )
    view = _view(
        ("data_snooping_proposal_registrations", ()),
        ("data_snooping_proposals", ()),
        ("search_candidates", ()),
        ("search_attempt_events", events),
    )
    codes = (
        "c5.verify_emission_seal_actual_proposal_count_v1",
        "c5.verify_emission_seal_terminal_result_totality_v1",
        "c5.verify_emission_seal_ordered_proposal_ids_v1",
        "c5.verify_emission_seal_ordered_non_quarantined_raw_hashes_v1",
        "c5.verify_emission_seal_ordered_canonical_proposal_hashes_v1",
        "c5.verify_emission_seal_ordered_quarantine_record_ids_v1",
        "c5.verify_emission_seal_accepted_config_set_v1",
        "c5.verify_emission_seal_candidate_chain_head_v1",
        "c5.verify_emission_seal_terminal_search_from_event_chain_v1",
    )
    for code in codes:
        assert verify_genesis_c4_c5_named(code, seal, view, None), code
    assert not verify_genesis_c4_c5_named(
        "c5.verify_emission_seal_candidate_chain_head_v1",
        {**seal, "candidate_chain_head": _A},
        view,
        None,
    )
    assert not verify_genesis_c4_c5_named(
        "c5.verify_emission_seal_candidate_chain_head_v1",
        {**seal, "candidate_count": 1},
        view,
        None,
    )


def test_c5_seal_named_verifiers_use_only_their_declared_aggregate_sources() -> None:
    seal, view = _seal_fixture()
    registration_only = _view(
        (
            "data_snooping_proposal_registrations",
            view.rows("data_snooping_proposal_registrations"),
        )
    )
    for code in (
        "c5.verify_emission_seal_actual_proposal_count_v1",
        "c5.verify_emission_seal_ordered_proposal_ids_v1",
        "c5.verify_emission_seal_ordered_non_quarantined_raw_hashes_v1",
        "c5.verify_emission_seal_ordered_quarantine_record_ids_v1",
    ):
        assert verify_genesis_c4_c5_named(code, seal, registration_only, None), code

    terminal_only = _view(
        ("data_snooping_proposals", view.rows("data_snooping_proposals"))
    )
    for code in (
        "c5.verify_emission_seal_ordered_canonical_proposal_hashes_v1",
        "c5.verify_emission_seal_accepted_config_set_v1",
    ):
        assert verify_genesis_c4_c5_named(code, seal, terminal_only, None), code

    candidate_only = _view(("search_candidates", view.rows("search_candidates")))
    assert verify_genesis_c4_c5_named(
        "c5.verify_emission_seal_candidate_chain_head_v1",
        seal,
        candidate_only,
        None,
    )
    assert verify_genesis_c4_c5_named(
        "c5.verify_emission_seal_terminal_search_from_event_chain_v1",
        seal,
        _view(
            ("search_candidates", view.rows("search_candidates")),
            ("search_attempt_events", view.rows("search_attempt_events")),
        ),
        None,
    )


@pytest.mark.parametrize("bad_code", [None, 1, "", "c5.verify_unknown_v1"])
def test_dispatcher_rejects_unknown_codes_and_malformed_inputs(bad_code: object) -> None:
    assert not verify_genesis_c4_c5_named(bad_code, {}, _view(), None)
    code = "family_schema.verify_genesis_row_hash_v1"
    assert not verify_genesis_c4_c5_named(code, None, _view(), None)
    assert not verify_genesis_c4_c5_named(code, {}, object(), None)
