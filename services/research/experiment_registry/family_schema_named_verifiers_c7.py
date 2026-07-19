"""Executable, read-only Pass-M implementations for every C7 named verifier.

The functions consume immutable SQLite-shaped row snapshots and perform no IO,
database access, clock reads, or schema activation.  The module machine-checks
that its callable inventory exactly covers both the C7 component declarations
and their canonical verifier-registry bindings.
"""

from __future__ import annotations

import inspect
import json
import math
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import FunctionType

from quantos.contracts import canonical as _canonical

from services.research.ports.persistence import EvaluationAuditIntent

from .family_schema_c7 import C7_SCHEMA_COMPONENT
from .family_schema_v2_semantics import evaluation_audit_hash
from .family_schema_verification_view import (
    FamilySchemaVerificationView,
    FrozenVerificationRow,
)
from .family_schema_verifier_registry import FAMILY_SCHEMA_VERIFIER_BINDINGS


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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
_COMPONENT_FIELDS = (
    ("ordered_period_keys", "ordered_period_keys_hash"),
    ("gross_returns", "gross_returns_hash"),
    ("costs", "costs_hash"),
    ("net_returns", "net_returns_hash"),
    ("turnovers", "turnovers_hash"),
)


class _VerificationInputError(ValueError):
    """A required row, scalar type, or semantic binding is invalid."""


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


def _ordinal(row: Mapping[str, object], field: str) -> int:
    value = row[field]
    if type(value) is not int or value not in (0, 1):
        raise _VerificationInputError(f"{field} must be the closed attempt ordinal")
    return value


def _nullable_sha256(row: Mapping[str, object], field: str) -> str | None:
    value = row[field]
    if value is None:
        return None
    return _sha256(row, field)


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


