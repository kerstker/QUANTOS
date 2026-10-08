from services.alpha.executable_value import ExecutableValueStatus
from services.research.portfolio_evidence import (
    build_portfolio_executable_inputs,
    evaluate_portfolio_executable_value,
)


_HASHES = {
    name: f"{index:064x}"
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


def _portfolio_row() -> dict:
    return {
        "candidate_id": "crowd_mechanism",
        "status": "OK",
        "common_exposure": {
            "available": True,
            "r_squared": 0.05,
            "max_residual_corr_vs_incumbent": 0.20,
        },
        "capacity": {"available": True, "low": 3_000_000.0},
        "tca": {
            "available": True,
            "weighted_shortfall_bps": 3.3,
            "note": "SIMULATED",
        },
    }


def _metrics(**updates) -> dict:
    values = {
        "incremental_net_return": 0.12,
        "double_cost_incremental_net_return": 0.08,
        "execution_confidence": 0.75,
        "annual_turnover": 12.0,
        "tail_loss_fraction": 0.10,
        "independent_bet_count": 400,
    }
    values.update(updates)
    return values


def test_portfolio_adapter_uses_conservative_common_and_residual_orthogonality() -> None:
    inputs = build_portfolio_executable_inputs(
        _portfolio_row(),
        reference_aum=1_000_000.0,
        executable_metrics=_metrics(),
        evidence_hashes=_HASHES,
    )

    assert inputs.orthogonality == 0.80
    assert inputs.capacity_low == 3_000_000.0
    result = evaluate_portfolio_executable_value(
        _portfolio_row(),
        reference_aum=1_000_000.0,
        executable_metrics=_metrics(),
        evidence_hashes=_HASHES,
    )
    assert result.status is ExecutableValueStatus.PASS


def test_legacy_portfolio_row_is_incomplete_under_new_objective() -> None:
    result = evaluate_portfolio_executable_value(
        _portfolio_row(),
        reference_aum=1_000_000.0,
        executable_metrics={},
        evidence_hashes={},
    )

    assert result.status is ExecutableValueStatus.INCOMPLETE
    assert set(result.reasons) >= {
        "INCREMENTAL_NET_RETURN_MISSING",
        "DOUBLE_COST_INCREMENTAL_NET_RETURN_MISSING",
        "EXECUTION_CONFIDENCE_MISSING",
        "TURNOVER_MISSING",
        "TAIL_RISK_MISSING",
        "INDEPENDENT_BET_COUNT_MISSING",
    }


def test_simulated_tca_never_fabricates_execution_confidence() -> None:
    inputs = build_portfolio_executable_inputs(
        _portfolio_row(),
        reference_aum=1_000_000.0,
        executable_metrics=_metrics(execution_confidence=None),
        evidence_hashes=_HASHES,
    )

    assert inputs.execution_confidence is None
