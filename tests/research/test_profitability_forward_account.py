import pytest
from services.research.profitability_forward_account import make_plan,validate_window,replay_window
from tools.btc_factory_research import seal
from services.research.ai_factory import HOUR

DAY=24*HOUR


def forecasts(plan):
    return [seal({'slot_ms':slot,'available_at_ms':slot+1,'issued_at_ms':slot+2,
                  'entry_ms':slot+300000,'exit_ms':slot+300000+4*HOUR,
                  'model_hash':'a'*64,'input_hash':'b'*64,'prediction':.01,'target_weight':.25})
            for slot in range(plan['start_ms']-300000,plan['end_ms']-300000,HOUR)]


def test_plan_is_future_and_missing_forecasts_are_not_backfilled():
    plan=make_plan(now_ms=DAY+123,model_hash='a'*64)
    assert plan['start_ms']==2*DAY+300000
    rows=forecasts(plan)
    assert validate_window(plan,rows,as_of_ms=plan['end_ms']+4*HOUR)['status']=='READY'
    result=validate_window(plan,rows[:-1],as_of_ms=plan['end_ms']+4*HOUR)
    assert result['status']=='MISSING_FORECASTS' and len(result['missing_slots'])==1
    assert validate_window(plan,rows,as_of_ms=plan['start_ms']+DAY-1)['status']=='WAITING_MATURITY'


def test_forecast_rewrite_and_late_publication_rejected():
    plan=make_plan(now_ms=DAY+1,model_hash='a'*64);rows=forecasts(plan)
    with pytest.raises(ValueError):validate_window(plan,[{**rows[0],'prediction':.2},*rows[1:]],as_of_ms=4*DAY)
    row={k:v for k,v in rows[0].items() if k!='result_hash'};row['issued_at_ms']=row['entry_ms']+1
    with pytest.raises(ValueError):validate_window(plan,[seal(row),*rows[1:]],as_of_ms=4*DAY)


def test_account_uses_canonical_ledger_and_same_cash_baseline():
    import numpy as np
    from decimal import Decimal as D
    plan=make_plan(now_ms=DAY+1,model_hash='a'*64);rows=forecasts(plan)
    times=np.arange(plan['start_ms'],plan['end_ms']+60000,60000)
    market={'times':times,'prices':np.full(len(times),100000.),'marks':np.full(len(times),100000.),
            'funding':[],'start':int(times[0]),'end':int(times[-1]),
            'daily_volume':{int(plan['start_ms']//DAY):D('1000000000')},'maximum_quantity':D('50')}
    result=replay_window(plan,rows,market,as_of_ms=4*DAY)
    assert len(result['cases'])==6
    assert all(c['attribution']['reconciliation_residual']=='0' for c in result['cases'])
    assert all(float(c['attribution']['net_pnl'])<=0 for c in result['cases'])
    assert result['economic_eligible'] is False
    assert result['actual_venue_costs'] is None
