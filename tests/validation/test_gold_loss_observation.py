"""Original callback observations must bind the exact retained ledger prefix."""

import copy
import hashlib
import json
from decimal import Decimal

import pytest

from services.execution.perpetual_simulation import EquityPoint, SimulationContext, SimulationCostModel
from services.research.gold_loss_control import GoldResearchLossControl
from services.validation.gold_execution_risk_review import review_execution_risk
from tests.research.test_gold_loss_control import run_control
from tests.research.test_gold_v2_strategy import T, frame


def expected_prefix(history, count):
    state = hashlib.sha256(b'QUANTOS_GOLD_LOSS_HISTORY_OBSERVATION_V1\n')
    for index, point in enumerate(history):
        if index == count:
            break
        state.update(json.dumps([point['timestamp'], point['sequence'], point['phase'], point['equity']],
                                separators=(',', ':'), ensure_ascii=True).encode('ascii') + b'\n')
    return state.hexdigest()


def original_case():
    control, simulation = run_control(tuple(frame(i) for i in (0, 10, 20, 30, 40)),
                                      costs=SimulationCostModel(fee_rate=Decimal('.5')))
    return simulation, control.report(simulation)


def test_native_callback_prefix_and_trigger_are_reconsumed_from_actual_ledger():
    simulation, loss = original_case()
    assert loss['schema'] == 'GOLD_RESEARCH_LOSS_CONTROL_V2'
    observation = loss['history_observation']
    assert observation['observed_points'] == len(simulation['equity_history'])
    assert observation['observed_prefix_sha256'] == expected_prefix(
        simulation['equity_history'], observation['observed_points'])
    before = copy.deepcopy((simulation, loss))
    review = review_execution_risk(simulation, loss)
    assert review['schema'] == 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V2'
    assert review['loss_control']['history_observation']['status'] == 'COMPLETE_RECORDED_PREFIX'
    assert review['loss_control']['original_trigger'] == review['loss_control']['first_threshold_breach']
    # The trace is emitted by the native producer; it does not prove independent
    # mathematics or that every required risk callback actually ran.
    assert 'LOSS_CALLBACK_OBSERVATION_COVERAGE_UNPROVEN' in review['limitations']
    assert review['mathematical_independence'] is review['full_research_risk_9_of_9'] is False
    assert review['economic_authority'] is review['submission_enabled'] is False
    assert (simulation, loss) == before


@pytest.mark.parametrize('damage', ['hash', 'count', 'boolean', 'missing', 'claim', 'trigger',
                                    'history-equity', 'history-phase'])
def test_changed_callback_prefix_or_observed_trigger_fails_closed(damage):
    simulation, loss = original_case()
    observation = loss.get('history_observation', dict(schema='QUANTOS_GOLD_LOSS_HISTORY_OBSERVATION_V1',
        observed_points=len(simulation['equity_history']), observed_prefix_sha256='a' * 64))
    loss['history_observation'] = observation
    if damage == 'hash':
        observation['observed_prefix_sha256'] = 'a' * 64
    elif damage == 'count':
        observation['observed_points'] = len(simulation['equity_history']) + 1
    elif damage == 'boolean':
        observation['observed_points'] = True
    elif damage == 'missing':
        del observation['observed_prefix_sha256']
    elif damage == 'claim':
        observation['independent_verification_complete'] = True
    elif damage == 'history-equity':
        simulation['equity_history'][2]['equity'] = '96'
    elif damage == 'history-phase':
        simulation['equity_history'][2]['phase'] = 'REWRITTEN'
    else:
        loss['trigger'] = None
        loss['status'] = 'ACTIVE'
    with pytest.raises(ValueError):
        review_execution_risk(simulation, loss)


def test_old_report_cannot_receive_a_posthoc_observation_upgrade():
    simulation, loss = original_case()
    loss['schema'] = 'GOLD_RESEARCH_LOSS_CONTROL_V1'
    loss['history_observation'] = dict(schema='QUANTOS_GOLD_LOSS_HISTORY_OBSERVATION_V1',
        observed_points=len(simulation['equity_history']), observed_prefix_sha256='a' * 64)
    with pytest.raises(ValueError, match='LOSS_OBSERVATION_VERSION'):
        review_execution_risk(simulation, loss)


def test_bound_legacy_report_remains_explicitly_without_observation_evidence():
    simulation, loss = original_case()
    loss['schema'] = 'GOLD_RESEARCH_LOSS_CONTROL_V1'
    loss.pop('history_observation', None)
    review = review_execution_risk(simulation, loss)
    assert review['loss_control']['history_observation']['status'] == 'UNAVAILABLE_LEGACY'
    assert 'LOSS_CALLBACK_OBSERVATION_COVERAGE_UNPROVEN' in review['limitations']


