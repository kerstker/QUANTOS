"""Original issuer group continuity: nonexecuting administrative certificates.

Public material custody and operator identity belong to the fixed boundary.
Historical verification uses the stored registry and event time; it never renews
permission. All spending remains in the original RESERVED/SIGNED chain.
"""
from copy import deepcopy
from decimal import Decimal
import base64
import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey, Ed25519PrivateKey
from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_common import require, amount
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.validation import demo_submission as submission

GROUP_SUBJECT_SCHEMA='DEMO_GOLD_GROUP_CONTINUITY_SUBJECT_V1'
SIGN_SUBJECT_SCHEMA='DEMO_GOLD_DELEGATION_SIGN_SUBJECT_V1'
CONFIRMATION_SCHEMA='GOLD_FUTURE_BUDGET_OPERATOR_CONFIRMATION_V1'
CERTIFICATE_SCHEMA='DEMO_GOLD_GROUP_CONTINUITY_CERTIFICATE_V1'
SIGN_RECEIPT_SCHEMA='DEMO_GOLD_DELEGATION_SIGN_RECEIPT_V1'
ANCHOR_SCHEMA='DEMO_GOLD_ISSUER_HISTORY_ANCHOR_V2'
DOMAIN=b'QUANTOS_DEMO_GOLD_GROUP_CONTINUITY_V1\x00'
GROUP_KINDS=('GROUP_INITIALIZED','GROUP_EXIT_RESERVE_MIGRATED')
ADMIN_KINDS=(*GROUP_KINDS,'DELEGATION_V2_SIGNED')
GROUP_DOCUMENTS=tuple('previous_authorization template registry profile pilot ancestry runtime definition dispatcher'.split())
SIGN_DOCUMENTS=tuple('template registry profile pilot runtime definition dispatcher continuity_event delegation_intent'.split())
_COMMON_SUBJECT='schema purpose source_commit created_at_ms expires_at_ms documents_sha256 expected_head'
_GROUP_SUBJECT=_COMMON_SUBJECT+' action previous_authorization_sha256 future_template_sha256 registry_sha256 profile_sha256 pilot_request_sha256 pilot_configuration_sha256 lineage_sha256 from_group_binding_sha256 to_group_binding_sha256'
_SIGN_SUBJECT=_COMMON_SUBJECT+' continuity_event_sha256 future_template_sha256 registry_sha256 delegation_intent_sha256'


def _canonical(value):
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    require(len(raw)<=131072,'ISSUER_ADMIN_CANONICAL_BUDGET')
    return raw


def lineage_sha256(template):
    return _digest(dict(schema='DEMO_GOLD_GROUP_LINEAGE_V1',source_commit=template['executor_release_commit'],
        account_ref_sha256=template['account_ref_sha256'],group_id=template['budget_group']['group_id'],
        profile_sha256=template['origin']['profile_sha256'],pilot=template['pilot']))


def _same_pilot(left,right):
    # group_id and the new template id cannot create another allowance. Even a
    # changed request is not a new pilot if the original baseline is reused.
    if (left['executor_release_commit'],left['account_ref_sha256']) != (right['executor_release_commit'],right['account_ref_sha256']):return False
    return (any(left['pilot'][key]==right['pilot'][key] for key in ('request_sha256','configuration_sha256'))
        or all(left['pilot'][key]==right['pilot'][key] for key in ('opening_origin_sha256','started_at_ms','ends_at_ms')))


def authorization_binding(value):
    from live.execution.demo_gold_future_template import SCHEMA, template_group_binding
    from live.execution.demo_gold_budget_scope import is_scope, budget_binding
    from live.execution.demo_issuer_policy import validate_policy, budget_group_binding, GOLD_GROUP_POLICY_SCHEMAS
    if value.get('schema')==SCHEMA:return template_group_binding(value)
    if is_scope(value):return budget_binding(value)
    validate_policy(value)
    require(value['schema'] in GOLD_GROUP_POLICY_SCHEMAS,'ISSUER_CONTINUITY_PREVIOUS_SCOPE')
    return budget_group_binding(value)


def _anchor_check(anchor):
    _fields(anchor,'schema records head_sha256 history_sha256 claims_sha256 group_id group_present group_binding_sha256 group_consumed reserved unsigned_reservations continuity_event_sha256')
    require(anchor['schema']==ANCHOR_SCHEMA and type(anchor['group_present']) is bool,'ISSUER_CONTINUITY_ANCHOR')
    for key in ('records','reserved','unsigned_reservations'):submission._integer(anchor[key])
    require(anchor['unsigned_reservations']<=anchor['reserved']<=anchor['records']<=20000,'ISSUER_CONTINUITY_ANCHOR_COUNTS')
    submission._identifier(anchor['group_id'])
    for key in ('history_sha256','claims_sha256'):submission._digest(anchor[key])
    if anchor['records']:submission._digest(anchor['head_sha256'])
    else:require(anchor['head_sha256']=='','ISSUER_CONTINUITY_EMPTY_HEAD')
    if anchor['group_present']:submission._digest(anchor['group_binding_sha256'])
    else:require(anchor['group_binding_sha256'] is None,'ISSUER_CONTINUITY_ABSENT_GROUP')
    if anchor['continuity_event_sha256'] is not None:submission._digest(anchor['continuity_event_sha256'])
    _fields(anchor['group_consumed'],'submission cancellation')
    for purpose in ('submission','cancellation'):
        value=anchor['group_consumed'][purpose];_fields(value,'grants orders notional')
        submission._integer(value['grants']);submission._integer(value['orders'])
        require(value['grants']<=value['orders']<=20*value['grants'],'ISSUER_CONTINUITY_ANCHOR_ORDERS')
        require(type(value['notional']) is str and re.fullmatch(r'\d{1,30}(\.\d{1,36})?',value['notional']) is not None,
            'ISSUER_CONTINUITY_ANCHOR_AMOUNT')
        require(purpose!='cancellation' or Decimal(value['notional'])==0,'ISSUER_CONTINUITY_CANCEL_AMOUNT')
    return anchor


