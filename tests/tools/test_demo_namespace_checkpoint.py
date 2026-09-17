"""Encrypted isolated restore preserves original Demo state, including pending work."""
from pathlib import Path
import json
import sqlite3

import pytest

from tools import demo_namespace_checkpoint as checkpoint

SECRET='isolated-checkpoint-test-passphrase'
COMMIT='c'*40


@pytest.fixture(autouse=True)
def private_fixture_files():
    import os
    previous=os.umask(0o077)
    try:yield
    finally:os.umask(previous)


def executor(tmp_path,monkeypatch):
    from tests.live.test_demo_pre_submit import setup,stores,signed
    from live.execution import demo_pre_submit
    from live.execution.order_event_store import SqliteOrderEventStore
    from services.research.demo_user_stream import UserStreamJournal
    private,envelope,values=setup(tmp_path,monkeypatch);opened=stores(tmp_path)
    dispatch,recovery,oms=opened
    proposal=demo_pre_submit.proposal(**values,dispatch=dispatch,recovery=recovery,oms=oms)
    demo_pre_submit.prepare(signed(private,envelope,proposal),**values,dispatch=dispatch,recovery=recovery,oms=oms,
        evidence_directory=tmp_path/'evidence')
    for store in opened:store.close()
    root=tmp_path/'source';root.mkdir()
    for name in ('dispatch','recovery','oms'):(tmp_path/(name+'.db')).rename(root/(name+'.sqlite3'))
    (tmp_path/'kill-switch.sqlite3').rename(root/'kill-switch.sqlite3')
    (tmp_path/'evidence').rename(root/'evidence')
    store=SqliteOrderEventStore(str(root/'order-events.sqlite3'),create=True);store.close()
    journal=UserStreamJournal(root/'user-stream.sqlite3',account_ref_sha256=values['origin']['account_ref_sha256'])
    journal.record('SESSION_START',at_ms=values['now_ms'],source_sha256='a'*64)
    journal.__exit__()
    return root,values['origin']['account_ref_sha256']


def test_encrypted_roundtrip_preserves_pending_orders_and_original_hashes(tmp_path,monkeypatch):
    root,account=executor(tmp_path,monkeypatch)
    (root/'evidence'/'empty').mkdir()
    original_stop=(root/'kill-switch.sqlite3').read_bytes()
    archive=tmp_path/'checkpoint.enc'
    result=checkpoint.create_checkpoint(root,archive,namespace='executor',source_commit=COMMIT,passphrase=SECRET)
    assert result['encrypted'] and result['namespace']=='executor' and not result['production_restore_performed']
    assert not archive.read_bytes().startswith(b'PK') and not result['cross_namespace_atomic']
    restored=tmp_path/'restored'
    report=checkpoint.restore_checkpoint(archive,restored,passphrase=SECRET,expected_sha256=result['sha256'])
    assert report['state']['account_ref_sha256']==account
    assert report['state']['reservations']==report['state']['pending_recoveries']==report['state']['orders']==1
    assert report['state']['fills']==0 and report['state']['kill_history_verified']
    assert report['files']==result['files'] and (root/'kill-switch.sqlite3').read_bytes()==original_stop
    assert report['state']['kill_record_sha256']==result['state']['kill_record_sha256']
    assert (restored/'evidence/empty').is_dir()
    assert not report['submission_enabled'] and not report['economic_authority']
    with pytest.raises(ValueError):checkpoint.restore_checkpoint(archive,restored,passphrase=SECRET,expected_sha256=result['sha256'])


@pytest.mark.parametrize('directory',['session-results','cancellation-session-results'])
def test_execution_session_results_survive_encrypted_restore(tmp_path,monkeypatch,directory):
    root,_=executor(tmp_path,monkeypatch)
    result=root/directory;result.mkdir()
    retained=b'{"status":"RECOVERY_REQUIRED","orders_submitted":false}\n'
    (result/'original.json').write_bytes(retained)
    archive=tmp_path/'checkpoint.enc'
    captured=checkpoint.create_checkpoint(root,archive,namespace='executor',source_commit=COMMIT,passphrase=SECRET)
    restored=tmp_path/'restored'
    report=checkpoint.restore_checkpoint(archive,restored,passphrase=SECRET,expected_sha256=captured['sha256'])
    assert (restored/directory/'original.json').read_bytes()==retained
    assert (result/'original.json').read_bytes()==retained
    assert report['state']['reservations']==report['state']['pending_recoveries']==1
    assert report['state']['fills']==0
    assert not report['production_restore_performed'] and not report['submission_enabled']


