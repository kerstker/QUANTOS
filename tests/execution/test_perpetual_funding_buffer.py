from decimal import Decimal as D

import pytest

from services.execution import perpetual_simulation as engine
from services.execution.perpetual_ledger_v2 import FundingEvent
from tests.execution.test_perpetual_simulation import T, frame
from tests.execution.test_perpetual_simulation_session import configuration


def event(rate='.01'):
    return FundingEvent(T + 1000, 'BTCUSDT', D(rate), D('100'))


def test_online_frames_wait_for_funding_then_continue_same_account():
    seen = []
    def strategy(ctx):
        seen.append(ctx.timestamp)
        if ctx.timestamp == T:
            return (engine.OrderIntent('entry', 'BTCUSDT', D('.1')),)
        if ctx.timestamp == T + 1100:
            return (engine.OrderIntent('exit', 'BTCUSDT', D('-.1'), reduce_only=True),)
        return ()
    s = engine.BufferedFundingSimulationSession(**configuration(strategy))
    s.advance((frame(0), frame(10), frame(1000), frame(1050)))
    assert seen == [T, T + 10]
    assert s.status()['buffered_frames'] == 2
    with pytest.raises(ValueError, match='FUNDING_OBSERVATION_PENDING'):
        s.report()
    s.observe_funding(event(), available_at=T + 1100)
    assert seen == [T, T + 10]
    with pytest.raises(ValueError, match='FUNDING_RECEIPT_PENDING'):
        s.report()
    s.advance((frame(1100), frame(1110)))
    report = s.report()
    assert report['reconciliation']['funding'] == '-0.1'
    assert report['reconciliation']['residual'] == '0'
    assert report['ending_state']['positions'] == {}
    assert len(report['orders']) == 2
    assert s.status()['buffered_frames'] == 0


def test_funding_between_quote_frames_is_inserted_at_actual_clock():
    s = engine.BufferedFundingSimulationSession(**configuration())
    s.advance((frame(0), frame(1010)))
    s.observe_funding(event(), available_at=T + 1020)
    s.advance((frame(1030),))
    assert s.report()['reconciliation']['funding'] == '0'
    assert s.status()['processed_through'] == T + 1030


def test_repeated_funding_does_not_charge_twice_or_move_first_receipt():
    s = engine.BufferedFundingSimulationSession(**configuration())
    s.advance((frame(0), frame(1100)))
    s.observe_funding(event(), available_at=T + 1050)
    before = s.report()
    s.observe_funding(event(), available_at=T + 1090)
    assert s.report() == before
    with pytest.raises(ValueError, match='CONFLICTING_FUNDING_OBSERVATION'):
        s.observe_funding(event('.02'), available_at=T + 1100)
    with pytest.raises(ValueError, match='BUFFERED_SIMULATION_FAILED'):
        s.report()


def test_unknown_settlement_does_not_rewrite_known_schedule():
    s = engine.BufferedFundingSimulationSession(**configuration())
    s.advance((frame(0),))
    with pytest.raises(ValueError, match='UNEXPECTED_FUNDING_SETTLEMENT'):
        s.observe_funding(FundingEvent(T + 1001, 'BTCUSDT', D('.01'), D('100')),
                          available_at=T + 1100)


def test_buffer_budget_stops_without_silently_dropping_frames():
    s = engine.BufferedFundingSimulationSession(max_buffered_frames=2, **configuration())
    s.advance((frame(0),))
    with pytest.raises(ValueError, match='FUNDING_BUFFER_BUDGET'):
        s.advance((frame(1000), frame(1010), frame(1020)))


def test_buffered_result_equals_explicit_original_engine_replay():
    from dataclasses import replace
    def strategy(ctx):
        return (engine.OrderIntent('entry', 'BTCUSDT', D('.1')), ) if ctx.timestamp == T else ()
    raw = (frame(0), frame(10), frame(1000), frame(1050), frame(1100))
    buffered = engine.BufferedFundingSimulationSession(**configuration(strategy))
    buffered.advance(raw)
    buffered.observe_funding(event(), available_at=T + 1100)
    expected = engine.LinearPerpetualSimulationSession(**configuration(strategy))
    expected.advance((raw[0], raw[1], replace(raw[2],
        market_events=(event(), *raw[2].market_events), funding_available_at=T + 1100), *raw[3:]))
    assert buffered.report() == expected.report()


def test_funding_can_arrive_before_later_quote_without_early_callback():
    seen = []
    s = engine.BufferedFundingSimulationSession(**configuration(lambda ctx: seen.append(ctx.timestamp) or ()))
    s.advance((frame(0),))
    s.observe_funding(event(), available_at=T + 1100)
    s.advance((frame(1000), frame(1050)))
    assert seen == [T]
    assert s.status()['status'] == 'WAITING_RECEIPT_FRAME'
    s.advance((frame(1100),))
    assert seen == [T, T + 1100]


def test_disordered_held_frames_fail_instead_of_reordering_input():
    s = engine.BufferedFundingSimulationSession(**configuration())
    s.advance((frame(0), frame(1010)))
    with pytest.raises(ValueError, match='BUFFERED_FRAME_ORDER'):
        s.advance((frame(1005),))
    with pytest.raises(ValueError, match='BUFFERED_SIMULATION_FAILED'):
        s.observe_funding(event(), available_at=T + 1020)
