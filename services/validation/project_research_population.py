"""Verify retained project facts without IO, trust selection or admission.

A valid cut describes the supplied originals. It does not establish independent
source/result authority, a current filesystem observation, or economic fitness.
"""
from __future__ import annotations

import base64
import copy
import json
import re

from quantos.contracts import registered_research_run as plan_contract
from quantos.contracts import project_chunked_results as chunked
from quantos.contracts import gold_native_source as source
from quantos.contracts.json_integrity import load_json_without_duplicate_keys
from quantos.contracts.project_research_binding import bind_project_runs
from quantos.contracts.research_evidence_json import canonical, digest, _decode
from quantos.contracts import research_registry_identity as registry_hash
from quantos.contracts.trial_identity import TRIAL_RECORD_FIELDS, trial_event_hash
from quantos.contracts.trial_provenance import TrialProvenance, ActorType, TrialStatus
from quantos.contracts.trial_receipt_identity import receipt_payload_hash, receipt_row_hash

SCHEMA = 'QUANTOS_PROJECT_RESEARCH_CUT_V1'
FIELDS = frozenset('schema registry_population registry_history runs attempts attempt_count mapped_attempt_count registered_attempt_count unique_definition_count population_coverage unmapped unfinished_attempts unfinished_runs all_runs_finalized submission_enabled economic_authority cut_sha256 read_identity_sha256'.split())
ORIGINALS = frozenset('plan.json input.json registration.json invocation.json outcome.json archive/input.json archive/running.json archive/report.json archive/manifest.json'.split())
TERMINAL = frozenset(('PASSED', 'FAILED', 'CANCELLED'))
TRANSITIONS = {'PLANNED': {'RUNNING', 'CANCELLED'}, 'RUNNING': TERMINAL,
               'PASSED': set(), 'FAILED': set(), 'CANCELLED': set()}
MAX_ROWS = 300_000


def require(condition, reason):
    if not condition:
        raise ValueError('PROJECT_POPULATION_' + reason)


def fields(value, names):
    require(type(value) is dict and set(value) == set(names), 'FIELDS')


def integer(value, minimum=0):
    require(type(value) is int and minimum <= value <= 2**63-1, 'INTEGER')


def text(value):
    require(type(value) is str and bool(value) and value == value.strip(), 'TEXT')


def hashed(value, *, empty=False):
    require(type(value) is str and ((empty and value == '') or re.fullmatch('[0-9a-f]{64}', value)), 'HASH')


def same(left, right, reason):
    require(canonical(left) == canonical(right), reason)


def rows(value):
    require(type(value) is list and len(value) <= MAX_ROWS, 'ROWS')
    return value


def _chain(value, *, sequence, previous, identity, function, payload):
    head = ''
    for index, row in enumerate(rows(value), 1):
        fields(row, (sequence, previous, identity, *payload))
        integer(row[sequence], 1)
        require(row[sequence] == index and row[previous] == head, 'REGISTRY_CHAIN')
        expected = function(**{key: row[key] for key in (*payload, previous)})
        require(row[identity] == expected, 'REGISTRY_HASH')
        head = expected


class _RegistryRows:
    """Read-only adapter, constructed only after every supplied chain verifies."""
    def __init__(self, population, history, searches):
        self.population, self.history, self.searches = population, history, searches

    def search_identity(self, search_id):
        first = self.searches[search_id]['first']
        identity = {key:first[key] for key in ('search_id','search_kind','source','dataset_version')}
        return dict(identity, identity_hash=registry_hash.search_identity_hash(**identity))

    def search_attempt_events(self, search_id):
        reasons = {row['terminal_event_hash']:row['reason'] for row in self.history['recoveries']}
        return [dict(row, recovery_reason=reasons.get(row['event_hash']))
                for row in self.history['events'] if row['search_id'] == search_id]

    def search_candidates(self, search_id):
        return [{key:value for key,value in row.items() if key not in ('search_identity','search_started_at')}
                for row in self.population['search_candidates'] if row['search_id'] == search_id]

    def trial_provenance(self, trial_id):
        match = next((row for row in self.population['provenance'] if row['trial_id'] == trial_id), None)
        if match is None:
            return None
        result = {key:match[key] for key in ('provenance_seq','trial_id','config_hash','prev_entry_hash','entry_hash')}
        return dict(result, record=_decode(match['record_json'].encode()))

    def search_history_valid(self):
        return True

    def trial_provenance_chain_valid(self):
        return True


