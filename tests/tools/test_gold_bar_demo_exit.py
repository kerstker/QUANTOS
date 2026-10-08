from copy import deepcopy

import pytest

from tests.tools.test_gold_bar_demo_entry import prepared, pilot_state as pilot_state
from tests.tools.test_gold_strategy_entry import origin_at


@pytest.fixture
def case(tmp_path, monkeypatch):
    from tools import gold_bar_demo_exit as tool

    def build(side):
        engine, _, at = prepared(tmp_path, side)
        entry = engine.prepare(origin=origin_at(at), now_ms=at)
        order = entry['orders'][0]
        context = dict(quantity='0.1' if side == 'BUY' else '-0.1', average_entry_price='100',
            ledger_state_hash='b'*64, position_episode=dict(status='REPORTED_FILL_START',
            opened_venue_at_ms=at, client_order_id=order['newClientOrderId'], reservation_sha256='c'*64))
        state = dict(reservations=[dict(record_hash='c'*64, payload=dict(orders=[order]))])
        monkeypatch.setattr(tool, '_state', lambda *a: deepcopy(state))
        monkeypatch.setattr(tool.position, 'load_context', lambda *a, **k: deepcopy(context))
        monkeypatch.setattr(tool.position, 'validate_origin', lambda *a, **k: {'equity': '5000'})
        monkeypatch.setattr(tool, '_venue_rules', lambda *a: None)
        monkeypatch.setattr(tool, '_venue_account_cap', lambda *a: None)
        origin = origin_at(at+1)
        book = next(r['data'] for r in origin['reads'] if r['kind'] == 'book')
        book['bidPrice'], book['askPrice'] = ('94', '94.1') if side == 'BUY' else ('105.9', '106')
        args = dict(engine=engine, entry_decision_ms=at-1, origin=origin,
                    dispatch=None, recovery=None, oms=None, now_ms=at+1)
        return tool, args, context
    return build


@pytest.mark.parametrize('side,closing', [('BUY', 'SELL'), ('SELL', 'BUY')])
def test_canonical_fill_basis_drives_both_reductions(case, side, closing):
    tool, args, context = case(side)
    result = tool.prepare(**args)
    assert result['reason'] == 'STOP'
    assert result['orders'][0]['side'] == closing
    assert result['orders'][0]['quantity'] == '0.1'
    assert result['orders'][0]['reduceOnly'] is True
    assert result['average_entry_price'] == '100'
    assert result['submission_enabled'] is False
    assert result == tool.prepare(**args)
    from tools.gold_strategy_request import constraints_from_draft
    assert constraints_from_draft(result, now_ms=args['now_ms'])['decision_sha256'] == result['result_hash']
    context['quantity'] = '0'
    context['position_episode'] = None
    assert tool.prepare(**args)['status'] == 'FLAT'


def test_other_episode_cannot_be_reduced(case):
    tool, args, context = case('SELL')
    context['position_episode']['client_order_id'] = 'QDunowned001'
    with pytest.raises(ValueError, match='OWNERSHIP'):
        tool.prepare(**args)


def test_partial_reduction_uses_remaining_quantity_and_latched_trigger(case):
    tool, args, context = case('BUY')
    first = tool.prepare(**args)
    context['quantity'] = '0.05'
    context['ledger_state_hash'] = 'e'*64
    book = next(r['data'] for r in args['origin']['reads'] if r['kind'] == 'book')
    book.update(bidPrice='100', askPrice='100.1')
    second = tool.prepare(**args)
    assert second['reason'] == 'STOP'
    assert second['orders'][0]['quantity'] == '0.05'
    assert second['orders'][0]['newClientOrderId'] != first['orders'][0]['newClientOrderId']


def test_persisted_account_loss_forces_owned_short_reduction(case, monkeypatch):
    tool, args, _ = case('SELL')
    book = next(r['data'] for r in args['origin']['reads'] if r['kind'] == 'book')
    book.update(bidPrice='100', askPrice='100.1')
    monkeypatch.setattr(tool.position, 'validate_origin', lambda *a, **k: {'equity': '4750'})
    result = tool.prepare(**args)
    assert result['reason'] == 'PILOT_LOSS_LIMIT'
    assert result['orders'][0]['reduceOnly'] is True
    assert result['orders'][0]['side'] == 'BUY'


def test_holding_and_stale_quote_do_not_emit_reduction(case):
    tool, args, _ = case('BUY')
    book = next(r['data'] for r in args['origin']['reads'] if r['kind'] == 'book')
    book.update(bidPrice='100', askPrice='100.1')
    assert tool.prepare(**args)['status'] == 'HOLDING'
    args['now_ms'] += 30001
    with pytest.raises(ValueError, match='QUOTE_CLOCK'):
        tool.prepare(**args)


def test_new_engine_recovers_exit_trigger_after_quote_recovers(case):
    import json
    from tools.gold_bar_demo_entry import BarDemoEntry
    tool, args, _ = case('BUY')
    first = tool.prepare(**args)
    old = args['engine']
    restored = BarDemoEntry(old._archive, candidate='channel', source_directory=old._source,
        warmup=old._cursor.bars[:-1], registered_at_ms=old._registered)
    bar = old._cursor.bars[-1]
    message = json.dumps(dict(stream='xauusdt@kline_15m', data=dict(e='kline',
        E=bar.available_ms, s='XAUUSDT', k=dict(t=bar.open_ms, T=bar.close_ms-1,
        s='XAUUSDT', i='15m', x=True, o=str(bar.open), h=str(bar.high), l=str(bar.low), c=str(bar.close)))))
    restored.ingest(message, received_at_ms=bar.available_ms)
    args['engine'] = restored
    book = next(r['data'] for r in args['origin']['reads'] if r['kind'] == 'book')
    book.update(bidPrice='100', askPrice='100.1')
    second = tool.prepare(**args)
    assert second['status'] == 'BAR_EXIT_DRAFT' and second['reason'] == 'STOP'
    assert second['exit_trigger_sha256'] == first['exit_trigger_sha256']


def test_changed_durable_exit_record_is_not_ignored(case, pilot_state):
    import json
    tool, args, _ = case('BUY')
    tool.prepare(**args)
    path, = list(pilot_state.parent.glob('bar-exit-trigger-*.json'))
    original = path.read_bytes()
    record = json.loads(original)
    record['reason'] = 'TIMEOUT'
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='BAR_EXIT_TRIGGER_HASH'):
        tool.prepare(**args)
    assert path.read_bytes() != original
