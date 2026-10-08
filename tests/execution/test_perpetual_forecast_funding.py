from dataclasses import asdict, replace
from decimal import Decimal as D

import pytest

from services.execution import perpetual_simulation as engine
from services.execution.perpetual_ledger_v2 import FundingEvent
from tests.execution.test_perpetual_simulation import T, frame
from tests.execution.test_perpetual_simulation_session import configuration


def config(strategy=lambda ctx: ()):
    value = configuration(strategy)
    original = value['funding_policies'][0]
    value['funding_policies'] = (engine.ForecastFundingPolicySnapshot(
        **asdict(original), settlement_window_ms=1000),)
    return value


def test_two_millisecond_actual_offset_preserved_with_nominal_embargo():
    seen = []
    def strategy(ctx):
        seen.append(ctx.timestamp)
        if ctx.timestamp == T:
            return (engine.OrderIntent('entry', 'BTCUSDT', D('.1')),)
        if ctx.timestamp == T + 10:
            return (engine.OrderIntent('rest', 'BTCUSDT', D('.1'), limit_price=D('95')),)
        return ()
    s = engine.BufferedFundingSimulationSession(**config(strategy))
    s.advance((frame(0), frame(10), frame(1000, '94'), frame(1001, '94'), frame(1010)))
    assert seen == [T, T + 10]
    event = FundingEvent(T + 1002, 'BTCUSDT', D('.01'), D('100'))
    s.observe_funding(event, available_at=T + 1100)
    assert seen == [T, T + 10]
    s.advance((frame(1100),))
    report = s.report()
    assert report['reconciliation']['funding'] == '-0.1'
    assert 'FORECAST_TIMING_BOUND_UNVERIFIED' in report['authority_blockers']
    assert report['funding_timing_assumptions'][0]['settlement_window_ms'] == 1000
    resting = next(o for o in report['orders'] if o['order_id'] == 'rest')
    assert resting['status'] == 'CANCELLED' and resting['filled_quantity'] == '0'
    cancellation = next(e for e in report['order_events']
                        if e['order_id'] == 'rest' and e['status'] == 'CANCELLED')
    assert cancellation['timestamp'] == T + 1000
    assert any(point['timestamp'] == T + 1002 for point in report['equity_history'])


def test_waits_for_quote_watermark_to_cover_actual_event():
    s = engine.BufferedFundingSimulationSession(**config())
    s.advance((frame(0), frame(1000)))
    s.observe_funding(FundingEvent(T + 1002, 'BTCUSDT', D('.01'), D('100')), available_at=T + 1100)
    assert s.status()['processed_through'] == T
    s.advance((frame(1100),))
    assert s.report()['reconciliation']['funding'] == '0'


def test_outside_explicit_window_is_rejected():
    s = engine.BufferedFundingSimulationSession(**config())
    s.advance((frame(0),))
    with pytest.raises(ValueError, match='UNEXPECTED_FUNDING_SETTLEMENT'):
        s.observe_funding(FundingEvent(T + 2001, 'BTCUSDT', D('.01'), D('100')), available_at=T + 2100)


def test_direct_engine_cannot_skip_nominal_embargo():
    s = engine.LinearPerpetualSimulationSession(**config())
    event = FundingEvent(T + 1002, 'BTCUSDT', D('.01'), D('100'))
    with pytest.raises(ValueError, match='FORECAST_FUNDING_EMBARGO_REQUIRED'):
        s.advance((frame(0), replace(frame(1002, funding=(event,)), funding_available_at=T + 1100), frame(1100)))


def test_forecast_assumption_is_bound_and_not_an_exact_schedule_claim():
    policy = config()['funding_policies'][0].as_canonical_dict()
    assert policy['settlement_window_ms'] == 1000
    assert policy['timing_policy'] == 'RESEARCH_FORECAST_WINDOW_NOT_VENUE_GUARANTEE'


@pytest.mark.parametrize('width', [0, 1001, True])
def test_unbounded_or_invalid_timing_assumption_rejected(width):
    original = configuration()['funding_policies'][0]
    with pytest.raises(ValueError, match='INVALID_FORECAST_SETTLEMENT_WINDOW'):
        engine.ForecastFundingPolicySnapshot(**asdict(original), settlement_window_ms=width)


def test_multiple_actual_events_cannot_be_hidden_in_one_forecast_window():
    s = engine.BufferedFundingSimulationSession(**config())
    s.advance((frame(0),))
    s.observe_funding(FundingEvent(T + 1001, 'BTCUSDT', D('.01'), D('100')), available_at=T + 1100)
    with pytest.raises(ValueError, match='CONFLICTING_FUNDING_OBSERVATION'):
        s.observe_funding(FundingEvent(T + 1002, 'BTCUSDT', D('.01'), D('100')), available_at=T + 1100)


def test_start_inside_uncertain_window_rejected():
    s = engine.LinearPerpetualSimulationSession(**config())
    with pytest.raises(ValueError, match='SIMULATION_STARTS_INSIDE_FORECAST_WINDOW'):
        s.advance((frame(1001),))


def test_overlapping_instrument_windows_do_not_advance_past_missing_receipt():
    value = config()
    btc_rule = value['contract_rules'][0]
    eth_rule = replace(btc_rule, rule_id='eth-rules', spec=replace(btc_rule.spec, instrument_id='ETHUSDT'))
    btc_policy = value['funding_policies'][0]
    eth_policy = replace(btc_policy, instrument_id='ETHUSDT', settlement_times=(T + 1001,))
    value.update(contract_rules=(btc_rule, eth_rule), funding_policies=(btc_policy, eth_policy))
    s = engine.BufferedFundingSimulationSession(**value)
    s.advance((frame(0), frame(1010)))
    s.observe_funding(FundingEvent(T + 1002, 'BTCUSDT', D('.01'), D('100')), available_at=T + 1100)
    assert s.status()['processed_through'] == T
    s.observe_funding(FundingEvent(T + 1001, 'ETHUSDT', D('.01'), D('100')), available_at=T + 1100)
    s.advance((frame(1100),))
    assert s.report()['reconciliation']['residual'] == '0'


def test_exact_multi_instrument_schedule_keeps_original_event_order_and_hash():
    value = configuration()
    btc_rule = value['contract_rules'][0]
    eth_rule = replace(btc_rule, rule_id='eth-rules', spec=replace(btc_rule.spec, instrument_id='ETHUSDT'))
    btc_policy = value['funding_policies'][0]
    value.update(contract_rules=(btc_rule, eth_rule), funding_policies=(btc_policy,
        replace(btc_policy, instrument_id='ETHUSDT')))
    events = tuple(FundingEvent(T + 1000, name, D('.01'), D('100')) for name in ('BTCUSDT', 'ETHUSDT'))
    raw = (frame(0), frame(1000), frame(1100))
    s = engine.BufferedFundingSimulationSession(**value)
    s.advance(raw)
    for event in reversed(events):
        s.observe_funding(event, available_at=T + 1100)
    direct = engine.LinearPerpetualSimulationSession(**value)
    direct.advance((raw[0], replace(raw[1], market_events=(*events, *raw[1].market_events),
        funding_available_at=T + 1100), raw[2]))
    assert s.report() == direct.report()
