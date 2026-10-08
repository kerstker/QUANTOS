"""Native research originals; only release identity and external inputs are isolated.

This component test supplies a layout, not a deployment/writer-barrier proof.
The original producers, registries, TrialLedgers, SQLite/WAL and validators run.
"""
import copy
import hashlib
import importlib
import os
from pathlib import Path
import shutil

import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case, execute, join
from tests.live.test_gold_feedback_recovery_fixture import completed_bar_feedback as completed_bar_feedback
from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence
from tools import gold_bar_experiment as gold

_TEST_OWNER = (os.geteuid(), os.getegid())


def module():
    return importlib.import_module('tools.demo_gold_research_checkpoint')


def component(name, kind, path, registry_path=None):
    return dict(name=name, kind=kind, path=path, registry_path=registry_path)


def layout(*items):
    return dict(schema='GOLD_RESEARCH_CHECKPOINT_LAYOUT_V1', source_commit='f'*40,
                uid=os.geteuid(), gid=os.getegid(), components=list(items))


def project_layout():
    return layout(component('registered', 'PROJECT', 'project', 'registry.db'))


def originals(root):
    return {str(p.relative_to(root)): (p.stat().st_ino, p.stat().st_mode,
            p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob('*') if p.is_file()}


def private(root):
    root.chmod(0o700)
    for path in root.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)


def test_native_success_failure_separate_ledgers_and_wal_are_preserved(registered_case, monkeypatch):
    f = registered_case
    assert f['store']._conn.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    f['store']._conn.execute('PRAGMA wal_autocheckpoint=0')
    plans = [f['prepare']('passed'), f['prepare']('failed')]
    execute(f, 'passed')
    original = gold._execute
    def failed(arguments):
        changed = copy.deepcopy(arguments)
        changed['bar_receipt']['response_sha256'] = '0'*64
        return original(changed)
    with monkeypatch.context() as patch:
        patch.setattr(gold, '_execute', failed)
        with pytest.raises(ValueError):
            execute(f, 'failed')
    assert Path(str(f['root']/'registry.db')+'-wal').stat().st_size > 0
    before = originals(f['root'])
    value = module().verify(f['root'], layout=project_layout())
    native = value['components']['registered']['native']
    assert [row['status'] for row in native['attempts']].count('PASSED') == 3
    assert [row['status'] for row in native['attempts']].count('FAILED') == 3
    assert native['attempt_count'] == 6 and native['all_runs_finalized'] is True
    for plan in plans:
        events = native['runs'][plan['invocation_id']]['ledger']['history']
        assert sorted(e['global_seq'] for rows in events.values() for e in rows) == list(range(1, 10))
    assert originals(f['root']) == before
    assert value['writer_barrier_verified'] is value['economic_authority'] is False


def test_real_interruption_remains_unfinished_without_replaying_producer(registered_case):
    f = registered_case
    plan = f['prepare']()
    pid = os.fork()
    if pid == 0:
        original = f['run']._put
        def put(path, *a, **kw):
            if path.name == 'manifest.json':
                os._exit(77)
            return original(path, *a, **kw)
        f['run']._put = put
        try:
            execute(f)
        except BaseException:
            os._exit(98)
        os._exit(99)
    assert join(pid) == 77
    before = originals(f['root'])
    value = module().verify(f['root'], layout=project_layout())
    native = value['components']['registered']['native']
    assert all(row['status'] == 'PASSED' for row in native['attempts'])
    assert native['unfinished_runs'] == [plan['invocation_id']]
    assert native['all_runs_finalized'] is False
    assert value['components']['registered']['partial']
    assert originals(f['root']) == before


def test_factory_campaign_and_raw_tapes_survive_without_suffix_filter(tmp_path):
    from services.research.ai_factory_store import FactoryStore
    root = tmp_path/'state'
    base = root/'factory'
    with FactoryStore(base/'factory.db') as store:
        store.create_campaign(dict(campaign_id=1, objective='BTC', mode='baseline', budget=3,
            max_cycles=2, interval_hours=1, source_ids=['aqr']), operator='isolated', now=1)
        store.reserve_campaign(1, now=1)
    tape = base/'messages.jsonl'
    tape.write_bytes(b'{"observed":1}\n{"observed":2}\n')
    private(root)
    before = originals(root)
    value = module().verify(root, layout=layout(component('factory', 'FACTORY', 'factory')))
    assert value['files']['factory/messages.jsonl']['sha256'] == hashlib.sha256(tape.read_bytes()).hexdigest()
    assert value['components']['factory']['native']['tables']['campaign_rounds']['count'] == 1
    assert originals(root) == before


