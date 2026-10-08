"""Pre-register native research attempts without changing their evidence status.

One fixed run directory owns one invocation.  A durable invocation with no result
is UNKNOWN: restarting does not silently call the producer a second time.
"""
from contextlib import contextmanager, ExitStack
import copy
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import time
import uuid

from quantos.contracts import registered_research_run as contract
from quantos.contracts.gold_forward_evaluation_design import FULL_REVIEW_SCHEMAS
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.trial_ledger import TrialLedger, TrialRecord
from services.research.registered_research_registration import (
    expected_provenance as _provenance, registration_evidence as registration_evidence,
    native_trial_identity as _identity, initial_archive_manifest as _manifest,
)
from tools import gold_bar_experiment as gold

canonical = contract.canonical
plan_digest = contract.digest
require = contract.require
FILES = frozenset(('plan.json','input.json','registration.json','invocation.json','outcome.json','archive','input-parts'))


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def _directory(path):
    path = Path(path).absolute()
    require(path.resolve() == path, 'PATH')
    value = path.lstat()
    require(stat.S_ISDIR(value.st_mode) and value.st_uid == os.getuid()
            and stat.S_IMODE(value.st_mode) == 0o700, 'DIRECTORY')
    return path


def _mkdir(path):
    try:
        path.mkdir(mode=0o700)
        _sync(path.parent)
    except FileExistsError:
        pass
    return _directory(path)


def _read(path, *, maximum=contract.MAX_OUTPUT_BYTES):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600
                and 0 < before.st_size <= maximum, 'FILE')
        chunks=[]; remaining=maximum+1
        while remaining:
            chunk=os.read(fd,min(remaining,65_536))
            if not chunk: break
            chunks.append(chunk); remaining-=len(chunk)
        raw=b''.join(chunks)
        after=os.fstat(fd)
        require(_identity_stat(before)==_identity_stat(after)==_identity_stat(path.lstat())
                and len(raw)==before.st_size, 'READ_CHANGED')
        return raw
    finally: os.close(fd)


def _identity_stat(value):
    # atime may legitimately change because of this very read.
    return tuple(getattr(value,k) for k in ('st_dev','st_ino','st_mode','st_uid','st_gid',
        'st_nlink','st_size','st_mtime_ns','st_ctime_ns'))


def _load(path, *, maximum=contract.MAX_OUTPUT_BYTES):
    raw=_read(path,maximum=maximum)
    value=load_json_without_duplicate_keys(raw)
    # The only two serializers are the new UTF-8 envelope and the unchanged
    # original ASCII Gold archive representation.
    require(raw in (canonical(value,maximum=maximum),gold._bytes(value)),'CANONICAL_JSON')
    return value


def _load_gold_report(path):
    # A supported four-candidate/720-quote tape retains each original account
    # frame. Keep the byte/depth bounds and the smaller plan/input node limit.
    raw = _read(path, maximum=contract.MAX_OUTPUT_BYTES)
    value = load_json_without_duplicate_keys(raw)
    utf8 = canonical(value, maximum=contract.MAX_OUTPUT_BYTES, max_nodes=1_000_000)
    require(raw in (utf8, gold._bytes(value)), 'CANONICAL_JSON')
    return value


def _put(path, value, *, raw=None):
    raw=canonical(value) if raw is None else raw
    require(0<len(raw)<=contract.MAX_OUTPUT_BYTES,'BYTES')
    run_root=path.parent.parent if path.parent.name=='archive' else path.parent
    if run_root.parent.name=='runs':
        require(_run_bytes(run_root)+ (0 if path.exists() else len(raw))<=contract.MAX_RUN_BYTES,'RUN_BYTES')
    try:
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    except FileExistsError:
        require(_read(path)==raw,'EXISTING_BYTES')
        return hashlib.sha256(raw).hexdigest()
    try:
        view=memoryview(raw)
        while view:
            count=os.write(fd,view)
            require(count>0,'WRITE')
            view=view[count:]
        os.fsync(fd)
        written=os.fstat(fd)
    finally: os.close(fd)
    _sync(path.parent)
    require(_identity_stat(path.lstat())==_identity_stat(written),'WRITE_CHANGED')
    require(_read(path)==raw,'WRITE_CHANGED')
    return hashlib.sha256(raw).hexdigest()


