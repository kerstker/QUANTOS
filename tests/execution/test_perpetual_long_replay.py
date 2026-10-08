"""Explicit synthetic month: one account, received epochs and replay recovery."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.execution.perpetual_ledger_v2 import FundingEvent, MarkEvent
from services.execution.perpetual_simulation import (
    ExecutableQuote, LinearPerpetualSimulationSession, OrderIntent, SimulationFrame,
)
from tests.execution.test_perpetual_environment_epochs import configuration
from tests.execution.test_perpetual_simulation import F, T

DAY = 86_400_000
STEP = 900_000
SPAN = 30 * DAY
NAME = 'XAUUSDT'


def budget(*, epochs=2881, span=SPAN + 1):
    return dict(schema='LINEAR_PERPETUAL_REPLAY_BUDGET_V1',
                maximum_environment_segments=epochs, maximum_elapsed_ms=span)


def market(offset, *, funding=None):
    now = T + offset
    if funding is None:
        funding = offset % (8 * 3_600_000) == 0
    capacity = replace(F['_capacity'](period_index=now // DAY), instrument_id=NAME)
    return SimulationFrame(now,
        quotes=(ExecutableQuote(NAME, now, now, D('100'), D('100'), D('1'), D('1')),),
        market_events=(*( (FundingEvent(now, NAME, D('.001'), D('100')), ) if funding else ()),
                       MarkEvent(now, NAME, D('100'))), capacity_snapshots=(capacity,))


def environment(index):
    original = configuration()
    now = T + index * STEP
    quantity = '.01' if index < 1440 else '.02'
    rule = replace(original['contract_rules'][0], rule_id=f'synthetic-month-rule-{index}',
        spec=replace(original['contract_rules'][0].spec, instrument_id=NAME,
                     quantity_step=D(quantity), min_quantity=D(quantity)),
        effective_from=T - 100, effective_until=T + SPAN + 100,
        observed_time=now - 3, available_time=now - 2, ingestion_time=now - 1)
    policy = replace(original['funding_policies'][0], instrument_id=NAME,
        policy_epoch_id=f'synthetic-month-funding-{index}', coverage_start=T - 100,
        coverage_end=T + SPAN + 100, settlement_interval_ms=8 * 3_600_000,
        settlement_times=tuple(T + i * 8 * 3_600_000 for i in range(91)),
        observed_time=now - 3, available_time=now - 2, ingestion_time=now - 1)
    order = replace(original['order_rule_profiles'][0], instrument_id=NAME,
        rule_id=f'synthetic-month-order-{index}', contract_rule_id=rule.rule_id,
        effective_from=T - 100, effective_until=T + SPAN + 100,
        observed_time=now - 3, available_time=now - 2, ingestion_time=now - 1)
    return dict(contract_rules=(rule,), funding_policies=(policy,), order_rule_profiles=(order,))


def trade(ctx):
    if ctx.timestamp == T:
        return (OrderIntent('entry', NAME, D('.1')),)
    if ctx.timestamp == T + SPAN:
        return (OrderIntent('exit', NAME, D('-.1'), reduce_only=True),)
    return ()


def config(**kwargs):
    return dict(configuration(trade), **environment(0), **kwargs)


def test_month_one_account_2881_epochs_funding_rules_and_checkpoint_recovery():
    original = LinearPerpetualSimulationSession(**config(replay_budget=budget()))
    frames = [market(0), market(1)]
    updates = []
    original.advance(frames)
    for index in range(1, 2881):
        update = environment(index)
        batch = (market(index * STEP, funding=index % 32 == 0),)
        updates.append(dict(first_frame=len(frames), **update))
        frames.extend(batch)
        original.advance_environment(batch, **update)
    checkpoint = original.checkpoint()
    assert checkpoint['schema'] == 'PERPETUAL_SIMULATION_REPLAY_CHECKPOINT_V3'
    assert checkpoint['environment_count'] == 2881
    restored = LinearPerpetualSimulationSession.restore_batches(checkpoint,
        batches=(frames[i:i + 257] for i in range(0, len(frames), 257)),
        environment_updates=updates, **config(replay_budget=budget()))
    assert restored.checkpoint() == checkpoint
    for account in (original, restored):
        account.advance((market(SPAN + 1),))
    result = original.report()
    assert restored.report() == result
    assert result['schema_version'] == 'LINEAR_PERPETUAL_FORWARD_SIMULATION_V4'
    assert result['replay_budget'] == budget()
    assert result['ending_state']['positions'] == {}
    assert result['reconciliation']['initial_equity'] == '1000'
    assert D(result['reconciliation']['funding']) == D('-.9')
    assert D(result['reconciliation']['fee']) == D('.02')
    assert D(result['reconciliation']['ending_equity']) == D('999.08')
    assert result['reconciliation']['residual'] == '0'
    assert len(result['orders']) == 2
    assert len([e for e in result['ledger_events'] if e['event_type'] == 'FUNDING_SETTLEMENT']) == 91
    assert result['authoritative'] is result['submission_enabled'] is False


def test_legacy_default_still_refuses_1001st_environment():
    session = LinearPerpetualSimulationSession(**config())
    session.advance((market(0),))
    for index in range(1, 1000):
        session.advance_environment((market(index * STEP, funding=index % 32 == 0),),
                                    **environment(index))
    with pytest.raises(ValueError, match='ENVIRONMENT_SEGMENT_BUDGET'):
        session.advance_environment((market(1000 * STEP),), **environment(1000))
    with pytest.raises(ValueError, match='SESSION_FAILED'):
        session.report()


@pytest.mark.parametrize('bad', [True, {}, budget(epochs=True), budget(epochs=4097),
                                budget(span=0), budget(span=33 * DAY)])
def test_invalid_budget_refused_before_callback(bad):
    with pytest.raises(ValueError, match='REPLAY_BUDGET'):
        LinearPerpetualSimulationSession(**config(replay_budget=bad))


def test_budget_identity_and_restore_cannot_silently_change():
    session = LinearPerpetualSimulationSession(**config(replay_budget=budget(epochs=2)))
    session.advance((market(0),))
    checkpoint = session.checkpoint()
    for different in (None, budget(epochs=3), budget(epochs=2, span=SPAN)):
        with pytest.raises(ValueError, match='REPLAY_BUDGET'):
            LinearPerpetualSimulationSession.restore(checkpoint, frames=(market(0),),
                environment_updates=[], **config(replay_budget=different))
    same = LinearPerpetualSimulationSession.restore(checkpoint, frames=(market(0),),
        environment_updates=[], **config(replay_budget=budget(epochs=2)))
    assert same.report() == session.report()
    other = LinearPerpetualSimulationSession(**config(replay_budget=budget(epochs=3)))
    other.advance((market(0),))
    assert other.report()['input_hash'] != session.report()['input_hash']
    assert other.report()['reconciliation'] == session.report()['reconciliation']


def test_elapsed_budget_checked_before_any_new_strategy_callback():
    callbacks = []
    session = LinearPerpetualSimulationSession(**dict(config(replay_budget=budget(span=1)),
        strategy=lambda ctx: callbacks.append(ctx.timestamp) or ()))
    session.advance((market(0),))
    with pytest.raises(ValueError, match='REPLAY_ELAPSED_BUDGET'):
        session.advance((market(2),))
    assert callbacks == [T]
    with pytest.raises(ValueError, match='SESSION_FAILED'):
        session.report()
