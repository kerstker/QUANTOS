"""Focused RED tests for executable Pass-M C7 named verifiers.

The final family-schema verifier will own catalog and exact-column coverage.
These tests freeze only the reusable read-only row view and the eighteen C7
semantic closures; no test opens or mutates a database.
"""

from __future__ import annotations

import json
import inspect
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from types import FunctionType

import pytest

from quantos.contracts import canonical
from quantos.contracts.research import ValidationVerdict
from services.research.experiment_registry.family_schema_c7 import (
    C7_SCHEMA_COMPONENT,
)
from services.research.experiment_registry.family_schema_named_verifiers_c7 import (
    C7_NAMED_VERIFIER_IMPLEMENTATIONS,
    C7NamedVerifierImplementation,
    verify_c7_named,
)
from services.research.experiment_registry.family_schema_v2_semantics import (
    evaluation_audit_hash,
)
from services.research.experiment_registry.family_schema_verification_view import (
    FrozenFamilySchemaVerificationView,
    FrozenVerificationRow,
)
from services.research.experiment_registry.family_schema_verifier_registry import (
    FAMILY_SCHEMA_VERIFIER_BINDINGS,
)
from services.research.ports.persistence import EvaluationAuditIntent


_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64
_E = "e" * 64
_F = "f" * 64
_ZERO = "0" * 64
_ONE = "1" * 64
_TWO = "2" * 64


def _component_named_rules() -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []
    for table in C7_SCHEMA_COMPONENT.tables:
        rows.extend(
            ("TABLE", table.table_name, rule.verifier_code)
            for rule in table.verification_rules
            if rule.verifier_code is not None
        )
    for extension in C7_SCHEMA_COMPONENT.extensions:
        rows.extend(
            ("EXTENSION", extension.target_table, rule.verifier_code)
            for rule in extension.verification_rules
            if rule.verifier_code is not None
        )
    return tuple(rows)


def _empty_view() -> FrozenFamilySchemaVerificationView:
    return FrozenFamilySchemaVerificationView.from_rows(())


def _view(
    *tables: tuple[str, tuple[Mapping[str, object], ...]],
) -> FrozenFamilySchemaVerificationView:
    return FrozenFamilySchemaVerificationView.from_rows(tables)


def _canonical_series_payload() -> dict[str, object]:
    periods = [
        {"event_time": 1_000_000, "frequency_id": "W1", "label_horizon": "P7D"},
        {"event_time": 2_000_000, "frequency_id": "W1", "label_horizon": "P7D"},
    ]
    gross_returns = [canonical.float64_hex(0.25), canonical.float64_hex(-0.125)]
    costs = [canonical.float64_hex(0.0625), canonical.float64_hex(0.0)]
    net_returns = [canonical.float64_hex(0.1875), canonical.float64_hex(-0.125)]
    turnovers = [canonical.float64_hex(0.5), canonical.float64_hex(0.0)]
    payload: dict[str, object] = {
        "ordered_period_keys": periods,
        "gross_returns": gross_returns,
        "costs": costs,
        "net_returns": net_returns,
        "turnovers": turnovers,
        "ordered_period_keys_hash": canonical.canonical_sha256(periods),
        "gross_returns_hash": canonical.canonical_sha256(gross_returns),
        "costs_hash": canonical.canonical_sha256(costs),
        "net_returns_hash": canonical.canonical_sha256(net_returns),
        "turnovers_hash": canonical.canonical_sha256(turnovers),
        "cost_contract_hash": _A,
        "cost_model_input_hash": _B,
    }
    payload["series_hash"] = canonical.canonical_sha256(payload)
    return payload


def _series_row(payload: Mapping[str, object] | None = None) -> dict[str, object]:
    value = dict(_canonical_series_payload() if payload is None else payload)
    return {
        "series_payload_json": canonical.canonical_json_bytes(value).decode("utf-8"),
        "ordered_period_keys_hash": value["ordered_period_keys_hash"],
        "gross_returns_hash": value["gross_returns_hash"],
        "costs_hash": value["costs_hash"],
        "net_returns_hash": value["net_returns_hash"],
        "turnovers_hash": value["turnovers_hash"],
        "cost_contract_hash": value["cost_contract_hash"],
        "cost_model_input_hash": value["cost_model_input_hash"],
        "series_hash": value["series_hash"],
    }


