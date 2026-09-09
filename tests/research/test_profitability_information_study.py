import json
import numpy as np

from tools import profitability_information_study as module
from tools.profitability_study import write_new
from tools.btc_factory_research import verified


def test_six_contrasts_recorded_and_outer_cannot_change_saved_choice(tmp_path,monkeypatch):
    p0=tmp_path/'p0';p0.mkdir();out=tmp_path/'out'
    write_new(p0/'preregistration.json',{'experiment':str(tmp_path/'experiment')})
    write_new(p0/'result.json',{'decision':'P1_BOUNDED_INNER_COMPARISON_ALLOWED','model_hash':'model'})
    t=(np.arange(90)+1)*3600000;x=np.ones((90,5))*.001;y=np.zeros(90)
    information=tmp_path/'information.json'
    information.write_text(json.dumps({'quote':'USDC','observations':[{'slot_ms':int(at),'available_ms':int(at),
        'flow':.1,'funding':.001,'oi_change':-.01,'source_hash':'a'*64,'funding_event_ms':int(at)-1,'funding_interval_ms':28800000,'oi_event_ms':int(at)-1,'oi_previous_ms':int(at)-2} for at in t]}))
    monkeypatch.setattr(module,'frozen_inputs',lambda *a:({'identity':{'inputs':{'input_hash':'data'}}},
        {'result_hash':'model'},{},t,x,y,{}))
    monkeypatch.setattr(module,'partition',lambda *a:(np.arange(30),np.arange(30,60),np.arange(60,90)))
    def fit(train,labels,predict):
        if len(train)>30:assert verified(out/'selection.json')['selected_id']==5
        return np.full(len(predict),train.shape[1]/1000)
    monkeypatch.setattr(module,'fit_information',fit)
    def evaluate(market,spec,t,p,**kwargs):
        return {'score':float(p[0]),'net_pnl':float(p[0]),'fills':2,
                'equity':[{'equity':'1500'},{'equity':'1501'}]}
    monkeypatch.setattr(module,'evaluate_market',evaluate)
    result=module.run_p4(p0,information,tmp_path/'input',out)
    assert result['trials_attempted']==6 and result['selected_id']==5
    assert len(result['cases'])==6 and result['causal_increment_established'] is False
