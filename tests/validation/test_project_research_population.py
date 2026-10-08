"""Semantic project cuts retain failed and separate real producer attempts."""
import copy
import pytest


def test_original_domain_exports_share_neutral_objects():
    from quantos.contracts import trial_provenance as neutral
    from services.research import trial_provenance as original
    assert neutral.TrialProvenance is original.TrialProvenance
    assert neutral.ActorType is original.ActorType
    assert neutral.trial_provenance_for_config is original.trial_provenance_for_config


def test_original_registry_hashes_share_neutral_implementation():
    from quantos.contracts import research_registry_identity as neutral
    from services.research.experiment_registry import family_schema_v2_semantics as original
    assert original.search_event_hash is neutral.search_event_hash
    assert original.canonical_hash is neutral.canonical_hash


def test_original_receipt_identity_is_available_without_research_imports():
    from quantos.contracts import trial_receipt_identity as neutral
    assert callable(neutral.receipt_row_hash)


def test_original_hash_preimages_are_unchanged():
    from quantos.contracts import research_registry_identity as h
    from quantos.contracts.trial_provenance import trial_provenance_for_config
    from quantos.contracts.trial_receipt_identity import receipt_row_hash
    assert trial_provenance_for_config('a'*64,research_session_id='搜索',actor_id='automated',recorded_at=100).entry_hash('') == '88c50a63a473de8b965eb03959705d17bc32312f53648c57cc976717c3564140'
    assert h.search_event_hash(search_id='搜索',search_kind='FORWARD',source='producer',dataset_version='data',status='STARTED',recorded_at=100,prev_event_hash='') == '23355d43c484d22c2523aa0c8fc19e7a7de1219d242309a441e4d17e82fe54bd'
    assert h.search_candidate_hash(search_id='搜索',config_hash='a'*64,status='REGISTERED',registered_at=100,prev_candidate_hash='') == '46ff0367fd0a2e3322b4e94f27efad8539c208d17a784c860550a287ded0d718'
    assert h.search_heartbeat_hash(search_id='搜索',recorded_at=101,prev_heartbeat_hash='') == 'c6ee813e2b86bc743011f6aa386e75def6e47a8c08dcf5e83c920cb4ce12f9ab'
    assert h.search_identity_hash(search_id='搜索',search_kind='FORWARD',source='producer',dataset_version='data') == 'ce2b3359873603746ed5337f054d2ac9cd7180c395ec5ab621f939f6e117d7ca'
    assert h.trial_oos_access_hash(trial_id='a'*64,method='read',boundary='锁定',viewed_at=100,prev_entry_hash='') == '896e56fbf1ed72f50777235142cafc69742844ad2e0d91188d5ce2892986605a'
    assert receipt_row_hash(trial_id='a'*64,stage='cheap',event_index=0,state='CLAIMED',payload_hash='b'*64,global_seq=1,prev_hash='',owner='原validator',lease_until=130,attempt=1) == 'aeddac343b8d9337c5fb8792b564ad449398478231f69444f00ed00f4149d170'


@pytest.fixture(scope='module')
def native_project(tmp_path_factory):
    from tests.tools.test_registered_research_run import registered_case, execute, technical_arguments
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from tools import gold_bar_experiment as gold, registered_technical_research_run as technical
    patch=pytest.MonkeyPatch(); tmp=tmp_path_factory.mktemp('population-native')
    generator=registered_case.__wrapped__(tmp,patch); f=next(generator)
    try:
        for name in ('one','two'):
            f['prepare'](name); execute(f,name)
        f['prepare']('failed'); original=gold._execute
        def bad(value):
            value=copy.deepcopy(value); value['bar_receipt']['response_sha256']='0'*64
            return original(value)
        patch.setattr(gold,'_execute',bad)
        with pytest.raises(ValueError): execute(f,'failed')
        patch.setattr(gold,'_execute',original)
        args=technical_arguments.__wrapped__(patch)
        for name in ('technical','technical-repeat'):
            plan=technical.prepare_technical(args,project_root=tmp/'project',search_id=name,
                producer_id='isolated-native',actor_id='AUTOMATED_TEST',dataset_version='native-technical',registered_at_ms=f['clock'][0])
            technical.run_technical(args,destination=f['run'].run_directory(tmp/'project',plan),
                registry=f['registry'],clock_ms=lambda:f['clock'][0])
        reader=ProjectResearchEvidenceReader(tmp/'project',registry_database=tmp/'registry.db')
        full=reader()
        f['prepare']('not-started')
        partial=reader()
        f['registry'].begin_search_attempt(search_id='outside-managed',search_kind='FORWARD',source='original',dataset_version='native',recorded_at=f['clock'][0])
        unmapped=reader()
        yield full,partial,unmapped
    finally:
        generator.close();patch.undo()