def _mutated_payload(
    mutate,
) -> str:
    payload = _canonical_series_payload()
    mutate(payload)
    return canonical.canonical_json_bytes(payload).decode("utf-8")


def _release_fixture() -> tuple[dict[str, object], FrozenFamilySchemaVerificationView]:
    release = {
        "authority_instance_id": "authority-1",
        "family_id": "family-1",
        "attempt_id": "attempt-1",
        "started_attempt_event_id": "attempt-event-start",
        "family_freeze_record_hash": _A,
        "exposure_consumption_record_hash": _B,
        "oos_exposure_id": "exposure-1",
        "released_at_us": 400,
    }
    start = {
        "authority_instance_id": "authority-1",
        "attempt_event_id": "attempt-event-start",
        "attempt_id": "attempt-1",
        "family_id": "family-1",
        "selection_decision_id": "decision-1",
        "oos_exposure_id": "exposure-1",
        "sequence_no": 0,
        "status": "STARTED",
        "event_at_us": 200,
        "record_hash": _C,
    }
    terminal = {
        **start,
        "attempt_event_id": "attempt-event-terminal",
        "sequence_no": 1,
        "status": "COMPLETED",
        "event_at_us": 500,
        "record_hash": _D,
    }
    view = _view(
        (
            "global_oos_exposures",
            (
                {
                    "authority_instance_id": "authority-1",
                    "oos_exposure_id": "exposure-1",
                    "selection_decision_id": "decision-1",
                    "family_id": "family-1",
                },
            ),
        ),
        (
            "data_snooping_family_freezes",
            (
                {
                    "authority_instance_id": "authority-1",
                    "family_id": "family-1",
                    "record_hash": _A,
                    "frozen_at_us": 100,
                },
            ),
        ),
        ("data_snooping_oos_access_attempts", (start, terminal)),
        (
            "global_oos_exposure_consumptions",
            (
                {
                    "authority_instance_id": "authority-1",
                    "oos_exposure_id": "exposure-1",
                    "selection_decision_id": "decision-1",
                    "family_id": "family-1",
                    "access_attempt_id": "attempt-1",
                    "consumed_at_us": 250,
                    "record_hash": _B,
                },
            ),
        ),
    )
    return release, view


def _disposition_fixture() -> tuple[dict[str, object], FrozenFamilySchemaVerificationView]:
    first = {
        "family_id": "family-1",
        "proposal_id": _A,
        "disposition": "PERFORMANCE_EVALUATED",
        "config_hash": _C,
        "evaluation_receipt_hash": _E,
        "performance_accessed": 1,
    }
    duplicate = {**first, "proposal_id": _B}
    unrelated = {
        **first,
        "proposal_id": _D,
        "config_hash": _F,
        "evaluation_receipt_hash": _ONE,
    }
    return first, _view(
        ("data_snooping_candidate_dispositions", (first, duplicate, unrelated)),
    )


