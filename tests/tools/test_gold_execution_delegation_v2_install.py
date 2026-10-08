"""V2 ENABLE consumes native Scope installation, group event and issuer signature."""
from copy import deepcopy

import pytest
from live.execution.demo_authority import _read_root_owned_json as native_public_read, _require_protected as native_protected
from tests.live.test_gold_strategy_constraints import case as _budget_case

budget_case = _budget_case

from tests.tools.test_gold_future_budget_install import (
    book as book,installed as installed,first as first,materials as materials,
    scope_inputs as scope_inputs,day_materials as day_materials,local as local,
    scope_records as scope_records,native_scope as native_scope,native_install as native_install,
)


@pytest.fixture
def signed_native(native_install):
    f=native_install;tool=f['tool'];case=f['case']
    source_commit=case['scope']['executor_release_commit']
    group=tool.install_approved(source_commit=source_commit,exit_reserve=dict(grants=1,orders=1,notional='250'))
    signed=tool.sign_approved(source_commit=source_commit,group_operation_sha256=group['group_operation_sha256'],
        opening_until_ms=case['configuration']['ends_at_ms']-60000,exit_until_ms=case['scope']['valid_until_ms'],
        max_holding_ms=case['local']['parts']['definition']['rules']['max_holding_bars']*900000)
    return f|{'group':group,'signed':signed}


def enable_value(f):
    from live.execution import demo_gold_delegation_enable as contract, demo_issuer_group_continuity as continuity
    tool=f['tool'];case=f['case'];rows=case['tool'].issuer_rows(case['registry'])
    artifact=rows[-1]['payload']['artifact'];stage=tool.stage_for(f['group']['group_operation_sha256'])
    docs=tool.read(stage/'candidate.json')['documents'];anchor=continuity.history_anchor(rows,authorization=docs['template'])
    from live.execution.demo_pre_submit import _state
    stores=case['local']['value']['stores'];state=_state(**{k:stores[k] for k in ('dispatch','recovery','oms')})
    candidate=dict(schema='GOLD_EXECUTION_DELEGATION_ENABLE_CANDIDATE_V2',delegation=artifact,
        continuity_event=rows[0],runtime=docs['runtime'],definition=docs['definition'],
        registration_sha256='1'*64,registration_candidate_sha256='2'*64,issuer_anchor=anchor,
        original_binding=dict(registry_sha256=tool.digest(docs['registry']),policy_sha256=tool.digest(docs['template']),
            profile_sha256=tool.digest(docs['profile']),runtime_sha256=tool.digest(docs['runtime']),
            definition_sha256=tool.digest(docs['definition']),original_setup_sha256=tool.digest(docs['ancestry']['result']),
            original_spec_sha256=tool.digest(docs['ancestry']['spec']),executor_ledger_state_hash=tool.digest(state),
            dispatcher_sha256=tool.digest(tool.materials._read(tool.supervisor.STATE/'strategy-dispatch.json')),issuer_current=anchor),
        consumer_enabled=False,submission_enabled=False,economic_authority=False)
    confirmation=dict(schema='GOLD_EXECUTION_DELEGATION_ENABLE_APPROVAL_V1',purpose='ENABLE_BOUNDED_FUTURE_SIGNALS',
        candidate_sha256=tool.digest(candidate),operator_id='isolated-new-future-confirmation',
        approved_at_ms=case['local']['clock'][0],consumer_enabled=True,submission_enabled=False,economic_authority=False)
    return {**candidate,'schema':'DEMO_GOLD_DELEGATION_ENABLE_V2','consumer_enabled':True,'operator_confirmation':confirmation},rows


def test_v2_enable_requires_actual_two_admin_history_with_zero_reserved(signed_native):
    from live.execution import demo_gold_delegation_enable as contract
    f=signed_native;value,rows=enable_value(f)
    assert [r['kind'] for r in rows]==['GROUP_INITIALIZED','DELEGATION_V2_SIGNED']
    assert value['issuer_anchor']['reserved']==0 and value['issuer_anchor']['records']==2
    assert contract.validate(value,registry=f['case']['registry'],source_commit='c'*40,
        account_ref_sha256=f['case']['scope']['account_ref_sha256'],now_ms=f['case']['local']['clock'][0])==value
    contract.verify_history_prefix(value['issuer_anchor'],rows,policy=value['delegation']['future_template'])


