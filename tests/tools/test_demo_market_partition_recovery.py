"""A fresh local operator completes the exact durable installation intent."""
import json
from copy import deepcopy

import pytest

from tests.tools.test_demo_market_partition_install import env as env, book as book, NOW


@pytest.fixture
def recovery_env(env,monkeypatch):
    tool,value,registry,controller=env
    monkeypatch.setattr(tool.os,'geteuid',lambda:0)
    monkeypatch.setattr(tool.successor,'verify_source',lambda source:None)
    monkeypatch.setattr(tool,'read_intent',lambda:tool.INTENT.read_bytes(),raising=False)
    monkeypatch.setattr(tool,'read_audit',lambda path:path.read_bytes(),raising=False)
    monkeypatch.setattr(tool,'read_mirror',lambda path,**kw:path.read_bytes(),raising=False)
    monkeypatch.setattr(tool,'confirm_recovery',lambda intent,operator:dict(
        schema='DEMO_PARTITION_LOCAL_RECOVERY_APPROVAL_V1',intent_sha256=tool._digest(intent),
        candidate_sha256=intent['candidate_sha256'],operator_id=operator,
        approved_at_ms=tool.supervisor.now_ms(),purpose='COMPLETE_ORIGINAL_PARTITION_INSTALL_ONLY'),raising=False)
    return env


def interrupt(tool,monkeypatch,point):
    write=tool.installer.write_new
    def failed(path,*args,**kw):
        hit=(point=='audit' and path.name=='candidate.json'
             or point=='issuer' and path==tool.ISSUER_RECORD
             or point=='executor' and path==tool.EXECUTOR_RECORD
             or point=='result' and path.name=='result.json'
             or point.startswith('activation') and path==tool.ACTIVATION)
        if hit:
            if point=='activation_after':write(path,*args,**kw)
            raise OSError('isolated interruption')
        return write(path,*args,**kw)
    monkeypatch.setattr(tool.installer,'write_new',failed)
    with pytest.raises(tool.InstallIncomplete):tool.install_approved(source_commit='c'*40)
    monkeypatch.setattr(tool.installer,'write_new',write)
    return tool.INTENT.read_bytes()


def test_first_durable_intent_retains_complete_original_materials(recovery_env,monkeypatch):
    tool,value,registry,_=recovery_env
    raw=interrupt(tool,monkeypatch,'audit');intent=json.loads(raw)
    assert intent['schema']=='DEMO_MARKET_PARTITION_INSTALL_INTENT_V2'
    assert intent['record']['candidate']==value
    assert intent['record']['legacy_registry']==registry
    assert intent['operator_approval_sha256']==tool._digest(intent['record']['operator_approval'])
    assert intent['record']['revalidation_sha256']==tool._digest(intent['revalidation'])
    assert intent['ready_result']['installed'] is False
    assert not tool.ISSUER_RECORD.exists() and not tool.ACTIVATION.exists()


@pytest.mark.parametrize('point',['audit','issuer','executor','result','activation_before','activation_after'])
def test_recovery_preserves_original_cut_and_partial_bytes(recovery_env,monkeypatch,point):
    tool,value,registry,controller=recovery_env
    original_intent=interrupt(tool,monkeypatch,point)
    old={path:path.read_bytes() for path in controller.rglob('*.json') if path.is_file()}
    mirrors={path:path.read_bytes() for path in (tool.ISSUER_RECORD,tool.EXECUTOR_RECORD,tool.ACTIVATION) if path.exists()}
    result=tool.recover_approved(source_commit='c'*40)
    assert result['installed'] is True and result['orders_submitted'] is False
    assert tool.INTENT.read_bytes()==original_intent
    assert all(path.read_bytes()==body for path,body in old.items())
    assert all(path.read_bytes()==body for path,body in mirrors.items())
    record=json.loads(tool.EXECUTOR_RECORD.read_bytes())
    assert record['candidate']==value and record['legacy_registry']==registry
    assert tool.EXECUTOR_RECORD.read_bytes()==tool.ISSUER_RECORD.read_bytes()
    assert record['submission_enabled'] is False and record['economic_authority'] is False
    assert tool.ACTIVATION.exists()