def test_native_project_restores_at_another_path_without_original_trees(registered_case, tmp_path):
    f = registered_case
    f['prepare'](); execute(f)
    before = module().verify(f['root'], layout=project_layout())
    target = tmp_path.parent/(tmp_path.name+'-restored')
    shutil.copytree(f['root'], target)
    shutil.rmtree(f['root']/'project')
    value = module().verify(target, layout=project_layout(),isolated_restore_owner=_TEST_OWNER,original_root=f['root'])
    assert value['semantic_sha256'] == before['semantic_sha256']
    assert value['files'] == before['files']


def test_original_unregistered_gold_success_failure_and_tape(registered_case, monkeypatch):
    f = registered_case
    area = f['root']/'gold'
    area.mkdir(mode=0o700)
    gold.archive(f['arguments'], area/'passed')
    original = gold._execute
    def fail(arguments):
        changed = copy.deepcopy(arguments); changed['bar_receipt']['response_sha256'] = '0'*64
        return original(changed)
    with monkeypatch.context() as patch:
        patch.setattr(gold, '_execute', fail)
        with pytest.raises(ValueError):
            gold.archive(f['arguments'], area/'failed')
    (area/'messages.jsonl').write_bytes(b'{"original_uninterpreted_tape":1}\n')
    private(area)
    before = originals(area)
    value = module().verify(f['root'], layout=layout(component('gold', 'GOLD_RESEARCH', 'gold')))
    archives = value['components']['gold']['native']['archives']
    assert archives['passed']['terminal'] == 'PASSED'
    assert archives['failed']['terminal'] == 'FAILED'
    assert originals(area) == before
    assert 'gold/messages.jsonl' in value['files']


def test_full_job_and_audit_rows_are_native_and_not_default_first_200(tmp_path):
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.contract import ResearchRun
    from services.monitoring.audit_store import DurableAuditLog
    jobs = ResearchRunStore(str(tmp_path/'jobs.db'))
    for index in range(201):
        jobs.upsert(ResearchRun(run_id=str(index),kind='gold_bar_research',title='native',
            status='error' if index == 200 else 'done', created_ts=index, finished_ts=index,
            params={'attempt':index},metrics={},series={},verdict='UNKNOWN',provenance='original',caveats=[]))
    jobs._c.close()
    audit = DurableAuditLog(str(tmp_path/'audit.db'))
    audit.append(event_id='one',actor='AUTOMATED_TEST',action='RESEARCH',timestamp=1)
    audit.close(); private(tmp_path)
    before = originals(tmp_path)
    value = module().verify(tmp_path,layout=layout(component('jobs','RUN_REGISTRY','jobs.db'),
        component('audit','AUDIT','audit.db')))
    assert value['components']['jobs']['native']['status_counts'] == {'done':200,'error':1}
    assert value['components']['audit']['native']['length'] == 1
    assert originals(tmp_path) == before


def test_original_ledger_and_real_bar_cash_feedback_are_verified(completed_bar_feedback, tmp_path, monkeypatch):
    from tools import demo_feedback_publish as publisher
    f = completed_bar_feedback
    root = tmp_path/'research-checkpoint'
    area = root/'feedback'; area.mkdir(parents=True,mode=0o700)
    path = area/(f.artifact['result_hash']+'.json')
    path.write_bytes(f.artifact_bytes)
    publisher.validate_bar_export(f.artifact,source_commit=f.source_commit,
        feedback_sha256=f.artifact['result_hash'],now=f.clock[0],cycle=f.artifact['bundle_sha256'])
    private(root)
    # The execution fixture virtualizes executor/root identity. The research
    # reader uses the actual temporary files' identity, not that simulated UID.
    with monkeypatch.context() as patch:
        patch.setattr(os,'geteuid',lambda:_TEST_OWNER[0])
        patch.setattr(os,'getegid',lambda:_TEST_OWNER[1])
        declared=layout(component('feedback','FEEDBACK','feedback'));declared['source_commit']=f.source_commit
        value = module().verify(root,layout=declared)
    native = value['components']['feedback']['native']
    assert native['artifacts'][path.name]['result_hash'] == f.artifact['result_hash']
    assert native['artifacts'][path.name]['publication_contract_verified'] is True
    assert value['whole_set_verified'] is False


