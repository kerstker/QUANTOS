"""Immutable public continuation must keep original accounts and registration."""
import base64
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import json

import pytest

from tests.execution.test_perpetual_public_replay import START, sealed
from tests.tools.test_gold_bar_observed_replay import continuous_inputs
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


def segmented_arguments(tmp_path, monkeypatch, *, overlap=False):
    from tools.gold_bar_experiment import JOURNAL_FILES

    journal, capture, _ = continuous_inputs(tmp_path, monkeypatch)
    original = {name: (journal/name).read_bytes() for name in JOURNAL_FILES}
    prefix = dict(original)
    for name in ('messages.jsonl', 'decisions.jsonl'):
        prefix[name] = original[name].splitlines(keepends=True)[0]
    summary = json.loads(prefix['summary.json'])
    summary.update(messages=1, closed_bars=1,
        messages_sha256=hashlib.sha256(prefix['messages.jsonl']).hexdigest(),
        decisions_sha256=hashlib.sha256(prefix['decisions.jsonl']).hexdigest())
    prefix['summary.json'] = json.dumps(summary).encode()
    first = deepcopy(capture)
    first['schema_version'] = 'GOLD_PUBLIC_REPLAY_CAPTURE_V1'
    first['samples'] = first['samples'][:4 if overlap else 3]
    end = 3000 if overlap else 2000
    first['exchange_after'].update(requested_at=START+end+3, received_at=START+end+4)
    second = deepcopy(capture)
    second['samples'] = second['samples'][3:]
    for field, received in [('exchange', 1900), ('candles', 1930), ('funding', 1960)]:
        second['bootstrap'][field].update(requested_at=START+received-1,
                                         received_at=START+received)
    def encode(files):
        return {n: base64.b64encode(b).decode() for n, b in files.items()}
    return dict(input_schema='QUANTOS_GOLD_SEGMENTED_INPUT_V1', capture=sealed(first),
        journal_files=encode(prefix), quantity=D('.1'), initial_collateral=D('1000'),
        fee_rate=D('.001'), release_identity={'fixture': True},
        continuation_segments=[dict(schema='QUANTOS_GOLD_CONTINUATION_SEGMENT_V1',
            capture=sealed(second), journal_files=encode(original))]), journal, capture


@pytest.mark.parametrize('overlap', [False, True])
@pytest.mark.parametrize('portfolio', [False, True])
def test_original_producer_retains_account_position_loss_and_one_trial_per_candidate(
        tmp_path, monkeypatch, overlap, portfolio):
    from tools.gold_bar_experiment import _execute
    from tools.gold_bar_observed_replay import compare_observed
    arguments, journal, capture = segmented_arguments(tmp_path, monkeypatch, overlap=overlap)
    if portfolio:
        arguments['portfolio_plan'] = dict(quantities={n: '.1' for n in ('channel','trend','reversion')},
            max_notional='100')
    result = _execute(arguments)
    whole = compare_observed(journal=journal, capture=capture, **{n:arguments[n] for n in
        ('quantity','initial_collateral','fee_rate','release_identity')})
    assert len(result['trials']) == 3
    for actual, reference in zip(result['trials'], whole['trials']):
        assert actual['outcome'] == reference['outcome']
        assert actual['simulation']['reconciliation'] == reference['simulation']['reconciliation']
        assert actual['loss_control'] == reference['loss_control']
        assert len(actual['simulation']['environment_segments']) == 2
        assert actual['simulation']['ending_state']['cashflow_identity']['residual'] == '0'
    channel = result['trials'][0]['simulation']
    assert len(channel['orders']) == 2
    assert channel['orders'][0]['filled_at'] < START+3002 < channel['orders'][1]['filled_at']
    assert result['cash_baseline']['reconciliation']['initial_equity'] == '1000'
    assert len(result['cash_baseline']['environment_segments']) == 2
    assert result['dataset_identity']['input_segment_count'] == 2
    assert len(result['dataset_identity']['segments']) == 2
    assert result['economic_eligible'] is result['submission_enabled'] is False
    if portfolio:
        assert len(result['portfolio']['simulation']['environment_segments']) == 2
        assert result['portfolio']['simulation']['reconciliation']['initial_equity'] == '1000'


