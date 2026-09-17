"""The fixed-account statement worker must not fall back to research keys."""
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from tests.tools.test_binance_demo_statement import NOW, transport


def worker_module():
    from tools import demo_executor_statement
    return demo_executor_statement


def fixed_worker(tmp_path, monkeypatch):
    worker = worker_module()
    key, secret = 'a'*64, 'b'*64
    account = hashlib.sha256(('BINANCE_USDM_DEMO:'+key).encode()).hexdigest()
    monkeypatch.setattr(worker, 'fixed_account', lambda: (tmp_path, account))
    seen = []
    monkeypatch.setattr(worker.transport, '_credentials', lambda ref: seen.append(ref) or (key, secret))
    monkeypatch.setattr(worker.original, 'load_credentials', lambda *_: pytest.fail('research credentials'))
    monkeypatch.setattr(worker, 'now_ms', lambda: NOW)
    opener = object(); monkeypatch.setattr(worker.demo_network, 'opener', lambda: opener)
    calls = []; get = transport(calls)
    def fixed_get(path, **kwargs):
        assert kwargs.pop('opener') is opener
        return get(path, **kwargs)
    monkeypatch.setattr(worker.original, 'get_response', fixed_get)
    return worker, account, seen, calls


@pytest.mark.parametrize('day_replay',[False,True])
def test_fixed_capture_binds_account_and_exact_window_without_exposing_credentials(tmp_path, monkeypatch,day_replay):
    worker, account, seen, calls = fixed_worker(tmp_path, monkeypatch)
    report = worker.capture_worker('BTCUSDT', NOW-(3*86400000 if day_replay else 1000), NOW,day_replay=day_replay)
    assert report['schema']==(worker.DAY_SCHEMA if day_replay else worker.SCHEMA)
    assert report['status'] == 'CAPTURED'
    assert report['account_ref_sha256'] == account and seen == [account]
    assert len(calls) == 6 and report['statement']['symbols'] == ['BTCUSDT']
    assert 'a'*64 not in json.dumps(report) and 'b'*64 not in json.dumps(report)
    assert report['canonical_ledger_reconciled'] is False
    assert report['economic_authority'] is False


@pytest.mark.parametrize('failure', [None, 'timeout', 'account', 'source', 'raw_source', 'window', 'digest', 'private_error'])
def test_parent_retains_worker_failures_and_never_retries_network(tmp_path, monkeypatch, failure):
    worker, account, _, _ = fixed_worker(tmp_path, monkeypatch)
    report = worker.capture_worker('BTCUSDT', NOW-1000, NOW)
    calls = []
    monkeypatch.setenv('HTTP_PROXY', 'http://must-not-inherit.invalid')
    def child(argv, **kwargs):
        calls.append(argv)
        assert kwargs['timeout'] == 115 and 'HTTP_PROXY' not in kwargs['env']
        assert '--credential-file' not in argv
        if failure == 'timeout': raise subprocess.TimeoutExpired(argv, 115)
        if failure == 'private_error': raise RuntimeError('PRIVATE_DATA_SHOULD_NOT_ESCAPE')
        if failure == 'account': report['account_ref_sha256'] = 'f'*64
        if failure == 'source': report['source_sha256'] = 'f'*64
        if failure == 'raw_source':
            report['statement']['source_sha256'] = 'f'*64
            report['statement_sha256'] = worker._digest(report['statement'])
        if failure == 'window': report['start_ms'] -= 1
        if failure == 'digest': report['statement_sha256'] = 'f'*64
        return SimpleNamespace(stdout=json.dumps(report).encode())
    monkeypatch.setattr(worker.subprocess, 'run', child)
    result = worker.run(symbol='BTCUSDT', start_ms=NOW-1000, end_ms=NOW, source_commit='a'*40)
    assert len(calls) == 1
    retained = json.loads(result['path'].read_bytes())
    assert retained['status'] == ('CAPTURED' if failure is None else 'FAILED')
    assert retained['account_ref_sha256'] == account
    assert result['orders_submitted'] is False
    assert 'PRIVATE_DATA_SHOULD_NOT_ESCAPE' not in json.dumps(retained)
    assert not list(tmp_path.glob('*.sqlite3'))


def test_structural_read_of_historical_evidence_does_not_require_current_collector_version(tmp_path, monkeypatch):
    worker, _, _, _ = fixed_worker(tmp_path, monkeypatch)
    report = worker.capture_worker('BTCUSDT', NOW-1000, NOW)
    monkeypatch.setattr(worker.original, 'source_hash', lambda: 'f'*64)
    assert worker.validate(report) == report
    assert report['source_verified'] is False


def test_symlink_rejected_before_child(tmp_path, monkeypatch):
    worker, _, _, _ = fixed_worker(tmp_path, monkeypatch)
    monkeypatch.setattr(worker.subprocess, 'run', lambda *a, **k: pytest.fail('network'))
    elsewhere = tmp_path/'other'; elsewhere.mkdir()
    (tmp_path/'receipt-reconciliation').symlink_to(elsewhere)
    with pytest.raises(ValueError):
        worker.run(symbol='BTCUSDT', start_ms=NOW-1000, end_ms=NOW, source_commit='a'*40)
    assert list(elsewhere.iterdir()) == []


def test_existing_origin_lease_serializes_fixed_account_reads(tmp_path, monkeypatch):
    worker, _, _, _ = fixed_worker(tmp_path, monkeypatch)
    from tools.binance_demo_user_stream import writer_lock
    monkeypatch.setattr(worker.subprocess, 'run', lambda *a, **k: pytest.fail('parallel network capture'))
    with writer_lock(tmp_path/'origin-capture.lock'):
        from tools import binance_demo_user_stream as locking
        clock=iter([0,35])
        monkeypatch.setattr(locking.time,'monotonic',lambda:next(clock))
        with pytest.raises(ValueError, match='STREAM_WRITER_ALREADY_RUNNING'):
            worker.run(symbol='BTCUSDT', start_ms=NOW-1000, end_ms=NOW, source_commit='a'*40)


@pytest.mark.parametrize('symbol,start,end', [('ETHUSDT',NOW-1000,NOW), ('BTCUSDT',NOW-86400001,NOW), ('BTCUSDT',NOW,NOW)])
def test_invalid_scope_rejected_before_fixed_account_access(monkeypatch, symbol, start, end):
    worker = worker_module()
    monkeypatch.setattr(worker, 'now_ms', lambda: NOW)
    monkeypatch.setattr(worker, 'fixed_account', lambda: pytest.fail('account access'))
    with pytest.raises(ValueError): worker.capture_worker(symbol, start, end)


def test_default_statement_stays_offline_with_injected_provider():
    worker = worker_module()
    result = worker.original.collect(credential_file=None, symbols=['BTCUSDT'], start_ms=NOW-1000,
        end_ms=NOW, clock=lambda: NOW,
        credential_reader=lambda _: pytest.fail('credential access'), get=lambda *a, **k: pytest.fail('network'))
    assert result['status'] == 'NOT_REQUESTED'


def test_explicit_day_replay_is_bounded_and_default_remains_one_day():
    worker=worker_module()
    worker._scope('BTCUSDC',NOW-3*86400000,NOW,NOW,day_replay=True)
    with pytest.raises(ValueError):worker._scope('BTCUSDC',NOW-3*86400000,NOW,NOW)
    with pytest.raises(ValueError):worker._scope('BTCUSDC',NOW-7*86400000-1,NOW,NOW,day_replay=True)
    with pytest.raises(ValueError):worker._scope('BTCUSDC',NOW-1000,NOW,NOW,day_replay=1)
