"""Native ledger continuity over real unsigned reservations and signed admin rows.

Fixture keys and declarations are synthetic. Direct event construction below
isolates historical replay; current public-material authorization has separate
native Scope installation tests. No live account or installed state is read.
"""
from copy import deepcopy
from contextlib import closing
import base64
import importlib
import pytest
from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_store import IssuerStore
from tests.live.test_gold_strategy_constraints import case as case, NOW
from tests.live.test_gold_strategy_budget_group import grouped, handoff


def module():
    return importlib.import_module('live.execution.demo_issuer_group_continuity')


def retained_budget(case, tmp_path, monkeypatch):
    from live.execution import demo_authority
    from live.execution.demo_issuer_policy import review_submission
    from services.validation.demo_submission import registry_sha256
    private, _, _, original_registry, _, store = case
    registry=deepcopy(original_registry)
    registry['keys'][0]['valid_until_ms']=NOW+86400000+600000
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:deepcopy(registry))
    args=grouped(case,limit='unlimited');policy=args['issuer_policy']
    policy['registry_sha256']=registry_sha256(registry)
    policy['valid_until_ms']=registry['keys'][0]['valid_until_ms']
    policy['budget_group']['valid_until_ms']=policy['valid_until_ms']
    first,previous=handoff(args,tmp_path,1)
    plan=review_submission(first,policy=previous,registry=registry,now_ms=NOW+11)
    store.reserve(plan,policy=previous,registry=registry)
    template={k:deepcopy(v) for k,v in previous.items() if k!='policy_id'}
    template.update(schema='DEMO_GOLD_FUTURE_EXIT_BUDGET_TEMPLATE_V1',template_id='synthetic-future-template',
        origin=dict(kind='BUDGET',document_sha256=_digest(previous),profile_sha256=_digest(args['profile']),
                    setup_sha256='3'*64,spec_sha256='4'*64),
        pilot=dict(request_sha256='5'*64,configuration_sha256='6'*64,opening_origin_sha256='7'*64,
                   started_at_ms=NOW,ends_at_ms=NOW+86400000),
        executable=False,submission_enabled=False,economic_authority=False)
    template['bindings'].pop('execution_constraints_sha256')
    template['budget_group']['exit_reserve']=dict(grants=1,orders=1,notional='120')
    config=dict(schema='GOLD_DEMO_PILOT_CONTROL_V1',environment='BINANCE_USDM_DEMO',symbol='XAUUSDT',
        account_ref_sha256=template['account_ref_sha256'],opening_origin_sha256=template['pilot']['opening_origin_sha256'],
        initial_equity='5000',started_at_ms=NOW,ends_at_ms=NOW+86400000,max_loss_fraction='0.05',submission_enabled=False)
    template['pilot']['configuration_sha256']=_digest(config)
    return dict(private=private,registry=registry,previous=previous,template=template,store=store,args=args,plan=plan,config=config)


