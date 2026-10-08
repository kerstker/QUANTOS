"""Original first Scope and exact-installation lineage survive every reader.

The exact market candidate is an explicit input substitute. Old BTC/Scope and
pilot installation, original issuer, current keys, independent confirmation,
exact CAS and ancestry verification are native in isolated role directories.
"""
from copy import deepcopy
import json

import pytest

from tests.tools.test_gold_first_scope_materials import first_scope_archive as first_scope_archive, PARENT
from tests.live.test_gold_first_scope_delivery import cold_scope_local as cold_scope_local, installed_first_scope as installed_first_scope
from tests.tools.test_gold_future_budget_materials import build_native_scope


@pytest.fixture
def first_scope_pilot(installed_first_scope,tmp_path,monkeypatch):
    value=installed_first_scope
    result=build_native_scope((value['records'],value['scope'],value['registry']),value['local'],
        tmp_path,monkeypatch,original_issuer_root=value['issuer_root'])
    result['history']=value['history']
    return result


def install_exact(case,monkeypatch,*,previous,request_hash='a'*64,constraints_hash='b'*64,material=None,advance_ms=6000):
    from live.execution import demo_gold_budget_scope as scope_contract
    from tools import gold_strategy_policy_install as installer, gold_strategy_approval_request as approval
    from live.execution.demo_issuer_policy import budget_group_binding
    tool=case['tool'];now=case['local']['clock'][0];scoped=scope_contract.is_scope(previous)
    for name in ('requests','claims','results'):(case['local']['ctl']/name).mkdir(mode=0o700,exist_ok=True)
    proposed=approval.candidate_policy(previous,registry=case['registry'],request_sha256=request_hash,
        constraints_sha256=constraints_hash,now_ms=now)
    candidate=dict(schema='GOLD_STRATEGY_POLICY_CANDIDATE_V2' if scoped else 'GOLD_STRATEGY_POLICY_CANDIDATE_V1',
        status='POLICY_CANDIDATE_REQUIRES_INDEPENDENT_APPROVAL',request_sha256=request_hash,
        result_sha256='d'*64,stored_request_sha256=request_hash,replaces_policy_sha256=tool.digest(previous),
        policy_candidate=proposed,policy_candidate_sha256=tool.digest(proposed),
        budget_group_binding_sha256=scope_contract.budget_binding(previous) if scoped else budget_group_binding(previous),
        decision_valid_until_ms=now+5000,approval_granted=False,submission_enabled=False,economic_authority=False)
    if scoped:candidate['dispatcher_configuration']=approval.scope_dispatch_configuration(previous,now_ms=now)
    if material is not None:
        candidate=deepcopy(material)
        candidate['stored_request_sha256']=candidate['request_sha256']
        candidate['dispatcher_configuration']=approval.scope_dispatch_configuration(previous,now_ms=now)
        proposed=candidate['policy_candidate'];request_hash=candidate['request_sha256']
    monkeypatch.setattr(installer,'review_retained',lambda **kw:deepcopy(candidate))
    monkeypatch.setattr(installer,'local_operator',lambda:'isolated-first-exact-operator')
    monkeypatch.setattr('builtins.input',lambda prompt:prompt.removeprefix('请输入 ').strip())
    result=installer.install_approved(source_commit=PARENT,stored_request_sha256=request_hash)
    assert result['installed'] and result['previous_policy_sha256']==tool.digest(previous)
    stage=case['local']['cfg']/'strategy-approvals'/tool.digest(candidate)
    assert json.loads((case['local']['cfg']/'policy.json').read_bytes())==proposed
    case['local']['clock'][0]+=advance_ms
    return dict(policy=proposed,candidate=candidate,result=result,stage=stage)


@pytest.fixture
def first_exact(first_scope_pilot,monkeypatch):
    case=first_scope_pilot
    return dict(case=case,**install_exact(case,monkeypatch,previous=case['scope']))


def capture(case):
    return case['tool'].capture(source_commit=PARENT,exit_reserve={'grants':1,'orders':1,'notional':'250'})


def test_first_exact_fixed_reader_preserves_signed_scope_and_original_issuer(first_exact):
    f=first_exact;case=f['case'];before=case['history']['issuer_store'].snapshot()
    assert f['policy']['schema']=='DEMO_GOLD_STRATEGY_BUDGET_POLICY_V2'
    assert f['policy']['original_scope']==case['scope']
    captured=capture(case);records=captured['documents']['ancestry']
    assert records['scope_ancestry']==case['records']
    assert records['policy_installation']['candidate']==f['candidate']
    assert records['policy_installation']['previous_policy']==case['scope']
    assert captured['rows']==before==case['history']['issuer_prefix']
    assert captured['documents']['template']['origin']['kind']=='BUDGET'
    assert captured['documents']['template']['pilot']['configuration_sha256']==case['tool'].digest(case['configuration'])
    assert case['history']['issuer_store'].snapshot()==before


def test_pure_exact_ancestry_accepts_actual_first_scope_installation(first_exact):
    from live.execution.demo_gold_scope_ancestry import validate_authorization_ancestry
    f=first_exact;case=f['case'];tool=case['tool']
    records=dict(schema='DEMO_GOLD_SCOPE_BUDGET_INSTALLATION_ANCESTRY_V1',
        result=case['records']['result'],spec=case['records']['spec'],scope_ancestry=case['records'],
        policy_installation=tool._exact_installation(f['policy']))
    result=validate_authorization_ancestry(records,authorization=f['policy'],registry=case['registry'],source_commit=PARENT)
    assert result['scope_sha256']==tool.digest(case['scope'])