def operation_check(operation, *, purpose, at_ms=None):
    _fields(operation,'subject confirmation');subject=operation['subject'];confirmation=operation['confirmation']
    require(purpose in ('GROUP_CONTINUITY','DELEGATION_V2_SIGN'),'ISSUER_ADMIN_PURPOSE')
    grouped=purpose=='GROUP_CONTINUITY'
    _fields(subject,_GROUP_SUBJECT if grouped else _SIGN_SUBJECT)
    require(subject['schema']==(GROUP_SUBJECT_SCHEMA if grouped else SIGN_SUBJECT_SCHEMA)
        and subject['purpose']==purpose,'ISSUER_ADMIN_SUBJECT_SCOPE')
    require(type(subject['source_commit']) is str and re.fullmatch('[a-f0-9]{40}',subject['source_commit']) is not None,
        'ISSUER_ADMIN_SOURCE')
    start=submission._integer(subject['created_at_ms']);end=submission._integer(subject['expires_at_ms'])
    require(0<end-start<=300000,'ISSUER_ADMIN_WINDOW')
    docs=subject['documents_sha256'];_fields(docs,' '.join(GROUP_DOCUMENTS if grouped else SIGN_DOCUMENTS))
    for value in docs.values():submission._digest(value)
    _anchor_check(subject['expected_head'])
    for key,value in subject.items():
        if key.endswith('_sha256') and key!='documents_sha256':submission._digest(value)
    if grouped:require(subject['action'] in ('INIT_SCOPE','MIGRATE_BUDGET'),'ISSUER_CONTINUITY_ACTION')
    _fields(confirmation,'schema purpose subject_sha256 operator_id approved_at_ms consumer_enabled submission_enabled economic_authority')
    require(confirmation['schema']==CONFIRMATION_SCHEMA and confirmation['purpose']==purpose
        and confirmation['subject_sha256']==_digest(subject)
        and type(confirmation['operator_id']) is str and 0<len(confirmation['operator_id'])<=128
        and all(confirmation[k] is False for k in ('consumer_enabled','submission_enabled','economic_authority')),
        'ISSUER_ADMIN_CONFIRMATION')
    confirmed=submission._integer(confirmation['approved_at_ms'])
    require(start<=confirmed<end,'ISSUER_ADMIN_CONFIRMATION_TIME')
    if at_ms is not None:
        submission._integer(at_ms)
        require(confirmed<=at_ms<end,'ISSUER_ADMIN_EXPIRED')
    return subject


def _certificate_relations(certificate, *, at_ms):
    from live.execution.demo_gold_future_template import validate_template,current_template_key,PERMISSION_FIELDS
    from live.execution.demo_gold_budget_scope import is_scope, historical_scope_binding
    from live.execution.demo_issuer_policy import validate_policy,current_key,GOLD_BUDGET_POLICY_SCHEMAS
    _fields(certificate,'schema operation registry previous_authorization future_template signature_base64url')
    require(certificate['schema']==CERTIFICATE_SCHEMA,'ISSUER_CONTINUITY_CERTIFICATE')
    subject=operation_check(certificate['operation'],purpose='GROUP_CONTINUITY',at_ms=at_ms)
    previous=certificate['previous_authorization'];template=validate_template(certificate['future_template']);registry=certificate['registry']
    key=current_template_key(template,registry,at_ms)
    if subject['action']=='INIT_SCOPE':
        require(is_scope(previous) and template['origin']['kind']=='SCOPE','ISSUER_CONTINUITY_INIT_SCOPE')
        historical_scope_binding(previous,registry)
    else:
        validate_policy(previous);current_key(previous,registry,at_ms)
        require(previous['schema'] in GOLD_BUDGET_POLICY_SCHEMAS and template['origin']['kind']=='BUDGET',
            'ISSUER_CONTINUITY_MIGRATE_SCOPE')
    expected={name:deepcopy(previous[name]) for name in PERMISSION_FIELDS.split()}
    expected['bindings'].pop('execution_constraints_sha256',None)
    expected['budget_group']['exit_reserve']=deepcopy(template['budget_group']['exit_reserve'])
    require(expected=={name:template[name] for name in PERMISSION_FIELDS.split()},'ISSUER_CONTINUITY_PERMISSION_CHANGED')
    docs=subject['documents_sha256']
    require(subject['source_commit']==previous['executor_release_commit']==template['executor_release_commit']
        and subject['previous_authorization_sha256']==_digest(previous)==template['origin']['document_sha256']==docs['previous_authorization']
        and subject['future_template_sha256']==_digest(template)==docs['template']
        and subject['registry_sha256']==_digest(registry)==docs['registry']
        and subject['profile_sha256']==template['origin']['profile_sha256']==docs['profile']
        and subject['pilot_request_sha256']==template['pilot']['request_sha256']
        and subject['pilot_configuration_sha256']==template['pilot']['configuration_sha256']
        and subject['lineage_sha256']==lineage_sha256(template)
        and subject['from_group_binding_sha256']==authorization_binding(previous)
        and subject['to_group_binding_sha256']==authorization_binding(template),'ISSUER_CONTINUITY_BINDING')
    require(template['pilot']['started_at_ms']<=at_ms<template['pilot']['ends_at_ms'],'ISSUER_CONTINUITY_PILOT_WINDOW')
    return subject,key


