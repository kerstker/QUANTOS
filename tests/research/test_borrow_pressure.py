import pytest

from services.research.borrow_pressure import (
    BorrowPressureError,
    BorrowPressureObservation,
    evaluate_cross_section,
)


PREREG = 1_000
DECISION = 10_000


def _row(index: int, **overrides) -> BorrowPressureObservation:
    values = {
        "instrument_id": f"A{index}USDT",
        "decision_at_ms": DECISION,
        "borrow_knowledge_time_ms": 9_000,
        "binance_knowledge_time_ms": 9_500,
        "average_borrow_rate": 0.01 + index * 0.01,
        "estimated_borrow_rate": 0.02 + index * 0.01,
        "funding_rate": -0.0001,
        "basis_bps": -3.0,
        "trailing_24h_return": -0.03,
        "spread_bps": 2.0,
    }
    values.update(overrides)
    return BorrowPressureObservation(**values)


def test_primary_is_joint_mechanism_and_controls_are_fixed() -> None:
    selections = evaluate_cross_section(
        [_row(index) for index in range(10)],
        preregistered_at_ms=PREREG,
    )
    by_id = {item.instrument_id: item for item in selections}

    assert by_id["A9USDT"].eligible_modes == (
        "PRIMARY",
        "SIGN_CONTROL",
        "BORROW_ABLATION_CONTROL",
        "FUNDING_ABLATION_CONTROL",
    )
    assert "PRIMARY" not in by_id["A0USDT"].eligible_modes
    assert "SCARCITY_CONTROL" in by_id["A0USDT"].eligible_modes


def test_borrow_only_does_not_masquerade_as_joint_primary() -> None:
    rows = [_row(index) for index in range(10)]
    rows[-1] = _row(9, funding_rate=0.0001, basis_bps=1.0)
    selection = evaluate_cross_section(
        rows,
        preregistered_at_ms=PREREG,
    )[-1]

    assert selection.eligible_modes == ("BORROW_ABLATION_CONTROL",)
    assert selection.primary_blockers == ("NEGATIVE_FUNDING", "NEGATIVE_BASIS")


def test_knowledge_after_decision_or_before_freeze_fails_closed() -> None:
    with pytest.raises(BorrowPressureError, match="knowledge time"):
        evaluate_cross_section(
            [_row(0, binance_knowledge_time_ms=DECISION + 1)],
            preregistered_at_ms=PREREG,
        )
    with pytest.raises(BorrowPressureError, match="knowledge time"):
        evaluate_cross_section(
            [_row(0, borrow_knowledge_time_ms=PREREG)],
            preregistered_at_ms=PREREG,
        )


def test_cross_section_identity_and_clock_are_coherent() -> None:
    with pytest.raises(BorrowPressureError, match="duplicate"):
        evaluate_cross_section(
            [_row(0), _row(0)],
            preregistered_at_ms=PREREG,
        )
    with pytest.raises(BorrowPressureError, match="mixed decision clocks"):
        evaluate_cross_section(
            [_row(0), _row(1, decision_at_ms=DECISION + 1)],
            preregistered_at_ms=PREREG,
        )
