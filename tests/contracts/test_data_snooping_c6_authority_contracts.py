"""Adversarial Pass-C/C6 contract semantics for external authorities.

The family authority must not alias an observed-PIT provider identity, an
authority-row hash, and an authority-event head merely because all three are
hash-shaped values.  These tests freeze the public names before any service or
schema-v3 activation exists.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from quantos.contracts.canonical import canonical_sha256
import quantos.contracts.research as research_contracts


OOS_EXPOSURE_FIELDS = {
    "oos_exposure_id",
    "selection_scope_id",
    "selection_decision_id",
    "family_id",
    "exposure_ordinal",
    "observed_pit_authority_id",
    "observed_pit_handle_id",
    "observed_pit_content_hash",
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
    "period_start_us",
    "period_end_us",
    "exposure_fingerprint",
    "exposure_version",
    "observed_pit_authority_head",
}

OOS_CONSUMPTION_FIELDS = {
    "oos_exposure_id",
    "selection_decision_id",
    "family_id",
    "access_attempt_id",
    "expected_exposure_version",
    "consumed_at_us",
    "consumption_authority_id",
    "consumption_record_hash",
    "consumption_authority_head",
}


def _contract(name: str) -> type[Any]:
    contract = getattr(research_contracts, name, None)
    assert contract is not None, f"Pass C C6 contract is missing: {name}"
    return contract


def _fingerprint_binding() -> dict[str, Any]:
    return {
        "observed_pit_content_hash": "1" * 64,
        "dataset_version": "dataset-v1",
        "universe_id": "observed-universe",
        "universe_hash": "2" * 64,
        "ordered_period_calendar_hash": "3" * 64,
        "label_horizon": "P7D",
        "purge_periods": 1,
        "embargo_periods": 2,
    }


def _exposure_payload(**overrides: Any) -> dict[str, Any]:
    binding = _fingerprint_binding()
    payload: dict[str, Any] = {
        "oos_exposure_id": "exposure-1",
        "selection_scope_id": "scope-1",
        "selection_decision_id": "decision-1",
        "family_id": "family-1",
        "exposure_ordinal": 1,
        "observed_pit_authority_id": "pit-authority-1",
        "observed_pit_handle_id": "pit-handle-1",
        **binding,
        "dataset_id": "crypto-daily",
        "knowledge_time_policy_id": "PIT_KNOWLEDGE_TIME_V1",
        "knowledge_time_policy_version": "1",
        "cost_contract_hash": "4" * 64,
        "benchmark_contract_hash": "5" * 64,
        "period_start_us": 1_700_000_000_000_000,
        "period_end_us": 1_710_000_000_000_000,
        "exposure_fingerprint": canonical_sha256(binding),
        "exposure_version": 1,
        "observed_pit_authority_head": "6" * 64,
    }
    payload.update(overrides)
    return payload


def _consumption_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "oos_exposure_id": "exposure-1",
        "selection_decision_id": "decision-1",
        "family_id": "family-1",
        "access_attempt_id": "attempt-1",
        "expected_exposure_version": 1,
        "consumed_at_us": 1_710_000_000_000_001,
        "consumption_authority_id": "family-authority-1",
        "consumption_record_hash": "7" * 64,
        "consumption_authority_head": "8" * 64,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("name", "expected_fields"),
    (
        ("OosExposureRef", OOS_EXPOSURE_FIELDS),
        ("OosExposureConsumption", OOS_CONSUMPTION_FIELDS),
    ),
)
def test_c6_authority_contracts_are_strict_frozen_and_unambiguous(
    name: str,
    expected_fields: set[str],
) -> None:
    contract = _contract(name)
    assert set(contract.model_fields) == expected_fields
    assert contract.model_config["frozen"] is True
    assert contract.model_config["strict"] is True
    assert contract.model_config["extra"] == "forbid"
    assert "authority_id" not in expected_fields
    assert "authority_head" not in expected_fields


def test_oos_exposure_pins_complete_observed_pit_policy_and_contract_identity() -> None:
    exposure_type = _contract("OosExposureRef")
    exposure = exposure_type(**_exposure_payload())
    assert exposure.observed_pit_authority_id == "pit-authority-1"
    assert exposure.knowledge_time_policy_id == "PIT_KNOWLEDGE_TIME_V1"
    assert exposure.cost_contract_hash == "4" * 64
    assert exposure.benchmark_contract_hash == "5" * 64

    for missing in (
        "observed_pit_authority_id",
        "knowledge_time_policy_id",
        "knowledge_time_policy_version",
        "cost_contract_hash",
        "benchmark_contract_hash",
        "observed_pit_authority_head",
    ):
        invalid = _exposure_payload()
        invalid.pop(missing)
        with pytest.raises(ValidationError):
            exposure_type(**invalid)


def test_oos_exposure_fingerprint_formula_remains_the_frozen_data_binding() -> None:
    exposure_type = _contract("OosExposureRef")
    expected = canonical_sha256(_fingerprint_binding())
    assert exposure_type(**_exposure_payload()).exposure_fingerprint == expected

    for field, value in (
        ("observed_pit_authority_id", "pit-authority-2"),
        ("observed_pit_handle_id", "pit-handle-2"),
        ("dataset_id", "renamed-dataset"),
        ("knowledge_time_policy_id", "PIT_KNOWLEDGE_TIME_V2"),
        ("knowledge_time_policy_version", "2"),
        ("cost_contract_hash", "a" * 64),
        ("benchmark_contract_hash", "b" * 64),
        ("observed_pit_authority_head", "c" * 64),
    ):
        changed = exposure_type(**_exposure_payload(**{field: value}))
        assert changed.exposure_fingerprint == expected

    invalid = deepcopy(_exposure_payload())
    invalid["dataset_version"] = "dataset-v2"
    with pytest.raises(ValidationError):
        exposure_type(**invalid)


def test_generic_authority_aliases_are_rejected_on_both_contracts() -> None:
    with pytest.raises(ValidationError):
        _contract("OosExposureRef")(**_exposure_payload(authority_id="ambiguous", authority_head="d" * 64))
    with pytest.raises(ValidationError):
        _contract("OosExposureConsumption")(**_consumption_payload(authority_id="ambiguous", authority_head="e" * 64))


def test_consumption_keeps_row_hash_and_authority_event_head_distinct() -> None:
    consumption_type = _contract("OosExposureConsumption")
    value = consumption_type(**_consumption_payload())
    assert value.consumption_authority_id == "family-authority-1"
    assert value.consumption_record_hash == "7" * 64
    assert value.consumption_authority_head == "8" * 64
    assert value.consumption_record_hash != value.consumption_authority_head