def test_actual_trial_ledger_preserves_original_global_sequence(registered_case):
    f=registered_case;plan=f['prepare']();execute(f)
    path=f['directories']['one']/'archive/trials.db'
    value=module().verify(f['root'],layout=layout(component('trials','TRIAL_LEDGER',str(path.relative_to(f['root'])))))
    history=value['components']['trials']['native']['history']
    assert set(history)=={row['native_trial_id'] for row in plan['trials']}
    assert sorted(row['global_seq'] for rows in history.values() for row in rows)==list(range(1,10))


@pytest.mark.parametrize('damage',['missing_ledger','receipt','wal_without_db','rollback_journal'])
def test_native_missing_or_changed_originals_fail(registered_case,damage):
    f=registered_case;f['prepare']();execute(f)
    area=f['directories']['one']
    if damage=='missing_ledger':
        (area/'archive/trials.db').unlink()
    elif damage=='receipt':
        import json
        path=area/'registration.json';value=json.loads(path.read_bytes());value['provenance']=[]
        path.write_bytes(f['run'].canonical(value))
    elif damage=='wal_without_db':
        (area/'archive/trials.db').unlink();(area/'archive/trials.db-wal').write_bytes(b'original-wal')
    else:
        (f['root']/'registry.db-journal').write_bytes(b'partial')
    with pytest.raises((ValueError,FileNotFoundError)):
        module().verify(f['root'],layout=project_layout())


@pytest.mark.parametrize('boundary',['files','bytes','rows'])
def test_bounds_reject_before_native_validation(registered_case,monkeypatch,boundary):
    f=registered_case;f['prepare']();execute(f)
    tool=module()
    monkeypatch.setattr(tool,{'files':'MAX_FILES','bytes':'MAX_BYTES','rows':'MAX_ROWS'}[boundary],1)
    monkeypatch.setattr(tool,'_native',lambda *a:pytest.fail('native verifier before bounds'))
    with pytest.raises(ValueError):tool.verify(f['root'],layout=project_layout())


@pytest.mark.parametrize('damage',['symlink','hardlink','fifo','changed_after_copy','extra_after_copy'])
def test_descriptor_and_membership_guards(registered_case,monkeypatch,damage):
    f=registered_case;f['prepare']();execute(f)
    path=f['directories']['one']/'input.json'
    if damage=='symlink':
        raw=path.read_bytes();path.unlink();target=f['root']/'detached';target.write_bytes(raw);path.symlink_to(target)
    elif damage=='hardlink':
        os.link(path,f['root']/'linked')
    elif damage=='fifo':
        path.unlink();os.mkfifo(path)
    else:
        tool=module();original=tool._native
        def native(*args,**kwargs):
            result=original(*args,**kwargs)
            if damage=='extra_after_copy':(path.parent/'unknown.json').write_bytes(b'{}')
            else:
                raw=path.read_bytes();path.unlink();path.write_bytes(raw)
            return result
        monkeypatch.setattr(tool,'_native',native)
    with pytest.raises(ValueError):module().verify(f['root'],layout=project_layout())


def test_restore_owner_does_not_rewrite_installed_identity(registered_case):
    f=registered_case;f['prepare']();execute(f)
    original=project_layout();original['uid']+=1000;original['gid']+=1000
    with pytest.raises(ValueError,match='PROCESS_OWNER'):module().verify(f['root'],layout=original)
    value=module().verify(f['root'],layout=original,isolated_restore_owner=(os.geteuid(),os.getegid()))
    assert value['custody_mode']=='ISOLATED_RESTORE' and value['transport_verified'] is False
    assert original['uid']==os.geteuid()+1000


