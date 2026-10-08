"""Administrative delivery crash boundaries; no simulated receipt grants rights."""
import os
import stat

import pytest

from tests.tools.test_gold_future_budget_materials import (
    book as book,installed as installed,first as first,materials as materials,
    scope_inputs as scope_inputs,day_materials as day_materials,local as local,
    scope_records as scope_records,native_scope as native_scope,
)


def local_tool(tmp_path,monkeypatch):
    from tools import gold_future_budget_install as tool
    stage=tmp_path/'stage';stage.mkdir(mode=0o700)
    uid=os.getuid()
    def private(info,*,maximum):
        assert stat.S_ISREG(info.st_mode) and info.st_uid==uid and not info.st_mode&0o077
        assert info.st_nlink==1 and 0<info.st_size<=maximum
    monkeypatch.setattr(tool.installer,'private_metadata',private)
    monkeypatch.setattr(tool.installer,'_require_protected',lambda info,**kw: (
        stat.S_ISDIR(info.st_mode) and info.st_uid in (0,uid)) or pytest.fail('ancestor custody'))
    monkeypatch.setattr(tool.installer.os,'fchown',lambda *a:None)
    monkeypatch.setattr(tool.supervisor,'_root_private_directory',lambda path: (
        path.is_dir() and not path.is_symlink() and stat.S_IMODE(path.stat().st_mode)==0o700) or pytest.fail('stage custody'))
    monkeypatch.setattr(tool.supervisor,'now_ms',lambda:1234)
    operation=dict(subject=dict(source_commit='c'*40,purpose='GROUP_CONTINUITY',expected_head={'test':'original head'}),
        confirmation={'test':'transport-only synthetic confirmation'})
    return tool,stage,operation


def test_attempt_is_durable_before_send_and_lost_response_never_reapplies(tmp_path,monkeypatch):
    tool,stage,operation=local_tool(tmp_path,monkeypatch);calls=[]
    def invoke(**kw):
        calls.append(kw['mode'])
        assert (stage/'group-attempt.json').is_file()
        if kw['mode']=='APPLY':raise OSError('isolated response lost after possible commit')
        return {'status':'UNKNOWN'}
    monkeypatch.setattr(tool,'invoke_admin',invoke)
    args=dict(stage=stage,operation=operation,manifest_sha256='a'*64,registry={},source_commit='c'*40,purpose='GROUP_CONTINUITY')
    with pytest.raises(OSError):tool.deliver_once(**args)
    path=stage/'group-attempt.json';original=(path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns)
    assert tool.deliver_once(**args)=={'status':'UNKNOWN'}
    assert calls==['APPLY','QUERY']
    assert original==(path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns)


def test_attempt_without_any_sent_request_still_queries_only(tmp_path,monkeypatch):
    tool,stage,operation=local_tool(tmp_path,monkeypatch)
    native=tool.installer.write_new
    def interrupted(path,body,**kw):
        native(path,body,**kw)
        if path.name=='group-attempt.json':raise OSError('after fsync, before send')
    args=dict(stage=stage,operation=operation,manifest_sha256='a'*64,registry={},source_commit='c'*40,purpose='GROUP_CONTINUITY')
    with monkeypatch.context() as stopped:
        stopped.setattr(tool.installer,'write_new',interrupted)
        with pytest.raises(OSError):tool.deliver_once(**args)
    calls=[];monkeypatch.setattr(tool,'invoke_admin',lambda **kw:calls.append(kw['mode']) or {'status':'UNKNOWN'})
    assert tool.deliver_once(**args)=={'status':'UNKNOWN'} and calls==['QUERY']


