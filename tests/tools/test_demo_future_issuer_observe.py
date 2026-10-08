"""Template display reports the shared original group, including unsigned use."""
from copy import deepcopy
import pytest
from tests.live.test_gold_strategy_constraints import case as case, NOW
from tests.live.test_demo_issuer_group_continuity import retained_budget,signed_group_row,append_historical
from tools import demo_issuer_observe as tool
from services.research import demo_issuer_status as model


def report(case,tmp_path,monkeypatch):
    f=retained_budget(case,tmp_path,monkeypatch)
    append_historical(f['store'],signed_group_row(f))
    return tool.build_status(f['template'],f['registry'],f['store'].snapshot(),source_commit='c'*40,observed_at_ms=NOW+20)


def test_native_shared_group_display_does_not_mislabel_template_as_exact_policy(case,tmp_path,monkeypatch):
    value=report(case,tmp_path,monkeypatch)
    assert value['schema']=='DEMO_ISSUER_STATUS_V2' and value['budget_basis']=='SHARED_GROUP'
    assert value['phase']=='GROUP_PENDING_SIGNATURE' and 'policy_id' not in value
    assert value['records']==2 and value['budgets'][0]['reserved_grants']==1
    assert value['exit_reserve']==dict(grants=1,orders=1,notional='120')
    assert value['opening_remaining']['grants']==8
    directory=tmp_path/'display';model.save(directory,value)
    assert model.load_latest(directory)==value
    expired=deepcopy(value);expired.update(observed_at_ms=value['valid_until_ms'],phase='TEMPLATE_INACTIVE')
    assert model.validate(expired)['budgets']==value['budgets']


@pytest.mark.parametrize('damage',['policy_id','basis','opening','reserve','event','history','symbol','phase'])
def test_future_display_rejects_fictitious_available_capacity_or_old_policy_fields(case,tmp_path,monkeypatch,damage):
    value=report(case,tmp_path,monkeypatch)
    if damage=='policy_id':value['policy_id']=value['template_id']
    elif damage=='basis':value['budget_basis']='PER_POLICY'
    elif damage=='opening':value['opening_remaining']['notional']=value['budgets'][0]['remaining_notional']
    elif damage=='reserve':value['exit_reserve']['grants']=0
    elif damage=='event':value['continuity_event_sha256']=None
    elif damage=='history':value['records']=1
    elif damage=='symbol':value['symbol']='BTCUSDT'
    else:value['phase']='LIMIT_REACHED'
    with pytest.raises(ValueError):model.validate(value)


def test_zero_opening_slice_does_not_claim_total_exit_budget_exhausted(case,tmp_path,monkeypatch):
    value=report(case,tmp_path,monkeypatch)
    value['exit_reserve']['grants']=value['budgets'][0]['max_grants']-1
    value['opening_remaining']['grants']=0
    assert model.validate(value)['phase']=='GROUP_PENDING_SIGNATURE'