def _audit_fixture() -> tuple[dict[str, object], FrozenFamilySchemaVerificationView]:
    evaluation = {
        "run_id": "run-1",
        "alpha_id": "alpha-1",
        "recorded_ts": 0,
        "verdict": ValidationVerdict.PASS.value,
        "content_hash": _C,
    }
    metadata = {
        "run_id": "run-1",
        "content_hash": _C,
        "verdict": ValidationVerdict.PASS.value,
    }
    intent = EvaluationAuditIntent(
        event_id="exp-run-1",
        actor="experiment-registry",
        action="EXPERIMENT_RECORDED",
        timestamp=0,
        target="alpha-1",
        metadata=metadata,
    )
    record_hash = evaluation_audit_hash(
        run_id="run-1",
        intent=intent,
        evaluation_content_hash=_C,
        prev_record_hash="",
    )
    audit = {
        "run_id": "run-1",
        "event_id": "exp-run-1",
        "actor": "experiment-registry",
        "action": "EXPERIMENT_RECORDED",
        "timestamp": 0,
        "target": "alpha-1",
        "metadata_json": json.dumps(
            metadata,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "evaluation_content_hash": _C,
        "prev_record_hash": "",
        "record_hash": record_hash,
    }
    owner = {
        "evaluation_run_id": "run-1",
        "evaluation_content_hash": _C,
        "evaluation_audit_intent_hash": record_hash,
    }
    return owner, _view(("evaluations", (evaluation,)), ("evaluation_audit_outbox", (audit,)))


def test_c7_executable_registry_exactly_covers_component_and_descriptor_registry() -> None:
    component_rows = _component_named_rules()
    component_id = C7_SCHEMA_COMPONENT.component_id
    descriptor_rows = tuple(
        binding for binding in FAMILY_SCHEMA_VERIFIER_BINDINGS if binding.binding_key.startswith(f"{component_id}:")
    )
    implementations = C7_NAMED_VERIFIER_IMPLEMENTATIONS

    assert len(component_rows) == len(descriptor_rows) == len(implementations) == 18
    assert all(type(item) is C7NamedVerifierImplementation for item in implementations)
    assert len({item.verifier_code for item in implementations}) == 18
    assert all(
        tuple(inspect.signature(item.implementation).parameters)
        == ("owner_row", "view", "providers")
        for item in implementations
    )
    assert (
        {code for _, _, code in component_rows}
        == {item.verifier_code for item in implementations}
        == {item.verifier_code for item in descriptor_rows}
    )
    assert {(surface, table, code) for surface, table, code in component_rows} == {
        (item.surface_kind, item.owner_table, item.verifier_code) for item in implementations
    }


def test_each_c7_callable_binds_one_material_helper_into_implementation_closure() -> None:
    for item in C7_NAMED_VERIFIER_IMPLEMENTATIONS:
        implementation = item.implementation
        assert type(implementation) is FunctionType
        names = set(implementation.__code__.co_names)
        helper_names = tuple(
            name
            for name in names
            if name.startswith("_verify_") and type(implementation.__globals__.get(name)) is FunctionType
        )
        assert "_invoke" in names
        assert len(helper_names) == 1


def test_frozen_verification_view_copies_rows_and_exposes_read_only_mappings() -> None:
    source = {"text": "value", "count": 1, "blob": b"abc", "optional": None}
    view = FrozenFamilySchemaVerificationView.from_rows((("table_a", (source,)),))
    source["text"] = "tampered"

    rows = view.rows("table_a")
    assert type(rows) is tuple
    assert type(rows[0]) is FrozenVerificationRow
    assert rows[0]["text"] == "value"
    assert rows[0]["blob"] == b"abc"
    with pytest.raises(TypeError):
        rows[0]["text"] = "forbidden"  # type: ignore[index]
    with pytest.raises(KeyError):
        view.rows("missing")


@pytest.mark.parametrize(
    ("tables", "error"),
    [
        ((("dup", ()), ("dup", ())), "duplicate table"),
        ((("bad", ({"flag": True},)),), "SQLite scalar"),
        ((("bad", ({"value": float("inf")},)),), "finite"),
        (((1, ()),), "table name"),
        ((("bad", ({1: "value"},)),), "column name"),
    ],
)
def test_frozen_verification_view_rejects_ambiguous_or_non_sqlite_input(
    tables: object,
    error: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=error):
        FrozenFamilySchemaVerificationView.from_rows(tables)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "code",
    [
        "c7.verify_oos_release_exact_exposure_tuple_v1",
        "c7.verify_oos_release_after_freeze_start_consumption_v1",
    ],
)
def test_oos_release_verifiers_accept_exact_frozen_start_consumption_path(code: str) -> None:
    release, view = _release_fixture()
    assert verify_c7_named(code, release, view)


def test_oos_release_exact_exposure_tuple_rejects_one_field_identity_tamper() -> None:
    release, _ = _release_fixture()
    bad_view = _view(
        (
            "global_oos_exposures",
            (
                {
                    "authority_instance_id": "authority-1",
                    "oos_exposure_id": "exposure-1",
                    "selection_decision_id": "decision-1",
                    "family_id": "family-tampered",
                },
            ),
        ),
    )
    assert not verify_c7_named(
        "c7.verify_oos_release_exact_exposure_tuple_v1",
        release,
        bad_view,
    )


