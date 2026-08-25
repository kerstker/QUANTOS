import json

import pytest

from tools.ict_liquidity_sweep_preregistration import (
    CONTRACT_HASH,
    MIN_COLLECTION_MS,
    UNIVERSE,
    build_preregistration,
    candidate_definitions,
    write_preregistration,
)


COMMIT = "1" * 40


def test_contract_freezes_exactly_five_non_rescuing_candidates() -> None:
    definitions = candidate_definitions()

    assert len(definitions) == 5
    assert len({item.definition_hash for item, _role in definitions}) == 5
    assert {role for _item, role in definitions} == {
        "PRIMARY",
        "ALL_DAY_CONTROL",
        "SWEEP_ONLY_CONTROL",
        "SIGN_CONTROL",
        "CLOCK_PLACEBO_CONTROL",
    }
    assert all(item.params["contract_hash"] == CONTRACT_HASH for item, _ in definitions)


def test_preregistration_contains_no_outcome_or_authority_claim() -> None:
    document = build_preregistration(
        source_commit=COMMIT,
        preregistered_at_ms=123_000,
    )

    assert document["earliest_unblind_at_ms"] == 123_000 + MIN_COLLECTION_MS
    assert document["outcomes_accessed"] is False
    assert document["return_statistics_computed"] is False
    assert document["trial_registry_mutated"] is False
    assert document["alpha_eligible_count"] == 0
    assert document["evaluation_contract"]["parameter_scan"] is False
    assert document["evaluation_contract"]["testnet_or_live_authority"] is False
    assert tuple(document["evaluation_contract"]["data"]["universe"]) == UNIVERSE
    assert len(document["candidates"]) == 5


def test_preregistration_is_deterministic_for_exact_identity() -> None:
    first = build_preregistration(source_commit=COMMIT, preregistered_at_ms=456)
    second = build_preregistration(source_commit=COMMIT, preregistered_at_ms=456)

    assert first == second
    assert first["contract_hash"] == CONTRACT_HASH
    assert first["preregistration_id"] == second["preregistration_id"]


@pytest.mark.parametrize("value", ["", "A" * 40, "1" * 39, "1" * 41])
def test_source_commit_must_be_exact_lowercase_git_hash(value: str) -> None:
    with pytest.raises(ValueError, match="source_commit"):
        build_preregistration(source_commit=value, preregistered_at_ms=1)


def test_timestamp_validation_is_fail_closed() -> None:
    with pytest.raises(TypeError, match="integer"):
        build_preregistration(source_commit=COMMIT, preregistered_at_ms=True)
    with pytest.raises(ValueError, match="non-negative"):
        build_preregistration(source_commit=COMMIT, preregistered_at_ms=-1)


def test_append_once_writer_refuses_overwrite(tmp_path) -> None:
    output = tmp_path / "ict-prereg.json"
    document = write_preregistration(
        output=output,
        source_commit=COMMIT,
        preregistered_at_ms=789,
    )
    raw = json.loads(output.read_text(encoding="utf-8"))

    assert raw == json.loads(json.dumps(document))
    with pytest.raises(FileExistsError, match="append-once"):
        write_preregistration(
            output=output,
            source_commit=COMMIT,
            preregistered_at_ms=790,
        )
