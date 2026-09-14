"""Protected origin capture reuses the fixed 16-read contract and account."""
import hashlib
import json
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from tools import demo_executor_origin as worker
from tests.tools.test_binance_demo_execution_origin import transport, collect, NOW


def test_protected_capture_uses_original_reads_and_only_bound_fixed_credentials(tmp_path,monkeypatch):
    key='a'*64;secret='b'*64
    ref=hashlib.sha256(('BINANCE_USDM_DEMO:'+key).encode()).hexdigest()
    monkeypatch.setattr(worker,'fixed_account',lambda:(tmp_path,ref))
    seen=[]
    monkeypatch.setattr(worker.transport,'_credentials',lambda account:seen.append(account) or (key,secret))
    get,calls=transport()
    opener=object()
    monkeypatch.setattr(worker.demo_network,'opener',lambda:opener)
    def fixed_get(path,**kwargs):
        assert kwargs.pop('opener') is opener
        return get(path,**kwargs)
    monkeypatch.setattr(worker.original,'get_response',fixed_get)
    monkeypatch.setattr(worker,'now_ms',lambda:NOW)
    monkeypatch.setattr(worker.original,'load_credentials',lambda *_:pytest.fail('research key file'))
    result=worker.capture_worker('BTCUSDC')
    assert result['status']=='CAPTURED' and len(result['reads'])==len(calls)==16
    assert result['account_ref_sha256']==ref and result['source_sha256']==worker.source_hash()
    assert seen==[ref] and key not in json.dumps(result) and secret not in json.dumps(result)


def test_controlled_read_provider_is_not_called_by_default_offline_origin(tmp_path):
    report=worker.original.collect(credential_file=None,symbol='BTCUSDC',
        credential_reader=lambda _:pytest.fail('credential read'),get=lambda *a,**k:pytest.fail('network'))
    assert report['status']=='NOT_REQUESTED'


@pytest.mark.parametrize('failure',[None,'timeout','private_error','wrong_account'])
def test_parent_retains_each_outcome_without_network_retry_or_secret_output(tmp_path,monkeypatch,failure):
    raw,_=collect(tmp_path)
    root=tmp_path/'executor';root.mkdir(mode=0o700)
    ref=raw['account_ref_sha256']
    monkeypatch.setattr(worker,'fixed_account',lambda:(root,ref))
    monkeypatch.setattr(worker,'now_ms',lambda:NOW)
    monkeypatch.setenv('HTTP_PROXY','http://not-inherited.invalid')
    raw['source_sha256']=worker.source_hash();calls=[]
    def child(argv,**kwargs):
        calls.append(argv)
        assert '--credential-file' not in argv and 'HTTP_PROXY' not in kwargs['env']
        assert kwargs['timeout']==115 and kwargs['check'] is True
        if failure=='timeout':raise subprocess.TimeoutExpired(argv,115)
        if failure=='private_error':raise RuntimeError('PRIVATE_HTTP_DATA')
        if failure=='wrong_account':
            raw['account_ref_sha256']='f'*64
            for r in raw['reads']:
                if r['account_ref_sha256'] is not None:r['account_ref_sha256']='f'*64
        return SimpleNamespace(stdout=json.dumps(raw).encode())
    monkeypatch.setattr(worker.subprocess,'run',child)
    result=worker.run(symbol='BTCUSDC',source_commit='a'*40)
    assert len(calls)==1 and result['status']==('CAPTURED' if failure is None else 'FAILED')
    retained=json.loads(Path(result['path']).read_bytes())
    assert retained['status']==result['status'] and 'PRIVATE_HTTP_DATA' not in json.dumps(retained)
    assert result['submission_enabled'] is False


def test_parallel_capture_cannot_race_the_latest_origin_pointer(tmp_path,monkeypatch):
    from tools.binance_demo_user_stream import writer_lock
    monkeypatch.setattr(worker,'fixed_account',lambda:(tmp_path,'a'*64))
    monkeypatch.setattr(worker.subprocess,'run',lambda *a,**k:pytest.fail('second capture'))
    with writer_lock(tmp_path/'origin-capture.lock'):
        from tools import binance_demo_user_stream as locking
        clock=iter([0,35])
        monkeypatch.setattr(locking.time,'monotonic',lambda:next(clock))
        with pytest.raises(ValueError,match='STREAM_WRITER_ALREADY_RUNNING'):
            worker.run(symbol='BTCUSDC',source_commit='a'*40)


def test_unprotected_parent_cannot_redirect_capture_files_before_rejection(tmp_path,monkeypatch):
    root=tmp_path/'executor';root.mkdir(mode=0o700)
    other=tmp_path/'elsewhere';other.mkdir()
    (root/'execution-origins').symlink_to(other,target_is_directory=True)
    monkeypatch.setattr(worker,'fixed_account',lambda:(root,'a'*64))
    monkeypatch.setattr(worker.subprocess,'run',lambda *a,**k:pytest.fail('network'))
    with pytest.raises(ValueError,match='RESIDENT_STATE_CUSTODY'):
        worker.run(symbol='BTCUSDC',source_commit='a'*40)
    assert list(other.iterdir())==[]
