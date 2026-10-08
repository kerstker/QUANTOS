"""Causal rule/funding epochs on one original perpetual account."""

from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.execution.perpetual_ledger_v2 import FundingEvent
from services.execution.perpetual_simulation import (
    LinearPerpetualSimulationSession, OrderIntent, SimulationCostModel,
)
from tests.execution.test_perpetual_order_admission import profile
from tests.execution.test_perpetual_simulation import F, T, frame


def configuration(strategy=lambda ctx: ()):
    rule = replace(F['_rule'](), effective_until=T + 20)
    policy = replace(F['_funding_policy'](), coverage_start=T - 100,
                     coverage_end=T + 19, settlement_times=(T + 15,),
                     settlement_interval_ms=1000)
    return dict(release_identity={'commit': 'f' * 40},
                dataset_identity={'dataset': 'synthetic-epoch-observation'},
                strategy_identity={'strategy': 'carry-original-account-v1'},
                contract_rules=(rule,), funding_policies=(policy,),
                order_rule_profiles=(profile(effective_until=T + 20),),
                initial_collateral=D('1000'),
                costs=SimulationCostModel(fee_rate=D('.001')), strategy=strategy)


def environment(**changes):
    spec = replace(F['_spec'](), quantity_step=D('.02'), min_quantity=D('.02'))
    rule = replace(F['_rule'](), rule_id='synthetic-contract-epoch-2',
                   spec=spec, effective_from=T + 20, effective_until=T + 200,
                   observed_time=T + 17, available_time=T + 18, ingestion_time=T + 19)
    policy = replace(F['_funding_policy'](), policy_epoch_id='synthetic-funding-epoch-2',
                     coverage_start=T + 20, coverage_end=T + 100,
                     settlement_times=(T + 35,), settlement_interval_ms=1000,
                     observed_time=T + 17, available_time=T + 18, ingestion_time=T + 19)
    order = profile(rule_id='synthetic-order-epoch-2', contract_rule_id=rule.rule_id,
                    effective_from=T + 20, effective_until=T + 200,
                    observed_time=T + 17, available_time=T + 18, ingestion_time=T + 19,
                    price_tick=D('1'), min_price=D('1'), max_notional=D('100'))
    return dict(contract_rules=(rule,), funding_policies=(policy,),
                order_rule_profiles=(order,), **changes)


def tape():
    return (
        frame(0), frame(10, size='.04'),
        frame(15, size='0', funding=(FundingEvent(T + 15, 'BTCUSDT', D('.01'), D('100')),)),
        frame(20), frame(30),
        frame(35, funding=(FundingEvent(T + 35, 'BTCUSDT', D('.01'), D('100')),)),
        frame(40, '110'), frame(50, '110'),
    )


def trade(ctx):
    if ctx.timestamp == T:
        return (OrderIntent('entry', 'BTCUSDT', D('.1')),)
    if ctx.timestamp == T + 40:
        return (OrderIntent('exit', 'BTCUSDT', D('-.04'), reduce_only=True),)
    return ()


def continued():
    session = LinearPerpetualSimulationSession(**configuration(trade))
    frames = tape()
    session.advance(frames[:3])
    prefix = session.report()
    session.advance_environment(frames[3:6], **environment())
    return session, prefix


def test_causal_epoch_keeps_partial_position_cash_funding_capacity_and_initial_risk():
    session, prefix = continued()
    middle = session.report()
    assert prefix['orders'][0]['status'] == 'PARTIALLY_FILLED'
    assert middle['orders'][0]['status'] == 'CANCELLED'
    assert middle['orders'][0]['reason'] == 'ENVIRONMENT_EPOCH_CHANGED'
    assert middle['orders'][0]['filled_quantity'] == '0.04'
    assert middle['ending_state']['positions']['BTCUSDT']['quantity'] == '0.04'
    assert middle['reconciliation']['initial_equity'] == '1000'
    assert middle['reconciliation']['funding'] == '-0.08'
    assert middle['ending_state']['capacity_consumed'] == prefix['ending_state']['capacity_consumed']
    assert middle['ledger_events'][:len(prefix['ledger_events'])] == prefix['ledger_events']
    assert middle['decision_events'][:len(prefix['decision_events'])] == prefix['decision_events']
    session.advance(tape()[6:])
    result = session.report()
    assert result['ending_state']['positions'] == {}
    assert D(result['reconciliation']['fee']) == D('.0084')
    assert D(result['reconciliation']['price_pnl']) == D('.4')
    assert D(result['reconciliation']['ending_equity']) == D('1000.3116')
    assert result['reconciliation']['residual'] == '0'
    assert len([e for e in result['ledger_events'] if e['event_type'] == 'FUNDING_SETTLEMENT']) == 2
    assert len(result['environment_segments']) == 2
    assert result['environment_segments'][1]['first_frame'] == 3
    assert result['authoritative'] is result['submission_enabled'] is False