def signed_group_row(f, *, rows=None, changes=None):
    """An actual signature exercises historical replay, not APPLY custody."""
    contract=module();rows=f['store'].snapshot() if rows is None else rows
    template=f['template'];previous=f['previous']
    from live.execution.demo_issuer_policy import budget_group_binding
    from live.execution.demo_gold_future_template import template_group_binding
    documents={k:'8'*64 for k in contract.GROUP_DOCUMENTS}
    documents.update(previous_authorization=_digest(previous),template=_digest(template),registry=_digest(f['registry']),
        profile=template['origin']['profile_sha256'],pilot='9'*64)
    subject=dict(schema=contract.GROUP_SUBJECT_SCHEMA,purpose='GROUP_CONTINUITY',source_commit='c'*40,
        created_at_ms=NOW+12,expires_at_ms=NOW+1000,documents_sha256=documents,
        expected_head=contract.history_anchor(rows,authorization=previous),action='MIGRATE_BUDGET',
        previous_authorization_sha256=_digest(previous),future_template_sha256=_digest(template),
        registry_sha256=_digest(f['registry']),profile_sha256=template['origin']['profile_sha256'],
        pilot_request_sha256=template['pilot']['request_sha256'],pilot_configuration_sha256=template['pilot']['configuration_sha256'],
        lineage_sha256=contract.lineage_sha256(template),from_group_binding_sha256=budget_group_binding(previous),
        to_group_binding_sha256=template_group_binding(template))
    if changes:changes(subject)
    confirmation=dict(schema=contract.CONFIRMATION_SCHEMA,purpose='GROUP_CONTINUITY',subject_sha256=_digest(subject),
        operator_id='synthetic-history-operator',approved_at_ms=NOW+13,
        consumer_enabled=False,submission_enabled=False,economic_authority=False)
    operation=dict(subject=subject,confirmation=confirmation)
    certificate=dict(schema=contract.CERTIFICATE_SCHEMA,operation=operation,registry=f['registry'],
        previous_authorization=previous,future_template=template,signature_base64url='')
    certificate['signature_base64url']=base64.urlsafe_b64encode(f['private'].sign(contract.group_signing_payload(certificate))).rstrip(b'=').decode()
    row=dict(seq=len(rows)+1,kind='GROUP_EXIT_RESERVE_MIGRATED',proposal_sha256=_digest(operation),at_ms=NOW+14,
             payload=certificate,prev_hash=rows[-1]['record_hash'] if rows else '')
    row['record_hash']=_digest(row)
    return row


def append_historical(store,row):
    store.db.execute('BEGIN IMMEDIATE')
    try:
        store._append(row['kind'],row['proposal_sha256'],row['payload'],row['at_ms'],store.snapshot())
        store._rows();store.db.commit()
    except BaseException:
        store.db.rollback();raise


def test_real_unsigned_budget_migration_keeps_consumption_and_rejects_old_binding(case,tmp_path,monkeypatch):
    f=retained_budget(case,tmp_path,monkeypatch);before=f['store'].snapshot()
    row=signed_group_row(f);checked=module().verify_group_change_history(row)
    assert checked['from_group_binding_sha256']!=checked['to_group_binding_sha256']
    append_historical(f['store'],row)
    with closing(IssuerStore(f['store'].path)) as reopened:
        rows=reopened.snapshot();state=module().reduce_groups(rows)[f['previous']['budget_group']['group_id']]
        assert rows[:1]==before and state['submission'][0:2]==[1,1]
        assert str(state['submission'][2])==f['plan']['notional']
        anchor=module().history_anchor(rows,authorization=f['template'])
        assert anchor['unsigned_reservations']==1 and anchor['reserved']==1
        later,policy=handoff(f['args'],tmp_path,2)
        from live.execution.demo_issuer_policy import review_submission
        plan=review_submission(later,policy=policy,registry=f['registry'],now_ms=NOW+15)
        with pytest.raises(ValueError,match='GROUP_ID_REBOUND'):
            reopened.reserve(plan,policy=policy,registry=f['registry'])
        assert reopened.snapshot()==rows


@pytest.mark.parametrize('damage',['head','prior_usage','from','to','profile','source','confirmation','signature'])
def test_resigned_administrative_history_rejects_rebinding_or_untrue_prefix(case,tmp_path,monkeypatch,damage):
    f=retained_budget(case,tmp_path,monkeypatch)
    def change(subject):
        if damage=='head':subject['expected_head']['head_sha256']='0'*64
        elif damage=='prior_usage':subject['expected_head']['group_consumed']['submission']['grants']=0
        elif damage=='from':subject['from_group_binding_sha256']='0'*64
        elif damage=='to':subject['to_group_binding_sha256']='0'*64
        elif damage=='profile':subject['profile_sha256']='0'*64
        elif damage=='source':subject['source_commit']='0'*40
    row=signed_group_row(f,changes=change)
    if damage=='confirmation':row['payload']['operation']['confirmation']['economic_authority']=True
    elif damage=='signature':row['payload']['signature_base64url']='A'*86
    if damage in ('confirmation','signature'):
        row['record_hash']=_digest({k:v for k,v in row.items() if k!='record_hash'})
    before=f['store'].snapshot()
    with pytest.raises(ValueError):append_historical(f['store'],row)
    assert f['store'].snapshot()==before