@pytest.mark.parametrize('failure',['password','digest','tamper'])
def test_restore_failure_never_publishes_a_partial_destination(tmp_path,monkeypatch,failure):
    root,_=executor(tmp_path,monkeypatch);archive=tmp_path/'checkpoint.enc'
    result=checkpoint.create_checkpoint(root,archive,namespace='executor',source_commit=COMMIT,passphrase=SECRET)
    if failure=='tamper':
        raw=bytearray(archive.read_bytes());raw[-1]^=1;archive.write_bytes(raw)
        result['sha256']=checkpoint.sha(archive)
    with pytest.raises(Exception):
        checkpoint.restore_checkpoint(archive,tmp_path/'restored',passphrase='wrong-password-long-enough' if failure=='password' else SECRET,
            expected_sha256='f'*64 if failure=='digest' else result['sha256'])
    assert not (tmp_path/'restored').exists()


@pytest.mark.parametrize('failure',['missing','symlink','hardlink','extra','bad_chain','unreadable_database'])
def test_damaged_or_out_of_scope_state_cannot_be_published_as_recoverable(tmp_path,monkeypatch,failure):
    root,_=executor(tmp_path,monkeypatch)
    if failure=='missing':(root/'dispatch.sqlite3').unlink()
    elif failure=='symlink':(root/'leak.json').symlink_to(tmp_path/'outside')
    elif failure=='hardlink':
        import os
        os.link(root/'dispatch.sqlite3',tmp_path/'linked.db')
    elif failure=='extra':(root/'signing-key.json').write_text('{"private":"not included"}')
    elif failure=='bad_chain':
        with sqlite3.connect(root/'user-stream.sqlite3') as db:
            db.execute('DROP TRIGGER stream_records_no_update')
    else:(root/'oms.sqlite3').write_bytes(b'not a database')
    archive=tmp_path/'checkpoint.enc'
    with pytest.raises(Exception):checkpoint.create_checkpoint(root,archive,namespace='executor',source_commit=COMMIT,passphrase=SECRET)
    assert not archive.exists()


def test_latest_pointer_or_record_inventory_changing_during_capture_is_rejected(tmp_path,monkeypatch):
    root,_=executor(tmp_path,monkeypatch);original=checkpoint.snapshot_file;changed=False
    def snapshot(*args,**kwargs):
        nonlocal changed
        result=original(*args,**kwargs)
        if not changed:
            changed=True;(root/'evidence'/'new.json').write_text('{"status":"later"}')
        return result
    monkeypatch.setattr(checkpoint,'snapshot_file',snapshot)
    with pytest.raises(ValueError,match='CHECKPOINT_SOURCE_CHANGED'):
        checkpoint.create_checkpoint(root,tmp_path/'checkpoint.enc',namespace='executor',source_commit=COMMIT,passphrase=SECRET)


def test_missing_namespaces_are_unknown_and_do_not_create_empty_state(tmp_path):
    for namespace in ('issuer','controller','research'):
        root=tmp_path/namespace
        with pytest.raises(ValueError):checkpoint.create_checkpoint(root,tmp_path/(namespace+'.enc'),namespace=namespace,source_commit=COMMIT,passphrase=SECRET)
        assert not root.exists()


def test_resident_recovery_empty_process_lock_does_not_block_checkpoint(tmp_path,monkeypatch):
    import zipfile
    root,_=executor(tmp_path,monkeypatch)
    resident=root/'resident-recovery';resident.mkdir()
    (resident/'cycle.lock').touch()
    (resident/'original.json').write_text('{"pending":true}')
    archive=tmp_path/'capture.zip'
    report=checkpoint.create_checkpoint(root,archive,namespace='executor',source_commit=COMMIT,passphrase=None,capture_only=True)
    with zipfile.ZipFile(archive) as saved:
        assert 'resident-recovery/cycle.lock' not in saved.namelist()
        assert saved.read('resident-recovery/original.json')==b'{"pending":true}'
    assert report['state']['pending_recoveries']==1
    assert (resident/'cycle.lock').is_file()


@pytest.mark.parametrize('lock',['observation.lock','resident-recovery/cycle.lock'])
def test_nonempty_runtime_lock_cannot_be_silently_omitted(tmp_path,monkeypatch,lock):
    root,_=executor(tmp_path,monkeypatch)
    path=root/lock;path.parent.mkdir(exist_ok=True);path.write_bytes(b'preserve this record')
    with pytest.raises(ValueError,match='CHECKPOINT_NONEMPTY_RUNTIME_LOCK'):
        checkpoint.create_checkpoint(root,tmp_path/'capture.zip',namespace='executor',source_commit=COMMIT,passphrase=None,capture_only=True)


