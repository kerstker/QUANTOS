"""Genuine installed Scope and enrolled pilot; no exact signal or reservation."""
from copy import deepcopy
import json
import os

import pytest

from tests.live.test_demo_gold_scope_ancestry import (
    book as book, installed as installed, first as first, materials as materials,
    scope_inputs as scope_inputs, day_materials as day_materials, local as local,
    scope_records as scope_records,
)
from tools import demo_operating_bundle as installer, demo_operator_setup as setup


@pytest.fixture
def native_scope(scope_records, local, tmp_path, monkeypatch):
    return build_native_scope(scope_records, local, tmp_path, monkeypatch)


def build_native_scope(scope_records, local, tmp_path, monkeypatch, *, original_issuer_root=None):
    """Enroll the original Scope window, reusing a supplied original issuer DB."""
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from live.execution.demo_issuer_store import IssuerStore
    from tests.live.test_demo_market_partition_runtime import shift
    from tools import gold_future_budget_materials as tool
    from tools.gold_pilot_install import _read_target as native_read
    from tools.demo_resident_recovery import _check_path as native_check
    records, scope, registry=scope_records;root=local['root']; stores=local['value']['stores']
    source_commit=scope['executor_release_commit']
    request=enrollment.scope_request(scope=scope,registry=registry,source_commit=source_commit,
        account_ref_sha256=scope['account_ref_sha256'])
    path=root/'gold-pilot-request.json';path.write_bytes(setup.canonical(request));path.chmod(0o600)
    at=local['clock'][0]+1000
    configuration=enrollment.enroll_requested(root=root,source_commit=source_commit,
        origin=shift(local['parts']['origin'],1000),now_ms=at,
        **{k:stores[k] for k in ('dispatch','recovery','oms')})
    local['clock'][0]=at
    if original_issuer_root is None:
        issuer=tmp_path/'issuer';issuer.mkdir(mode=0o700)
        store=IssuerStore(issuer/'issuance.sqlite3',create=True);store.close()
    else:
        issuer=original_issuer_root
        # Read-only, fail-closed; never provision a replacement for old history.
        store=IssuerStore(issuer/'issuance.sqlite3',read_only=True)
        try:
            from live.execution.demo_gold_first_scope_history import verify_prefix
            verify_prefix(scope['scope_enrollment']['original_issuer_history'],store.snapshot())
        finally:store.close()
    monkeypatch.setattr(installer,'ISSUER',issuer)
    monkeypatch.setattr(tool.demo_issuer,'_state_custody',lambda path,uid:native_check(path,os.getuid(),directory=True))
    def read(path,*,uid,maximum=65536):
        assert uid in (registry['executor_uid'],registry['issuer_uid'])
        path.relative_to(tmp_path)
        return native_read(path,uid=os.getuid(),maximum=maximum)
    monkeypatch.setattr(tool,'_read_target',read)
    monkeypatch.setattr(tool,'_check_path',lambda path,uid,**kw:native_check(path,os.getuid(),**kw))
    return dict(local=local,records=records,scope=scope,registry=registry,configuration=configuration,
        issuer=issuer,tool=tool,request=request)


def test_native_scope_without_exact_signal_builds_same_pilot_future_template(native_scope):
    case=native_scope;tool=case['tool']
    before={p:p.read_bytes() for root in (case['local']['cfg'],case['local']['root'],case['issuer'])
        for p in root.rglob('*') if p.is_file() and not p.is_symlink() and '.git' not in p.parts}
    captured=tool.capture(source_commit='c'*40,exit_reserve={'grants':1,'orders':1,'notional':'250'})
    docs=captured['documents'];template=docs['template']
    assert captured['rows']==[] and docs['dispatcher']['present'] is False
    assert template['pilot']['configuration_sha256']==tool.digest(case['configuration'])
    assert template['pilot']['started_at_ms']==case['configuration']['started_at_ms']
    assert template['budget_group']['group_id']==case['scope']['budget_group']['group_id']
    assert template['bindings']==case['scope']['bindings']
    assert template['executable'] is False
    assert all(p.read_bytes()==body for p,body in before.items())
    assert not (case['local']['ctl']/'strategy-dispatch.json').exists()


@pytest.mark.parametrize('damage',['missing_activation','pilot_changed','scope_changed','new_source'])
def test_materials_reject_changed_originals(native_scope,damage):
    case=native_scope;source='c'*40
    if damage=='missing_activation':
        from tools import demo_successor_setup as successor
        (successor.active_setup()/'activation.json').unlink()
    elif damage=='pilot_changed':
        p=case['local']['root']/'gold-pilot/configuration.json'
        doc=json.loads(p.read_bytes());doc['started_at_ms']+=1;p.write_bytes(setup.canonical(doc))
    elif damage=='scope_changed':
        p=case['local']['cfg']/'policy.json';doc=json.loads(p.read_bytes());doc['scope']['max_quantity_per_order']='1'
        p.write_bytes(setup.canonical(doc))
    else:source='d'*40
    with pytest.raises((ValueError,FileNotFoundError)):
        case['tool'].capture(source_commit=source,exit_reserve={'grants':1,'orders':1,'notional':'250'})