def test_changed_attempt_refuses_any_issuer_send(tmp_path,monkeypatch):
    tool,stage,operation=local_tool(tmp_path,monkeypatch)
    monkeypatch.setattr(tool,'invoke_admin',lambda **kw: {'status':'UNKNOWN'})
    args=dict(stage=stage,operation=operation,manifest_sha256='a'*64,registry={},source_commit='c'*40,purpose='GROUP_CONTINUITY')
    tool.deliver_once(**args)
    monkeypatch.setattr(tool,'invoke_admin',lambda **kw:pytest.fail('changed attempt sent'))
    with pytest.raises(ValueError):tool.deliver_once(**(args|{'manifest_sha256':'b'*64}))


def test_admin_launcher_keeps_original_private_isolation_and_separate_purpose():
    from tools import gold_future_budget_install as tool
    command=tool.admin_command(source_commit='c'*40,registry={'issuer_uid':3002},purpose='GROUP_CONTINUITY',
        unit='quantos-demo-issuer-abcdefgh1234')
    assert command[command.index('-m')+1]=='tools.demo_issuer_group_admin'
    assert command[command.index('--purpose')+1]=='GROUP_CONTINUITY'
    assert 'InaccessiblePaths=/etc/quantos/demo-submission /var/lib/quantos-demo-executor' in command
    assert any(x.startswith('ReadOnlyPaths=') and '-/etc/quantos/demo-gold-continuity' in x for x in command)
    with pytest.raises(ValueError):tool.admin_command(source_commit='c'*40,registry={'issuer_uid':3002},
        purpose='submission',unit='quantos-demo-issuer-abcdefgh1234')


@pytest.mark.parametrize('option',['--exit-reserve-grants','--exit-reserve-orders','--exit-reserve-notional'])
def test_resume_cli_rejects_new_budget_parameters_before_loading_history(monkeypatch,option):
    from tools import gold_future_budget_install as tool
    calls=[]
    monkeypatch.setattr(tool,'install_approved',lambda **kw:calls.append(kw) or {'status':'unexpected accepted'})
    assert tool.main(['--source-commit','c'*40,'--resume-group','d'*64,option,'1'])==3
    assert calls==[]