def test_recovery_identity_precedes_protected_reads(recovery_env,monkeypatch):
    tool,_,_,_=recovery_env
    monkeypatch.setattr(tool,'local_operator',lambda:(_ for _ in ()).throw(ValueError('TTY_REQUIRED')))
    monkeypatch.setattr(tool,'read_intent',lambda:pytest.fail('read before independent identity'))
    with pytest.raises(ValueError):tool.recover_approved(source_commit='c'*40)
    assert not tool.INTENT.exists()


def test_changed_existing_mirror_is_not_overwritten_or_activated(recovery_env,monkeypatch):
    tool,_,_,_=recovery_env
    interrupt(tool,monkeypatch,'executor')
    tool.ISSUER_RECORD.write_bytes(b'changed')
    with pytest.raises(ValueError):tool.recover_approved(source_commit='c'*40)
    assert tool.ISSUER_RECORD.read_bytes()==b'changed'
    assert not tool.EXECUTOR_RECORD.exists() and not tool.ACTIVATION.exists()


@pytest.mark.parametrize('change',['wallet','stop','state','operator'])
def test_recovery_rechecks_original_inputs_after_confirmation(recovery_env,monkeypatch,change):
    tool,value,registry,_=recovery_env
    interrupt(tool,monkeypatch,'audit')
    original=tool.confirm_recovery
    def confirm(intent,operator):
        response=original(intent,operator)
        if change=='operator':monkeypatch.setattr(tool,'local_operator',lambda:'changed-operator')
        else:
            fresh=tool.fresh_revalidation
            def changed(*args):
                report=deepcopy(fresh(*args))
                field={'wallet':'gold_initial_wallet','stop':'kill_switch_sha256','state':'full_state_sha256'}[change]
                replacement='4300' if change=='wallet' else ('e'*64 if report[field]!='e'*64 else 'f'*64)
                assert report[field]!=replacement
                report[field]=replacement
                return report
            monkeypatch.setattr(tool,'fresh_revalidation',changed)
        return response
    monkeypatch.setattr(tool,'confirm_recovery',confirm)
    with pytest.raises(ValueError):tool.recover_approved(source_commit='c'*40)
    assert not tool.ISSUER_RECORD.exists() and not tool.ACTIVATION.exists()


def test_completed_install_recovery_is_read_only_and_does_not_rebase(recovery_env,monkeypatch):
    tool,_,_,_=recovery_env
    tool.install_approved(source_commit='c'*40)
    before=tool.INTENT.read_bytes(),tool.ACTIVATION.read_bytes()
    monkeypatch.setattr(tool,'confirm_recovery',lambda *_:pytest.fail('completed install confirmation'))
    monkeypatch.setattr(tool,'fresh_candidate',lambda *_:pytest.fail('completed install rebase'))
    monkeypatch.setattr(tool.installer,'write_new',lambda *_ ,**kw:pytest.fail('completed install write'))
    result=tool.recover_approved(source_commit='c'*40)
    assert result['status']=='PARTITION_INSTALL_ALREADY_COMPLETE' and result['installed'] is True
    assert before==(tool.INTENT.read_bytes(),tool.ACTIVATION.read_bytes())


def test_a_second_recovery_failure_keeps_all_previous_audit(recovery_env,monkeypatch):
    tool,_,_,controller=recovery_env
    interrupt(tool,monkeypatch,'executor')
    old={path:path.read_bytes() for path in controller.rglob('*.json') if path.is_file()}
    write=tool.installer.write_new
    def again(path,*args,**kw):
        if path==tool.ACTIVATION:raise OSError('second isolated failure')
        return write(path,*args,**kw)
    monkeypatch.setattr(tool.installer,'write_new',again)
    with pytest.raises(tool.InstallIncomplete) as error:tool.recover_approved(source_commit='c'*40)
    assert error.value.result['installed'] is None and not tool.ACTIVATION.exists()
    assert all(path.read_bytes()==body for path,body in old.items())
    monkeypatch.setattr(tool.installer,'write_new',write)
    assert tool.recover_approved(source_commit='c'*40)['installed'] is True
    assert all(path.read_bytes()==body for path,body in old.items())


