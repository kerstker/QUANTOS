"""First Scope installer boundaries; all identities and paths are isolated.

The later cold-start fixture comes from original BTC installation/issuer output.
These entrypoint tests never read host deployment or credential paths.
"""
import json

import pytest

from tests.tools.test_gold_first_scope_materials import first_scope_archive as first_scope_archive, PARENT, OLD_SOURCE


@pytest.fixture
def cold_scope_local(tmp_path, monkeypatch, first_scope_archive):
    """Real old installer, BTC issuer/recovery, partition and cold fixed readers.

    Only host identities, accepted source, private transport and external account
    observations are isolated. Original compiler, databases, replay and writes run.
    """
    from copy import deepcopy
    from functools import partial
    from pathlib import Path
    import os
    import shutil
    import stat
    from types import SimpleNamespace
    from tests.live.test_gold_first_scope_original_history import build_original_btc_history, native_old_operator_materials
    from tests.live import test_demo_pre_submit as pre_fixture
    from tests.live.test_demo_market_partition_revalidation import fresh_observations
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    from live.execution import demo_authority, demo_market_partition_runtime as partition
    from services.research.demo_execution_origin import save
    from tools import demo_operating_bundle as installer, demo_operator_setup as setup, demo_successor_setup as successor
    from tools import demo_supervisor as supervisor, demo_market_partition_install as partition_install
    from tools import demo_functional_cycle as channel, demo_functional_worker as worker
    from tools import gold_first_scope_install as tool, gold_first_scope_enrollment as enrollment
    from tools import gold_strategy_budget_enrollment as strategy, gold_future_budget_materials as future
    from tools import demo_resident_recovery as custody, demo_issuer
    # Load native aliases before a local custody replacement can be captured by
    # a lazy import. Patch each alias per case so monkeypatch restores it too.
    from tools import (gold_bar_fresh_inputs, gold_bar_episode_tail,
        demo_reported_recovery, gold_bar_worker_inputs, gold_bar_pilot_capacity,
        gold_pilot_retention, gold_pilot_retention_store, gold_strategy_worker,
        gold_bar_cash, gold_feedback_legacy)
    custody_readers = (gold_bar_fresh_inputs, gold_bar_episode_tail,
        demo_reported_recovery, gold_bar_worker_inputs, gold_bar_pilot_capacity,
        gold_pilot_retention, gold_pilot_retention_store, gold_strategy_worker,
        gold_bar_cash, gold_feedback_legacy)
    uid = os.getuid()
    def fixed_stores(root):
        return (DemoDispatchStore(root/'dispatch.sqlite3', create=True),
            SqliteOrderRecoveryStore(str(root/'recovery.sqlite3'), create=True),
            LiveLedger(str(root/'oms.sqlite3'), create=True))
    monkeypatch.setattr(pre_fixture, 'stores', fixed_stores)
    history = build_original_btc_history(tmp_path, monkeypatch,
        materials_factory=partial(native_old_operator_materials, source_commit=OLD_SOURCE))
    old = history['installed_materials'];state, context, values, stores, events = history['book_tuple']
    stores={name:stores[name] for name in ('dispatch','recovery','oms')}
    root, area, cfg, ctl, public = (old[k] for k in ('executor','setup','config','controller','public_registry'))
    clock = [values['now_ms']+10000]
    (ctl/'functional-cycle').mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setattr(successor, 'SETUP', area)
    for key, path in dict(EXECUTOR=root, ISSUER=history['issuer_root'], CONFIG=cfg, CONTROLLER=ctl, REGISTRY=public).items():
        monkeypatch.setattr(installer, key, path)
    for key, path in dict(STATE=ctl, EXECUTOR_REGISTRY=public, ISSUER_REGISTRY=cfg/'authority.json', ISSUER_POLICY=cfg/'policy.json').items():
        monkeypatch.setattr(supervisor, key, path)
    # Exact native custody checks with local UID metadata substituted only.
    native_check = custody._check_path
    fixture_parts = tmp_path.parts
    def check(path, expected, *, directory):
        parts = Path(path).parts
        if parts[:len(fixture_parts)] != fixture_parts or '..' in parts:
            raise ValueError('outside isolated fixture')
        return native_check(path, uid, directory=directory)
    monkeypatch.setattr(custody, '_check_path', check)
    monkeypatch.setattr(future, '_check_path', check)
    for module in custody_readers:
        monkeypatch.setattr(module, '_check_path', check)
    native_read = future._read_target
    monkeypatch.setattr(future, '_read_target', lambda path, **kw:native_read(path, **(kw|{'uid':uid})))
    native_issuer = demo_issuer._state_custody
    monkeypatch.setattr(demo_issuer, '_require_protected', lambda info, **kw:None)
    monkeypatch.setattr(demo_issuer, '_state_custody', lambda path, expected:native_issuer(path, uid))
    native_load = strategy.load_definition
    monkeypatch.setattr(strategy, 'load_definition', lambda *a, **kw:native_load(*a, **(kw|{'uid':uid})))
    native_metadata = installer.private_metadata
    def metadata(info, *, maximum):
        native_metadata(SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_gid=0,
            st_nlink=info.st_nlink, st_size=info.st_size), maximum=maximum)
    monkeypatch.setattr(installer, 'private_metadata', metadata)
    monkeypatch.setattr(os, 'geteuid', lambda:0)
    monkeypatch.setattr(os, 'chown', lambda *a, **kw:None)
    monkeypatch.setattr(os, 'fchown', lambda *a, **kw:None)
    monkeypatch.setattr(installer, '_require_protected', lambda info, **kw:None)
    def private_dir(path):
        info=path.lstat();assert stat.S_ISDIR(info.st_mode) and not info.st_mode&0o022
    monkeypatch.setattr(supervisor, '_root_private_directory', private_dir)
    for module in (installer, supervisor, successor):
        monkeypatch.setattr(module, '_read_root_owned_json', lambda path:path.read_bytes())
    for module in (supervisor, successor):
        monkeypatch.setattr(module, 'verify_source', lambda source:None if source==PARENT else pytest.fail('source changed'))
    for module in (installer, supervisor, worker):
        monkeypatch.setattr(module, 'now_ms', lambda:clock[0])
    monkeypatch.setattr(demo_authority, 'load_registry_for_executor', lambda:json.loads(public.read_bytes()))
    monkeypatch.setattr(worker, '_open', lambda stack, path:stores)
    monkeypatch.setattr(worker, '_check_path', check)
    def capture(source, *, continuation, symbol):
        assert source==PARENT and continuation
        origin=fresh_observations(values,clock[0])['legacy_origin' if symbol=='BTCUSDC' else 'gold_origin']
        save(root/'execution-origins'/symbol, origin)
        for path in (root/'execution-origins',root/'execution-origins'/symbol):path.chmod(0o700)
        return json.loads(public.read_bytes()), origin
    monkeypatch.setattr(setup, '_runtime_inputs', capture)
    calls=[]
    class NativeChannel:
        def __init__(self, source, registry, cycle):
            assert source==PARENT and registry==json.loads(public.read_bytes())
        def invoke(self, operation, *, payload, timeout):
            assert operation=='context' and timeout==60
            calls.append(deepcopy(payload))
            if 'market_partition_origins' in payload:
                fn=worker.prepare_market_partition
            elif 'first_gold_activation_origin_sha256' in payload:
                fn=worker.review_first_gold_activation
            else:fn=worker.review_first_gold
            return fn(root, context['account_ref_sha256'], PARENT, payload)
        def close(self):pass
    monkeypatch.setattr(channel, 'Runtime', NativeChannel)
    paths=dict(EXECUTOR_RECORD=cfg/'partition-executor.json',ISSUER_RECORD=cfg/'partition-issuer.json',
        INTENT=cfg/'partition-intent.json',ACTIVATION=ctl/'partition-activation.json')
    for key,path in paths.items():monkeypatch.setattr(partition_install,key,path)
    for key in ('EXECUTOR_RECORD','ISSUER_RECORD','ACTIVATION'):monkeypatch.setattr(partition,key,paths[key])
    monkeypatch.setattr(partition_install,'protected_parents',lambda:None)
    monkeypatch.setattr(partition_install,'read_root',lambda path:path.read_bytes())
    monkeypatch.setattr(partition_install,'read_mirror',lambda path,**kw:path.read_bytes())
    monkeypatch.setattr(partition_install,'local_operator',lambda:'isolated-operator')
    monkeypatch.setattr('builtins.input',lambda prompt:prompt.split('请输入 ',1)[1].strip())
    native_revalidation=partition_install.fresh_revalidation
    def revalidation(source,candidate,registry):
        clock[0]=max(clock[0],candidate['binding']['recorded_at_ms']+4000)
        return native_revalidation(source,candidate,registry)
    monkeypatch.setattr(partition_install,'fresh_revalidation',revalidation)
    assert partition_install.install_approved(source_commit=PARENT)['installed'] is True
    monkeypatch.setattr(partition,'_read_record',lambda path,**kw:path.read_bytes())
    monkeypatch.setattr(partition,'_role_identity',lambda *args:None)
    monkeypatch.setattr(partition,'executing_source_commit',lambda:PARENT)
    clock[0]+=1000
    definition, runtime=deepcopy(tuple(first_scope_archive))
    trial=root/'strategy-bar-trials'/runtime['trial_manifest_sha256']
    source=root/'strategy-bar-sources'/runtime['archive_source_commit']
    shutil.copytree(first_scope_archive.archive,trial)
    shutil.copytree(first_scope_archive.source,source)
    for tree in (root/'strategy-bar-trials', root/'strategy-bar-sources'):
        for path in (tree,*tree.rglob('*')):path.chmod(0o700 if path.is_dir() else 0o600)
    (ctl/'strategy-runtime.json').write_bytes(setup.canonical(runtime));(ctl/'strategy-runtime.json').chmod(0o600)
    confirmations=[]
    for module in (tool,enrollment):monkeypatch.setattr(module,'local_operator',lambda:'isolated-operator')
    native_confirmation=tool.operator_confirmation
    def confirmation(request,cycle,who):
        confirmations.append(deepcopy(request))
        return native_confirmation(request,cycle,who)
    monkeypatch.setattr(tool,'operator_confirmation',confirmation)
    value=dict(state=state,context=context,stores=stores,events=events,legacy=deepcopy(old['registry']),
        record=json.loads(paths['EXECUTOR_RECORD'].read_bytes()),origin=fresh_observations(values,clock[0])['gold_origin'])
    local=dict(root=root,area=area,cfg=cfg,ctl=ctl,public=public,clock=clock,value=value,
        parts=dict(definition=definition,runtime=runtime,origin=value['origin'],registry=old['registry']),
        original=old['result'],history=history,calls=calls,confirmations=confirmations,
        old_files={p.name:p.read_bytes() for p in cfg.iterdir()},
        old_tasks={p.name:p.read_bytes() for p in (ctl/'tasks').iterdir()},
        archive=trial,source=source)
    try:yield local
    finally:history['close']()


