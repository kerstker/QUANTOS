import numpy as np
import pytest

from services.research.ai_factory_portfolio import candidate_grid, combine_predictions, select_inner


def test_cancelling_forecasts_use_one_flat_decimal_account_without_double_haircut():
    from decimal import Decimal as D
    from services.research.ai_factory import HOUR
    from tools.ai_quant_factory import evaluate_market
    t=np.arange(48,120,dtype=np.int64)*HOUR
    minutes=np.arange(0,130*HOUR,60000,dtype=np.int64)
    market={'times':minutes,'prices':np.full(len(minutes),100.),'marks':np.full(len(minutes),100.),
        'start':0,'end':129*HOUR,'funding':[{'time':h*HOUR,'rate':D(0),'mark':D(100)} for h in range(0,129,8)],
        'daily_volume':{d:D('10000000') for d in range(6)}}
    p=combine_predictions([{'threshold_bps':20}]*2,np.array([np.full(len(t),.01),np.full(len(t),-.01)]),[2,2])
    result=evaluate_market(market,{'threshold_bps':0},t,p,cost_aware=True)
    assert result['fills']==0 and result['net_pnl']==0 and D(result['fees'])==0
    assert all(D(r['equity'])==1500 for r in result['equity'])


def test_portfolio_search_is_bounded_and_includes_single_models_and_cash():
    grid=candidate_grid([2,5,9])
    assert len(grid)==16 and grid[0]['quarters']==[0,0,0]
    assert len({tuple(c['quarters']) for c in grid})==16
    assert all(sum(c['quarters']) in (0,4) for c in grid)
    assert all([4*int(i==j) for i in range(3)] in [c['quarters'] for c in grid] for j in range(3))
    with pytest.raises(ValueError):candidate_grid([0,1,2,3])
    with pytest.raises(ValueError):candidate_grid([1,1])


def test_component_haircuts_precede_combination_and_opposing_signals_net():
    specs=[{'threshold_bps':20},{'threshold_bps':30}]
    p=np.array([[.003,-.003],[.004,-.004]])
    assert np.allclose(combine_predictions(specs,p,[4,0]),[.002,-.002])
    assert np.allclose(combine_predictions(specs,p,[2,2]),[.00225,-.00225])
    assert np.allclose(combine_predictions(specs,np.array([[.003],[-.0035]]),[2,2]),[0])
    assert np.array_equal(combine_predictions(specs,p,[0,0]),[0,0])
    with pytest.raises(ValueError):combine_predictions(specs,p,[4,4])
    with pytest.raises(ValueError):combine_predictions(specs,[[np.nan],[0]],[2,2])


def test_selection_uses_net_cost_score_and_cash_wins_uninformative_ties():
    grid=candidate_grid([1,2])
    records=[{'spec':s,'status':'PASSED','metrics':{'net_pnl':0,'drawdown':0,'fills':0}} for s in grid]
    assert select_inner(records)['spec']==grid[0]
    records[1]['metrics']={'net_pnl':10,'drawdown':.02,'fills':10}
    assert select_inner(records)['spec']==grid[0]
    records[-1]['metrics']={'net_pnl':8,'drawdown':.001,'fills':5}
    assert select_inner(records)['spec']==grid[-1]
    records[-1]['status']='FAILED'
    assert select_inner(records)['spec']==grid[0]
