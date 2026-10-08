"""Native original registry and producers; no approved actors or authority."""
import copy
import json
import os
import signal
import time

import pytest

from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.ports.persistence import ExperimentStoreError
from services.research.trial_ledger import TrialLedger
from tests.tools.test_gold_bar_cost_replay import inputs
from tools import gold_bar_experiment as gold


def join(pid):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        child,status=os.waitpid(pid,os.WNOHANG)
        if child:return os.waitstatus_to_exitcode(status)
        time.sleep(.01)
    os.kill(pid,signal.SIGKILL)
    os.waitpid(pid,0)
    pytest.fail('isolated child exceeded 20 second test budget')


@pytest.fixture
def registered_case(tmp_path, monkeypatch):
    from tools import registered_research_run as run
    arguments = inputs()
    arguments['release_identity']=dict(commit='f'*40,python_source_sha256='1'*64,
        dependency_lock_sha256='2'*64,python_version='3.12.12',architecture='isolated')
    monkeypatch.setattr(gold, 'source_identity', lambda: arguments['release_identity'])
    store = SqliteExperimentStore(str(tmp_path/'registry.db'), create=True)
    registry = ExperimentRegistry(store)
    clock = [2_100_000_000_000]
    directories={}
    def prepare(name='one', **extra):
        plan=run.prepare_gold(arguments, project_root=tmp_path/'project', search_id=name,
            producer_id='isolated-native', actor_id='AUTOMATED_TEST', dataset_version='native-xau',
            registered_at_ms=clock[0], **extra)
        directories[name]=run.run_directory(tmp_path/'project',plan)
        return plan
    yield dict(root=tmp_path, run=run, arguments=arguments, store=store,
               registry=registry, clock=clock, prepare=prepare,directories=directories)
    store.close()


def execute(f, name='one'):
    return f['run'].run_gold(f['arguments'], destination=f['directories'][name],
                           registry=f['registry'], clock_ms=lambda: f['clock'][0])


def test_two_searches_two_native_ledgers_repeated_config_are_real_attempts(registered_case):
    f=registered_case
    plans=[f['prepare'](name) for name in ('one','two')]
    outputs=[execute(f,name) for name in ('one','two')]
    assert set(outputs[0]['trial_ids']).isdisjoint(outputs[1]['trial_ids'])
    assert [v['config_hash'] for v in plans[0]['trials']]==[v['config_hash'] for v in plans[1]['trials']]
    for name, plan in zip(('one','two'), plans):
        assert len(f['registry'].search_candidates(name))==3
        with TrialLedger(str(f['directories'][name]/'archive'/'trials.db'),create=False,read_only=True) as ledger:
            ledger.verify()
            for item in plan['trials']:
                native=ledger.load(item['native_trial_id'])
                provenance=f['registry'].trial_provenance(item['provenance_trial_id'])
                assert native.status=='PASSED'
                assert provenance['record']['started_at']<=native.events[0].at
                assert provenance['record']['artifact_hashes'][0]==f['run'].plan_digest(plan)
        assert gold.verify(f['directories'][name]/'archive')['status']=='RECOMPUTED_NOT_INDEPENDENTLY_APPROVED'
    population=f['store'].research_evidence_population()
    assert len(population['trials'])==3
    assert len(population['provenance'])==6
    assert len(population['search_candidates'])==6
    assert execute(f)==outputs[0]


def test_failed_original_producer_keeps_registered_and_failed_population(registered_case,monkeypatch):
    f=registered_case; plan=f['prepare']()
    f['arguments']['bar_receipt']['response_sha256']='0'*64
    # A changed caller cannot alter the already frozen attempt.
    with pytest.raises(ValueError): execute(f)
    f['arguments']['bar_receipt']['response_sha256']=inputs()['bar_receipt']['response_sha256']
    original=gold._execute
    def fail(value):
        actual=copy.deepcopy(value); actual['bar_receipt']['response_sha256']='0'*64
        return original(actual)
    monkeypatch.setattr(gold,'_execute',fail)
    with pytest.raises(ValueError): execute(f)
    with TrialLedger(str(f['directories']['one']/'archive'/'trials.db'),create=False,read_only=True) as ledger:
        assert all(ledger.load(t['native_trial_id']).status=='FAILED' for t in plan['trials'])
    assert len(f['registry'].search_candidates('one'))==3
    monkeypatch.setattr(gold,'_execute',lambda _:pytest.fail('terminal failure must not rerun'))
    with pytest.raises(ValueError,match='TERMINAL_FAILURE'): execute(f)


