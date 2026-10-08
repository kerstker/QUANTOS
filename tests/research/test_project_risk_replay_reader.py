"""Full managed originals, fixed source/trust and actual engineering replay."""
import copy
import dataclasses
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from quantos.contracts.research_evidence_json import canonical,digest
from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments
from tests.tools.test_registered_risk_replay import prepare,run
from tests.tools.test_registered_research_run import join
from tests.validation.test_project_research_evidence_authority import signed_project


def gold(value):
    # Native source inputs are converted before plan, manifest, or any execution.
    if dataclasses.is_dataclass(value):
        return dataclasses.replace(value,**{f.name:gold(getattr(value,f.name)) for f in dataclasses.fields(value)})
    if type(value) is dict:return {gold(k):gold(v) for k,v in value.items()}
    if type(value) in (tuple,list):return type(value)(gold(v) for v in value)
    return value.replace('BTCUSDT','XAUUSDT') if type(value) is str else value


@pytest.fixture
def risk_project(registered_case,technical_arguments):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    f=registered_case;args=gold(technical_arguments)
    plan=prepare(f,args);result=run(f,args,plan)
    reader=ProjectResearchEvidenceReader(f['root']/'project',registry_database=f['root']/'registry.db')
    return f,args,plan,result,reader


def test_pure_facts_retain_engineering_rejection_without_losing_attempt(risk_project):
    from services.validation import project_research_evidence_authority as authority
    from services.validation.project_research_population import validate_project_cut
    f,_,plan,_,reader=risk_project;cut=reader();validate_project_cut(cut)
    snapshot,_=signed_project(cut,plan['invocation_id'],now=f['clock'][0])
    assessment=authority.verify_project_evidence(snapshot,now_ms=f['clock'][0])
    facts=assessment['projection']
    assert facts['schema']=='QUANTOS_PROJECT_RESEARCH_FACTS_V3'
    assert facts['selected_attempt']['status']=='PASSED' and facts['attempt_count']==1
    assert facts['risk_replay']['status']=='REJECTED' and facts['risk_replay']['policy_preregistered'] is True
    assert assessment['qualification']=='REJECTED'
    assert 'MULTIPLE_TESTING_AND_BENCHMARK_REVIEW_NOT_COMPLETED' in facts['limitations']
    assert facts['promotion_approved'] is False


@pytest.mark.parametrize('damage',['missing','changed','old_version'])
def test_fixed_reader_rejects_missing_changed_or_posthoc_attachment(risk_project,damage):
    f,_,plan,_,reader=risk_project;directory=f['run'].run_directory(f['root']/'project',plan)
    path=directory/'archive/risk-replay.json'
    if damage=='missing':path.unlink()
    elif damage=='changed':
        value=json.loads(path.read_bytes());value['input_sha256']='0'*64;path.write_bytes(canonical(value))
    else:
        value=json.loads((directory/'plan.json').read_bytes());value['schema']='QUANTOS_REGISTERED_RESEARCH_RUN_V2'
        del value['risk_review_policy'];(directory/'plan.json').write_bytes(canonical(value))
    with pytest.raises(ValueError):reader()


