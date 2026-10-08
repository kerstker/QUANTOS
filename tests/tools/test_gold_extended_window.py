"""Bounded longer forward tapes keep the original account and timing rules."""
import asyncio
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import io
import json
import shutil

import pytest

from tests.execution.test_perpetual_public_replay import START, gold_capture_fixture, sealed
from tests.research.test_gold_bar_dataset import STEP, raw_rows
from tests.tools.test_gold_bar_observe import recorded


def extended_inputs(tmp_path, monkeypatch):
    from services.research.gold_bar_dataset import load_bars
    from services.research.gold_bar_stream import GoldBarStream
    first_close = START // STEP * STEP + STEP
    rows = raw_rows()
    shift = first_close - 1001 * STEP
    for row in rows:
        row[0] += shift
        row[6] += shift
        for index in (1, 2, 3, 4):
            row[index] = str(D(row[index]) * 40)
    _, journal = recorded(tmp_path, monkeypatch, values=rows, received=first_close + 11500)
    receipt = json.loads((journal / 'warmup-receipt.json').read_text())
    cursor = GoldBarStream(load_bars((journal / 'warmup.json').read_bytes(),
        requested_at_ms=receipt['requested_at_ms'], received_at_ms=receipt['received_at_ms']))
    original = json.loads((journal / 'messages.jsonl').read_text())
    messages, decisions = [], []
    for i, price in enumerate(('109', '109', '90')):
        message = json.loads(original['raw'])
        received = original['received_at_ms'] + i * STEP
        message['data']['E'] = received
        candle = message['data']['k']
        candle.update(t=candle['t'] + i * STEP, T=candle['T'] + i * STEP,
                      o='4000', h='4400', l='3560' if i == 2 else '3960', c=str(D(price) * 40))
        raw = json.dumps(message)
        messages.append(dict(received_at_ms=received, raw=raw))
        decisions.append(cursor.ingest(raw, received_at_ms=received))
    summary = json.loads((journal / 'summary.json').read_text())
    summary.update(schema='GOLD_BAR_EXTENDED_STREAM_SUMMARY_V1', messages=3, closed_bars=3)
    for name, values in [('messages', messages), ('decisions', decisions)]:
        raw = ''.join(json.dumps(row) + '\n' for row in values).encode()
        (journal / (name + '.jsonl')).write_bytes(raw)
        summary[name + '_sha256'] = hashlib.sha256(raw).hexdigest()
    (journal / 'summary.json').write_text(json.dumps(summary))
    plan = json.loads((journal / 'plan.json').read_text())
    plan.update(seconds=2000, extended=True)
    (journal / 'plan.json').write_text(json.dumps(plan))
    capture = gold_capture_fixture()
    template = capture['samples'][0]
    timestamps = sorted(set(range(first_close - 10000, first_close + 2 * STEP + 30001, 10000))
                        | {first_close + i * STEP + 12000 for i in range(3)})
    capture['samples'] = []
    for i, timestamp in enumerate(timestamps):
        sample = deepcopy(template)
        for kind, delay in [('mark', 1), ('depth', 2)]:
            sample[kind].update(requested_at=timestamp, received_at=timestamp + delay)
        sample['mark']['payload']['time'] = timestamp
        sample['mark']['payload']['markPrice'] = '4000'
        sample['depth']['payload'].update(T=timestamp, E=timestamp, lastUpdateId=i + 1)
        sample['depth']['payload'].update(bids=[['3996', '1']], asks=[['4004', '1']])
        capture['samples'].append(sample)
    capture['exchange_after'].update(requested_at=timestamp + 3, received_at=timestamp + 4)
    capture.update(schema_version='GOLD_PUBLIC_EXTENDED_CAPTURE_V1', window=dict(
        close_times_ms=[first_close + i * STEP for i in range(3)], post_close_ms=30000))
    return journal, sealed(capture), first_close