def test_real_technical_runner_uses_original_ledger_and_registration(tmp_path,monkeypatch):
    from tools import registered_technical_research_run as run
    from tests.execution import test_authoritative_perpetual_backtest as native
    from services.execution.authoritative_perpetual_backtest import run_authoritative_linear_perpetual_v2
    captured={}
    monkeypatch.setattr(native,'run_authoritative_linear_perpetual_v2',lambda **kw:captured.update(kw))
    native._run()
    captured['release_identity']=dict(commit='f'*40,python_source_sha256='1'*64,
        dependency_lock_sha256='2'*64,python_version='3.12.12',architecture='isolated')
    monkeypatch.setattr(run,'source_identity',lambda:captured['release_identity'])
    store=SqliteExperimentStore(str(tmp_path/'registry.db'),create=True)
    try:
        registry=ExperimentRegistry(store)
        plan=run.prepare_technical(captured,project_root=tmp_path/'project',search_id='technical',
            producer_id='isolated-native',actor_id='AUTOMATED_TEST',dataset_version='native-btc',
            registered_at_ms=2_100_000_000_000)
        destination=run.common.run_directory(tmp_path/'project',plan)
        result=run.run_technical(captured,destination=destination,registry=registry,
                                clock_ms=lambda:2_100_000_000_000)
        assert result==run_authoritative_linear_perpetual_v2(**captured)
        assert result['authoritative'] is False
        assert result['detail']['engine_attestations']['risk_history_ledger_replayed'] is False
        with TrialLedger(str(destination/'archive'/'trials.db'),create=False,read_only=True) as ledger:
            assert ledger.load(plan['trials'][0]['native_trial_id']).status=='PASSED'
        assert run.run_technical(captured,destination=destination,registry=registry,
            clock_ms=lambda:2_100_000_000_001)==result
    finally: store.close()


FORK_STAGES=('plan.json','input.json','search','candidates','provenance','registration.json',
    'archive/input.json','ledger','PLANNED','RUNNING','running.json','invocation.json',
    'producer_return','report.json','outcome.json','PASSED','manifest.json','search_finish')


@pytest.mark.parametrize('stage',FORK_STAGES)
def test_real_fork_same_plan_never_creates_second_native_invocation(registered_case,stage):
    f=registered_case; identity='a'*64
    directory=f['root']/'project'/'runs'/identity
    calls=f['root']/'calls'
    pid=os.fork()
    if pid==0:
        try:
            original_put=f['run']._put
            def put(path,*args,**kwargs):
                value=original_put(path,*args,**kwargs)
                key='archive/input.json' if path.name=='input.json' and path.parent.name=='archive' else path.name
                if key==stage: os._exit(71)
                return value
            f['run']._put=put
            for method,label in (('begin_search_attempt','search'),('register_search_candidates','candidates'),
                                 ('record_trial_provenance','provenance'),('finish_search_attempt','search_finish')):
                original=getattr(f['registry'],method)
                def wrapped(*a,_fn=original,_label=label,**kw):
                    result=_fn(*a,**kw)
                    if stage==_label: os._exit(71)
                    return result
                setattr(f['registry'],method,wrapped)
            original_init=TrialLedger.__init__
            def init(self,*a,**kw):
                original_init(self,*a,**kw)
                if stage=='ledger': os._exit(71)
            TrialLedger.__init__=init
            original_append=TrialLedger.append
            def append(self,record):
                value=original_append(self,record)
                if record.status==stage: os._exit(71)
                return value
            TrialLedger.append=append
            original_execute=gold._execute
            def actual(arguments):
                with calls.open('ab') as handle: handle.write(b'x');handle.flush();os.fsync(handle.fileno())
                value=original_execute(arguments)
                if stage=='producer_return':os._exit(71)
                return value
            gold._execute=actual
            f['prepare'](invocation_id=identity)
            execute(f)
            os._exit(99)
        except BaseException:
            import traceback
            traceback.print_exc()
            os._exit(98)
    assert join(pid)==71
    before_rows=f['registry'].search_attempt_events('one')
    prefix=None
    if (directory/'archive'/'trials.db').exists():
        with TrialLedger(str(directory/'archive'/'trials.db'),create=False,read_only=True) as ledger:
            prefix=list(ledger._c.execute('SELECT * FROM trial_events ORDER BY global_seq'))
    f['prepare'](invocation_id=identity)
    original_execute=gold._execute
    def actual(arguments):
        with calls.open('ab') as handle: handle.write(b'x');handle.flush();os.fsync(handle.fileno())
        return original_execute(arguments)
    gold._execute=actual
    try:
        if stage in ('invocation.json','producer_return'):
            with pytest.raises(ValueError,match='UNKNOWN_INVOCATION_OUTCOME'): execute(f)
            assert calls.read_bytes()==b'x' if stage=='producer_return' else not calls.exists()
        else:
            result=execute(f)
            assert len(result['trial_ids'])==3
            assert calls.read_bytes()==b'x'
            assert execute(f)==result and calls.read_bytes()==b'x'
    finally: gold._execute=original_execute
    assert f['registry'].search_attempt_events('one')[:len(before_rows)]==before_rows
    if prefix is not None:
        with TrialLedger(str(directory/'archive'/'trials.db'),create=False,read_only=True) as ledger:
            assert list(ledger._c.execute('SELECT * FROM trial_events ORDER BY global_seq'))[:len(prefix)]==prefix