@pytest.fixture
def installed_first_scope(cold_scope_local, capsys):
    from tools import gold_first_scope_install as tool, gold_future_budget_materials as future
    local=cold_scope_local
    assert tool.main(['--source-commit',PARENT,'--enroll-approved'])==0,capsys.readouterr().out
    scope=json.loads((local['cfg']/'policy.json').read_bytes());registry=json.loads(local['public'].read_bytes())
    records=future.authorization_ancestry(scope,registry,PARENT)
    return dict(local=local,records=records,scope=scope,registry=registry,
        issuer_root=local['history']['issuer_root'],history=local['history'])


def test_first_scope_cold_install_preserves_original_btc_history_and_tasks(cold_scope_local,capsys):
    from tools import gold_first_scope_install as tool
    from live.execution.demo_pre_submit import _state
    local=cold_scope_local;history=local['history']
    before=history['issuer_store'].snapshot(),_state(**local['value']['stores'])
    assert tool.main(['--source-commit',PARENT,'--enroll-approved'])==0,capsys.readouterr().out
    assert len(local['confirmations'])==1
    scope=json.loads((local['cfg']/'policy.json').read_bytes())
    assert scope['schema']=='DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V2'
    assert scope['scope_enrollment']['original_source_commit']==OLD_SOURCE
    stage=tool.successor.active_setup()
    assert json.loads((stage/'bundle/manifest.json').read_bytes())['tasks']==[]
    assert local['old_tasks']=={p.name:p.read_bytes() for p in (local['ctl']/'tasks').iterdir()}
    assert before==(history['issuer_store'].snapshot(),_state(**local['value']['stores']))
    assert (stage/'previous-files/policy.json').read_bytes()==local['old_files']['policy.json']


