from decimal import Decimal
import numpy as np
import pytest

from services.research.profitability_causal_information import make_plan,observation,panel
from tools import profitability_causal_study as module
from tools.btc_factory_research import seal,verified
from tools.profitability_study import write_new
from tests.research.test_profitability_causal_information import HOUR,receipts


@pytest.mark.parametrize('quote',['USDC','USDT'])
def test_frozen_feature_panel_reaches_registered_study_with_exact_labels_and_quote(tmp_path,monkeypatch,quote):
    plan=seal(make_plan(now_ms=47*HOUR+1,p0_hash='a'*64))
    rows=[observation(receipts(t,quote),slot=t,quote=quote,issued_ms=t+1000)
          for t in range(plan['warmup_ms'],plan['end_slot_ms']+HOUR,HOUR)]
    collected=panel(plan,rows,quote=quote,as_of_ms=plan['mature_ms'])
    write_new(tmp_path/'source.json',{'source':{'frozen':'implementation'}})
    def capture(output,*,start,end,quote):
        times=np.arange(start,end+60000,60000);prices=np.linspace(100,120,len(times))
        return {'times':times,'prices':prices,'marks':prices.copy(),'start':start,'end':end,
                'funding':[],'daily_volume':{},'maximum_quantity':Decimal(50),'quote':quote}
    seen=[]
    def evaluate(output,registration,t,x,y,info,market,*,information_hash,trials):
        assert registration['created_at_ms']<int(t[0]) and registration['quote']==quote
        assert registration['history']=='PROSPECTIVE_FEATURES_POST_OUTCOME_RESEARCH_NOT_ISSUED_PREDICTIONS'
        assert len(t)==504 and np.all(y>0) and info.shape==(504,6)
        assert market['quote']==quote
        for spec in registration['specs']:write_new(output/f'inner-{spec["id"]}.started.json',{'scope':'INNER'})
        seen.append(registration['prior_input_revision_trials'])
        result={'status':'COMPLETED_TECHNICAL_ONLY','stage':registration['stage'],'decision':'STOP_NO_INCREMENT',
                'trials_attempted':len(registration['specs']),'selected_id':0,'cases':[],'economic_eligible':False}
        write_new(output/'result.json',result);return result
    monkeypatch.setattr(module,'evaluate_information_panel',evaluate)
    result=module.run_collected_studies(tmp_path/'study',plan,collected,capture=capture)
    assert seen==[0,4] and result['quote']==quote
    assert result['results']['P3']['result_hash']==verified(tmp_path/'study/P3/result.json')['result_hash']
    assert result['prediction_evidence']=='NONE_FEATURE_COLLECTION_ONLY'
    with pytest.raises(FileExistsError):module.run_collected_studies(tmp_path/'study',plan,collected,capture=capture)


def test_joint_capture_rejects_changed_chunk_boundary(tmp_path):
    day=24*HOUR;start=2*day+300000;end=start+11*day;calls=[]
    def capture(output,*,start,end,quote):
        calls.append((start,end,quote));times=np.arange(start,end+60000,60000);prices=np.full(len(times),100.)
        if len(calls)==2:prices[0]=101.
        return {'times':times,'prices':prices,'marks':prices.copy(),'start':start,'end':end,
                'funding':[],'daily_volume':{},'maximum_quantity':Decimal(50),'quote':quote},[]
    with pytest.raises(ValueError,match='revision'):
        module.capture_window(tmp_path/'market',start=start,end=end,quote='USDT',capture=capture)
    assert len(calls)==2 and all(c[2]=='USDT' for c in calls)


def test_market_adapter_rejects_the_other_settlement_currency():
    from tests.research.test_profitability_forward_market import receipts as market_receipts
    from tools.profitability_forward_market import market_from_receipts
    start,end,data=market_receipts()
    with pytest.raises(ValueError):market_from_receipts(data,start=start,end=end,quote='USDT')
    for row in data[-1]['payload']:row['symbol']='BTCUSDT'
    market=market_from_receipts(data,start=start,end=end,quote='USDT')
    assert market['quote']=='USDT' and market['maximum_quantity']==Decimal(50)
