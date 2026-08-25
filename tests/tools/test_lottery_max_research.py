from __future__ import annotations

from services.research.alpha_expression import FieldPanel
from tools.lottery_max_research import (
    CONTRACT_HASH,
    EVALUATION_CONTRACT,
    HASH_ALIAS,
    LIQUID_ALIAS,
    PRIMARY_ALIAS,
    REVERSAL_ALIAS,
    SIGN_ALIAS,
    candidate_definitions,
    judge,
    run,
)


def test_contract_freezes_conflicting_signs_and_exactly_five_candidates() -> None:
    assert EVALUATION_CONTRACT["candidate_aliases"] == (
        PRIMARY_ALIAS,
        SIGN_ALIAS,
        REVERSAL_ALIAS,
        HASH_ALIAS,
        LIQUID_ALIAS,
    )
    assert EVALUATION_CONTRACT["return_lookback_rows"] == 7
    assert EVALUATION_CONTRACT["liquidity_lookback_rows"] == 28
    assert EVALUATION_CONTRACT["holding_rows"] == 7
    assert EVALUATION_CONTRACT["universe_size"] == 60
    assert EVALUATION_CONTRACT["liquid_size"] == 30
    assert EVALUATION_CONTRACT["parameter_scan"] is False
    assert EVALUATION_CONTRACT["competing_sign_can_rescue_primary"] is False
    assert [paper["role"] for paper in EVALUATION_CONTRACT["source_papers"]] == [
        "LOW_MAX_PRIMARY_PRIOR",
        "HIGH_MAX_COMPETING_SIGN_CONTROL",
    ]
    assert len(CONTRACT_HASH) == 64


def test_candidate_definitions_are_unique_and_contract_bound() -> None:
    definitions = candidate_definitions()
    assert len(definitions) == 5
    assert len({item[0].alpha_id for item in definitions}) == 5
    assert len({item[0].definition_hash for item in definitions}) == 5
    assert all(item[0].params["contract_hash"] == CONTRACT_HASH for item in definitions)
    assert {item[3] for item in definitions} == {
        "PRIMARY",
        "COMPETING_SIGN_CONTROL",
        "WEEKLY_REVERSAL_CONTROL",
        "HASH_PLACEBO",
        "INVESTABILITY_CONTROL",
    }


def _fixture_inputs(*, verdict: str = "PASS"):
    aliases = {
        PRIMARY_ALIAS: "primary",
        SIGN_ALIAS: "sign",
        REVERSAL_ALIAS: "reversal",
        HASH_ALIAS: "hash",
        LIQUID_ALIAS: "liquid",
    }
    evaluation = {
        "primary": {
            "verified": True,
            "full_evidence_verified": True,
            "verdict": verdict,
            "deflated_sharpe": 0.99,
            "pbo": 0.10,
        }
    }
    base = {
        "net_sharpe": 1.4,
        "net_return": 0.20,
        "max_drawdown": 0.08,
        "max_participation": 0.02,
    }
    replays = {
        "primary": {
            "base_cost": base,
            "double_cost_impact": {**base, "net_return": 0.08},
            "terminal_adverse_stress": {**base, "net_return": 0.05},
        },
        "sign": {"base_cost": {**base, "net_sharpe": -0.8}},
        "reversal": {"base_cost": {**base, "net_sharpe": 0.7}},
        "hash": {"base_cost": {**base, "net_sharpe": 0.1}},
        "liquid": {"base_cost": {**base, "net_sharpe": 0.8, "net_return": 0.05}},
    }
    suffix = {"net_sharpe": 1.0, "net_return": 0.03}
    return evaluation, replays, aliases, suffix


def test_judge_requires_economic_uniqueness_cost_and_capacity() -> None:
    decision = judge(*_fixture_inputs())
    assert decision["status"] == "HISTORICAL_DIAGNOSTIC_PASS"
    assert decision["formal_pass"] is True
    assert decision["economic_candidate"] is True
    assert decision["competing_sign_can_rescue_primary"] is False
    assert decision["forward_admission_allowed"] is False
    assert decision["live_eligible"] is False


def test_competing_peer_reviewed_sign_falsifies_but_never_rescues() -> None:
    evaluation, replays, aliases, suffix = _fixture_inputs()
    replays["sign"]["base_cost"]["net_sharpe"] = 1.6

    decision = judge(evaluation, replays, aliases, suffix)

    assert decision["formal_pass"] is False
    assert decision["economic_candidate"] is False
    assert "beats_competing_sign_control" in decision["failed_bars"]
    assert decision["forward_admission_allowed"] is False


def test_ordinary_weekly_reversal_must_not_explain_primary() -> None:
    evaluation, replays, aliases, suffix = _fixture_inputs()
    replays["reversal"]["base_cost"]["net_sharpe"] = 1.5

    decision = judge(evaluation, replays, aliases, suffix)

    assert decision["formal_pass"] is False
    assert "beats_weekly_reversal_control" in decision["failed_bars"]


def _data_gap_panel() -> FieldPanel:
    rows, names = 42, 65
    instruments = [f"c{index}" for index in range(names)]
    event_times = [1_700_000_000_000 + row * 86_400_000 for row in range(rows)]
    close = [
        [
            (100.0 + index)
            * (1.001**row)
            * (1.0 + ((row + index) % 5) * index / 100_000.0)
            for index in range(names)
        ]
        for row in range(rows)
    ]
    volume = [
        [10_000.0 * (index + 1) for index in range(names)]
        for _row in range(rows)
    ]
    close[35][64] = None
    return FieldPanel(instruments, event_times, {"close": close, "volume": volume})


def test_data_preflight_rejects_before_opening_trial_registry(
    monkeypatch, tmp_path
) -> None:
    from tools import lottery_max_research as research

    monkeypatch.setattr(research, "_research_panel", _data_gap_panel)
    monkeypatch.setattr(research.time, "time_ns", lambda: 123_000_000)
    monkeypatch.setattr(
        research,
        "production_experiment_registry",
        lambda: (_ for _ in ()).throw(AssertionError("registry must not open")),
    )
    output = tmp_path / "lottery-max-preflight-rejection.json"

    document = run(output=output, source_commit="a" * 40)

    assert output.is_file()
    assert document["decision"]["status"] == "REJECTED_DATA_INTEGRITY"
    assert document["preflight_failure"]["reason_code"] == (
        "MISSING_NON_TERMINAL_HOLDING_LABEL"
    )
    assert document["trial_registry"] == {
        "mutation_attempted": False,
        "registered_candidate_count": 0,
        "recorded_run_ids": [],
    }
    assert document["return_statistics_computed"] is False
