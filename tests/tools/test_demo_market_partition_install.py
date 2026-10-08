"""Root partition handoff has durable partial-install barriers and no signer."""
from copy import deepcopy
from pathlib import Path
import pytest

from tests.live.test_demo_market_partition import book as book, candidate as prepare, NOW


@pytest.fixture
def env(book,tmp_path,monkeypatch):
    from tools import demo_market_partition_install as tool
    value=prepare(book);registry=book[2]['registry']
    executor,issuer,controller=[tmp_path/name for name in ('executor-config','issuer-config','controller')]
    for path in (executor,issuer,controller):path.mkdir(mode=0o700)
    monkeypatch.setattr(tool,'EXECUTOR_RECORD',executor/'market-partition.json')
    monkeypatch.setattr(tool,'ISSUER_RECORD',issuer/'market-partition.json')
    monkeypatch.setattr(tool,'INTENT',issuer/'market-partition-intent.json')
    monkeypatch.setattr(tool,'ACTIVATION',controller/'partition-activation.json',raising=False)
    monkeypatch.setattr(tool.supervisor,'STATE',controller)
    monkeypatch.setattr(tool.supervisor,'now_ms',lambda:NOW+130001)
    monkeypatch.setattr(tool,'fresh_candidate',lambda source:(deepcopy(value),deepcopy(registry)))
    def revalidate(source,sealed,original_registry):
        from live.execution.demo_market_partition import revalidate_pre_activation
        from tests.live.test_demo_market_partition_revalidation import fresh_observations
        at=tool.supervisor.now_ms()+10000
        monkeypatch.setattr(tool.supervisor,'now_ms',lambda:at)
        return revalidate_pre_activation(state=book[0],candidate=sealed,registry=original_registry,
            source_commit=source,kill_switch_sha256=sealed['binding']['kill_switch_sha256'],now_ms=at,
            **fresh_observations(book[2],at))
    monkeypatch.setattr(tool,'fresh_revalidation',revalidate)
    monkeypatch.setattr(tool,'protected_parents',lambda:None)
    monkeypatch.setattr(tool,'read_root',lambda p:p.read_bytes())
    monkeypatch.setattr(tool,'read_intent',lambda:tool.INTENT.read_bytes())
    monkeypatch.setattr(tool,'read_audit',lambda p:p.read_bytes())
    monkeypatch.setattr(tool,'read_mirror',lambda p,**kw:tool.read_root(p))
    monkeypatch.setattr(tool.supervisor,'_root_private_directory',lambda p:None)
    monkeypatch.setattr(tool,'local_operator',lambda:'isolated-operator')
    monkeypatch.setattr(tool,'confirm',lambda c,o:dict(schema='DEMO_PARTITION_LOCAL_OPERATOR_APPROVAL_V1',
        candidate_sha256=tool._digest(c),operator_id=o,approved_at_ms=NOW+130001,
        purpose='DEMO_MARKET_PARTITION_INSTALL_ONLY'))
    # Only virtual identity/custody. Lease, exclusive writes, fsync and readback
    # are real and stay entirely within these isolated directories.
    monkeypatch.setattr(tool.installer.os,'chown',lambda *a,**k:None)
    monkeypatch.setattr(tool.installer.os,'fchown',lambda *a,**k:None)
    return tool,value,registry,controller


def test_default_review_does_not_publish_configuration(env):
    tool,_,_,_=env
    result=tool.review(source_commit='c'*40)
    assert result['installed'] is False and result['orders_submitted'] is False
    assert not tool.EXECUTOR_RECORD.exists() and not tool.ISSUER_RECORD.exists()
    assert not tool.INTENT.exists()


def test_confirmed_install_retains_original_materials_and_identical_two_roles(env):
    tool,value,registry,controller=env
    result=tool.install_approved(source_commit='c'*40)
    assert result['status']=='PARTITION_INSTALLED_REQUIRES_NEW_GOLD_ENROLLMENT'
    assert result['installed'] is True and result['orders_submitted'] is False
    assert tool.EXECUTOR_RECORD.read_bytes()==tool.ISSUER_RECORD.read_bytes()
    import json
    record=json.loads(tool.EXECUTOR_RECORD.read_bytes())
    assert record['candidate']==value and record['legacy_registry']==registry
    stage=Path(result['audit_directory'])
    assert json.loads((stage/'candidate.json').read_bytes())==value
    assert json.loads((stage/'legacy-registry.json').read_bytes())==registry
    assert tool.INTENT.exists() and not record['submission_enabled']
    before=tool.EXECUTOR_RECORD.read_bytes()
    with pytest.raises(ValueError):tool.install_approved(source_commit='c'*40)
    assert tool.EXECUTOR_RECORD.read_bytes()==before