def test_validly_signed_other_scope_cannot_replace_actual_installation(first_exact):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from services.validation.demo_submission import _decode
    from live.execution import demo_gold_budget_scope as scope_contract
    from live.execution.demo_issuer_policy import historical_policy_binding
    from live.execution.demo_gold_scope_ancestry import validate_authorization_ancestry
    f=first_exact;case=f['case'];tool=case['tool'];policy=deepcopy(f['policy'])
    other=policy['original_scope'];other['scope_enrollment']['original_issuer_history']['head_sha256']='e'*64
    # An actual fixture signature distinguishes origin equality from merely
    # rejecting corrupt signatures. This is never an installed host key.
    keydoc=json.loads((case['local']['cfg']/'signing-key.json').read_bytes())
    private=Ed25519PrivateKey.from_private_bytes(_decode(keydoc['private_key_base64url'],32))
    other['signature_base64url']=base64.urlsafe_b64encode(private.sign(scope_contract.signed_bytes(other))).rstrip(b'=').decode()
    historical_policy_binding(policy,case['registry'])
    records=dict(schema='DEMO_GOLD_SCOPE_BUDGET_INSTALLATION_ANCESTRY_V1',
        result=case['records']['result'],spec=case['records']['spec'],scope_ancestry=case['records'],
        policy_installation=tool._exact_installation(f['policy']))
    with pytest.raises(ValueError,match='GOLD_SCOPE_BUDGET_ORIGINAL_SCOPE'):
        validate_authorization_ancestry(records,authorization=policy,registry=case['registry'],source_commit=PARENT)


def test_later_exact_retains_full_scope_without_downgrading_and_can_prepare_migration(first_exact,monkeypatch):
    from live.execution.demo_issuer_group_continuity import make_group_subject
    f=first_exact;case=f['case']
    later=install_exact(case,monkeypatch,previous=f['policy'],request_hash='f'*64,constraints_hash='1'*64)
    assert later['policy']['schema']=='DEMO_GOLD_STRATEGY_BUDGET_POLICY_V2'
    assert later['policy']['original_scope']==case['scope']
    captured=capture(case);records=captured['documents']['ancestry']
    assert records['policy_installation']['previous_policy']==f['policy']
    assert records['policy_installation']['result']==later['result']
    subject=make_group_subject(**captured,created_at_ms=case['local']['clock'][0],
        expires_at_ms=case['local']['clock'][0]+300000)
    assert subject['action']=='MIGRATE_BUDGET'
    assert subject['previous_authorization_sha256']==case['tool'].digest(later['policy'])


@pytest.mark.parametrize('damage',['missing','changed_scope','downgraded'])
def test_fixed_reader_rejects_missing_changed_or_downgraded_scope(first_exact,damage):
    f=first_exact;case=f['case'];policy=deepcopy(f['policy'])
    if damage=='changed_scope':policy['original_scope']['scope_enrollment']['original_issuer_history']['head_sha256']='e'*64
    else:
        policy.pop('original_scope')
        if damage=='downgraded':policy['schema']='DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'
    (case['local']['cfg']/'policy.json').write_bytes(case['tool'].canonical(policy))
    before=case['history']['issuer_store'].snapshot()
    with pytest.raises(ValueError):capture(case)
    assert case['history']['issuer_store'].snapshot()==before


def test_actual_signed_exact_group_migrates_with_original_scope_prefix(first_scope_pilot,installed_first_scope,tmp_path,monkeypatch):
    from contextlib import closing
    from live.execution.demo_issuer_store import IssuerStore
    from live.execution import demo_issuer_group_continuity as continuity
    from tests.live.test_gold_first_scope_exact_continuity import native_exact_case,issue
    case=first_scope_pilot;f=native_exact_case(installed_first_scope,case,tmp_path,monkeypatch)
    installed=install_exact(case,monkeypatch,previous=case['scope'],material=f.candidate,advance_ms=0)
    assert installed['policy']==f.policy
    original=case['history']['issuer_prefix']
    with closing(IssuerStore(case['history']['issuer_path'])) as store:
        issue(f,store)
        used=store.snapshot();assert used[:len(original)]==original
        case['local']['clock'][0]+=6000
        captured=capture(case);at=case['local']['clock'][0]
        assert captured['rows']==used
        subject=continuity.make_group_subject(**captured,created_at_ms=at,expires_at_ms=at+300000)
        operation=dict(subject=subject,confirmation=dict(schema=continuity.CONFIRMATION_SCHEMA,
            purpose='GROUP_CONTINUITY',subject_sha256=case['tool'].digest(subject),operator_id='isolated-migrate-operator',
            approved_at_ms=at,consumer_enabled=False,submission_enabled=False,economic_authority=False))
        event=store.group_change(operation,documents=captured['documents'],registry=case['registry'],
            installed_authorization=installed['policy'],key=f.key,now_ms=at)
        assert event['kind']=='GROUP_EXIT_RESERVE_MIGRATED'
        assert event['payload']['previous_authorization']['original_scope']==case['scope']
        assert store.snapshot()[:-1]==used
