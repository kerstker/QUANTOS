import json
from pathlib import Path

import pytest

from tests.tools.test_gold_bar_definition import archive
from tests.tools.test_gold_strategy_entry import origin_at
from tests.research.test_gold_bar_dataset import raw_rows, STEP
from services.research.gold_bar_dataset import load_bars


@pytest.fixture(autouse=True)
def pilot_state(tmp_path, monkeypatch):
    from live.execution import demo_position_context as position
    root = tmp_path / 'executor'
    area = root / 'gold-pilot'
    area.mkdir(parents=True)
    monkeypatch.setattr(position, '_ROOT', root)
    monkeypatch.setattr(position, '_custody', lambda *a, **kw: None)
    start = 1000 * STEP + 100
    config = dict(schema='GOLD_DEMO_PILOT_CONTROL_V1', environment='BINANCE_USDM_DEMO',
        symbol='XAUUSDT', account_ref_sha256=origin_at(start)['account_ref_sha256'],
        opening_origin_sha256='a'*64, initial_equity='5000', started_at_ms=start,
        ends_at_ms=start+86400000, max_loss_fraction='0.05', submission_enabled=False)
    (area / 'configuration.json').write_text(json.dumps(config))
    return area


def prepared(tmp_path, side, use_prepared=False):
    from tools.gold_bar_demo_entry import BarDemoEntry
    root = archive(tmp_path)
    end = 1000 * STEP
    warmup = load_bars(json.dumps(raw_rows()).encode(), requested_at_ms=end, received_at_ms=end+10)
    from tools.gold_bar_definition import PreparedBarDefinition
    source = Path(__file__).resolve().parents[2]
    definition = PreparedBarDefinition(root, candidate='channel', source_directory=source) if use_prepared else None
    engine = BarDemoEntry(root, candidate='channel', source_directory=source,
                          warmup=warmup, registered_at_ms=end+100, prepared_definition=definition)
    received = end+STEP+20
    message = json.dumps(dict(stream='xauusdt@kline_15m', data=dict(e='kline',
        E=received, s='XAUUSDT', k=dict(t=end, T=end+STEP-1, s='XAUUSDT', i='15m', x=True,
        o='100', h='110' if side == 'BUY' else '102', l='99' if side == 'BUY' else '89',
        c='109' if side == 'BUY' else '90'))))
    engine.ingest(message, received_at_ms=received)
    return engine, root, received+1


@pytest.mark.parametrize('side,price', [('BUY', '100.1'), ('SELL', '100')])
def test_both_sides_reuse_original_demo_terms_without_execution(tmp_path, side, price):
    engine, _, at = prepared(tmp_path, side)
    result = engine.prepare(origin=origin_at(at), now_ms=at)
    assert result['status'] == 'BAR_ENTRY_DRAFT'
    order, = result['orders']
    assert order['side'] == side and order['price'] == price
    assert order['quantity'] == '0.1' and order['reduceOnly'] is False
    assert result['signal_valid_until_ms'] == at - 1 + 5000
    assert result['exit_plan']['stop_basis'] == 'ACTUAL_FILLED_AVERAGE_PRICE'
    assert result['submission_enabled'] is False and result['risk_status'] == 'NOT_RUN'
    assert result == engine.prepare(origin=origin_at(at), now_ms=at)
    from tools.gold_strategy_request import constraints_from_draft
    bound = constraints_from_draft(result, now_ms=at)
    assert bound['strategy_definition_sha256'] == result['definition_hash']
    assert bound['valid_until_ms'] <= result['signal_valid_until_ms']
    from live.execution.demo_session_inputs import decode
    with pytest.raises(ValueError):
        decode(json.dumps(result).encode(), now_ms=at)


@pytest.mark.parametrize('damage', ['expired', 'archive', 'pending', 'minimum'])
def test_entry_rejects_stale_or_changed_evidence_and_keeps_size(tmp_path, damage):
    engine, root, at = prepared(tmp_path, 'SELL')
    origin = origin_at(at)
    if damage == 'expired':
        at += 5000
    elif damage == 'archive':
        (root / 'report.json').write_text('{}')
    elif damage == 'pending':
        origin['status'] = 'FAILED'
    else:
        rules = next(r['data'] for r in origin['reads'] if r['kind'] == 'rules')
        for group in (rules['terms']['filters'], rules['venue_filters']):
            for item in group:
                if item['filterType'] == 'MIN_NOTIONAL':
                    item['notional'] = '100'
    with pytest.raises(ValueError):
        engine.prepare(origin=origin, now_ms=at)


