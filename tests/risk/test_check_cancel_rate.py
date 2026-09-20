from types import SimpleNamespace

import pytest

from quantos.contracts.rejection import RejectionReason as R
from services.risk.pre_trade.checks import check_cancel_rate
from services.risk.pre_trade.config import RiskLimits


def _ctx(statuses, *, position=0, delta=1):
    account = SimpleNamespace(position_qty=lambda _instrument: position)
    return SimpleNamespace(
        account=account,
        recent_order_statuses=tuple(statuses),
        signed_delta=lambda _intent: delta,
    )


_INTENT = SimpleNamespace(instrument_id="BTC")


def test_cancel_rate_passes_below_minimum_sample_size():
    limits = RiskLimits(min_cancel_rate_samples=20, max_cancel_rate=0.80)
    assert check_cancel_rate(_INTENT, _ctx(["CANCELED"] * 19), limits) == ()


def test_cancel_rate_rejects_when_ratio_exceeds_limit():
    limits = RiskLimits(min_cancel_rate_samples=10, max_cancel_rate=0.80)
    statuses = ["CANCELED"] * 9 + ["FILLED"]
    assert check_cancel_rate(_INTENT, _ctx(statuses), limits) == (
        R.TURNOVER_LIMIT_EXCEEDED,
    )


def test_cancel_rate_allows_ratio_at_limit():
    limits = RiskLimits(min_cancel_rate_samples=10, max_cancel_rate=0.80)
    statuses = ["CANCELED"] * 8 + ["FILLED"] * 2
    assert check_cancel_rate(_INTENT, _ctx(statuses), limits) == ()


def test_cancel_rate_fails_closed_on_unknown_status():
    limits = RiskLimits(min_cancel_rate_samples=2, max_cancel_rate=0.80)
    assert check_cancel_rate(_INTENT, _ctx(["FILLED", "MYSTERY"]), limits) == (
        R.TURNOVER_LIMIT_EXCEEDED,
    )


def test_cancel_rate_does_not_block_proven_exposure_reduction():
    limits = RiskLimits(min_cancel_rate_samples=2, max_cancel_rate=0.20)
    ctx = _ctx(["CANCELED", "CANCELED"], position=10, delta=-5)
    assert check_cancel_rate(_INTENT, ctx, limits) == ()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_cancel_rate": -0.01},
        {"max_cancel_rate": 1.01},
        {"max_cancel_rate": float("nan")},
        {"min_cancel_rate_samples": 0},
        {"min_cancel_rate_samples": float("nan")},   # NaN < 1 is False -> old floor bypassed
    ],
)
def test_cancel_rate_limits_reject_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        RiskLimits(**kwargs)