def test_epoch_checkpoint_replays_original_prefix_and_exact_continuation(tmp_path):
    import json

    session, _ = continued()
    path = tmp_path / 'checkpoint.json'
    path.write_text(json.dumps(session.checkpoint()))
    checkpoint = json.loads(path.read_text())
    assert checkpoint['schema'] == 'PERPETUAL_SIMULATION_REPLAY_CHECKPOINT_V2'
    restored = LinearPerpetualSimulationSession.restore(
        checkpoint, frames=tape()[:6], environment_updates=[
            dict(first_frame=3, **environment())], **configuration(trade))
    assert restored.report() == session.report()
    for instance in (session, restored):
        instance.advance(tape()[6:])
    assert restored.report() == session.report()


@pytest.mark.parametrize('damage', [
    'late_rule', 'late_funding', 'late_order', 'venue', 'currency',
    'multiplier', 'cash_quantum', 'unrepresentable_position', 'missing_mark',
    'missing_profile', 'past_funding_revision', 'rule_identity_reuse',
])
def test_invalid_epoch_poisoned_session_cannot_publish_partial_success(damage):
    session = LinearPerpetualSimulationSession(**configuration(trade))
    session.advance(tape()[:3])
    update = environment()
    batch = tape()[3:6]
    rule, = update['contract_rules']
    policy, = update['funding_policies']
    order, = update['order_rule_profiles']
    if damage == 'late_rule':
        update['contract_rules'] = (replace(rule, ingestion_time=T + 21),)
    elif damage == 'late_funding':
        update['funding_policies'] = (replace(policy, ingestion_time=T + 21),)
    elif damage == 'late_order':
        update['order_rule_profiles'] = (replace(order, ingestion_time=T + 21),)
    elif damage == 'venue':
        update['funding_policies'] = (replace(policy, venue_id='OTHER'),)
    elif damage in ('currency', 'multiplier', 'cash_quantum', 'unrepresentable_position'):
        changes = {
            'currency': dict(contract_type='LINEAR_USDC_PERPETUAL', margin_asset='USDC'),
            'multiplier': dict(contract_multiplier=D('2')),
            'cash_quantum': dict(settlement_quantum=D('.000001')),
            'unrepresentable_position': dict(quantity_step=D('.03'), min_quantity=D('.03')),
        }
        # Contract type uses the actual canonical value from the original spec.
        if damage == 'currency':
            from services.execution.perpetual_ledger_v2 import LINEAR_USDC_PERPETUAL
            changes['currency']['contract_type'] = LINEAR_USDC_PERPETUAL
        update['contract_rules'] = (replace(rule, spec=replace(rule.spec, **changes[damage])),)
    elif damage == 'missing_mark':
        batch = (replace(batch[0], market_events=()), *batch[1:])
    elif damage == 'missing_profile':
        update['order_rule_profiles'] = None
    elif damage == 'past_funding_revision':
        update['funding_policies'] = (replace(policy, coverage_start=T + 10, coverage_end=T + 40,
            settlement_times=(T + 16, T + 35), settlement_interval_ms=19),)
    else:
        update['contract_rules'] = (replace(rule, rule_id=F['_rule']().rule_id),)
        update['order_rule_profiles'] = (replace(order, contract_rule_id=F['_rule']().rule_id),)
    with pytest.raises(ValueError):
        session.advance_environment(batch, **update)
    with pytest.raises(ValueError, match='SESSION_FAILED'):
        session.report()


