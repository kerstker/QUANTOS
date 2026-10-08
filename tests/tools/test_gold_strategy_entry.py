import json

import pytest

from tests.tools.test_gold_forward_review import captured
from tests.tools.test_gold_demo_execution_origin import capture, NOW


def origin_at(at):
    origin, _ = capture()
    def shift(value):
        if type(value) is int and NOW-86400000 <= value <= NOW+86400000:
            return value+at-NOW
        if type(value) is dict:
            return {k: shift(v) for k, v in value.items()}
        if type(value) is list:
            return [shift(v) for v in value]
        return value
    origin = shift(origin)
    data = {row['kind']: row['data'] for row in origin['reads']}
    data['mark'].update(markPrice='100', indexPrice='100')
    data['book'].update(askPrice='100.1', bidPrice='100')
    # Explicit synthetic venue terms for the synthetic 100-price strategy.
    for group in (data['rules']['terms']['filters'], data['rules']['venue_filters']):
        for item in group:
            if item['filterType'] == 'MIN_NOTIONAL':
                item['notional'] = '5'
    return origin


def test_strategy_signal_produces_bounded_demo_draft(tmp_path, monkeypatch):
    from tools.gold_strategy_entry import prepare
    root, trial, definition = captured(tmp_path, monkeypatch)
    rows = [json.loads(row) for row in (root/'decisions.jsonl').read_bytes().splitlines()]
    at = rows[-1]['decision_available_at_ms']+1
    result = prepare(root, strategy_trial=trial, origin=origin_at(at), now_ms=at)
    assert result['status'] == 'STRATEGY_ENTRY_DRAFT'
    order = result['orders'][0]
    assert order['quantity'] == definition['exit_and_size_plan']['quantity']
    assert order['price'] == '100.1' and order['side'] == 'BUY'
    assert result['signal_valid_until_ms'] == rows[-1]['decision']['decision_ms']+50
    assert result['valid_until_ms'] == at+20
    assert result['submission_enabled'] is False
    assert result == prepare(root, strategy_trial=trial, origin=origin_at(at), now_ms=at)
    from live.execution.demo_session_inputs import decode
    with pytest.raises(ValueError):
        decode(json.dumps(result).encode(), now_ms=at)


@pytest.mark.parametrize('damage', ['expired', 'quote_before_signal', 'price_outside_plan', 'pending', 'minimum_notional'])
def test_entry_rejects_expired_signal_or_bad_account_context(tmp_path, monkeypatch, damage):
    from tools.gold_strategy_entry import prepare
    root, trial, _ = captured(tmp_path, monkeypatch)
    rows = [json.loads(row) for row in (root/'decisions.jsonl').read_bytes().splitlines()]
    at = rows[-1]['decision_available_at_ms']+1
    origin = origin_at(at)
    data = {row['kind']: row['data'] for row in origin['reads']}
    if damage == 'expired':
        at += 51
    elif damage == 'quote_before_signal':
        data['book']['time'] -= 2
    elif damage == 'price_outside_plan':
        data['book']['askPrice'] = '106'
    elif damage == 'pending':
        origin['status'] = 'FAILED'
    else:
        for group in (data['rules']['terms']['filters'], data['rules']['venue_filters']):
            for item in group:
                if item['filterType'] == 'MIN_NOTIONAL':
                    item['notional'] = '100'
    with pytest.raises(ValueError):
        prepare(root, strategy_trial=trial, origin=origin, now_ms=at)


def test_no_signal_never_builds_order(tmp_path, monkeypatch):
    from tools.gold_strategy_entry import prepare
    root, trial, _ = captured(tmp_path, monkeypatch, sell_pressure=False)
    result = prepare(root, strategy_trial=trial, origin=None, now_ms=2**62)
    assert result['status'] == 'NO_ENTRY_SIGNAL'
    assert result['orders'] == []


@pytest.mark.parametrize('offset', [-100, 100])
def test_entry_quote_deadline_uses_local_clock_interval(tmp_path, monkeypatch, offset):
    from tools.gold_strategy_entry import prepare
    root, trial, _ = captured(tmp_path, monkeypatch)
    rows = [json.loads(row) for row in (root/'decisions.jsonl').read_bytes().splitlines()]
    at = rows[-1]['decision_available_at_ms']+1
    origin = origin_at(at)
    for row in origin['reads']:
        if row['kind'].startswith('clock'):
            row['data']['serverTime'] += offset
        elif row['kind'] == 'book':
            row['data']['time'] += offset
    result = prepare(root, strategy_trial=trial, origin=origin, now_ms=at)
    assert result['status'] == 'STRATEGY_ENTRY_DRAFT'
    assert result['valid_until_ms'] == at+20
    # A venue timestamp that numerically looks current can still precede the
    # locally available signal once the observed clock offset is applied.
    for row in origin['reads']:
        if row['kind'] == 'book':
            row['data']['time'] -= 2
    with pytest.raises(ValueError, match='QUOTE_NOT_CURRENT_FOR_SIGNAL'):
        prepare(root, strategy_trial=trial, origin=origin, now_ms=at)
