"""Economic regression cases from the 2026-09-05 DGX audit."""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
import runpy

import pytest

from services.execution.perpetual_ledger_v2 import (
    LinearPerpetualLedger, MarkEvent, QuantityRuleViolation, InsufficientMargin,
)

def _fixtures():
    return runpy.run_path(str(Path(__file__).with_name(
        "test_authoritative_perpetual_backtest.py")))

def _ledger_fixtures():
    return runpy.run_path(str(Path(__file__).with_name("test_perpetual_ledger_v2.py")))

def test_future_fill_does_not_change_pretrade_risk_context():
    f = _fixtures()
    contexts = []
    for price in (D("100"), D("200")):
        execution = f["_execution"]()
        events = list(execution.opportunity.executable_events)
        events[-1] = replace(events[-1], execution_price=price)
        execution = replace(execution, execution_price=price,
            opportunity=replace(execution.opportunity, executable_events=tuple(events)))
        result = f["_run"](decision=f["_decision"](execution=execution))
        contexts.append(result["detail"]["result_hash_payload"]["decision_contexts"][0])
    assert contexts[0] == contexts[1]
    assert contexts[0]["target_weights"] == {"BTCUSDT": "0.01"}

def test_runner_allows_full_close_below_opening_minimum():
    f = _fixtures()
    t = f["DECISION"]
    first = f["_execution"]()
    zero = replace(first.cost_model, fee_rate=D("0"), spread_rate=D("0"), impact_rate=D("0"))
    first = replace(first, cost_model=zero)
    close = f["_execution"](timestamp=t+50, quantity="-0.1", cost=zero)
    close = replace(close, execution_id="close-2", execution_price=D("20"),
        opportunity=replace(close.opportunity, executable_events=(
            f["ExecutableVenueEvent"](t+30, D("20")),
            f["ExecutableVenueEvent"](t+50, D("20")))))
    second = f["_decision"](execution=close, risk=f["_risk"](total_aum=992.0))
    second = replace(second, decision_id="decision-close", timestamp=t+40,
        signal_inputs=(replace(second.signal_inputs[0],
            value={"instrument_id":"BTCUSDT", "score":D("0")}),))
    events = f["_market_events"]()
    events = (
        events[0],
        replace(events[1], event=MarkEvent(t+30, "BTCUSDT", D("20"))),
        replace(events[2], event=replace(events[2].event, funding_rate=D("0"))),
        replace(events[1], event=MarkEvent(t+60, "BTCUSDT", D("20")),
            available_time=t+61, ingestion_time=t+62, source_identity="close-terminal"),
    )
    result = f["_run"](decisions=(f["_decision"](execution=first), second), market_events=events)
    payload = result["detail"]["result_hash_payload"]
    assert payload["ending_state"]["positions"] == {}
    assert D(payload["reconciliation"]["ending_equity"]) == D("992")

def test_processing_new_events_never_copies_committed_history():
    f = _ledger_fixtures()
    ledger = LinearPerpetualLedger((f["_spec"](),), initial_collateral=D("1000"))
    ledger.process_events((MarkEvent(1, "BTCUSDT", D("100")),))
    class HistoricalRecord(dict):
        def __deepcopy__(self, memo):
            pytest.fail("processing a new event revisited immutable historical records")
    ledger._events[0] = HistoricalRecord(ledger._events[0])
    ledger.process_events((MarkEvent(2, "BTCUSDT", D("101")),))
    assert ledger.account_equity() == D("1000")
    assert len(ledger._events) == 2

def test_failed_trade_restores_account_and_capacity_then_can_retry():
    f = _ledger_fixtures()
    ledger = LinearPerpetualLedger((f["_spec"](),), initial_collateral=D("10"))
    ledger.process_events((MarkEvent(1, "BTCUSDT", D("100")),))
    before = ledger.state_as_canonical_dict()
    records = ledger.event_records
    with pytest.raises(InsufficientMargin):
        ledger.apply_event(f["_trade"](2, "2", "100"))
    assert ledger.state_as_canonical_dict() == before
    assert ledger.event_records == records
    result = ledger.apply_event(f["_trade"](2, "0.1", "100"))
    assert result.status == "FILLED"

def test_reversal_cannot_hide_subminimum_new_position_in_closing_notional():
    f = _ledger_fixtures()
    ledger = LinearPerpetualLedger((f["_spec"](),), initial_collateral=D("1000"))
    ledger.apply_event(f["_trade"](1, "0.1", "100"))
    before = ledger.state_as_canonical_dict()
    with pytest.raises(QuantityRuleViolation, match="min_notional"):
        ledger.apply_event(f["_trade"](2, "-0.11", "100"))
    assert ledger.state_as_canonical_dict() == before

def test_explicit_flat_book_has_no_concentration_but_missing_book_is_rejected():
    from services.risk.research_risk.domain.models import RiskConfig
    from services.risk.research_risk.domain.risk_modules import ConcentrationRiskEngine
    gate = ConcentrationRiskEngine()
    config = RiskConfig()
    assert gate.evaluate({"BTCUSDT": 0.0}, None, config).passed
    assert not gate.evaluate({}, None, config).passed
    assert not gate.evaluate({"BTCUSDT": 0.2}, None, config).passed

def test_executable_price_cannot_charge_embedded_spread_and_impact_twice():
    f = _fixtures()
    with pytest.raises(ValueError, match="embedded"):
        replace(f["_execution"](), price_basis="EXECUTABLE_PRICE")
    original = f["_execution"]()
    executable = replace(original, cost_model=replace(original.cost_model,
        spread_rate=D("0"), impact_rate=D("0")), price_basis="EXECUTABLE_PRICE")
    result = f["_run"](decision=f["_decision"](execution=executable))
    assert result["detail"]["result_hash_payload"]["reconciliation"]["spread"] == "0"