def test_migration_cannot_carve_reserve_larger_than_original_unspent_slice(case,tmp_path,monkeypatch):
    f=retained_budget(case,tmp_path,monkeypatch)
    group=f['template']['budget_group'];group['exit_reserve']['notional']=group['max_total_notional']
    with pytest.raises(ValueError):signed_group_row(f)

# Native nonexecuting Scope -> real original baseline -> empty installed issuer
# journal. This path requires no exact decision and creates no RESERVED row.
from tests.live.test_demo_market_partition import book as book
from tests.live.test_demo_market_partition_runtime import installed as installed
from tests.live.test_gold_first_enrollment_review import first as first
from tests.tools.test_gold_first_enrollment_materials import materials as materials
from tests.tools.test_gold_budget_scope_preparation import scope_inputs as scope_inputs
from tests.tools.test_gold_budget_scope_materials import day_materials as day_materials
from tests.tools.test_gold_budget_scope_install import local as local
from tests.live.test_demo_gold_scope_ancestry import scope_records as scope_records


from tests.tools.test_gold_future_budget_materials import native_scope as native_scope,scope_exact_install as scope_exact_install


@pytest.fixture
def scope_admin_case(native_scope):
    import json
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from services.validation import demo_submission as auth
    f=native_scope
    captured=f['tool'].capture(source_commit='c'*40,exit_reserve=dict(grants=1,orders=1,notional='250'))
    docs=captured['documents']
    keydoc=json.loads((f['local']['cfg']/'signing-key.json').read_bytes())
    private=Ed25519PrivateKey.from_private_bytes(auth._decode(keydoc['private_key_base64url'],32))
    store=IssuerStore(f['issuer']/'issuance.sqlite3')
    yield dict(documents=docs,template=docs['template'],previous=f['scope'],registry=f['registry'],
        private=private,store=store,now_ms=f['local']['clock'][0]+1)
    store.close()


def operation_for(f, *, sign=False):
    contract=module();builder=contract.make_sign_subject if sign else contract.make_group_subject
    subject=builder(documents=f['documents'],rows=f['store'].snapshot(),created_at_ms=f['now_ms'],expires_at_ms=f['now_ms']+1000)
    return dict(subject=subject,confirmation=dict(schema=contract.CONFIRMATION_SCHEMA,
        purpose=subject['purpose'],subject_sha256=_digest(subject),operator_id='isolated-new-future-confirmation',
        approved_at_ms=f['now_ms'],consumer_enabled=False,submission_enabled=False,economic_authority=False))


def test_native_scope_baseline_can_initialize_empty_original_group_without_exact_approval(scope_admin_case):
    f=scope_admin_case;before=deepcopy(f['documents']['pilot']);operation=operation_for(f)
    record=f['store'].group_change(operation,documents=f['documents'],registry=f['registry'],
        installed_authorization=f['previous'],key=f['private'],now_ms=f['now_ms'],clock_ms=lambda:f['now_ms']+1)
    assert record['kind']=='GROUP_INITIALIZED'
    assert f['store'].snapshot()==[record]
    assert f['documents']['pilot']==before
    assert f['store'].db.execute('SELECT COUNT(*) FROM issuer_order_claims').fetchone()[0]==0
    assert module().history_anchor([record],authorization=f['template'])['reserved']==0
    assert f['store'].query_admin(_digest(operation),purpose='GROUP_CONTINUITY')==record
    from live.execution.demo_issuer_policy import validate_policy
    with pytest.raises(ValueError):validate_policy(f['template'])


@pytest.mark.parametrize('advance',['subject','pilot','key','backward'])
def test_admin_final_commit_clock_crossing_deadline_or_rollback_never_appends(scope_admin_case,advance):
    f=scope_admin_case;operation=operation_for(f)
    final={'subject':operation['subject']['expires_at_ms'],'pilot':f['template']['pilot']['ends_at_ms'],
        'key':f['registry']['keys'][-1]['valid_until_ms'],'backward':f['now_ms']-1}[advance]
    with pytest.raises(ValueError):
        f['store'].group_change(operation,documents=f['documents'],registry=f['registry'],
            installed_authorization=f['previous'],key=f['private'],now_ms=f['now_ms'],clock_ms=lambda:final)
    assert f['store'].snapshot()==[]


