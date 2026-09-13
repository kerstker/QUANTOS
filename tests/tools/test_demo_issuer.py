"""The issuer is a separate fixed identity; no caller key or path overrides."""
import io
import json
import os
import stat
from types import SimpleNamespace

import pytest

from tools import demo_issuer as cli


class Channel(io.StringIO):
    def fileno(self):return 1


def test_issuer_identity_rejects_supplementary_executor_access(monkeypatch):
    monkeypatch.setattr(cli.os,'geteuid',lambda:2002)
    monkeypatch.setattr(cli.os,'getegid',lambda:2002)
    monkeypatch.setattr(cli.os,'getgroups',lambda:[2002,2001])
    with pytest.raises(ValueError,match='ISSUER_IDENTITY'):
        cli.require_identity(2002)
    monkeypatch.setattr(cli.os,'getgroups',lambda:[2002])
    cli.require_identity(2002)


def test_missing_configuration_never_initializes_state_or_reads_a_key(monkeypatch,capsys):
    monkeypatch.setattr(cli,'configuration',lambda *_:(_ for _ in ()).throw(ValueError('PRIVATE_CONFIGURATION_DETAIL')))
    monkeypatch.setattr(cli,'load_key',lambda *a:pytest.fail('key accessed'))
    assert cli.main(['--source-commit','a'*40])==3
    assert 'PRIVATE_CONFIGURATION_DETAIL' not in capsys.readouterr().out


@pytest.mark.parametrize('change',[{'st_uid':1000},{'st_mode':stat.S_IFIFO|0o666},{'st_mode':stat.S_IFREG|0o600}])
def test_uncontrolled_input_channels_are_rejected(monkeypatch,change):
    data={'st_uid':0,'st_gid':0,'st_mode':stat.S_IFIFO|0o600};data.update(change)
    monkeypatch.setattr(cli.os,'fstat',lambda _:SimpleNamespace(**data))
    with pytest.raises(ValueError):cli.private_pipe(123)


@pytest.mark.parametrize('change',[{'st_gid':1000},{'st_mode':stat.S_IFREG|0o644},{'st_mode':stat.S_IFREG|0o660}])
def test_signing_key_metadata_requires_issuer_group_and_no_other_readers(change):
    data={'st_uid':0,'st_gid':2002,'st_mode':stat.S_IFREG|0o640,'st_nlink':1,'st_size':200};data.update(change)
    with pytest.raises(ValueError):cli.private_key_metadata(SimpleNamespace(**data),2002)


def test_root_owned_pipes_allow_one_exact_grant_with_original_journal_preserved(tmp_path,monkeypatch):
    from tests.live.test_demo_issuer import fixture,NOW
    private,values,opened,session,handoff,registry,policy,log=fixture(tmp_path,monkeypatch)
    log.close();(tmp_path/'issuer.sqlite3').rename(tmp_path/'issuance.sqlite3');(tmp_path/'results').mkdir(mode=0o700)
    monkeypatch.setattr(cli,'configuration',lambda _: (tmp_path,registry,policy))
    monkeypatch.setattr(cli,'private_pipe',lambda _:None)
    monkeypatch.setattr(cli,'load_key',lambda *a:private)
    monkeypatch.setattr(cli,'now_ms',lambda:NOW+1)
    monkeypatch.setattr(cli,'_state_custody',lambda *a:None)
    reader,writer=os.pipe();output=Channel()
    import threading
    def send():
        raw=memoryview(json.dumps(handoff).encode()+b'\n')
        while raw:raw=raw[os.write(writer,raw):]
    sender=threading.Thread(target=send,daemon=True);sender.start()
    try:
        assert cli.run(source_commit='c'*40,input_fd=reader,output=output)==0
        grant=json.loads(output.getvalue());assert grant['schema']=='DEMO_EXECUTION_SESSION_GRANT_V1'
        assert grant['proposal_sha256']==handoff['proposal_sha256']
        from live.execution.demo_issuer_store import IssuerStore
        store=IssuerStore(tmp_path/'issuance.sqlite3')
        try:assert [r['kind'] for r in store.snapshot()]==['RESERVED','SIGNED']
        finally:store.close()
        assert opened[0].snapshot()==[] and opened[2].orders()==[]
        assert len(list((tmp_path/'results').glob('*.json')))==1
    finally:
        os.close(reader);os.close(writer);sender.join(timeout=1)
        for store in opened:store.close()


