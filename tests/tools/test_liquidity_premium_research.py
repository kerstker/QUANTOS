import pytest

from services.execution.backtest_authority import (
    BacktestIntegrityBlocked,
    LEGACY_INVALIDATION_CODE,
)
from tools.liquidity_premium_research import (
    CONTRACT_HASH,
    EVALUATION_CONTRACT,
    LARGE_CAP_ALIAS,
    PRIMARY_ALIAS,
    candidate_definitions,
    judge,
    run,
)

from services.research.alpha_expression import FieldPanel


def test_frozen_contract_has_four_candidates_and_no_parameter_scan() -> None:
    assert EVALUATION_CONTRACT["candidate_aliases"] == (
        PRIMARY_ALIAS,
        "liquidity_liquid_sign_control_v1",
        "liquidity_hash_placebo_v1",
        LARGE_CAP_ALIAS,
    )
    assert EVALUATION_CONTRACT["lookback_rows"] == 28
    assert EVALUATION_CONTRACT["holding_rows"] == 7
    assert EVALUATION_CONTRACT["universe_size"] == 48
    assert EVALUATION_CONTRACT["large_cap_size"] == 24
    assert EVALUATION_CONTRACT["target_aum_usd"] == 1_000_000.0
    assert EVALUATION_CONTRACT["parameter_scan"] is False
    assert len(CONTRACT_HASH) == 64


def test_candidate_definitions_are_unique_and_contract_bound() -> None:
    definitions = candidate_definitions()

    assert len(definitions) == 4
    assert len({item[0].alpha_id for item in definitions}) == 4
    assert len({item[0].definition_hash for item in definitions}) == 4
    assert all(item[0].params["contract_hash"] == CONTRACT_HASH for item in definitions)


def _fixture_inputs(
    *,
    verdict: str = "PASS",
    full_evidence_verified: bool = True,
    participation: float | None = 0.02,
):
    aliases = {
        PRIMARY_ALIAS: "primary",
        "liquidity_liquid_sign_control_v1": "sign",
        "liquidity_hash_placebo_v1": "hash",
        LARGE_CAP_ALIAS: "large",
    }
    evaluations = {
        "primary": {
            "verified": True,
            "full_evidence_verified": full_evidence_verified,
            "verdict": verdict,
            "deflated_sharpe": 0.99,
            "pbo": 0.10,
        }
    }
    base = {
        "net_sharpe": 1.4,
        "net_return": 0.20,
        "max_drawdown": 0.08,
        "max_participation": participation,
    }
    replays = {
        "primary": {
            "base_cost": base,
            "double_cost_impact": {**base, "net_sharpe": 1.0, "net_return": 0.08},
        },
        "sign": {"base_cost": {**base, "net_sharpe": -1.0}},
        "hash": {"base_cost": {**base, "net_sharpe": 0.2}},
        "large": {"base_cost": {**base, "net_sharpe": 0.8, "net_return": 0.06}},
    }
    holdout = {"net_sharpe": 1.0, "net_return": 0.03}
    return evaluations, replays, aliases, holdout


def test_judge_requires_all_frozen_economic_and_formal_bars() -> None:
    decision = judge(*_fixture_inputs())

    assert decision["status"] == "HISTORICAL_DIAGNOSTIC_PASS"
    assert decision["formal_pass"] is True
    assert decision["economic_candidate"] is True
    assert decision["forward_admission_allowed"] is False
    assert decision["live_eligible"] is False


def test_incomplete_catalog_evidence_rejects_formally_without_erasing_economics() -> None:
    decision = judge(*_fixture_inputs(full_evidence_verified=False))

    assert decision["formal_pass"] is False
    assert decision["economic_candidate"] is True
    assert decision["failed_bars"] == ["full_evidence_verified"]


def test_unassessed_participation_fails_closed_economically() -> None:
    decision = judge(*_fixture_inputs(participation=None))

    assert decision["formal_pass"] is False
    assert decision["economic_candidate"] is False
    assert "participation_assessed" in decision["failed_bars"]
    assert "max_participation_at_most_10pct" in decision["failed_bars"]


def _preflight_failure_panel() -> FieldPanel:
    rows, names = 40, 50
    instruments = [f"c{index}" for index in range(names)]
    event_times = [1_700_000_000_000 + row * 86_400_000 for row in range(rows)]
    close = [[100.0 + index for index in range(names)] for _row in range(rows)]
    volume = [
        [1_000.0 * (index + 1) for index in range(names)]
        for _row in range(rows)
    ]
    # c49 is the most liquid selected name at t=28 and has no t+7 label.
    close[35][49] = None
    return FieldPanel(instruments, event_times, {"close": close, "volume": volume})


def test_public_run_blocks_before_audited_data_rejection_persistence(
    monkeypatch, tmp_path
) -> None:
    from tools import liquidity_premium_research as research

    def fail_if_touched(*_args, **_kwargs):
        pytest.fail("authority quarantine must precede panel and registry access")

    monkeypatch.setattr(research, "_research_panel", fail_if_touched)
    monkeypatch.setattr(
        research,
        "production_experiment_registry",
        fail_if_touched,
    )
    output = tmp_path / "preflight-rejection.json"

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        run(
            output=output,
            source_commit="a" * 40,
            recovery_tool_commit="b" * 40,
        )

    assert not output.exists()


def test_public_run_blocks_before_unaudited_preflight(
    monkeypatch, tmp_path
) -> None:
    from tools import liquidity_premium_research as research

    def fail_if_touched(*_args, **_kwargs):
        pytest.fail("legacy preflight must not run before authority quarantine")

    monkeypatch.setattr(research, "_research_panel", fail_if_touched)
    output = tmp_path / "must-not-exist.json"

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        run(output=output, source_commit="a" * 40)

    assert not output.exists()
