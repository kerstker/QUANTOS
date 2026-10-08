"""RED contracts for exact-v3 Pass C slice C7.

These tests freeze the public one-shot OOS evidence values only.  They do not
activate schema v3, render DDL, access OOS bytes, or implement an evaluation
service.  C7 is intentionally absent while this file is introduced, so the
first run must fail because the wished-for contracts are missing.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

import pytest
from pydantic import ValidationError

import quantos.contracts.research as research_contracts
from quantos.contracts.canonical import canonical_sha256, float64_hex


C7_CONTRACT_FIELDS = {
    "EvaluationSeriesReceipt": {
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
    },
    "CandidateReturnSeries": {
        "family_id",
        "evaluation_batch_id",
        "config_hash",
        "alpha_id",
        "evaluation_run_id",
        "evaluation_content_hash",
        "evaluation_audit_intent_hash",
        "source_search_id",
        "source_proposal_id",
        "series",
        "evaluation_receipt_hash",
    },
    "BenchmarkReturnSeries": {
        "family_id",
        "evaluation_batch_id",
        "benchmark_id",
        "benchmark_contract_hash",
        "series",
        "benchmark_receipt_hash",
    },
    "FamilyEvaluationBatchReceipt": {
        "record_type",
        "authority_instance_id",
        "genesis_record_hash",
        "family_id",
        "attempt_id",
        "evaluation_batch_id",
        "frozen_config_set_hash",
        "candidate_count",
        "candidate_order",
        "candidate_receipt_hashes",
        "benchmark_receipt_hash",
        "ordered_period_keys_hash",
        "cost_contract_hash",
        "start_execution_attestation_hash",
        "terminal_execution_attestation_hash",
        "immutable_artifact_lease_hash",
        "committed_at_us",
        "batch_receipt_hash",
    },
    "OosAccessAttempt": {
        "record_type",
        "authority_instance_id",
        "genesis_record_hash",
        "attempt_event_id",
        "attempt_id",
        "family_id",
        "selection_decision_id",
        "oos_exposure_id",
        "evaluation_batch_id",
        "sequence_no",
        "status",
        "frozen_config_set_hash",
        "benchmark_contract_hash",
        "ordered_period_calendar_hash",
        "cost_contract_hash",
        "start_execution_attestation_hash",
        "terminal_execution_attestation_hash",
        "immutable_artifact_lease_hash",
        "evaluation_batch_receipt_hash",
        "reason_code",
        "event_at_us",
        "previous_event_hash",
        "event_hash",
    },
    "OosReleaseReceipt": {
        "record_type",
        "authority_instance_id",
        "genesis_record_hash",
        "oos_release_id",
        "family_id",
        "attempt_id",
        "started_attempt_event_id",
        "family_freeze_record_hash",
        "exposure_consumption_record_hash",
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
        "released_at_us",
        "release_record_hash",
        "authority_head",
    },
}


def _contract(name: str) -> type[Any]:
    contract = getattr(research_contracts, name, None)
    assert contract is not None, f"Pass C C7 contract is missing: {name}"
    return contract


@pytest.mark.parametrize(("name", "fields"), C7_CONTRACT_FIELDS.items())
def test_c7_contracts_are_present_frozen_strict_and_exact(
    name: str,
    fields: set[str],
) -> None:
    contract = _contract(name)
    assert set(contract.model_fields) == fields
    assert contract.model_config["frozen"] is True
    assert contract.model_config["extra"] == "forbid"
    assert contract.model_config["strict"] is True


def _period(event_time: int) -> dict[str, Any]:
    return {
        "event_time": event_time,
        "frequency_id": "P1D",
        "label_horizon": "1D",
    }


def test_period_key_is_strict_frozen_nfc_and_forbids_extra_fields() -> None:
    period_type = _contract("PeriodKey")
    assert period_type.model_config["frozen"] is True
    assert period_type.model_config["strict"] is True
    assert period_type.model_config["extra"] == "forbid"
    assert period_type(**_period(1)).event_time == 1

    for invalid in (
        {**_period(1), "event_time": True},
        {**_period(1), "event_time": "1"},
        {**_period(1), "frequency_id": "e\u0301"},
        {**_period(1), "label_horizon": "e\u0301"},
        {**_period(1), "unexpected": "field"},
    ):
        with pytest.raises(ValidationError):
            period_type(**invalid)


def _series_payload(**overrides: Any) -> dict[str, Any]:
    periods = (_period(1_700_000_000_000_000), _period(1_700_086_400_000_000))
    gross = (float64_hex(0.02), float64_hex(-0.01))
    costs = (float64_hex(0.005), float64_hex(0.002))
    net = (
        float64_hex(0.02 - 0.005),
        float64_hex(-0.01 - 0.002),
    )
    turnovers = (float64_hex(0.25), float64_hex(0.5))
    payload: dict[str, Any] = {
        "ordered_period_keys": periods,
        "gross_returns": gross,
        "costs": costs,
        "net_returns": net,
        "turnovers": turnovers,
        "ordered_period_keys_hash": canonical_sha256(periods),
        "gross_returns_hash": canonical_sha256(gross),
        "costs_hash": canonical_sha256(costs),
        "net_returns_hash": canonical_sha256(net),
        "turnovers_hash": canonical_sha256(turnovers),
        "cost_contract_hash": "a" * 64,
        "cost_model_input_hash": "b" * 64,
    }
    payload.update(overrides)
    payload["series_hash"] = canonical_sha256(payload)
    return payload


def _rehash_series(payload: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(payload)
    result.pop("series_hash", None)
    for field, hash_field in (
        ("ordered_period_keys", "ordered_period_keys_hash"),
        ("gross_returns", "gross_returns_hash"),
        ("costs", "costs_hash"),
        ("net_returns", "net_returns_hash"),
        ("turnovers", "turnovers_hash"),
    ):
        result[hash_field] = canonical_sha256(result[field])
    result["series_hash"] = canonical_sha256(result)
    return result


def test_evaluation_series_is_nonempty_canonical_and_exactly_reconciled() -> None:
    series_type = _contract("EvaluationSeriesReceipt")
    receipt = series_type(**_series_payload())
    assert receipt.series_hash == _series_payload()["series_hash"]
    assert receipt.net_returns == (
        float64_hex(0.015),
        float64_hex(-0.012),
    )


@pytest.mark.parametrize(
    "mutation",
    (
        {"gross_returns": (float64_hex(0.02),)},
        {"costs": (float64_hex(0.005),)},
        {"net_returns": (float64_hex(0.015),)},
        {"turnovers": (float64_hex(0.25),)},
        {"ordered_period_keys": (_period(1_700_000_000_000_000),)},
    ),
)
def test_series_rejects_every_cardinality_mismatch(mutation: dict[str, Any]) -> None:
    payload = _rehash_series({**_series_payload(), **mutation})
    with pytest.raises(ValidationError):
        _contract("EvaluationSeriesReceipt")(**payload)


@pytest.mark.parametrize(
    "periods",
    (
        (_period(2), _period(1)),
        (_period(1), _period(1)),
    ),
)
def test_period_keys_are_strictly_increasing_and_unique(
    periods: tuple[dict[str, Any], ...],
) -> None:
    payload = _series_payload(ordered_period_keys=periods)
    payload = _rehash_series(payload)
    with pytest.raises(ValidationError):
        _contract("EvaluationSeriesReceipt")(**payload)


@pytest.mark.parametrize(
    "periods",
    (
        (
            _period(1),
            {**_period(2), "frequency_id": "P1W"},
        ),
        (
            _period(1),
            {**_period(2), "label_horizon": "7D"},
        ),
    ),
)
def test_all_periods_share_one_frequency_and_label_horizon(
    periods: tuple[dict[str, Any], ...],
) -> None:
    payload = _rehash_series(_series_payload(ordered_period_keys=periods))
    with pytest.raises(ValidationError):
        _contract("EvaluationSeriesReceipt")(**payload)


@pytest.mark.parametrize(
    "field,bad_value",
    (
        ("gross_returns", "nan"),
        ("costs", "inf"),
        ("net_returns", "-inf"),
        ("turnovers", "-0x0.0p+0"),
        ("gross_returns", "0X1.0P+0"),
    ),
)
def test_series_rejects_nonfinite_or_noncanonical_float64_hex(
    field: str,
    bad_value: str,
) -> None:
    payload = _series_payload()
    values = list(payload[field])
    values[0] = bad_value
    payload[field] = tuple(values)
    payload = _rehash_series(payload)
    with pytest.raises((ValidationError, ValueError)):
        _contract("EvaluationSeriesReceipt")(**payload)


def test_positive_zero_is_the_only_zero_encoding() -> None:
    canonical_zero = float64_hex(-0.0)
    assert canonical_zero == "0x0.0p+0"
    payload = _series_payload(
        gross_returns=(canonical_zero, float64_hex(-0.01)),
        costs=(canonical_zero, float64_hex(0.002)),
        net_returns=(canonical_zero, float64_hex(-0.012)),
    )
    receipt = _contract("EvaluationSeriesReceipt")(**_rehash_series(payload))
    assert receipt.gross_returns[0] == receipt.costs[0] == receipt.net_returns[0] == canonical_zero


def test_series_rejects_one_ulp_reconciliation_drift() -> None:
    payload = _series_payload()
    payload["net_returns"] = (
        float64_hex(math.nextafter(0.015, math.inf)),
        payload["net_returns"][1],
    )
    payload = _rehash_series(payload)
    with pytest.raises(ValidationError):
        _contract("EvaluationSeriesReceipt")(**payload)


@pytest.mark.parametrize("field", ("costs", "turnovers"))
def test_costs_and_turnovers_must_be_nonnegative(field: str) -> None:
    payload = _series_payload()
    values = list(payload[field])
    values[0] = float64_hex(-0.005)
    payload[field] = tuple(values)
    if field == "costs":
        payload["net_returns"] = (
            float64_hex(0.02 - (-0.005)),
            payload["net_returns"][1],
        )
    payload = _rehash_series(payload)
    with pytest.raises(ValidationError):
        _contract("EvaluationSeriesReceipt")(**payload)


def test_series_rejects_component_hash_drift_even_if_series_hash_is_recomputed() -> None:
    payload = _series_payload(gross_returns_hash="f" * 64)
    payload_without_hash = dict(payload)
    payload_without_hash.pop("series_hash")
    payload["series_hash"] = canonical_sha256(payload_without_hash)
    with pytest.raises(ValidationError):
        _contract("EvaluationSeriesReceipt")(**payload)


def _candidate_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "family_id": "family-1",
        "evaluation_batch_id": "batch-1",
        "config_hash": "1" * 64,
        "alpha_id": "alpha-1",
        "evaluation_run_id": "run-1",
        "evaluation_content_hash": "2" * 64,
        "evaluation_audit_intent_hash": "3" * 64,
        "source_search_id": "search-1",
        "source_proposal_id": "4" * 64,
        "series": _series_payload(),
    }
    payload.update(overrides)
    payload["evaluation_receipt_hash"] = canonical_sha256(payload)
    return payload


def _benchmark_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "family_id": "family-1",
        "evaluation_batch_id": "batch-1",
        "benchmark_id": "EQUAL_WEIGHT_UNIVERSE_V1",
        "benchmark_contract_hash": "5" * 64,
        "series": _series_payload(),
    }
    payload.update(overrides)
    payload["benchmark_receipt_hash"] = canonical_sha256(payload)
    return payload


@pytest.mark.parametrize(
    ("name", "payload_factory", "hash_field"),
    (
        ("CandidateReturnSeries", _candidate_payload, "evaluation_receipt_hash"),
        ("BenchmarkReturnSeries", _benchmark_payload, "benchmark_receipt_hash"),
    ),
)
def test_candidate_and_benchmark_receipt_hashes_cover_all_payload_bytes(
    name: str,
    payload_factory: Any,
    hash_field: str,
) -> None:
    contract = _contract(name)
    payload = payload_factory()
    assert getattr(contract(**payload), hash_field) == payload[hash_field]

    tampered = deepcopy(payload)
    tampered["family_id"] = "family-2"
    with pytest.raises(ValidationError):
        contract(**tampered)


def _batch_payload(**overrides: Any) -> dict[str, Any]:
    candidates = (
        _candidate_payload(),
        _candidate_payload(
            config_hash="6" * 64,
            alpha_id="alpha-2",
            evaluation_run_id="run-2",
            evaluation_content_hash="7" * 64,
            evaluation_audit_intent_hash="8" * 64,
            source_proposal_id="9" * 64,
        ),
    )
    candidate_order = tuple(item["config_hash"] for item in candidates)
    receipt_hashes = tuple(item["evaluation_receipt_hash"] for item in candidates)
    payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_EVALUATION_BATCH_V1",
        "authority_instance_id": "family-authority-1",
        "genesis_record_hash": "0" * 64,
        "family_id": "family-1",
        "attempt_id": "attempt-1",
        "evaluation_batch_id": "batch-1",
        "frozen_config_set_hash": canonical_sha256(candidate_order),
        "candidate_count": len(candidate_order),
        "candidate_order": candidate_order,
        "candidate_receipt_hashes": receipt_hashes,
        "benchmark_receipt_hash": _benchmark_payload()["benchmark_receipt_hash"],
        "ordered_period_keys_hash": _series_payload()["ordered_period_keys_hash"],
        "cost_contract_hash": _series_payload()["cost_contract_hash"],
        "start_execution_attestation_hash": "a" * 64,
        "terminal_execution_attestation_hash": "b" * 64,
        "immutable_artifact_lease_hash": "c" * 64,
        "committed_at_us": 1_700_200_000_000_000,
    }
    payload.update(overrides)
    payload["batch_receipt_hash"] = canonical_sha256(_batch_authority_row(payload))
    return payload


def _batch_authority_row(payload: dict[str, Any]) -> dict[str, Any]:
    """Map the public batch receipt onto its exact authority-row hash fields."""

    return {
        "record_type": payload["record_type"],
        "authority_instance_id": payload["authority_instance_id"],
        "genesis_record_hash": payload["genesis_record_hash"],
        "evaluation_batch_id": payload["evaluation_batch_id"],
        "family_id": payload["family_id"],
        "attempt_id": payload["attempt_id"],
        "frozen_config_set_hash": payload["frozen_config_set_hash"],
        "candidate_count": payload["candidate_count"],
        "ordered_candidate_config_hashes_hash": canonical_sha256(payload["candidate_order"]),
        "ordered_candidate_receipt_hashes_hash": canonical_sha256(payload["candidate_receipt_hashes"]),
        "benchmark_receipt_hash": payload["benchmark_receipt_hash"],
        "ordered_period_keys_hash": payload["ordered_period_keys_hash"],
        "cost_contract_hash": payload["cost_contract_hash"],
        "start_execution_attestation_hash": payload["start_execution_attestation_hash"],
        "terminal_execution_attestation_hash": payload["terminal_execution_attestation_hash"],
        "immutable_artifact_lease_hash": payload["immutable_artifact_lease_hash"],
        "committed_at_us": payload["committed_at_us"],
    }


def test_batch_receipt_freezes_sorted_unique_candidates_and_exact_hashes() -> None:
    batch_type = _contract("FamilyEvaluationBatchReceipt")
    payload = _batch_payload()
    batch = batch_type(**payload)
    assert batch.candidate_count == 2
    assert batch.candidate_order == tuple(sorted(batch.candidate_order))
    assert batch.batch_receipt_hash == canonical_sha256(_batch_authority_row(payload))

    tampered = dict(payload)
    tampered["authority_instance_id"] = "family-authority-2"
    with pytest.raises(ValidationError):
        batch_type(**tampered)


@pytest.mark.parametrize(
    "overrides",
    (
        {"candidate_count": 1},
        {"candidate_order": ("6" * 64, "1" * 64)},
        {"candidate_order": ("1" * 64, "1" * 64)},
        {"candidate_receipt_hashes": ("a" * 64,)},
        {"candidate_receipt_hashes": ("a" * 64, "a" * 64)},
    ),
)
def test_batch_rejects_count_order_or_receipt_multiplicity_drift(
    overrides: dict[str, Any],
) -> None:
    payload = _batch_payload(**overrides)
    with pytest.raises(ValidationError):
        _contract("FamilyEvaluationBatchReceipt")(**payload)


def _attempt_payload(status: str, **overrides: Any) -> dict[str, Any]:
    terminal = status in {"COMPLETED", "INCOMPLETE"}
    genesis_record_hash = "0" * 64
    previous_event_hash = genesis_record_hash
    if terminal:
        previous_event_hash = _attempt_payload("STARTED")["event_hash"]
    payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_OOS_ACCESS_ATTEMPT_EVENT_V1",
        "authority_instance_id": "family-authority-1",
        "genesis_record_hash": genesis_record_hash,
        "attempt_event_id": f"attempt-1-event-{1 if terminal else 0}",
        "attempt_id": "attempt-1",
        "family_id": "family-1",
        "selection_decision_id": "decision-1",
        "oos_exposure_id": "exposure-1",
        "evaluation_batch_id": "batch-1",
        "sequence_no": 1 if terminal else 0,
        "status": status,
        "frozen_config_set_hash": "1" * 64,
        "benchmark_contract_hash": "2" * 64,
        "ordered_period_calendar_hash": "3" * 64,
        "cost_contract_hash": "4" * 64,
        "start_execution_attestation_hash": "5" * 64,
        "terminal_execution_attestation_hash": "6" * 64 if status == "COMPLETED" else None,
        "immutable_artifact_lease_hash": "7" * 64,
        "evaluation_batch_receipt_hash": "8" * 64 if status == "COMPLETED" else None,
        "reason_code": "EVALUATION_FAILED" if status == "INCOMPLETE" else None,
        "event_at_us": 1_700_100_000_000_001 if terminal else 1_700_100_000_000_000,
        "previous_event_hash": previous_event_hash,
    }
    payload.update(overrides)
    payload["event_hash"] = canonical_sha256(_attempt_authority_row(payload))
    return payload


def _attempt_authority_row(payload: dict[str, Any]) -> dict[str, Any]:
    """Map public aliases onto every final C7 attempt authority-row field."""

    return {
        "record_type": payload["record_type"],
        "authority_instance_id": payload["authority_instance_id"],
        "genesis_record_hash": payload["genesis_record_hash"],
        "attempt_event_id": payload["attempt_event_id"],
        "attempt_id": payload["attempt_id"],
        "family_id": payload["family_id"],
        "selection_decision_id": payload["selection_decision_id"],
        "oos_exposure_id": payload["oos_exposure_id"],
        "evaluation_batch_id": payload["evaluation_batch_id"],
        "sequence_no": payload["sequence_no"],
        "status": payload["status"],
        "frozen_config_set_hash": payload["frozen_config_set_hash"],
        "benchmark_contract_hash": payload["benchmark_contract_hash"],
        "ordered_period_calendar_hash": payload["ordered_period_calendar_hash"],
        "cost_contract_hash": payload["cost_contract_hash"],
        "start_execution_attestation_hash": payload["start_execution_attestation_hash"],
        "terminal_execution_attestation_hash": payload["terminal_execution_attestation_hash"],
        "immutable_artifact_lease_hash": payload["immutable_artifact_lease_hash"],
        "evaluation_batch_receipt_hash": payload["evaluation_batch_receipt_hash"],
        "reason_code": payload["reason_code"],
        "event_at_us": payload["event_at_us"],
        "prev_record_hash": payload["previous_event_hash"],
    }


@pytest.mark.parametrize("status", ("STARTED", "COMPLETED", "INCOMPLETE"))
def test_attempt_event_truth_table_accepts_exact_legal_branches(status: str) -> None:
    payload = _attempt_payload(status)
    event = _contract("OosAccessAttempt")(**payload)
    assert event.status == status
    assert event.event_hash == canonical_sha256(_attempt_authority_row(payload))


def test_attempt_chain_hashes_are_exact_authority_rows_and_start_binds_genesis() -> None:
    attempt_type = _contract("OosAccessAttempt")
    started = _attempt_payload("STARTED")
    assert started["previous_event_hash"] == started["genesis_record_hash"]
    start = attempt_type(**started)

    completed = _attempt_payload("COMPLETED")
    assert completed["previous_event_hash"] == start.event_hash
    assert attempt_type(**completed).event_hash == canonical_sha256(_attempt_authority_row(completed))

    wrong_genesis = _attempt_payload("STARTED", previous_event_hash="f" * 64)
    with pytest.raises(ValidationError):
        attempt_type(**wrong_genesis)

    stale_hash = dict(started)
    stale_hash["authority_instance_id"] = "family-authority-2"
    with pytest.raises(ValidationError):
        attempt_type(**stale_hash)


def test_incomplete_terminal_attestation_hash_is_optional_but_reason_is_not() -> None:
    attempt_type = _contract("OosAccessAttempt")
    without_terminal = attempt_type(**_attempt_payload("INCOMPLETE"))
    with_terminal = attempt_type(**_attempt_payload("INCOMPLETE", terminal_execution_attestation_hash="a" * 64))
    assert without_terminal.terminal_execution_attestation_hash is None
    assert with_terminal.terminal_execution_attestation_hash == "a" * 64

    with pytest.raises(ValidationError):
        attempt_type(**_attempt_payload("INCOMPLETE", reason_code=None))


@pytest.mark.parametrize(
    ("status", "overrides"),
    (
        ("STARTED", {"sequence_no": 1}),
        ("STARTED", {"reason_code": "EARLY_REASON"}),
        ("STARTED", {"terminal_execution_attestation_hash": "a" * 64}),
        ("STARTED", {"evaluation_batch_receipt_hash": "a" * 64}),
        ("COMPLETED", {"sequence_no": 0}),
        ("COMPLETED", {"terminal_execution_attestation_hash": None}),
        ("COMPLETED", {"evaluation_batch_receipt_hash": None}),
        ("COMPLETED", {"reason_code": "NOT_ALLOWED"}),
        ("INCOMPLETE", {"sequence_no": 0}),
        ("INCOMPLETE", {"evaluation_batch_receipt_hash": "a" * 64}),
        ("RETRYING", {}),
    ),
)
def test_attempt_event_truth_table_rejects_every_illegal_branch(
    status: str,
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        _contract("OosAccessAttempt")(**_attempt_payload(status, **overrides))


def _release_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "record_type": "DATA_SNOOPING_OOS_RELEASE_V1",
        "authority_instance_id": "family-authority-1",
        "genesis_record_hash": "0" * 64,
        "oos_release_id": "release-1",
        "family_id": "family-1",
        "attempt_id": "attempt-1",
        "started_attempt_event_id": "attempt-1-event-0",
        "family_freeze_record_hash": "1" * 64,
        "exposure_consumption_record_hash": "2" * 64,
        "oos_exposure_id": "exposure-1",
        "exposure_fingerprint": "3" * 64,
        "exposure_authority_id": "exposure-authority-1",
        "exposure_authority_head": "4" * 64,
        "start_execution_attestation_hash": "5" * 64,
        "execution_attestation_authority_id": "execution-authority-1",
        "execution_attestation_authority_head": "6" * 64,
        "immutable_artifact_lease_id": "lease-1",
        "immutable_artifact_lease_hash": "7" * 64,
        "immutable_artifact_lease_authority_id": "lease-authority-1",
        "immutable_artifact_lease_authority_head": "8" * 64,
        "released_at_us": 1_700_100_000_000_001,
    }
    payload.update(overrides)
    payload["release_record_hash"] = canonical_sha256(_release_authority_row(payload))
    payload["authority_head"] = payload["release_record_hash"]
    return payload


def _release_authority_row(payload: dict[str, Any]) -> dict[str, Any]:
    """Compose the C7 core and required C6 extension into the final row hash."""

    return {
        "record_type": payload["record_type"],
        "authority_instance_id": payload["authority_instance_id"],
        "genesis_record_hash": payload["genesis_record_hash"],
        "exposure_fingerprint": payload["exposure_fingerprint"],
        "exposure_authority_id": payload["exposure_authority_id"],
        "exposure_authority_head": payload["exposure_authority_head"],
        "start_execution_attestation_hash": payload["start_execution_attestation_hash"],
        "execution_attestation_authority_id": payload["execution_attestation_authority_id"],
        "execution_attestation_authority_head": payload["execution_attestation_authority_head"],
        "immutable_artifact_lease_id": payload["immutable_artifact_lease_id"],
        "immutable_artifact_lease_hash": payload["immutable_artifact_lease_hash"],
        "immutable_artifact_lease_authority_id": payload["immutable_artifact_lease_authority_id"],
        "immutable_artifact_lease_authority_head": payload["immutable_artifact_lease_authority_head"],
        "oos_release_id": payload["oos_release_id"],
        "family_id": payload["family_id"],
        "attempt_id": payload["attempt_id"],
        "started_attempt_event_id": payload["started_attempt_event_id"],
        "family_freeze_record_hash": payload["family_freeze_record_hash"],
        "exposure_consumption_record_hash": payload["exposure_consumption_record_hash"],
        "oos_exposure_id": payload["oos_exposure_id"],
        "released_at_us": payload["released_at_us"],
    }


def test_release_receipt_binds_freeze_consumption_attempt_exposure_and_c6_refs() -> None:
    receipt_type = _contract("OosReleaseReceipt")
    payload = _release_payload()
    receipt = receipt_type(**payload)
    assert receipt.release_record_hash == canonical_sha256(_release_authority_row(payload))
    assert receipt.authority_head == receipt.release_record_hash

    tampered = dict(payload)
    tampered["exposure_consumption_record_hash"] = "f" * 64
    with pytest.raises(ValidationError):
        receipt_type(**tampered)

    wrong_head = _release_payload(authority_instance_id="family-authority-2")
    wrong_head["authority_head"] = payload["authority_head"]
    with pytest.raises(ValidationError):
        receipt_type(**wrong_head)


def test_candidate_disposition_remains_the_exact_c5_contract_and_branches() -> None:
    disposition = _contract("CandidateDisposition")
    assert set(disposition.model_fields) == {
        "family_id",
        "proposal_id",
        "disposition",
        "config_hash",
        "evaluation_receipt_hash",
        "quarantine_record_id",
        "reason_code",
        "performance_accessed",
    }
    evaluated = disposition(
        family_id="family-1",
        proposal_id="1" * 64,
        disposition="PERFORMANCE_EVALUATED",
        config_hash="2" * 64,
        evaluation_receipt_hash="3" * 64,
        quarantine_record_id=None,
        reason_code=None,
        performance_accessed=True,
    )
    assert evaluated.disposition == "PERFORMANCE_EVALUATED"

    with pytest.raises(ValidationError):
        disposition(
            family_id="family-1",
            proposal_id="1" * 64,
            disposition="PERFORMANCE_ATTEMPTED_INCOMPLETE",
            config_hash="2" * 64,
            evaluation_receipt_hash="3" * 64,
            quarantine_record_id=None,
            reason_code="FAILED",
            performance_accessed=True,
        )