def test_oos_release_exact_exposure_tuple_rejects_duplicate_related_rows() -> None:
    release, view = _release_fixture()
    exposure = dict(view.rows("global_oos_exposures")[0])
    duplicate_view = _view(("global_oos_exposures", (exposure, exposure)))
    assert not verify_c7_named(
        "c7.verify_oos_release_exact_exposure_tuple_v1",
        release,
        duplicate_view,
    )


def test_oos_release_chronology_rejects_release_at_consumption_time() -> None:
    release, view = _release_fixture()
    release["released_at_us"] = 250
    assert not verify_c7_named(
        "c7.verify_oos_release_after_freeze_start_consumption_v1",
        release,
        view,
    )


def test_oos_attempt_count_is_distinct_committed_started_attempt_not_event_count() -> None:
    release, view = _release_fixture()
    attempts = view.rows("data_snooping_oos_access_attempts")
    assert len(attempts) == 2
    assert verify_c7_named(
        "c7.verify_oos_attempt_performance_access_count_v1",
        attempts[1],
        view,
    )


def test_oos_attempt_count_rejects_second_logical_start() -> None:
    _, view = _release_fixture()
    start, terminal = view.rows("data_snooping_oos_access_attempts")
    second_start = dict(start)
    second_start["attempt_event_id"] = "second-start"
    second_start["attempt_id"] = "attempt-2"
    second_start["record_hash"] = _E
    bad_view = _view(
        (
            "data_snooping_oos_access_attempts",
            (dict(start), dict(terminal), second_start),
        ),
    )
    assert not verify_c7_named(
        "c7.verify_oos_attempt_performance_access_count_v1",
        start,
        bad_view,
    )


def test_duplicate_dispositions_share_one_byte_identical_receipt() -> None:
    disposition, view = _disposition_fixture()
    assert verify_c7_named(
        "c7.verify_candidate_disposition_duplicate_receipt_identity_v1",
        disposition,
        view,
    )


def test_duplicate_disposition_receipt_identity_rejects_one_hash_tamper() -> None:
    disposition, view = _disposition_fixture()
    first, duplicate, unrelated = view.rows("data_snooping_candidate_dispositions")
    bad_duplicate = dict(duplicate)
    bad_duplicate["evaluation_receipt_hash"] = _TWO
    bad_view = _view(
        (
            "data_snooping_candidate_dispositions",
            (dict(first), bad_duplicate, dict(unrelated)),
        ),
    )
    assert not verify_c7_named(
        "c7.verify_candidate_disposition_duplicate_receipt_identity_v1",
        disposition,
        bad_view,
    )


def test_family_evaluation_audit_intent_verifies_legacy_intent_and_chain_hash() -> None:
    owner, view = _audit_fixture()
    assert verify_c7_named(
        "c7.verify_family_oos_evaluation_audit_intent_v1",
        owner,
        view,
    )


def test_family_evaluation_audit_intent_rejects_one_actor_tamper() -> None:
    owner, view = _audit_fixture()
    audit = dict(view.rows("evaluation_audit_outbox")[0])
    audit["actor"] = "tampered-actor"
    bad_view = _view(
        ("evaluations", (dict(view.rows("evaluations")[0]),)),
        ("evaluation_audit_outbox", (audit,)),
    )
    assert not verify_c7_named(
        "c7.verify_family_oos_evaluation_audit_intent_v1",
        owner,
        bad_view,
    )


def _set_vector_item(payload: dict[str, object], field: str, index: int, value: str) -> None:
    vector = list(payload[field])  # type: ignore[arg-type]
    vector[index] = value
    payload[field] = vector


def _set_period_field(payload: dict[str, object], index: int, field: str, value: object) -> None:
    periods = [dict(item) for item in payload["ordered_period_keys"]]  # type: ignore[union-attr]
    periods[index][field] = value
    payload["ordered_period_keys"] = periods