def test_cli_exposes_fixed_checkpoint_reason_without_raw_external_errors(tmp_path,monkeypatch,capsys):
    command=['capture','--namespace','executor','--source-dir',str(tmp_path/'missing'),
        '--output',str(tmp_path/'capture.zip'),'--source-commit',COMMIT]
    assert checkpoint.main(command)==3
    assert json.loads(capsys.readouterr().out)['reason_code']=='CHECKPOINT_SOURCE_MISSING'
    def error(*args,**kwargs):raise ValueError('private external exception detail')
    monkeypatch.setattr(checkpoint,'create_checkpoint',error)
    assert checkpoint.main(command)==3
    output=capsys.readouterr().out
    assert json.loads(output)['reason_code']=='CHECKPOINT_UNCLASSIFIED' and 'private' not in output


def test_order_database_wal_change_during_capture_is_not_an_atomic_order_snapshot(tmp_path,monkeypatch):
    root,_=executor(tmp_path,monkeypatch);original=checkpoint.snapshot_file;changed=False
    with sqlite3.connect(root/'oms.sqlite3') as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        def snapshot(*args,**kwargs):
            nonlocal changed
            result=original(*args,**kwargs)
            if not changed:
                changed=True;writer.execute('CREATE TABLE isolated_concurrent_write(value INTEGER)');writer.commit()
            return result
        monkeypatch.setattr(checkpoint,'snapshot_file',snapshot)
        with pytest.raises(ValueError,match='CHECKPOINT_SOURCE_CHANGED'):
            checkpoint.create_checkpoint(root,tmp_path/'checkpoint.enc',namespace='executor',source_commit=COMMIT,passphrase=SECRET)
    assert not (tmp_path/'checkpoint.enc').exists()


def test_controller_claim_identity_and_task_bytes_survive_restore(tmp_path):
    import hashlib
    root=tmp_path/'controller';root.mkdir()
    for name in ('tasks','requests','claims','results','task-claims','task-results'):(root/name).mkdir()
    task=b'{"schema":"DEMO_REQUEST_TASK_V1","marker":"isolated"}';identity=hashlib.sha256(task).hexdigest()
    (root/'tasks'/(identity+'.json')).write_bytes(task)
    (root/'task-claims'/(identity+'.json')).write_text(json.dumps({'task_sha256':identity,'schema':'DEMO_TASK_CLAIM_V1'}))
    result=checkpoint.create_checkpoint(root,tmp_path/'controller.enc',namespace='controller',source_commit=COMMIT,passphrase=SECRET)
    restored=tmp_path/'restore-controller'
    checked=checkpoint.restore_checkpoint(tmp_path/'controller.enc',restored,passphrase=SECRET,expected_sha256=result['sha256'])
    assert (restored/'tasks'/(identity+'.json')).read_bytes()==task and checked['state']['task_claims']==1


def test_optional_functional_claim_survives_controller_restore(tmp_path):
    root=tmp_path/'controller';root.mkdir()
    for name in ('tasks','requests','claims','results','task-claims','task-results'):(root/name).mkdir()
    area=root/'functional-cycle';area.mkdir()
    claim=b'{"cycle_sha256":"isolated-original-cycle"}'
    (area/'claim.json').write_bytes(claim)
    from tools.demo_operator_setup import canonical,digest
    portfolio=root/'portfolio-decisions';portfolio.mkdir()
    decision={'schema':'DEMO_PORTFOLIO_DECISION_CHECKPOINT_V1','identity':'a'*64,
        'allocation_sha256':'b'*64,'forecast_sha256':'c'*64,'build_sha256':'d'*64,
        'task':None,'observed_at_ms':1}
    decision['record_sha256']=digest(decision);decision_bytes=canonical(decision)
    (portfolio/('a'*64+'.json')).write_bytes(decision_bytes)
    result=checkpoint.create_checkpoint(root,tmp_path/'controller.enc',namespace='controller',source_commit=COMMIT,passphrase=SECRET)
    restored=tmp_path/'restored'
    checkpoint.restore_checkpoint(tmp_path/'controller.enc',restored,passphrase=SECRET,expected_sha256=result['sha256'])
    assert (restored/'functional-cycle/claim.json').read_bytes()==claim
    assert (restored/'portfolio-decisions'/('a'*64+'.json')).read_bytes()==decision_bytes