def _registry(population, history):
    fields(population, ('schema','trials','provenance','oos','search_candidates','sha256'))
    require(population['schema'] == 'QUANTOS_RESEARCH_REGISTRY_POPULATION_V1', 'REGISTRY_SCHEMA')
    require(population['sha256'] == digest({k:v for k,v in population.items() if k != 'sha256'}), 'REGISTRY_DIGEST')
    fields(history, ('events','heartbeats','recoveries','evaluations','evaluation_links'))
    trials = {}; previous = ''
    for row in rows(population['trials']):
        fields(row, ('config_hash','first_seen_ts')); text(row['config_hash']); integer(row['first_seen_ts'])
        require(row['config_hash'] > previous, 'DEFINITION_ORDER')
        previous = row['config_hash']; trials[previous] = row
    _chain(history['events'], sequence='event_seq', previous='prev_event_hash', identity='event_hash',
           function=registry_hash.search_event_hash,
           payload=('search_id','search_kind','source','dataset_version','status','recorded_at'))
    searches = {}; high_water = -1
    for row in history['events']:
        for key in ('search_id','search_kind','source','dataset_version'): text(row[key])
        integer(row['recorded_at']); sid = row['search_id']
        require(row['status'] in ('STARTED','COMPLETED','FAILED'), 'SEARCH_STATUS')
        if row['status'] == 'STARTED':
            require(sid not in searches and row['recorded_at'] >= high_water, 'SEARCH_START')
            searches[sid] = dict(first=row, terminal=None, activity=row['recorded_at'])
            high_water = row['recorded_at']
        else:
            require(sid in searches and searches[sid]['terminal'] is None, 'SEARCH_TRANSITION')
            first = searches[sid]['first']
            require(all(row[key] == first[key] for key in ('search_kind','source','dataset_version'))
                    and row['recorded_at'] >= first['recorded_at'], 'SEARCH_BINDING')
            searches[sid]['terminal'] = row
    candidates = []; pairs = set(); times = {}
    for row in rows(population['search_candidates']):
        fields(row, ('candidate_seq','search_id','config_hash','status','registered_at',
                     'prev_candidate_hash','candidate_hash','search_identity','search_started_at'))
        sid = row['search_id']; integer(row['registered_at']); integer(row['search_started_at'])
        require(sid in searches and row['config_hash'] in trials and row['status'] == 'REGISTERED', 'CANDIDATE')
        pair=(sid,row['config_hash']); require(pair not in pairs, 'DUPLICATE_CANDIDATE'); pairs.add(pair)
        first=searches[sid]['first']; terminal=searches[sid]['terminal']
        require(row['search_started_at'] == first['recorded_at']
                and row['registered_at'] >= max(first['recorded_at'], times.get(sid,-1),trials[row['config_hash']]['first_seen_ts'])
                and (terminal is None or row['registered_at'] <= terminal['recorded_at']), 'CANDIDATE_TIME')
        identity={key:first[key] for key in ('search_id','search_kind','source','dataset_version')}
        same(row['search_identity'],dict(identity,identity_hash=registry_hash.search_identity_hash(**identity)),'SEARCH_IDENTITY')
        candidates.append({k:v for k,v in row.items() if k not in ('search_identity','search_started_at')})
        times[sid]=row['registered_at'];searches[sid]['activity']=max(searches[sid]['activity'],row['registered_at'])
    _chain(candidates, sequence='candidate_seq', previous='prev_candidate_hash', identity='candidate_hash',
           function=registry_hash.search_candidate_hash,payload=('search_id','config_hash','status','registered_at'))
    _chain(history['heartbeats'],sequence='heartbeat_seq',previous='prev_heartbeat_hash',identity='heartbeat_hash',
           function=registry_hash.search_heartbeat_hash,payload=('search_id','recorded_at'))
    times={}
    for row in history['heartbeats']:
        sid=row['search_id'];integer(row['recorded_at']);require(sid in searches,'HEARTBEAT_SEARCH')
        search=searches[sid];terminal=search['terminal']
        require(row['recorded_at'] >= max(search['first']['recorded_at'],times.get(sid,-1))
                and (terminal is None or row['recorded_at'] <= terminal['recorded_at']), 'HEARTBEAT_TIME')
        times[sid]=row['recorded_at'];search['activity']=max(search['activity'],row['recorded_at'])
    _chain(history['recoveries'],sequence='recovery_seq',previous='prev_recovery_hash',identity='recovery_hash',
           function=registry_hash.search_recovery_hash,payload=('search_id','reason','timeout_ms','last_activity_at','recovered_at','terminal_event_hash'))
    evaluations={}; last_id=''
    for row in rows(history['evaluations']):
        fields(row,('run_id','dataset_version','recorded_ts','content_hash'))
        text(row['run_id']);text(row['dataset_version']);integer(row['recorded_ts']);hashed(row['content_hash'])
        require(row['run_id']>last_id,'EVALUATION_ORDER');last_id=row['run_id'];evaluations[last_id]=row
    _chain(history['evaluation_links'],sequence='link_seq',previous='prev_link_hash',identity='link_hash',
           function=registry_hash.search_evaluation_hash,payload=('search_id','config_hash','run_id','recorded_at','evaluation_content_hash'))
    seen_links=set();times={}
    for row in history['evaluation_links']:
        sid=row['search_id'];identity=(sid,row['run_id']);integer(row['recorded_at'])
        require(sid in searches and row['run_id'] in evaluations and identity not in seen_links
                and (sid,row['config_hash']) in pairs,'EVALUATION_LINK')
        seen_links.add(identity);search=searches[sid];terminal=search['terminal'];evaluation=evaluations[row['run_id']]
        require(evaluation['dataset_version']==search['first']['dataset_version']
                and evaluation['content_hash']==row['evaluation_content_hash']
                and row['recorded_at']>=max(search['first']['recorded_at'],times.get(sid,-1))
                and (terminal is None or row['recorded_at']<=terminal['recorded_at']),'EVALUATION_BINDING')
        times[sid]=row['recorded_at'];search['activity']=max(search['activity'],row['recorded_at'])
    recovered=set()
    for row in history['recoveries']:
        sid=row['search_id'];require(sid in searches and sid not in recovered,'RECOVERY_SEARCH');recovered.add(sid)
        for key in ('timeout_ms','last_activity_at','recovered_at'): integer(row[key],1 if key=='timeout_ms' else 0)
        search=searches[sid];terminal=search['terminal']
        require(terminal is not None and terminal['status']=='FAILED'
                and row['reason']=='STALE_STARTED_TIMEOUT' and row['terminal_event_hash']==terminal['event_hash']
                and row['recovered_at']==terminal['recorded_at'] and row['last_activity_at']==search['activity']
                and row['last_activity_at']+row['timeout_ms']<=row['recovered_at'],'RECOVERY_BINDING')
    previous='';ids=set()
    columns=('trial_id','research_session_id','config_hash','actor_id','actor_type','status','started_at',
             'completed_at','parent_trial_id','hypothesis_id','expression_hash','parameter_hash','code_hash')
    for index,row in enumerate(rows(population['provenance']),1):
        fields(row,('provenance_seq',*columns,'oos_peek_count','record_json','prev_entry_hash','entry_hash'))
        integer(row['provenance_seq'],1);integer(row['oos_peek_count'])
        require(row['provenance_seq']==index and row['trial_id'] not in ids and row['config_hash'] in trials,'PROVENANCE')
        ids.add(row['trial_id']);require(type(row['record_json']) is str,'PROVENANCE_JSON')
        payload=_decode(row['record_json'].encode())
        record=TrialProvenance(**dict(payload,actor_type=ActorType(payload['actor_type']),status=TrialStatus(payload['status'])))
        same(record.canonical_payload(),payload,'PROVENANCE_CONTENT')
        for key in columns: same(row[key],payload[key],'PROVENANCE_COLUMN')
        require(row['oos_peek_count']==record.oos_peek_count and row['prev_entry_hash']==previous
                and row['entry_hash']==record.entry_hash(previous),'PROVENANCE_CHAIN')
        previous=row['entry_hash']
    _chain(population['oos'],sequence='access_seq',previous='prev_entry_hash',identity='entry_hash',
           function=registry_hash.trial_oos_access_hash,payload=('trial_id','method','boundary','viewed_at'))
    for row in population['oos']:
        require(row['trial_id'] in ids,'OOS_TRIAL');integer(row['viewed_at']);text(row['method']);text(row['boundary'])
    return _RegistryRows(population,history,searches)