def _series_cases() -> tuple[tuple[str, dict[str, object]], ...]:
    canonical_bad = _series_row()
    canonical_bad["series_payload_json"] = str(canonical_bad["series_payload_json"]).rstrip("\n")

    finite_bad = _series_row()
    finite_bad["series_payload_json"] = _mutated_payload(
        lambda payload: _set_vector_item(payload, "gross_returns", 0, "-0x0.0p+0")
    )

    nonnegative_bad = _series_row()
    nonnegative_bad["series_payload_json"] = _mutated_payload(
        lambda payload: _set_vector_item(
            payload,
            "costs",
            0,
            canonical.float64_hex(-0.0625),
        )
    )

    order_bad = _series_row()
    order_bad["series_payload_json"] = _mutated_payload(
        lambda payload: _set_period_field(payload, 1, "event_time", 1_000_000)
    )

    frequency_bad = _series_row()
    frequency_bad["series_payload_json"] = _mutated_payload(
        lambda payload: _set_period_field(payload, 1, "frequency_id", "D1")
    )

    reconciliation_bad = _series_row()
    reconciliation_bad["series_payload_json"] = _mutated_payload(
        lambda payload: _set_vector_item(
            payload,
            "net_returns",
            0,
            canonical.float64_hex(0.125),
        )
    )

    component_bad = _series_row()
    component_bad["gross_returns_hash"] = _ZERO

    return (
        ("canonical_series_payload", canonical_bad),
        ("finite_float64_positive_zero", finite_bad),
        ("nonnegative_costs_turnovers", nonnegative_bad),
        ("strict_period_order", order_bad),
        ("common_frequency_horizon", frequency_bad),
        ("exact_cost_reconciliation", reconciliation_bad),
        ("component_hashes", component_bad),
    )


@pytest.mark.parametrize(("suffix", "bad_row"), _series_cases())
def test_evaluation_series_verifier_accepts_exact_payload_and_rejects_one_field_tamper(
    suffix: str,
    bad_row: dict[str, object],
) -> None:
    code = f"c7.verify_evaluation_receipt_{suffix}_v1"
    assert verify_c7_named(code, _series_row(), _empty_view())
    assert not verify_c7_named(code, bad_row, _empty_view())


@pytest.mark.parametrize(
    ("suffix", "bad_row"),
    tuple(item for item in _series_cases() if item[0] != "component_hashes"),
)
def test_benchmark_series_verifier_accepts_exact_payload_and_rejects_one_field_tamper(
    suffix: str,
    bad_row: dict[str, object],
) -> None:
    code = f"c7.verify_benchmark_return_{suffix}_v1"
    assert verify_c7_named(code, _series_row(), _empty_view())
    assert not verify_c7_named(code, bad_row, _empty_view())


def test_evaluation_component_hashes_bind_cost_contract_and_input_hash_rows() -> None:
    row = _series_row()
    row["cost_model_input_hash"] = _D
    assert not verify_c7_named(
        "c7.verify_evaluation_receipt_component_hashes_v1",
        row,
        _empty_view(),
    )


@pytest.mark.parametrize("field", ["gross_returns_hash", "series_hash"])
def test_evaluation_component_hashes_reject_internal_payload_hash_tamper(
    field: str,
) -> None:
    payload = _canonical_series_payload()
    payload[field] = _ZERO
    assert not verify_c7_named(
        "c7.verify_evaluation_receipt_component_hashes_v1",
        _series_row(payload),
        _empty_view(),
    )


def test_c7_dispatcher_fails_closed_on_unknown_code_missing_rows_and_type_confusion() -> None:
    release, view = _release_fixture()
    assert not verify_c7_named("c7.verify_unknown_v1", release, _empty_view())
    assert not verify_c7_named(
        "c7.verify_oos_release_exact_exposure_tuple_v1",
        release,
        _empty_view(),
    )
    assert not verify_c7_named(
        "c7.verify_oos_release_after_freeze_start_consumption_v1",
        {**release, "released_at_us": True},
        view,
    )
    assert not verify_c7_named(
        "c7.verify_oos_release_exact_exposure_tuple_v1",
        release,
        object(),
    )


def test_c7_implementation_descriptors_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(
            C7_NAMED_VERIFIER_IMPLEMENTATIONS[0],
            "verifier_code",
            "tampered",
        )
