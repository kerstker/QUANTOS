"""Owned exact basis comes from completed protected receipt/OMS provenance."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from live.execution import demo_position_context as position
from live.execution.demo_pre_submit import _state, _retain
from live.execution.order_event_store import SqliteOrderEventStore
from services.research.demo_user_stream import normalize_message
from tests.live.test_demo_oms_reconciliation import fixture, add_query, reconciliation, close, NOW
from live.execution import demo_pre_submit as preparation
from services.research.demo_account_baseline import normalize_response
from tests.live import test_demo_prepared_execution as execution_fixture
from tests.live.test_demo_pre_submit import signed
from live.execution import demo_prepared_execution as executor
from live.exchange import binance_demo_exact as transport


def completed(tmp_path, monkeypatch):
    batch, args, stream, message = fixture(tmp_path, monkeypatch)
    event_store = SqliteOrderEventStore(str(tmp_path/'events.sqlite3'), create=True)
    stream.record('MESSAGE', at_ms=NOW+12, message=normalize_message(json.dumps(message)))
    add_query(batch, args)
    root = tmp_path/'receipt-reconciliation'
    plan = reconciliation.prepare_resolution(batch, dispatch=args['dispatch'], stream=stream,
        start_ms=NOW+1, end_ms=NOW+1000, source_commit='a'*40, output_directory=root, as_of_ms=NOW+1001)
    result = reconciliation._project_resolution(plan, prepared=batch, dispatch=args['dispatch'],
        recovery=args['recovery'], oms=args['oms'], event_store=event_store, now_ms=NOW+1001)
    _retain(root/'outcomes', result)
    monkeypatch.setattr(position, '_ROOT', tmp_path)
    monkeypatch.setattr(position, '_custody', lambda *a, **k: None)
    return batch, args, stream, event_store, plan


def test_closed_original_batch_rebuilds_exact_basis_and_keeps_cash_authority_unverified(tmp_path, monkeypatch):
    batch, args, stream, events, _ = completed(tmp_path, monkeypatch)
    try:
        before = _state(args['dispatch'], args['recovery'], args['oms'])
        result = position.load_context(before, symbol='BTCUSDC',
            account_ref=batch['context']['account_ref_sha256'], as_of_ms=NOW+2000)
        assert result['quantity'] == '0.002'
        assert result['entry_notional'] == '120'
        assert result['canonical_wallet'] == '4999.946'
        assert result['initial_equity'] == '5000'
        assert result['available_at_ms'] >= NOW+1000
        assert result['cash_flow_policy'] == 'REPORTED_FILLS_ONLY_CONTINUATION_WALLET_MUST_MATCH_V1'
        assert result['cash_reconciled'] is False and result['source_verified'] is False
        assert _state(args['dispatch'], args['recovery'], args['oms']) == before
    finally:
        events.close(); close(args, stream)


@pytest.mark.parametrize('case', ['future', 'no_outcome', 'wrong_account', 'wrong_symbol', 'unresolved', 'journal_changed'])
def test_missing_or_changed_provenance_cannot_supply_a_position(tmp_path, monkeypatch, case):
    batch, args, stream, events, _ = completed(tmp_path, monkeypatch)
    try:
        state = _state(args['dispatch'], args['recovery'], args['oms'])
        at = NOW+500 if case == 'future' else NOW+2000
        account = 'f'*64 if case == 'wrong_account' else batch['context']['account_ref_sha256']
        symbol = 'BTCUSDT' if case == 'wrong_symbol' else 'BTCUSDC'
        if case == 'no_outcome':
            for path in (tmp_path/'receipt-reconciliation/outcomes').glob('*.json'): path.unlink()
        elif case == 'unresolved': state['recovery'][0]['state'] = 'INDETERMINATE'
        elif case == 'journal_changed':
            for path in (tmp_path/'receipt-reconciliation/receipts/journals').glob('*.sqlite3'): path.write_bytes(b'bad fixture')
        with pytest.raises(position.PositionContextRejected):
            position.load_context(state, symbol=symbol, account_ref=account, as_of_ms=at)
    finally:
        events.close(); close(args, stream)


def held_inputs(batch, args):
    origin = json.loads(Path(batch['proposal_path']).read_text())['origin']
    origin['observed_at_ms'] += 2000; origin['completed_at_ms'] += 2000
    for read in origin['reads']:
        read['requested_at_ms'] += 2000; read['received_at_ms'] += 2000
        kind = read['kind']; data = read['data']
        if kind.startswith('clock'): data['serverTime'] += 2000
        elif kind in ('mark', 'book'): data['time'] += 2000
        elif kind.startswith('account'):
            asset = next(a for a in data['assets'] if a['asset'] == 'USDC')
            asset.update(walletBalance='4999.946', crossWalletBalance='4999.946', availableBalance='4993.946',
                         unrealizedProfit='0', marginBalance='4999.946')
            data['positions'] = [p for p in data['positions'] if p['symbol'] != 'BTCUSDC']
            data['positions'].append({'symbol': 'BTCUSDC', 'positionSide': 'BOTH', 'positionAmt': '0.002'})
            read['data'] = normalize_response(kind, data)
        elif kind == 'positions':
            data[:] = [p for p in data if p['symbol'] != 'BTCUSDC']
            data.append({'symbol': 'BTCUSDC', 'positionSide': 'BOTH', 'positionAmt': '0.002', 'entryPrice': '60000',
                'markPrice': '60000', 'unRealizedProfit': '0', 'isolatedWallet': '0', 'marginAsset': 'USDC', 'updateTime': NOW+10})
            read['data'] = normalize_response(kind, data)
    risk = deepcopy(args['risk_inputs'])
    risk['now_ms'] = NOW+2000; risk['market']['observed_at_ms'] = NOW+2000
    risk['account'].update(cash='4993.946', equity='4999.946', day_pnl='-0.054',
        positions=[{'symbol': 'BTCUSDC', 'quantity': '0.002', 'mark_price': '60000'}])
    return dict(origin=origin, risk_inputs=risk, mandate=args['mandate'], attestations=args['attestations'], now_ms=NOW+2000,
        orders=[dict(batch['orders'][0], newClientOrderId='QDpartialclose001', side='SELL', quantity='0.001', reduceOnly=True, timeInForce='IOC')])


def test_partial_reduction_proposal_binds_owned_basis_and_current_account_without_reopening(tmp_path, monkeypatch):
    batch, args, stream, events, _ = completed(tmp_path, monkeypatch)
    try:
        values = held_inputs(batch, args)
        result = preparation.proposal(**values, **{k: args[k] for k in ('dispatch', 'recovery', 'oms')})
        context = result['state']['position_context']
        assert context['quantity'] == '0.002' and context['entry_notional'] == '120'
        assert result['risk']['status'] == 'RISK_CHECKS_PASSED'
        assert len(args['dispatch'].snapshot()) == 1
    finally:
        events.close(); close(args, stream)


def prepared_reduction(tmp_path, monkeypatch, *, quantity='0.001'):
    captured = {}; original = execution_fixture.setup
    def capture(*a, **k):
        private, envelope, values = original(*a, **k)
        captured.update(private=private, envelope=envelope)
        return private, envelope, values
    monkeypatch.setattr(execution_fixture, 'setup', capture)
    batch, args, stream, events, plan = completed(tmp_path, monkeypatch)
    values = held_inputs(batch, args)
    values['orders'][0]['quantity'] = quantity
    stores = {k: args[k] for k in ('dispatch', 'recovery', 'oms')}
    proposal = preparation.proposal(**values, **stores)
    captured['envelope']['payload']['approval_id'] = 'd'*64
    prepared = preparation.prepare(signed(captured['private'], captured['envelope'], proposal),
        **values, **stores, evidence_directory=tmp_path/'evidence')
    args['risk_inputs'] = values['risk_inputs']
    monkeypatch.setattr(executor, 'now_ms', lambda: NOW+2001)
    return prepared, args, stream, events, plan


def test_independently_signed_reduction_reaches_exact_transport_once(tmp_path, monkeypatch):
    from urllib.parse import parse_qs
    batch, args, stream, events, _ = prepared_reduction(tmp_path, monkeypatch)
    calls = []
    def http(body, headers):
        request = parse_qs(body.decode()); calls.append(request)
        assert request['reduceOnly'] == ['true'] and request['quantity'] == ['0.001']
        return 200, json.dumps({'symbol': 'BTCUSDC', 'clientOrderId': batch['orders'][0]['newClientOrderId'],
                               'orderId': 9007199254740994}).encode()
    monkeypatch.setattr(transport, '_http_once', http)
    try:
        report = executor.execute(batch, **args)
        assert report['status'] == 'WIRE_ATTEMPTS_RECORDED' and len(calls) == 1
        assert args['oms'].position('BTCUSDC') == .002 and len(args['oms'].fills()) == 1
        with pytest.raises(executor.DemoExecutionStopped): executor.execute(batch, **args)
        assert len(calls) == 1
    finally:
        events.close(); close(args, stream)


@pytest.mark.parametrize('quantity,fee,realized,wallet,remaining,basis', [
    ('0.001','0.027045','0.1','5000.018955','0.001','60'),
    ('0.002','0.05409','0.2','5000.09191','0','0'),
])
def test_partial_close_receipts_extend_same_exact_basis_and_cumulative_wallet(tmp_path, monkeypatch,
        quantity, fee, realized, wallet, remaining, basis):
    from tests.research.test_binance_order_receipts import message
    from live.execution.demo_wire_protocol import outcome_for_query
    batch, args, stream, events, _ = prepared_reduction(tmp_path, monkeypatch, quantity=quantity)
    order = batch['orders'][0]; venue = 9007199254740994
    monkeypatch.setattr(transport, '_http_once', lambda *_: (200, json.dumps(
        {'symbol': 'BTCUSDC', 'clientOrderId': order['newClientOrderId'], 'orderId': venue}).encode()))
    try:
        executor.execute(batch, **args)
        event = message(NOW+2010, 'FILLED', quantity, quantity, trade_id=8, fee=fee)
        event['o'].update(c=order['newClientOrderId'], i=venue, q=quantity, S='SELL', p='60000',
                          L='60100', ap='60100', f='IOC', m=False, R=True, er='0', rp=realized)
        stream.record('MESSAGE', at_ms=NOW+2012, message=normalize_message(json.dumps(event)))
        query = {'symbol': 'BTCUSDC', 'clientOrderId': order['newClientOrderId'], 'orderId': venue,
            'side': 'SELL', 'origQty': quantity, 'price': '60000', 'positionSide': 'BOTH', 'reduceOnly': True,
            'timeInForce': 'IOC', 'type': 'LIMIT', 'status': 'FILLED', 'executedQty': quantity,
            'time': NOW+2001, 'updateTime': NOW+2010}
        args['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
            client_order_id=order['newClientOrderId'], requested_at_ms=NOW+2030,
            outcome=outcome_for_query(order, 200, json.dumps(query).encode()), now_ms=NOW+2031)
        root = tmp_path/'receipt-reconciliation'
        plan = reconciliation.prepare_resolution(batch, dispatch=args['dispatch'], stream=stream,
            start_ms=NOW+2001, end_ms=NOW+3000, source_commit='a'*40, output_directory=root, as_of_ms=NOW+3001)
        assert plan['status'] == 'READY_FOR_OMS_PROJECTION', plan
        result = reconciliation._project_resolution(plan, prepared=batch, dispatch=args['dispatch'],
            recovery=args['recovery'], oms=args['oms'], event_store=events, now_ms=NOW+3001)
        _retain(root/'outcomes', result)
        assert args['oms'].position('BTCUSDC') == float(remaining) and len(args['oms'].fills()) == 2
        context = position.load_context(_state(args['dispatch'], args['recovery'], args['oms']),
            symbol='BTCUSDC', account_ref=batch['context']['account_ref_sha256'], as_of_ms=NOW+4000)
        assert context['quantity'] == remaining and context['entry_notional'] == basis
        assert context['canonical_wallet'] == wallet
        assert context['initial_equity'] == '5000' and len(context['sources']) == 2
        assert context['cash_reconciled'] is False
        if remaining == '0':
            later = held_inputs(batch, args)
            later['now_ms'] = NOW+4000
            later['risk_inputs']['now_ms'] = NOW+4000
            later['risk_inputs']['market']['observed_at_ms'] = NOW+4000
            later['risk_inputs']['account'].update(cash=wallet, equity=wallet, peak_equity=wallet,
                day_pnl='0.09191', positions=[])
            for read in later['origin']['reads']:
                if read['kind'].startswith('account'):
                    read['data']['positions'] = [p for p in read['data']['positions'] if p['symbol'] != 'BTCUSDC']
                    asset = next(a for a in read['data']['assets'] if a['asset'] == 'USDC')
                    asset.update(walletBalance=wallet,crossWalletBalance=wallet,availableBalance=wallet,marginBalance=wallet)
                elif read['kind'] == 'positions': read['data'] = [p for p in read['data'] if p['symbol'] != 'BTCUSDC']
            later['orders'][0].update(newClientOrderId='QDreopen001',side='BUY',quantity='0.003',reduceOnly=False)
            proposed = preparation.proposal(**later, **{k: args[k] for k in ('dispatch','recovery','oms')})
            assert proposed['state']['position_context'] == context
            assert len(args['dispatch'].snapshot()) == 2
    finally:
        events.close(); close(args, stream)


def test_new_flat_observation_cannot_erase_unclosed_owned_position(tmp_path, monkeypatch):
    batch, args, stream, events, _ = completed(tmp_path, monkeypatch)
    try:
        values = held_inputs(batch, args)
        for read in values['origin']['reads']:
            if read['kind'].startswith('account'):
                read['data']['positions'] = [p for p in read['data']['positions'] if p['symbol'] != 'BTCUSDC']
                asset = next(a for a in read['data']['assets'] if a['asset'] == 'USDC')
                asset.update(walletBalance='5000', crossWalletBalance='5000', availableBalance='5000', marginBalance='5000')
            elif read['kind'] == 'positions': read['data'] = [p for p in read['data'] if p['symbol'] != 'BTCUSDC']
        values['risk_inputs']['account'].update(cash='5000', equity='5000', positions=[], day_pnl='0')
        values['orders'][0].update(side='BUY', quantity='0.003', reduceOnly=False)
        assert position.assess(values['origin'], as_of_ms=values['now_ms'])['status'] == 'OBSERVED_FLAT_QUOTE'
        with pytest.raises(preparation.DemoPreparationRejected):
            preparation.proposal(**values, **{k: args[k] for k in ('dispatch', 'recovery', 'oms')})
        assert len(args['dispatch'].snapshot()) == 1
    finally:
        events.close(); close(args, stream)


@pytest.mark.parametrize('change', ['new_risk', 'reverse_direction', 'too_much', 'day_pnl', 'peak', 'wallet'])
def test_reduction_cannot_replace_frozen_risk_or_unexplained_account_facts(tmp_path, monkeypatch, change):
    batch, args, stream, events, _ = completed(tmp_path, monkeypatch)
    try:
        values = held_inputs(batch, args)
        if change == 'new_risk': values['orders'][0]['reduceOnly'] = False
        elif change == 'reverse_direction': values['orders'][0]['side'] = 'BUY'
        elif change == 'too_much': values['orders'][0]['quantity'] = '0.003'
        elif change == 'day_pnl': values['risk_inputs']['account']['day_pnl'] = '0'
        elif change == 'peak': values['risk_inputs']['account']['peak_equity'] = '4999.946'
        else:
            for read in values['origin']['reads']:
                if read['kind'].startswith('account'):
                    asset = next(a for a in read['data']['assets'] if a['asset'] == 'USDC')
                    asset.update(walletBalance='5000', crossWalletBalance='5000', marginBalance='5000')
            values['risk_inputs']['account']['equity'] = '5000'
        with pytest.raises(preparation.DemoPreparationRejected):
            preparation.proposal(**values, **{k: args[k] for k in ('dispatch', 'recovery', 'oms')})
        assert len(args['dispatch'].snapshot()) == 1
    finally:
        events.close(); close(args, stream)


@pytest.mark.parametrize('change', ['outcome_removed', 'journal_changed'])
def test_changed_original_basis_stops_after_signature_before_credentials(tmp_path, monkeypatch, change):
    batch, args, stream, events, plan = prepared_reduction(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(transport, '_credentials', lambda *_: calls.append('credentials'))
    try:
        if change == 'outcome_removed':
            for path in (tmp_path/'receipt-reconciliation/outcomes').glob('*.json'): path.unlink()
        else: Path(plan['journal_path']).write_bytes(b'corrupt fixture')
        with pytest.raises(executor.DemoExecutionStopped): executor.execute(batch, **args)
        assert not calls
        assert not [w for w in args['dispatch'].wire_snapshot() if w['reservation_sha256'] == batch['reservation_sha256']]
    finally:
        events.close(); close(args, stream)


def test_original_day_loss_and_peak_survive_the_next_position_proposal(tmp_path, monkeypatch):
    original = execution_fixture.setup
    def with_loss(*a, **k):
        private, envelope, values = original(*a, **k)
        values['risk_inputs']['account'].update(day_pnl='-7',peak_equity='5050')
        return private, envelope, values
    monkeypatch.setattr(execution_fixture, 'setup', with_loss)
    batch, args, stream, events, _ = completed(tmp_path, monkeypatch)
    try:
        values = held_inputs(batch, args)
        with pytest.raises(preparation.DemoPreparationRejected, match='DEMO_CONTINUATION_RISK_ACCOUNT'):
            preparation.proposal(**values, **{k: args[k] for k in ('dispatch','recovery','oms')})
        values['risk_inputs']['account'].update(day_pnl='-7.054',peak_equity='5000')
        with pytest.raises(preparation.DemoPreparationRejected, match='DEMO_CONTINUATION_PEAK_EQUITY'):
            preparation.proposal(**values, **{k: args[k] for k in ('dispatch','recovery','oms')})
        values['risk_inputs']['account']['peak_equity'] = '5050'
        result = preparation.proposal(**values, **{k: args[k] for k in ('dispatch','recovery','oms')})
        assert result['state']['position_context']['initial_day_pnl'] == '-7'
        assert result['state']['position_context']['peak_equity_floor'] == '5050'
    finally:
        events.close(); close(args, stream)


def test_completed_fills_cannot_reset_the_original_turnover_limit(tmp_path, monkeypatch):
    from dataclasses import replace
    original = execution_fixture.setup
    def limited(*a, **k):
        private, envelope, values = original(*a, **k)
        values['risk_inputs']['native_limits'] = replace(values['risk_inputs']['native_limits'],max_turnover=.03)
        return private, envelope, values
    monkeypatch.setattr(execution_fixture, 'setup', limited)
    batch, args, stream, events, _ = completed(tmp_path, monkeypatch)
    try:
        values = held_inputs(batch, args)
        with pytest.raises(preparation.DemoPreparationRejected, match='DEMO_BATCH_RISK_REJECTED'):
            preparation.proposal(**values, **{k: args[k] for k in ('dispatch','recovery','oms')})
    finally:
        events.close(); close(args, stream)


def test_new_volume_declaration_cannot_reset_prefix_participation_capacity(tmp_path, monkeypatch):
    original = execution_fixture.setup
    def limited(*a, **k):
        private, envelope, values = original(*a, **k)
        values['risk_inputs']['market']['daily_volume_base'] = '0.021'
        return private, envelope, values
    monkeypatch.setattr(execution_fixture, 'setup', limited)
    batch, args, stream, events, _ = completed(tmp_path, monkeypatch)
    try:
        values = held_inputs(batch, args)
        values['risk_inputs']['market']['daily_volume_base'] = '10'
        with pytest.raises(preparation.DemoPreparationRejected, match='DEMO_CONTINUATION_CAPACITY'):
            preparation.proposal(**values, **{k: args[k] for k in ('dispatch','recovery','oms')})
    finally:
        events.close(); close(args, stream)


@pytest.mark.parametrize('case', ['valid','file_writable','directory_writable','symlink','hardlink','wrong_uid'])
def test_protected_position_sources_require_executor_custody(tmp_path, monkeypatch, case):
    root = tmp_path/'executor'; root.mkdir(mode=0o700)
    folder = root/'receipts'; folder.mkdir(mode=0o700)
    path = folder/'journal'; path.write_bytes(b'isolated fixture'); path.chmod(0o600)
    uid = root.stat().st_uid
    monkeypatch.setattr(position, '_ROOT', root)
    monkeypatch.setattr(position.demo_authority, 'load_registry_for_executor', lambda: {'executor_uid': uid+1 if case=='wrong_uid' else uid})
    # Temporary test ancestors are not production /var/lib custody.
    monkeypatch.setattr(position.demo_authority, '_require_protected', lambda *a, **k: None)
    if case == 'file_writable': path.chmod(0o620)
    elif case == 'directory_writable': folder.chmod(0o720)
    elif case == 'symlink':
        other = folder/'target'; path.rename(other); path.symlink_to(other)
    elif case == 'hardlink': (folder/'link').hardlink_to(path)
    if case == 'valid': position._custody(path,directory=False)
    else:
        with pytest.raises(position.PositionContextRejected): position._custody(path,directory=False)