@pytest.mark.parametrize('damage',['v1','clock','purpose','path','revalidation','truncated'])
def test_invalid_intent_never_publishes_a_role_or_activation(recovery_env,monkeypatch,damage):
    tool,_,_,_=recovery_env
    interrupt(tool,monkeypatch,'audit')
    intent=json.loads(tool.INTENT.read_bytes())
    if damage=='v1':intent['schema']='DEMO_MARKET_PARTITION_INSTALL_INTENT_V1'
    elif damage=='clock':intent['record']['installed_at_ms']=True
    elif damage=='purpose':intent['record']['operator_approval']['purpose']='ALLOW_ORDERS'
    elif damage=='path':intent['ready_result']['audit_directory']='/outside-original-stage'
    elif damage=='revalidation':intent['record']['revalidation_sha256']='0'*64
    tool.INTENT.write_bytes(b'{' if damage=='truncated' else tool.installer.canonical(intent))
    before=tool.INTENT.read_bytes()
    monkeypatch.setattr(tool,'confirm_recovery',lambda *_:pytest.fail('invalid intent confirmed'))
    with pytest.raises(ValueError):tool.recover_approved(source_commit='c'*40)
    assert tool.INTENT.read_bytes()==before
    assert not tool.ISSUER_RECORD.exists() and not tool.ACTIVATION.exists()


def test_completed_recovery_remains_read_only_after_utc_day_changes(recovery_env,monkeypatch):
    tool,_,_,_=recovery_env
    tool.install_approved(source_commit='c'*40)
    monkeypatch.setattr(tool.supervisor,'now_ms',lambda:NOW+86400000+130001)
    monkeypatch.setattr(tool.installer,'write_new',lambda *a,**kw:pytest.fail('completed record changed'))
    assert tool.review_recovery(source_commit='c'*40)['installed'] is True
    assert tool.recover_approved(source_commit='c'*40)['status']=='PARTITION_INSTALL_ALREADY_COMPLETE'


def test_recovery_review_leaves_partial_files_and_no_attempt(recovery_env,monkeypatch,capsys):
    tool,_,_,controller=recovery_env
    interrupt(tool,monkeypatch,'executor')
    before={path:path.read_bytes() for path in controller.rglob('*.json') if path.is_file()}
    monkeypatch.setattr(tool,'confirm_recovery',lambda *_:pytest.fail('read-only review confirmation'))
    assert tool.main(['--source-commit','c'*40,'--review-recovery'])==0
    assert json.loads(capsys.readouterr().out)['installed'] is None
    assert all(path.read_bytes()==body for path,body in before.items())
    assert not tool.ACTIVATION.exists()
    assert not any(controller.rglob('recovery-attempts'))


def test_wrong_mirror_readers_are_rejected_before_returning_bytes(recovery_env,monkeypatch):
    from types import SimpleNamespace
    tool,_,registry,_=recovery_env
    reader=tool.__dict__['read_mirror']
    # Exercise the real boundary rather than this fixture's virtual custody.
    from importlib.util import spec_from_file_location,module_from_spec
    spec=spec_from_file_location('partition_custody_only',tool.__file__)
    fresh=module_from_spec(spec);spec.loader.exec_module(fresh)
    def read(path,*,file_check):
        file_check(SimpleNamespace(st_gid=registry['issuer_uid']+1,st_mode=0o640))
        return b'unchanged'
    monkeypatch.setattr(fresh,'_read_root_owned_json',read)
    with pytest.raises(ValueError):fresh.read_mirror(tool.ISSUER_RECORD,gid=registry['issuer_uid'])
    assert reader is tool.read_mirror