@pytest.fixture
def fixed_risk_snapshot(registered_case,technical_arguments,monkeypatch):
    from tools import registered_technical_research_run as technical
    from tools.perpetual_replay_research import source_identity
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.research.project_research_evidence_snapshot import ProjectResearchEvidenceSnapshotReader
    f=registered_case;source=Path(technical.__file__).resolve().parents[1]
    if os.environ.get('QUANTOS_RISK_ISOLATED_SOURCE') != str(source):
        return None
    identity=source_identity();args=gold(technical_arguments);args['release_identity']=identity
    monkeypatch.setattr(technical,'source_identity',source_identity)
    plan=prepare(f,args);result=run(f,args,plan)
    files={p.relative_to(source).as_posix():digest(p.read_bytes()) for folder in ('services','quantos','tools')
           for p in (source/folder).rglob('*.py')}
    cut=ProjectResearchEvidenceReader(f['root']/'project',registry_database=f['root']/'registry.db')()
    manifest=dict(schema='QUANTOS_RESEARCH_SOURCE_MANIFEST_V1',source_identity_sha256=digest(identity),files=files)
    snapshot,_=signed_project(cut,plan['invocation_id'],now=f['clock'][0],source_manifest=manifest)
    material=f['root']/'materials';material.mkdir(mode=0o700)
    for key in ('subject','source_manifest','source_envelope','result_envelope'):
        p=material/(key+'.json');p.write_bytes(canonical(snapshot[key]));p.chmod(0o600)
    governance=f['root']/'governance';governance.mkdir(mode=0o700)
    trust=governance/'registry.json';trust.write_bytes(canonical(snapshot['trust_registry']));trust.chmod(0o600)
    reader=ProjectResearchEvidenceSnapshotReader(material,project_root=f['root']/'project',registry_database=f['root']/'registry.db',
        source_directory=source,source_commit=identity['commit'],producer_id='isolated-native',trust_registry=trust,
        expected_registry_sha256=digest(trust.read_bytes()),minimum_generation=1)
    return f,reader,snapshot,plan,result