@pytest.fixture
def native_install(native_scope,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from services.validation.demo_submission import _decode
    from live.execution import demo_issuer_group_continuity as continuity
    from live.execution.demo_issuer_store import IssuerStore
    from tools import gold_future_budget_install as tool
    case=native_scope;uid=os.getuid();pub=tool.public
    def chown(path,owner,group):
        path.relative_to(tmp_path)
        assert owner==0 and group in (0,case['registry']['issuer_uid'])
    monkeypatch.setattr(tool.installer.os,'chown',chown)
    monkeypatch.setattr(pub,'PUBLIC_ROOT',tmp_path/'public-continuity')
    original_meta=pub._metadata
    def metadata(info,**kw):
        assert info.st_uid==uid
        mapped=SimpleNamespace(**{k:getattr(info,k) for k in ('st_mode','st_nlink','st_size')},st_uid=0,st_gid=0)
        return original_meta(mapped,**kw)
    monkeypatch.setattr(pub,'_metadata',metadata)
    monkeypatch.setattr(pub,'_parent_metadata',lambda info: (
        stat.S_ISDIR(info.st_mode) and info.st_uid in (0,uid)) or pytest.fail('parent'))
    monkeypatch.setattr(tool,'local_operator',lambda:'isolated-new-future-confirmation')
    confirmations=[];calls=[]
    def confirm(subject,documents,operator):
        confirmations.append(tool.digest(subject))
        return dict(schema='GOLD_FUTURE_BUDGET_OPERATOR_CONFIRMATION_V1',purpose=subject['purpose'],
            subject_sha256=tool.digest(subject),operator_id=operator,approved_at_ms=case['local']['clock'][0],
            consumer_enabled=False,submission_enabled=False,economic_authority=False)
    monkeypatch.setattr(tool,'confirmation',confirm)
    # Only this generated isolated fixture key is opened. The production root
    # tool has no signer; this native worker substitute represents its UID.
    keydoc=tool.decode((case['local']['cfg']/'signing-key.json').read_bytes())
    key=Ed25519PrivateKey.from_private_bytes(_decode(keydoc['private_key_base64url'],32))
    def invoke(**kw):
        calls.append(kw['mode'])
        store=IssuerStore(case['issuer']/'issuance.sqlite3')
        try:
            if kw['mode']=='QUERY': row=store.query_admin(kw['operation_sha256'],purpose=kw['purpose'])
            else:
                material=pub.load_operation(kw['operation_sha256'],source_commit=kw['source_commit'],
                    purpose=kw['purpose'],registry=kw['registry'],role='controller')
                installed=tool.materials._read(tool.supervisor.ISSUER_POLICY)
                if kw['purpose']=='GROUP_CONTINUITY':
                    row=store.group_change(material['operation'],documents=material['documents'],registry=kw['registry'],
                        installed_authorization=installed,key=key,now_ms=case['local']['clock'][0],clock_ms=lambda:case['local']['clock'][0])
                else:
                    row=store.sign_delegation(material['operation'],documents=material['documents'],registry=kw['registry'],
                        installed_template=installed,key=key,now_ms=case['local']['clock'][0],clock_ms=lambda:case['local']['clock'][0])
            artifact=row['payload']['artifact'] if row and kw['purpose']=='DELEGATION_V2_SIGN' else None
            template=(artifact['future_template'] if artifact else row['payload']['future_template']) if row else None
            return dict(schema='DEMO_GOLD_ISSUER_ADMIN_RESULT_V1',source_commit=kw['source_commit'],purpose=kw['purpose'],
                operation_sha256=kw['operation_sha256'],status=('COMMITTED' if kw['mode']=='APPLY' else 'FOUND') if row else 'UNKNOWN',
                record=row,event_sha256=row['record_hash'] if row else None,
                anchor=continuity.history_anchor(store.snapshot(),authorization=template) if row else None,
                artifact=artifact,submission_enabled=False,economic_authority=False)
        finally:store.close()
    monkeypatch.setattr(tool,'invoke_admin',invoke)
    return dict(case=case,tool=tool,calls=calls,confirmations=confirmations,invoke=invoke)


def test_scope_group_install_uses_real_event_and_never_reserves_or_restarts_pilot(native_install):
    f=native_install;tool=f['tool'];case=f['case']
    originals={p:p.read_bytes() for p in (case['local']['root']/'gold-pilot').rglob('*') if p.is_file()}
    result=tool.install_approved(source_commit='c'*40,exit_reserve=dict(grants=1,orders=1,notional='250'))
    assert result['status']=='FUTURE_TEMPLATE_INSTALLED_REQUIRES_SEPARATE_SIGNING'
    assert f['calls']==['APPLY'] and len(f['confirmations'])==1
    rows=case['tool'].issuer_rows(case['registry'])
    assert [r['kind'] for r in rows]==['GROUP_INITIALIZED']
    assert tool.materials._read(tool.supervisor.ISSUER_POLICY)['schema']=='DEMO_GOLD_FUTURE_EXIT_BUDGET_TEMPLATE_V1'
    assert tool.materials._read(tool.supervisor.STATE/'strategy-dispatch.json')['budget_group_binding_sha256']==rows[0]['payload']['operation']['subject']['to_group_binding_sha256']
    assert all(p.read_bytes()==body for p,body in originals.items())


def test_committed_group_response_loss_recovers_only_same_event_without_new_confirmation(native_install,monkeypatch):
    f=native_install;tool=f['tool'];case=f['case'];responses=[]
    def lost(**kw):
        result=f['invoke'](**kw);responses.append(result)
        if kw['mode']=='APPLY':raise OSError('response lost after native commit')
        return result
    monkeypatch.setattr(tool,'invoke_admin',lost)
    with pytest.raises(OSError):tool.install_approved(source_commit='c'*40,exit_reserve=dict(grants=1,orders=1,notional='250'))
    identity=responses[0]['operation_sha256'];case['local']['clock'][0]+=301000
    result=tool.install_approved(source_commit='c'*40,resume_operation_sha256=identity)
    assert result['group_operation_sha256']==identity and f['calls']==['APPLY','QUERY']
    assert len(f['confirmations'])==1 and len(case['tool'].issuer_rows(case['registry']))==1


def test_second_new_confirmation_signs_v2_and_retains_disabled_materials(native_install):
    f=native_install;tool=f['tool'];case=f['case']
    installed=tool.install_approved(source_commit='c'*40,exit_reserve=dict(grants=1,orders=1,notional='250'))
    result=tool.sign_approved(source_commit='c'*40,group_operation_sha256=installed['group_operation_sha256'],
        opening_until_ms=case['configuration']['ends_at_ms']-60000,exit_until_ms=case['scope']['valid_until_ms'],
        max_holding_ms=case['local']['parts']['definition']['rules']['max_holding_bars']*900000)
    assert result['status']=='FUTURE_DELEGATION_SIGNED_REQUIRES_DISABLED_REGISTRATION'
    assert len(f['confirmations'])==2 and len(set(f['confirmations']))==2 and f['calls']==['APPLY','APPLY']
    rows=case['tool'].issuer_rows(case['registry'])
    assert [r['kind'] for r in rows]==['GROUP_INITIALIZED','DELEGATION_V2_SIGNED']
    assert result['delegation_sha256']==rows[-1]['payload']['artifact_sha256']
    assert not (tool.supervisor.STATE/'gold-execution-delegations').exists()
    assert not (tool.public.PUBLIC_ROOT.parent/'demo-gold-delegation').exists()


def test_lost_sign_response_queries_without_resigning_after_subject_expiry(native_install,monkeypatch):
    f=native_install;tool=f['tool'];case=f['case']
    installed=tool.install_approved(source_commit='c'*40,exit_reserve=dict(grants=1,orders=1,notional='250'))
    def lost(**kw):
        result=f['invoke'](**kw)
        if kw['mode']=='APPLY':raise OSError('lost committed signature response')
        return result
    monkeypatch.setattr(tool,'invoke_admin',lost)
    args=dict(source_commit='c'*40,group_operation_sha256=installed['group_operation_sha256'])
    with pytest.raises(OSError):tool.sign_approved(**args,opening_until_ms=case['configuration']['ends_at_ms']-60000,
        exit_until_ms=case['scope']['valid_until_ms'],
        max_holding_ms=case['local']['parts']['definition']['rules']['max_holding_bars']*900000)
    rows=case['tool'].issuer_rows(case['registry']);case['local']['clock'][0]+=301000
    result=tool.sign_approved(**args,resume=True)
    assert result['delegation_sha256']==rows[-1]['payload']['artifact_sha256']
    assert f['calls']==['APPLY','APPLY','QUERY'] and len(f['confirmations'])==2
    assert case['tool'].issuer_rows(case['registry'])==rows


def test_partial_publication_without_attempt_stays_unknown_and_never_sends(native_install,monkeypatch):
    f=native_install;tool=f['tool'];native=tool.installer.write_new
    def stop(path,body,**kw):
        if path.name=='complete.json' and tool.public.PUBLIC_ROOT in path.parents:
            raise OSError('isolated incomplete public operation')
        return native(path,body,**kw)
    with monkeypatch.context() as interrupted:
        interrupted.setattr(tool.installer,'write_new',stop)
        with pytest.raises(OSError):tool.install_approved(source_commit='c'*40,exit_reserve=dict(grants=1,orders=1,notional='250'))
    stages=list((tool.supervisor.STATE/'gold-future-budget-installations').iterdir());assert len(stages)==1
    assert not (stages[0]/'group-attempt.json').exists() and f['calls']==[]
    with pytest.raises((ValueError,FileNotFoundError)):
        tool.install_approved(source_commit='c'*40,resume_operation_sha256=stages[0].name)
    assert f['calls']==[] and f['case']['tool'].issuer_rows(f['case']['registry'])==[]


@pytest.mark.parametrize('resume',[True,False])
def test_missing_committed_group_after_attempt_never_initializes_again(native_install,monkeypatch,resume):
    f=native_install;tool=f['tool'];case=f['case']
    database=case['issuer']/'issuance.sqlite3';empty_original=database.read_bytes()
    with monkeypatch.context() as interrupted:
        interrupted.setattr(tool,'_replace_exact',lambda *a,**kw:(_ for _ in ()).throw(OSError('before root install')))
        with pytest.raises(OSError):tool.install_approved(source_commit='c'*40,exit_reserve=dict(grants=1,orders=1,notional='250'))
    stage=next((tool.supervisor.STATE/'gold-future-budget-installations').iterdir())
    # Restore the actual valid pre-operation database, not a trigger bypass or
    # an invented corrupt row. The independent root attempt must detect loss.
    database.write_bytes(empty_original)
    case['local']['clock'][0]+=1
    before=tool.supervisor.ISSUER_POLICY.read_bytes()
    args=dict(resume_operation_sha256=stage.name) if resume else dict(exit_reserve=dict(grants=1,orders=1,notional='250'))
    with pytest.raises(ValueError,match='UNKNOWN_NEEDS_INDEPENDENT_CONTINUITY'):
        tool.install_approved(source_commit='c'*40,**args)
    assert f['calls']==(['APPLY','QUERY'] if resume else ['APPLY'])
    assert len(f['confirmations'])==1 and tool.supervisor.ISSUER_POLICY.read_bytes()==before
    assert case['tool'].issuer_rows(case['registry'])==[]


def test_incompatible_holding_limit_is_rejected_before_second_confirmation(native_install):
    f=native_install;tool=f['tool'];case=f['case']
    installed=tool.install_approved(source_commit='c'*40,exit_reserve=dict(grants=1,orders=1,notional='250'))
    with pytest.raises(ValueError,match='FUTURE_SIGN_EXPLICIT_WINDOW'):
        tool.sign_approved(source_commit='c'*40,group_operation_sha256=installed['group_operation_sha256'],
            opening_until_ms=case['configuration']['ends_at_ms']-60000,exit_until_ms=case['scope']['valid_until_ms'],
            max_holding_ms=900000)
    assert f['calls']==['APPLY'] and len(f['confirmations'])==1


def test_retained_stage_documents_are_authenticated_before_pilot_selection(native_install,monkeypatch):
    from copy import deepcopy
    f=native_install;tool=f['tool']
    with monkeypatch.context() as interrupted:
        interrupted.setattr(tool,'_replace_exact',lambda *a,**kw:(_ for _ in ()).throw(OSError('before root install')))
        with pytest.raises(OSError):tool.install_approved(source_commit='c'*40,exit_reserve=dict(grants=1,orders=1,notional='250'))
    stage=next((tool.supervisor.STATE/'gold-future-budget-installations').iterdir())
    original=tool.read(stage/'candidate.json');changed=deepcopy(original)
    changed['documents']['definition']['definition_hash']='0'*64
    (stage/'candidate.json').write_bytes(tool.canonical(changed))
    # The actual retained operation is unchanged. Corrupt documents must never
    # reach the pilot/group selector, even if that selector would also reject.
    monkeypatch.setattr(tool.materials.pilot,'completed_configuration_records',
        lambda **kw:pytest.fail('unauthenticated stage reached pilot selection'))
    with pytest.raises(ValueError,match='FUTURE_INSTALL_HISTORY_DOCUMENTS_CHANGED'):
        tool._new_install_history(original)
    assert f['calls']==['APPLY'] and len(f['confirmations'])==1