def test_research_scope_excludes_other_research_and_preserves_demo_records(tmp_path):
    from services.research.demo_user_stream import UserStreamJournal
    root=tmp_path/'research';root.mkdir();(root/'unrelated.json').write_text('{"do_not_archive":true}')
    with UserStreamJournal(root/'binance_demo_user_stream.db',account_ref_sha256='a'*64):pass
    directory=root/'binance_demo_cash_windows';directory.mkdir();(directory/'original.json').write_text('{"failed":true}')
    result=checkpoint.create_checkpoint(root,tmp_path/'research.enc',namespace='research',source_commit=COMMIT,passphrase=SECRET)
    restored=tmp_path/'restored'
    report=checkpoint.restore_checkpoint(tmp_path/'research.enc',restored,passphrase=SECRET,expected_sha256=result['sha256'])
    assert not (restored/'unrelated.json').exists() and (restored/'binance_demo_cash_windows/original.json').read_text()=='{"failed":true}'
    assert report['state']['stream_records']==0 and report['state']['stream_session_present'] is False


@pytest.mark.parametrize('change',[None,'source','path','directory','clock','extra'])
def test_authenticated_but_invalid_manifest_is_rejected(tmp_path,change):
    import zipfile
    from tools.live_backup import encrypt_file
    manifest={'schema':checkpoint.SCHEMA,'namespace':'controller','source_commit':COMMIT,'source_uid':1000,
        'captured_from_ms':100,'captured_until_ms':101,'entries':{},'directories':sorted(checkpoint.CONTROLLER_DIRECTORIES),
        'state':{'namespace':'controller','tasks':0,'requests':0,'claims':0,'task_claims':0}}
    body=b'{}';name='installation.json'
    if change=='source':manifest['source_commit']='not-a-source'
    elif change=='path':name='../escape.json'
    elif change=='directory':manifest['directories'].append('../../outside')
    elif change=='clock':manifest['captured_until_ms']=99
    elif change=='extra':manifest['approved']=True
    import hashlib
    manifest['entries'][name]={'sha256':hashlib.sha256(body).hexdigest(),'size':len(body),'snapshot_method':'STABLE_FILE_READ'}
    archive=tmp_path/'plain.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr(name,body);z.writestr('checkpoint-manifest.json',json.dumps(manifest))
    encrypted=tmp_path/'encrypted';encrypt_file(str(archive),str(encrypted),SECRET)
    if change:
        with pytest.raises(ValueError):checkpoint.restore_checkpoint(encrypted,tmp_path/'restored',passphrase=SECRET,expected_sha256=checkpoint.sha(encrypted))
        assert not (tmp_path/'restored').exists() and not (tmp_path/'escape.json').exists()
    else:assert checkpoint.restore_checkpoint(encrypted,tmp_path/'restored',passphrase=SECRET,
        expected_sha256=checkpoint.sha(encrypted))['state']==manifest['state']