def test_recorded_prefix_can_be_read_from_an_immutable_mapping_view():
    from types import MappingProxyType

    simulation, loss = original_case()
    loss['history_observation'] = MappingProxyType(loss['history_observation'])
    review = review_execution_risk(simulation, loss)
    assert review['loss_control']['history_observation']['status'] == 'COMPLETE_RECORDED_PREFIX'


def test_report_does_not_silently_observe_a_later_unseen_loss():
    simulation, _ = original_case()
    history = simulation['equity_history']
    stop = next(row['sequence'] for row in history if Decimal(row['equity']) <= Decimal('95'))
    assert stop > 0
    points = tuple(EquityPoint(row['timestamp'], row['sequence'], Decimal(row['equity']),
                              None, Decimal(row['drawdown']), row['phase'])
                   for row in history[:stop])
    control = GoldResearchLossControl(lambda ctx: (), initial_equity=Decimal('100'))
    control(SimulationContext(points[-1].timestamp, points[-1].equity, {}, {}, {},
                              points, {}, points[-1].equity))
    loss = control.report(simulation)
    assert loss['trigger'] is None
    assert loss['history_observation']['observed_points'] == stop
    review = review_execution_risk(simulation, loss)
    observed = review['loss_control']['history_observation']
    assert observed['status'] == 'INCOMPLETE_RECORDED_PREFIX'
    assert observed['unobserved_points'] == len(history) - stop
    assert observed['first_unobserved_sequence'] == stop
    assert review['loss_control']['first_threshold_breach']['sequence'] == stop
    assert review['loss_control']['original_trigger'] is None


def test_disk_history_observation_never_slices_or_reads_an_old_prefix_again():
    from collections.abc import Sequence

    class AppendOnly(Sequence):
        def __init__(self):
            self.rows = []
            self.reads = []

        def __len__(self):
            return len(self.rows)

        def __getitem__(self, index):
            assert type(index) is int
            self.reads.append(index)
            return self.rows[index]

    history = AppendOnly()
    control = GoldResearchLossControl(lambda ctx: (), initial_equity=Decimal('100'))
    for index, value in enumerate(('100', '99', '94', '97')):
        history.rows.append(EquityPoint(T + index, index, Decimal(value), None, Decimal(0),
                                       'INITIAL' if index == 0 else 'MARKET'))
        control(SimulationContext(T + index, Decimal(value), {}, {}, {}, history, {}, Decimal(value)))
    # The existing account check reads INITIAL each time. The observation loop
    # consumes each newly appended point once and never materializes history.
    assert history.reads == [0, 0, 0, 1, 0, 2, 0, 3]
    assert control.trigger['sequence'] == 2
    assert control._seen == 4


def test_original_checkpoint_replay_reconstructs_the_same_loss_prefix_and_latch():
    from dataclasses import replace

    from services.execution.perpetual_simulation import LinearPerpetualSimulationSession
    from tests.research.test_gold_loss_control import always_enter
    from tests.research.test_gold_v2_strategy import F

    rule = replace(F['_rule'](), rule_id='synthetic-gold-rules',
                   spec=replace(F['_spec'](), instrument_id='XAUUSDT'))
    common = dict(release_identity={'commit': 'f' * 40}, dataset_identity={'dataset': 'synthetic'},
        strategy_identity={'strategy': 'regression.v1'}, contract_rules=(rule,),
        funding_policies=(replace(F['_funding_policy'](), instrument_id='XAUUSDT',
                                  settlement_times=(T + 1000,)),),
        initial_collateral=Decimal('100'), costs=SimulationCostModel(fee_rate=Decimal('.5')))
    frames = tuple(frame(i) for i in (0, 10, 20, 30, 40))
    control = GoldResearchLossControl(always_enter, initial_equity=Decimal('100'))
    original = LinearPerpetualSimulationSession(**common, strategy=control,
        risk_policy=control.risk, risk_policy_identity=control.identity)
    original.advance(frames[:3])
    prefix_report = control.report(original.report())
    checkpoint = json.loads(json.dumps(original.checkpoint()))
    restored_control = GoldResearchLossControl(always_enter, initial_equity=Decimal('100'))
    restored = LinearPerpetualSimulationSession.restore(checkpoint, frames=frames[:3],
        **common, strategy=restored_control, risk_policy=restored_control.risk,
        risk_policy_identity=restored_control.identity)
    assert restored_control.report(restored.report()) == prefix_report
    original.advance(frames[3:])
    restored.advance(frames[3:])
    assert restored.report() == original.report()
    assert restored_control.report(restored.report()) == control.report(original.report())
    assert restored_control.trigger == control.trigger
