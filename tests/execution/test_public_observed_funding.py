from copy import deepcopy
from decimal import Decimal as D

import pytest

from services.execution.perpetual_public_replay import prepare_public_replay
from services.execution.perpetual_simulation import OrderIntent, SimulationCostModel, run_linear_perpetual_simulation
from tests.execution.test_perpetual_public_replay import START, gold_capture_fixture, sealed


def inputs(*, received=None, offset=2):
    document = gold_capture_fixture()
    nominal = START + 1500
    for index, sample in enumerate(document['samples']):
        sample['mark']['payload']['nextFundingTime'] = nominal if index < 2 else nominal + 14_400_000
    record = deepcopy(document['bootstrap']['funding'])
    record.update(requested_at=START + 2400,
                  received_at=START + 2500 if received is None else received,
                  payload=[dict(symbol='XAUUSDT', fundingTime=nominal + offset,
                                fundingRate='0.01', markPrice='100')])
    record = sealed({'record': record})['record']
    return sealed(document), record, nominal


def prepared(document, records):
    return prepare_public_replay(document, observed_funding_records=records, settlement_window_ms=1000)


def test_public_receipt_drives_original_funding_ledger_without_rounding():
    document, record, nominal = inputs()
    with pytest.raises(ValueError):
        prepare_public_replay(document)
    value = prepared(document, [record])
    frames = value['frames']
    assert next(f for f in frames if f.timestamp == nominal).funding_pause_until == START + 2500
    assert next(f for f in frames if f.timestamp == nominal + 2).funding_available_at == START + 2500
    seen = []
    def strategy(ctx):
        seen.append(ctx.timestamp)
        return (OrderIntent('entry', 'XAUUSDT', D('.1')), ) if ctx.timestamp == START + 2 else ()
    result = run_linear_perpetual_simulation(**{k: value[k] for k in
        ('frames', 'contract_rules', 'funding_policies', 'order_rule_profiles')},
        release_identity={'commit': 'f' * 40}, dataset_identity=value['manifest'],
        strategy_identity={'test': 'observed-funding'}, strategy=strategy,
        initial_collateral=D('1000'), costs=SimulationCostModel(fee_rate=D('.001')))
    assert result['reconciliation']['funding'] == '-0.1'
    assert result['reconciliation']['residual'] == '0'
    assert nominal not in seen and nominal + 2 not in seen and START + 2002 not in seen
    assert START + 3002 in seen
    assert 'FORECAST_TIMING_BOUND_UNVERIFIED' in result['authority_blockers']


def test_late_receipt_requires_more_market_observation_not_fabricated_frames():
    document, record, _ = inputs(received=START + 4200)
    with pytest.raises(ValueError, match='FUNDING_RECEIPT_OUTSIDE_CAPTURE'):
        prepared(document, [record])


def test_changed_forecast_before_boundary_is_not_an_accepted_rollover():
    document, record, nominal = inputs()
    document['samples'][1]['mark']['payload']['nextFundingTime'] = nominal + 14_400_000
    with pytest.raises(ValueError, match='FORECAST_CHANGED_BEFORE_SETTLEMENT'):
        prepared(sealed(document), [record])


def test_funding_raw_corruption_is_rejected():
    document, record, _ = inputs()
    record['payload'][0]['fundingRate'] = '0.02'
    with pytest.raises(ValueError, match='raw public response binding mismatch'):
        prepared(document, [record])


def test_duplicate_receipt_retains_first_availability():
    document, record, nominal = inputs()
    later = deepcopy(record)
    later.update(requested_at=START + 2600, received_at=START + 2700)
    value = prepared(document, [record, later])
    assert next(f for f in value['frames'] if f.timestamp == nominal + 2).funding_available_at == START + 2500


@pytest.mark.parametrize('offset', [-1, 1001])
def test_actual_settlement_outside_declared_window_rejected(offset):
    document, record, _ = inputs(offset=offset)
    record.update(requested_at=START + 2700, received_at=START + 2800)
    with pytest.raises(ValueError, match='FUNDING_OUTSIDE_FORECAST_WINDOW'):
        prepared(document, [record])


def test_conflicting_duplicate_not_silently_overwritten():
    document, record, _ = inputs()
    other = deepcopy(record)
    other['payload'][0]['fundingRate'] = '0.02'
    other = sealed({'record': other})['record']
    with pytest.raises(ValueError, match='CONFLICTING_FUNDING_SETTLEMENT'):
        prepared(document, [record, other])


def test_exact_nominal_settlement_merges_pause_and_event():
    document, record, nominal = inputs(offset=0)
    value = prepared(document, [record])
    frame = next(f for f in value['frames'] if f.timestamp == nominal)
    assert frame.funding_pause_until == frame.funding_available_at == START + 2500
    assert len(frame.market_events) == 1


def test_receipt_order_does_not_change_first_availability():
    document, record, nominal = inputs()
    later = deepcopy(record)
    later.update(requested_at=START + 2600, received_at=START + 2700)
    value = prepared(document, [later, record])
    assert next(f for f in value['frames'] if f.timestamp == nominal + 2).funding_available_at == START + 2500


def test_missing_actual_settlement_cannot_be_zero_filled():
    document, record, _ = inputs()
    record['payload'] = []
    record = sealed({'record': record})['record']
    with pytest.raises(ValueError, match='SINGLE_OBSERVED_SETTLEMENT_REQUIRED'):
        prepared(document, [record])