@pytest.mark.parametrize('damage',['event','anchor','operator','scope_as_delegation'])
def test_v2_enable_refuses_changed_actual_ancestry(signed_native,damage):
    from live.execution import demo_gold_delegation_enable as contract
    f=signed_native;value,rows=enable_value(f);value=deepcopy(value)
    if damage=='event':value['continuity_event']['record_hash']='0'*64
    elif damage=='anchor':value['issuer_anchor']['continuity_event_sha256']='0'*64
    elif damage=='operator':value['operator_confirmation']['purpose']='REGISTER_BOUNDED_FUTURE_SIGNALS_CONSUMER_DISABLED'
    else:value['delegation']=f['case']['scope']
    with pytest.raises(ValueError):contract.validate(value,registry=f['case']['registry'],source_commit='c'*40,
        account_ref_sha256=f['case']['scope']['account_ref_sha256'],now_ms=f['case']['local']['clock'][0])


@pytest.fixture
def v2_installation(signed_native,monkeypatch):
    import os
    import sqlite3
    from tools import gold_execution_delegation_review as review
    from tools import gold_execution_delegation_install as install
    from tools import gold_execution_delegation_enable as enable
    from tools.demo_resident_recovery import _check_path as native_check
    from tools.gold_pilot_install import _read_target as native_read
    f=signed_native;case=f['case'];root=case['local']['root'];uid=case['registry']['executor_uid']
    # Preserve the original native SQLite state while mapping the old fixture's
    # *.db names to the deployed fixed filenames consumed by the new RO review.
    for name in ('dispatch','recovery','oms'):
        original=root/(name+'.db');fixed=root/(name+'.sqlite3')
        if not fixed.exists():
            assert original.is_file()
            source=sqlite3.connect(original.as_uri()+'?mode=ro',uri=True)
            target=sqlite3.connect(fixed)
            try:source.backup(target)
            finally:source.close();target.close()
            fixed.chmod(0o600)
    # A first-install fixture already uses these actual paths; reuse its whole
    # original stores and check equality instead of creating another database.
    from contextlib import ExitStack
    from tools.gold_delegated_proof import _read_only_stores
    from live.execution.demo_pre_submit import _state
    with ExitStack() as stack:
        fixed_stores=_read_only_stores(stack,root,os.getuid())
        originals=case['local']['value']['stores']
        assert _state(**fixed_stores)==_state(**{k:originals[k] for k in ('dispatch','recovery','oms')})
    def check(path,owner,**kw):
        assert owner==uid and (path==root or root in path.parents)
        return native_check(path,os.getuid(),**kw)
    def read(path,*,uid,maximum=65536):
        assert uid in (case['registry']['executor_uid'],case['registry']['issuer_uid'])
        return native_read(path,uid=os.getuid(),maximum=maximum)
    monkeypatch.setattr(review,'_check_path',check);monkeypatch.setattr(review,'_read_target',read)
    for module in (install,enable):monkeypatch.setattr(module,'local_operator',lambda:'isolated-new-future-confirmation')
    def register(candidate,operator):
        return dict(schema='GOLD_EXECUTION_DELEGATION_REGISTRATION_APPROVAL_V1',
            purpose='REGISTER_BOUNDED_FUTURE_SIGNALS_CONSUMER_DISABLED',candidate_sha256=f['tool'].digest(candidate),
            operator_id=operator,approved_at_ms=case['local']['clock'][0],consumer_enabled=False,submission_enabled=False,economic_authority=False)
    monkeypatch.setattr(install,'confirmation',register)
    def enabling(candidate,operator):
        return dict(schema='GOLD_EXECUTION_DELEGATION_ENABLE_APPROVAL_V1',purpose='ENABLE_BOUNDED_FUTURE_SIGNALS',
            candidate_sha256=f['tool'].digest(candidate),operator_id=operator,approved_at_ms=case['local']['clock'][0],
            consumer_enabled=True,submission_enabled=False,economic_authority=False)
    monkeypatch.setattr(enable,'confirmation',enabling)
    monkeypatch.setattr(enable.contract,'PUBLIC_ROOT',f['tool'].public.PUBLIC_ROOT.parent/'public-enable')
    from types import SimpleNamespace
    native_metadata=enable.contract._public_metadata
    def metadata(info,**kw):
        assert info.st_uid==os.getuid()
        value=SimpleNamespace(**{name:getattr(info,name) for name in ('st_mode','st_nlink','st_size')},st_uid=0,st_gid=0)
        return native_metadata(value,**kw)
    monkeypatch.setattr(enable.contract,'_public_metadata',metadata)
    from live.execution import demo_authority
    def protected(info,*,directory):
        value=SimpleNamespace(**{name:getattr(info,name) for name in ('st_mode','st_nlink','st_size')},st_uid=0,st_gid=0)
        assert info.st_uid in (0,os.getuid())
        if directory:value.st_mode &= ~0o022
        return native_protected(value,directory=directory)
    monkeypatch.setattr(demo_authority,'_require_protected',protected)
    monkeypatch.setattr(enable.contract,'_read_root_owned_json',native_public_read)
    monkeypatch.setattr(enable,'_require_public_parent',lambda info: f['tool'].public._parent_metadata(info))
    args=dict(source_commit=case['scope']['executor_release_commit'],
        delegation_file=f['tool'].supervisor.STATE/'gold-execution-delegation-materials/delegations'/(f['signed']['delegation_sha256']+'.json'),
        operator_approval_file=f['tool'].supervisor.STATE/'gold-execution-delegation-materials/operators'/(f['signed']['operator_material_sha256']+'.json'))
    return f|{'review':review,'install':install,'enable':enable,'args':args}