def test_refused_local_identity_precedes_protected_reads(env,monkeypatch):
    tool,_,_,_=env
    monkeypatch.setattr(tool,'local_operator',lambda:(_ for _ in ()).throw(ValueError('TTY_REQUIRED')))
    monkeypatch.setattr(tool,'fresh_candidate',lambda *_:pytest.fail('protected reads before local identity'))
    with pytest.raises(ValueError):tool.install_approved(source_commit='c'*40)
    assert not tool.INTENT.exists()


@pytest.mark.parametrize('damage',['state','context','stop','wallet','registry','source'])
def test_changed_candidate_after_confirmation_is_rejected_before_publication(env,monkeypatch,damage):
    tool,value,registry,_=env;calls=[]
    def fresh(source):
        current=deepcopy(value);calls.append(1)
        if len(calls)>1:
            key={'state':'legacy_state_sha256','context':'legacy_context_sha256',
                 'stop':'kill_switch_sha256','wallet':'gold_initial_wallet',
                 'registry':'registry_sha256','source':'source_commit'}[damage]
            current['binding'][key]='4300' if damage=='wallet' else ('d'*40 if damage=='source' else 'e'*64)
            current['binding_sha256']=tool._digest(current['binding'])
        return current,deepcopy(registry)
    monkeypatch.setattr(tool,'fresh_candidate',fresh)
    with pytest.raises(ValueError):tool.install_approved(source_commit='c'*40)
    assert not tool.EXECUTOR_RECORD.exists() and not tool.ISSUER_RECORD.exists()


@pytest.mark.parametrize('failure',['intent','audit','issuer','executor','readback','result'])
def test_every_possible_persistent_failure_is_unknown_and_not_replayed(env,monkeypatch,failure):
    tool,_,_,_=env;original=tool.installer.write_new
    def write(path,*a,**kw):
        hit=(failure=='intent' and path==tool.INTENT or failure=='audit' and path.name=='candidate.json'
             or failure=='issuer' and path==tool.ISSUER_RECORD or failure=='executor' and path==tool.EXECUTOR_RECORD
             or failure=='result' and path.name=='result.json')
        if hit:
            if failure=='intent':original(path,*a,**kw)
            raise OSError('isolated storage failure')
        return original(path,*a,**kw)
    monkeypatch.setattr(tool.installer,'write_new',write)
    if failure=='readback':
        original_read=tool.read_root
        monkeypatch.setattr(tool,'read_root',lambda p:b'wrong' if p==tool.EXECUTOR_RECORD else original_read(p))
    with pytest.raises(tool.InstallIncomplete) as error:tool.install_approved(source_commit='c'*40)
    assert error.value.result['installed'] is None and tool.INTENT.exists()
    with pytest.raises(ValueError):tool.install_approved(source_commit='c'*40)


def test_new_observation_times_do_not_rebase_the_approved_prefix(env,monkeypatch):
    tool,value,registry,_=env;calls=[]
    def fresh(source):
        current=deepcopy(value);calls.append(1)
        current['binding']['recorded_at_ms']+=len(calls)
        current['review']['reviewed_at_ms']=current['binding']['recorded_at_ms']
        current['review']['legacy_origin_sha256']='a'*64
        current['review']['gold_origin_sha256']='b'*64
        current['review']['gold_observation']['observed_at_ms']+=len(calls)
        current['binding']['review_sha256']=tool._digest(current['review'])
        current['binding_sha256']=tool._digest(current['binding'])
        return current,deepcopy(registry)
    monkeypatch.setattr(tool,'fresh_candidate',fresh)
    monkeypatch.setattr(tool.supervisor,'now_ms',lambda:NOW+140000)
    result=tool.install_approved(source_commit='c'*40)
    import json
    installed=json.loads(tool.EXECUTOR_RECORD.read_bytes())['candidate']
    assert installed['binding']['recorded_at_ms']==value['binding']['recorded_at_ms']+1
    assert installed['binding']['gold_initial_wallet']==value['binding']['gold_initial_wallet']
    assert result['installed'] is True and len(calls)==2
    assert list(Path(result['audit_directory']).glob('pre-activation-revalidations/*.json'))


