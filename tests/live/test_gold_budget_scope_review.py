"""Read-only successor review never renews an expired execution permission."""
from copy import deepcopy
from decimal import Decimal

import pytest

from live.execution import demo_market_partition_runtime as runtime
from tests.live.test_demo_market_partition import NOW, book as book
from tests.live.test_demo_market_partition_runtime import installed as installed


def test_expired_key_allows_only_diagnostic_budget_review(installed):
    value = installed
    value['registry']['keys'][-1]['valid_until_ms'] = NOW + 139999
    before = deepcopy(value['state']), deepcopy(value['registry']), value['events'].row_count()
    report = runtime.gold_budget_scope_review(state=value['state'], origin=value['origin'],
        as_of_ms=NOW + 140000)
    assert report['status'] == 'GOLD_BUDGET_SCOPE_REVIEW_REQUIRED'
    assert report['gold_position_context'] is None and report['gold_risk'] is None
    assert report['observed_usdt_wallet'] == '4200'
    assert report['legacy_risk']['canonical_wallet'] == '4999.6801'
    assert report['legacy_risk']['initial_equity'] == '5000'
    assert sum(map(Decimal, report['legacy_risk']['capacity_consumed'].values())) == Decimal('239.8')
    assert report['collateral_conversion'] == 'NOT_PERFORMED'
    assert all(report[key] is False for key in ('approval_granted', 'source_verified',
        'authorization_current_verified', 'budget_enrolled', 'submission_enabled', 'economic_authority'))
    with pytest.raises(ValueError, match='PARTITION_NEW_GOLD_AUTHORITY_REQUIRED'):
        runtime.resolve_context(state=value['state'], origin=value['origin'], as_of_ms=NOW + 140000)
    assert before == (value['state'], value['registry'], value['events'].row_count())


@pytest.mark.parametrize('damage', ['activation_missing', 'local_missing', 'source', 'registry_prefix',
    'no_gold_key', 'early_key', 'old_state', 'old_wallet', 'new_wallet', 'unowned_position',
    'stale', 'clock_bool', 'account', 'symbol'])
def test_budget_review_does_not_relax_original_account_and_partition_checks(installed, monkeypatch, damage):
    value = installed;state = deepcopy(value['state']);origin = deepcopy(value['origin']);at = NOW+140000
    if damage == 'activation_missing': value['activation'].unlink()
    elif damage == 'local_missing': value['executor'].unlink()
    elif damage == 'source': monkeypatch.setattr(runtime, 'executing_source_commit', lambda: 'd'*40)
    elif damage == 'registry_prefix': value['registry']['keys'][0]['status'] = 'REVOKED'
    elif damage == 'no_gold_key': value['registry']['keys'] = value['legacy']['keys']
    elif damage == 'early_key': value['registry']['keys'][-1]['valid_from_ms'] = NOW+129999
    elif damage == 'old_state': state['oms'][0]['quantity'] += .001
    elif damage in ('old_wallet', 'new_wallet'):
        for read in origin['reads']:
            if read['kind'].startswith('account'):
                asset = 'USDC' if damage == 'old_wallet' else 'USDT'
                row = next(item for item in read['data']['assets'] if item['asset'] == asset)
                for key in ('walletBalance', 'crossWalletBalance', 'availableBalance', 'marginBalance'):
                    row[key] = '6000'
    elif damage == 'unowned_position':
        for read in origin['reads']:
            if read['kind'] == 'positions': read['data'] = [dict(symbol='BTCUSDC', positionAmt='.002')]
    elif damage == 'stale': at += 31000
    elif damage == 'clock_bool': at = True
    elif damage == 'account': origin['account_ref_sha256'] = 'f'*64
    else: origin['symbol'] = 'BTCUSDC'
    before = deepcopy(value['state']), value['events'].row_count()
    with pytest.raises(ValueError): runtime.gold_budget_scope_review(state=state, origin=origin, as_of_ms=at)
    assert before == (value['state'], value['events'].row_count())


@pytest.mark.parametrize('damage', ['risk_type', 'capacity', 'legacy_context', 'original_cut', 'descriptor',
    'full_state', 'gold_context', 'gold_risk', 'origin', 'registry', 'authority_bool', 'authority_zero', 'time_bool'])