def test_cli_never_echoes_secret_failures_or_creates_missing_namespaces(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv('QUANTOS_BACKUP_PASSPHRASE','sensitive-short')
    monkeypatch.delenv('QUANTOS_BACKUP_PASSPHRASE_FILE',raising=False)
    monkeypatch.delenv('CREDENTIALS_DIRECTORY',raising=False)
    assert checkpoint.main(['create','--namespace','issuer','--source-dir',str(tmp_path/'missing'),
        '--output',str(tmp_path/'archive.enc'),'--source-commit',COMMIT])==3
    assert 'sensitive' not in capsys.readouterr().out and not (tmp_path/'archive.enc').exists()


def test_capture_needs_no_secret_and_separate_sealing_verifies_exact_capture(tmp_path,monkeypatch,capsys):
    root,_=executor(tmp_path,monkeypatch);plain=tmp_path/'private-capture.zip'
    resolver=checkpoint.resolve_backup_passphrase
    monkeypatch.setattr(checkpoint,'resolve_backup_passphrase',lambda **kw:pytest.fail('capture read backup key'))
    assert checkpoint.main(['capture','--namespace','executor','--source-dir',str(root),
        '--source-commit',COMMIT,'--output',str(plain)])==0
    captured=json.loads(capsys.readouterr().out)
    assert captured['status']=='CAPTURED_PRIVATE_NAMESPACE_ARCHIVE' and captured['encrypted'] is False
    assert plain.stat().st_mode&0o777==0o600
    monkeypatch.setattr(checkpoint,'resolve_backup_passphrase',resolver)
    sealed=checkpoint.seal_checkpoint(plain,tmp_path/'sealed.enc',passphrase=SECRET,expected_sha256=captured['sha256'])
    assert sealed['encrypted'] and sealed['state']['pending_recoveries']==1
    assert plain.read_bytes().startswith(b'PK') and checkpoint.sha(plain)==captured['sha256']
    with pytest.raises(ValueError):checkpoint.seal_checkpoint(plain,tmp_path/'other.enc',passphrase=SECRET,expected_sha256='f'*64)
    assert not (tmp_path/'other.enc').exists()


def test_issuer_empty_append_log_and_directory_layout_restore_without_reset(tmp_path):
    from live.execution.demo_issuer_store import IssuerStore
    root=tmp_path/'issuer';root.mkdir();(root/'results').mkdir();(root/'views').mkdir()
    store=IssuerStore(root/'issuance.sqlite3',create=True);store.close()
    report=checkpoint.create_checkpoint(root,tmp_path/'issuer.enc',namespace='issuer',source_commit=COMMIT,passphrase=SECRET)
    restored=tmp_path/'restored'
    checked=checkpoint.restore_checkpoint(tmp_path/'issuer.enc',restored,passphrase=SECRET,expected_sha256=report['sha256'])
    assert checked['state']['issuer_records']==0 and (restored/'results').is_dir() and (restored/'views').is_dir()


def research_observations(tmp_path):
    from services.research.demo_user_stream import UserStreamJournal
    root=tmp_path/'research';root.mkdir()
    with UserStreamJournal(root/'binance_demo_user_stream.db',account_ref_sha256='a'*64):pass
    directory=root/'binance_demo_market_inputs/BTCUSDC';directory.mkdir(parents=True)
    body=b'{"observation":1}';digest=checkpoint.hashlib.sha256(body).hexdigest()
    (directory/(digest+'.json')).write_bytes(body)
    (directory/'latest.json').write_text(json.dumps({'sha256':digest}))
    return root,directory,digest


def test_research_prefix_restores_frozen_pointer_while_new_observations_arrive(tmp_path,monkeypatch):
    root,directory,first=research_observations(tmp_path)
    original=checkpoint.snapshot_file;updated=False
    second_body=b'{"observation":2}';second=checkpoint.hashlib.sha256(second_body).hexdigest()
    def capture(*args,**kwargs):
        nonlocal updated
        result=original(*args,**kwargs)
        if not updated:
            updated=True;(directory/(second+'.json')).write_bytes(second_body)
            (directory/'latest.json').write_text(json.dumps({'sha256':second}))
        return result
    monkeypatch.setattr(checkpoint,'snapshot_file',capture)
    archive=tmp_path/'research.enc'
    result=checkpoint.create_checkpoint(root,archive,namespace='research',source_commit=COMMIT,passphrase=SECRET,observation_prefix=True)
    restored=tmp_path/'restored'
    report=checkpoint.restore_checkpoint(archive,restored,passphrase=SECRET,expected_sha256=result['sha256'])
    saved=restored/'binance_demo_market_inputs/BTCUSDC'
    assert json.loads((saved/'latest.json').read_text())=={'sha256':first}
    assert (saved/(first+'.json')).read_bytes()==b'{"observation":1}'
    assert not (saved/(second+'.json')).exists() and (directory/(second+'.json')).exists()
    assert report['consistency_model']=='RESEARCH_FROZEN_FILE_INVENTORY'
    assert not report['cross_namespace_atomic'] and not report['source_verified']


@pytest.mark.parametrize('change',['history','dangling_pointer'])
def test_research_prefix_rejects_mutated_history_or_unincluded_pointer_target(tmp_path,monkeypatch,change):
    root,directory,digest=research_observations(tmp_path)
    if change=='dangling_pointer':(directory/'latest.json').write_text(json.dumps({'sha256':'f'*64}))
    else:
        original=checkpoint.snapshot_file;updated=False
        def capture(*args,**kwargs):
            nonlocal updated
            result=original(*args,**kwargs)
            if not updated:updated=True;(directory/(digest+'.json')).write_text('{"rewritten":true}')
            return result
        monkeypatch.setattr(checkpoint,'snapshot_file',capture)
    with pytest.raises(ValueError):
        checkpoint.create_checkpoint(root,tmp_path/'capture.zip',namespace='research',source_commit=COMMIT,
            passphrase=None,capture_only=True,observation_prefix=True)
    assert not (tmp_path/'capture.zip').exists()


def test_research_prefix_mode_cannot_relax_executor_or_controller_snapshot(tmp_path):
    with pytest.raises(ValueError,match='CHECKPOINT_OBSERVATION_PREFIX_SCOPE'):
        checkpoint.create_checkpoint(tmp_path,tmp_path/'other.zip',namespace='executor',source_commit=COMMIT,
            passphrase=None,capture_only=True,observation_prefix=True)