@pytest.mark.parametrize('profile', ['LEGACY_V1', 'FRESH_CADENCE_V1'])
@pytest.mark.parametrize('bars', [3, 4])
def test_extended_capture_covers_every_close_without_reset_or_shortening(monkeypatch, bars, profile):
    from tools import perpetual_public_capture as tool
    clock, calls = [800.0], []

    class Response(io.BytesIO):
        def geturl(self):
            return calls[-1]

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            clock[0] += 0.1
            return Response(b'{"fixture":true}')

    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(tool.time, 'time', lambda: clock[0])
    monkeypatch.setattr(tool.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(tool.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    closes = tuple(900000 * (i + 1) for i in range(bars))
    result = tool.capture_gold_extended(close_times=closes, retry_profile=profile)
    assert result['schema_version'] == 'GOLD_PUBLIC_EXTENDED_CAPTURE_V1'
    receipts = [r['depth']['received_at'] for r in result['samples']]
    assert receipts[0] < closes[0] and closes[-1] + 30000 <= receipts[-1] <= closes[-1] + 60000
    assert 61 <= len(receipts) <= 720
    assert max(b - a for a, b in zip(receipts, receipts[1:])) <= 30000
    for close in result['window']['close_times_ms']:
        assert min(r for r in receipts if r >= close) - close < 2000
    assert sum(url.endswith('/exchangeInfo') for url in calls) == 2
    assert result['funding_after']['requested_at'] >= receipts[-1]


def test_extended_observer_records_reconstructable_journal_under_explicit_limit(tmp_path, monkeypatch):
    from tools.gold_bar_journal import restore
    original_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, 'timeout', lambda seconds: original_timeout(0.01))
    result, journal = recorded(tmp_path, monkeypatch, seconds=2000, extended=True)
    assert result['schema'] == 'GOLD_BAR_EXTENDED_STREAM_SUMMARY_V1'
    assert result['status'] == 'OBSERVED'
    assert restore(journal)[1]['closed_bars'] == 1
    from tools.gold_bar_observe import observe
    with pytest.raises(ValueError, match='BAR_OBSERVATION_DURATION'):
        asyncio.run(observe(tmp_path / 'history', tmp_path / 'unused', seconds=3601, extended=True))


@pytest.mark.parametrize('closes', [(900000, 1800000), (900000, 2700000, 3600000),
                                  (900000, 1800000, 2700000, 3600000, 4500000)])
def test_invalid_long_window_cannot_start_network(closes, monkeypatch):
    from tools import perpetual_public_capture as tool
    def unexpected(*args):
        pytest.fail('network must not be reached')
    monkeypatch.setattr(tool.urllib.request, 'build_opener', unexpected)
    with pytest.raises(ValueError, match='INVALID_EXTENDED_GOLD_WINDOW'):
        tool.capture_gold_extended(close_times=closes)


def test_extended_tape_holds_then_exits_on_third_bar_with_original_cash_identity(tmp_path, monkeypatch):
    from tools.gold_bar_observed_replay import compare_observed
    journal, capture, first_close = extended_inputs(tmp_path, monkeypatch)
    result = compare_observed(journal=journal, capture=capture, release_identity={'fixture': True},
        quantity=D('.02'), initial_collateral=D('1000'), fee_rate=D('.001'))
    trial = result['trials'][0]
    orders = trial['simulation']['orders']
    assert trial['outcome'] == 'FLAT_TERMINAL'
    assert len(orders) == 2
    assert first_close < orders[0]['filled_at'] < first_close + STEP
    assert orders[1]['created_at'] >= first_close + 2 * STEP + 11500
    assert orders[1]['filled_at'] > orders[1]['created_at']
    assert trial['simulation']['ending_state']['cashflow_identity']['residual'] == '0'
    assert result['economic_eligible'] is False


@pytest.mark.parametrize('loss', [False, True])
def test_extended_program_uses_one_registered_experiment_for_three_closes(tmp_path, monkeypatch, loss):
    from tools import gold_bar_program as program, gold_bar_experiment, gold_bar_research
    journal, capture, close = extended_inputs(tmp_path, monkeypatch)
    if loss:
        for sample in capture['samples']:
            if sample['depth']['received_at'] >= close + STEP:
                sample['mark']['payload']['markPrice'] = '1000'
                sample['depth']['payload'].update(bids=[['999', '1']], asks=[['1001', '1']])
        capture = sealed(capture)
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path / 'state'))
    for module in (program, gold_bar_experiment, gold_bar_research):
        monkeypatch.setattr(module, 'source_identity', lambda: {'fixture': True})
    monkeypatch.setattr(program, 'next_close', lambda now: close)
    monkeypatch.setattr(program.time, 'time_ns', lambda: (close - 100000) * 1000000)

    async def wait(target):
        pass

    async def observe(history, output, *, seconds, extended):
        assert extended is True and 1800 < seconds <= 3600
        shutil.copytree(journal, output)
        return json.loads((journal / 'summary.json').read_text())

    def collect(**kwargs):
        assert kwargs['close_times'] == (close, close + STEP, close + 2 * STEP)
        return capture

    monkeypatch.setattr(program, 'wait_for_capture', wait)
    monkeypatch.setattr(program, 'capture', lambda path: path.mkdir())
    monkeypatch.setattr(program, 'observe', observe)
    monkeypatch.setattr(program, 'capture_gold_extended', collect, raising=False)
    result = asyncio.run(program.run_program(program_id=1, first_attempt=1,
        rounds=2 if loss else 1, continuous=True, window_bars=3, max_capture_failures=2,
        register_original_trials=False))
    row = result['rounds'][0]
    assert result['status'] == ('RESEARCH_PROGRAM_STOPPED_LOSS_LIMIT' if loss else 'RESEARCH_PROGRAM_COMPLETE')
    assert row['report']['candidates'][0]['outcome'] == 'FLAT_TERMINAL'
    assert row['review']['trial_count'] == 3
    assert row['review']['status'] == 'INDEPENDENT_AUTHORITY_UNCONFIGURED'
    assert row['report']['candidates'][0]['loss_control']['status'] == ('STOPPED_FLAT' if loss else 'ACTIVE')
    if loss:
        assert len(result['rounds']) == 1
        assert result['loss_stop']['candidates'] == ['channel', 'trend']
        assert not (tmp_path / 'state/data/cache/gold_bar_research/requests/2').exists()


@pytest.mark.parametrize('damage', ['missing_end', 'gap', 'funding', 'budget'])
def test_extended_tape_still_rejects_incomplete_or_uncovered_inputs(tmp_path, monkeypatch, damage):
    from services.execution.perpetual_public_replay import prepare_public_replay
    _, capture, _ = extended_inputs(tmp_path, monkeypatch)
    if damage == 'missing_end':
        capture['samples'] = capture['samples'][:-3]
    elif damage == 'gap':
        del capture['samples'][5:10]
    elif damage == 'funding':
        capture['samples'][-1]['mark']['payload']['nextFundingTime'] += STEP
    else:
        capture['samples'] *= 10
    with pytest.raises(ValueError):
        prepare_public_replay(sealed(capture))