@pytest.mark.parametrize('damage', ['missing_updates', 'changed_source', 'boundary', 'hash'])
def test_epoch_restore_rejects_altered_environment_or_cut(damage):
    session, _ = continued()
    checkpoint = session.checkpoint()
    updates = [dict(first_frame=3, **environment())]
    if damage == 'missing_updates':
        updates = []
    elif damage == 'changed_source':
        rule, = updates[0]['contract_rules']
        updates[0]['contract_rules'] = (replace(rule, source_identity='changed-original'),)
    elif damage == 'boundary':
        updates[0]['first_frame'] = 2
    else:
        checkpoint['environment_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        LinearPerpetualSimulationSession.restore(
            checkpoint, frames=tape()[:6], environment_updates=updates, **configuration(trade))


def test_repeated_rule_snapshot_cannot_erase_due_funding_between_batches():
    session = LinearPerpetualSimulationSession(**configuration())
    session.advance(tape()[:2])
    with pytest.raises(ValueError):
        session.advance_environment(tape()[3:6], **environment())


def test_transition_is_explicit_and_atomic_history_keeps_checkpoint_shape():
    from tests.execution.test_perpetual_simulation import run

    result = run((frame(0), frame(10, '120')))
    assert result['market_history_policy'] == 'ATOMIC_MARKET_PHASE_EQUITY_V1'
    assert result['position_history_policy'] == 'ACTUAL_LEDGER_POSITION_MARKS_V1'
    assert result['result_hash'] == 'a4103c702c1234ce36d9c475cb06964d42ce87f299d5a1f8a9a5091ce8619b1d'
    assert result['result_hash'] != '314ddc4d98aa9fcaa69adfa9136d102823d1acb7ae316b5ec604477f0781e0e0'
    session = LinearPerpetualSimulationSession(**configuration())
    session.advance(tape()[:1])
    assert session.checkpoint()['schema'] == 'PERPETUAL_SIMULATION_REPLAY_CHECKPOINT_V1'
    assert 'environment_segments' not in session.report()


def test_single_batch_entrypoint_uses_exact_same_original_epoch_account():
    from services.execution.perpetual_simulation import run_linear_perpetual_simulation

    session, _ = continued()
    session.advance(tape()[6:])
    result = run_linear_perpetual_simulation(
        frames=tape(), environment_updates=[dict(first_frame=3, **environment())],
        **configuration(trade))
    assert result == session.report()


def test_schedule_cannot_remove_settlement_already_due_when_revision_becomes_known():
    # The old fixed schedule has a settlement exactly at the new boundary.
    config = configuration()
    old, = config['funding_policies']
    config['funding_policies'] = (replace(old, coverage_end=T + 20,
                                          settlement_times=(T + 20,)),)
    session = LinearPerpetualSimulationSession(**config)
    session.advance(tape()[:2])
    update = environment()
    policy, = update['funding_policies']
    update['funding_policies'] = (replace(policy, ingestion_time=T + 20),)
    with pytest.raises(ValueError, match='DUE_FUNDING_REVISED_TOO_LATE'):
        session.advance_environment(tape()[3:6], **update)


