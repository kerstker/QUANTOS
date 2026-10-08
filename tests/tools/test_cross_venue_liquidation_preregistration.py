import json

import pytest

from services.data.channel_registry import channel_specs
from tools.cross_venue_liquidation_preregistration import (
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


def test_contract_freezes_primary_and_four_non_rescuing_controls() -> None:
    definitions = candidate_definitions()

    assert len(definitions) == 5
    assert len({item.definition_hash for item, _role in definitions}) == 5
    assert {role for _item, role in definitions} == {
        "PRIMARY",
        "SIGN_CONTROL",
        "VENUE_CONTROL",
        "PAYER_CONTROL",
        "CLOCK_CONTROL",
    }
    assert all(item.params["contract_hash"] == CONTRACT_HASH for item, _ in definitions)
    assert all(item.universe == "forward_bybit_binance_linear_3" for item, _ in definitions)


def test_contract_names_payer_persistence_and_independent_bet() -> None:
    payer = EVALUATION_CONTRACT["economic_payer"]

    assert payer["primary"] == "FORCED_LIQUIDATOR"
    assert payer["secondary"] == "CAPITAL_CONSTRAINED_CROSS_VENUE"
    assert payer["who_pays"].strip()
    assert payer["persistence_assumption"].strip()
    assert "30-minute" in payer["independent_bet_unit"]
    assert EVALUATION_CONTRACT["data"]["historical_backfill_credit"] is False
    assert EVALUATION_CONTRACT["episode_definition"]["future_event_access"] is False
    assert EVALUATION_CONTRACT["episode_definition"]["outcome_in_episode_selection"] is False


def test_contract_authority_paths_match_channel_registry() -> None:
    channels = {item.channel_id: item for item in channel_specs()}
    data = EVALUATION_CONTRACT["data"]

    assert data["bybit_authority"] == channels[
        data["bybit_channel_id"]
    ].artifact_path
    assert data["binance_authority"] == channels[
        data["binance_channel_id"]
    ].artifact_path


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
    assert document["evaluation_contract"]["unblinding"][
        "minimum_independent_episodes"
    ] == MIN_INDEPENDENT_EPISODES


def test_executable_objective_is_load_bearing_not_sharpe_only() -> None:
    execution = EVALUATION_CONTRACT["portfolio_and_execution"]
    bars = EVALUATION_CONTRACT["decision_bars"]

    assert "blocks PASS" in execution["fees"]
    assert execution["impact"] == "walk reconstructed depth or fail closed"
    assert execution["incremental_baseline"]
    assert bars["executable_value_policy"] == "executable_value.v1"
    assert bars["incremental_net_return_positive"] is True
    assert bars["minimum_capacity_usd"] >= 1_000_000
    assert bars["minimum_independent_prediction_count"] == MIN_INDEPENDENT_EPISODES
    assert bars["maximum_abs_correlation_to_existing_book"] <= 0.60


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
    output = tmp_path / "cross-venue-liquidation-prereg.json"
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