def test_two_projects_share_all_original_registry_history_once(registered_case):
    f=registered_case;first=f['prepare']('one');execute(f)
    second=f['run'].prepare_gold(f['arguments'],project_root=f['root']/'second',search_id='two',
        producer_id='isolated-native',actor_id='AUTOMATED_TEST',dataset_version='native-xau',
        registered_at_ms=f['clock'][0])
    f['run'].run_gold(f['arguments'],destination=f['run'].run_directory(f['root']/'second',second),
        registry=f['registry'],clock_ms=lambda:f['clock'][0])
    value=module().verify(f['root'],layout=layout(component('one','PROJECT','project','registry.db'),
        component('two','PROJECT','second','registry.db')))
    group=value['registries']['registry.db']
    assert group['population_coverage']=='COMPLETE'
    assert set(group['runs'])=={first['invocation_id'],second['invocation_id']}
    assert len(group['registry_population']['search_candidates'])==6
    assert len([p for p in value['files'] if p=='registry.db'])==1
    assert value['bytes']==sum(row['size'] for row in value['files'].values())


@pytest.mark.parametrize('missing',['input.json','running.json'])
def test_completed_gold_cannot_disappear_when_one_original_is_deleted(registered_case,missing):
    f=registered_case;area=f['root']/'gold';area.mkdir(mode=0o700)
    gold.archive(f['arguments'],area/'one');private(area)
    (area/'one'/missing).unlink()
    with pytest.raises((ValueError,FileNotFoundError)):
        module().verify(f['root'],layout=layout(component('gold','GOLD_RESEARCH','gold')))


@pytest.mark.parametrize('field,value',[('uid',False),('gid',True),('source_commit','current'),('extra',False)])
def test_layout_has_no_numeric_alias_or_unknown_fields(field,value):
    proposed=project_layout();proposed[field]=value
    with pytest.raises(ValueError):module().validate_layout(proposed)


@pytest.mark.parametrize('path',['../other','/tmp/other','project/../other','project/key/file','project/token.json'])
def test_unsafe_scopes_rejected_before_any_original_read(path):
    proposed=layout(component('bad','PROJECT',path,'registry.db'))
    with pytest.raises(ValueError):module().validate_layout(proposed)