def test_first_scope_fixed_ancestry_reader_accepts_original_old_source(installed_first_scope):
    value=installed_first_scope
    assert value['records']['schema']=='DEMO_GOLD_FIRST_SCOPE_INSTALLATION_ANCESTRY_V1'
    assert value['records']['original_setup']=={
        name:value['history']['installed_materials'][name] for name in ('spec','result')}


def test_completed_receipt_after_expiry_is_historical_only(installed_first_scope,monkeypatch,capsys):
    from tools import gold_first_scope_install as tool
    from live.execution import demo_gold_budget_scope as contract
    value=installed_first_scope;local=value['local']
    local['clock'][0]=value['scope']['valid_until_ms']+1
    monkeypatch.setattr(tool,'operator_confirmation',lambda *a:pytest.fail('historical receipt cannot renew'))
    capsys.readouterr()
    assert tool.main(['--source-commit',PARENT,'--enroll-approved'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='FIRST_GOLD_SCOPE_ALREADY_INSTALLED'
    assert result['authorization_current_verified'] is False
    with pytest.raises(ValueError):contract.pilot_window(value['scope'],registry=value['registry'],now_ms=local['clock'][0])


@pytest.mark.parametrize('damage',['result','result_rebound','pointer','rolled_back_active'])
def test_completed_first_scope_cannot_be_reopened_or_reported_from_changed_activation(cold_scope_local,monkeypatch,capsys,damage):
    from tools import gold_first_scope_install as tool
    local=cold_scope_local
    assert tool.main(['--source-commit',PARENT,'--enroll-approved'])==0,capsys.readouterr().out
    capsys.readouterr();stage=tool.successor.active_setup()
    if damage in ('result','result_rebound'):
        result=json.loads((stage/'result.json').read_bytes());result['task_sha256']='a'*64
        (stage/'result.json').write_bytes(tool.installer.canonical(result))
        if damage=='result_rebound':
            pointer=json.loads((local['area']/'active.json').read_bytes());pointer['result_sha256']=tool.setup.digest(result)
            (local['area']/'active.json').write_bytes(tool.installer.canonical(pointer))
    elif damage=='pointer':
        pointer=json.loads((local['area']/'active.json').read_bytes());pointer['result_sha256']='a'*64
        (local['area']/'active.json').write_bytes(tool.installer.canonical(pointer))
    else:
        (local['area']/'active.json').unlink()
        for name,body in local['old_files'].items():(local['cfg']/name).write_bytes(body)
        local['public'].write_bytes(tool.installer.canonical(local['history']['old_registry']))
    monkeypatch.setattr(tool,'create_stage',lambda **kw:pytest.fail('completed original first attempt must not enroll again'))
    before={str(p):p.read_bytes() for p in local['area'].rglob('*') if p.is_file()}
    assert tool.main(['--source-commit',PARENT,'--enroll-approved'])==3
    result=json.loads(capsys.readouterr().out)
    assert result['scope_installed'] is None,result
    assert before=={str(p):p.read_bytes() for p in local['area'].rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage',['refused','late','runtime','old_setup','issuer_rollback'])
def test_confirmation_cannot_replace_original_inputs_or_issuer(cold_scope_local,monkeypatch,capsys,damage):
    import os
    from tools import gold_first_scope_install as tool
    from live.execution.demo_issuer_store import IssuerStore
    local=cold_scope_local;native=tool.operator_confirmation
    before=local['public'].read_bytes(),(local['cfg']/'policy.json').read_bytes()
    def confirm(*args):
        if damage=='refused':raise ValueError('FIRST_SCOPE_NOT_CONFIRMED')
        result=native(*args)
        if damage=='late':local['clock'][0]+=30001
        elif damage=='runtime':(local['ctl']/'strategy-runtime.json').write_text('{}')
        elif damage=='old_setup':
            result_old=json.loads((local['area']/'result.json').read_bytes());result_old['task_sha256']='f'*64
            (local['area']/'result.json').write_bytes(tool.installer.canonical(result_old))
        else:
            path=local['history']['issuer_path'];empty=path.with_name('isolated-empty.sqlite3')
            with IssuerStore(empty,create=True) as store:assert store.snapshot()==[]
            empty.chmod(0o600);os.replace(empty,path)
        return result
    monkeypatch.setattr(tool,'operator_confirmation',confirm)
    assert tool.main(['--source-commit',PARENT,'--enroll-approved'])==3
    output=json.loads(capsys.readouterr().out.splitlines()[-1])
    assert output['scope_installed'] is False and output['orders_submitted'] is False,output
    assert before==(local['public'].read_bytes(),(local['cfg']/'policy.json').read_bytes())
    assert not (local['area']/'cycles').exists()


@pytest.mark.parametrize('target',['profile.json','registry','active.json'])
def test_real_process_interruption_resumes_original_seed_and_window(cold_scope_local,monkeypatch,capsys,target):
    import multiprocessing
    import os
    from tools import gold_first_scope_install as tool
    local=cold_scope_local;native=tool.successor._replace
    before=local['history']['issuer_store'].snapshot()
    def interrupt(path,body,**kw):
        native(path,body,**kw)
        if path==(local['public'] if target=='registry' else
                  local['area']/'active.json' if target=='active.json' else local['cfg']/target):
            os._exit(86)
    def child():
        with monkeypatch.context() as stopped:
            stopped.setattr(tool.successor,'_replace',interrupt)
            result=tool.main(['--source-commit',PARENT,'--enroll-approved'])
        os._exit(result)
    process=multiprocessing.get_context('fork').Process(target=child)
    process.start()
    try:
        process.join(25)
        assert not process.is_alive() and process.exitcode==86
    finally:
        if process.is_alive():process.kill();process.join(5)
    stage=tool.pending_stage();assert stage is not None
    seeds={name:(stage/name).read_bytes() for name in tool.SEEDS}
    policy=(stage/'bundle/policy.json').read_bytes()
    monkeypatch.setattr(tool,'operator_confirmation',lambda *a:pytest.fail('resume must not reconfirm or regenerate'))
    local['clock'][0]+=1000
    assert tool.main(['--source-commit',PARENT,'--enroll-approved'])==0,capsys.readouterr().out
    assert seeds=={name:(stage/name).read_bytes() for name in tool.SEEDS}
    assert policy==(local['cfg']/'policy.json').read_bytes()
    assert tool.successor.active_setup()==stage and (stage/'activation.json').exists()
    assert not (local['area']/'pending.json').exists()
    assert local['old_tasks']=={p.name:p.read_bytes() for p in (local['ctl']/'tasks').iterdir()}
    assert before==local['history']['issuer_store'].snapshot()


@pytest.mark.parametrize('name',['spec.json','result.json'])
def test_resume_rechecks_actual_original_installation_records(cold_scope_local,monkeypatch,capsys,name):
    from tools import gold_first_scope_install as tool
    local=cold_scope_local
    with monkeypatch.context() as stopped:
        stopped.setattr(tool.successor,'install_successor',lambda *a,**kw:(_ for _ in ()).throw(OSError('isolated pre-activation stop')))
        assert tool.main(['--source-commit',PARENT,'--enroll-approved'])==3
    capsys.readouterr();stage=tool.pending_stage()
    seeds={key:(stage/key).read_bytes() for key in tool.SEEDS}
    path=local['area']/name;value=json.loads(path.read_bytes())
    value['source_commit']='f'*40;path.write_bytes(tool.installer.canonical(value))
    before=local['public'].read_bytes(),{p.name:p.read_bytes() for p in local['cfg'].iterdir()}
    monkeypatch.setattr(tool,'operator_confirmation',lambda *a:pytest.fail('resume cannot confirm changed originals'))
    assert tool.main(['--source-commit',PARENT,'--enroll-approved'])==3
    output=json.loads(capsys.readouterr().out)
    assert output['scope_installed'] is None,output
    assert before==(local['public'].read_bytes(),{p.name:p.read_bytes() for p in local['cfg'].iterdir()})
    assert seeds=={key:(stage/key).read_bytes() for key in tool.SEEDS}


def test_denied_operator_precedes_source_or_protected_reads(monkeypatch, capsys):
    from tools import gold_first_scope_install as tool
    monkeypatch.setattr(tool, 'local_operator', lambda: (_ for _ in ()).throw(ValueError('isolated tty denied')))
    monkeypatch.setattr(tool.successor, 'verify_source', lambda *a: pytest.fail('source read before operator'))
    assert tool.main(['--source-commit', 'c' * 40, '--enroll-approved']) == 3
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'NOT_COMPLETED' and result['phase'] == 'PREFLIGHT'
    assert result['orders_submitted'] is False and result['services_started'] is False


def test_installation_is_never_the_default_action(monkeypatch, capsys):
    from tools import gold_first_scope_install as tool
    monkeypatch.setattr(tool, 'local_operator', lambda: pytest.fail('implicit installation operator gate'))
    assert tool.main(['--source-commit', 'c' * 40]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'NOT_COMPLETED' and result['scope_installed'] is False


def test_existing_controller_lease_blocks_first_scope(monkeypatch, tmp_path, capsys):
    from tools import gold_first_scope_install as tool
    from tools import demo_supervisor
    root = tmp_path / 'controller'
    root.mkdir(mode=0o700)
    monkeypatch.setattr(tool.installer, 'CONTROLLER', root)
    monkeypatch.setattr(demo_supervisor, 'STATE', root)
    monkeypatch.setattr(demo_supervisor, '_root_private_directory', lambda p: None)
    monkeypatch.setattr(tool, 'local_operator', lambda: 'isolated-operator')
    monkeypatch.setattr(tool.successor, 'verify_source', lambda source: None)
    monkeypatch.setattr(tool.successor, 'active_setup', lambda: pytest.fail('protected read during shared work'))
    with demo_supervisor.controller_lease():
        assert tool.main(['--source-commit', 'c' * 40, '--enroll-approved']) == 3
    assert json.loads(capsys.readouterr().out)['scope_installed'] is False


@pytest.mark.parametrize('damage', ['orphan', 'missing_intent', 'malformed_pending'])
def test_durable_partial_discovery_never_reports_verified_absence(monkeypatch, tmp_path, capsys, damage):
    from tools import gold_first_scope_install as tool
    from tools import demo_supervisor
    area, controller = tmp_path / 'setup', tmp_path / 'controller'
    area.mkdir(mode=0o700); controller.mkdir(mode=0o700)
    (controller / 'functional-cycle').mkdir(mode=0o700)
    # This test stops at stage discovery, before old materials can be consumed.
    (area / 'result.json').write_text('{}')
    cycles = area / 'cycles'; cycles.mkdir(mode=0o700)
    stage = cycles / ('a' * 64); stage.mkdir(mode=0o700)
    if damage == 'missing_intent':
        (stage / 'spec.json').write_text(json.dumps({'schema': tool.SPEC_SCHEMA}))
    elif damage == 'malformed_pending':
        (area / 'pending.json').write_text('{}')
    monkeypatch.setattr(tool.successor, 'SETUP', area)
    monkeypatch.setattr(tool.installer, 'CONTROLLER', controller)
    monkeypatch.setattr(demo_supervisor, 'STATE', controller)
    monkeypatch.setattr(demo_supervisor, '_root_private_directory', lambda path: None)
    monkeypatch.setattr(tool.successor, '_read_root_owned_json', lambda path: path.read_bytes())
    monkeypatch.setattr(tool, 'local_operator', lambda: 'isolated-operator')
    monkeypatch.setattr(tool.successor, 'verify_source', lambda source: None)
    monkeypatch.setattr(tool, 'create_stage', lambda **kwargs: pytest.fail('partial attempt must not re-enroll'))
    before = {p.relative_to(area): p.read_bytes() for p in area.rglob('*') if p.is_file()}
    assert tool.main(['--source-commit', 'c' * 40, '--enroll-approved']) == 3
    result = json.loads(capsys.readouterr().out)
    assert result['scope_installed'] is None, result
    assert before == {p.relative_to(area): p.read_bytes() for p in area.rglob('*') if p.is_file()}
