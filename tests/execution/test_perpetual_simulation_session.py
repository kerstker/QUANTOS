from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.execution.perpetual_simulation import (
    LinearPerpetualSimulationSession, OrderIntent, SimulationCostModel,
)
from services.execution.perpetual_ledger_v2 import FundingEvent
from tests.execution.test_perpetual_simulation import F, T, frame, run


def configuration(strategy=lambda ctx: ()):
    return dict(
        release_identity={'commit': 'f' * 40}, dataset_identity={'dataset': 'synthetic'},
        strategy_identity={'strategy': 'regression.v1'}, contract_rules=(F['_rule'](),),
        funding_policies=(replace(F['_funding_policy'](), settlement_times=(T + 1000,)),),
        initial_collateral=D('1000'), costs=SimulationCostModel(fee_rate=D('0')), strategy=strategy)


def session(strategy=lambda ctx: ()):
    return LinearPerpetualSimulationSession(**configuration(strategy))


def test_saved_checkpoint_replays_pending_inventory_and_continues_identically(tmp_path):
    import json
    def strategy(ctx):
        if ctx.timestamp == T:
            return (OrderIntent('entry', 'BTCUSDT', D('.1')),)
        if ctx.timestamp == T + 30:
            return (OrderIntent('exit', 'BTCUSDT', D('-.1'), reduce_only=True),)
        return ()
    prefix = (frame(0), frame(10, size='.04'))
    original = session(strategy)
    original.advance(prefix)
    path = tmp_path / 'checkpoint.json'
    path.write_text(json.dumps(original.checkpoint()))
    restored = LinearPerpetualSimulationSession.restore(json.loads(path.read_text()),
        frames=prefix, **configuration(strategy))
    assert restored.report() == original.report()
    tail = (frame(20), frame(30, '105'), frame(40, '105'))
    original.advance(tail)
    restored.advance(tail)
    assert restored.report() == original.report()
    assert restored.report()['ending_state']['positions'] == {}


@pytest.mark.parametrize('change', ['frame', 'missing', 'source', 'cost', 'checkpoint'])
def test_restore_rejects_changed_replay_evidence(change):
    original = session()
    prefix = (frame(0), frame(10))
    original.advance(prefix)
    checkpoint = original.checkpoint()
    config = configuration()
    if change == 'frame':
        prefix = (frame(0), frame(10, '110'))
    elif change == 'missing':
        prefix = prefix[:1]
    elif change == 'source':
        config['release_identity'] = {'commit': 'a' * 40}
    elif change == 'cost':
        config['costs'] = SimulationCostModel(fee_rate=D('.001'))
    else:
        checkpoint['result_hash'] = '0' * 64
    with pytest.raises(ValueError, match='CHECKPOINT'):
        LinearPerpetualSimulationSession.restore(checkpoint, frames=prefix, **config)


def test_batches_preserve_pending_fill_inventory_history_and_exact_result():
    def strategy(ctx):
        if ctx.timestamp == T:
            return (OrderIntent('entry', 'BTCUSDT', D('.1')),)
        if ctx.timestamp == T + 30:
            return (OrderIntent('exit', 'BTCUSDT', D('-.1'), reduce_only=True),)
        return ()
    frames = (frame(0), frame(10), frame(20, '105'), frame(30, '110'), frame(40, '110'))
    s = session(strategy)
    s.advance(frames[:1])
    first = s.report()
    assert first['orders'][0]['status'] == 'OPEN'
    s.advance(frames[1:3])
    second = s.report()
    assert second['ending_state']['positions']['BTCUSDT']['quantity'] == '0.1'
    assert second['decision_events'][:1] == first['decision_events']
    s.advance(frames[3:])
    assert s.report() == run(frames, strategy)
    assert s.report()['ending_state']['positions'] == {}
    assert first['orders'][0]['status'] == 'OPEN'


def test_known_funding_charges_carried_inventory_once_across_batches():
    def strategy(ctx):
        return (OrderIntent('entry', 'BTCUSDT', D('.1')), ) if ctx.timestamp == T else ()
    s = session(strategy)
    s.advance((frame(0), frame(10)))
    s.advance((frame(1000, funding=(FundingEvent(T + 1000, 'BTCUSDT', D('.01'), D('100')),)),))
    assert s.report()['reconciliation']['funding'] == '-0.1'
    s.advance((frame(1010),))
    assert s.report()['reconciliation']['funding'] == '-0.1'
    assert s.report()['reconciliation']['residual'] == '0'


def test_missing_funding_and_overlap_are_rejected_without_restarting_account():
    for invalid in ((frame(0),), (frame(1000),)):
        s = session()
        s.advance((frame(0), frame(10)))
        with pytest.raises(ValueError):
            s.advance(invalid)
        with pytest.raises(ValueError, match='SESSION_FAILED'):
            s.report()
        with pytest.raises(ValueError, match='SESSION_FAILED'):
            s.advance((frame(20),))


