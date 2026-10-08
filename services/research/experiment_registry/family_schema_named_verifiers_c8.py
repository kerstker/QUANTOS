"""Read-only executable Pass-M implementations for all C8 named rules.

The public functions deliberately share the closed three-argument
``(owner_row, view, providers)`` ABI.  They consume only an immutable authority
snapshot, ignore providers, and reject every exception.  Statistical evidence
is not trusted as a formatted payload: candidate and benchmark receipts are
decoded and the canonical White RC / Hansen SPA kernel is rerun.
"""

from __future__ import annotations

import inspect
import math
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import FunctionType
from typing import TypeVar

from quantos.contracts import canonical as _canonical
from quantos.contracts.research import (
    DataSnoopingAssessment,
    DataSnoopingDeclaration,
    DataSnoopingGridAssessment,
    FamilyFreezeReceipt,
    PeriodKey,
    RealityCheckResult,
    SpaResult,
)
from pydantic import BaseModel

from services.validation.reality_check import (
    RealityCheckInputError,
    compute_reality_check_and_spa,
)

from .family_schema_c8 import C8_SCHEMA_COMPONENT
from .family_schema_verification_view import (
    FamilySchemaVerificationView,
    FrozenVerificationRow,
)
from .family_schema_verifier_registry import FAMILY_SCHEMA_VERIFIER_BINDINGS


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_C8_COMPONENT_ID = "C8_DECLARATION_FREEZE_CLOSURE_ASSESSMENT_V1"
_C9_COMPONENT_ID = "C9_ARTIFACT_PUBLICATION_RECEIPTS_V1"
_ASSESSMENT_CODE = "c8.verify_family_assessment_v1"
_SERIES_KEYS = frozenset(
    {
        "ordered_period_keys",
        "gross_returns",
        "costs",
        "net_returns",
        "turnovers",
        "ordered_period_keys_hash",
        "gross_returns_hash",
        "costs_hash",
        "net_returns_hash",
        "turnovers_hash",
        "cost_contract_hash",
        "cost_model_input_hash",
        "series_hash",
    }
)
_PERIOD_KEYS = frozenset({"event_time", "frequency_id", "label_horizon"})
_VECTOR_FIELDS = ("gross_returns", "costs", "net_returns", "turnovers")
_ARRAY_HASH_FIELDS = (
    ("ordered_period_keys", "ordered_period_keys_hash"),
    ("gross_returns", "gross_returns_hash"),
    ("costs", "costs_hash"),
    ("net_returns", "net_returns_hash"),
    ("turnovers", "turnovers_hash"),
)
_BEFORE_FREEZE_TIME = {
    "data_snooping_family_searches": "linked_at_us",
    "data_snooping_proposal_registrations": "registered_at_us",
    "data_snooping_proposals": "parsed_at_us",
    "data_snooping_search_emission_seals": "sealed_at_us",
}
_AFTER_FREEZE_TIME = {
    "data_snooping_oos_releases": "released_at_us",
    "data_snooping_oos_access_attempts": "event_at_us",
    "data_snooping_candidate_dispositions": "recorded_at_us",
    "data_snooping_evaluation_batches": "committed_at_us",
    "data_snooping_family_oos_evaluations": "linked_at_us",
    "data_snooping_evaluation_receipts": "committed_at_us",
    "data_snooping_benchmark_returns": "committed_at_us",
}


class _VerificationInputError(ValueError):
    """The supplied snapshot cannot prove the named rule."""


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _text(row: Mapping[str, object], field: str, *, allow_empty: bool = False) -> str:
    value = row[field]
    if type(value) is not str or (not allow_empty and not value) or unicodedata.normalize("NFC", value) != value:
        raise _VerificationInputError(f"{field} must be exact NFC text")
    return value


def _sha256(row: Mapping[str, object], field: str) -> str:
    value = _text(row, field)
    if _SHA256_RE.fullmatch(value) is None:
        raise _VerificationInputError(f"{field} must be lowercase SHA-256 text")
    return value


def _optional_sha256(row: Mapping[str, object], field: str) -> str | None:
    if row[field] is None:
        return None
    return _sha256(row, field)


def _integer(
    row: Mapping[str, object],
    field: str,
    *,
    minimum: int | None = None,
) -> int:
    value = row[field]
    if type(value) is not int or (minimum is not None and value < minimum):
        raise _VerificationInputError(f"{field} must be an exact INTEGER")
    return value


def _owner_row(owner_row: object) -> FrozenVerificationRow:
    if not isinstance(owner_row, Mapping):
        raise _VerificationInputError("owner row must be a Mapping")
    return FrozenVerificationRow.from_mapping(owner_row)


def _view_rows(
    view: FamilySchemaVerificationView,
    table_name: str,
    *,
    optional: bool = False,
) -> tuple[FrozenVerificationRow, ...]:
    if not isinstance(view, FamilySchemaVerificationView):
        raise _VerificationInputError("verification view does not implement rows()")
    try:
        rows = view.rows(table_name)
    except KeyError:
        if optional:
            return ()
        raise
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


def _same_owner_row(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    table_name: str,
) -> bool:
    rows = _view_rows(view, table_name)
    identity_field = next(
        (field for field in ("record_hash", "receipt_record_hash") if field in owner),
        None,
    )
    if identity_field is None:
        raise _VerificationInputError("owner row has no immutable identity")
    identity = _sha256(owner, identity_field)
    match = _exactly_one(
        rows,
        lambda row: identity_field in row and _sha256(row, identity_field) == identity,
        f"{table_name} owner",
    )
    return all(field in match and match[field] == value for field, value in owner.items())


def _family_rows(
    view: FamilySchemaVerificationView,
    table_name: str,
    authority_id: str,
    family_id: str,
    *,
    optional: bool = False,
) -> tuple[FrozenVerificationRow, ...]:
    rows = _view_rows(view, table_name, optional=optional)
    result: list[FrozenVerificationRow] = []
    for row in rows:
        row_authority = _text(row, "authority_instance_id")
        row_family = _text(row, "family_id")
        if row_authority == authority_id and row_family == family_id:
            result.append(row)
    return tuple(result)


def _canonical_value(value: object, context: str) -> object:
    if type(value) is not str:
        raise _VerificationInputError(f"{context} must be canonical JSON text")
    try:
        decoded = _canonical.load_canonical_json_bytes(value.encode("utf-8"))
    except Exception as exc:
        raise _VerificationInputError(f"{context} is not canonical JSON") from exc
    if _canonical.canonical_json_bytes(decoded).decode("utf-8") != value:
        raise _VerificationInputError(f"{context} is not exact canonical JSON")
    return decoded


def _canonical_array(
    row: Mapping[str, object],
    field: str,
    *,
    item_type: type,
    nonempty: bool = False,
) -> tuple[object, ...]:
    decoded = _canonical_value(row[field], field)
    if type(decoded) is not list or (nonempty and not decoded):
        raise _VerificationInputError(f"{field} must be a canonical array")
    values = tuple(decoded)
    if any(type(item) is not item_type for item in values):
        raise _VerificationInputError(f"{field} has an invalid scalar type")
    if item_type is str:
        for item in values:
            assert type(item) is str
            if not item or unicodedata.normalize("NFC", item) != item:
                raise _VerificationInputError(f"{field} contains invalid text")
    return values


def _canonical_text_array(
    row: Mapping[str, object],
    field: str,
    *,
    nonempty: bool = False,
    hashes: bool = False,
) -> tuple[str, ...]:
    values = _canonical_array(row, field, item_type=str, nonempty=nonempty)
    result = tuple(str(item) for item in values)
    if hashes and any(_SHA256_RE.fullmatch(item) is None for item in result):
        raise _VerificationInputError(f"{field} must contain SHA-256 values")
    return result


