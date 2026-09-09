import pytest
from tools.profitability_study import write_new, run_p0
from tools.btc_factory_research import verified


def test_attempt_receipt_cannot_be_overwritten(tmp_path):
    path=tmp_path/'trial.json'
    write_new(path,{'status':'FAILED','reason':'INPUT_GAP'})
    with pytest.raises(FileExistsError):write_new(path,{'status':'PASSED'})
    assert verified(path)['status']=='FAILED'


def test_failed_preflight_retains_registered_attempt(tmp_path):
    output=tmp_path/'new-study'
    with pytest.raises(FileNotFoundError):run_p0(tmp_path/'absent',tmp_path/'input',output)
    assert verified(output/'preregistration.json')['attempts']==1
    assert verified(output/'failure.json')['status']=='FAILED'
    with pytest.raises(FileExistsError):run_p0(tmp_path/'absent',tmp_path/'input',output)


def test_frozen_artifacts_are_never_output_targets(tmp_path):
    with pytest.raises(ValueError):run_p0(tmp_path,tmp_path/'input',tmp_path/'child')


def test_p1_choice_is_saved_before_outer_read_and_failed_attempts_count(tmp_path,monkeypatch):
    import numpy as np
    from tools import profitability_position_study as module
    from services.research.ai_factory_store import digest
    p0=tmp_path/'p0';p0.mkdir();output=tmp_path/'p1'
    write_new(p0/'preregistration.json',{'experiment':str(tmp_path/'frozen')})
    write_new(p0/'result.json',{'stage':'P0','decision':'P1_BOUNDED_INNER_COMPARISON_ALLOWED',
              'model_hash':'model','input_hash':'data','prediction_hash':digest([.003]*30)})
    t=np.arange(90)*3600000;x=np.zeros((90,5));y=np.zeros(90)
    monkeypatch.setattr(module,'frozen_inputs',lambda *a:({'identity':{'inputs':{'input_hash':'data'}}},
        {'result_hash':'model','decision_policy':'DISCRETE_COST_RISK_V1','spec':{},'training_x':[], 'training_y':[]},
        {},t,x,y,{}))
    monkeypatch.setattr(module,'partition',lambda *a:(np.arange(30),np.arange(30,60),np.arange(60,90)))
    def predict(spec,xx,yy,features):
        if len(xx)==0:assert verified(output/'selection.json')['policy']=='HYSTERESIS_20_5'
        return np.full(len(features),.003)
    monkeypatch.setattr(module,'fit_predict',predict)
    def evaluate(market,spec,t,p,**kw):
        policy=kw['position_policy'];inner=t[0]<60*3600000
        if inner and policy=='HYSTERESIS_30_10':raise ValueError('record this failed attempt')
        score=10 if policy=='HYSTERESIS_20_5' else 0
        if not inner:
            assert verified(output/'selection.json')['policy']=='HYSTERESIS_20_5'
            score=-1000 if policy=='HYSTERESIS_20_5' else 9999
        return {'score':score,'net_pnl':score,'fills':1,'equity':[{'equity':'1500'},{'equity':'1499'}]}
    monkeypatch.setattr(module,'evaluate_market',evaluate)
    result=module.run_p1(p0,tmp_path/'input',output)
    assert result['selected_policy']=='HYSTERESIS_20_5'
    assert result['trials_attempted']==4
    assert result['trials'][-1]['status']=='FAILED'
    assert len(result['cases'])==10
    assert not result['historical_out_of_sample_claim']
    assert result['decision']=='STOP_COST_UNSTABLE_INCREMENT'