def test_original_gold_loss_stop_stays_latched_through_epoch_and_replay():
    from services.research.gold_loss_control import GoldResearchLossControl
    from tests.research.test_gold_v2_strategy import frame as gold_frame

    def strategy(ctx):
        if not ctx.positions and not ctx.open_orders:
            return (OrderIntent('gold-' + str(ctx.timestamp), 'XAUUSDT', D('.1')),)
        return ()

    def setup():
        control = GoldResearchLossControl(strategy, initial_equity=D('100'))
        config = configuration(control)
        rule, = config['contract_rules']
        policy, = config['funding_policies']
        order, = config['order_rule_profiles']
        config.update(
            contract_rules=(replace(rule, spec=replace(rule.spec, instrument_id='XAUUSDT')),),
            funding_policies=(replace(policy, instrument_id='XAUUSDT'),),
            order_rule_profiles=(replace(order, instrument_id='XAUUSDT'),),
            initial_collateral=D('100'), costs=SimulationCostModel(fee_rate=D('.3')),
            risk_policy=control.risk, risk_policy_identity=control.identity)
        return control, config

    update = environment()
    rule, = update['contract_rules']
    policy, = update['funding_policies']
    order, = update['order_rule_profiles']
    update.update(
        contract_rules=(replace(rule, spec=replace(rule.spec, instrument_id='XAUUSDT')),),
        funding_policies=(replace(policy, instrument_id='XAUUSDT'),),
        order_rule_profiles=(replace(order, instrument_id='XAUUSDT'),))
    prefix = (gold_frame(0), gold_frame(10, size='.04'),
              replace(gold_frame(15, '1', size='0'),
                      market_events=(FundingEvent(T + 15, 'XAUUSDT', D('.01'), D('100')),
                                     *gold_frame(15, '1').market_events)))
    tail = (gold_frame(20), gold_frame(30),
            replace(gold_frame(35), market_events=(
                FundingEvent(T + 35, 'XAUUSDT', D('.01'), D('100')),
                *gold_frame(35).market_events)))
    control, config = setup()
    session = LinearPerpetualSimulationSession(**config)
    session.advance(prefix)
    assert control.trigger['equity'] == '94.8'
    session.advance_environment(tail, **update)
    fresh, fresh_config = setup()
    restored = LinearPerpetualSimulationSession.restore(
        session.checkpoint(), frames=(*prefix, *tail),
        environment_updates=[dict(first_frame=3, **update)], **fresh_config)
    assert fresh.trigger == control.trigger
    for instance in (session, restored):
        instance.advance((gold_frame(40), gold_frame(50)))
    result = session.report()
    assert result == restored.report()
    assert result['ending_state']['positions'] == {}
    assert result['reconciliation']['initial_equity'] == '100'
    assert result['reconciliation']['ending_equity'] == '97.56'
    assert result['reconciliation']['funding'] == '-0.04'
    assert result['reconciliation']['fee'] == '2.4'
    assert control.report(result)['status'] == fresh.report(result)['status'] == 'STOPPED_FLAT'
    assert len([row for row in result['orders'] if row['order_id'].startswith('gold-')
                and not row['order_id'].startswith('gold-loss-stop-')]) == 1


def test_epoch_keeps_venue_quote_watermark_and_rejects_conflicting_old_quote():
    session = LinearPerpetualSimulationSession(**configuration(trade))
    prefix = tape()[:3]
    session.advance(prefix)
    old = prefix[-1].quotes[0]
    changed = replace(old, bid_price=D('99'), ask_price=D('99'))
    batch = (replace(tape()[3], quotes=(changed,)), *tape()[4:6])
    with pytest.raises(ValueError, match='conflicting quote'):
        session.advance_environment(batch, **environment())


def test_current_rule_maximum_amount_is_enforced_after_epoch():
    def strategy(ctx):
        if ctx.timestamp == T + 20:
            return (OrderIntent('oversized-new-epoch', 'BTCUSDT', D('.1')),)
        return ()

    session = LinearPerpetualSimulationSession(**configuration(strategy))
    session.advance(tape()[:3])
    update = environment()
    order, = update['order_rule_profiles']
    update['order_rule_profiles'] = (replace(order, max_notional=D('5')),)
    session.advance_environment(tape()[3:6], **update)
    result = session.report()
    assert result['orders'][0]['reason'] == 'MAX_ORDER_NOTIONAL'
    assert result['orders'][0]['filled_at'] is None
    assert result['reconciliation']['fee'] == '0'


