import base64
import hashlib
import json

import pytest

from tools import demo_portfolio_sources as sources
from tools.ai_factory_portfolio_forward import _prediction
from tools.btc_factory_research import seal
from services.research.ai_factory import HOUR,candidates
from services.research.ai_factory_forward import forward_features,forecast_document
from live.execution.demo_batch_risk import _digest
from live.execution.demo_portfolio_task import strategy_id
from tests.live.test_demo_request_inputs import inputs


def receipt(path,rows,at):
    raw=json.dumps(rows).encode()
    return {'path':path,'requested_at_ms':at-1,'received_at_ms':at,'payload':rows,
        'raw_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()}


def test_bound_native_sources_feed_original_task_and_reject_rehashed_prediction(tmp_path,monkeypatch):
    kwargs,opened,log=inputs(tmp_path,monkeypatch)
    try:
        kwargs.pop('orders');now=kwargs['now_ms'];slot=(now-300000)//HOUR*HOUR
        model=seal({'schema':'QUANTOS_FACTORY_PORTFOLIO_V1','components':candidates()[:1],
            'quarters':[4],'baseline_quarters':[4],'training_x':[[0.]*5]*40,'training_y':[.1,-.1]*20,
            'training_last_label_ms':HOUR,'authoritative':False,'live_eligible':False,'economic_eligible':False})
        freeze=seal({'schema':'QUANTOS_PORTFOLIO_FORWARD_V1','attempt':1,'activated_at_ms':slot-HOUR,
            'model':model,'authoritative':False,'account_pnl_computed':False})
        source=seal({'source':{'python_source_sha256':'d'*64}})
        rows=[[slot-(25-i)*HOUR,100,150,99,100+i,1,slot-(24-i)*HOUR-1,100,1,1,50,0] for i in range(25)]
        capture=seal(receipt(sources.bars_path(slot),rows,slot+1000))
        features=forward_features(rows,slot);options=_prediction(model,features)
        forecast=forecast_document(slot=slot,available=slot+1000,issued=slot+2000,activated=slot-HOUR,
            model_hash=model['result_hash'],input_hash=capture['result_hash'],features=features,
            **{k:v for k,v in options.items() if k!='extra'})
        forecast.update(options['extra']);forecast=seal(forecast)
        base=tmp_path/'data/cache/ai_factory/1/portfolio/forward'
        for relative,value in [('freeze.json',freeze),('source.json',source),
                               (f'forecasts/{slot}.json',forecast),(f"captures/{capture['result_hash']}.json",capture)]:
            path=base/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
        binding={'schema':'DEMO_PORTFOLIO_SOURCE_BINDING_V1','attempt':1,
            'freeze_sha256':freeze['result_hash'],'observer_source_sha256':source['result_hash']}
        allocation={'schema':'DEMO_PORTFOLIO_ALLOCATION_V2','source_commit':'c'*40,'symbol':'BTCUSDC',
            'account_ref_sha256':kwargs['origin']['account_ref_sha256'],'model_hash':model['result_hash'],
            'capital':'500','fee_bps':4,'slip_bps':5,'ioc_offset_bps':5,'source_binding_sha256':_digest(binding)}
        profile=kwargs['profile'];policy=kwargs['issuer_policy'];old=profile['strategy']['strategy_id'];sid=strategy_id(allocation)
        profile['strategy']['strategy_id']=sid;profile['attestations'][sid]=profile['attestations'].pop(old)
        profile['attestations'][sid]['strategy_id']=sid
        policy['bindings'].update(strategy_id=sid,strategy_approval_sha256=_digest(profile['strategy']),
            validation_sha256=_digest(profile['attestations']))
        last=(now-1)//(8*HOUR)*(8*HOUR)
        funding=receipt(sources.funding_path(now-2),[{'symbol':'BTCUSDC','fundingTime':last-i*8*HOUR,
            'fundingRate':'0','markPrice':'60000'} for i in (2,1,0)],now-1)
        result=sources.build_from_sources(allocation=allocation,binding=binding,root=tmp_path,
            funding_receipt=funding,**kwargs)
        assert result['status']=='TASK_PREPARED' and result['task']['orders'][0]['side']=='BUY'
        assert result['source_inputs']['forecast_sha256']==forecast['result_hash']
        assert opened[0].snapshot()==[] and opened[2].orders()==[]
        forecast.pop('result_hash');forecast['prediction']=-.2
        (base/f'forecasts/{slot}.json').write_text(json.dumps(seal(forecast)))
        with pytest.raises(ValueError,match='PORTFOLIO_FORECAST_RECOMPUTATION'):
            sources.build_from_sources(allocation=allocation,binding=binding,root=tmp_path,
                funding_receipt=funding,**kwargs)
    finally:
        log.close()
        for store in opened:store.close()
