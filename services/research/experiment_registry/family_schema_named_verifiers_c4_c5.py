"""Executable named verifiers for authority genesis, C4, and C5.

All functions consume immutable SQLite-shaped snapshots and use the common
three-argument named-verifier ABI.  C4 external claims cross only the typed
provider seam; missing providers, provider exceptions, malformed evidence, or
false claims reject.  C5 verifies the append-only registration/result split,
exact canonical proposal bytes, and every emission-seal aggregate without
reading restricted raw proposal material.
"""

from __future__ import annotations

import inspect
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from types import FunctionType

from quantos.contracts import canonical as _canonical

from .family_schema_c4 import (
    C4_ACTION_POLICIES,
    C4_IDENTITY_ISSUANCE,
    C4_SCHEMA_COMPONENT,
)
from .family_schema_c5_c6 import C5_SCHEMA_COMPONENT
from .family_schema_external_verification_c4 import (
    ActorProvenanceClaims,
    ActorProvenanceEvidence,
    ActorProvenanceVerificationRequest,
    C4ExternalProviderBundle,
    MultiPartyGrantClaims,
    MultiPartyGrantEvidence,
    MultiPartyGrantVerificationRequest,
)
from .family_schema_spec import FAMILY_AUTHORITY_GENESIS_TABLE_SPEC
from .family_schema_verification_view import (
    FamilySchemaVerificationView,
    FrozenVerificationRow,
)
from .family_schema_verifier_registry import FAMILY_SCHEMA_VERIFIER_BINDINGS


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_QUARANTINE_ID_RE = re.compile(r"^qtn_[0-9a-f]{32}$")
_EMPTY_CANDIDATE_CHAIN_DOMAIN = "QUANTOS_EMPTY_SEARCH_CANDIDATE_CHAIN_V1"
_PROPOSAL_LENGTH_BUCKETS = (
    "LE_1_KIB",
    "GT_1_LE_4_KIB",
    "GT_4_LE_16_KIB",
    "GT_16_LE_64_KIB",
    "GT_64_LE_256_KIB",
    "GT_256_LE_1024_KIB",
    "GT_1_MIB",
)
_GENESIS_COMPONENT_ID = "FAMILY_AUTHORITY_GENESIS_V1"
_GENESIS_FIELDS = tuple(column.name for column in FAMILY_AUTHORITY_GENESIS_TABLE_SPEC.columns)
_GENESIS_HASH_FIELDS = FAMILY_AUTHORITY_GENESIS_TABLE_SPEC.row_hash.canonical_fields
_GRANT_REFERENCE_FIELDS = (
    "grant_id",
    "grant_record_hash",
    "grant_policy_id",
    "grant_policy_version",
    "grant_policy_hash",
    "grant_authority_id",
    "grant_authority_head",
)
_GOVERNED_SUBJECTS = (
    ("RESEARCH_QUESTION_V1", "research_question_id", "QUESTION_ISSUED"),
    ("RESEARCH_QUESTION_ALIAS_V1", "alias_hash", "QUESTION_ALIAS_REGISTERED"),
    ("STRATEGY_LINEAGE_ROOT_V1", "strategy_lineage_root_id", "LINEAGE_ROOT_REGISTERED"),
    ("STRATEGY_LINEAGE_NODE_V1", "strategy_lineage_node_id", "LINEAGE_NODE_REGISTERED"),
    ("STRATEGY_CONFIG_LINEAGE_BINDING_V1", "config_hash", "CONFIG_LINEAGE_BOUND"),
    ("SELECTION_SCOPE_V1", "selection_scope_id", "SELECTION_SCOPE_ISSUED"),
    ("SELECTION_SCOPE_ALIAS_V1", "alias_hash", "SELECTION_SCOPE_ALIAS_REGISTERED"),
)


class _VerificationInputError(ValueError):
    """A required scalar, row, provider response, or relation is malformed."""


def _text(row: Mapping[str, object], field: str, *, allow_empty: bool = False) -> str:
    value = row[field]
    if (
        type(value) is not str
        or (not allow_empty and not value)
        or unicodedata.normalize("NFC", value) != value
    ):
        raise _VerificationInputError(f"{field} must be exact NFC text")
    return value


def _sha256(row: Mapping[str, object], field: str) -> str:
    value = _text(row, field)
    if _SHA256_RE.fullmatch(value) is None:
        raise _VerificationInputError(f"{field} must be lowercase SHA-256 text")
    return value


def _nullable_text(row: Mapping[str, object], field: str) -> str | None:
    if row[field] is None:
        return None
    return _text(row, field)


def _nullable_sha256(row: Mapping[str, object], field: str) -> str | None:
    if row[field] is None:
        return None
    return _sha256(row, field)


def _positive_integer(row: Mapping[str, object], field: str) -> int:
    value = row[field]
    if type(value) is not int or value <= 0:
        raise _VerificationInputError(f"{field} must be a positive exact INTEGER")
    return value


def _nonnegative_integer(row: Mapping[str, object], field: str) -> int:
    value = row[field]
    if type(value) is not int or value < 0:
        raise _VerificationInputError(f"{field} must be a non-negative exact INTEGER")
    return value


def _owner_row(owner_row: object) -> FrozenVerificationRow:
    if not isinstance(owner_row, Mapping):
        raise _VerificationInputError("owner row must be a Mapping")
    return FrozenVerificationRow.from_mapping(owner_row)


def _view_rows(
    view: FamilySchemaVerificationView,
    table_name: str,
) -> tuple[FrozenVerificationRow, ...]:
    if not isinstance(view, FamilySchemaVerificationView):
        raise _VerificationInputError("verification view does not implement rows()")
    rows = view.rows(table_name)
    if type(rows) is not tuple:
        raise _VerificationInputError("verification view rows must be a tuple")
    return tuple(FrozenVerificationRow.from_mapping(row) for row in rows)


def _exactly_one(
    rows: tuple[FrozenVerificationRow, ...],
    predicate: Callable[[FrozenVerificationRow], bool],
    context: str,
) -> FrozenVerificationRow:
    matches = tuple(row for row in rows if predicate(row))
    if len(matches) != 1:
        raise _VerificationInputError(f"{context} must resolve exactly once")
    return matches[0]


