"""Original registration, native calculation and a separate diagnostic lifecycle."""
import copy
import json
import os

import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments, join
from tests.tools.test_registered_research_evaluation import declaration


def policy(core):
    from quantos.contracts.research_evaluation_plan import core_sha256
    return dict(schema='QUANTOS_REGISTERED_RISK_REVIEW_POLICY_V1',
        adapter='TECHNICAL_LINEAR_PERPETUAL_V2',input_sha256=core['input_sha256'],
        source_identity_sha256=core['source_identity_sha256'],evaluation_core_sha256=core_sha256(core),
        native_trial_id=core['cohort'][0]['native_trial_id'],report_schema='QUANTOS_TECHNICAL_RISK_REPLAY_V1',
        method='ORIGINAL_ENGINE_REPRODUCTION_AND_CANONICAL_LEDGER_COVERAGE',
        failure_policy='RETAIN_DIAGNOSTIC_WITHOUT_ECONOMIC_QUALIFICATION',
        submission_enabled=False,economic_authority=False)


def prepare(f,args,*,review=None,invocation='9'*64,search_id='risk-review'):
    from tools import registered_technical_research_run as tool
    core=declaration(f,args,invocation)
    return tool.prepare_technical(args,project_root=f['root']/'project',search_id=search_id,
        producer_id='isolated-native',actor_id='AUTOMATED_TEST',dataset_version='native-technical',
        registered_at_ms=f['clock'][0],invocation_id=invocation,evaluation_core=core,
        risk_review_policy=policy(core) if review is None else review)


def run(f,args,plan):
    from tools import registered_technical_research_run as tool
    return tool.run_technical(args,destination=f['run'].run_directory(f['root']/'project',plan),
        registry=f['registry'],clock_ms=lambda:f['clock'][0])