def rehash(cut):
    from quantos.contracts.research_evidence_json import digest
    p=cut['registry_population'];p['sha256']=digest({k:v for k,v in p.items() if k!='sha256'})
    cut['cut_sha256']=digest({k:v for k,v in cut.items() if k not in ('cut_sha256','read_identity_sha256')})
    return cut


def test_real_gold_failure_and_repeated_technical_runs_are_independent(native_project):
    from services.validation.project_research_population import validate_project_cut
    full,partial,unmapped=native_project
    assert validate_project_cut(full)==full
    assert full['attempt_count']==11 and full['unique_definition_count']==4
    assert sum(x['status']=='FAILED' for x in full['attempts'])==3
    assert validate_project_cut(partial)==partial and partial['all_runs_finalized'] is False
    assert validate_project_cut(unmapped)==unmapped and unmapped['unmapped']['searches']==['outside-managed']


@pytest.mark.parametrize('mutation',[
    'count','status','head','event','global_seq','event_bool','trial_duplicate','provenance',
    'candidate','identity','search_event','definition_count','partial','authority','original',
    'plan','unmapped','receipt_phantom','unknown_fields','input_missing','receipt_hash',
])
def test_outer_hash_cannot_hide_changed_native_facts(native_project,mutation):
    from services.validation.project_research_population import validate_project_cut
    value=copy.deepcopy(native_project[0]); run=next(iter(value['runs'].values()))
    rows=next(iter(run['ledger']['history'].values()))
    population=value['registry_population']
    if mutation=='count': value['attempt_count']-=1
    elif mutation=='status': value['attempts'][0]['status']='PASSED' if value['attempts'][0]['status']=='FAILED' else 'FAILED'
    elif mutation=='head': run['ledger']['head_hash']='0'*64
    elif mutation=='event': rows[0]['at']+=1
    elif mutation=='global_seq': rows[0]['global_seq']=2
    elif mutation=='event_bool': rows[0]['event_index']=False
    elif mutation=='trial_duplicate': run['ledger']['history']['0'*64]=copy.deepcopy(rows)
    elif mutation=='provenance': population['provenance'][0]['actor_id']='changed'
    elif mutation=='candidate': population['search_candidates'][0]['registered_at']+=1
    elif mutation=='identity': population['search_candidates'][0]['search_identity']['source']='changed'
    elif mutation=='search_event': value['registry_history']['events'][0]['source']='changed'
    elif mutation=='definition_count': value['unique_definition_count']=3
    elif mutation=='partial': run['partial']=['OUTCOME_UNKNOWN']
    elif mutation=='authority': value['economic_authority']=True
    elif mutation=='original': run['originals']['input.json']['sha256']='0'*64
    elif mutation=='plan': run['plan']['actor_id']='changed'
    elif mutation=='unmapped': value['unmapped']['definitions']=['0'*64]
    elif mutation=='receipt_phantom': run['ledger']['validation_receipts']=[{}]
    elif mutation=='unknown_fields': run['extra']=True
    elif mutation=='input_missing': del run['originals']['input.json']
    elif mutation=='receipt_hash': run['ledger']['validation_receipt_head']['head_hash']='0'*64
    with pytest.raises(ValueError): validate_project_cut(rehash(value))


from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.research.test_project_research_evidence_reader import project as project, material as material


@pytest.mark.parametrize('stage', ['plan.json','input.json','registration.json','PLANNED','RUNNING',
    'running.json','invocation.json','report.json','outcome.json','PASSED','manifest.json'])
def test_actual_producer_fork_cut_classification_is_rederived(registered_case,stage):
    import os
    from tests.tools.test_registered_research_run import execute,join
    from services.research.trial_ledger import TrialLedger
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation.project_research_population import validate_project_cut
    f=registered_case;pid=os.fork()
    if pid==0:
        try:
            original=f['run']._put
            def put(path,*args,**kwargs):
                value=original(path,*args,**kwargs)
                if path.name==stage:os._exit(71)
                return value
            f['run']._put=put
            append=TrialLedger.append
            def interrupted(ledger,row):
                value=append(ledger,row)
                if row.status==stage:os._exit(71)
                return value
            TrialLedger.append=interrupted
            f['prepare']();execute(f);os._exit(99)
        except BaseException:
            import traceback
            traceback.print_exc();os._exit(98)
    assert join(pid)==71
    cut=ProjectResearchEvidenceReader(f['root']/'project',registry_database=f['root']/'registry.db')()
    assert validate_project_cut(cut)==cut
    assert cut['all_runs_finalized'] is False
    forged=copy.deepcopy(cut);forged['all_runs_finalized']=True;forged['unfinished_runs']=[]
    for run in forged['runs'].values():run['partial']=[]
    with pytest.raises(ValueError):validate_project_cut(rehash(forged))


