"""Admin worker native pipe and ledger, with explicit isolated custody/public I/O."""
import importlib
import io
import json
import os
from copy import deepcopy
import pytest
from live.execution.demo_batch_risk import _digest
from tools.demo_issuer import _state_custody as native_state_custody
from tests.live.test_demo_issuer_group_continuity import (
    book as book,installed as installed,first as first,materials as materials,
    scope_inputs as scope_inputs,day_materials as day_materials,local as local,
    scope_records as scope_records,native_scope as native_scope,scope_admin_case as scope_admin_case,
    operation_for,
)


class Channel(io.StringIO):
    def fileno(self):return 1


def invoke(worker,request):
    reader,writer=os.pipe();output=Channel()
    os.write(writer,json.dumps(request).encode()+b'\n');os.close(writer)
    try:return worker.run(source_commit='c'*40,purpose='GROUP_CONTINUITY',input_fd=reader,output=output),json.loads(output.getvalue())
    finally:os.close(reader)


def boundary(monkeypatch,f):
    worker=importlib.import_module('tools.demo_issuer_group_admin')
    from tools import demo_issuer as original
    monkeypatch.setattr(worker,'configuration',lambda source:(f['store'].path.parent,f['registry']))
    monkeypatch.setattr(worker,'installed_authorization',lambda registry,purpose,at:deepcopy(f['previous']))
    monkeypatch.setattr(original,'private_pipe',lambda fd:None)
    monkeypatch.setattr(original,'now_ms',lambda:f['now_ms'])
    monkeypatch.setattr(worker,'load_admin_key',lambda registry,authorization:f['private'])
    return worker


def test_native_admin_private_pipe_applies_once_and_query_does_not_read_policy_public_material_or_key(scope_admin_case,monkeypatch):
    f=scope_admin_case;worker=boundary(monkeypatch,f);operation=operation_for(f)
    loaded=dict(operation=operation,documents=f['documents'],manifest_sha256='a'*64)
    monkeypatch.setattr(worker,'load_operation',lambda *a,**k:deepcopy(loaded))
    request=dict(schema='DEMO_GOLD_ISSUER_ADMIN_INPUT_V1',source_commit='c'*40,purpose='GROUP_CONTINUITY',
        operation_sha256=_digest(operation),mode='APPLY')
    code,result=invoke(worker,request)
    assert code==0 and result['status']=='COMMITTED'
    assert result['record']['kind']=='GROUP_INITIALIZED' and result['artifact'] is None
    assert result['event_sha256']==result['record']['record_hash']
    before=f['store'].snapshot()
    def denied(*a,**kw):pytest.fail('QUERY attempted current authority/key/material read')
    monkeypatch.setattr(worker,'installed_authorization',denied)
    monkeypatch.setattr(worker,'load_admin_key',denied);monkeypatch.setattr(worker,'load_operation',denied)
    monkeypatch.setattr(worker.original,'now_ms',lambda:f['template']['valid_until_ms']+100)
    code,found=invoke(worker,{**request,'mode':'QUERY'})
    assert code==0 and found['status']=='FOUND' and found['record']==result['record']
    code,missing=invoke(worker,{**request,'mode':'QUERY','operation_sha256':'f'*64})
    assert code==3 and missing['status']=='UNKNOWN'
    assert f['store'].snapshot()==before


@pytest.mark.parametrize('damage',['extra_path','purpose','source','operation'])
def test_invalid_admin_pipe_never_opens_private_key_or_appends(scope_admin_case,monkeypatch,damage):
    f=scope_admin_case;worker=boundary(monkeypatch,f);operation=operation_for(f)
    monkeypatch.setattr(worker,'load_operation',lambda *a,**kw:dict(operation=operation,documents=f['documents'],manifest_sha256='a'*64))
    monkeypatch.setattr(worker,'load_admin_key',lambda *a:pytest.fail('invalid pipe reached key'))
    request=dict(schema='DEMO_GOLD_ISSUER_ADMIN_INPUT_V1',source_commit='c'*40,purpose='GROUP_CONTINUITY',operation_sha256=_digest(operation),mode='APPLY')
    if damage=='extra_path':request['policy_path']='/caller/chosen'
    elif damage=='purpose':request['purpose']='submission'
    elif damage=='source':request['source_commit']='d'*40
    else:request['operation_sha256']='f'*64
    code,result=invoke(worker,request)
    assert code==3 and result['status']=='REJECTED' and f['store'].snapshot()==[]


def test_query_missing_original_database_is_unknown_and_never_initializes_replacement(scope_admin_case,monkeypatch):
    f=scope_admin_case;worker=boundary(monkeypatch,f)
    path=f['store'].path;retained=path.with_name('retained-original.sqlite3');path.rename(retained)
    monkeypatch.setattr(worker,'load_admin_key',lambda *a:pytest.fail('QUERY key read'))
    request=dict(schema='DEMO_GOLD_ISSUER_ADMIN_INPUT_V1',source_commit='c'*40,purpose='GROUP_CONTINUITY',operation_sha256='f'*64,mode='QUERY')
    code,result=invoke(worker,request)
    assert code==3 and result['status']=='UNKNOWN' and not path.exists() and retained.exists()


def test_query_native_configuration_missing_database_custody_is_unknown(scope_admin_case,monkeypatch):
    """Keep the real configuration/state-custody path, isolating only host trust."""
    f=scope_admin_case;worker=importlib.import_module('tools.demo_issuer_group_admin');original=worker.original
    registry=deepcopy(f['registry']);registry.update(issuer_uid=os.getuid(),executor_uid=os.getuid()+10001,research_uids=[os.getuid()+10002])
    root=f['store'].path.parent;(root/'results').mkdir(mode=0o700)
    root.chmod(0o700);f['store'].path.rename(root/'retained-original.sqlite3')
    monkeypatch.setattr(original,'STATE',root)
    monkeypatch.setattr(original,'verify_source',lambda source:None)
    monkeypatch.setattr(original,'require_identity',lambda uid:None)
    monkeypatch.setattr(original,'_require_protected',lambda *a,**kw:None)
    monkeypatch.setattr(original,'_state_custody',native_state_custody)
    monkeypatch.setattr(original,'_read_root_owned_json',lambda path:json.dumps(registry).encode())
    monkeypatch.setattr(original,'private_pipe',lambda fd:None)
    monkeypatch.setattr(worker,'load_admin_key',lambda *a:pytest.fail('QUERY key read'))
    with pytest.raises(FileNotFoundError):worker.configuration('c'*40)
    request=dict(schema='DEMO_GOLD_ISSUER_ADMIN_INPUT_V1',source_commit='c'*40,purpose='GROUP_CONTINUITY',operation_sha256='f'*64,mode='QUERY')
    code,result=invoke(worker,request)
    assert code==3 and result['status']=='UNKNOWN' and not (root/'issuance.sqlite3').exists()