def test_changed_margin_blocks_new_exposure_and_keeps_actual_exit_available():
    from services.execution.perpetual_ledger_v2 import MarginBracket
    from services.execution.perpetual_simulation import SimulationLimits

    def strategy(ctx):
        if ctx.timestamp == T:
            return (OrderIntent('entry', 'BTCUSDT', D('.05')),)
        if ctx.timestamp == T + 20:
            return (OrderIntent('new-risk', 'BTCUSDT', D('.1')),)
        if ctx.timestamp == T + 40:
            return (OrderIntent('exit', 'BTCUSDT', D('-.04'), reduce_only=True),)
        return ()

    config = configuration(strategy)
    config.update(initial_collateral=D('1'),
                  limits=SimulationLimits(max_gross_leverage=D('20'), max_single_weight=D('20')))
    session = LinearPerpetualSimulationSession(**config)
    session.advance(tape()[:3])
    update = environment()
    rule, = update['contract_rules']
    update['contract_rules'] = (replace(rule, spec=replace(rule.spec,
        margin_brackets=(MarginBracket(D('0'), None, D('.5'), D('.2')),))),)
    session.advance_environment(tape()[3:6], **update)
    middle = session.report()
    assert D(middle['ending_reservation']['available']) < 0
    assert middle['orders'][1]['reason'] == 'INSUFFICIENT_RESERVED_COLLATERAL'
    assert middle['ending_state']['positions']['BTCUSDT']['quantity'] == '0.04'
    session.advance(tape()[6:])
    result = session.report()
    assert result['orders'][-1]['status'] == 'FILLED'
    assert result['ending_state']['positions'] == {}
    assert result['reconciliation']['residual'] == '0'


def test_thirty_day_synthetic_epoch_tape_preserves_one_account_and_restores_exactly():
    # Sparse synthetic timestamps prove continuity, not real public coverage,
    # natural forward maturity, throughput or a profitable trading strategy.
    day = 86_400_000
    prefix = tape()[:3]
    session = LinearPerpetualSimulationSession(**configuration(trade))
    session.advance(prefix)
    batches, updates = [], []
    for index in range(30):
        start = T + 20 + index * day
        end = start + day
        base = environment()
        rule, = base['contract_rules']
        policy, = base['funding_policies']
        order, = base['order_rule_profiles']
        rule = replace(rule, rule_id='synthetic-day-rule-' + str(index),
                       effective_from=start, effective_until=end,
                       observed_time=start - 3, available_time=start - 2, ingestion_time=start - 1)
        policy = replace(policy, policy_epoch_id='synthetic-day-funding-' + str(index),
                         coverage_start=start, coverage_end=end - 1,
                         settlement_interval_ms=day // 3,
                         settlement_times=(start, start + day // 3, start + 2 * day // 3),
                         observed_time=start - 3, available_time=start - 2, ingestion_time=start - 1)
        order = replace(order, rule_id='synthetic-day-order-' + str(index),
                        contract_rule_id=rule.rule_id, effective_from=start, effective_until=end,
                        observed_time=start - 3, available_time=start - 2, ingestion_time=start - 1)
        update = dict(contract_rules=(rule,), funding_policies=(policy,), order_rule_profiles=(order,))
        batch = tuple(frame(timestamp - T,
            funding=(FundingEvent(timestamp, 'BTCUSDT', D('.01'), D('100')),))
            for timestamp in policy.settlement_times) + (frame(end - T - 1),)
        batches.append(batch)
        updates.append(update)
        if index < 12:
            session.advance_environment(batch, **update)
    retained = (*prefix, *(f for batch in batches[:12] for f in batch))
    restored = LinearPerpetualSimulationSession.restore(
        session.checkpoint(), frames=retained,
        environment_updates=[dict(first_frame=3 + index * 4, **update)
                             for index, update in enumerate(updates[:12])],
        **configuration(trade))
    for batch, update in zip(batches[12:], updates[12:]):
        session.advance_environment(batch, **update)
        restored.advance_environment(batch, **update)
    result = session.report()
    assert result == restored.report()
    assert result['schema_version'] == 'LINEAR_PERPETUAL_FORWARD_SIMULATION_V3'
    assert result['equity_history'][-1]['timestamp'] - result['equity_history'][0]['timestamp'] >= 30 * day
    assert len(result['environment_segments']) == 31
    assert len(result['orders']) == 1
    assert result['ending_state']['positions']['BTCUSDT']['quantity'] == '0.04'
    assert result['reconciliation']['initial_equity'] == '1000'
    assert D(result['reconciliation']['funding']) == D('-3.64')
    assert D(result['reconciliation']['fee']) == D('.004')
    assert D(result['reconciliation']['ending_equity']) == D('996.356')
    assert len([e for e in result['ledger_events'] if e['event_type'] == 'FUNDING_SETTLEMENT']) == 91
    assert result['reconciliation']['residual'] == '0'
    assert result['authoritative'] is result['submission_enabled'] is False