def test_untrusted_or_invalid_handoff_does_not_read_signing_key(tmp_path,monkeypatch):
    from tests.live.test_demo_issuer import fixture,NOW
    private,values,opened,session,handoff,registry,policy,log=fixture(tmp_path,monkeypatch)
    log.close();(tmp_path/'issuer.sqlite3').rename(tmp_path/'issuance.sqlite3');(tmp_path/'results').mkdir(mode=0o700)
    monkeypatch.setattr(cli,'configuration',lambda _: (tmp_path,registry,policy))
    monkeypatch.setattr(cli,'private_pipe',lambda _:None)
    monkeypatch.setattr(cli,'load_key',lambda *a:pytest.fail('untrusted request read key'))
    monkeypatch.setattr(cli,'now_ms',lambda:NOW+1)
    monkeypatch.setattr(cli,'_state_custody',lambda *a:None)
    reader,writer=os.pipe();os.write(writer,b'{"schema":"BINANCE_MAINNET"}\n');os.close(writer)
    output=Channel()
    try:
        assert cli.run(source_commit='c'*40,input_fd=reader,output=output)==3
        report=json.loads(output.getvalue());assert report['status']=='NOT_ISSUED'
        assert report['record_persisted'] and not report['submission_enabled']
        assert len(list((tmp_path/'results').glob('*.json')))==1
    finally:
        os.close(reader)
        for store in opened:store.close()


@pytest.mark.parametrize('failure',['configuration_changed','broken_output'])
def test_failure_after_signing_preserves_grant_and_reports_uncertain_delivery(tmp_path,monkeypatch,failure):
    from tests.live.test_demo_issuer import fixture,NOW
    from live.execution.demo_issuer_store import IssuerStore
    private,_,opened,_,handoff,registry,policy,log=fixture(tmp_path,monkeypatch)
    log.close();(tmp_path/'issuer.sqlite3').rename(tmp_path/'issuance.sqlite3');(tmp_path/'results').mkdir(mode=0o700)
    calls=[]
    def config(_):
        calls.append(1)
        if len(calls)>1 and failure=='configuration_changed':raise ValueError('changed')
        return tmp_path,registry,policy
    monkeypatch.setattr(cli,'configuration',config)
    monkeypatch.setattr(cli,'private_pipe',lambda _:None)
    monkeypatch.setattr(cli,'load_key',lambda *a:private)
    monkeypatch.setattr(cli,'now_ms',lambda:NOW+1)
    monkeypatch.setattr(cli,'_state_custody',lambda *a:None)
    monkeypatch.setattr(cli,'read_frame',lambda *a,**k:json.dumps(handoff).encode())
    # Fixture keys describe synthetic UIDs; exercise persistence without
    # asserting deployment ownership on files created by the test user.
    monkeypatch.setattr(cli,'_result',lambda root,registry,value:cli._retain(root/'results',value))
    class Broken(Channel):
        def write(self,_):raise BrokenPipeError('private transport detail')
    output=Broken() if failure=='broken_output' else Channel()
    try:
        assert cli.run(source_commit='c'*40,input_fd=123,output=output)==3
        with __import__('contextlib').closing(IssuerStore(tmp_path/'issuance.sqlite3')) as store:
            assert [r['kind'] for r in store.snapshot()]==['RESERVED','SIGNED']
        records=[json.loads(p.read_text()) for p in (tmp_path/'results').glob('*.json')]
        failed=next(r for r in records if r['status']=='GRANT_DELIVERY_UNCONFIRMED')
        assert failed['grant_recorded'] is True and 'record_persisted' not in failed
        if failure=='configuration_changed':
            value=json.loads(output.getvalue())
            assert value['status']=='GRANT_DELIVERY_UNCONFIRMED' and value['record_persisted']
        assert opened[0].snapshot()==[] and opened[2].orders()==[]
    finally:
        for store in opened:store.close()