def _run_bytes(root):
    total=0
    plan_file=root/'plan.json'
    plan=_load(plan_file,maximum=contract.MAX_PLAN_BYTES) if plan_file.exists() else None
    storage=plan.get('result_storage') if plan is not None else None
    if storage is not None:
        contract.validate_plan(plan)
    for leaf in root.iterdir():
        if leaf.name == 'input-parts':
            require(plan is not None and plan['schema'] in contract.SOURCE_GOLD_SCHEMAS, 'SOURCE_DIRECTORY')
            from tools.gold_native_source_run import source_bytes
            source_bytes(leaf, plan['source_storage'])
            continue
        if leaf.name=='archive':
            _directory(leaf)
            leaves=list(leaf.iterdir())
        else: leaves=[leaf]
        for item in leaves:
            if item.name=='report-parts' and leaf.name=='archive' and storage is not None:
                total+=_chunked_bytes(item,storage)
                require(total<=contract.MAX_RUN_BYTES,'RUN_BYTES')
                continue
            value=item.lstat()
            require(stat.S_ISREG(value.st_mode) and value.st_uid==os.getuid()
                    and value.st_nlink==1,'RUN_FILE')
            total+=value.st_size
            require(total<=contract.MAX_RUN_BYTES,'RUN_BYTES')
    return total


def _chunked_bytes(root, storage):
    """Bound report files separately; preserve the original metadata byte cap."""
    from quantos.contracts.gold_chunked_result import MAX_PARTS, MAX_MANIFEST_BYTES
    _directory(root)
    require({p.name for p in root.iterdir()} <= {'blobs','manifest.json','failure.json'},'GOLD_RESULT_MEMBERS')
    blobs=_directory(root/'blobs')
    total=0
    count=0
    for item in blobs.iterdir():
        value=item.lstat()
        count+=1
        require(contract._hash(item.name) and count<=MAX_PARTS and stat.S_ISREG(value.st_mode)
            and value.st_uid==os.getuid() and value.st_nlink==1
            and stat.S_IMODE(value.st_mode)==0o600 and 0<value.st_size<=storage['chunk_bytes'], 'GOLD_RESULT_FILE')
        total+=value.st_size
        require(total<=storage['maximum_bytes'],'GOLD_RESULT_BYTES')
    metadata=0
    for name in ('manifest.json','failure.json'):
        if (root/name).exists():
            metadata+=len(_read(root/name,maximum=MAX_MANIFEST_BYTES))
    return metadata


def _put_gold_report(root, report, plan):
    if plan['schema'] not in contract.CHUNKED_GOLD_SCHEMAS:
        return _put(root/'archive/report.json',report,raw=gold._bytes(report))
    from tools.gold_chunked_result import write_report
    reference=write_report(report,root/'archive/report-parts',input_sha256=plan['input_sha256'],
        source_identity_sha256=plan_digest(plan['source_identity']),storage=plan['result_storage'])
    _put(root/'archive/report.json',reference)


@contextmanager
def _chunked_gold_report(root, plan):
    from quantos.contracts import gold_chunked_result as shape
    from services.research.gold_chunked_result_reader import open_gold_result
    reference=shape.validate_reference(_load(root/'archive/report.json'))
    require(reference['input_sha256']==plan['input_sha256']
        and reference['source_identity_sha256']==plan_digest(plan['source_identity']), 'GOLD_RESULT_BINDING')
    parts=root/'archive/report-parts'
    manifest=shape.validate(_load(parts/'manifest.json',maximum=shape.MAX_MANIFEST_BYTES))
    require(shape.reference(manifest,reference['manifest_sha256'])==reference, 'GOLD_RESULT_REFERENCE')
    with tempfile.TemporaryDirectory(prefix='quantos-registered-gold-result-') as directory:
        with open_gold_result(parts,Path(directory)/'index',expected_manifest_sha256=reference['manifest_sha256'],
                maximum_source_bytes=plan['result_storage']['maximum_bytes'],
                maximum_index_bytes=plan['result_storage']['maximum_index_bytes']) as report:
            yield report


@contextmanager
def _locked(path):
    # Same native writer lock for both registration and the original archive.
    # Lock files live outside the managed run directory and are not evidence.
    with gold._writer_lock(path):
        yield


def run_directory(project_root, plan):
    contract.validate_plan(plan)
    return Path(project_root).absolute()/'runs'/plan['invocation_id']


