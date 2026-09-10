"""One-way sleeve ownership follows committed quantity, never requested fills."""
from decimal import Decimal as D
import pytest
from services.research.ai_factory_sleeves import SleeveBook
from services.research.perpetual_alpha import replay
from tests.research.test_perpetual_alpha import market


def test_offsetting_intents_do_not_fabricate_two_filled_positions():
    book=SleeveBook([2,5],[2,2])
    assert book.propose([D('.125'),D('-.125')])==D(0)
    assert book.positions=={2:D(0),5:D(0)}
    book.execution({'status':'NO_NET_CHANGE','quantity':'0'})
    assert book.positions=={2:D(0),5:D(0)}
    assert book.report()['gross_desired_weight']=='0.25'
    assert book.report()['actual_quantity']=='0'


def test_rejected_trade_retains_ownership_and_partial_quantity_conserves_lots():
    book=SleeveBook([2,5,9],[1,1,2])
    book.propose([D('.0625'),D('.0625'),D('.125')])
    book.execution({'status':'REJECTED_POST_FILL_RISK','quantity':'0'})
    assert sum(book.positions.values())==0
    book.execution({'status':'FILLED','quantity':'0.007'})
    assert book.positions=={2:D('.002'),5:D('.002'),9:D('.003')}
    before=dict(book.positions)
    book.propose([D('-.0625'),D('-.0625'),D('-.125')])
    book.execution({'status':'REJECTED_CAPACITY','quantity':'0.007'})
    assert book.positions==before
    book.execution({'status':'FILLED','quantity':'-0.005'})
    assert sum(book.positions.values())==D('-.005')
    assert all(v<=0 for v in book.positions.values())


def test_flattened_ledger_state_clears_sleeves_after_forced_risk_exit():
    book=SleeveBook([2,5],[2,2]);book.propose([D('.125'),D('.125')])
    book.execution({'status':'FILLED','quantity':'1'})
    book.execution({'status':'LEDGER_STATE','quantity':'0'})
    assert all(v==0 for v in book.positions.values())
    with pytest.raises(ValueError):book.execution({'status':'LEDGER_STATE','quantity':'-1'})


def test_budget_and_acknowledgement_constraints_reject_ambiguous_allocation():
    book=SleeveBook([2,5],[2,2])
    with pytest.raises(ValueError):book.propose([D('.25'),D('.25')])
    with pytest.raises(ValueError):book.propose([D('NaN'),D(0)])
    with pytest.raises(ValueError):book.execution({'status':'FILLED','quantity':'1'})
    with pytest.raises(ValueError):SleeveBook([2,2],[2,2])
    with pytest.raises(ValueError):SleeveBook([2,5],[True,3])


def test_lot_remainders_conserve_quantity_for_all_bounded_allocations():
    from services.research.ai_factory_portfolio import candidate_grid
    for candidate in candidate_grid([2,5,9])[1:]:
        book=SleeveBook([2,5,9],candidate['quarters'])
        book.propose([D(q)/16 for q in candidate['quarters']])
        for sign in (1,-1):
            book.propose([sign*D(q)/16 for q in candidate['quarters']])
            for lots in (1,2,7,101,999):
                quantity=sign*D(lots)/1000
                book.execution({'status':'FILLED','quantity':str(quantity)})
                assert sum(book.positions.values())==quantity
                assert all(q%D('.001')==0 for q in book.positions.values())
                assert all(book.positions[i]==0 for i,w in zip((2,5,9),candidate['quarters']) if not w)


def test_explicit_net_targets_share_existing_risk_and_committed_feedback():
    book=SleeveBook([2,5],[3,1]);seen=[]
    def choose(at,context):
        assert sum(book.positions.values())==D(context['quantity'])
        return float(book.propose([D('.1875'),D(0)]))
    def observe(at,event):
        with pytest.raises(TypeError):event['quantity']='999'
        book.execution(event);seen.append(dict(event))
    result=replay(**market(),decision=lambda *a:None,context_decision=choose,slip_bps=5,stop=True,
        target_policy='SLEEVE_NET_V1',execution_observer=observe)
    assert result['reconciliation']['residual']=='0'
    assert sum(book.positions.values())==D(result['daily'][-1]['quantity'])
    assert book.positions[5]==0 and any(e['status']=='FILLED' for e in seen)
    assert all(D(f['fee'])==abs(D(f['quantity']))*D(f['price'])*D('.0004') for f in result['fills'])
    rejected=SleeveBook([2],[4])
    result=replay(**market(),decision=lambda *a:.1875,slip_bps=5,stop=True,maximum_quantity=D('.001'),
        target_policy='SLEEVE_NET_V1',execution_observer=lambda at,e:rejected.execution(e))
    assert not result['fills'] and sum(rejected.positions.values())==0


