"""Restart recovery uses only original reservations and never sends new orders."""
import json

import pytest

from live.execution import demo_resident_recovery as resident
from live.execution.demo_pre_submit import _retain
from tests.live.test_demo_prepared_execution import ready, NOW


def close(args):
    for name in ('dispatch', 'recovery', 'oms'):
        args[name].close()


def test_missing_prepared_return_is_reconstructed_from_original_proof_and_reservation(tmp_path, monkeypatch):
    prepared, args = ready(tmp_path, monkeypatch)
    try:
        original = args['dispatch'].evidence_snapshot()
        plan = resident.discover(dispatch=args['dispatch'], recovery=args['recovery'],
            oms=args['oms'], evidence_directory=tmp_path/'evidence')
        assert plan['status'] == 'RECOVERY_REQUIRED'
        assert plan['pending'][0]['prepared'] == prepared
        assert plan['pending'][0]['send_claims'] == 0
        assert args['dispatch'].evidence_snapshot() == original
        assert args['oms'].fills() == []
    finally:
        close(args)


@pytest.mark.parametrize('damage', ['missing', 'modified', 'symlink', 'ambiguous'])
def test_proof_failures_do_not_discard_pending_ids(tmp_path, monkeypatch, damage):
    from pathlib import Path
    prepared, args = ready(tmp_path, monkeypatch)
    path = Path(prepared['proposal_path'])
    try:
        if damage == 'missing':
            path.unlink()
        elif damage == 'modified':
            value = json.loads(path.read_text()); value['orders'][0]['quantity'] = '0.003'
            path.write_text(json.dumps(value))
        elif damage == 'symlink':
            alternate = tmp_path/'foreign.json'; path.rename(alternate); path.symlink_to(alternate)
        else:
            value = json.loads(path.read_text()); value['unexpected_review_note'] = 'different proof'
            _retain(tmp_path/'evidence', value)
        result = resident.discover(dispatch=args['dispatch'], recovery=args['recovery'],
            oms=args['oms'], evidence_directory=tmp_path/'evidence')
        assert result['status'] == 'INPUTS_UNRESOLVED'
        assert result['pending'][0]['client_order_ids'] == [prepared['orders'][0]['newClientOrderId']]
        assert result['pending'][0]['prepared'] is None
        assert len(args['recovery'].pending()) == 1
    finally:
        close(args)


def test_cycle_records_intent_before_query_and_restart_waits_without_new_order(tmp_path, monkeypatch):
    prepared, args = ready(tmp_path, monkeypatch)
    calls = []
    now = [NOW+1000]
    output = tmp_path/'resident'
    def query(batch, **stores):
        assert batch == prepared
        assert list((output/'attempts').glob('*.json'))
        calls.append('GET')
        raise SystemExit('isolated crash after possible GET')
    monkeypatch.setattr(resident.executor, 'query_reserved', query)
    try:
        loop = resident.RecoveryLoop(dispatch=args['dispatch'], recovery=args['recovery'], oms=args['oms'],
            evidence_directory=tmp_path/'evidence', output_directory=output, clock=lambda: now[0])
        with pytest.raises(SystemExit):
            loop.tick()
        resumed = resident.RecoveryLoop(dispatch=args['dispatch'], recovery=args['recovery'], oms=args['oms'],
            evidence_directory=tmp_path/'evidence', output_directory=output, clock=lambda: now[0])
        result = resumed.tick()
        assert result['status'] == 'WAITING_QUERY_INTERVAL' and calls == ['GET']
        now[0] += 30001
        with pytest.raises(SystemExit):
            resumed.tick()
        assert calls == ['GET', 'GET']
        assert args['dispatch'].wire_snapshot() == [] and args['oms'].fills() == []
        assert len(list((output/'attempts').glob('*.json'))) == 2
    finally:
        close(args)


