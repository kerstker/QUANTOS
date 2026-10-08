"""A recovered mark must not erase the preceding atomic funding loss."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.execution.perpetual_ledger_v2 import (
    EventOrderViolation, FundingEvent, LinearPerpetualLedger, MarginBracket, MarkEvent,
)
from services.execution.perpetual_simulation import LinearPerpetualSimulationSession, OrderIntent, SimulationCostModel
from services.research.gold_loss_control import GoldResearchLossControl
from tests.execution.test_perpetual_ledger_v2 import _spec, _trade
from tests.research.test_gold_v2_strategy import F, T, execute, frame


def enter_one_lot(ctx):
    return (OrderIntent('entry-' + str(ctx.timestamp), 'XAUUSDT', D('.1')),) if not ctx.positions and not ctx.open_orders else ()


def recovered_settlement(*, delayed=False):
    value = frame(1000, price='115')
    return replace(value,
        market_events=(FundingEvent(T + 1000, 'XAUUSDT', D('.1'), D('100')), *value.market_events),
        funding_available_at=T + 1010 if delayed else None)


def test_atomic_funding_loss_latches_before_same_frame_mark_recovery():
    control = GoldResearchLossControl(enter_one_lot, initial_equity=D('100'))
    result = execute((frame(0), frame(10), recovered_settlement(),
                      frame(1010, price='115'), frame(1020, price='115')), control,
        initial_collateral=D('100'), costs=SimulationCostModel(fee_rate=D('.45')),
        risk_policy=control.risk, risk_policy_identity=control.identity)
    report = control.report(result)
    assert result['orders'][0]['status'] == 'FILLED'
    assert D(result['orders'][0]['filled_quantity']) == D('.1')
    assert result['reconciliation']['funding'] == '-1'
    assert report['trigger'] is not None
    assert report['trigger']['equity'] == '94.5'
    assert report['trigger']['timestamp'] == T + 1000
    point = result['equity_history'][report['trigger']['sequence']]
    assert point['phase'] == 'FUNDING'
    assert result['market_history_policy'] == 'ATOMIC_MARKET_PHASE_EQUITY_V1'
    assert report['status'] == 'STOPPED_FLAT'
    assert result['orders'][1]['created_at'] == T + 1000
    assert result['orders'][1]['filled_at'] == T + 1010
    assert result['reconciliation']['fee'] == '9.675'
    assert result['reconciliation']['price_pnl'] == '1.5'
    assert result['reconciliation']['ending_equity'] == '90.825'
    assert result['reconciliation']['residual'] == '0'
    assert result['authoritative'] is result['full_research_risk_9_of_9'] is False


def test_delayed_funding_phase_is_not_exposed_to_stop_until_receipt():
    calls = []
    def strategy(ctx):
        calls.append(ctx.timestamp)
        return enter_one_lot(ctx)
    control = GoldResearchLossControl(strategy, initial_equity=D('100'))
    rule = replace(F['_rule'](), rule_id='synthetic-gold-rules',
                   spec=replace(F['_spec'](), instrument_id='XAUUSDT'))
    common = dict(release_identity={'commit': 'f' * 40}, dataset_identity={'dataset': 'synthetic'},
        strategy_identity={'strategy': 'atomic-phase-regression.v1'}, contract_rules=(rule,),
        funding_policies=(replace(F['_funding_policy'](), instrument_id='XAUUSDT',
                                  settlement_times=(T + 1000,)),),
        initial_collateral=D('100'), costs=SimulationCostModel(fee_rate=D('.45')))
    session = LinearPerpetualSimulationSession(**common, strategy=control,
        risk_policy=control.risk, risk_policy_identity=control.identity)
    session.advance((frame(0), frame(10), recovered_settlement(delayed=True)))
    assert control.trigger is None
    assert calls == [T, T + 10]
    with pytest.raises(ValueError, match='FUNDING_RECEIPT_PENDING'):
        session.report()
    session.advance((frame(1010, price='115'), frame(1020, price='115')))
    result = session.report()
    assert control.trigger is not None
    assert result['orders'][0]['status'] == 'FILLED'
    assert result['reconciliation']['funding'] == '-1'
    assert control.trigger['equity'] == '94.5'
    assert control.trigger['timestamp'] == T + 1000
    assert result['orders'][1]['created_at'] == T + 1010
    assert result['orders'][1]['filled_at'] == T + 1020
    assert control.report(result)['status'] == 'STOPPED_FLAT'


@pytest.mark.parametrize('phase', ['FUNDING', 'MARK'])
def test_observer_sees_complete_account_phase_without_cross_instrument_order_dependence(phase):
    brackets = (MarginBracket(D('0'), None, D('.10'), D('.05'), D('0')),)
    events = ((FundingEvent(2, 'A', D('.10'), D('100')), FundingEvent(2, 'B', D('-.10'), D('100')))
              if phase == 'FUNDING' else (MarkEvent(2, 'A', D('88')), MarkEvent(2, 'B', D('112'))))
    states = []
    for ordered in (events, tuple(reversed(events))):
        ledger = LinearPerpetualLedger((_spec('A', liquidation_fee_rate=D('0'), brackets=brackets),
                                        _spec('B', liquidation_fee_rate=D('0'), brackets=brackets)),
                                       initial_collateral=D('1000'))
        ledger.apply_event(_trade(1, '50', '100', instrument_id='A'))
        ledger.apply_event(_trade(1, '50', '100', instrument_id='B'))
        before = ledger.state_as_canonical_dict()
        observed = []
        results = ledger.process_events(ordered, phase_observer=lambda timestamp, name:
            observed.append((timestamp, name, ledger.account_equity())))
        assert results == (None, None)
        assert observed == [(2, phase, D('1000'))]
        assert ledger.liquidations == ()
        assert ledger.position('A') is not None and ledger.position('B') is not None
        states.append(ledger.state_as_canonical_dict())
        assert states[-1]['account_equity'] == before['account_equity']
    assert states[0] == states[1]


def test_invalid_observer_is_rejected_before_trade_or_market_mutation():
    ledger = LinearPerpetualLedger((_spec(),), initial_collateral=D('1000'))
    before = ledger.state_as_canonical_dict()
    with pytest.raises(TypeError, match='phase_observer must be callable'):
        ledger.process_events((_trade(1, '1', '100'), MarkEvent(2, 'BTCUSDT', D('110'))),
                              phase_observer=object())
    assert ledger.state_as_canonical_dict() == before


def test_failed_atomic_group_is_not_observed_and_committed_cash_cannot_be_vetoed():
    ledger = LinearPerpetualLedger((_spec(),), initial_collateral=D('1000'))
    ledger.apply_event(_trade(1, '1', '100'))
    before = ledger.state_as_canonical_dict()
    event = FundingEvent(2, 'BTCUSDT', D('.1'), D('100'))
    observed = []
    with pytest.raises(EventOrderViolation, match='duplicate'):
        ledger.process_events((event, event), phase_observer=lambda *args: observed.append(args))
    assert observed == []
    assert ledger.state_as_canonical_dict() == before

    def fail_after_commit(timestamp, phase):
        observed.append((timestamp, phase, ledger.account_equity()))
        raise RuntimeError('synthetic history storage failure')

    with pytest.raises(RuntimeError, match='synthetic history storage failure'):
        ledger.process_events((event,), phase_observer=fail_after_commit)
    assert observed == [(2, 'FUNDING', D('990'))]
    committed = ledger.state_as_canonical_dict()
    with pytest.raises(EventOrderViolation, match='already closed'):
        ledger.process_events((event,), phase_observer=lambda *args: observed.append(args))
    assert ledger.state_as_canonical_dict() == committed
    assert len(observed) == 1


def test_history_storage_failure_poisoned_session_cannot_publish_committed_partial_phase(monkeypatch):
    from tests.execution.test_perpetual_simulation import frame as btc_frame
    from tests.execution.test_perpetual_simulation_session import session

    def strategy(ctx):
        return (OrderIntent('entry', 'BTCUSDT', D('.1')),) if ctx.timestamp == T else ()

    instance = session(strategy)
    instance.advance((btc_frame(0), btc_frame(10)))
    account = instance._state['account']
    original = account.checkpoint

    def failing_history(timestamp, phase):
        if phase == 'FUNDING':
            raise OSError('synthetic history storage failure')
        original(timestamp, phase)

    monkeypatch.setattr(account, 'checkpoint', failing_history)
    with pytest.raises(OSError, match='synthetic history storage failure'):
        instance.advance((btc_frame(1000, funding=(FundingEvent(T + 1000, 'BTCUSDT', D('.1'), D('100')),)),))
    assert account.ledger.account_equity() == D('999')
    for operation in (instance.report, instance.report_stream, instance.checkpoint):
        with pytest.raises(ValueError, match='SESSION_FAILED'):
            operation()
