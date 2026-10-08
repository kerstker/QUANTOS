"""Installed templates select only explicit delegated/cancellation worker paths."""
import io
import json
import os
import threading
import pytest
from tools import demo_issuer as cli
from tests.live.test_demo_future_issuer import (
    book as book,installed as installed,first as first,materials as materials,scope_inputs as scope_inputs,
    day_materials as day_materials,local as local,scope_records as scope_records,native_scope as native_scope,
    scope_admin_case as scope_admin_case,future_issuer_case,
)
from tests.live.test_gold_strategy_constraints import case as case,NOW


class Channel(io.StringIO):
    def fileno(self):return 1


def test_installed_template_configuration_uses_its_current_key_without_accepting_it_as_exact(scope_admin_case,monkeypatch):
    f=scope_admin_case
    monkeypatch.setattr(cli,'verify_source',lambda source:None)
    monkeypatch.setattr(cli,'require_identity',lambda uid:None)
    monkeypatch.setattr(cli,'_state_custody',lambda *args:None)
    monkeypatch.setattr(cli,'now_ms',lambda:f['now_ms'])
    values={cli.REGISTRY:f['registry'],cli.POLICY:f['template']}
    monkeypatch.setattr(cli,'_read_root_owned_json',lambda path:json.dumps(values[path]).encode())
    root,registry,template=cli.configuration('c'*40)
    assert registry==f['registry'] and template==f['template']
    from live.execution.demo_issuer_policy import validate_policy
    with pytest.raises(ValueError):validate_policy(template)


def test_native_future_proposal_crosses_original_worker_pipe_and_persists_original_reservation(scope_admin_case,native_scope,monkeypatch):
    f=future_issuer_case(scope_admin_case,native_scope,monkeypatch)
    root=f.log.path.parent;(root/'results').mkdir(mode=0o700)
    monkeypatch.setattr(cli,'configuration',lambda source:(root,f.registry,f.base_policy))
    monkeypatch.setattr(cli,'private_pipe',lambda fd:None)
    monkeypatch.setattr(cli,'_state_custody',lambda *args:None)
    monkeypatch.setattr(cli,'now_ms',lambda:f.now_ms)
    def key(registry,policy,checker):
        checker(policy,registry,f.now_ms)
        return f.private
    monkeypatch.setattr(cli,'_load_key_checked',key)
    reader,writer=os.pipe();output=Channel()
    def send():
        raw=memoryview(json.dumps(f.handoff).encode()+b'\n')
        while raw:raw=raw[os.write(writer,raw):]
        os.close(writer)
    thread=threading.Thread(target=send);thread.start()
    try:
        assert cli.run(source_commit='c'*40,input_fd=reader,output=output)==0,output.getvalue()
        grant=json.loads(output.getvalue());assert grant['proposal_sha256']==f.handoff['proposal_sha256']
        assert [r['kind'] for r in f.log.snapshot()]==['GROUP_INITIALIZED','DELEGATION_V2_SIGNED','RESERVED','SIGNED']
        assert len(list((root/'results').glob('*.json')))==1
    finally:
        os.close(reader);thread.join(timeout=1);f.session.close()


def test_native_template_cancel_crosses_original_worker_pipe_with_real_signed_ancestry(case,tmp_path,monkeypatch):
    from tests.live.test_demo_future_cancellation import cancellation_case,cancellation_handoff
    from live.execution.demo_issuer_store import IssuerStore
    f=cancellation_case(case,tmp_path,monkeypatch);handoff=cancellation_handoff(f)
    root=f['store'].path.parent;(root/'results').mkdir(mode=0o700)
    original=f['store'].path;f['store'].close();original.rename(root/'issuance.sqlite3')
    f['store']=IssuerStore(root/'issuance.sqlite3')
    monkeypatch.setattr(cli,'configuration',lambda source:(root,f['registry'],f['template']))
    monkeypatch.setattr(cli,'private_pipe',lambda fd:None)
    monkeypatch.setattr(cli,'_state_custody',lambda *args:None)
    monkeypatch.setattr(cli,'now_ms',lambda:NOW+20)
    def key(registry,policy,checker):
        checker(policy,registry,NOW+20)
        return f['private']
    monkeypatch.setattr(cli,'_load_key_checked',key)
    reader,writer=os.pipe();output=Channel()
    os.write(writer,json.dumps(handoff).encode()+b'\n');os.close(writer)
    try:
        assert cli.run(source_commit='c'*40,purpose='cancellation',input_fd=reader,output=output)==0,output.getvalue()
        grant=json.loads(output.getvalue())
        assert grant['schema']=='DEMO_CANCELLATION_SESSION_GRANT_V1'
        rows=f['store'].snapshot()
        assert rows[-2]['payload']['cancellation']['schema']=='DEMO_GOLD_TEMPLATE_CANCEL_RESERVATION_V1'
        assert rows[-2]['payload']['policy']['schema']=='DEMO_GOLD_TEMPLATE_CANCEL_VIEW_V1'
        assert rows[-1]['kind']=='SIGNED' and len(list((root/'results').glob('*.json')))==1
    finally:os.close(reader);f['store'].close()
