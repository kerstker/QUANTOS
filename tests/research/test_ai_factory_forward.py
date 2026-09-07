import numpy as np
import pytest
from services.research.ai_factory_forward import forward_features, forecast_document, label_document
from services.research.ai_factory import HOUR,candidates


def bars(slot):
    return [[slot-(25-i)*HOUR,100,102,99,100+i,1,slot-(24-i)*HOUR-1,100,1,1,50,0] for i in range(25)]


def test_features_match_closed_hour_contract():
    slot=100*HOUR
    x=forward_features(bars(slot),slot)
    assert x[0]==pytest.approx(124/123-1)
    assert x[1]==pytest.approx(124/118-1)
    assert x[2]==pytest.approx(.24) and x[4]==0
    bad=bars(slot);bad[-1][6]+=1
    with pytest.raises(ValueError):forward_features(bad,slot)


def test_forecast_cannot_be_backdated_or_issued_after_entry():
    slot=100*HOUR
    kwargs=dict(slot=slot,available=slot+1000,issued=slot+2000,activated=slot-1,model_hash='a'*64,input_hash='b'*64,features=[0]*5,prediction=.003,threshold_bps=20)
    result=forecast_document(**kwargs)
    assert result['entry_ms']==slot+300000 and result['exit_ms']==slot+300000+4*HOUR
    for change in ({'issued':slot+300000},{'available':slot+3000},{'activated':slot+1}):
        with pytest.raises(ValueError):forecast_document(**{**kwargs,**change})


def test_outcome_requires_closed_mature_references_and_preserves_forecast():
    slot=100*HOUR
    f=forecast_document(slot=slot,available=slot+1000,issued=slot+2000,activated=slot-1,model_hash='a'*64,input_hash='b'*64,features=[0]*5,prediction=.003,threshold_bps=20)
    a={'event_ms':f['entry_ms'],'available_at_ms':f['exit_ms']+60000,'open':100,'source_hash':'c'*64}
    b={**a,'event_ms':f['exit_ms'],'open':101}
    y=label_document(f,a,b,now=f['exit_ms']+60000)
    assert y['realized_return']==pytest.approx(.01) and f['prediction']==.003
    with pytest.raises(ValueError):label_document(f,a,b,now=f['exit_ms'])