def _invoke(
    helper: Callable[[FrozenVerificationRow, FamilySchemaVerificationView, object], bool],
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    try:
        owner = _owner_row(owner_row)
        if not isinstance(view, FamilySchemaVerificationView):
            return False
        return helper(owner, view, providers) is True
    except Exception:
        return False


def _verify_genesis_row_hash(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del view, providers
    if owner["singleton_key"] != 1:
        return False
    _text(owner, "authority_instance_id")
    _sha256(owner, "v2_origin_fingerprint")
    _sha256(owner, "sealed_v3_manifest_hash")
    _sha256(owner, "activation_code_manifest_evidence_hash")
    if _text(owner, "migration_id") != "FAMILY_SCHEMA_V2_TO_V3_V1":
        return False
    _positive_integer(owner, "activated_at_us")
    if _text(owner, "record_type") != "FAMILY_AUTHORITY_GENESIS_V1":
        return False
    expected = _canonical.canonical_sha256(
        {field: owner[field] for field in _GENESIS_HASH_FIELDS}
    )
    return _sha256(owner, "record_hash") == expected


def _verify_genesis_read(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    if not _verify_genesis_row_hash(owner, view, providers):
        return False
    rows = _view_rows(view, "family_authority_genesis")
    if len(rows) != 1:
        return False
    stored = rows[0]
    return all(stored[field] == owner[field] for field in _GENESIS_FIELDS)


def _canonical_identity_source(value: object, field: str) -> object:
    if value is None:
        return None
    if type(value) is int:
        if not -(1 << 63) <= value <= (1 << 63) - 1:
            raise _VerificationInputError(f"{field} is outside SQLite INTEGER range")
        return value
    if type(value) is str and value and unicodedata.normalize("NFC", value) == value:
        return value
    raise _VerificationInputError(f"{field} is not an exact canonical scalar")


def _identity_predecessor(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    recipe: object,
) -> str:
    predecessor = recipe.predecessor_source
    if predecessor.kind.value == "LOCAL_COLUMN":
        return _sha256(owner, predecessor.local_column)
    binding = _text(owner, predecessor.binding_field)
    rows = _view_rows(view, predecessor.source_table)
    event = _exactly_one(
        rows,
        lambda row: _text(row, predecessor.source_key_field) == binding,
        "identity authority event predecessor",
    )
    return _sha256(event, predecessor.source_value_field)


def _identity_rule_matches(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    rule_id: str,
) -> bool:
    recipes = tuple(
        recipe
        for recipe in C4_IDENTITY_ISSUANCE
        if recipe.verification_rule_id == rule_id
    )
    if not recipes:
        return False
    for recipe in recipes:
        predecessor = _identity_predecessor(owner, view, recipe)
        payload = {
            recipe.payload_schema_version_field: recipe.payload_schema_version,
            recipe.domain_separator_field: recipe.domain_separator,
            recipe.predecessor_field: predecessor,
            recipe.ordered_fields_field: [
                {
                    recipe.ordered_field_name_field: field,
                    recipe.ordered_field_value_field: _canonical_identity_source(
                        owner[field],
                        field,
                    ),
                }
                for field in recipe.source_fields
            ],
        }
        if _sha256(owner, recipe.field_name) != _canonical.canonical_sha256(payload):
            return False
    return True


def _verify_research_questions_question_id_issuance(
    owner: FrozenVerificationRow, view: FamilySchemaVerificationView, providers: object
) -> bool:
    del providers
    return _identity_rule_matches(owner, view, "research_questions_question_id_issuance")


def _verify_strategy_lineage_roots_root_id_issuance(
    owner: FrozenVerificationRow, view: FamilySchemaVerificationView, providers: object
) -> bool:
    del providers
    return _identity_rule_matches(owner, view, "strategy_lineage_roots_root_id_issuance")


def _verify_strategy_lineage_nodes_node_id_issuance(
    owner: FrozenVerificationRow, view: FamilySchemaVerificationView, providers: object
) -> bool:
    del providers
    return _identity_rule_matches(owner, view, "strategy_lineage_nodes_node_id_issuance")


def _verify_research_authority_events_event_id_issuance(
    owner: FrozenVerificationRow, view: FamilySchemaVerificationView, providers: object
) -> bool:
    del providers
    return _identity_rule_matches(owner, view, "research_authority_events_event_id_issuance")


def _verify_selection_scopes_scope_id_issuance(
    owner: FrozenVerificationRow, view: FamilySchemaVerificationView, providers: object
) -> bool:
    del providers
    return _identity_rule_matches(owner, view, "selection_scopes_scope_id_issuance")


def _verify_research_programs_program_id_issuance(
    owner: FrozenVerificationRow, view: FamilySchemaVerificationView, providers: object
) -> bool:
    del providers
    return _identity_rule_matches(owner, view, "research_programs_program_id_issuance")


def _verify_selection_decisions_decision_and_family_id_issuance(
    owner: FrozenVerificationRow, view: FamilySchemaVerificationView, providers: object
) -> bool:
    del providers
    return _identity_rule_matches(
        owner,
        view,
        "selection_decisions_decision_and_family_id_issuance",
    )


def _verify_global_oos_exposures_global_exposure_id(
    owner: FrozenVerificationRow, view: FamilySchemaVerificationView, providers: object
) -> bool:
    del providers
    return _identity_rule_matches(owner, view, "global_oos_exposures_global_exposure_id")


def _alias_hash_matches(owner: FrozenVerificationRow) -> bool:
    alias = _text(owner, "alias")
    return _sha256(owner, "alias_hash") == _canonical.canonical_sha256({"alias": alias})


def _verify_research_question_aliases_canonical_alias_hash(
    owner: FrozenVerificationRow, view: FamilySchemaVerificationView, providers: object
) -> bool:
    del view, providers
    return _alias_hash_matches(owner)


def _verify_selection_scope_aliases_canonical_alias_hash(
    owner: FrozenVerificationRow, view: FamilySchemaVerificationView, providers: object
) -> bool:
    del view, providers
    return _alias_hash_matches(owner)


def _verify_selection_decisions_decision_class_slot_policy(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    scope_id = _text(owner, "selection_scope_id")
    decision_id = _sha256(owner, "selection_decision_id")
    _positive_integer(owner, "decision_ordinal")
    _sha256(owner, "prev_record_hash")
    decision_class = _text(owner, "decision_class")
    if decision_class not in ("PUBLISHABLE", "DIAGNOSTIC_ONLY"):
        return False
    rows = _view_rows(view, "selection_scope_publishable_decisions")
    slot = _exactly_one(
        rows,
        lambda row: _text(row, "selection_scope_id") == scope_id,
        "publishable decision slot",
    )
    slot_decision = _sha256(slot, "selection_decision_id")
    return (decision_class == "PUBLISHABLE") == (decision_id == slot_decision)


def _action_policy(action: str) -> object:
    matches = tuple(policy for policy in C4_ACTION_POLICIES if policy.action == action)
    if len(matches) != 1:
        raise _VerificationInputError("authority action policy is not closed")
    return matches[0]


def _provider_bundle(providers: object) -> C4ExternalProviderBundle:
    if type(providers) is not C4ExternalProviderBundle:
        raise _VerificationInputError("C4 external provider bundle is missing")
    return providers


def _grant_request(owner: FrozenVerificationRow) -> MultiPartyGrantVerificationRequest:
    return MultiPartyGrantVerificationRequest(
        grant_id=_text(owner, "grant_id"),
        grant_record_hash=_sha256(owner, "grant_record_hash"),
        policy_id=_text(owner, "grant_policy_id"),
        policy_version=_text(owner, "grant_policy_version"),
        policy_hash=_sha256(owner, "grant_policy_hash"),
        authority_id=_text(owner, "grant_authority_id"),
        authority_head=_sha256(owner, "grant_authority_head"),
        action=_text(owner, "action"),
        subject_record_type=_text(owner, "subject_record_type"),
        subject_id=_text(owner, "subject_id"),
        subject_record_hash=_sha256(owner, "subject_record_hash"),
        consumed_by_actor_id=_text(owner, "audit_actor_id"),
        consumed_at_us=_positive_integer(owner, "event_at_us"),
    )


def _grant_claims_pass(claims: MultiPartyGrantClaims) -> bool:
    return (
        claims.state == "CONSUMED"
        and claims.verified_human_actor_count >= 3
        and claims.distinct_human_actors is True
        and claims.complete_hash_chain is True
        and claims.grant_not_reused is True
        and claims.subject_action_bound is True
        and claims.consumption_time_bound is True
        and claims.policy_verified is True
        and claims.authority_head_verified is True
    )


def _grant_evidence_matches(
    owner: FrozenVerificationRow,
    providers: object,
) -> bool:
    action = _text(owner, "action")
    policy = _action_policy(action)
    present = tuple(owner[field] is not None for field in _GRANT_REFERENCE_FIELDS)
    if policy.grant_required:
        if not all(present):
            return False
    else:
        return not any(present)
    bundle = _provider_bundle(providers)
    provider = bundle.multi_party_grant_evidence
    if provider is None:
        return False
    request = _grant_request(owner)
    evidence = provider.verify(request)
    if type(evidence) is not MultiPartyGrantEvidence:
        return False
    request_values = tuple(getattr(request, field.name) for field in fields(request))
    evidence_values = tuple(
        getattr(evidence, field.name)
        for field in fields(request)
    )
    return request_values == evidence_values and _grant_claims_pass(evidence.claims)


def _verify_research_authority_events_grant_policy(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del view
    return _grant_evidence_matches(owner, providers)


def _actor_request(owner: FrozenVerificationRow) -> ActorProvenanceVerificationRequest:
    return ActorProvenanceVerificationRequest(
        actor_id=_text(owner, "audit_actor_id"),
        actor_kind=_text(owner, "audit_actor_kind"),
        evidence_hash=_sha256(owner, "audit_actor_evidence_hash"),
        policy_id=_text(owner, "audit_actor_policy_id"),
        policy_version=_text(owner, "audit_actor_policy_version"),
        policy_hash=_sha256(owner, "audit_actor_policy_hash"),
        authority_id=_text(owner, "audit_actor_authority_id"),
        authority_head=_sha256(owner, "audit_actor_authority_head"),
        action=_text(owner, "action"),
        subject_record_type=_text(owner, "subject_record_type"),
        subject_id=_text(owner, "subject_id"),
        subject_record_hash=_sha256(owner, "subject_record_hash"),
        event_at_us=_positive_integer(owner, "event_at_us"),
    )


def _actor_claims_pass(claims: ActorProvenanceClaims) -> bool:
    return (
        claims.provenance_verified is True
        and claims.actor_kind_verified is True
        and claims.action_context_bound is True
        and claims.subject_context_bound is True
        and claims.event_time_bound is True
        and claims.evidence_hash_verified is True
        and claims.policy_verified is True
        and claims.authority_head_verified is True
    )


def _verify_research_authority_events_audit_actor_provenance(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del view
    bundle = _provider_bundle(providers)
    provider = bundle.actor_provenance_evidence
    if provider is None:
        return False
    request = _actor_request(owner)
    evidence = provider.verify(request)
    if type(evidence) is not ActorProvenanceEvidence:
        return False
    request_values = tuple(getattr(request, field.name) for field in fields(request))
    evidence_values = tuple(
        getattr(evidence, field.name)
        for field in fields(request)
    )
    return request_values == evidence_values and _actor_claims_pass(evidence.claims)


def _verify_subject_grant_required(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    record_type = _text(owner, "record_type")
    subjects = tuple(item for item in _GOVERNED_SUBJECTS if item[0] == record_type)
    if len(subjects) != 1:
        return False
    _, subject_column, expected_action = subjects[0]
    authority_id = _text(owner, "authority_instance_id")
    genesis_hash = _sha256(owner, "genesis_record_hash")
    event_id = _text(owner, "authority_event_id")
    subject_id = _text(owner, subject_column)
    subject_hash = _sha256(owner, "record_hash")
    events = _view_rows(view, "research_authority_events")
    event = _exactly_one(
        events,
        lambda row: _text(row, "event_id") == event_id,
        "subject authority event",
    )
    if (
        _text(event, "authority_instance_id") != authority_id
        or _sha256(event, "genesis_record_hash") != genesis_hash
        or _text(event, "action") != expected_action
        or _text(event, "subject_record_type") != record_type
        or _text(event, "subject_id") != subject_id
        or _sha256(event, "subject_record_hash") != subject_hash
    ):
        return False
    return _grant_evidence_matches(event, providers)


def _verify_proposal_branch_totality(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    registration = _proposal_registration_for_result(owner, view)
    return _registration_result_branch_matches(registration, owner)


def _proposal_registration_for_result(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> FrozenVerificationRow:
    authority_id = _text(owner, "authority_instance_id")
    family_id = _text(owner, "family_id")
    search_id = _text(owner, "search_id")
    ordinal = _positive_integer(owner, "emission_ordinal")
    proposal_id = _sha256(owner, "proposal_id")
    registration_hash = _sha256(owner, "registration_record_hash")
    return _exactly_one(
        _view_rows(view, "data_snooping_proposal_registrations"),
        lambda row: (
            _text(row, "authority_instance_id") == authority_id
            and _text(row, "family_id") == family_id
            and _text(row, "search_id") == search_id
            and _positive_integer(row, "emission_ordinal") == ordinal
            and _sha256(row, "proposal_id") == proposal_id
            and _sha256(row, "record_hash") == registration_hash
        ),
        "terminal proposal registration",
    )


def _proposal_length_bucket(raw_byte_length: int) -> str:
    for upper_bound, bucket in (
        (1024, "LE_1_KIB"),
        (4096, "GT_1_LE_4_KIB"),
        (16384, "GT_4_LE_16_KIB"),
        (65536, "GT_16_LE_64_KIB"),
        (262144, "GT_64_LE_256_KIB"),
        (1048576, "GT_256_LE_1024_KIB"),
    ):
        if raw_byte_length <= upper_bound:
            return bucket
    return "GT_1_MIB"


def _registration_branch_is_valid(registration: FrozenVerificationRow) -> bool:
    branch = _text(registration, "proposal_branch")
    dlp_result = _text(registration, "dlp_result_code")
    raw_hash = _nullable_sha256(registration, "raw_envelope_hash")
    receipt = _nullable_sha256(
        registration,
        "restricted_clean_blob_receipt_hash",
    )
    quarantine_id = _nullable_text(registration, "quarantine_record_id")
    raw_length = registration["raw_byte_length"]
    if _text(registration, "length_bucket") not in _PROPOSAL_LENGTH_BUCKETS:
        return False
    for field in (
        "media_type",
        "dlp_policy_id",
        "dlp_policy_version",
        "scanner_implementation",
        "scanner_version",
    ):
        _text(registration, field)
    _positive_integer(registration, "registered_at_us")
    if branch == "CLEAN":
        return (
            dlp_result == "CLEAN"
            and raw_hash is not None
            and type(raw_length) is int
            and raw_length > 0
            and _text(registration, "length_bucket")
            == _proposal_length_bucket(raw_length)
            and receipt is not None
            and quarantine_id is None
        )
    if branch != "SECRET_QUARANTINED" or dlp_result != "SECRET_QUARANTINED":
        return False
    return (
        raw_hash is None
        and raw_length is None
        and receipt is None
        and quarantine_id is not None
        and _QUARANTINE_ID_RE.fullmatch(quarantine_id) is not None
    )


def _terminal_parse_fields_are_closed(result: FrozenVerificationRow) -> bool:
    status = _text(result, "parse_status")
    parsed_fields = (
        result["canonical_proposal_json"],
        result["canonical_proposal_hash"],
        result["config_hash"],
        result["alpha_id"],
    )
    if status == "ACCEPTED":
        return all(value is not None for value in parsed_fields)
    if status not in ("INVALID", "SECRET_QUARANTINED"):
        return False
    return all(value is None for value in parsed_fields)


def _registration_result_branch_matches(
    registration: FrozenVerificationRow,
    result: FrozenVerificationRow,
) -> bool:
    if not _registration_branch_is_valid(registration):
        return False
    if not _terminal_parse_fields_are_closed(result):
        return False
    if _positive_integer(result, "parsed_at_us") < _positive_integer(
        registration,
        "registered_at_us",
    ):
        return False
    branch = _text(registration, "proposal_branch")
    status = _text(result, "parse_status")
    if branch == "CLEAN":
        return status in ("ACCEPTED", "INVALID")
    return branch == "SECRET_QUARANTINED" and status == "SECRET_QUARANTINED"


def _proposal_public_key(registration: FrozenVerificationRow) -> dict[str, object]:
    branch = _text(registration, "proposal_branch")
    payload: dict[str, object] = {
        "family_id": _text(registration, "family_id"),
        "search_id": _text(registration, "search_id"),
        "emission_ordinal": _positive_integer(registration, "emission_ordinal"),
    }
    if branch == "SECRET_QUARANTINED":
        payload["status"] = "SECRET_QUARANTINED"
    elif branch == "CLEAN":
        payload["raw_envelope_hash"] = _sha256(
            registration,
            "raw_envelope_hash",
        )
    else:
        raise _VerificationInputError("proposal branch is not closed")
    return payload


def _verify_quarantine_public_id_safe_formula(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    registration = _proposal_registration_for_result(owner, view)
    if _text(registration, "proposal_branch") != "SECRET_QUARANTINED":
        return True
    return _sha256(registration, "proposal_id") == _canonical.canonical_sha256(
        _proposal_public_key(registration)
    )


def _verify_proposal_public_id(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    registration = _proposal_registration_for_result(owner, view)
    return _sha256(registration, "proposal_id") == _canonical.canonical_sha256(
        _proposal_public_key(registration)
    )


def _proposal_identity_payload(
    registration: FrozenVerificationRow,
) -> dict[str, object]:
    return {
        "family_id": _text(registration, "family_id"),
        "search_id": _text(registration, "search_id"),
        "emission_ordinal": _positive_integer(registration, "emission_ordinal"),
        "proposal_id": _sha256(registration, "proposal_id"),
        "proposal_branch": _text(registration, "proposal_branch"),
        "raw_envelope_hash": _nullable_sha256(registration, "raw_envelope_hash"),
    }


def _verify_proposal_identity_payload(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    registration = _proposal_registration_for_result(owner, view)
    return _registration_identity_payload_is_valid(registration)


def _registration_identity_payload_is_valid(
    registration: FrozenVerificationRow,
) -> bool:
    text = _text(registration, "identity_payload_json")
    payload_bytes = text.encode("utf-8")
    parsed = _canonical.load_canonical_json_bytes(payload_bytes)
    expected = _proposal_identity_payload(registration)
    return parsed == expected and payload_bytes == _canonical.canonical_json_bytes(expected)


def _canonical_proposal_identities(
    canonical_proposal_json: str,
) -> tuple[str, str, str]:
    payload_bytes = canonical_proposal_json.encode("utf-8")
    proposal = _canonical.load_canonical_json_bytes(payload_bytes)
    if _canonical.canonical_json_bytes(proposal) != payload_bytes or type(proposal) is not dict:
        raise _VerificationInputError("canonical proposal must be one exact object")
    if set(proposal) != {"alpha_id", "factor_ids", "name", "params", "universe"}:
        raise _VerificationInputError("canonical proposal shape is not AlphaDefinition")
    alpha_id = proposal["alpha_id"]
    name = proposal["name"]
    factor_ids = proposal["factor_ids"]
    universe = proposal["universe"]
    params = proposal["params"]
    if type(alpha_id) is not str or not alpha_id or type(name) is not str or not name:
        raise _VerificationInputError("canonical proposal identifiers must be text")
    if type(factor_ids) is not list or any(
        type(item) is not str or not item for item in factor_ids
    ):
        raise _VerificationInputError("canonical factor IDs must be exact text")
    if type(universe) is not str:
        raise _VerificationInputError("canonical universe must be exact text")
    if type(params) is not dict or any(
        type(key) is not str or not key or type(value) is not str
        for key, value in params.items()
    ):
        raise _VerificationInputError("canonical params must be an exact text mapping")
    config = {"factor_ids": factor_ids, "universe": universe, "params": params}
    return (
        _canonical.canonical_sha256(proposal),
        _canonical.canonical_sha256(config),
        alpha_id,
    )


def _terminal_canonical_identity_is_valid(result: FrozenVerificationRow) -> bool:
    if not _terminal_parse_fields_are_closed(result):
        return False
    if _text(result, "parse_status") != "ACCEPTED":
        return True
    canonical_json = _text(result, "canonical_proposal_json")
    proposal_hash, config_hash, alpha_id = _canonical_proposal_identities(
        canonical_json
    )
    return (
        _sha256(result, "canonical_proposal_hash") == proposal_hash
        and _sha256(result, "config_hash") == config_hash
        and _text(result, "alpha_id") == alpha_id
    )


def _verify_proposal_canonical_identity_from_canonical_bytes(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del view, providers
    return _terminal_canonical_identity_is_valid(owner)


def _search_attempt_rows(
    view: FamilySchemaVerificationView,
    search_id: str,
) -> tuple[FrozenVerificationRow, ...]:
    matches = []
    seen_sequences: set[int] = set()
    for row in _view_rows(view, "search_attempt_events"):
        row_search_id = _text(row, "search_id")
        if row_search_id == search_id:
            sequence = _positive_integer(row, "event_seq")
            if sequence in seen_sequences:
                raise _VerificationInputError("search event sequence is duplicated")
            seen_sequences.add(sequence)
            _text(row, "status")
            _nonnegative_integer(row, "recorded_at")
            matches.append(row)
    rows = tuple(sorted(matches, key=lambda row: _positive_integer(row, "event_seq")))
    if len(rows) not in (1, 2) or _text(rows[0], "status") != "STARTED":
        raise _VerificationInputError("search attempt must have one STARTED row")
    if len(rows) == 2:
        if _text(rows[1], "status") not in ("FAILED", "COMPLETED"):
            raise _VerificationInputError("search terminal status is not closed")
        if _nonnegative_integer(rows[1], "recorded_at") < _nonnegative_integer(
            rows[0],
            "recorded_at",
        ):
            raise _VerificationInputError("search terminal precedes STARTED")
    return rows


def _verify_family_search_registration_totality(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    search_id = _text(owner, "search_id")
    return bool(_search_attempt_rows(view, search_id))


def _registration_public_id_is_valid(
    registration: FrozenVerificationRow,
) -> bool:
    return _sha256(
        registration,
        "proposal_id",
    ) == _canonical.canonical_sha256(_proposal_public_key(registration))


def _registration_rows_for_seal(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> tuple[FrozenVerificationRow, ...]:
    authority_id = _text(owner, "authority_instance_id")
    family_id = _text(owner, "family_id")
    search_id = _text(owner, "search_id")
    matches: list[FrozenVerificationRow] = []
    ordinals: set[int] = set()
    for row in _view_rows(view, "data_snooping_proposal_registrations"):
        row_family = _text(row, "family_id")
        row_search = _text(row, "search_id")
        if row_family == family_id and row_search == search_id:
            if _text(row, "authority_instance_id") != authority_id:
                raise _VerificationInputError("registration authority does not match seal")
            ordinal = _positive_integer(row, "emission_ordinal")
            if ordinal in ordinals:
                raise _VerificationInputError("registration emission ordinal is duplicated")
            ordinals.add(ordinal)
            _sha256(row, "proposal_id")
            _sha256(row, "record_hash")
            if not _registration_branch_is_valid(row):
                raise _VerificationInputError("registration branch is malformed")
            if not _registration_public_id_is_valid(row):
                raise _VerificationInputError("registration proposal ID is malformed")
            if not _registration_identity_payload_is_valid(row):
                raise _VerificationInputError("registration identity payload is malformed")
            matches.append(row)
    rows = tuple(sorted(matches, key=lambda row: _positive_integer(row, "emission_ordinal")))
    if tuple(_positive_integer(row, "emission_ordinal") for row in rows) != tuple(
        range(1, len(rows) + 1)
    ):
        raise _VerificationInputError("registration emission ordinals are not contiguous")
    return rows


def _result_rows_for_seal(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> tuple[FrozenVerificationRow, ...]:
    authority_id = _text(owner, "authority_instance_id")
    family_id = _text(owner, "family_id")
    search_id = _text(owner, "search_id")
    matches: list[FrozenVerificationRow] = []
    ordinals: set[int] = set()
    for row in _view_rows(view, "data_snooping_proposals"):
        row_family = _text(row, "family_id")
        row_search = _text(row, "search_id")
        if row_family == family_id and row_search == search_id:
            if _text(row, "authority_instance_id") != authority_id:
                raise _VerificationInputError("proposal result authority does not match seal")
            ordinal = _positive_integer(row, "emission_ordinal")
            if ordinal in ordinals:
                raise _VerificationInputError("proposal result ordinal is duplicated")
            ordinals.add(ordinal)
            _sha256(row, "proposal_id")
            _sha256(row, "registration_record_hash")
            _positive_integer(row, "parsed_at_us")
            if not _terminal_canonical_identity_is_valid(row):
                raise _VerificationInputError("terminal proposal identity is malformed")
            matches.append(row)
    rows = tuple(
        sorted(matches, key=lambda row: _positive_integer(row, "emission_ordinal"))
    )
    if tuple(_positive_integer(row, "emission_ordinal") for row in rows) != tuple(
        range(1, len(rows) + 1)
    ):
        raise _VerificationInputError("proposal result ordinals are not contiguous")
    return rows


def _terminal_result_totality_matches(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    registrations = _registration_rows_for_seal(owner, view)
    result_rows = _result_rows_for_seal(owner, view)
    expected = _nonnegative_integer(owner, "actual_raw_proposal_count")
    return len(registrations) == len(result_rows) == expected


def _verify_emission_seal_actual_proposal_count(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    registrations = _registration_rows_for_seal(owner, view)
    return _nonnegative_integer(owner, "actual_raw_proposal_count") == len(
        registrations
    )


def _verify_emission_seal_terminal_result_totality(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    return _terminal_result_totality_matches(owner, view)


def _verify_emission_seal_ordered_proposal_ids(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    registrations = _registration_rows_for_seal(owner, view)
    values = [_sha256(row, "proposal_id") for row in registrations]
    return _sha256(owner, "ordered_proposal_ids_hash") == _canonical.canonical_sha256(values)


def _verify_emission_seal_ordered_non_quarantined_raw_hashes(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    registrations = _registration_rows_for_seal(owner, view)
    values = [
        _sha256(row, "raw_envelope_hash")
        for row in registrations
        if _text(row, "proposal_branch") == "CLEAN"
    ]
    return _sha256(
        owner,
        "ordered_non_quarantined_raw_hashes_hash",
    ) == _canonical.canonical_sha256(values)


def _verify_emission_seal_ordered_canonical_proposal_hashes(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    results = _result_rows_for_seal(owner, view)
    values = [
        _sha256(row, "canonical_proposal_hash")
        for row in results
        if _text(row, "parse_status") == "ACCEPTED"
    ]
    return _sha256(
        owner,
        "ordered_canonical_proposal_hashes_hash",
    ) == _canonical.canonical_sha256(values)


def _verify_emission_seal_ordered_quarantine_record_ids(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    registrations = _registration_rows_for_seal(owner, view)
    values: list[str] = []
    for row in registrations:
        branch = _text(row, "proposal_branch")
        if branch == "SECRET_QUARANTINED":
            value = _text(row, "quarantine_record_id")
            if _QUARANTINE_ID_RE.fullmatch(value) is None:
                return False
            values.append(value)
        elif branch != "CLEAN":
            return False
    return _sha256(
        owner,
        "ordered_quarantine_record_ids_hash",
    ) == _canonical.canonical_sha256(values)


def _verify_emission_seal_accepted_config_set(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    results = _result_rows_for_seal(owner, view)
    values = sorted(
        {
            _sha256(row, "config_hash")
            for row in results
            if _text(row, "parse_status") == "ACCEPTED"
        }
    )
    return _sha256(owner, "accepted_config_set_hash") == _canonical.canonical_sha256(values)


def _candidate_rows_for_search(
    view: FamilySchemaVerificationView,
    search_id: str,
) -> tuple[FrozenVerificationRow, ...]:
    matches: list[FrozenVerificationRow] = []
    seen_sequences: set[int] = set()
    for row in _view_rows(view, "search_candidates"):
        if _text(row, "search_id") == search_id:
            sequence = _positive_integer(row, "candidate_seq")
            if sequence in seen_sequences:
                raise _VerificationInputError("candidate sequence is duplicated")
            seen_sequences.add(sequence)
            _sha256(row, "candidate_hash")
            _nonnegative_integer(row, "registered_at")
            matches.append(row)
    rows = tuple(
        sorted(matches, key=lambda row: _positive_integer(row, "candidate_seq"))
    )
    if tuple(_positive_integer(row, "candidate_seq") for row in rows) != tuple(
        range(1, len(rows) + 1)
    ):
        raise _VerificationInputError("candidate sequences are not contiguous")
    return rows


def _empty_candidate_chain_head(owner: FrozenVerificationRow) -> str:
    return _canonical.canonical_sha256(
        {
            "domain_separator": _EMPTY_CANDIDATE_CHAIN_DOMAIN,
            "authority_instance_id": _text(owner, "authority_instance_id"),
            "family_id": _text(owner, "family_id"),
            "search_id": _text(owner, "search_id"),
        }
    )


def _candidate_seal_matches(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    actual_count = _nonnegative_integer(owner, "actual_raw_proposal_count")
    rows = _candidate_rows_for_search(view, _text(owner, "search_id"))
    candidate_count = _nonnegative_integer(owner, "candidate_count")
    if candidate_count != len(rows) or candidate_count > actual_count:
        return False
    expected_head = (
        _sha256(rows[-1], "candidate_hash")
        if rows
        else _empty_candidate_chain_head(owner)
    )
    return _sha256(owner, "candidate_chain_head") == expected_head


def _verify_emission_seal_candidate_chain_head(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    return _candidate_seal_matches(owner, view)


def _verify_emission_seal_terminal_search_from_event_chain(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    providers: object,
) -> bool:
    del providers
    search_id = _text(owner, "search_id")
    events = _search_attempt_rows(view, search_id)
    if len(events) != 2:
        return False
    if not _candidate_seal_matches(owner, view):
        return False
    candidates = _candidate_rows_for_search(view, search_id)
    started_at = _nonnegative_integer(events[0], "recorded_at")
    terminal_at = _nonnegative_integer(events[1], "recorded_at")
    return all(
        started_at <= _nonnegative_integer(row, "registered_at") <= terminal_at
        for row in candidates
    )


def verify_genesis_row_hash(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_genesis_row_hash, owner_row, view, providers)


def verify_genesis_read(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_genesis_read, owner_row, view, providers)


def verify_research_questions_question_id_issuance(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_research_questions_question_id_issuance, owner_row, view, providers)


def verify_strategy_lineage_roots_root_id_issuance(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_strategy_lineage_roots_root_id_issuance, owner_row, view, providers)


def verify_strategy_lineage_nodes_node_id_issuance(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_strategy_lineage_nodes_node_id_issuance, owner_row, view, providers)


def verify_research_authority_events_event_id_issuance(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_research_authority_events_event_id_issuance, owner_row, view, providers)


def verify_selection_scopes_scope_id_issuance(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_selection_scopes_scope_id_issuance, owner_row, view, providers)


def verify_research_programs_program_id_issuance(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_research_programs_program_id_issuance, owner_row, view, providers)


def verify_selection_decisions_decision_and_family_id_issuance(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(
        _verify_selection_decisions_decision_and_family_id_issuance,
        owner_row,
        view,
        providers,
    )


def verify_global_oos_exposures_global_exposure_id(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_global_oos_exposures_global_exposure_id, owner_row, view, providers)


def verify_research_question_aliases_canonical_alias_hash(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(
        _verify_research_question_aliases_canonical_alias_hash,
        owner_row,
        view,
        providers,
    )


def verify_selection_scope_aliases_canonical_alias_hash(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(
        _verify_selection_scope_aliases_canonical_alias_hash,
        owner_row,
        view,
        providers,
    )


def verify_selection_decisions_decision_class_slot_policy(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(
        _verify_selection_decisions_decision_class_slot_policy,
        owner_row,
        view,
        providers,
    )


def verify_research_authority_events_grant_policy(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_research_authority_events_grant_policy, owner_row, view, providers)


def verify_research_authority_events_audit_actor_provenance(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(
        _verify_research_authority_events_audit_actor_provenance,
        owner_row,
        view,
        providers,
    )


def verify_subject_grant_required(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_subject_grant_required, owner_row, view, providers)


def verify_family_search_registration_totality(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_family_search_registration_totality, owner_row, view, providers)


def verify_proposal_branch_totality_composite(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_proposal_branch_totality, owner_row, view, providers)


def verify_quarantine_public_id_safe_formula(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_quarantine_public_id_safe_formula, owner_row, view, providers)


def verify_proposal_public_id(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_proposal_public_id, owner_row, view, providers)


def verify_proposal_identity_payload(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_proposal_identity_payload, owner_row, view, providers)


def verify_proposal_canonical_identity_from_canonical_bytes(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(
        _verify_proposal_canonical_identity_from_canonical_bytes,
        owner_row,
        view,
        providers,
    )


def verify_emission_seal_actual_proposal_count(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_emission_seal_actual_proposal_count, owner_row, view, providers)


def verify_emission_seal_terminal_result_totality(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(
        _verify_emission_seal_terminal_result_totality,
        owner_row,
        view,
        providers,
    )


def verify_emission_seal_ordered_proposal_ids(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_emission_seal_ordered_proposal_ids, owner_row, view, providers)


def verify_emission_seal_ordered_non_quarantined_raw_hashes(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(
        _verify_emission_seal_ordered_non_quarantined_raw_hashes,
        owner_row,
        view,
        providers,
    )


def verify_emission_seal_ordered_canonical_proposal_hashes(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(
        _verify_emission_seal_ordered_canonical_proposal_hashes,
        owner_row,
        view,
        providers,
    )


def verify_emission_seal_ordered_quarantine_record_ids(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(
        _verify_emission_seal_ordered_quarantine_record_ids,
        owner_row,
        view,
        providers,
    )


def verify_emission_seal_accepted_config_set(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_emission_seal_accepted_config_set, owner_row, view, providers)


def verify_emission_seal_candidate_chain_head(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(_verify_emission_seal_candidate_chain_head, owner_row, view, providers)


def verify_emission_seal_terminal_search_from_event_chain(
    owner_row: object, view: object, providers: object
) -> bool:
    return _invoke(
        _verify_emission_seal_terminal_search_from_event_chain,
        owner_row,
        view,
        providers,
    )


@dataclass(frozen=True, slots=True)
class GenesisC4C5NamedVerifierImplementation:
    """One unique code, every exact declaration binding, and its callable."""

    verifier_code: str
    binding_keys: tuple[str, ...]
    implementation_id: str
    implementation_requirement_id: str
    implementation: object
    production_ready: bool = True

    def __post_init__(self) -> None:
        if type(self.verifier_code) is not str or ".verify_" not in self.verifier_code:
            raise TypeError("named verifier code must be exact closed text")
        if type(self.binding_keys) is not tuple or not self.binding_keys:
            raise TypeError("named verifier binding keys must be a non-empty tuple")
        if len(set(self.binding_keys)) != len(self.binding_keys):
            raise ValueError("named verifier binding keys must be unique")
        expected_id = f"FAMILY_SCHEMA_NAMED_VERIFIER::{self.verifier_code}"
        expected_requirement = f"NAMED_VERIFIER::{self.verifier_code}"
        if self.implementation_id != expected_id:
            raise ValueError("named verifier implementation ID does not match its code")
        if self.implementation_requirement_id != expected_requirement:
            raise ValueError("named verifier requirement ID does not match its code")
        if type(self.implementation) is not FunctionType:
            raise TypeError("named verifier implementation must be a Python function")
        parameters = tuple(inspect.signature(self.implementation).parameters.values())
        if len(parameters) != 3 or tuple(parameter.name for parameter in parameters) != (
            "owner_row",
            "view",
            "providers",
        ) or any(
            parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
            or parameter.default is not inspect.Parameter.empty
            for parameter in parameters
        ):
            raise TypeError("named verifier must accept exactly owner_row, view, providers")
        if type(self.production_ready) is not bool:
            raise TypeError("production_ready must be an exact bool")


_IMPLEMENTATION_FUNCTIONS_BY_CODE = (
    ("family_schema.verify_genesis_row_hash_v1", verify_genesis_row_hash),
    ("family_schema.verify_genesis_read_v1", verify_genesis_read),
    ("c4.verify_research_questions_question_id_issuance_v1", verify_research_questions_question_id_issuance),
    ("c4.verify_subject_grant_required_v1", verify_subject_grant_required),
    ("c4.verify_research_question_aliases_canonical_alias_hash_v1", verify_research_question_aliases_canonical_alias_hash),
    ("c4.verify_strategy_lineage_roots_root_id_issuance_v1", verify_strategy_lineage_roots_root_id_issuance),
    ("c4.verify_strategy_lineage_nodes_node_id_issuance_v1", verify_strategy_lineage_nodes_node_id_issuance),
    ("c4.verify_research_authority_events_grant_policy_v1", verify_research_authority_events_grant_policy),
    ("c4.verify_research_authority_events_audit_actor_provenance_v1", verify_research_authority_events_audit_actor_provenance),
    ("c4.verify_research_authority_events_event_id_issuance_v1", verify_research_authority_events_event_id_issuance),
    ("c4.verify_selection_scopes_scope_id_issuance_v1", verify_selection_scopes_scope_id_issuance),
    ("c4.verify_selection_scope_aliases_canonical_alias_hash_v1", verify_selection_scope_aliases_canonical_alias_hash),
    ("c4.verify_research_programs_program_id_issuance_v1", verify_research_programs_program_id_issuance),
    ("c4.verify_selection_decisions_decision_class_slot_policy_v1", verify_selection_decisions_decision_class_slot_policy),
    ("c4.verify_selection_decisions_decision_and_family_id_issuance_v1", verify_selection_decisions_decision_and_family_id_issuance),
    ("c4.verify_global_oos_exposures_global_exposure_id_v1", verify_global_oos_exposures_global_exposure_id),
    ("c5.verify_family_search_registration_totality_v1", verify_family_search_registration_totality),
    ("c5.verify_proposal_branch_totality_composite_v1", verify_proposal_branch_totality_composite),
    ("c5.verify_quarantine_public_id_safe_formula_v1", verify_quarantine_public_id_safe_formula),
    ("c5.verify_proposal_public_id_v1", verify_proposal_public_id),
    ("c5.verify_proposal_identity_payload_v1", verify_proposal_identity_payload),
    ("c5.verify_proposal_canonical_identity_from_canonical_bytes_v1", verify_proposal_canonical_identity_from_canonical_bytes),
    ("c5.verify_emission_seal_actual_proposal_count_v1", verify_emission_seal_actual_proposal_count),
    ("c5.verify_emission_seal_terminal_result_totality_v1", verify_emission_seal_terminal_result_totality),
    ("c5.verify_emission_seal_ordered_proposal_ids_v1", verify_emission_seal_ordered_proposal_ids),
    ("c5.verify_emission_seal_ordered_non_quarantined_raw_hashes_v1", verify_emission_seal_ordered_non_quarantined_raw_hashes),
    ("c5.verify_emission_seal_ordered_canonical_proposal_hashes_v1", verify_emission_seal_ordered_canonical_proposal_hashes),
    ("c5.verify_emission_seal_ordered_quarantine_record_ids_v1", verify_emission_seal_ordered_quarantine_record_ids),
    ("c5.verify_emission_seal_accepted_config_set_v1", verify_emission_seal_accepted_config_set),
    ("c5.verify_emission_seal_candidate_chain_head_v1", verify_emission_seal_candidate_chain_head),
    ("c5.verify_emission_seal_terminal_search_from_event_chain_v1", verify_emission_seal_terminal_search_from_event_chain),
)


def _declared_bindings() -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    components = (
        (_GENESIS_COMPONENT_ID, (FAMILY_AUTHORITY_GENESIS_TABLE_SPEC,), ()),
        (C4_SCHEMA_COMPONENT.component_id, C4_SCHEMA_COMPONENT.tables, C4_SCHEMA_COMPONENT.extensions),
        (C5_SCHEMA_COMPONENT.component_id, C5_SCHEMA_COMPONENT.tables, C5_SCHEMA_COMPONENT.extensions),
    )
    for component_id, tables, extensions in components:
        for surface_kind, items in (("TABLE", tables), ("EXTENSION", extensions)):
            for item in items:
                owner_table = item.table_name if surface_kind == "TABLE" else item.target_table
                rows.extend(
                    (
                        f"{component_id}:{surface_kind}:{owner_table}:{rule.rule_id}",
                        rule.verifier_code,
                    )
                    for rule in item.verification_rules
                    if rule.verifier_code is not None
                )
    return tuple(rows)


_DECLARED_BINDINGS = _declared_bindings()
_IMPLEMENTATION_BY_CODE = dict(_IMPLEMENTATION_FUNCTIONS_BY_CODE)
if (
    len(_IMPLEMENTATION_BY_CODE) != len(_IMPLEMENTATION_FUNCTIONS_BY_CODE)
    or set(_IMPLEMENTATION_BY_CODE) != {code for _, code in _DECLARED_BINDINGS}
):
    raise ValueError("genesis/C4/C5 named implementation codes do not match declarations")

GENESIS_C4_C5_NAMED_VERIFIER_IMPLEMENTATIONS = tuple(
    GenesisC4C5NamedVerifierImplementation(
        verifier_code=code,
        binding_keys=tuple(key for key, candidate in _DECLARED_BINDINGS if candidate == code),
        implementation_id=f"FAMILY_SCHEMA_NAMED_VERIFIER::{code}",
        implementation_requirement_id=f"NAMED_VERIFIER::{code}",
        implementation=_IMPLEMENTATION_BY_CODE[code],
        production_ready=True,
    )
    for code in dict.fromkeys(code for _, code in _DECLARED_BINDINGS)
)


def _validate_descriptor_coverage() -> None:
    prefixes = (
        f"{_GENESIS_COMPONENT_ID}:",
        f"{C4_SCHEMA_COMPONENT.component_id}:",
        f"{C5_SCHEMA_COMPONENT.component_id}:",
    )
    descriptors = tuple(
        (item.binding_key, item.verifier_code, item.implementation_id, item.implementation_requirement_id)
        for item in FAMILY_SCHEMA_VERIFIER_BINDINGS
        if item.binding_key.startswith(prefixes)
    )
    expected = tuple(
        (
            key,
            code,
            f"FAMILY_SCHEMA_NAMED_VERIFIER::{code}",
            f"NAMED_VERIFIER::{code}",
        )
        for key, code in _DECLARED_BINDINGS
    )
    if len(descriptors) != len(expected) or set(descriptors) != set(expected):
        raise ValueError("genesis/C4/C5 descriptor coverage is not exact")


_validate_descriptor_coverage()


def verify_genesis_c4_c5_named(
    verifier_code: object,
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    """Dispatch one exact code; unknown codes and all exceptions reject."""

    if type(verifier_code) is not str:
        return False
    for item in GENESIS_C4_C5_NAMED_VERIFIER_IMPLEMENTATIONS:
        if item.verifier_code == verifier_code:
            implementation = item.implementation
            if type(implementation) is not FunctionType:
                return False
            try:
                return implementation(owner_row, view, providers) is True
            except Exception:
                return False
    return False


__all__ = [
    "GENESIS_C4_C5_NAMED_VERIFIER_IMPLEMENTATIONS",
    "GenesisC4C5NamedVerifierImplementation",
    "verify_genesis_c4_c5_named",
]