def group_signing_payload(certificate):
    _fields(certificate,'schema operation registry previous_authorization future_template signature_base64url')
    return DOMAIN+_canonical({k:v for k,v in certificate.items() if k!='signature_base64url'})


def _row_check(row):
    _fields(row,'seq kind proposal_sha256 at_ms payload prev_hash record_hash')
    require(submission._integer(row['seq'])>0,'ISSUER_CONTINUITY_SEQUENCE')
    submission._integer(row['at_ms']);submission._digest(row['proposal_sha256']);submission._digest(row['record_hash'])
    require((row['seq']==1 and row['prev_hash']=='') or (row['seq']>1 and type(row['prev_hash']) is str and re.fullmatch('[a-f0-9]{64}',row['prev_hash']) is not None),
        'ISSUER_CONTINUITY_PREVIOUS')
    require(_digest({k:v for k,v in row.items() if k!='record_hash'})==row['record_hash']
        and len(_canonical(row['payload']))<=131072,'ISSUER_CONTINUITY_RECORD_HASH')


def verify_group_change_history(row):
    _row_check(row)
    require(row['kind'] in GROUP_KINDS,'ISSUER_CONTINUITY_EVENT_KIND')
    certificate=row['payload'];subject,key=_certificate_relations(certificate,at_ms=row['at_ms'])
    require(row['proposal_sha256']==_digest(certificate['operation'])
        and row['kind']==('GROUP_INITIALIZED' if subject['action']=='INIT_SCOPE' else 'GROUP_EXIT_RESERVE_MIGRATED'),
        'ISSUER_CONTINUITY_EVENT_BINDING')
    public=Ed25519PublicKey.from_public_bytes(submission._decode(key['public_key_base64url'],32))
    try:public.verify(submission._decode(certificate['signature_base64url'],64),group_signing_payload(certificate))
    except InvalidSignature:raise ValueError('ISSUER_CONTINUITY_SIGNATURE') from None
    template=certificate['future_template']
    return dict(operation_sha256=row['proposal_sha256'],event_sha256=row['record_hash'],event_at_ms=row['at_ms'],
        action=subject['action'],from_group_binding_sha256=subject['from_group_binding_sha256'],
        to_group_binding_sha256=subject['to_group_binding_sha256'],future_template_sha256=_digest(template),
        future_template=template,pilot=template['pilot'],lineage_sha256=subject['lineage_sha256'],certificate=certificate)


def _anchor(rows,authorization,groups):
    identity=authorization['budget_group']['group_id'];state=groups.get(identity)
    reserved=[r for r in rows if r['kind']=='RESERVED'];signed={r['proposal_sha256'] for r in rows if r['kind']=='SIGNED'}
    claims=[]
    for row in reserved:
        p=row['payload'];plan=p['plan'];is_submit=plan['purpose']=='submission'
        claims.extend((p['policy']['account_ref_sha256'],plan['purpose'],o['newClientOrderId' if is_submit else 'origClientOrderId'],row['proposal_sha256'])
            for o in plan['orders'] if is_submit)
        if not is_submit:claims.extend((p['policy']['account_ref_sha256'],plan['purpose'],o['origClientOrderId'],row['proposal_sha256']) for o in plan['commands'])
    consumed={purpose:dict(zip(('grants','orders','notional'),(state[purpose][0],state[purpose][1],format(state[purpose][2],'f'))))
        if state else dict(grants=0,orders=0,notional='0') for purpose in ('submission','cancellation')}
    return dict(schema=ANCHOR_SCHEMA,records=len(rows),head_sha256=rows[-1]['record_hash'] if rows else '',
        history_sha256=_digest(rows),claims_sha256=_digest(sorted(claims)),group_id=identity,group_present=state is not None,
        group_binding_sha256=state['binding'] if state else None,group_consumed=consumed,reserved=len(reserved),
        unsigned_reservations=sum(r['proposal_sha256'] not in signed for r in reserved),
        continuity_event_sha256=state.get('continuity_event_sha256') if state else None)