@pytest.mark.parametrize('offset', [-100, 100])
def test_short_entry_uses_clock_envelope_and_stable_identity(tmp_path, offset):
    engine, _, at = prepared(tmp_path, 'SELL')
    original = engine.prepare(origin=origin_at(at), now_ms=at)
    origin = origin_at(at)
    for row in origin['reads']:
        if row['kind'].startswith('clock'):
            row['data']['serverTime'] += offset
        elif row['kind'] == 'book':
            row['data']['time'] += offset
            row['data']['bidPrice'] = '99.9'
    revised = engine.prepare(origin=origin, now_ms=at)
    assert revised['orders'][0]['newClientOrderId'] == original['orders'][0]['newClientOrderId']
    assert revised['valid_until_ms'] == original['valid_until_ms']
    assert revised['orders'][0]['price'] == '99.9'
    next(r['data'] for r in origin['reads'] if r['kind'] == 'book')['time'] -= 10
    with pytest.raises(ValueError, match='QUOTE_NOT_CURRENT'):
        engine.prepare(origin=origin, now_ms=at)


def test_invalid_stream_cannot_reuse_previous_signal(tmp_path):
    engine, _, at = prepared(tmp_path, 'BUY')
    with pytest.raises(ValueError):
        engine.ingest('{}', received_at_ms=at)
    with pytest.raises(ValueError, match='STREAM_BLOCKED'):
        engine.prepare(origin=origin_at(at), now_ms=at)


def test_missing_pilot_cannot_prepare_opening(tmp_path, pilot_state):
    engine, _, at = prepared(tmp_path, 'SELL')
    pilot_state.rename(pilot_state.parent / 'saved-pilot')
    with pytest.raises(ValueError, match='PILOT_REQUIRED'):
        engine.prepare(origin=origin_at(at), now_ms=at)


def test_loss_stop_uses_account_equity_and_survives_new_engine(tmp_path, pilot_state):
    engine, root, at = prepared(tmp_path, 'BUY')
    origin = json.loads(json.dumps(origin_at(at)).replace('"5000"', '"4750"'))
    result = engine.prepare(origin=origin, now_ms=at)
    assert result['status'] == 'PILOT_STOPPED' and result['orders'] == []
    assert result['pilot_control']['reason'] == 'LOSS_LIMIT'
    stopped = (pilot_state / 'stopped.json').read_bytes()
    other = tmp_path / 'new-engine'
    other.mkdir()
    restarted, _, _ = prepared(other, 'SELL')
    result = restarted.prepare(origin=origin_at(at+1), now_ms=at+1)
    assert result['status'] == 'PILOT_STOPPED' and result['orders'] == []
    assert (pilot_state / 'stopped.json').read_bytes() == stopped


def test_pilot_expiry_blocks_even_current_signal(tmp_path, pilot_state):
    engine, _, at = prepared(tmp_path, 'SELL')
    path = pilot_state / 'configuration.json'
    config = json.loads(path.read_text())
    config['started_at_ms'] -= 86400000
    config['ends_at_ms'] -= 86400000
    path.write_text(json.dumps(config))
    result = engine.prepare(origin=origin_at(at), now_ms=at)
    assert result['status'] == 'PILOT_STOPPED' and result['orders'] == []
    assert result['pilot_control']['reason'] == 'DURATION_COMPLETE'


def test_prepared_entry_uses_same_signal_and_rejects_archive_change(tmp_path):
    engine, root, at = prepared(tmp_path, 'BUY', use_prepared=True)
    draft = engine.prepare(origin=origin_at(at), now_ms=at)
    assert draft['status'] == 'BAR_ENTRY_DRAFT'
    assert draft['signal_valid_until_ms'] == at-1+5000
    assert draft['orders'][0]['side'] == 'BUY'
    with (root/'report.json').open('ab') as handle:
        handle.write(b' ')
    with pytest.raises(ValueError, match='PREPARED_CHANGED'):
        engine.prepare(origin=origin_at(at), now_ms=at)
