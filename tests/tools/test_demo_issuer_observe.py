"""Display is derived from original issuer history, never an order receipt."""
import json
from copy import deepcopy
import pytest

from tools import demo_issuer_observe as tool
from services.research import demo_issuer_status as model
from tests.live.test_demo_issuer import fixture,NOW
from live.execution import demo_issuer as issuer
from live.execution.demo_issuer_policy import review_submission


def report(tmp_path,monkeypatch,*,signed=False):
    private,_,opened,_,handoff,registry,policy,store=fixture(tmp_path,monkeypatch)
    try:
        if signed:issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=store,now_ms=NOW+1)
        else:store.reserve(review_submission(handoff,policy=policy,registry=registry,now_ms=NOW+1),policy=policy,registry=registry)
        return tool.build_status(policy,registry,store.snapshot(),source_commit='c'*40,observed_at_ms=NOW+2)
    finally:
        store.close()
        for s in opened:s.close()


@pytest.mark.parametrize('signed',[False,True])
def test_reserved_budget_is_spent_even_without_signature_and_cannot_imply_execution(tmp_path,monkeypatch,signed):
    raw=report(tmp_path,monkeypatch,signed=signed)
    assert raw['phase']==('POLICY_OBSERVED' if signed else 'PENDING_SIGNATURE')
    b=raw['budgets'][0]
    assert (b['reserved_grants'],b['signed_grants'],b['reserved_orders'])==(1,int(signed),1)
    assert b['reserved_notional']=='120.000' and b['remaining_notional']=='180.000'
    assert raw['key_material_status']=='NOT_CHECKED' and raw['submission_enabled'] is False and raw['cash_reconciled'] is False
    directory=tmp_path/'views';model.save(directory,raw)
    assert model.load_latest(directory)==raw


@pytest.mark.parametrize('bad',['signature_count','budget','authority','extra','phase','future','notional'])
def test_inconsistent_or_private_display_fields_are_rejected(tmp_path,monkeypatch,bad):
    raw=report(tmp_path,monkeypatch)
    if bad=='signature_count':raw['budgets'][0]['signed_grants']=2
    elif bad=='budget':raw['budgets'][0]['max_grants']=0
    elif bad=='authority':raw['submission_enabled']=True
    elif bad=='extra':raw['private_key_base64url']='PRIVATE'
    elif bad=='phase':raw['phase']='POLICY_OBSERVED'
    elif bad=='future':raw['last_record_at_ms']=raw['observed_at_ms']+1
    elif bad=='notional':raw['budgets'][0]['remaining_notional']='300'
    with pytest.raises(ValueError):model.validate(raw)


def test_expired_policy_keeps_spent_history_visible(tmp_path,monkeypatch):
    raw=report(tmp_path,monkeypatch,signed=True)
    later=deepcopy(raw);later['observed_at_ms']=later['valid_until_ms'];later['phase']='POLICY_INACTIVE'
    assert model.validate(later)['budgets']==raw['budgets']


def test_display_notional_conservation_cannot_round_away_a_small_difference(tmp_path,monkeypatch):
    raw=report(tmp_path,monkeypatch)
    raw['budgets'][0].update(reserved_notional='99999999999999999998.000000000000000000000000000000000001',
        max_notional='99999999999999999999',remaining_notional='1')
    with pytest.raises(ValueError,match='ISSUER_DISPLAY_NOTIONAL'):model.validate(raw)


def test_display_publisher_accepts_only_issuer_summary(tmp_path,monkeypatch,capsys):
    from tools.demo_oms_status_publish import main
    raw=report(tmp_path,monkeypatch);source=tmp_path/'private';output=tmp_path/'research'
    model.save(source,raw);before={p.name:p.read_bytes() for p in source.iterdir()}
    assert main(['--source-dir',str(source),'--output-dir',str(output),'--kind','issuer'])==0
    assert model.load_latest(output)==raw and before=={p.name:p.read_bytes() for p in source.iterdir()}
    assert json.loads(capsys.readouterr().out)['submission_enabled'] is False