def initialized_scope(f):
    operation=operation_for(f)
    return f['store'].group_change(operation,documents=f['documents'],registry=f['registry'],
        installed_authorization=f['previous'],key=f['private'],now_ms=f['now_ms'])


def signing_case(f,record):
    docs={k:deepcopy(v) for k,v in f['documents'].items() if k in module().SIGN_DOCUMENTS}
    template=f['template'];runtime=docs['runtime'];config=docs['pilot']['configuration']
    docs['dispatcher']=dict(schema='GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1',source_commit='c'*40,
        account_ref_sha256=template['account_ref_sha256'],budget_group_binding_sha256=module().authorization_binding(template),
        kill_switch=docs['dispatcher']['kill_switch'])
    docs['continuity_event']=deepcopy(record)
    docs['delegation_intent']=dict(original_budget_group_binding_sha256=module().authorization_binding(f['previous']),
        active_budget_group_binding_sha256=module().authorization_binding(template),runtime_sha256=_digest(runtime),
        strategy_source_commit=runtime['archive_source_commit'],trial_manifest_sha256=runtime['trial_manifest_sha256'],
        pilot_request_sha256=template['pilot']['request_sha256'],pilot_configuration=config,
        opening_until_ms=config['ends_at_ms']-60000,exit_until_ms=template['valid_until_ms'],
        max_holding_ms=900000,entry_timeout_ms=docs['definition']['size_and_timing']['entry_timeout_ms'])
    return {**f,'documents':docs,'now_ms':f['now_ms']+2}


def test_native_scope_group_then_independent_delegation_signature_is_queryable_after_restart(scope_admin_case):
    f=scope_admin_case;record=initialized_scope(f);signed=signing_case(f,record);op=operation_for(signed,sign=True)
    row=f['store'].sign_delegation(op,documents=signed['documents'],registry=f['registry'],
        installed_template=f['template'],key=f['private'],now_ms=signed['now_ms'],clock_ms=lambda:signed['now_ms']+1)
    assert row['kind']=='DELEGATION_V2_SIGNED' and row['payload']['artifact']['continuity_event_sha256']==record['record_hash']
    assert row['payload']['artifact']['future_template']==f['template']
    assert row['payload']['artifact_sha256']==_digest(row['payload']['artifact'])
    from live.execution import demo_gold_execution_delegation as delegation
    assert delegation.verify(row['payload']['artifact'],registry=f['registry'],source_commit='c'*40,
        account_ref_sha256=f['template']['account_ref_sha256'],now_ms=signed['now_ms']+1,continuity_event=record)
    with closing(IssuerStore(f['store'].path,read_only=True)) as reopened:
        assert reopened.query_admin(_digest(op),purpose='DELEGATION_V2_SIGN')==row
        assert [r['kind'] for r in reopened.snapshot()]==['GROUP_INITIALIZED','DELEGATION_V2_SIGNED']
    with pytest.raises(ValueError,match='OPERATION_EXISTS'):
        f['store'].sign_delegation(op,documents=signed['documents'],registry=f['registry'],
            installed_template=f['template'],key=f['private'],now_ms=signed['now_ms'])


@pytest.mark.parametrize('damage',['uncommitted','installed','stale','clock_rollback','time_after_replay'])
def test_delegation_sign_cannot_use_missing_event_or_change_installed_template_or_final_time(scope_admin_case,damage):
    f=scope_admin_case;record=initialized_scope(f);signed=signing_case(f,record);op=operation_for(signed,sign=True)
    installed=f['template'];clock=lambda:signed['now_ms']+1
    if damage=='uncommitted':
        signed['documents']['continuity_event']['record_hash']='0'*64
    elif damage=='installed':installed=deepcopy(installed);installed['template_id']='another-uninstalled-template'
    elif damage=='stale':clock=lambda:op['subject']['expires_at_ms']
    elif damage=='clock_rollback':clock=lambda:signed['now_ms']-1
    else:
        calls=iter([signed['now_ms']+1,op['subject']['expires_at_ms']]);clock=lambda:next(calls)
    with pytest.raises(ValueError):
        f['store'].sign_delegation(op,documents=signed['documents'],registry=f['registry'],
            installed_template=installed,key=f['private'],now_ms=signed['now_ms'],clock_ms=clock)
    assert f['store'].snapshot()==[record]


