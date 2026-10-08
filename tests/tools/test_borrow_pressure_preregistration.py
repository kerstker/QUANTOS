import json

import pytest

from services.data.channel_registry import channel_specs
from tools.borrow_pressure_preregistration import (
    CONTRACT_HASH,
    EVALUATION_CONTRACT,
    MIN_COLLECTION_MS,
    MIN_INDEPENDENT_EPISODES,
    UNIVERSE,
    build_preregistration,
    candidate_definitions,
    write_preregistration,
)


COMMIT = "1" * 40


def test_contract_freezes_primary_and_four_controls() -> None:
    definitions = candidate_definitions()

    assert len(definitions) == 5
    assert len({item.definition_hash for item, _role in definitions}) == 5
    assert {role for _item, role in definitions} == {
        "PRIMARY",
        "SIGN_CONTROL",
        "BORROW_ABLATION_CONTROL",
        "FUNDING_ABLATION_CONTROL",
        "SCARCITY_CONTROL",
    }
    assert all(item.params["contract_hash"] == CONTRACT_HASH for item, _ in definitions)
    assert all(item.universe == "forward_okx_binance_common_14" for item, _ in definitions)


def test_contract_names_payer_and_forbids_availability_inference() -> None:
    payer = EVALUATION_CONTRACT["economic_payer"]
    data = EVALUATION_CONTRACT["data"]

    assert payer["primary"] == "LEVERAGED_CROWD"
    assert payer["secondary"] == "CAPITAL_CONSTRAINED_CROSS_VENUE"
    assert "seven-day" in payer["independent_bet_unit"]
    assert data["historical_backfill_credit"] is False
    assert data["historical_okx_rows_pit_before_fetch"] is False
    assert data["borrow_availability_may_infer_capacity"] is False
    assert "UNAVAILABLE" in data["borrow_availability"]


def test_contract_authority_paths_match_channel_registry() -> None:
    channels = {item.channel_id: item for item in channel_specs()}
    data = EVALUATION_CONTRACT["data"]

    assert data["okx_snapshot_authority"] == channels[
        data["okx_channel_id"]
    ].artifact_path
    assert data["binance_authority"] == channels[
        data["binance_channel_id"]
    ].artifact_path


def test_preregistration_is_outcome_blind_and_executable_value_gated() -> None:
    document = build_preregistration(
        source_commit=COMMIT,
        preregistered_at_ms=123_000,
    )
    bars = document["evaluation_contract"]["decision_bars"]

    assert document["earliest_unblind_at_ms"] == 123_000 + MIN_COLLECTION_MS
    assert document["outcomes_accessed"] is False
    assert document["return_statistics_computed"] is False
    assert document["trial_registry_mutated"] is False
    assert document["alpha_eligible_count"] == 0
    assert tuple(document["evaluation_contract"]["data"]["universe"]) == UNIVERSE
    assert bars["executable_value_policy"] == "executable_value.v1"
    assert bars["minimum_independent_prediction_count"] == MIN_INDEPENDENT_EPISODES
    assert bars["minimum_capacity_usd"] >= 1_000_000
    assert document["evaluation_contract"]["testnet_or_live_authority"] is False


def test_identity_is_deterministic_and_writer_is_append_once(tmp_path) -> None:
    first = build_preregistration(source_commit=COMMIT, preregistered_at_ms=456)
    second = build_preregistration(source_commit=COMMIT, preregistered_at_ms=456)
    output = tmp_path / "borrow-pressure-prereg.json"
    written = write_preregistration(
        output=output,
        source_commit=COMMIT,
        preregistered_at_ms=456,
    )

    assert first == second == written
    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(
        json.dumps(written)
    )
    with pytest.raises(FileExistsError, match="append-once"):
        write_preregistration(
            output=output,
            source_commit=COMMIT,
            preregistered_at_ms=457,
        )


@pytest.mark.parametrize("value", ["", "A" * 40, "1" * 39, "1" * 41])
def test_identity_inputs_fail_closed(value: str) -> None:
    with pytest.raises(ValueError, match="source_commit"):
        build_preregistration(source_commit=value, preregistered_at_ms=1)
    with pytest.raises(TypeError, match="integer"):
        build_preregistration(source_commit=COMMIT, preregistered_at_ms=True)