def native_program(tmp_path, monkeypatch, *, source=None, initial_proposal=None, state=None, extended=False, failure=None,
                   register_original_trials=False, funding_receipt=True, program_id=1, first_attempt=1):
    """Real program/queue/observer/native computation, synthetic public transport.

    The clock moves before collection, rather than backdating a retained plan.
    The test adapter chooses the release identity before the observer writes it.
    """
    import asyncio
    import json
    from tools import gold_bar_program as program, gold_bar_research as research, gold_bar_observe as observer
    from tests.tools.test_gold_bar_observe import recorded
    from tests.research.test_gold_bar_dataset import raw_rows, STEP
    from tests.execution.test_perpetual_public_replay import START, gold_capture_fixture
    source = source or {'commit':'f'*40}
    tmp_path.mkdir(mode=0o700,parents=True,exist_ok=True)
    rows=raw_rows();shift=START//STEP*STEP-1001*STEP
    for row in rows:row[0]+=shift;row[6]+=shift
    class CapturePatches:
        def setattr(self,obj,name,value):
            if obj is observer and name=='source_identity':value=lambda:source
            monkeypatch.setattr(obj,name,value)
    if extended:
        from tests.tools.test_gold_extended_window import extended_inputs
        from tests.execution.test_perpetual_public_replay import sealed
        from tools.gold_bar_journal import restore
        journal,capture,close=extended_inputs(tmp_path,CapturePatches())
        cursor,_=restore(journal)
        last=json.loads((journal/'messages.jsonl').read_text().splitlines()[-1])
        message=json.loads(last['raw']);received=last['received_at_ms']+STEP
        message['data']['E']=received
        for key in ('t','T'):message['data']['k'][key]+=STEP
        raw=json.dumps(message);decision=cursor.ingest(raw,received_at_ms=received)
        for name,value in [('messages',dict(received_at_ms=received,raw=raw)),('decisions',decision)]:
            with (journal/(name+'.jsonl')).open('a') as stream:stream.write(json.dumps(value)+'\n')
        summary=json.loads((journal/'summary.json').read_bytes());summary.update(messages=4,closed_bars=4)
        for name in ('messages','decisions'):summary[name+'_sha256']=hashlib.sha256((journal/(name+'.jsonl')).read_bytes()).hexdigest()
        (journal/'summary.json').write_text(json.dumps(summary))
        plan=json.loads((journal/'plan.json').read_bytes());plan['seconds']=2900
        (journal/'plan.json').write_text(json.dumps(plan))
        last_sample=capture['samples'][-1];last_time=last_sample['mark']['requested_at']
        for timestamp in sorted(set(range(last_time+10000,close+3*STEP+30001,10000))|{close+3*STEP+12000}):
            sample=copy.deepcopy(last_sample)
            for kind,delay in [('mark',1),('depth',2)]:sample[kind].update(requested_at=timestamp,received_at=timestamp+delay)
            sample['mark']['payload']['time']=timestamp
            sample['depth']['payload'].update(T=timestamp,E=timestamp,lastUpdateId=len(capture['samples'])+1)
            capture['samples'].append(sample)
        capture['exchange_after'].update(requested_at=timestamp+3,received_at=timestamp+4)
        capture['window']['close_times_ms'].append(close+3*STEP)
        if register_original_trials and funding_receipt:
            # The original receipt mode requires a forecast boundary and raw
            # response. These are synthetic observations, never account cash.
            nominal = close+20000
            capture['bootstrap']['funding']['payload'][0]['fundingTime'] = nominal-14400000
            for sample in capture['samples']:
                mark = sample['mark']['payload']
                mark['nextFundingTime'] = nominal if mark['time'] < nominal else nominal+14400000
            record = copy.deepcopy(capture['bootstrap']['funding'])
            record.update(requested_at=nominal+10000, received_at=nominal+10001,
                payload=[dict(symbol='XAUUSDT', fundingTime=nominal+2, fundingRate='0.01', markPrice='4000')])
            capture['funding_observations'] = [sealed({'record':record})['record']]
        capture=sealed(capture)
    else:
        _,journal=recorded(tmp_path,CapturePatches(),values=rows,received=START+1500)
        close=START//STEP*STEP;received=START+1500;capture=gold_capture_fixture()
    root=state or tmp_path/'state';clock=[close-STEP-180000 if register_original_trials else close-200000]
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(root))
    for current in (program,gold,research):monkeypatch.setattr(current,'source_identity',lambda:source)
    monkeypatch.setattr(program.time,'time_ns',lambda:clock[0]*1000000)
    async def wait(target):clock[0]=target-100000
    async def observe(history,output,*,seconds,**kwargs):
        shutil.copytree(journal,output);clock[0]=received
        return json.loads((journal/'summary.json').read_bytes())
    monkeypatch.setattr(program,'wait_for_capture',wait)
    monkeypatch.setattr(program,'capture',lambda path:shutil.copytree(tmp_path/'history',path))
    monkeypatch.setattr(program,'observe',observe)
    def quotes(**kwargs):
        records=list(capture['bootstrap'].values())
        records += [record for sample in capture['samples'] for record in sample.values()]
        records += [capture['exchange_after']]
        if 'funding_after' in capture:records.append(capture['funding_after'])
        records += capture.get('funding_observations',[])
        for record in records:kwargs['on_record'](record)
        return capture
    monkeypatch.setattr(program,'capture_public',quotes)
    monkeypatch.setattr(program,'capture_gold_extended',quotes)
    monkeypatch.setattr(program,'propose',lambda *a,**kw:{'candidate_id':0,'model':'synthetic-public-model'})
    # Commit-only historical fixtures retain the standalone diagnostic format.
    kwargs={'register_original_trials':register_original_trials}
    if initial_proposal is not None:
        kwargs['initial_proposal']=initial_proposal
    if extended:kwargs.update(continuous=True,window_bars=4,settlement_window_ms=1000,portfolio=True)
    if failure=='review':
        from tools import gold_bar_receipt
        original=gold_bar_receipt.run_check
        def failed_review(params):
            original(params)
            raise OSError('SYNTHETIC_RECEIPT_RESPONSE_LOSS')
        monkeypatch.setattr(gold_bar_receipt,'run_check',failed_review)
    elif failure=='research':
        def failed_research(arguments):raise ValueError('SYNTHETIC_COMPUTATION_FAILURE')
        monkeypatch.setattr(gold,'_execute',failed_research)
    if failure:
        with pytest.raises(ValueError):
            asyncio.run(program.run_program(program_id=program_id,first_attempt=first_attempt,rounds=1,ai_plan=True,**kwargs))
        result=json.loads((root/f'data/cache/gold_bar_research/programs/{program_id}/summary.json').read_bytes())
    else:
        result=asyncio.run(program.run_program(program_id=program_id,first_attempt=first_attempt,rounds=1,ai_plan=True,**kwargs))
    private(root)
    return dict(root=root,source=source,result=result,clock=clock)


