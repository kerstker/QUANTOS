from __future__ import annotations

import json

import pytest

from tools.attention_momentum_preregistration import (
    ATTENTION_ONLY_ALIAS,
    CONTRACT_HASH,
    EARLIEST_UNBLIND_AT_MS,
    EVALUATION_CONTRACT,
    MIN_ELIGIBLE_OUTCOME_DAYS,
    PRICE_ONLY_ALIAS,
    PRIMARY_ALIAS,
    TIMESTAMP_PLACEBO_ALIAS,
    build_preregistration,
    candidate_definitions,
    write_preregistration,
)


def test_contract_freezes_one_forward_family_without_parameter_scan() -> None:
    assert EVALUATION_CONTRACT["candidate_aliases"] == (
        PRIMARY_ALIAS,
        PRICE_ONLY_ALIAS,
        TIMESTAMP_PLACEBO_ALIAS,
        ATTENTION_ONLY_ALIAS,
    )
    assert EVALUATION_CONTRACT["attention_lookback_days"] == 28
    assert EVALUATION_CONTRACT["momentum_lookback_days"] == 7
    assert EVALUATION_CONTRACT["decision_and_label"]["holding_days"] == 1
    assert EVALUATION_CONTRACT["portfolio"]["max_participation"] == 0.05
    assert EVALUATION_CONTRACT["parameter_scan"] is False
    assert EVALUATION_CONTRACT["outcome_access_before_unblind"] is False
    assert EVALUATION_CONTRACT["unblinding"]["earliest_unblind_at_ms"] == (
        EARLIEST_UNBLIND_AT_MS
    )
    assert EVALUATION_CONTRACT["unblinding"]["minimum_eligible_outcome_days"] == (
        MIN_ELIGIBLE_OUTCOME_DAYS
    )
    assert len(CONTRACT_HASH) == 64


def test_candidate_definitions_are_unique_and_contract_bound() -> None:
    definitions = candidate_definitions()

    assert len(definitions) == 4
    assert len({item[0].alpha_id for item in definitions}) == 4
    assert len({item[0].definition_hash for item in definitions}) == 4
    assert all(item[0].params["contract_hash"] == CONTRACT_HASH for item in definitions)


def test_preregistration_contains_no_outcomes_or_registry_mutation() -> None:
    document = build_preregistration(
        source_commit="a" * 40,
        preregistered_at_ms=123_000,
    )

    assert document["admissible_knowledge_time_rule"] == (
        "knowledge_time > preregistered_at_ms"
    )
    assert document["outcomes_accessed"] is False
    assert document["return_statistics_computed"] is False
    assert document["trial_registry_mutated"] is False
    assert document["alpha_eligible_count"] == 0
    assert len(document["candidates"]) == 4
    assert len(document["preregistration_id"]) == 64


def test_append_once_recorder_refuses_overwrite(tmp_path) -> None:
    output = tmp_path / "attention-preregistration.json"
    first = write_preregistration(
        output=output,
        source_commit="b" * 40,
        preregistered_at_ms=456_000,
    )

    recorded = json.loads(output.read_text(encoding="utf-8"))
    assert recorded["preregistration_id"] == first["preregistration_id"]
    assert recorded["contract_hash"] == first["contract_hash"]
    assert recorded["outcomes_accessed"] is False
    assert recorded["return_statistics_computed"] is False
    with pytest.raises(FileExistsError, match="append-once"):
        write_preregistration(
            output=output,
            source_commit="b" * 40,
            preregistered_at_ms=456_001,
        )


def test_invalid_commit_or_timestamp_fails_closed() -> None:
    with pytest.raises(ValueError, match="source_commit"):
        build_preregistration(source_commit="not-a-commit", preregistered_at_ms=1)
    with pytest.raises(TypeError, match="preregistered_at_ms"):
        build_preregistration(source_commit="c" * 40, preregistered_at_ms=True)
