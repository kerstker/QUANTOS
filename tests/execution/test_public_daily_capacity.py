from copy import deepcopy
from decimal import Decimal as D

import pytest

from services.execution.perpetual_public_replay import prepare_public_replay
from tests.execution.test_perpetual_public_replay import DAY, START, gold_capture_fixture, sealed


def crossing():
    doc = gold_capture_fixture()
    doc['schema_version'] = 'GOLD_PUBLIC_SESSION_PREFIX_V1'
    boundary = (START // DAY + 1) * DAY
    for i, sample in enumerate(doc['samples']):
        t = boundary - 1500 + i * 1000
        for kind, delta in [('mark', 1), ('depth', 2)]:
            sample[kind].update(requested_at=t, received_at=t+delta)
        sample['mark']['payload'].update(time=t, nextFundingTime=boundary+8*3600000)
        sample['depth']['payload'].update(T=t, E=t)
    doc['exchange_after'].update(requested_at=boundary+2000, received_at=boundary+2001)
    receipt = deepcopy(doc['bootstrap']['candles'])
    receipt.update(requested_at=boundary+600, received_at=boundary+700)
    for candle in receipt['payload']:
        candle[0] += DAY
        candle[6] += DAY
        candle[7] = '2000000'
    doc['capacity_observations'] = [receipt]
    return sealed(doc), boundary


def test_new_day_capacity_enters_at_receipt_time_not_at_midnight():
    doc, boundary = crossing()
    prepared = prepare_public_replay(doc)
    frames = {f.timestamp: f for f in prepared['frames']}
    assert not frames[boundary+502].capacity_snapshots
    cap = frames[boundary+700].capacity_snapshots[0]
    assert cap.adv_window_end == boundary
    assert cap.available_time == boundary+700
    assert str(cap.policy.adv_notional) == '2000000'
    assert prepared['manifest']['capacity_observations'][0]['available_at_ms'] == boundary+700


@pytest.mark.parametrize('mutation', ['future', 'old_day', 'duplicate', 'early_request'])
def test_invalid_daily_receipts_are_rejected(mutation):
    doc, boundary = crossing()
    receipt = doc['capacity_observations'][0]
    if mutation == 'future':
        receipt['received_at'] = boundary+3000
    elif mutation == 'old_day':
        receipt['payload'] = deepcopy(doc['bootstrap']['candles']['payload'])
    elif mutation == 'duplicate':
        doc['capacity_observations'].append(deepcopy(receipt))
    else:
        receipt['requested_at'] = boundary-1
    with pytest.raises(ValueError, match='CAPACITY'):
        prepare_public_replay(sealed(doc))


def test_cross_day_without_daily_evidence_still_rejects():
    doc, _ = crossing()
    doc.pop('capacity_observations')
    with pytest.raises(ValueError, match='day boundary'):
        prepare_public_replay(sealed(doc))


def test_new_day_receipt_does_not_change_previous_day_frames():
    doc, boundary = crossing()
    full = prepare_public_replay(doc)
    earlier = deepcopy(doc)
    earlier['samples'] = earlier['samples'][:2]
    # Use a third pre-midnight quote to satisfy the original sample minimum.
    extra = deepcopy(earlier['samples'][-1])
    for kind in ('mark', 'depth'):
        extra[kind]['requested_at'] += 100
        extra[kind]['received_at'] += 100
    extra['mark']['payload']['time'] += 100
    extra['depth']['payload']['T'] += 100
    extra['depth']['payload']['E'] += 100
    earlier['samples'].append(extra)
    earlier.pop('capacity_observations')
    prior = prepare_public_replay(sealed(earlier))
    assert full['frames'][:2] == prior['frames'][:2]
    assert full['frames'][0].timestamp < boundary


def test_original_account_holds_across_midnight_and_rejects_until_capacity_arrives():
    from services.execution.perpetual_simulation import (
        OrderIntent, SimulationCostModel, run_linear_perpetual_simulation,
    )
    doc, boundary = crossing()
    value = prepare_public_replay(doc)

    def strategy(context):
        names = {boundary-1498: 'before', boundary+502: 'missing', boundary+700: 'received'}
        name = names.get(context.timestamp)
        return (OrderIntent(name, 'XAUUSDT', D('.1')),) if name else ()

    result = run_linear_perpetual_simulation(**{k: value[k] for k in
        ('frames', 'contract_rules', 'funding_policies', 'order_rule_profiles')},
        release_identity={'commit': 'f'*40}, dataset_identity=value['manifest'],
        strategy_identity={'test': 'daily-capacity'}, strategy=strategy,
        initial_collateral=D('1000'), costs=SimulationCostModel(fee_rate=D('.001')))
    assert result['reconciliation']['initial_equity'] == '1000'
    assert result['reconciliation']['residual'] == '0'
    assert any(e.get('reason') == 'MISSING_CURRENT_CAPACITY' for e in result['order_events'])
    orders = {o['order_id']: o for o in result['orders']}
    assert orders['before']['filled_at'] < boundary
    assert orders['received']['filled_at'] > boundary


def test_cross_day_five_percent_stop_stays_latched_while_exit_waits_for_capacity():
    from services.execution.perpetual_simulation import (
        OrderIntent, SimulationCostModel, run_linear_perpetual_simulation,
    )
    from services.research.gold_loss_control import GoldResearchLossControl

    doc, boundary = crossing()
    last = deepcopy(doc['samples'][-1])
    for kind in ('mark', 'depth'):
        last[kind]['requested_at'] += 1000
        last[kind]['received_at'] += 1000
    last['mark']['payload']['time'] += 1000
    last['depth']['payload']['T'] += 1000
    last['depth']['payload']['E'] += 1000
    doc['samples'].append(last)
    doc['exchange_after'].update(requested_at=boundary+3000, received_at=boundary+3001)
    # A 5.6% marked loss occurs before the new-day capacity receipt. The
    # subsequent price recovery must not reset the stop or permit reopening.
    doc['samples'][2]['mark']['payload']['markPrice'] = '60'
    doc['samples'][2]['depth']['payload'].update(bids=[['59.9', '1']], asks=[['60.1', '1']])
    prepared = prepare_public_replay(sealed(doc))

    def strategy(context):
        return (OrderIntent('entry-'+str(context.timestamp), 'XAUUSDT', D('.14')),)

    control = GoldResearchLossControl(strategy, initial_equity=D('100'))
    result = run_linear_perpetual_simulation(**{k: prepared[k] for k in
        ('frames', 'contract_rules', 'funding_policies', 'order_rule_profiles')},
        release_identity={'commit': 'f'*40}, dataset_identity=prepared['manifest'],
        strategy_identity={'test': 'daily-capacity-loss'}, strategy=control,
        initial_collateral=D('100'), costs=SimulationCostModel(fee_rate=D('0')),
        risk_policy=control.risk, risk_policy_identity=control.identity)
    assert control.trigger['timestamp'] == boundary+502
    assert control.report(result)['status'] == 'STOPPED_FLAT'
    assert result['reconciliation']['initial_equity'] == '100'
    assert result['reconciliation']['residual'] == '0'
    assert any(e.get('reason') == 'MISSING_CURRENT_CAPACITY' for e in result['order_events'])
    assert not any(o['order_id'].startswith('entry-') for o in result['orders']
                   if o['created_at'] >= boundary)