def _receipts(ledger):
    history=ledger['history']; previous='';indices={}
    for position,row in enumerate(rows(ledger['validation_receipts']),1):
        fields(row,('trial_id','stage','event_index','state','payload_json','payload_hash','global_seq','prev_hash','row_hash','owner','lease_until','attempt'))
        tid,stage=row['trial_id'],row['stage'];key=(tid,stage)
        require(tid in history and stage in ('cheap','oos','realism','__artifact__'),'RECEIPT_IDENTITY')
        for name in ('event_index','global_seq','lease_until','attempt'): integer(row[name])
        require(row['global_seq']==position and row['prev_hash']==previous
                and row['event_index']==indices.get(key,-1)+1,'RECEIPT_SEQUENCE')
        indices[key]=row['event_index'];text(row['owner'])
        require(row['state'] in ('CLAIMED','DONE','BLOCKED','FAILED'),'RECEIPT_STATE')
        require(type(row['payload_json']) is str,'RECEIPT_JSON')
        payload=load_json_without_duplicate_keys(row['payload_json'].encode())
        require(type(payload) is dict and json.dumps(payload,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False)==row['payload_json'],'RECEIPT_CANONICAL')
        require(receipt_payload_hash(row['payload_json'])==row['payload_hash'],'RECEIPT_PAYLOAD_HASH')
        expected=receipt_row_hash(**{k:v for k,v in row.items() if k not in ('row_hash','payload_json')})
        require(expected==row['row_hash'],'RECEIPT_ROW_HASH');previous=expected
        if row['state']=='CLAIMED':
            fields(payload,('stage','attempt','panel_digest','policy_digest'))
            require(payload['stage']==stage,'RECEIPT_STAGE');integer(payload['attempt'],1)
        elif stage!='__artifact__':
            fields(payload,('trial_id','panel_digest','policy_digest','event_at','effective_trial_count','trial_set_digest','evidence_hash','stage','version','implementation_id','outcome','evidence','reason'))
            integer(payload['event_at']);integer(payload['effective_trial_count'])
            for name in ('trial_id','panel_digest','policy_digest','trial_set_digest','evidence_hash'): hashed(payload[name])
            for name in ('version','implementation_id'): text(payload[name])
            require(payload['trial_id']==tid and payload['event_at']==history[tid][0]['at']
                    and payload['stage']==stage and (payload['reason'] is None or type(payload['reason']) is str),'RECEIPT_BINDING')
            require(payload['outcome']==('PASS' if row['state']=='DONE' else 'BLOCK'),'RECEIPT_OUTCOME')
            evidence={k:payload[k] for k in ('stage','version','implementation_id','outcome','evidence','reason')}
            require(payload['evidence_hash']==registry_hash.canonical_hash(evidence),'RECEIPT_EVIDENCE')
    head=ledger['validation_receipt_head']
    if head is None:
        require(not ledger['history'] and not ledger['validation_receipts'],'RECEIPT_HEAD_MISSING')
    else:
        same(head,dict(event_count=len(ledger['validation_receipts']),head_hash=previous),'RECEIPT_HEAD')