def _canonical_int_array(
    row: Mapping[str, object],
    field: str,
    *,
    nonempty: bool = False,
) -> tuple[int, ...]:
    values = _canonical_array(row, field, item_type=int, nonempty=nonempty)
    return tuple(int(item) for item in values)


def _model_from_row(
    model: type[_ModelT],
    row: Mapping[str, object],
    *,
    json_arrays: Mapping[str, tuple[str, type]],
    renames: Mapping[str, str],
    extras: Mapping[str, object] | None = None,
) -> _ModelT:
    payload: dict[str, object] = {}
    supplied = {} if extras is None else dict(extras)
    for field in model.model_fields:
        if field in supplied:
            payload[field] = supplied[field]
        elif field in json_arrays:
            source, item_type = json_arrays[field]
            payload[field] = _canonical_array(
                row,
                source,
                item_type=item_type,
            )
        else:
            payload[field] = row[renames.get(field, field)]
    try:
        return model.model_validate(payload)
    except Exception as exc:
        raise _VerificationInputError(f"{model.__name__} row is invalid") from exc


def _declaration_contract(row: Mapping[str, object]) -> DataSnoopingDeclaration:
    array_fields = {
        "ordered_research_program_ids": ("ordered_research_program_ids_json", str),
        "ordered_research_question_ids": ("ordered_research_question_ids_json", str),
        "ordered_strategy_lineage_root_ids": (
            "ordered_strategy_lineage_root_ids_json",
            str,
        ),
        "block_length_grid": ("block_length_grid_json", int),
        "replication_schedule": ("replication_schedule_json", int),
        "allowed_search_kinds": ("allowed_search_kinds_json", str),
        "generator_seeds": ("generator_seeds_json", int),
        "search_proposal_budgets": ("search_proposal_budgets_json", int),
    }
    return _model_from_row(
        DataSnoopingDeclaration,
        row,
        json_arrays=array_fields,
        renames={"declaration_record_hash": "record_hash"},
    )


def _freeze_contract(row: Mapping[str, object]) -> FamilyFreezeReceipt:
    return _model_from_row(
        FamilyFreezeReceipt,
        row,
        json_arrays={
            "ordered_search_ids": ("ordered_search_ids_json", str),
            "ordered_search_terminal_hashes": (
                "ordered_search_terminal_hashes_json",
                str,
            ),
            "ordered_emission_seal_hashes": (
                "ordered_emission_seal_hashes_json",
                str,
            ),
            "ordered_candidate_chain_heads": (
                "ordered_candidate_chain_heads_json",
                str,
            ),
        },
        renames={"freeze_record_hash": "record_hash"},
    )


def _result_model(
    model: type[_ModelT],
    value: object,
    context: str,
) -> _ModelT:
    decoded = _canonical_value(value, context)
    try:
        return model.model_validate(decoded)
    except Exception as exc:
        raise _VerificationInputError(f"{context} has an invalid contract") from exc


def _grid_contract(row: Mapping[str, object]) -> DataSnoopingGridAssessment:
    reality_text = row["reality_check_payload_json"]
    spa_text = row["spa_payload_json"]
    reality = (
        None if reality_text is None else _result_model(RealityCheckResult, reality_text, "reality_check_payload_json")
    )
    spa = None if spa_text is None else _result_model(SpaResult, spa_text, "spa_payload_json")
    reasons = _canonical_text_array(row, "failure_reasons_json")
    return _model_from_row(
        DataSnoopingGridAssessment,
        row,
        json_arrays={},
        renames={
            "previous_grid_assessment_hash": "prev_record_hash",
            "grid_assessment_record_hash": "record_hash",
        },
        extras={
            "reality_check_result": reality,
            "spa_result": spa,
            "failure_reasons": reasons,
        },
    )


def _assessment_contract(row: Mapping[str, object]) -> DataSnoopingAssessment:
    return _model_from_row(
        DataSnoopingAssessment,
        row,
        json_arrays={
            "ordered_latest_grid_assessment_hashes": (
                "ordered_latest_grid_assessment_hashes_json",
                str,
            ),
            "failure_reasons": ("failure_reasons_json", str),
        },
        renames={"assessment_record_hash": "record_hash"},
    )


def _declaration_for_family(
    view: FamilySchemaVerificationView,
    authority_id: str,
    family_id: str,
    *,
    declaration_id: str | None = None,
    record_hash: str | None = None,
) -> FrozenVerificationRow:
    return _exactly_one(
        _view_rows(view, "data_snooping_declarations"),
        lambda row: (
            _text(row, "authority_instance_id") == authority_id
            and _text(row, "family_id") == family_id
            and (declaration_id is None or _text(row, "declaration_id") == declaration_id)
            and (record_hash is None or _sha256(row, "record_hash") == record_hash)
        ),
        "family declaration",
    )


def _freeze_for_family(
    view: FamilySchemaVerificationView,
    authority_id: str,
    family_id: str,
) -> FrozenVerificationRow:
    return _exactly_one(
        _family_rows(
            view,
            "data_snooping_family_freezes",
            authority_id,
            family_id,
            optional=True,
        ),
        lambda _: True,
        "family freeze",
    )


def _closure_for_family(
    view: FamilySchemaVerificationView,
    authority_id: str,
    family_id: str,
    *,
    record_hash: str | None = None,
) -> FrozenVerificationRow:
    return _exactly_one(
        _family_rows(
            view,
            "data_snooping_family_closures",
            authority_id,
            family_id,
            optional=True,
        ),
        lambda row: record_hash is None or _sha256(row, "record_hash") == record_hash,
        "family closure",
    )