def read_jobs(path):
    import sqlite3
    from services.research.run_registry.store import ResearchRunStore,_COLS
    store=object.__new__(ResearchRunStore)
    store._c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:return {row.run_id:row.to_dict() for row in (store._row(v) for v in store._c.execute('SELECT '+','.join(_COLS)+' FROM runs'))}
    finally:store._c.close()


@pytest.mark.parametrize('failure',['review','research'])
def test_actual_program_failed_and_unfinished_jobs_survive_without_reconciliation(tmp_path,monkeypatch,failure):
    f=native_program(tmp_path,monkeypatch,failure=failure)
    root=f['root'];before=originals(root)
    value=module().verify(root,layout=layout(component('gold','GOLD_RESEARCH','data/cache/gold_bar_research'),
        component('jobs','RUN_REGISTRY','data/cache/research_runs.db')))
    assert value['components']['gold']['native']['programs']['1']['summary']==f['result']
    assert value['components']['gold']['partial']
    assert value['components']['jobs']['native']['status_counts']['error']==1
    assert originals(root)==before


@pytest.mark.parametrize('damage',['program_deleted','receipt_deleted','request_changed','round_changed',
    'events_deleted','unknown_program_member','job_metrics_changed','warmup_changed','quote_receipt_deleted','program_quantity'])
def test_native_program_missing_or_replaced_relationship_never_completes(tmp_path,monkeypatch,damage):
    import json
    import sqlite3
    f=native_program(tmp_path,monkeypatch);root=f['root'];area=root/'data/cache/gold_bar_research'
    if damage=='program_deleted':shutil.rmtree(area/'programs')
    elif damage=='receipt_deleted':shutil.rmtree(area/'results/1/receipt-checks')
    elif damage=='events_deleted':(area/'programs/1/events.jsonl').unlink()
    elif damage=='unknown_program_member':(area/'programs/1/ignored.json').write_text('{}')
    elif damage=='warmup_changed':(area/'requests/1/history/response.json').write_bytes(b'[]')
    elif damage=='quote_receipt_deleted':(area/'requests/1/quote-receipts/0001.json').unlink()
    elif damage=='program_quantity':
        path=area/'programs/1/plan.json';value=json.loads(path.read_bytes());value['quantity']='0.03';path.write_text(json.dumps(value))
    elif damage=='job_metrics_changed':
        with sqlite3.connect(root/'data/cache/research_runs.db') as db:
            row=db.execute("SELECT run_id,payload_json FROM runs WHERE kind='gold_bar_research'").fetchone()
            payload=json.loads(row[1]);payload['metrics']['result_hash']='0'*64
            db.execute('UPDATE runs SET payload_json=? WHERE run_id=?',(json.dumps(payload),row[0]))
    else:
        path=area/('requests/1/plan.json' if damage=='request_changed' else 'programs/1/round-1.json')
        value=json.loads(path.read_bytes())
        value['quantity' if damage=='request_changed' else 'result_hash']='0.03' if damage=='request_changed' else '0'*64
        path.write_text(json.dumps(value))
    private(root)
    with pytest.raises((ValueError,FileNotFoundError)):
        module().verify(root,layout=layout(component('gold','GOLD_RESEARCH','data/cache/gold_bar_research'),
            component('jobs','RUN_REGISTRY','data/cache/research_runs.db')))


