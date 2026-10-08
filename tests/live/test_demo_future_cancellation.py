"""A current template may cancel only native issued originals on the same group."""
from copy import deepcopy
from contextlib import closing
import base64
import pytest
from live.execution import demo_issuer as issuer
from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_store import IssuerStore,unsigned_envelope
from tests.live.test_gold_strategy_constraints import case as case,NOW
from tests.live.test_demo_issuer_group_continuity import retained_budget,signed_group_row,append_historical


def cancellation_handoff(f):
    from services.validation import demo_cancellation as contract
    plan=f['plan'];context={k:plan['context'][k] for k in ('deployment_sha256','executor_boot_sha256','account_ref_sha256','registry_sha256')}
    commands=[dict(symbol=o['symbol'],origClientOrderId=o['newClientOrderId'],orderId=str(i+1)) for i,o in enumerate(plan['orders'])]
    context.update(reservation_sha256='a'*64,original_batch_sha256=plan['context']['batch_sha256'],
        cancel_batch_sha256=contract.batch_sha256(commands,schema=contract.GOLD_BATCH_SCHEMA),query_snapshot_sha256='b'*64)
    proposal=dict(schema='DEMO_CANCEL_PROPOSAL_V1',context=context,commands=commands,query_record_hashes=['c'*64]*len(commands),
        new_order_submission_enabled=False,economic_authority=False)
    return dict(schema='DEMO_CANCELLATION_SESSION_PROPOSAL_V1',proposal_sha256=_digest(proposal),proposal_path='/synthetic/cancel.json',
        proposal=proposal,status='WAITING_INDEPENDENT_GRANT',submission_enabled=False)


def cancellation_case(case,tmp_path,monkeypatch,*,signed=True):
    from services.validation import demo_submission
    f=retained_budget(case,tmp_path,monkeypatch)
    if signed:
        envelope=unsigned_envelope(f['plan'],f['previous'])
        envelope['signature_base64url']=base64.urlsafe_b64encode(f['private'].sign(demo_submission.signing_payload(envelope))).rstrip(b'=').decode()
        f['store'].signed(f['plan']['proposal_sha256'],envelope,now_ms=NOW+12)
    append_historical(f['store'],signed_group_row(f))
    return f


def test_template_cancel_uses_original_signed_ancestry_current_binding_and_native_grant(case,tmp_path,monkeypatch):
    from services.validation import demo_cancellation as contract
    f=cancellation_case(case,tmp_path,monkeypatch);handoff=cancellation_handoff(f)
    result=issuer.issue_cancellation(handoff,policy=f['template'],registry=f['registry'],key=f['private'],store=f['store'],now_ms=NOW+20)
    proof=contract.verify(result['envelope'],registry=f['registry'],expected_context=handoff['proposal']['context'],
        commands=handoff['proposal']['commands'],now_ms=NOW+20)
    assert proof['signature_verified'] and not proof['economic_authority']
    with closing(IssuerStore(f['store'].path)) as reopened:
        rows=reopened.snapshot();stored=rows[-2]['payload']
        assert stored['plan']['purpose']=='cancellation'
        assert stored['policy']['schema']=='DEMO_GOLD_TEMPLATE_CANCEL_VIEW_V1'
        assert stored['policy']['bindings']['execution_constraints_sha256']==f['previous']['bindings']['execution_constraints_sha256']
        from live.execution.demo_issuer_group_continuity import reduce_groups
        state=reduce_groups(rows)[f['template']['budget_group']['group_id']]
        assert state['submission'][0]==1 and state['cancellation'][0]==1
        from live.execution.demo_issuer_policy import validate_policy
        with pytest.raises(ValueError):validate_policy(stored['policy'])
        with pytest.raises(ValueError):issuer.issue_submission({},policy=stored['policy'],registry=f['registry'],key=f['private'],store=reopened,now_ms=NOW+21)
        assert reopened.snapshot()==rows


@pytest.mark.parametrize('damage',['unsigned','wrong_order','wrong_batch','changed_template','expiry'])
def test_template_cancellation_rejects_missing_issued_original_or_changed_current_scope(case,tmp_path,monkeypatch,damage):
    f=cancellation_case(case,tmp_path,monkeypatch,signed=damage!='unsigned');handoff=cancellation_handoff(f)
    policy=deepcopy(f['template']);at=NOW+20
    if damage=='wrong_order':handoff['proposal']['commands'][0]['origClientOrderId']='QDunknown'
    elif damage=='wrong_batch':handoff['proposal']['context']['original_batch_sha256']='f'*64
    elif damage=='changed_template':policy['budget_group']['max_cancel_grants']+=1
    elif damage=='expiry':at=policy['valid_until_ms']
    handoff['proposal_sha256']=_digest(handoff['proposal']);before=f['store'].snapshot()
    with pytest.raises(ValueError):issuer.issue_cancellation(handoff,policy=policy,registry=f['registry'],key=f['private'],store=f['store'],now_ms=at)
    assert f['store'].snapshot()==before


def test_active_template_group_rejects_plain_exact_cancel_without_native_lineage_compact(case,tmp_path,monkeypatch):
    from live.execution.demo_issuer_policy import review_template_cancellation,GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA
    f=cancellation_case(case,tmp_path,monkeypatch);before=f['store'].snapshot()
    checked=review_template_cancellation(cancellation_handoff(f),template=f['template'],registry=f['registry'],history=before,now_ms=NOW+20)
    plain=deepcopy(checked['policy']);plain['schema']=GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA
    with pytest.raises(ValueError,match='FUTURE_GROUP_ROUTE'):
        f['store'].reserve(checked['plan'],policy=plain,registry=f['registry'])
    assert f['store'].snapshot()==before
