"""RED schema-semantic freeze for exact-v3 Pass C slices C5 and C6."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
import importlib
import inspect
from types import ModuleType
from typing import Any

import pytest


C5_TABLE_NAMES = (
    "data_snooping_family_searches",
    "data_snooping_proposal_registrations",
    "data_snooping_proposals",
    "data_snooping_hypotheses",
    "data_snooping_search_emission_seals",
)

STANDARD_BUSINESS_COLUMNS = {
    "record_type": "TEXT",
    "authority_instance_id": "TEXT",
    "genesis_record_hash": "TEXT",
    "record_hash": "TEXT",
}

C5_REQUIRED_COLUMNS = {
    "data_snooping_family_searches": {
        **STANDARD_BUSINESS_COLUMNS,
        "family_id": "TEXT",
        "search_id": "TEXT",
        "research_program_id": "TEXT",
        "search_kind": "TEXT",
        "generator_seed": "INTEGER",
        "proposal_budget": "INTEGER",
        "stopping_rule_hash": "TEXT",
        "external_ingestion_deadline_us": "INTEGER",
        "search_authority_id": "TEXT",
        "search_authority_head": "TEXT",
        "linked_at_us": "INTEGER",
        "link_ordinal": "INTEGER",
        "prev_record_hash": "TEXT",
    },
    "data_snooping_proposal_registrations": {
        **STANDARD_BUSINESS_COLUMNS,
        "proposal_id": "TEXT",
        "family_id": "TEXT",
        "search_id": "TEXT",
        "emission_ordinal": "INTEGER",
        "proposal_branch": "TEXT",
        "identity_payload_json": "TEXT",
        "raw_envelope_hash": "TEXT",
        "media_type": "TEXT",
        "raw_byte_length": "INTEGER",
        "length_bucket": "TEXT",
        "dlp_policy_id": "TEXT",
        "dlp_policy_version": "TEXT",
        "scanner_implementation": "TEXT",
        "scanner_version": "TEXT",
        "dlp_result_code": "TEXT",
        "restricted_clean_blob_receipt_hash": "TEXT",
        "quarantine_record_id": "TEXT",
        "registered_at_us": "INTEGER",
        "prev_record_hash": "TEXT",
    },
    "data_snooping_proposals": {
        **STANDARD_BUSINESS_COLUMNS,
        "proposal_id": "TEXT",
        "family_id": "TEXT",
        "search_id": "TEXT",
        "emission_ordinal": "INTEGER",
        "registration_record_hash": "TEXT",
        "parse_status": "TEXT",
        "canonical_proposal_json": "TEXT",
        "canonical_proposal_hash": "TEXT",
        "config_hash": "TEXT",
        "alpha_id": "TEXT",
        "parsed_at_us": "INTEGER",
    },
    "data_snooping_hypotheses": {
        **STANDARD_BUSINESS_COLUMNS,
        "family_id": "TEXT",
        "first_search_id": "TEXT",
        "config_hash": "TEXT",
        "alpha_id": "TEXT",
        "canonical_hypothesis_hash": "TEXT",
        "first_proposal_id": "TEXT",
    },
    "data_snooping_search_emission_seals": {
        **STANDARD_BUSINESS_COLUMNS,
        "family_id": "TEXT",
        "search_id": "TEXT",
        "declared_proposal_budget": "INTEGER",
        "actual_raw_proposal_count": "INTEGER",
        "ordered_proposal_ids_hash": "TEXT",
        "ordered_non_quarantined_raw_hashes_hash": "TEXT",
        "ordered_canonical_proposal_hashes_hash": "TEXT",
        "ordered_quarantine_record_ids_hash": "TEXT",
        "accepted_config_set_hash": "TEXT",
        "candidate_count": "INTEGER",
        "candidate_chain_head": "TEXT",
        "terminal_reason": "TEXT",
        "sealed_at_us": "INTEGER",
    },
}

C5_RECORD_TYPES = {
    "data_snooping_family_searches": "DATA_SNOOPING_FAMILY_SEARCH_V1",
    "data_snooping_proposal_registrations": (
        "DATA_SNOOPING_PROPOSAL_REGISTRATION_V1"
    ),
    "data_snooping_proposals": "DATA_SNOOPING_PROPOSAL_V1",
    "data_snooping_hypotheses": "DATA_SNOOPING_HYPOTHESIS_V1",
    "data_snooping_search_emission_seals": ("DATA_SNOOPING_SEARCH_EMISSION_SEAL_V1"),
}

C5_INSERT_GUARDS = {
    "data_snooping_family_searches": {
        "SEARCH_NOT_STARTED",
    },
    "data_snooping_proposal_registrations": {
        "SEARCH_LINK_EXISTS",
        "SEARCH_NOT_TERMINAL",
        "REGISTRATION_WITHIN_DEADLINE",
        "EMISSION_WITHIN_PROPOSAL_BUDGET",
        "SEARCH_EMISSION_NOT_SEALED",
    },
    "data_snooping_proposals": {
        "SEARCH_NOT_TERMINAL",
        "SEARCH_EMISSION_NOT_SEALED",
    },
    "data_snooping_hypotheses": {
        "PROPOSAL_CLEAN_AND_PARSED",
        "SEARCH_EMISSION_NOT_SEALED",
    },
    "data_snooping_search_emission_seals": {
        "SEARCH_LINK_EXISTS",
        "SEARCH_TERMINAL",
        "DECLARED_BUDGET_MATCH",
        "EMISSION_COUNTS_MATCH",
        "PROPOSAL_CHAIN_HEAD_MATCHES",
        "NO_EXISTING_EMISSION_SEAL",
    },
}

C5_COMPONENT_ID = "C5_PROPOSALS_HYPOTHESES_DLP_SEALS_V1"
C5_CAPABILITIES = (
    "c5.proposals_hypotheses.dispositions",
    "c5.family_search.core",
    "c5.proposal_registration.core",
    "c5.proposal.core",
    "c5.hypothesis.core",
    "c5.emission_seal.core",
    "c5.deferred_disposition.policy",
)
C5_REQUIRED_EXTENSIONS = (
    (
        "C8_FAMILY_SEARCH_DECLARATION_AND_LATE_GUARDS_V1",
        "data_snooping_family_searches",
        "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
        "c8.family_search.declaration_and_late_guards",
    ),
    (
        "C8_PROPOSAL_DECLARATION_DLP_AND_LATE_GUARDS_V1",
        "data_snooping_proposal_registrations",
        "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
        "c8.proposal.declaration_dlp_and_late_guards",
    ),
    (
        "C8_PROPOSAL_TERMINAL_RESULT_LATE_GUARDS_V1",
        "data_snooping_proposals",
        "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
        "c8.proposal_terminal_result.late_guards",
    ),
    (
        "C8_HYPOTHESIS_LATE_GUARDS_V1",
        "data_snooping_hypotheses",
        "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
        "c8.hypothesis.late_guards",
    ),
    (
        "C8_EMISSION_SEAL_LATE_GUARDS_V1",
        "data_snooping_search_emission_seals",
        "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1",
        "c8.emission_seal.late_guards",
    ),
)

C5_CONTRACT_DERIVATIONS = (
    (
        "c5.proposal_registration.chain_head",
        "RegisteredEnvelopeHandle",
        "registration_authority_head",
        "IMMUTABLE_ROW_LOOKUP",
        "data_snooping_proposal_registrations",
        "proposal_id",
        "data_snooping_proposal_registrations",
        "proposal_id",
        "record_hash",
    ),
)

C5_REQUIRED_CONTRACT_SURFACES = (
    (
        "CandidateDisposition",
        (
            "family_id",
            "proposal_id",
            "disposition",
            "config_hash",
            "evaluation_receipt_hash",
            "quarantine_record_id",
            "reason_code",
            "performance_accessed",
        ),
        "C7_ONE_SHOT_OOS_V1",
        "c7.candidate_disposition.core",
    ),
)

C5_CONTRACT_FIELDS = {
    "FamilySearchRecord": {
        "family_id",
        "search_id",
        "research_program_id",
        "search_kind",
        "generator_seed",
        "proposal_budget",
        "stopping_rule_hash",
        "external_ingestion_deadline_us",
        "search_authority_id",
        "search_authority_head",
        "linked_at_us",
        "link_ordinal",
    },
    "CandidateIdentity": {
        "family_id",
        "search_id",
        "emission_ordinal",
        "proposal_id",
        "proposal_branch",
        "raw_envelope_hash",
    },
    "CandidateProposalRegistrationRecord": {
        "identity",
        "media_type",
        "raw_byte_length",
        "length_bucket",
        "dlp_policy_id",
        "dlp_policy_version",
        "scanner_implementation",
        "scanner_version",
        "dlp_result_code",
        "restricted_clean_blob_receipt_hash",
        "quarantine_record_id",
        "registered_at_us",
    },
    "CandidateProposalRecord": {
        "proposal_id",
        "family_id",
        "search_id",
        "emission_ordinal",
        "registration_record_hash",
        "parse_status",
        "canonical_proposal_json",
        "canonical_proposal_hash",
        "config_hash",
        "alpha_id",
        "parsed_at_us",
    },
    "CandidateHypothesisRecord": {
        "family_id",
        "first_search_id",
        "config_hash",
        "alpha_id",
        "canonical_hypothesis_hash",
        "first_proposal_id",
    },
    "CandidateEmissionSeal": {
        "family_id",
        "search_id",
        "declared_proposal_budget",
        "actual_raw_proposal_count",
        "ordered_proposal_ids_hash",
        "ordered_non_quarantined_raw_hashes_hash",
        "ordered_canonical_proposal_hashes_hash",
        "ordered_quarantine_record_ids_hash",
        "accepted_config_set_hash",
        "candidate_count",
        "candidate_chain_head",
        "terminal_reason",
        "sealed_at_us",
    },
    "CandidateDisposition": {
        "family_id",
        "proposal_id",
        "disposition",
        "config_hash",
        "evaluation_receipt_hash",
        "quarantine_record_id",
        "reason_code",
        "performance_accessed",
    },
    "RegisteredEnvelopeHandle": {
        "proposal_id",
        "family_id",
        "search_id",
        "emission_ordinal",
        "restricted_clean_blob_receipt_hash",
        "registration_record_hash",
        "registration_authority_head",
    },
}

C5_TIMESTAMP_COLUMNS = {
    "data_snooping_family_searches": {
        "external_ingestion_deadline_us",
        "linked_at_us",
    },
    "data_snooping_proposal_registrations": {"registered_at_us"},
    "data_snooping_proposals": {"parsed_at_us"},
    "data_snooping_search_emission_seals": {"sealed_at_us"},
}

C6_EMBEDDED_COLUMNS = {
    "data_snooping_declarations": {
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
    },
    "global_oos_exposures": {
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
    },
    "data_snooping_oos_releases": {
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
    },
    "data_snooping_family_closures": {
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
    },
    "artifact_publication_receipts": {
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
    },
}


def _schema_module() -> ModuleType:
    try:
        return importlib.import_module("services.research.experiment_registry.family_schema_c5_c6")
    except ModuleNotFoundError as exc:
        if exc.name == "services.research.experiment_registry.family_schema_c5_c6":
            pytest.fail("Pass C C5/C6 schema module is missing: family_schema_c5_c6.py")
        raise


def _required(name: str) -> Any:
    value = getattr(_schema_module(), name, None)
    assert value is not None, f"Pass C schema symbol is missing: {name}"
    return value


def _payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise AssertionError(f"schema AST value is not inspectable: {type(value).__name__}")


def _items(value: Any, name: str) -> tuple[Any, ...]:
    payload = _payload(value)
    result = payload.get(name)
    assert isinstance(result, Sequence) and not isinstance(result, (str, bytes))
    return tuple(result)


def _names(values: Sequence[Any], key: str = "name") -> set[str]:
    return {str(_payload(item)[key]) for item in values}


def _walk_mapping(value: Any):
    if isinstance(value, Mapping):
        payload = dict(value)
        yield payload
        for item in payload.values():
            yield from _walk_mapping(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _walk_mapping(item)


def _table_specs() -> tuple[Any, ...]:
    specs = _required("C5_AUTHORITY_TABLE_SPECS")
    assert isinstance(specs, tuple)
    return specs


def _table(table_name: str) -> Any:
    return next(spec for spec in _table_specs() if _payload(spec)["table_name"] == table_name)


def _guard(table_name: str, error_code: str) -> Any:
    return next(
        trigger
        for trigger in _table(table_name).triggers
        if str(trigger.kind) == "INSERT_GUARD"
        if trigger.error_code == error_code
    )


def _binding_shape(expression: Any) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            binding.related_column,
            binding.value.column,
            str(binding.value.source),
        )
        for binding in expression.bindings
    )


def _comparison_shape(expression: Any) -> tuple[str, str, str, str, str]:
    return (
        expression.left.column,
        str(expression.left.source),
        str(expression.operator),
        expression.right.column,
        str(expression.right.source),
    )


def _scalar_value(expression: Any, row: Mapping[str, Any]) -> Any:
    payload = _payload(expression)
    if "column" in payload:
        return row[str(payload["column"])]
    assert set(payload) == {"value"}
    return payload["value"]


def _evaluate_check(expression: Any, row: Mapping[str, Any]) -> bool:
    payload = _payload(expression)
    operator = str(payload["operator"])
    if "left" in payload and "right" in payload:
        left = _scalar_value(payload["left"], row)
        right = _scalar_value(payload["right"], row)
        return {
            "EQ": left == right,
            "NE": left != right,
            "LT": left < right,
            "LE": left <= right,
            "GT": left > right,
            "GE": left >= right,
        }[operator]
    if "operands" in payload:
        values = (_evaluate_check(item, row) for item in payload["operands"])
        return all(values) if operator == "AND" else any(values)
    if "values" in payload:
        value = _scalar_value(payload["value"], row)
        members = {_scalar_value(item, row) for item in payload["values"]}
        result = value in members
        return not result if payload["negated"] else result
    if operator in {"IS_NULL", "IS_NOT_NULL"}:
        result = _scalar_value(payload["value"], row) is None
        return result if operator == "IS_NULL" else not result
    raise AssertionError(f"unsupported closed check node: {payload!r}")


def test_c5_has_exactly_five_business_tables_and_c6_has_zero() -> None:
    c5_specs = _table_specs()
    assert tuple(_payload(spec)["table_name"] for spec in c5_specs) == C5_TABLE_NAMES
    assert _required("C6_BUSINESS_TABLE_SPECS") == ()


def test_c5_component_owns_only_core_and_requires_c8_late_guard_extensions() -> None:
    component = _required("C5_SCHEMA_COMPONENT")
    assert component.component_id == C5_COMPONENT_ID
    assert component.tables is _required("C5_AUTHORITY_TABLE_SPECS")
    assert tuple(component.capabilities) == C5_CAPABILITIES
    assert tuple(
        (
            dependency.component_id,
            str(dependency.kind),
            tuple(dependency.required_capabilities),
        )
        for dependency in component.dependencies
    ) == (
        (
            "C4_QUESTIONS_LINEAGE_SCOPE_DECISIONS_EXPOSURES_V1",
            "CONTRACT_LINEAGE",
            ("c4.research_program.core",),
        ),
    )
    assert (
        tuple(
            (
                requirement.extension_id,
                requirement.target_table,
                requirement.provider_component_id,
                requirement.required_capability,
            )
            for requirement in component.required_extensions
        )
        == C5_REQUIRED_EXTENSIONS
    )

    coverage = {(item.contract_type, item.field_name) for item in component.coverage}
    assert coverage == {
        (contract_type, field_name)
        for contract_type, fields in C5_CONTRACT_FIELDS.items()
        if contract_type != "CandidateDisposition"
        for field_name in fields
    }
    identity_coverage = next(
        item
        for item in component.coverage
        if (item.contract_type, item.field_name)
        == ("CandidateProposalRegistrationRecord", "identity")
    )
    assert (
        str(identity_coverage.kind),
        identity_coverage.table_name,
        identity_coverage.target_field,
    ) == (
        "CANONICAL_PAYLOAD",
        "data_snooping_proposal_registrations",
        "identity_payload_json",
    )
    assert (
        tuple(
            (
                derivation.derivation_id,
                derivation.contract_type,
                derivation.field_name,
                str(derivation.kind),
                derivation.binding_table,
                derivation.binding_field,
                derivation.source_table,
                derivation.source_key_field,
                derivation.source_value_field,
            )
            for derivation in component.contract_derivations
        )
        == C5_CONTRACT_DERIVATIONS
    )
    assert (
        tuple(
            (
                surface.contract_type,
                tuple(surface.field_names),
                surface.provider_component_id,
                surface.required_capability,
            )
            for surface in component.required_contract_surfaces
        )
        == C5_REQUIRED_CONTRACT_SURFACES
    )
    assert coverage | {
        (surface.contract_type, field_name)
        for surface in component.required_contract_surfaces
        for field_name in surface.field_names
    } == {(contract_type, field_name) for contract_type, fields in C5_CONTRACT_FIELDS.items() for field_name in fields}

    for table in component.tables:
        assert all(foreign_key.referenced_table != "data_snooping_declarations" for foreign_key in table.foreign_keys)
        assert {trigger.error_code for trigger in table.triggers if str(trigger.kind) == "INSERT_GUARD"}.isdisjoint(
            {"DECLARATION_EXISTS", "FAMILY_NOT_FROZEN", "FAMILY_NOT_CLOSED"}
        )


def test_c5_table_specs_use_the_closed_typed_authority_ast() -> None:
    authority_table_spec = _required("AuthorityTableSpec")
    expected_keys = {
        "table_name",
        "columns",
        "primary_key_columns",
        "row_hash",
        "triggers",
        "foreign_keys",
        "indexes",
        "checks",
        "chain",
        "verification_rules",
    }
    for spec in _table_specs():
        assert isinstance(spec, authority_table_spec)
        assert set(_payload(spec)) == expected_keys
        for node in (
            *_items(spec, "columns"),
            *_items(spec, "foreign_keys"),
            *_items(spec, "indexes"),
            *_items(spec, "checks"),
            *_items(spec, "triggers"),
            *_items(spec, "verification_rules"),
        ):
            assert "raw_sql" not in _payload(node)
            assert "sql" not in _payload(node)


def test_c5_columns_types_record_domains_and_genesis_binding_are_exact() -> None:
    for spec in _table_specs():
        payload = _payload(spec)
        table_name = str(payload["table_name"])
        columns = {str(column["name"]): str(column["sql_type"]) for column in map(_payload, payload["columns"])}
        assert columns == C5_REQUIRED_COLUMNS[table_name]
        row_hash = _payload(payload["row_hash"])
        assert row_hash["version_literal"] == C5_RECORD_TYPES[table_name]
        assert {
            "record_type",
            "authority_instance_id",
            "genesis_record_hash",
        }.issubset(row_hash["canonical_fields"])
        assert row_hash["encoding"] == "QUANTOS_CANONICAL_JSON_V1"
        assert row_hash["algorithm"] == "SHA256"
        assert row_hash["record_hash_column"] == "record_hash"
        genesis_fks = [
            _payload(foreign_key)
            for foreign_key in payload["foreign_keys"]
            if tuple(_payload(foreign_key)["columns"]) == ("authority_instance_id", "genesis_record_hash")
        ]
        assert len(genesis_fks) == 1
        assert genesis_fks[0]["referenced_table"] == "family_authority_genesis"
        assert tuple(genesis_fks[0]["referenced_columns"]) == (
            "authority_instance_id",
            "record_hash",
        )


def test_c5_authority_timestamps_are_positive_epoch_microseconds_only() -> None:
    for spec in _table_specs():
        payload = _payload(spec)
        table_name = payload["table_name"]
        columns = {column["name"]: column for column in map(_payload, payload["columns"])}
        timestamps = C5_TIMESTAMP_COLUMNS.get(table_name, set())
        assert all(name.endswith("_us") for name in timestamps)
        assert all(columns[name]["nullable"] is False for name in timestamps)
        assert all(not name.endswith("_at") and "deadline" not in name for name in columns if not name.endswith("_us"))
        check_columns = {
            node["column"]
            for check in payload["checks"]
            for node in _walk_mapping(_payload(check)["expression"])
            if "column" in node
        }
        assert timestamps.issubset(check_columns)


def test_c5_every_table_is_append_only_and_has_a_nonempty_verifier() -> None:
    for spec in _table_specs():
        payload = _payload(spec)
        triggers = tuple(map(_payload, payload["triggers"]))
        by_kind = {str(trigger["kind"]): trigger for trigger in triggers}
        assert {"NO_UPDATE", "NO_DELETE"}.issubset(by_kind)
        for kind in ("NO_UPDATE", "NO_DELETE"):
            assert by_kind[kind]["timing"] == "BEFORE"
            assert by_kind[kind]["effect"] == "ABORT"
        assert payload["verification_rules"]


def test_c5_verification_rules_bind_exact_structural_or_named_fail_closed_sources() -> None:
    rules = {
        rule.rule_id: rule
        for table in _table_specs()
        for rule in table.verification_rules
    }
    expected_structural_sources = {
        "family_search_row_hash": {
            "kind": "ROW_HASH_SPEC",
        },
        "family_search_chain": {
            "kind": "CHAIN_SPEC",
        },
        "proposal_registration_row_hash": {
            "kind": "ROW_HASH_SPEC",
        },
        "proposal_registration_chain": {
            "kind": "CHAIN_SPEC",
        },
        "proposal_registration_dlp_policy_scanner_binding": {
            "kind": "DELEGATED_EXTENSION",
            "source_id": "C8_PROPOSAL_DECLARATION_DLP_AND_LATE_GUARDS_V1",
            "required_rule_ids": (
                "proposal_declaration_dlp_match",
            ),
        },
        "proposal_row_hash": {
            "kind": "ROW_HASH_SPEC",
        },
        "proposal_registration_exact": {
            "kind": "EXACT_FOREIGN_KEY",
            "columns": (
                "authority_instance_id",
                "family_id",
                "search_id",
                "emission_ordinal",
                "proposal_id",
                "registration_record_hash",
            ),
            "referenced_table": "data_snooping_proposal_registrations",
            "referenced_columns": (
                "authority_instance_id",
                "family_id",
                "search_id",
                "emission_ordinal",
                "proposal_id",
                "record_hash",
            ),
        },
        "proposal_terminal_parse_branch": {
            "kind": "CHECK_CONSTRAINT",
            "source_id": "terminal_parse_branch",
        },
        "hypothesis_row_hash": {
            "kind": "ROW_HASH_SPEC",
        },
        "hypothesis_identity_totality": {
            "kind": "EXACT_FOREIGN_KEY",
            "columns": (
                "family_id",
                "first_search_id",
                "first_proposal_id",
                "canonical_hypothesis_hash",
                "config_hash",
                "alpha_id",
            ),
            "referenced_table": "data_snooping_proposals",
            "referenced_columns": (
                "family_id",
                "search_id",
                "proposal_id",
                "canonical_proposal_hash",
                "config_hash",
                "alpha_id",
            ),
        },
        "emission_seal_row_hash": {
            "kind": "ROW_HASH_SPEC",
        },
        "emission_seal_declared_budget": {
            "kind": "TRIGGER_POLICY",
            "source_id": "data_snooping_search_emission_seals_declared_budget_match",
        },
    }
    expected_named_verifiers = {
        "family_search_registration_totality": (
            "c5.verify_family_search_registration_totality_v1"
        ),
        "proposal_branch_totality": (
            "c5.verify_proposal_branch_totality_composite_v1"
        ),
        "quarantine_public_id_safe_formula": (
            "c5.verify_quarantine_public_id_safe_formula_v1"
        ),
        "proposal_public_id_recomputed": "c5.verify_proposal_public_id_v1",
        "proposal_identity_payload_matches_flattened_fields": (
            "c5.verify_proposal_identity_payload_v1"
        ),
        "proposal_canonical_identity_recomputed": (
            "c5.verify_proposal_canonical_identity_from_canonical_bytes_v1"
        ),
        "emission_seal_actual_proposal_count": (
            "c5.verify_emission_seal_actual_proposal_count_v1"
        ),
        "emission_seal_terminal_result_totality": (
            "c5.verify_emission_seal_terminal_result_totality_v1"
        ),
        "emission_seal_ordered_proposal_ids": (
            "c5.verify_emission_seal_ordered_proposal_ids_v1"
        ),
        "emission_seal_ordered_non_quarantined_raw_hashes": (
            "c5.verify_emission_seal_ordered_non_quarantined_raw_hashes_v1"
        ),
        "emission_seal_ordered_canonical_proposal_hashes": (
            "c5.verify_emission_seal_ordered_canonical_proposal_hashes_v1"
        ),
        "emission_seal_ordered_quarantine_record_ids": (
            "c5.verify_emission_seal_ordered_quarantine_record_ids_v1"
        ),
        "emission_seal_accepted_config_set": (
            "c5.verify_emission_seal_accepted_config_set_v1"
        ),
        "emission_seal_candidate_chain_head": (
            "c5.verify_emission_seal_candidate_chain_head_v1"
        ),
        "emission_seal_terminal_search": (
            "c5.verify_emission_seal_terminal_search_from_event_chain_v1"
        ),
    }

    assert len(rules) == 27
    assert set(rules) == set(expected_structural_sources) | set(expected_named_verifiers)
    assert len(set(expected_named_verifiers.values())) == len(expected_named_verifiers)

    for rule_id, expected in expected_structural_sources.items():
        rule = rules[rule_id]
        source = rule.structural_source
        assert source is not None, f"{rule_id} must name its exact typed structural source"
        assert rule.verifier_code is None
        actual = {
            "kind": str(source.kind),
            "source_id": source.source_id,
            "columns": source.columns,
            "referenced_table": source.referenced_table,
            "referenced_columns": source.referenced_columns,
            "required_rule_ids": source.required_rule_ids,
            "required_verifier_codes": source.required_verifier_codes,
        }
        assert actual == {
            "kind": expected["kind"],
            "source_id": expected.get("source_id"),
            "columns": expected.get("columns", ()),
            "referenced_table": expected.get("referenced_table"),
            "referenced_columns": expected.get("referenced_columns", ()),
            "required_rule_ids": expected.get("required_rule_ids", ()),
            "required_verifier_codes": (),
        }

    for rule_id, verifier_code in expected_named_verifiers.items():
        rule = rules[rule_id]
        assert rule.verifier_code == verifier_code
        assert rule.structural_source is None


def test_c5_only_external_terminal_event_input_remains_a_named_requirement() -> None:
    rules = {
        rule.rule_id: rule
        for table in _table_specs()
        for rule in table.verification_rules
    }
    fail_closed_requirements = {
        # The seal has no terminal-event-hash column.  This obligation must run
        # the inherited event-chain verifier and fail closed rather than treating
        # the existential SEARCH_TERMINAL insert guard as a terminal-head proof.
        "emission_seal_terminal_search": (
            "c5.verify_emission_seal_terminal_search_from_event_chain_v1"
        ),
    }

    for rule_id, verifier_code in fail_closed_requirements.items():
        rule = rules[rule_id]
        assert rule.verifier_code == verifier_code
        assert rule.structural_source is None


def test_c5_ordered_chains_have_one_exact_partition_ordinal_and_genesis() -> None:
    by_table = {_payload(spec)["table_name"]: _payload(spec) for spec in _table_specs()}
    search_chain = _payload(by_table["data_snooping_family_searches"]["chain"])
    proposal_chain = _payload(
        by_table["data_snooping_proposal_registrations"]["chain"]
    )

    assert search_chain == {
        "partition_key_columns": ("family_id",),
        "ordinal_column": "link_ordinal",
        "genesis_rule": "FAMILY_AUTHORITY_GENESIS_RECORD_HASH",
        "previous_hash_column": "prev_record_hash",
        "record_hash_column": "record_hash",
        "terminal_head_rule": "LATEST_RECORD_HASH",
    }
    assert proposal_chain == {
        "partition_key_columns": ("family_id", "search_id"),
        "ordinal_column": "emission_ordinal",
        "genesis_rule": "FAMILY_AUTHORITY_GENESIS_RECORD_HASH",
        "previous_hash_column": "prev_record_hash",
        "record_hash_column": "record_hash",
        "terminal_head_rule": "LATEST_RECORD_HASH",
    }
    assert by_table["data_snooping_proposals"]["chain"] is None
    assert by_table["data_snooping_hypotheses"]["chain"] is None
    assert by_table["data_snooping_search_emission_seals"]["chain"] is None


def test_c5_chain_ordinals_reject_zero_in_the_closed_check_ast() -> None:
    for table_name, check_name, ordinal_column in (
        (
            "data_snooping_family_searches",
            "link_ordinal_positive",
            "link_ordinal",
        ),
        (
            "data_snooping_proposal_registrations",
            "emission_ordinal_positive",
            "emission_ordinal",
        ),
    ):
        checks = {check.name: check for check in _table(table_name).checks}
        check = checks[check_name]
        assert not _evaluate_check(check.expression, {ordinal_column: 0})
        assert _evaluate_check(check.expression, {ordinal_column: 1})


def test_c5_terminal_guards_are_complete_and_attached_to_the_owning_table() -> None:
    for spec in _table_specs():
        payload = _payload(spec)
        guard_ids = {
            str(trigger["error_code"])
            for trigger in map(_payload, payload["triggers"])
            if trigger["kind"] == "INSERT_GUARD"
        }
        assert guard_ids == C5_INSERT_GUARDS[payload["table_name"]]


def test_registration_guards_bind_link_terminal_deadline_budget_and_seal_exactly() -> None:
    table_name = "data_snooping_proposal_registrations"
    link = _guard(table_name, "SEARCH_LINK_EXISTS").violation_when
    assert link.table == "data_snooping_family_searches"
    assert _binding_shape(link) == (
        ("family_id", "family_id", "NEW"),
        ("search_id", "search_id", "NEW"),
    )
    assert link.predicate is None
    assert link.negated is True

    terminal = _guard(table_name, "SEARCH_NOT_TERMINAL").violation_when
    assert terminal.table == "search_attempt_events"
    assert _binding_shape(terminal) == (("search_id", "search_id", "NEW"),)
    terminal_predicate = _payload(terminal.predicate)
    assert terminal_predicate["value"]["column"] == "status"
    assert terminal_predicate["value"]["source"] == "RELATED"
    assert tuple(item["value"] for item in terminal_predicate["values"]) == (
        "FAILED",
        "COMPLETED",
    )
    assert terminal.negated is False

    deadline = _guard(table_name, "REGISTRATION_WITHIN_DEADLINE").violation_when
    assert deadline.table == "data_snooping_family_searches"
    assert _binding_shape(deadline) == (
        ("family_id", "family_id", "NEW"),
        ("search_id", "search_id", "NEW"),
    )
    assert _comparison_shape(deadline.predicate) == (
        "external_ingestion_deadline_us",
        "RELATED",
        "GE",
        "registered_at_us",
        "NEW",
    )
    assert deadline.negated is True

    budget = _guard(table_name, "EMISSION_WITHIN_PROPOSAL_BUDGET").violation_when
    assert budget.table == "data_snooping_family_searches"
    assert _binding_shape(budget) == (
        ("family_id", "family_id", "NEW"),
        ("search_id", "search_id", "NEW"),
    )
    assert _comparison_shape(budget.predicate) == (
        "proposal_budget",
        "RELATED",
        "GE",
        "emission_ordinal",
        "NEW",
    )
    assert budget.negated is True

    seal = _guard(table_name, "SEARCH_EMISSION_NOT_SEALED").violation_when
    assert seal.table == "data_snooping_search_emission_seals"
    assert _binding_shape(seal) == (
        ("family_id", "family_id", "NEW"),
        ("search_id", "search_id", "NEW"),
    )
    assert seal.negated is False


def test_terminal_proposal_results_cannot_arrive_after_terminal_or_seal() -> None:
    terminal = _guard("data_snooping_proposals", "SEARCH_NOT_TERMINAL").violation_when
    assert terminal.table == "search_attempt_events"
    assert _binding_shape(terminal) == (("search_id", "search_id", "NEW"),)
    assert terminal.negated is False

    seal = _guard("data_snooping_proposals", "SEARCH_EMISSION_NOT_SEALED").violation_when
    assert seal.table == "data_snooping_search_emission_seals"
    assert _binding_shape(seal) == (
        ("family_id", "family_id", "NEW"),
        ("search_id", "search_id", "NEW"),
    )
    assert seal.negated is False


def test_hypothesis_identity_is_one_shot_and_exactly_bound_to_first_clean_proposal() -> None:
    hypothesis = _table("data_snooping_hypotheses")
    column_names = tuple(column.name for column in hypothesis.columns)
    assert "first_search_id" in column_names
    assert "accepted_proposal_count" not in column_names
    assert hypothesis.primary_key_columns == ("family_id", "config_hash")
    assert tuple(hypothesis.row_hash.canonical_fields) == tuple(name for name in column_names if name != "record_hash")

    proposal_fk = next(
        foreign_key
        for foreign_key in hypothesis.foreign_keys
        if foreign_key.referenced_table == "data_snooping_proposals"
    )
    assert proposal_fk.columns == (
        "family_id",
        "first_search_id",
        "first_proposal_id",
        "canonical_hypothesis_hash",
        "config_hash",
        "alpha_id",
    )
    assert proposal_fk.referenced_columns == (
        "family_id",
        "search_id",
        "proposal_id",
        "canonical_proposal_hash",
        "config_hash",
        "alpha_id",
    )

    clean = _guard("data_snooping_hypotheses", "PROPOSAL_CLEAN_AND_PARSED").violation_when
    assert clean.table == "data_snooping_proposals"
    assert _binding_shape(clean) == (
        ("family_id", "family_id", "NEW"),
        ("search_id", "first_search_id", "NEW"),
        ("proposal_id", "first_proposal_id", "NEW"),
        ("canonical_proposal_hash", "canonical_hypothesis_hash", "NEW"),
        ("config_hash", "config_hash", "NEW"),
        ("alpha_id", "alpha_id", "NEW"),
    )
    assert _payload(clean.predicate) == {
        "left": {"column": "parse_status", "source": "RELATED"},
        "operator": "EQ",
        "right": {"value": "ACCEPTED"},
    }
    assert clean.negated is True

    first_search_seal = _guard("data_snooping_hypotheses", "SEARCH_EMISSION_NOT_SEALED").violation_when
    assert first_search_seal.table == "data_snooping_search_emission_seals"
    assert _binding_shape(first_search_seal) == (
        ("family_id", "family_id", "NEW"),
        ("search_id", "first_search_id", "NEW"),
    )
    assert first_search_seal.negated is False


def test_emission_seal_recomputes_budget_counts_digests_and_chain_head() -> None:
    budget = _guard("data_snooping_search_emission_seals", "DECLARED_BUDGET_MATCH").violation_when
    assert budget.table == "data_snooping_family_searches"
    assert _binding_shape(budget) == (
        ("family_id", "family_id", "NEW"),
        ("search_id", "search_id", "NEW"),
    )
    assert _comparison_shape(budget.predicate) == (
        "proposal_budget",
        "RELATED",
        "EQ",
        "declared_proposal_budget",
        "NEW",
    )
    assert budget.negated is True

    seal = _table("data_snooping_search_emission_seals")
    rules = {rule.rule_id: rule for rule in seal.verification_rules}
    expected_recomputations = {
        "emission_seal_declared_budget": (
            "declared_proposal_budget",
            "data_snooping_family_searches",
        ),
        "emission_seal_actual_proposal_count": (
            "actual_raw_proposal_count",
            "data_snooping_proposal_registrations",
        ),
        "emission_seal_terminal_result_totality": (
            "actual_raw_proposal_count",
            "data_snooping_proposals",
        ),
        "emission_seal_ordered_proposal_ids": (
            "ordered_proposal_ids_hash",
            "data_snooping_proposal_registrations",
        ),
        "emission_seal_ordered_non_quarantined_raw_hashes": (
            "ordered_non_quarantined_raw_hashes_hash",
            "data_snooping_proposal_registrations",
        ),
        "emission_seal_ordered_canonical_proposal_hashes": (
            "ordered_canonical_proposal_hashes_hash",
            "data_snooping_proposals",
        ),
        "emission_seal_ordered_quarantine_record_ids": (
            "ordered_quarantine_record_ids_hash",
            "data_snooping_proposal_registrations",
        ),
        "emission_seal_accepted_config_set": (
            "accepted_config_set_hash",
            "data_snooping_proposals",
        ),
        "emission_seal_candidate_chain_head": (
            ("candidate_count", "candidate_chain_head"),
            "search_candidates",
        ),
    }
    for rule_id, (value_column, referenced_table) in expected_recomputations.items():
        rule = rules[rule_id]
        assert str(rule.kind) == "CROSS_TABLE_TOTALITY"
        value_columns = (
            value_column if type(value_column) is tuple else (value_column,)
        )
        prefix = (
            ("authority_instance_id", "family_id", "search_id")
            if rule_id
            in {
                "emission_seal_terminal_result_totality",
                "emission_seal_candidate_chain_head",
            }
            else ("family_id", "search_id")
        )
        assert rule.columns == (*prefix, *value_columns)
        assert rule.referenced_table == referenced_table

    assert {
        item.field_name
        for item in _required("C5_SCHEMA_COMPONENT").coverage
        if item.contract_type == "CandidateEmissionSeal"
    } == C5_CONTRACT_FIELDS["CandidateEmissionSeal"]

    checks = {check.name: check for check in seal.checks}
    candidate_order = checks["candidate_count_lte_raw_proposal_count"]
    assert _evaluate_check(
        candidate_order.expression,
        {"candidate_count": 0, "actual_raw_proposal_count": 0},
    )
    assert _evaluate_check(
        candidate_order.expression,
        {"candidate_count": 2, "actual_raw_proposal_count": 2},
    )
    assert not _evaluate_check(
        candidate_order.expression,
        {"candidate_count": 1, "actual_raw_proposal_count": 0},
    )


def test_c5_registration_branch_checks_keep_quarantine_metadata_secret_safe() -> None:
    registration = _table("data_snooping_proposal_registrations")
    checks = {
        _payload(item)["name"]: _payload(item)
        for item in _items(registration, "checks")
    }
    assert {
        "clean_proposal_branch",
        "secret_quarantined_proposal_branch",
    }.issubset(checks)
    clean_columns = {
        node["column"]
        for node in _walk_mapping(_payload(checks["clean_proposal_branch"])["expression"])
        if "column" in node
    }
    quarantine_columns = {
        node["column"]
        for node in _walk_mapping(_payload(checks["secret_quarantined_proposal_branch"])["expression"])
        if "column" in node
    }
    assert {
        "proposal_branch",
        "length_bucket",
        "media_type",
        "dlp_policy_id",
        "dlp_policy_version",
        "scanner_implementation",
        "scanner_version",
        "dlp_result_code",
        "quarantine_record_id",
        "raw_envelope_hash",
        "raw_byte_length",
        "restricted_clean_blob_receipt_hash",
    }.issubset(quarantine_columns)
    assert {
        "proposal_branch",
        "dlp_result_code",
        "raw_envelope_hash",
        "raw_byte_length",
        "restricted_clean_blob_receipt_hash",
        "quarantine_record_id",
    }.issubset(clean_columns)

    clean = {
        "proposal_branch": "CLEAN",
        "dlp_result_code": "CLEAN",
        "raw_envelope_hash": "a" * 64,
        "raw_byte_length": 16,
        "restricted_clean_blob_receipt_hash": "b" * 64,
        "quarantine_record_id": None,
        "length_bucket": "LE_1_KIB",
        "media_type": "application/json",
        "dlp_policy_id": "DLP_POLICY_V1",
        "dlp_policy_version": "1",
        "scanner_implementation": "QUANTOS_DLP_SCANNER",
        "scanner_version": "1",
    }
    quarantine = {
        **clean,
        "proposal_branch": "SECRET_QUARANTINED",
        "dlp_result_code": "SECRET_QUARANTINED",
        "raw_envelope_hash": None,
        "raw_byte_length": None,
        "restricted_clean_blob_receipt_hash": None,
        "quarantine_record_id": "qtn_" + "a" * 32,
    }

    def branch_is_valid(row: Mapping[str, Any]) -> bool:
        return all(
            _evaluate_check(checks[name]["expression"], row)
            for name in (
                "clean_proposal_branch",
                "secret_quarantined_proposal_branch",
            )
        )

    assert branch_is_valid(clean)
    assert not branch_is_valid({**clean, "dlp_result_code": "SECRET_QUARANTINED"})
    assert not branch_is_valid({**clean, "restricted_clean_blob_receipt_hash": None})
    assert not branch_is_valid({**clean, "quarantine_record_id": "qtn_" + "a" * 32})
    assert branch_is_valid(quarantine)
    assert not branch_is_valid({**quarantine, "dlp_result_code": "CLEAN"})
    for forbidden_column, forbidden_value in (
        ("raw_envelope_hash", "a" * 64),
        ("restricted_clean_blob_receipt_hash", "b" * 64),
    ):
        assert not branch_is_valid({**quarantine, forbidden_column: forbidden_value})

    verification = {
        _payload(item)["rule_id"]: _payload(item)
        for item in _items(registration, "verification_rules")
    }
    assert "proposal_registration_dlp_policy_scanner_binding" in verification
    assert tuple(
        verification["proposal_registration_dlp_policy_scanner_binding"]["columns"]
    ) == (
        "dlp_policy_id",
        "dlp_policy_version",
        "scanner_implementation",
        "scanner_version",
        "dlp_result_code",
    )


def test_c5_terminal_proposal_is_exactly_bound_and_owns_only_parse_results() -> None:
    proposal = _table("data_snooping_proposals")
    verification = {
        _payload(item)["rule_id"]: _payload(item)
        for item in _items(proposal, "verification_rules")
    }
    safe_formula = verification["quarantine_public_id_safe_formula"]
    assert safe_formula["kind"] == "STATE_BRANCH"
    assert tuple(safe_formula["columns"]) == (
        "registration_record_hash",
        "family_id",
        "search_id",
        "emission_ordinal",
        "proposal_id",
    )
    assert {
        "proposal_public_id_recomputed",
        "proposal_identity_payload_matches_flattened_fields",
        "proposal_canonical_identity_recomputed",
    }.issubset(verification)
    assert tuple(verification["proposal_public_id_recomputed"]["columns"]) == (
        "registration_record_hash",
        "family_id",
        "search_id",
        "emission_ordinal",
        "proposal_id",
    )
    assert tuple(verification["proposal_identity_payload_matches_flattened_fields"]["columns"]) == (
        "registration_record_hash",
        "family_id",
        "search_id",
        "emission_ordinal",
        "proposal_id",
    )
    assert tuple(verification["proposal_canonical_identity_recomputed"]["columns"]) == (
        "parse_status",
        "canonical_proposal_json",
        "canonical_proposal_hash",
        "config_hash",
        "alpha_id",
    )
    checks = {check.name: check for check in proposal.checks}
    terminal = checks["terminal_parse_branch"]
    accepted = {
        "parse_status": "ACCEPTED",
        "canonical_proposal_json": "{}\n",
        "canonical_proposal_hash": "a" * 64,
        "config_hash": "b" * 64,
        "alpha_id": "alpha-1",
    }
    invalid = {
        "parse_status": "INVALID",
        "canonical_proposal_json": None,
        "canonical_proposal_hash": None,
        "config_hash": None,
        "alpha_id": None,
    }
    assert _evaluate_check(terminal.expression, accepted)
    assert _evaluate_check(terminal.expression, invalid)
    assert _evaluate_check(
        terminal.expression,
        {**invalid, "parse_status": "SECRET_QUARANTINED"},
    )
    assert not _evaluate_check(
        terminal.expression,
        {**invalid, "parse_status": "ACCEPTED"},
    )
    assert not _evaluate_check(
        terminal.expression,
        {**invalid, "canonical_proposal_hash": "a" * 64},
    )


def test_c5_deferred_disposition_constraints_are_typed_metadata_not_a_table() -> None:
    constraints = _required("C5_DEFERRED_DISPOSITION_CONSTRAINTS")
    constraint_type = _required("DeferredDispositionConstraintSpec")
    assert isinstance(constraints, tuple)
    assert all(isinstance(item, constraint_type) for item in constraints)
    by_disposition = {_payload(item)["disposition"]: _payload(item) for item in constraints}
    assert set(by_disposition) == {
        "PERFORMANCE_EVALUATED",
        "PERFORMANCE_ATTEMPTED_INCOMPLETE",
        "PRE_EVALUATION_INVALID",
        "SECRET_QUARANTINED",
    }
    assert by_disposition["PERFORMANCE_EVALUATED"] == {
        "disposition": "PERFORMANCE_EVALUATED",
        "required_identity_state": "CONFIG_ACCEPTED",
        "required_fields": ("config_hash", "evaluation_receipt_hash"),
        "forbidden_fields": ("quarantine_record_id",),
        "performance_access": "COMPLETED",
    }
    assert by_disposition["PERFORMANCE_ATTEMPTED_INCOMPLETE"] == {
        "disposition": "PERFORMANCE_ATTEMPTED_INCOMPLETE",
        "required_identity_state": "CONFIG_ACCEPTED",
        "required_fields": ("config_hash", "reason_code"),
        "forbidden_fields": ("evaluation_receipt_hash", "quarantine_record_id"),
        "performance_access": "ATTEMPTED",
    }
    assert by_disposition["PRE_EVALUATION_INVALID"] == {
        "disposition": "PRE_EVALUATION_INVALID",
        "required_identity_state": "PRE_EVALUATION_INVALID",
        "required_fields": ("reason_code",),
        "forbidden_fields": (
            "config_hash",
            "evaluation_receipt_hash",
            "quarantine_record_id",
        ),
        "performance_access": "FORBIDDEN",
    }
    assert by_disposition["SECRET_QUARANTINED"] == {
        "disposition": "SECRET_QUARANTINED",
        "required_identity_state": "SECRET_QUARANTINED",
        "required_fields": ("quarantine_record_id", "reason_code"),
        "forbidden_fields": ("config_hash", "evaluation_receipt_hash"),
        "performance_access": "FORBIDDEN",
    }
    assert "data_snooping_candidate_dispositions" not in {_payload(spec)["table_name"] for spec in _table_specs()}


def test_c6_embeds_five_complete_external_reference_surfaces() -> None:
    surfaces = _required("C6_EMBEDDED_REFERENCE_SPECS")
    surface_type = _required("EmbeddedReferenceSurfaceSpec")
    assert isinstance(surfaces, tuple)
    assert all(isinstance(surface, surface_type) for surface in surfaces)
    by_table = {_payload(surface)["table_name"]: _payload(surface) for surface in surfaces}
    assert set(by_table) == set(C6_EMBEDDED_COLUMNS)
    for table_name, required_columns in C6_EMBEDDED_COLUMNS.items():
        surface = by_table[table_name]
        columns = _names(surface["columns"])
        assert columns == required_columns
        assert set(surface["reference_kinds"]).issubset(
            {"OBSERVED_PIT", "OOS_EXPOSURE", "CODE_MANIFEST", "EXECUTION_ATTESTATION", "IMMUTABLE_LEASE"}
        )
        assert surface["verifier_ids"]
        assert required_columns.issubset(set(surface["row_hash_fields"]))
    expected_reference_kinds = (
        "OBSERVED_PIT",
        "OOS_EXPOSURE",
        "CODE_MANIFEST",
        "EXECUTION_ATTESTATION",
        "IMMUTABLE_LEASE",
    )
    for table_name in (
        "data_snooping_family_closures",
        "artifact_publication_receipts",
    ):
        assert tuple(by_table[table_name]["reference_kinds"]) == (
            expected_reference_kinds
        )


def test_c6_extensions_close_provider_requirements_without_replacing_c4_core() -> None:
    surfaces = tuple(_required("C6_EMBEDDED_REFERENCE_SPECS"))
    extensions = tuple(_required("C6_TABLE_EXTENSIONS"))
    requirements = tuple(_required("C6_REQUIRED_EXTENSION_SPECS"))
    component = _required("C6_SCHEMA_COMPONENT")

    assert tuple(surface.extension for surface in surfaces) == extensions
    assert tuple(surface.requirement for surface in surfaces) == requirements
    assert tuple(component.tables) == ()
    assert tuple(component.extensions) == extensions
    for surface in surfaces:
        assert surface.extension.extension_id == surface.requirement.extension_id
        assert surface.extension.target_table == surface.table_name
        assert surface.requirement.target_table == surface.table_name
        assert surface.requirement.provider_component_id == component.component_id
        assert surface.requirement.required_capability in component.capabilities

    exposure = next(surface for surface in surfaces if surface.table_name == "global_oos_exposures")
    assert tuple(exposure.core_owned_fields) == (
        "selection_scope_id",
        "selection_decision_id",
        "exposure_fingerprint",
    )
    extension_columns = {column.name for column in exposure.extension.column_insertion.columns}
    assert not extension_columns.intersection(exposure.core_owned_fields)


def test_c6_defines_no_shadow_external_authority_or_secret_business_table() -> None:
    forbidden_fragments = (
        "observed_pit",
        "dataset_catalog",
        "code_manifest",
        "execution_attestation",
        "immutable_artifact_lease",
        "restricted_raw",
        "secret",
    )
    c5_names = {_payload(spec)["table_name"] for spec in _table_specs()}
    c6_names = {_payload(spec)["table_name"] for spec in _required("C6_BUSINESS_TABLE_SPECS")}
    assert not c6_names
    assert all(fragment not in table_name for table_name in c5_names | c6_names for fragment in forbidden_fragments)


def test_pass_c_spec_exposes_no_renderer_ddl_activation_or_migration() -> None:
    module = _schema_module()
    source = inspect.getsource(module).upper()
    assert "CREATE TABLE" not in source
    assert "PRAGMA USER_VERSION" not in source
    for forbidden_symbol in (
        "render_family_schema_v3",
        "activate_family_schema_v3",
        "verify_family_authority",
        "require_family_schema_v3",
        "_require_family_schema_v3",
    ):
        assert not hasattr(module, forbidden_symbol)