def signed_delegation_row(f,group):
    """Real V2 signature for native historical replay; no mocked admin verifier."""
    from live.execution import demo_gold_execution_delegation as delegation
    c=module();rows=f['store'].snapshot();template=f['template']
    intent=dict(original_budget_group_binding_sha256=c.authorization_binding(f['previous']),
        active_budget_group_binding_sha256=c.authorization_binding(template),runtime_sha256='3'*64,
        strategy_source_commit='4'*40,trial_manifest_sha256='5'*64,pilot_request_sha256=template['pilot']['request_sha256'],
        pilot_configuration=f['config'],opening_until_ms=f['config']['ends_at_ms']-60000,
        exit_until_ms=template['valid_until_ms'],max_holding_ms=900000,entry_timeout_ms=5000)
    docs={k:'9'*64 for k in c.SIGN_DOCUMENTS}
    docs.update(template=_digest(template),registry=_digest(f['registry']),delegation_intent=_digest(intent),
        continuity_event=_digest(group),profile=template['origin']['profile_sha256'],runtime=intent['runtime_sha256'])
    subject=dict(schema=c.SIGN_SUBJECT_SCHEMA,purpose='DELEGATION_V2_SIGN',source_commit='c'*40,
        created_at_ms=NOW+14,expires_at_ms=NOW+1000,documents_sha256=docs,expected_head=c.history_anchor(rows,authorization=template),
        continuity_event_sha256=group['record_hash'],future_template_sha256=_digest(template),registry_sha256=_digest(f['registry']),
        delegation_intent_sha256=_digest(intent))
    confirmation=dict(schema=c.CONFIRMATION_SCHEMA,purpose='DELEGATION_V2_SIGN',subject_sha256=_digest(subject),
        operator_id='synthetic-sign-history',approved_at_ms=NOW+14,consumer_enabled=False,submission_enabled=False,economic_authority=False)
    operation=dict(subject=subject,confirmation=confirmation)
    artifact=delegation.build_delegation_v2(intent,future_template=template,confirmation=confirmation,continuity_event=group)
    artifact['signature_base64url']=base64.urlsafe_b64encode(f['private'].sign(delegation.signing_payload(artifact))).rstrip(b'=').decode()
    payload=dict(schema=c.SIGN_RECEIPT_SCHEMA,operation=operation,registry=f['registry'],artifact=artifact,artifact_sha256=_digest(artifact))
    row=dict(seq=len(rows)+1,kind='DELEGATION_V2_SIGNED',proposal_sha256=_digest(operation),at_ms=NOW+14,
        payload=payload,prev_hash=rows[-1]['record_hash'])
    row['record_hash']=_digest(row)
    return row


def future_reservation(f,tmp_path):
    """Store-only input: no claim that this synthetic proof hash was admitted."""
    from live.execution.demo_gold_future_template import derive_exact_policy
    from live.execution.demo_issuer_policy import review_submission
    second,_=handoff(f['args'],tmp_path,2);identity='f'*64
    policy=derive_exact_policy(f['template'],proof_sha256=identity,constraints=second['proposal']['execution_constraints'],
        orders=second['proposal']['orders'],now_ms=NOW+15)
    plan=review_submission(second,policy=policy,registry=f['registry'],now_ms=NOW+15)
    plan['context']['delegated_admission_sha256']=identity
    plan['proposal_sha256']=_digest(plan)
    rows=f['store'].snapshot()
    group=next(r for r in rows if r['kind'] in module().GROUP_KINDS)
    signed=signed_delegation_row(f,group);append_historical(f['store'],signed)
    rows=f['store'].snapshot();anchor=module().history_anchor(rows,authorization=f['template'])
    compact=dict(schema='DEMO_GOLD_DELEGATED_ISSUER_RESERVATION_V2',issuer_anchor=anchor,enable_issuer_anchor=deepcopy(anchor),
        delegated_admission_sha256=identity,enable_sha256='e'*64,delegation_sha256=signed['payload']['artifact_sha256'],
        base_policy_sha256=_digest(f['template']),continuity_event_sha256=group['record_hash'])
    return plan,policy,compact