def _verify_declaration_scope_history_predecessor(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_declarations"):
        return False
    declaration = _declaration_contract(owner)
    decisions = _view_rows(view, "selection_decisions")
    decision = _exactly_one(
        decisions,
        lambda row: (
            _text(row, "authority_instance_id") == declaration.authority_instance_id
            and _text(row, "selection_scope_id") == declaration.selection_scope_id
            and _text(row, "selection_decision_id") == declaration.selection_decision_id
            and _text(row, "family_id") == declaration.family_id
        ),
        "selection decision",
    )
    event_id = _text(decision, "authority_event_id")
    decision_hash = _sha256(decision, "record_hash")
    event = _exactly_one(
        _view_rows(view, "research_authority_events"),
        lambda row: (
            _text(row, "authority_instance_id") == declaration.authority_instance_id
            and _text(row, "event_id") == event_id
        ),
        "selection decision authority event",
    )
    return (
        _text(event, "subject_id") == declaration.selection_decision_id
        and _sha256(event, "subject_record_hash") == decision_hash
        and _sha256(event, "prev_record_hash") == declaration.selection_scope_predecessor_head
        and _integer(decision, "issued_at_us", minimum=1) < declaration.declared_at_us
    )


def _verify_declaration_program_question_root_totality(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_declarations"):
        return False
    declaration = _declaration_contract(owner)
    programs: list[FrozenVerificationRow] = []
    for program_id in declaration.ordered_research_program_ids:
        programs.append(
            _exactly_one(
                _view_rows(view, "research_programs"),
                lambda row, expected=program_id: (
                    _text(row, "authority_instance_id") == declaration.authority_instance_id
                    and _text(row, "selection_scope_id") == declaration.selection_scope_id
                    and _text(row, "research_program_id") == expected
                ),
                f"research program {program_id}",
            )
        )
    question_ids = tuple(sorted({_text(row, "research_question_id") for row in programs}))
    root_ids = tuple(sorted({_text(row, "strategy_lineage_root_id") for row in programs}))
    if (
        question_ids != declaration.ordered_research_question_ids
        or root_ids != declaration.ordered_strategy_lineage_root_ids
    ):
        return False
    searches = _family_rows(
        view,
        "data_snooping_family_searches",
        declaration.authority_instance_id,
        declaration.family_id,
        optional=True,
    )
    if not searches:
        return True
    search_programs = tuple(sorted({_text(row, "research_program_id") for row in searches}))
    return search_programs == declaration.ordered_research_program_ids


def _verify_declaration_search_oos_non_overlap(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_declarations"):
        return False
    declaration = _declaration_contract(owner)
    exposure = _exactly_one(
        _view_rows(view, "global_oos_exposures"),
        lambda row: (
            _text(row, "authority_instance_id") == declaration.authority_instance_id
            and _text(row, "oos_exposure_id") == declaration.oos_exposure_id
        ),
        "global OOS exposure",
    )
    if (
        _text(exposure, "selection_scope_id") != declaration.selection_scope_id
        or _text(exposure, "selection_decision_id") != declaration.selection_decision_id
        or _text(exposure, "family_id") != declaration.family_id
    ):
        return False
    oos_handle = _text(exposure, "observed_pit_handle_id")
    oos_content = _sha256(exposure, "observed_pit_content_hash")
    fingerprint = _sha256(exposure, "exposure_fingerprint")
    if (
        oos_handle == declaration.observed_pit_handle_id
        or oos_content == declaration.observed_pit_content_hash
        or fingerprint == declaration.search_data_contract_hash
    ):
        return False
    expected = _canonical.canonical_sha256(
        {
            "proof_version": "SEARCH_OOS_NON_OVERLAP_V1",
            "search_data_contract_hash": declaration.search_data_contract_hash,
            "search_observed_pit_handle_id": declaration.observed_pit_handle_id,
            "search_observed_pit_content_hash": declaration.observed_pit_content_hash,
            "oos_exposure_id": declaration.oos_exposure_id,
            "oos_exposure_fingerprint": fingerprint,
            "oos_observed_pit_handle_id": oos_handle,
            "oos_observed_pit_content_hash": oos_content,
            "purge_periods": declaration.purge_periods,
            "embargo_periods": declaration.embargo_periods,
        }
    )
    return declaration.non_overlap_proof_hash == expected


def _verify_declaration_ordered_sets_hashes(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_declarations"):
        return False
    declaration = _declaration_contract(owner)
    return all(
        tuple(values) == tuple(sorted(values))
        and len(set(values)) == len(values)
        and _canonical.canonical_sha256(values) == digest
        for values, digest in (
            (
                declaration.ordered_research_program_ids,
                declaration.ordered_research_program_ids_hash,
            ),
            (
                declaration.ordered_research_question_ids,
                declaration.ordered_research_question_ids_hash,
            ),
            (
                declaration.ordered_strategy_lineage_root_ids,
                declaration.ordered_strategy_lineage_root_ids_hash,
            ),
        )
    )


def _verify_declaration_grid_schedule(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_declarations"):
        return False
    declaration = _declaration_contract(owner)
    grid = declaration.block_length_grid
    schedule = declaration.replication_schedule
    return (
        bool(grid)
        and all(value > 0 for value in grid)
        and grid == tuple(sorted(set(grid)))
        and bool(schedule)
        and all(value > 0 for value in schedule)
        and schedule == tuple(sorted(set(schedule)))
        and schedule[-1] >= 10_000
        and 0 <= declaration.bootstrap_seed <= (1 << 63) - 1
    )


def _verify_declaration_code_data_dlp_binding(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_declarations"):
        return False
    declaration = _declaration_contract(owner)
    registrations = _family_rows(
        view,
        "data_snooping_proposal_registrations",
        declaration.authority_instance_id,
        declaration.family_id,
        optional=True,
    )
    for registration in registrations:
        if any(
            _text(registration, field) != expected
            for field, expected in (
                ("dlp_policy_id", declaration.dlp_policy_id),
                ("dlp_policy_version", declaration.dlp_policy_version),
                ("scanner_implementation", declaration.scanner_implementation),
                ("scanner_version", declaration.scanner_version),
            )
        ):
            return False
    freezes = _family_rows(
        view,
        "data_snooping_family_freezes",
        declaration.authority_instance_id,
        declaration.family_id,
        optional=True,
    )
    return all(
        _sha256(freeze, "observed_pit_content_hash") == declaration.observed_pit_content_hash
        and _sha256(freeze, "code_manifest_evidence_hash") == declaration.code_manifest_evidence_hash
        and _sha256(freeze, "dlp_policy_hash") == declaration.dlp_policy_hash
        for freeze in freezes
    )


def _verify_declaration_search_plan(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_declarations"):
        return False
    declaration = _declaration_contract(owner)
    count = declaration.required_search_count
    if not (
        count
        == declaration.maximum_search_count
        == len(declaration.allowed_search_kinds)
        == len(declaration.generator_seeds)
        == len(declaration.search_proposal_budgets)
        == len(declaration.ordered_research_program_ids)
    ):
        return False
    searches = _family_rows(
        view,
        "data_snooping_family_searches",
        declaration.authority_instance_id,
        declaration.family_id,
        optional=True,
    )
    ordinals: set[int] = set()
    for search in searches:
        ordinal = _integer(search, "link_ordinal", minimum=1)
        if ordinal > count or ordinal in ordinals:
            return False
        ordinals.add(ordinal)
        index = ordinal - 1
        if (
            _text(search, "research_program_id") not in declaration.ordered_research_program_ids
            or _text(search, "search_kind") != declaration.allowed_search_kinds[index]
            or _integer(search, "generator_seed", minimum=0) != declaration.generator_seeds[index]
            or _integer(search, "proposal_budget", minimum=1) != declaration.search_proposal_budgets[index]
            or _sha256(search, "stopping_rule_hash") != declaration.stopping_rule_hash
            or _integer(search, "external_ingestion_deadline_us", minimum=1)
            != declaration.external_ingestion_deadline_us
        ):
            return False
    return not searches or ordinals == set(range(1, count + 1))


def _verify_declaration_precedes_family_activity(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_declarations"):
        return False
    declaration = _declaration_contract(owner)
    for table, time_field in (
        ("data_snooping_family_searches", "linked_at_us"),
        ("data_snooping_proposal_registrations", "registered_at_us"),
        ("data_snooping_proposals", "parsed_at_us"),
        ("data_snooping_oos_access_attempts", "event_at_us"),
    ):
        for row in _family_rows(
            view,
            table,
            declaration.authority_instance_id,
            declaration.family_id,
            optional=True,
        ):
            if _integer(row, time_field, minimum=1) <= declaration.declared_at_us:
                return False
            if "declaration_id" in row and (
                _text(row, "declaration_id") != declaration.declaration_id
                or _sha256(row, "declaration_record_hash") != declaration.declaration_record_hash
            ):
                return False
    return True


def _verify_family_freeze_terminal_search_totality(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_family_freezes"):
        return False
    freeze = _freeze_contract(owner)
    searches = sorted(
        _family_rows(
            view,
            "data_snooping_family_searches",
            freeze.authority_instance_id,
            freeze.family_id,
        ),
        key=lambda row: (
            _integer(row, "link_ordinal", minimum=1),
            _text(row, "search_id"),
        ),
    )
    if tuple(_text(row, "search_id") for row in searches) != freeze.ordered_search_ids:
        return False
    if len(searches) != freeze.search_count:
        return False
    terminal_hashes: list[str] = []
    all_events = _view_rows(view, "search_attempt_events")
    for search in searches:
        search_id = _text(search, "search_id")
        events = sorted(
            (row for row in all_events if _text(row, "search_id") == search_id),
            key=lambda row: _integer(row, "event_seq", minimum=1),
        )
        terminals = tuple(row for row in events if _text(row, "status") in ("FAILED", "COMPLETED"))
        if len(terminals) != 1 or not events or terminals[0] is not events[-1]:
            return False
        terminal = terminals[0]
        if _integer(terminal, "recorded_at", minimum=0) >= freeze.frozen_at_us:
            return False
        terminal_hashes.append(_sha256(terminal, "event_hash"))
    return tuple(terminal_hashes) == freeze.ordered_search_terminal_hashes


def _cutoff_bounds(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    *,
    table_name: str,
    time_field: str,
    phase: str,
) -> bool:
    if not _same_owner_row(owner, view, table_name):
        return False
    authority = _text(owner, "authority_instance_id")
    family = _text(owner, "family_id")
    event_time = _integer(owner, time_field, minimum=1)
    freezes = _family_rows(
        view,
        "data_snooping_family_freezes",
        authority,
        family,
        optional=True,
    )
    closures = _family_rows(
        view,
        "data_snooping_family_closures",
        authority,
        family,
        optional=True,
    )
    if len(freezes) > 1 or len(closures) > 1:
        return False
    if phase == "BEFORE_FREEZE":
        if freezes and event_time >= _integer(freezes[0], "frozen_at_us", minimum=1):
            return False
    elif phase == "AFTER_FREEZE":
        if len(freezes) != 1 or event_time <= _integer(freezes[0], "frozen_at_us", minimum=1):
            return False
    else:
        raise _VerificationInputError("cutoff phase is not closed")
    return not closures or event_time < _integer(closures[0], "closed_at_us", minimum=1)


def _verify_family_freeze_activity_cutoff(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_family_freezes"):
        return False
    freeze = _freeze_contract(owner)
    for table, time_field in _BEFORE_FREEZE_TIME.items():
        for row in _family_rows(
            view,
            table,
            freeze.authority_instance_id,
            freeze.family_id,
            optional=True,
        ):
            if not _cutoff_bounds(
                row,
                view,
                table_name=table,
                time_field=time_field,
                phase="BEFORE_FREEZE",
            ):
                return False
    for hypothesis in _family_rows(
        view,
        "data_snooping_hypotheses",
        freeze.authority_instance_id,
        freeze.family_id,
        optional=True,
    ):
        if not _verify_hypothesis_cutoff(hypothesis, view):
            return False
    for table, time_field in _AFTER_FREEZE_TIME.items():
        for row in _family_rows(
            view,
            table,
            freeze.authority_instance_id,
            freeze.family_id,
            optional=True,
        ):
            if not _cutoff_bounds(
                row,
                view,
                table_name=table,
                time_field=time_field,
                phase="AFTER_FREEZE",
            ):
                return False
    closures = _family_rows(
        view,
        "data_snooping_family_closures",
        freeze.authority_instance_id,
        freeze.family_id,
        optional=True,
    )
    return not closures or (
        len(closures) == 1 and freeze.frozen_at_us < _integer(closures[0], "closed_at_us", minimum=1)
    )


def _authority_chain_segment(
    view: FamilySchemaVerificationView,
    authority_id: str,
    ancestor_hash: str,
    head_hash: str,
) -> tuple[FrozenVerificationRow, ...]:
    events = sorted(
        (
            row
            for row in _view_rows(view, "research_authority_events")
            if _text(row, "authority_instance_id") == authority_id
        ),
        key=lambda row: _integer(row, "event_ordinal", minimum=1),
    )
    if not events or len({_integer(row, "event_ordinal") for row in events}) != len(events):
        raise _VerificationInputError("authority event order is not unique")
    heads = {_sha256(row, "record_hash"): index for index, row in enumerate(events)}
    if head_hash not in heads:
        raise _VerificationInputError("scope history head is absent")
    end = heads[head_hash]
    if ancestor_hash in heads:
        start = heads[ancestor_hash] + 1
    elif _sha256(events[0], "prev_record_hash") == ancestor_hash:
        start = 0
    else:
        raise _VerificationInputError("scope history ancestor is absent")
    if start > end:
        raise _VerificationInputError("scope history head precedes its ancestor")
    previous = ancestor_hash
    segment = events[start : end + 1]
    for event in segment:
        if _sha256(event, "prev_record_hash") != previous:
            raise _VerificationInputError("scope history chain has a gap")
        previous = _sha256(event, "record_hash")
    if previous != head_hash:
        raise _VerificationInputError("scope history head is not terminal")
    return tuple(segment)


def _verify_family_closure_scope_history(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_family_closures"):
        return False
    authority = _text(owner, "authority_instance_id")
    family = _text(owner, "family_id")
    declaration = _declaration_for_family(
        view,
        authority,
        family,
        declaration_id=_text(owner, "declaration_id"),
        record_hash=_sha256(owner, "declaration_record_hash"),
    )
    declaration_contract = _declaration_contract(declaration)
    if (
        _text(owner, "selection_scope_id") != declaration_contract.selection_scope_id
        or _text(owner, "selection_decision_id") != declaration_contract.selection_decision_id
    ):
        return False
    head = _sha256(owner, "selection_scope_history_head")
    segment = _authority_chain_segment(
        view,
        authority,
        declaration_contract.selection_scope_predecessor_head,
        head,
    )
    decision = _exactly_one(
        _view_rows(view, "selection_decisions"),
        lambda row: (
            _text(row, "authority_instance_id") == authority
            and _text(row, "selection_scope_id") == declaration_contract.selection_scope_id
            and _text(row, "selection_decision_id") == declaration_contract.selection_decision_id
            and _text(row, "family_id") == family
        ),
        "closure selection decision",
    )
    event_id = _text(decision, "authority_event_id")
    if not any(_text(event, "event_id") == event_id for event in segment):
        return False
    outcome_hash = _optional_sha256(owner, "selection_scope_outcome_record_hash")
    if outcome_hash is None:
        return True
    outcome = _exactly_one(
        _view_rows(view, "selection_scope_outcomes"),
        lambda row: _sha256(row, "record_hash") == outcome_hash,
        "selection scope outcome",
    )
    return (
        _text(outcome, "authority_instance_id") == authority
        and _text(outcome, "selection_scope_id") == declaration_contract.selection_scope_id
        and _text(outcome, "selection_decision_id") == declaration_contract.selection_decision_id
        and _text(outcome, "family_id") == family
    )


def _series_from_receipt(
    row: Mapping[str, object],
) -> tuple[tuple[PeriodKey, ...], tuple[float, ...]]:
    decoded = _canonical_value(row["series_payload_json"], "series_payload_json")
    if type(decoded) is not dict or set(decoded) != _SERIES_KEYS:
        raise _VerificationInputError("series payload has an incompatible shape")
    for value_field, hash_field in _ARRAY_HASH_FIELDS:
        expected = _canonical.canonical_sha256(decoded[value_field])
        if decoded[hash_field] != expected:
            raise _VerificationInputError(f"{hash_field} does not bind its payload")
        if hash_field in row and _sha256(row, hash_field) != expected:
            raise _VerificationInputError(f"receipt {hash_field} does not bind its payload")
    without_series_hash = {key: value for key, value in decoded.items() if key != "series_hash"}
    expected_series_hash = _canonical.canonical_sha256(without_series_hash)
    if decoded["series_hash"] != expected_series_hash:
        raise _VerificationInputError("series hash does not bind its payload")
    if "series_hash" in row and _sha256(row, "series_hash") != expected_series_hash:
        raise _VerificationInputError("receipt series hash mismatch")

    raw_periods = decoded["ordered_period_keys"]
    if type(raw_periods) is not list or not raw_periods:
        raise _VerificationInputError("period keys must be a non-empty array")
    periods: list[PeriodKey] = []
    for item in raw_periods:
        if type(item) is not dict or set(item) != _PERIOD_KEYS:
            raise _VerificationInputError("period key has an incompatible shape")
        try:
            periods.append(PeriodKey.model_validate(item))
        except Exception as exc:
            raise _VerificationInputError("period key is invalid") from exc

    parsed: dict[str, tuple[float, ...]] = {}
    for field in _VECTOR_FIELDS:
        values = decoded[field]
        if type(values) is not list or len(values) != len(periods):
            raise _VerificationInputError(f"{field} is not calendar aligned")
        numbers: list[float] = []
        for value in values:
            if type(value) is not str:
                raise _VerificationInputError(f"{field} must contain float64 hex")
            try:
                number = _canonical.parse_float64_hex(value)
            except Exception as exc:
                raise _VerificationInputError(f"{field} has invalid float64 hex") from exc
            if not math.isfinite(number) or (number == 0.0 and math.copysign(1.0, number) < 0.0):
                raise _VerificationInputError(f"{field} has invalid float64 values")
            numbers.append(number)
        parsed[field] = tuple(numbers)
    if any(value < 0.0 for field in ("costs", "turnovers") for value in parsed[field]):
        raise _VerificationInputError("costs and turnovers must be non-negative")
    net_hex = decoded["net_returns"]
    assert type(net_hex) is list
    if any(
        _canonical.float64_hex(gross - cost) != net
        for gross, cost, net in zip(
            parsed["gross_returns"],
            parsed["costs"],
            net_hex,
            strict=True,
        )
    ):
        raise _VerificationInputError("gross minus cost does not exactly equal net")
    return tuple(periods), parsed["net_returns"]


def _statistical_inputs(
    grid: DataSnoopingGridAssessment,
    view: FamilySchemaVerificationView,
) -> tuple[
    tuple[tuple[str, tuple[float, ...]], ...],
    tuple[float, ...],
    tuple[PeriodKey, ...],
    DataSnoopingDeclaration,
]:
    closure = _closure_for_family(
        view,
        grid.authority_instance_id,
        grid.family_id,
        record_hash=grid.closure_manifest_hash,
    )
    if _text(closure, "closure_status") != "COMPLETE":
        raise _VerificationInputError("grid assessment requires a complete closure")
    declaration_row = _declaration_for_family(
        view,
        grid.authority_instance_id,
        grid.family_id,
        declaration_id=_text(closure, "declaration_id"),
        record_hash=_sha256(closure, "declaration_record_hash"),
    )
    declaration = _declaration_contract(declaration_row)
    configs = _canonical_text_array(
        closure,
        "ordered_config_hashes_json",
        nonempty=True,
        hashes=True,
    )
    receipt_hashes = _canonical_text_array(
        closure,
        "ordered_evaluation_receipt_hashes_json",
        nonempty=True,
        hashes=True,
    )
    if configs != tuple(sorted(configs)) or len(set(configs)) != len(configs) or len(configs) != len(receipt_hashes):
        raise _VerificationInputError("closure candidate order is invalid")
    batch_id = _text(closure, "evaluation_batch_id")
    candidates: list[tuple[str, tuple[float, ...]]] = []
    common_periods: tuple[PeriodKey, ...] | None = None
    receipts = _view_rows(view, "data_snooping_evaluation_receipts")
    for config_hash, receipt_hash in zip(configs, receipt_hashes, strict=True):
        receipt = _exactly_one(
            receipts,
            lambda row, config=config_hash, digest=receipt_hash: (
                _text(row, "authority_instance_id") == grid.authority_instance_id
                and _text(row, "family_id") == grid.family_id
                and _text(row, "evaluation_batch_id") == batch_id
                and _sha256(row, "evaluation_receipt_hash") == digest
                and _sha256(row, "config_hash") == config
            ),
            f"candidate receipt {config_hash}",
        )
        periods, returns = _series_from_receipt(receipt)
        if common_periods is None:
            common_periods = periods
        elif periods != common_periods:
            raise _VerificationInputError("candidate calendars do not match")
        candidates.append((config_hash, returns))
    benchmark_hash = _sha256(closure, "benchmark_receipt_hash")
    benchmark = _exactly_one(
        _view_rows(view, "data_snooping_benchmark_returns"),
        lambda row: (
            _text(row, "authority_instance_id") == grid.authority_instance_id
            and _text(row, "family_id") == grid.family_id
            and _text(row, "evaluation_batch_id") == batch_id
            and _sha256(row, "benchmark_receipt_hash") == benchmark_hash
        ),
        "benchmark return receipt",
    )
    benchmark_periods, benchmark_returns = _series_from_receipt(benchmark)
    if common_periods is None or benchmark_periods != common_periods:
        raise _VerificationInputError("benchmark calendar does not match candidates")
    if len(common_periods) != grid.period_count:
        raise _VerificationInputError("grid period count does not match receipts")
    if len(candidates) < 2:
        raise RealityCheckInputError("at least two candidate configurations are required")
    return tuple(candidates), benchmark_returns, common_periods, declaration


def _expected_grid_state(
    reality: RealityCheckResult,
    spa: SpaResult,
    *,
    schedule_ordinal: int,
    schedule_length: int,
) -> tuple[str, str]:
    primary = (reality.decision, spa.variants[1].decision)
    ambiguous = reality.tail_result.ambiguous or spa.variants[1].tail_result.ambiguous
    if ambiguous:
        return (
            "AMBIGUOUS",
            "PENDING_MORE_REPLICATIONS" if schedule_ordinal < schedule_length else "MONTE_CARLO_UNRESOLVED",
        )
    if reality.run_status != "PUBLISHABLE":
        return "RESOLVED", "PENDING_MORE_REPLICATIONS"
    if primary == ("REJECT", "REJECT"):
        return "RESOLVED", "SUPPORTS_SUPERIORITY"
    if primary == ("NON_REJECT", "NON_REJECT"):
        return "RESOLVED", "DOES_NOT_SUPPORT_SUPERIORITY"
    if set(primary) == {"REJECT", "NON_REJECT"}:
        return "RESOLVED", "METHOD_DISAGREEMENT"
    raise _VerificationInputError("primary kernel decisions are invalid")


def _verify_grid_assessment_result_payload_hashes(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_grid_assessments"):
        return False
    grid = _grid_contract(owner)
    try:
        candidates, benchmark, periods, declaration = _statistical_inputs(grid, view)
        if grid.block_length not in declaration.block_length_grid:
            return False
        schedule = declaration.replication_schedule
        if grid.schedule_ordinal > len(schedule) or grid.replication_count != schedule[grid.schedule_ordinal - 1]:
            return False
        prefixes = schedule[: grid.schedule_ordinal]
        reality, spa = compute_reality_check_and_spa(
            candidates,
            benchmark,
            periods,
            grid.block_length,
            _canonical.parse_float64_hex(declaration.alpha_level),
            declaration.bootstrap_seed,
            grid.replication_count,
            prefixes,
        )
    except (RealityCheckInputError, _VerificationInputError, ValueError):
        return (
            grid.grid_point_status == "INVALID"
            and grid.grid_point_verdict == "NOT_APPLICABLE"
            and bool(grid.failure_reasons)
            and grid.reality_check_result is None
            and grid.spa_result is None
        )
    if grid.grid_point_status == "INVALID":
        return False
    expected_status, expected_verdict = _expected_grid_state(
        reality,
        spa,
        schedule_ordinal=grid.schedule_ordinal,
        schedule_length=len(schedule),
    )
    return (
        grid.reality_check_result == reality
        and grid.spa_result == spa
        and grid.reality_check_result_hash == _canonical.canonical_sha256(reality)
        and grid.spa_result_hash == _canonical.canonical_sha256(spa)
        and grid.prefix_index_stream_hash == reality.prefix_hashes[-1].index_stream_sha256
        and grid.grid_point_status == expected_status
        and grid.grid_point_verdict == expected_verdict
    )


def _latest_grid_rows(
    rows: tuple[FrozenVerificationRow, ...],
) -> tuple[FrozenVerificationRow, ...]:
    by_block: dict[int, list[FrozenVerificationRow]] = {}
    for row in rows:
        by_block.setdefault(_integer(row, "block_length", minimum=1), []).append(row)
    latest: list[FrozenVerificationRow] = []
    for block in sorted(by_block):
        ordered = sorted(
            by_block[block],
            key=lambda row: _integer(row, "schedule_ordinal", minimum=1),
        )
        ordinals = tuple(_integer(row, "schedule_ordinal", minimum=1) for row in ordered)
        if ordinals != tuple(range(1, len(ordered) + 1)):
            raise _VerificationInputError("grid schedule has a gap or duplicate")
        if any(_text(row, "grid_point_verdict") != "PENDING_MORE_REPLICATIONS" for row in ordered[:-1]):
            raise _VerificationInputError("grid rows continued after a terminal point")
        latest.append(ordered[-1])
    return tuple(latest)


def _assessment_expected_verdict(latest: Sequence[FrozenVerificationRow]) -> str:
    verdicts = tuple(_text(row, "grid_point_verdict") for row in latest)
    if "MONTE_CARLO_UNRESOLVED" in verdicts:
        return "MONTE_CARLO_UNRESOLVED"
    if "METHOD_DISAGREEMENT" in verdicts:
        return "METHOD_DISAGREEMENT"
    if verdicts and all(value == "SUPPORTS_SUPERIORITY" for value in verdicts):
        return "SUPPORTS_SUPERIORITY"
    if verdicts and all(value in ("SUPPORTS_SUPERIORITY", "DOES_NOT_SUPPORT_SUPERIORITY") for value in verdicts):
        return "DOES_NOT_SUPPORT_SUPERIORITY"
    raise _VerificationInputError("latest grid rows are not terminal")


def _complete_family_inputs_are_assessable(
    closure: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    """Recompute whether a complete closure can legitimately omit all grids."""

    authority = _text(closure, "authority_instance_id")
    family = _text(closure, "family_id")
    benchmark_hash = _sha256(closure, "benchmark_receipt_hash")
    benchmark_row = _exactly_one(
        _view_rows(view, "data_snooping_benchmark_returns"),
        lambda row: (
            _text(row, "authority_instance_id") == authority
            and _text(row, "family_id") == family
            and _sha256(row, "benchmark_receipt_hash") == benchmark_hash
        ),
        "benchmark return receipt",
    )
    periods, _ = _series_from_receipt(benchmark_row)
    probe = DataSnoopingGridAssessment.model_construct(
        authority_instance_id=authority,
        family_id=family,
        closure_manifest_hash=_sha256(closure, "record_hash"),
        period_count=len(periods),
    )
    candidates, benchmark, common_periods, declaration = _statistical_inputs(
        probe,
        view,
    )
    if not declaration.replication_schedule:
        return False
    terminal_replications = declaration.replication_schedule[-1]
    for block_length in declaration.block_length_grid:
        compute_reality_check_and_spa(
            candidates,
            benchmark,
            common_periods,
            block_length,
            _canonical.parse_float64_hex(declaration.alpha_level),
            declaration.bootstrap_seed,
            terminal_replications,
            declaration.replication_schedule,
        )
    return True


def _verify_assessment_row(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_assessments"):
        return False
    assessment = _assessment_contract(owner)
    closure = _closure_for_family(
        view,
        assessment.authority_instance_id,
        assessment.family_id,
        record_hash=assessment.closure_manifest_hash,
    )
    grids = tuple(
        row
        for row in _family_rows(
            view,
            "data_snooping_grid_assessments",
            assessment.authority_instance_id,
            assessment.family_id,
            optional=True,
        )
        if _sha256(row, "closure_manifest_hash") == assessment.closure_manifest_hash
        and _text(row, "algorithm_version") == assessment.algorithm_version
    )
    if _text(closure, "closure_status") == "NOT_ASSESSABLE":
        return (
            assessment.assessment_status == "NOT_ASSESSABLE"
            and assessment.diagnostic_verdict == "NOT_APPLICABLE"
            and not grids
            and not assessment.ordered_latest_grid_assessment_hashes
            and bool(assessment.failure_reasons)
        )
    declaration_row = _declaration_for_family(
        view,
        assessment.authority_instance_id,
        assessment.family_id,
        declaration_id=_text(closure, "declaration_id"),
        record_hash=_sha256(closure, "declaration_record_hash"),
    )
    declaration = _declaration_contract(declaration_row)
    if not grids:
        try:
            assessable = _complete_family_inputs_are_assessable(closure, view)
        except Exception:
            assessable = False
        return (
            not assessable
            and assessment.assessment_status == "NOT_ASSESSABLE"
            and assessment.diagnostic_verdict == "NOT_APPLICABLE"
            and bool(assessment.failure_reasons)
            and not assessment.ordered_latest_grid_assessment_hashes
        )
    if not all(_verify_grid_assessment_result_payload_hashes(row, view) for row in grids):
        return False
    latest = _latest_grid_rows(grids)
    blocks = tuple(_integer(row, "block_length", minimum=1) for row in latest)
    if blocks != declaration.block_length_grid:
        return False
    latest_hashes = tuple(_sha256(row, "record_hash") for row in latest)
    if assessment.ordered_latest_grid_assessment_hashes != latest_hashes:
        return False
    invalid = tuple(row for row in latest if _text(row, "grid_point_status") == "INVALID")
    if invalid:
        reasons = tuple(
            sorted({reason for row in invalid for reason in _canonical_text_array(row, "failure_reasons_json")})
        )
        return (
            assessment.assessment_status == "NOT_ASSESSABLE"
            and assessment.diagnostic_verdict == "NOT_APPLICABLE"
            and assessment.failure_reasons == reasons
        )
    if any(_text(row, "grid_point_verdict") == "PENDING_MORE_REPLICATIONS" for row in latest):
        return False
    realities = tuple(_grid_contract(row).reality_check_result for row in latest)
    if any(result is None for result in realities):
        return False
    typed_realities = tuple(result for result in realities if result is not None)
    candidate_orders = {result.candidate_order for result in typed_realities}
    period_counts = {result.period_count for result in typed_realities}
    if len(candidate_orders) != 1 or len(period_counts) != 1:
        return False
    candidate_order = next(iter(candidate_orders))
    period_count = next(iter(period_counts))
    minimum_replications = min(_integer(row, "replication_count", minimum=1) for row in latest)
    return (
        assessment.assessment_status == "ASSESSED"
        and assessment.diagnostic_verdict == _assessment_expected_verdict(latest)
        and assessment.candidate_order_hash == _canonical.canonical_sha256(candidate_order)
        and assessment.period_count == period_count
        and assessment.minimum_replication_count == minimum_replications
        and minimum_replications >= 10_000
    )


def _verify_family_assessment(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if "family_assessment_conclusion_hash" not in owner:
        return _verify_assessment_row(owner, view)
    if not _same_owner_row(owner, view, "artifact_publication_receipts"):
        return False
    assessment_hash = _sha256(owner, "family_assessment_conclusion_hash")
    assessment_row = _exactly_one(
        _view_rows(view, "data_snooping_assessments"),
        lambda row: _sha256(row, "record_hash") == assessment_hash,
        "published family assessment",
    )
    assessment = _assessment_contract(assessment_row)
    published_hashes = _canonical_text_array(
        owner,
        "ordered_grid_assessment_record_hashes_json",
        hashes=True,
    )
    return (
        _text(owner, "authority_instance_id") == assessment.authority_instance_id
        and _text(owner, "family_id") == assessment.family_id
        and _sha256(owner, "closure_manifest_hash") == assessment.closure_manifest_hash
        and published_hashes == assessment.ordered_latest_grid_assessment_hashes
        and _sha256(owner, "ordered_grid_assessment_record_hashes_hash")
        == assessment.ordered_latest_grid_assessment_hashes_hash
        and owner["minimum_replication_count"] == assessment.minimum_replication_count
        and _text(owner, "assessment_status") == assessment.assessment_status
        and _text(owner, "diagnostic_verdict") == assessment.diagnostic_verdict
        and _verify_assessment_row(assessment_row, view)
    )


def _verify_before_freeze_cutoff(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    table_name: str,
    time_field: str,
) -> bool:
    return _cutoff_bounds(
        owner,
        view,
        table_name=table_name,
        time_field=time_field,
        phase="BEFORE_FREEZE",
    )


def _verify_after_freeze_cutoff(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
    table_name: str,
    time_field: str,
) -> bool:
    return _cutoff_bounds(
        owner,
        view,
        table_name=table_name,
        time_field=time_field,
        phase="AFTER_FREEZE",
    )


def _verify_hypothesis_cutoff(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    if not _same_owner_row(owner, view, "data_snooping_hypotheses"):
        return False
    authority = _text(owner, "authority_instance_id")
    family = _text(owner, "family_id")
    proposal_id = _sha256(owner, "first_proposal_id")
    search_id = _text(owner, "first_search_id")
    proposal = _exactly_one(
        _family_rows(view, "data_snooping_proposals", authority, family),
        lambda row: _sha256(row, "proposal_id") == proposal_id,
        "hypothesis first proposal",
    )
    if (
        _text(proposal, "search_id") != search_id
        or _text(proposal, "parse_status") != "ACCEPTED"
        or _sha256(proposal, "config_hash") != _sha256(owner, "config_hash")
        or _sha256(proposal, "canonical_proposal_hash")
        != _sha256(owner, "canonical_hypothesis_hash")
        or _text(proposal, "alpha_id") != _text(owner, "alpha_id")
    ):
        return False
    return _cutoff_bounds(
        proposal,
        view,
        table_name="data_snooping_proposals",
        time_field="parsed_at_us",
        phase="BEFORE_FREEZE",
    )


def _invoke(
    helper: Callable[[FrozenVerificationRow, FamilySchemaVerificationView], bool],
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    try:
        _ = providers
        if not isinstance(view, FamilySchemaVerificationView):
            raise _VerificationInputError("verification view is not the typed read view")
        return helper(_owner_row(owner_row), view) is True
    except Exception:
        return False


def verify_declaration_scope_history_predecessor(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_declaration_scope_history_predecessor, owner_row, view, providers)


def verify_declaration_program_question_root_totality(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        _verify_declaration_program_question_root_totality,
        owner_row,
        view,
        providers,
    )


def verify_declaration_search_oos_non_overlap(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_declaration_search_oos_non_overlap, owner_row, view, providers)


def verify_declaration_ordered_sets_hashes(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_declaration_ordered_sets_hashes, owner_row, view, providers)


def verify_declaration_grid_schedule(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_declaration_grid_schedule, owner_row, view, providers)


def verify_declaration_code_data_dlp_binding(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_declaration_code_data_dlp_binding, owner_row, view, providers)


def verify_declaration_search_plan(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_declaration_search_plan, owner_row, view, providers)


def verify_declaration_precedes_family_activity(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_declaration_precedes_family_activity, owner_row, view, providers)


def verify_family_freeze_terminal_search_totality(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_family_freeze_terminal_search_totality, owner_row, view, providers)


def verify_family_freeze_activity_cutoff(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_family_freeze_activity_cutoff, owner_row, view, providers)


def verify_family_closure_scope_history(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_family_closure_scope_history, owner_row, view, providers)


def verify_grid_assessment_result_payload_hashes(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        _verify_grid_assessment_result_payload_hashes,
        owner_row,
        view,
        providers,
    )


def verify_family_assessment(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_family_assessment, owner_row, view, providers)


def verify_family_search_freeze_closure_cutoff(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda owner, typed_view: _verify_before_freeze_cutoff(
            owner,
            typed_view,
            "data_snooping_family_searches",
            "linked_at_us",
        ),
        owner_row,
        view,
        providers,
    )


def verify_proposal_freeze_closure_cutoff(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda owner, typed_view: _verify_before_freeze_cutoff(
            owner,
            typed_view,
            "data_snooping_proposal_registrations",
            "registered_at_us",
        ),
        owner_row,
        view,
        providers,
    )


def verify_proposal_terminal_result_freeze_closure_cutoff(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        lambda owner, typed_view: _verify_before_freeze_cutoff(
            owner,
            typed_view,
            "data_snooping_proposals",
            "parsed_at_us",
        ),
        owner_row,
        view,
        providers,
    )


def verify_hypothesis_freeze_closure_cutoff(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(_verify_hypothesis_cutoff, owner_row, view, providers)


def verify_emission_seal_freeze_closure_cutoff(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda owner, typed_view: _verify_before_freeze_cutoff(
            owner,
            typed_view,
            "data_snooping_search_emission_seals",
            "sealed_at_us",
        ),
        owner_row,
        view,
        providers,
    )


def verify_candidate_disposition_freeze_closure_cutoff(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda owner, typed_view: _verify_after_freeze_cutoff(
            owner,
            typed_view,
            "data_snooping_candidate_dispositions",
            "recorded_at_us",
        ),
        owner_row,
        view,
        providers,
    )


def verify_evaluation_batch_freeze_closure_cutoff(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda owner, typed_view: _verify_after_freeze_cutoff(
            owner,
            typed_view,
            "data_snooping_evaluation_batches",
            "committed_at_us",
        ),
        owner_row,
        view,
        providers,
    )


def verify_family_oos_evaluation_freeze_closure_cutoff(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda owner, typed_view: _verify_after_freeze_cutoff(
            owner,
            typed_view,
            "data_snooping_family_oos_evaluations",
            "linked_at_us",
        ),
        owner_row,
        view,
        providers,
    )


def verify_evaluation_receipt_freeze_closure_cutoff(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda owner, typed_view: _verify_after_freeze_cutoff(
            owner,
            typed_view,
            "data_snooping_evaluation_receipts",
            "committed_at_us",
        ),
        owner_row,
        view,
        providers,
    )


def verify_benchmark_return_freeze_closure_cutoff(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda owner, typed_view: _verify_after_freeze_cutoff(
            owner,
            typed_view,
            "data_snooping_benchmark_returns",
            "committed_at_us",
        ),
        owner_row,
        view,
        providers,
    )


@dataclass(frozen=True, slots=True)
class C8NamedVerifierImplementation:
    """One exact C8 named binding and its fail-closed Python callable."""

    surface_kind: str
    owner_table: str
    verifier_code: str
    implementation_id: str
    implementation_requirement_id: str
    implementation: object

    def __post_init__(self) -> None:
        if self.surface_kind not in ("TABLE", "EXTENSION"):
            raise ValueError("C8 verifier surface kind is not closed")
        if type(self.owner_table) is not str or not self.owner_table:
            raise TypeError("C8 verifier owner table must be exact text")
        if type(self.verifier_code) is not str or not self.verifier_code.startswith("c8.verify_"):
            raise TypeError("C8 verifier code must be exact closed text")
        if self.implementation_id != (f"FAMILY_SCHEMA_NAMED_VERIFIER::{self.verifier_code}"):
            raise ValueError("C8 verifier implementation ID does not match its code")
        if self.implementation_requirement_id != f"NAMED_VERIFIER::{self.verifier_code}":
            raise ValueError("C8 verifier requirement ID does not match its code")
        if type(self.implementation) is not FunctionType:
            raise TypeError("C8 named verifier implementation must be a Python function")
        parameters = tuple(inspect.signature(self.implementation).parameters.values())
        if len(parameters) != 3 or any(
            parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
            or parameter.default is not inspect.Parameter.empty
            for parameter in parameters
        ):
            raise TypeError("C8 named verifier must accept exactly owner_row, view, and providers")


_IMPLEMENTATION_FUNCTIONS_BY_CODE = (
    (
        "c8.verify_declaration_scope_history_predecessor_v1",
        verify_declaration_scope_history_predecessor,
    ),
    (
        "c8.verify_declaration_program_question_root_totality_v1",
        verify_declaration_program_question_root_totality,
    ),
    (
        "c8.verify_declaration_search_oos_non_overlap_v1",
        verify_declaration_search_oos_non_overlap,
    ),
    (
        "c8.verify_declaration_ordered_sets_hashes_v1",
        verify_declaration_ordered_sets_hashes,
    ),
    ("c8.verify_declaration_grid_schedule_v1", verify_declaration_grid_schedule),
    (
        "c8.verify_declaration_code_data_dlp_binding_v1",
        verify_declaration_code_data_dlp_binding,
    ),
    ("c8.verify_declaration_search_plan_v1", verify_declaration_search_plan),
    (
        "c8.verify_declaration_precedes_family_activity_v1",
        verify_declaration_precedes_family_activity,
    ),
    (
        "c8.verify_family_freeze_terminal_search_totality_v1",
        verify_family_freeze_terminal_search_totality,
    ),
    (
        "c8.verify_family_freeze_activity_cutoff_v1",
        verify_family_freeze_activity_cutoff,
    ),
    (
        "c8.verify_family_closure_scope_history_v1",
        verify_family_closure_scope_history,
    ),
    (
        "c8.verify_grid_assessment_result_payload_hashes_v1",
        verify_grid_assessment_result_payload_hashes,
    ),
    (_ASSESSMENT_CODE, verify_family_assessment),
    (
        "c8.verify_family_search_freeze_closure_cutoff_v1",
        verify_family_search_freeze_closure_cutoff,
    ),
    (
        "c8.verify_proposal_freeze_closure_cutoff_v1",
        verify_proposal_freeze_closure_cutoff,
    ),
    (
        "c8.verify_proposal_terminal_result_freeze_closure_cutoff_v1",
        verify_proposal_terminal_result_freeze_closure_cutoff,
    ),
    (
        "c8.verify_hypothesis_freeze_closure_cutoff_v1",
        verify_hypothesis_freeze_closure_cutoff,
    ),
    (
        "c8.verify_emission_seal_freeze_closure_cutoff_v1",
        verify_emission_seal_freeze_closure_cutoff,
    ),
    (
        "c8.verify_candidate_disposition_freeze_closure_cutoff_v1",
        verify_candidate_disposition_freeze_closure_cutoff,
    ),
    (
        "c8.verify_evaluation_batch_freeze_closure_cutoff_v1",
        verify_evaluation_batch_freeze_closure_cutoff,
    ),
    (
        "c8.verify_family_oos_evaluation_freeze_closure_cutoff_v1",
        verify_family_oos_evaluation_freeze_closure_cutoff,
    ),
    (
        "c8.verify_evaluation_receipt_freeze_closure_cutoff_v1",
        verify_evaluation_receipt_freeze_closure_cutoff,
    ),
    (
        "c8.verify_benchmark_return_freeze_closure_cutoff_v1",
        verify_benchmark_return_freeze_closure_cutoff,
    ),
)


def _declared_c8_named_bindings() -> tuple[tuple[str, str, str, str], ...]:
    rows: list[tuple[str, str, str, str]] = []
    for table in C8_SCHEMA_COMPONENT.tables:
        rows.extend(
            ("TABLE", table.table_name, rule.rule_id, rule.verifier_code)
            for rule in table.verification_rules
            if rule.verifier_code is not None
        )
    for extension in C8_SCHEMA_COMPONENT.extensions:
        rows.extend(
            ("EXTENSION", extension.target_table, rule.rule_id, rule.verifier_code)
            for rule in extension.verification_rules
            if rule.verifier_code is not None
        )
    return tuple(rows)


_DECLARED_BINDINGS = _declared_c8_named_bindings()
_IMPLEMENTATION_BY_CODE = dict(_IMPLEMENTATION_FUNCTIONS_BY_CODE)
if (
    len(_DECLARED_BINDINGS) != len(_IMPLEMENTATION_FUNCTIONS_BY_CODE)
    or len(_IMPLEMENTATION_BY_CODE) != len(_IMPLEMENTATION_FUNCTIONS_BY_CODE)
    or {row[3] for row in _DECLARED_BINDINGS} != set(_IMPLEMENTATION_BY_CODE)
):
    raise ValueError("C8 named verifier implementation count does not match declarations")


C8_NAMED_VERIFIER_IMPLEMENTATIONS = tuple(
    C8NamedVerifierImplementation(
        surface_kind=surface_kind,
        owner_table=owner_table,
        verifier_code=verifier_code,
        implementation_id=f"FAMILY_SCHEMA_NAMED_VERIFIER::{verifier_code}",
        implementation_requirement_id=f"NAMED_VERIFIER::{verifier_code}",
        implementation=_IMPLEMENTATION_BY_CODE[verifier_code],
    )
    for surface_kind, owner_table, _, verifier_code in _DECLARED_BINDINGS
)


def _validate_exact_c8_coverage() -> None:
    declared = {
        verifier_code: (surface_kind, owner_table, rule_id)
        for surface_kind, owner_table, rule_id, verifier_code in _DECLARED_BINDINGS
    }
    implementations = {item.verifier_code: item for item in C8_NAMED_VERIFIER_IMPLEMENTATIONS}
    component_descriptors = tuple(
        item for item in FAMILY_SCHEMA_VERIFIER_BINDINGS if item.binding_key.startswith(f"{_C8_COMPONENT_ID}:")
    )
    descriptors = {item.verifier_code: item for item in component_descriptors}
    if (
        len(declared) != len(_DECLARED_BINDINGS)
        or len(implementations) != len(C8_NAMED_VERIFIER_IMPLEMENTATIONS)
        or len(descriptors) != len(component_descriptors)
        or set(declared) != set(implementations)
        or set(declared) != set(descriptors)
    ):
        raise ValueError("C8 executable verifier coverage is not exact")
    for code, (surface_kind, owner_table, rule_id) in declared.items():
        implementation = implementations[code]
        descriptor = descriptors[code]
        expected_key = f"{_C8_COMPONENT_ID}:{surface_kind}:{owner_table}:{rule_id}"
        if (
            implementation.surface_kind != surface_kind
            or implementation.owner_table != owner_table
            or descriptor.binding_key != expected_key
            or implementation.implementation_id != descriptor.implementation_id
            or implementation.implementation_requirement_id != descriptor.implementation_requirement_id
        ):
            raise ValueError(f"C8 verifier binding mismatch: {code}")

    assessment_bindings = tuple(
        item for item in FAMILY_SCHEMA_VERIFIER_BINDINGS if item.verifier_code == _ASSESSMENT_CODE
    )
    expected_keys = {
        (f"{_C8_COMPONENT_ID}:TABLE:data_snooping_assessments:assessment_semantic_recomputation"),
        (f"{_C9_COMPONENT_ID}:TABLE:artifact_publication_receipts:publication_receipt_c8_semantic_recomputation"),
    }
    implementation = implementations[_ASSESSMENT_CODE]
    if (
        {item.binding_key for item in assessment_bindings} != expected_keys
        or {item.implementation_id for item in assessment_bindings} != {implementation.implementation_id}
        or {item.implementation_requirement_id for item in assessment_bindings}
        != {implementation.implementation_requirement_id}
    ):
        raise ValueError("C8/C9 shared assessment implementation binding is inconsistent")


_validate_exact_c8_coverage()


def verify_c8_named(
    verifier_code: object,
    owner_row: object,
    view: object,
    providers: object = None,
) -> bool:
    """Dispatch one exact C8 code, rejecting unknown or malformed input."""

    if type(verifier_code) is not str:
        return False
    for item in C8_NAMED_VERIFIER_IMPLEMENTATIONS:
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
    "C8NamedVerifierImplementation",
    "C8_NAMED_VERIFIER_IMPLEMENTATIONS",
    "verify_c8_named",
]