def test_real_validation_receipt_chain_and_reclaimed_attempts(project):
    from tests.research.test_project_research_evidence_reader import running_trial,reader
    from services.research.trial_ledger import TrialLedger
    from services.research.validation_dag import StageEvidence
    from services.validation.project_research_population import validate_project_cut
    from quantos.contracts.trial_receipt_identity import receipt_row_hash,receipt_payload_hash
    import json
    plan,directory=running_trial(project);tid=plan['trials'][0]['native_trial_id']
    with TrialLedger(str(directory/'archive/trials.db'),create=False) as ledger:
        ledger.claim_validation_stage(tid,'cheap',owner='original-validator',now=130,lease_seconds=5,panel_digest='1'*64,policy_digest='2'*64)
        claim=ledger.claim_validation_stage(tid,'cheap',owner='original-validator',now=136,lease_seconds=5,panel_digest='1'*64,policy_digest='2'*64)
        evidence=StageEvidence('cheap','1','BLOCK',{'available':False},'missing-independent-risk','isolated.native')
        payload=dict(trial_id=tid,panel_digest='1'*64,policy_digest='2'*64,event_at=110,
            effective_trial_count=1,trial_set_digest='3'*64,evidence_hash=evidence.evidence_hash,
            stage='cheap',version='1',implementation_id='isolated.native',outcome='BLOCK',evidence=evidence.evidence,reason=evidence.reason)
        ledger.complete_validation_stage(tid,'cheap','BLOCKED',payload,owner='original-validator',now=137,attempt=claim.attempt)
    cut=reader(project)();assert validate_project_cut(cut)==cut
    retained=cut['runs'][plan['invocation_id']]['ledger'];assert len(retained['validation_receipts'])==3
    for attack in ('chain','payload','phantom','index_alias','self_consistent_wrong_outcome'):
        altered=copy.deepcopy(cut);view=altered['runs'][plan['invocation_id']]['ledger'];row=view['validation_receipts'][-1]
        if attack=='chain':row['prev_hash']='0'*64
        elif attack=='payload':row['payload_json']=row['payload_json'].replace('missing-independent-risk','changed')
        elif attack=='phantom':row['trial_id']='0'*64
        elif attack=='index_alias':view['validation_receipts'][0]['event_index']=False
        else:
            value=json.loads(row['payload_json']);value['outcome']='PASS'
            from quantos.contracts.research_registry_identity import canonical_hash
            value['evidence_hash']=canonical_hash({k:value[k] for k in ('stage','version','implementation_id','outcome','evidence','reason')})
            row['payload_json']=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)
            row['payload_hash']=receipt_payload_hash(row['payload_json'])
            row['row_hash']=receipt_row_hash(**{k:v for k,v in row.items() if k not in ('row_hash','payload_json')})
            view['validation_receipt_head']['head_hash']=row['row_hash']
        with pytest.raises(ValueError):validate_project_cut(rehash(altered))


def test_original_search_recovery_counts_evaluation_activity_exactly(project):
    from quantos.contracts.alpha import AlphaEvaluation
    from quantos.contracts.research import ValidationVerdict
    from services.research.experiment_registry.registry import prepare_evaluation_artifact
    from tests.research.test_project_research_evidence_reader import prepare,reader
    from services.validation.project_research_population import validate_project_cut
    from quantos.contracts.research_registry_identity import search_recovery_hash
    prepare(project,1)
    store=project['store'];store.begin_search_attempt(search_id='recovered',search_kind='TEST',source='original',dataset_version='data',recorded_at=120)
    store.register_search_candidates('recovered',['f'*64],registered_at=121)
    store.heartbeat_search_attempt('recovered',recorded_at=122)
    write=prepare_evaluation_artifact(AlphaEvaluation(alpha_id='diagnostic',verdict=ValidationVerdict.FAIL,reproducibility_hash='diagnostic'), 'data',130)
    store.insert_evaluation(**vars(write),search_id='recovered',config_hash='f'*64)
    assert store.recover_stale_search_attempts(recorded_at=180,timeout_ms=50)==('recovered',)
    cut=reader(project)();assert validate_project_cut(cut)==cut
    assert cut['registry_history']['recoveries'][0]['last_activity_at']==130
    bad=copy.deepcopy(cut);row=bad['registry_history']['recoveries'][0];row['last_activity_at']=122
    row['recovery_hash']=search_recovery_hash(**{k:v for k,v in row.items() if k not in ('recovery_seq','recovery_hash')})
    with pytest.raises(ValueError,match='RECOVERY_BINDING'):validate_project_cut(rehash(bad))
    bad=copy.deepcopy(cut);bad['registry_history']['evaluations'][0]['content_hash']='0'*64
    with pytest.raises(ValueError,match='EVALUATION_BINDING'):validate_project_cut(rehash(bad))


def test_validator_import_does_not_load_research_tools_or_execution():
    import subprocess,sys
    result=subprocess.run([sys.executable,'-c',"import sys; from services.validation.project_research_population import validate_project_cut; assert not [n for n in sys.modules if n.startswith(('services.research','services.execution','tools.'))]"],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