def test_real_native_risk_rejection_preserves_passed_computation(registered_case,technical_arguments):
    from services.research.trial_ledger import TrialLedger
    from quantos.contracts import registered_risk_replay as contract
    f=registered_case;plan=prepare(f,technical_arguments)
    result=run(f,technical_arguments,plan)
    directory=f['run'].run_directory(f['root']/'project',plan)
    artifact=json.loads((directory/'archive/risk-replay.json').read_bytes())
    assert plan['schema']=='QUANTOS_REGISTERED_RESEARCH_RUN_V3'
    assert artifact['replay']['status']=='REJECTED'  # Real original risk key mismatch.
    assert result['authoritative'] is False
    assert artifact['replay']['reproducibility']['mathematical_independence'] is False
    contract.validate_artifact(artifact,plan=plan,original_result=result,
        invocation=json.loads((directory/'invocation.json').read_bytes()))
    outcome=json.loads((directory/'outcome.json').read_bytes())
    assert outcome['status']=='PASSED' and outcome['risk_replay_sha256']==f['run'].plan_digest(artifact)
    with TrialLedger(str(directory/'archive/trials.db'),create=False,read_only=True) as ledger:
        assert ledger.load(plan['trials'][0]['native_trial_id']).status=='PASSED'
    before={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert run(f,technical_arguments,plan)==result
    assert before=={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}


@pytest.mark.parametrize('field',['input_sha256','source_identity_sha256','evaluation_core_sha256','native_trial_id','submission_enabled'])
def test_policy_mismatch_rejects_before_any_plan(registered_case,technical_arguments,field):
    f=registered_case;value=policy(declaration(f,technical_arguments))
    value[field]=True if field=='submission_enabled' else '0'*64
    with pytest.raises(ValueError):prepare(f,technical_arguments,review=value)
    assert not (f['root']/'project').exists()


@pytest.mark.parametrize('stage',['registration.json','RUNNING','invocation.json','report.json','risk-replay.json','outcome.json','PASSED','manifest.json'])
def test_real_fork_recovery_never_reinvokes_registered_computation(registered_case,technical_arguments,stage,monkeypatch):
    from tools import registered_technical_research_run as tool
    from services.research.trial_ledger import TrialLedger
    f=registered_case;plan=prepare(f,technical_arguments);directory=f['run'].run_directory(f['root']/'project',plan)
    calls=f['root']/'registered-executions'; original=tool.common._native_run
    def track(**kw):
        execute=kw['execute']
        def compute():
            with calls.open('ab') as stream:stream.write(b'x');stream.flush();os.fsync(stream.fileno())
            return execute()
        return original(**dict(kw,execute=compute))
    monkeypatch.setattr(tool.common,'_native_run',track)
    pid=os.fork()
    if pid==0:
        try:
            put=tool.common._put
            append=TrialLedger.append
            def appended(ledger,row):
                value=append(ledger,row)
                if row.status==stage:os._exit(71)
                return value
            TrialLedger.append=appended
            def stop(path,*args,**kw):
                result=put(path,*args,**kw)
                if path.name==stage:os._exit(71)
                return result
            tool.common._put=stop
            run(f,technical_arguments,plan)
        except BaseException:
            import traceback;traceback.print_exc();os._exit(98)
        os._exit(99)
    assert join(pid)==71
    if stage=='invocation.json':
        with pytest.raises(ValueError,match='UNKNOWN_INVOCATION_OUTCOME'):run(f,technical_arguments,plan)
        assert not calls.exists()
        return
    original_artifact=(directory/'archive/risk-replay.json').read_bytes() if (directory/'archive/risk-replay.json').exists() else None
    f['clock'][0]+=100
    result=run(f,technical_arguments,plan)
    assert result['authoritative'] is False and calls.read_bytes()==b'x'
    if original_artifact is not None:assert (directory/'archive/risk-replay.json').read_bytes()==original_artifact


def test_terminal_risk_attachment_loss_cannot_be_backfilled(registered_case,technical_arguments):
    f=registered_case;plan=prepare(f,technical_arguments);run(f,technical_arguments,plan)
    directory=f['run'].run_directory(f['root']/'project',plan)
    (directory/'archive/risk-replay.json').unlink()
    with pytest.raises(ValueError):run(f,technical_arguments,plan)
    assert not (directory/'archive/risk-replay.json').exists()


def test_actual_native_failure_stays_in_population_without_fake_risk_report(registered_case,technical_arguments):
    from decimal import Decimal
    f=registered_case;args=copy.deepcopy(technical_arguments);args['initial_collateral']=Decimal('-1')
    plan=prepare(f,args)
    with pytest.raises(ValueError):run(f,args,plan)
    directory=f['run'].run_directory(f['root']/'project',plan)
    assert not (directory/'archive/risk-replay.json').exists()
    outcome=json.loads((directory/'outcome.json').read_bytes())
    assert outcome['status']=='FAILED' and outcome['risk_replay_sha256'] is None


@pytest.mark.parametrize('stage',['risk-replay.json','outcome.json'])
@pytest.mark.parametrize('change',['source','input','clock'])
def test_last_publication_change_cannot_finalize_native_trial(registered_case,technical_arguments,monkeypatch,stage,change):
    from tools import registered_technical_research_run as tool
    from services.research.trial_ledger import TrialLedger
    f=registered_case;plan=prepare(f,technical_arguments);directory=f['run'].run_directory(f['root']/'project',plan)
    put=tool.common._put
    def mutate(path,*args,**kw):
        value=put(path,*args,**kw)
        if path.name==stage:
            if change=='source':
                monkeypatch.setattr(tool,'source_identity',lambda:dict(technical_arguments['release_identity'],python_source_sha256='0'*64))
            if change=='input':(directory/'input.json').write_bytes(b'{}')
            if change=='clock':f['clock'][0]-=1
        return value
    monkeypatch.setattr(tool.common,'_put',mutate)
    with pytest.raises(ValueError):run(f,technical_arguments,plan)
    with TrialLedger(str(directory/'archive/trials.db'),create=False,read_only=True) as ledger:
        assert ledger.load(plan['trials'][0]['native_trial_id']).status=='RUNNING'


def test_lost_terminal_outcome_cannot_get_new_timestamp(registered_case,technical_arguments):
    f=registered_case;plan=prepare(f,technical_arguments);run(f,technical_arguments,plan)
    directory=f['run'].run_directory(f['root']/'project',plan)
    (directory/'outcome.json').unlink();f['clock'][0]+=1
    with pytest.raises(ValueError):run(f,technical_arguments,plan)
    assert not (directory/'outcome.json').exists()


def test_risk_publication_io_error_preserves_successful_native_original(registered_case,technical_arguments,monkeypatch):
    from tools import registered_technical_research_run as tool
    f=registered_case;plan=prepare(f,technical_arguments);directory=f['run'].run_directory(f['root']/'project',plan)
    put=tool.common._put
    with monkeypatch.context() as patch:
        def failed(path,*args,**kw):
            value=put(path,*args,**kw)
            if path.name=='risk-replay.json':raise OSError('isolated post-fsync failure')
            return value
        patch.setattr(tool.common,'_put',failed)
        with pytest.raises(OSError):run(f,technical_arguments,plan)
    assert not (directory/'outcome.json').exists()
    original=(directory/'archive/report.json').read_bytes();risk=(directory/'archive/risk-replay.json').read_bytes()
    run(f,technical_arguments,plan)
    assert (directory/'archive/report.json').read_bytes()==original
    assert (directory/'archive/risk-replay.json').read_bytes()==risk


@pytest.mark.parametrize('version',[1,2])
def test_old_completed_versions_never_acquire_risk_attachment(registered_case,technical_arguments,version):
    from tools import registered_technical_research_run as tool
    f=registered_case
    core=declaration(f,technical_arguments) if version==2 else None
    plan=tool.prepare_technical(technical_arguments,project_root=f['root']/'project',search_id='risk-review',
        producer_id='isolated-native',actor_id='AUTOMATED_TEST',dataset_version='native-technical',
        registered_at_ms=f['clock'][0],invocation_id='9'*64,evaluation_core=core)
    record=run(f,technical_arguments,plan);directory=f['run'].run_directory(f['root']/'project',plan)
    before={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):prepare(f,technical_arguments)
    assert run(f,technical_arguments,plan)==record
    assert before=={p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert not (directory/'archive/risk-replay.json').exists()


@pytest.mark.parametrize('stage',['created','partial_write','written','file_fsync','directory_fsync'])
def test_attachment_physical_fork_keeps_originals_and_never_repairs_partial_bytes(registered_case,technical_arguments,stage):
    from tools import registered_technical_research_run as tool
    f=registered_case;plan=prepare(f,technical_arguments);directory=f['run'].run_directory(f['root']/'project',plan)
    target=directory/'archive/risk-replay.json'
    pid=os.fork()
    if pid==0:
        try:
            original_open=os.open;original_write=os.write;original_fsync=os.fsync;original_sync=tool.common._sync
            tracked=[]
            def opened(path,flags,*args,**kw):
                fd=original_open(path,flags,*args,**kw)
                if path==target and flags & os.O_CREAT:
                    tracked.append(fd)
                    if stage=='created':os._exit(71)
                return fd
            def written(fd,raw):
                if tracked==[fd] and stage=='partial_write':
                    original_write(fd,raw[:17]);original_fsync(fd);os._exit(71)
                count=original_write(fd,raw)
                if tracked==[fd] and stage=='written':os._exit(71)
                return count
            def synced(fd):
                original_fsync(fd)
                if tracked==[fd] and stage=='file_fsync':os._exit(71)
            def directory_sync(path):
                original_sync(path)
                if path==target.parent and target.exists() and stage=='directory_fsync':os._exit(71)
            os.open=opened;os.write=written;os.fsync=synced;tool.common._sync=directory_sync
            run(f,technical_arguments,plan)
        except BaseException:
            import traceback;traceback.print_exc();os._exit(98)
        os._exit(99)
    assert join(pid)==71
    original_report=(directory/'archive/report.json').read_bytes();risk_bytes=target.read_bytes()
    if stage in ('created','partial_write'):
        with pytest.raises(ValueError):run(f,technical_arguments,plan)
        assert not (directory/'outcome.json').exists()
    else:
        run(f,technical_arguments,plan)
        assert json.loads((directory/'outcome.json').read_bytes())['status']=='PASSED'
    assert target.read_bytes()==risk_bytes
    assert (directory/'archive/report.json').read_bytes()==original_report