@ledger_decimal_context()
def _group_transition(groups,certificate,previous_rows):
    subject=certificate['operation']['subject'];template=certificate['future_template']
    previous=certificate['previous_authorization'];identity=template['budget_group']['group_id']
    require(subject['expected_head']==_anchor(previous_rows,previous,groups),'ISSUER_CONTINUITY_HEAD_CHANGED')
    from live.execution.demo_gold_budget_scope import SCHEMA_V2
    if previous['schema']==SCHEMA_V2:
        from live.execution.demo_gold_first_scope_history import verify_prefix
        verify_prefix(previous['scope_enrollment']['original_issuer_history'],previous_rows)
    else:
        from live.execution.demo_gold_exact_scope import verify_history
        verify_history(previous,previous_rows)
    for prior in previous_rows:
        if prior['kind'] in GROUP_KINDS:
            require(not _same_pilot(prior['payload']['future_template'],template),'ISSUER_CONTINUITY_PILOT_ALREADY_INITIALIZED')
    state=groups.get(identity)
    if subject['action']=='INIT_SCOPE':
        require(state is None,'ISSUER_CONTINUITY_GROUP_ALREADY_USED')
        state=dict(binding=subject['from_group_binding_sha256'],submission=[0,0,Decimal(0)],cancellation=[0,0,Decimal(0)])
    else:
        require(state is not None and state['binding']==subject['from_group_binding_sha256']
            and any(r['kind']=='RESERVED' and r['payload']['policy']['budget_group']['group_id']==identity
                    for r in previous_rows if r['kind']=='RESERVED' and 'budget_group' in r['payload']['policy']),
            'ISSUER_CONTINUITY_ORIGINAL_GROUP_REQUIRED')
    group=template['budget_group'];reserve=group['exit_reserve'];used=state['submission']
    require(used[0]+reserve['grants']<=group['max_grants'] and used[1]+reserve['orders']<=group['max_orders']
        and used[2]+amount(reserve['notional'])<=amount(group['max_total_notional']),'ISSUER_CONTINUITY_EXIT_SLICE_UNAVAILABLE')
    return state


@ledger_decimal_context()
def apply_group_event(groups,row,previous_rows):
    checked=verify_group_change_history(row);certificate=row['payload'];template=checked['future_template']
    require(row['seq']==len(previous_rows)+1 and row['prev_hash']==(previous_rows[-1]['record_hash'] if previous_rows else ''),
        'ISSUER_CONTINUITY_PREFIX')
    state=_group_transition(groups,certificate,previous_rows)
    identity=template['budget_group']['group_id']
    state.update(binding=checked['to_group_binding_sha256'],template=deepcopy(template),
        continuity_event_sha256=checked['event_sha256'],lineage_sha256=checked['lineage_sha256'])
    groups[identity]=state


@ledger_decimal_context()
def reduce_groups(rows):
    from live.execution.demo_issuer_store import consume_group,require_group_route
    groups={};prefix=[]
    for row in rows:
        if row['kind']=='RESERVED':
            require_group_route(groups,row['payload'])
            consume_group(groups,row['payload']['policy'],row['payload']['plan'])
        elif row['kind'] in GROUP_KINDS:apply_group_event(groups,row,prefix)
        elif row['kind'] not in ('SIGNED','DELEGATION_V2_SIGNED'):raise ValueError('ISSUER_CONTINUITY_UNKNOWN_EVENT')
        prefix.append(row)
    return groups


def history_anchor(rows, *, authorization):
    authorization_binding(authorization)
    return _anchor(rows,authorization,reduce_groups(rows))


def verify_history_prefix(anchor,rows,*,authorization):
    _anchor_check(anchor);require(anchor['records']<=len(rows),'ISSUER_CONTINUITY_PREFIX_MISSING')
    require(history_anchor(rows[:anchor['records']],authorization=authorization)==anchor,'ISSUER_CONTINUITY_PREFIX_CHANGED')


def _documents(documents, *, signing=False):
    _fields(documents,' '.join(SIGN_DOCUMENTS if signing else GROUP_DOCUMENTS))
    require(all(type(value) is dict and 0<len(_canonical(value))<=131072 for value in documents.values()),
        'ISSUER_ADMIN_DOCUMENT_BUDGET')
    require(sum(len(_canonical(value)) for value in documents.values())<=1048576,'ISSUER_ADMIN_MATERIAL_BUDGET')
    return {name:_digest(value) for name,value in documents.items()}


def _pilot_and_declarations(documents, *, now_ms):
    from live.execution.demo_gold_future_template import current_template_key
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from services.execution.backtest_authority import exact_identity_hash
    template=documents['template'];registry=documents['registry'];profile=documents['profile']
    current_template_key(template,registry,now_ms)
    original=documents['pilot'];_fields(original,'request configuration intent receipt')
    config=enrollment.completed_configuration_records(**original)
    request=original['request'];expected=dict(request_sha256=_digest(request),configuration_sha256=_digest(config),
        opening_origin_sha256=config['opening_origin_sha256'],started_at_ms=config['started_at_ms'],ends_at_ms=config['ends_at_ms'])
    require(template['pilot']==expected and config['account_ref_sha256']==template['account_ref_sha256']
        and request['source_commit']==template['executor_release_commit']
        and config['started_at_ms']<=now_ms<config['ends_at_ms'],'ISSUER_ADMIN_ORIGINAL_PILOT')
    _fields(profile,'schema environment symbol market_source_sha256 volatility_policy native_limits strategy mandate attestations initial_day_pnl initial_peak_equity minimum_plan_turnover')
    require(profile['schema']=='DEMO_REQUEST_PROFILE_V1' and profile['environment']=='BINANCE_USDM_DEMO'
        and profile['symbol']=='XAUUSDT' and _digest(profile)==template['origin']['profile_sha256'],'ISSUER_ADMIN_PROFILE')
    from live.execution.demo_gold_scope_ancestry import _profile_binding
    _profile_binding(profile,template)
    runtime=documents['runtime'];definition=documents['definition']
    extra={'GOLD_BAR_RETAINED_EPISODE_INPUT_V1':'observation_sha256 archive_source_commit entry_decision_ms',
        'GOLD_BAR_FRESH_EPISODE_INPUT_V1':'archive_source_commit candidate seconds history_sha256',
        'GOLD_BAR_AUTO_EPISODE_INPUT_V1':'archive_source_commit candidate seconds episode_id'}
    require(runtime.get('schema') in extra,'ISSUER_ADMIN_BAR_RUNTIME')
    _fields(runtime,'schema source_commit trial_manifest_sha256 '+extra[runtime['schema']])
    require(runtime['source_commit']==template['executor_release_commit'],'ISSUER_ADMIN_RUNTIME_SOURCE')
    for name in ('trial_manifest_sha256','observation_sha256','history_sha256','episode_id'):
        if name in runtime:submission._digest(runtime[name])
    require(type(runtime['archive_source_commit']) is str and re.fullmatch('[a-f0-9]{40}',runtime['archive_source_commit']) is not None,
        'ISSUER_ADMIN_STRATEGY_SOURCE')
    if 'entry_decision_ms' in runtime:submission._integer(runtime['entry_decision_ms'])
    else:require(runtime['candidate'] in ('channel','trend','reversion') and type(runtime['seconds']) is int and 1<=runtime['seconds']<=900,
        'ISSUER_ADMIN_RUNTIME_PLAN')
    require(definition['definition_hash']==exact_identity_hash({k:v for k,v in definition.items() if k!='definition_hash'},name='gold_bar_research_definition')
        ==template['budget_group']['strategy_definition_sha256']
        and definition['archive_files']['manifest.json']==runtime['trial_manifest_sha256']
        and definition['source']['commit']==runtime['archive_source_commit'],'ISSUER_ADMIN_DEFINITION')