def test_failed_public_clock_is_retained_and_not_immediately_retried(tmp_path, monkeypatch):
    _, args = ready(tmp_path, monkeypatch)
    calls = []
    def query(*a, **k):
        calls.append(1)
        return {'status': 'QUERY_RECOVERY_REQUIRED', 'reason': 'QUERY_CLOCK_UNRELIABLE'}
    monkeypatch.setattr(resident.executor, 'query_reserved', query)
    try:
        values = dict(dispatch=args['dispatch'], recovery=args['recovery'], oms=args['oms'],
            evidence_directory=tmp_path/'evidence', output_directory=tmp_path/'resident', clock=lambda: NOW+1000)
        result = resident.RecoveryLoop(**values).tick()
        assert result['status'] == 'QUERY_RECOVERY_REQUIRED'
        assert result['query']['reason'] == 'QUERY_CLOCK_UNRELIABLE'
        assert resident.RecoveryLoop(**values).tick()['status'] == 'WAITING_QUERY_INTERVAL'
        assert calls == [1]
    finally:
        close(args)


def test_corrupt_schedule_and_backward_clock_do_not_issue_reads(tmp_path, monkeypatch):
    _, args = ready(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(resident.executor, 'query_reserved', lambda *a, **k: calls.append(1))
    try:
        output = tmp_path/'resident'
        _retain(output/'attempts', {'schema': resident.ATTEMPT_SCHEMA, 'at_ms': NOW+3000,
            'reservation_sha256': 'a'*64, 'executor_boot_sha256': 'b'*64})
        values = dict(dispatch=args['dispatch'], recovery=args['recovery'], oms=args['oms'],
            evidence_directory=tmp_path/'evidence', output_directory=output, clock=lambda: NOW+1000)
        result = resident.RecoveryLoop(**values).tick()
        assert result['status'] == 'INPUTS_UNRESOLVED' and result['reason'] == 'RESIDENT_CLOCK_REGRESSION'
        path = next((output/'attempts').glob('*.json')); path.write_text('{}')
        result = resident.RecoveryLoop(**values).tick()
        assert result['status'] == 'INPUTS_UNRESOLVED' and result['reason'] == 'RESIDENT_SCHEDULE_INVALID'
        assert calls == []
    finally:
        close(args)


def test_resident_does_not_mark_an_ack_or_found_query_as_oms_or_cash_completion(tmp_path, monkeypatch):
    _, args = ready(tmp_path, monkeypatch)
    monkeypatch.setattr(resident.executor, 'query_reserved', lambda *a, **k:
        {'status': 'ORDER_SNAPSHOTS_OBSERVED', 'reason': None})
    try:
        result = resident.RecoveryLoop(dispatch=args['dispatch'], recovery=args['recovery'], oms=args['oms'],
            evidence_directory=tmp_path/'evidence', output_directory=tmp_path/'resident', clock=lambda: NOW+1000).tick()
        assert result['status'] == 'QUERY_OBSERVED_RECEIPTS_REQUIRED'
        assert result['cash_reconciled'] is False and result['new_order_submission_enabled'] is False
        assert len(args['recovery'].pending()) == 1 and args['oms'].fills() == []
    finally:
        close(args)


def test_resident_pairs_original_query_and_private_fill_and_restarts_without_duplicate_fill(tmp_path, monkeypatch):
    from live.execution.order_event_store import SqliteOrderEventStore
    from live.exchange import binance_demo_exact as transport
    from services.research.demo_user_stream import normalize_message
    from tests.live.test_demo_receipt_binding import fixture, close as close_stream
    from tests.live.test_demo_reserved_queries import snapshot
    batch, args, stream, message = fixture(tmp_path, monkeypatch, ack=False)
    events = SqliteOrderEventStore(str(tmp_path/'events.sqlite3'), create=True)
    now = [NOW+1020]; calls = []
    monkeypatch.setattr(resident.executor, 'now_ms', lambda: now[0])
    monkeypatch.setattr(resident.executor, '_STATE_ROOT', tmp_path)
    monkeypatch.setattr(resident.executor, '_require_receipt_store_custody', lambda *a: None)
    def get(path, query, headers):
        calls.append(path)
        if path == '/fapi/v1/time':
            return 200, json.dumps({'serverTime': now[0]+400}).encode()
        return 200, json.dumps(snapshot(batch['orders'][0], status='FILLED', executedQty='0.002',
            time=NOW+1000, updateTime=NOW+1400)).encode()
    monkeypatch.setattr(transport, '_http_get_once', get)
    monkeypatch.setattr(transport, '_http_once', lambda *a: pytest.fail('resident cannot POST'))
    try:
        message['E'] = message['T'] = message['o']['T'] = NOW+1400
        stream.record('MESSAGE', at_ms=NOW+1012, message=normalize_message(json.dumps(message)))
        values = dict(dispatch=args['dispatch'], recovery=args['recovery'], oms=args['oms'], stream=stream,
            event_store=events, evidence_directory=tmp_path/'evidence', output_directory=tmp_path/'resident',
            source_commit='a'*40, clock=lambda: now[0])
        result = resident.RecoveryLoop(**values).tick()
        assert result['status'] == 'OMS_TERMINAL_CASH_SEPARATE', result
        assert len(args['oms'].fills()) == 1 and args['oms'].position('BTCUSDC') == .002
        assert args['recovery'].pending() == ()
        now[0] += 30001
        assert resident.RecoveryLoop(**values).tick()['status'] == 'OMS_TERMINAL_CASH_SEPARATE'
        assert len(args['oms'].fills()) == 1 and calls == ['/fapi/v1/time', '/fapi/v1/order']
    finally:
        events.close(); close_stream(args, stream)


def test_expired_clock_scope_is_not_extended_to_make_reconciliation_pass(tmp_path, monkeypatch):
    from tests.live.test_demo_receipt_binding import fixture, close as close_stream
    batch, args, stream, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(resident.executor, 'query_reserved', lambda *a, **k:
        {'status': 'ORDER_SNAPSHOTS_OBSERVED', 'reason': None})
    monkeypatch.setattr(resident.executor, 'reconcile_reserved', lambda *a, **k: pytest.fail('scope cannot be stretched'))
    try:
        result = resident.RecoveryLoop(dispatch=args['dispatch'], recovery=args['recovery'], oms=args['oms'],
            stream=stream, event_store=object(), source_commit='a'*40, clock=lambda: NOW+160000,
            evidence_directory=tmp_path/'evidence', output_directory=tmp_path/'resident').tick()
        assert result['reason'] == 'RESIDENT_RECEIPT_WINDOW_UNRESOLVED'
        assert result['cash_reconciled'] is False and len(args['recovery'].pending()) == 1
    finally:
        close_stream(args, stream)


def test_competing_resident_cycle_cannot_issue_a_parallel_query(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    _, args = ready(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def query(*a, **k):
        calls.append(1); entered.set(); assert release.wait(5)
        return {'status':'QUERY_RECOVERY_REQUIRED', 'reason':'QUERY_UNRESOLVED'}
    monkeypatch.setattr(resident.executor, 'query_reserved', query)
    paths = (args['dispatch'].path, args['recovery'].db_path,
        args['oms']._conn.execute('PRAGMA database_list').fetchone()[2])
    def cycle():
        # Each process/thread opens its own SQLite connections, just as the
        # resident entrypoint does; LiveLedger connections are thread-confined.
        ds, rs, model = DemoDispatchStore(paths[0]), SqliteOrderRecoveryStore(paths[1]), LiveLedger(paths[2])
        try:
            return resident.RecoveryLoop(dispatch=ds, recovery=rs, oms=model,
                evidence_directory=tmp_path/'evidence', output_directory=tmp_path/'resident', clock=lambda: NOW+1000).tick()
        finally:
            for store in (ds, rs, model): store.close()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(cycle)
            assert entered.wait(5)
            try:
                other = pool.submit(cycle).result(timeout=3)
                assert other['status'] == 'RESIDENT_BUSY' and calls == [1]
            finally:
                release.set()
            assert running.result(timeout=3)['status'] == 'QUERY_RECOVERY_REQUIRED'
        assert len(list((tmp_path/'resident/attempts').glob('*.json'))) == 1
    finally:
        close(args)