def test_registration_is_observed_before_each_real_native_planned(registered_case,monkeypatch):
    f=registered_case;plan=f['prepare']();original=TrialLedger.append;seen=[]
    def append(ledger,row):
        if row.status=='PLANNED':
            receipt=f['run'].registration_evidence(plan,f['registry'])
            assert len(receipt['candidates'])==len(receipt['provenance'])==3
            assert (f['directories']['one']/'registration.json').is_file()
            seen.append(row.trial_id)
        return original(ledger,row)
    monkeypatch.setattr(TrialLedger,'append',append)
    execute(f)
    assert seen==[t['native_trial_id'] for t in plan['trials']]


def test_same_search_different_plan_conflict_does_not_add_native_attempt(registered_case):
    f=registered_case;first=f['prepare']();execute(f)
    second=f['prepare']()
    assert second['invocation_id']!=first['invocation_id']
    with pytest.raises(ValueError,match='SEARCH_EXISTING'):execute(f)
    assert not (f['directories']['one']/'archive').exists()
    assert len(f['registry'].trial_provenance_for_config(first['trials'][0]['config_hash']))==1


def test_plan_candidate_set_cannot_disagree_with_native_input(registered_case):
    f=registered_case;plan=f['prepare']();path=f['directories']['one']/'plan.json'
    # Self-consistent plan hashes still cannot drop one actual native candidate.
    plan['trials']=plan['trials'][:-1]
    path.write_bytes(f['run'].canonical(plan))
    with pytest.raises(ValueError,match='NATIVE_CANDIDATES'):execute(f)
    assert not f['registry'].search_attempt_events('one')


@pytest.mark.parametrize('leaf',('registration.json','invocation.json','archive/trials.db'))
def test_missing_completed_original_is_not_reconstructed(registered_case,leaf):
    f=registered_case;f['prepare']();execute(f)
    (f['directories']['one']/leaf).unlink()
    with pytest.raises((ValueError,OSError)):execute(f)


@pytest.mark.parametrize('stage',('outcome.json','FAILED','manifest.json','search_finish'))
def test_real_failed_fork_finishes_same_failed_attempt_without_rerun(registered_case,stage):
    f=registered_case
    # The bad request exists before registration, and the real original
    # producer rejects it. No synthetic PASSED report stands in for failure.
    f['arguments']['bar_receipt']['response_sha256']='0'*64
    plan=f['prepare']();root=f['directories']['one']
    pid=os.fork()
    if pid==0:
        try:
            put=f['run']._put;append=TrialLedger.append;finish=f['registry'].finish_search_attempt
            def wrapped(path,*a,**kw):
                result=put(path,*a,**kw)
                if path.name==stage:os._exit(71)
                return result
            def appended(ledger,record):
                result=append(ledger,record)
                if record.status==stage:os._exit(71)
                return result
            def finished(*a,**kw):
                result=finish(*a,**kw)
                if stage=='search_finish':os._exit(71)
                return result
            f['run']._put=wrapped;TrialLedger.append=appended;f['registry'].finish_search_attempt=finished
            execute(f)
        except BaseException:os._exit(98)
        os._exit(99)
    assert join(pid)==71
    with pytest.raises(ValueError,match='TERMINAL_FAILURE'):execute(f)
    assert json.loads((root/'outcome.json').read_bytes())['status']=='FAILED'
    with TrialLedger(str(root/'archive'/'trials.db'),create=False,read_only=True) as ledger:
        assert all(ledger.load(t['native_trial_id']).status=='FAILED' for t in plan['trials'])
    assert len(f['registry'].search_attempt_events('one'))==2