def _dispatcher(documents, *, previous=None):
    import json
    from quantos.contracts.killswitch import KillSwitchState
    template=documents['template'];value=documents['dispatcher']
    if previous is not None:
        _fields(value,'schema present configuration kill_switch')
        require(value['schema']=='GOLD_FUTURE_DISPATCHER_PREIMAGE_V1' and type(value['present']) is bool,
            'ISSUER_ADMIN_DISPATCHER_PREIMAGE')
        scope=template['origin']['kind']=='SCOPE'
        require(value['present'] is (not scope) and (not scope or value['configuration'] is None),
            'ISSUER_ADMIN_DISPATCHER_ORIGINAL')
        stop=value['kill_switch'];config=value['configuration']
        expected=authorization_binding(previous)
    else:
        config=value;stop=config['kill_switch'];expected=authorization_binding(template)
    require(type(stop) is dict and set(stop)==set(KillSwitchState.model_fields),'ISSUER_ADMIN_STOP_FIELDS')
    state=KillSwitchState.model_validate_json(json.dumps(stop),strict=True)
    require(not state.tripped,'ISSUER_ADMIN_STOP_ACTIVE')
    if config is not None:
        _fields(config,'schema source_commit account_ref_sha256 budget_group_binding_sha256 kill_switch')
        require(config['schema']=='GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1'
            and config['source_commit']==template['executor_release_commit']
            and config['account_ref_sha256']==template['account_ref_sha256']
            and config['budget_group_binding_sha256']==expected and config['kill_switch']==stop,'ISSUER_ADMIN_DISPATCHER_BINDING')


def make_group_subject(*,documents,rows,created_at_ms,expires_at_ms):
    hashes=_documents(documents);template=documents['template'];previous=documents['previous_authorization']
    from live.execution.demo_gold_future_template import validate_template
    from live.execution.demo_gold_budget_scope import is_scope
    validate_template(template)
    subject=dict(schema=GROUP_SUBJECT_SCHEMA,purpose='GROUP_CONTINUITY',source_commit=template['executor_release_commit'],
        created_at_ms=created_at_ms,expires_at_ms=expires_at_ms,documents_sha256=hashes,
        expected_head=history_anchor(rows,authorization=previous),
        action='INIT_SCOPE' if is_scope(previous) else 'MIGRATE_BUDGET',
        previous_authorization_sha256=_digest(previous),future_template_sha256=_digest(template),registry_sha256=_digest(documents['registry']),
        profile_sha256=_digest(documents['profile']),pilot_request_sha256=template['pilot']['request_sha256'],
        pilot_configuration_sha256=template['pilot']['configuration_sha256'],lineage_sha256=lineage_sha256(template),
        from_group_binding_sha256=authorization_binding(previous),to_group_binding_sha256=authorization_binding(template))
    # A candidate is not a confirmation. Shape checks requiring a confirmation
    # are intentionally performed only after the independent operator ceremony.
    submission._integer(created_at_ms);submission._integer(expires_at_ms)
    require(0<expires_at_ms-created_at_ms<=300000,'ISSUER_ADMIN_WINDOW')
    return subject