def _plan(*, adapter, input_raw, source, candidates, search_id, producer_id, actor_id,
          dataset_version, registered_at_ms, invocation_id, evaluation_core=None, risk_review_policy=None,
          forward_design=None, result_storage=None, history_storage=None, source_storage=None):
    reanalysis = False
    if source_storage is not None:
        from quantos.contracts.gold_native_source import REANALYSIS_INPUT_SCHEMAS
        source_document = load_json_without_duplicate_keys(input_raw)
        reanalysis = source_document.get('input_schema') in REANALYSIS_INPUT_SCHEMAS
    if evaluation_core is not None and adapter == contract.GOLD:
        require(reanalysis and risk_review_policy is None and forward_design is None,
                'GOLD_EVALUATION_SOURCE')
    search=dict(search_id=search_id,search_kind='FORWARD' if adapter==contract.GOLD else 'BACKTEST',
                source=producer_id,dataset_version=dataset_version,recorded_at_ms=registered_at_ms)
    trials=[]
    for candidate, definition, preimage in candidates:
        family=gold._hash('GOLD_BAR_'+candidate.upper()) if adapter==contract.GOLD else plan_digest('TECHNICAL_LINEAR_PERPETUAL_V2')
        policy=gold._hash({'mode':'TECHNICAL_COMPUTATION_ONLY','automatic_selection':False})
        trials.append(dict(candidate_id=candidate,config_hash=definition,
            native_trial_id=contract.native_trial_id(invocation_id,candidate,definition),
            provenance_trial_id=search_id+':'+definition,alpha_id=definition,family_id=family,
            validation_policy_digest=policy,definition_preimage=preimage))
    plan = dict(schema=contract.SCHEMA,invocation_id=invocation_id,adapter=adapter,
        search=search,actor_id=actor_id,input_sha256=plan_digest(input_raw),source_identity=source,
        trials=trials,submission_enabled=False,economic_authority=False)
    if evaluation_core is not None:
        from quantos.contracts.research_evaluation_plan import core_sha256
        frozen = copy.deepcopy(evaluation_core)
        policy = core_sha256(frozen)
        if adapter == contract.GOLD:
            require(frozen['dataset_manifest_sha256'] == source_document['source_binding']['manifest_sha256'],
                    'GOLD_EVALUATION_DATASET')
        plan.update(schema=contract.EVALUATED_SCHEMA, evaluation_core=frozen)
        for trial in trials:
            trial['validation_policy_digest'] = policy
    if risk_review_policy is not None:
        from quantos.contracts.registered_risk_replay import validation_policy_sha256
        require(evaluation_core is not None, "RISK_EVALUATION_REQUIRED")
        plan.update(schema=contract.RISK_EVALUATED_SCHEMA,risk_review_policy=copy.deepcopy(risk_review_policy))
        for trial in trials:
            trial["validation_policy_digest"]=validation_policy_sha256(plan["evaluation_core"],plan["risk_review_policy"])
    if forward_design is not None:
        require(evaluation_core is None and risk_review_policy is None, 'FORWARD_EVALUATION_MODE')
        plan.update(schema=contract.FORWARD_DECLARED_SCHEMA, forward_design=copy.deepcopy(forward_design))
        if forward_design['schema'] in FULL_REVIEW_SCHEMAS:
            for trial in trials:
                trial['validation_policy_digest'] = plan_digest(forward_design)
    if result_storage is not None:
        require(adapter==contract.GOLD and (evaluation_core is None or reanalysis) and risk_review_policy is None,
                'GOLD_RESULT_ADAPTER')
        plan.update(schema=contract.CHUNKED_GOLD_FORWARD_SCHEMA if forward_design is not None
            else contract.CHUNKED_GOLD_SCHEMA,result_storage=copy.deepcopy(result_storage))
    if history_storage is not None:
        require(result_storage is not None, 'GOLD_HISTORY_RESULT_REQUIRED')
        contract.validate_history_storage(history_storage)
        plan.update(schema=contract.STREAMED_GOLD_FORWARD_SCHEMA if forward_design is not None
            else contract.STREAMED_GOLD_SCHEMA, history_storage=copy.deepcopy(history_storage))
    if source_storage is not None:
        from quantos.contracts.gold_native_source import input_storage, BUDGETED_STORAGE_SCHEMA, REANALYSIS_INPUT_SCHEMAS
        require(history_storage is not None and result_storage is not None, 'SOURCE_STORAGE_REQUIRES_STREAMING')
        source_document = load_json_without_duplicate_keys(input_raw)
        input_storage(source_storage, source_document)
        schemas = ((contract.BUDGETED_SOURCE_GOLD_SCHEMA, contract.BUDGETED_SOURCE_GOLD_FORWARD_SCHEMA)
            if source_storage['schema'] == BUDGETED_STORAGE_SCHEMA
            else (contract.SOURCE_GOLD_SCHEMA, contract.SOURCE_GOLD_FORWARD_SCHEMA))
        plan.update(schema=schemas[forward_design is not None], source_storage=copy.deepcopy(source_storage))
        if source_document['input_schema'] in REANALYSIS_INPUT_SCHEMAS:
            require(forward_design is None and risk_review_policy is None,
                    'REANALYSIS_EVALUATION_MODE')
            schemas = ((contract.EVALUATED_SOURCE_REANALYSIS_SCHEMA,
                        contract.BUDGETED_EVALUATED_SOURCE_REANALYSIS_SCHEMA) if evaluation_core is not None
                       else (contract.SOURCE_REANALYSIS_SCHEMA, contract.BUDGETED_SOURCE_REANALYSIS_SCHEMA))
            plan['schema'] = schemas[source_storage['schema'] == BUDGETED_STORAGE_SCHEMA]
            plan['search']['search_kind'] = 'BACKTEST'
    plan = contract.validate_plan(plan)
    if source_storage is not None:
        from quantos.contracts.gold_native_source import bind_plan
        bind_plan(plan, source_document)
    return plan


