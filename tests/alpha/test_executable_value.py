import math

import pytest

from services.alpha.executable_value import (
    ExecutableValueInputs,
    ExecutableValuePolicy,
    ExecutableValueStatus,
    evaluate_executable_value,
    rank_executable_candidates,
)


_HASHES = {
    name: (f"{index:064x}")
    for index, name in enumerate(
        (
            "incremental_net_return",
            "double_cost_incremental_net_return",
            "orthogonality",
            "execution",
            "capacity",
            "turnover",
            "tail_risk",
            "independent_bets",
        ),
        1,
    )
}


def _inputs(**updates) -> ExecutableValueInputs:
    values = {
        "candidate_id": "mechanism_a",
        "reference_aum": 1_000_000.0,
        "incremental_net_return": 0.12,
        "double_cost_incremental_net_return": 0.08,
        "orthogonality": 0.80,
        "execution_confidence": 0.75,
        "capacity_low": 3_000_000.0,
        "annual_turnover": 12.0,
        "tail_loss_fraction": 0.10,
        "independent_bet_count": 400,
        "evidence_hashes": _HASHES,
    }
    values.update(updates)
    return ExecutableValueInputs(**values)


def test_executable_value_is_conservative_incremental_profit_not_sharpe() -> None:
    result = evaluate_executable_value(_inputs(), ExecutableValuePolicy())

    assert result.status is ExecutableValueStatus.PASS
    assert result.conservative_incremental_profit_usd == 80_000.0
    assert result.capacity_multiplier == 1.0
    assert result.turnover_penalty == 1.5
    assert result.tail_penalty == 1.5
    assert result.score_usd == pytest.approx(21_333.333333333332)
    assert "sharpe" not in result.to_dict()


def test_missing_execution_or_provenance_is_incomplete_not_zero_filled() -> None:
    missing_execution = evaluate_executable_value(
        _inputs(execution_confidence=None), ExecutableValuePolicy()
    )
    missing_hash = evaluate_executable_value(
        _inputs(evidence_hashes={k: v for k, v in _HASHES.items() if k != "capacity"}),
        ExecutableValuePolicy(),
    )

    assert missing_execution.status is ExecutableValueStatus.INCOMPLETE
    assert missing_execution.score_usd is None
    assert "EXECUTION_CONFIDENCE_MISSING" in missing_execution.reasons
    assert missing_hash.status is ExecutableValueStatus.INCOMPLETE
    assert "CAPACITY_EVIDENCE_HASH_MISSING" in missing_hash.reasons


def test_double_cost_loss_low_capacity_and_too_few_bets_fail_closed() -> None:
    result = evaluate_executable_value(
        _inputs(
            double_cost_incremental_net_return=-0.01,
            capacity_low=400_000.0,
            independent_bet_count=40,
        ),
        ExecutableValuePolicy(),
    )

    assert result.status is ExecutableValueStatus.FAIL
    assert set(result.reasons) >= {
        "DOUBLE_COST_INCREMENTAL_PROFIT_NON_POSITIVE",
        "CAPACITY_RATIO_BELOW_MIN",
        "INDEPENDENT_BET_COUNT_BELOW_MIN",
    }
    assert result.score_usd is not None and result.score_usd < 0.0


def test_score_rewards_orthogonality_execution_and_capacity_but_penalizes_risk() -> None:
    baseline = evaluate_executable_value(_inputs(), ExecutableValuePolicy())
    weak = evaluate_executable_value(
        _inputs(
            candidate_id="weak",
            orthogonality=0.50,
            execution_confidence=0.55,
            capacity_low=1_000_000.0,
            annual_turnover=24.0,
            tail_loss_fraction=0.20,
        ),
        ExecutableValuePolicy(
            min_orthogonality=0.40,
            min_execution_confidence=0.50,
            max_tail_loss_fraction=0.25,
        ),
    )

    assert baseline.score_usd is not None
    assert weak.score_usd is not None
    assert baseline.score_usd > weak.score_usd


def test_ranking_excludes_incomplete_and_never_promotes_failed_rows() -> None:
    policy = ExecutableValuePolicy()
    passed = evaluate_executable_value(_inputs(candidate_id="passed"), policy)
    failed = evaluate_executable_value(
        _inputs(candidate_id="failed", double_cost_incremental_net_return=-0.01),
        policy,
    )
    incomplete = evaluate_executable_value(
        _inputs(candidate_id="incomplete", execution_confidence=None), policy
    )

    ranked = rank_executable_candidates((failed, incomplete, passed))
    assert [row.candidate_id for row in ranked] == ["passed", "failed", "incomplete"]
    assert ranked[0].rank == 1
    assert ranked[1].rank is None
    assert ranked[2].rank is None


@pytest.mark.parametrize(
    "field,value",
    (
        ("reference_aum", True),
        ("incremental_net_return", math.inf),
        ("orthogonality", 1.1),
        ("execution_confidence", -0.1),
        ("capacity_low", 0.0),
        ("annual_turnover", -1.0),
        ("tail_loss_fraction", -0.1),
        ("independent_bet_count", True),
    ),
)
def test_invalid_economic_inputs_are_rejected(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _inputs(**{field: value})