def authorize_group_change(operation,*,documents,registry,installed_authorization,rows,now_ms):
    from live.execution.demo_gold_scope_ancestry import validate_authorization_ancestry
    subject=operation_check(operation,purpose='GROUP_CONTINUITY',at_ms=now_ms)
    hashes=_documents(documents)
    require(hashes==subject['documents_sha256'] and documents['registry']==registry
        and documents['previous_authorization']==installed_authorization,'ISSUER_ADMIN_INSTALLED_OR_MATERIAL_CHANGED')
    expected=make_group_subject(documents=documents,rows=rows,created_at_ms=subject['created_at_ms'],expires_at_ms=subject['expires_at_ms'])
    require(subject==expected,'ISSUER_ADMIN_HEAD_OR_SUBJECT_CHANGED')
    _pilot_and_declarations(documents,now_ms=now_ms);_dispatcher(documents,previous=installed_authorization)
    records=documents['ancestry'];template=documents['template']
    validate_authorization_ancestry(records,authorization=installed_authorization,registry=registry,source_commit=subject['source_commit'])
    original_scope_records=(records['scope_ancestry']
        if records['schema']=='DEMO_GOLD_SCOPE_BUDGET_INSTALLATION_ANCESTRY_V1' else records)
    from live.execution.demo_gold_budget_scope import SCHEMA_V2
    original_scope=original_scope_records.get('public_documents',{}).get('policy.json',{})
    if original_scope.get('schema')==SCHEMA_V2:
        from live.execution.demo_gold_first_scope_history import verify_prefix
        verify_prefix(original_scope['scope_enrollment']['original_issuer_history'],rows)
    require(template['origin']['setup_sha256']==_digest(records['result'])
        and template['origin']['spec_sha256']==_digest(records['spec']),'ISSUER_ADMIN_ANCESTRY_BINDING')
    if subject['action']=='INIT_SCOPE':
        request=documents['pilot']['request']
        require(request.get('budget_scope')==installed_authorization and request.get('scope_registry')==registry
            and _digest(documents['runtime'])==installed_authorization['scope_enrollment']['runtime_sha256']
            and documents['profile']==records['public_documents']['profile.json'],'ISSUER_ADMIN_SCOPE_PILOT_BINDING')
    else:
        original_profile=(records['scope_ancestry']['public_documents']['profile.json']
            if records['schema']=='DEMO_GOLD_SCOPE_BUDGET_INSTALLATION_ANCESTRY_V1' else records['profile'])
        require(documents['profile']==original_profile,'ISSUER_ADMIN_PROFILE_ORIGINAL')
    certificate=dict(schema=CERTIFICATE_SCHEMA,operation=deepcopy(operation),registry=deepcopy(registry),
        previous_authorization=deepcopy(installed_authorization),future_template=deepcopy(template),signature_base64url='')
    _certificate_relations(certificate,at_ms=now_ms)
    _group_transition(reduce_groups(rows),certificate,rows)
    require(len(_canonical(certificate))+86<=131072,'ISSUER_ADMIN_EVENT_BUDGET')
    return certificate


def sign_group_certificate(certificate,key,*,now_ms):
    _,registered=_certificate_relations(certificate,at_ms=now_ms)
    require(isinstance(key,Ed25519PrivateKey) and hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()==registered['public_key_sha256'],
        'ISSUER_PRIVATE_KEY_MISMATCH')
    value=deepcopy(certificate)
    value['signature_base64url']=base64.urlsafe_b64encode(key.sign(group_signing_payload(value))).rstrip(b'=').decode()
    return value


def final_group_time(certificate, *, started_at_ms, clock_ms, last_at_ms=None):
    now=started_at_ms if clock_ms is None else clock_ms()
    submission._integer(now)
    require(now>=started_at_ms and (last_at_ms is None or now>=last_at_ms),'ISSUER_ADMIN_CLOCK_REVERSED')
    _certificate_relations(certificate,at_ms=now)
    return now


def make_sign_subject(*,documents,rows,created_at_ms,expires_at_ms):
    hashes=_documents(documents,signing=True);template=documents['template']
    from live.execution.demo_gold_future_template import validate_template
    validate_template(template)
    submission._integer(created_at_ms);submission._integer(expires_at_ms)
    require(0<expires_at_ms-created_at_ms<=300000,'ISSUER_ADMIN_WINDOW')
    return dict(schema=SIGN_SUBJECT_SCHEMA,purpose='DELEGATION_V2_SIGN',source_commit=template['executor_release_commit'],
        created_at_ms=created_at_ms,expires_at_ms=expires_at_ms,documents_sha256=hashes,
        expected_head=history_anchor(rows,authorization=template),continuity_event_sha256=documents['continuity_event']['record_hash'],
        future_template_sha256=_digest(template),registry_sha256=_digest(documents['registry']),
        delegation_intent_sha256=_digest(documents['delegation_intent']))


def _committed_group(event_sha256,rows,template):
    matching=[r for r in rows if r['kind'] in GROUP_KINDS and r['record_hash']==event_sha256]
    require(len(matching)==1,'ISSUER_ADMIN_COMMITTED_GROUP_REQUIRED')
    row=matching[0];checked=verify_group_change_history(row)
    require(checked['future_template']==template,'ISSUER_ADMIN_COMMITTED_TEMPLATE')
    state=reduce_groups(rows).get(template['budget_group']['group_id'])
    require(state is not None and state.get('continuity_event_sha256')==event_sha256
        and state['binding']==authorization_binding(template),'ISSUER_ADMIN_ACTIVE_GROUP')
    return row