@pytest.mark.parametrize('change',('time','source','input'))
def test_invalid_current_inputs_do_not_register_any_native_population(registered_case,monkeypatch,change):
    f=registered_case;f['prepare']()
    if change=='time':f['clock'][0]-=1
    if change=='source':monkeypatch.setattr(gold,'source_identity',lambda:dict(f['arguments']['release_identity'],python_source_sha256='3'*64))
    if change=='input':f['arguments']['quantity']*=2
    with pytest.raises(ValueError):execute(f)
    assert not f['registry'].search_attempt_events('one')
    assert not (f['directories']['one']/'archive').exists()


def test_registry_loss_after_registration_is_not_recreated(registered_case):
    f=registered_case;f['prepare']();execute(f)
    replacement=SqliteExperimentStore(str(f['root']/'empty.db'),create=True)
    try:
        f['registry']=ExperimentRegistry(replacement)
        with pytest.raises(ExperimentStoreError):execute(f)
        assert f['registry'].search_attempt_events('one')==[]
    finally:replacement.close()


@pytest.mark.parametrize('leaf',('input.json','archive/trials.db'))
def test_same_bytes_hardlinked_original_is_rejected(registered_case,leaf):
    f=registered_case;f['prepare']();execute(f)
    path=f['directories']['one']/leaf
    os.link(path,f['root']/'other-link')
    with pytest.raises(ValueError):execute(f)