def _ledger(value, plan):
    fields(value,('history','event_count','head_hash','trial_count','validation_receipts','validation_receipt_head'))
    require(type(value['history']) is dict,'LEDGER_HISTORY')
    expected={row['native_trial_id']:row for row in plan['trials']}
    require(set(value['history'])<=set(expected),'NATIVE_POPULATION')
    events=[]
    for tid,items in value['history'].items():
        require(bool(rows(items)),'EMPTY_TRIAL')
        previous='';prior=None
        for index,row in enumerate(items):
            fields(row,(*TRIAL_RECORD_FIELDS,'global_seq','prev_hash','event_hash'))
            for name in ('trial_id','alpha_id','family_id','exact_definition_id','validation_policy_digest'): hashed(row[name])
            integer(row['event_index']);integer(row['global_seq'],1)
            require(type(row['at']) is int and -(2**63)<=row['at']<=2**63-1,'TRIAL_TIME')
            require(row['trial_id']==tid and row['event_index']==index and row['prev_hash']==previous,'TRIAL_SEQUENCE')
            require(row['status'] in ({'PLANNED'} if prior is None else TRANSITIONS[prior]),'TRIAL_TRANSITION')
            item=expected[tid]
            require(all(row[k]==item[v] for k,v in (('alpha_id','alpha_id'),('family_id','family_id'),
                ('exact_definition_id','config_hash'),('validation_policy_digest','validation_policy_digest'))),'TRIAL_BINDING')
            previous=row['event_hash'];prior=row['status'];events.append(row)
    require(len(events)<=MAX_ROWS,'LEDGER_BOUND');events.sort(key=lambda row:row['global_seq'])
    head=''
    for sequence,row in enumerate(events,1):
        require(row['global_seq']==sequence,'GLOBAL_SEQUENCE')
        expected=trial_event_hash({k:row[k] for k in TRIAL_RECORD_FIELDS},global_seq=sequence,
                                  previous_hash=row['prev_hash'],global_previous_hash=head)
        require(row['event_hash']==expected,'TRIAL_HASH');head=expected
    same({k:value[k] for k in ('event_count','head_hash','trial_count')},
         dict(event_count=len(events),head_hash=head,trial_count=len(value['history'])),'LEDGER_HEAD')
    _receipts(value)