def test_atomic_market_history_versions_result_and_rejects_original_checkpoint():
    frames = (frame(0), frame(10, '120'))
    result = run(frames)
    legacy_hash = '314ddc4d98aa9fcaa69adfa9136d102823d1acb7ae316b5ec604477f0781e0e0'
    assert result['market_history_policy'] == 'ATOMIC_MARKET_PHASE_EQUITY_V1'
    assert result['position_history_policy'] == 'ACTUAL_LEDGER_POSITION_MARKS_V1'
    assert result['result_hash'] == 'a4103c702c1234ce36d9c475cb06964d42ce87f299d5a1f8a9a5091ce8619b1d'
    assert result['result_hash'] != legacy_hash
    assert result['reconciliation']['initial_equity'] == result['reconciliation']['ending_equity'] == '1000'
    assert result['reconciliation']['attributed_change'] == result['reconciliation']['residual'] == '0'
    assert [point['phase'] for point in result['equity_history']] == [
        'INITIAL', 'MARK', 'MARKET', 'MARK', 'MARKET']
    # Captured from the parent source with these exact original inputs.
    checkpoint = dict(schema='PERPETUAL_SIMULATION_REPLAY_CHECKPOINT_V1',
        input_hash='c6bc6bb24e7bd8a748d17235dd8d454b797016e9e520e9db77705544b693f22c',
        result_hash=legacy_hash, frame_count=2, last_timestamp=T + 10,
        authoritative=False, submission_enabled=False)
    with pytest.raises(ValueError, match='CHECKPOINT'):
        LinearPerpetualSimulationSession.restore(checkpoint, frames=frames, **configuration())


def test_strategy_exception_poisoned_session_cannot_publish_partial_batch():
    def strategy(ctx):
        if ctx.timestamp == T + 10:
            raise RuntimeError('synthetic callback failure')
        return ()
    s = session(strategy)
    s.advance((frame(0),))
    with pytest.raises(RuntimeError, match='synthetic callback failure'):
        s.advance((frame(10), frame(20)))
    with pytest.raises(ValueError, match='SESSION_FAILED'):
        s.report()


def test_gold_loss_limit_uses_original_equity_across_batches_and_stays_latched():
    from services.research.gold_loss_control import GoldResearchLossControl
    from tests.research.test_gold_v2_strategy import frame as gold_frame
    def strategy(ctx):
        if not ctx.positions and not ctx.open_orders:
            return (OrderIntent('entry-' + str(ctx.timestamp), 'XAUUSDT', D('.1')),)
        return ()
    control = GoldResearchLossControl(strategy, initial_equity=D('100'))
    rule = replace(F['_rule'](), rule_id='synthetic-gold-rules',
                   spec=replace(F['_spec'](), instrument_id='XAUUSDT'))
    config = dict(
        release_identity={'commit': 'f' * 40}, dataset_identity={'dataset': 'synthetic'},
        strategy_identity={'strategy': 'cross-batch-loss'}, contract_rules=(rule,),
        funding_policies=(replace(F['_funding_policy'](), instrument_id='XAUUSDT',
                                  settlement_times=(T + 1000,)),),
        initial_collateral=D('100'), costs=SimulationCostModel(fee_rate=D('.3')),
        strategy=control, risk_policy=control.risk, risk_policy_identity=control.identity)
    s = LinearPerpetualSimulationSession(**config)
    s.advance((gold_frame(0), gold_frame(10)))
    first = s.report()
    assert first['ending_state']['account_equity'] == '97'
    assert control.trigger is None
    s.advance((gold_frame(20, '80'),))
    assert control.trigger['equity'] == '95'
    fresh_control = GoldResearchLossControl(strategy, initial_equity=D('100'))
    restored = LinearPerpetualSimulationSession.restore(s.checkpoint(),
        frames=(gold_frame(0), gold_frame(10), gold_frame(20, '80')),
        **{**config, 'strategy': fresh_control, 'risk_policy': fresh_control.risk,
           'risk_policy_identity': fresh_control.identity})
    assert fresh_control.trigger == control.trigger
    s.advance((gold_frame(30, '80'), gold_frame(40), gold_frame(50)))
    restored.advance((gold_frame(30, '80'), gold_frame(40), gold_frame(50)))
    result = s.report()
    assert restored.report() == result
    assert fresh_control.report(result)['status'] == 'STOPPED_FLAT'
    assert control.report(result)['status'] == 'STOPPED_FLAT'
    assert len(result['orders']) == 2
    assert result['reconciliation']['initial_equity'] == '100'
    assert result['reconciliation']['residual'] == '0'
    assert first['ending_state']['account_equity'] == '97'