def test_native_manifest_material_helper_is_same_original_identity(monkeypatch):
    from services.execution import authoritative_perpetual_backtest as producer
    from tests.execution import test_authoritative_perpetual_backtest as fixture
    from quantos.contracts.backtest_identity import exact_identity_hash
    captured={}; actual=producer.build_run_manifest_v2
    def build(**kwargs):
        captured.update({k:kwargs[k] for k in ('release_identity','data_identity','universe')})
        return actual(**kwargs)
    monkeypatch.setattr(producer,'build_run_manifest_v2',build)
    inputs={}; original=fixture.run_authoritative_linear_perpetual_v2
    def run(**kwargs):
        inputs.update(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(fixture,'run_authoritative_linear_perpetual_v2',run)
    record=fixture._run()
    assert record['run_id']=='9098434783ec4cc04e42026724b01cc742670220dc6b36be2c6ebeda1d431171'
    assert record['detail']['result_hash']=='3f0a3561e4490fe663440683864e29289a86e75df5c2f38dfbbaa13523e95fe3'
    value=producer.research_manifest_inputs(**{k:inputs[k] for k in
        ('release_identity','dataset_identity','universe_snapshot','decisions','market_events','contract_rules')})
    assert exact_identity_hash(value)==exact_identity_hash(captured)
    assert record['detail']['run_manifest_v2']['data_hash']==exact_identity_hash(value['data_identity'])


@pytest.fixture
def technical_arguments(monkeypatch):
    from tests.execution import test_authoritative_perpetual_backtest as fixture
    from tools import registered_technical_research_run as tool
    captured={}
    monkeypatch.setattr(fixture,'run_authoritative_linear_perpetual_v2',lambda **kwargs:captured.update(kwargs))
    fixture._run()
    captured['release_identity']=dict(commit='f'*40,python_source_sha256='1'*64,
        dependency_lock_sha256='2'*64,python_version='3.12.12',architecture='isolated')
    monkeypatch.setattr(tool,'source_identity',lambda:captured['release_identity'])
    return captured


def test_full_technical_transport_derives_original_projection_without_native_import(technical_arguments):
    from quantos.contracts.registered_technical_input import project_manifest_inputs
    from quantos.contracts.backtest_identity import decode_identity_payload,exact_identity_hash
    from tools import registered_technical_research_run as tool
    from services.execution import authoritative_perpetual_backtest as native
    document=tool.technical_input(technical_arguments)
    record=native.run_authoritative_linear_perpetual_v2(**technical_arguments)
    projected=project_manifest_inputs(document,manifest=record['detail']['run_manifest_v2'])
    expected=native.research_manifest_inputs(**{k:technical_arguments[k] for k in
        ('release_identity','dataset_identity','universe_snapshot','decisions','market_events','contract_rules')})
    assert projected['schema']=='QUANTOS_TECHNICAL_PERPETUAL_INPUT_V1'
    assert exact_identity_hash({k:decode_identity_payload(v) for k,v in projected['values'].items()})==exact_identity_hash(expected)


@pytest.mark.parametrize('change',('tag','duplicate','unknown_type','fields','projection','universe','alias'))
def test_complete_technical_description_rejects_unknown_or_ambiguous_transport(technical_arguments,change):
    from quantos.contracts.registered_technical_input import project_manifest_inputs
    from quantos.contracts.backtest_identity import encode_identity_payload
    from tools import registered_technical_research_run as tool
    value=tool.technical_input(technical_arguments)
    pairs=value['arguments'][1]
    universe=next(pair[1] for pair in pairs if pair[0]==['value',['str','universe_snapshot']])
    if change=='tag':value['arguments'][0]='pickle'
    if change=='duplicate':pairs.append(copy.deepcopy(pairs[0]))
    if change=='unknown_type':universe[1]='arbitrary.ExecutableConstructor'
    if change=='fields':universe[2].pop()
    if change=='projection':value['manifest_inputs']['release_identity']=encode_identity_payload({'commit':'0'*40})
    if change=='universe':value['manifest_inputs']['universe']=encode_identity_payload({'value':['OTHER']})
    if change=='alias':pairs.extend([[['value',['bool',False]],['value',['none']]], [['value',['int','0']],['value',['none']]]])
    with pytest.raises(ValueError):project_manifest_inputs(value)


@pytest.mark.parametrize('stage',('registration.json','RUNNING','invocation.json','report.json','outcome.json','PASSED','manifest.json'))
def test_technical_native_fork_uses_same_plan_trial_and_original_result(tmp_path,technical_arguments,stage):
    from tools import registered_technical_research_run as tool
    common=tool.common
    store=SqliteExperimentStore(str(tmp_path/'registry.db'),create=True)
    try:
        registry=ExperimentRegistry(store);now=2_100_000_000_000
        plan=tool.prepare_technical(technical_arguments,project_root=tmp_path/'project',search_id='technical',
            producer_id='isolated-native',actor_id='AUTOMATED_TEST',dataset_version='native-btc',registered_at_ms=now)
        directory=common.run_directory(tmp_path/'project',plan);calls=tmp_path/'calls'
        pid=os.fork()
        if pid==0:
            try:
                put=common._put;append=TrialLedger.append;actual=tool.native.run_authoritative_linear_perpetual_v2
                def written(path,*a,**kw):
                    value=put(path,*a,**kw)
                    if path.name==stage:os._exit(71)
                    return value
                def appended(ledger,row):
                    value=append(ledger,row)
                    if row.status==stage:os._exit(71)
                    return value
                def run(**kwargs):
                    with calls.open('ab') as stream:stream.write(b'x');stream.flush();os.fsync(stream.fileno())
                    return actual(**kwargs)
                common._put=written;TrialLedger.append=appended
                tool.native.run_authoritative_linear_perpetual_v2=run
                tool.run_technical(technical_arguments,destination=directory,registry=registry,clock_ms=lambda:now)
            except BaseException:
                import traceback;traceback.print_exc();os._exit(98)
            os._exit(99)
        assert join(pid)==71
        actual=tool.native.run_authoritative_linear_perpetual_v2
        def run(**kwargs):
            with calls.open('ab') as stream:stream.write(b'x');stream.flush();os.fsync(stream.fileno())
            return actual(**kwargs)
        tool.native.run_authoritative_linear_perpetual_v2=run
        try:
            if stage=='invocation.json':
                with pytest.raises(ValueError,match='UNKNOWN_INVOCATION_OUTCOME'):
                    tool.run_technical(technical_arguments,destination=directory,registry=registry,clock_ms=lambda:now)
                assert not calls.exists()
            else:
                value=tool.run_technical(technical_arguments,destination=directory,registry=registry,clock_ms=lambda:now)
                assert value==actual(**technical_arguments)
                assert tool.run_technical(technical_arguments,destination=directory,registry=registry,clock_ms=lambda:now)==value
                assert calls.read_bytes()==b'x'
                with TrialLedger(str(directory/'archive'/'trials.db'),create=False,read_only=True) as ledger:
                    assert ledger.load(plan['trials'][0]['native_trial_id']).status=='PASSED'
        finally:tool.native.run_authoritative_linear_perpetual_v2=actual
    finally:store.close()


def test_actual_technical_native_failure_keeps_original_registered_trial(tmp_path,technical_arguments):
    from decimal import Decimal
    from tools import registered_technical_research_run as tool
    arguments=dict(technical_arguments,initial_collateral=Decimal('-1'))
    store=SqliteExperimentStore(str(tmp_path/'registry.db'),create=True)
    try:
        registry=ExperimentRegistry(store);now=2_100_000_000_000
        plan=tool.prepare_technical(arguments,project_root=tmp_path/'project',search_id='technical',
            producer_id='isolated-native',actor_id='AUTOMATED_TEST',dataset_version='native-btc',registered_at_ms=now)
        directory=tool.common.run_directory(tmp_path/'project',plan)
        with pytest.raises(ValueError):tool.run_technical(arguments,destination=directory,registry=registry,clock_ms=lambda:now)
        with TrialLedger(str(directory/'archive'/'trials.db'),create=False,read_only=True) as ledger:
            assert ledger.load(plan['trials'][0]['native_trial_id']).status=='FAILED'
        assert len(registry.search_candidates('technical'))==1
        assert registry.trial_provenance(plan['trials'][0]['provenance_trial_id']) is not None
    finally:store.close()


@pytest.mark.parametrize('identity',(False,0,True,'../run','A'*64))
def test_explicit_invalid_invocation_is_not_replaced_with_a_new_attempt(registered_case,identity):
    f=registered_case
    with pytest.raises(ValueError):f['prepare'](invocation_id=identity)
    assert not (f['root']/'project').exists()


def test_gold_raw_input_budget_precedes_base64_and_native_encoder(registered_case,monkeypatch):
    f=registered_case
    monkeypatch.setattr(f['run'].contract,'MAX_INPUT_BYTES',1024)
    monkeypatch.setattr(gold,'_encode',lambda _:pytest.fail('must reject before raw byte expansion'))
    with pytest.raises(ValueError,match='INPUT_BYTES'):f['prepare']()


def test_registration_wire_is_explicit_thin_gold_entrypoint(registered_case):
    f=registered_case;f['prepare']()
    value=gold.registered_archive(f['arguments'],f['directories']['one'],registry=f['registry'],clock_ms=lambda:f['clock'][0])
    assert value==execute(f)


@pytest.mark.parametrize('phase',('directory_sync','readback'))
def test_durable_native_report_io_error_remains_recoverable_without_false_failed_outcome(registered_case,monkeypatch,phase):
    f=registered_case;f['prepare']();root=f['directories']['one'];seen=[]
    actual=gold._execute
    def run(arguments):seen.append(True);return actual(arguments)
    monkeypatch.setattr(gold,'_execute',run)
    original_sync=f['run']._sync;original_read=f['run']._read;injected=[]
    def sync(directory):
        original_sync(directory)
        if phase=='directory_sync' and directory==root/'archive' and (directory/'report.json').exists() and not injected:
            injected.append(True);raise OSError('isolated post-report directory sync fault')
    def read(path,**kwargs):
        result=original_read(path,**kwargs)
        if phase=='readback' and path==root/'archive'/'report.json' and not injected:
            injected.append(True);raise OSError('isolated post-report readback fault')
        return result
    monkeypatch.setattr(f['run'],'_sync',sync);monkeypatch.setattr(f['run'],'_read',read)
    with pytest.raises((ValueError,OSError)):execute(f)
    assert injected and (root/'archive'/'report.json').exists()
    assert not (root/'outcome.json').exists(), 'storage failure must not forge a native FAILED outcome'
    value=execute(f)
    assert value['status']=='TECHNICAL_COMPUTATION_COMPLETE' and seen==[True]


def test_fifo_fixed_input_rejects_before_blocking_for_a_writer(registered_case):
    f=registered_case;f['prepare']();path=f['directories']['one']/'input.json'
    path.unlink();os.mkfifo(path,0o600)
    pid=os.fork()
    if pid==0:
        signal.alarm(2)
        try:execute(f)
        except (ValueError,OSError):os._exit(71)
        os._exit(99)
    assert join(pid)==71