def test_v2_reservation_follows_real_prior_template_event_and_both_native_prefixes(case,tmp_path,monkeypatch):
    f=retained_budget(case,tmp_path,monkeypatch);event=signed_group_row(f);append_historical(f['store'],event)
    plan,policy,compact=future_reservation(f,tmp_path)
    f['store'].reserve(plan,policy=policy,registry=f['registry'],delegation=compact)
    with closing(IssuerStore(f['store'].path)) as reopened:
        rows=reopened.snapshot();state=module().reduce_groups(rows)[policy['budget_group']['group_id']]
        assert state['submission'][0:2]==[2,2]
        assert rows[-1]['payload']['delegation']==compact
        assert module().history_anchor(rows,authorization=f['template'])['unsigned_reservations']==2


@pytest.mark.parametrize('damage',['event','base','enable_anchor','old_schema','permission','purpose'])
def test_v2_compact_cannot_invent_template_continuity_or_drop_later_prefix(case,tmp_path,monkeypatch,damage):
    f=retained_budget(case,tmp_path,monkeypatch);append_historical(f['store'],signed_group_row(f))
    plan,policy,compact=future_reservation(f,tmp_path)
    if damage=='event':compact['continuity_event_sha256']='0'*64
    elif damage=='base':compact['base_policy_sha256']='0'*64
    elif damage=='enable_anchor':compact['enable_issuer_anchor']['head_sha256']='0'*64
    elif damage=='old_schema':compact['schema']='DEMO_GOLD_DELEGATED_ISSUER_RESERVATION_V1'
    elif damage=='permission':policy['max_orders']+=1
    else:plan['purpose']='cancellation'
    before=f['store'].snapshot()
    with pytest.raises(ValueError):f['store'].reserve(plan,policy=policy,registry=f['registry'],delegation=compact)
    assert f['store'].snapshot()==before


def test_second_independently_signed_group_cannot_reuse_original_pilot_baseline(scope_admin_case):
    from live.execution import demo_gold_budget_scope as scope
    f=scope_admin_case;first=initialized_scope(f);rows=f['store'].snapshot()
    certificate=deepcopy(first['payload']);previous=certificate['previous_authorization'];template=certificate['future_template']
    previous['budget_group']['group_id']='synthetic-second-group'
    previous['signature_base64url']=base64.urlsafe_b64encode(f['private'].sign(scope.signed_bytes(previous))).rstrip(b'=').decode()
    template['budget_group']['group_id']=previous['budget_group']['group_id']
    template['origin']['document_sha256']=_digest(previous)
    template['pilot'].update(request_sha256='a'*64,configuration_sha256='b'*64)
    subject=certificate['operation']['subject']
    subject.update(expected_head=module().history_anchor(rows,authorization=previous),previous_authorization_sha256=_digest(previous),
        future_template_sha256=_digest(template),pilot_request_sha256=template['pilot']['request_sha256'],
        pilot_configuration_sha256=template['pilot']['configuration_sha256'],lineage_sha256=module().lineage_sha256(template),
        from_group_binding_sha256=module().authorization_binding(previous),to_group_binding_sha256=module().authorization_binding(template))
    subject['documents_sha256'].update(previous_authorization=_digest(previous),template=_digest(template))
    certificate['operation']['confirmation']['subject_sha256']=_digest(subject)
    certificate['signature_base64url']=base64.urlsafe_b64encode(f['private'].sign(module().group_signing_payload(certificate))).rstrip(b'=').decode()
    row=dict(seq=2,kind='GROUP_INITIALIZED',proposal_sha256=_digest(certificate['operation']),at_ms=f['now_ms']+1,payload=certificate,prev_hash=first['record_hash'])
    row['record_hash']=_digest(row)
    assert module().verify_group_change_history(row)['future_template']==template
    with pytest.raises(ValueError,match='PILOT_ALREADY_INITIALIZED'):append_historical(f['store'],row)
    assert f['store'].snapshot()==rows