def test_original_scope_admin_sign_register_then_independent_enable_is_native(v2_installation):
    f=v2_installation;case=f['case'];before=case['tool'].issuer_rows(case['registry'])
    checked=f['review'].review(**f['args'])
    assert checked['schema']=='GOLD_EXECUTION_DELEGATION_REVIEW_V2'
    assert checked['continuity_event']==before[0]
    registered=f['install'].install_approved(**f['args'])
    assert registered['consumer_enabled'] is False
    result=f['enable'].enable_approved(source_commit='c'*40,delegation_sha256=f['signed']['delegation_sha256'])
    assert result['consumer_enabled'] is True and result['submission_enabled'] is False
    public=f['enable'].contract.load_enabled(registry=case['registry'],source_commit='c'*40,
        account_ref_sha256=case['scope']['account_ref_sha256'],now_ms=case['local']['clock'][0],role='controller')
    assert public['schema']=='DEMO_GOLD_DELEGATION_ENABLE_V2' and public['continuity_event']==before[0]
    assert case['tool'].issuer_rows(case['registry'])==before


def test_maximum_32_key_registry_and_128_character_confirmation_fit_original_public_budget(budget_case,tmp_path,monkeypatch):
    """Real historical signature/store replay; no claim of APPLY installation.

Registry cardinality and free-text confirmation are the variable public-row
shape bounds. The actual native Scope installation is covered separately above.
    """
    import base64,hashlib,json
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from tests.live.test_demo_issuer_group_continuity import retained_budget,signed_group_row,append_historical
    from live.execution import demo_issuer_group_continuity as continuity
    from live.execution.demo_batch_risk import _digest
    from services.validation.demo_submission import validate_registry
    registry=budget_case[3]
    for index in range(32-len(registry['keys'])):
        key=Ed25519PrivateKey.generate().public_key().public_bytes_raw()
        registry['keys'].append(dict(authority_id=('historical-'+str(index)+'-').ljust(80,'a'),
            key_id=('historical-'+str(index)+'-').ljust(80,'k'),public_key_base64url=base64.urlsafe_b64encode(key).rstrip(b'=').decode(),
            public_key_sha256=hashlib.sha256(key).hexdigest(),status='REVOKED',valid_from_ms=1,valid_until_ms=2))
    validate_registry(registry)
    f=retained_budget(budget_case,tmp_path,monkeypatch);row=signed_group_row(f)
    row['payload']['operation']['confirmation']['operator_id']='\U0001f600'*128
    row['payload']['signature_base64url']=base64.urlsafe_b64encode(f['private'].sign(
        continuity.group_signing_payload(row['payload']))).rstrip(b'=').decode()
    row['proposal_sha256']=_digest(row['payload']['operation'])
    row['record_hash']=_digest({k:v for k,v in row.items() if k!='record_hash'})
    assert continuity.verify_group_change_history(row)['event_sha256']==row['record_hash']
    append_historical(f['store'],row)
    raw=json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    # No reason was found to widen V2 ENABLE to 512KiB: even this valid largest
    # registry/confirmation row plus the measured native 21KiB remainder fits.
    assert len(raw)+21000<65536 and len(raw)<131072