def _prepare(input_raw, *, project_root, adapter, source, candidates, search_id,
             producer_id, actor_id, dataset_version, registered_at_ms, invocation_id=None,
             evaluation_core=None, risk_review_policy=None, forward_design=None, result_storage=None,
             history_storage=None, source_storage=None):
    require(type(input_raw) is bytes and 0<len(input_raw)<=contract.MAX_INPUT_BYTES,'INPUT_BYTES')
    invocation_id=(uuid.uuid4().hex+uuid.uuid4().hex) if invocation_id is None else invocation_id
    plan=_plan(adapter=adapter,input_raw=input_raw,source=source,candidates=candidates,search_id=search_id,
               producer_id=producer_id,actor_id=actor_id,dataset_version=dataset_version,
               registered_at_ms=registered_at_ms,invocation_id=invocation_id,evaluation_core=evaluation_core,risk_review_policy=risk_review_policy,
               forward_design=forward_design,result_storage=result_storage,history_storage=history_storage,
               source_storage=source_storage)
    if forward_design is not None:
        from quantos.contracts.gold_forward_evaluation_design import bind_input
        bind_input(plan['forward_design'], plan, load_json_without_duplicate_keys(input_raw))
    project=_mkdir(Path(project_root).absolute())
    runs=_mkdir(project/'runs')
    destination=runs/invocation_id
    with _locked(destination):
        _mkdir(destination)
        require(set(p.name for p in destination.iterdir())<=FILES,'RUN_MEMBERS')
        if not (destination/'plan.json').exists():
            require(not any(destination.iterdir()),'PLAN_MISSING')
        _put(destination/'plan.json',plan)
        if not (destination/'input.json').exists():
            require(set(p.name for p in destination.iterdir())=={'plan.json'},'INPUT_MISSING')
        _put(destination/'input.json',None,raw=input_raw)
    return plan


def prepare_gold(arguments, *, project_root, search_id, producer_id, actor_id, dataset_version,
                 registered_at_ms, invocation_id=None, forward_design=None, result_storage=None,
                 history_storage=None, source_storage=None, evaluation_core=None):
    source=gold.source_identity()
    require(canonical(arguments.get('release_identity'))==canonical(source),'SOURCE')
    raw=_gold_input(arguments)
    input_hash=plan_digest(raw)
    candidates=[]
    for name in gold.trial_candidates(arguments):
        preimage=dict(input_sha256=input_hash,candidate=name,source=source)
        candidates.append((name,gold.gold_trial_definition_id(**preimage),preimage))
    return _prepare(raw,project_root=project_root,adapter=contract.GOLD,source=source,candidates=candidates,
        search_id=search_id,producer_id=producer_id,actor_id=actor_id,dataset_version=dataset_version,
        registered_at_ms=registered_at_ms,invocation_id=invocation_id,forward_design=forward_design,
        result_storage=result_storage, history_storage=history_storage, source_storage=source_storage,
        evaluation_core=evaluation_core)