def test_changed_operator_before_the_first_persistent_write_is_rejected(env,monkeypatch):
    tool,_,_,_=env;calls=[]
    def operator():
        calls.append(1)
        return 'isolated-operator' if len(calls)==1 else 'different-operator'
    monkeypatch.setattr(tool,'local_operator',operator)
    with pytest.raises(ValueError):tool.install_approved(source_commit='c'*40)
    assert not tool.INTENT.exists()


def test_clock_reversal_during_revalidation_is_rejected_without_publication(env,monkeypatch):
    tool,value,registry,_=env;calls=[]
    def fresh(source):
        current=deepcopy(value);calls.append(1)
        if len(calls)>1:
            current['binding']['recorded_at_ms']-=1
            current['review']['reviewed_at_ms']=current['binding']['recorded_at_ms']
            current['binding']['review_sha256']=tool._digest(current['review'])
            current['binding_sha256']=tool._digest(current['binding'])
        return current,deepcopy(registry)
    monkeypatch.setattr(tool,'fresh_candidate',fresh)
    with pytest.raises(ValueError):tool.install_approved(source_commit='c'*40)
    assert not tool.INTENT.exists()


def test_inputs_change_after_the_two_copies_are_written_keeps_unknown(env,monkeypatch):
    tool,_,_,_=env;original=tool.fresh_revalidation
    def fresh(*args):
        report=deepcopy(original(*args))
        if tool.ISSUER_RECORD.exists() and tool.EXECUTOR_RECORD.exists():report['kill_switch_sha256']='e'*64
        return report
    monkeypatch.setattr(tool,'fresh_revalidation',fresh)
    with pytest.raises(tool.InstallIncomplete) as error:tool.install_approved(source_commit='c'*40)
    assert error.value.result['installed'] is None
    assert tool.EXECUTOR_RECORD.exists() and tool.ISSUER_RECORD.exists() and tool.INTENT.exists()
    with pytest.raises(ValueError):tool.install_approved(source_commit='c'*40)


def test_repeated_cli_does_not_relabel_a_partial_install_as_absent(env,monkeypatch,capsys):
    tool,_,_,_=env;original=tool.installer.write_new
    def write(path,*args,**kwargs):
        if path==tool.EXECUTOR_RECORD:raise OSError('isolated executor publication failure')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(tool.installer,'write_new',write)
    with pytest.raises(tool.InstallIncomplete):tool.install_approved(source_commit='c'*40)
    assert tool.main(['--source-commit','c'*40,'--install-approved'])==3
    import json
    result=json.loads(capsys.readouterr().out)
    assert result['installed'] is None and not result['orders_submitted']


def test_existing_controller_work_prevents_partition_installation(env):
    tool,_,_,_=env
    with tool.supervisor.controller_lease():
        with pytest.raises(ValueError):tool.install_approved(source_commit='c'*40)
    assert not tool.INTENT.exists()


def test_single_final_activation_follows_the_complete_audit(env):
    tool,_,_,_=env
    result=tool.install_approved(source_commit='c'*40)
    import json
    activation=json.loads(tool.ACTIVATION.read_bytes())
    retained=json.loads((Path(result['audit_directory'])/'result.json').read_bytes())
    assert retained['status']=='PARTITION_READY_FOR_ACTIVATION' and retained['installed'] is False
    assert activation['installed_record_sha256']==result['record_sha256']
    assert activation['ready_result_sha256']==tool._digest(retained)
    assert activation['submission_enabled'] is False and result['installed'] is True


def test_audit_failure_does_not_leave_a_runtime_activation(env,monkeypatch):
    tool,_,_,_=env;original=tool.installer.write_new
    def write(path,*args,**kwargs):
        if path.name=='result.json':raise OSError('isolated result storage failure')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(tool.installer,'write_new',write)
    with pytest.raises(tool.InstallIncomplete):tool.install_approved(source_commit='c'*40)
    assert tool.EXECUTOR_RECORD.exists() and tool.ISSUER_RECORD.exists()
    assert not tool.ACTIVATION.exists()