def authorize_delegation_sign(operation,*,documents,registry,installed_template,rows,now_ms):
    from live.execution import demo_gold_execution_delegation as delegation
    subject=operation_check(operation,purpose='DELEGATION_V2_SIGN',at_ms=now_ms)
    hashes=_documents(documents,signing=True)
    require(subject['documents_sha256']==hashes and documents['registry']==registry
        and documents['template']==installed_template,'ISSUER_ADMIN_INSTALLED_OR_MATERIAL_CHANGED')
    require(subject==make_sign_subject(documents=documents,rows=rows,created_at_ms=subject['created_at_ms'],expires_at_ms=subject['expires_at_ms']),
        'ISSUER_ADMIN_HEAD_OR_SUBJECT_CHANGED')
    _pilot_and_declarations(documents,now_ms=now_ms);_dispatcher(documents)
    event=_committed_group(subject['continuity_event_sha256'],rows,installed_template)
    require(event==documents['continuity_event'],'ISSUER_ADMIN_CONTINUITY_ORIGINAL')
    intent=documents['delegation_intent'];runtime=documents['runtime'];definition=documents['definition']
    require(intent['runtime_sha256']==_digest(runtime)
        and intent['strategy_source_commit']==runtime['archive_source_commit']
        and intent['trial_manifest_sha256']==runtime['trial_manifest_sha256']
        and intent['pilot_configuration']==documents['pilot']['configuration']
        and intent['entry_timeout_ms']==definition['size_and_timing']['entry_timeout_ms']
        and intent['max_holding_ms']<=definition['rules']['max_holding_bars']*900000,'ISSUER_ADMIN_DELEGATION_DECLARATIONS')
    artifact=delegation.build_delegation_v2(intent,future_template=installed_template,
        confirmation=operation['confirmation'],continuity_event=event)
    receipt=dict(schema=SIGN_RECEIPT_SCHEMA,operation=deepcopy(operation),registry=deepcopy(registry),
        artifact=artifact,artifact_sha256=_digest(artifact))
    require(len(_canonical(receipt))+86<=131072,'ISSUER_ADMIN_EVENT_BUDGET')
    return receipt


def sign_delegation_receipt(receipt,key,*,now_ms):
    from live.execution.demo_gold_future_template import current_template_key
    from live.execution import demo_gold_execution_delegation as delegation
    value=deepcopy(receipt);artifact=value['artifact']
    registered=current_template_key(artifact['future_template'],value['registry'],now_ms)
    require(isinstance(key,Ed25519PrivateKey) and hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()==registered['public_key_sha256'],
        'ISSUER_PRIVATE_KEY_MISMATCH')
    artifact['signature_base64url']=base64.urlsafe_b64encode(key.sign(delegation.signing_payload(artifact))).rstrip(b'=').decode()
    value['artifact_sha256']=_digest(artifact)
    return value


def verify_delegation_sign_history(row,previous_rows):
    from live.execution import demo_gold_execution_delegation as delegation
    _row_check(row);require(row['kind']=='DELEGATION_V2_SIGNED','ISSUER_ADMIN_SIGN_EVENT_KIND')
    receipt=row['payload'];_fields(receipt,'schema operation registry artifact artifact_sha256')
    require(receipt['schema']==SIGN_RECEIPT_SCHEMA,'ISSUER_ADMIN_SIGN_RECEIPT')
    subject=operation_check(receipt['operation'],purpose='DELEGATION_V2_SIGN',at_ms=row['at_ms'])
    artifact=receipt['artifact'];template=artifact['future_template'];registry=receipt['registry']
    require(subject['expected_head']==history_anchor(previous_rows,authorization=template)
        and row['seq']==len(previous_rows)+1 and row['prev_hash']==(previous_rows[-1]['record_hash'] if previous_rows else ''),
        'ISSUER_ADMIN_SIGN_PREFIX_CHANGED')
    event=_committed_group(subject['continuity_event_sha256'],previous_rows,template)
    intent={k:deepcopy(artifact['terms'][k]) for k in delegation.INTENT_FIELDS.split()}
    expected=delegation.build_delegation_v2(intent,future_template=template,confirmation=receipt['operation']['confirmation'],continuity_event=event)
    docs=subject['documents_sha256']
    require(row['proposal_sha256']==_digest(receipt['operation'])
        and receipt['artifact_sha256']==_digest(artifact)
        and subject['source_commit']==template['executor_release_commit']
        and subject['future_template_sha256']==_digest(template)==docs['template']
        and subject['registry_sha256']==_digest(registry)==docs['registry']
        and subject['delegation_intent_sha256']==_digest(intent)==docs['delegation_intent']
        and docs['continuity_event']==_digest(event)
        and docs['profile']==template['origin']['profile_sha256']
        and docs['runtime']==artifact['terms']['runtime_sha256']
        and {k:v for k,v in artifact.items() if k!='signature_base64url'}=={k:v for k,v in expected.items() if k!='signature_base64url'},
        'ISSUER_ADMIN_SIGN_ARTIFACT_BINDING')
    delegation.verify(artifact,registry=registry,source_commit=subject['source_commit'],
        account_ref_sha256=template['account_ref_sha256'],now_ms=row['at_ms'],continuity_event=event)
    return artifact


def final_sign_time(receipt,*,started_at_ms,clock_ms,rows,last_at_ms=None):
    from live.execution import demo_gold_execution_delegation as delegation
    now=started_at_ms if clock_ms is None else clock_ms();submission._integer(now)
    require(now>=started_at_ms and (last_at_ms is None or now>=last_at_ms),'ISSUER_ADMIN_CLOCK_REVERSED')
    operation_check(receipt['operation'],purpose='DELEGATION_V2_SIGN',at_ms=now)
    artifact=receipt['artifact'];template=artifact['future_template']
    require(template['pilot']['started_at_ms']<=now<template['pilot']['ends_at_ms'],'ISSUER_ADMIN_ORIGINAL_PILOT')
    event=_committed_group(artifact['continuity_event_sha256'],rows,template)
    delegation.verify(artifact,registry=receipt['registry'],source_commit=template['executor_release_commit'],
        account_ref_sha256=template['account_ref_sha256'],now_ms=now,continuity_event=event)
    return now