def _runs(value, *, chunked_cut=False, source_cut=False):
    require(type(value) is dict and len(value)<=10_000,'RUNS')
    result=copy.deepcopy(value); used=0
    for invocation,run in result.items():
        hashed(invocation)
        plan=plan_contract.validate_plan(run['plan']);require(plan['invocation_id']==invocation,'RUN_IDENTITY')
        new_result = plan['schema'] in plan_contract.CHUNKED_GOLD_SCHEMAS
        new_source = plan['schema'] in plan_contract.SOURCE_GOLD_SCHEMAS
        require(not new_result or chunked_cut, 'CHUNKED_CUT_REQUIRED')
        require(not new_source or source_cut, 'SOURCE_CUT_REQUIRED')
        fields(run, ('plan','originals','ledger','partial', *(('result_material',) if new_result else ()),
                     *(('input_material',) if new_source else ())))
        allowed = ORIGINALS | (chunked.ORIGINALS if new_result else set()) | ({source.MANIFEST} if new_source else set()) | ({'archive/risk-replay.json'} if plan['schema']==plan_contract.RISK_EVALUATED_SCHEMA else set())
        originals=run['originals'];require(type(originals) is dict and {'plan.json'}<=set(originals)<=allowed,'ORIGINAL_FILES')
        documents={}
        for name,retained in originals.items():
            fields(retained,('sha256','bytes_base64'));hashed(retained['sha256'])
            encoded=retained['bytes_base64'];limit=plan_contract.MAX_PLAN_BYTES if name=='plan.json' else plan_contract.MAX_OUTPUT_BYTES
            require(type(encoded) is str and len(encoded)<=(limit+2)//3*4,'ORIGINAL_BOUND')
            raw=base64.b64decode(encoded,validate=True);used+=len(raw)
            require(len(raw)<=limit and used<=32_000_000 and base64.b64encode(raw).decode()==encoded,'ORIGINAL_BOUND')
            require(digest(raw)==retained['sha256'],'ORIGINAL_HASH');documents[name]=_decode(raw)
        same(documents['plan.json'],plan,'PLAN_CONTENT')
        require(originals['plan.json']['sha256']==plan_contract.digest(plan),'PLAN_CANONICAL')
        run['partial']=[]
        if 'input.json' in originals:
            require(originals['input.json']['sha256']==plan['input_sha256'],'INPUT_BINDING')
        else:
            require(set(originals)=={'plan.json'} and not run['ledger']['history']
                    and run['ledger']['validation_receipt_head'] is None,'INPUT_MISSING')
            run['partial'].append('INPUT_NOT_PERSISTED')
        if 'archive/input.json' in originals:
            require(originals['archive/input.json']['sha256']==plan['input_sha256'],'ARCHIVE_INPUT')
        if new_result:
            chunked.validate_material(plan, originals, documents, run['result_material'])
        if new_source:
            if 'input.json' in documents:
                source.validate_material(plan, documents['input.json'], originals, run['input_material'])
            else:
                require(run['input_material'] == dict(schema=source.MATERIAL_SCHEMA, status='INCOMPLETE',
                    files={}, bytes=0, part_count=0, parts=[]), 'SOURCE_INPUT_MISSING')
            if run['input_material']['status'] != 'COMPLETE':
                run['partial'].append('INPUT_ORIGINALS_NOT_PERSISTED')
        _ledger(run['ledger'],plan)
        require(not new_source or not run['ledger']['history'] or run['input_material']['status'] == 'COMPLETE', 'NATIVE_SOURCE_MISSING')
        require(not run['ledger']['history'] or 'archive/input.json' in originals,'NATIVE_INPUT_MISSING')
        if run['ledger']['validation_receipt_head'] is None:
            require(not set(originals)&{'invocation.json','outcome.json','archive/running.json','archive/report.json','archive/manifest.json','archive/risk-replay.json'},'DATABASE_MISSING')
        run['documents']=documents
    return result


def validate_project_cut(cut):
    """Return a verified copy; historical completeness never grants authority."""
    try:
        source_cut = cut.get('schema') == source.CUT_SCHEMA
        new_cut = cut.get('schema') in (chunked.CUT_SCHEMA, source.CUT_SCHEMA)
        fields(cut, FIELDS | ({'result_limits'} if new_cut else set()) | ({'input_limits'} if source_cut else set()))
        canonical(cut)
        require(cut['schema'] in (SCHEMA, chunked.CUT_SCHEMA, source.CUT_SCHEMA) and cut['submission_enabled'] is False
                and cut['economic_authority'] is False,'SCHEMA_AUTHORITY')
        if new_cut:
            same(cut['result_limits'], chunked.LIMITS, 'RESULT_LIMITS')
        if source_cut:
            same(cut['input_limits'], source.LIMITS, 'INPUT_LIMITS')
        hashed(cut['read_identity_sha256']);hashed(cut['cut_sha256'])
        require(cut['cut_sha256']==digest({k:v for k,v in cut.items() if k not in ('cut_sha256','read_identity_sha256')}),'CUT_HASH')
        registry=_registry(cut['registry_population'],cut['registry_history'])
        runs=_runs(cut['runs'], chunked_cut=new_cut, source_cut=source_cut)
        if source_cut:
            require(any(r['plan']['schema'] in plan_contract.SOURCE_GOLD_SCHEMAS for r in runs.values()), 'SOURCE_RUN_REQUIRED')
            require(sum(r.get('input_material', {}).get('bytes', 0) for r in runs.values())
                <= source.LIMITS['maximum_bytes'], 'INPUT_BYTES')
        if new_cut:
            require(any(r['plan']['schema'] in plan_contract.CHUNKED_GOLD_SCHEMAS for r in runs.values()), 'CHUNKED_RUN_REQUIRED')
            require(sum(f['bytes'] for r in runs.values() for n,f in r.get('result_material', {}).get('files', {}).items()
                if n.startswith('blobs/')) <= chunked.LIMITS['maximum_source_bytes'], 'RESULT_BYTES')
        attempts,unmapped=bind_project_runs(runs,cut['registry_population'],cut['registry_history'],registry)
        fields(cut['unmapped'],('definitions','search_candidates','provenance','searches','unplanned_invocations'))
        unplanned=cut['unmapped']['unplanned_invocations'];rows(unplanned)
        for identity in unplanned: hashed(identity)
        require(unplanned==sorted(set(unplanned)) and not set(unplanned)&set(runs),'UNPLANNED')
        unmapped['unplanned_invocations']=unplanned
        unfinished=sorted(invocation for invocation,run in runs.items() if run['partial'] or any(
            row['invocation_id']==invocation and row['status'] not in TERMINAL for row in attempts))
        for invocation,run in runs.items(): same(run['partial'],cut['runs'][invocation]['partial'],'RUN_PARTIAL')
        derived=dict(attempts=attempts,unmapped=unmapped,attempt_count=len(attempts)+len(unmapped['search_candidates']),
            mapped_attempt_count=len(attempts),registered_attempt_count=len(cut['registry_population']['search_candidates']),
            unique_definition_count=len({r['config_hash'] for r in cut['registry_population']['trials']}|{r['config_hash'] for r in attempts}),
            population_coverage='UNMAPPED' if any(unmapped.values()) else 'COMPLETE',
            unfinished_attempts=[r['native_trial_id'] for r in attempts if r['status'] not in TERMINAL],
            unfinished_runs=unfinished,all_runs_finalized=bool(runs) and not unfinished and not any(unmapped.values()))
        same(derived,{k:cut[k] for k in derived},'DERIVED_FACTS')
        return copy.deepcopy(cut)
    except (KeyError,TypeError,OverflowError,RecursionError) as exc:
        raise ValueError('PROJECT_POPULATION_MALFORMED') from exc