def test_private_response_consumer_rejects_changed_report(installed, damage):
    value = installed;at = NOW+140000
    report = runtime.gold_budget_scope_review(state=value['state'], origin=value['origin'], as_of_ms=at)
    if damage == 'risk_type': report['legacy_risk']['day_id'] = float(report['legacy_risk']['day_id'])
    elif damage == 'capacity': report['legacy_risk']['capacity_consumed'] = {}
    elif damage == 'legacy_context': report['legacy_context_sha256'] = 'f'*64
    elif damage == 'original_cut': report['original_cut_at_ms'] -= 1
    elif damage == 'descriptor': report['partition_binding']['candidate_sha256'] = 'f'*64
    elif damage == 'full_state': report['full_state_sha256'] = 'f'*64
    elif damage == 'gold_context': report['gold_position_context'] = {'quantity':'0'}
    elif damage == 'gold_risk': report['gold_risk'] = {'canonical_wallet':'4200'}
    elif damage == 'origin': report['origin_sha256'] = 'f'*64
    elif damage == 'registry': report['registry_sha256'] = 'f'*64
    elif damage == 'authority_bool': report['budget_enrolled'] = True
    elif damage == 'authority_zero': report['approval_granted'] = 0
    else: report['reviewed_at_ms'] = True
    with pytest.raises(ValueError):
        runtime.validate_gold_budget_review(report, origin=value['origin'], registry=value['registry'],
            source_commit='c'*40, as_of_ms=at)


def terminal_gold_history(installed, tmp_path, monkeypatch):
    import json
    from live.execution import demo_prepared_execution as executor, demo_position_context as position
    from live.exchange import binance_demo_exact as transport
    from live.execution.demo_pre_submit import _state
    from live.execution.demo_wire_protocol import outcome_for_query
    from services.research.demo_user_stream import UserStreamJournal, normalize_message
    from services.validation.demo_submission import GOLD_BATCH_SCHEMA
    from tests.live.test_demo_market_partition_runtime import signed_batch, execution_values, shift
    from tests.research.test_binance_order_receipts import message
    value = installed;batch, args = signed_batch(value, tmp_path, monkeypatch);order = batch['orders'][0]
    root = value['stores']['dispatch'].path.parent
    monkeypatch.setattr(executor, '_STATE_ROOT', root)
    monkeypatch.setattr(executor, '_require_state_custody', lambda *a: None)
    monkeypatch.setattr(executor, '_require_receipt_store_custody', lambda *a: None)
    monkeypatch.setattr(executor, 'now_ms', lambda: NOW+140002)
    monkeypatch.setattr(position, '_ROOT', root)
    monkeypatch.setattr(transport, '_credentials', lambda _: ('isolated-key', 'isolated-secret'))
    monkeypatch.setattr(transport, '_http_once', lambda *a: (200, json.dumps(dict(
        symbol='XAUUSDT', clientOrderId=order['newClientOrderId'], orderId=21)).encode()))
    executor.execute(batch, **execution_values(value, args))
    query = dict(symbol='XAUUSDT', clientOrderId=order['newClientOrderId'], orderId=21,
        side=order['side'], type='LIMIT', timeInForce=order['timeInForce'], positionSide='BOTH',
        reduceOnly=False, origQty=order['quantity'], executedQty='0', price=order['price'],
        status='EXPIRED', time=NOW+140002, updateTime=NOW+140010)
    value['stores']['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
        client_order_id=order['newClientOrderId'], requested_at_ms=NOW+140020, now_ms=NOW+140021,
        outcome=outcome_for_query(order, 200, json.dumps(query).encode(), schema=GOLD_BATCH_SCHEMA))
    stream = UserStreamJournal(tmp_path/'gold-stream.db', account_ref_sha256=batch['context']['account_ref_sha256'])
    try:
        stream.start_session(source_sha256='a'*64, at_ms=NOW+140000)
        event = message(NOW+140010, 'EXPIRED', '0', '0', trade_id=0, fee='0')
        event['o'].update(s='XAUUSDT', c=order['newClientOrderId'], i=21, q=order['quantity'],
            p=order['price'], S=order['side'], N='USDT', f=order['timeInForce'], m=False, R=False, er='0', x='EXPIRED')
        stream.record('MESSAGE', at_ms=NOW+140012, message=normalize_message(json.dumps(event)))
        monkeypatch.setattr(executor, 'now_ms', lambda: NOW+142000)
        stores = {key:value['stores'][key] for key in ('dispatch', 'recovery', 'oms')}
        result = executor.reconcile_reserved(batch, **stores, event_store=value['events'],
            stream=stream, start_ms=NOW+140001, end_ms=NOW+141000, source_commit='c'*40)
        assert result['status'] == 'OMS_TERMINAL_PROJECTED'
        state = _state(**stores);origin = shift(value['origin'], 3000)
        report = runtime.gold_budget_scope_review(state=state, origin=origin, as_of_ms=NOW+143000)
        return value, state, origin, report
    finally: stream.db.close()


def test_native_terminal_gold_history_is_retained_instead_of_an_empty_context(installed, tmp_path, monkeypatch):
    value, state, _, report = terminal_gold_history(installed, tmp_path, monkeypatch)
    assert report['gold_position_context'] is not None
    assert report['gold_risk']['initial_equity'] == report['gold_risk']['canonical_wallet'] == '4200'
    assert report['gold_risk']['quantity'] == '0'
    assert report['legacy_risk']['canonical_wallet'] == '4999.6801'
    assert report['full_state_sha256'] != value['record']['candidate']['binding']['legacy_state_sha256']
    assert state['reservations'][:len(value['state']['reservations'])] == value['state']['reservations']
