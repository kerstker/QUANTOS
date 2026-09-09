import pytest
from tools.profitability_forward_account import advance_cost_account
from tools.profitability_study import write_new
from tools.btc_factory_research import verified
from services.research.profitability_forward_account import DAY


def test_unregistered_worker_does_not_create_a_plan_and_registration_is_immutable(tmp_path):
    assert advance_cost_account(tmp_path,1,clock=lambda:DAY+1) is None
    model=tmp_path/'data/cache/ai_factory/1/model.json';model.parent.mkdir(parents=True)
    write_new(model,{'authoritative':False,'live_eligible':False,'training_last_label_ms':0})
    status=advance_cost_account(tmp_path,1,register=True,clock=lambda:DAY+1)
    assert status['status']=='WAITING_MATURITY'
    plan=tmp_path/'data/cache/profitability/1/p6/plan.json';original=plan.read_bytes()
    status=advance_cost_account(tmp_path,1,register=True,clock=lambda:4*DAY)
    assert status['status']=='MISSING_FORECASTS' and status['missing_slots']
    assert plan.read_bytes()==original


def test_model_drift_turns_current_status_into_error_without_deleting_plan(tmp_path):
    model=tmp_path/'data/cache/ai_factory/1/model.json';model.parent.mkdir(parents=True)
    write_new(model,{'authoritative':False,'live_eligible':False,'training_last_label_ms':0})
    advance_cost_account(tmp_path,1,register=True,clock=lambda:DAY+1)
    # Simulate an external replacement of the source model, retaining the plan.
    model.rename(model.with_suffix('.old.json'))
    write_new(model,{'authoritative':False,'live_eligible':False,'training_last_label_ms':1})
    with pytest.raises(ValueError):advance_cost_account(tmp_path,1,clock=lambda:DAY+2)
    output=tmp_path/'data/cache/profitability/1/p6'
    assert verified(output/'status.json')['status']=='ERROR'
    assert (output/'plan.json').exists()


def test_completed_prefix_is_reused_and_old_predictions_cannot_change(tmp_path):
    from tests.research.test_profitability_forward_account import forecasts
    from tests.research.test_profitability_forward_market import receipts
    from tools.profitability_forward_market import market_from_receipts
    model=tmp_path/'data/cache/ai_factory/1/model.json';model.parent.mkdir(parents=True)
    write_new(model,{'authoritative':False,'live_eligible':False,'training_last_label_ms':0})
    advance_cost_account(tmp_path,1,register=True,clock=lambda:DAY+1)
    output=tmp_path/'data/cache/profitability/1/p6';plan=verified(output/'plan.json')
    path=model.parent/'forward/forecasts';path.mkdir(parents=True)
    for row in forecasts(plan)[:24]:
        write_new(path/f"{row['slot_ms']}.json",{**{k:v for k,v in row.items() if k!='result_hash'},'model_hash':plan['model_hash']})
    def capture(*args,**kwargs):
        start,end,records=receipts()
        return market_from_receipts(records,start=start,end=end),records
    first=advance_cost_account(tmp_path,1,clock=lambda:4*DAY,capture=capture)
    assert first['status']=='FORWARD_FORECAST_ACCOUNT_TECHNICAL_ONLY'
    assert len(first['cases'])==6 and len(first['cases'][0]['equity'])>=2
    def no_capture(*args,**kwargs):raise AssertionError('completed prefix must not run again')
    assert advance_cost_account(tmp_path,1,clock=lambda:4*DAY+1,capture=no_capture)['cases']==first['cases']
    earliest=sorted(path.glob('*.json'))[0];row=verified(earliest);earliest.rename(earliest.with_suffix('.old'))
    write_new(earliest,{**{k:v for k,v in row.items() if k!='result_hash'},'prediction':.02})
    with pytest.raises(ValueError):advance_cost_account(tmp_path,1,clock=lambda:4*DAY+2,capture=no_capture)
    assert list((output/'completed').glob('*.json'))


def test_cli_missing_plan_exits_without_implicit_registration(tmp_path,capsys,monkeypatch):
    from tools.profitability_forward_account import main
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    monkeypatch.setattr('signal.signal',lambda *_:None)
    assert main(['--state-root',str(tmp_path),'--attempt','1','--once'])==1
    assert 'NOT_REGISTERED' in capsys.readouterr().out
    assert not (tmp_path/'data/cache/profitability/1/p6/plan.json').exists()


def test_cli_rejects_unbounded_polling_before_creating_state(tmp_path):
    from tools.profitability_forward_account import main
    with pytest.raises(SystemExit):main(['--state-root',str(tmp_path),'--attempt','1','--interval','0'])
    assert not (tmp_path/'data').exists()