def _verify_release_exact_exposure_tuple(
    release: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    exposure_id = _text(release, "oos_exposure_id")
    family_id = _text(release, "family_id")
    exposures = _view_rows(view, "global_oos_exposures")
    for exposure in exposures:
        _text(exposure, "oos_exposure_id")
        _text(exposure, "family_id")
    exposure = _exactly_one(
        exposures,
        lambda row: _text(row, "oos_exposure_id") == exposure_id,
        "OOS exposure",
    )
    return _text(exposure, "family_id") == family_id


def _verify_release_after_freeze_start_consumption(
    release: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    authority_id = _text(release, "authority_instance_id")
    family_id = _text(release, "family_id")
    attempt_id = _text(release, "attempt_id")
    start_event_id = _text(release, "started_attempt_event_id")
    freeze_hash = _sha256(release, "family_freeze_record_hash")
    consumption_hash = _sha256(release, "exposure_consumption_record_hash")
    exposure_id = _text(release, "oos_exposure_id")
    released_at = _positive_integer(release, "released_at_us")

    freezes = _view_rows(view, "data_snooping_family_freezes")
    starts = _view_rows(view, "data_snooping_oos_access_attempts")
    consumptions = _view_rows(view, "global_oos_exposure_consumptions")
    freeze = _exactly_one(
        freezes,
        lambda row: _sha256(row, "record_hash") == freeze_hash,
        "family freeze",
    )
    start = _exactly_one(
        starts,
        lambda row: _text(row, "attempt_event_id") == start_event_id,
        "started OOS attempt",
    )
    consumption = _exactly_one(
        consumptions,
        lambda row: _sha256(row, "record_hash") == consumption_hash,
        "OOS exposure consumption",
    )

    if (
        _text(freeze, "authority_instance_id") != authority_id
        or _text(freeze, "family_id") != family_id
        or _text(start, "authority_instance_id") != authority_id
        or _text(start, "family_id") != family_id
        or _text(start, "attempt_id") != attempt_id
        or _text(start, "oos_exposure_id") != exposure_id
        or _ordinal(start, "sequence_no") != 0
        or _text(start, "status") != "STARTED"
        or _text(consumption, "authority_instance_id") != authority_id
        or _text(consumption, "family_id") != family_id
        or _text(consumption, "access_attempt_id") != attempt_id
        or _text(consumption, "oos_exposure_id") != exposure_id
        or _text(consumption, "selection_decision_id") != _text(start, "selection_decision_id")
    ):
        return False

    frozen_at = _positive_integer(freeze, "frozen_at_us")
    started_at = _positive_integer(start, "event_at_us")
    consumed_at = _positive_integer(consumption, "consumed_at_us")
    return frozen_at < started_at and frozen_at < consumed_at and started_at < released_at and consumed_at < released_at


def _verify_oos_attempt_performance_access_count(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    authority_id = _text(owner, "authority_instance_id")
    family_id = _text(owner, "family_id")
    owner_attempt_id = _text(owner, "attempt_id")
    owner_event_id = _text(owner, "attempt_event_id")
    owner_sequence = _ordinal(owner, "sequence_no")
    all_rows = _view_rows(view, "data_snooping_oos_access_attempts")
    family_rows: list[FrozenVerificationRow] = []
    owner_event_rows: list[tuple[str, str, str, int]] = []
    for row in all_rows:
        row_authority = _text(row, "authority_instance_id")
        row_family = _text(row, "family_id")
        row_event_id = _text(row, "attempt_event_id")
        row_attempt_id = _text(row, "attempt_id")
        row_sequence = _ordinal(row, "sequence_no")
        _text(row, "status")
        if row_authority == authority_id and row_family == family_id:
            family_rows.append(row)
        if row_event_id == owner_event_id:
            owner_event_rows.append((row_authority, row_family, row_attempt_id, row_sequence))
    if owner_event_rows != [(authority_id, family_id, owner_attempt_id, owner_sequence)] or not family_rows:
        return False

    starts = tuple(
        row for row in family_rows if _ordinal(row, "sequence_no") == 0 and _text(row, "status") == "STARTED"
    )
    distinct_started_attempts = {_text(row, "attempt_id") for row in starts}
    if len(starts) != 1 or len(distinct_started_attempts) != 1:
        return False
    logical_attempt_id = next(iter(distinct_started_attempts))
    if owner_attempt_id != logical_attempt_id:
        return False
    if any(_text(row, "attempt_id") != logical_attempt_id for row in family_rows):
        return False
    sequences = tuple(sorted(_ordinal(row, "sequence_no") for row in family_rows))
    if sequences not in ((0,), (0, 1)):
        return False
    terminals = tuple(row for row in family_rows if _ordinal(row, "sequence_no") == 1)
    return not terminals or _text(terminals[0], "status") in ("COMPLETED", "INCOMPLETE")


def _verify_candidate_duplicate_receipt_identity(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    family_id = _text(owner, "family_id")
    proposal_id = _sha256(owner, "proposal_id")
    disposition = _text(owner, "disposition")
    config_hash = _nullable_sha256(owner, "config_hash")
    receipt_hash = _nullable_sha256(owner, "evaluation_receipt_hash")
    rows = _view_rows(view, "data_snooping_candidate_dispositions")
    owner_matches = 0
    normalized: list[tuple[str, str, str, str | None, str | None]] = []
    for row in rows:
        values = (
            _text(row, "family_id"),
            _sha256(row, "proposal_id"),
            _text(row, "disposition"),
            _nullable_sha256(row, "config_hash"),
            _nullable_sha256(row, "evaluation_receipt_hash"),
        )
        normalized.append(values)
        if values == (family_id, proposal_id, disposition, config_hash, receipt_hash):
            owner_matches += 1
    proposal_identities = [(row_family, row_proposal) for row_family, row_proposal, *_ in normalized]
    if owner_matches != 1 or len(proposal_identities) != len(set(proposal_identities)):
        return False
    if (disposition == "PERFORMANCE_EVALUATED") != (receipt_hash is not None):
        return False
    if receipt_hash is not None and config_hash is None:
        return False
    if config_hash is None:
        return True

    evaluated_receipts = {
        row_receipt
        for row_family, _, row_disposition, row_config, row_receipt in normalized
        if row_family == family_id and row_config == config_hash and row_disposition == "PERFORMANCE_EVALUATED"
    }
    if None in evaluated_receipts or len(evaluated_receipts) > 1:
        return False
    if disposition == "PERFORMANCE_EVALUATED" and evaluated_receipts != {receipt_hash}:
        return False
    if receipt_hash is not None:
        receipt_identities = {
            (row_family, row_config)
            for row_family, _, _, row_config, row_receipt in normalized
            if row_receipt == receipt_hash
        }
        if receipt_identities != {(family_id, config_hash)}:
            return False
    return True


def _reject_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _VerificationInputError(f"duplicate legacy audit metadata key: {key}")
        result[key] = value
    return result


def _legacy_audit_metadata(value: object) -> dict[str, object]:
    if type(value) is not str:
        raise _VerificationInputError("legacy audit metadata must be exact JSON text")
    try:
        decoded = json.loads(value, object_pairs_hook=_reject_duplicate_json_pairs)
    except Exception as exc:
        raise _VerificationInputError("legacy audit metadata is invalid") from exc
    if type(decoded) is not dict:
        raise _VerificationInputError("legacy audit metadata must be an object")
    canonical_text = json.dumps(
        decoded,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if value != canonical_text:
        raise _VerificationInputError("legacy audit metadata is not exact canonical text")
    return decoded


def _verify_family_oos_evaluation_audit_intent(
    owner: FrozenVerificationRow,
    view: FamilySchemaVerificationView,
) -> bool:
    run_id = _text(owner, "evaluation_run_id")
    content_hash = _sha256(owner, "evaluation_content_hash")
    intent_hash = _sha256(owner, "evaluation_audit_intent_hash")
    evaluations = _view_rows(view, "evaluations")
    outbox = _view_rows(view, "evaluation_audit_outbox")
    for evaluation in evaluations:
        _text(evaluation, "run_id")
    for audit in outbox:
        _text(audit, "run_id")
    evaluation = _exactly_one(
        evaluations,
        lambda row: _text(row, "run_id") == run_id,
        "evaluation artifact",
    )
    audit = _exactly_one(
        outbox,
        lambda row: _text(row, "run_id") == run_id,
        "evaluation audit intent",
    )
    alpha_id = _text(evaluation, "alpha_id")
    recorded_ts = _nonnegative_integer(evaluation, "recorded_ts")
    verdict = _text(evaluation, "verdict")
    if _sha256(evaluation, "content_hash") != content_hash:
        return False
    expected_metadata = {
        "run_id": run_id,
        "content_hash": content_hash,
        "verdict": verdict,
    }
    metadata = _legacy_audit_metadata(audit["metadata_json"])
    previous_hash = _text(audit, "prev_record_hash", allow_empty=True)
    if previous_hash and _SHA256_RE.fullmatch(previous_hash) is None:
        return False
    if (
        _text(audit, "event_id") != f"exp-{run_id}"
        or _text(audit, "actor") != "experiment-registry"
        or _text(audit, "action") != "EXPERIMENT_RECORDED"
        or _nonnegative_integer(audit, "timestamp") != recorded_ts
        or _text(audit, "target") != alpha_id
        or metadata != expected_metadata
        or _sha256(audit, "evaluation_content_hash") != content_hash
    ):
        return False
    intent = EvaluationAuditIntent(
        event_id=_text(audit, "event_id"),
        actor=_text(audit, "actor"),
        action=_text(audit, "action"),
        timestamp=_nonnegative_integer(audit, "timestamp"),
        target=_text(audit, "target"),
        metadata=metadata,
    )
    expected_hash = evaluation_audit_hash(
        run_id=run_id,
        intent=intent,
        evaluation_content_hash=content_hash,
        prev_record_hash=previous_hash,
    )
    return _sha256(audit, "record_hash") == expected_hash == intent_hash


def _series_payload(owner: FrozenVerificationRow) -> dict[str, object]:
    payload_text = _text(owner, "series_payload_json")
    try:
        payload = _canonical.load_canonical_json_bytes(payload_text.encode("utf-8"))
    except Exception as exc:
        raise _VerificationInputError("series payload is not canonical JSON") from exc
    if type(payload) is not dict or set(payload) != _SERIES_KEYS:
        raise _VerificationInputError("series payload has an incompatible object shape")

    periods = payload["ordered_period_keys"]
    if type(periods) is not list or not periods:
        raise _VerificationInputError("ordered period keys must be a non-empty array")
    for period in periods:
        if type(period) is not dict or set(period) != _PERIOD_KEYS:
            raise _VerificationInputError("period key has an incompatible object shape")
        event_time = period["event_time"]
        if type(event_time) is not int or event_time <= 0:
            raise _VerificationInputError("period event_time must be a positive INTEGER")
        for field in ("frequency_id", "label_horizon"):
            value = period[field]
            if type(value) is not str or not value or unicodedata.normalize("NFC", value) != value:
                raise _VerificationInputError(f"period {field} must be exact NFC text")

    cardinalities = {len(periods)}
    for field in _VECTOR_FIELDS:
        vector = payload[field]
        if type(vector) is not list or not vector or any(type(item) is not str for item in vector):
            raise _VerificationInputError(f"{field} must be a non-empty text array")
        cardinalities.add(len(vector))
    if len(cardinalities) != 1:
        raise _VerificationInputError("series vectors must have equal cardinality")

    for field in (
        "ordered_period_keys_hash",
        "gross_returns_hash",
        "costs_hash",
        "net_returns_hash",
        "turnovers_hash",
        "cost_contract_hash",
        "cost_model_input_hash",
        "series_hash",
    ):
        value = payload[field]
        if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
            raise _VerificationInputError(f"payload {field} must be lowercase SHA-256")
    return payload


def _parse_vector(payload: Mapping[str, object], field: str) -> tuple[float, ...]:
    values = payload[field]
    if type(values) is not list:
        raise _VerificationInputError(f"{field} must be an array")
    parsed: list[float] = []
    for value in values:
        if type(value) is not str:
            raise _VerificationInputError(f"{field} values must be exact text")
        try:
            number = _canonical.parse_float64_hex(value)
        except Exception as exc:
            raise _VerificationInputError(f"{field} contains invalid float64 hex") from exc
        if not math.isfinite(number) or (number == 0.0 and math.copysign(1.0, number) < 0.0):
            raise _VerificationInputError(f"{field} contains non-finite or negative zero")
        parsed.append(number)
    return tuple(parsed)


def _verify_series_canonical_payload(
    owner: FrozenVerificationRow,
    _: FamilySchemaVerificationView,
) -> bool:
    _series_payload(owner)
    return True


def _verify_series_finite_float64_positive_zero(
    owner: FrozenVerificationRow,
    _: FamilySchemaVerificationView,
) -> bool:
    payload = _series_payload(owner)
    for field in _VECTOR_FIELDS:
        _parse_vector(payload, field)
    return True


def _verify_series_nonnegative_costs_turnovers(
    owner: FrozenVerificationRow,
    _: FamilySchemaVerificationView,
) -> bool:
    payload = _series_payload(owner)
    return all(value >= 0.0 for field in ("costs", "turnovers") for value in _parse_vector(payload, field))


def _verify_series_strict_period_order(
    owner: FrozenVerificationRow,
    _: FamilySchemaVerificationView,
) -> bool:
    periods = _series_payload(owner)["ordered_period_keys"]
    if type(periods) is not list:
        return False
    event_times = tuple(period["event_time"] for period in periods)
    return all(left < right for left, right in zip(event_times, event_times[1:]))


def _verify_series_common_frequency_horizon(
    owner: FrozenVerificationRow,
    _: FamilySchemaVerificationView,
) -> bool:
    periods = _series_payload(owner)["ordered_period_keys"]
    if type(periods) is not list:
        return False
    return (
        len({period["frequency_id"] for period in periods}) == 1
        and len({period["label_horizon"] for period in periods}) == 1
    )


def _verify_series_exact_cost_reconciliation(
    owner: FrozenVerificationRow,
    _: FamilySchemaVerificationView,
) -> bool:
    payload = _series_payload(owner)
    if _sha256(owner, "cost_contract_hash") != payload["cost_contract_hash"]:
        return False
    gross = _parse_vector(payload, "gross_returns")
    costs = _parse_vector(payload, "costs")
    net_hex = payload["net_returns"]
    if type(net_hex) is not list:
        return False
    _parse_vector(payload, "net_returns")
    return all(
        _canonical.float64_hex(gross_item - cost_item) == net_item
        for gross_item, cost_item, net_item in zip(gross, costs, net_hex, strict=True)
    )


def _verify_evaluation_receipt_component_hashes(
    owner: FrozenVerificationRow,
    _: FamilySchemaVerificationView,
) -> bool:
    payload = _series_payload(owner)
    for value_field, hash_field in _COMPONENT_FIELDS:
        expected = _canonical.canonical_sha256(payload[value_field])
        if payload[hash_field] != expected or _sha256(owner, hash_field) != expected:
            return False
    if (
        _sha256(owner, "cost_contract_hash") != payload["cost_contract_hash"]
        or _sha256(owner, "cost_model_input_hash") != payload["cost_model_input_hash"]
    ):
        return False
    without_series_hash = {key: value for key, value in payload.items() if key != "series_hash"}
    expected_series_hash = _canonical.canonical_sha256(without_series_hash)
    return payload["series_hash"] == expected_series_hash and _sha256(owner, "series_hash") == expected_series_hash


def _invoke(
    helper: Callable[[FrozenVerificationRow, FamilySchemaVerificationView], bool],
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    try:
        _ = providers
        if not isinstance(view, FamilySchemaVerificationView):
            raise _VerificationInputError("verification view is not read-only typed view")
        return helper(_owner_row(owner_row), view) is True
    except Exception:
        return False


def verify_oos_release_exact_exposure_tuple(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(_verify_release_exact_exposure_tuple, owner_row, view, providers)


def verify_oos_release_after_freeze_start_consumption(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_release_after_freeze_start_consumption,
        owner_row,
        view,
        providers,
    )


def verify_oos_attempt_performance_access_count(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_oos_attempt_performance_access_count,
        owner_row,
        view,
        providers,
    )


def verify_candidate_disposition_duplicate_receipt_identity(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_candidate_duplicate_receipt_identity,
        owner_row,
        view,
        providers,
    )


def verify_family_oos_evaluation_audit_intent(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_family_oos_evaluation_audit_intent,
        owner_row,
        view,
        providers,
    )


def verify_evaluation_receipt_canonical_series_payload(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(_verify_series_canonical_payload, owner_row, view, providers)


def verify_evaluation_receipt_finite_float64_positive_zero(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_series_finite_float64_positive_zero,
        owner_row,
        view,
        providers,
    )


def verify_evaluation_receipt_nonnegative_costs_turnovers(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_series_nonnegative_costs_turnovers,
        owner_row,
        view,
        providers,
    )


def verify_evaluation_receipt_strict_period_order(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(_verify_series_strict_period_order, owner_row, view, providers)


def verify_evaluation_receipt_common_frequency_horizon(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_series_common_frequency_horizon,
        owner_row,
        view,
        providers,
    )


def verify_evaluation_receipt_exact_cost_reconciliation(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_series_exact_cost_reconciliation,
        owner_row,
        view,
        providers,
    )


def verify_evaluation_receipt_component_hashes(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_evaluation_receipt_component_hashes,
        owner_row,
        view,
        providers,
    )


def verify_benchmark_return_canonical_series_payload(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(_verify_series_canonical_payload, owner_row, view, providers)


def verify_benchmark_return_finite_float64_positive_zero(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_series_finite_float64_positive_zero,
        owner_row,
        view,
        providers,
    )


def verify_benchmark_return_nonnegative_costs_turnovers(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_series_nonnegative_costs_turnovers,
        owner_row,
        view,
        providers,
    )


def verify_benchmark_return_strict_period_order(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(_verify_series_strict_period_order, owner_row, view, providers)


def verify_benchmark_return_common_frequency_horizon(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_series_common_frequency_horizon,
        owner_row,
        view,
        providers,
    )


def verify_benchmark_return_exact_cost_reconciliation(
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    return _invoke(
        _verify_series_exact_cost_reconciliation,
        owner_row,
        view,
        providers,
    )


@dataclass(frozen=True, slots=True)
class C7NamedVerifierImplementation:
    """One exact C7 rule binding and its fail-closed Python implementation."""

    surface_kind: str
    owner_table: str
    verifier_code: str
    implementation_id: str
    implementation_requirement_id: str
    implementation: object

    def __post_init__(self) -> None:
        if self.surface_kind not in ("TABLE", "EXTENSION"):
            raise ValueError("C7 verifier surface kind is not closed")
        if type(self.owner_table) is not str or not self.owner_table:
            raise TypeError("C7 verifier owner table must be exact text")
        if type(self.verifier_code) is not str or not self.verifier_code.startswith("c7.verify_"):
            raise TypeError("C7 verifier code must be exact closed text")
        expected_id = f"FAMILY_SCHEMA_NAMED_VERIFIER::{self.verifier_code}"
        expected_requirement = f"NAMED_VERIFIER::{self.verifier_code}"
        if self.implementation_id != expected_id:
            raise ValueError("C7 verifier implementation ID does not match its code")
        if self.implementation_requirement_id != expected_requirement:
            raise ValueError("C7 verifier requirement ID does not match its code")
        if type(self.implementation) is not FunctionType:
            raise TypeError("C7 named verifier implementation must be a Python function")
        parameters = tuple(inspect.signature(self.implementation).parameters.values())
        if len(parameters) != 3 or any(
            parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
            or parameter.default is not inspect.Parameter.empty
            for parameter in parameters
        ):
            raise TypeError(
                "C7 named verifier must accept exactly owner_row, view, and providers"
            )


_IMPLEMENTATION_FUNCTIONS_BY_CODE = (
    (
        "c7.verify_oos_release_exact_exposure_tuple_v1",
        verify_oos_release_exact_exposure_tuple,
    ),
    (
        "c7.verify_oos_release_after_freeze_start_consumption_v1",
        verify_oos_release_after_freeze_start_consumption,
    ),
    (
        "c7.verify_oos_attempt_performance_access_count_v1",
        verify_oos_attempt_performance_access_count,
    ),
    (
        "c7.verify_candidate_disposition_duplicate_receipt_identity_v1",
        verify_candidate_disposition_duplicate_receipt_identity,
    ),
    (
        "c7.verify_family_oos_evaluation_audit_intent_v1",
        verify_family_oos_evaluation_audit_intent,
    ),
    (
        "c7.verify_evaluation_receipt_canonical_series_payload_v1",
        verify_evaluation_receipt_canonical_series_payload,
    ),
    (
        "c7.verify_evaluation_receipt_finite_float64_positive_zero_v1",
        verify_evaluation_receipt_finite_float64_positive_zero,
    ),
    (
        "c7.verify_evaluation_receipt_nonnegative_costs_turnovers_v1",
        verify_evaluation_receipt_nonnegative_costs_turnovers,
    ),
    (
        "c7.verify_evaluation_receipt_strict_period_order_v1",
        verify_evaluation_receipt_strict_period_order,
    ),
    (
        "c7.verify_evaluation_receipt_common_frequency_horizon_v1",
        verify_evaluation_receipt_common_frequency_horizon,
    ),
    (
        "c7.verify_evaluation_receipt_exact_cost_reconciliation_v1",
        verify_evaluation_receipt_exact_cost_reconciliation,
    ),
    (
        "c7.verify_evaluation_receipt_component_hashes_v1",
        verify_evaluation_receipt_component_hashes,
    ),
    (
        "c7.verify_benchmark_return_canonical_series_payload_v1",
        verify_benchmark_return_canonical_series_payload,
    ),
    (
        "c7.verify_benchmark_return_finite_float64_positive_zero_v1",
        verify_benchmark_return_finite_float64_positive_zero,
    ),
    (
        "c7.verify_benchmark_return_nonnegative_costs_turnovers_v1",
        verify_benchmark_return_nonnegative_costs_turnovers,
    ),
    (
        "c7.verify_benchmark_return_strict_period_order_v1",
        verify_benchmark_return_strict_period_order,
    ),
    (
        "c7.verify_benchmark_return_common_frequency_horizon_v1",
        verify_benchmark_return_common_frequency_horizon,
    ),
    (
        "c7.verify_benchmark_return_exact_cost_reconciliation_v1",
        verify_benchmark_return_exact_cost_reconciliation,
    ),
)


def _declared_c7_named_bindings() -> tuple[tuple[str, str, str, str], ...]:
    rows: list[tuple[str, str, str, str]] = []
    for table in C7_SCHEMA_COMPONENT.tables:
        rows.extend(
            ("TABLE", table.table_name, rule.rule_id, rule.verifier_code)
            for rule in table.verification_rules
            if rule.verifier_code is not None
        )
    for extension in C7_SCHEMA_COMPONENT.extensions:
        rows.extend(
            ("EXTENSION", extension.target_table, rule.rule_id, rule.verifier_code)
            for rule in extension.verification_rules
            if rule.verifier_code is not None
        )
    return tuple(rows)


_DECLARED_BINDINGS = _declared_c7_named_bindings()
_IMPLEMENTATION_BY_CODE = dict(_IMPLEMENTATION_FUNCTIONS_BY_CODE)
if (
    len(_DECLARED_BINDINGS) != len(_IMPLEMENTATION_FUNCTIONS_BY_CODE)
    or len(_IMPLEMENTATION_BY_CODE) != len(_IMPLEMENTATION_FUNCTIONS_BY_CODE)
    or {row[3] for row in _DECLARED_BINDINGS} != set(_IMPLEMENTATION_BY_CODE)
):
    raise ValueError("C7 named verifier implementation count does not match declarations")

C7_NAMED_VERIFIER_IMPLEMENTATIONS = tuple(
    C7NamedVerifierImplementation(
        surface_kind=surface_kind,
        owner_table=owner_table,
        verifier_code=verifier_code,
        implementation_id=f"FAMILY_SCHEMA_NAMED_VERIFIER::{verifier_code}",
        implementation_requirement_id=f"NAMED_VERIFIER::{verifier_code}",
        implementation=_IMPLEMENTATION_BY_CODE[verifier_code],
    )
    for surface_kind, owner_table, _, verifier_code in _DECLARED_BINDINGS
)


def _validate_exact_c7_coverage() -> None:
    component_id = C7_SCHEMA_COMPONENT.component_id
    declared = {
        verifier_code: (surface_kind, owner_table, rule_id)
        for surface_kind, owner_table, rule_id, verifier_code in _DECLARED_BINDINGS
    }
    implementations = {item.verifier_code: item for item in C7_NAMED_VERIFIER_IMPLEMENTATIONS}
    descriptors = {
        item.verifier_code: item
        for item in FAMILY_SCHEMA_VERIFIER_BINDINGS
        if item.binding_key.startswith(f"{component_id}:")
    }
    if (
        len(declared) != len(_DECLARED_BINDINGS)
        or len(implementations) != len(C7_NAMED_VERIFIER_IMPLEMENTATIONS)
        or len(descriptors)
        != len(
            tuple(item for item in FAMILY_SCHEMA_VERIFIER_BINDINGS if item.binding_key.startswith(f"{component_id}:"))
        )
        or set(declared) != set(implementations)
        or set(declared) != set(descriptors)
    ):
        raise ValueError("C7 executable verifier coverage is not exact")
    for code, (surface_kind, owner_table, rule_id) in declared.items():
        implementation = implementations[code]
        descriptor = descriptors[code]
        expected_binding_key = f"{component_id}:{surface_kind}:{owner_table}:{rule_id}"
        if (
            implementation.surface_kind != surface_kind
            or implementation.owner_table != owner_table
            or descriptor.binding_key != expected_binding_key
            or implementation.implementation_id != descriptor.implementation_id
            or implementation.implementation_requirement_id != descriptor.implementation_requirement_id
        ):
            raise ValueError(f"C7 verifier binding mismatch: {code}")


_validate_exact_c7_coverage()


def verify_c7_named(
    verifier_code: object,
    owner_row: object,
    view: object,
    providers: object = None,
) -> bool:
    """Dispatch one exact C7 code, rejecting unknown or malformed input."""

    if type(verifier_code) is not str:
        return False
    for item in C7_NAMED_VERIFIER_IMPLEMENTATIONS:
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
    "C7NamedVerifierImplementation",
    "C7_NAMED_VERIFIER_IMPLEMENTATIONS",
    "verify_c7_named",
]