def test_actual_feedback_program_has_readonly_experiment_relationship(completed_bar_feedback,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from tools import gold_bar_feedback_research as research,gold_bar_feedback_experiment as experiment
    f=completed_bar_feedback;root=tmp_path/'feedback-native';base=root/'data/cache/ai_factory'
    pub=base/'bar-execution-feedback';pub.mkdir(parents=True,mode=0o700)
    key=f.artifact['result_hash'];(pub/(key+'.json')).write_bytes(f.artifact_bytes)
    source={'commit':f.source_commit}
    for current in (research,experiment):
        monkeypatch.setattr(current,'runtime_state_root',lambda:root)
        monkeypatch.setattr(current,'source_identity',lambda:source)
    monkeypatch.setattr(research.time,'time_ns',lambda:f.clock[0]*1000000)
    monkeypatch.setattr(research,'propose',lambda *a,**kw:{'candidate_id':0,'model':'synthetic-public-model'})
    proposal=research.run(key);calls=[]
    def child(argv,**kwargs):
        calls.append(argv)
        with monkeypatch.context() as patch:
            native_program(tmp_path/'child-inputs',patch,source=source,initial_proposal=Path(proposal['proposal_directory']),
                state=Path(kwargs['env']['QUANTOS_STATE_ROOT']),extended=True)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(experiment.subprocess,'run',child)
    result=experiment.run_research({'feedback_sha256':key});private(root)
    from services.research.ai_factory_store import FactoryStore
    with FactoryStore(base/'factory.db') as store:
        store.create_campaign(dict(campaign_id=1,objective='BTC',mode='baseline',budget=3,
            max_cycles=2,interval_hours=1,source_ids=['aqr']),operator='isolated',now=1)
        store.reserve_campaign(1,now=1)
    private(root)
    assert len(calls)==1
    before=originals(root)
    monkeypatch.setattr(experiment.subprocess,'run',lambda *a,**kw:pytest.fail('readonly child'))
    value=experiment.review_retained_experiment(base,key,original_source=source,original_base=base,
        jobs=read_jobs(base/'bar-feedback-experiments'/key/'state/data/cache/research_runs.db'))
    assert value['result']==result and value['partial']==[]
    assert originals(root)==before
    # Full reader consumes the same originals, including child jobs, all trial
    # ledgers and raw observation tapes. No source path is opened after moving.
    with monkeypatch.context() as patch:
        patch.setattr(os,'geteuid',lambda:_TEST_OWNER[0]);patch.setattr(os,'getegid',lambda:_TEST_OWNER[1])
        declared=layout(component('feedback','FACTORY','data/cache/ai_factory'));declared['source_commit']=f.source_commit
        first=module().verify(root,layout=declared)
        assert first['components']['feedback']['partial']==[]
        assert first['components']['feedback']['native']['feedback']['experiments'][key]['result']==result
        assert first['components']['feedback']['native']['tables']['campaign_rounds']['count']==1
        assert first['trial_ledgers']
        assert any('messages.jsonl' in name for name in first['files'])
        target=tmp_path/'different-research-root';shutil.copytree(root,target)
        shutil.rmtree(root)
        second=module().verify(target,layout=declared,isolated_restore_owner=_TEST_OWNER,original_root=root)
        assert first['semantic_sha256']==second['semantic_sha256']
        with pytest.raises(ValueError,match='RESEARCH_RESULT_CHANGED'):
            module().verify(target,layout=declared,isolated_restore_owner=_TEST_OWNER,original_root=target)


def test_total_rows_are_bounded_across_project_and_shared_registry_before_native(registered_case,monkeypatch):
    f=registered_case;f['prepare']();execute(f)
    tool=module()
    paths=[f['root']/'registry.db',f['directories']['one']/'archive/trials.db']
    counts=[sum(v['count'] for v in tool._database_tables(path)['tables'].values()) for path in paths]
    assert all(count<sum(counts)-1 for count in counts)
    monkeypatch.setattr(tool,'MAX_ROWS',sum(counts)-1)
    monkeypatch.setattr(tool,'_native',lambda *a,**kw:pytest.fail('native ran before total row budget'))
    with pytest.raises(ValueError,match='SQL_ROWS'):tool.verify(f['root'],layout=project_layout())


def test_real_program_exit_before_initial_plan_preserves_unknown_directory(tmp_path,monkeypatch):
    from tools import gold_bar_program as program
    pid=os.fork()
    if pid==0:
        original=program._write
        def interrupted(path,value):
            if path.name=='plan.json' and path.parent.name=='1':os._exit(77)
            return original(path,value)
        program._write=interrupted
        native_program(tmp_path,monkeypatch)
        os._exit(88)
    assert join(pid)==77
    root=tmp_path/'state';private(root);before=originals(root)
    value=module().verify(root,layout=layout(component('gold','GOLD_RESEARCH','data/cache/gold_bar_research')))
    assert value['components']['gold']['partial']==[dict(path='programs/1',reason='PROGRAM_PLAN_NOT_PERSISTED')]
    assert originals(root)==before


@pytest.mark.parametrize('damage',['unchanged','bytes','inode','membership'])
def test_verified_cut_keeps_original_read_set_until_inventory_finishes(registered_case,damage):
    f=registered_case;f['prepare']();execute(f)
    tool=module();held=tool.capture_verified(f['root'],layout=project_layout())
    value=held.view();manifest=held.files_manifest()
    assert value['files']==manifest and held.recheck() is held
    value['whole_set_verified']=True;manifest.clear()
    assert held.view()['whole_set_verified'] is False and held.files_manifest()
    path=f['directories']['one']/'input.json'
    if damage=='bytes':path.write_bytes(path.read_bytes()+b' ')
    elif damage=='inode':
        body=path.read_bytes();path.unlink();path.write_bytes(body)
    elif damage=='membership':(path.parent/'later.json').write_text('{}')
    if damage=='unchanged':assert held.recheck() is held
    else:
        with pytest.raises(ValueError):held.recheck()


def test_native_program_has_readonly_relation_verifier(tmp_path,monkeypatch):
    f=native_program(tmp_path,monkeypatch)
    from tools import gold_bar_program as program
    from services.research.run_registry.store import ResearchRunStore,_COLS
    import sqlite3
    store=object.__new__(ResearchRunStore)
    store._c=sqlite3.connect((f['root']/'data/cache/research_runs.db').as_uri()+'?mode=ro',uri=True)
    try:jobs={row.run_id:row.to_dict() for row in (store._row(v) for v in store._c.execute('SELECT '+','.join(_COLS)+' FROM runs'))}
    finally:store._c.close()
    gold_root=f['root']/'data/cache/gold_bar_research';before=originals(f['root'])
    value=program.verify_retained_program(gold_root/'programs/1',requests_root=gold_root/'requests',
        results_root=gold_root/'results',jobs=jobs,original_source=f['source'])
    assert value['summary']==f['result'] and value['partial']==[]
    assert originals(f['root'])==before


def test_native_feedback_hypothesis_has_readonly_historical_entry(completed_bar_feedback,tmp_path,monkeypatch):
    from tools import gold_bar_feedback_research as research
    from quantos.contracts.json_integrity import load_json_without_duplicate_keys
    f=completed_bar_feedback;root=tmp_path/'feedback-research';base=root/'data/cache/ai_factory'
    pub=base/'bar-execution-feedback';pub.mkdir(parents=True,mode=0o700)
    key=f.artifact['result_hash'];(pub/(key+'.json')).write_bytes(f.artifact_bytes)
    source={'commit':f.source_commit}
    monkeypatch.setattr(research,'runtime_state_root',lambda:root)
    monkeypatch.setattr(research,'source_identity',lambda:source)
    monkeypatch.setattr(research.time,'time_ns',lambda:f.clock[0]*1000000)
    monkeypatch.setattr(research,'propose',lambda *a,**kw:{'candidate_id':0,'model':'synthetic-public-model'})
    result=research.run(key);private(root)
    before=originals(root)
    monkeypatch.setattr(research,'propose',lambda *a,**kw:pytest.fail('readonly invoked model'))
    value=research.review_retained_hypothesis(base,key,original_source=source,original_base=base)
    assert value['result']==result and value['partial']==[]
    assert load_json_without_duplicate_keys((base/'bar-feedback-research'/key/'result.json').read_bytes())==result
    assert originals(root)==before