@pytest.mark.parametrize('damage', ['plan', 'prefix', 'quote', 'omit', 'mark', 'gap', 'override', 'marker'])
def test_changed_history_and_unsafe_continuation_are_rejected(tmp_path, monkeypatch, damage):
    from tools.gold_bar_experiment import _execute
    args, _, whole = segmented_arguments(tmp_path, monkeypatch, overlap=damage in ('quote','omit'))
    second = args['continuation_segments'][0]
    if damage == 'plan':
        raw = base64.b64decode(second['journal_files']['plan.json']) + b' '
        second['journal_files']['plan.json'] = base64.b64encode(raw).decode()
    elif damage == 'prefix':
        raw = base64.b64decode(second['journal_files']['messages.jsonl'])
        lines = raw.splitlines(keepends=True)
        lines[0] = b' ' + lines[0]
        raw = b''.join(lines)
        second['journal_files']['messages.jsonl'] = base64.b64encode(raw).decode()
        summary = json.loads(base64.b64decode(second['journal_files']['summary.json']))
        summary['messages_sha256'] = hashlib.sha256(raw).hexdigest()
        second['journal_files']['summary.json'] = base64.b64encode(json.dumps(summary).encode()).decode()
    elif damage == 'quote':
        second['capture']['samples'][0]['depth']['payload']['lastUpdateId'] += 99
        second['capture'] = sealed(second['capture'])
    elif damage == 'omit':
        capture = deepcopy(whole)
        capture['samples'] = [s for index, s in enumerate(capture['samples']) if index >= 1 and index != 2]
        for field, received in [('exchange',700), ('candles',730), ('funding',760)]:
            capture['bootstrap'][field].update(requested_at=START+received-1, received_at=START+received)
        second['capture'] = sealed(capture)
    elif damage == 'mark':
        second['capture']['samples'][0]['mark']['payload']['time'] = START+1999
        second['capture'] = sealed(second['capture'])
    elif damage == 'gap':
        capture = second['capture']
        for sample in capture['samples']:
            for field in ('mark','depth'):
                for clock in ('requested_at','received_at'):
                    sample[field][clock] += 60_000
            sample['mark']['payload']['time'] += 60_000
            for clock in ('T','E'):
                sample['depth']['payload'][clock] += 60_000
        for clock in ('requested_at','received_at'):
            capture['exchange_after'][clock] += 60_000
        second['capture'] = sealed(capture)
    elif damage == 'override':
        second['initial_collateral'] = '1000'
    else:
        del args['input_schema']
    with pytest.raises(ValueError, match='SEGMENT'):
        _execute(args)


def funding_arguments(tmp_path, monkeypatch, *, early=False):
    args, _, capture = segmented_arguments(tmp_path, monkeypatch, overlap=True)
    # Synthetic archived gap exceeds this tape; do not weaken the original
    # completeness guard to accept a fixture with omitted nominal settlements.
    capture['bootstrap']['funding']['payload'][0]['fundingTime'] -= 86_400_000
    second_nominal = 3500 if early else 4500
    for index, sample in enumerate(capture['samples']):
        timestamp = START + (index*1000 if index < 7 else 6000+(index-6)*20000)
        for field, delay in [('mark',1), ('depth',2)]:
            sample[field].update(requested_at=timestamp, received_at=timestamp+delay)
        sample['mark']['payload'].update(time=timestamp,
            nextFundingTime=START+(2500 if index < 3 else
                second_nominal if index < (4 if early else 5) else 10_000_000))
        sample['depth']['payload'].update(T=timestamp, E=timestamp)
    capture['exchange_after'].update(requested_at=timestamp+3, received_at=timestamp+4)
    first = deepcopy(capture)
    first['schema_version'] = 'GOLD_PUBLIC_REPLAY_CAPTURE_V1'
    first['samples'] = first['samples'][:4]
    first['exchange_after'].update(requested_at=START+3003, received_at=START+3004)
    second = deepcopy(capture)
    second['samples'] = second['samples'][3:]
    for field, received in [('exchange',2900), ('candles',2930), ('funding',2960)]:
        second['bootstrap'][field].update(requested_at=START+received-1, received_at=START+received)
    records = []
    for nominal, rate in [(2500,'0.01'), (second_nominal,'0.02')]:
        record = deepcopy(capture['bootstrap']['funding'])
        record.update(requested_at=START+nominal+290, received_at=START+nominal+300,
            payload=[dict(symbol='XAUUSDT', fundingTime=START+nominal+2,
                          fundingRate=rate, markPrice='100')])
        records.append(sealed({'record':record})['record'])
    args.update(capture=sealed(first), observed_funding_records=[records[0]], settlement_window_ms=1000)
    args['continuation_segments'][0].update(capture=sealed(second),
        observed_funding_records=[records[1]], settlement_window_ms=1000)
    return args


@pytest.mark.parametrize('early', [False, True])
def test_two_received_funding_segments_keep_one_original_account_and_cold_receipt_statistics(tmp_path, monkeypatch, early):
    from tools.gold_bar_experiment import _execute, _encode
    from services.validation.gold_native_statistics import funding_settlements, receipt_available_history
    args = funding_arguments(tmp_path, monkeypatch, early=early)
    result = _execute(args)
    simulation = result['trials'][0]['simulation']
    assert simulation['reconciliation']['funding'] == '-0.3'
    assert len([e for e in simulation['ledger_events'] if e['event_type'] == 'FUNDING_SETTLEMENT']) == 2
    assert simulation['reconciliation']['residual'] == '0'
    assert len(simulation['funding_timing_assumptions']) == 2
    assert 'FORECAST_TIMING_BOUND_UNVERIFIED' in simulation['authority_blockers']
    settlements = funding_settlements(_encode(args), result['dataset_identity'])
    second_nominal = 3500 if early else 4500
    assert [v['settlement_at_ms'] for v in settlements] == [START+2502, START+second_nominal+2]
    history = receipt_available_history(simulation, settlements)
    assert any(p['timestamp'] == START+2800 for p in history)
    assert any(p['timestamp'] == START+second_nominal+300 for p in history)
    swapped = deepcopy(result['dataset_identity'])
    swapped['quote_manifest']['segments'].reverse()
    with pytest.raises(ValueError, match='SEGMENT_IDENTITY'):
        funding_settlements(_encode(args), swapped)