def test_two_issuer_connections_cannot_initialize_one_scope_twice(scope_admin_case):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    f=scope_admin_case;operation=operation_for(f);gate=Barrier(2)
    def attempt(_):
        with closing(IssuerStore(f['store'].path)) as store:
            gate.wait(timeout=5)
            try:
                store.group_change(operation,documents=f['documents'],registry=f['registry'],installed_authorization=f['previous'],
                    key=f['private'],now_ms=f['now_ms'])
                return 'COMMITTED'
            except ValueError as error:
                assert str(error)=='ISSUER_ADMIN_OPERATION_EXISTS'
                return 'EXISTS'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
    assert sorted(results)==['COMMITTED','EXISTS']
    assert len(f['store'].snapshot())==1


def test_commit_recheck_failure_keeps_original_empty_chain(scope_admin_case):
    f=scope_admin_case;operation=operation_for(f)
    def changed():raise ValueError('synthetic current public drift before commit')
    with pytest.raises(ValueError,match='public drift'):
        f['store'].group_change(operation,documents=f['documents'],registry=f['registry'],installed_authorization=f['previous'],
            key=f['private'],now_ms=f['now_ms'],recheck=changed)
    assert f['store'].snapshot()==[]


@pytest.mark.parametrize('path',['reserve','cold_replay'])
def test_active_template_group_cannot_accept_plain_exact_submission_without_v2_proof(path,case,tmp_path,monkeypatch):
    f=retained_budget(case,tmp_path,monkeypatch);append_historical(f['store'],signed_group_row(f))
    plan,policy,_=future_reservation(f,tmp_path)
    del plan['context']['delegated_admission_sha256']
    plan['proposal_sha256']=_digest(plan);policy['policy_id']='plain-exact-cannot-reopen-template'
    before=f['store'].snapshot()
    if path=='reserve':
        with pytest.raises(ValueError,match='FUTURE_GROUP_ROUTE'):
            f['store'].reserve(plan,policy=policy,registry=f['registry'])
    else:
        store=f['store'];store.db.execute('BEGIN IMMEDIATE')
        try:
            store._append('RESERVED',plan['proposal_sha256'],dict(plan=plan,policy=policy,registry=f['registry']),plan['issued_at_ms'],before)
            store.db.executemany('INSERT INTO issuer_order_claims VALUES(?,?,?,?)',[
                (policy['account_ref_sha256'],'submission',order['newClientOrderId'],plan['proposal_sha256']) for order in plan['orders']])
            with pytest.raises(ValueError,match='FUTURE_GROUP_ROUTE'):store._rows()
        finally:store.db.rollback()
    assert f['store'].snapshot()==before


def test_native_scope_derived_exact_installation_reaches_real_empty_group_rejection(scope_exact_install):
    """The actual old installer changes authority; it never fabricates usage."""
    f=scope_exact_install;case=f['case'];captured=case['tool'].capture(source_commit='c'*40,
        exit_reserve=dict(grants=1,orders=1,notional='250'))
    documents=captured['documents'];at=case['local']['clock'][0]
    assert captured['rows']==[] and documents['ancestry']['schema']=='DEMO_GOLD_SCOPE_BUDGET_INSTALLATION_ANCESTRY_V1'
    subject=module().make_group_subject(documents=documents,rows=[],created_at_ms=at,expires_at_ms=at+60000)
    confirmation=dict(schema=module().CONFIRMATION_SCHEMA,purpose='GROUP_CONTINUITY',subject_sha256=_digest(subject),
        operator_id='synthetic-migration-confirmation',approved_at_ms=at,consumer_enabled=False,submission_enabled=False,economic_authority=False)
    with pytest.raises(ValueError,match='ORIGINAL_GROUP_REQUIRED'):
        module().authorize_group_change(dict(subject=subject,confirmation=confirmation),documents=documents,
            registry=case['registry'],installed_authorization=f['policy'],rows=[],now_ms=at)
    with closing(IssuerStore(case['issuer']/'issuance.sqlite3',read_only=True)) as store:assert store.snapshot()==[]