CANCEL_VIEW_SCHEMA='DEMO_GOLD_TEMPLATE_CANCEL_VIEW_V1'
CANCEL_COMPACT_SCHEMA='DEMO_GOLD_TEMPLATE_CANCEL_RESERVATION_V1'


def _cancel_exact(view):
    """Internal declaration projection; never an exported submit authority."""
    from live.execution.demo_issuer_policy import GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA,validate_policy
    require(type(view) is dict and view.get('schema')==CANCEL_VIEW_SCHEMA,'ISSUER_CANCEL_VIEW_SCOPE')
    result=deepcopy(view);result['schema']=GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA
    validate_policy(result)
    return result


def validate_cancel_view(view):
    _cancel_exact(view)
    return view


def derive_cancel_view(template,*,original_reserved,original_signed,rows,registry,now_ms):
    from live.execution.demo_gold_future_template import current_template_key,PERMISSION_FIELDS
    from live.execution.demo_issuer_policy import budget_group_binding
    from live.execution.demo_issuer_store import validate_reservation,unsigned_envelope
    current_template_key(template,registry,now_ms)
    state=reduce_groups(rows).get(template['budget_group']['group_id'])
    require(state is not None and state.get('template')==template,'ISSUER_CANCEL_CURRENT_TEMPLATE')
    event=_committed_group(state['continuity_event_sha256'],rows,template)
    for record,kind in ((original_reserved,'RESERVED'),(original_signed,'SIGNED')):
        _row_check(record)
        require(record['kind']==kind and sum(record==r for r in rows)==1,'ISSUER_CANCEL_NATIVE_ANCESTRY')
    require(original_reserved['seq']<original_signed['seq']
        and original_reserved['proposal_sha256']==original_signed['proposal_sha256'],'ISSUER_CANCEL_ORIGINAL_NOT_ISSUED')
    original=original_reserved['payload'];validate_reservation(original)
    plan=original['plan'];policy=original['policy']
    checked=verify_group_change_history(event)
    require(plan['purpose']=='submission' and budget_group_binding(policy) in
        (checked['from_group_binding_sha256'],checked['to_group_binding_sha256'])
        and original['registry']==registry,'ISSUER_CANCEL_ORIGINAL_LINEAGE')
    envelope=original_signed['payload']['envelope'];expected=unsigned_envelope(plan,policy)
    require({k:v for k,v in envelope.items() if k!='signature_base64url'}=={k:v for k,v in expected.items() if k!='signature_base64url'},
        'ISSUER_CANCEL_ORIGINAL_ENVELOPE')
    submission.verify(envelope,registry=original['registry'],expected_context=plan['context'],orders=plan['orders'],now_ms=original_signed['at_ms'])
    compact=dict(schema=CANCEL_COMPACT_SCHEMA,future_template_sha256=_digest(template),
        continuity_event_sha256=event['record_hash'],original_reserved_record_sha256=original_reserved['record_hash'],
        original_signed_record_sha256=original_signed['record_hash'],original_proposal_sha256=original_reserved['proposal_sha256'],
        issuer_anchor=history_anchor(rows,authorization=template))
    identity=_digest({k:v for k,v in compact.items() if k!='issuer_anchor'})
    view={k:deepcopy(template[k]) for k in PERMISSION_FIELDS.split()}
    view.update(schema=CANCEL_VIEW_SCHEMA,policy_id='gold-cancel-'+identity)
    view['bindings']['execution_constraints_sha256']=policy['bindings']['execution_constraints_sha256']
    validate_cancel_view(view)
    return dict(policy=view,cancellation=compact)


def verify_cancellation_history(value,rows):
    compact=value['cancellation'];view=value['policy'];plan=value['plan']
    _fields(compact,'schema future_template_sha256 continuity_event_sha256 original_reserved_record_sha256 original_signed_record_sha256 original_proposal_sha256 issuer_anchor')
    require(compact['schema']==CANCEL_COMPACT_SCHEMA and plan['purpose']=='cancellation','ISSUER_CANCEL_COMPACT_SCOPE')
    for name in compact:
        if name.endswith('_sha256'):submission._digest(compact[name])
    def one(identity,kind):
        matched=[r for r in rows if r['record_hash']==identity and r['kind'] in kind]
        require(len(matched)==1,'ISSUER_CANCEL_NATIVE_ANCESTRY')
        return matched[0]
    event=one(compact['continuity_event_sha256'],GROUP_KINDS)
    template=verify_group_change_history(event)['future_template']
    reserved=one(compact['original_reserved_record_sha256'],('RESERVED',));signed=one(compact['original_signed_record_sha256'],('SIGNED',))
    rebuilt=derive_cancel_view(template,original_reserved=reserved,original_signed=signed,rows=rows,
        registry=value['registry'],now_ms=plan['issued_at_ms'])
    require(view==rebuilt['policy'] and compact==rebuilt['cancellation'],'ISSUER_CANCEL_VIEW_DERIVATION')
    original={o['newClientOrderId']:o for o in reserved['payload']['plan']['orders']}
    require(plan['context']['original_batch_sha256']==reserved['payload']['plan']['context']['batch_sha256']
        and all(c['origClientOrderId'] in original and c['symbol']==original[c['origClientOrderId']]['symbol'] for c in plan['commands']),
        'ISSUER_CANCEL_ORIGINAL_COMMANDS')
