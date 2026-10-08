"""Original registration rows shared by native producers and read-only consumers.

This module never starts searches, appends trials, repairs history or grants
research authority.  Callers supply the original registry interface.
"""
from quantos.contracts import registered_research_run as contract
from quantos.contracts.trial_provenance import TrialProvenance, ActorType, TrialStatus
from quantos.contracts.gold_forward_evaluation_design import FULL_REVIEW_SCHEMAS

canonical = contract.canonical
plan_digest = contract.digest
require = contract.require


def expected_provenance(plan, trial):
    search=plan['search']; at=search['recorded_at_ms']
    return TrialProvenance(trial_id=trial['provenance_trial_id'],research_session_id=search['search_id'],
        config_hash=trial['config_hash'],actor_id=plan['actor_id'],actor_type=ActorType.AUTOMATED_SEARCH,
        status=TrialStatus.RECORDED,started_at=at,completed_at=at,
        dataset_versions=(search['dataset_version'],),code_hash=plan_digest(plan['source_identity']),
        artifact_paths=('plan.json','input.json'),artifact_hashes=(plan_digest(plan),plan['input_sha256']))

def registration_evidence(plan, registry):
    """Read and independently compare the actual original search/provenance rows."""
    contract.validate_plan(plan)
    search=plan['search']; identity=registry.search_identity(search['search_id'])
    require(all(identity[k]==search[k] for k in ('search_id','search_kind','source','dataset_version')),'SEARCH_IDENTITY')
    events=registry.search_attempt_events(search['search_id'])
    require(events and events[0]['status']=='STARTED' and events[0]['recorded_at']==search['recorded_at_ms'],'SEARCH_START')
    candidates=registry.search_candidates(search['search_id'])
    require({x['config_hash'] for x in candidates}=={t['config_hash'] for t in plan['trials']}
            and all(x['registered_at']==search['recorded_at_ms'] and x['status']=='REGISTERED' for x in candidates),'CANDIDATE_ROWS')
    require(registry.search_history_valid() and registry.trial_provenance_chain_valid(),'REGISTRY_HISTORY')
    provenance=[]
    for trial in plan['trials']:
        row=registry.trial_provenance(trial['provenance_trial_id'])
        require(row is not None and canonical(row['record'])==canonical(expected_provenance(plan,trial).canonical_payload()),'PROVENANCE')
        provenance.append(row)
    return dict(schema='QUANTOS_REGISTERED_RESEARCH_RECEIPT_V1',plan_sha256=plan_digest(plan),
                search_identity=identity,started_event=events[0],candidates=candidates,provenance=provenance)


def native_trial_identity(trial):
    return dict(trial_id=trial['native_trial_id'],alpha_id=trial['alpha_id'],family_id=trial['family_id'],
                exact_definition_id=trial['config_hash'],validation_policy_digest=trial['validation_policy_digest'])

def initial_archive_manifest(plan):
    value = dict(schema='GOLD_BAR_EXPERIMENT_V1',source=plan['source_identity'],
        trial_ids=[t['native_trial_id'] for t in plan['trials']],input_sha256=plan['input_sha256'],
        submission_enabled=False,economic_eligible=False)
    if plan.get('forward_design', {}).get('schema') in FULL_REVIEW_SCHEMAS:
        value.update(schema='QUANTOS_REGISTERED_GOLD_BAR_EXPERIMENT_V2', registered_plan=plan)
    if plan['schema'] in contract.CHUNKED_GOLD_SCHEMAS:
        value.update(schema='QUANTOS_REGISTERED_GOLD_CHUNKED_EXPERIMENT_V1', registered_plan=plan)
    return value


def gold_archive_policy(manifest, document):
    """Validate the embedded original plan before consuming its native policy."""
    if manifest.get('schema') == 'GOLD_BAR_EXPERIMENT_V1':
        return plan_digest(dict(mode='TECHNICAL_COMPUTATION_ONLY', automatic_selection=False))
    require(manifest.get('schema') == 'QUANTOS_REGISTERED_GOLD_BAR_EXPERIMENT_V2', 'GOLD_ARCHIVE_SCHEMA')
    plan = contract.validate_plan(manifest['registered_plan'])
    require(plan['schema'] == contract.FORWARD_DECLARED_SCHEMA and plan['adapter'] == contract.GOLD
        and plan['forward_design']['schema'] in FULL_REVIEW_SCHEMAS, 'GOLD_ARCHIVE_PLAN')
    require(all(canonical(manifest.get(k)) == canonical(v)
        for k,v in initial_archive_manifest(plan).items()), 'GOLD_ARCHIVE_MANIFEST')
    from quantos.contracts.gold_forward_evaluation_design import bind_input
    bind_input(plan['forward_design'], plan, document)
    return plan_digest(plan['forward_design'])
