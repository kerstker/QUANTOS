from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.execution.perpetual_public_replay import prepare_public_replay
from services.execution.perpetual_simulation import OrderIntent, SimulationCostModel, run_linear_perpetual_simulation
from tests.execution.test_perpetual_public_replay import START, gold_capture_fixture, sealed


def inputs():
    doc = gold_capture_fixture()
    doc['schema_version'] = 'GOLD_PUBLIC_SESSION_PREFIX_V1'
    template = deepcopy(doc['samples'][0])
    doc['samples'] = []
    for i in range(7):
        sample = deepcopy(template)
        for name, delay in [('mark', 1), ('depth', 2)]:
            sample[name].update(requested_at=START+i*1000, received_at=START+i*1000+delay)
        sample['mark']['payload'].update(time=START+i*1000,
            nextFundingTime=START+(1500 if i<2 else 3500 if i<4 else 7500))
        sample['depth']['payload'].update(T=START+i*1000, E=START+i*1000, lastUpdateId=i+1)
        doc['samples'].append(sample)
    doc['exchange_after'].update(requested_at=START+7000, received_at=START+7001)
    records = []
    for nominal, available in [(1500, 2500), (3500, 4500)]:
        record = deepcopy(doc['bootstrap']['funding'])
        record.update(requested_at=START+available-10, received_at=START+available,
            payload=[dict(symbol='XAUUSDT', fundingTime=START+nominal+2, fundingRate='0.01', markPrice='100')])
        records.append(sealed({'record': record})['record'])
    return sealed(doc), records


def prepare(doc, records):
    return prepare_public_replay(doc, observed_funding_records=records, settlement_window_ms=1000)


def simulate(value, *, through=None):
    seen = []
    def strategy(ctx):
        seen.append(ctx.timestamp)
        return (OrderIntent('entry', 'XAUUSDT', D('.1')),) if ctx.timestamp == START+2 else ()
    args = {k: value[k] for k in ('frames', 'contract_rules', 'funding_policies', 'order_rule_profiles')}
    if through is not None:
        args['frames'] = tuple(f for f in args['frames'] if f.timestamp <= through)
    result = run_linear_perpetual_simulation(**args, release_identity={'commit': 'f'*40},
        dataset_identity=value['manifest'], strategy_identity={'test': 'rolling'}, strategy=strategy,
        initial_collateral=D('1000'), costs=SimulationCostModel(fee_rate=D('.001')))
    return result, seen


def test_two_observed_boundaries_use_original_ledger_and_each_receipt_embargo():
    value = prepare(*inputs())
    result, seen = simulate(value)
    assert result['reconciliation']['funding'] == '-0.2'
    assert result['reconciliation']['residual'] == '0'
    assert START+2002 not in seen and START+4002 not in seen
    assert START+3002 in seen and START+5002 in seen
    assert value['funding_policies'][0].forecast_observations[1][2] == START+2001
    assert result['authoritative'] is False


def test_later_forecast_does_not_change_earlier_decisions_or_cash_events():
    value = prepare(*inputs())
    prefix, _ = simulate(value, through=START+3002)
    full, _ = simulate(value)
    for key in ('decision_events', 'ledger_events', 'order_events', 'equity_history'):
        assert full[key][:len(prefix[key])] == prefix[key]


def test_missing_second_actual_receipt_is_not_zero_filled():
    doc, records = inputs()
    with pytest.raises(ValueError, match='OBSERVED_SETTLEMENT_COVERAGE'):
        prepare(doc, records[:1])


def test_later_known_forecast_cannot_be_backdated():
    value = prepare(*inputs())
    policy = value['funding_policies'][0]
    observations = list(policy.forecast_observations)
    nominal, event, _ = observations[1]
    observations[1] = (nominal, event, nominal+1)
    with pytest.raises(ValueError, match='ROLLING_FORECAST'):
        replace(policy, forecast_observations=tuple(observations))


def test_second_funding_loss_latches_original_five_percent_stop_without_reentry():
    from services.research.gold_loss_control import GoldResearchLossControl
    doc, records = inputs()
    for record, rate in zip(records, ('0.1', '0.1')):
        record['payload'][0]['fundingRate'] = rate
    # The admitted small position loses less than 5% before the second charge;
    # that charge crosses the threshold without relaxing the exposure gate.
    for sample in doc['samples'][3:]:
        sample['mark']['payload']['markPrice'] = '65'
        sample['depth']['payload'].update(bids=[['64.9', '1']], asks=[['65.1', '1']])
    records[1]['payload'][0]['markPrice'] = '65'
    records = [sealed({'record': r})['record'] for r in records]
    value = prepare(sealed(doc), records)
    def strategy(ctx):
        if not ctx.positions and not ctx.open_orders:
            return (OrderIntent('entry-'+str(ctx.timestamp), 'XAUUSDT', D('.1')),)
        return ()
    control = GoldResearchLossControl(strategy, initial_equity=D('100'))
    result = run_linear_perpetual_simulation(**{k: value[k] for k in
        ('frames', 'contract_rules', 'funding_policies', 'order_rule_profiles')},
        release_identity={'commit': 'f'*40}, dataset_identity=value['manifest'],
        strategy_identity={'test': 'rolling-loss'}, strategy=control,
        initial_collateral=D('100'), costs=SimulationCostModel(fee_rate=D('0')),
        risk_policy=control.risk, risk_policy_identity=control.identity)
    assert control.report(result)['status'] == 'STOPPED_FLAT'
    assert control.trigger['timestamp'] == START+3502
    assert result['reconciliation']['funding'] == '-1.65'
    assert result['reconciliation']['residual'] == '0'
    assert len(result['orders']) == 2


def test_changing_future_forecast_preserves_processed_prefix():
    doc, records = inputs()
    original, _ = simulate(prepare(doc, records))
    for sample in doc['samples'][4:]:
        sample['mark']['payload']['nextFundingTime'] += 1000
    changed, _ = simulate(prepare(sealed(doc), records))
    for key in ('decision_events', 'ledger_events', 'order_events', 'equity_history'):
        assert original[key] == changed[key]
    assert original['input_hash'] != changed['input_hash']


def test_missing_intermediate_forecast_cannot_skip_actual_charge():
    doc, records = inputs()
    for sample in doc['samples'][2:]:
        sample['mark']['payload']['nextFundingTime'] = START+7500
    with pytest.raises(ValueError):
        prepare(sealed(doc), records)


def test_extending_single_boundary_prefix_preserves_existing_account_history():
    doc, records = inputs()
    earlier = deepcopy(doc)
    earlier['samples'] = earlier['samples'][:4]
    before, _ = simulate(prepare(sealed(earlier), records[:1]))
    after, _ = simulate(prepare(doc, records))
    for key in ('decision_events', 'ledger_events', 'order_events', 'equity_history'):
        assert after[key][:len(before[key])] == before[key]


def test_rolling_mode_does_not_silently_change_independent_capture_identity():
    doc, records = inputs()
    doc['schema_version'] = 'GOLD_PUBLIC_REPLAY_CAPTURE_V1'
    with pytest.raises(ValueError, match='ROLLING_FUNDING_REQUIRES_SESSION_PREFIX'):
        prepare(sealed(doc), records)