def _isolated_test(tmp_path, name):
    original=Path(__file__).resolve().parents[2];destination=tmp_path/'isolated-source'
    subprocess.run(['git','clone','--quiet','--no-local',str(original),str(destination)],check=True,capture_output=True)
    # This candidate contains uncommitted new modules. Copy only code/tests, never
    # broker state, keys, environments, caches, or deployment material.
    for folder in ('services','quantos','tools','tests'):
        shutil.copytree(original/folder,destination/folder,dirs_exist_ok=True,
            ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for path in (destination,*destination.rglob('*')):
        if not path.is_symlink():path.chmod(path.stat().st_mode & ~0o022)
    environment=dict(os.environ,QUANTOS_RISK_ISOLATED_SOURCE=str(destination),PYTHONDONTWRITEBYTECODE='1')
    child=subprocess.run([sys.executable,'-m','pytest','-q',
        'tests/research/test_project_risk_replay_reader.py::'+name],cwd=destination,env=environment,
        text=True,capture_output=True,timeout=90)
    assert child.returncode==0, child.stdout+child.stderr


def test_fixed_snapshot_replays_after_real_source_check_then_double_reads(fixed_risk_snapshot,monkeypatch,tmp_path):
    if fixed_risk_snapshot is None:
        return _isolated_test(tmp_path,'test_fixed_snapshot_replays_after_real_source_check_then_double_reads')
    from services.execution import research_risk_replay as replay
    from services.validation.project_research_evidence_authority import consume_project_evidence
    f,reader,snapshot,_,_=fixed_risk_snapshot;actual=replay.replay_technical_risk;calls=[]
    def observe(**kw):calls.append(1);return actual(**kw)
    monkeypatch.setattr(replay,'replay_technical_risk',observe)
    before={p:p.read_bytes() for p in f['root'].rglob('*') if p.is_file()}
    assessment=consume_project_evidence(read_snapshot=reader,expected_subject=snapshot['subject'],clock_ms=lambda:f['clock'][0])
    assert calls==[1,1]
    assert assessment['qualification']=='REJECTED'
    assert before=={p:p.read_bytes() for p in f['root'].rglob('*') if p.is_file()}


def test_fixed_snapshot_refuses_retained_diagnostic_if_actual_replay_differs(fixed_risk_snapshot,monkeypatch,tmp_path):
    if fixed_risk_snapshot is None:
        return _isolated_test(tmp_path,'test_fixed_snapshot_refuses_retained_diagnostic_if_actual_replay_differs')
    from services.execution import research_risk_replay as replay
    _,reader,_,_,_=fixed_risk_snapshot;actual=replay.replay_technical_risk
    def different(**kw):
        value=copy.deepcopy(actual(**kw));value['input_sha256']='0'*64;return value
    monkeypatch.setattr(replay,'replay_technical_risk',different)
    with pytest.raises(ValueError,match='RISK'):reader()


@pytest.mark.parametrize('stage',['report.json','risk-replay.json'])
def test_real_partial_run_is_retained_without_inventing_terminal_facts(registered_case,technical_arguments,stage):
    from tools import registered_technical_research_run as tool
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation.project_research_evidence_authority import project_subject,project_facts
    from services.validation.project_research_population import validate_project_cut
    f=registered_case;args=gold(technical_arguments);plan=prepare(f,args)
    pid=os.fork()
    if pid==0:
        try:
            put=tool.common._put
            def exit_after(path,*a,**kw):
                result=put(path,*a,**kw)
                if path.name==stage:os._exit(71)
                return result
            tool.common._put=exit_after;run(f,args,plan)
        except BaseException:
            import traceback;traceback.print_exc();os._exit(98)
        os._exit(99)
    assert join(pid)==71
    cut=ProjectResearchEvidenceReader(f['root']/'project',registry_database=f['root']/'registry.db')()
    validate_project_cut(cut)
    partial=cut['runs'][plan['invocation_id']]['partial']
    assert 'OUTCOME_UNKNOWN' in partial
    assert ('RISK_REPLAY_NOT_PERSISTED' in partial)==(stage=='report.json')
    subject=project_subject(cut,invocation_id=plan['invocation_id'],native_trial_id=plan['trials'][0]['native_trial_id'])
    facts=project_facts(cut,subject)
    assert facts['attempt_count']==facts['unfinished_attempt_count']==1
    assert facts['metrics'] is None and facts['promotion_approved'] is False


def test_failed_and_unfinished_registered_attempts_remain_denominator(risk_project):
    from decimal import Decimal
    from services.validation.project_research_population import validate_project_cut
    f,args,_,_,reader=risk_project
    failed=copy.deepcopy(args);failed['initial_collateral']=Decimal('-1')
    plan=prepare(f,failed,invocation='8'*64,search_id='failed')
    with pytest.raises(ValueError):run(f,failed,plan)
    prepare(f,args,invocation='7'*64,search_id='not-started')
    cut=reader();validate_project_cut(cut)
    assert cut['attempt_count']==3
    assert {a['status'] for a in cut['attempts']}=={'PASSED','FAILED','NOT_REGISTERED'}
    assert cut['unique_definition_count']==1


def test_actual_fixed_replay_change_is_checked_again_at_end(fixed_risk_snapshot,monkeypatch,tmp_path):
    if fixed_risk_snapshot is None:
        return _isolated_test(tmp_path,'test_actual_fixed_replay_change_is_checked_again_at_end')
    from services.execution import research_risk_replay as replay
    f,reader,_,plan,_=fixed_risk_snapshot;actual=replay.replay_technical_risk
    path=f['run'].run_directory(f['root']/'project',plan)/'archive/risk-replay.json'
    def late_change(**kw):
        result=actual(**kw);raw=path.read_bytes();path.write_bytes(raw);return result
    monkeypatch.setattr(replay,'replay_technical_risk',late_change)
    with pytest.raises(ValueError,match='CHANGED'):reader()


def test_another_source_tree_cannot_authorize_this_executing_adapter(fixed_risk_snapshot,tmp_path):
    if fixed_risk_snapshot is None:
        return _isolated_test(tmp_path,'test_another_source_tree_cannot_authorize_this_executing_adapter')
    _,reader,_,_,_=fixed_risk_snapshot;different=tmp_path/'other-source';different.mkdir(mode=0o700)
    for folder in ('services','quantos','tools'):
        shutil.copytree(reader.source/folder,different/folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copyfile(reader.source/'requirements-ci-linux-aarch64.lock',different/'requirements-ci-linux-aarch64.lock')
    (different/'requirements-ci-linux-aarch64.lock').chmod(0o600)
    reader.source=different
    with pytest.raises(ValueError,match='RISK_EXECUTING_SOURCE'):reader()


def test_expired_statement_after_actual_second_replay_cannot_be_consumed(fixed_risk_snapshot,monkeypatch,tmp_path):
    if fixed_risk_snapshot is None:
        return _isolated_test(tmp_path,'test_expired_statement_after_actual_second_replay_cannot_be_consumed')
    from services.execution import research_risk_replay as replay
    from services.validation.project_research_evidence_authority import consume_project_evidence
    f,reader,snapshot,_,_=fixed_risk_snapshot;actual=replay.replay_technical_risk;calls=[]
    def elapsed(**kw):
        result=actual(**kw);calls.append(1)
        if len(calls)==2:f['clock'][0]+=1001
        return result
    monkeypatch.setattr(replay,'replay_technical_risk',elapsed)
    with pytest.raises(ValueError):
        consume_project_evidence(read_snapshot=reader,expected_subject=snapshot['subject'],clock_ms=lambda:f['clock'][0])
    assert calls==[1,1]


def test_v3_native_fixed_review_uses_original_store_audit_and_cold_read(fixed_risk_snapshot,tmp_path):
    if fixed_risk_snapshot is None:
        return _isolated_test(tmp_path,'test_v3_native_fixed_review_uses_original_store_audit_and_cold_read')
    from services.research.experiment_registry.store import SqliteExperimentStore
    from services.research.experiment_registry.registry import ExperimentRegistry
    f,reader,snapshot,_,_=fixed_risk_snapshot
    population=f['store'].research_evidence_population()
    f['store'].upgrade_research_evidence_schema(expected_version=f['store']._schema_version(),
        expected_population_sha256=population['sha256'])
    original=f['store']._conn.execute('SELECT * FROM search_attempt_events ORDER BY event_seq').fetchall()
    f['clock'][0]+=1
    def record():return f['registry'].record_project_research_evidence(reviewed_cut=snapshot['project_cut'],
        read_snapshot=reader.read_retained,expected_subject=snapshot['subject'],clock_ms=lambda:f['clock'][0])
    row=record();assert row['verdict']=='REJECTED'
    assert f['store']._conn.execute('SELECT count(*) FROM search_evaluations').fetchone()[0]==0
    assert f['store']._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0]==1
    assert f['store']._conn.execute('SELECT * FROM search_attempt_events ORDER BY event_seq').fetchall()==original
    f['clock'][0]+=1;assert record()==row
    cold=SqliteExperimentStore(str(f['root']/'registry.db'),create=False)
    try:
        registry=ExperimentRegistry(cold);retained=registry.get_project_research_evidence(row['run_id'])
        assert retained['current_admission_verified'] is False
        checked=registry.preflight_project_research_evidence(row['run_id'],read_snapshot=reader.read_retained,
            clock_ms=lambda:f['clock'][0])
        assert checked['qualification']=='REJECTED' and checked['projection']['risk_replay']['policy_preregistered'] is True
    finally:cold.close()


def test_bad_independent_trust_pin_does_not_enter_risk_engine(fixed_risk_snapshot,monkeypatch,tmp_path):
    if fixed_risk_snapshot is None:
        return _isolated_test(tmp_path,'test_bad_independent_trust_pin_does_not_enter_risk_engine')
    from services.execution import research_risk_replay as replay
    _,reader,_,_,_=fixed_risk_snapshot;actual=replay.replay_technical_risk;calls=[]
    reader.trust.write_bytes(reader.trust.read_bytes()+b'\n')
    def observe(**kw):calls.append(1);return actual(**kw)
    monkeypatch.setattr(replay,'replay_technical_risk',observe)
    with pytest.raises(ValueError,match='TRUST_PIN'):reader()
    assert calls==[]