def test_existing_discrete_policy_and_total_exposure_bounds_remain_enforced():
    with pytest.raises(ValueError,match='bounded target'):
        replay(**market(),decision=lambda *a:.1875,slip_bps=5,stop=True)
    with pytest.raises(ValueError,match='bounded target'):
        replay(**market(),decision=lambda *a:.2501,slip_bps=5,stop=True,target_policy='SLEEVE_NET_V1')
    with pytest.raises(ValueError,match='sleeve risk'):
        replay(**market(),decision=lambda *a:0.,slip_bps=5,stop=False,target_policy='SLEEVE_NET_V1')


def test_independent_sleeve_decisions_are_not_a_blended_prediction_policy():
    import numpy as np
    from tools.ai_factory_sleeve_replay import evaluate_sleeves
    from services.research.ai_factory import HOUR
    data=market();data['funding']=[{'time':data['start']-i*8*HOUR,'rate':D(0),'mark':D(100)} for i in (3,2,1)]
    t=np.arange(data['start']-300000,data['end']-300000,HOUR,dtype=np.int64)
    specs=[{'id':2,'threshold_bps':20},{'id':5,'threshold_bps':20}]
    p=np.array([np.full(len(t),.02),np.full(len(t),-.004)])
    result=evaluate_sleeves(data,specs,[2,2],t,p,variance_by_slot={int(at):.00001 for at in t})
    # Each sleeve independently chooses +/-.25 of its half budget. Forecast
    # magnitudes would leave a positive blend, but these position intents net.
    assert result['fills']==0 and result['net_pnl']==0
    trace=result['sleeve_execution']['decisions']
    assert trace[0]['gross_desired_weight']=='0.25' and trace[0]['net_desired_weight']=='0'
    assert trace[0]['positions']=={'2':'0','5':'0'}
    assert result['execution_feedback']['reconciliation']['residual']=='0'


def test_rejected_sleeve_orders_cannot_become_cost_free_hold_state():
    import numpy as np
    from tools.ai_factory_sleeve_replay import evaluate_sleeves
    from services.research.ai_factory import HOUR
    data=market();data['funding']=[{'time':data['start']-i*8*HOUR,'rate':D(0),'mark':D(100)} for i in (3,2,1)]
    data['daily_volume']={day:D('1') for day in data['daily_volume']}
    t=np.arange(data['start']-300000,data['end']-300000,HOUR,dtype=np.int64)
    result=evaluate_sleeves(data,[{'id':2,'threshold_bps':20}],[4],t,np.full((1,len(t)),.02))
    assert result['fills']==0
    assert all(row['positions']=={'2':'0'} for row in result['sleeve_execution']['decisions'])
    assert any(row['status']=='REJECTED_CAPACITY' for row in result['sleeve_execution']['execution'])


@pytest.mark.parametrize('price',[70000,80000,100000])
@pytest.mark.parametrize('sign',[1,-1])
def test_holding_one_sleeve_with_another_flat_does_not_generate_rounding_trades(price,sign):
    import numpy as np
    from services.research.ai_factory_sleeve_replay import replay_sleeves
    from services.research.ai_factory import HOUR
    data=market();data['prices'][:]=price;data['marks'][:]=price
    data['funding']=[{'time':data['start']-i*8*HOUR,'rate':D(0),'mark':D(price)} for i in (3,2,1)]
    t=np.arange(data['start']-300000,data['end']-300000,HOUR,dtype=np.int64)
    predictions=np.zeros((2,len(t)));predictions[0,0]=sign*.02
    result=replay_sleeves(data,[{'id':2,'threshold_bps':20},{'id':5,'threshold_bps':20}],
        [2,2],t,predictions,variance_by_slot={int(at):0 for at in t})
    assert len(result['fills'])==1
    for decision in result['sleeve_execution']['decisions'][1:]:
        assert decision['choices'][0]['reason']=='HOLD_COST_BAND'
        assert decision['target'] is None
    assert result['daily'][-1]['quantity']==result['fills'][0]['quantity']


def test_flat_component_does_not_suppress_an_owned_sleeve_exit():
    import numpy as np
    from services.research.ai_factory_sleeve_replay import replay_sleeves
    from services.research.ai_factory import HOUR
    data=market();data['prices'][:]=70000;data['marks'][:]=70000
    data['funding']=[{'time':data['start']-i*8*HOUR,'rate':D(0),'mark':D(70000)} for i in (3,2,1)]
    t=np.arange(data['start']-300000,data['end']-300000,HOUR,dtype=np.int64)
    p=np.zeros((2,len(t)));p[0,0]=.02;p[0,1]=-.0015
    result=replay_sleeves(data,[{'id':2,'threshold_bps':20},{'id':5,'threshold_bps':20}],
        [2,2],t,p,variance_by_slot={int(at):.0004 for at in t})
    assert len(result['fills'])==2 and result['daily'][-1]['quantity']=='0'
    assert result['fills'][1]['time']==data['start']+HOUR
