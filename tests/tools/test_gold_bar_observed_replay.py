from decimal import Decimal as D

import pytest

from tests.tools.test_gold_bar_observe import recorded
from tests.research.test_gold_bar_dataset import raw_rows, STEP
from tests.execution.test_perpetual_public_replay import START, gold_capture_fixture
from tools.gold_bar_observed_replay import compare_observed


def test_actual_journal_reconstruction_and_original_v2_share_causal_decision_frame(tmp_path, monkeypatch):
    rows = raw_rows()
    shift = START // STEP * STEP - 1001 * STEP
    for row in rows:
        row[0] += shift
        row[6] += shift
    _, journal = recorded(tmp_path, monkeypatch, values=rows, received=START + 1500)
    result = compare_observed(journal=journal, capture=gold_capture_fixture(),
        release_identity={'commit': 'f' * 40}, quantity=D('.1'), initial_collateral=D('1000'), fee_rate=D('.001'))
    order = result['trials'][0]['simulation']['orders'][0]
    assert order['created_at'] == START + 1500
    assert order['filled_at'] == START + 2002
    assert result['dataset_identity']['closed_decision_times'] == [START + 1500]
    assert result['trials'][0]['outcome'] == 'OPEN_AT_END'


def test_old_or_future_quote_window_cannot_substitute_for_contemporaneous_quotes(tmp_path, monkeypatch):
    _, journal = recorded(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='COVERAGE'):
        compare_observed(journal=journal, capture=gold_capture_fixture(),
            release_identity={'commit': 'f' * 40}, quantity=D('.1'), initial_collateral=D('1000'), fee_rate=D('.001'))


def continuous_inputs(tmp_path, monkeypatch):
    import hashlib
    import json
    from copy import deepcopy
    from tests.execution.test_perpetual_public_replay import sealed
    from tools.gold_bar_journal import restore
    rows = raw_rows()
    shift = START // STEP * STEP - 1001 * STEP
    for row in rows:
        row[0] += shift
        row[6] += shift
    _, journal = recorded(tmp_path, monkeypatch, values=rows, received=START + 1500)
    cursor, _ = restore(journal)
    first = json.loads((journal / 'messages.jsonl').read_text())
    second = json.loads(first['raw'])
    second['data']['E'] += STEP
    candle = second['data']['k']
    candle.update(t=candle['t'] + STEP, T=candle['T'] + STEP, o='109', h='110', l='89', c='90')
    received = first['received_at_ms'] + STEP
    raw = json.dumps(second)
    decision = cursor.ingest(raw, received_at_ms=received)
    with (journal / 'messages.jsonl').open('a') as handle:
        handle.write(json.dumps({'received_at_ms': received, 'raw': raw}) + '\n')
    with (journal / 'decisions.jsonl').open('a') as handle:
        handle.write(json.dumps(decision) + '\n')
    summary = json.loads((journal / 'summary.json').read_text())
    summary.update(messages=2, closed_bars=2)
    for field, name in [('messages_sha256', 'messages.jsonl'), ('decisions_sha256', 'decisions.jsonl')]:
        summary[field] = hashlib.sha256((journal / name).read_bytes()).hexdigest()
    (journal / 'summary.json').write_text(json.dumps(summary))
    plan = json.loads((journal / 'plan.json').read_text())
    plan['seconds'] = 1800
    (journal / 'plan.json').write_text(json.dumps(plan))
    capture = gold_capture_fixture()
    sample = capture['samples'][0]
    capture['samples'] = []
    for i in range(64):
        row = deepcopy(sample)
        timestamp = START + (i * 1000 if i < 4 else 3000 + (i - 3) * 20000)
        for kind, delay in [('mark', 1), ('depth', 2)]:
            row[kind].update(requested_at=timestamp, received_at=timestamp + delay)
        row['mark']['payload']['time'] = timestamp
        row['depth']['payload'].update(T=timestamp, E=timestamp, lastUpdateId=i + 1)
        capture['samples'].append(row)
    capture['exchange_after'].update(requested_at=timestamp + 3, received_at=timestamp + 4)
    capture['schema_version'] = 'GOLD_PUBLIC_CONTINUOUS_CAPTURE_V1'
    return journal, sealed(capture), received


def test_continuous_quotes_keep_one_position_across_two_closed_bars(tmp_path, monkeypatch):
    from services.research.gold_bar_v2_strategy import GoldBarV2Strategy
    appended = []
    original = GoldBarV2Strategy.append_bars
    def append(self, bars):
        appended.append((self.candidate, self.last_time, self.entry_id, bars))
        return original(self, bars)
    monkeypatch.setattr(GoldBarV2Strategy, 'append_bars', append)
    journal, capture, received = continuous_inputs(tmp_path, monkeypatch)
    result = compare_observed(journal=journal, capture=capture,
        release_identity={'fixture': True}, quantity=D('.1'), initial_collateral=D('1000'), fee_rate=D('.001'))
    trial = result['trials'][0]
    assert trial['outcome'] == 'FLAT_TERMINAL'
    orders = trial['simulation']['orders']
    assert len(orders) == 2
    assert orders[0]['filled_at'] < received < orders[1]['filled_at']
    assert trial['simulation']['ending_state']['cashflow_identity']['residual'] == '0'
    assert result['economic_eligible'] is False
    channel = [r for r in appended if r[0] == 'channel']
    assert len(channel) == 2
    assert channel[0][2] is None and channel[1][2] is not None
    assert all(last < bars[0].available_ms for _, last, _, bars in channel)
    from tools.gold_bar_cost_replay import _compare_bars
    def whole_tape(**kwargs):
        kwargs['incremental_bars'] = False
        return _compare_bars(**kwargs)
    monkeypatch.setattr('tools.gold_bar_observed_replay._compare_bars', whole_tape)
    legacy = compare_observed(journal=journal, capture=capture,
        release_identity={'fixture': True}, quantity=D('.1'), initial_collateral=D('1000'), fee_rate=D('.001'))
    assert result == legacy
