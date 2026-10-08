"""Received forecast epochs retain the original funding embargo and blockers."""
from dataclasses import asdict, replace
from decimal import Decimal as D

from services.execution.perpetual_ledger_v2 import FundingEvent
from services.execution.perpetual_simulation import ForecastFundingPolicySnapshot, LinearPerpetualSimulationSession
from tests.execution.test_perpetual_environment_epochs import configuration, environment, tape, trade
from tests.execution.test_perpetual_simulation import T, frame


def inputs():
    config = configuration(trade)
    config['funding_policies'] = (ForecastFundingPolicySnapshot(
        **asdict(config['funding_policies'][0]), settlement_window_ms=2),)
    prefix = (frame(0), frame(10), replace(frame(15), funding_pause_until=T+18),
        replace(frame(16, funding=(FundingEvent(T+16, 'BTCUSDT', D('.01'), D('100')),)),
            funding_available_at=T+18), frame(18))
    return config, (*prefix, *tape()[3:]), dict(first_frame=len(prefix), **environment())


def test_forecast_then_known_epoch_retains_original_position_and_timing_assumption():
    config, frames, update = inputs()
    seen = []
    original = config['strategy']
    def observed(ctx):
        seen.append(ctx.timestamp)
        return original(ctx)
    session = LinearPerpetualSimulationSession(**{**config, 'strategy':observed})
    session.advance(frames[:update['first_frame']])
    session.advance_environment(frames[update['first_frame']:],
        **{k:v for k,v in update.items() if k != 'first_frame'})
    result = session.report()
    assert T+15 not in seen and T+16 not in seen and T+18 in seen
    assert result['reconciliation']['funding'] == '-0.2'
    assert 'FORECAST_TIMING_BOUND_UNVERIFIED' in result['authority_blockers']
    assert result['funding_timing_assumptions'][0]['settlement_window_ms'] == 2
    assert result['environment_policy'] == 'CAUSAL_ENVIRONMENT_CANCEL_PENDING_RETAIN_ACCOUNT_V2'
    assert len([e for e in result['ledger_events'] if e['event_type'] == 'FUNDING_SETTLEMENT']) == 2
    assert result['authoritative'] is result['submission_enabled'] is False


def test_forecast_epoch_checkpoint_recomputes_every_original_clock_and_policy():
    from services.execution.perpetual_simulation import run_linear_perpetual_simulation
    config, frames, update = inputs()
    original = run_linear_perpetual_simulation(frames=frames, environment_updates=[update], **config)
    session = LinearPerpetualSimulationSession(**config)
    session.advance(frames[:update['first_frame']])
    session.advance_environment(frames[update['first_frame']:],
        **{k:v for k,v in update.items() if k != 'first_frame'})
    restored = LinearPerpetualSimulationSession.restore(session.checkpoint(), frames=frames,
        environment_updates=[update], **config)
    assert restored.report() == original == session.report()