def _gold_input(arguments):
    if 'raw_bars' in arguments:
        require(type(arguments['raw_bars']) is bytes
                and len(arguments['raw_bars'])<=contract.MAX_INPUT_BYTES*3//4,'INPUT_BYTES')
    document=gold._encode(arguments)
    canonical(document,maximum=contract.MAX_INPUT_BYTES)  # pre-expansion bounds
    raw=gold._bytes(document)  # original ASCII archive bytes remain unchanged
    require(len(raw)<=contract.MAX_INPUT_BYTES,'INPUT_BYTES')
    return raw


def _register(plan, root, registry):
    receipt=root/'registration.json'
    if receipt.exists():
        require(_read(receipt)==canonical(registration_evidence(plan,registry)),'REGISTRATION_CHANGED')
        return
    require(not (root/'archive').exists() and not (root/'invocation.json').exists()
            and not (root/'outcome.json').exists(),'REGISTRATION_MISSING')
    search=plan['search']; sid=search['search_id']
    events=registry.search_attempt_events(sid)
    if not events:
        registry.begin_search_attempt(search_id=sid,search_kind=search['search_kind'],source=search['source'],
            dataset_version=search['dataset_version'],recorded_at=search['recorded_at_ms'])
    identity=registry.search_identity(sid)
    require(all(identity[k]==search[k] for k in ('search_id','search_kind','source','dataset_version')),'SEARCH_IDENTITY')
    events=registry.search_attempt_events(sid)
    require(len(events)==1 and events[0]['status']=='STARTED' and events[0]['recorded_at']==search['recorded_at_ms'],'SEARCH_EXISTING')
    existing=registry.search_candidates(sid)
    allowed={t['config_hash'] for t in plan['trials']}
    require({x['config_hash'] for x in existing}<=allowed and all(x['registered_at']==search['recorded_at_ms'] for x in existing),'CANDIDATE_CONFLICT')
    registry.register_search_candidates(sid,[t['config_hash'] for t in plan['trials']],registered_at=search['recorded_at_ms'])
    require({x['config_hash'] for x in registry.search_candidates(sid)}==allowed,'CANDIDATE_READBACK')
    for trial in plan['trials']:
        row=_provenance(plan,trial)
        registry.record_trial_provenance(row)
        require(canonical(registry.trial_provenance(row.trial_id)['record'])==canonical(row.canonical_payload()),'PROVENANCE_CONFLICT')
    _put(receipt,registration_evidence(plan,registry))


def _time(clock_ms, minimum):
    value=clock_ms()
    require(type(value) is int and minimum<=value<=2**63-1,'CLOCK')
    return value


def _rows(ledger, plan):
    ledger.verify()
    # Full enumeration, never just selected IDs: foreign or duplicate attempts
    # in this plan-owned native ledger cannot be hidden by a mapping.
    ids={r[0] for r in ledger._c.execute('SELECT DISTINCT trial_id FROM trial_events')}
    require(ids<={t['native_trial_id'] for t in plan['trials']},'LEDGER_POPULATION')
    out={}
    for trial in plan['trials']:
        if trial['native_trial_id'] in ids:
            aggregate=ledger.load(trial['native_trial_id'])
            require(all(getattr(aggregate,k)==v for k,v in _identity(trial).items()),'LEDGER_IDENTITY')
            require(aggregate.events[0].at>=plan['search']['recorded_at_ms'],'REGISTRATION_ORDER')
            out[trial['native_trial_id']]=aggregate
    return out


def _native_run(*, destination, input_raw, registry, clock_ms, adapter, execute, verify_source,
                replay_risk=None, execute_context=None):
    root=_directory(Path(destination).absolute())
    require(root.parent.name=='runs' and root.parent.parent.resolve()==root.parent.parent,'RUN_PATH')
    with _locked(root):
        require(set(p.name for p in root.iterdir())<=FILES,'RUN_MEMBERS')
        plan=contract.validate_plan(_load(root/'plan.json',maximum=contract.MAX_PLAN_BYTES))
        require(root.name==plan['invocation_id'] and plan['adapter']==adapter,'RUN_BINDING')
        require(_read(root/'plan.json')==canonical(plan) and _read(root/'input.json')==input_raw
                and plan['input_sha256']==plan_digest(input_raw),'INPUT_CHANGED')
        verify_source(plan)
        if 'forward_design' in plan:
            from quantos.contracts.gold_forward_evaluation_design import bind_input
            bind_input(plan['forward_design'], plan, load_json_without_duplicate_keys(input_raw))
        _time(clock_ms,plan['search']['recorded_at_ms'])
        _register(plan,root,registry)
        risk_version=plan['schema']==contract.RISK_EVALUATED_SCHEMA
        require((replay_risk is not None)==risk_version,'RISK_ADAPTER')
        archive=root/'archive'
        _mkdir(archive)
        allowed=set('input.json trials.db trials.db-wal trials.db-shm trials.db-journal running.json report.json manifest.json'.split())
        if plan['schema'] in contract.CHUNKED_GOLD_SCHEMAS:
            allowed.add('report-parts')
        if risk_version: allowed.add('risk-replay.json')
        require(set(p.name for p in archive.iterdir())<=allowed,'ARCHIVE_MEMBERS')
        _run_bytes(root)
        _put(archive/'input.json',None,raw=input_raw)
        db=archive/'trials.db'
        invocation=root/'invocation.json'; outcome_file=root/'outcome.json'
        new_db=not db.exists()
        if new_db:
            require(not invocation.exists() and not outcome_file.exists() and not (archive/'running.json').exists(),'LEDGER_MISSING')
            # Set custody before native SQLite initialization, so a crash just
            # after its schema commit does not leave a permissive old file.
            fd=os.open(db,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            try: os.fsync(fd)
            finally: os.close(fd)
            _sync(archive)
        else:
            metadata=db.lstat()
            require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink==1
                    and metadata.st_uid==os.getuid() and stat.S_IMODE(metadata.st_mode)==0o600,'LEDGER_CUSTODY')
        with TrialLedger(str(db),create=new_db) as ledger:
            _sync(archive)
            rows=_rows(ledger,plan)
            # These original calls must have completed and been re-read before
            # any native PLANNED append (including resumed prefixes).
            require(_read(root/'registration.json')==canonical(registration_evidence(plan,registry)),'REGISTRATION_CHANGED')
            minimum=max([plan['search']['recorded_at_ms']]+[r.events[-1].at for r in rows.values()])
            if invocation.exists():
                require(set(rows)=={t['native_trial_id'] for t in plan['trials']}
                        and all(r.status in ('RUNNING','PASSED','FAILED') for r in rows.values()),'INVOCATION_LEDGER')
            else:
                require(not outcome_file.exists() and not (archive/'report.json').exists()
                        and not (archive/'risk-replay.json').exists()
                        and all(r.status in ('PLANNED','RUNNING') for r in rows.values()),'INVOCATION_MISSING')
                for status,index in (('PLANNED',0),('RUNNING',1)):
                    for trial in plan['trials']:
                        row=rows.get(trial['native_trial_id'])
                        if row is None or len(row.events)<=index:
                            minimum=_time(clock_ms,minimum)
                            ledger.append(TrialRecord(**_identity(trial),status=status,event_index=index,at=minimum))
                    rows=_rows(ledger,plan)
                running=dict(schema='GOLD_BAR_RUNNING_ARCHIVE_V1' if adapter==contract.GOLD else 'QUANTOS_TECHNICAL_RUNNING_ARCHIVE_V1',
                             manifest=_manifest(plan),identities=[_identity(t) for t in plan['trials']])
                _put(archive/'running.json',running,raw=gold._bytes(running))
                require(_read(root/'registration.json')==canonical(registration_evidence(plan,registry)),'REGISTRATION_CHANGED')
                verify_source(plan)
                minimum=_time(clock_ms,minimum)
                witness=dict(schema='QUANTOS_REGISTERED_RESEARCH_INVOCATION_V1',plan_sha256=plan_digest(plan),
                    registration_sha256=plan_digest(_read(root/'registration.json')),started_at_ms=minimum)
                _put(invocation,witness)
                with ExitStack() as production:
                    try:
                        if execute_context is not None:
                            production.enter_context(execute_context(plan))
                        report=execute()
                    except Exception as exc:
                        failed=dict(schema='QUANTOS_REGISTERED_RESEARCH_OUTCOME_V1',plan_sha256=plan_digest(plan),
                            status='FAILED',error_type=type(exc).__name__,report_sha256=None,completed_at_ms=_time(clock_ms,minimum))
                        if risk_version:
                            failed.update(schema='QUANTOS_REGISTERED_RESEARCH_OUTCOME_V2',risk_replay_sha256=None)
                        _put(outcome_file,failed)
                        _finish(ledger,plan,root,registry,failed,clock_ms)
                        raise
                    # Publication/source/cut failures remain interrupted outcomes.
                    # Hold all original histories until publication is rechecked,
                    # and close their context before a successful outcome exists.
                    verify_source(plan)
                    require(_read(root/'input.json')==input_raw,'INPUT_CHANGED')
                    if adapter==contract.GOLD:
                        _put_gold_report(root,report,plan)
                    else:
                        _put(archive/'report.json',report,raw=canonical(report))
            witness=_load(invocation)
            require(set(witness)=={'schema','plan_sha256','registration_sha256','started_at_ms'}
                and witness['schema']=='QUANTOS_REGISTERED_RESEARCH_INVOCATION_V1'
                and witness['plan_sha256']==plan_digest(plan)
                and witness['registration_sha256']==plan_digest(_read(root/'registration.json'))
                and type(witness['started_at_ms']) is int
                and witness['started_at_ms']>=max(r.events[1].at for r in _rows(ledger,plan).values()),'INVOCATION')
            if outcome_file.exists():
                outcome=_load(outcome_file)
            else:
                require((archive/'report.json').exists(),'UNKNOWN_INVOCATION_OUTCOME')
                risk=None
                if risk_version:
                    # This continuation consumes the durable native report. It
                    # never invokes the registered computation a second time.
                    from quantos.contracts.registered_risk_replay import validate_artifact, build_artifact
                    report=_load(archive/'report.json')
                    # Only a genuinely unfinished native invocation can acquire
                    # its first outcome. Missing terminal history is not repaired.
                    require(all(r.status=='RUNNING' for r in _rows(ledger,plan).values())
                        and not (archive/'manifest.json').exists()
                        and len(registry.search_attempt_events(plan['search']['search_id']))==1,'RISK_TERMINAL_MISSING')
                    if (archive/'risk-replay.json').exists():
                        risk=validate_artifact(_load(archive/'risk-replay.json'),plan=plan,original_result=report,invocation=witness)
                    else:
                        started=_time(clock_ms,witness['started_at_ms'])
                        replay=replay_risk(plan,report)
                        verify_source(plan)
                        require(_read(root/'input.json')==input_raw
                            and _read(archive/'report.json')==canonical(report),'RISK_ORIGINAL_CHANGED')
                        require(_read(root/'registration.json')==canonical(registration_evidence(plan,registry)),'REGISTRATION_CHANGED')
                        risk=build_artifact(plan=plan,original_result=report,invocation=witness,replay=replay,
                            started_at_ms=started,completed_at_ms=_time(clock_ms,started))
                        _put(archive/'risk-replay.json',risk)
                    verify_source(plan)
                    require(_read(root/'input.json')==input_raw
                        and _read(archive/'report.json')==canonical(report)
                        and _read(archive/'risk-replay.json')==canonical(risk),'RISK_ORIGINAL_CHANGED')
                outcome=dict(schema='QUANTOS_REGISTERED_RESEARCH_OUTCOME_V1',plan_sha256=plan_digest(plan),
                    status='PASSED',error_type=None,report_sha256=plan_digest(_read(archive/'report.json')),
                    completed_at_ms=_time(clock_ms,risk['completed_at_ms'] if risk is not None else witness['started_at_ms']))
                if risk_version: outcome.update(schema='QUANTOS_REGISTERED_RESEARCH_OUTCOME_V2',risk_replay_sha256=plan_digest(risk))
                _put(outcome_file,outcome)
            if risk_version:
                verify_source(plan)
                require(_read(root/'input.json')==input_raw,'RISK_ORIGINAL_CHANGED')
                require(_read(root/'plan.json')==canonical(plan)
                    and _read(outcome_file)==canonical(outcome),'RISK_WITNESS_CHANGED')
                _time(clock_ms,outcome['completed_at_ms'])
            if plan['schema'] in contract.CHUNKED_GOLD_SCHEMAS and outcome['status']=='PASSED':
                with _chunked_gold_report(root,plan) as report:
                    _finish(ledger,plan,root,registry,outcome,clock_ms)
                    verify_source(plan)
                    return dict(status='TECHNICAL_COMPUTATION_COMPLETE',
                        trial_ids=[t['native_trial_id'] for t in plan['trials']], result_hash=report['result_hash'],
                        submission_enabled=False,economic_eligible=False)
            _finish(ledger,plan,root,registry,outcome,clock_ms)
            require(outcome['status']=='PASSED','TERMINAL_FAILURE')
            verify_source(plan)
            report=(_load_gold_report if adapter == contract.GOLD else _load)(archive/'report.json')
            if adapter==contract.GOLD:
                return dict(status='TECHNICAL_COMPUTATION_COMPLETE',trial_ids=[t['native_trial_id'] for t in plan['trials']],
                            result_hash=report['result_hash'],submission_enabled=False,economic_eligible=False)
            return report


def _finish(ledger,plan,root,registry,outcome,clock_ms):
    risk_version=plan['schema']==contract.RISK_EVALUATED_SCHEMA
    fields={'schema','plan_sha256','status','error_type','report_sha256','completed_at_ms'} | ({'risk_replay_sha256'} if risk_version else set())
    require(set(outcome)==fields
        and outcome['schema']==('QUANTOS_REGISTERED_RESEARCH_OUTCOME_V2' if risk_version else 'QUANTOS_REGISTERED_RESEARCH_OUTCOME_V1') and outcome['plan_sha256']==plan_digest(plan)
        and outcome['status'] in ('PASSED','FAILED') and type(outcome['completed_at_ms']) is int,'OUTCOME')
    witness=_load(root/'invocation.json')
    require(outcome['completed_at_ms']>=witness['started_at_ms'],'OUTCOME_TIME')
    report=root/'archive'/'report.json'
    if outcome['status']=='PASSED':
        require(outcome['error_type'] is None and outcome['report_sha256']==plan_digest(_read(report)),'OUTCOME_REPORT')
    else:
        require(type(outcome['error_type']) is str and 0<len(outcome['error_type'])<=200
                and outcome['report_sha256'] is None and not report.exists(),'FAILED_REPORT')
    if risk_version:
        from quantos.contracts.registered_risk_replay import verify_lifecycle
        documents={'invocation.json':witness}
        for leaf in ('report.json','risk-replay.json'):
            if (root/'archive'/leaf).exists():documents['archive/'+leaf]=_load(root/'archive'/leaf)
        verify_lifecycle(plan=plan,documents=documents,outcome=outcome)
    rows=_rows(ledger,plan)
    require(_read(root/'registration.json')==canonical(registration_evidence(plan,registry)),'REGISTRATION_CHANGED')
    for trial in plan['trials']:
        row=rows[trial['native_trial_id']]
        require(row.status in ('RUNNING',outcome['status']),'TERMINAL_CONFLICT')
        if row.status=='RUNNING':
            ledger.append(TrialRecord(**_identity(trial),status=outcome['status'],event_index=2,at=outcome['completed_at_ms']))
        else:
            require(row.events[-1].at==outcome['completed_at_ms'],'TERMINAL_TIME')
    if plan['adapter']==contract.GOLD:
        manifest=_manifest(plan)
        manifest.update(terminal=outcome['status'])
        if outcome['status']=='PASSED': manifest['report_sha256']=outcome['report_sha256']
        else: manifest['error_type']=outcome['error_type']
    else:
        manifest=_load(report)['detail']['run_manifest_v2'] if outcome['status']=='PASSED' else dict(
            schema='QUANTOS_TECHNICAL_FAILED_MANIFEST_V1',plan_sha256=plan_digest(plan),error_type=outcome['error_type'])
    _put(root/'archive'/'manifest.json',manifest,raw=gold._bytes(manifest) if plan['adapter']==contract.GOLD else canonical(manifest))
    require(_read(root/'registration.json')==canonical(registration_evidence(plan,registry)),'REGISTRATION_CHANGED')
    events=registry.search_attempt_events(plan['search']['search_id'])
    status='COMPLETED' if outcome['status']=='PASSED' else 'FAILED'
    if len(events)==1:
        registry.finish_search_attempt(plan['search']['search_id'],status=status,recorded_at=outcome['completed_at_ms'])
    events=registry.search_attempt_events(plan['search']['search_id'])
    require(len(events)==2 and events[-1]['status']==status and events[-1]['recorded_at']==outcome['completed_at_ms'],'SEARCH_TERMINAL')
    _rows(ledger,plan)


def run_gold(arguments, *, destination, registry, clock_ms=None):
    from quantos.contracts.gold_native_source import INPUT_SCHEMAS
    if arguments.get('input_schema') in INPUT_SCHEMAS:
        from tools.gold_native_source_run import run
        return run(arguments, destination=destination, registry=registry, clock_ms=clock_ms)
    arguments=copy.deepcopy(arguments)
    raw=_gold_input(arguments)
    def verify_source(plan):
        require(canonical(plan['source_identity'])==canonical(gold.source_identity())
                and canonical(arguments['release_identity'])==canonical(plan['source_identity']),'SOURCE_CHANGED')
        require([t['candidate_id'] for t in plan['trials']]==list(gold.trial_candidates(arguments)),'NATIVE_CANDIDATES')
    active_history = [None]
    def execute():
        context = {} if active_history[0] is None else {'history': active_history[0]}
        return gold._execute(arguments, **context)
    @contextmanager
    def execute_context(plan):
        if plan['schema'] not in contract.STREAMED_GOLD_SCHEMAS:
            yield
            return
        from tools.gold_stream_history import GoldResearchHistory
        # Private derived scratch is outside the immutable native run/cut.
        # Both successful and failed originals remain; recovery consumes the
        # durable complete report and never deserializes this scratch balance.
        scratch = Path(tempfile.mkdtemp(prefix='quantos-gold-history-'))
        with GoldResearchHistory(scratch/'accounts',
                maximum_history_bytes=plan['history_storage']['maximum_bytes']) as history:
            active_history[0] = history
            try:
                yield
            finally:
                active_history[0] = None
    return _native_run(destination=destination,input_raw=raw,registry=registry,
        clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000),adapter=contract.GOLD,
        execute=execute,verify_source=verify_source,execute_context=execute_context)