@pytest.fixture
def scope_exact_install(native_scope,tmp_path,monkeypatch):
    from live.execution import demo_gold_budget_scope as scope_contract
    from tools import gold_strategy_policy_install as old, gold_strategy_approval_request as approval
    case=native_scope;tool=case['tool'];scope=case['scope'];now=case['local']['clock'][0]
    for name in ('requests','claims','results'):
        (case['local']['ctl']/name).mkdir(mode=0o700,exist_ok=True)
    # The exact market candidate is an explicit input substitute. Scope install,
    # original pilot, candidate authority validation, independent TTY protocol,
    # immutable history, policy CAS and dispatcher creation are the real tools.
    proposed=approval.candidate_policy(scope,registry=case['registry'],request_sha256='a'*64,
        constraints_sha256='b'*64,now_ms=now)
    candidate=dict(schema='GOLD_STRATEGY_POLICY_CANDIDATE_V2',status='POLICY_CANDIDATE_REQUIRES_INDEPENDENT_APPROVAL',
        request_sha256='a'*64,result_sha256='d'*64,stored_request_sha256='e'*64,
        replaces_policy_sha256=tool.digest(scope),policy_candidate=proposed,policy_candidate_sha256=tool.digest(proposed),
        budget_group_binding_sha256=scope_contract.budget_binding(scope),decision_valid_until_ms=now+5000,
        approval_granted=False,submission_enabled=False,economic_authority=False,
        dispatcher_configuration=approval.scope_dispatch_configuration(scope,now_ms=now))
    monkeypatch.setattr(old,'review_retained',lambda **kw:deepcopy(candidate))
    monkeypatch.setattr(old,'local_operator',lambda:'isolated-old-exact-operator')
    monkeypatch.setattr('builtins.input',lambda prompt:prompt.removeprefix('请输入 ').strip())
    def chown(path,owner,group):
        path.relative_to(tmp_path);assert owner==0 and group in (0,case['registry']['issuer_uid'])
    monkeypatch.setattr(old.installer.os,'chown',chown)
    result=old.install_approved(source_commit='c'*40,stored_request_sha256='e'*64)
    assert result['installed'] and result['previous_policy_sha256']==tool.digest(scope)
    stage=old.supervisor.ISSUER_POLICY.parent/'strategy-approvals'/tool.digest(candidate)
    assert (stage/'previous-dispatch.absent').read_bytes()==b'ABSENT\n'
    assert tool._read(old.supervisor.ISSUER_POLICY)==proposed
    case['local']['clock'][0]+=6000  # The original exact signal is now expired.
    return dict(case=case,policy=proposed,candidate=candidate,result=result,stage=stage)


def test_scope_derived_budget_keeps_true_scope_and_exact_installation_ancestry(scope_exact_install):
    f=scope_exact_install;case=f['case'];tool=case['tool']
    captured=tool.capture(source_commit='c'*40,exit_reserve={'grants':1,'orders':1,'notional':'250'})
    ancestry=captured['documents']['ancestry']
    assert ancestry['schema']=='DEMO_GOLD_SCOPE_BUDGET_INSTALLATION_ANCESTRY_V1'
    assert ancestry['scope_ancestry']==case['records']
    assert ancestry['policy_installation']['result']==f['result']
    assert ancestry['policy_installation']['candidate']==f['candidate']
    assert ancestry['policy_installation']['previous_policy']==case['scope']
    assert captured['documents']['template']['origin']['kind']=='BUDGET'
    assert captured['documents']['template']['pilot']['configuration_sha256']==tool.digest(case['configuration'])
    assert captured['documents']['dispatcher']['present'] and captured['rows']==[]


@pytest.mark.parametrize('name',['candidate.json','operator-approval.json','result.json','previous-policy.json','dispatcher-candidate.json','previous-dispatch.absent'])
def test_scope_derived_budget_requires_unchanged_exact_installation_originals(scope_exact_install,name):
    f=scope_exact_install;tool=f['case']['tool'];path=f['stage']/name
    if name=='previous-dispatch.absent':path.write_bytes(b'PRESENT\n')
    else:
        value=json.loads(path.read_bytes())
        if name=='candidate.json':value['decision_valid_until_ms']=value['policy_candidate']['valid_from_ms']
        elif name=='operator-approval.json':value['operator_id']='changed-after-confirmation'
        elif name=='result.json':value['operator_approval_sha256']='0'*64
        elif name=='previous-policy.json':value['signature_base64url']='A'*86
        else:value['kill_switch']['trip_count']+=1
        path.write_bytes(setup.canonical(value))
    with pytest.raises(ValueError):
        tool.capture(source_commit='c'*40,exit_reserve={'grants':1,'orders':1,'notional':'250'})


def test_later_exact_budget_still_requires_signed_scope_and_latest_new_confirmation(scope_exact_install,monkeypatch):
    from tools import gold_strategy_policy_install as old, gold_strategy_approval_request as approval
    f=scope_exact_install;case=f['case'];tool=case['tool'];now=case['local']['clock'][0]
    next_policy=approval.candidate_policy(f['policy'],registry=case['registry'],request_sha256='f'*64,
        constraints_sha256='1'*64,now_ms=now)
    candidate=deepcopy(f['candidate']);candidate.pop('dispatcher_configuration')
    candidate.update(schema='GOLD_STRATEGY_POLICY_CANDIDATE_V1',request_sha256='f'*64,
        result_sha256='2'*64,stored_request_sha256='3'*64,policy_candidate=next_policy,
        policy_candidate_sha256=tool.digest(next_policy),replaces_policy_sha256=tool.digest(f['policy']),
        decision_valid_until_ms=now+5000)
    monkeypatch.setattr(old,'review_retained',lambda **kw:deepcopy(candidate))
    result=old.install_approved(source_commit='c'*40,stored_request_sha256='3'*64)
    case['local']['clock'][0]+=6000
    records=tool.capture(source_commit='c'*40,exit_reserve={'grants':1,'orders':1,'notional':'250'})['documents']['ancestry']
    assert records['scope_ancestry']==case['records']
    assert records['policy_installation']['result']==result
    assert records['policy_installation']['previous_policy']==f['policy']
