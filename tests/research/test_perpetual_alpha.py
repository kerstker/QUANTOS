from decimal import Decimal as D
import numpy as np
import pytest

from services.research.perpetual_alpha import replay, hypothesis_target


def market():
    start=1735689900000
    times=np.arange(start,start+2*86400000+60000,60000,dtype=np.int64)
    return dict(times=times,prices=np.full(len(times),100.),marks=np.full(len(times),100.),
                funding=[],start=start,end=int(times[-1]),daily_volume={int(t//86400000):D('10000000') for t in times})


def test_perpetual_funding_and_cash_use_canonical_ledger():
    data=market()
    data['funding']=[{'time':data['start']+3600000,'rate':D('.001'),'mark':D(100)}]
    result=replay(**data,decision=lambda at,held:.25,slip_bps=5,stop=True)
    assert D(result['reconciliation']['funding'])<0
    assert result['reconciliation']['residual']=='0'
    assert all(D(f['fee'])==abs(D(f['quantity']))*D(f['price'])*D('.0004') for f in result['fills'])
    assert result['cash_model']=='CANONICAL_LINEAR_USDC_PERPETUAL'


def test_mark_loss_exits_on_next_trade_quote_and_does_not_reenter():
    data=market();data['marks'][10:]=50.;data['prices'][10:]=70.
    r=replay(**data,decision=lambda at,held:.25,slip_bps=5,stop=True)
    assert r['halted_at']==int(data['times'][10])
    assert r['fills'][-1]['time']==int(data['times'][11])
    assert D(r['fills'][-1]['price'])>D('69')
    assert len(r['fills'])==2
    assert D(r['daily'][-1]['quantity'])==0


def test_funding_loss_latches_and_short_receives_positive_funding():
    data=market();data['funding']=[{'time':data['start']+i*60000+1,'rate':D('.1'),'mark':D(100)} for i in range(1,5)]
    long=replay(**data,decision=lambda at,held:.25,slip_bps=5,stop=True)
    assert long['halted_at']==data['start']+240001
    assert long['fills'][-1]['time']==data['start']+300000
    short=replay(**data,decision=lambda at,held:-.25,slip_bps=5,stop=False)
    assert D(short['reconciliation']['funding'])>0


def test_missing_minute_rejected_and_future_marks_cannot_change_prefix():
    data=market();data['times']=np.delete(data['times'],20)
    with pytest.raises(ValueError): replay(**data,decision=lambda at,held:0.,slip_bps=5,stop=True)
    data=market();data['end']=int(data['times'][1440])
    a=replay(**data,decision=lambda at,held:.25,slip_bps=5,stop=True)
    data['marks'][1441:]=1e9
    b=replay(**data,decision=lambda at,held:.25,slip_bps=5,stop=True)
    assert a==b


def test_new_hypotheses_are_fixed_and_causal():
    values=np.arange(1.,20.)
    assert hypothesis_target(('close_return','trend',3),values)==.25
    assert hypothesis_target(('close_return','fade',3),values)==-.25
    assert hypothesis_target(('settled_funding','fade',3),np.zeros(5))==0.
    with pytest.raises(ValueError):hypothesis_target(('unknown','fade',3),values)


def test_reversal_checks_new_risk_even_when_absolute_quantity_decreases():
    data=market()
    data['marks'][1440:]=200.
    data['prices'][1440:]=80.
    result=replay(**data,decision=lambda at,held:-.25 if at==data['start'] else .25,
                  slip_bps=5,stop=False)
    assert result['orders'][-1]['status']=='REJECTED_POST_FILL_RISK'
    assert D(result['daily'][-1]['quantity'])<0


def test_context_decision_observes_real_fills_without_ledger_access():
    data=market();contexts=[]
    def choose(at,context):
        contexts.append(context)
        with pytest.raises(TypeError):context['quantity']='999'
        return .25 if len(contexts)==1 else None
    result=replay(**data,decision=lambda *a:pytest.fail('legacy callback called'),
                  context_decision=choose,slip_bps=5,stop=True)
    assert contexts[0]['quantity']=='0'
    assert D(contexts[1]['quantity'])>0
    assert len(result['fills'])==1
