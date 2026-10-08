"""P0-1 — portfolio values must be finite at construction.

AccountRiskState derives gross/net exposure by summing Position.market_value, so a
single non-finite nested value poisons the aggregate: every `>` comparison in the
pre-trade checks then evaluates False and the gate approves. Pydantic applies the
NESTED model's own config, so the guard belongs on these models
(services/risk/pre_trade/context.py `_FROZEN_FINITE` is the precedent).
No extra='forbid' here — that would be a separate behaviour change.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from quantos.contracts.portfolio import (
    AttributionReport,
    PortfolioSnapshot,
    Position,
    Signal,
)
from services.risk.pre_trade import AccountRiskState

_NAN, _INF, _NEG_INF = float("nan"), float("inf"), float("-inf")
_NON_FINITE = (_NAN, _INF, _NEG_INF)
_IDS = ("nan", "inf", "-inf")


@pytest.mark.parametrize("value", _NON_FINITE, ids=_IDS)
def test_position_market_value_must_be_finite(value):
    with pytest.raises(ValidationError):
        Position(instrument_id="AAA", quantity=1, avg_cost=1.0, market_value=value)


@pytest.mark.parametrize("value", _NON_FINITE, ids=_IDS)
def test_signal_value_must_be_finite(value):
    with pytest.raises(ValidationError):
        Signal(signal_id="s1", alpha_id="a1", instrument_id="AAA", value=value)


@pytest.mark.parametrize("value", _NON_FINITE, ids=_IDS)
def test_portfolio_snapshot_net_exposure_must_be_finite(value):
    with pytest.raises(ValidationError):
        PortfolioSnapshot(portfolio_id="p1", as_of=0, net_exposure=value)


@pytest.mark.parametrize("value", _NON_FINITE, ids=_IDS)
def test_attribution_report_total_pnl_must_be_finite(value):
    with pytest.raises(ValidationError):
        AttributionReport(portfolio_id="p1", total_pnl=value)


@pytest.mark.parametrize("value", _NON_FINITE, ids=_IDS)
def test_account_risk_state_rejects_non_finite_position_market_value(value):
    """The gate must never see a non-finite gross/net exposure."""
    with pytest.raises(ValidationError):
        AccountRiskState(account_id="acct1",
                         positions=(Position(instrument_id="AAA", quantity=1,
                                             avg_cost=1.0, market_value=value),))


def test_finite_values_including_shorts_stay_accepted():
    position = Position(instrument_id="AAA", quantity=-5, avg_cost=0.0, market_value=-500.0)
    account = AccountRiskState(account_id="acct1", cash=1_000.0, equity=1_000.0,
                               positions=(position,))
    assert position.market_value == -500.0
    assert account.gross_exposure == 500.0 and account.net_exposure == -500.0
