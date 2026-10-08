"""A reported fill is either replayed exactly or left unresolved, never resized."""
from decimal import Decimal as D

import pytest

from services.execution import perpetual_ledger_v2 as v2
from tests.execution.test_perpetual_ledger_v2 import _spec, _capacity


def fill(**changes):
    args = dict(timestamp=100, instrument_id="BTCUSDT", quantity_delta=D("0.001"),
                execution_price=D("100"), margin_mode=v2.MarginMode.CROSS,
                capacity=_capacity(), fee=D("0.00001234"), order_id="frozen-1",
                receipt_sha256="a" * 64)
    return v2.ReportedFillReplayEvent(**(args | changes))


def test_reported_subminimum_slice_keeps_exact_fee_and_source_identity():
    ledger = v2.LinearPerpetualLedger([_spec()], initial_collateral=D("1000"))
    result = ledger.apply_event(fill())
    assert result.filled_quantity == D("0.001")
    assert ledger.state_as_canonical_dict()["wallet_balance"] == "999.99998766"
    record = ledger.event_records[-1]
    assert record["applied_fee"] == "0.00001234"
    assert record["quantity_basis"] == "REPORTED_FILL_TECHNICAL_REPLAY_V1"
    assert record["receipt_sha256"] == "a" * 64
    assert "fee_rate" not in record


@pytest.mark.parametrize("action", list(v2.CapacityAction))
def test_capacity_cannot_truncate_reported_fill_or_its_commission(action):
    ledger = v2.LinearPerpetualLedger([_spec()], initial_collateral=D("1000"))
    before = ledger.state_as_canonical_dict()
    capacity = _capacity(adv=D("0.5"), rate=D("0.1"), action=action)
    with pytest.raises(v2.ReportedFillReplayError):
        ledger.apply_event(fill(capacity=capacity))
    assert ledger.state_as_canonical_dict() == before
    assert ledger.event_records == ()


def test_reported_replay_cannot_add_estimated_costs_or_admission_callback():
    with pytest.raises(ValueError):
        fill(spread_cost=D("1"))
    ledger = v2.LinearPerpetualLedger([_spec()], initial_collateral=D("1000"))
    with pytest.raises(TypeError):
        ledger.apply_event(fill(), trade_guard=lambda result: None)
    assert ledger.event_records == ()
