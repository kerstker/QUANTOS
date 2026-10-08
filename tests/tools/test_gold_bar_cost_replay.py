import hashlib
import json
from decimal import Decimal as D

import pytest

from tests.execution.test_perpetual_public_replay import gold_capture_fixture, START
from tests.research.test_gold_bar_dataset import raw_rows, STEP
from tools.gold_bar_cost_replay import compare_public


def inputs():
    rows = raw_rows()
    shift = START // STEP * STEP - 1000 * STEP
    for row in rows:
        row[0] += shift
        row[6] += shift
    rows[-1][2], rows[-1][4] = '110', '109'
    raw = json.dumps(rows).encode()
    return dict(raw_bars=raw, bar_receipt=dict(requested_at_ms=START - 20,
        received_at_ms=START - 10, response_sha256=hashlib.sha256(raw).hexdigest()),
        capture=gold_capture_fixture(), release_identity={'commit': 'f' * 40},
        quantity=D('.1'), initial_collateral=D('1000'), fee_rate=D('.001'))


def test_three_candidates_and_cash_baseline_use_observed_quotes_only():
    report = compare_public(**inputs())
    assert len(report['trials']) == 3
    assert report['winner'] is None and report['economic_eligible'] is False
    assert report['cash_baseline']['orders'] == []
    channel = report['trials'][0]
    assert channel['candidate'] == 'channel' and channel['outcome'] == 'OPEN_AT_END'
    assert channel['simulation']['orders'][0]['filled_at'] == START + 1002
    assert D(channel['simulation']['reconciliation']['fee']) > 0
    assert report['fee_policy'] == 'EXPLICIT_UNCALIBRATED_SIMULATION_RATE'


def test_future_history_receipt_and_changed_bytes_are_rejected():
    for changed in ('time', 'hash'):
        request = inputs()
        if changed == 'time':
            request['bar_receipt']['received_at_ms'] = START + 100
        else:
            request['bar_receipt']['response_sha256'] = '0' * 64
        with pytest.raises(ValueError):
            compare_public(**request)


def test_insufficient_warmup_is_not_reported_as_no_signal():
    request = inputs()
    request['raw_bars'] = json.dumps(json.loads(request['raw_bars'])[-100:]).encode()
    request['bar_receipt']['response_sha256'] = hashlib.sha256(request['raw_bars']).hexdigest()
    result = compare_public(**request)
    assert all(t['outcome'] == 'INPUT_UNAVAILABLE' for t in result['trials'])
    assert all(o['status'] == 'WARMUP' for t in result['trials'] for o in t['signal_observations'])
